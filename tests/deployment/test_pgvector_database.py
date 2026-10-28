from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = (
    "pgvector/pgvector:0.8.6-pg16-trixie@sha256:"
    "c8483555ce48101872f888c1df8a895ff689d6c7c7a5f7ac266475f9dfe89e0b"
)
pytestmark = pytest.mark.operational


@pytest.mark.parametrize(
    "path",
    [
        "docker-compose.yml",
        "docker-compose.deployment.yml",
        "docker-compose.visual.yml",
        "docker-compose.bib-local.yml",
        ".github/workflows/ci.yml",
    ],
)
def test_database_targets_pin_vector_capable_postgresql_16(path):
    text = (ROOT / path).read_text()
    assert IMAGE in text
    assert "postgres:16" not in text


def test_restore_tools_require_vector_extension_capability():
    text = (ROOT / "scripts/clone-deployed-db.sh").read_text()
    assert "vector.control" in text
    assert "pgvector" in text
