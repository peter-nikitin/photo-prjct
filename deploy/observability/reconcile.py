#!/usr/bin/python3
"""Root-owned, exact-main host reconciliation; install this entrypoint once as an operator."""

from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

REPOSITORY = "https://github.com/peter-nikitin/photo-prjct.git"
STATE = Path("/var/lib/findme-observability-reconcile")
CONFIG = Path("/etc/findme-observability-reconcile.json")
COMMON = (
    "deploy/observability/reconcile.py",
    "deploy/monitoring/prometheus/exporter.py",
    "deploy/monitoring/prometheus/install.py",
    "deploy/monitoring/prometheus/render_agent.py",
    "deploy/monitoring/prometheus/units/findme-prometheus-canonical.service",
    "deploy/monitoring/prometheus/units/findme-prometheus-public.service",
    "scripts/monitor_public_health.py",
    "scripts/monitor_commerce.py",
)
CANONICAL = (
    "deploy/configure-monitoring-agent.sh",
    "deploy/monitoring/unified-agent.yml.template",
    "deploy/monitoring/merge_native_agent.py",
    "deploy/run-commerce-worker-health.sh",
    "deploy/monitoring/commerce-vm/install.py",
    "deploy/monitoring/commerce-vm/findme-commerce-monitoring.service",
    "deploy/monitoring/commerce-vm/findme-commerce-monitoring.timer",
    "deploy/selfie-observability/root-helper.sh",
    "deploy/selfie-observability/journald.conf",
    "deploy/selfie-observability/selfie-search-summary.service",
    "deploy/selfie-observability/selfie-search-summary.timer",
    "deploy/selfie-observability/run-daily-summary.sh",
    "deploy/selfie-observability/summarize.py",
    "deploy/worker-pools/metrics.py",
    "deploy/worker-pools/metrics.service",
    "deploy/worker-pools/metrics.timer",
    "deploy/worker-pools/metrics-root-helper.sh",
)


def run(*args: str) -> str:
    return subprocess.run(
        list(args), check=True, text=True, capture_output=True, timeout=180
    ).stdout.strip()


def root_owned(path: Path) -> None:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or details.st_uid != 0 or details.st_mode & 0o022:
        raise ValueError(f"unsafe root-owned file: {path}")


def configuration(path: Path = CONFIG) -> dict[str, str]:
    root_owned(path)
    value = json.loads(path.read_text())
    if set(value) != {"role", "instance_id", "folder_id", "workspace_id"}:
        raise ValueError("fixed host foundation configuration required")
    if value["role"] not in {"canonical", "public"}:
        raise ValueError("invalid host role")
    for key in ("instance_id", "folder_id", "workspace_id"):
        if not re.fullmatch(r"[a-z0-9-]{8,64}", value[key]):
            raise ValueError(f"invalid {key}")
    request = Request(
        "http://169.254.169.254/computeMetadata/v1/instance/id",
        headers={"Metadata-Flavor": "Google"},
    )
    with urlopen(request, timeout=5) as response:
        if response.read(256).decode().strip() != value["instance_id"]:
            raise ValueError("VM identity mismatch")
    return value


def authenticate_revision(revision: str, repository: Path) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("exact lowercase main SHA required")
    if not repository.exists():
        run("git", "init", "--bare", str(repository))
    # The URL/ref and Git options are fixed. No checkout, hooks, submodules or runner archive.
    run(
        "git",
        "--git-dir",
        str(repository),
        "-c",
        "core.hooksPath=/dev/null",
        "fetch",
        "--no-tags",
        REPOSITORY,
        "+refs/heads/main:refs/heads/main",
    )
    run(
        "git",
        "--git-dir",
        str(repository),
        "merge-base",
        "--is-ancestor",
        revision,
        "refs/heads/main",
    )


