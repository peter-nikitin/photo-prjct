"""Canonical complete membership observations using each actual machine's launch evidence."""

from __future__ import annotations

import re
from typing import Any

from django.utils import timezone

from processing.models import WorkerPool
from processing.services.worker_pool_cloud import CloudReader, identifier, metadata_token
from processing.services.worker_pool_lifecycle import (
    CLOUD_STATES,
    OBSERVATION_MAX_AGE,
    RUNNING,
    STOPPED,
    record_cloud_snapshot,
)


def validate_config(config: dict[str, Any]) -> None:
    if set(config) != {
        "folder_id",
        "canonical_folder_id",
        "zone",
        "groups",
        "boot_image_id",
        "releases",
    }:
        raise ValueError("invalid cloud configuration")
    for key in ("folder_id", "canonical_folder_id", "zone", "boot_image_id"):
        identifier(config[key])
    if config["folder_id"] == config["canonical_folder_id"]:
        raise ValueError("worker and canonical folders must differ")
    if (
        not isinstance(config["groups"], dict)
        or not config["groups"]
        or not set(config["groups"]) <= {"bulk", "selfie"}
    ):
        raise ValueError("invalid group configuration")
    for value in config["groups"].values():
        identifier(value)
    if not isinstance(config["releases"], dict) or not 1 <= len(config["releases"]) <= 2:
        raise ValueError("invalid release configuration")
    for build, image in config["releases"].items():
        if (
            re.fullmatch(r"[0-9a-f]{40}", build) is None
            or not isinstance(image, str)
            or re.fullmatch(
                r"ghcr\.io/[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9._-]*-worker@sha256:[0-9a-f]{64}",
                image,
            )
            is None
        ):
            raise ValueError("invalid immutable release")


def observe_cloud(name: str, config: dict[str, Any], *, reader: CloudReader | None = None) -> bool:
    validate_config(config)
    group_id = config["groups"][name]
    pool = WorkerPool.objects.get(name=name)
    if pool.group_id != group_id:
        raise ValueError("wrong configured group")
    sequence = pool.observation_sequence + 1
    started = timezone.now()
    reader = reader or CloudReader(metadata_token())
    group = reader.get(f"instanceGroups/{group_id}", view="FULL")
    if (
        group.get("id") != group_id
        or group.get("folderId") != config["folder_id"]
        or group.get("allocationPolicy", {}).get("zones") != [{"zoneId": config["zone"]}]
    ):
        raise ValueError("wrong cloud scope")
    template = group["instanceTemplate"]
    if template["bootDiskSpec"]["diskSpec"]["imageId"] != config["boot_image_id"]:
        raise ValueError("unknown group template")
    managed = group["managedInstancesState"]
    if not isinstance(managed, dict):
        raise ValueError("invalid managed instance state")
    target = managed.get("targetSize", "0")
    if target not in {"0", "1", "2"}:
        raise ValueError("cloud target exceeds hard maximum")
    rows = reader.pages(f"instanceGroups/{group_id}/instances", "instances")
    members: list[dict[str, str]] = []
    ids: set[str] = set()
    if len(rows) > 4 or sum(row.get("status") != "DELETED" for row in rows) > 2:
        raise ValueError("cloud capacity exceeds hard maximum")
    for row in rows:
        status = row.get("status")
        instance_id = row.get("instanceId", "")
        if status not in CLOUD_STATES or row.get("zoneId") != config["zone"]:
            raise ValueError("unknown member state")
        if not instance_id:
            if status == "DELETED":
                continue
            if status in RUNNING | STOPPED:
                raise ValueError("unidentified running member")
            members.append({"instance_id": "", "status": status, "worker_build": ""})
            continue
        identifier(instance_id)
        if instance_id in ids:
            raise ValueError("duplicate cloud member")
        ids.add(instance_id)
        build = ""
        if status in RUNNING:
            instance = reader.get(f"instances/{instance_id}", view="FULL")
            if (
                instance.get("id") != instance_id
                or instance.get("folderId") != config["folder_id"]
                or instance.get("zoneId") != config["zone"]
            ):
                raise ValueError("wrong actual instance")
            actual = instance.get("metadata", {})
            build = actual.get("findme-worker-build", "")
            # Launch metadata is diagnostic only; an in-place container update leaves it stale.
            if not isinstance(build, str) or re.fullmatch(r"[0-9a-f]{40}", build) is None:
                build = ""
            disk_id = identifier(instance["bootDisk"]["diskId"])
            disk = reader.get(f"disks/{disk_id}")
            if (
                disk.get("id") != disk_id
                or disk.get("folderId") != config["folder_id"]
                or disk.get("sourceImageId") != config["boot_image_id"]
            ):
                raise ValueError("unverified boot image")
            for nic in instance["networkInterfaces"]:
                if nic.get("primaryV4Address", {}).get("oneToOneNat") or nic.get(
                    "primaryV6Address"
                ):
                    raise ValueError("public worker interface")
        members.append({"instance_id": instance_id, "status": status, "worker_build": build})
    # A changing template/target is not a coherent observation across pagination.
    after = reader.get(f"instanceGroups/{group_id}", view="FULL")
    if after != group:
        raise ValueError("cloud group changed during observation")
    completed = timezone.now()
    if completed - started > OBSERVATION_MAX_AGE:
        raise ValueError("cloud snapshot collection stale")
    return record_cloud_snapshot(
        name,
        group_id=group_id,
        sequence=sequence,
        started_at=started,
        completed_at=completed,
        target_size=int(target),
        members=members,
        complete=True,
    )
