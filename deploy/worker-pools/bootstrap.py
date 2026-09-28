#!/usr/bin/env python3
"""Worker-host bootstrap: narrow pinned Lockbox payload and immutable OCI revision gate."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/backend"))
try:
    from worker_pool_cloud import identifier, metadata_token, request_bytes, request_json
except ImportError:
    from processing.services.worker_pool_cloud import (
        identifier,
        metadata_token,
        request_bytes,
        request_json,
    )

KEYS = {"PHOTO_PROCESSING_FLEET_TOKEN", "IMAGE_PULL_AUTH"}


def validate_payload(payload, version):
    if (
        not isinstance(payload, dict)
        or set(payload) != {"versionId", "entries"}
        or payload["versionId"] != version
        or not isinstance(payload["entries"], list)
    ):
        raise ValueError("invalid bootstrap payload")
    values = {}
    for row in payload["entries"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"key", "textValue"}
            or row["key"] not in KEYS
            or row["key"] in values
        ):
            raise ValueError("invalid bootstrap payload")
        value = row["textValue"]
        if (
            not isinstance(value, str)
            or not value
            or not value.isascii()
            or any(c.isspace() for c in value)
        ):
            raise ValueError("invalid bootstrap payload")
        values[row["key"]] = value
    if set(values) != KEYS:
        raise ValueError("invalid bootstrap payload")
    import re

    if re.fullmatch(r"[A-Za-z0-9_-]+", values["PHOTO_PROCESSING_FLEET_TOKEN"]) is None:
        raise ValueError("invalid fleet credential")
    try:
        decoded = base64.b64decode(values["IMAGE_PULL_AUTH"], validate=True).decode("ascii")
    except (ValueError, UnicodeError):
        raise ValueError("invalid image pull credential") from None
    if ":" not in decoded or not all(decoded.split(":", 1)):
        raise ValueError("invalid image pull credential")
    return values


def private_file(path, content, *, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("unexpected bootstrap path")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(content)


def activate(config, values, instance_id, *, root=Path("/"), run=subprocess.run):
    identifier(instance_id)
    pool = config["pool"]
    if pool not in {"bulk", "selfie"}:
        raise ValueError("invalid pool")
    base = root / "etc/findme-worker"
    image = config["worker_image"]
    if (
        re.fullmatch(r"[0-9a-f]{40}", config["worker_build"]) is None
        or re.fullmatch(
            r"ghcr\.io/[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9._-]*-worker@sha256:[0-9a-f]{64}", image
        )
        is None
    ):
        raise ValueError("immutable GHCR worker release required")
    # No secrets in argv, userdata or logging. Docker auth is a private standard config file.
    auth = {"auths": {"ghcr.io": {"auth": values["IMAGE_PULL_AUTH"]}}}
    private_file(base / "docker/config.json", json.dumps(auth))
    from_config = {
        "PHOTO_WORKER_POOL": pool,
        "PHOTO_WORKER_BUILD": config["worker_build"],
        "WORKER_IMAGE": image,
        "PHOTO_WORKER_API_URL": "https://findme-photo.ru:8443/internal/photo-processing/v1",
        "WORKER_POOL_PRIVATE_API_IPV4": config["private_api_ipv4"],
        "PHOTO_WORKER_PROCESSOR_IDENTITIES": config["identities"],
        "PHOTO_PROCESSING_FLEET_TOKEN": values["PHOTO_PROCESSING_FLEET_TOKEN"],
    }
    telemetry_enabled = config.get("telemetry_enabled", False)
    if type(telemetry_enabled) is not bool:
        raise ValueError("explicit boolean telemetry opt-in required")
    if telemetry_enabled:
        from_config["PHOTO_WORKER_RUNTIME_TELEMETRY_ENABLED"] = "True"
    # JSON quoting produces dotenv double-quoted values; escape Compose interpolation explicitly.
    private_file(
        base / "runtime.env",
        "".join(
            f"{key}={json.dumps(value.replace('$', '$$'))}\n" for key, value in from_config.items()
        ),
    )
    # The worker's unprivileged UID reads this nonsecret, bind-mounted machine identity.
    private_file(base / "instance-id", instance_id + "\n", mode=0o644)
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "DOCKER_CONFIG": str(base / "docker"),
    }

    def invoke(args):
        return run(args, check=True, capture_output=True, text=True, timeout=300, env=environment)

    if (
        invoke(["docker", "version", "--format", "{{.Server.Version}}"]).stdout.strip()
        != config["docker_version"]
    ):
        raise ValueError("unreviewed Docker version")
    if (
        invoke(["docker", "compose", "version", "--short"]).stdout.strip().removeprefix("v")
        != config["compose_version"]
    ):
        raise ValueError("unreviewed Compose version")
    invoke(["docker", "pull", image])
    actual = invoke(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            '{{index .Config.Labels "org.opencontainers.image.revision"}}',
            image,
        ]
    ).stdout.strip()
    if actual != config["worker_build"]:
        raise ValueError("worker OCI revision mismatch")
    hosts = root / "etc/hosts"
    original = hosts.read_text() if hosts.exists() else ""
    if any(
        "findme-photo.ru" in line.split("#", 1)[0].split()[1:] for line in original.splitlines()
    ):
        raise ValueError("unexpected canonical host mapping")
    hosts.write_text(original + f"\n{config['private_api_ipv4']} findme-photo.ru\n")
    invoke(
        [
            "docker",
            "compose",
            "--project-name",
            "findme-worker",
            "--env-file",
            str(base / "runtime.env"),
            "-f",
            str(root / "usr/local/lib/findme-worker/compose.yml"),
            "up",
            "-d",
        ]
    )
    invoke(["systemctl", "daemon-reload"])
    invoke(["systemctl", "enable", "--now", "findme-worker-retire.timer"])
    if telemetry_enabled:
        try:
            if config["zone"] not in {"ru-central1-a", "ru-central1-b", "ru-central1-d"}:
                raise ValueError("unsupported telemetry zone")
            # Dependencies are an immutable reviewed-image prerequisite, never installed here.
            run(
                [
                    "/opt/findme-worker-telemetry/bin/python",
                    "-c",
                    "from importlib.metadata import version; "
                    "assert version('psutil') == '7.1.3'; "
                    "assert version('prometheus-client') == '0.25.0'",
                ],
                check=True,
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
            )
            telemetry_env = {
                "PHOTO_WORKER_POOL": pool,
                "PHOTO_WORKER_BUILD": config["worker_build"],
                "PHOTO_WORKER_ZONE": config["zone"],
                "PHOTO_PROCESSING_FLEET_TOKEN": values["PHOTO_PROCESSING_FLEET_TOKEN"],
            }
            private_file(
                base / "telemetry.env",
                "".join(f"{key}={json.dumps(value)}\n" for key, value in telemetry_env.items()),
            )
            run(
                ["systemctl", "enable", "--now", "findme-worker-telemetry.timer"],
                check=True,
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
            )
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            print("worker_telemetry_setup_unavailable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        token = metadata_token()
        secret = identifier(config["bootstrap_secret_id"])
        version = identifier(config["bootstrap_version_id"])
        payload = request_json(
            Request(
                "https://payload.lockbox.api.cloud.yandex.net/lockbox/v1/secrets/"
                f"{secret}/payload?{urlencode({'versionId': version})}",
                headers={"Authorization": f"Bearer {token}"},
            )
        )
        values = validate_payload(payload, version)
        instance = (
            request_bytes(
                Request(
                    "http://169.254.169.254/computeMetadata/v1/instance/id",
                    headers={"Metadata-Flavor": "Google"},
                ),
                max_body=64,
            )
            .decode()
            .strip()
        )
        activate(config, values, instance)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("worker bootstrap failed")
        return 1
    print("worker bootstrap complete; coordinator readiness still required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
