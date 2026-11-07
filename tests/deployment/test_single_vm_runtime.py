from pathlib import Path

from tests.deployment.compose_config import render_compose


def test_single_vm_services_keep_workers_and_database_private(tmp_path: Path) -> None:
    default = render_compose(tmp_path / "default", "docker-compose.deployment.yml")
    services = default["services"]
    assert set(services) == {"db", "web", "nginx"}
    assert "ports" not in services["db"]
    assert services["db"]["volumes"][0]["source"] == "/var/lib/findme-photo/postgres"
    assert "ports" not in services["web"]
    assert services["nginx"]["ports"][0]["published"] == "80"
    assert services["nginx"]["image"] == (
        "nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10"
    )

    all_services = render_compose(
        tmp_path / "workers",
        "docker-compose.deployment.yml",
        profiles=("photo-worker", "import", "commerce"),
        environment={"PHOTO_WORKER_IMAGE": "photo-worker", "IMPORT_WORKER_IMAGE": "import-worker"},
    )["services"]
    assert all_services["photo-worker"]["environment"]["PHOTO_WORKER_API_URL"].startswith(
        "http://web:8000/"
    )
    assert all_services["import-worker"]["environment"]["PHOTO_IMPORT_API_URL"].startswith(
        "http://web:8000/"
    )
    assert "DB_PASSWORD" not in all_services["photo-worker"]["environment"]


def test_public_edge_denies_worker_and_diagnostic_routes() -> None:
    config = (Path(__file__).resolve().parents[2] / "deploy/nginx/http.conf").read_text()
    assert "location ^~ /internal/ { return 404; }" in config
    assert "location = /metrics/ { return 404; }" in config
    assert "location ^~ /worker-diagnostics/ { return 404; }" in config
