# ruff: noqa: E501
"""Dry-run and least-privilege contracts for gallery image delivery provisioning."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.deployment import test_environment_secrets as resolver_contract

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "deploy/image-origin"
PROVISION = PACKAGE / "provision.sh"
MANIFEST = ROOT / "deploy/environment-secrets.json"
RESOURCE_NAME = "findme-gallery-image-origin"
NEW_SECRET_KEYS = {
    "GALLERY_CDN_TOKEN_SECRET",
    "GALLERY_IMGPROXY_KEY",
    "GALLERY_IMGPROXY_SALT",
    "IMAGE_ORIGIN_HEADER_SECRET",
    "IMAGE_ORIGIN_S3_ACCESS_KEY_ID",
    "IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY",
}
DJANGO_SIGNING_KEYS = {
    "GALLERY_CDN_TOKEN_SECRET",
    "GALLERY_IMGPROXY_KEY",
    "GALLERY_IMGPROXY_SALT",
}
ORIGIN_KEYS = {
    "GALLERY_IMGPROXY_KEY",
    "GALLERY_IMGPROXY_SALT",
    "IMAGE_ORIGIN_HEADER_SECRET",
    "IMAGE_ORIGIN_S3_ACCESS_KEY_ID",
    "IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY",
    "VM_SSH_KEY",
}


@pytest.fixture
def cloud_environment(tmp_path: Path) -> dict[str, str]:
    existing_policy = tmp_path / "existing-policy.json"
    existing_policy.write_text(
        json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "KeepUnrelatedCoverRead",
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": "s3:GetObject",
                        "Resource": "arn:aws:s3:::canonical-media/covers/*",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    ssh_key = tmp_path / "origin.pub"
    ssh_key.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest reviewed@test\n")
    projection = tmp_path / "projection.env"
    projection.write_text(
        'GALLERY_CDN_TOKEN_SECRET="cdn-token-private-sentinel"\n'
        'IMAGE_ORIGIN_HEADER_SECRET="origin-header-private-sentinel"\n'
    )
    projection.chmod(0o600)
    return {
        **os.environ,
        "CANONICAL_VM_ID": "canonical-vm-id",
        "PRIVATE_MEDIA_S3_BUCKET": "canonical-media",
        "IMAGE_ORIGIN_ZONE_ID": "ru-central1-a",
        "IMAGE_ORIGIN_BOOT_IMAGE_ID": "immutable-image-id",
        "IMAGE_ORIGIN_SSH_PUBLIC_KEY_FILE": str(ssh_key),
        "IMAGE_ORIGIN_SSH_SOURCE_CIDR": "203.0.113.8/32",
        "IMAGE_ORIGIN_ACCEPTED_PREVIEW_KEY": (
            "derivatives/previews/photo-1/preview-small-v1/accepted.jpg"
        ),
        "IMAGE_ORIGIN_DENIED_ORIGINAL_KEY": "originals/existing.jpg",
        "IMAGE_ORIGIN_DENIED_STAGING_KEY": "staging/existing.jpg",
        "IMAGE_ORIGIN_PROBE_WRITE_KEY": "derivatives/previews/probe/write.jpg",
        "IMAGE_ORIGIN_CONTRACT_STATE": str(tmp_path / "contract-state.json"),
        "FINDME_ENV_FILE": str(projection),
    }


def _install_fake_yc(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    log = tmp_path / "yc-commands.jsonl"
    log.unlink(missing_ok=True)
    executable = fake_bin / "yc"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """
import json
import os
import pathlib
import sys

args = sys.argv[1:]
with pathlib.Path(os.environ["FAKE_YC_LOG"]).open("a", encoding="utf-8") as target:
    target.write(json.dumps(args) + "\\n")

