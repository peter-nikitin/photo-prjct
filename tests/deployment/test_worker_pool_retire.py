import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

pytestmark = pytest.mark.operational
ROOT = Path(__file__).resolve().parents[2]


def helper():
    spec = importlib.util.spec_from_file_location(
        "retire_worker_host", ROOT / "deploy/worker-pools/retire.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retirement_dry_run_performs_no_stop_or_poweroff():
    module = helper()
    envelope = {
        "pool": "selfie",
        "instance_id": "instance-1",
        "boot_id": str(uuid4()),
        "worker_build": "a" * 40,
    }
    grant = {
        "instance_id": envelope["instance_id"],
        "boot_id": envelope["boot_id"],
        "grant_id": str(uuid4()),
    }
    runner = Mock()
    assert module.retire(envelope, {"grant": grant}, dry_run=True, run=runner) == "would_retire"
    runner.assert_not_called()
    assert module.retire(envelope, {"grant": None}, dry_run=False, run=runner) == "serving"
    runner.assert_not_called()


def test_grant_for_old_boot_is_rejected_before_any_host_action():
    module = helper()
    envelope = {
        "pool": "bulk",
        "instance_id": "instance-1",
        "boot_id": str(uuid4()),
        "worker_build": "a" * 40,
    }
    runner = Mock()
    for grant in (
        {"instance_id": "other", "boot_id": envelope["boot_id"], "grant_id": str(uuid4())},
        {"instance_id": "instance-1", "boot_id": str(uuid4()), "grant_id": str(uuid4())},
        {"instance_id": "instance-1", "boot_id": envelope["boot_id"], "grant_id": "bad"},
    ):
        with pytest.raises(ValueError):
            module.retire(envelope, {"grant": grant}, dry_run=False, run=runner)
    runner.assert_not_called()


def test_helper_stops_only_named_container_then_own_host_and_stop_failure_aborts(monkeypatch):
    module = helper()
    monkeypatch.setattr(module, "active_slot", lambda: {"slot": "b"})
    envelope = {
        "pool": "bulk",
        "instance_id": "instance-1",
        "boot_id": str(uuid4()),
        "worker_build": "a" * 40,
    }
    reply = {
        "grant": {
            "instance_id": "instance-1",
            "boot_id": envelope["boot_id"],
            "grant_id": str(uuid4()),
        }
    }
    runner = Mock()
    assert module.retire(envelope, reply, dry_run=False, run=runner) == "retiring"
    assert [call.args[0] for call in runner.call_args_list] == [
        ["docker", "stop", "--time", "930", "findme-photo-worker-b"],
        ["systemctl", "poweroff"],
    ]
    runner = Mock(side_effect=subprocess.CalledProcessError(1, ["docker", "stop"]))
    with pytest.raises(subprocess.CalledProcessError):
        module.retire(envelope, reply, dry_run=False, run=runner)
    assert runner.call_count == 1
