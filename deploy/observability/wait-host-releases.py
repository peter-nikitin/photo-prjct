#!/usr/bin/env python3
"""Wait for required main-push host jobs at the cloud package's exact source SHA."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPOSITORY = "peter-nikitin/photo-prjct"
HOST_JOBS = {
    "canonical_changed": ("deploy.yml", "Reconcile canonical observability"),
    "public_changed": ("deploy-public-probe.yml", "Reconcile public observability"),
    "probe_changed": ("deploy-public-probe.yml", "Deploy public health probe"),
    "image_monitoring_changed": ("deploy-image-origin.yml", "Deploy isolated image origin"),
}


class HostReleaseError(Exception):
    pass


def github_api(path: str) -> dict[str, Any]:
    result = subprocess.run(
        ["gh", "api", path], check=True, capture_output=True, text=True, timeout=30
    )
    return json.loads(result.stdout)


def wait_for_hosts(
    revision: str,
    selected: dict[str, bool],
    *,
    api: Callable[[str], dict[str, Any]] = github_api,
    timeout: int = 1800,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not 0 < timeout <= 1800:
        raise HostReleaseError("exact source SHA and bounded timeout required")
    required: dict[str, set[str]] = {}
    for key, (workflow, job) in HOST_JOBS.items():
        if selected.get(key):
            required.setdefault(workflow, set()).add(job)
    deadline = clock() + timeout
    while required:
        for workflow, names in list(required.items()):
            response = api(
                f"repos/{REPOSITORY}/actions/workflows/{workflow}/runs?head_sha={revision}&event=push&per_page=100"
            )
            trusted = [
                run
                for run in response.get("workflow_runs", [])
                if run.get("head_sha") == revision
                and run.get("event") == "push"
                and run.get("head_branch") == "main"
                and run.get("path")
                in {".github/workflows/" + workflow, ".github/workflows/" + workflow + "@main"}
                and run.get("repository", {}).get("full_name") == REPOSITORY
                and isinstance(run.get("id"), int)
                and run["id"] > 0
            ]
            if not trusted:
                continue
            run = max(trusted, key=lambda item: item["id"])
            jobs = api(
                f"repos/{REPOSITORY}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100"
            ).get("jobs", [])
            complete = True
            for name in names:
                matches = [job for job in jobs if job.get("name") == name]
                if len(matches) > 1:
                    raise HostReleaseError(f"ambiguous required host job: {workflow}/{name}")
                if not matches:
                    if run.get("status") == "completed":
                        raise HostReleaseError(f"missing required host job: {workflow}/{name}")
                    complete = False
                elif matches[0].get("status") != "completed":
                    complete = False
                elif matches[0].get("conclusion") != "success":
                    raise HostReleaseError(
                        f"host job {workflow}/{name}: {matches[0].get('conclusion')}"
                    )
            if complete:
                del required[workflow]
                print(f"HOST_RECONCILIATION_SHA={revision} workflow={workflow} status=green")
        if required:
            remaining = deadline - clock()
            if remaining <= 0:
                raise HostReleaseError(
                    "timed out waiting for same-SHA host jobs: " + ", ".join(required)
                )
            sleep(min(10, remaining))


def wait_for_samples(
    check: Callable[[], None],
    retry_error: type[Exception],
    *,
    timeout: int = 360,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if not 0 < timeout <= 360:
        raise HostReleaseError("bounded fresh-sample timeout required")
    deadline = clock() + timeout
    while True:
        try:
            check()
            print("HOST_METRIC_SAMPLES=green")
            return
        except retry_error:
            remaining = deadline - clock()
            if remaining <= 0:
                raise HostReleaseError(
                    "timed out waiting for fresh samples after host reconciliation"
                ) from None
            sleep(min(10, remaining))


def sample_check(config_path: Path, oidc_path: Path) -> tuple[Callable[[], None], type[Exception]]:
    path = Path(__file__).resolve().parents[1] / "monitoring/prometheus/control.py"
    spec = importlib.util.spec_from_file_location("observability_control", path)
    assert spec is not None and spec.loader is not None
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    config = control.load_config(config_path)
    control.validate_config(config, live=True)
    transport = control.CloudTransport(config, control.identity("github-oidc", oidc_path))
    return lambda: control.preflight(config, transport), control.ControlError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    hosts = commands.add_parser("hosts")
    hosts.add_argument("--revision", required=True)
    hosts.add_argument("--repository", required=True, choices=(REPOSITORY,))
    for key in HOST_JOBS:
        hosts.add_argument("--" + key.replace("_", "-"), choices=("true", "false"), required=True)
    samples = commands.add_parser("samples")
    samples.add_argument("--config", type=Path, required=True)
    samples.add_argument("--oidc-config", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "hosts":
            selected = {key: getattr(args, key) == "true" for key in HOST_JOBS}
            wait_for_hosts(args.revision, selected)
        else:
            check, retry_error = sample_check(args.config, args.oidc_config)
            wait_for_samples(check, retry_error)
    except (HostReleaseError, OSError, ValueError, subprocess.SubprocessError) as error:
        reason = str(error) if isinstance(error, HostReleaseError) else type(error).__name__
        print(f"HOST_RECONCILIATION=failed reason={reason}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
