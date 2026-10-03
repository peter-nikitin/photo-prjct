"""Root-owned local worker slots and one lock for replacement/host retirement."""

from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from uuid import UUID

SLOTS = {"a": 9101, "b": 9102}


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name("." + path.name)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_json(path):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError("host state too large")
    return json.loads(raw)


def validate_slot(state):
    if (
        not isinstance(state, dict)
        or state.get("slot") not in SLOTS
        or re.fullmatch(r"[0-9a-f]{40}", state.get("build", "")) is None
        or re.fullmatch(
            r"ghcr\.io/[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9._-]*-worker@sha256:[0-9a-f]{64}",
            state.get("digest", ""),
        )
        is None
    ):
        raise ValueError("invalid active worker slot")
    if "registration_generation" in state:
        if str(UUID(state["registration_generation"])) != state["registration_generation"]:
            raise ValueError("invalid active worker generation")
    return state


def active_slot(*, root=Path("/")):
    return validate_slot(read_json(root / "etc/findme-worker/active.json"))


def save_active(state, *, root=Path("/")):
    private_json(root / "etc/findme-worker/active.json", validate_slot(state))


@contextmanager
def host_lock(*, root=Path("/")):
    base = root / "etc/findme-worker"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(base / "host.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def status(port):
    if port not in SLOTS.values():
        raise ValueError("invalid worker status port")
    with build_opener(ProxyHandler({}), RejectRedirects()).open(
        f"http://127.0.0.1:{port}/status", timeout=3
    ) as response:
        raw = response.read(4097)
    if len(raw) > 4096:
        raise ValueError("worker status too large")
    reply = json.loads(raw)
    if not isinstance(reply, dict) or set(reply) != {"ready", "registration_generation"}:
        raise ValueError("invalid worker status")
    if (
        type(reply["ready"]) is not bool
        or str(UUID(reply["registration_generation"])) != reply["registration_generation"]
    ):
        raise ValueError("worker not admitted")
    return reply
