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


def test_import_token_is_optional_and_limited_to_application_consumers():
    manifest = json.loads((ROOT / "deploy/environment-secrets.json").read_text())
    token = next(
        entry for entry in manifest["entries"] if entry["key"] == "PHOTO_IMPORT_WORKER_TOKEN"
    )
    assert token["required"] is False
    assert token["key"] in manifest["consumers"]["deploy"]
    assert {name for name, keys in manifest["consumers"].items() if token["key"] in keys} <= {
        "local-web",
        "deploy",
    }


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
