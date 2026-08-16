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
