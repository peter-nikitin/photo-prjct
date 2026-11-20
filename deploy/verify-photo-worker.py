"""Validate the running photo-worker against the effective deployment configuration."""

import json
import subprocess

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


def _status() -> str:
    service = json.loads(_output(*COMPOSE, "config", "--format", "json", "photo-worker"))[
        "services"
    ]["photo-worker"]
    containers = _output(*COMPOSE, "ps", "--all", "-q", "photo-worker").split()
    if len(containers) != 1:
        return "container-mismatch"

    actual = json.loads(
        _output(
            "docker",
            "inspect",
            "--type",
            "container",
            "--format",
            INSPECT_FORMAT,
            "--",
            containers[0],
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


def main() -> int:
    try:
        status = _status()
    except (AttributeError, KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError):
        status = "unavailable"
    print(f"PHOTO_WORKER_VERIFY={status}")
    return int(status != "ok")


if __name__ == "__main__":
    raise SystemExit(main())
