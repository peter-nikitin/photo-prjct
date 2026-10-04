"""Canonical deploy import stop/readiness ordering; no real host mutations."""

import json
import re
import runpy
from unittest.mock import Mock, patch

import pytest

from tests.deployment.test_deployment_scripts import (  # noqa: F401
    ROOT,
    _apply_env,
    _apply_log,
    _run,
)


@pytest.fixture
def fake_bin(tmp_path):
    path = tmp_path / "bin"
    path.mkdir()
    return path


def test_canonical_deployment_always_starts_import_and_processing_without_retired_settings(
    tmp_path, fake_bin
):
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        IMPORT_WORKER_IMAGE="import:new-image",
        PHOTO_IMPORT_BUILD="release",
        PHOTO_IMPORT_WORKER_TOKEN="import-secret",
        PRIVATE_MEDIA_ALLOWED_ORIGINS="https://findme-photo.ru",
        SELFIE_FEEDBACK_S3_BUCKET="feedback-bucket",
        SELFIE_FEEDBACK_S3_ACCESS_KEY_ID="feedback-access",
        SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY="feedback-secret",
        SELFIE_FEEDBACK_KMS_KEY_ID="feedback-kms",
        SELFIE_FEEDBACK_STORAGE_PREFLIGHT_CONFIRMED="True",
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    generated_env = (tmp_path / ".env").read_text()
    retired = (
        "PHOTO_UPLOAD_ENABLED",
        "PHOTO_IMPORT_ENABLED",
        "PHOTO_PROCESSING_ENABLED",
        "PHOTO_PROCESSING_FACE_ENABLED",
        "PHOTO_PROCESSING_PREVIEW_ENABLED",
        "SELFIE_FEEDBACK_ENABLED",
    )
    for name in retired:
        assert not re.search(rf"^{name}=", generated_env, re.MULTILINE)
        for source in (
            ROOT / "deploy/apply-deployment.sh",
            ROOT / "deploy/run-remote.sh",
            ROOT / "deploy/verify-selfie-observability.sh",
            ROOT / "docker-compose.deployment.yml",
            ROOT / ".github/workflows/deploy.yml",
        ):
            assert name not in source.read_text(), source
    commands = "\n".join(_apply_log(tmp_path))
    assert "up -d --no-deps web nginx" in commands
    assert "up -d --no-deps import-worker" in commands
    assert "findme-worker-pool-metrics verify" in commands
    assert "--entrypoint python web manage.py check" in commands
    assert commands.index("manage.py check") < commands.index("manage.py migrate")


def test_import_acceptance_candidate_fixtures_do_not_toggle_retired_gate():
    acceptance = ROOT / "tests/deployment/import_acceptance"
    for name in ("seed.py", "verify.py"):
        source = (acceptance / name).read_text()
        assert "yandex-disk-import" not in source
        assert "FeatureFlag" not in source
    old_probe = (acceptance / "old_probe.py").read_text()
    assert 'FeatureFlag.objects.filter(key="yandex-disk-import").exists()' in old_probe
    assert 'FeatureFlag.objects.get(key="yandex-disk-import")' not in old_probe


def test_import_acceptance_verifier_requires_exact_permanent_processing_enrollment(capsys):
    photo = Mock(original_size=52_428_800, original_key="fixture-private-original")
    photo.processing_states.values_list.return_value = [
        "capture_metadata",
        "generate_preview",
        "face_embedding",
    ]
    pending_item = Mock()
    batch = Mock(jpeg_count=100)
    batch.items.count.return_value = 100
    batch.manifest_pages.count.return_value = 2
    batch.items.get.return_value = Mock(photo=photo)
    batch.items.filter.return_value.first.return_value = pending_item

    with (
        patch("ingestion.models.ImportBatch.objects.get", return_value=batch),
        patch("ingestion.models.ImportAttempt.objects.create") as create_attempt,
        patch("ingestion.storage.PrivateUploadStorage") as storage,
    ):
        storage.return_value.inspect.return_value.size = 52_428_800
        runpy.run_path(str(ROOT / "tests/deployment/import_acceptance/verify.py"))
        for invalid_states in (
            ["capture_metadata", "generate_preview"],
            ["capture_metadata", "generate_preview", "face_embedding", "unexpected"],
        ):
            photo.processing_states.values_list.return_value = invalid_states
            with pytest.raises(AssertionError):
                runpy.run_path(str(ROOT / "tests/deployment/import_acceptance/verify.py"))

    assert json.loads(capsys.readouterr().out) == {
        "persisted_manifest_items": 100,
        "persisted_manifest_pages": 2,
        "imported_photos": 1,
        "private_original_bytes": 52_428_800,
        "processing_enrolled": True,
        "interrupted_attempt_retained": True,
    }
    create_attempt.assert_called_once()
    assert create_attempt.call_args.kwargs["batch"] is batch
    assert create_attempt.call_args.kwargs["item"] is pending_item
    assert create_attempt.call_args.kwargs["kind"] == "file"


