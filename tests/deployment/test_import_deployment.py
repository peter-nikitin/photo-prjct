"""Canonical deploy import stop/readiness ordering; no real host mutations."""

import json

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


def test_disabled_import_needs_no_token_and_is_not_started(tmp_path, fake_bin):
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode == 0, result.stderr
    commands = "\n".join(_apply_log(tmp_path))
    assert "label=com.docker.compose.service=import-worker" in commands
    assert "--no-deps import-worker" not in commands


def test_enabled_import_missing_token_fails_before_mutation(tmp_path, fake_bin):
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        PHOTO_IMPORT_ENABLED="True", IMPORT_WORKER_IMAGE="new-image", PHOTO_IMPORT_BUILD="test"
    )
    env.pop("PHOTO_IMPORT_WORKER_TOKEN", None)
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode != 0
    assert "PHOTO_IMPORT_WORKER_TOKEN" in result.stderr
    assert not (tmp_path / "apply.log").exists()


def test_enabled_import_checks_protocol_after_web_then_starts(tmp_path, fake_bin):
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        PHOTO_IMPORT_ENABLED="True",
        IMPORT_WORKER_IMAGE="import:new-image",
        PHOTO_IMPORT_BUILD="release",
        PHOTO_IMPORT_WORKER_TOKEN="import-secret",
    )
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode == 0, result.stderr
    commands = "\n".join(_apply_log(tmp_path))
    assert commands.index("--check-ready") < commands.index("up -d --no-deps import-worker")
    assert "import-secret" not in result.stdout + result.stderr


def test_failed_candidate_stops_import_before_restoring_web(tmp_path, fake_bin):
    env = _apply_env(tmp_path, fake_bin, scenario="public-failure")
    env.update(
        PHOTO_IMPORT_ENABLED="True",
        IMPORT_WORKER_IMAGE="import:new-image",
        PHOTO_IMPORT_BUILD="release",
        PHOTO_IMPORT_WORKER_TOKEN="import-secret",
    )
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode != 0
    commands = "\n".join(_apply_log(tmp_path))
    assert commands.rindex("label=com.docker.compose.service=import-worker") < commands.rindex(
        "up -d --remove-orphans"
    )
    assert "sleep 300" in (ROOT / "deploy/apply-deployment.sh").read_text()


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


def test_protocol_readiness_failure_restores_previous_images_without_starting_import(
    tmp_path, fake_bin
):
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(
        PHOTO_IMPORT_ENABLED="True",
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
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode != 0
    assert "Import API protocol readiness failed" in result.stderr
    assert "up -d --no-deps import-worker" not in "\n".join(_apply_log(tmp_path))
    assert (tmp_path / ".env").read_bytes() == (tmp_path / "previous-env.expected").read_bytes()


def test_disabled_import_without_image_uses_valid_compose_configuration(tmp_path, fake_bin):
    import os
    import subprocess

    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(IMPORT_WORKER_IMAGE="", PHOTO_IMPORT_ENABLED="False", PHOTO_IMPORT_WORKER_TOKEN="")
    # Read-only real Compose reproduction for the exact profile used by removal.
    script = (ROOT / "deploy/apply-deployment.sh").read_text()
    stop = script.split("stop_import_before_web_change() {", 1)[1].split("\n}", 1)[0]
    command = [
        "docker",
        "compose",
        "--env-file",
        ".env.example",
        "-f",
        "docker-compose.deployment.yml",
        "-f",
        "docker-compose.https.yml",
    ]
    if "--profile import" in stop:
        command += ["--profile", "import"]
    command += ["config", "--services"]
    actual = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, **env, "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
    )
    assert actual.returncode == 0, actual.stderr
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode == 0, result.stderr


def test_previous_enabled_import_container_is_removed_and_drained_before_web_change(
    tmp_path, fake_bin
):
    from tests.deployment.test_deployment_scripts import _write_executable

    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    with (tmp_path / ".env").open("a") as stream:
        stream.write("PHOTO_IMPORT_ENABLED=True\nIMPORT_WORKER_IMAGE=import:old-release\n")
    (tmp_path / "previous-env.expected").write_bytes((tmp_path / ".env").read_bytes())
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
        < commands.index("sleep 300")
        < commands.index("up -d --remove-orphans")
    )
    assert "yandex-disk-import" not in commands


def test_failed_import_deploy_does_not_override_operator_gate(tmp_path, fake_bin):
    env = _apply_env(tmp_path, fake_bin, scenario="public-failure")
    with (tmp_path / ".env").open("a") as stream:
        stream.write("PHOTO_IMPORT_ENABLED=True\nIMPORT_WORKER_IMAGE=import:old-release\n")
    (tmp_path / "previous-env.expected").write_bytes((tmp_path / ".env").read_bytes())
    env.update(
        PHOTO_IMPORT_ENABLED="True",
        IMPORT_WORKER_IMAGE="import:new-image",
        PHOTO_IMPORT_BUILD="release",
        PHOTO_IMPORT_WORKER_TOKEN="import-secret",
    )

    result = _run("deploy/apply-deployment.sh", env=env)

    assert result.returncode != 0
    assert "yandex-disk-import" not in "\n".join(_apply_log(tmp_path))
