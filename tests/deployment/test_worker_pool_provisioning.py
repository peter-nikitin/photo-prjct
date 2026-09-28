import base64
import importlib.util
import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from photo_worker.runner import WorkerConfig

pytestmark = pytest.mark.operational
ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"deploy/worker-pools/{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def config():
    return {
        "cloud_id": "cloud",
        "folder_id": "folder",
        "zone": "ru-central1-a",
        "network_id": "network",
        "subnet_id": "subnet",
        "worker_sg_id": "worker-sg",
        "canonical_vm_id": "canonical",
        "private_api_ipv4": "10.0.0.5",
        "worker_sa_id": "worker-sa",
        "manager_sa_id": "manager-sa",
        "bootstrap_secret_id": "worker-secret",
        "bootstrap_version_id": "secret-version",
        "application_secret_id": "app-secret",
        "boot_image_id": "boot-image",
        "docker_version": "27.5.1",
        "compose_version": "2.32.4",
        "worker_build": "a" * 40,
        "worker_image": "ghcr.io/example/photo-prjct-worker@sha256:" + "a" * 64,
        "egress_gateway_id": "gateway",
        "route_table_id": "routes",
        "groups": {
            "bulk": {"id": None, "baseline": None},
            "selfie": {"id": None, "baseline": None},
        },
    }


def test_dry_run_is_deterministic_secretless_and_has_fixed_bounded_shapes():
    provision = module("provision")
    cloud = Mock()
    plan = provision.prepare(config())
    assert plan == provision.prepare(config())
    cloud.assert_not_called()
    assert set(plan["groups"]) == {"bulk", "selfie"}
    for pool, body in plan["groups"].items():
        assert body["name"] == f"findme-photo-worker-{pool}"
        assert body["instanceTemplate"]["resourcesSpec"] == {
            "cores": "2",
            "coreFraction": "100",
            "memory": "8589934592",
        }
        assert body["instanceTemplate"]["bootDiskSpec"]["diskSpec"]["size"] == "34359738368"
        assert body["instanceTemplate"]["schedulingPolicy"]["preemptible"] is (pool == "bulk")
        policy = body["scalePolicy"]["autoScale"]
        assert (policy["minZoneSize"], policy["maxSize"], policy["initialSize"]) == (
            "0" if pool == "bulk" else "1",
            "2",
            "1",
        )
        assert policy["customRules"][0]["labels"] == {"pool": pool, "zone_id": "ru-central1-a"}
        assert body["deployPolicy"] == {
            "strategy": "OPPORTUNISTIC",
            "maxUnavailable": "1",
            "maxExpansion": "0",
            "maxDeleting": "1",
            "maxCreating": "1",
            "startupDuration": "600s",
        }
        assert "oneToOneNat" not in json.dumps(body)
        assert body["instanceTemplate"]["serviceAccountId"] == "worker-sa"
        user_data = body["instanceTemplate"]["metadata"]["user-data"]
        assert "PHOTO_PROCESSING_FLEET_TOKEN=" not in user_data
        assert "app-secret" not in user_data
        assert "boot-image" in json.dumps(body)


def test_prepared_bootstrap_projects_real_compose_environment_into_worker_config(
    tmp_path, monkeypatch
):
    provision = module("provision")
    bootstrap = module("bootstrap")
    plan = provision.prepare(config())
    user_data = plan["groups"]["selfie"]["instanceTemplate"]["metadata"]["user-data"]
    files = {
        entry["path"]: base64.b64decode(entry["content"]).decode()
        for entry in yaml.safe_load(user_data)["write_files"]
    }
    conf = json.loads(files["/etc/findme-worker/bootstrap.json"])
    compose = tmp_path / "compose.yml"
    compose.write_text(files["/usr/local/lib/findme-worker/compose.yml"])

    def run(args, **kwargs):
        if args[1:3] == ["image", "inspect"]:
            return Mock(stdout=conf["worker_build"] + "\n")
        if args[1] == "version":
            return Mock(stdout="27.5.1\n")
        if args[1:3] == ["compose", "version"]:
            return Mock(stdout="2.32.4\n")
        return Mock(stdout="")

    bootstrap.activate(
        conf,
        {"PHOTO_PROCESSING_FLEET_TOKEN": "fleet-token", "IMAGE_PULL_AUTH": "dXNlcjpwYXNz"},
        "instance-1",
        root=tmp_path,
        run=run,
    )
    auth = json.loads((tmp_path / "etc/findme-worker/docker/config.json").read_text())
    assert auth == {"auths": {"ghcr.io": {"auth": "dXNlcjpwYXNz"}}}
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(tmp_path / "etc/findme-worker/runtime.env"),
            "-f",
            str(compose),
            "config",
        ],
        env=os.environ,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    worker = yaml.safe_load(result.stdout)["services"]["photo-worker"]
    assert worker["image"] == conf["worker_image"]
    for key, value in worker["environment"].items():
        monkeypatch.setenv(key, str(value))
    actual, _client = WorkerConfig.from_env()
    assert actual.worker_build == conf["worker_build"]
    assert actual.remote_pool == "selfie"


