from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import select_test_suites

ROOT = Path(__file__).resolve().parents[1]

MANIFEST = """
version = 1

[[categories]]
name = "documentation"
patterns = ["docs/**", "README.md"]
suites = []

[[categories]]
name = "operational"
patterns = ["deploy/**", "tests/deployment/**"]
suites = ["operational"]
layer = "operational"

[[categories]]
name = "migrations"
patterns = ["src/backend/*/migrations/**", "tests/test_migration_immutability.py"]
suites = ["migrations"]
layer = "migration"

[[categories]]
name = "visual"
patterns = ["src/backend/**/templates/**", "tests/visual/**"]
suites = ["visual"]

[[categories]]
name = "product-flow"
patterns = ["tests/processing/**"]
suites = []
layer = "product_flow"

[[categories]]
name = "python"
patterns = ["src/**", "tests/**", "scripts/**"]
suites = []
"""


def write_manifest(tmp_path: Path, content: str = MANIFEST) -> Path:
    manifest = tmp_path / "suite-selection.toml"
    manifest.write_text(content, encoding="utf-8")
    return manifest


def test_load_config_requires_every_expensive_suite(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path, MANIFEST.replace('suites = ["visual"]', "suites = []"))

    with pytest.raises(ValueError, match="visual"):
        select_test_suites.load_config(manifest)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (MANIFEST.replace("version = 1", "version = 1\nunknown = true"), "unsupported"),
        (MANIFEST.replace('"docs/**"', '"/docs/**"'), "relative"),
        (
            MANIFEST.replace('name = "documentation"', 'name = "orphan"').replace(
                '"docs/**"', '"nope/**"'
            ),
            "known",
        ),
    ],
)
def test_load_config_rejects_invalid_manifest_contract(
    tmp_path: Path, content: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        select_test_suites.load_config(write_manifest(tmp_path, content))


@pytest.mark.parametrize(
    ("changed_files", "expected"),
    [
        (["docs/testing.md"], (False, False, False)),
        (["deploy/apply-deployment.sh"], (True, False, False)),
        (["src/backend/picflow/migrations/0001_initial.py"], (False, True, False)),
        (["src/backend/picflow/templates/picflow/gallery.html"], (False, False, True)),
        (["deploy/apply-deployment.sh", "tests/visual/visual.spec.js"], (True, False, True)),
        (["infrastructure/new-resource.tf"], (True, True, True)),
    ],
)
def test_select_suites_is_core_always_and_fails_closed(
    tmp_path: Path, changed_files: list[str], expected: tuple[bool, bool, bool]
) -> None:
    selection = select_test_suites.select_suites(
        select_test_suites.load_config(write_manifest(tmp_path)), changed_files
    )

    assert selection.core is True
    assert (selection.operational, selection.migrations, selection.visual) == expected
    assert selection.reasons["operational"] == tuple(sorted(set(selection.reasons["operational"])))


@pytest.mark.parametrize("path", ["/absolute/path.py", "../escape.py", "src//backend/app.py"])
def test_select_suites_fails_closed_for_malformed_paths(tmp_path: Path, path: str) -> None:
    selection = select_test_suites.select_suites(
        select_test_suites.load_config(write_manifest(tmp_path)), [path]
    )

    assert (selection.operational, selection.migrations, selection.visual) == (True, True, True)
    assert selection.reasons["operational"] == (f"fail-closed: malformed path {path!r}",)


def test_select_command_supports_explicit_files_and_json_output(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(Path(select_test_suites.__file__)),
            "select",
            "--config",
            str(manifest),
            "--changed-file",
            "deploy/apply-deployment.sh",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "core": True,
        "migrations": False,
        "operational": True,
        "reasons": {
            "migrations": [],
            "operational": ["deploy/apply-deployment.sh"],
            "visual": [],
        },
        "visual": False,
    }


def test_github_output_preserves_a_shell_metacharacter_filename_as_data(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(Path(select_test_suites.__file__)),
            "select",
            "--config",
            str(manifest),
            "--changed-file",
            "deploy/$(printf injected).sh",
            "--format",
            "github",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "operational_reason=deploy/$(printf injected).sh" in result.stdout.splitlines()


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (".agents/skills/select-verification-suites/SKILL.md", (False, False, False)),
        ("src/backend/commerce/tests/test_models.py", (False, False, False)),
        ("src/backend/processing/tests/test_models.py", (False, True, False)),
        ("src/backend/config/settings.py", (True, False, False)),
        ("deploy/apply-deployment.sh", (True, True, False)),
        ("scripts/clone-deployed-db.sh", (True, False, False)),
        ("scripts/local-web.sh", (True, False, False)),
        ("scripts/run-with-environment-secrets.py", (True, False, False)),
        ("scripts/copy-object-storage-bucket.py", (True, False, False)),
        ("scripts/monitor_public_health.py", (True, False, False)),
        ("scripts/create-worktree.py", (False, False, False)),
        ("scripts/run-in-test-env.sh", (False, False, False)),
        ("scripts/unknown-entrypoint.py", (True, True, True)),
        ("tests/processing/test_worker_container_contract.py", (True, False, False)),
        ("tests/test_visual_test_runner.py", (False, False, True)),
    ],
)
def test_production_manifest_selects_runtime_and_schema_sensitive_paths(
    path: str, expected: tuple[bool, bool, bool]
) -> None:
    selection = select_test_suites.select_suites(
        select_test_suites.load_config(ROOT / "tests" / "suite-selection.toml"), [path]
    )

    assert (selection.operational, selection.migrations, selection.visual) == expected


