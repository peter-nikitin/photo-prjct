#!/usr/bin/env python3
"""Close only the pinned initial receipt using the installed release authority."""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

BUILD = "18b8c27eb18b65fcf663b0e713df6142ebd39321"
DIGEST = "sha256:1b4073b781995e89b0ee0025a98a7739f4e98b776ad91c77c643a483ef2f1bbc"
GROUPS = {"bulk": "cl13d5ffaml0f42s0s0s", "selfie": "cl1136g0efv0pl7u3d9a"}
FILES = {"previous.env", "deployed-image", "package-path", "worker-topology"}


def owned_package(root, path):
    package = Path(path)
    if (
        package.parent != root
        or not re.fullmatch(r"\.deployment-previous\.[A-Za-z0-9_-]+", package.name)
        or package.is_symlink()
        or package.resolve() != package
    ):
        raise ValueError("unsafe recovery package")
    return package


def recovery_scope(root):
    recovery = root / ".deployment-recovery"
    if (
        recovery.is_symlink()
        or not recovery.is_dir()
        or {p.name for p in recovery.iterdir()} != FILES
    ):
        raise ValueError("unsafe recovery snapshot")
    if any(p.is_symlink() or not p.is_file() for p in recovery.iterdir()):
        raise ValueError("unsafe recovery snapshot file")
    package = owned_package(root, (recovery / "package-path").read_text().strip())
    if not package.is_dir():
        raise ValueError("missing original recovery package")
    return {
        "package": str(package),
        "files": {
            name: hashlib.sha256((recovery / name).read_bytes()).hexdigest() for name in FILES
        },
    }


