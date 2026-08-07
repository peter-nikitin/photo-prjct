from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection


def _loader(*identities: tuple[str, str]) -> Mock:
    loader = Mock()
    loader.disk_migrations = {identity: object() for identity in identities}
    return loader


def _recorder(*identities: tuple[str, str]) -> Mock:
    recorder = Mock()
    recorder.applied_migrations.return_value = set(identities)
    return recorder


def _command() -> str:
    return "picflow.management.commands.verify_migration_history"


def test_all_applied_migrations_present_on_disk_are_accepted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    loader = _loader(("picflow", "0001_initial"), ("selfie_search", "0003_feedback"))
    recorder = _recorder(("picflow", "0001_initial"), ("selfie_search", "0003_feedback"))
    schema_editor = Mock()
    migration_executor = Mock()

    with (
        patch(f"{_command()}.MigrationLoader", return_value=loader) as migration_loader,
        patch(f"{_command()}.MigrationRecorder", return_value=recorder) as migration_recorder,
        patch.object(connection, "schema_editor", schema_editor),
        patch("django.db.migrations.executor.MigrationExecutor", migration_executor),
    ):
        call_command("verify_migration_history")

    assert capsys.readouterr().out == "migration-history-ok\n"
    migration_loader.assert_called_once_with(connection, ignore_no_migrations=True)
    migration_recorder.assert_called_once_with(connection)
    recorder.applied_migrations.assert_called_once_with()
    schema_editor.assert_not_called()
    migration_executor.assert_not_called()


def test_applied_migration_missing_from_disk_is_rejected() -> None:
    loader = _loader(("picflow", "0001_initial"))
    recorder = _recorder(("picflow", "0001_initial"), ("selfie_search", "0003_feedback"))

    with (
        patch(f"{_command()}.MigrationLoader", return_value=loader),
        patch(f"{_command()}.MigrationRecorder", return_value=recorder),
        pytest.raises(
            CommandError,
            match=r"^Applied migrations missing from candidate: selfie_search\.0003_feedback$",
        ),
    ):
        call_command("verify_migration_history")


def test_missing_applied_migration_identities_are_sorted_deterministically() -> None:
    loader = _loader()
    recorder = _recorder(
        ("selfie_search", "0003_feedback"),
        ("catalog", "0002_event"),
        ("catalog", "0001_initial"),
    )

    with (
        patch(f"{_command()}.MigrationLoader", return_value=loader),
        patch(f"{_command()}.MigrationRecorder", return_value=recorder),
        pytest.raises(
            CommandError,
            match=(
                r"^Applied migrations missing from candidate: "
                r"catalog\.0001_initial, catalog\.0002_event, selfie_search\.0003_feedback$"
            ),
        ),
    ):
        call_command("verify_migration_history")


def test_unapplied_disk_migrations_are_allowed(capsys: pytest.CaptureFixture[str]) -> None:
    loader = _loader(("picflow", "0001_initial"), ("picflow", "0002_add_photo"))
    recorder = _recorder(("picflow", "0001_initial"))

    with (
        patch(f"{_command()}.MigrationLoader", return_value=loader),
        patch(f"{_command()}.MigrationRecorder", return_value=recorder),
    ):
        call_command("verify_migration_history")

    assert capsys.readouterr().out == "migration-history-ok\n"


@pytest.mark.parametrize(
    "failure", [ConnectionError("db password=leak"), RuntimeError("loader detail")]
)
def test_database_or_loader_errors_are_sanitized(failure: Exception) -> None:
    with (
        patch(f"{_command()}.MigrationLoader", side_effect=failure),
        pytest.raises(CommandError, match=r"^Could not verify migration history$"),
    ):
        call_command("verify_migration_history")


def test_applied_migration_ledger_error_is_sanitized() -> None:
    recorder = _recorder()
    recorder.applied_migrations.side_effect = ConnectionError("database detail must stay hidden")

    with (
        patch(f"{_command()}.MigrationLoader", return_value=_loader()),
        patch(f"{_command()}.MigrationRecorder", return_value=recorder),
        pytest.raises(CommandError, match=r"^Could not verify migration history$"),
    ):
        call_command("verify_migration_history")