def fetch_blob(revision: str, name: str, stage: Path, repository: Path) -> str:
    entry = run("git", "--git-dir", str(repository), "ls-tree", revision, "--", name)
    if not re.fullmatch(r"100(?:644|755) blob [0-9a-f]{40}\t" + re.escape(name), entry):
        raise ValueError(f"missing or non-regular reviewed source: {name}")
    content = subprocess.run(
        ["git", "--git-dir", str(repository), "show", f"{revision}:{name}"],
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout
    target = stage / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    target.chmod(0o644)
    return hashlib.sha256(content).hexdigest()


def fetch_helper(revision: str, stage: Path, repository: Path) -> Path:
    authenticate_revision(revision, repository)
    name = "deploy/observability/reconcile.py"
    fetch_blob(revision, name, stage, repository)
    path = stage / name
    root_owned(path)
    return path


def fetch_source(revision: str, role: str, stage: Path, repository: Path) -> dict[str, str]:
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or role not in {"canonical", "public"}:
        raise ValueError("exact lowercase main SHA and fixed host role required")
    authenticate_revision(revision, repository)
    return {
        name: fetch_blob(revision, name, stage, repository)
        for name in COMMON + (CANONICAL if role == "canonical" else ())
    }


def module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("findme_reviewed_install", path)
    if spec is None or spec.loader is None:
        raise ValueError("missing reviewed module")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def copy(source: Path, target: Path, mode: int = 0o644) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    candidate = target.with_name(target.name + ".findme-reconcile")
    shutil.copyfile(source, candidate)
    candidate.chmod(mode)
    candidate.replace(target)


def managed(role: str) -> tuple[list[Path], list[str]]:
    agent = (
        "etc/yc/unified_agent/config.yml"
        if role == "canonical"
        else "etc/yandex/unified_agent/config.yml"
    )
    files = [
        Path("/usr/local/sbin/findme-observability-reconcile"),
        Path("/" + agent),
        Path(f"/etc/systemd/system/findme-prometheus-{role}.service"),
    ]
    files += [
        Path("/usr/local/lib/findme-prometheus") / name
        for name in ("exporter.py", "monitor_public_health.py", "monitor_commerce.py")
    ]
    units = [
        f"findme-prometheus-{role}.service",
        "unified_agent.service" if role == "canonical" else "unified-agent.service",
    ]
    if role == "canonical":
        files += [
            Path("/usr/local/lib/findme-commerce-worker-health/run-commerce-worker-health.sh")
        ]
        files += [
            Path("/usr/local/lib/findme-commerce-monitoring") / name
            for name in ("monitor_commerce.py", "monitor_public_health.py", "config.json")
        ]
        files += [
            Path("/etc/systemd/system") / name
            for name in (
                "findme-commerce-monitoring.service",
                "findme-commerce-monitoring.timer",
                "selfie-search-summary.service",
                "selfie-search-summary.timer",
                "findme-worker-pool-metrics.service",
                "findme-worker-pool-metrics.timer",
            )
        ]
        files += [Path("/etc/systemd/journald.conf.d/60-findme-selfie-observability.conf")]
        files += [
            Path("/usr/local/lib/findme-selfie-observability") / name
            for name in ("run-daily-summary.sh", "summarize.py")
        ]
        files += [
            Path("/usr/local/lib/findme-selfie-observability-package") / Path(name).name
            for name in CANONICAL
            if name.startswith("deploy/selfie-observability/")
            and not name.endswith("root-helper.sh")
        ]
        files += [
            Path("/usr/local/sbin/findme-selfie-observability"),
            Path("/usr/local/sbin/findme-worker-pool-metrics"),
        ]
        files += [
            Path("/usr/local/lib/findme-worker-pool-metrics-package") / name
            for name in ("metrics.py", "metrics.service", "metrics.timer")
        ]
        files += [Path("/usr/local/lib/findme-worker-pools/metrics.py")]
        units += [
            "findme-commerce-monitoring.service",
            "findme-commerce-monitoring.timer",
            "selfie-search-summary.timer",
            "systemd-journald.service",
            "findme-worker-pool-metrics.service",
            "findme-worker-pool-metrics.timer",
        ]
    return files, units


def snapshot(files: list[Path], units: list[str], backup: Path) -> dict[str, Any]:
    state: dict[str, Any] = {"files": {}, "units": {}}
    for path in files:
        root_owned(path)  # Existing packages only; no implicit host provisioning.
        state["files"][str(path)] = stat.S_IMODE(path.stat().st_mode)
        destination = backup / "files" / str(path).lstrip("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    for unit in units:
        run("systemctl", "cat", unit)
        state["units"][unit] = {}
        for operation in ("is-active", "is-enabled"):
            state["units"][unit][operation] = (
                subprocess.run(["systemctl", operation, "--quiet", unit], timeout=15).returncode
                == 0
            )
    (backup / "state.json").write_text(json.dumps(state))
    return state


def restore(state: dict[str, Any], backup: Path) -> None:
    failures = []
    for name, mode in state["files"].items():
        try:
            copy(backup / "files" / name.lstrip("/"), Path(name), mode)
        except OSError as exc:
            failures.append(str(exc))
    commands = [("daemon-reload",)]
    for unit, prior in state["units"].items():
        commands += [
            ("enable" if prior["is-enabled"] else "disable", unit),
            ("restart" if prior["is-active"] else "stop", unit),
        ]
    for args in commands:
        try:
            run("systemctl", *args)
        except (OSError, subprocess.SubprocessError) as exc:
            failures.append(str(exc))
    if failures:
        raise RuntimeError(f"host rollback failed; retained backup {backup}")


def install_selfie(helper: Path) -> None:
    try:
        run(str(helper), "install")
        run(str(helper), "verify")
        run(str(helper), "commit")
    except Exception:
        # Verification can fail after a successful install armed its transaction.
        # Existing rollback owns marker cleanup and retains them when restoration fails.
        run(str(helper), "rollback")
        raise


def apply(source: Path, config: dict[str, str], revision: str) -> None:
    role = config["role"]
    root_owned(Path("/usr/bin/unified_agent"))
    run("/usr/bin/unified_agent", "--svnrevision")
    if role == "canonical":
        run(
            "sh",
            str(source / "deploy/configure-monitoring-agent.sh"),
            "--folder-id",
            config["folder_id"],
        )
        run(
            "/usr/bin/python3",
            str(source / "deploy/monitoring/commerce-vm/install.py"),
            "--folder-id",
            config["folder_id"],
            "--deploy-root",
            "/opt/photo-prjct",
        )
        helper = Path("/usr/local/sbin/findme-selfie-observability")
        package = Path("/usr/local/lib/findme-selfie-observability-package")
        for name in CANONICAL:
            if name.startswith("deploy/selfie-observability/"):
                target = helper if name.endswith("root-helper.sh") else package / Path(name).name
                copy(source / name, target, 0o755 if name.endswith((".py", ".sh")) else 0o644)
        install_selfie(helper)
        for name in ("metrics.py", "metrics.service", "metrics.timer"):
            asset = source / "deploy/worker-pools" / name
            copy(asset, Path("/usr/local/lib/findme-worker-pool-metrics-package") / name)
            target = (
                Path("/usr/local/lib/findme-worker-pools/metrics.py")
                if name == "metrics.py"
                else Path("/etc/systemd/system/findme-worker-pool-" + name)
            )
            copy(asset, target)
        copy(
            source / "deploy/worker-pools/metrics-root-helper.sh",
            Path("/usr/local/sbin/findme-worker-pool-metrics"),
            0o755,
        )
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", "--now", "findme-worker-pool-metrics.timer")
        run("systemctl", "start", "findme-worker-pool-metrics.service")
        run("/usr/local/sbin/findme-worker-pool-metrics", "verify")
    current = Path(
        "/etc/yc/unified_agent/config.yml"
        if role == "canonical"
        else "/etc/yandex/unified_agent/config.yml"
    )
    rendered = source / "candidate-agent.yml"
    renderer = module(source / "deploy/monitoring/prometheus/render_agent.py")
    import yaml

    # Preserve optional worker diagnostics when it was already enabled.
    content = current.read_bytes()
    existing = yaml.safe_load(content)
    enabled = (
        role == "canonical"
        and "http://127.0.0.1:8080/worker-diagnostics/metrics/" in content.decode()
    )
    candidate = renderer.merge_agent(
        existing, role=role, workspace_id=config["workspace_id"], worker_telemetry=enabled
    )
    rendered.write_text(yaml.safe_dump(candidate, sort_keys=False))
    installer = module(source / "deploy/monitoring/prometheus/install.py")
    backup = installer.install(
        source,
        rendered,
        role=role,
        expected_current_sha256=hashlib.sha256(content).hexdigest(),
        expected_source_sha256=installer.source_hash(source),
        expected_rendered_sha256=hashlib.sha256(rendered.read_bytes()).hexdigest(),
        expected_instance_id=config["instance_id"],
        revision=revision,
    )
    print(f"PROMETHEUS_BACKUP={backup}")
    copy(
        source / "deploy/observability/reconcile.py",
        Path("/usr/local/sbin/findme-observability-reconcile"),
        0o755,
    )


def reconcile_transaction(
    revision: str, config: dict[str, str], source: Path, repository: Path
) -> None:
    """Run only through the authenticated bootstrap while its host lock remains held."""
    try:
        manifest = fetch_source(revision, config["role"], source, repository)
    except Exception as error:
        report_stage("source", error)
        raise
    receipt = STATE / "receipt.json"
    backup = STATE / f"backup-{revision}-{time.time_ns()}"
    backup.mkdir(mode=0o700)
    (backup / "source.json").write_text(json.dumps({"sha": revision, "manifest": manifest}))
    files, units = managed(config["role"])
    try:
        state = snapshot(files, units, backup)
    except Exception as error:
        report_stage("snapshot", error, files)
        raise
    try:
        apply(source, config, revision)
    except Exception as error:
        report_stage("apply", error)
        restore(state, backup)
        raise
    receipt.write_text(json.dumps({"sha": revision, "manifest": manifest, "backup": str(backup)}))
    print(f"OBSERVABILITY_HOST_SHA={revision} status=green backup={backup}")


def report_stage(stage: str, error: Exception, files: list[Path] | None = None) -> None:
    """Expose only fixed stage/type markers, never remote command output or file contents."""
    reason = type(error).__name__
    if stage == "snapshot" and files is not None:
        for index, path in enumerate(files):
            if isinstance(error, ValueError) and str(error) == f"unsafe root-owned file: {path}":
                reason = f"unsafe_file_{index}"
                break
    print(f"OBSERVABILITY_STAGE={stage} reason={reason}", file=sys.stderr)


def reconcile(revision: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("exact lowercase main SHA required")
    config = configuration()
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    details = STATE.lstat()
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != 0 or details.st_mode & 0o077:
        raise ValueError("unsafe root reconciliation state")
    with (STATE / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(dir=STATE) as scratch:
            source = Path(scratch)
            repository = STATE / "repository.git"
            helper = fetch_helper(revision, source, repository)
            # The exact reviewed helper supplies this run's allowlist, backup inventory
            # and apply logic. It does not reacquire or release the bootstrap lock.
            module(helper).reconcile_transaction(revision, config, source, repository)


if __name__ == "__main__":
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise SystemExit("root-owned helper accepts only one exact main SHA")
    try:
        reconcile(sys.argv[1])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"OBSERVABILITY_HOST=failed reason={type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from error
