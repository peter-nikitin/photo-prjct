from __future__ import annotations

import os
import subprocess

import yaml

from tests.deployment.test_deployment_scripts import ROOT


def test_local_adaface_compose_isolated_runtime_contract() -> None:
    environment = {
        **os.environ,
        "COMPOSE_PROJECT_NAME": "photo-adaface-contract",
        "ADAFACE_DB_HOST_PORT": "15433",
        "ADAFACE_WEB_HOST_PORT": "18080",
        "ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD": "0.42",
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.adaface-local.yml",
            "--profile",
            "manual-seed",
            "--profile",
            "worker",
            "config",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    compose = yaml.safe_load(result.stdout)
    assert compose["name"] == "photo-adaface-contract"
    db_port = compose["services"]["db"]["ports"]
    web_port = compose["services"]["web"]["ports"]
    assert len(db_port) == 1
    assert db_port[0]["host_ip"] == "127.0.0.1"
    assert db_port[0]["published"] == "15433"
    assert db_port[0]["target"] == 5432
    assert len(web_port) == 1
    assert web_port[0]["host_ip"] == "127.0.0.1"
    assert web_port[0]["published"] == "18080"
    assert web_port[0]["target"] == 8000
    assert "ports" not in compose["services"]["minio"]
    assert compose["services"]["web"]["depends_on"]["minio-init"] == {
        "condition": "service_completed_successfully",
        "required": True,
    }
    assert compose["services"]["minio"]["healthcheck"]
    web_environment = compose["services"]["web"]["environment"]
    assert "MONITORING_ENVIRONMENT" not in (ROOT / "docker-compose.adaface-local.yml").read_text(
        encoding="utf-8"
    )
    assert web_environment["ADAFACE_LOCAL_EXPERIMENT_ENABLED"] == "True"
    assert web_environment["ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD"] == "0.42"
    worker_environment = compose["services"]["worker"]["environment"]
    assert worker_environment["PHOTO_WORKER_ALLOW_INSECURE_LOCAL_MINIO"] == "true"
    assert worker_environment["PHOTO_WORKER_PROCESSOR_IDENTITIES"] == (
        "3/face_embedding/5,1/selfie_query/2"
    )
    assert "PHOTO_WORKER_PROCESSOR_TYPE" not in worker_environment
    assert "PHOTO_WORKER_PROCESSOR_TYPES" not in worker_environment
    assert (
        "PHOTO_WORKER_ALLOW_INSECURE_LOCAL_MINIO" not in (ROOT / "docker-compose.yml").read_text()
    )
    seeder = compose["services"]["seed-local-preview-corpus"]
    assert seeder["profiles"] == ["manual-seed"]
    assert seeder["entrypoint"] == ["python", "manage.py"]
    assert "--apply" not in seeder["command"]
    corpus_mount = compose["services"]["seed-local-preview-corpus"]["volumes"]
    assert len(corpus_mount) == 1
    assert corpus_mount[0]["type"] == "bind"
    assert corpus_mount[0]["source"] == (
        "/Users/petrnikitin/Documents/Projects/photo-prjct-private/event-corpora/"
        "cyclingrace-vechernee-sadovoe/previews/preview-small-v1"
    )
    assert corpus_mount[0]["target"] == "/corpus"
    assert corpus_mount[0]["read_only"] is True
    assert all(
        volume["name"].startswith("photo-adaface-contract_")
        for volume in compose["volumes"].values()
    )


def test_local_adaface_compose_requires_an_explicit_distance_threshold() -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != "ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD"
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.adaface-local.yml",
            "config",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD" in result.stderr


def test_base_and_production_compose_ship_adaface_without_local_only_gates() -> None:
    for compose_path in (ROOT / "docker-compose.yml", ROOT / "docker-compose.deployment.yml"):
        content = compose_path.read_text(encoding="utf-8")
        assert "3/face_embedding/5" in content
        assert "1/selfie_query/2" in content
        assert "ADAFACE_LOCAL_EXPERIMENT_ENABLED" not in content
        assert "ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD" not in content
