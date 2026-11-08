#!/usr/bin/env python3
"""Select publication from effective Docker and canonical deployment inputs."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

CANONICAL = {
    "docker-compose.deployment.yml",
    "deploy/bootstrap-selfie-observability.sh",
    "deploy/configure-monitoring-agent.sh",
    "deploy/run-commerce-worker-health.sh",
    "deploy/verify-selfie-observability.sh",
}


def _observability_only(path):
    """Keep non-image operational files out of the existing component classifier."""
    return (
        path in CANONICAL
        or path
        in {
            "scripts/monitor_public_health.py",
            "scripts/monitor_commerce.py",
            "deploy/monitoring/merge_native_agent.py",
            "deploy/monitoring/unified-agent.yml.template",
            "deploy/worker-pools/metrics.py",
            "deploy/worker-pools/metrics.service",
            "deploy/worker-pools/metrics.timer",
            "deploy/worker-pools/metrics-root-helper.sh",
            ".github/workflows/deploy-image-origin.yml",
            ".github/workflows/monitoring.yml",
        }
        or path.startswith(
            (
                "deploy/selfie-observability/",
                "deploy/monitoring/commerce-vm/",
                "deploy/postgres-monitoring/",
                "deploy/image-origin/monitoring/",
            )
        )
        or path.startswith("deploy/monitoring/prometheus/")
    )


DASHBOARD_INPUTS = {
    ".github/workflows/monitoring.yml",
    "deploy/classify-release.py",
    "deploy/monitoring/prometheus/single_vm_dashboard.py",
    "deploy/monitoring/prometheus/single-vm-dashboard.json",
    "deploy/monitoring/prometheus/control.py",
    "deploy/monitoring/prometheus/environment.json",
    "deploy/monitoring/prometheus/requirements.txt",
    "deploy/monitoring/prometheus/oidc.json",
    "scripts/run-with-environment-secrets.py",
}
COLLECTOR_INPUTS = {
    "infra/ansible/site.yml",
    "infra/terraform/iam.tf",
    "infra/terraform/compute.tf",
    "infra/terraform/storage.tf",
    "infra/ansible/roles/host/tasks/main.yml",
    ".github/workflows/infrastructure.yml",
    ".github/workflows/deploy.yml",
    "deploy/environment-secrets.json",
    "deploy/run-single-vm.py",
    "deploy/run-remote.sh",
    "deploy/apply-deployment.sh",
    "docker-compose.deployment.yml",
}
APPLICATION_INPUTS = {
    "src/backend/config/metrics.py",
    "src/backend/config/gunicorn.py",
    "src/backend/entrypoint.sh",
    "src/backend/config/views.py",
    "src/backend/config/urls.py",
    "src/backend/config/settings.py",
    "src/backend/requirements.txt",
    "Dockerfile",
}


def classify_observability(paths):
    return {
        "cloud_changed": any(path in DASHBOARD_INPUTS for path in paths),
        "collector_changed": any(
            path in COLLECTOR_INPUTS
            or path.startswith(("infra/ansible/roles/monitoring/", "deploy/postgres-monitoring/"))
            and not path.endswith(".md")
            for path in paths
        ),
        "application_changed": any(path in APPLICATION_INPUTS for path in paths),
    }


def trusted_release(run, repository):
    return (
        run.get("conclusion") == "success"
        and run.get("head_branch") == "main"
        and (run.get("head_repository") or {}).get("full_name") == repository
        and (run.get("name"), run.get("event"))
        in {("Infrastructure", "push"), ("Deploy", "workflow_dispatch")}
    )


def release_readiness(run, repository, jobs):
    if not trusted_release(run, repository):
        return None
    if run["name"] == "Infrastructure":
        if jobs.get("application-release / deploy") == "success":
            return "application_changed"
        if jobs.get("application-release / monitoring") == "success":
            return "collector_changed"
        if jobs.get("release") == "success":
            return "collector_changed"
    if run["name"] == "Deploy" and all(
        jobs.get(name) == "success" for name in ("deploy", "monitoring")
    ):
        return "application_changed"
    return None


def classify_release_event(event, repository):
    selected = classify_observability([])
    run = event.get("workflow_run") or {}
    run_id = str(run.get("id", ""))
    if (
        (event.get("repository") or {}).get("full_name") != repository
        or not trusted_release(run, repository)
        or not re.fullmatch(r"[0-9]{1,20}", run_id)
    ):
        return selected
    result = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", f"repos/{repository}/actions/runs/{run_id}/jobs"],
        check=True,
        text=True,
        capture_output=True,
    )
    pages = json.loads(result.stdout)
    jobs = {}
    for page in pages:
        for job in page["jobs"]:
            name = job["name"]
            if name in jobs:
                raise ValueError("duplicate job name")
            jobs[name] = job["conclusion"]
    readiness = release_readiness(run, repository, jobs)
    if readiness:
        selected[readiness] = True
    return selected


def classify(paths):
    web = worker = base = importer = commerce = False
    terraform = ansible = exporter = False
    for path in paths:
        if path == "docker-compose.deployment.yml" or (
            path.startswith("deploy/postgres-monitoring/") and not path.endswith(".md")
        ):
            exporter = True
        if path.startswith(("infra/terraform/", "infra/ci/")) or path in {
            ".github/workflows/infrastructure.yml",
            "deploy/classify-release.py",
        }:
            terraform = True
            continue
        if path.startswith("infra/ansible/"):
            ansible = True
            continue
        if path.startswith("infra/"):
            continue
        if path.startswith(("docs/", "tests/", ".agents/", ".superpowers/")) or path.endswith(
            ".md"
        ):
            continue
        if _observability_only(path) and path != "docker-compose.deployment.yml":
            continue
        if path in {".github/workflows/deploy.yml", "deploy/run-remote.sh"}:
            # Orchestration is consumed by the next selected release; it is not an image input.
            continue
        if path == ".dockerignore":
            web = worker = base = importer = commerce = True
        elif path == "Dockerfile.worker-base" or path in {
            "src/worker/requirements.txt",
            "src/worker/requirements.cpu.txt",
        }:
            worker = base = True
        elif path == "Dockerfile.worker" or path.startswith("src/worker/photo_worker/"):
            worker = True
        elif path == "Dockerfile.import-worker" or path.startswith("src/import_worker/"):
            importer = True
        elif path.startswith("src/backend/") and ("/templates/" in path or "/static/" in path):
            web = True
        elif path.startswith("src/backend/commerce/"):
            web = commerce = True
        elif (
            path == "Dockerfile"
            or path.startswith(("src/backend/", "deploy/"))
            or path.startswith("docker-compose")
            or path == ".env.example"
        ):
            web = True
            if (
                path == "Dockerfile"
                or path.startswith(("src/backend/config/", "src/backend/picflow/", "deploy/"))
                or path in {"src/backend/requirements.txt", ".env.example"}
                or path.startswith("docker-compose")
            ):
                commerce = True
            if path.startswith(("deploy/", "docker-compose")) or path == ".env.example":
                importer = True
        elif not path.startswith(("src/worker/tests/", ".github/")):
            # Unknown operational/configuration inputs must not silently look like documentation.
            web = True
    return {
        "web_changed": web,
        "worker_changed": worker,
        "worker_base_changed": base,
        "import_changed": importer,
        "commerce_changed": commerce,
        "terraform_changed": terraform,
        "ansible_changed": ansible,
        "exporter_changed": exporter,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--observability", action="store_true")
    parser.add_argument("--monitoring-release-event", action="store_true")
    args = parser.parse_args()
    if args.monitoring_release_event:
        if args.base or args.head or args.observability:
            parser.error("release event cannot be combined with a diff")
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        selected = classify_release_event(event, os.environ["GITHUB_REPOSITORY"])
    elif not args.base or not args.head:
        parser.error("--base and --head are required")
    elif args.base == "0" * 40:
        # An initial push has no predecessor; publish both complete components explicitly.
        keys = classify_observability([]) if args.observability else classify([])
        selected = dict.fromkeys(keys, True)
    else:
        paths = subprocess.run(
            ["git", "diff", "--name-only", args.base, args.head],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.splitlines()
        selected = classify_observability(paths) if args.observability else classify(paths)
    for key, value in selected.items():
        print(f"{key}={str(value).lower()}")


if __name__ == "__main__":
    main()
