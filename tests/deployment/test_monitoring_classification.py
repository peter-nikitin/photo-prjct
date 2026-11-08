import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def classifier():
    spec = importlib.util.spec_from_file_location(
        "release_classifier", ROOT / "deploy/classify-release.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "path",
    [
        "deploy/monitoring/prometheus/single_vm_dashboard.py",
        "deploy/monitoring/prometheus/single-vm-dashboard.json",
        "deploy/monitoring/prometheus/control.py",
        "deploy/monitoring/prometheus/environment.json",
        "deploy/monitoring/prometheus/requirements.txt",
        "deploy/monitoring/prometheus/oidc.json",
        "scripts/run-with-environment-secrets.py",
        ".github/workflows/monitoring.yml",
        "deploy/classify-release.py",
    ],
)
def test_dashboard_dependencies_select_cloud(path):
    assert classifier().classify_observability([path])["cloud_changed"]


@pytest.mark.parametrize(
    "path",
    [
        "docs/architecture.md",
        "deploy/monitoring/prometheus/README.md",
        "deploy/monitoring/prometheus/verify_metrics.py",
        "src/backend/config/metrics.py",
        "infra/ansible/roles/monitoring/files/config.yml",
        "deploy/postgres-monitoring/prepare-role.py",
    ],
)
def test_source_only_paths_do_not_select_dashboard(path):
    assert not classifier().classify_observability([path])["cloud_changed"]


def test_mixed_dashboard_and_source_changes_select_dashboard():
    assert classifier().classify_observability(
        [
            "deploy/monitoring/prometheus/single-vm-dashboard.json",
            "src/backend/config/metrics.py",
        ]
    )["cloud_changed"]
