import ast
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tarfile
from copy import deepcopy
from dataclasses import replace
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.operational
ROOT = Path(__file__).resolve().parents[2]


def test_remote_install_trap_retains_compatible_package_after_failed_fleet_recovery(tmp_path):
    source = (ROOT / "deploy/run-remote.sh").read_text()
    program = source.split("REMOTE_PROGRAM=$(cat <<'PY'\n", 1)[1].split("\nPY\n)", 1)[0]
    tree = ast.parse(program)
    command = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "deployment_command"
            for target in node.targets
        )
    )
    command = command.replace(
        "deployment_root=/opt/photo-prjct", "deployment_root=" + shlex.quote(str(tmp_path))
    )
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/version").write_text("previous")
    (tmp_path / "docker-compose.deployment.yml").write_text("previous")
    (tmp_path / "docker-compose.https.yml").write_text("previous")
    (tmp_path / ".env").write_text("previous-env")
    candidate = tmp_path / "candidate"
    (candidate / "deploy").mkdir(parents=True)
    (candidate / "deploy/version").write_text("compatible-candidate")
    (candidate / "deploy/apply-deployment.sh").write_text(
        'mkdir -m 700 "$DEPLOY_ROOT/.deployment-recovery"\n'
        'cp "$DEPLOY_ROOT/.env" "$DEPLOY_ROOT/.deployment-recovery/previous.env"\nexit 1\n'
    )
    for name in ("docker-compose.deployment.yml", "docker-compose.https.yml"):
        (candidate / name).write_text("candidate")
    with tarfile.open(tmp_path / ".deployment-candidate.fixture.tar", "w") as archive:
        for path in candidate.iterdir():
            archive.add(path, arcname=path.name)
    binary = tmp_path / "bin"
    binary.mkdir()
    for name, body in {
        "docker": "printf 'worker-bulk\\nworker-selfie\\n'",
        "flock": "exit 0",
    }.items():
        path = binary / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    result = subprocess.run(
        ["sh", "-c", command],
        env={
            "PATH": str(binary) + ":" + os.environ["PATH"],
            "DEPLOYMENT_ARCHIVE_NAME": ".deployment-candidate.fixture.tar",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stderr
    assert (tmp_path / "deploy/version").read_text() == "compatible-candidate"
    backups = list(tmp_path.glob(".deployment-previous.*"))
    assert len(backups) == 1
    assert (backups[0] / "deploy/version").read_text() == "previous"


def release_module():
    path = ROOT / "deploy/worker-pools/release.py"
    assert path.exists(), "canonical fleet release controller is missing"
    spec = importlib.util.spec_from_file_location("fleet_release", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.django_db
def test_fresh_execute_initializes_both_pools_before_real_all_pool_observation(
    tmp_path, monkeypatch, settings
):
    from django.core.management import call_command
    from processing.models import WorkerPool
    from processing.services import worker_pool_lifecycle as lifecycle

    release = release_module()
    settings.PHOTO_WORKER_POOL_COORDINATOR_ENABLED = True
    settings.PHOTO_PROCESSING_ENABLED = True
    settings.PHOTO_PROCESSING_WORKER_TOKEN = "fixture-local-only"
    settings.PHOTO_PROCESSING_FLEET_TOKEN = "fixture-fleet-only"
    build = "a" * 40
    image = "ghcr.io/example/photo-prjct-worker@sha256:" + "b" * 64
    template = {
        "metadata": {"findme-worker-build": build, "findme-worker-image": image},
        "bootDiskSpec": {"diskSpec": {"imageId": "boot-image"}},
    }
    groups = {
        name: {
            "id": name + "-group",
            "folderId": "folder",
            "allocationPolicy": {"zones": [{"zoneId": "ru-central1-a"}]},
            "instanceTemplate": template,
            "managedInstancesState": {"targetSize": "1"},
            "scalePolicy": {
                "autoScale": {"maxSize": "2", "minZoneSize": "0" if name == "bulk" else "1"}
            },
        }
        for name in ("bulk", "selfie")
    }
    manifest = {
        "configuration": {
            "pool_max_size": 2,
            "worker_build": build,
            "groups": {name: {"id": name + "-group"} for name in groups},
        },
        "groups": groups,
    }
    config = {
        "folder_id": "folder",
        "canonical_folder_id": "canonical-folder",
        "zone": "ru-central1-a",
        "boot_image_id": "boot-image",
        "groups": {name: name + "-group" for name in groups},
        "releases": {build: image},
    }
    release.Journal(
        tmp_path / "worker-pools-release.json",
        {
            "phase": "prepared",
            "pending": None,
            "previous": None,
            "verified": [],
            "candidate": {"manifest": manifest, "proof": {}},
        },
    )
    release.Journal(tmp_path / "worker-pools-observation.json", config)

    class Cloud:
        def get(self, path, **kwargs):
            kind, identity = path.split("/")
            name = identity.split("-")[0]
            if kind == "instanceGroups":
                return groups[name]
            if kind == "instances":
                return {
                    "id": identity,
                    "folderId": "folder",
                    "zoneId": "ru-central1-a",
                    "metadata": template["metadata"],
                    "bootDisk": {"diskId": name + "-disk"},
                    "networkInterfaces": [{"primaryV4Address": {"address": "10.0.0.4"}}],
                }
            assert kind == "disks"
            return {"id": identity, "folderId": "folder", "sourceImageId": "boot-image"}

        def pages(self, path, key):
            name = path.split("/")[1].split("-")[0]
            return [
                {
                    "instanceId": name + "-node",
                    "status": "RUNNING_ACTUAL",
                    "zoneId": "ru-central1-a",
                }
            ]

    cloud = Cloud()
    monkeypatch.setattr(
        release, "provision_module", lambda: SimpleNamespace(Cloud=lambda token: cloud)
    )
    monkeypatch.setattr("processing.services.worker_pool_cloud.metadata_token", lambda: "fixture")
    monkeypatch.setattr(
        "processing.services.worker_pool_observation.metadata_token", lambda: "fixture"
    )
    monkeypatch.setattr(
        "processing.services.worker_pool_observation.CloudReader", lambda token: cloud
    )
    monkeypatch.setattr("processing.services.worker_pool_metrics.write_metrics", lambda *args: None)
    events = []

    class Host(release.Host):
        def verify_web(self, proof):
            events.append("verified-web")

        def command(self, command, *, payload=None, timeout=950):
            output = StringIO()
            with patch("sys.stdin", StringIO(json.dumps(payload))):
                call_command(*command, stdout=output)
            return json.loads(output.getvalue())

        def observe(self):
            snapshot = super().observe()  # Actual Host, management commands, reader and DB.
            for name, pool in snapshot.items():
                if pool["members"]:
                    continue
                assert pool["claims_paused"] and not pool["local_claims_paused"]
                identity = lifecycle.MemberIdentity(name, name + "-node", uuid4(), build)
                registration = lifecycle.register(identity)
                identity = replace(
                    identity, registration_generation=UUID(registration["registration_generation"])
                )
                lifecycle.heartbeat(identity, ready=True, draining=False)
            return self.control("status")

        def template(self, name, desired, floor):
            events.append(("template", name, floor))

        def stop_local(self):
            snapshot = self.control("status")
            assert all(
                row["claims_paused"] and row["local_claims_paused"] and not row["live_attempts"]
                for row in snapshot.values()
            )
            events.append("stop-local")

    monkeypatch.setattr(release, "Host", Host)
    assert not WorkerPool.objects.exists()
    release.execute("rollout", tmp_path, None, None, None)
    assert events[0] == "verified-web"
    assert "stop-local" in events
    assert list(
        WorkerPool.objects.order_by("name").values_list(
            "name", "claims_paused", "local_claims_paused"
        )
    ) == [
        ("bulk", False, True),
        ("selfie", False, True),
    ]
    receipt = release.Journal(tmp_path / "worker-pools-release.json").data
    assert receipt["phase"] == "verified" and receipt["verified"] == ["bulk", "selfie"]


def state(*builds, staged=None):
    return {
        "active_build": "a" * 40,
        "staged_build": staged,
        "fresh": True,
        "claims_paused": False,
        "local_claims_paused": True,
        "live_attempts": 0,
        "observed_members": [
            {"instance_id": f"node-{i}", "status": "RUNNING_ACTUAL", "worker_build": build}
            for i, build in enumerate(builds)
        ],
        "members": [
            {
                "instance_id": f"node-{i}",
                "boot_id": f"boot-{i}",
                "worker_build": build,
                "warm": True,
                "serving": build == "a" * 40,
                "grant": None,
                "draining": False,
                "reconciled": False,
            }
            for i, build in enumerate(builds)
        ],
    }


def test_one_node_warms_spare_but_two_node_release_retires_only_one_before_promotion():
    release = release_module()
    build = "b" * 40
    one = state("a" * 40, staged=build)
    assert release.next_step(one, build) == ("wait", None)
    two = state("a" * 40, "a" * 40, staged=build)
    assert release.next_step(two, build) == ("retire", two["members"][0])
    warming = state("a" * 40, build, staged=build)
    warming["members"][1]["warm"] = False
    assert release.next_step(warming, build) == ("wait", None)
    warming["members"][1]["warm"] = True
    assert release.next_step(warming, build) == ("promote", None)


def test_two_node_transition_does_not_choose_the_only_serving_node():
    release = release_module()
    snapshot = state("a" * 40, "a" * 40, staged="b" * 40)
    snapshot["members"][1]["serving"] = False
    assert release.next_step(snapshot, "b" * 40) == ("retire", snapshot["members"][1])


def test_after_promotion_old_node_needs_fresh_active_survivor_and_reconciliation():
    release = release_module()
    build = "b" * 40
    snapshot = state("a" * 40, build)
    snapshot["active_build"] = build
    snapshot["members"][1]["serving"] = False
    assert release.next_step(snapshot, build) == ("wait", None)
    snapshot["members"][1]["serving"] = True
    assert release.next_step(snapshot, build)[0] == "retire"
    snapshot["members"][0]["grant"] = "grant"
    assert release.next_step(snapshot, build) == ("wait", None)
    snapshot["observed_members"][0]["status"] = "STOPPED"
    snapshot["members"][0]["reconciled"] = True
    assert release.next_step(snapshot, build) == ("verified", None)


@pytest.mark.parametrize("failure", ["stale", "pending", "unknown", "over-capacity"])
def test_partial_or_stale_cloud_state_never_grants_or_promotes(failure):
    release = release_module()
    snapshot = state("a" * 40, "b" * 40, staged="b" * 40)
    if failure == "stale":
        snapshot["fresh"] = False
    elif failure == "pending":
        snapshot["observed_members"][0]["status"] = "CREATING_INSTANCE"
    elif failure == "unknown":
        snapshot["observed_members"][0]["status"] = "UNKNOWN"
    else:
        snapshot["observed_members"].append(snapshot["observed_members"][0])
    assert release.next_step(snapshot, "b" * 40) == ("wait", None)


def test_actual_image_requires_digest_and_oci_revision_not_tag():
    release = release_module()
    image = "ghcr.io/example/photo-prjct-worker@sha256:" + "a" * 64
    run = Mock(
        return_value=Mock(
            stdout='[{"Id":"sha256:local", "RepoDigests":["'
            + image
            + '"],"Config":{"Labels":{"org.opencontainers.image.revision":"'
            + "b" * 40
            + '"}}}]'
        )
    )
    with pytest.raises(ValueError, match="revision"):
        release.verify_image(image, "a" * 40, run=run)


def test_workflow_builds_candidate_revision_even_for_unchanged_worker_sources():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    steps = workflow["jobs"]["build"]["steps"]
    worker = next(
        step for step in steps if step.get("with", {}).get("file") == "./Dockerfile.worker"
    )
    assert "if" not in worker
    assert (
        worker["with"]["build-args"]
        == "RELEASE_SHA=${{ needs.classify-release.outputs.release_sha }}"
    )
    assert not any("imagetools create" in step.get("run", "") for step in steps)


def test_local_drain_timeout_never_stops_any_container_or_unpauses_remote():
    release = release_module()
    events = []
    gateway = Mock()

    def control(operation, **args):
        events.append((operation, args))
        if operation == "drain-local" and args["pool"] == "selfie":
            raise ValueError("drain timeout")
        return {"drained": True}

    gateway.control.side_effect = control
    with pytest.raises(ValueError, match="drain timeout"):
        release.cutover(gateway)
    gateway.stop_local.assert_not_called()
    assert not any(op == "pause" and not args["paused"] for op, args in events)


def test_cutover_stops_exact_locals_only_after_both_authoritative_drains():
    release = release_module()
    events = []
    gateway = Mock()
    gateway.control.side_effect = lambda op, **args: events.append((op, args)) or {"drained": True}
    gateway.stop_local.side_effect = lambda: events.append(("stop", {}))
    release.cutover(gateway)
    assert [event[0] for event in events] == [
        "pause",
        "pause",
        "drain-local",
        "drain-local",
        "stop",
        "pause",
        "pause",
    ]
    assert all(event[1]["local"] for event in events[:2])
    assert all(not event[1]["local"] for event in events[-2:])


def test_uncertain_cloud_submission_reconciles_without_resubmitting(tmp_path):
    release = release_module()
    cloud = Mock()
    cloud.get.return_value = {"instanceTemplate": {"metadata": {"build": "old"}}}
    cloud.mutate.side_effect = TimeoutError("lost response")
    journal = release.Journal(tmp_path / "receipt.json", {"pending": None})
    body = {"instanceTemplate": {"metadata": {"build": "new"}}}
    with pytest.raises(TimeoutError):
        release.update_group("group", body, cloud=cloud, journal=journal)
    assert journal.data["pending"]["group"] == "group"
    cloud.get.return_value = body
    release.update_group("group", body, cloud=cloud, journal=journal)
    assert cloud.mutate.call_count == 1
    assert journal.data["pending"] is None


def test_partial_failure_cannot_commit_a_successful_release(tmp_path):
    release = release_module()
    journal = release.Journal(tmp_path / "receipt.json", {"phase": "rolling", "verified": ["bulk"]})
    with pytest.raises(ValueError):
        release.commit_release(journal, tmp_path / "current.json")
    assert not (tmp_path / "current.json").exists()


def test_prepared_rollback_does_not_require_cloud_or_running_candidate(tmp_path, monkeypatch):
    release = release_module()
    journal = release.Journal(
        tmp_path / "worker-pools-release.json",
        {
            "phase": "prepared",
            "pending": None,
            "candidate": {"manifest": {}},
        },
    )
    token = Mock(side_effect=OSError("metadata unavailable"))
    monkeypatch.setattr("processing.services.worker_pool_cloud.metadata_token", token)
    release.execute("rollback", tmp_path, None, None, None)
    assert release.Journal(journal.path).data["phase"] == "rolled-back"
    token.assert_not_called()


def test_terminal_receipt_with_uncertain_cloud_write_blocks_another_preflight(
    tmp_path, monkeypatch
):
    release = release_module()
    manifest = {"configuration": {}, "checksum": "reviewed"}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    release.Journal(
        tmp_path / "worker-pools-release.json",
        {
            "phase": "rolled-back-local",
            "pending": {"group": "bulk", "body": {}},
        },
    )
    provision = Mock()
    provision.prepare.return_value = manifest
    monkeypatch.setattr(release, "provision_module", lambda: provision)
    monkeypatch.setattr(release, "validate_manifest", lambda value: None)
    monkeypatch.setattr("processing.services.worker_pool_cloud.metadata_token", lambda: "fixture")
    with pytest.raises(ValueError, match="unfinished release"):
        release.execute("preflight", tmp_path, path, "reviewed", "unused")
    provision.inspect.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("uncertain", [False, True])
def test_initial_app_failure_after_fleet_commit_restores_absence_and_recovery_mode(
    tmp_path, monkeypatch, settings, uncertain
):
    from processing.management.commands.control_worker_pools import execute as control

    settings.PHOTO_WORKER_POOL_COORDINATOR_ENABLED = True
    release = release_module()
    manifest = {
        "checksum": "reviewed",
        "configuration": {
            "pool_max_size": 2,
            "folder_id": "folder",
            "canonical_folder_id": "canonical-folder",
            "zone": "ru-central1-a",
            "boot_image_id": "boot",
            "worker_build": "a" * 40,
            "worker_image": "ghcr.io/example/worker@sha256:" + "b" * 64,
            "groups": {name: {"id": name + "-group"} for name in ("bulk", "selfie")},
        },
    }
    for name in ("bulk", "selfie"):
        control(
            {
                "operation": "configure",
                "pool": name,
                "group_id": name + "-group",
                "active_build": "a" * 40,
            }
        )
        control({"operation": "pause", "pool": name, "local": True, "paused": True})
        control({"operation": "pause", "pool": name, "local": False, "paused": False})
    pending = {"group": "bulk-group", "body": {"scalePolicy": {}}} if uncertain else None
    receipt = tmp_path / "worker-pools-release.json"
    journal = release.Journal(
        receipt,
        {
            "phase": "verified",
            "previous": None,
            "pending": pending,
            "operation_id": "retained-operation",
            "verified": ["bulk", "selfie"],
            "candidate": {"manifest": manifest, "proof": {}},
        },
    )
    marker = tmp_path / "worker-pools-current.json"
    release.commit_release(journal, marker)
    assert marker.exists()
    host = Mock()
    host.control.side_effect = lambda operation, **args: control({"operation": operation, **args})
    monkeypatch.setattr(release, "Host", lambda *args: host)
    provision = Mock()
    provision.prepare.return_value = manifest
    monkeypatch.setattr(release, "provision_module", lambda: provision)
    monkeypatch.setattr("processing.services.worker_pool_cloud.metadata_token", lambda: "fixture")
    # Canonical apply has committed the fleet, but a later application commit step failed.
    release.execute("rollback", tmp_path, None, None, None)
    assert not marker.exists()
    restored = release.Journal(receipt).data
    assert restored["phase"] == "rolled-back-local"
    assert restored["pending"] == pending
    assert restored["operation_id"] == "retained-operation"
    assert restored["candidate"] == journal.data["candidate"]
    for pool in control({"operation": "status"}).values():
        assert pool["claims_paused"] and not pool["local_claims_paused"]
        assert pool["live_attempts"] == 0
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(release, "validate_manifest", lambda value: None)
    monkeypatch.setattr(release, "image_proof", lambda *args: {})
    if uncertain:
        with pytest.raises(ValueError, match="unfinished release"):
            release.execute("preflight", tmp_path, path, "reviewed", "app-image")
        assert release.Journal(receipt).data == restored
    else:
        release.execute("preflight", tmp_path, path, "reviewed", "app-image")
        assert release.Journal(receipt).data["previous"] is None


def test_initial_rollback_fences_both_pools_before_recovering_and_resuming_local(tmp_path):
    release = release_module()
    journal = release.Journal(
        tmp_path / "receipt.json",
        {"phase": "rolling", "previous": None, "pending": None, "verified": []},
    )
    gateway = Mock()
    events = []

    def control(op, **args):
        events.append((op, args))
        if op == "status":
            return {name: {"live_attempts": 0} for name in ("bulk", "selfie")}
        return {"ok": True}

    gateway.control.side_effect = control
    release.rollback_initial(gateway, journal, tmp_path / "worker-pools-current.json")
    assert events[:2] == [
        ("pause", {"pool": "bulk", "paused": True, "local": False}),
        ("pause", {"pool": "selfie", "paused": True, "local": False}),
    ]
    assert events[2:4] == [
        ("pause", {"pool": "bulk", "paused": True, "local": True}),
        ("pause", {"pool": "selfie", "paused": True, "local": True}),
    ]
    assert events[-2:] == [
        ("pause", {"pool": "bulk", "paused": False, "local": True}),
        ("pause", {"pool": "selfie", "paused": False, "local": True}),
    ]
    assert journal.data["phase"] == "rolled-back-local"


def test_initial_rollback_timeout_preserves_remote_fence_and_never_restores_local(tmp_path):
    release = release_module()
    journal = release.Journal(tmp_path / "receipt.json", {"previous": None})
    gateway = Mock()
    events = []

    def control(op, **args):
        events.append((op, args))
        if op == "status":
            return {name: {"live_attempts": 1} for name in ("bulk", "selfie")}
        return {"ok": True}

    gateway.control.side_effect = control
    with pytest.raises(ValueError, match="drain"):
        release.rollback_initial(
            gateway, journal, tmp_path / "worker-pools-current.json", timeout=0
        )
    assert not any(op == "pause" and not args["paused"] for op, args in events)


def test_zero_bulk_verification_checks_future_template_and_rejects_old_image():
    release = release_module()
    candidate = {
        "configuration": {"worker_build": "b" * 40},
        "groups": {"bulk": {"instanceTemplate": {"metadata": {"findme-worker-build": "b" * 40}}}},
    }
    snapshot = state()
    snapshot["active_build"] = "b" * 40
    assert release.verify_pool("bulk", snapshot, candidate["groups"]["bulk"], candidate)
    with pytest.raises(ValueError, match="template"):
        release.verify_pool("bulk", snapshot, {"instanceTemplate": {}}, candidate)


def test_initial_zero_bulk_requires_a_warm_acceptance_node_before_cutover():
    release = release_module()
    gateway = Mock()
    empty = state()
    empty["claims_paused"] = True
    gateway.observe.return_value = {"bulk": empty}
    manifest = {"configuration": {"worker_build": "a" * 40, "pool_max_size": 2}}
    with pytest.raises(ValueError, match="transition timeout"):
        release.transition(gateway, "bulk", manifest, timeout=0, pause=0)
    gateway.template.assert_called_once_with("bulk", manifest, 1)
    gateway.stop_local.assert_not_called()
    warm = state("a" * 40)
    warm["claims_paused"] = True
    warm["members"][0]["serving"] = False
    gateway.observe.return_value = {"bulk": warm}
    release.transition(gateway, "bulk", manifest, timeout=0, pause=0)


def test_collector_reads_release_owned_build_allowlist_from_canonical_path(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "collector", ROOT / "deploy/worker-pools/metrics.py"
    )
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    path = tmp_path / "worker-pools-observation.json"
    config = {
        "zone": "ru-central1-a",
        "folder_id": "worker-folder",
        "canonical_folder_id": "canonical-folder",
        "releases": {"a" * 40: "digest"},
    }
    path.write_text(json.dumps(config))
    run = Mock()
    collector.collect({"deploy_root": str(tmp_path), "cloud": str(path)}, run=run)
    assert json.loads(run.call_args_list[0].kwargs["input"]) == config
    assert run.call_count == 2
    assert run.call_args_list[1].args[0][-2:] == ["--folder-id", "canonical-folder"]


def test_host_observe_publishes_in_canonical_folder(tmp_path):
    release = release_module()
    config = {
        "folder_id": "worker-folder",
        "canonical_folder_id": "canonical-folder",
        "zone": "ru-central1-a",
    }
    (tmp_path / "worker-pools-observation.json").write_text(json.dumps(config))
    host = release.Host(tmp_path, Mock(), Mock())
    host.command = Mock()
    host.control = Mock(return_value={"bulk": {}})

    assert host.observe() == {"bulk": {}}
    assert host.command.call_args_list[0].kwargs["payload"] == config
    assert host.command.call_args_list[1].args[0][-2:] == ["--folder-id", "canonical-folder"]


def test_host_observe_does_not_publish_when_cloud_command_rejects_config(tmp_path):
    release = release_module()
    (tmp_path / "worker-pools-observation.json").write_text(
        json.dumps({"folder_id": "worker-folder"})
    )
    host = release.Host(tmp_path, Mock(), Mock())
    host.command = Mock(side_effect=ValueError("invalid cloud configuration"))

    with pytest.raises(ValueError, match="invalid cloud configuration"):
        host.observe()
    assert host.command.call_count == 1


class FleetFixture:
    """Injected host/cloud actions, not evidence of native autoscaler behaviour."""

    def __init__(self, count=1):
        self.snapshot_state = state(*(["a" * 40] * count))
        self.desired = "a" * 40
        self.events = []

    def observe(self):
        return {"selfie": self.snapshot_state}

    def control(self, operation, **args):
        self.events.append(operation)
        s = self.snapshot_state
        if operation == "stage":
            assert s["active_build"] == args["active_build"]
            s["staged_build"] = args["staged_build"]
        elif operation == "promote":
            assert s["active_build"] == args["active_build"]
            s["active_build"], s["staged_build"] = args["staged_build"], None
            for m in s["members"]:
                m["serving"] = m["worker_build"] == s["active_build"]
        elif operation == "retire":
            victim = args["identity"]["instance_id"]
            assert any(m["instance_id"] != victim and m["serving"] for m in s["members"])
            for field in ("members", "observed_members"):
                s[field] = [m for m in s[field] if m["instance_id"] != victim]
            self.add_candidate()
            return {"grant": {"grant_id": "fixture"}}
        elif operation == "cancel":
            assert not any(m["worker_build"] == s["staged_build"] for m in s["members"])
            s["staged_build"] = None
        elif operation != "recover":
            raise AssertionError(operation)
        return {"ok": True}

    def add_candidate(self):
        s = self.snapshot_state
        if len(s["members"]) < 2 and not any(
            m["worker_build"] == self.desired for m in s["members"]
        ):
            node = {
                "instance_id": "candidate-" + self.desired[:1],
                "worker_build": self.desired,
                "boot_id": "new-boot",
                "warm": True,
                "serving": self.desired == s["active_build"],
                "grant": None,
                "draining": False,
                "reconciled": False,
            }
            s["members"].append(node)
            s["observed_members"].append({**node, "status": "RUNNING_ACTUAL"})
        assert len(s["members"]) <= 2

    def template(self, pool, manifest, floor):
        assert 1 <= floor <= 2
        self.events.append("template")
        self.desired = manifest["configuration"]["worker_build"]
        self.add_candidate()


@pytest.mark.parametrize("count", [1, 2])
def test_transition_preserves_survivor_and_rolls_back_as_new_staged_build(count):
    release = release_module()
    fixture = FleetFixture(count)
    manifest = {"configuration": {"worker_build": "b" * 40, "pool_max_size": 2}}
    release.transition(fixture, "selfie", manifest, timeout=1, pause=0)
    assert fixture.snapshot_state["active_build"] == "b" * 40
    assert all(m["worker_build"] == "b" * 40 for m in fixture.snapshot_state["members"])
    assert (
        fixture.events.index("stage")
        < fixture.events.index("template")
        < fixture.events.index("promote")
    )
    release.transition(
        fixture,
        "selfie",
        {"configuration": {"worker_build": "a" * 40, "pool_max_size": 2}},
        timeout=1,
        pause=0,
    )
    assert fixture.events.count("stage") == 2
    assert fixture.snapshot_state["active_build"] == "a" * 40


def test_cancel_retires_candidate_then_uses_guarded_cancel():
    release = release_module()
    fixture = FleetFixture()
    fixture.control("stage", active_build="a" * 40, staged_build="b" * 40)
    fixture.template("selfie", {"configuration": {"worker_build": "b" * 40}}, 2)
    release.cancel(
        fixture,
        "selfie",
        {"configuration": {"worker_build": "a" * 40, "pool_max_size": 2}},
        timeout=1,
        pause=0,
    )
    assert fixture.snapshot_state["staged_build"] is None
    assert fixture.events.index("retire") < fixture.events.index("cancel")


def test_plan_rejects_unreviewed_checksum_and_legacy_previous_release_before_cloud(tmp_path):
    release = release_module()
    with pytest.raises(ValueError, match="checksum"):
        release.validate_manifest({"checksum": "wrong", "configuration": {}})
    with pytest.raises(ValueError):
        release.validate_manifest({"worker_image": "old-tag", "worker_build": "legacy"})


def test_host_web_proof_uses_running_container_image_id_not_requested_environment(tmp_path):
    release = release_module()
    host = release.Host(tmp_path, Mock(), Mock())
    host.run = Mock(
        side_effect=[
            Mock(stdout="container\n"),
            Mock(stdout='[{"Image":"sha256:old","State":{"Running":true}}]'),
        ]
    )
    with pytest.raises(ValueError, match="running web"):
        host.verify_web({"web_id": "sha256:new"})


def test_canonical_archive_runs_exact_cloud_helper_without_source_checkout(tmp_path):
    archive = tmp_path / "package.tar"
    result = subprocess.run(
        ["sh", str(ROOT / "deploy/package-deployment.sh"), str(archive)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    target = tmp_path / "installed"
    target.mkdir()
    with tarfile.open(archive) as package:
        assert all(not Path(name).name.startswith(".env") for name in package.getnames())
        package.extractall(target, filter="data")
    package_dir = target / "deploy/worker-pools"
    copied = package_dir / "_canonical/processing/services/worker_pool_cloud.py"
    assert (
        copied.read_bytes()
        == (ROOT / "src/backend/processing/services/worker_pool_cloud.py").read_bytes()
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import provision; print(provision.digest(provision.cloud_init({"
            "'bootstrap_secret_id':'secret','bootstrap_version_id':'version','worker_build':'a'*40,"
            "'worker_image':'ghcr.io/example/photo-prjct-worker@sha256:'+'a'*64,"
            "'zone':'ru-central1-a',"
            "'docker_version':'27.5.1','compose_version':'2.32.4','private_api_ipv4':'10.0.0.5'},'bulk')))",
        ],
        cwd=package_dir,
        env={**os.environ, "PYTHONPATH": str(package_dir / "_canonical")},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.strip()) == 64


def test_actual_image_proof_checks_web_and_worker_revision_and_digest():
    release = release_module()
    web = "ghcr.io/example/photo-prjct@sha256:" + "a" * 64
    worker = "ghcr.io/example/photo-prjct-worker@sha256:" + "b" * 64

    def run(args, **kwargs):
        image = args[-1]
        if args[1] == "pull":
            return Mock(stdout="")
        digest = worker if "worker" in image else web
        return Mock(
            stdout=json.dumps(
                [
                    {
                        "Id": "sha256:worker" if "worker" in image else "sha256:web",
                        "RepoDigests": [digest],
                        "Config": {"Labels": {"org.opencontainers.image.revision": "a" * 40}},
                    }
                ]
            )
        )

    proof = release.image_proof(
        {"configuration": {"worker_build": "a" * 40, "worker_image": worker}},
        "ghcr.io/example/photo-prjct:" + "a" * 40,
        run=run,
    )
    assert proof == {"web_image": web, "web_id": "sha256:web", "worker_id": "sha256:worker"}


def capped_manifest(cap=1, build="a" * 40):
    from tests.deployment.test_worker_pool_provisioning import config

    configuration = config(cap)
    configuration["worker_build"] = build
    configuration["worker_image"] = "ghcr.io/example/photo-prjct-worker@sha256:" + build[0] * 64
    for name, entry in configuration["groups"].items():
        entry.update(id=name + "-group", baseline="0" * 64)
    return release_module().provision_module().prepare(configuration)


@pytest.mark.parametrize("cap", [1, 2])
def test_release_manifest_binds_each_pool_to_explicit_reviewed_ceiling(cap):
    release = release_module()
    manifest = capped_manifest(cap)
    release.validate_manifest(manifest)
    manifest["groups"]["bulk"]["scalePolicy"]["autoScale"]["maxSize"] = str(3 - cap)
    manifest["checksum"] = release.provision_module().digest(
        {key: value for key, value in manifest.items() if key != "checksum"}
    )
    with pytest.raises(ValueError, match="manifest"):
        release.validate_manifest(manifest)


def test_release_rejects_cross_ceiling_previous_manifest():
    with pytest.raises(ValueError, match="ceiling"):
        release_module().observation_config(capped_manifest(1), {"manifest": capped_manifest(2)})


def test_release_observation_carries_both_folder_ids():
    config = release_module().observation_config(capped_manifest(), None)
    assert config["folder_id"] == "worker-folder"
    assert config["canonical_folder_id"] == "canonical-folder"


@pytest.mark.parametrize("mode", ["rollout", "rollback"])
@pytest.mark.parametrize("field", ["folder_id", "canonical_folder_id"])
def test_release_rejects_folder_drift_before_journal_change(tmp_path, monkeypatch, mode, field):
    release = release_module()
    candidate = capped_manifest()
    previous = deepcopy(candidate)
    previous["configuration"][field] = "other-folder"
    original = {
        "phase": "rolling",
        "pending": None,
        "candidate": {"manifest": candidate, "proof": {}},
        "previous": {"manifest": previous, "proof": {}},
    }
    path = tmp_path / "worker-pools-release.json"
    release.Journal(path, deepcopy(original))
    monkeypatch.setattr("processing.services.worker_pool_cloud.metadata_token", lambda: "fixture")
    monkeypatch.setattr(
        release, "provision_module", lambda: SimpleNamespace(Cloud=lambda token: Mock())
    )
    monkeypatch.setattr(release, "Host", Mock(side_effect=AssertionError("host started")))

    with pytest.raises(ValueError, match="release scope changed"):
        release.execute(mode, tmp_path, None, None, None)
    assert release.Journal(path).data == original


class DiskCloud:
    """Provider boundary with paginated inventories and independent VM/disk lifetimes."""

    def __init__(self, manifest):
        from processing.services.worker_pool_cloud import CloudReader

        self.pages = CloudReader.pages.__get__(self)
        self.groups = {
            name + "-group": {"id": name + "-group", **deepcopy(group)}
            for name, group in manifest["groups"].items()
        }
        self.members = {name + "-group": [] for name in ("bulk", "selfie")}
        self.instances = {}
        self.disks = {}
        self.writes = []
        self.lost_response = False
        self.incomplete_disks = False
        for name in ("bulk", "selfie"):
            self.add(name, name + "-old")

    def add(self, name, identity, *, status="RUNNING_ACTUAL"):
        self.members[name + "-group"].append(
            {"instanceId": identity, "status": status, "zoneId": "ru-central1-a"}
        )
        self.instances[identity] = {
            "id": identity,
            "folderId": "worker-folder",
            "zoneId": "ru-central1-a",
            "status": "RUNNING",
            "bootDisk": {"diskId": identity + "-disk"},
        }
        self.disks[identity + "-disk"] = {
            "id": identity + "-disk",
            "folderId": "worker-folder",
            "zoneId": "ru-central1-a",
            "sourceImageId": "boot-image",
            "status": "READY",
            "instanceIds": [identity],
        }

    def get(self, path, **parameters):
        if path.startswith("instanceGroups/"):
            parts = path.split("/")
            if len(parts) == 2:
                return deepcopy(self.groups[parts[1]])
            return {"instances": deepcopy(self.members[parts[1]])}
        assert path in {"instances", "disks"}, path
        rows = list(getattr(self, path).values())
        # Exercise canonical complete pagination, including the final empty page.
        offset = int(parameters.get("pageToken") or "0")
        if path == "disks" and self.incomplete_disks and offset:
            raise TimeoutError("disk page unavailable")
        return {
            path: deepcopy(rows[offset : offset + 1]),
            **({"nextPageToken": str(offset + 1)} if offset < len(rows) else {}),
        }

    def mutate(self, method, path, body):
        assert method == "PATCH"
        self.writes.append((path, deepcopy(body)))
        self.groups[path.split("/")[1]].update(
            {key: deepcopy(value) for key, value in body.items() if key != "updateMask"}
        )
        if self.lost_response:
            self.lost_response = False
            raise TimeoutError("lost response")
        return {"id": "operation"}


def disk_host(tmp_path, monkeypatch):
    release = release_module()
    monkeypatch.setattr(release.time, "sleep", lambda delay: None)
    manifest = capped_manifest()
    cloud = DiskCloud(manifest)
    journal = release.Journal(tmp_path / "journal.json", {"pending": None})
    return release, release.Host(tmp_path, cloud, journal), manifest


def test_temporary_ceiling_is_restored_and_other_pool_cannot_expand(tmp_path, monkeypatch):
    _, host, manifest = disk_host(tmp_path, monkeypatch)
    host.template("bulk", manifest, 2)
    assert host.cloud.groups["bulk-group"]["scalePolicy"]["autoScale"]["maxSize"] == "2"
    with pytest.raises(ValueError, match="serial"):
        host.template("selfie", manifest, 2)
    host.template("bulk", manifest, 0)
    host.disk_fence(manifest, settled="bulk")
    host.template("selfie", manifest, 2)
    assert host.cloud.groups["bulk-group"]["scalePolicy"]["autoScale"]["maxSize"] == "1"


@pytest.mark.parametrize("status", ["STOPPED", "DELETING", "CREATING"])
def test_stopped_or_transitional_allocation_blocks_fourth_disk(tmp_path, monkeypatch, status):
    _, host, manifest = disk_host(tmp_path, monkeypatch)
    host.cloud.add("bulk", "bulk-spare", status=status)
    with pytest.raises(ValueError):
        host.template("selfie", manifest, 2)
    assert not host.cloud.writes


@pytest.mark.parametrize("status", ["STOPPED", "STOPPING", "STARTING", "UNKNOWN", None])
def test_compute_instance_must_affirm_running_before_expansion(tmp_path, monkeypatch, status):
    release, host, manifest = disk_host(tmp_path, monkeypatch)
    instance = host.cloud.instances["bulk-old"]
    if status is None:
        del instance["status"]
    else:
        instance["status"] = status
    # Group membership remains RUNNING_ACTUAL and the disk remains READY.
    with pytest.raises(ValueError, match="stopped or transitional"):
        host.template("selfie", manifest, 2)
    assert not host.cloud.writes
    assert release.Journal(host.journal.path).data["worker_disks"]["bulk-old-disk"] == {
        "pool": "bulk",
        "instance_id": "bulk-old",
    }


@pytest.mark.parametrize("status", ["READY", "DELETING"])
def test_disk_absence_is_independent_of_member_absence_and_survives_restart(
    tmp_path, monkeypatch, status
):
    release, host, manifest = disk_host(tmp_path, monkeypatch)
    host.disk_fence(manifest)
    assert "bulk-old-disk" in release.Journal(host.journal.path).data["worker_disks"]
    host.cloud.members["bulk-group"] = []
    del host.cloud.instances["bulk-old"]
    host.cloud.disks["bulk-old-disk"]["status"] = status
    restarted = release.Host(tmp_path, host.cloud, release.Journal(host.journal.path))
    with pytest.raises(ValueError, match="retained"):
        restarted.template("selfie", manifest, 2)
    assert not host.cloud.writes
    del host.cloud.disks["bulk-old-disk"]
    restarted.template("selfie", manifest, 2)
    assert len(host.cloud.writes) == 1


@pytest.mark.parametrize(
    "problem", ["unexplained", "folder", "image", "duplicate", "incomplete", "changing"]
)
def test_ambiguous_inventory_cannot_authorize_expansion(tmp_path, monkeypatch, problem):
    _, host, manifest = disk_host(tmp_path, monkeypatch)
    cloud = host.cloud
    if problem == "unexplained":
        cloud.disks["retained"] = {**cloud.disks["bulk-old-disk"], "id": "retained"}
    elif problem == "folder":
        cloud.disks["bulk-old-disk"]["folderId"] = "other"
    elif problem == "image":
        cloud.disks["bulk-old-disk"]["sourceImageId"] = "other"
    elif problem == "duplicate":
        cloud.members["selfie-group"] = deepcopy(cloud.members["bulk-group"])
    elif problem == "incomplete":
        cloud.incomplete_disks = True
    else:
        original = cloud.get
        calls = 0

        def changed(path, **kwargs):
            nonlocal calls
            result = original(path, **kwargs)
            if path == "instanceGroups/bulk-group/instances":
                calls += 1
                if calls > 1:
                    return {"instances": []}
            return result

        cloud.get = changed
    with pytest.raises((ValueError, TimeoutError)):
        host.template("selfie", manifest, 2)
    assert not cloud.writes


def test_lost_expansion_response_reconciles_without_a_second_submission(tmp_path, monkeypatch):
    release, host, manifest = disk_host(tmp_path, monkeypatch)
    host.cloud.lost_response = True
    with pytest.raises(TimeoutError):
        host.template("bulk", manifest, 2)
    restarted = release.Host(tmp_path, host.cloud, release.Journal(host.journal.path))
    restarted.template("bulk", manifest, 2)
    assert len(host.cloud.writes) == 1
    assert restarted.journal.data["pending"] is None
    with pytest.raises(ValueError, match="serial"):
        restarted.template("selfie", manifest, 2)


def capped_fleet(tmp_path, monkeypatch, *, initial=False):
    release = release_module()
    monkeypatch.setattr(release.time, "sleep", lambda delay: None)
    old, new = capped_manifest(), capped_manifest(build="b" * 40)
    cloud = DiskCloud(new if initial else old)
    journal = release.Journal(
        tmp_path / "worker-pools-release.json",
        {
            "phase": "rolling",
            "pending": None,
            "candidate": {"manifest": new, "proof": {}},
            "previous": None if initial else {"manifest": old, "proof": {}},
        },
    )

    class Host(release.Host):
        def __init__(self):
            super().__init__(tmp_path, cloud, journal)
            self.events = []
            self.retain_disk = False
            self.interrupt_pool = None
            self.states = {}
            for name in ("bulk", "selfie"):
                snapshot = state(("b" if initial else "a") * 40)
                snapshot["active_build"] = ("b" if initial else "a") * 40
                snapshot["claims_paused"] = initial
                for key in ("members", "observed_members"):
                    snapshot[key][0]["instance_id"] = name + "-old"
                snapshot["members"][0]["serving"] = not initial
                self.states[name] = snapshot

        def observe(self):
            return deepcopy(self.states)

        def verify_web(self, proof):
            pass

        def stop_local(self):
            self.events.append(("stop-local",))

        def template(self, name, manifest, floor):
            if floor == 0:
                assert not self.states[name]["claims_paused"], "paused sole candidate lost"
            super().template(name, manifest, floor)
            self.events.append(("template", name, floor))
            build = manifest["configuration"]["worker_build"]
            snapshot = self.states[name]
            if floor > len(snapshot["members"]) and not any(
                member["worker_build"] == build for member in snapshot["members"]
            ):
                identity = name + "-" + build[0]
                cloud.add(name, identity)
                member = {
                    **state(build)["members"][0],
                    "instance_id": identity,
                    "serving": False,
                }
                snapshot["members"].append(member)
                snapshot["observed_members"].append({**member, "status": "RUNNING_ACTUAL"})
            assert len(cloud.disks) <= 3, "fourth allocated worker disk"
            if name == self.interrupt_pool and floor == 2:
                self.interrupt_pool = None
                raise TimeoutError("interrupted after expansion")

        def control(self, operation, **args):
            if operation == "status":
                return self.observe()
            name = args.get("pool", args.get("identity", {}).get("pool"))
            snapshot = self.states[name]
            self.events.append((operation, name))
            if operation == "stage":
                snapshot["staged_build"] = args["staged_build"]
            elif operation == "promote":
                snapshot["active_build"], snapshot["staged_build"] = args["staged_build"], None
                for member in snapshot["members"]:
                    member["serving"] = member["worker_build"] == snapshot["active_build"]
            elif operation == "retire":
                victim = args["identity"]["instance_id"]
                assert any(m["instance_id"] != victim and m["serving"] for m in snapshot["members"])
                assert cloud.groups[name + "-group"]["scalePolicy"]["autoScale"]["maxSize"] == "1"
                assert victim + "-disk" in release.Journal(journal.path).data["worker_disks"]
                for key in ("members", "observed_members"):
                    snapshot[key] = [m for m in snapshot[key] if m["instance_id"] != victim]
                cloud.members[name + "-group"] = [
                    row for row in cloud.members[name + "-group"] if row["instanceId"] != victim
                ]
                del cloud.instances[victim]
                if not self.retain_disk:
                    del cloud.disks[victim + "-disk"]
            elif operation == "cancel":
                snapshot["staged_build"] = None
            elif operation == "pause":
                key = "local_claims_paused" if args["local"] else "claims_paused"
                snapshot[key] = args["paused"]
                if not args["local"]:
                    for member in snapshot["members"]:
                        member["serving"] = not args["paused"]
            elif operation not in {"recover", "drain-local"}:
                raise AssertionError(operation)
            return {"ok": True}

    host = Host()

    def restore_host(root, provider, restored_journal):
        host.journal = restored_journal
        return host

    monkeypatch.setattr(release, "Host", restore_host)
    monkeypatch.setattr(
        release, "provision_module", lambda: SimpleNamespace(Cloud=lambda token: cloud)
    )
    monkeypatch.setattr("processing.services.worker_pool_cloud.metadata_token", lambda: "fixture")
    return release, host, new, old


def test_capped_forward_and_rollback_restore_each_pool_before_next_expansion(tmp_path, monkeypatch):
    release, host, new, old = capped_fleet(tmp_path, monkeypatch)
    for mode, manifest in (("rollout", new), ("rollback", old)):
        host.events.clear()
        release.execute(mode, tmp_path, None, None, None)
        assert host.events.index(("template", "bulk", 0)) < host.events.index(
            ("template", "selfie", 2)
        )
        assert len(host.cloud.disks) == 2
        for name in ("bulk", "selfie"):
            assert (
                host.cloud.groups[name + "-group"]["scalePolicy"]
                == manifest["groups"][name]["scalePolicy"]
            )
            assert host.states[name]["active_build"] == manifest["configuration"]["worker_build"]


@pytest.mark.parametrize("mode", ["rollout", "rollback"])
def test_retained_disk_blocks_next_pool_and_reentry_until_complete_absence(
    tmp_path, monkeypatch, mode
):
    release, host, _, _ = capped_fleet(tmp_path, monkeypatch)
    if mode == "rollback":
        release.execute("rollout", tmp_path, None, None, None)
        host.events.clear()
    host.retain_disk = True
    with pytest.raises(ValueError, match="retained"):
        release.execute(mode, tmp_path, None, None, None)
    assert ("template", "selfie", 2) not in host.events
    host.journal = release.Journal(host.journal.path)
    with pytest.raises(ValueError, match="retained"):
        release.execute(mode, tmp_path, None, None, None)
    del host.cloud.disks["bulk-old-disk" if mode == "rollout" else "bulk-b-disk"]
    host.retain_disk = False
    release.execute(mode, tmp_path, None, None, None)
    assert host.journal.data["phase"] == ("verified" if mode == "rollout" else "rolled-back")


@pytest.mark.parametrize("mode", ["rollout", "rollback"])
def test_interrupted_second_pool_is_reconciled_first_in_either_direction(
    tmp_path, monkeypatch, mode
):
    release, host, _, old = capped_fleet(tmp_path, monkeypatch)
    host.interrupt_pool = "selfie"
    with pytest.raises(TimeoutError, match="interrupted"):
        release.execute("rollout", tmp_path, None, None, None)
    host.journal = release.Journal(host.journal.path)
    host.events.clear()
    release.execute(mode, tmp_path, None, None, None)
    assert host.events[0][1] == "selfie"
    assert len(host.cloud.disks) == 2
    if mode == "rollback":
        assert all(
            row["active_build"] == old["configuration"]["worker_build"]
            for row in host.states.values()
        )


def test_initial_capped_cutover_preserves_paused_bulk_until_claims_open(tmp_path, monkeypatch):
    release, host, _, _ = capped_fleet(tmp_path, monkeypatch, initial=True)
    release.execute("rollout", tmp_path, None, None, None)
    assert host.events.index(("stop-local",)) < host.events.index(("template", "bulk", 0))
    assert all(any(member["serving"] for member in row["members"]) for row in host.states.values())


def test_initial_warm_floor_cannot_allocate_over_unexplained_worker_disks(tmp_path, monkeypatch):
    _, host, manifest = disk_host(tmp_path, monkeypatch)
    host.cloud.disks["retained"] = {**host.cloud.disks["bulk-old-disk"], "id": "retained"}
    with pytest.raises(ValueError, match="unexplained"):
        host.template("bulk", manifest, 1)
    assert not host.cloud.writes


def test_unjournaled_extra_running_member_blocks_release_expansion(tmp_path, monkeypatch):
    _, host, manifest = disk_host(tmp_path, monkeypatch)
    host.cloud.add("bulk", "bulk-extra")
    with pytest.raises(ValueError, match="unexplained"):
        host.template("bulk", manifest, 2)
    assert not host.cloud.writes


def test_final_fleet_verification_rejects_unsettled_extra_candidate(tmp_path, monkeypatch):
    release, host, new, _ = capped_fleet(tmp_path, monkeypatch, initial=True)
    release.execute("rollout", tmp_path, None, None, None)
    host.cloud.add("bulk", "extra-candidate")
    with pytest.raises(ValueError, match="settled"):
        release.verify_fleet(host, new)


def test_uncertain_unapplied_expansion_does_not_retry_or_switch_pools(tmp_path, monkeypatch):
    release, host, manifest = disk_host(tmp_path, monkeypatch)
    calls = []

    def lost(method, path, body):
        calls.append(path)
        raise TimeoutError("unknown submission outcome")

    host.cloud.mutate = lost
    with pytest.raises(TimeoutError):
        host.template("bulk", manifest, 2)
    restarted = release.Host(tmp_path, host.cloud, release.Journal(host.journal.path))
    with pytest.raises(ValueError, match="uncertain"):
        restarted.template("selfie", manifest, 2)
    assert calls == ["instanceGroups/bulk-group"]
    assert restarted.journal.data["expanded_pool"] == "bulk"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name,target,allowed", [("selfie", 1, False), ("bulk", 1, False), ("bulk", 0, True)]
)
def test_capped_sole_worker_uses_fresh_target_and_existing_retirement_grants(
    settings, name, target, allowed
):
    from datetime import timedelta

    from django.utils import timezone
    from processing.models import WorkerPoolMember
    from processing.services import worker_pool_lifecycle as lifecycle

    settings.PHOTO_WORKER_POOL_COORDINATOR_ENABLED = True
    now = timezone.now()
    build = "a" * 40
    lifecycle.configure_pool(name, group_id=name + "-group", active_build=build)
    assert lifecycle.record_cloud_snapshot(
        name,
        group_id=name + "-group",
        sequence=1,
        started_at=now,
        completed_at=now,
        target_size=target,
        members=[{"instance_id": "sole", "status": "RUNNING_ACTUAL", "worker_build": build}],
        complete=True,
    )
    lifecycle.record_queue_observation(name, observed_at=now, endpoint_available=True)
    lifecycle.set_claims_paused(name, paused=False)
    identity = lifecycle.MemberIdentity(name, "sole", uuid4(), build)
    response = lifecycle.register(identity)
    identity = replace(identity, registration_generation=UUID(response["registration_generation"]))
    lifecycle.heartbeat(identity, ready=True, draining=False)
    WorkerPoolMember.objects.filter(instance_id="sole").update(
        idle_since=now - timedelta(minutes=20)
    )
    grant = lifecycle.request_retirement(identity)
    assert bool(grant) is allowed
    if allowed:
        assert lifecycle.request_retirement(identity) == grant
    else:
        member = WorkerPoolMember.objects.get(instance_id="sole")
        assert member.ready and not member.draining