def test_processing_model_migration_contracts_select_the_migration_suite() -> None:
    path = "src/backend/processing/tests/test_models.py"
    selection = select_test_suites.select_suites(
        select_test_suites.load_config(ROOT / "tests" / "suite-selection.toml"), [path]
    )

    assert selection.core is True
    assert selection.migrations is True
    assert selection.reasons["migrations"] == (path,)


def test_manifest_layer_patterns_keep_mixed_test_files_on_their_explicit_or_db_layer(
    tmp_path: Path,
) -> None:
    manifest = write_manifest(
        tmp_path,
        MANIFEST.replace(
            '"src/backend/*/migrations/**", "tests/test_migration_immutability.py"',
            '"src/backend/*/migrations/**", "tests/test_migration_immutability.py", '
            '"src/backend/processing/tests/test_models.py"',
        ).replace(
            'layer = "migration"',
            'layer = "migration"\n'
            'layer_patterns = ["src/backend/*/migrations/**", '
            '"tests/test_migration_immutability.py"]',
        ),
    )
    config = select_test_suites.load_config(manifest)

    assert (
        select_test_suites.layer_for_path(
            config, "src/backend/processing/tests/test_models.py", True
        )
        == "db"
    )
    assert (
        select_test_suites.layer_for_path(
            config, "src/backend/picflow/migrations/0001_initial.py", True
        )
        == "migration"
    )


def test_production_manifest_reserves_operational_and_product_flow_test_boundaries() -> None:
    config = select_test_suites.load_config(ROOT / "tests" / "suite-selection.toml")

    assert (
        select_test_suites.layer_for_path(
            config, "tests/processing/test_worker_container_contract.py", True
        )
        == "operational"
    )
    assert (
        select_test_suites.layer_for_path(config, "tests/processing/test_pipeline_e2e.py", True)
        == "product_flow"
    )
    assert (
        select_test_suites.layer_for_path(
            config, "tests/processing/test_selfie_search_e2e.py", True
        )
        == "product_flow"
    )


def init_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test User"],
    ):
        subprocess.run(command, cwd=repository, check=True)
    (repository / "docs").mkdir()
    (repository / "docs" / "guide.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, check=True)
    return repository


def test_select_command_reads_changed_paths_from_git(tmp_path: Path) -> None:
    repository = init_repository(tmp_path)
    manifest = write_manifest(repository)
    (repository / "deploy").mkdir()
    (repository / "deploy" / "apply.sh").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "deploy"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "deploy"], cwd=repository, check=True)

    result = subprocess.run(
        [
            sys.executable,
            str(Path(select_test_suites.__file__)),
            "select",
            "--config",
            str(manifest),
            "--base",
            "HEAD~1",
            "--head",
            "HEAD",
            "--format",
            "github",
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [
        "core=true",
        "operational=true",
        "migrations=false",
        "visual=false",
        "operational_reason=deploy/apply.sh",
        "migrations_reason=not selected",
        "visual_reason=not selected",
    ]


def test_fingerprint_is_canonical_across_git_representations_and_final_states(
    tmp_path: Path,
) -> None:
    repository = init_repository(tmp_path)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()
    package_file = repository / "task.txt"
    package_file.write_text("same package\n", encoding="utf-8")
    untracked = select_test_suites.fingerprint(repository, base)

    subprocess.run(["git", "add", "task.txt"], cwd=repository, check=True)
    staged = select_test_suites.fingerprint(repository, base)
    subprocess.run(["git", "commit", "-qm", "package"], cwd=repository, check=True)
    committed = select_test_suites.fingerprint(repository, base)

    assert untracked == staged == committed

    package_file.write_text("changed package\n", encoding="utf-8")
    content_changed = select_test_suites.fingerprint(repository, base)
    os.chmod(package_file, 0o755)
    mode_changed = select_test_suites.fingerprint(repository, base)
    package_file.rename(repository / "renamed-task.txt")
    path_changed = select_test_suites.fingerprint(repository, base)
    (repository / "docs" / "guide.md").unlink()
    deleted = select_test_suites.fingerprint(repository, base)

    assert committed != content_changed != mode_changed != path_changed != deleted


def test_manifest_layer_ownership_is_specific_before_db_fallback(tmp_path: Path) -> None:
    config = select_test_suites.load_config(write_manifest(tmp_path))

    assert (
        select_test_suites.layer_for_path(config, "tests/deployment/test_apply.py", False)
        == "operational"
    )
    assert (
        select_test_suites.layer_for_path(config, "tests/processing/test_upload.py", True)
        == "product_flow"
    )
    assert (
        select_test_suites.layer_for_path(config, "src/backend/picflow/tests/test_models.py", True)
        == "db"
    )
    assert select_test_suites.layer_for_path(config, "scripts/test_script.py", False) == "unit"
