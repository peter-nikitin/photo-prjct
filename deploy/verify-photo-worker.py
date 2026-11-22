"""Validate every photo-worker replica against the effective deployment configuration."""

import json
import subprocess
from typing import Any

COMPOSE = (
    "docker",
    "compose",
    "--project-name",
    "photo-prjct",
    "--env-file",
    ".env",
    "-f",
    "docker-compose.deployment.yml",
    "--profile",
    "photo-worker",
)
INSPECT_FORMAT = (
    '{"image":{{json .Config.Image}},"environment":{{json .Config.Env}},'
    '"status":{{json .State.Status}}}'
)


def _output(*command: str) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=30).stdout


def _replica_status(container: str, service: dict[str, Any]) -> str:
    actual = json.loads(
        _output(
            "docker",
            "inspect",
            "--type",
            "container",
            "--format",
            INSPECT_FORMAT,
            "--",
            container,
        )
    )
    if actual["status"] != "running":
        return "not-running"
    if actual["image"] != service["image"]:
        return "image-mismatch"

    expected = service["environment"]
    if not expected:
        return "unavailable"
    environment = dict(entry.split("=", 1) for entry in actual["environment"])
    if any(key not in environment or environment[key] != value for key, value in expected.items()):
        return "environment-mismatch"
    return "ok"


def _status() -> str:
    services = json.loads(_output(*COMPOSE, "config", "--format", "json"))["services"]
    workers = {
        name: spec for name, spec in services.items() if "photo-worker" in spec.get("profiles", [])
    }
    if not workers:
        return "unavailable"
    for role, service in workers.items():
        containers = _output(
            "docker",
            "ps",
            "-a",
            "-q",
            "--filter",
            "label=com.docker.compose.project=photo-prjct",
            "--filter",
            "label=com.docker.compose.oneoff=False",
            "--filter",
            f"label=com.docker.compose.service={role}",
        ).split()
        if len(containers) != service.get("scale", 1):
            return "container-mismatch"
        for container in containers:
            status = _replica_status(container, service)
            if status != "ok":
                return status
    return "ok"


def main() -> int:
    try:
        status = _status()
    except (AttributeError, KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError):
        status = "unavailable"
    print(f"PHOTO_WORKER_VERIFY={status}")
    return int(status != "ok")


if __name__ == "__main__":
    raise SystemExit(main())
