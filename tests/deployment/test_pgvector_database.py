from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.operational


def test_web_release_checks_database_capability_without_replacing_database(tmp_path):
    from tests.deployment.test_deployment_scripts import _apply_env, _apply_log, _run

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    result = _run(
        "deploy/apply-deployment.sh", env=_apply_env(tmp_path, fake_bin, scenario="success")
    )
    assert result.returncode == 0, result.stderr
    commands = _apply_log(tmp_path)
    assert commands.index("candidate-vector-capability") < commands.index("candidate-migrate")
    assert not any(
        " pull db" in command or "up -d --wait --no-deps db" in command for command in commands
    )
