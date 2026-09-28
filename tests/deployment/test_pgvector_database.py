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


def test_deployment_preflight_precedes_migration_and_pins_rollback_database():
    text = (ROOT / "deploy/apply-deployment.sh").read_text()
    assert text.index("phase vector-database-preflight") < text.index("manage.py migrate --noinput")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in text
    assert "extversion" in text
    assert IMAGE in text
    assert "retain_vector_database_image || return 1" in text
    assert "run --rm --no-deps -T --entrypoint python web manage.py migrate" in text


def test_restore_tools_require_vector_extension_capability():
    text = (ROOT / "scripts/clone-deployed-db.sh").read_text()
    assert "vector.control" in text
    assert "pgvector" in text


def test_compose_identity_cutover_uses_vector_capable_archive_inspector():
    text = (ROOT / "deploy/cutover-compose-identity.sh").read_text()
    assert IMAGE in text
    assert "postgres:16" not in text


def test_collation_capability_precedes_extension_and_database_image_retention():
    text = (ROOT / "deploy/apply-deployment.sh").read_text()
    collation = text.index("pg_database_collation_actual_version")
    assert collation < text.index("vector_database_reconciled=1")
    assert collation < text.index("CREATE EXTENSION IF NOT EXISTS vector")
    assert "pg_collation_actual_version" in text
    assert "REFRESH COLLATION" not in text