key = " ".join(args)
policy = {"Version": "2012-10-17", "Statement": [{"Sid": "KeepUnrelatedCoverRead", "Effect": "Allow", "Principal": "*", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::canonical-media/covers/*"}]}
if args[:3] == ["config", "profile", "list"]:
    if "FAKE_PROFILE_LIST_OUTPUT" in os.environ:
        print(os.environ["FAKE_PROFILE_LIST_OUTPUT"])
        raise SystemExit(0)
    value = [{"name": "contract-profile", "is_active": True}]
elif args[:3] == ["config", "get", "cloud-id"]:
    print("cloud-contract-id")
    raise SystemExit(0)
elif args[:3] == ["config", "get", "folder-id"]:
    print("folder-contract-id")
    raise SystemExit(0)
elif args[:2] == ["iam", "create-token"]:
    print("iam-test-token")
    raise SystemExit(0)
elif args[:3] == ["compute", "instance", "get"] and "canonical-vm-id" in args:
    value = {
        "id": "canonical-vm-id",
        "folder_id": "folder-contract-id",
        "network_interfaces": [{"subnet_id": "subnet-contract-id"}],
    }
elif args[:3] == ["iam", "service-account", "get"]:
    value = {"id": "created-service-account-id", "name": os.environ["EXPECTED_RESOURCE_NAME"], "folder_id": "folder-contract-id"}
elif args[:3] == ["vpc", "security-group", "get"]:
    value = {"id": "created-security-group-id", "name": os.environ["EXPECTED_RESOURCE_NAME"], "folder_id": "folder-contract-id", "network_id": "network-contract-id", "rules": list(reversed([
        {"id": f"generated-rule-{index}", "direction": d, "protocol_name": p, "ports": {"from_port": port, "to_port": port}, "cidr_blocks": {"v4_cidr_blocks": [cidr], "v6_cidr_blocks": []}}
        for index, (d, p, port, cidr) in enumerate([("INGRESS", "TCP", "22", "203.0.113.8/32"), ("INGRESS", "TCP", "80", "0.0.0.0/0"), ("INGRESS", "TCP", "443", "0.0.0.0/0"), ("EGRESS", "TCP", "443", "0.0.0.0/0"), ("EGRESS", "TCP", "53", "0.0.0.0/0"), ("EGRESS", "UDP", "53", "0.0.0.0/0")])
    ]))}
elif args[:3] == ["vpc", "address", "get"]:
    value = {"id": "created-address-id", "name": os.environ["EXPECTED_RESOURCE_NAME"], "folder_id": "folder-contract-id", "reserved": True, "external_ipv4_address": {"address": "198.51.100.44", "zone_id": "ru-central1-a"}}
elif args[:3] == ["compute", "instance", "get"] and "created-vm-id" in args:
    value = {"id": "created-vm-id", "name": os.environ["EXPECTED_RESOURCE_NAME"], "folder_id": "folder-contract-id", "zone_id": "ru-central1-a", "platform_id": "standard-v3", "resources": {"cores": "2", "core_fraction": "100", "memory": "4294967296", "gpus": "0"}, "service_account_id": "created-service-account-id", "metadata": {"user-data": "vm-metadata-private-sentinel"}, "network_interfaces": [{"index": "0", "subnet_id": "subnet-contract-id", "security_group_ids": ["created-security-group-id"], "primary_v4_address": {"address": "10.0.0.4", "one_to_one_nat": {"address": "198.51.100.44", "ip_version": "IPV4"}}}], "boot_disk": {"disk_id": "created-boot-disk-id", "auto_delete": True}, "secondary_disks": []}
    if os.environ.get("FAKE_EXTRA_INTERFACE") == "1": value["network_interfaces"].append({"index": "1"})
    if os.environ.get("FAKE_EXTRA_DISK") == "1": value["secondary_disks"].append({"disk_id": "extra-disk-id"})
elif args[:3] == ["compute", "disk", "get"]:
    value = {"id": "created-boot-disk-id", "folder_id": "folder-contract-id", "type_id": "network-hdd", "size": "21474836480", "source_image_id": "immutable-image-id"}
elif args[:3] == ["cdn", "origin-group", "get"]:
    value = {"id": "existing-origin-id", "name": os.environ["EXPECTED_RESOURCE_NAME"], "folder_id": "folder-contract-id"}
elif args[:3] == ["cdn", "resource", "get"]:
    value = {"id": "existing-cdn-id", "cname": "img.findme-photo.ru", "folder_id": "folder-contract-id", "origin_group_id": "existing-origin-id", "options": {"secure_key": {"key": "cdn-secure-private-sentinel"}, "static_request_headers": {"X-Origin-Secret": "origin-header-private-sentinel"}}}
elif args[:3] == ["certificate-manager", "certificate", "get"]:
    value = {"id": "existing-cert-id", "name": os.environ["EXPECTED_RESOURCE_NAME"], "folder_id": "folder-contract-id", "domains": ["img.findme-photo.ru"]}
elif args[:4] == ["resource-manager", "folder", "list-access-bindings", "--id"]:
    value = ([{"role_id": "monitoring.editor", "subject": {"id": "created-service-account-id", "type": "serviceAccount"}}] if os.environ.get("FAKE_MONITORING_BINDING") == "1" else [])
elif args[:4] == ["resource-manager", "folder", "add-access-binding", "--id"]:
    value = {"status": "done"}
elif args[:3] == ["compute", "zone", "get"]:
    value = {"id": "ru-central1-a"}
elif args[:3] == ["compute", "image", "get"]:
    value = {"id": "immutable-image-id", "status": "READY"}
elif args[:3] == ["vpc", "subnet", "get"]:
    value = {"id": "subnet-contract-id", "network_id": "network-contract-id", "folder_id": "folder-contract-id", "zone_id": "ru-central1-a"}
elif args[:3] == ["vpc", "network", "get"]:
    value = {"id": "network-contract-id", "folder_id": "folder-contract-id"}
elif args[:4] == ["storage", "bucket", "get", "canonical-media"]:
    counter = pathlib.Path(os.environ.get("FAKE_POLICY_COUNTER", "/nonexistent"))
    if str(counter) != "/nonexistent":
        count = int(counter.read_text()) + 1 if counter.exists() else 1
        counter.write_text(str(count))
        if count >= int(os.environ.get("FAKE_POLICY_DRIFT_AT", "999")):
            policy["Statement"].append({"Sid": "ConcurrentChange", "Effect": "Allow", "Principal": "*", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::canonical-media/new/*"})
    value = {"name": "canonical-media", "id": "canonical-media", "folder_id": "folder-contract-id", "policy": policy}
elif args[:3] == ["cdn", "resource", "list"] and os.environ.get("FAKE_CDN_EXISTING") == "1":
    value = [{"id": "existing-cdn-id", "cname": "img.findme-photo.ru", "folder_id": "folder-contract-id"}]
elif " list " in f" {key} ":
    value = []
elif args[:3] == ["iam", "service-account", "create"]:
    value = {"id": "created-service-account-id", "name": os.environ["EXPECTED_RESOURCE_NAME"], "folder_id": "folder-contract-id"}
elif args[:3] == ["vpc", "security-group", "create"]:
    value = {"id": "created-security-group-id", "name": os.environ["EXPECTED_RESOURCE_NAME"]}
elif args[:3] == ["vpc", "address", "create"]:
    value = {"id": "created-address-id", "name": os.environ["EXPECTED_RESOURCE_NAME"], "folder_id": "folder-contract-id", "reserved": True, "external_ipv4_address": {"address": "198.51.100.44", "zone_id": "ru-central1-a"}}
elif args[:3] == ["compute", "instance", "create"]:
    interface = args[args.index("--network-interface") + 1]
    boot = args[args.index("--create-boot-disk") + 1]
    if "address-id=" in interface or "nat-ip-version=ipv4" not in interface or "nat-address=198.51.100.44" not in interface or "image-id=immutable-image-id" not in boot or "--ssh-key" not in args:
        raise SystemExit(9)
    if os.environ.get("FAKE_FAIL_VM") == "1": raise SystemExit(8)
    value = {"id": "created-vm-id", "name": os.environ["EXPECTED_RESOURCE_NAME"]}
elif args[:3] == ["iam", "access-key", "create"]:
    value = {
        "access_key": {"id": "created-access-key-resource-id", "key_id": "created-access-key-id"},
        "secret": "generated-secret-private-sentinel",
    }
elif args[:4] == ["lockbox", "secret", "get", "--id"]:
    initialized = bootstrap_seeded = False
    state_path = pathlib.Path(os.environ.get("IMAGE_ORIGIN_CONTRACT_STATE", "/nonexistent"))
    if state_path.exists():
        state = json.loads(state_path.read_text())
        initialized = state.get("secrets_initialized") is True
        bootstrap_seeded = state.get("bootstrap_seeded") is True
    keys = (["GALLERY_CDN_TOKEN_SECRET", "GALLERY_IMGPROXY_KEY", "GALLERY_IMGPROXY_SALT", "IMAGE_ORIGIN_HEADER_SECRET", "IMAGE_ORIGIN_S3_ACCESS_KEY_ID", "IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY"] if initialized else (["GALLERY_CDN_TOKEN_SECRET", "IMAGE_ORIGIN_HEADER_SECRET"] if bootstrap_seeded else ["GALLERY_CDN_TOKEN_SECRET", "IMAGE_ORIGIN_HEADER_SECRET"]))
    if "FAKE_LOCKBOX_KEYS" in os.environ: keys = json.loads(os.environ["FAKE_LOCKBOX_KEYS"])
    version_id = "created-lockbox-version-id" if initialized else "current-lockbox-version-id"
    if "FAKE_LOCKBOX_METADATA_COUNTER" in os.environ:
        counter = pathlib.Path(os.environ["FAKE_LOCKBOX_METADATA_COUNTER"])
        count = int(counter.read_text()) + 1 if counter.exists() else 1
        counter.write_text(str(count))
        if count >= int(os.environ.get("FAKE_LOCKBOX_DRIFT_AT", "999")):
            version_id = "concurrent-lockbox-version-id"
    value = {"current_version": {"id": version_id, "payload_entry_keys": keys}}
elif args[:3] == ["lockbox", "secret", "add-version"]:
    if os.environ.get("FAKE_FAIL_LOCKBOX") == "1": raise SystemExit(7)
    changes = json.loads(sys.stdin.read())
    if "FAKE_LOCKBOX_STDIN" in os.environ:
        pathlib.Path(os.environ["FAKE_LOCKBOX_STDIN"]).write_text(json.dumps(changes))
    inherited = [
        {"key": "EXISTING_APPLICATION_SECRET", "text_value": "existing-private-sentinel"},
        {"key": "EXISTING_BINARY_SECRET", "binary_value": "AAEC-private-sentinel"},
    ]
    if "FAKE_LOCKBOX_RESULT" in os.environ:
        merged = {entry["key"]: entry for entry in inherited}
        merged.update({entry["key"]: entry for entry in changes})
        pathlib.Path(os.environ["FAKE_LOCKBOX_RESULT"]).write_text(json.dumps(list(merged.values())))
    value = {"id": "created-lockbox-version-id"}
else:
    value = {"status": "ok"}
print(json.dumps(value))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    curl = fake_bin / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        + """
import os, pathlib, sys
config = sys.stdin.read()
method = next(line for line in config.splitlines() if line.startswith("request = "))
url = next(line for line in config.splitlines() if line.startswith("url = "))
with pathlib.Path(os.environ["FAKE_CURL_LOG"]).open("a") as target: target.write(method + "\\n")
allowed = "derivatives/previews/photo-1/preview-small-v1/accepted.jpg" in url and '"GET"' in method
sys.stdout.write("200" if allowed else "403")
"""
    )
    curl.chmod(0o755)
    return fake_bin, log


def _run_provision(
    tmp_path: Path,
    cloud_environment: dict[str, str],
    *arguments: str,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    fake_bin, log = _install_fake_yc(tmp_path)
    environment = {
        **cloud_environment,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_YC_LOG": str(log),
        "EXPECTED_RESOURCE_NAME": RESOURCE_NAME,
        "FAKE_CURL_LOG": str(tmp_path / "curl.log"),
        "FAKE_LOCKBOX_STDIN": str(tmp_path / "lockbox-stdin.json"),
        "FAKE_LOCKBOX_RESULT": str(tmp_path / "lockbox-result.json"),
    }
    result = subprocess.run(
        ["sh", str(PROVISION), *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, commands


def _plan(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return json.loads(result.stdout)


def _apply_identity_phase(tmp_path: Path, environment: dict[str, str]) -> dict[str, Any]:
    dry, _ = _run_provision(tmp_path, environment)
    result, _ = _run_provision(
        tmp_path, environment, "--apply", "--approval-nonce", _plan(dry)["approval_nonce"]
    )
    return _plan(result)


def test_policy_statement_is_one_principal_scoped_preview_read() -> None:
    statement = json.loads((PACKAGE / "bucket-policy-statement.json").read_text())

    assert statement == {
        "Sid": "AllowFindMeGalleryImageOriginPreviewReads",
        "Effect": "Allow",
        "Principal": {"CanonicalUser": "${IMAGE_ORIGIN_SERVICE_ACCOUNT_ID}"},
        "Action": "s3:GetObject",
        "Resource": "arn:aws:s3:::${PRIVATE_MEDIA_S3_BUCKET}/derivatives/previews/*",
    }


def test_manifest_adds_exactly_six_optional_secrets_with_closed_consumers() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = {entry["key"]: entry for entry in manifest["entries"]}

    assert NEW_SECRET_KEYS <= entries.keys()
    assert all(
        entries[key]
        == {
            "key": key,
            "target": key,
            "type": "text",
            "required": False,
            "local": False,
        }
        for key in NEW_SECRET_KEYS
    )
    consumers = {name: set(keys) for name, keys in manifest["consumers"].items()}
    assert consumers["local-web"] & NEW_SECRET_KEYS == DJANGO_SIGNING_KEYS
    assert consumers["deploy"] & NEW_SECRET_KEYS == DJANGO_SIGNING_KEYS
    assert consumers["image-origin"] == ORIGIN_KEYS
    assert consumers["image-delivery-provision"] == {
        "GALLERY_CDN_TOKEN_SECRET",
        "IMAGE_ORIGIN_HEADER_SECRET",
    }


def test_default_is_read_only_discovery_and_machine_readable_plan(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    result, commands = _run_provision(tmp_path, cloud_environment)
    plan = _plan(result)

    assert plan["mode"] == "dry-run"
    assert plan["resource_name"] == RESOURCE_NAME
    assert plan["resolved"] == {
        "cloud_id": "cloud-contract-id",
        "folder_id": "folder-contract-id",
        "network_id": "network-contract-id",
        "subnet_id": "subnet-contract-id",
        "bucket_id": "canonical-media",
        "boot_image_id": "immutable-image-id",
        "service_account_id": None,
        "reserved_address_id": None,
        "security_group_id": None,
        "vm_id": None,
        "origin_group_id": None,
        "cdn_resource_id": None,
        "certificate_id": None,
        "reserved_address_ipv4": None,
    }
    assert plan["desired"]["vm"] | {"reserved_ipv4": True} == {
        "platform_id": "standard-v3",
        "cores": 2,
        "memory_gib": 4,
        "boot_image_id": "immutable-image-id",
        "zone_id": "ru-central1-a",
        "subnet_id": "subnet-contract-id",
        "reserved_ipv4": True,
        "ssh_key_sha256": plan["desired"]["vm"]["ssh_key_sha256"],
    }
    assert len(plan["desired"]["security_group_rules"]) == 6
    serialized_commands = json.dumps(commands)
    assert " create " not in f" {serialized_commands} "
    assert " update " not in f" {serialized_commands} "
    assert " delete " not in f" {serialized_commands} "


def test_discovery_accepts_yc_1_21_plain_text_active_profile(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    result, _ = _run_provision(
        tmp_path,
        {**cloud_environment, "FAKE_PROFILE_LIST_OUTPUT": "default ACTIVE"},
    )

    assert _plan(result)["mode"] == "dry-run"


def test_discovery_fails_closed_when_plain_text_has_no_active_profile(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    result, commands = _run_provision(
        tmp_path,
        {**cloud_environment, "FAKE_PROFILE_LIST_OUTPUT": "default"},
    )

    assert result.returncode == 2
    assert "profile_invalid" in result.stderr
    assert commands == [["config", "profile", "list", "--format", "json"]]


def test_discovery_uses_yc_1_21_positional_zone_get_contract(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    result, commands = _run_provision(tmp_path, cloud_environment)

    _plan(result)
    assert [command for command in commands if command[:3] == ["compute", "zone", "get"]] == [
        ["compute", "zone", "get", "ru-central1-a", "--format", "json"]
    ]


def test_plan_preserves_policy_and_declares_independent_credential_denials(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    result, _commands = _run_provision(tmp_path, cloud_environment)
    plan = _plan(result)
    patch = plan["bucket_policy"]

    assert patch["before"]["Statement"] == [
        {
            "Sid": "KeepUnrelatedCoverRead",
            "Effect": "Allow",
            "Principal": "*",
            "Action": "s3:GetObject",
            "Resource": "arn:aws:s3:::canonical-media/covers/*",
        }
    ]
    assert patch["after"]["Statement"][:1] == patch["before"]["Statement"]
    assert patch["after"] == patch["before"]
    assert patch["before_sha256"]
    probe = plan["credential_probe"]
    assert probe["matrix"] == {"allowed": 1, "denied": 7}
    assert probe["default_apply_execution"] is False
    assert plan["lockbox"]["state"] == "bootstrap-seeded"
    assert set(plan["lockbox"]["keys"]) == {
        "GALLERY_IMGPROXY_KEY",
        "GALLERY_IMGPROXY_SALT",
        "IMAGE_ORIGIN_S3_ACCESS_KEY_ID",
        "IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY",
    }


def test_plan_contains_provider_validated_immutable_vm_contract(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    result, _commands = _run_provision(tmp_path, cloud_environment)
    plan = _plan(result)
    assert plan["phase"] == "resource-identities"
    assert plan["resolved"]["boot_image_id"] == "immutable-image-id"
    assert plan["desired"]["vm"]["ssh_key_sha256"]


@pytest.mark.parametrize(
    "arguments",
    [
        ("--apply",),
        ("--approval-nonce", "not-an-approval"),
        ("--apply", "--approval-nonce", "wrong"),
    ],
)
def test_mutation_refuses_unless_apply_and_exact_plan_nonce_are_both_present(
    tmp_path: Path, cloud_environment: dict[str, str], arguments: tuple[str, ...]
) -> None:
    result, commands = _run_provision(tmp_path, cloud_environment, *arguments)

    assert result.returncode == 2
    assert "approval" in result.stderr.lower()
    assert all("create" not in command and "update" not in command for command in commands)


def test_empty_lockbox_requires_approved_two_key_bootstrap_and_fresh_review(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    environment = {
        **cloud_environment,
        "FAKE_LOCKBOX_KEYS": "[]",
        "IMAGE_ORIGIN_CONTRACT_STATE": str(tmp_path / "state.json"),
    }
    dry, commands = _run_provision(tmp_path, environment)
    plan = _plan(dry)
    assert plan["phase"] == "secret-bootstrap"
    assert plan["lockbox"] == {
        "secret_id": "e6q85jjl76r45maigtfb",
        "base_version_id": "current-lockbox-version-id",
        "initialized": False,
        "state": "empty-bootstrap-required",
        "operation": "initialize-two",
        "keys": ["GALLERY_CDN_TOKEN_SECRET", "IMAGE_ORIGIN_HEADER_SECRET"],
        "rotation": "not-authorized",
    }
    assert all("create" not in command and "add-version" not in command for command in commands)
    assert "private-sentinel" not in dry.stdout + dry.stderr

    applied, apply_commands = _run_provision(
        tmp_path,
        environment,
        "--apply",
        "--approval-nonce",
        plan["approval_nonce"],
    )
    result = _plan(applied)
    assert result["mode"] == "applied"
    assert result["next_review_required"] is True
    mutations = [
        command
        for command in apply_commands
        if "create" in command or "update" in command or "add-version" in command
    ]
    assert len(mutations) == 1
    assert mutations[0][:3] == ["lockbox", "secret", "add-version"]
    assert "--base-version-id" in mutations[0]
    assert "current-lockbox-version-id" in mutations[0]
    assert "private-sentinel" not in applied.stdout + applied.stderr + json.dumps(apply_commands)
    patch = json.loads((tmp_path / "lockbox-stdin.json").read_text())
    assert {entry["key"] for entry in patch} == {
        "GALLERY_CDN_TOKEN_SECRET",
        "IMAGE_ORIGIN_HEADER_SECRET",
    }
    assert len(patch) == 2
    inherited = json.loads((tmp_path / "lockbox-result.json").read_text())
    assert inherited[:2] == [
        {"key": "EXISTING_APPLICATION_SECRET", "text_value": "existing-private-sentinel"},
        {"key": "EXISTING_BINARY_SECRET", "binary_value": "AAEC-private-sentinel"},
    ]
    assert {entry["key"] for entry in inherited[2:]} == {
        "GALLERY_CDN_TOKEN_SECRET",
        "IMAGE_ORIGIN_HEADER_SECRET",
    }
    assert not any(command[:3] == ["lockbox", "payload", "get"] for command in apply_commands)
    assert sum(command[:3] == ["lockbox", "secret", "get"] for command in apply_commands) == 2
    state = json.loads(Path(environment["IMAGE_ORIGIN_CONTRACT_STATE"]).read_text())
    assert state["bootstrap_seeded"] is True
    assert state["lockbox_version_id"] == "created-lockbox-version-id"


def test_empty_lockbox_bootstrap_rechecks_current_version_before_patch(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    environment = {
        **cloud_environment,
        "FAKE_LOCKBOX_KEYS": "[]",
        "FAKE_LOCKBOX_METADATA_COUNTER": str(tmp_path / "lockbox-metadata-count"),
        "FAKE_LOCKBOX_DRIFT_AT": "3",
        "IMAGE_ORIGIN_CONTRACT_STATE": str(tmp_path / "state.json"),
    }
    dry, _ = _run_provision(tmp_path, environment)
    plan = _plan(dry)

    applied, commands = _run_provision(
        tmp_path,
        environment,
        "--apply",
        "--approval-nonce",
        plan["approval_nonce"],
    )

    assert applied.returncode == 2
    assert "lockbox_version_drift" in applied.stderr
    assert not any(command[:3] == ["lockbox", "secret", "add-version"] for command in commands)


def test_approved_apply_uses_returned_ids_and_never_exposes_secret_values(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    dry_result, _ = _run_provision(tmp_path, cloud_environment)
    dry_plan = _plan(dry_result)
    canonical_plan = json.dumps(
        {key: value for key, value in dry_plan.items() if key != "approval_nonce"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert dry_plan["approval_nonce"] == hashlib.sha256(canonical_plan).hexdigest()

    apply_tmp = tmp_path / "apply"
    apply_tmp.mkdir()
    apply_environment = {
        **cloud_environment,
        "IMAGE_ORIGIN_CONTRACT_STATE": str(apply_tmp / "contract-state.json"),
    }
    result, commands = _run_provision(
        apply_tmp,
        apply_environment,
        "--apply",
        "--approval-nonce",
        dry_plan["approval_nonce"],
    )

    applied = _plan(result)
    assert applied["mode"] == "applied"
    assert applied["next_review_required"] is True
    second_dry, _ = _run_provision(apply_tmp, apply_environment)
    second_plan = _plan(second_dry)
    assert second_plan["phase"] == "origin-and-policy"
    vm_command = next(
        command
        for command in second_plan["proposed_commands"]
        if command[1:3] == ["compute", "instance"]
    )
    assert "address-id=" not in " ".join(vm_command)
    assert "nat-address=198.51.100.44" in " ".join(vm_command)
    result, commands = _run_provision(
        apply_tmp, apply_environment, "--apply", "--approval-nonce", second_plan["approval_nonce"]
    )
    applied = _plan(result)
    command_text = json.dumps(commands)
    assert "created-service-account-id" in command_text
    for secret in (
        "cdn-token-private-sentinel",
        "origin-header-private-sentinel",
        "generated-secret-private-sentinel",
    ):
        assert secret not in result.stdout + result.stderr + command_text
    state_path = Path(apply_environment["IMAGE_ORIGIN_CONTRACT_STATE"])
    assert state_path.stat().st_mode & 0o777 == 0o600
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["service_account_id"] == "created-service-account-id"
    assert state["vm_id"] == "created-vm-id"
    assert state["access_key_resource_id"] == "created-access-key-resource-id"
    assert "private-sentinel" not in json.dumps(state).lower()


def test_apply_validates_its_secret_projection_before_mutation(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    dry_result, _ = _run_provision(tmp_path, cloud_environment)
    approval_nonce = _plan(dry_result)["approval_nonce"]
    apply_tmp = tmp_path / "missing-secret"
    apply_tmp.mkdir()
    bad_projection = apply_tmp / "projection.env"
    bad_projection.write_text(
        'GALLERY_CDN_TOKEN_SECRET="x"\nIMAGE_ORIGIN_HEADER_SECRET="origin-header-private-sentinel"\n'
    )
    bad_projection.chmod(0o600)
    missing_secret_environment = {
        **cloud_environment,
        "FINDME_ENV_FILE": str(bad_projection),
        "IMAGE_ORIGIN_CONTRACT_STATE": str(apply_tmp / "contract-state.json"),
    }

    first, commands = _run_provision(
        apply_tmp, missing_secret_environment, "--apply", "--approval-nonce", approval_nonce
    )
    assert first.returncode == 2
    assert "gallery_cdn_token_secret" in first.stderr
    assert all("create" not in command and "update" not in command for command in commands)


def test_policy_is_provider_sourced_and_refetched_before_mutation(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    environment = {**cloud_environment, "IMAGE_ORIGIN_CONTRACT_STATE": str(tmp_path / "state.json")}
    _apply_identity_phase(tmp_path, environment)
    counter = tmp_path / "policy-count"
    reviewed, _ = _run_provision(tmp_path, {**environment, "FAKE_POLICY_COUNTER": str(counter)})
    plan = _plan(reviewed)
    result, commands = _run_provision(
        tmp_path,
        {**environment, "FAKE_POLICY_COUNTER": str(counter), "FAKE_POLICY_DRIFT_AT": "3"},
        "--apply",
        "--approval-nonce",
        plan["approval_nonce"],
    )
    assert result.returncode == 2
    assert "bucket_policy_drift" in result.stderr
    assert all("update" not in command and "access-key" not in command for command in commands)


def test_partial_failure_keeps_all_returned_and_preexisting_ids_for_resume(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "origin_group_id": "existing-origin-id",
                "cdn_resource_id": "existing-cdn-id",
                "certificate_id": "existing-cert-id",
            }
        )
    )
    state.chmod(0o600)
    environment = {**cloud_environment, "IMAGE_ORIGIN_CONTRACT_STATE": str(state)}
    _apply_identity_phase(tmp_path, environment)
    reviewed, _ = _run_provision(tmp_path, environment)
    result, _ = _run_provision(
        tmp_path,
        {**environment, "FAKE_FAIL_VM": "1"},
        "--apply",
        "--approval-nonce",
        _plan(reviewed)["approval_nonce"],
    )
    assert result.returncode == 2
    persisted = json.loads(state.read_text())
    assert persisted["service_account_id"] == "created-service-account-id"
    assert persisted["reserved_address_id"] == "created-address-id"
    assert persisted["origin_group_id"] == "existing-origin-id"
    assert persisted["cdn_resource_id"] == "existing-cdn-id"
    assert persisted["certificate_id"] == "existing-cert-id"


def test_repeat_apply_does_not_rotate_or_create_another_access_key(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    environment = {**cloud_environment, "IMAGE_ORIGIN_CONTRACT_STATE": str(tmp_path / "state.json")}
    _apply_identity_phase(tmp_path, environment)
    reviewed, _ = _run_provision(tmp_path, environment)
    first, _ = _run_provision(
        tmp_path, environment, "--apply", "--approval-nonce", _plan(reviewed)["approval_nonce"]
    )
    assert first.returncode == 0
    repeat_plan, _ = _run_provision(tmp_path, environment)
    assert _plan(repeat_plan)["lockbox"]["operation"] == "none"
    repeat, commands = _run_provision(
        tmp_path, environment, "--apply", "--approval-nonce", _plan(repeat_plan)["approval_nonce"]
    )
    assert repeat.returncode == 0
    assert not any(command[:3] == ["iam", "access-key", "create"] for command in commands)
    assert not any(command[:3] == ["lockbox", "secret", "add-version"] for command in commands)


def test_probe_is_separate_fail_closed_and_reports_only_aggregates(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    projection = tmp_path / "probe.env"
    projection.write_text(
        'GALLERY_IMGPROXY_KEY="imgproxy-key"\n'
        'GALLERY_IMGPROXY_SALT="imgproxy-salt"\n'
        'IMAGE_ORIGIN_HEADER_SECRET="origin-header-private-sentinel"\n'
        'IMAGE_ORIGIN_S3_ACCESS_KEY_ID="probe-access"\n'
        'IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY="probe-secret"\n'
    )
    projection.chmod(0o600)
    result, commands = _run_provision(
        tmp_path, {**cloud_environment, "FINDME_ENV_FILE": str(projection)}, "--probe"
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "mode": "credential-probe",
        "status": "green",
        "allowed": 1,
        "denied": 7,
    }
    assert "probe-access" not in result.stdout + result.stderr
    assert commands == []
    assert (tmp_path / "curl.log").read_text().splitlines() == [
        'request = "GET"',
        'request = "GET"',
        'request = "GET"',
        'request = "GET"',
        'request = "PUT"',
        'request = "POST"',
        'request = "PUT"',
        'request = "DELETE"',
    ]


def test_cdn_discovery_uses_provider_cname_not_display_name(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    result, _ = _run_provision(tmp_path, {**cloud_environment, "FAKE_CDN_EXISTING": "1"})
    assert _plan(result)["resolved"]["cdn_resource_id"] == "existing-cdn-id"


def test_plan_allowlists_provider_fields_and_never_renders_private_values(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "service_account_id": "created-service-account-id",
                "security_group_id": "created-security-group-id",
                "reserved_address_id": "created-address-id",
                "reserved_address_ipv4": "198.51.100.44",
                "vm_id": "created-vm-id",
                "cdn_resource_id": "existing-cdn-id",
            }
        )
    )
    state.chmod(0o600)
    result, _ = _run_provision(
        tmp_path, {**cloud_environment, "IMAGE_ORIGIN_CONTRACT_STATE": str(state)}
    )
    assert result.returncode == 0
    assert "cdn-secure-private-sentinel" not in result.stdout
    assert "origin-header-private-sentinel" not in result.stdout
    assert "vm-metadata-private-sentinel" not in result.stdout


@pytest.mark.parametrize("drift", ["FAKE_EXTRA_INTERFACE", "FAKE_EXTRA_DISK"])
def test_reused_vm_rejects_extra_interfaces_and_disks(
    tmp_path: Path, cloud_environment: dict[str, str], drift: str
) -> None:
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "service_account_id": "created-service-account-id",
                "security_group_id": "created-security-group-id",
                "reserved_address_id": "created-address-id",
                "reserved_address_ipv4": "198.51.100.44",
                "vm_id": "created-vm-id",
            }
        )
    )
    state.chmod(0o600)
    result, _ = _run_provision(
        tmp_path, {**cloud_environment, "IMAGE_ORIGIN_CONTRACT_STATE": str(state), drift: "1"}
    )
    assert result.returncode == 2
    assert "vm_drift" in result.stderr


def test_incomplete_access_key_recovery_refuses_retry_before_mutation(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"access_key_resource_id": "orphan-access-key-id"}))
    state.chmod(0o600)
    result, commands = _run_provision(
        tmp_path, {**cloud_environment, "IMAGE_ORIGIN_CONTRACT_STATE": str(state)}
    )
    assert result.returncode == 2
    assert "access_key_initialization_incomplete" in result.stderr
    assert "orphan-access-key-id" in result.stderr
    assert all("create" not in command and "update" not in command for command in commands)


def test_lockbox_failure_preserves_access_key_and_retry_has_zero_mutations(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    state = tmp_path / "state.json"
    environment = {**cloud_environment, "IMAGE_ORIGIN_CONTRACT_STATE": str(state)}
    _apply_identity_phase(tmp_path, environment)
    reviewed, _ = _run_provision(tmp_path, environment)
    failed, _ = _run_provision(
        tmp_path,
        {**environment, "FAKE_FAIL_LOCKBOX": "1"},
        "--apply",
        "--approval-nonce",
        _plan(reviewed)["approval_nonce"],
    )
    assert failed.returncode == 2
    assert (
        json.loads(state.read_text())["access_key_resource_id"] == "created-access-key-resource-id"
    )
    retry, commands = _run_provision(tmp_path, environment)
    assert retry.returncode == 2
    assert "access_key_initialization_incomplete" in retry.stderr
    assert "created-access-key-resource-id" in retry.stderr
    assert all("create" not in command and "update" not in command for command in commands)


def test_plan_restores_task6_inventory_quota_and_exact_pricing_refresh(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    result, _ = _run_provision(tmp_path, cloud_environment)
    refresh = _plan(result)["task6_refresh"]
    rendered = "\n".join(" ".join(command) for command in refresh["commands"])
    assert all(
        item in rendered
        for item in (
            "profile list",
            "cloud-id",
            "folder-id",
            "instance list",
            "disk list",
            "network list",
            "subnet list",
            "quota-limit list",
        )
    )
    quota_commands = [
        command
        for command in refresh["commands"]
        if command[1:4] == ["quota-manager", "quota-limit", "list"]
    ]
    assert quota_commands == [
        [
            "yc",
            "quota-manager",
            "quota-limit",
            "list",
            "--resource-type",
            "resource-manager.cloud",
            "--resource-id",
            "cloud-contract-id",
            "--service",
            service,
            "--format",
            "json",
        ]
        for service in ("compute", "vpc", "cdn")
    ]
    assert refresh["pricing"]["items"] == [
        "vm_standard_v3_2vcpu_4gib",
        "network_hdd_20gib",
        "reserved_public_ipv4",
        "cdn_base_resource",
        "expected_egress",
    ]
    assert refresh["read_only"] is True


def test_origin_service_account_gets_only_monitoring_editor_for_custom_metrics(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    environment = {**cloud_environment, "IMAGE_ORIGIN_CONTRACT_STATE": str(tmp_path / "state.json")}
    _apply_identity_phase(tmp_path, environment)

    dry, commands = _run_provision(tmp_path, environment)
    plan = _plan(dry)
    assert plan["desired"]["service_account_roles"] == ["monitoring.editor"]
    assert plan["actual"]["service_account_roles"] == []
    binding = next(
        command
        for command in plan["proposed_commands"]
        if command[1:4] == ["resource-manager", "folder", "add-access-binding"]
    )
    assert binding == [
        "yc",
        "resource-manager",
        "folder",
        "add-access-binding",
        "--id",
        "folder-contract-id",
        "--role",
        "monitoring.editor",
        "--service-account-id",
        "created-service-account-id",
    ]
    assert any(
        command[:3] == ["resource-manager", "folder", "list-access-bindings"]
        for command in commands
    )


def test_existing_monitoring_editor_binding_is_reused_without_broader_role(
    tmp_path: Path, cloud_environment: dict[str, str]
) -> None:
    environment = {
        **cloud_environment,
        "IMAGE_ORIGIN_CONTRACT_STATE": str(tmp_path / "state.json"),
        "FAKE_MONITORING_BINDING": "1",
    }
    _apply_identity_phase(tmp_path, environment)

    dry, _ = _run_provision(tmp_path, environment)
    plan = _plan(dry)
    assert plan["actual"]["service_account_roles"] == ["monitoring.editor"]
    assert not any(
        command[1:4] == ["resource-manager", "folder", "add-access-binding"]
        for command in plan["proposed_commands"]
    )


def test_real_resolver_bootstrap_projection_applies_both_provisioning_phases(
    tmp_path: Path,
    cloud_environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = resolver_contract.resolver.__wrapped__()
    manifest = json.loads(MANIFEST.read_text())
    values = resolver_contract._sentinel_values(manifest)
    values["GALLERY_CDN_TOKEN_SECRET"] = "seed-token"
    values["IMAGE_ORIGIN_HEADER_SECRET"] = "seed-origin-header"
    state = tmp_path / "state.json"
    environment = {**cloud_environment, "IMAGE_ORIGIN_CONTRACT_STATE": str(state)}
    fake_bin, log = _install_fake_yc(tmp_path)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("FAKE_YC_LOG", str(log))
    monkeypatch.setenv("EXPECTED_RESOURCE_NAME", RESOURCE_NAME)
    monkeypatch.setenv("FAKE_CURL_LOG", str(tmp_path / "curl.log"))

    for expected_phase in ("resource-identities", "origin-and-policy"):
        dry, _ = _run_provision(tmp_path, environment)
        reviewed = _plan(dry)
        assert reviewed["phase"] == expected_phase
        http = resolver_contract._HttpBoundary(
            [resolver_contract._metadata(), resolver_contract._payload(values)]
        )
        assert (
            resolver_contract._run_main(
                resolver,
                monkeypatch,
                tmp_path,
                http,
                consumer="image-delivery-provision",
                command=[
                    "sh",
                    str(PROVISION),
                    "--apply",
                    "--approval-nonce",
                    reviewed["approval_nonce"],
                ],
                binary_dir=fake_bin,
            )
            == 0
        )
    persisted = json.loads(state.read_text())
    assert persisted["secrets_initialized"] is True
    assert persisted["access_key_resource_id"] == "created-access-key-resource-id"


@pytest.mark.parametrize(
    "keys",
    [
        ["GALLERY_CDN_TOKEN_SECRET"],
        ["GALLERY_CDN_TOKEN_SECRET", "IMAGE_ORIGIN_HEADER_SECRET", "GALLERY_IMGPROXY_KEY"],
    ],
)
def test_lockbox_rejects_every_non_bootstrap_partial_gallery_set(
    tmp_path: Path, cloud_environment: dict[str, str], keys: list[str]
) -> None:
    result, commands = _run_provision(
        tmp_path, {**cloud_environment, "FAKE_LOCKBOX_KEYS": json.dumps(keys)}
    )
    assert result.returncode == 2
    assert "lockbox_secret_set_partial" in result.stderr
    assert all("create" not in command and "update" not in command for command in commands)


def test_runbook_keeps_dark_deploy_first_and_s3_signal_as_activation_blocker() -> None:
    runbook = (ROOT / "docs/runbooks/gallery-image-delivery.md").read_text(encoding="utf-8")
    dark = runbook.index("gallery-cdn-images=off")
    provision = runbook.index("Provision the reviewed resources")
    validate = runbook.index("Validate the origin, credentials, and CDN gates")
    staff = runbook.index("gallery-cdn-images=staff")
    public = runbook.index("gallery-cdn-images=on")

    assert dark < provision < validate < staff < public
    assert "S3 403" in runbook
    assert "Task 6 staff-activation blocker" in runbook
    assert "wired alert" not in runbook.lower()
    assert "fresh manual approval" in runbook.lower()
