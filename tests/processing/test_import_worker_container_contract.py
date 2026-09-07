"""Isolation and immutable packaging contract for the import runtime."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_import_worker_is_isolated_and_bounded():
    for filename in ("docker-compose.yml", "docker-compose.deployment.yml"):
        services = yaml.safe_load((ROOT / filename).read_text())["services"]
        worker = services["import-worker"]
        assert worker["profiles"] == ["import"]
        assert worker["read_only"] is True
        assert worker["mem_limit"] == "256m"
        assert worker["pids_limit"] == 32
        assert "size=67108864" in worker["tmpfs"][0]
        assert not worker.get("ports") and not worker.get("volumes") and not worker.get("env_file")
        assert set(worker["environment"]) == {
            "PHOTO_IMPORT_API_URL",
            "PHOTO_IMPORT_WORKER_TOKEN",
            "PHOTO_IMPORT_BUILD",
        }
        assert (
            worker["environment"]["PHOTO_IMPORT_API_URL"]
            == "http://web:8000/internal/photo-import/v1/"
        )
        for name, service in services.items():
            if name not in {"web", "import-worker"}:
                assert "PHOTO_IMPORT_WORKER_TOKEN" not in service.get("environment", {})
    dockerfile = (ROOT / "Dockerfile.import-worker").read_text()
    assert "USER importer" in dockerfile and "src/backend" not in dockerfile
    assert "manage.py" not in dockerfile
