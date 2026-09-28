#!/usr/bin/env python3
"""Explicit bounded host installation with local backup and transactional rollback."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

AGENT_HOSTS = {
    "canonical": (Path("etc/yc/unified_agent/config.yml"), "unified_agent.service"),
    "public": (Path("etc/yandex/unified_agent/config.yml"), "unified-agent.service"),
}
LIBRARY = Path("usr/local/lib/findme-prometheus")
UNIT_DIRECTORY = Path("etc/systemd/system")
SOURCE_FILES = (
    "deploy/monitoring/prometheus/exporter.py",
    "deploy/monitoring/prometheus/install.py",
    "deploy/monitoring/prometheus/units/findme-prometheus-public.service",
    "deploy/monitoring/prometheus/units/findme-prometheus-canonical.service",
    "scripts/monitor_public_health.py",
    "scripts/monitor_commerce.py",
)


class InstallError(Exception):
    pass


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def source_hash(source: Path) -> str:
    digest = hashlib.sha256()
    for name in sorted(SOURCE_FILES):
        digest.update(name.encode() + b"\0" + (source / name).read_bytes() + b"\0")
    return digest.hexdigest()


def vm_instance_id() -> str:
    request = Request(
        "http://169.254.169.254/computeMetadata/v1/instance/id",
        headers={"Metadata-Flavor": "Google"},
    )
    with urlopen(request, timeout=5) as response:
        return response.read(256).decode().strip()


def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=False, capture_output=True, text=True, timeout=60)


def _atomic(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".findme-install")
    try:
        temporary.write_bytes(content)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore(backup: Path, root: Path, runner: Callable[..., Any]) -> None:
    manifest = json.loads((backup / "manifest.json").read_text())
    role = manifest["role"]
    if role not in AGENT_HOSTS:
        raise InstallError("backup managed-file identity mismatch")
    agent_config, agent_unit = AGENT_HOSTS[role]
    unit = f"findme-prometheus-{role}.service"
    allowed = {str(agent_config), str(UNIT_DIRECTORY / unit)} | {
        str(LIBRARY / name)
        for name in ("exporter.py", "monitor_public_health.py", "monitor_commerce.py")
    }
    if {item["path"] for item in manifest["files"]} != allowed:
        raise InstallError("backup managed-file identity mismatch")
    if set(manifest["units"]) != {unit, agent_unit}:
        raise InstallError("backup managed-unit identity mismatch")
    cleanup_failed = False
    for unit, state in manifest["units"].items():
        if not state["existed"] and (root / UNIT_DIRECTORY / unit).exists():
            try:
                if runner(["systemctl", "is-active", unit]).returncode == 0:
                    cleanup_failed = runner(["systemctl", "stop", unit]).returncode != 0
                cleanup_failed = (
                    runner(["systemctl", "disable", unit]).returncode != 0 or cleanup_failed
                )
            except (OSError, subprocess.SubprocessError):
                cleanup_failed = True
    for item in manifest["files"]:
        destination = root / item["path"]
        if item["existed"]:
            _atomic(destination, (backup / "files" / item["path"]).read_bytes(), item["mode"])
        else:
            destination.unlink(missing_ok=True)
    commands = [["systemctl", "daemon-reload"]]
    for unit, state in manifest["units"].items():
        if not state["existed"]:
            continue
        commands.append(["systemctl", "enable" if state["enabled"] else "disable", unit])
        commands.append(["systemctl", "restart" if state["active"] else "stop", unit])
    # Run every restoration step, even when one service operation fails.
    failed = cleanup_failed
    for command in commands:
        try:
            failed = runner(command).returncode != 0 or failed
        except (OSError, subprocess.SubprocessError):
            failed = True
    if failed:
        raise InstallError(f"rollback service restoration failed; retained backup: {backup}")


def install(
    source: Path,
    rendered: Path,
    *,
    role: str,
    expected_current_sha256: str,
    expected_source_sha256: str,
    expected_rendered_sha256: str,
    expected_instance_id: str,
    revision: str,
    root: Path = Path("/"),
    runner: Callable[..., Any] = run,
    instance_id: Callable[[], str] = vm_instance_id,
) -> Path:
    if role not in ("canonical", "public") or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise InstallError("exact source revision and host role required")
    if not expected_instance_id or instance_id() != expected_instance_id:
        raise InstallError("VM instance identity mismatch")
    agent_config, agent_unit = AGENT_HOSTS[role]
    current = root / agent_config
    if sha256(current.read_bytes()) != expected_current_sha256:
        raise InstallError("current agent configuration hash mismatch")
    if source_hash(source) != expected_source_sha256:
        raise InstallError("reviewed source package hash mismatch")
    version = runner(["/usr/bin/unified_agent", "--svnrevision"])
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version.stdout)
    if version.returncode or not match or tuple(map(int, match.groups())) < (25, 3, 80):
        raise InstallError(
            "Unified Agent version does not support the reviewed Remote Write contract"
        )
    if sha256(rendered.read_bytes()) != expected_rendered_sha256:
        raise InstallError("reviewed rendered agent configuration hash mismatch")
    validation = runner(["/usr/bin/unified_agent", "--config", str(rendered), "check-config"])
    if validation.returncode:
        raise InstallError("Unified Agent configuration validation failed")
    lock_path = root / "run/lock/findme-prometheus.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError("another monitoring installation is running") from None
        if sha256(current.read_bytes()) != expected_current_sha256:
            raise InstallError("current agent configuration changed before install")
        if sha256(rendered.read_bytes()) != expected_rendered_sha256:
            raise InstallError("rendered agent configuration changed before install")
        unit = f"findme-prometheus-{role}.service"
        opposite = f"findme-prometheus-{'canonical' if role == 'public' else 'public'}.service"
        if (root / UNIT_DIRECTORY / opposite).exists():
            raise InstallError("opposite host role already installed")
        files: dict[Path, bytes] = {
            agent_config: rendered.read_bytes(),
            LIBRARY / "exporter.py": (source / SOURCE_FILES[0]).read_bytes(),
            LIBRARY / "monitor_public_health.py": (
                source / "scripts/monitor_public_health.py"
            ).read_bytes(),
            LIBRARY / "monitor_commerce.py": (source / "scripts/monitor_commerce.py").read_bytes(),
            UNIT_DIRECTORY / unit: (
                source / "deploy/monitoring/prometheus/units" / unit
            ).read_bytes(),
        }
        backup = root / "var/lib/findme-prometheus/backups" / f"{time.time_ns()}-{revision}"
        backup.mkdir(mode=0o700, parents=True, exist_ok=False)
        manifest: dict[str, Any] = {
            "revision": revision,
            "role": role,
            "source_sha256": expected_source_sha256,
            "instance_id": expected_instance_id,
            "files": [],
            "units": {},
        }
        for relative in files:
            target = root / relative
            if target.is_symlink():
                raise InstallError("managed destination must not be a symlink")
            existed = target.exists()
            mode = stat.S_IMODE(target.stat().st_mode) if existed else 0o644
            manifest["files"].append({"path": str(relative), "existed": existed, "mode": mode})
            if existed:
                saved = backup / "files" / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
        for service in (unit, agent_unit):
            enabled = runner(["systemctl", "is-enabled", service])
            if enabled.stdout.strip() in ("masked", "masked-runtime"):
                raise InstallError(
                    "masked service requires operator resolution before installation"
                )
            manifest["units"][service] = {
                "existed": service == agent_unit or (root / UNIT_DIRECTORY / service).exists(),
                "enabled": enabled.returncode == 0,
                "active": runner(["systemctl", "is-active", service]).returncode == 0,
            }
        (backup / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        try:
            for item in manifest["files"]:
                relative = Path(item["path"])
                _atomic(root / relative, files[relative], item["mode"])
            for command in [
                ["systemctl", "daemon-reload"],
                ["systemctl", "enable", unit],
                ["systemctl", "restart", unit],
                ["systemctl", "restart", agent_unit],
                ["systemctl", "is-active", unit],
                ["systemctl", "is-active", agent_unit],
            ]:
                if runner(command).returncode:
                    raise InstallError("service activation failed")
        except (OSError, subprocess.SubprocessError, InstallError):
            try:
                _restore(backup, root, runner)
            except (OSError, subprocess.SubprocessError, InstallError):
                raise InstallError(
                    f"installation failed; rollback incomplete; retained backup: {backup}"
                ) from None
            raise InstallError(
                f"installation failed and rolled back; retained backup: {backup}"
            ) from None
        return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    hashing = commands.add_parser("source-hash")
    hashing.add_argument("--source-root", type=Path, required=True)
    applying = commands.add_parser("install")
    applying.add_argument("--source-root", type=Path, required=True)
    applying.add_argument("--rendered-agent", type=Path, required=True)
    applying.add_argument("--role", choices=("canonical", "public"), required=True)
    applying.add_argument("--current-sha256", required=True)
    applying.add_argument("--source-sha256", required=True)
    applying.add_argument("--rendered-sha256", required=True)
    applying.add_argument("--instance-id", required=True)
    applying.add_argument("--revision", required=True)
    restoring = commands.add_parser("rollback")
    restoring.add_argument("--backup", type=Path, required=True)
    restoring.add_argument("--instance-id", required=True)
    args = parser.parse_args()
    try:
        if args.command == "source-hash":
            print(source_hash(args.source_root))
        else:
            if os.geteuid() != 0:
                raise InstallError("host changes require root")
            if args.command == "rollback":
                if args.backup.resolve().parent != Path("/var/lib/findme-prometheus/backups"):
                    raise InstallError("rollback requires a retained monitoring backup")
                expected = json.loads((args.backup / "manifest.json").read_text())["instance_id"]
                if args.instance_id != expected or vm_instance_id() != expected:
                    raise InstallError("rollback VM instance identity mismatch")
                _restore(args.backup, Path("/"), run)
            else:
                print(
                    install(
                        args.source_root,
                        args.rendered_agent,
                        role=args.role,
                        expected_current_sha256=args.current_sha256,
                        expected_source_sha256=args.source_sha256,
                        expected_rendered_sha256=args.rendered_sha256,
                        expected_instance_id=args.instance_id,
                        revision=args.revision,
                    )
                )
    except (InstallError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(
            str(error) if isinstance(error, InstallError) else "host monitoring operation failed",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
