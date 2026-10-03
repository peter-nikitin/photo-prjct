#!/usr/bin/env python3
"""Fleet phases of canonical Deploy. No worker-token administration or independent scheduler."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from urllib.request import Request

RUNNING = {"RUNNING_ACTUAL", "RUNNING_OUTDATED"}
TERMINAL = {"STOPPED", "DELETED"}
NATIVE_CAPABILITY = "ru.findme-photo.historical-adaface-contract"


def require_native_compatible_release(root, app_image, worker_image, *, run=subprocess.run):
    """Read current canonical DB before changing any package, image or cloud template."""
    root = Path(root)
    if not (root / ".env").is_file():
        return
    host = Host(root, None, None)
    host.run = run
    # Query the DB directly: an unhealthy web must not prevent initial no-native abort.
    # Any historical native activation on a currently AdaFace event is conservative;
    # supported event selection forbids returning that event to legacy evidence.
    proof = run(
        host.compose
        + [
            "exec",
            "-T",
            "db",
            "sh",
            "-c",
            "exec psql --no-psqlrc --no-password --tuples-only --no-align "
            '--username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command "$1"',
            "native-generation-proof",
            "SELECT EXISTS (SELECT 1 FROM processing_eventfaceembeddingactivation a "
            "JOIN picflow_event e ON e.id = a.event_id "
            "WHERE e.face_search_generation = 'adaface_v5' "
            "AND a.generations @> "
            """'[{"configuration":{"embedding_storage":"vector_only"}}]'::jsonb)::text;""",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=60,
    )
    native = json.loads(proof.stdout)
    if type(native) is not bool:
        raise ValueError("invalid active native-generation proof")
    if not native:
        return
    if not app_image or not worker_image:
        raise ValueError("active native generation requires a compatible remote release")
    for image in (app_image, worker_image):
        run(["docker", "pull", image], check=True, capture_output=True, timeout=600)
        result = run(
            ["docker", "image", "inspect", image],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        rows = json.loads(result.stdout)
        if (
            len(rows) != 1
            or rows[0].get("Config", {}).get("Labels", {}).get(NATIVE_CAPABILITY)
            != "vector-only-v1"
        ):
            raise ValueError("image is incompatible with active native AdaFace generations")


def deployment_guard(root, app_image=None, worker_image=None, manifest_path=None):
    root = Path(root)
    marker = root / "worker-pools-current.json"
    receipt_path = root / "worker-pools-release.json"
    if not marker.is_file() or not receipt_path.is_file():
        raise ValueError("committed remote fleet marker is required")
    receipt = Journal(receipt_path).data
    if receipt.get("pending"):
        raise ValueError("unfinished release; uncertain cloud submission")
    if receipt.get("phase") not in {"committed", "rolled-back"}:
        raise ValueError("committed remote fleet release is required")
    if not worker_image and manifest_path and Path(manifest_path).is_file():
        worker_image = (
            json.loads(Path(manifest_path).read_text()).get("configuration", {}).get("worker_image")
        )
    if not worker_image and (Path(root) / "worker-pools-current.json").is_file():
        worker_image = Journal(Path(root) / "worker-pools-current.json").data["manifest"][
            "configuration"
        ]["worker_image"]
    require_native_compatible_release(root, app_image, worker_image)


def next_step(snapshot, build):
    """Choose one bounded step; durable coordinator CAS remains the mutation authority."""
    rows = snapshot["observed_members"]
    if (
        not snapshot["fresh"]
        or any(row["status"] not in RUNNING | TERMINAL for row in rows)
        or sum(row["status"] != "DELETED" for row in rows) > 2
        or any(m["grant"] and not m["reconciled"] for m in snapshot["members"])
    ):
        return "wait", None
    running = {row["instance_id"] for row in rows if row["status"] in RUNNING}
    members = [m for m in snapshot["members"] if m["instance_id"] in running]
    candidates = [m for m in members if m["worker_build"] == build and m["warm"]]
    old = [m for m in members if m["worker_build"] != build]
    if snapshot["active_build"] != build:
        if snapshot["staged_build"] != build:
            raise ValueError("unexpected staged release")
        if candidates:
            return "promote", None
        if len(old) == 2 and sum(m["serving"] for m in old) >= 1:
            # Prefer continuing the already drained member; never drain its survivor.
            eligible = [m for m in old if any(other["serving"] and other != m for other in old)]
            if eligible:
                return "retire", next((m for m in eligible if m["draining"]), eligible[0])
        return "wait", None
    if old:
        if any(m["serving"] for m in candidates) or (
            snapshot["claims_paused"]
            and snapshot["local_claims_paused"]
            and snapshot["live_attempts"] == 0
            and candidates
        ):
            return "retire", old[0]
        return "wait", None
    if candidates:
        return "verified", None
    return "wait", None


def verify_image(image, build, *, run=subprocess.run):
    result = run(
        ["docker", "image", "inspect", image],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    rows = json.loads(result.stdout)
    if (
        len(rows) != 1
        or rows[0].get("Config", {}).get("Labels", {}).get("org.opencontainers.image.revision")
        != build
    ):
        raise ValueError("actual image revision mismatch")
    if "@sha256:" not in image or image not in rows[0].get("RepoDigests", []):
        raise ValueError("actual image digest mismatch")
    return rows[0]["Id"]


class Journal:
    def __init__(self, path, data=None):
        self.path = Path(path)
        self.data = data if data is not None else json.loads(self.path.read_text())
        if data is not None:
            self.save()

    def save(self):
        fd, name = tempfile.mkstemp(prefix=".fleet-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.data, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def provider_field_matches(field, actual, reviewed):
    """Compare only observed protobuf omissions and the numeric workload target."""
    if actual == reviewed:
        return True
    if not isinstance(actual, dict) or not isinstance(reviewed, dict):
        return False
    actual = deepcopy(actual)
    if field == "deployPolicy" and reviewed.get("maxExpansion") == "0":
        actual.setdefault("maxExpansion", "0")
    elif field == "instanceTemplate":
        disk = actual.get("bootDiskSpec", {}).get("diskSpec")
        reviewed_disk = reviewed.get("bootDiskSpec", {}).get("diskSpec", {})
        if isinstance(disk, dict) and reviewed_disk.get("preserveAfterInstanceDelete") is False:
            disk.setdefault("preserveAfterInstanceDelete", False)
        if (
            reviewed.get("schedulingPolicy") == {"preemptible": False}
            and actual.get("schedulingPolicy") == {}
        ):
            actual["schedulingPolicy"] = {"preemptible": False}
    elif field == "scalePolicy":
        autoscale = actual.get("autoScale")
        reviewed_autoscale = reviewed.get("autoScale", {})
        if isinstance(autoscale, dict):
            if reviewed_autoscale.get("minZoneSize") == "0":
                autoscale.setdefault("minZoneSize", "0")
            rules = autoscale.get("customRules")
            reviewed_rules = reviewed_autoscale.get("customRules")
            if isinstance(rules, list) and isinstance(reviewed_rules, list):
                for rule, reviewed_rule in zip(rules, reviewed_rules, strict=False):
                    if (
                        isinstance(rule, dict)
                        and isinstance(reviewed_rule, dict)
                        and reviewed_rule.get("target") == "1"
                        and type(rule.get("target")) in {int, float}
                        and rule["target"] == 1
                    ):
                        rule["target"] = "1"
    return actual == reviewed


def update_group(group, body, *, cloud, journal):
    """Write ahead of one cloud submission. Lost responses are inspected, never retried."""
    desired = {"group": group, "body": body}
    pending = journal.data.get("pending")
    if pending and pending != desired:
        raise ValueError("another cloud submission requires reconciliation")
    current = cloud.get(f"instanceGroups/{group}", view="FULL")
    if all(provider_field_matches(key, current.get(key), value) for key, value in body.items()):
        journal.data["pending"] = None
        journal.save()
        return
    if pending:
        raise ValueError("cloud submission uncertain; inspect operation before continuing")
    journal.data["pending"] = desired
    journal.save()
    operation = cloud.mutate(
        "PATCH", f"instanceGroups/{group}", body | {"updateMask": ",".join(body)}
    )
    journal.data["operation_id"] = operation["id"]
    journal.save()
    # Desired configuration is re-read by the next poll, independently of operation response.


def commit_release(journal, marker):
    if journal.data["phase"] != "verified" or set(journal.data["verified"]) != {"bulk", "selfie"}:
        raise ValueError("both pools must be verified before release commit")
    Journal(marker, journal.data["candidate"])
    journal.data["phase"] = "committed"
    journal.save()


def verify_pool(name, snapshot, group, candidate):
    if not provider_field_matches(
        "instanceTemplate", group["instanceTemplate"], candidate["groups"][name]["instanceTemplate"]
    ):
        raise ValueError("future launch template mismatch")
    build = candidate["configuration"]["worker_build"]
    rows = snapshot["observed_members"]
    if (
        not snapshot["fresh"]
        or snapshot["active_build"] != build
        or snapshot["staged_build"] is not None
        or any(row["status"] not in RUNNING | TERMINAL for row in rows)
        or any(row["worker_build"] != build for row in rows if row["status"] in RUNNING)
        or any(m["grant"] and not m["reconciled"] for m in snapshot["members"])
    ):
        raise ValueError("pool not verified")
    running = [row for row in rows if row["status"] in RUNNING]
    if not running and name == "bulk":
        return True
    if snapshot["claims_paused"] or not any(m["serving"] for m in snapshot["members"]):
        raise ValueError("no active serving capacity")
    return True


def retire(gateway, name, snapshot, member):
    gateway.control("recover", pool=name)
    identity = {key: member[key] for key in ("instance_id", "boot_id", "worker_build")}
    gateway.control(
        "retire",
        identity={"pool": name, **identity},
        active_build=snapshot["active_build"],
        staged_build=snapshot["staged_build"],
    )


def transition(gateway, name, manifest, *, timeout=1800, pause=5):
    build = manifest["configuration"]["worker_build"]
    capped = manifest["configuration"]["pool_max_size"] == 1
    snapshot = gateway.observe()[name]
    if snapshot["active_build"] != build:
        gateway.control(
            "stage", pool=name, active_build=snapshot["active_build"], staged_build=build
        )
    # Re-entry after promotion must not allocate another replacement for the retired VM.
    floor = 1 if snapshot["active_build"] == build and (capped or snapshot["claims_paused"]) else 2
    gateway.template(name, manifest, floor)
    deadline = time.monotonic() + timeout
    while True:
        snapshot = gateway.observe()[name]
        if capped and snapshot["active_build"] == build and floor != 1:
            # Bound replenishment before granting retirement, then refresh survivor evidence.
            gateway.template(name, manifest, 1)
            floor = 1
            snapshot = gateway.observe()[name]
        step, member = next_step(snapshot, build)
        if (
            name == "bulk"
            and snapshot["fresh"]
            and snapshot["active_build"] == build
            and snapshot["staged_build"] is None
            and not snapshot["claims_paused"]
            and snapshot.get("target_size") == 0
            and all(row["status"] == "DELETED" for row in snapshot["observed_members"])
            and all(not m["grant"] or m["reconciled"] for m in snapshot["members"])
        ):
            return
        if step == "verified":
            return
        if step == "retire":
            if capped:
                gateway.disk_fence(manifest)
            retire(gateway, name, snapshot, member)
        elif step == "promote":
            gateway.control(
                "promote", pool=name, active_build=snapshot["active_build"], staged_build=build
            )
        if time.monotonic() >= deadline:
            raise ValueError("fleet transition timeout")
        time.sleep(pause)


def cancel(gateway, name, previous, *, timeout=900, pause=5):
    capped = previous["configuration"]["pool_max_size"] == 1
    gateway.template(name, previous, 1 if capped else 2)
    deadline = time.monotonic() + timeout
    while True:
        snapshot = gateway.observe()[name]
        if snapshot["staged_build"] is None:
            return
        candidates = [
            m
            for m in snapshot["members"]
            if m["worker_build"] == snapshot["staged_build"]
            and any(
                row["instance_id"] == m["instance_id"] and row["status"] in RUNNING
                for row in snapshot["observed_members"]
            )
        ]
        if candidates:
            if capped:
                gateway.disk_fence(previous)
            retire(gateway, name, snapshot, candidates[0])
        elif snapshot["fresh"] and not any(
            m["grant"] and not m["reconciled"] for m in snapshot["members"]
        ):
            gateway.control(
                "cancel",
                pool=name,
                active_build=snapshot["active_build"],
                staged_build=snapshot["staged_build"],
            )
            return
        if time.monotonic() >= deadline:
            raise ValueError("staged cancellation timeout")
        time.sleep(pause)


def provision_module():
    spec = importlib.util.spec_from_file_location(
        "canonical_pool_provision", Path(__file__).with_name("provision.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_instance_id():
    from processing.services.worker_pool_cloud import identifier, request_bytes

    raw = request_bytes(
        Request(
            "http://169.254.169.254/computeMetadata/v1/instance/id",
            headers={"Metadata-Flavor": "Google"},
        ),
        max_body=128,
    )
    return identifier(raw.decode().strip())


def validate_manifest(manifest):
    provision = provision_module()
    unsigned = {key: value for key, value in manifest.items() if key != "checksum"}
    if manifest.get("checksum") != provision.digest(unsigned):
        raise ValueError("reviewed manifest checksum mismatch")
    provision.validate(manifest["configuration"])
    if any(entry["id"] is None for entry in manifest["configuration"]["groups"].values()):
        raise ValueError("release requires existing exact group IDs")
    # Prior artifacts preserve their original bootstrap; no legacy image may be invented.
    expected = provision.prepare(manifest["configuration"])["groups"]
    if set(manifest["groups"]) != {"bulk", "selfie"}:
        raise ValueError("unsafe fleet manifest")
    for name, group in manifest["groups"].items():
        if (
            name not in {"bulk", "selfie"}
            or group["deployPolicy"]["strategy"] != "OPPORTUNISTIC"
            or group["deployPolicy"]["maxExpansion"] != "0"
            or group["scalePolicy"] != expected[name]["scalePolicy"]
            or group["deployPolicy"] != expected[name]["deployPolicy"]
        ):
            raise ValueError("unsafe fleet manifest")


class Host:
    def __init__(self, root, cloud, journal):
        self.root, self.cloud, self.journal = Path(root), cloud, journal
        self.run = subprocess.run
        self.compose = [
            "docker",
            "compose",
            "--project-name",
            "photo-prjct",
            "--env-file",
            str(self.root / ".env"),
            "-f",
            str(self.root / "docker-compose.deployment.yml"),
            "-f",
            str(self.root / "docker-compose.https.yml"),
        ]

    def command(self, command, *, payload=None, timeout=950):
        result = self.run(
            self.compose + ["exec", "-T", "web", "python", "manage.py", *command],
            input=json.dumps(payload) if payload is not None else None,
            text=True,
            capture_output=True,
            check=True,
            timeout=timeout,
        )
        return json.loads(result.stdout)

    def control(self, operation, **args):
        return self.command(["control_worker_pools"], payload={"operation": operation, **args})

    def verify_web(self, proof):
        container = self.run(
            self.compose + ["ps", "-q", "web"],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        ).stdout.strip()
        result = self.run(
            ["docker", "inspect", container], check=True, text=True, capture_output=True, timeout=30
        )
        rows = json.loads(result.stdout)
        if len(rows) != 1 or rows[0]["Image"] != proof["web_id"] or not rows[0]["State"]["Running"]:
            raise ValueError("running web image differs from verified candidate")

    def observe(self):
        config = json.loads((self.root / "worker-pools-observation.json").read_text())
        self.command(
            ["observe_worker_pool_cloud", "--config", "-", "--record"], payload=config, timeout=85
        )
        # Successful queue publication alone advances queue freshness in the existing service.
        self.command(
            [
                "publish_worker_pool_metrics",
                "--publish",
                "--zone",
                config["zone"],
                "--folder-id",
                config["canonical_folder_id"],
            ],
            timeout=35,
        )
        return self.control("status")

    def template(self, name, manifest, floor):
        self.reconcile_pending()
        group = manifest["groups"][name]
        body = {
            key: deepcopy(group[key]) for key in ("instanceTemplate", "scalePolicy", "deployPolicy")
        }
        body["scalePolicy"]["autoScale"]["minZoneSize"] = str(floor)
        body["scalePolicy"]["autoScale"]["maxSize"] = str(
            max(manifest["configuration"]["pool_max_size"], floor)
        )
        if manifest["configuration"]["pool_max_size"] == 1 and floor:
            self.disk_fence(manifest, expanding=name if floor == 2 else None)
            if floor == 2:
                self.journal.data["expanded_pool"] = name
                self.journal.save()
        group_id = manifest["configuration"]["groups"][name]["id"]
        deadline = time.monotonic() + 90
        while True:
            try:
                update_group(group_id, body, cloud=self.cloud, journal=self.journal)
                if self.journal.data.get("pending") is None:
                    return
            except ValueError:
                if not self.journal.data.get("pending"):
                    raise
            if time.monotonic() >= deadline:
                raise ValueError("cloud configuration submission uncertain")
            time.sleep(2)

    def reconcile_pending(self):
        pending = self.journal.data.get("pending")
        if pending:
            update_group(pending["group"], pending["body"], cloud=self.cloud, journal=self.journal)

    def disk_fence(self, manifest, *, expanding=None, settled=None):
        """Persist disk identities; only complete stable listings can prove their absence."""
        from processing.services.worker_pool_cloud import identifier

        if self.journal.data.get("pending"):
            raise ValueError("cloud submission uncertain; disk fence cannot proceed")
        config = manifest["configuration"]
        groups, members = {}, {}
        started = time.monotonic()
        for name, entry in config["groups"].items():
            group_id = identifier(entry["id"])
            group = self.cloud.get(f"instanceGroups/{group_id}", view="FULL")
            if (
                group.get("id") != group_id
                or group.get("folderId") != config["folder_id"]
                or group.get("labels") != manifest["groups"][name]["labels"]
                or group.get("allocationPolicy") != manifest["groups"][name]["allocationPolicy"]
                or group["instanceTemplate"]["bootDiskSpec"]["diskSpec"]["imageId"]
                != config["boot_image_id"]
                or not provider_field_matches(
                    "deployPolicy",
                    group.get("deployPolicy"),
                    manifest["groups"][name]["deployPolicy"],
                )
            ):
                raise ValueError("wrong worker group ownership")
            groups[name] = group
            members[name] = self.cloud.pages(f"instanceGroups/{group_id}/instances", "instances")

        def inventory(kind):
            rows = self.cloud.pages(kind, kind, folderId=config["folder_id"])
            indexed = {identifier(row.get("id")): row for row in rows}
            if len(indexed) != len(rows):
                raise ValueError("duplicate cloud inventory")
            return indexed

        instances = inventory("instances")
        disks = inventory("disks")
        known = self.journal.data.setdefault("worker_disks", {})
        current, identities, counts = {}, set(), {name: 0 for name in groups}
        transitional = False
        for name, rows in members.items():
            for row in rows:
                status = row.get("status")
                instance_id = row.get("instanceId")
                if status == "DELETED" and instance_id not in instances:
                    continue
                instance_id = identifier(instance_id)
                if instance_id in identities or row.get("zoneId") != config["zone"]:
                    raise ValueError("ambiguous worker membership")
                identities.add(instance_id)
                instance = instances.get(instance_id, {})
                if (
                    instance.get("folderId") != config["folder_id"]
                    or instance.get("zoneId") != config["zone"]
                ):
                    raise ValueError("missing or wrong worker instance")
                disk_id = identifier(instance.get("bootDisk", {}).get("diskId"))
                disk = disks.get(disk_id, {})
                if (
                    disk.get("folderId") != config["folder_id"]
                    or disk.get("zoneId") != config["zone"]
                    or disk.get("sourceImageId") != config["boot_image_id"]
                    or disk.get("instanceIds") != [instance_id]
                    or disk_id in current
                ):
                    raise ValueError("unverified worker boot disk")
                owner = {"pool": name, "instance_id": instance_id}
                if disk_id in known and known[disk_id] != owner:
                    raise ValueError("worker disk ownership changed")
                current[disk_id] = owner
                counts[name] += 1
                transitional |= (
                    status not in RUNNING
                    or instance.get("status") != "RUNNING"
                    or disk.get("status") != "READY"
                )
        # Save identified disks before any retirement, including an interrupted observation.
        known.update(current)
        self.journal.save()
        for name, group in groups.items():
            path = f"instanceGroups/{group['id']}"
            if (
                self.cloud.pages(path + "/instances", "instances") != members[name]
                or self.cloud.get(path, view="FULL") != group
            ):
                raise ValueError("worker membership changed during disk inventory")
        if inventory("disks") != disks or inventory("instances") != instances:
            raise ValueError("worker disk inventory changed during observation")
        if time.monotonic() - started > 60:
            raise ValueError("worker disk inventory stale")
        relevant = {
            identity
            for identity, disk in disks.items()
            if identity in known or disk.get("sourceImageId") == config["boot_image_id"]
        }
        if relevant - known.keys():
            raise ValueError("unexplained worker disk")
        if relevant - current.keys():
            raise ValueError("retained worker disk; complete listing must prove deletion")
        if len(relevant) > 3 or any(count > 2 for count in counts.values()):
            raise ValueError("worker disk allocation exceeds release bound")
        if transitional:
            raise ValueError("stopped or transitional worker allocation")
        if expanding:
            if self.journal.data.get("expanded_pool") not in {None, expanding}:
                raise ValueError("serial release must finish the expanded pool first")
            if not self.journal.data.get("expanded_pool") and counts[expanding] > 1:
                raise ValueError("unexplained worker expansion outside release journal")
            for name, group in groups.items():
                if name != expanding and (
                    counts[name] > 1 or group["scalePolicy"]["autoScale"]["maxSize"] != "1"
                ):
                    raise ValueError("serial release cannot expand both pools")
        if settled:
            if counts[settled] > 1 or groups[settled]["scalePolicy"]["autoScale"]["maxSize"] != "1":
                raise ValueError("pool disk retirement has not settled")
            if self.journal.data.get("expanded_pool") == settled:
                self.journal.data["expanded_pool"] = None
                self.journal.save()


def image_proof(manifest, app_image, *, run=subprocess.run):
    config = manifest["configuration"]
    build = config["worker_build"]
    if re.fullmatch(r"[0-9a-f]{40}", build) is None or app_image.rsplit(":", 1)[-1] != build:
        raise ValueError("web and worker must share candidate SHA")
    inspected = run(
        ["docker", "image", "inspect", app_image],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
    )
    rows = json.loads(inspected.stdout)
    repository = app_image.rsplit(":", 1)[0]
    digests = [
        ref for ref in rows[0].get("RepoDigests", []) if ref.startswith(repository + "@sha256:")
    ]
    if len(digests) != 1:
        raise ValueError("unverified web digest")
    web_id = verify_image(digests[0], build, run=run)
    run(["docker", "pull", config["worker_image"]], check=True, capture_output=True, timeout=600)
    worker_id = verify_image(config["worker_image"], build, run=run)
    return {"web_image": digests[0], "web_id": web_id, "worker_id": worker_id}


def observation_config(candidate, previous):
    config = candidate["configuration"]
    releases = {config["worker_build"]: config["worker_image"]}
    if previous:
        old = previous["manifest"]["configuration"]
        if old["pool_max_size"] != config["pool_max_size"]:
            raise ValueError("cross-ceiling release requires separately reviewed activation")
        if any(
            old[key] != config[key]
            for key in ("folder_id", "canonical_folder_id", "zone", "boot_image_id")
        ):
            raise ValueError("release scope changed")
        if {name: row["id"] for name, row in old["groups"].items()} != {
            name: row["id"] for name, row in config["groups"].items()
        }:
            raise ValueError("release group identity changed")
        releases[old["worker_build"]] = old["worker_image"]
    return {
        "folder_id": config["folder_id"],
        "canonical_folder_id": config["canonical_folder_id"],
        "zone": config["zone"],
        "boot_image_id": config["boot_image_id"],
        "groups": {name: row["id"] for name, row in config["groups"].items()},
        "releases": releases,
    }


def verify_fleet(host, manifest):
    snapshot = host.observe()
    for name in ("bulk", "selfie"):
        group_id = manifest["configuration"]["groups"][name]["id"]
        group = host.cloud.get(f"instanceGroups/{group_id}", view="FULL")
        if not provider_field_matches(
            "scalePolicy", group["scalePolicy"], manifest["groups"][name]["scalePolicy"]
        ):
            raise ValueError("steady pool scale policy mismatch")
        verify_pool(name, snapshot[name], group, manifest)
    if manifest["configuration"]["pool_max_size"] == 1:
        for name in ("bulk", "selfie"):
            host.disk_fence(manifest, settled=name)


def settle_remote_pool(host, name, manifest):
    snapshot = host.observe()[name]
    if not snapshot["local_claims_paused"]:
        raise ValueError("local claims must remain paused")
    if snapshot["claims_paused"]:
        if snapshot["live_attempts"] != 0:
            raise ValueError("paused remote repair still has live attempts")
        host.control("pause", pool=name, paused=False, local=False)
    host.template(name, manifest, 0 if name == "bulk" else 1)
    if manifest["configuration"]["pool_max_size"] == 1:
        host.disk_fence(manifest, settled=name)


def execute(mode, root, manifest_path, checksum, app_image, worker_image=None):
    root = Path(root)
    receipt = root / "worker-pools-release.json"
    marker = root / "worker-pools-current.json"
    if mode not in {"preflight", "rollout", "rollback", "status", "verify", "commit"}:
        raise ValueError("unsupported remote release operation")
    if mode == "preflight":
        print("FLEET_PREFLIGHT_STEP=guard", flush=True)
        deployment_guard(root, app_image, worker_image, manifest_path)
    elif mode in {"rollout", "rollback"}:
        data = Journal(receipt).data
        target = data.get("previous") if mode == "rollback" else data["candidate"]
        if not target:
            raise ValueError("committed remote predecessor is required")
        require_native_compatible_release(
            root,
            target.get("proof", {}).get("web_image"),
            target["manifest"]["configuration"]["worker_image"],
        )
    provision = provision_module()
    from processing.services.worker_pool_cloud import metadata_token

    if mode == "preflight":
        print("FLEET_PREFLIGHT_STEP=manifest", flush=True)
        manifest = json.loads(Path(manifest_path).read_text())
        print("FLEET_PREFLIGHT_STEP=manifest-validation", flush=True)
        validate_manifest(manifest)
        print("FLEET_PREFLIGHT_STEP=manifest-checksum", flush=True)
        if checksum != manifest["checksum"]:
            raise ValueError("candidate differs from reviewed package")
        print("FLEET_PREFLIGHT_STEP=manifest-rebuild", flush=True)
        if provision.prepare(manifest["configuration"]) != manifest:
            raise ValueError("candidate differs from reviewed package")
        print("FLEET_PREFLIGHT_STEP=previous", flush=True)
        previous = json.loads(marker.read_text())
        validate_manifest(previous["manifest"])
        verify_image(
            previous["manifest"]["configuration"]["worker_image"],
            previous["manifest"]["configuration"]["worker_build"],
        )
        print("FLEET_PREFLIGHT_STEP=observation", flush=True)
        config = observation_config(manifest, previous)
        cloud = provision.Cloud(metadata_token())
        print("FLEET_PREFLIGHT_STEP=cloud", flush=True)
        provision.inspect(manifest["configuration"], cloud)
        print("FLEET_PREFLIGHT_STEP=image", flush=True)
        proof = image_proof(manifest, app_image)
        print("FLEET_PREFLIGHT_STEP=receipt", flush=True)
        Journal(
            receipt,
            {
                "phase": "prepared",
                "pending": None,
                "verified": [],
                "previous": previous,
                "candidate": {"manifest": manifest, "proof": proof},
            },
        )
        Journal(root / "worker-pools-observation.json", config)
        return
    journal = Journal(receipt)
    if mode == "rollback" and journal.data["phase"] == "prepared":
        journal.data["phase"] = "rolled-back"
        journal.save()
        return
    if mode in {"rollout", "rollback"}:
        observation_config(journal.data["candidate"]["manifest"], journal.data["previous"])
    cloud = provision.Cloud(metadata_token()) if mode != "status" else None
    host = Host(root, cloud, journal)
    candidate = journal.data["candidate"]
    manifest = candidate["manifest"]
    if mode == "status":
        print(
            json.dumps(
                {
                    "phase": journal.data["phase"],
                    "pending": journal.data.get("pending") is not None,
                    "placement": "remote",
                    "pools": host.control("status"),
                },
                sort_keys=True,
            )
        )
        return
    host.verify_web(candidate["proof"])
    capped = manifest["configuration"]["pool_max_size"] == 1
    order = ["bulk", "selfie"]
    if capped and mode in {"rollout", "rollback"}:
        host.reconcile_pending()
        expanded = journal.data.get("expanded_pool")
        if expanded:
            if expanded not in order:
                raise ValueError("invalid expanded pool receipt")
            order.remove(expanded)
            order.insert(0, expanded)
    if mode == "rollout":
        existing = host.control("status")
        for name in order:
            if (
                name not in existing
                or existing[name]["group_id"] != manifest["configuration"]["groups"][name]["id"]
            ):
                raise ValueError("existing remote coordinator identity differs from candidate")
            if not existing[name]["local_claims_paused"]:
                raise ValueError("local claims must remain paused")
        journal.data["phase"] = "rolling"
        journal.save()
        for name in order:
            transition(host, name, manifest)
            settle_remote_pool(host, name, manifest)
        verify_fleet(host, manifest)
        journal.data.update(phase="verified", verified=["bulk", "selfie"])
        journal.save()
    elif mode == "rollback":
        previous = journal.data["previous"]
        old = previous["manifest"]
        for name in order:
            snapshot = host.observe()[name]
            if snapshot["active_build"] == old["configuration"]["worker_build"]:
                if snapshot["staged_build"]:
                    cancel(host, name, old)
            else:
                transition(host, name, old)
            settle_remote_pool(host, name, old)
        verify_fleet(host, old)
        Journal(marker, previous)
        journal.data["phase"] = "rolled-back"
        journal.save()
    elif mode in {"verify", "commit"}:
        verify_fleet(host, manifest)
        if mode == "commit":
            commit_release(journal, marker)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=(
            "preflight",
            "rollout",
            "status",
            "verify",
            "commit",
            "rollback",
            "deployment-guard",
        ),
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--checksum")
    parser.add_argument("--app-image")
    parser.add_argument("--worker-image")
    args = parser.parse_args()
    try:
        if args.mode == "deployment-guard":
            deployment_guard(args.root, args.app_image, args.worker_image, args.manifest)
            return 0
        if args.mode == "commit" and os.environ.get("FINDME_CANONICAL_DEPLOY") != "1":
            raise ValueError("activation requires canonical Deploy health gates")
        # Deploy holds fd9 over package installation, app reconciliation and every fleet phase.
        # A direct invocation takes exactly that same lock; there is no second writer authority.
        if args.mode == "preflight":
            print("FLEET_PREFLIGHT_STEP=lock", flush=True)
        if os.environ.get("FINDME_CANONICAL_LOCK") == "1":
            lock_fd = 9
            if os.fstat(lock_fd).st_ino != (args.root / ".deployment.lock").stat().st_ino:
                raise ValueError("wrong canonical deployment lock")
        else:
            lock_fd = os.open(args.root / ".deployment.lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        execute(
            args.mode, args.root, args.manifest, args.checksum, args.app_image, args.worker_image
        )
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print(
            "canonical worker release failed; inspect durable receipt and retain compatible web",
            file=sys.stderr,
        )
        return 1
    if args.mode != "status":
        print("canonical worker release phase complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
