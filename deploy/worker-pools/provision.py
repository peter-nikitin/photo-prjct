#!/usr/bin/env python3
"""Checksum-bound preparation/update of exactly two managed worker Instance Groups.

Prerequisite IAM, network, SG, egress, Lockbox and reviewed OS image creation is deliberately
outside this interface. Default mode is local preparation only. Inspect/status never mutate.
An apply receipt fences uncertain submissions; there is no automatic recreation or retry.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/backend"))
from processing.services import worker_pool_cloud  # noqa: E402
from processing.services.worker_pool_cloud import (  # noqa: E402
    COMPUTE,
    CloudReader,
    identifier,
    metadata_token,
    request_bytes,
    request_json,
)

FIELDS = {
    "pool_max_size",
    "cloud_id",
    "folder_id",
    "canonical_folder_id",
    "zone",
    "network_id",
    "subnet_id",
    "worker_sg_id",
    "canonical_vm_id",
    "private_api_ipv4",
    "worker_sa_id",
    "manager_sa_id",
    "bootstrap_secret_id",
    "bootstrap_version_id",
    "application_secret_id",
    "boot_image_id",
    "docker_version",
    "compose_version",
    "worker_build",
    "worker_image",
    "egress_gateway_id",
    "route_table_id",
    "groups",
}
LABELS = {"project": "findme-photo", "deployment": "canonical", "managed-by": "worker-pools"}
MANAGED_FIELDS = (
    "name",
    "labels",
    "instanceTemplate",
    "scalePolicy",
    "deployPolicy",
    "allocationPolicy",
    "serviceAccountId",
    "deletionProtection",
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate(config):
    if (
        not isinstance(config, dict)
        or set(config) - {"telemetry_enabled", "predecessors"} != FIELDS
    ):
        raise ValueError("unsupported provisioning input")
    predecessors = config.get("predecessors")
    if predecessors is not None:
        if (
            not isinstance(predecessors, dict)
            or set(predecessors) != {"bulk", "selfie"}
            or any(
                not isinstance(row, dict)
                or set(row) != {"group_id", "active_build"}
                or not isinstance(row["active_build"], str)
                or re.fullmatch(r"[0-9a-f]{40}", row["active_build"]) is None
                or not identifier(row["group_id"])
                for row in predecessors.values()
            )
        ):
            raise ValueError("invalid predecessor identities")
        if len({row["group_id"] for row in predecessors.values()}) != 2:
            raise ValueError("duplicate predecessor group")
    if type(config.get("telemetry_enabled", False)) is not bool:
        raise ValueError("explicit boolean telemetry opt-in required")
    if type(config["pool_max_size"]) is not int or config["pool_max_size"] not in {1, 2}:
        raise ValueError("explicit integer pool maximum of one or two required")
    if config["zone"] not in {"ru-central1-a", "ru-central1-b", "ru-central1-d"}:
        raise ValueError("unsupported worker zone")
    for key in FIELDS - {
        "pool_max_size",
        "groups",
        "private_api_ipv4",
        "worker_build",
        "worker_image",
        "docker_version",
        "compose_version",
    }:
        identifier(config[key])
    if config["folder_id"] == config["canonical_folder_id"]:
        raise ValueError("worker and canonical folders must differ")
    address = ipaddress.IPv4Address(config["private_api_ipv4"])
    if not any(
        address in ipaddress.IPv4Network(cidr)
        for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    ):
        raise ValueError("private endpoint required")
    if (
        config["bootstrap_secret_id"] == config["application_secret_id"]
        or config["worker_sa_id"] == config["manager_sa_id"]
    ):
        raise ValueError("worker authority must be isolated")
    if (
        re.fullmatch(r"[0-9a-f]{40}", config["worker_build"]) is None
        or re.fullmatch(
            r"ghcr\.io/[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9._-]*-worker@sha256:[0-9a-f]{64}",
            config["worker_image"],
        )
        is None
    ):
        raise ValueError("immutable worker release required")
    for key in ("docker_version", "compose_version"):
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", config[key]) is None:
            raise ValueError("reviewed installed runtime required")
    if not isinstance(config["groups"], dict) or set(config["groups"]) != {"bulk", "selfie"}:
        raise ValueError("exactly two managed groups required")
    ids = []
    for entry in config["groups"].values():
        if not isinstance(entry, dict) or set(entry) != {"id", "baseline"}:
            raise ValueError("invalid managed group")
        if entry["id"] is None:
            if entry["baseline"] is not None:
                raise ValueError("creation requires expected absence")
        else:
            ids.append(identifier(entry["id"]))
            if (
                not isinstance(entry["baseline"], str)
                or re.fullmatch(r"[0-9a-f]{64}", entry["baseline"]) is None
            ):
                raise ValueError("update requires exact inspected baseline")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate group IDs")
    if predecessors is not None and any(
        entry["id"] is not None for entry in config["groups"].values()
    ):
        if any(
            entry["id"] in {row["group_id"] for row in predecessors.values()}
            for entry in config["groups"].values()
        ):
            raise ValueError("predecessor cannot be a candidate group")


def cloud_init(config, pool):
    runtime = {
        key: config[key]
        for key in (
            "bootstrap_secret_id",
            "bootstrap_version_id",
            "worker_image",
            "docker_version",
            "compose_version",
            "private_api_ipv4",
        )
    }
    runtime["worker_image"] = config["worker_image"].split("@", 1)[0] + ":latest"
    contract = dict(
        line.split("=", 1)
        for line in (ROOT / "deploy/worker-pools/contract.env.example").read_text().splitlines()
        if line and not line.startswith("#")
    )
    runtime.update(
        pool=pool,
        identities=contract[f"WORKER_POOL_{pool.upper()}_IDENTITIES"],
        zone=config["zone"],
        telemetry_enabled=config.get("telemetry_enabled", False),
    )
    files = {
        "/etc/findme-worker/bootstrap.json": json.dumps(runtime, sort_keys=True),
        "/usr/local/lib/findme-worker/bootstrap.py": (
            ROOT / "deploy/worker-pools/bootstrap.py"
        ).read_text(),
        "/usr/local/lib/findme-worker/worker_pool_cloud.py": Path(
            worker_pool_cloud.__file__
        ).read_text(),
        "/usr/local/lib/findme-worker/compose.yml": (
            ROOT / "deploy/worker-pools/compose.yml"
        ).read_text(),
        "/usr/local/lib/findme-worker/retire.py": (
            ROOT / "deploy/worker-pools/retire.py"
        ).read_text(),
        "/etc/systemd/system/findme-worker-retire.service": (
            ROOT / "deploy/worker-pools/retire.service"
        ).read_text(),
        "/etc/systemd/system/findme-worker-retire.timer": (
            ROOT / "deploy/worker-pools/retire.timer"
        ).read_text(),
        "/usr/local/lib/findme-worker/telemetry.py": (
            ROOT / "deploy/worker-pools/telemetry.py"
        ).read_text(),
        "/usr/local/lib/findme-worker/telemetry-requirements.txt": (
            ROOT / "deploy/worker-pools/telemetry-requirements.txt"
        ).read_text(),
        "/etc/systemd/system/findme-worker-telemetry.service": (
            ROOT / "deploy/worker-pools/telemetry.service"
        ).read_text(),
        "/etc/systemd/system/findme-worker-telemetry.timer": (
            ROOT / "deploy/worker-pools/telemetry.timer"
        ).read_text(),
    }
    for name in ("host.py", "updater.py", "updater.service", "updater.timer"):
        target = (
            f"/etc/systemd/system/findme-worker-{name}"
            if name.endswith((".service", ".timer"))
            else f"/usr/local/lib/findme-worker/{name}"
        )
        files[target] = (ROOT / "deploy/worker-pools" / name).read_text()
    # Reviewed base image already contains Python, Docker and Compose. No apt/curl installs.
    return "#cloud-config\n" + json.dumps(
        {
            "write_files": [
                {
                    "path": path,
                    "owner": "root:root",
                    "permissions": "0600" if path.endswith("bootstrap.json") else "0644",
                    "encoding": "b64",
                    "content": base64.b64encode(content.encode()).decode(),
                }
                for path, content in files.items()
            ],
            "runcmd": [
                [
                    "python3",
                    "/usr/local/lib/findme-worker/bootstrap.py",
                    "--config",
                    "/etc/findme-worker/bootstrap.json",
                ]
            ],
        },
        sort_keys=True,
    )


def prepare(config):
    validate(config)
    groups = {}
    for pool in ("bulk", "selfie"):
        groups[pool] = {
            "folderId": config["folder_id"],
            "name": f"findme-photo-worker-{pool}",
            "labels": LABELS | {"pool": pool},
            "serviceAccountId": config["manager_sa_id"],
            "deletionProtection": True,
            "instanceTemplate": {
                "platformId": "standard-v3",
                "resourcesSpec": {"cores": "2", "coreFraction": "100", "memory": "8589934592"},
                "bootDiskSpec": {
                    "mode": "READ_WRITE",
                    "diskSpec": {
                        "typeId": "network-ssd",
                        "size": "34359738368",
                        "imageId": config["boot_image_id"],
                        "preserveAfterInstanceDelete": False,
                    },
                },
                "networkInterfaceSpecs": [
                    {
                        "networkId": config["network_id"],
                        "subnetIds": [config["subnet_id"]],
                        "primaryV4AddressSpec": {},
                        "securityGroupIds": [config["worker_sg_id"]],
                    }
                ],
                "schedulingPolicy": {"preemptible": pool == "bulk"},
                "serviceAccountId": config["worker_sa_id"],
                "metadata": {
                    "findme-worker-build": config["worker_build"],
                    "findme-worker-image": config["worker_image"],
                    "user-data": cloud_init(config, pool),
                },
            },
            "scalePolicy": {
                "autoScale": {
                    "minZoneSize": "0" if pool == "bulk" else "1",
                    "maxSize": str(config["pool_max_size"]),
                    "initialSize": "1",
                    "measurementDuration": "60s",
                    "warmupDuration": "300s",
                    "stabilizationDuration": "300s",
                    "autoScaleType": "ZONAL",
                    "customRules": [
                        {
                            "ruleType": "WORKLOAD",
                            "metricType": "GAUGE",
                            "metricName": "worker_pool_workload",
                            "labels": {"pool": pool, "zone_id": config["zone"]},
                            "target": "1",
                            "folderId": config["canonical_folder_id"],
                            "service": "custom",
                        }
                    ],
                }
            },
            "deployPolicy": {
                "strategy": "OPPORTUNISTIC",
                "maxUnavailable": "1",
                "maxExpansion": "0",
                "maxDeleting": "1",
                "maxCreating": "1",
                "startupDuration": "600s",
            },
            "allocationPolicy": {"zones": [{"zoneId": config["zone"]}]},
        }
    plan = {
        "configuration": config,
        "groups": groups,
        "boundary": (
            "create/update exact worker groups only; "
            "organization/effective-policy IAM review, prerequisites, and "
            "live acceptance remain separate"
        ),
    }
    return plan | {"checksum": digest(plan)}


def managed_baseline(group):
    return digest({key: group.get(key) for key in MANAGED_FIELDS})


def allows_private_port(rule):
    if rule.get("direction") != "INGRESS" or rule.get("protocolName", "ANY") not in {"ANY", "TCP"}:
        return False
    ports = rule.get("ports")
    return not ports or int(ports.get("fromPort", 0)) <= 8443 <= int(ports.get("toPort", 65535))


def validate_private_edge(config, canonical, groups):
    if (
        canonical.get("id") != config["canonical_vm_id"]
        or canonical.get("folderId") != config["canonical_folder_id"]
    ):
        raise ValueError("wrong canonical VM")
    found = False
    allowed = False
    for nic in canonical["networkInterfaces"]:
        found = (
            found or nic.get("primaryV4Address", {}).get("address") == config["private_api_ipv4"]
        )
        # Caller resolves implicit/default groups from the actual NIC network. Empty is unknown.
        attached = nic.get("securityGroupIds")
        if not attached:
            raise ValueError("unknown effective canonical security group union")
        for group_id in attached:
            for rule in groups[group_id]["rules"]:
                if allows_private_port(rule):
                    if (
                        rule.get("securityGroupId") != config["worker_sg_id"]
                        or rule.get("cidrBlocks")
                        or rule.get("predefinedTarget")
                    ):
                        raise ValueError("canonical 8443 is reachable outside exact worker SG")
                    allowed = True
    if not found or not allowed:
        raise ValueError("canonical private edge prerequisite missing")


class Cloud(CloudReader):
    def resource(self, service, collection, resource_id):
        base = {
            "vpc": "https://vpc.api.cloud.yandex.net/vpc/v1",
            "lockbox": "https://lockbox.api.cloud.yandex.net/lockbox/v1",
            "resourcemanager": "https://resource-manager.api.cloud.yandex.net/resource-manager/v1",
            "iam": "https://iam.api.cloud.yandex.net/iam/v1",
        }[service]
        return request_json(
            Request(
                f"{base}/{collection}/{identifier(resource_id)}",
                headers={"Authorization": f"Bearer {self.token}"},
            )
        )

    def bindings(self, service, collection, resource_id):
        base = {
            "lockbox": "https://lockbox.api.cloud.yandex.net/lockbox/v1",
            "resourcemanager": "https://resource-manager.api.cloud.yandex.net/resource-manager/v1",
            "compute": COMPUTE,
        }[service]
        rows, page, seen = [], "", set()
        from urllib.parse import urlencode

        for _ in range(10):
            result = request_json(
                Request(
                    f"{base}/{collection}/{identifier(resource_id)}:listAccessBindings?"
                    + urlencode({"pageToken": page, "pageSize": "100"}),
                    headers={"Authorization": f"Bearer {self.token}"},
                )
            )
            rows.extend(result.get("accessBindings", []))
            page = result.get("nextPageToken", "")
            if not page:
                return rows
            if not isinstance(page, str) or page in seen:
                raise ValueError("incomplete access bindings")
            seen.add(page)
        raise ValueError("incomplete access bindings")

    def mutate(self, method, path, body):
        return request_json(
            Request(
                f"{COMPUTE}/{path}",
                data=json.dumps(body, separators=(",", ":")).encode(),
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                method=method,
            )
        )


def relevant_bindings(rows, worker):
    return [
        row
        for row in rows
        if row.get("subject", {}).get("id") in {worker, "allUsers", "allAuthenticatedUsers"}
    ]


def inspect(config, cloud):
    validate(config)
    for folder_id in (config["folder_id"], config["canonical_folder_id"]):
        folder = cloud.resource("resourcemanager", "folders", folder_id)
        if folder.get("id") != folder_id or folder.get("cloudId") != config["cloud_id"]:
            raise ValueError("wrong explicit cloud/folder")
    for account in ("manager_sa_id", "worker_sa_id"):
        identity = cloud.resource("iam", "serviceAccounts", config[account])
        if identity.get("id") != config[account] or identity.get("folderId") != config["folder_id"]:
            raise ValueError("worker identity belongs outside worker folder")
    # Direct cloud/folder bindings are inspectable. Organization and effective-policy
    # authority require a separate operator read-back before activation.
    for kind, resource in (
        ("clouds", config["cloud_id"]),
        ("folders", config["folder_id"]),
        ("folders", config["canonical_folder_id"]),
    ):
        rows = cloud.bindings("resourcemanager", kind, resource)
        if relevant_bindings(rows, config["worker_sa_id"]):
            raise ValueError("worker has ancestor authority")
        manager = relevant_bindings(rows, config["manager_sa_id"])
        expected = {
            config["cloud_id"]: set(),
            config["folder_id"]: {"compute.editor"},
            config["canonical_folder_id"]: {"vpc.user", "monitoring.viewer"},
        }[resource]
        if (
            {row.get("roleId") for row in manager} != expected
            or len(manager) != len(expected)
            or any(
                row.get("subject") != {"id": config["manager_sa_id"], "type": "serviceAccount"}
                for row in manager
            )
        ):
            raise ValueError("manager authority differs from reviewed folder scope")
    for service, collection, resource, role in (
        ("lockbox", "secrets", config["bootstrap_secret_id"], "lockbox.payloadViewer"),
    ):
        grants = relevant_bindings(
            cloud.bindings(service, collection, resource), config["worker_sa_id"]
        )
        if grants != [
            {"roleId": role, "subject": {"id": config["worker_sa_id"], "type": "serviceAccount"}}
        ]:
            raise ValueError("worker scoped authority differs from narrow prerequisite")
    application_grants = cloud.bindings("lockbox", "secrets", config["application_secret_id"])
    if any(
        relevant_bindings(application_grants, config[account])
        for account in ("manager_sa_id", "worker_sa_id")
    ):
        raise ValueError("worker identity has direct application secret authority")
    application = cloud.resource("lockbox", "secrets", config["application_secret_id"])
    if (
        application.get("id") != config["application_secret_id"]
        or application.get("folderId") != config["canonical_folder_id"]
    ):
        raise ValueError("wrong canonical application secret")
    secret = cloud.resource("lockbox", "secrets", config["bootstrap_secret_id"])
    if (
        secret.get("id") != config["bootstrap_secret_id"]
        or secret.get("folderId") != config["folder_id"]
        or secret.get("status") != "ACTIVE"
        or secret.get("currentVersion", {}).get("id") != config["bootstrap_version_id"]
        or set(secret.get("currentVersion", {}).get("payloadEntryKeys", []))
        != {"PHOTO_PROCESSING_FLEET_TOKEN", "IMAGE_PULL_AUTH"}
    ):
        raise ValueError("narrow bootstrap secret prerequisite missing")
    subnet = cloud.resource("vpc", "subnets", config["subnet_id"])
    network = cloud.resource("vpc", "networks", config["network_id"])
    if (
        network.get("id") != config["network_id"]
        or network.get("folderId") != config["canonical_folder_id"]
    ):
        raise ValueError("shared VPC belongs outside canonical folder")
    if (
        subnet.get("folderId") != config["folder_id"]
        or subnet.get("zoneId") != config["zone"]
        or subnet.get("networkId") != config["network_id"]
        or subnet.get("routeTableId") != config["route_table_id"]
    ):
        raise ValueError("wrong private subnet/egress")
    routes = cloud.resource("vpc", "routeTables", config["route_table_id"])
    if (
        routes.get("folderId") != config["folder_id"]
        or routes.get("networkId") != config["network_id"]
        or not any(
            row.get("destinationPrefix") == "0.0.0.0/0"
            and row.get("gatewayId") == config["egress_gateway_id"]
            for row in routes.get("staticRoutes", [])
        )
    ):
        raise ValueError("private egress prerequisite missing")
    gateway = cloud.resource("vpc", "gateways", config["egress_gateway_id"])
    if gateway.get("folderId") != config["folder_id"] or "sharedEgressGateway" not in gateway:
        raise ValueError("wrong private egress gateway")
    worker_sg = cloud.resource("vpc", "securityGroups", config["worker_sg_id"])
    if (
        worker_sg.get("folderId") != config["folder_id"]
        or worker_sg.get("networkId") != config["network_id"]
        or any(rule.get("direction") == "INGRESS" for rule in worker_sg.get("rules", []))
        or not any(rule.get("direction") == "EGRESS" for rule in worker_sg.get("rules", []))
    ):
        raise ValueError("worker SG prerequisite missing")
    canonical = cloud.get(f"instances/{config['canonical_vm_id']}", view="FULL")
    canonical_grants = cloud.bindings("compute", "instances", config["canonical_vm_id"])
    if any(
        relevant_bindings(canonical_grants, config[account])
        for account in ("manager_sa_id", "worker_sa_id")
    ):
        raise ValueError("worker identity has direct canonical VM authority")
    attached_disks = [canonical.get("bootDisk", {})] + canonical.get("secondaryDisks", [])
    for attached in attached_disks:
        disk_id = identifier(attached.get("diskId"))
        disk = cloud.get(f"disks/{disk_id}")
        if disk.get("id") != disk_id or disk.get("folderId") != config["canonical_folder_id"]:
            raise ValueError("canonical disk belongs outside canonical folder")
        disk_grants = cloud.bindings("compute", "disks", disk_id)
        if any(
            relevant_bindings(disk_grants, config[account])
            for account in ("manager_sa_id", "worker_sa_id")
        ):
            raise ValueError("worker identity has direct canonical disk authority")
    groups = {}
    for nic in canonical["networkInterfaces"]:
        nic_subnet = cloud.resource("vpc", "subnets", identifier(nic.get("subnetId")))
        if (
            nic_subnet.get("folderId") != config["canonical_folder_id"]
            or nic_subnet.get("networkId") != config["network_id"]
        ):
            raise ValueError("canonical NIC is outside reviewed network")
        if not nic.get("securityGroupIds"):
            nic["securityGroupIds"] = [identifier(network.get("defaultSecurityGroupId"))]
        for resource in nic["securityGroupIds"]:
            group = cloud.resource("vpc", "securityGroups", resource)
            if (
                group.get("folderId") != config["canonical_folder_id"]
                or group.get("networkId") != config["network_id"]
            ):
                raise ValueError("canonical security group is outside reviewed network")
            groups[resource] = group
    validate_private_edge(config, canonical, groups)
    image = cloud.get(f"images/{config['boot_image_id']}")
    if (
        image.get("id") != config["boot_image_id"]
        or image.get("folderId") != config["folder_id"]
        or image.get("status") != "READY"
    ):
        raise ValueError("reviewed base image prerequisite missing")
    existing = cloud.pages("instanceGroups", "instanceGroups", folderId=config["folder_id"])
    managed = {}
    for pool, entry in config["groups"].items():
        candidates = [row for row in existing if row.get("name") == f"findme-photo-worker-{pool}"]
        if entry["id"] is None:
            if candidates:
                raise ValueError("creation target already exists; inspect and reconcile")
            managed[pool] = None
        else:
            actual = cloud.get(f"instanceGroups/{entry['id']}", view="FULL")
            if (
                len(candidates) != 1
                or candidates[0].get("id") != entry["id"]
                or actual.get("folderId") != config["folder_id"]
                or actual.get("labels") != LABELS | {"pool": pool}
                or actual.get("name") != f"findme-photo-worker-{pool}"
            ):
                raise ValueError("unknown or ambiguous managed target")
            if managed_baseline(actual) != entry["baseline"]:
                raise ValueError("managed target drift")
            members = cloud.pages(f"instanceGroups/{entry['id']}/instances", "instances")
            if sum(row.get("status") != "DELETED" for row in members) > 2:
                raise ValueError("existing capacity exceeds hard maximum")
            managed[pool] = {"id": entry["id"], "baseline": managed_baseline(actual)}
    return managed


def write_receipt(path, value, *, exclusive=False):
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_EXCL if exclusive else os.O_TRUNC)
    with os.fdopen(os.open(path, flags, 0o600), "w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


def apply(config, checksum, *, cloud, receipt_path=None):
    plan = prepare(config)
    if checksum != plan["checksum"]:
        raise ValueError("reviewed checksum mismatch")
    if receipt_path is None or Path(receipt_path).exists():
        raise ValueError("fresh durable receipt required; reconcile prior submission first")
    inspect(config, cloud)
    if any(entry["id"] is None for entry in config["groups"].values()):
        raise ValueError("existing managed group IDs required; initial creation is retired")
    receipt = {
        "checksum": checksum,
        "folder_id": config["folder_id"],
        "canonical_folder_id": config["canonical_folder_id"],
        "groups": {},
    }
    write_receipt(receipt_path, receipt, exclusive=True)
    for pool, body in plan["groups"].items():
        entry = config["groups"][pool]
        # Recheck each concrete target immediately before its submission, including after
        # a preceding group update. Any concurrent operator drift aborts the remainder.
        if (
            managed_baseline(cloud.get(f"instanceGroups/{entry['id']}", view="FULL"))
            != entry["baseline"]
        ):
            raise ValueError("managed target drift after inspection")
        receipt["groups"][pool] = {
            "name": body["name"],
            "id": entry["id"],
            "state": "submission_uncertain",
        }
        write_receipt(receipt_path, receipt)
        # Do not retry a timed-out mutation. Exact-name status and durable receipt reconcile it.
        update = {key: body[key] for key in MANAGED_FIELDS}
        update["updateMask"] = ",".join(MANAGED_FIELDS)
        operation = cloud.mutate("PATCH", f"instanceGroups/{entry['id']}", update)
        operation_id = identifier(operation.get("id"))
        resource_id = identifier(operation.get("metadata", {}).get("instanceGroupId"))
        if entry["id"] is not None and resource_id != entry["id"]:
            raise ValueError("mutation returned wrong target")
        receipt["groups"][pool].update(id=resource_id, operation_id=operation_id, state="submitted")
        write_receipt(receipt_path, receipt)
    return receipt


def status(config, cloud):
    validate(config)
    existing = cloud.pages("instanceGroups", "instanceGroups", folderId=config["folder_id"])
    result = {}
    for pool in ("bulk", "selfie"):
        matches = [row for row in existing if row.get("name") == f"findme-photo-worker-{pool}"]
        if len(matches) > 1 or any(row.get("labels") != LABELS | {"pool": pool} for row in matches):
            raise ValueError("ambiguous or unmanaged target")
        if not matches:
            result[pool] = {"state": "absent"}
            continue
        group_id = identifier(matches[0]["id"])
        if config["groups"][pool]["id"] not in {None, group_id}:
            raise ValueError("status target mismatch")
        group = cloud.get(f"instanceGroups/{group_id}", view="FULL")
        if (
            group.get("id") != group_id
            or group.get("folderId") != config["folder_id"]
            or group.get("name") != f"findme-photo-worker-{pool}"
            or group.get("labels") != LABELS | {"pool": pool}
        ):
            raise ValueError("wrong status target")
        result[pool] = {
            "id": group_id,
            "baseline": managed_baseline(group),
            "scale_policy": group.get("scalePolicy"),
            "state": group.get("status"),
            "managed_instances": group.get("managedInstancesState"),
        }
    return result


def install_updater(config, *, cloud):
    """One-time cap-one install. Never used by ordinary worker image publication."""
    inspect(config, cloud)
    snapshots = {}
    for pool, entry in config["groups"].items():
        if entry["id"] is None:
            raise ValueError("existing exact group IDs required")
        group = cloud.get(f"instanceGroups/{entry['id']}", view="FULL")
        policy = group.get("scalePolicy", {}).get("autoScale", {})
        deploy = group.get("deployPolicy", {})
        min_zone_size = policy.get("minZoneSize")
        min_zone_size_valid = (
            pool == "bulk" and ("minZoneSize" not in policy or str(min_zone_size) == "0")
        ) or (pool == "selfie" and str(min_zone_size) == "1")
        if (
            str(policy.get("maxSize")) != "1"
            or not min_zone_size_valid
            or str(deploy.get("maxExpansion", "0")) != "0"
        ):
            raise ValueError("one-time install requires cap one without expansion")
        if (
            len(
                [
                    row
                    for row in cloud.pages(f"instanceGroups/{entry['id']}/instances", "instances")
                    if row.get("status") != "DELETED"
                ]
            )
            > 1
        ):
            raise ValueError("one-time install requires cap one")
        snapshots[pool] = group
    result = {}
    for pool, original in snapshots.items():
        entry = config["groups"][pool]
        if (
            managed_baseline(cloud.get(f"instanceGroups/{entry['id']}", view="FULL"))
            != entry["baseline"]
        ):
            raise ValueError("managed target drift before updater install")
        metadata = deepcopy(original["instanceTemplate"]["metadata"])
        metadata["user-data"] = cloud_init(config, pool)
        # Only userdata changes. The old build/image metadata is inert; no group shape,
        # disks, scale policy, labels, SSH access or deployment policy is reconstructed.
        body = {
            "updateMask": "instanceTemplate.metadata",
            "instanceTemplate": {"metadata": metadata},
        }
        operation = cloud.mutate("PATCH", f"instanceGroups/{entry['id']}", body)
        if operation.get("metadata", {}).get("instanceGroupId") != entry["id"]:
            raise ValueError("updater install returned wrong target; inspect before retry")
        expected = deepcopy(original)
        expected["instanceTemplate"]["metadata"] = metadata
        current = cloud.get(f"instanceGroups/{entry['id']}", view="FULL")
        if managed_baseline(current) != managed_baseline(expected):
            raise ValueError("updater install read-back differs; inspect before retry")
        result[pool] = {
            "id": entry["id"],
            "operation_id": identifier(operation["id"]),
            "baseline": managed_baseline(current),
            "template_verified": True,
        }
    return result


def canonical_token(config):
    instance = (
        request_bytes(
            Request(
                "http://169.254.169.254/computeMetadata/v1/instance/id",
                headers={"Metadata-Flavor": "Google"},
            ),
            max_body=64,
        )
        .decode()
        .strip()
    )
    if instance != config["canonical_vm_id"]:
        raise ValueError("one-time install must run on exact canonical VM")
    return metadata_token()


def replace_selfie(config, managed_id, *, cloud):
    """Explicit one-time pause: operator first pauses claims and verifies zero live work.

    Recreate exactly the listed managed instance so cloud-init runs on a new boot disk.
    The caller must inspect the returned operation before doing anything else; no retry.
    """
    identifier(managed_id)
    inspect(config, cloud)
    group_id = identifier(config["groups"]["selfie"]["id"])
    group = cloud.get(f"instanceGroups/{group_id}", view="FULL")
    if (
        str(group.get("scalePolicy", {}).get("autoScale", {}).get("maxSize")) != "1"
        or str(group.get("deployPolicy", {}).get("maxExpansion", "0")) != "0"
    ):
        raise ValueError("one-time recreate requires cap one without expansion")
    if group["instanceTemplate"]["metadata"].get("user-data") != cloud_init(config, "selfie"):
        raise ValueError("install updater template before one-time recreate")
    members = [
        row
        for row in cloud.pages(f"instanceGroups/{group_id}/instances", "instances")
        if row.get("status") != "DELETED"
    ]
    if (
        len(members) != 1
        or members[0].get("id") != managed_id
        or members[0].get("status") not in {"RUNNING_ACTUAL", "RUNNING_OUTDATED"}
    ):
        raise ValueError("exact running selfie managed instance required")
    operation = cloud.mutate(
        "POST", f"instanceGroups/{group_id}:rollingRecreate", {"managedInstanceIds": [managed_id]}
    )
    if operation.get("metadata", {}).get("instanceGroupId") != group_id or operation.get("error"):
        raise ValueError("recreate operation unverified; inspect before retry")
    readback = cloud.get(f"instanceGroups/{group_id}", view="FULL")
    if managed_baseline(readback) != managed_baseline(group):
        raise ValueError("group drift after recreate submission; inspect before retry")
    return {
        "id": group_id,
        "managed_instance_id": managed_id,
        "operation_id": identifier(operation["id"]),
        "operation_done": operation.get("done") is True,
        "group_status": readback.get("status"),
        "runtime_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--profile")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--apply", metavar="REVIEWED_SHA256")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--install-updater", action="store_true")
    parser.add_argument("--canonical-identity", action="store_true")
    parser.add_argument(
        "--replace-selfie",
        metavar="MANAGED_INSTANCE_ID",
        help="one-time recreate after operator pauses claims and verifies zero live work",
    )
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        plan = prepare(config)
        if (
            sum(
                bool(value)
                for value in (
                    args.inspect,
                    args.status,
                    args.apply,
                    args.install_updater,
                    args.replace_selfie,
                )
            )
            > 1
        ):
            raise ValueError("one explicit operation required")
        if not any(
            (args.inspect, args.status, args.apply, args.install_updater, args.replace_selfie)
        ):
            result = plan
        else:
            if bool(args.profile) == bool(args.canonical_identity):
                raise ValueError("one explicit cloud identity required")
            token = (
                canonical_token(config)
                if args.canonical_identity
                else subprocess.run(
                    ["yc", "--profile", args.profile, "iam", "create-token"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=20,
                ).stdout.strip()
            )
            if not token or any(c.isspace() for c in token):
                raise ValueError("cloud identity unavailable")
            cloud = Cloud(token)
            result = (
                replace_selfie(config, args.replace_selfie, cloud=cloud)
                if args.replace_selfie
                else install_updater(config, cloud=cloud)
                if args.install_updater
                else apply(
                    config,
                    args.apply,
                    cloud=cloud,
                    receipt_path=args.receipt,
                )
                if args.apply
                else status(config, cloud)
                if args.status
                else {"inspected": inspect(config, cloud), "checksum": plan["checksum"]}
            )
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("worker pool preparation failed; inspect any durable receipt before retry")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
