#!/usr/bin/env python3
"""Select publication from effective Docker and canonical deployment inputs."""

import argparse
import subprocess

HOST_SHARED = {
    "deploy/monitoring/prometheus/exporter.py",
    "deploy/monitoring/prometheus/install.py",
    "deploy/monitoring/prometheus/render_agent.py",
    "deploy/observability/reconcile.py",
    "deploy/classify-release.py",
}
CANONICAL = {
    "docker-compose.deployment.yml",
    "deploy/bootstrap-selfie-observability.sh",
    "deploy/configure-monitoring-agent.sh",
    "deploy/run-commerce-worker-health.sh",
    "deploy/verify-selfie-observability.sh",
}


def classify_observability(paths):
    selected = dict.fromkeys(
        (
            "cloud_changed",
            "canonical_changed",
            "public_changed",
            "probe_changed",
            "image_monitoring_changed",
            "application_changed",
        ),
        False,
    )
    for path in paths:
        if path.endswith(".md") or path.startswith(
            ("docs/", "tests/", ".agents/", ".superpowers/")
        ):
            continue
        if path in HOST_SHARED or path in {
            "scripts/monitor_public_health.py",
            "scripts/monitor_commerce.py",
        }:
            selected["canonical_changed"] = selected["public_changed"] = True
        if (
            path in CANONICAL
            or path.startswith(
                (
                    "deploy/selfie-observability/",
                    "deploy/monitoring/commerce-vm/",
                    "deploy/postgres-monitoring/",
                )
            )
            or path
            in {
                "deploy/monitoring/merge_native_agent.py",
                "deploy/monitoring/unified-agent.yml.template",
                "deploy/monitoring/prometheus/units/findme-prometheus-canonical.service",
                "deploy/worker-pools/metrics.py",
                "deploy/worker-pools/metrics.service",
                "deploy/worker-pools/metrics.timer",
                "deploy/worker-pools/metrics-root-helper.sh",
            }
        ):
            selected["canonical_changed"] = True
        if path == "deploy/monitoring/prometheus/units/findme-prometheus-public.service":
            selected["public_changed"] = True
        if path in {
            "deploy/observability/bootstrap.py",
            "deploy/monitoring/probe-vm/run-remote.sh",
        }:
            selected["public_changed"] = True
        if (
            path.startswith("deploy/monitoring/probe-vm/")
            or path == ".github/workflows/deploy-public-probe.yml"
        ):
            selected["probe_changed"] = True
        if path == "scripts/monitor_public_health.py":
            selected["probe_changed"] = True
        if (
            path.startswith("deploy/image-origin/monitoring/")
            or path == ".github/workflows/deploy-image-origin.yml"
        ):
            selected["image_monitoring_changed"] = True
        if (
            path.startswith("deploy/monitoring/prometheus/")
            and path not in HOST_SHARED
            and not path.startswith("deploy/monitoring/prometheus/units/")
        ):
            selected["cloud_changed"] = True
        if path in {
            ".github/workflows/monitoring.yml",
            "deploy/observability/wait-host-releases.py",
        }:
            selected["cloud_changed"] = True
        if path in {"src/backend/config/metrics.py", "src/backend/config/views.py"}:
            selected["application_changed"] = True
    # Cloud apply also retries after a host-only merge. A previous cloud run may have stopped at
    # the same-SHA host barrier, so unchanged rules/dashboard still need reconciliation.
    selected["cloud_changed"] |= any(
        selected[key]
        for key in (
            "canonical_changed",
            "public_changed",
            "probe_changed",
            "image_monitoring_changed",
            "application_changed",
        )
    )
    return selected


def classify(paths):
    web = worker = base = False
    for path in paths:
        if path.startswith(("docs/", "tests/", ".agents/", ".superpowers/")) or path.endswith(
            ".md"
        ):
            continue
        if any(
            value
            for key, value in classify_observability([path]).items()
            if key != "application_changed"
        ) and path not in {
            "src/backend/config/metrics.py",
            "src/backend/config/views.py",
            "docker-compose.deployment.yml",
        }:
            continue
        if path in {".github/workflows/deploy.yml", "deploy/run-remote.sh"}:
            # Orchestration is consumed by the next selected release; it is not an image input.
            continue
        if path == ".dockerignore":
            web = worker = base = True
        elif path == "Dockerfile.worker-base" or path in {
            "src/worker/requirements.txt",
            "src/worker/requirements.cpu.txt",
        }:
            worker = base = True
        elif path == "Dockerfile.worker" or path.startswith("src/worker/photo_worker/"):
            worker = True
        elif (
            path in {"Dockerfile", "Dockerfile.import-worker"}
            or path.startswith(("src/backend/", "src/import_worker/", "deploy/"))
            or path.startswith("docker-compose")
            or path == ".env.example"
        ):
            web = True
        elif not path.startswith(("src/worker/tests/", ".github/")):
            # Unknown operational/configuration inputs must not silently look like documentation.
            web = True
    return {"web_changed": web, "worker_changed": worker, "worker_base_changed": base}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--observability", action="store_true")
    args = parser.parse_args()
    if args.base == "0" * 40:
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
