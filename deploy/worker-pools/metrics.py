#!/usr/bin/env python3
"""Canonical host collector; no dependency on a running worker VM."""

import argparse
import json
import re
import subprocess
from pathlib import Path


def collect(config, *, run=subprocess.run):
    if set(config) != {"deploy_root", "cloud"} or not Path(config["deploy_root"]).is_absolute():
        raise ValueError("invalid collector config")
    root = Path(config["deploy_root"])
    # Operator-owned configuration identifies the exact cloud scope for observation.
    if config["cloud"] != str(root / "worker-pools-observation.json"):
        raise ValueError("invalid cloud observation path")
    cloud = json.loads(Path(config["cloud"]).read_text())
    if not isinstance(cloud, dict):
        raise ValueError("invalid cloud configuration")
    for key in ("folder_id", "canonical_folder_id"):
        value = cloud.get(key)
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value) is None
        ):
            raise ValueError("invalid cloud folder")
    if cloud["folder_id"] == cloud["canonical_folder_id"]:
        raise ValueError("worker and canonical folders must differ")
    prefix = [
        "docker",
        "compose",
        "--project-name",
        "photo-prjct",
        "--env-file",
        str(root / ".env"),
        "-f",
        str(root / "docker-compose.deployment.yml"),
        "-f",
        str(root / "docker-compose.https.yml"),
        "exec",
        "-T",
        "web",
        "python",
        "manage.py",
    ]
    # This trusted read never changes jobs, attempts, enrollment, features or release state.
    cloud_failed = False
    try:
        run(
            prefix + ["observe_worker_pool_cloud", "--config", "-", "--record"],
            input=json.dumps(cloud),
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=80,
        )
    except (OSError, subprocess.SubprocessError):
        # Cloud telemetry cannot suppress durable demand, including wake-up from bulk zero.
        # The publisher independently marks stale/absent capacity; no synthetic snapshot.
        cloud_failed = True
    run(
        prefix
        + [
            "publish_worker_pool_metrics",
            "--publish",
            "--zone",
            cloud["zone"],
            "--folder-id",
            cloud["canonical_folder_id"],
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    if cloud_failed:
        raise ValueError("cloud observation unavailable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        collect(json.loads(args.config.read_text()))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("worker pool collection failed")
        return 1
    print("worker pool metrics published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
