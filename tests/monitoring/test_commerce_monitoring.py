import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def collector():
    sys.path.insert(0, str(ROOT / "scripts"))
    return load("monitor_commerce", ROOT / "scripts/monitor_commerce.py")


@pytest.mark.parametrize("alive,age", [(False, 700), (True, 0)])
def test_valid_observation_publishes_actual_state(collector, alive, age):
    writes = []
    config = collector.Config("folder", "/srv/findme")
    result = collector.run(
        config,
        observe=lambda config: json.dumps({"worker_alive": alive, "oldest_ready_age_seconds": age}),
        metric_writer=lambda config, metrics: writes.append(metrics),
        emit=lambda message: None,
    )
    assert result == 0
    assert writes == [
        [
            {
                "name": "commerce_worker_alive",
                "labels": {"check": "canonical-commerce"},
                "value": float(alive),
                "type": "DGAUGE",
            },
            {
                "name": "commerce_oldest_ready_age_seconds",
                "labels": {"check": "canonical-commerce"},
                "value": float(age),
                "type": "DGAUGE",
            },
        ]
    ]


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        "garbage",
        '{"worker_alive":1,"oldest_ready_age_seconds":0}',
        '{"worker_alive":true,"oldest_ready_age_seconds":-1}',
        '{"worker_alive":true,"oldest_ready_age_seconds":NaN}',
        '{"worker_alive":true,"oldest_ready_age_seconds":false}',
    ],
)
def test_invalid_observation_never_writes(collector, payload):
    output = []
    assert (
        collector.run(
            collector.Config("folder", "/srv/findme"),
            observe=lambda config: payload,
            metric_writer=lambda *_: pytest.fail("must not write"),
            emit=output.append,
        )
        == 1
    )
    assert output == ["commerce observation failed"]


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired("secret", 30),
        subprocess.CalledProcessError(1, "secret"),
        OSError("secret"),
    ],
)
def test_observation_transport_failure_is_safe(collector, error):
    def fail(config):
        raise error

    output = []
    assert (
        collector.run(
            collector.Config("folder", "/srv/findme"),
            observe=fail,
            metric_writer=lambda *_: pytest.fail("must not write"),
            emit=output.append,
        )
        == 1
    )
    assert output == ["commerce observation failed"]


def test_docker_observation_has_bound_and_uses_web(collector, monkeypatch):
    calls = []

    def execute(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0, '{"worker_alive":false,"oldest_ready_age_seconds":0}'
        )

    monkeypatch.setattr(collector.subprocess, "run", execute)
    collector.observe(collector.Config("folder", "/srv/findme"))
    command, options = calls[0]
    assert command[-10:] == [
        "exec",
        "-T",
        "web",
        "python",
        "manage.py",
        "commerce_worker_health",
        "--max-ready-age-seconds",
        "300",
        "--format",
        "json",
    ]
    assert options["timeout"] == 30
    assert options["stderr"] == subprocess.DEVNULL


def test_write_failure_never_logs_secret(collector):
    def fail(*args):
        raise RuntimeError("Bearer secret")

    output = []
    assert (
        collector.run(
            collector.Config("folder", "/srv/findme"),
            observe=lambda config: '{"worker_alive":true,"oldest_ready_age_seconds":0}',
            metric_writer=fail,
            emit=output.append,
        )
        == 1
    )
    assert output == ["commerce metrics write failed"]


@pytest.fixture
def installer():
    return load("commerce_install", ROOT / "deploy/monitoring/commerce-vm/install.py")


