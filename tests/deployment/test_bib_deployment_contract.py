"""The fixed remote bulk release retains the exact bib processing identity."""

from pathlib import Path

from tests.deployment.test_deployment_scripts import _apply_env, _run
from tests.deployment.test_deployment_scripts import fake_bin as fake_bin


def test_bib_remains_in_the_remote_bulk_identity_set(tmp_path: Path, fake_bin: Path):
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode == 0, result.stderr
    assert "1/bib_recognition/1" in (tmp_path / ".env").read_text()
    for name in ("PHOTO_WORKER_CPUS", "PHOTO_WORKER_MEMORY_LIMIT", "PHOTO_WORKER_REPLICAS"):
        assert name not in (tmp_path / ".env").read_text()
