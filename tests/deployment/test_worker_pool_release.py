import ast
import importlib.util
import json
import os
import shlex
import subprocess
import tarfile
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
        }
        for name in ("bulk", "selfie")
    }
    manifest = {
        "configuration": {
            "worker_build": build,
            "groups": {name: {"id": name + "-group"} for name in groups},
        },
        "groups": groups,
    }
    config = {
        "folder_id": "folder",
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
            return {"id": identity, "sourceImageId": "boot-image"}

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
            "folder_id": "folder",
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
    manifest = {"configuration": {"worker_build": "a" * 40}}
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
    config = {"zone": "ru-central1-a", "folder_id": "folder", "releases": {"a" * 40: "digest"}}
    path.write_text(json.dumps(config))
    run = Mock()
    collector.collect({"deploy_root": str(tmp_path), "cloud": str(path)}, run=run)
    assert json.loads(run.call_args_list[0].kwargs["input"]) == config
    assert run.call_count == 2


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
    manifest = {"configuration": {"worker_build": "b" * 40}}
    release.transition(fixture, "selfie", manifest, timeout=1, pause=0)
    assert fixture.snapshot_state["active_build"] == "b" * 40
    assert all(m["worker_build"] == "b" * 40 for m in fixture.snapshot_state["members"])
    assert (
        fixture.events.index("stage")
        < fixture.events.index("template")
        < fixture.events.index("promote")
    )
    release.transition(
        fixture, "selfie", {"configuration": {"worker_build": "a" * 40}}, timeout=1, pause=0
    )
    assert fixture.events.count("stage") == 2
    assert fixture.snapshot_state["active_build"] == "a" * 40


def test_cancel_retires_candidate_then_uses_guarded_cancel():
    release = release_module()
    fixture = FleetFixture()
    fixture.control("stage", active_build="a" * 40, staged_build="b" * 40)
    fixture.template("selfie", {"configuration": {"worker_build": "b" * 40}}, 2)
    release.cancel(
        fixture, "selfie", {"configuration": {"worker_build": "a" * 40}}, timeout=1, pause=0
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
            str(ROOT / ".venv/bin/python"),
            "-c",
            "import provision; print(provision.digest(provision.cloud_init({"
            "'bootstrap_secret_id':'secret','bootstrap_version_id':'version','worker_build':'a'*40,"
            "'worker_image':'ghcr.io/example/photo-prjct-worker@sha256:'+'a'*64,"
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
