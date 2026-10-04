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


def config(pool_max_size=2):
    return {
        "pool_max_size": pool_max_size,
        "cloud_id": "cloud",
        "folder_id": "worker-folder",
        "canonical_folder_id": "canonical-folder",
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


@pytest.mark.parametrize("pool_max_size", [1, 2])
def test_dry_run_is_deterministic_secretless_and_has_bounded_shapes(pool_max_size):
    provision = module("provision")
    plan = provision.prepare(config(pool_max_size))
    assert plan == provision.prepare(config(pool_max_size))
    assert plan["configuration"]["pool_max_size"] == pool_max_size
    assert set(plan["groups"]) == {"bulk", "selfie"}
    for pool, body in plan["groups"].items():
        assert body["folderId"] == "worker-folder"
        assert body["name"] == f"findme-photo-worker-{pool}"
        assert body["instanceTemplate"]["resourcesSpec"] == {
            "cores": "2",
            "coreFraction": "100",
            "memory": "8589934592",
        }
        assert body["instanceTemplate"]["bootDiskSpec"]["diskSpec"]["size"] == "34359738368"
        assert body["instanceTemplate"]["platformId"] == "standard-v3"
        assert body["instanceTemplate"]["networkInterfaceSpecs"] == [
            {
                "networkId": "network",
                "subnetIds": ["subnet"],
                "primaryV4AddressSpec": {},
                "securityGroupIds": ["worker-sg"],
            }
        ]
        assert body["instanceTemplate"]["schedulingPolicy"]["preemptible"] is (pool == "bulk")
        assert body["scalePolicy"] == {
            "autoScale": {
                "minZoneSize": "0" if pool == "bulk" else "1",
                "maxSize": "1" if pool_max_size == 1 else "2",
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
                        "labels": {"pool": pool, "zone_id": "ru-central1-a"},
                        "target": "1",
                        "folderId": "canonical-folder",
                        "service": "custom",
                    }
                ],
            }
        }
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
        bootstrap = json.loads(
            next(
                base64.b64decode(entry["content"])
                for entry in yaml.safe_load(user_data)["write_files"]
                if entry["path"] == "/etc/findme-worker/bootstrap.json"
            )
        )
        assert set(bootstrap) == {
            "bootstrap_secret_id",
            "bootstrap_version_id",
            "worker_image",
            "docker_version",
            "compose_version",
            "private_api_ipv4",
            "pool",
            "identities",
            "zone",
            "telemetry_enabled",
        }
        assert bootstrap["private_api_ipv4"] == "10.0.0.5"
        assert bootstrap["bootstrap_secret_id"] == "worker-secret"
        assert "boot-image" in json.dumps(body)


@pytest.mark.parametrize(
    "pool_max_size", [None, "", "1", "2", 0, 3, -1, True, False, 1.0, 2.0, {}, []]
)
def test_configuration_rejects_invalid_or_non_integer_pool_max_size(pool_max_size):
    with pytest.raises(ValueError):
        module("provision").prepare(config(pool_max_size))


def test_configuration_requires_pool_max_size_without_legacy_default():
    conf = config()
    del conf["pool_max_size"]
    with pytest.raises(ValueError):
        module("provision").prepare(conf)


@pytest.mark.parametrize(
    "mutation",
    [
        {"canonical_folder_id": None},
        {"canonical_folder_id": "worker-folder"},
        {"folder_id": "canonical-folder"},
    ],
)
def test_distinct_explicit_folders_are_required(mutation):
    with pytest.raises(ValueError):
        module("provision").prepare(config() | mutation)
    conf = config()
    del conf["canonical_folder_id"]
    with pytest.raises(ValueError):
        module("provision").prepare(conf)


@pytest.mark.parametrize("field", ["folder_id", "canonical_folder_id"])
def test_either_folder_changes_checksum_and_stale_apply_is_rejected(field, tmp_path):
    provision = module("provision")
    original = config()
    changed = config() | {field: "other-folder"}
    assert provision.prepare(original)["checksum"] != provision.prepare(changed)["checksum"]
    cloud = FakeCloud(provision, changed)
    with pytest.raises(ValueError, match="reviewed checksum mismatch"):
        provision.apply(
            changed,
            provision.prepare(original)["checksum"],
            cloud=cloud,
            receipt_path=tmp_path / "receipt.json",
        )
    assert cloud.calls == []
    assert not (tmp_path / "receipt.json").exists()


