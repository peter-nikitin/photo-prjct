"""Single-VM worker retains the current bib processor identity."""

from pathlib import Path


def test_bib_remains_in_the_local_worker_identity_set():
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.deployment.yml").read_text()
    assert "1/bib_recognition/1" in compose
