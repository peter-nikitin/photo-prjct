#!/usr/bin/env python3
"""Explicitly install the canonical host Commerce timer, restoring prior state on failure."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
from pathlib import Path

TIMER = "findme-commerce-monitoring.timer"
SERVICE = "findme-commerce-monitoring.service"


def systemctl(*arguments: str) -> None:
    subprocess.run(
        ["systemctl", *arguments],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=65,
    )


def state(operation: str, unit: str) -> bool:
    return (
        subprocess.run(
            ["systemctl", operation, "--quiet", unit],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).returncode
        == 0
    )


def install(source: Path, folder_id: str, deploy_root: str, *, root: Path = Path("/")) -> None:
    if (
        not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id)
        or not Path(deploy_root).is_absolute()
        or any(c in deploy_root for c in "\n\r\0")
    ):
        raise ValueError("invalid configuration")
    base = root / "usr/local/lib/findme-commerce-monitoring"
    units = root / "etc/systemd/system"
    content = {
        base / name: (source / "scripts" / name).read_bytes()
        for name in ("monitor_commerce.py", "monitor_public_health.py")
    }
    for path, data in content.items():
        compile(data, str(path), "exec")
    content[base / "config.json"] = json.dumps(
        {"folder_id": folder_id, "deploy_root": deploy_root}
    ).encode()
    for name in (SERVICE, TIMER):
        content[units / name] = (source / "deploy/monitoring/commerce-vm" / name).read_bytes()
    base.mkdir(parents=True, exist_ok=True)
    base.chmod(0o755)
    units.mkdir(parents=True, exist_ok=True)
    with (base / ".install.lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = {
            path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
            for path in content
        }
        enabled = state("is-enabled", TIMER)
        active = state("is-active", TIMER)
        service_active = state("is-active", SERVICE)
        try:
            if old[units / TIMER] is not None or active:
                systemctl("stop", TIMER)
            if old[units / SERVICE] is not None or service_active:
                systemctl("stop", SERVICE)
            for path, data in content.items():
                temporary = path.with_name(path.name + ".new")
                temporary.write_bytes(data)
                temporary.chmod(0o600 if path.name == "config.json" else 0o644)
                temporary.replace(path)
            systemctl("daemon-reload")
            systemctl("start", SERVICE)
            systemctl("enable", "--now", TIMER)
            systemctl("is-active", "--quiet", TIMER)
        except (OSError, subprocess.SubprocessError):
            for arguments in (("stop", TIMER), ("stop", SERVICE), ("disable", TIMER)):
                try:
                    systemctl(*arguments)
                except (OSError, subprocess.SubprocessError):
                    pass
            for path, prior in old.items():
                path.with_name(path.name + ".new").unlink(missing_ok=True)
                if prior is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(prior[0])
                    path.chmod(prior[1])
            systemctl("daemon-reload")
            if enabled:
                systemctl("enable", TIMER)
            if active:
                systemctl("start", TIMER)
            if service_active:
                systemctl("start", SERVICE)
            raise


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder-id", required=True)
    parser.add_argument("--deploy-root", required=True)
    args = parser.parse_args(arguments)
    if os.geteuid() != 0:
        parser.error("requires root")
    try:
        install(Path(__file__).resolve().parents[3], args.folder_id, args.deploy_root)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("commerce monitoring installation failed")
        return 1
    print("commerce monitoring timer active")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
