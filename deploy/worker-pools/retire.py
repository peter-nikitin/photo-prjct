#!/usr/bin/env python3
"""One host-owned retirement poll. No cloud credential or privileged worker container.

Install on the host with a root-owned fleet environment and identity file. A systemd
timer invokes this helper; the controller's durable grant is the only stop authority.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID

API = "https://findme-photo.ru:8443/internal/photo-processing/v1/members/retire"
BOOT_PATH = Path("/proc/sys/kernel/random/boot_id")
INSTANCE_PATH = Path("/etc/findme-worker/instance-id")


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def own_identity() -> dict[str, str]:
    identity = {
        "pool": os.environ["PHOTO_WORKER_POOL"],
        "instance_id": INSTANCE_PATH.read_text().strip(),
        "boot_id": BOOT_PATH.read_text().strip(),
        "worker_build": os.environ["PHOTO_WORKER_BUILD"],
    }
    if (
        identity["pool"] not in {"bulk", "selfie"}
        or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", identity["instance_id"]) is None
        or re.fullmatch(r"[0-9a-f]{40}", identity["worker_build"]) is None
        or str(UUID(identity["boot_id"])) != identity["boot_id"]
    ):
        raise ValueError("invalid host identity")
    return identity


def permission(identity: dict[str, str]) -> dict:
    token = os.environ["PHOTO_PROCESSING_FLEET_TOKEN"]
    if not token or not token.isascii() or any(char.isspace() for char in token):
        raise ValueError("invalid fleet credential")
    request = Request(
        API,
        data=json.dumps(identity).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    # Default HTTPS handler verifies the system CA and canonical hostname. Host projection
    # resolves that name to the private edge. No redirects or environment proxy fallback.
    with build_opener(ProxyHandler({}), RejectRedirects()).open(request, timeout=15) as response:
        raw = response.read(4097)
    if len(raw) > 4096:
        raise ValueError("invalid retirement response")
    reply = json.loads(raw)
    if not isinstance(reply, dict) or set(reply) != {"grant"}:
        raise ValueError("invalid retirement response")
    return reply


def retire(identity: dict[str, str], reply: dict, *, dry_run: bool, run=subprocess.run) -> str:
    if set(reply) != {"grant"}:
        raise ValueError("invalid retirement response")
    grant = reply["grant"]
    if grant is None:
        return "serving"
    if (
        not isinstance(grant, dict)
        or set(grant) != {"instance_id", "boot_id", "grant_id"}
        or grant["instance_id"] != identity["instance_id"]
        or grant["boot_id"] != identity["boot_id"]
    ):
        raise ValueError("retirement host binding mismatch")
    if str(UUID(grant["grant_id"])) != grant["grant_id"]:
        raise ValueError("invalid retirement grant")
    if dry_run:
        return "would_retire"
    run(
        ["docker", "stop", "--time", "930", "findme-photo-worker"],
        check=True,
        timeout=960,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    run(
        ["systemctl", "poweroff"],
        check=True,
        timeout=30,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return "retiring"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="stop this host only after durable permission"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="request permission without host actions"
    )
    args = parser.parse_args()
    try:
        identity = own_identity()
        reply = permission(identity)
        if own_identity() != identity:
            raise ValueError("host changed during retirement request")
        print(retire(identity, reply, dry_run=not args.apply or args.dry_run))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print("retirement_unavailable")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
