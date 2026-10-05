#!/usr/bin/env python3
"""Discover approved running workers and activate once over private SSH.

The caller supplies reviewed OpenSSH configuration including the canonical-bastion
identity and pinned host keys. Access provisioning is a separate operational step.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
import tempfile
from pathlib import Path

from discovery import private_address


def activate(addresses, *, ssh_config: Path, user, run=subprocess.run):
    if re.fullmatch(r"[a-z_][a-z0-9_-]*", user) is None:
        raise ValueError("invalid deployment user")
    for address in addresses:
        run(
            [
                "ssh",
                "-F",
                str(ssh_config),
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=20",
                "-o",
                "ForwardAgent=no",
                "-J",
                "findme-worker-bastion",
                f"{user}@{private_address(address)}",
                "sudo -n /usr/bin/python3 /usr/local/lib/findme-worker/updater.py --ci-activation",
            ],
            check=True,
            timeout=2800,
        )


def run_ci(environment, *, run=subprocess.run):
    def require(name, pattern=r"[A-Za-z0-9][A-Za-z0-9_.-]*"):
        value = environment.get(name, "")
        if re.fullmatch(pattern, value) is None:
            raise ValueError("missing or invalid deployment input: " + name)
        return value

    projection = Path(environment["FINDME_ENV_FILE"])
    if stat.S_IMODE(projection.stat().st_mode) != 0o600:
        raise ValueError("private SSH projection required")
    values = dict(line.split("=", 1) for line in projection.read_text().splitlines())
    key = Path(json.loads(values["VM_SSH_KEY_FILE"]))
    if not key.is_file() or stat.S_IMODE(key.stat().st_mode) != 0o600:
        raise ValueError("private existing CI key required")
    if any(character in str(key) for character in ('"', "\n", "\r")):
        raise ValueError("invalid projected key path")
    host, user = require("VM_HOST"), require("VM_USER", r"[a-z_][a-z0-9_-]*")
    parameters = {
        "folder": require("WORKER_POOL_FOLDER_ID"),
        "groups": {
            pool: require(f"WORKER_POOL_{pool.upper()}_GROUP_ID") for pool in ("bulk", "selfie")
        },
        "subnet": require("WORKER_POOL_SUBNET_ID"),
        "security_group": require("WORKER_POOL_SECURITY_GROUP_ID"),
    }
    canonical_id = require("WORKER_POOL_CANONICAL_VM_ID")
    canonical_keys = environment.get("VM_SSH_KNOWN_HOSTS", "")
    if not canonical_keys.strip():
        raise ValueError("pinned canonical host keys required")
    root = Path(__file__).resolve().parents[2]
    program = (root / "deploy/worker-pools/discovery.py").read_text()
    parameters["canonical_id"] = canonical_id
    command = "python3 - " + shlex.quote(json.dumps(parameters))
    with tempfile.TemporaryDirectory(prefix="findme-worker-push-") as scratch:
        base = Path(scratch)
        known_hosts, ssh_config = base / "known_hosts", base / "ssh_config"
        known_hosts.write_text(canonical_keys + "\n")
        ssh_config.write_text(
            f'Host *\n    IdentityFile "{key}"\n'
            "    IdentitiesOnly yes\n    BatchMode yes\n    StrictHostKeyChecking yes\n"
            f'    UserKnownHostsFile "{known_hosts}"\n'
            "    GlobalKnownHostsFile /dev/null\n    ForwardAgent no\n    ConnectTimeout 20\n"
            f"Host findme-worker-bastion\n    HostName {host}\n    User {user}\n"
        )
        reply = run(
            ["ssh", "-F", str(ssh_config), "findme-worker-bastion", command],
            input=program,
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        payload = json.loads(reply.stdout)
        addresses = [private_address(address) for address in payload["addresses"]]
        if len(addresses) != len(set(addresses)) or len(addresses) > 4:
            raise ValueError("invalid worker discovery")
        scanned = {}
        for line in payload["host_keys"].splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            if len(parts) != 3 or parts[0] not in addresses or parts[1] != "ssh-ed25519":
                raise ValueError("invalid private worker host key")
            if parts[0] in scanned:
                raise ValueError("ambiguous private worker host key")
            scanned[parts[0]] = line
        if set(scanned) != set(addresses):
            raise ValueError("incomplete private worker host keys")
        known_hosts.write_text(canonical_keys + "\n" + "\n".join(scanned.values()) + "\n")
        activate(addresses, ssh_config=ssh_config, user="findme-deploy", run=run)


if __name__ == "__main__":
    try:
        run_ci(os.environ)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("WORKER_PUSH_RESULT=failure")
        raise SystemExit(1) from None
    print("WORKER_PUSH_RESULT=success")
