#!/usr/bin/env python3
"""Publish one bounded Commerce observation from the canonical VM."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from monitor_public_health import Metric, ProbeConfig, write_metrics


@dataclass(frozen=True)
class Config:
    folder_id: str
    deploy_root: str


def observe(config: Config) -> str:
    root = Path(config.deploy_root)
    result = subprocess.run(
        [
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
            "commerce_worker_health",
            "--max-ready-age-seconds",
            "300",
            "--format",
            "json",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=30,
    )
    return result.stdout


def run(
    config: Config,
    *,
    observe: Callable[[Config], str] = observe,
    metric_writer: Callable[[ProbeConfig, list[Metric]], None] = write_metrics,
    emit: Callable[[str], None] = print,
) -> int:
    try:
        payload = json.loads(observe(config))
        if not isinstance(payload, dict) or set(payload) != {
            "worker_alive",
            "oldest_ready_age_seconds",
        }:
            raise ValueError("invalid observation")
        alive = payload["worker_alive"]
        age = payload["oldest_ready_age_seconds"]
        if (
            type(alive) is not bool
            or type(age) not in (int, float)
            or not math.isfinite(age)
            or age < 0
        ):
            raise ValueError("invalid observation")
    except (OSError, ValueError, subprocess.SubprocessError, OverflowError):
        emit("commerce observation failed")
        return 1
    metrics: list[Metric] = [
        {"name": name, "labels": {"check": "canonical-commerce"}, "value": value, "type": "DGAUGE"}
        for name, value in (
            ("commerce_worker_alive", float(alive)),
            ("commerce_oldest_ready_age_seconds", float(age)),
        )
    ]
    try:
        metric_writer(
            ProbeConfig(
                target="",
                folder_id=config.folder_id,
                check_name="canonical-commerce",
                metadata_iam_token=True,
            ),
            metrics,
        )
    except (OSError, ValueError, RuntimeError):
        emit("commerce metrics write failed")
        return 1
    emit("commerce metrics published")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        payload = json.loads(arguments.config.read_text())
        config = Config(**payload)
        if not config.folder_id or not Path(config.deploy_root).is_absolute():
            raise ValueError("invalid config")
    except (OSError, ValueError, TypeError):
        print("commerce configuration failed")
        return 1
    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())
