"""Executable deployment resource and exact bib activation boundary."""

from pathlib import Path

import pytest
import yaml

from tests.deployment.test_deployment_scripts import (
    PREVIOUS_ENV,
    ROOT,
    _apply_env,
    _run,
)
from tests.deployment.test_deployment_scripts import (
    fake_bin as fake_bin,
)


@pytest.mark.parametrize(("cpus", "memory"), [("1.0", "2g"), ("2", "3584m"), ("2.0", "6g")])
def test_resources_are_persisted(tmp_path: Path, fake_bin: Path, cpus: str, memory: str):
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(PHOTO_WORKER_CPUS=cpus, PHOTO_WORKER_MEMORY_LIMIT=memory)
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode == 0, result.stderr
    saved = (tmp_path / ".env").read_text()
    assert f"PHOTO_WORKER_CPUS={cpus}\n" in saved
    assert f"PHOTO_WORKER_MEMORY_LIMIT={memory}\n" in saved


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PHOTO_WORKER_CPUS", "0"),
        ("PHOTO_WORKER_CPUS", "3"),
        ("PHOTO_WORKER_CPUS", "nan"),
        ("PHOTO_WORKER_MEMORY_LIMIT", "7g"),
        ("PHOTO_WORKER_MEMORY_LIMIT", "1g"),
        ("PHOTO_WORKER_MEMORY_LIMIT", "3500m"),
        ("PHOTO_WORKER_MEMORY_LIMIT", "2g\nOTHER=1"),
    ],
)
def test_invalid_resources_fail_before_mutation(
    tmp_path: Path, fake_bin: Path, name: str, value: str
):
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env[name] = value
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode == 2
    assert name in result.stderr
    assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV
    assert not (tmp_path / "apply.log").exists()


@pytest.mark.parametrize("replicas", ["1", "2"])
def test_bib_requires_one_replica(tmp_path: Path, fake_bin: Path, replicas: str):
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env["PHOTO_WORKER_PROCESSOR_IDENTITIES"] = (
        "1/capture_metadata/2,2/generate_preview/1,2/face_embedding/3,3/face_embedding/5,1/selfie_query/2,1/bib_recognition/1"
    )
    env["PHOTO_WORKER_REPLICAS"] = replicas
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode == (0 if replicas == "1" else 2), result.stderr
    if replicas == "2":
        assert "bib_recognition requires PHOTO_WORKER_REPLICAS=1" in result.stderr
        assert (tmp_path / ".env").read_bytes() == PREVIOUS_ENV


def test_resource_defaults_forwarding_and_rollback():
    worker = yaml.safe_load((ROOT / "docker-compose.deployment.yml").read_text())["services"][
        "worker"
    ]
    assert worker["cpus"] == "${PHOTO_WORKER_CPUS:-1.0}"
    assert worker["mem_limit"] == "${PHOTO_WORKER_MEMORY_LIMIT:-2g}"
    assert "bib_recognition" not in worker["environment"]["PHOTO_WORKER_PROCESSOR_IDENTITIES"]
    for variable in ("PHOTO_WORKER_CPUS", "PHOTO_WORKER_MEMORY_LIMIT"):
        assert (
            variable
            in (ROOT / "deploy/run-remote.sh")
            .read_text()
            .split("REMOTE_DEPLOYMENT_VALUES='")[1]
            .split("'\n")[0]
        )
        assert (
            variable
            in (ROOT / "deploy/cutover-compose-identity.sh")
            .read_text()
            .split("clear_candidate_compose_interpolation()")[1]
            .split("restart_source()")[0]
        )
        assert (
            variable
            in (ROOT / "deploy/apply-deployment.sh")
            .read_text()
            .split("clear_candidate_compose_interpolation()")[1]
            .split("}\n")[0]
        )
        assert f"vars.{variable}" in (ROOT / ".github/workflows/deploy.yml").read_text()


def test_resource_defaults_persist(tmp_path: Path, fake_bin: Path):
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode == 0, result.stderr
    saved = (tmp_path / ".env").read_text()
    assert "PHOTO_WORKER_CPUS=1.0\n" in saved
    assert "PHOTO_WORKER_MEMORY_LIMIT=2g\n" in saved


def test_previous_resource_limits_survive_rollback(tmp_path: Path, fake_bin: Path):
    env = _apply_env(tmp_path, fake_bin, scenario="certificate-failure")
    previous = PREVIOUS_ENV + b"PHOTO_WORKER_CPUS=2.0\nPHOTO_WORKER_MEMORY_LIMIT=3584m\n"
    (tmp_path / ".env").write_bytes(previous)
    (tmp_path / "previous-env.expected").write_bytes(previous)
    env.update(PHOTO_WORKER_CPUS="1.0", PHOTO_WORKER_MEMORY_LIMIT="2g")
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode != 0
    assert (tmp_path / ".env").read_bytes() == previous
