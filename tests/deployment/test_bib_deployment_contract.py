"""The regular local upload worker claims bib recognition jobs by default."""

from pathlib import Path

from tests.deployment.compose_config import render_compose


def test_bib_remains_in_the_local_worker_identity_set(tmp_path: Path) -> None:
    compose = render_compose(
        tmp_path,
        "docker-compose.yml",
        profiles=("worker",),
        environment={
            "PHOTO_WORKER_BULK_PROCESSOR_IDENTITIES": "",
            "PHOTO_WORKER_BULK_PROCESSOR_TYPES": "",
        },
    )
    worker = compose["services"]["worker-bulk"]["environment"]
    assert "1/bib_recognition/1" in worker["PHOTO_WORKER_PROCESSOR_IDENTITIES"].split(",")
    assert "bib_recognition" in worker["PHOTO_WORKER_PROCESSOR_TYPES"].split(",")
