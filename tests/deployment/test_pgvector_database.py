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


def test_web_release_checks_database_capability_without_replacing_database(tmp_path):
    from tests.deployment.test_deployment_scripts import _apply_env, _apply_log, _run

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    result = _run(
        "deploy/apply-deployment.sh", env=_apply_env(tmp_path, fake_bin, scenario="success")
    )
    assert result.returncode == 0, result.stderr
    commands = _apply_log(tmp_path)
    assert commands.index("candidate-vector-capability") < commands.index("candidate-migrate")
    assert not any(
        " pull db" in command or "up -d --wait --no-deps db" in command for command in commands
    )


def test_restore_tools_require_vector_extension_capability():
    text = (ROOT / "scripts/clone-deployed-db.sh").read_text()
    assert "vector.control" in text
    assert "pgvector" in text


def test_collation_capability_precedes_extension_and_database_image_retention():
    text = (ROOT / "deploy/apply-deployment.sh").read_text()
    collation = text.index("pg_database_collation_actual_version")
    assert collation < text.index("CREATE EXTENSION IF NOT EXISTS vector")
    assert "pg_collation_actual_version" in text
    assert "REFRESH COLLATION" not in text