def cleanup_owned(root, scope):
    package = owned_package(root, scope["package"])
    recovery = root / ".deployment-recovery"
    if recovery.exists() or recovery.is_symlink():
        if recovery.is_symlink() or not recovery.is_dir():
            raise ValueError("recovery snapshot ownership changed")
        if {p.name for p in recovery.iterdir()} - FILES:
            raise ValueError("unexpected recovery snapshot file")
        for path in recovery.iterdir():
            if (
                path.is_symlink()
                or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != scope["files"][path.name]
            ):
                raise ValueError("recovery snapshot ownership changed")
    if package.exists():
        shutil.rmtree(package)
    if recovery.exists():
        for path in recovery.iterdir():
            path.unlink()
        recovery.rmdir()
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def health_gates(root, host):
    env = {
        **os.environ,
        "DEPLOY_ROOT": str(root),
        "COMPOSE_PROJECT_NAME": "photo-prjct",
        "PUBLIC_DOMAIN": "findme-photo.ru",
    }

    def run(command):
        host.run(command, check=True, capture_output=True, timeout=90, env=env)

    run(["sudo", "-n", "/usr/local/sbin/findme-worker-pool-metrics", "verify"])
    collector = host.run(
        [
            "systemctl",
            "show",
            "findme-worker-pool-metrics.service",
            "--property=Result",
            "--property=ExecMainExitTimestamp",
            "--property=ExecMainStatus",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    properties = dict(line.split("=", 1) for line in collector.splitlines())
    timestamp = host.run(
        ["date", "--date", properties["ExecMainExitTimestamp"], "+%s"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    if (
        properties["Result"] != "success"
        or properties["ExecMainStatus"] != "0"
        or not 0 <= time.time() - int(timestamp) <= 90
    ):
        raise ValueError("native collector stale or failed")
    run(["sudo", "-n", "/usr/local/sbin/findme-selfie-observability", "verify"])
    run(["sh", str(root / "deploy/verify-public-edge.sh")])
    run(
        host.compose
        + [
            "exec",
            "-T",
            "web",
            "python",
            "-c",
            "import urllib.request; "
            "urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=10)",
        ]
    )
    private = host.run(
        [
            "curl",
            "--request",
            "POST",
            "--silent",
            "--show-error",
            "--max-time",
            "15",
            "--resolve",
            "findme-photo.ru:8443:127.0.0.1",
            "--output",
            "/dev/null",
            "--write-out",
            "%{http_code}",
            "https://findme-photo.ru:8443/internal/photo-processing/v1/claim",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    if private != "401":
        raise ValueError("private worker API authentication health failed")
    for service in ("web", "nginx"):
        container = host.run(
            host.compose + ["ps", "-q", service],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        tag = host.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{.HostConfig.LogConfig.Type}}|{{index .HostConfig.LogConfig.Config "tag"}}',
                container,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        if tag != "journald|findme.service=" + service:
            raise ValueError("application logging contract mismatch")
    probe = str(uuid4())
    code = (
        "import logging; from selfie_search.observability "
        "import SelfieEventName, emit_selfie_event; "
        "emit_selfie_event(logging.getLogger('selfie_search'), "
        "event=SelfieEventName.OBSERVABILITY_PROBE, probe_id='" + probe + "')"
    )
    run(
        host.compose
        + [
            "exec",
            "-T",
            "web",
            "sh",
            "-c",
            'python manage.py shell --no-imports -c "$1" 2>/proc/1/fd/2',
            "sh",
            code,
        ]
    )
    run(["sudo", "-n", "/usr/local/sbin/findme-selfie-observability", "verify-probe", probe])


def finalize(root, release, host, *, gates=health_gates):
    root = Path(root)
    journal = release.Journal(root / "worker-pools-release.json")
    host.journal = journal
    candidate = journal.data["candidate"]
    manifest = candidate["manifest"]
    config = manifest["configuration"]
    if (
        journal.data.get("previous") is not None
        or journal.data.get("pending")
        or config["worker_build"] != BUILD
        or config["worker_image"].split("@")[-1] != DIGEST
        or config["pool_max_size"] != 1
        or {name: config["groups"][name]["id"] for name in GROUPS} != GROUPS
        or release.canonical_instance_id() != config["canonical_vm_id"]
        or (root / "deployed-image").read_text().strip().split(":")[-1] != BUILD
    ):
        raise ValueError("initial release pin mismatch")
    release.validate_manifest(manifest)
    marker = root / "worker-pools-current.json"
    if journal.data["phase"] == "committed":
        if not marker.is_file() or json.loads(marker.read_text()) != candidate:
            raise ValueError("committed marker mismatch")
        scope = journal.data.get("initial_finalizer_recovery")
        if not scope or journal.data.get("initial_finalizer_cleaned"):
            raise ValueError("already finalized")
        cleanup_owned(root, scope)
    else:
        if journal.data["phase"] != "verified" or (
            marker.exists() and json.loads(marker.read_text()) != candidate
        ):
            raise ValueError("initial receipt is not verified")
        scope = recovery_scope(root)
        gates(root, host)
        host.verify_web(candidate["proof"])
        release.verify_image(candidate["proof"]["web_image"], BUILD)
        release.verify_image(config["worker_image"], BUILD)
        release.verify_fleet(host, manifest)
        status = host.control("status")
        if set(status) != set(GROUPS):
            raise ValueError("missing coordinator pool")
        for name, row in status.items():
            if (
                name not in GROUPS
                or row["group_id"] != GROUPS[name]
                or row["claims_paused"]
                or not row["local_claims_paused"]
                or row["local_live_attempts"]
                or row["live_attempts"] > 1
            ):
                raise ValueError("initial live lease or claim anomaly")
        if recovery_scope(root) != scope:
            raise ValueError("recovery ownership changed")
        journal.data["initial_finalizer_recovery"] = scope
        journal.save()
        release.commit_release(journal, marker)
        cleanup_owned(root, scope)
    journal.data["initial_finalizer_cleaned"] = True
    journal.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--expected-web-sha", required=True)
    parser.add_argument("--expected-worker-digest", required=True)
    parser.add_argument("--expected-bulk-group", required=True)
    parser.add_argument("--expected-selfie-group", required=True)
    args = parser.parse_args()
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != args.source_sha256 or (
        args.expected_web_sha,
        args.expected_worker_digest,
        args.expected_bulk_group,
        args.expected_selfie_group,
    ) != (BUILD, DIGEST, GROUPS["bulk"], GROUPS["selfie"]):
        raise ValueError("reviewed source or expected pins mismatch")
    root = Path("/opt/photo-prjct")
    with (root / ".deployment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sys.path.insert(0, str(root / "deploy/worker-pools/_canonical"))
        spec = importlib.util.spec_from_file_location(
            "installed_release", root / "deploy/worker-pools/release.py"
        )
        release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(release)
        from processing.services.worker_pool_cloud import metadata_token

        journal = release.Journal(root / "worker-pools-release.json")
        host = release.Host(root, release.provision_module().Cloud(metadata_token()), journal)
        finalize(root, release, host)
    print("INITIAL_REMOTE_RELEASE_FINALIZED")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print(
            "initial remote finalization failed; inspect durable receipt and exact recovery inputs",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
