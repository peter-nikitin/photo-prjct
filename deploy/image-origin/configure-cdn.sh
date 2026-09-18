#!/bin/sh
set -eu

exec python3 - "$@" <<'PY'
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

NAME = "findme-gallery-image-origin"
ORIGIN = "img-origin.findme-photo.ru"
CNAME = "img.findme-photo.ru"
HEADER = "X-FindMe-Origin-Auth"
EDGE_TTL = 2_592_000
BROWSER_TTL = 21_600
ID = re.compile(r"[A-Za-z0-9_-]+\Z")


def fail(code: str) -> None:
    print(f"configure-cdn: {code}", file=sys.stderr)
    raise SystemExit(2)


def yc(*arguments: str) -> Any:
    result = subprocess.run(
        ["yc", *arguments], capture_output=True, text=True, check=False
    )
    if result.returncode:
        fail("provider_command_failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        value = result.stdout.strip()
        if value:
            return value
        fail("provider_response_invalid")


def one(values: Any, field: str, expected: str) -> dict[str, Any] | None:
    if not isinstance(values, list):
        fail("provider_response_invalid")
    matches = [item for item in values if isinstance(item, dict) and item.get(field) == expected]
    if len(matches) > 1:
        fail("provider_identity_ambiguous")
    return matches[0] if matches else None


def identifier(value: Any) -> str:
    raw = value.get("id") if isinstance(value, dict) else None
    rendered = str(raw) if raw is not None else ""
    if not ID.fullmatch(rendered):
        fail("provider_identity_invalid")
    return rendered


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def origin_actual(origin: dict[str, Any] | None) -> dict[str, Any] | None:
    if origin is None:
        return None
    origins = origin.get("origins")
    if not isinstance(origins, list) or len(origins) != 1 or not isinstance(origins[0], dict):
        fail("origin_group_drift")
    item = origins[0]
    actual = {
        "origins": [
            {
                "source": item.get("source"),
                "enabled": item.get("enabled"),
                "backup": item.get("backup", False),
            }
        ]
    }
    if actual != {
        "origins": [{"source": ORIGIN, "enabled": True, "backup": False}]
    }:
        fail("origin_group_drift")
    return actual


def certificate_actual(resource: dict[str, Any]) -> dict[str, str]:
    certificate = resource.get("ssl_certificate")
    if not isinstance(certificate, dict) or not isinstance(certificate.get("type"), str):
        fail("cdn_certificate_drift")
    if certificate["type"] == "DONT_USE":
        return {"type": "DONT_USE"}
    if certificate["type"] != "CM" or not isinstance(certificate.get("status"), str):
        fail("cdn_certificate_drift")
    data = certificate.get("data")
    manager = data.get("cm") if isinstance(data, dict) else None
    certificate_id = manager.get("id") if isinstance(manager, dict) else None
    if not isinstance(certificate_id, str) or not ID.fullmatch(certificate_id):
        fail("cdn_certificate_drift")
    return {
        "type": "CM",
        "status": certificate["status"],
        "certificate_manager_id": certificate_id,
    }


def resource_actual(resource: dict[str, Any] | None) -> dict[str, Any] | None:
    if resource is None:
        return None
    active = resource.get("active", False)
    origin_group_id = resource.get("origin_group_id")
    if not isinstance(active, bool) or not isinstance(origin_group_id, str) or not ID.fullmatch(origin_group_id):
        fail("cdn_resource_drift")
    return {
        "active": active,
        "origin_group_id": origin_group_id,
        "certificate": certificate_actual(resource),
    }


def load_state(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        fail("state_file_not_private")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("state_file_invalid")
    if not isinstance(value, dict):
        fail("state_file_invalid")
    return value


def persist(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=".image-origin-state-", dir=path.parent)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        json.dump(state, target, sort_keys=True)
        target.flush()
        os.fsync(target.fileno())
    os.replace(raw, path)


def projection() -> dict[str, str]:
    path = Path(os.environ.get("FINDME_ENV_FILE", ""))
    try:
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            fail("projection_not_private")
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        fail("projection_missing")

    def decode(value: str) -> str:
        if len(value) < 2 or value[0] != '"' or value[-1] != '"':
            fail("projection_invalid")
        result: list[str] = []
        index = 1
        while index < len(value) - 1:
            character = value[index]
            if character != "\\":
                result.append(character)
                index += 1
                continue
            index += 1
            if index >= len(value) - 1:
                fail("projection_invalid")
            result.append({"n": "\n", "r": "\r", "t": "\t"}.get(value[index], value[index]))
            index += 1
        return "".join(result)

    values: dict[str, str] = {}
    for line in lines:
        name, separator, encoded = line.partition("=")
        if separator:
            values[name] = decode(encoded)
    expected = {
        "GALLERY_CDN_TOKEN_SECRET",
        "IMAGE_ORIGIN_HEADER_SECRET",
        "PRIVATE_MEDIA_S3_ACCESS_KEY_ID",
    }
    if set(values) != expected:
        fail("projection_scope_invalid")
    if not 6 <= len(values["GALLERY_CDN_TOKEN_SECRET"]) <= 32:
        fail("cdn_secret_invalid")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", values["IMAGE_ORIGIN_HEADER_SECRET"]):
        fail("origin_header_invalid")
    return {
        "GALLERY_CDN_TOKEN_SECRET": values["GALLERY_CDN_TOKEN_SECRET"],
        "IMAGE_ORIGIN_HEADER_SECRET": values["IMAGE_ORIGIN_HEADER_SECRET"],
    }


def discover(state: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]:
    cloud = yc("config", "get", "cloud-id")
    folder = yc("config", "get", "folder-id")
    if not isinstance(cloud, str) or not ID.fullmatch(cloud):
        fail("cloud_invalid")
    if not isinstance(folder, str) or not ID.fullmatch(folder):
        fail("folder_invalid")
    if state.get("origin_group_id"):
        origin = yc("cdn", "origin-group", "get", "--id", str(state["origin_group_id"]), "--format", "json")
    else:
        listed_origin = one(
            yc("cdn", "origin-group", "list", "--folder-id", folder, "--format", "json"),
            "name",
            NAME,
        )
        origin = (
            yc("cdn", "origin-group", "get", "--id", identifier(listed_origin), "--format", "json")
            if listed_origin
            else None
        )
    if state.get("cdn_resource_id"):
        resource = yc("cdn", "resource", "get", "--id", str(state["cdn_resource_id"]), "--format", "json")
    else:
        resource = one(
            yc("cdn", "resource", "list", "--folder-id", folder, "--format", "json"),
            "cname",
            CNAME,
        )
        if resource:
            resource = yc("cdn", "resource", "get", "--id", identifier(resource), "--format", "json")
    if origin and origin.get("folder_id") != folder:
        fail("origin_group_drift")
    if resource and (resource.get("folder_id") != folder or resource.get("cname") != CNAME):
        fail("cdn_resource_drift")
    origin_actual(origin)
    resource_actual(resource)
    return cloud, folder, origin, resource


def desired() -> dict[str, Any]:
    return {
        "origin_host": ORIGIN,
        "cname": CNAME,
        "active": False,
        "origin_protocol": "https",
        "ignore_query_string": True,
        "ignore_cookie": True,
        "edge_ttl_seconds": EDGE_TTL,
        "browser_ttl_seconds": BROWSER_TTL,
        "static_request_header": HEADER,
        "secure_key": "projected",
        "origin_shielding": False,
        "logs": False,
        "dedicated_ip": False,
        "slicing": False,
        "compression": False,
        "cache_warming": False,
    }


def resource_arguments(
    origin_id: str,
    secrets: dict[str, str] | None,
    *,
    initial_create: bool,
) -> list[str]:
    header = secrets["IMAGE_ORIGIN_HEADER_SECRET"] if secrets else "<projected-origin-header>"
    secure = secrets["GALLERY_CDN_TOKEN_SECRET"] if secrets else "<projected-secure-key>"
    arguments = [
        "--origin-group-id", origin_id,
        "--origin-protocol", "https",
        "--active=false",
        "--cache-expiration-time", str(EDGE_TTL),
        "--browser-cache-expiration-time", str(BROWSER_TTL),
        "--ignore-query-string",
        "--ignore-cookie",
        "--host-header", ORIGIN,
        "--static-request-headers", f"{HEADER}={header}",
        "--secure-key", secure,
        "--folder-id", "{folder_id}",
        "--format", "json",
    ]
    if initial_create:
        arguments[5:5] = ["--dont-use-ssl-cert"]
    return arguments


def make_plan(
    cloud: str,
    folder: str,
    origin: dict[str, Any] | None,
    resource: dict[str, Any] | None,
) -> dict[str, Any]:
    origin_id = identifier(origin) if origin else "{origin_group_id}"
    operations: list[list[str]] = []
    if not origin:
        operations.append([
            "yc", "cdn", "origin-group", "create", "--name", NAME,
            "--origin", f"source={ORIGIN},enabled=true,backup=false",
            "--folder-id", folder, "--format", "json",
        ])
    action = "update" if resource else "create"
    identity = ["--id", identifier(resource)] if resource else ["--cname", CNAME]
    operations.append([
        "yc", "cdn", "resource", action, *identity,
        *resource_arguments(origin_id, None, initial_create=resource is None),
    ])
    operations[-1] = [folder if value == "{folder_id}" else value for value in operations[-1]]
    plan = {
        "mode": "dry-run",
        "cloud_id": cloud,
        "folder_id": folder,
        "resolved": {
            "origin_group_id": identifier(origin) if origin else None,
            "cdn_resource_id": identifier(resource) if resource else None,
        },
        "actual": {
            "origin_group": origin_actual(origin),
            "cdn_resource": resource_actual(resource),
        },
        "desired": desired(),
        "proposed_commands": operations,
    }
    plan["approval_nonce"] = hashlib.sha256(canonical(plan)).hexdigest()
    return plan


parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true")
parser.add_argument("--approval-nonce")
arguments = parser.parse_args(sys.argv[1:])
state_path = Path(os.environ["IMAGE_ORIGIN_CONTRACT_STATE"]) if os.environ.get("IMAGE_ORIGIN_CONTRACT_STATE") else None
state = load_state(state_path)
cloud_id, folder_id, existing_origin, existing_resource = discover(state)
plan = make_plan(cloud_id, folder_id, existing_origin, existing_resource)
if not arguments.apply:
    if arguments.approval_nonce:
        fail("approval_requires_apply")
    print(json.dumps(plan, indent=2, sort_keys=True))
    raise SystemExit(0)
if arguments.approval_nonce != plan["approval_nonce"]:
    fail("approval_nonce_mismatch")
if state_path is None:
    fail("state_path_required")
secrets = projection()
origin = existing_origin
if not origin:
    origin = yc(
        "cdn", "origin-group", "create", "--name", NAME,
        "--origin", f"source={ORIGIN},enabled=true,backup=false",
        "--folder-id", folder_id, "--format", "json",
    )
    state["origin_group_id"] = identifier(origin)
    persist(state_path, state)
origin_id = identifier(origin)
if state.get("origin_group_id") != origin_id:
    state["origin_group_id"] = origin_id
    persist(state_path, state)
resource_args = [
    folder_id if value == "{folder_id}" else value
    for value in resource_arguments(origin_id, secrets, initial_create=existing_resource is None)
]
if existing_resource:
    resource = yc("cdn", "resource", "update", "--id", identifier(existing_resource), *resource_args)
else:
    resource = yc("cdn", "resource", "create", "--cname", CNAME, *resource_args)
state["cdn_resource_id"] = identifier(resource)
persist(state_path, state)
print(json.dumps({
    "mode": "applied",
    "cloud_id": cloud_id,
    "folder_id": folder_id,
    "origin_group_id": state["origin_group_id"],
    "cdn_resource_id": state["cdn_resource_id"],
    "active": False,
}, indent=2, sort_keys=True))
PY