def test_configuration_rejects_unknown_targets_shared_secret_public_api_and_unknown_inputs():
    provision = module("provision")
    for mutation in (
        {"extra": "secret"},
        {"private_api_ipv4": "111.88.151.64"},
        {"bootstrap_secret_id": "app-secret"},
        {"worker_sa_id": "manager-sa"},
        {"worker_image": "cr.yandex/registry/worker@sha256:" + "a" * 64},
        {"worker_image": "ghcr.io/example/photo-prjct-worker:latest"},
        {"worker_image": "ghcr.io/example/photo-prjct-worker@sha256:wrong"},
        {
            "groups": {
                "bulk": {"id": None, "baseline": None},
                "foreign": {"id": None, "baseline": None},
            }
        },
    ):
        value = config() | mutation
        with pytest.raises(ValueError):
            provision.prepare(value)


def test_checksum_mismatch_cannot_make_cloud_calls():
    provision = module("provision")
    cloud = Mock()
    with pytest.raises(ValueError):
        provision.apply(config(), "0" * 64, cloud=cloud)
    cloud.assert_not_called()


def test_bootstrap_rejects_unexpected_secret_payload_without_materializing_or_running(tmp_path):
    bootstrap = module("bootstrap")
    payload = {
        "versionId": "secret-version",
        "entries": [
            {"key": "PHOTO_PROCESSING_FLEET_TOKEN", "textValue": "fleet-token"},
            {"key": "IMAGE_PULL_AUTH", "textValue": "pull-auth"},
            {"key": "DATABASE_URL", "textValue": "private-db"},
        ],
    }
    with pytest.raises(ValueError):
        bootstrap.validate_payload(payload, "secret-version")
    assert list(tmp_path.iterdir()) == []


def test_bootstrap_private_files_and_real_oci_revision_gate(tmp_path):
    bootstrap = module("bootstrap")
    conf = config()
    conf["pool"] = "bulk"
    conf["identities"] = "1/capture_metadata/2"
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["image", "inspect"]:
            return Mock(stdout="b" * 40 + "\n")
        if args[1] == "version":
            return Mock(stdout="27.5.1\n")
        if args[1:3] == ["compose", "version"]:
            return Mock(stdout="2.32.4\n")
        return Mock(stdout="")

    with pytest.raises(ValueError):
        bootstrap.activate(
            conf,
            {"PHOTO_PROCESSING_FLEET_TOKEN": "fleet-token", "IMAGE_PULL_AUTH": "pull-auth"},
            "instance-1",
            root=tmp_path,
            run=run,
        )
    assert not any("up" in args for args in calls)
    assert any(args[1:3] == ["image", "inspect"] for args in calls)
    assert (tmp_path / "etc/findme-worker/runtime.env").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "etc/findme-worker/docker/config.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "etc/findme-worker/instance-id").stat().st_mode & 0o777 == 0o644
    assert (tmp_path / "etc/findme-worker/instance-id").read_text() == "instance-1\n"


