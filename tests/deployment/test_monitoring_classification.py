import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def classifier():
    spec = importlib.util.spec_from_file_location(
        "release_classifier", ROOT / "deploy/classify-release.py"
    )
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
        "infra/ansible/roles/monitoring/files/config.yml",
        "infra/ansible/roles/monitoring/tasks/main.yml",
        "infra/ansible/site.yml",
        "infra/terraform/iam.tf",
        "infra/terraform/storage.tf",
        "infra/ansible/roles/host/tasks/main.yml",
        ".github/workflows/deploy.yml",
        ".github/workflows/infrastructure.yml",
        "deploy/postgres-monitoring/prepare-role.py",
        "deploy/run-single-vm.py",
        "docker-compose.deployment.yml",
    ],
)
def test_collector_inputs_select_collector(path):
    assert classifier().classify_observability([path])["collector_changed"]


@pytest.mark.parametrize(
    "path",
    [
        "src/backend/config/metrics.py",
        "src/backend/config/views.py",
        "src/backend/config/urls.py",
        "src/backend/config/settings.py",
    ],
)
def test_application_metric_inputs_select_application(path):
    assert classifier().classify_observability([path])["application_changed"]


@pytest.mark.parametrize(
    "path",
    [
        "docs/architecture.md",
        "tests/monitoring/test_single_vm_monitoring.py",
        "tests/deployment/test_monitoring_classification.py",
        "deploy/monitoring/prometheus/README.md",
        "src/backend/processing/views.py",
        "infra/terraform/network.tf",
        "deploy/image-origin/monitoring/metrics.js",
    ],
)
def test_unrelated_paths_do_not_select_monitoring(path):
    assert not any(classifier().classify_observability([path]).values())


def test_release_readiness_requires_trusted_completed_source_and_real_jobs():
    decide = classifier().release_readiness
    jobs = {"deploy": "success", "monitoring": "success"}
    event = {
        "name": "Deploy",
        "event": "workflow_dispatch",
        "conclusion": "success",
        "head_branch": "main",
        "head_repository": {"full_name": "owner/repo"},
    }
    assert decide(event, "owner/repo", jobs) == "application_changed"
    assert decide(event, "owner/repo", {"deploy": "skipped", "monitoring": "skipped"}) is None
    assert decide(event, "owner/repo", {"deploy": "success", "monitoring": "failure"}) is None
    assert decide({**event, "head_branch": "feature"}, "owner/repo", jobs) is None
    assert (
        decide({**event, "head_repository": {"full_name": "fork/repo"}}, "owner/repo", jobs) is None
    )
    assert decide({**event, "event": "pull_request"}, "owner/repo", jobs) is None
    assert decide({**event, "conclusion": "failure"}, "owner/repo", jobs) is None
    infra = {**event, "name": "Infrastructure", "event": "push"}
    assert decide(infra, "owner/repo", {"release": "success"}) == "collector_changed"
    assert decide(infra, "owner/repo", {"release": "skipped"}) is None
    assert decide(infra, "owner/repo", {"application-release / monitoring": "success"}) == (
        "collector_changed"
    )
    assert decide(infra, "owner/repo", {"application-release / monitoring": "skipped"}) is None
    assert (
        decide(
            {**infra, "conclusion": "failure"},
            "owner/repo",
            {
                "application-release / deploy": "success",
                "application-release / monitoring": "failure",
            },
        )
        is None
    )
    assert decide(infra, "owner/repo", {"application-release / deploy": "success"}) == (
        "application_changed"
    )


def test_release_event_never_queries_api_for_untrusted_source(monkeypatch):
    module = classifier()
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: pytest.fail("API called"))
    run = {
        "id": 123,
        "name": "Deploy",
        "event": "workflow_dispatch",
        "conclusion": "success",
        "head_branch": "main",
        "head_repository": {"full_name": "owner/repo"},
    }
    for event in (
        {"repository": {"full_name": "fork/repo"}, "workflow_run": run},
        {"repository": {"full_name": "owner/repo"}, "workflow_run": {**run, "head_branch": "x"}},
        {"repository": {"full_name": "owner/repo"}, "workflow_run": {**run, "id": "1/../../x"}},
    ):
        assert not any(module.classify_release_event(event, "owner/repo").values())


def test_release_event_classifies_paginated_successful_jobs(monkeypatch):
    import json
    from types import SimpleNamespace

    module = classifier()
    event = {
        "repository": {"full_name": "owner/repo"},
        "workflow_run": {
            "id": 123,
            "name": "Deploy",
            "event": "workflow_dispatch",
            "conclusion": "success",
            "head_branch": "main",
            "head_repository": {"full_name": "owner/repo"},
        },
    }
    pages = [
        {"jobs": [{"name": "deploy", "conclusion": "success"}]},
        {"jobs": [{"name": "monitoring", "conclusion": "success"}]},
    ]
    called = []

    def run(command, **kwargs):
        called.append(command)
        return SimpleNamespace(stdout=json.dumps(pages))

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.classify_release_event(event, "owner/repo") == {
        "cloud_changed": False,
        "collector_changed": False,
        "application_changed": True,
    }
    assert called[0][-1] == "repos/owner/repo/actions/runs/123/jobs"


def test_mixed_release_retains_all_independent_classification_flags():
    selected = classifier().classify_observability(
        [
            "deploy/monitoring/prometheus/single-vm-dashboard.json",
            "infra/ansible/roles/monitoring/files/config.yml",
            "src/backend/config/metrics.py",
        ]
    )
    assert selected == {
        "cloud_changed": True,
        "collector_changed": True,
        "application_changed": True,
    }


@pytest.mark.parametrize("path", ["src/backend/entrypoint.sh", "src/backend/config/gunicorn.py"])
def test_metric_runtime_changes_defer_mixed_dashboard_release(path):
    selected = classifier().classify_observability(
        ["deploy/monitoring/prometheus/single-vm-dashboard.json", path]
    )
    assert selected == {
        "cloud_changed": True,
        "collector_changed": False,
        "application_changed": True,
    }