def test_swapped_folder_inputs_fail_inspection_before_group_mutation(tmp_path):
    provision = module("provision")
    conf = config() | {"folder_id": "canonical-folder", "canonical_folder_id": "worker-folder"}
    cloud = FakeCloud(provision, conf)
    with pytest.raises(ValueError):
        provision.apply(
            conf,
            provision.prepare(conf)["checksum"],
            cloud=cloud,
            receipt_path=tmp_path / "receipt.json",
        )
    assert cloud.calls == []
    assert not (tmp_path / "receipt.json").exists()


def test_pool_ceiling_changes_reviewed_checksum_and_rejects_stale_apply(tmp_path):
    provision = module("provision")
    cap_two = provision.prepare(config(2))
    cap_one = provision.prepare(config(1))
    assert cap_one["checksum"] != cap_two["checksum"]
    assert cap_one["checksum"] == provision.prepare(config(1))["checksum"]
    cloud = FakeCloud(provision, config(1))
    with pytest.raises(ValueError, match="reviewed checksum mismatch"):
        provision.apply(
            config(1),
            cap_two["checksum"],
            cloud=cloud,
            receipt_path=tmp_path / "receipt.json",
        )
    assert cloud.calls == []
    assert not (tmp_path / "receipt.json").exists()


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
    slot_env = tmp_path / "slot.env"
    slot_env.write_text(
        "WORKER_IMAGE=ghcr.io/example/photo-prjct-worker@sha256:"
        + "b" * 64
        + "\nPHOTO_WORKER_BUILD="
        + "b" * 40
        + "\nWORKER_SLOT=b\nWORKER_PORT=9102\n"
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(tmp_path / "etc/findme-worker/runtime.env"),
            "--env-file",
            str(slot_env),
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
    assert worker["image"] == "ghcr.io/example/photo-prjct-worker@sha256:" + "b" * 64
    assert worker["container_name"] == "findme-photo-worker-b"
    assert worker["ports"][0]["host_ip"] == "127.0.0.1"
    assert worker["ports"][0]["published"] == "9102"
    for key, value in worker["environment"].items():
        monkeypatch.setenv(key, str(value))
    actual, _client = WorkerConfig.from_env()
    assert actual.worker_build == "b" * 40
    assert actual.remote_pool == "selfie"


@pytest.mark.parametrize("pool_max_size", [1, 2])
def test_configuration_rejects_unknown_targets_shared_secret_public_api_and_unknown_inputs(
    pool_max_size,
):
    provision = module("provision")
    for mutation in (
        {"extra": "secret"},
        {"capacity_mode": "fixed"},
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
        value = config(pool_max_size) | mutation
        with pytest.raises(ValueError):
            provision.prepare(value)


def test_checksum_mismatch_cannot_make_cloud_calls():
    provision = module("provision")
    cloud = Mock()
    with pytest.raises(ValueError):
        provision.apply(config(), "0" * 64, cloud=cloud)
    cloud.assert_not_called()


def updater_install_fixture():
    provision = module("provision")
    conf = config(1)
    cloud = FakeCloud(provision, conf)
    cloud.groups = [
        body | {"id": f"{pool}-group", "status": "ACTIVE"}
        for pool, body in provision.prepare(conf)["groups"].items()
    ]
    for group in cloud.groups:
        group["instanceTemplate"]["metadata"]["ssh-keys"] = "preserved-nonsecret-key"
    readback = provision.status(conf, cloud)
    conf["groups"] = {
        pool: {"id": row["id"], "baseline": row["baseline"]} for pool, row in readback.items()
    }
    return provision, conf, cloud


def test_one_time_updater_install_only_patches_userdata_and_keeps_bulk_zero():
    provision, conf, cloud = updater_install_fixture()
    before = deepcopy(cloud.groups)
    result = provision.install_updater(conf, cloud=cloud)
    assert set(result) == {"bulk", "selfie"}
    assert len(cloud.calls) == 2
    for original, current, call in zip(before, cloud.groups, cloud.calls, strict=True):
        assert call[0] == "PATCH"
        assert call[2]["updateMask"] == "instanceTemplate.metadata"
        original["instanceTemplate"]["metadata"]["user-data"] = current["instanceTemplate"][
            "metadata"
        ]["user-data"]
        assert current == original
        assert current["scalePolicy"]["autoScale"]["maxSize"] == "1"
    assert cloud.groups[0]["scalePolicy"]["autoScale"]["minZoneSize"] == "0"


def test_one_time_updater_install_rejects_non_cap_one_before_mutation():
    provision, conf, cloud = updater_install_fixture()
    cloud.groups[1]["scalePolicy"]["autoScale"]["maxSize"] = "2"
    conf["groups"]["selfie"]["baseline"] = provision.managed_baseline(cloud.groups[1])
    with pytest.raises(ValueError, match="cap one"):
        provision.install_updater(conf, cloud=cloud)
    assert cloud.calls == []


def test_one_time_recreate_uses_exact_managed_id_and_never_starts_empty_bulk(monkeypatch):
    provision, conf, cloud = updater_install_fixture()
    monkeypatch.setattr(
        cloud,
        "pages",
        lambda path, key, **kwargs: (
            deepcopy(cloud.groups)
            if path == "instanceGroups"
            else [
                {"id": "managed-selfie-1", "instanceId": "compute-vm-1", "status": "RUNNING_ACTUAL"}
            ]
            if path == "instanceGroups/selfie-group/instances"
            else []
        ),
    )
    calls = []
    monkeypatch.setattr(
        cloud,
        "mutate",
        lambda method, path, body: (
            calls.append((method, path, body))
            or {"id": "recreate-op", "done": False, "metadata": {"instanceGroupId": "selfie-group"}}
        ),
    )
    result = provision.replace_selfie(conf, "managed-selfie-1", cloud=cloud)
    assert calls == [
        (
            "POST",
            "instanceGroups/selfie-group:rollingRecreate",
            {"managedInstanceIds": ["managed-selfie-1"]},
        )
    ]
    assert result["operation_id"] == "recreate-op"
    assert result["operation_done"] is False
    with pytest.raises(ValueError):
        provision.replace_selfie(conf, "compute-vm-1", cloud=cloud)
    assert len(calls) == 1


def test_one_time_recreate_does_not_retry_uncertain_submission(monkeypatch):
    provision, conf, cloud = updater_install_fixture()
    monkeypatch.setattr(
        cloud,
        "pages",
        lambda path, key, **kwargs: (
            deepcopy(cloud.groups)
            if path == "instanceGroups"
            else [
                {"id": "managed-selfie-1", "instanceId": "compute-vm-1", "status": "RUNNING_ACTUAL"}
            ]
        ),
    )
    calls = []

    def uncertain(method, path, body):
        calls.append(path)
        raise TimeoutError("response lost")

    monkeypatch.setattr(cloud, "mutate", uncertain)
    with pytest.raises(TimeoutError):
        provision.replace_selfie(conf, "managed-selfie-1", cloud=cloud)
    assert calls == ["instanceGroups/selfie-group:rollingRecreate"]


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


def test_bootstrap_materializes_private_credentials_and_invokes_bounded_host_updater(tmp_path):
    bootstrap = module("bootstrap")
    conf = config() | {
        "pool": "bulk",
        "identities": "1/capture_metadata/2",
        "worker_image": "ghcr.io/example/photo-prjct-worker:latest",
    }
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
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
    base = tmp_path / "etc/findme-worker"
    assert (base / "runtime.env").stat().st_mode & 0o777 == 0o600
    assert (base / "docker/config.json").stat().st_mode & 0o777 == 0o600
    assert (base / "instance-id").stat().st_mode & 0o777 == 0o644
    assert (base / "instance-id").read_text() == "instance-1\n"
    assert not any("fleet-token" in " ".join(args) for args, _ in calls)
    update = next(
        kwargs
        for args, kwargs in calls
        if args == ["systemctl", "start", "findme-worker-updater.service"]
    )
    assert update["timeout"] == 2700
    assert all(kwargs["timeout"] == 300 for args, kwargs in calls if "start" not in args)


@pytest.mark.parametrize(
    ("failure", "phase", "category"),
    [
        ("update-timeout", "image-update", "timeout"),
        ("update-exit", "image-update", "command-exit"),
    ],
)
def test_bootstrap_reports_safe_docker_failure_phase_and_category(
    tmp_path, monkeypatch, capsys, failure, phase, category
):
    bootstrap = module("bootstrap")
    secret = "private-credential-must-not-appear"
    conf = config() | {
        "pool": "bulk",
        "identities": "1/capture_metadata/2",
        "worker_image": "ghcr.io/example/photo-prjct-worker:latest",
    }
    config_path = tmp_path / "bootstrap.json"
    config_path.write_text(json.dumps(conf))
    monkeypatch.setattr(bootstrap, "metadata_token", lambda: "metadata-token")
    monkeypatch.setattr(
        bootstrap,
        "request_json",
        lambda request: {
            "versionId": "secret-version",
            "entries": [
                {"key": "PHOTO_PROCESSING_FLEET_TOKEN", "textValue": "fleet-token"},
                {"key": "IMAGE_PULL_AUTH", "textValue": "dXNlcjpwYXNz"},
            ],
        },
    )
    monkeypatch.setattr(bootstrap, "request_bytes", lambda request, **kwargs: b"instance-1")

    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args == ["systemctl", "start", "findme-worker-updater.service"]:
            if failure == "update-timeout":
                raise subprocess.TimeoutExpired(args, 2700, output=secret, stderr=secret)
            raise subprocess.CalledProcessError(1, args, output=secret, stderr=secret)
        if args[1] == "version":
            return Mock(stdout="27.5.1\n")
        if args[1:3] == ["compose", "version"]:
            return Mock(stdout="2.32.4\n")
        return Mock(stdout="")

    real_activate = bootstrap.activate
    monkeypatch.setattr(
        bootstrap,
        "activate",
        lambda cfg, values, instance, **kwargs: real_activate(
            cfg, values, instance, root=tmp_path, run=run, **kwargs
        ),
    )
    monkeypatch.setattr("sys.argv", ["bootstrap.py", "--config", str(config_path)])

    assert bootstrap.main() == 1
    output = capsys.readouterr()
    assert output.out == f"worker bootstrap failed phase={phase} category={category}\n"
    assert output.err == ""
    assert secret not in output.out + output.err
    assert not any("enable" in args for args, _kwargs in calls)


@pytest.mark.parametrize(
    ("failure", "phase", "category"),
    [
        ("metadata", "metadata-token", "io-error"),
        ("lockbox-request", "lockbox-request", "io-error"),
        ("lockbox-validation", "lockbox-validation", "invalid-data"),
    ],
)
def test_bootstrap_reports_safe_pre_activation_failure_phase_and_category(
    tmp_path, monkeypatch, capsys, failure, phase, category
):
    bootstrap = module("bootstrap")
    secret = "private-credential-must-not-appear"
    config_path = tmp_path / "bootstrap.json"
    config_path.write_text(json.dumps(config()))

    def metadata_token():
        if failure == "metadata":
            raise OSError(secret)
        return "metadata-token"

    def request_json(request):
        if failure == "lockbox-request":
            raise OSError(secret)
        return {"versionId": "secret-version", "entries": [{"key": secret, "textValue": secret}]}

    monkeypatch.setattr(bootstrap, "metadata_token", metadata_token)
    monkeypatch.setattr(bootstrap, "request_json", request_json)
    monkeypatch.setattr("sys.argv", ["bootstrap.py", "--config", str(config_path)])

    assert bootstrap.main() == 1
    output = capsys.readouterr()
    assert output.out == f"worker bootstrap failed phase={phase} category={category}\n"
    assert output.err == ""
    assert secret not in output.out + output.err


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
        "folderId": "canonical-folder",
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
        self.resources = {
            "worker-folder": {"id": "worker-folder", "cloudId": "cloud"},
            "canonical-folder": {"id": "canonical-folder", "cloudId": "cloud"},
            "manager-sa": {"id": "manager-sa", "folderId": "worker-folder"},
            "worker-sa": {"id": "worker-sa", "folderId": "worker-folder"},
            "app-secret": {"id": "app-secret", "folderId": "canonical-folder"},
            "worker-secret": {
                "id": "worker-secret",
                "folderId": "worker-folder",
                "status": "ACTIVE",
                "currentVersion": {
                    "id": "secret-version",
                    "payloadEntryKeys": ["PHOTO_PROCESSING_FLEET_TOKEN", "IMAGE_PULL_AUTH"],
                },
            },
            "subnet": {
                "folderId": "worker-folder",
                "zoneId": "ru-central1-a",
                "networkId": "network",
                "routeTableId": "routes",
            },
            "routes": {
                "folderId": "worker-folder",
                "networkId": "network",
                "staticRoutes": [{"destinationPrefix": "0.0.0.0/0", "gatewayId": "gateway"}],
            },
            "gateway": {"folderId": "worker-folder", "sharedEgressGateway": {}},
            "network": {
                "id": "network",
                "folderId": "canonical-folder",
                "defaultSecurityGroupId": "edge-sg",
            },
            "worker-sg": {
                "folderId": "worker-folder",
                "networkId": "network",
                "rules": [{"direction": "EGRESS"}],
            },
            "edge-sg": {
                "folderId": "canonical-folder",
                "networkId": "network",
                "rules": [
                    {
                        "direction": "INGRESS",
                        "protocolName": "TCP",
                        "ports": {"fromPort": "8443", "toPort": "8443"},
                        "securityGroupId": "worker-sg",
                    }
                ],
            },
        }
        self.grants = {
            "worker-folder": [
                {
                    "roleId": "compute.editor",
                    "subject": {"id": "manager-sa", "type": "serviceAccount"},
                },
            ],
            "canonical-folder": [
                {"roleId": "vpc.user", "subject": {"id": "manager-sa", "type": "serviceAccount"}},
                {
                    "roleId": "monitoring.viewer",
                    "subject": {"id": "manager-sa", "type": "serviceAccount"},
                },
            ],
            "worker-secret": [
                {
                    "roleId": "lockbox.payloadViewer",
                    "subject": {"id": "worker-sa", "type": "serviceAccount"},
                },
            ],
        }
        self.computes = {
            "instances/canonical": {
                "id": "canonical",
                "folderId": "canonical-folder",
                "bootDisk": {"diskId": "canonical-boot-disk"},
                "secondaryDisks": [{"diskId": "canonical-data-disk"}],
                "networkInterfaces": [
                    {
                        "securityGroupIds": [],
                        "subnetId": "canonical-subnet",
                        "primaryV4Address": {"address": "10.0.0.5"},
                    }
                ],
            },
            "images/boot-image": {
                "id": "boot-image",
                "folderId": "worker-folder",
                "status": "READY",
            },
            "disks/canonical-boot-disk": {
                "id": "canonical-boot-disk",
                "folderId": "canonical-folder",
            },
            "disks/canonical-data-disk": {
                "id": "canonical-data-disk",
                "folderId": "canonical-folder",
            },
        }
        self.resources["canonical-subnet"] = {
            "id": "canonical-subnet",
            "folderId": "canonical-folder",
            "networkId": "network",
        }

    def resource(self, service, collection, resource):
        return deepcopy(self.resources[resource])

    def bindings(self, service, collection, resource):
        return deepcopy(self.grants.get(resource, []))

    def get(self, path, **parameters):
        if path in self.computes:
            return deepcopy(self.computes[path])
        return deepcopy(next(row for row in self.groups if path == "instanceGroups/" + row["id"]))

    def pages(self, path, key, **parameters):
        return deepcopy(self.groups) if path == "instanceGroups" else []

    def mutate(self, method, path, body):
        self.calls.append((method, path, body))
        pool = (
            body["labels"]["pool"]
            if "labels" in body
            else path.split("/")[-1].removesuffix("-group")
        )
        if pool == "selfie" and self.fail_second:
            raise TimeoutError("lost response")
        resource = f"{pool}-group"
        if method == "PATCH":
            target = next(row for row in self.groups if row["id"] == resource)
            for key, value in body.items():
                if key == "updateMask":
                    continue
                if key == "instanceTemplate" and body["updateMask"] == "instanceTemplate.metadata":
                    target[key]["metadata"] = value["metadata"]
                else:
                    target[key] = value
        else:
            self.groups.append(body | {"id": resource, "status": "ACTIVE"})
        return {"id": f"{pool}-operation", "metadata": {"instanceGroupId": resource}}


@pytest.mark.parametrize("resource", ["worker-folder", "canonical-folder"])
def test_inspection_rejects_folder_outside_reviewed_cloud(resource):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.resources[resource]["cloudId"] = "other-cloud"
    with pytest.raises(ValueError):
        provision.inspect(conf, cloud)


@pytest.mark.parametrize(
    "resource",
    [
        "app-secret",
        "worker-secret",
        "network",
        "subnet",
        "routes",
        "gateway",
        "worker-sg",
        "manager-sa",
        "worker-sa",
    ],
)
def test_inspection_rejects_resource_in_wrong_folder(resource):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.resources[resource]["folderId"] = "other-folder"
    with pytest.raises(ValueError):
        provision.inspect(conf, cloud)


@pytest.mark.parametrize("resource", ["instances/canonical", "images/boot-image"])
def test_inspection_rejects_compute_resource_in_wrong_folder(resource):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.computes[resource]["folderId"] = "other-folder"
    with pytest.raises(ValueError):
        provision.inspect(conf, cloud)


@pytest.mark.parametrize("resource", ["canonical-subnet", "edge-sg"])
def test_inspection_rejects_canonical_nic_resource_in_wrong_folder(resource):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.resources[resource]["folderId"] = "other-folder"
    with pytest.raises(ValueError):
        provision.inspect(conf, cloud)


@pytest.mark.parametrize("resource", ["cloud", "worker-folder", "canonical-folder"])
def test_inspection_rejects_runtime_grant_from_either_folder_or_cloud(resource):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.grants.setdefault(resource, []).append(
        {"roleId": "monitoring.editor", "subject": {"id": "worker-sa", "type": "serviceAccount"}}
    )
    with pytest.raises(ValueError):
        provision.inspect(conf, cloud)


@pytest.mark.parametrize("resource", ["cloud", "canonical-folder"])
def test_inspection_rejects_manager_compute_authority_on_canonical_ancestors(resource):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.grants.setdefault(resource, []).append(
        {"roleId": "compute.editor", "subject": {"id": "manager-sa", "type": "serviceAccount"}}
    )
    with pytest.raises(ValueError):
        provision.inspect(conf, cloud)


@pytest.mark.parametrize("identity", ["manager-sa", "worker-sa"])
@pytest.mark.parametrize("role", ["compute.editor", "compute.operator", "editor", "admin"])
def test_direct_canonical_vm_grant_blocks_group_creation(identity, role, tmp_path):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.grants["canonical"] = [
        {"roleId": role, "subject": {"id": identity, "type": "serviceAccount"}}
    ]
    receipt_path = tmp_path / "receipt.json"
    with pytest.raises(ValueError, match="canonical VM"):
        provision.apply(
            conf,
            provision.prepare(conf)["checksum"],
            cloud=cloud,
            receipt_path=receipt_path,
        )
    assert cloud.calls == []
    assert not receipt_path.exists()


@pytest.mark.parametrize("disk_id", ["canonical-boot-disk", "canonical-data-disk"])
@pytest.mark.parametrize("identity", ["manager-sa", "worker-sa"])
def test_direct_attached_disk_grant_blocks_group_creation(disk_id, identity, tmp_path):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.grants[disk_id] = [
        {"roleId": "compute.editor", "subject": {"id": identity, "type": "serviceAccount"}}
    ]
    receipt_path = tmp_path / "receipt.json"
    with pytest.raises(ValueError, match="canonical disk"):
        provision.apply(
            conf,
            provision.prepare(conf)["checksum"],
            cloud=cloud,
            receipt_path=receipt_path,
        )
    assert cloud.calls == []
    assert not receipt_path.exists()


@pytest.mark.parametrize("disk_id", ["canonical-boot-disk", "canonical-data-disk"])
def test_attached_disk_outside_canonical_folder_blocks_group_creation(disk_id, tmp_path):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.computes[f"disks/{disk_id}"]["folderId"] = "other-folder"
    receipt_path = tmp_path / "receipt.json"
    with pytest.raises(ValueError, match="canonical disk"):
        provision.apply(
            conf,
            provision.prepare(conf)["checksum"],
            cloud=cloud,
            receipt_path=receipt_path,
        )
    assert cloud.calls == []
    assert not receipt_path.exists()


def test_missing_canonical_boot_disk_blocks_group_creation(tmp_path):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    del cloud.computes["instances/canonical"]["bootDisk"]
    receipt_path = tmp_path / "receipt.json"
    with pytest.raises(ValueError):
        provision.apply(
            conf,
            provision.prepare(conf)["checksum"],
            cloud=cloud,
            receipt_path=receipt_path,
        )
    assert cloud.calls == []
    assert not receipt_path.exists()


def test_manager_direct_application_secret_grant_blocks_group_creation(tmp_path):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.grants["app-secret"] = [
        {
            "roleId": "lockbox.payloadViewer",
            "subject": {"id": "manager-sa", "type": "serviceAccount"},
        }
    ]
    receipt_path = tmp_path / "receipt.json"
    with pytest.raises(ValueError, match="application secret"):
        provision.apply(
            conf,
            provision.prepare(conf)["checksum"],
            cloud=cloud,
            receipt_path=receipt_path,
        )
    assert cloud.calls == []
    assert not receipt_path.exists()


@pytest.mark.parametrize("resource", ["worker-folder", "canonical-folder"])
def test_inspection_rejects_missing_manager_folder_authority(resource):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.grants[resource] = []
    with pytest.raises(ValueError):
        provision.inspect(conf, cloud)


def test_inspection_requires_only_read_access_to_canonical_monitoring():
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.grants["canonical-folder"] = [cloud.grants["canonical-folder"][0]]
    with pytest.raises(ValueError, match="manager authority"):
        provision.inspect(conf, cloud)
    cloud.grants["canonical-folder"].append(
        {"roleId": "monitoring.editor", "subject": {"id": "manager-sa", "type": "serviceAccount"}}
    )
    with pytest.raises(ValueError, match="manager authority"):
        provision.inspect(conf, cloud)


def test_inspection_rejects_redundant_worker_folder_vpc_grant():
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.grants["worker-folder"].append(
        {"roleId": "vpc.user", "subject": {"id": "manager-sa", "type": "serviceAccount"}}
    )
    with pytest.raises(ValueError):
        provision.inspect(conf, cloud)


def test_cap_one_inspection_and_status_are_read_only_and_policy_drift_blocks_apply(tmp_path):
    provision = module("provision")
    conf = config(1)
    cloud = FakeCloud(provision, conf)
    cloud.groups = [
        body | {"id": f"{pool}-group", "status": "ACTIVE"}
        for pool, body in provision.prepare(conf)["groups"].items()
    ]
    read_back = provision.status(conf, cloud)
    conf["groups"] = {
        pool: {"id": row["id"], "baseline": row["baseline"]} for pool, row in read_back.items()
    }
    assert provision.inspect(conf, cloud) == conf["groups"]
    assert cloud.calls == []
    cloud.groups[0]["scalePolicy"]["autoScale"]["maxSize"] = "2"
    assert provision.status(conf, cloud)["bulk"]["scale_policy"]["autoScale"]["maxSize"] == "2"
    with pytest.raises(ValueError, match="managed target drift"):
        provision.apply(
            conf,
            provision.prepare(conf)["checksum"],
            cloud=cloud,
            receipt_path=tmp_path / "receipt.json",
        )
    assert cloud.calls == []
    assert not (tmp_path / "receipt.json").exists()


def test_partial_update_or_uncertain_response_requires_reconciliation_not_automatic_retry(
    tmp_path,
):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    cloud.groups = [
        body | {"id": f"{pool}-group"} for pool, body in provision.prepare(conf)["groups"].items()
    ]
    conf["groups"] = {
        pool: {"id": f"{pool}-group", "baseline": provision.managed_baseline(cloud.groups[index])}
        for index, pool in enumerate(("bulk", "selfie"))
    }
    cloud.fail_second = True
    path = tmp_path / "receipt.json"
    checksum = provision.prepare(conf)["checksum"]
    with pytest.raises(TimeoutError):
        provision.apply(
            conf,
            checksum,
            cloud=cloud,
            receipt_path=path,
        )
    receipt = json.loads(path.read_text())
    assert receipt["groups"]["bulk"]["state"] == "submitted"
    assert receipt["groups"]["selfie"]["state"] == "submission_uncertain"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        provision.apply(conf, checksum, cloud=cloud, receipt_path=path)
    assert len(cloud.calls) == 2
    assert path.read_bytes() == before
    assert provision.status(conf, cloud)["bulk"]["id"] == "bulk-group"


def test_initial_creation_is_rejected_without_cloud_or_receipt_mutation(tmp_path):
    provision = module("provision")
    conf = config()
    cloud = FakeCloud(provision, conf)
    with pytest.raises(ValueError, match="existing managed group IDs required"):
        provision.apply(
            conf,
            provision.prepare(conf)["checksum"],
            cloud=cloud,
            receipt_path=tmp_path / "receipt.json",
        )
    assert cloud.calls == []
    assert not (tmp_path / "receipt.json").exists()


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
    path.write_text(
        json.dumps(
            {
                "zone": "ru-central1-a",
                "folder_id": "worker-folder",
                "canonical_folder_id": "canonical-folder",
            }
        )
    )
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
    assert runner.call_args_list[1].args[0][-2:] == ["--folder-id", "canonical-folder"]


def test_collector_publication_fault_is_not_reported_as_success(tmp_path):
    collector = module("metrics")
    runner = Mock(side_effect=[Mock(), subprocess.CalledProcessError(1, "publish")])
    path = tmp_path / "worker-pools-observation.json"
    path.write_text(
        json.dumps(
            {
                "zone": "ru-central1-a",
                "folder_id": "worker-folder",
                "canonical_folder_id": "canonical-folder",
            }
        )
    )
    with pytest.raises(subprocess.CalledProcessError):
        collector.collect({"deploy_root": str(tmp_path), "cloud": str(path)}, run=runner)
    assert runner.call_count == 2


@pytest.mark.parametrize(
    "change",
    [
        {"canonical_folder_id": None},
        {"canonical_folder_id": "worker-folder"},
        {"canonical_folder_id": "bad/folder"},
        {"folder_id": "bad/folder"},
    ],
)
def test_collector_rejects_invalid_folder_route_before_subprocess(tmp_path, change):
    collector = module("metrics")
    cloud = {
        "zone": "ru-central1-a",
        "folder_id": "worker-folder",
        "canonical_folder_id": "canonical-folder",
    } | change
    path = tmp_path / "worker-pools-observation.json"
    path.write_text(json.dumps(cloud))
    runner = Mock()

    with pytest.raises(ValueError):
        collector.collect({"deploy_root": str(tmp_path), "cloud": str(path)}, run=runner)
    runner.assert_not_called()


def test_collector_requires_explicit_canonical_folder_before_subprocess(tmp_path):
    collector = module("metrics")
    path = tmp_path / "worker-pools-observation.json"
    path.write_text(json.dumps({"zone": "ru-central1-a", "folder_id": "worker-folder"}))
    runner = Mock()

    with pytest.raises(ValueError):
        collector.collect({"deploy_root": str(tmp_path), "cloud": str(path)}, run=runner)
    runner.assert_not_called()


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
        assert calls.index(["systemctl", "start", "findme-worker-updater.service"]) < calls.index(
            telemetry_calls[0]
        )
        assert ["systemctl", "enable", "--now", "findme-worker-telemetry.timer"] in calls
        telemetry_env = tmp_path / "etc/findme-worker/telemetry.env"
        assert telemetry_env.stat().st_mode & 0o777 == 0o600
        values = dict(line.split("=", 1) for line in telemetry_env.read_text().splitlines())
        assert set(values) == {
            "PHOTO_WORKER_POOL",
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
    assert ["systemctl", "start", "findme-worker-updater.service"] in calls
    assert ["systemctl", "enable", "--now", "findme-worker-retire.timer"] in calls
    assert ["systemctl", "enable", "--now", "findme-worker-telemetry.timer"] not in calls
    assert capsys.readouterr().out == "worker_telemetry_setup_unavailable\n"


@pytest.mark.parametrize("value", ["True", 1, None, {}, []])
def test_telemetry_opt_in_rejects_non_boolean_configuration(value):
    with pytest.raises(ValueError):
        module("provision").prepare(config() | {"telemetry_enabled": value})
