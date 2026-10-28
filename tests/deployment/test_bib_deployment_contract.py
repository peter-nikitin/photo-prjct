"""The shared local worker can execute the current bib processor identity."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_bib_is_in_the_single_vm_worker_identity_set() -> None:
    services = yaml.safe_load((ROOT / "docker-compose.deployment.yml").read_text())["services"]
    worker = services["photo-worker"]
    identities = worker["environment"]["PHOTO_WORKER_PROCESSOR_IDENTITIES"].split(",")
    assert "1/bib_recognition/1" in identities
    assert "1/selfie_query/2" in identities
    assert worker["profiles"] == ["processing"]