@pytest.mark.parametrize(
    "image",
    [
        "cr.yandex/registry/worker@sha256:" + "a" * 64,
        "ghcr.io/example/photo-prjct-worker@sha256:wrong",
    ],
)
def test_bootstrap_rejects_noncanonical_or_malformed_image_before_credentials(tmp_path, image):
    bootstrap = module("bootstrap")
    run = Mock()
    conf = config() | {"pool": "bulk", "worker_image": image}
    with pytest.raises(ValueError):
        bootstrap.activate(conf, {}, "instance-1", root=tmp_path, run=run)
    run.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_public_8443_in_any_attached_canonical_security_group_is_rejected():
    provision = module("provision")
    canonical = {
        "id": "canonical",
        "folderId": "folder",
        "networkInterfaces": [
            {
                "subnetId": "subnet",
                "securityGroupIds": ["restricted", "broad"],
                "primaryV4Address": {"address": "10.0.0.5"},
            }
        ],
    }
    groups = {
        "restricted": {
            "rules": [
                {
                    "direction": "INGRESS",
                    "protocolName": "TCP",
                    "ports": {"fromPort": "8443", "toPort": "8443"},
                    "securityGroupId": "worker-sg",
                }
            ]
        },
        "broad": {
            "rules": [
                {
                    "direction": "INGRESS",
                    "protocolName": "ANY",
                    "cidrBlocks": {"v4CidrBlocks": ["0.0.0.0/0"]},
                }
            ]
        },
    }
    with pytest.raises(ValueError):
        provision.validate_private_edge(config(), canonical, groups)
    canonical["networkInterfaces"][0]["securityGroupIds"] = ["restricted"]
    provision.validate_private_edge(config(), canonical, groups)


class FakeCloud:
    def __init__(self, provision, conf):
        self.provision = provision
        self.conf = conf
        self.groups = []
        self.calls = []
        self.fail_second = False

    def resource(self, service, collection, resource):
        rows = {
            "folder": {"id": "folder", "cloudId": "cloud"},
            "worker-secret": {
                "folderId": "folder",
                "status": "ACTIVE",
                "currentVersion": {
                    "id": "secret-version",
                    "payloadEntryKeys": ["PHOTO_PROCESSING_FLEET_TOKEN", "IMAGE_PULL_AUTH"],
                },
            },
            "subnet": {
                "folderId": "folder",
                "zoneId": "ru-central1-a",
                "networkId": "network",
                "routeTableId": "routes",
            },
            "routes": {
                "networkId": "network",
                "staticRoutes": [{"destinationPrefix": "0.0.0.0/0", "gatewayId": "gateway"}],
            },
            "gateway": {"folderId": "folder", "sharedEgressGateway": {}},
            "network": {"defaultSecurityGroupId": "edge-sg"},
            "worker-sg": {"networkId": "network", "rules": [{"direction": "EGRESS"}]},
            "edge-sg": {
                "rules": [
                    {
                        "direction": "INGRESS",
                        "protocolName": "TCP",
                        "ports": {"fromPort": "8443", "toPort": "8443"},
                        "securityGroupId": "worker-sg",
                    }
                ]
            },
        }
        return deepcopy(rows[resource])

    def bindings(self, service, collection, resource):
        role = {
            "worker-secret": "lockbox.payloadViewer",
        }.get(resource)
        return (
            [{"roleId": role, "subject": {"id": "worker-sa", "type": "serviceAccount"}}]
            if role
            else []
        )

    def get(self, path, **parameters):
        if path == "instances/canonical":
            return {
                "id": "canonical",
                "folderId": "folder",
                "networkInterfaces": [
                    {
                        "securityGroupIds": [],
                        "subnetId": "subnet",
                        "primaryV4Address": {"address": "10.0.0.5"},
                    }
                ],
            }
        if path == "images/boot-image":
            return {"id": "boot-image", "status": "READY"}
        return deepcopy(next(row for row in self.groups if path == "instanceGroups/" + row["id"]))

    def pages(self, path, key, **parameters):
        return deepcopy(self.groups) if path == "instanceGroups" else []

    def mutate(self, method, path, body):
        self.calls.append((method, path, body))
        pool = body["labels"]["pool"]
        if pool == "selfie" and self.fail_second:
            raise TimeoutError("lost response")
        resource = f"{pool}-group"
        self.groups.append(body | {"id": resource, "status": "ACTIVE"})
        return {"id": f"{pool}-operation", "metadata": {"instanceGroupId": resource}}


def test_initial_creation_records_exact_ids_and_never_writes_prerequisites(tmp_path):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    receipt = provision.apply(
        conf,
        provision.prepare(conf)["checksum"],
        cloud=cloud,
        receipt_path=tmp_path / "receipt.json",
    )
    assert [call[:2] for call in cloud.calls] == [
        ("POST", "instanceGroups"),
        ("POST", "instanceGroups"),
    ]
    assert receipt["groups"]["bulk"]["id"] == "bulk-group"
    assert receipt["groups"]["selfie"]["id"] == "selfie-group"
    assert json.loads((tmp_path / "receipt.json").read_text()) == receipt
    assert provision.status(conf, cloud)["bulk"]["id"] == "bulk-group"