def test_installer_sets_permissions_interval_and_explicit_activation(
    installer, tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(installer, "systemctl", lambda *args: calls.append(args))
    monkeypatch.setattr(installer, "state", lambda *args: False)
    installer.install(ROOT, "folder", "/srv/findme", root=tmp_path)
    base = tmp_path / "usr/local/lib/findme-commerce-monitoring"
    assert base.stat().st_mode & 0o777 == 0o755
    assert (base / "config.json").stat().st_mode & 0o777 == 0o600
    assert (base / "monitor_commerce.py").stat().st_mode & 0o777 == 0o644
    units = tmp_path / "etc/systemd/system"
    assert "OnUnitActiveSec=60s" in (units / installer.TIMER).read_text()
    assert "User=root" in (units / installer.SERVICE).read_text()
    assert ("enable", "--now", installer.TIMER) in calls
    assert ("stop", installer.TIMER) not in calls
    assert ("stop", installer.SERVICE) not in calls


def test_failed_install_restores_prior_files_and_disabled_state(installer, tmp_path, monkeypatch):
    base = tmp_path / "usr/local/lib/findme-commerce-monitoring"
    base.mkdir(parents=True)
    old = base / "config.json"
    old.write_text("old-config")
    old.chmod(0o640)
    calls = []

    def execute(*args):
        calls.append(args)
        if args == ("start", installer.SERVICE):
            raise subprocess.CalledProcessError(1, "secret")

    monkeypatch.setattr(installer, "systemctl", execute)
    monkeypatch.setattr(installer, "state", lambda *args: False)
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(ROOT, "folder", "/srv/findme", root=tmp_path)
    assert old.read_text() == "old-config"
    assert old.stat().st_mode & 0o777 == 0o640
    assert not (base / "monitor_commerce.py").exists()
    assert not (tmp_path / "etc/systemd/system" / installer.TIMER).exists()
    assert ("disable", installer.TIMER) in calls


def test_installer_requires_root(installer, monkeypatch):
    monkeypatch.setattr(installer.os, "geteuid", lambda: 1000)
    with pytest.raises(SystemExit):
        installer.main(["--folder-id", "folder", "--deploy-root", "/srv/findme"])


@pytest.mark.parametrize("alive,age", [(False, None), (True, 700)])
def test_management_json_records_unhealthy_state_without_failing(alive, age):
    from datetime import timedelta
    from io import StringIO
    from unittest.mock import patch

    from commerce.worker import CommerceWorkerHealth
    from django.core.management import call_command

    output = StringIO()
    health = CommerceWorkerHealth(
        worker_alive=alive,
        oldest_ready_work_type=None,
        oldest_ready_age=timedelta(seconds=age) if age is not None else None,
        healthy=False,
    )
    with patch(
        "commerce.management.commands.commerce_worker_health.commerce_worker_health",
        return_value=health,
    ):
        call_command(
            "commerce_worker_health",
            "--max-ready-age-seconds",
            "300",
            "--format",
            "json",
            stdout=output,
            skip_checks=True,
        )
    assert json.loads(output.getvalue()) == {
        "worker_alive": alive,
        "oldest_ready_age_seconds": age or 0,
    }


def test_installer_operates_from_bounded_transport_bundle(installer, tmp_path, monkeypatch):
    import shutil

    bundle = tmp_path / "bundle"
    (bundle / "scripts").mkdir(parents=True)
    for name in ("monitor_commerce.py", "monitor_public_health.py"):
        shutil.copyfile(ROOT / "scripts" / name, bundle / "scripts" / name)
    shutil.copytree(
        ROOT / "deploy/monitoring/commerce-vm", bundle / "deploy/monitoring/commerce-vm"
    )
    monkeypatch.setattr(installer, "systemctl", lambda *args: None)
    monkeypatch.setattr(installer, "state", lambda *args: False)
    installer.install(bundle, "folder", "/opt/photo-prjct", root=tmp_path / "host")
    assert (tmp_path / "host/usr/local/lib/findme-commerce-monitoring/monitor_commerce.py").exists()


def test_rollback_restores_active_timer_even_when_disable_fails(installer, tmp_path, monkeypatch):
    base = tmp_path / "usr/local/lib/findme-commerce-monitoring"
    base.mkdir(parents=True)
    (base / "config.json").write_text("prior")
    calls = []

    def execute(*args):
        calls.append(args)
        if args in (("start", installer.SERVICE), ("disable", installer.TIMER)):
            raise subprocess.CalledProcessError(1, "secret")

    monkeypatch.setattr(installer, "systemctl", execute)
    monkeypatch.setattr(installer, "state", lambda operation, unit: unit == installer.TIMER)
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(ROOT, "folder", "/opt/photo-prjct", root=tmp_path)
    assert (base / "config.json").read_text() == "prior"
    assert ("enable", installer.TIMER) in calls
    assert ("start", installer.TIMER) in calls
