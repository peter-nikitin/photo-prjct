#!/usr/bin/env python3
"""Install the isolated host probe; no container or application deployment operations."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

FILES = (
    "install.py",
    "monitor_public_health.py",
    "findme-public-probe.service",
    "findme-public-probe.timer",
)
TIMER = "findme-public-probe.timer"
SERVICE = "findme-public-probe.service"


def validate(package: Path) -> str:
    manifest = json.loads((package / "manifest.json").read_text())
    release = manifest["release"]
    if not isinstance(release, str) or not re.fullmatch("[0-9a-f]{40}", release):
        raise ValueError("invalid release")
    if set(manifest["sha256"]) != set(FILES):
        raise ValueError("invalid file list")
    for name in FILES:
        path = package / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("invalid package member")
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"][name]:
            raise ValueError("package digest mismatch")
    compile((package / "monitor_public_health.py").read_bytes(), "monitor_public_health.py", "exec")
    return release


def systemctl(*arguments: str) -> None:
    subprocess.run(
        ["systemctl", *arguments], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def link(base: Path, name: str, target: Path) -> None:
    temporary = base / (name + ".new")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target.relative_to(base))
    temporary.replace(base / name)


def activate(root: Path, package: Path) -> None:
    units = root / "etc/systemd/system"
    units.mkdir(parents=True, exist_ok=True)
    for name in (SERVICE, TIMER):
        shutil.copyfile(package / name, units / name)
        (units / name).chmod(0o644)
    systemctl("daemon-reload")
    systemctl("enable", "--now", TIMER)
    systemctl("restart", TIMER)
    systemctl("is-active", "--quiet", TIMER)


def apply(action: str, package: Path | None, root: Path = Path("/")) -> None:
    release = validate(package) if action == "install" and package else None
    base = root / "opt/findme-public-probe"
    base.mkdir(parents=True, exist_ok=True)
    base.chmod(0o755)
    with (base / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == "disable":
            systemctl("disable", "--now", TIMER)
            systemctl("stop", SERVICE)
            print("PUBLIC_PROBE_TIMER=disabled")
            return
        old = (base / "current").resolve() if (base / "current").exists() else None
        if action == "rollback":
            if not (base / "previous").exists():
                raise ValueError("no previous package")
            target = (base / "previous").resolve()
            validate(target)
        else:
            if package is None or release is None:
                raise ValueError("package required")
            releases = base / "releases"
            releases.mkdir(exist_ok=True)
            releases.chmod(0o755)
            target = releases / release
            if target.exists():
                validate(target)
                if (target / "manifest.json").read_bytes() != (
                    package / "manifest.json"
                ).read_bytes():
                    raise ValueError("release already exists with different contents")
            else:
                stage = Path(tempfile.mkdtemp(prefix=".install-", dir=releases))
                try:
                    for name in (*FILES, "manifest.json"):
                        shutil.copyfile(package / name, stage / name)
                        (stage / name).chmod(0o644)
                    stage.chmod(0o755)
                    validate(stage)
                    stage.rename(target)
                finally:
                    if stage.exists():
                        shutil.rmtree(stage)
        if (root / "etc/systemd/system" / TIMER).exists():
            systemctl("stop", TIMER)
            systemctl("stop", SERVICE)
        link(base, "current", target)
        try:
            activate(root, target)
        except subprocess.CalledProcessError:
            systemctl("disable", "--now", TIMER)
            if old:
                link(base, "current", old)
                activate(root, old)
            else:
                (base / "current").unlink(missing_ok=True)
            raise
        if old and old != target:
            link(base, "previous", old)
        print(f"PUBLIC_PROBE_RELEASE={target.name}")
        print("PUBLIC_PROBE_TIMER=active")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "rollback", "disable"])
    parser.add_argument("--package", type=Path)
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("requires root")
    try:
        apply(arguments.action, arguments.package)
    except (OSError, ValueError, KeyError, SyntaxError, subprocess.CalledProcessError):
        print("PUBLIC_PROBE_INSTALL=failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
