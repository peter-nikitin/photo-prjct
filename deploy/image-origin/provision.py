# ruff: noqa: E501
"""Dry-run-first provisioning contract for the isolated gallery image origin."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import ipaddress
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

NAME = "findme-gallery-image-origin"
CNAME = "img.findme-photo.ru"
LOCKBOX = "e6q85jjl76r45maigtfb"
PACKAGE = Path(__file__).resolve().parent
PUBLIC_KEY = PACKAGE / "workflow-ssh-key.pub"
CLOUD_INIT = PACKAGE / "cloud-init.sh"
PUBLIC_KEY_VALUE = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJ1h18E0nI6hijk2Ua9fG7hHcWfReZCn3fg8TeiQOVCJ findme-staging-lockbox-2026-08-08\n"
SECRET_KEYS = [
    "GALLERY_CDN_TOKEN_SECRET",
    "GALLERY_IMGPROXY_KEY",
    "GALLERY_IMGPROXY_SALT",
    "IMAGE_ORIGIN_HEADER_SECRET",
    "IMAGE_ORIGIN_S3_ACCESS_KEY_ID",
    "IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY",
]
BOOTSTRAP_KEYS = {"GALLERY_CDN_TOKEN_SECRET", "IMAGE_ORIGIN_HEADER_SECRET"}
INITIALIZATION_KEYS = [key for key in SECRET_KEYS if key not in BOOTSTRAP_KEYS]
ID = re.compile(r"[A-Za-z0-9_-]+\Z")
BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
KEY = re.compile(r"[A-Za-z0-9_./-]+\Z")
HEADER = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")
STATE_KEYS = {
    "service_account_id",
    "security_group_id",
    "reserved_address_id",
    "reserved_address_ipv4",
    "vm_id",
    "origin_group_id",
    "cdn_resource_id",
    "certificate_id",
    "access_key_resource_id",
    "lockbox_version_id",
    "bootstrap_seeded",
    "secrets_initialized",
}


def fail(code: str) -> None:
    print(f"[image-origin-provision] status=error code={code}", file=sys.stderr)
    raise SystemExit(2)


def env(name: str, pattern: re.Pattern[str] | None = None) -> str:
    value = os.environ.get(name, "")
    if not value or pattern and not pattern.fullmatch(value):
        fail(f"invalid_{name.lower()}")
    return value


def yc(*args: str, stdin: bytes | None = None) -> Any:
    try:
        result = subprocess.run(["yc", *args], input=stdin, capture_output=True, check=False)
    except OSError:
        fail("yc_unavailable")
    if result.returncode:
        fail("yc_command_failed")
    raw = result.stdout.decode().strip()
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return raw


def ident(value: Any, code: str = "inventory_invalid") -> str:
    result = value.get("id") if isinstance(value, dict) else None
    if not isinstance(result, str) or not ID.fullmatch(result):
        fail(code)
    return result


def one(values: Any, key: str, value: str) -> dict[str, Any] | None:
    if not isinstance(values, list):
        fail("inventory_invalid")
    matches = [item for item in values if isinstance(item, dict) and item.get(key) == value]
    if len(matches) > 1:
        fail("inventory_ambiguous")
    return matches[0] if matches else None


def has_active_profile(profiles: Any) -> bool:
    if isinstance(profiles, list):
        return any(item.get("is_active") is True for item in profiles if isinstance(item, dict))
    if isinstance(profiles, str):
        return any(
            len(fields) == 2 and fields[1] == "ACTIVE"
            for line in profiles.splitlines()
            if (fields := line.split())
        )
    return False


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def normalize_policy(value: Any) -> dict[str, Any]:
    value = {"Version": "2012-10-17", "Statement": []} if value is None else value
    if not isinstance(value, dict):
        fail("policy_invalid")
    statements = value.get("Statement", [])
    statements = [statements] if isinstance(statements, dict) else statements
    if not isinstance(statements, list) or any(not isinstance(item, dict) for item in statements):
        fail("policy_invalid")
    return {
        **value,
        "Version": value.get("Version", "2012-10-17"),
        "Statement": sorted(statements, key=lambda item: canon(item)),
    }


def policy(value: Any, bucket: str, principal: str | None) -> dict[str, Any]:
    before = normalize_policy(value)
    statements = [
        item
        for item in before["Statement"]
        if item.get("Sid") != "AllowFindMeGalleryImageOriginPreviewReads"
    ]
    if principal:
        statements.append(
            {
                "Sid": "AllowFindMeGalleryImageOriginPreviewReads",
                "Effect": "Allow",
                "Principal": {"CanonicalUser": principal},
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket}/derivatives/previews/*",
            }
        )
    after = normalize_policy({**before, "Statement": statements})
    return {
        "before": before,
        "after": after,
        "before_sha256": hashlib.sha256(canon(before)).hexdigest(),
        "after_sha256": hashlib.sha256(canon(after)).hexdigest(),
        "normalized_diff": list(
            difflib.unified_diff(
                json.dumps(before, indent=2, sort_keys=True).splitlines(),
                json.dumps(after, indent=2, sort_keys=True).splitlines(),
                lineterm="",
            )
        ),
    }


def load_state(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        info, value = path.lstat(), json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        fail("state_invalid")
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or not isinstance(value, dict)
        or set(value) - STATE_KEYS
    ):
        fail("state_invalid")
    return value


def persist(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as target:
            json.dump(value, target, indent=2, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


def projected(expected: set[str], optional: set[str] | None = None) -> dict[str, str]:
    path = Path(os.environ.get("FINDME_ENV_FILE", ""))
    try:
        info, text = path.lstat(), path.read_text()
    except OSError:
        fail("projection_invalid")
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        fail("projection_invalid")
    values: dict[str, str] = {}
    escapes = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t", "$": "$"}
    for line in text.splitlines():
        key, sep, raw = line.partition("=")
        if (
            not sep
            or key in values
            or not key.isidentifier()
            or not key.isupper()
            or len(raw) < 2
            or raw[0] != '"'
            or raw[-1] != '"'
        ):
            fail("projection_invalid")
        out, index = [], 1
        while index < len(raw) - 1:
            if raw[index] != "\\":
                out.append(raw[index])
                index += 1
                continue
            index += 1
            if index >= len(raw) - 1 or raw[index] not in escapes:
                fail("projection_invalid")
            out.append(escapes[raw[index]])
            index += 1
        values[key] = "".join(out)
    optional = optional or set()
    if not expected <= set(values) <= expected | optional:
        fail("projection_scope_invalid")
    return {key: values[key] for key in expected}


def rules(cidr: str) -> list[dict[str, Any]]:
    return [
        {"direction": direction, "protocol_name": protocol, "port": port, "v4_cidr_blocks": [block]}
        for direction, protocol, port, block in [
            ("INGRESS", "TCP", "22", cidr),
            ("INGRESS", "TCP", "80", "0.0.0.0/0"),
            ("INGRESS", "TCP", "443", "0.0.0.0/0"),
            ("EGRESS", "TCP", "443", "0.0.0.0/0"),
            ("EGRESS", "TCP", "53", "0.0.0.0/0"),
            ("EGRESS", "UDP", "53", "0.0.0.0/0"),
        ]
    ]


def bastion_rule(security_group_id: str) -> dict[str, Any]:
    return {
        "direction": "INGRESS",
        "protocol_name": "TCP",
        "port": "22",
        "security_group_id": security_group_id,
    }


def desired_rules(cidr: str, security_group_id: str) -> list[dict[str, Any]]:
    return rules(cidr) + [bastion_rule(security_group_id)]


def normalized_rules(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        fail("security_group_drift")
    normalized = []
    for item in value:
        if not isinstance(item, dict):
            fail("security_group_drift")
        ports = item.get("ports", {}) if isinstance(item, dict) else {}
        cidrs = item.get("cidr_blocks", {}) if isinstance(item, dict) else {}
        from_port, to_port = ports.get("from_port"), ports.get("to_port")
        blocks = cidrs.get("v4_cidr_blocks", [])
        security_group_id = item.get("security_group_id")
        if (
            not isinstance(from_port, str)
            or from_port != to_port
            or not isinstance(blocks, list)
            or (len(blocks) != 1) == (not isinstance(security_group_id, str))
            or isinstance(security_group_id, str)
            and not ID.fullmatch(security_group_id)
        ):
            fail("security_group_drift")
        normalized.append(
            {
                "direction": item.get("direction"),
                "protocol_name": item.get("protocol_name"),
                "port": from_port,
                **(
                    {"security_group_id": security_group_id}
                    if security_group_id
                    else {"v4_cidr_blocks": blocks}
                ),
            }
        )
    return sorted(normalized, key=lambda item: canon(item))


def safe_actual(found: dict[str, Any]) -> dict[str, Any]:
    def fields(value: Any, names: tuple[str, ...]) -> dict[str, Any] | None:
        return {name: value.get(name) for name in names} if isinstance(value, dict) else None

    return {
        "service_account": fields(found["sa"], ("id", "name", "folder_id")),
        "security_group": (
            {
                **(fields(found["sg"], ("id", "name", "folder_id", "network_id")) or {}),
                "rules": normalized_rules(found["sg"].get("rules")),
            }
            if found["sg"]
            else None
        ),
        "reserved_address": (
            {
                **(fields(found["address"], ("id", "name", "folder_id", "reserved")) or {}),
                "address": found["ipv4"],
                "zone_id": found["address"].get("external_ipv4_address", {}).get("zone_id"),
            }
            if found["address"]
            else None
        ),
        "vm": (
            {
                **(
                    fields(
                        found["vm"],
                        ("id", "name", "folder_id", "zone_id", "platform_id", "service_account_id"),
                    )
                    or {}
                ),
                "resources": {
                    key: found["vm"].get("resources", {}).get(key)
                    for key in ("cores", "core_fraction", "memory")
                },
                "preemptible": found["vm"].get("scheduling_policy", {}).get("preemptible", False),
                "boot_disk": fields(
                    found["boot_disk"],
                    ("id", "folder_id", "type_id", "size", "source_image_id"),
                ),
                "interface_count": len(found["vm"].get("network_interfaces", [])),
                "secondary_disk_count": len(found["vm"].get("secondary_disks", [])),
                "user_data_sha256": found["vm_user_data_sha256"],
            }
            if found["vm"]
            else None
        ),
        "origin_group": fields(found["origin"], ("id", "name", "folder_id")),
        "cdn_resource": (
            {
                **(fields(found["cdn"], ("id", "cname", "folder_id", "origin_group_id")) or {}),
                "secure_key_configured": bool(
                    found["cdn"].get("options", {}).get("secure_key", {}).get("key")
                ),
                "static_request_header_names": sorted(
                    found["cdn"].get("options", {}).get("static_request_headers", {})
                ),
            }
            if found["cdn"]
            else None
        ),
        "certificate": fields(found["cert"], ("id", "name", "folder_id", "domains")),
        "service_account_roles": found["service_account_roles"],
    }


def task6_refresh(cloud: str, folder: str) -> dict[str, Any]:
    return {
        "read_only": True,
        "live_execution": "not-performed-by-task-4",
        "commands": [
            ["yc", "config", "profile", "list"],
            ["yc", "config", "get", "cloud-id"],
            ["yc", "config", "get", "folder-id"],
            ["yc", "compute", "instance", "list", "--folder-id", folder, "--format", "json"],
            ["yc", "compute", "disk", "list", "--folder-id", folder, "--format", "json"],
            ["yc", "vpc", "network", "list", "--folder-id", folder, "--format", "json"],
            ["yc", "vpc", "subnet", "list", "--folder-id", folder, "--format", "json"],
            ["yc", "vpc", "security-group", "list", "--folder-id", folder, "--format", "json"],
            ["yc", "vpc", "address", "list", "--folder-id", folder, "--format", "json"],
            ["yc", "iam", "service-account", "list", "--folder-id", folder, "--format", "json"],
            *[
                [
                    "yc",
                    "quota-manager",
                    "quota-limit",
                    "list",
                    "--resource-type",
                    "resource-manager.cloud",
                    "--resource-id",
                    cloud,
                    "--service",
                    service,
                    "--format",
                    "json",
                ]
                for service in ("compute", "vpc", "cdn")
            ],
        ],
        "pricing": {
            "evidence": "refresh-required-before-approval",
            "source": "Official Yandex Cloud calculator and CDN pricing pages",
            "items": [
                "vm_standard_v3_2vcpu_4gib",
                "network_hdd_20gib",
                "reserved_public_ipv4",
                "cdn_base_resource",
                "expected_egress",
            ],
        },
    }


def get_or_list(
    state: dict[str, Any],
    state_key: str,
    prefix: tuple[str, ...],
    folder: str,
    identity_key: str,
    identity: str,
) -> dict[str, Any] | None:
    if state.get(state_key):
        result = yc(*prefix, "get", "--id", state[state_key], "--format", "json")
        if ident(result) != state[state_key]:
            fail("state_resource_drift")
        return result
    return one(
        yc(*prefix, "list", "--folder-id", folder, "--format", "json"), identity_key, identity
    )


def discover(state: dict[str, Any], cfg: dict[str, str]) -> dict[str, Any]:
    profiles = yc("config", "profile", "list", "--format", "json")
    if not has_active_profile(profiles):
        fail("profile_invalid")
    cloud, folder = yc("config", "get", "cloud-id"), yc("config", "get", "folder-id")
    zone, image = (
        yc("compute", "zone", "get", cfg["zone"], "--format", "json"),
        yc("compute", "image", "get", "--id", cfg["image"], "--format", "json"),
    )
    if ident(zone) != cfg["zone"]:
        fail("zone_invalid")
    if ident(image) != cfg["image"] or image.get("status") != "READY":
        fail("boot_image_invalid")
    canonical_vm = yc(
        "compute",
        "instance",
        "get",
        "--id",
        cfg["canonical_vm"],
        "--folder-id",
        folder,
        "--format",
        "json",
    )
    if canonical_vm.get("folder_id") != folder:
        fail("canonical_network_invalid")
    try:
        interfaces = canonical_vm["network_interfaces"]
        if len(interfaces) != 1:
            raise ValueError
        subnet_id = interfaces[0]["subnet_id"]
        security_group_ids = interfaces[0]["security_group_ids"]
        if (
            not isinstance(security_group_ids, list)
            or len(security_group_ids) != 1
            or not isinstance(security_group_ids[0], str)
            or not ID.fullmatch(security_group_ids[0])
        ):
            fail("canonical_security_group_invalid")
        canonical_security_group_id = security_group_ids[0]
    except (KeyError, IndexError, TypeError, ValueError):
        fail("canonical_network_invalid")
    subnet = yc(
        "vpc", "subnet", "get", "--id", subnet_id, "--folder-id", folder, "--format", "json"
    )
    if subnet.get("folder_id") != folder or subnet.get("zone_id") != cfg["zone"]:
        fail("canonical_network_invalid")
    network_id = subnet.get("network_id")
    network = yc(
        "vpc", "network", "get", "--id", network_id, "--folder-id", folder, "--format", "json"
    )
    if network.get("folder_id") != folder:
        fail("canonical_network_invalid")
    canonical_security_group = yc(
        "vpc",
        "security-group",
        "get",
        "--id",
        canonical_security_group_id,
        "--folder-id",
        folder,
        "--format",
        "json",
    )
    if (
        ident(canonical_security_group, "canonical_security_group_invalid")
        != canonical_security_group_id
        or canonical_security_group.get("folder_id") != folder
        or canonical_security_group.get("network_id") != network_id
    ):
        fail("canonical_security_group_invalid")
    bucket = yc(
        "storage",
        "bucket",
        "get",
        cfg["bucket"],
        "--full",
        "--folder-id",
        folder,
        "--format",
        "json",
    )
    if bucket.get("name") != cfg["bucket"] or bucket.get("folder_id") != folder:
        fail("bucket_invalid")
    resources = {
        "sa": get_or_list(
            state, "service_account_id", ("iam", "service-account"), folder, "name", NAME
        ),
        "sg": get_or_list(
            state, "security_group_id", ("vpc", "security-group"), folder, "name", NAME
        ),
        "address": get_or_list(
            state, "reserved_address_id", ("vpc", "address"), folder, "name", NAME
        ),
        "vm": get_or_list(state, "vm_id", ("compute", "instance"), folder, "name", NAME),
        "origin": get_or_list(
            state, "origin_group_id", ("cdn", "origin-group"), folder, "name", NAME
        ),
        "cdn": get_or_list(state, "cdn_resource_id", ("cdn", "resource"), folder, "cname", CNAME),
        "cert": get_or_list(
            state, "certificate_id", ("certificate-manager", "certificate"), folder, "name", NAME
        ),
    }
    bindings = yc(
        "resource-manager",
        "folder",
        "list-access-bindings",
        "--id",
        folder,
        "--format",
        "json",
    )
    if not isinstance(bindings, list):
        fail("service_account_role_inventory_invalid")
    service_account_roles = sorted(
        {
            item.get("role_id")
            for item in bindings
            if isinstance(item, dict)
            and isinstance(item.get("subject"), dict)
            and item["subject"].get("type") == "serviceAccount"
            and resources["sa"]
            and item["subject"].get("id") == ident(resources["sa"])
            and isinstance(item.get("role_id"), str)
        }
    )
    if set(service_account_roles) - {"monitoring.editor"}:
        fail("service_account_role_drift")
    if resources["sa"] and resources["sa"].get("folder_id") != folder:
        fail("service_account_drift")
    origin_access_configured = False
    if resources["sg"]:
        actual_rules = normalized_rules(resources["sg"].get("rules"))
        pre_bastion = sorted(rules(cfg["cidr"]), key=lambda item: canon(item))
        post_bastion = sorted(
            desired_rules(cfg["cidr"], canonical_security_group_id), key=lambda item: canon(item)
        )
        if (
            resources["sg"].get("folder_id") != folder
            or resources["sg"].get("network_id") != network_id
            or actual_rules not in (pre_bastion, post_bastion)
        ):
            fail("security_group_drift")
        origin_access_configured = actual_rules == post_bastion
    ipv4 = None
    if resources["address"]:
        external = resources["address"].get("external_ipv4_address", {})
        if (
            resources["address"].get("folder_id") != folder
            or external.get("zone_id") != cfg["zone"]
            or resources["address"].get("reserved") is not True
        ):
            fail("address_drift")
        ipv4 = external.get("address")
        try:
            ipaddress.IPv4Address(ipv4)
        except (ipaddress.AddressValueError, TypeError):
            fail("address_drift")
    boot_disk = None
    if resources["vm"]:
        vm, sa, sg = resources["vm"], resources["sa"], resources["sg"]
        if (
            vm.get("folder_id"),
            vm.get("zone_id"),
            vm.get("platform_id"),
            vm.get("service_account_id"),
        ) != (folder, cfg["zone"], "standard-v3", ident(sa) if sa else None):
            fail("vm_drift")
        metadata = vm.get("metadata", {})
        user_data = metadata.get("user-data") if isinstance(metadata, dict) else None
        if not isinstance(user_data, str) or user_data.encode() != cfg["cloud_init"]:
            fail("vm_drift")
        vm_user_data_sha256 = hashlib.sha256(user_data.encode()).hexdigest()
        resources_value = vm.get("resources", {})
        if (
            resources_value.get("cores") != "2"
            or resources_value.get("core_fraction") != "100"
            or resources_value.get("memory") != "4294967296"
            or vm.get("scheduling_policy", {}).get("preemptible", False) is not False
        ):
            fail("vm_drift")
        try:
            interfaces, boot = vm["network_interfaces"], vm["boot_disk"]
        except (KeyError, IndexError, TypeError):
            fail("vm_drift")
        if len(interfaces) != 1 or vm.get("secondary_disks", []) != []:
            fail("vm_drift")
        interface = interfaces[0]
        disk_id = boot.get("disk_id") if isinstance(boot, dict) else None
        disk = yc(
            "compute", "disk", "get", "--id", disk_id, "--folder-id", folder, "--format", "json"
        )
        if (
            interface.get("subnet_id") != subnet_id
            or interface.get("security_group_ids") != ([ident(sg)] if sg else None)
            or interface.get("primary_v4_address", {}).get("one_to_one_nat", {}).get("address")
            != ipv4
            or ident(disk) != disk_id
            or disk.get("folder_id") != folder
            or disk.get("source_image_id") != cfg["image"]
            or disk.get("type_id") != "network-hdd"
            or disk.get("size") != "21474836480"
        ):
            fail("vm_drift")
        boot_disk = disk
    for item, code in [
        (resources["origin"], "origin_group_drift"),
        (resources["cdn"], "cdn_resource_drift"),
        (resources["cert"], "certificate_drift"),
    ]:
        if item and item.get("folder_id") != folder:
            fail(code)
    if resources["cdn"] and (
        resources["cdn"].get("cname") != CNAME
        or resources["origin"]
        and resources["cdn"].get("origin_group_id") != ident(resources["origin"])
    ):
        fail("cdn_resource_drift")
    if resources["cert"] and resources["cert"].get("domains") != [CNAME]:
        fail("certificate_drift")
    lockbox = yc("lockbox", "secret", "get", "--id", LOCKBOX, "--format", "json")
    version = lockbox.get("current_version", {})
    present = set(version.get("payload_entry_keys", [])) & set(SECRET_KEYS)
    if present not in (set(), BOOTSTRAP_KEYS, set(SECRET_KEYS)):
        fail("lockbox_secret_set_partial")
    initialized = present == set(SECRET_KEYS)
    if state.get("access_key_resource_id") and not initialized:
        fail(f"access_key_initialization_incomplete recovery_id={state['access_key_resource_id']}")
    if state.get("secrets_initialized") and not initialized:
        fail("lockbox_secret_drift")
    return {
        "cloud": cloud,
        "folder": folder,
        "network": network_id,
        "subnet": subnet_id,
        "bucket": bucket,
        "policy": normalize_policy(bucket.get("policy")),
        "image": cfg["image"],
        "version": version.get("id"),
        "initialized": initialized,
        "ipv4": ipv4,
        "boot_disk": boot_disk,
        "canonical_sg": canonical_security_group,
        "origin_access_configured": origin_access_configured,
        "vm_user_data_sha256": vm_user_data_sha256 if resources["vm"] else None,
        "service_account_roles": service_account_roles,
        "secret_keys_present": present,
        **resources,
    }


def plan(found: dict[str, Any], cfg: dict[str, str]) -> dict[str, Any]:
    ids = {
        f"{key}_id": ident(found[short]) if found[short] else None
        for key, short in [
            ("service_account", "sa"),
            ("security_group", "sg"),
            ("reserved_address", "address"),
            ("vm", "vm"),
            ("origin_group", "origin"),
            ("cdn_resource", "cdn"),
            ("certificate", "cert"),
        ]
    }
    commands: list[list[str]] = []
    if not found["secret_keys_present"]:
        phase = "secret-bootstrap"
    elif not all(found[item] for item in ("sa", "sg", "address")):
        phase = "resource-identities"
    elif not found["origin_access_configured"]:
        phase = "origin-access"
    else:
        phase = "origin-and-policy"
    if phase == "secret-bootstrap":
        commands.append(
            [
                "yc",
                "lockbox",
                "secret",
                "add-version",
                "--id",
                LOCKBOX,
                "--base-version-id",
                found["version"],
                "--payload",
                "-",
                "--format",
                "json",
            ]
        )
    elif phase == "resource-identities":
        if not found["sa"]:
            commands.append(
                [
                    "yc",
                    "iam",
                    "service-account",
                    "create",
                    "--name",
                    NAME,
                    "--folder-id",
                    found["folder"],
                    "--format",
                    "json",
                ]
            )
        if not found["sg"]:
            command = [
                "yc",
                "vpc",
                "security-group",
                "create",
                "--name",
                NAME,
                "--network-id",
                found["network"],
                "--folder-id",
                found["folder"],
            ]
            for item in rules(cfg["cidr"]):
                command += [
                    "--rule",
                    f"direction={item['direction'].lower()},protocol={item['protocol_name'].lower()},port={item['port']},v4-cidrs={item['v4_cidr_blocks'][0]}",
                ]
            commands.append(command + ["--format", "json"])
        if not found["address"]:
            commands.append(
                [
                    "yc",
                    "vpc",
                    "address",
                    "create",
                    "--name",
                    NAME,
                    "--external-ipv4",
                    f"zone={cfg['zone']}",
                    "--folder-id",
                    found["folder"],
                    "--format",
                    "json",
                ]
            )
    elif phase == "origin-access":
        commands.append(
            [
                "yc",
                "vpc",
                "security-group",
                "update-rules",
                "--id",
                ident(found["sg"]),
                "--add-rule",
                "direction=ingress,protocol=tcp,port=22,"
                f"security-group-id={ident(found['canonical_sg'])}",
                "--format",
                "json",
            ]
        )
    else:
        if "monitoring.editor" not in found["service_account_roles"]:
            commands.append(
                [
                    "yc",
                    "resource-manager",
                    "folder",
                    "add-access-binding",
                    "--id",
                    found["folder"],
                    "--role",
                    "monitoring.editor",
                    "--service-account-id",
                    ident(found["sa"]),
                ]
            )
        if not found["vm"]:
            commands.append(
                [
                    "yc",
                    "compute",
                    "instance",
                    "create",
                    "--name",
                    NAME,
                    "--zone",
                    cfg["zone"],
                    "--platform",
                    "standard-v3",
                    "--cores",
                    "2",
                    "--core-fraction",
                    "100",
                    "--memory",
                    "4",
                    "--create-boot-disk",
                    f"size=20,type=network-hdd,image-id={cfg['image']}",
                    "--network-interface",
                    f"subnet-id={found['subnet']},nat-ip-version=ipv4,nat-address={found['ipv4']},security-group-ids={ident(found['sg'])}",
                    "--service-account-id",
                    ident(found["sa"]),
                    "--metadata-from-file",
                    f"user-data={cfg['cloud_init_path']}",
                    "--folder-id",
                    found["folder"],
                    "--format",
                    "json",
                ]
            )
        patch = policy(found["policy"], cfg["bucket"], ident(found["sa"]))
        if patch["before"] != patch["after"]:
            commands.append(
                [
                    "yc",
                    "storage",
                    "bucket",
                    "update",
                    cfg["bucket"],
                    "--policy-from-file",
                    "<protected-policy-file>",
                    "--folder-id",
                    found["folder"],
                    "--format",
                    "json",
                ]
            )
        if not found["initialized"]:
            commands += [
                [
                    "yc",
                    "iam",
                    "access-key",
                    "create",
                    "--service-account-id",
                    ident(found["sa"]),
                    "--format",
                    "json",
                ],
                [
                    "yc",
                    "lockbox",
                    "secret",
                    "add-version",
                    "--id",
                    LOCKBOX,
                    "--base-version-id",
                    found["version"],
                    "--payload",
                    "-",
                    "--format",
                    "json",
                ],
            ]
    patch = policy(found["policy"], cfg["bucket"], ident(found["sa"]) if found["sa"] else None)
    result = {
        "mode": "dry-run",
        "phase": phase,
        "resource_name": NAME,
        "resolved": {
            "cloud_id": found["cloud"],
            "folder_id": found["folder"],
            "network_id": found["network"],
            "subnet_id": found["subnet"],
            "bucket_id": found["bucket"].get("id"),
            "boot_image_id": found["image"],
            "reserved_address_ipv4": found["ipv4"],
            "canonical_security_group_id": ident(found["canonical_sg"]),
            **ids,
        },
        "actual": safe_actual(found),
        "desired": {
            "vm": {
                "platform_id": "standard-v3",
                "cores": 2,
                "memory_gib": 4,
                "boot_image_id": cfg["image"],
                "zone_id": cfg["zone"],
                "subnet_id": found["subnet"],
                "reserved_ipv4": found["ipv4"],
                "ssh_key_sha256": cfg["ssh_hash"],
                "cloud_init_sha256": cfg["cloud_init_hash"],
            },
            "security_group_rules": desired_rules(cfg["cidr"], ident(found["canonical_sg"])),
            "cdn_cname": CNAME,
            "service_account_roles": ["monitoring.editor"],
        },
        "bucket_policy": patch,
        "lockbox": {
            "secret_id": LOCKBOX,
            "base_version_id": found["version"],
            "initialized": found["initialized"],
            "state": (
                "initialized"
                if found["initialized"]
                else "bootstrap-seeded"
                if found["secret_keys_present"] == BOOTSTRAP_KEYS
                else "empty-bootstrap-required"
            ),
            "operation": (
                "none"
                if found["initialized"]
                else "initialize-four"
                if found["secret_keys_present"] == BOOTSTRAP_KEYS
                else "initialize-two"
            ),
            "keys": (
                []
                if found["initialized"]
                else INITIALIZATION_KEYS
                if found["secret_keys_present"] == BOOTSTRAP_KEYS
                else sorted(BOOTSTRAP_KEYS)
            ),
            "rotation": "not-authorized",
        },
        "credential_probe": {
            "invocation": "sh deploy/image-origin/provision.sh --probe",
            "execution": "separate-fail-closed",
            "default_apply_execution": False,
            "matrix": {"allowed": 1, "denied": 7},
        },
        "proposed_commands": commands,
        "task6_refresh": task6_refresh(found["cloud"], found["folder"]),
    }
    result["approval_nonce"] = hashlib.sha256(canon(result)).hexdigest()
    return result


def protected(value: Any) -> Path:
    fd, raw = tempfile.mkstemp(prefix="findme-image-origin-", suffix=".json")
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as target:
        json.dump(value, target, separators=(",", ":"))
    return Path(raw)


def protected_user_data(value: bytes) -> Path:
    fd, raw = tempfile.mkstemp(prefix="findme-image-origin-user-data-", suffix=".sh")
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "wb") as target:
        target.write(value.replace(b"$", b"$$"))
    return Path(raw)


def apply(
    reviewed: dict[str, Any],
    found: dict[str, Any],
    cfg: dict[str, str],
    state_path: Path,
    state: dict[str, Any],
    values: dict[str, str],
) -> dict[str, Any]:
    persist(state_path, state)
    if reviewed["phase"] == "secret-bootstrap":
        current = yc("lockbox", "secret", "get", "--id", LOCKBOX, "--format", "json")
        current_version = current.get("current_version") if isinstance(current, dict) else None
        if (
            not isinstance(current_version, dict)
            or ident(current_version, "lockbox_metadata_invalid") != found["version"]
            or set(current_version.get("payload_entry_keys", [])) & set(SECRET_KEYS)
        ):
            fail("lockbox_version_drift")
        payload = [
            {"key": "GALLERY_CDN_TOKEN_SECRET", "text_value": secrets.token_urlsafe(18)},
            {"key": "IMAGE_ORIGIN_HEADER_SECRET", "text_value": secrets.token_urlsafe(32)},
        ]
        path = protected(payload)
        try:
            version = yc(
                "lockbox",
                "secret",
                "add-version",
                "--id",
                LOCKBOX,
                "--base-version-id",
                found["version"],
                "--payload",
                "-",
                "--format",
                "json",
                stdin=path.read_bytes(),
            )
        finally:
            path.unlink(missing_ok=True)
        state.update(
            lockbox_version_id=ident(version),
            bootstrap_seeded=True,
        )
        persist(state_path, state)
        output = dict(reviewed)
        output.update(mode="applied", next_review_required=True, state_keys=sorted(state))
        return output
    if reviewed["phase"] == "resource-identities":
        for command in reviewed["proposed_commands"]:
            result = yc(*command[1:])
            kind = command[1:3]
            key = {
                ("iam", "service-account"): "service_account_id",
                ("vpc", "security-group"): "security_group_id",
                ("vpc", "address"): "reserved_address_id",
            }[tuple(kind)]
            state[key] = ident(result)
            persist(state_path, state)
            if key == "reserved_address_id":
                state["reserved_address_ipv4"] = result["external_ipv4_address"]["address"]
                persist(state_path, state)
        output = dict(reviewed)
        output.update(mode="applied", next_review_required=True, state_keys=sorted(state))
        return output
    if reviewed["phase"] == "origin-access":
        if len(reviewed["proposed_commands"]) != 1:
            fail("origin_access_plan_invalid")
        yc(*reviewed["proposed_commands"][0][1:])
        output = dict(reviewed)
        output.update(mode="applied", next_review_required=True, state_keys=sorted(state))
        return output

    def check_policy() -> None:
        current = yc(
            "storage",
            "bucket",
            "get",
            cfg["bucket"],
            "--full",
            "--folder-id",
            found["folder"],
            "--format",
            "json",
        )
        if normalize_policy(current.get("policy")) != reviewed["bucket_policy"]["before"]:
            fail("bucket_policy_drift")

    check_policy()
    for command in reviewed["proposed_commands"]:
        if command[1:3] == ["compute", "instance"]:
            actual = list(command[1:])
            metadata = actual.index("--metadata-from-file") + 1
            path = protected_user_data(cfg["cloud_init"])
            actual[metadata] = f"user-data={path}"
            try:
                state["vm_id"] = ident(yc(*actual))
                persist(state_path, state)
            finally:
                path.unlink(missing_ok=True)
        elif command[1:4] == ["resource-manager", "folder", "add-access-binding"]:
            yc(*command[1:])
    check_policy()
    if reviewed["bucket_policy"]["before"] != reviewed["bucket_policy"]["after"]:
        path = protected(reviewed["bucket_policy"]["after"])
        try:
            yc(
                "storage",
                "bucket",
                "update",
                cfg["bucket"],
                "--policy-from-file",
                str(path),
                "--folder-id",
                found["folder"],
                "--format",
                "json",
            )
        finally:
            path.unlink(missing_ok=True)
    if not found["initialized"]:
        access = yc(
            "iam",
            "access-key",
            "create",
            "--service-account-id",
            ident(found["sa"]),
            "--format",
            "json",
        )
        state["access_key_resource_id"] = ident(access["access_key"])
        persist(state_path, state)
        payload = [
            {"key": "GALLERY_IMGPROXY_KEY", "text_value": secrets.token_hex(32)},
            {"key": "GALLERY_IMGPROXY_SALT", "text_value": secrets.token_hex(32)},
            {"key": "IMAGE_ORIGIN_S3_ACCESS_KEY_ID", "text_value": access["access_key"]["key_id"]},
            {"key": "IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY", "text_value": access["secret"]},
        ]
        path = protected(payload)
        try:
            version = yc(
                "lockbox",
                "secret",
                "add-version",
                "--id",
                LOCKBOX,
                "--base-version-id",
                found["version"],
                "--payload",
                "-",
                "--format",
                "json",
                stdin=path.read_bytes(),
            )
        finally:
            path.unlink(missing_ok=True)
        state.update(lockbox_version_id=ident(version), secrets_initialized=True)
        persist(state_path, state)
    output = dict(reviewed)
    output.update(mode="applied", next_review_required=False, state_keys=sorted(state))
    return output


def probe(cfg: dict[str, str]) -> int:
    values = projected(
        {
            "GALLERY_IMGPROXY_KEY",
            "GALLERY_IMGPROXY_SALT",
            "IMAGE_ORIGIN_HEADER_SECRET",
            "IMAGE_ORIGIN_S3_ACCESS_KEY_ID",
            "IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY",
        },
        {"VM_SSH_KEY_FILE"},
    )
    operations = [
        ("GET", cfg["preview"], "", "200"),
        ("GET", "", "list-type=2", "403"),
        ("GET", cfg["original"], "", "403"),
        ("GET", cfg["staging"], "", "403"),
        ("PUT", cfg["write"], "", "403"),
        ("POST", cfg["write"], "uploads", "403"),
        ("PUT", cfg["write"], "acl", "403"),
        ("DELETE", cfg["write"], "", "403"),
    ]
    for method, key, query, expected in operations:
        url = (
            f"https://storage.yandexcloud.net/{cfg['bucket']}"
            + ("/" + quote(key, safe="/") if key else "")
            + ("?" + query if query else "")
        )
        config = f'url = "{url}"\nrequest = "{method}"\nuser = "{values["IMAGE_ORIGIN_S3_ACCESS_KEY_ID"]}:{values["IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY"]}"\naws-sigv4 = "aws:amz:ru-central1:s3"\noutput = "/dev/null"\nwrite-out = "%{{http_code}}"\nsilent\nshow-error\n'
        result = subprocess.run(
            ["curl", "--config", "-"], input=config.encode(), capture_output=True
        )
        if result.returncode or result.stdout.decode().strip() != expected:
            fail("credential_probe_authorization_mismatch")
    print(
        json.dumps(
            {"mode": "credential-probe", "status": "green", "allowed": 1, "denied": 7},
            sort_keys=True,
        )
    )
    return 0


def config() -> dict[str, Any]:
    try:
        cidr = str(ipaddress.IPv4Network(env("IMAGE_ORIGIN_SSH_SOURCE_CIDR"), strict=True))
    except ValueError:
        fail("invalid_image_origin_ssh_source_cidr")
    try:
        key_info, key_data = PUBLIC_KEY.lstat(), PUBLIC_KEY.read_bytes()
        cloud_init_info, cloud_init_data = CLOUD_INIT.lstat(), CLOUD_INIT.read_bytes()
    except OSError:
        fail("reviewed_bootstrap_artifact_invalid")
    if (
        stat.S_ISLNK(key_info.st_mode)
        or not stat.S_ISREG(key_info.st_mode)
        or key_data.decode(errors="replace") != PUBLIC_KEY_VALUE
        or stat.S_ISLNK(cloud_init_info.st_mode)
        or not stat.S_ISREG(cloud_init_info.st_mode)
        or not cloud_init_data.startswith(b"#!/bin/sh\n")
    ):
        fail("reviewed_bootstrap_artifact_invalid")
    return {
        "canonical_vm": env("CANONICAL_VM_ID", ID),
        "bucket": env("PRIVATE_MEDIA_S3_BUCKET", BUCKET),
        "zone": env("IMAGE_ORIGIN_ZONE_ID", ID),
        "image": env("IMAGE_ORIGIN_BOOT_IMAGE_ID", ID),
        "cidr": cidr,
        "ssh_hash": hashlib.sha256(key_data).hexdigest(),
        "cloud_init_path": str(CLOUD_INIT),
        "cloud_init": cloud_init_data,
        "cloud_init_hash": hashlib.sha256(cloud_init_data).hexdigest(),
        "preview": env("IMAGE_ORIGIN_ACCEPTED_PREVIEW_KEY", KEY),
        "original": env("IMAGE_ORIGIN_DENIED_ORIGINAL_KEY", KEY),
        "staging": env("IMAGE_ORIGIN_DENIED_STAGING_KEY", KEY),
        "write": env("IMAGE_ORIGIN_PROBE_WRITE_KEY", KEY),
    }


def provisioning_values() -> dict[str, str]:
    values = projected({"GALLERY_CDN_TOKEN_SECRET", "IMAGE_ORIGIN_HEADER_SECRET"})
    if not 6 <= len(values["GALLERY_CDN_TOKEN_SECRET"]) <= 32:
        fail("invalid_gallery_cdn_token_secret")
    if not HEADER.fullmatch(values["IMAGE_ORIGIN_HEADER_SECRET"]):
        fail("invalid_image_origin_header_secret")
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--probe", action="store_true")
    parser.add_argument("--approval-nonce")
    args = parser.parse_args()
    cfg = config()
    if args.probe:
        return probe(cfg)
    state_path = (
        Path(os.environ["IMAGE_ORIGIN_CONTRACT_STATE"])
        if os.environ.get("IMAGE_ORIGIN_CONTRACT_STATE")
        else None
    )
    state = load_state(state_path)
    found = discover(state, cfg)
    reviewed = plan(found, cfg)
    if not args.apply:
        if args.approval_nonce:
            fail("approval_requires_apply")
        print(json.dumps(reviewed, indent=2, sort_keys=True))
        return 0
    if args.approval_nonce != reviewed["approval_nonce"]:
        fail("approval_nonce_mismatch")
    if state_path is None:
        fail("state_path_required")
    values = provisioning_values() if reviewed["phase"] == "origin-and-policy" else {}
    print(
        json.dumps(
            apply(reviewed, found, cfg, state_path, state, values),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