def test_partial_creation_or_uncertain_response_requires_reconciliation_not_automatic_retry(
    tmp_path,
):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.fail_second = True
    path = tmp_path / "receipt.json"
    checksum = provision.prepare(conf)["checksum"]
    with pytest.raises(TimeoutError):
        provision.apply(conf, checksum, cloud=cloud, receipt_path=path)
    receipt = json.loads(path.read_text())
    assert receipt["groups"]["bulk"]["state"] == "submitted"
    assert receipt["groups"]["selfie"]["state"] == "submission_uncertain"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        provision.apply(conf, checksum, cloud=cloud, receipt_path=path)
    assert len(cloud.calls) == 2
    assert path.read_bytes() == before
    assert provision.status(conf, cloud)["bulk"]["id"] == "bulk-group"


def test_existing_unknown_duplicate_and_drifted_targets_are_rejected_before_mutation(tmp_path):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.groups = [provision.prepare(conf)["groups"]["bulk"] | {"id": "bulk-group"}]
    with pytest.raises(ValueError):
        provision.apply(
            conf,
            provision.prepare(conf)["checksum"],
            cloud=cloud,
            receipt_path=tmp_path / "creation.json",
        )
    conf["groups"]["bulk"] = {"id": "bulk-group", "baseline": "0" * 64}
    with pytest.raises(ValueError):
        provision.apply(
            conf,
            provision.prepare(conf)["checksum"],
            cloud=cloud,
            receipt_path=tmp_path / "update.json",
        )
    assert cloud.calls == []


def test_collector_cloud_fault_still_publishes_demand_but_reports_incomplete_collection(tmp_path):
    collector = module("metrics")
    runner = Mock(side_effect=[TimeoutError("sensitive-url"), Mock()])
    path = tmp_path / "worker-pools-observation.json"
    path.write_text(json.dumps({"zone": "ru-central1-a", "folder_id": "folder"}))
    with pytest.raises(ValueError, match="cloud observation unavailable"):
        collector.collect(
            {
                "deploy_root": str(tmp_path),
                "cloud": str(path),
            },
            run=runner,
        )
    assert runner.call_count == 2
    assert "publish_worker_pool_metrics" in runner.call_args_list[1].args[0]


def test_collector_publication_fault_is_not_reported_as_success(tmp_path):
    collector = module("metrics")
    runner = Mock(side_effect=[Mock(), subprocess.CalledProcessError(1, "publish")])
    path = tmp_path / "worker-pools-observation.json"
    path.write_text(json.dumps({"zone": "ru-central1-a", "folder_id": "folder"}))
    with pytest.raises(subprocess.CalledProcessError):
        collector.collect({"deploy_root": str(tmp_path), "cloud": str(path)}, run=runner)
    assert runner.call_count == 2


def test_valid_narrow_bootstrap_secret_has_exact_keys_and_no_application_projection():
    bootstrap = module("bootstrap")
    payload = {
        "versionId": "secret-version",
        "entries": [
            {"key": "PHOTO_PROCESSING_FLEET_TOKEN", "textValue": "fleet-token"},
            {"key": "IMAGE_PULL_AUTH", "textValue": "dXNlcjpwYXNz"},
        ],
    }
    assert bootstrap.validate_payload(payload, "secret-version") == {
        "PHOTO_PROCESSING_FLEET_TOKEN": "fleet-token",
        "IMAGE_PULL_AUTH": "dXNlcjpwYXNz",
    }
    payload["entries"].append(deepcopy(payload["entries"][0]))
    with pytest.raises(ValueError):
        bootstrap.validate_payload(payload, "secret-version")


