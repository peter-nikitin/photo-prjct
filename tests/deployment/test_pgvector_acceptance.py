"""Deployment database shared-memory contract."""

import pytest


@pytest.mark.operational
def test_local_and_deployment_database_allow_concurrent_exact_search_shared_memory():
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    for filename in ("docker-compose.yml", "docker-compose.deployment.yml"):
        db = yaml.safe_load((root / filename).read_text())["services"]["db"]
        assert db["shm_size"] == "256m"
        assert "/var/lib/postgresql/data" in str(db["volumes"])
