"""Local OCR replicas share an isolated visual validation service."""

from pathlib import Path

from tests.deployment.compose_config import render_compose


def test_bib_worker_uses_its_own_internal_validation_service(tmp_path: Path) -> None:
    compose = render_compose(
        tmp_path,
        "docker-compose.yml",
        profiles=("worker",),
        environment={
            "PHOTO_WORKER_BULK_PROCESSOR_IDENTITIES": "",
            "PHOTO_WORKER_BULK_PROCESSOR_TYPES": "",
        },
    )
    services = compose["services"]
    worker = services["worker-bib"]
    validator = services["bib-validator"]
    assert worker["environment"]["PHOTO_WORKER_PROCESSOR_IDENTITIES"] == "1/bib_recognition/1"
    assert worker["environment"]["PHOTO_WORKER_PROCESSOR_TYPES"] == "bib_recognition"
    assert worker["environment"]["PHOTO_WORKER_BIB_VALIDATOR_URL"] == "http://bib-validator:8180"
    assert worker["depends_on"]["bib-validator"]["condition"] == "service_healthy"
    assert "bib-validation" in worker["networks"]
    assert set(validator["networks"]) == {"bib-validation"}
    assert compose["networks"]["bib-validation"]["internal"] is True
    for field in ("ports", "environment", "env_file", "volumes"):
        assert field not in validator