@pytest.mark.parametrize("enabled", [False, True])
def test_telemetry_packaging_opt_in_is_default_off_and_uses_reviewed_image_dependencies(
    tmp_path, monkeypatch, enabled
):
    provision = module("provision")
    bootstrap = module("bootstrap")
    supplied = config() | ({"telemetry_enabled": True} if enabled else {})
    user_data = yaml.safe_load(
        provision.prepare(supplied)["groups"]["bulk"]["instanceTemplate"]["metadata"]["user-data"]
    )
    files = {
        entry["path"]: base64.b64decode(entry["content"]).decode()
        for entry in user_data["write_files"]
    }
    conf = json.loads(files["/etc/findme-worker/bootstrap.json"])
    assert conf["telemetry_enabled"] is enabled
    assert conf["zone"] == "ru-central1-a"
    assert "/usr/local/lib/findme-worker/telemetry.py" in files
    assert "/usr/local/lib/findme-worker/telemetry-requirements.txt" in files
    assert set(user_data) == {"write_files", "runcmd"}
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["image", "inspect"]:
            return Mock(stdout=conf["worker_build"] + "\n")
        if args[1] == "version":
            return Mock(stdout="27.5.1\n")
        if args[1:3] == ["compose", "version"]:
            return Mock(stdout="2.32.4\n")
        return Mock(stdout="")

    bootstrap.activate(
        conf,
        {"PHOTO_PROCESSING_FLEET_TOKEN": "fleet-token", "IMAGE_PULL_AUTH": "dXNlcjpwYXNz"},
        "instance-1",
        root=tmp_path,
        run=run,
    )
    telemetry_calls = [
        args
        for args in calls
        if args[0] == "/opt/findme-worker-telemetry/bin/python"
        or args[-1] == "findme-worker-telemetry.timer"
    ]
    assert bool(telemetry_calls) is enabled
    assert not any(arg in {"pip", "apt", "curl"} for args in calls for arg in args)
    if enabled:
        assert calls.index(next(args for args in calls if "up" in args)) < calls.index(
            telemetry_calls[0]
        )
        assert ["systemctl", "enable", "--now", "findme-worker-telemetry.timer"] in calls
        telemetry_env = tmp_path / "etc/findme-worker/telemetry.env"
        assert telemetry_env.stat().st_mode & 0o777 == 0o600
        values = dict(line.split("=", 1) for line in telemetry_env.read_text().splitlines())
        assert set(values) == {
            "PHOTO_WORKER_POOL",
            "PHOTO_WORKER_BUILD",
            "PHOTO_WORKER_ZONE",
            "PHOTO_PROCESSING_FLEET_TOKEN",
        }
        assert json.loads(values["PHOTO_WORKER_ZONE"]) == "ru-central1-a"
        runtime = (tmp_path / "etc/findme-worker/runtime.env").read_text()
        assert 'PHOTO_WORKER_RUNTIME_TELEMETRY_ENABLED="True"' in runtime


def test_failed_optional_probe_setup_preserves_started_worker_and_retirement(tmp_path, capsys):
    provision = module("provision")
    bootstrap = module("bootstrap")
    user_data = yaml.safe_load(
        provision.prepare(config() | {"telemetry_enabled": True})["groups"]["bulk"][
            "instanceTemplate"
        ]["metadata"]["user-data"]
    )
    conf = json.loads(
        next(
            base64.b64decode(entry["content"])
            for entry in user_data["write_files"]
            if entry["path"].endswith("bootstrap.json")
        )
    )
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if any("/opt/findme-worker-telemetry/bin/python" in arg for arg in args):
            raise subprocess.CalledProcessError(1, args, stderr="private exception")
        if args[1:3] == ["image", "inspect"]:
            return Mock(stdout=conf["worker_build"] + "\n")
        if args[1] == "version":
            return Mock(stdout="27.5.1\n")
        if args[1:3] == ["compose", "version"]:
            return Mock(stdout="2.32.4\n")
        return Mock(stdout="")

    bootstrap.activate(
        conf,
        {"PHOTO_PROCESSING_FLEET_TOKEN": "fleet-token", "IMAGE_PULL_AUTH": "dXNlcjpwYXNz"},
        "instance-1",
        root=tmp_path,
        run=run,
    )
    assert any("up" in args for args in calls)
    assert ["systemctl", "enable", "--now", "findme-worker-retire.timer"] in calls
    assert ["systemctl", "enable", "--now", "findme-worker-telemetry.timer"] not in calls
    assert capsys.readouterr().out == "worker_telemetry_setup_unavailable\n"


@pytest.mark.parametrize("value", ["True", 1, None, {}, []])
def test_telemetry_opt_in_rejects_non_boolean_configuration(value):
    with pytest.raises(ValueError):
        module("provision").prepare(config() | {"telemetry_enabled": value})
