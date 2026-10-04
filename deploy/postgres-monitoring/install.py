#!/usr/bin/python3
"""Reconcile only the private exporter, independently of product Compose operations."""

import stat
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

CONFIG = Path("/var/lib/findme-observability-reconcile/postgres-compose.yml")
ENV = Path("/opt/photo-prjct/.env")


def compose(config: Path, *args: str) -> None:
    subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "photo-prjct",
            "--env-file",
            str(ENV),
            "-f",
            str(config),
            "--profile",
            "observability",
            *args,
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )


def snapshot(backup: Path) -> bool:
    existed = CONFIG.exists()
    if existed:
        (backup / "postgres-compose.yml").write_bytes(CONFIG.read_bytes())
    return existed


def install(source: Path) -> None:
    # Provisioning the root-owned 0640 root:nogroup credential and SQL role is a
    # separately approved operator action. Never create credentials or alter SQL here.
    secret = Path("/opt/photo-prjct/secrets/postgres-exporter-password")
    metadata = secret.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 65534
        or stat.S_IMODE(metadata.st_mode) != 0o640
    ):
        raise ValueError("unsafe PostgreSQL monitoring credential")
    candidate = source / "docker-compose.deployment.yml"
    compose(candidate, "up", "-d", "--no-deps", "postgres-exporter")
    deadline = time.monotonic() + 30
    while True:
        try:
            with urlopen("http://127.0.0.1:9187/metrics", timeout=5) as response:
                content = response.read(2_000_000).decode()
            if "\npg_up 1\n" in "\n" + content:
                break
        except (OSError, URLError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("PostgreSQL exporter source unavailable")
        time.sleep(1)
    CONFIG.write_bytes(candidate.read_bytes())
    CONFIG.chmod(0o600)


def restore(source: Path, backup: Path, existed: bool) -> None:
    if existed:
        previous = backup / "postgres-compose.yml"
        compose(previous, "up", "-d", "--no-deps", "postgres-exporter")
        CONFIG.write_bytes(previous.read_bytes())
    else:
        compose(source / "docker-compose.deployment.yml", "rm", "-sf", "postgres-exporter")
        CONFIG.unlink(missing_ok=True)
