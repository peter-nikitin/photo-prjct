import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.operational
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy/worker-pools"))


def module(name="activate"):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"deploy/worker-pools/{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def cloud():
    reader = Mock()
    reader.get.side_effect = lambda path: (
        {
            "folderId": "folder",
            "labels": {
                "project": "findme-photo",
                "deployment": "canonical",
                "managed-by": "worker-pools",
                "pool": path.split("/")[-1],
            },
        }
        if path.startswith("instanceGroups/")
        else {
            "status": "RUNNING",
            "folderId": "folder",
            "networkInterfaces": [
                {
                    "subnetId": "subnet",
                    "securityGroupIds": ["sg"],
                    "primaryV4Address": {"address": "10.0.0.9"},
                }
            ],
        }
    )
    reader.pages.side_effect = lambda path, key: (
        [] if "/bulk/" in path else [{"instanceId": "vm", "status": "RUNNING_ACTUAL"}]
    )
    return reader


def test_zero_bulk_and_running_selfie_are_discovered_without_mutation():
    assert module("discovery").discover(
        cloud(),
        folder="folder",
        groups={"bulk": "bulk", "selfie": "selfie"},
        subnet="subnet",
        security_group="sg",
    ) == ["10.0.0.9"]


def test_public_worker_is_rejected():
    reader = cloud()
    original = reader.get.side_effect
    reader.get.side_effect = lambda path: (
        original(path)
        | (
            {
                "networkInterfaces": [
                    {
                        "subnetId": "subnet",
                        "securityGroupIds": ["sg"],
                        "primaryV4Address": {"address": "10.0.0.9", "oneToOneNat": {}},
                    }
                ]
            }
            if path.startswith("instances/")
            else {}
        )
    )
    with pytest.raises(ValueError, match="private worker"):
        module("discovery").discover(
            reader,
            folder="folder",
            groups={"bulk": "bulk", "selfie": "selfie"},
            subnet="subnet",
            security_group="sg",
        )


@pytest.mark.parametrize("failure", [255, 1])
def test_connection_or_warmup_failure_fails_activation(failure):
    run = Mock(side_effect=subprocess.CalledProcessError(failure, "ssh"))
    with pytest.raises(subprocess.CalledProcessError):
        module().activate(["10.0.0.9"], ssh_config=Path("/tmp/config"), user="deploy", run=run)


def test_each_approved_member_receives_one_activation():
    run = Mock()
    module().activate(
        ["10.0.0.9", "10.0.0.10"], ssh_config=Path("/tmp/config"), user="deploy", run=run
    )
    assert run.call_count == 2
    assert all(
        call.args[0][-1]
        == "sudo -n /usr/bin/python3 /usr/local/lib/findme-worker/updater.py --ci-activation"
        for call in run.call_args_list
    )


def test_ci_runner_reuses_projected_key_and_pins_ephemeral_keys(tmp_path):
    key = tmp_path / "key"
    key.write_text("private-test-key")
    key.chmod(0o600)
    environment = tmp_path / "projection.env"
    environment.write_text(f'VM_SSH_KEY_FILE="{key}"\n')
    environment.chmod(0o600)
    env = {
        "FINDME_ENV_FILE": str(environment),
        "VM_HOST": "canonical.example",
        "VM_USER": "operator",
        "VM_SSH_KNOWN_HOSTS": "canonical.example ssh-ed25519 canonical-key",
        "WORKER_POOL_FOLDER_ID": "folder",
        "WORKER_POOL_BULK_GROUP_ID": "bulk",
        "WORKER_POOL_SELFIE_GROUP_ID": "selfie",
        "WORKER_POOL_SUBNET_ID": "subnet",
        "WORKER_POOL_SECURITY_GROUP_ID": "sg",
        "WORKER_POOL_CANONICAL_VM_ID": "canonical",
    }
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        config = Path(args[2]).read_text()
        assert str(key) in config
        assert "IdentityFile" in config and "IdentitiesOnly yes" in config
        if args[-1].startswith("python3 - "):
            compile(kwargs["input"], "worker-discovery", "exec")
            assert "metadata_token()" in kwargs["input"]
            return SimpleNamespace(
                stdout='{"addresses":["10.0.0.9"],"host_keys":"10.0.0.9 ssh-ed25519 worker-key\\n"}'
            )
        assert (
            "10.0.0.9 ssh-ed25519 worker-key" in Path(args[2]).with_name("known_hosts").read_text()
        )
        return SimpleNamespace(stdout="")

    module().run_ci(env, run=run)
    assert len(calls) == 2
    assert "findme-worker-bastion" in calls[1]


@pytest.mark.parametrize("source", ["0.0.0.0/0", "10.0.0.8/32"])
def test_worker_ssh_ingress_rejects_noncanonical_source(source):
    group = {
        "rules": [
            {"direction": "EGRESS"},
            {
                "direction": "INGRESS",
                "protocolName": "TCP",
                "ports": {"fromPort": "22", "toPort": "22"},
                "cidrBlocks": {"v4CidrBlocks": [source]},
            },
        ]
    }
    canonical = {"networkInterfaces": [{"securityGroupIds": ["canonical-sg"]}]}
    with pytest.raises(ValueError, match="canonical SSH"):
        module("discovery").validate_ingress(group, canonical)


def test_worker_ssh_ingress_accepts_only_attached_canonical_security_group():
    canonical = {"networkInterfaces": [{"securityGroupIds": ["canonical-sg"]}]}
    group = {
        "rules": [
            {"direction": "EGRESS"},
            {
                "direction": "INGRESS",
                "protocolName": "TCP",
                "ports": {"fromPort": "22", "toPort": "22"},
                "securityGroupId": "canonical-sg",
            },
        ]
    }
    module("discovery").validate_ingress(group, canonical)
    group["rules"][1]["securityGroupId"] = "unapproved-sg"
    with pytest.raises(ValueError, match="canonical SSH"):
        module("discovery").validate_ingress(group, canonical)


def test_live_api_tcp_rule_includes_protocol_number():
    canonical = {"networkInterfaces": [{"securityGroupIds": ["canonical-sg"]}]}
    group = {
        "rules": [
            {"direction": "EGRESS"},
            {
                "id": "ssh-rule",
                "description": "CI activation",
                "direction": "INGRESS",
                "protocolName": "TCP",
                "protocolNumber": "6",
                "ports": {"fromPort": "22", "toPort": "22"},
                "securityGroupId": "canonical-sg",
            },
        ]
    }
    module("discovery").validate_ingress(group, canonical)


def test_missing_warm_selfie_fails_instead_of_successful_empty_release():
    reader = cloud()
    reader.pages.side_effect = lambda path, key: []
    with pytest.raises(ValueError, match="one running selfie"):
        module("discovery").discover(
            reader,
            folder="folder",
            groups={"bulk": "bulk", "selfie": "selfie"},
            subnet="subnet",
            security_group="sg",
        )


def test_workflow_worker_only_release_activates_and_restores_worker_pointer():
    text = (ROOT / ".github/workflows/deploy.yml").read_text()
    assert "Run photo-worker activation" in text
    assert "-- deploy/worker-pools/activate.py" in text
    assert "PREVIOUS_WORKER_IMAGE: ${{ needs.build.outputs.previous_worker_image }}" in text