def test_candidate_web_configuration_check_fails_before_mutation(tmp_path, fake_bin):
    env = _apply_env(tmp_path, fake_bin, scenario="web-check-failure")

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert "Candidate web configuration check failed" in result.stderr
    assert "candidate-web-check" in _apply_log(tmp_path)
    assert not any("observability-install" in command for command in _apply_log(tmp_path))


def test_import_missing_token_fails_before_mutation(tmp_path, fake_bin):
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(IMPORT_WORKER_IMAGE="import:new-image", PHOTO_IMPORT_BUILD="test")
    env.pop("PHOTO_IMPORT_WORKER_TOKEN", None)
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode != 0
    assert "PHOTO_IMPORT_WORKER_TOKEN" in result.stderr
    assert not (tmp_path / "apply.log").exists()


def test_import_checks_protocol_after_web_then_starts(tmp_path, fake_bin):
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        IMPORT_WORKER_IMAGE="import:new-image",
        PHOTO_IMPORT_BUILD="release",
        PHOTO_IMPORT_WORKER_TOKEN="import-secret",
    )
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode == 0, result.stderr
    commands = "\n".join(_apply_log(tmp_path))
    assert commands.index("--check-ready") < commands.index("up -d --no-deps import-worker")
    assert "import-secret" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "lease_result", ["0\n6\n0\n", "0\nerror\n"], ids=["lease-expiry", "probe-failure"]
)
def test_failed_candidate_stops_import_before_stopping_web_for_forward_recovery(
    tmp_path, fake_bin, lease_result
):
    from tests.deployment.test_deployment_scripts import _write_executable

    env = _apply_env(tmp_path, fake_bin, scenario="public-failure")
    env.update(
        IMPORT_WORKER_IMAGE="import:new-image",
        PHOTO_IMPORT_BUILD="release",
        PHOTO_IMPORT_WORKER_TOKEN="import-secret",
    )
    docker = fake_bin / "docker"
    docker.write_text(
        docker.read_text().replace(
            "set -eu",
            'set -eu\ncase "$*" in *"label=com.docker.compose.service=import-worker"*) '
            'printf "import-fixture-id\\n" ;; esac',
            1,
        )
    )
    _write_executable(fake_bin / "sleep", 'printf "sleep %s\\n" "$*" >> "$COMMAND_LOG"')
    probe_file = tmp_path / "import-lease-probes"
    probe_file.write_text(lease_result)
    env["IMPORT_LEASE_PROBE_FILE"] = str(probe_file)
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode != 0
    commands = "\n".join(_apply_log(tmp_path))
    started = commands.index("up -d --no-deps import-worker")
    stopped_import = commands.rindex("rm -f import-fixture-id")
    lease_probe = commands.rindex("import-lease-probe")
    stopped_web = commands.rindex(" stop web")
    assert started < stopped_import < lease_probe < stopped_web
    assert commands.count("up -d --no-deps import-worker") == 1
    assert commands.count("up -d --no-deps web nginx") == 1
    assert "previous-web-processing-schema-probe" not in commands
    assert "phase=public-health rollback=failed" in result.stdout
    assert (tmp_path / ".deployment-recovery/candidate.env").read_bytes() == (
        tmp_path / ".env"
    ).read_bytes()
    assert (tmp_path / ".deployment-recovery/previous.env").is_file()
    if "error" in lease_result:
        assert "Forward recovery could not drain candidate import leases" in result.stderr
    else:
        assert _apply_log(tmp_path).count("import-lease-probe") == 3
        assert commands.index("sleep 5", stopped_import) < lease_probe


def test_import_token_projection_is_optional_and_edge_denies_internal_api():
    manifest = json.loads((ROOT / "deploy/environment-secrets.json").read_text())
    token = next(e for e in manifest["entries"] if e["key"] == "PHOTO_IMPORT_WORKER_TOKEN")
    assert token["required"] is False
    assert {name for name, keys in manifest["consumers"].items() if token["key"] in keys} == {
        "local-web",
        "deploy",
    }
    assert (
        "location ^~ /internal/photo-import/ {\n        return 404;"
        in (ROOT / "deploy/nginx/https.conf.template").read_text()
    )


def test_protocol_readiness_failure_keeps_candidate_without_starting_import(tmp_path, fake_bin):
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        IMPORT_WORKER_IMAGE="import:new-image",
        PHOTO_IMPORT_BUILD="release",
        PHOTO_IMPORT_WORKER_TOKEN="import-secret",
    )
    docker = fake_bin / "docker"
    docker.write_text(
        docker.read_text().replace(
            "set -eu", 'set -eu\ncase "$*" in *--check-ready*) exit 1 ;; esac', 1
        )
    )
    probe_file = tmp_path / "import-lease-probes"
    probe_file.write_text("0\n")
    env["IMPORT_LEASE_PROBE_FILE"] = str(probe_file)
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode != 0
    assert "Import API protocol readiness failed" in result.stderr
    assert "up -d --no-deps import-worker" not in "\n".join(_apply_log(tmp_path))
    assert _apply_log(tmp_path).count("import-lease-probe") == 1
    commands = "\n".join(_apply_log(tmp_path))
    assert commands.count("up -d --no-deps web nginx") == 1
    assert " stop web" in commands
    assert "previous-web-processing-schema-probe" not in commands
    assert (tmp_path / ".env").read_bytes() != (tmp_path / "previous-env.expected").read_bytes()
    assert (tmp_path / ".deployment-recovery/candidate.env").read_bytes() == (
        tmp_path / ".env"
    ).read_bytes()


def test_missing_import_image_fails_before_mutation(tmp_path, fake_bin):
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env["IMPORT_WORKER_IMAGE"] = ""
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode == 2
    assert "IMPORT_WORKER_IMAGE" in result.stderr
    assert not (tmp_path / "apply.log").exists()


def test_previous_enabled_import_container_with_no_live_lease_proceeds_without_sleep(
    tmp_path, fake_bin
):
    from tests.deployment.test_deployment_scripts import _write_executable

    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    docker = fake_bin / "docker"
    docker.write_text(
        docker.read_text().replace(
            "set -eu",
            'set -eu\ncase "$*" in *"label=com.docker.compose.service=import-worker"*) '
            'printf "import-fixture-id\\n" ;; esac',
            1,
        )
    )
    _write_executable(fake_bin / "sleep", 'printf "sleep %s\\n" "$*" >> "$COMMAND_LOG"')
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode == 0, result.stderr
    commands = "\n".join(_apply_log(tmp_path))
    assert (
        commands.index("rm -f import-fixture-id")
        < commands.index("import-lease-probe")
        < commands.index("up -d --no-deps web nginx")
    )
    assert (
        "sleep "
        not in commands[
            commands.index("rm -f import-fixture-id") : commands.index("up -d --no-deps web nginx")
        ]
    )
    assert "yandex-disk-import" not in commands


def test_previous_enabled_import_waits_only_while_a_lease_is_live(tmp_path, fake_bin):
    from tests.deployment.test_deployment_scripts import _write_executable

    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    with (tmp_path / ".env").open("a") as stream:
        stream.write("IMPORT_WORKER_IMAGE=import:old-release\n")
    (tmp_path / "previous-env.expected").write_bytes((tmp_path / ".env").read_bytes())
    probe_file = tmp_path / "import-lease-probes"
    probe_file.write_text("6\n0\n")
    env["IMPORT_LEASE_PROBE_FILE"] = str(probe_file)
    _write_executable(fake_bin / "sleep", 'printf "sleep %s\\n" "$*" >> "$COMMAND_LOG"')

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode == 0, result.stderr
    commands = "\n".join(_apply_log(tmp_path))
    assert commands.index("import-lease-probe") < commands.index("sleep 5")
    assert commands.index("sleep 5") < commands.index(
        "import-lease-probe", commands.index("sleep 5")
    )
    assert commands.index("import-lease-probe", commands.index("sleep 5")) < commands.index(
        "up -d --no-deps web nginx"
    )


def test_import_lease_probe_failure_blocks_web_replacement(tmp_path, fake_bin):
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    with (tmp_path / ".env").open("a") as stream:
        stream.write("IMPORT_WORKER_IMAGE=import:old-release\n")
    (tmp_path / "previous-env.expected").write_bytes((tmp_path / ".env").read_bytes())
    probe_file = tmp_path / "import-lease-probes"
    probe_file.write_text("error\nerror\n")
    env["IMPORT_LEASE_PROBE_FILE"] = str(probe_file)

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert "Import worker stop failed" in result.stderr
    assert "up -d --no-deps web nginx" not in "\n".join(_apply_log(tmp_path))


def test_failed_candidate_before_import_start_needs_no_lease_probe(tmp_path, fake_bin):
    env = _apply_env(tmp_path, fake_bin, scenario="vector-capability-failure")
    env.update(
        IMPORT_WORKER_IMAGE="import:new-image",
        PHOTO_IMPORT_BUILD="release",
        PHOTO_IMPORT_WORKER_TOKEN="import-secret",
    )
    probe_file = tmp_path / "import-lease-probes"
    probe_file.write_text("0\n")
    env["IMPORT_LEASE_PROBE_FILE"] = str(probe_file)

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert "Vector database capability preflight failed" in result.stderr
    assert "import-lease-probe" in "\n".join(_apply_log(tmp_path))
    assert (tmp_path / ".env").read_bytes() == (tmp_path / "previous-env.expected").read_bytes()


def test_failed_import_deploy_does_not_override_operator_gate(tmp_path, fake_bin):
    env = _apply_env(tmp_path, fake_bin, scenario="public-failure")
    env.update(
        IMPORT_WORKER_IMAGE="import:new-image",
        PHOTO_IMPORT_BUILD="release",
        PHOTO_IMPORT_WORKER_TOKEN="import-secret",
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert "yandex-disk-import" not in "\n".join(_apply_log(tmp_path))
