import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "deploy/monitoring/prometheus"


def load(name):
    spec = importlib.util.spec_from_file_location("findme_" + name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_host_exporter_exists():
    assert (HERE / "exporter.py").is_file(), "fresh private host exporter missing"


@pytest.fixture
def exporter():
    if not (HERE / "exporter.py").exists():
        pytest.skip("exporter not implemented")
    return load("exporter")


def test_public_failed_https_exports_observed_zero(exporter):
    probe = exporter.probe

    def failed(config, metric_writer, emit):
        return probe.run_probe(
            config,
            fetch_health=lambda *args: (_ for _ in ()).throw(OSError()),
            metric_writer=metric_writer,
            emit=emit,
        )

    response = exporter.collect("public", public_runner=failed)
    assert response[0] == 200
    assert 'findme_probe_success{check="canonical-health"} 0.0' in response[1]


def test_commerce_failed_observation_returns_error_without_zero(exporter):
    def failed(config, metric_writer, emit):
        return 1

    status, body = exporter.collect("canonical", commerce_runner=failed)
    assert status == 503
    assert "commerce_worker_alive" not in body
    assert "commerce_oldest_ready_age_seconds" not in body


def test_commerce_observations_are_fresh_each_scrape(exporter):
    observed = []

    def run(config, metric_writer, emit):
        return exporter.commerce.run(
            config,
            observe=lambda config: (
                observed.append(1)
                or json.dumps({"worker_alive": True, "oldest_ready_age_seconds": len(observed)})
            ),
            metric_writer=metric_writer,
            emit=emit,
        )

    first = exporter.collect("canonical", commerce_runner=run)
    second = exporter.collect("canonical", commerce_runner=run)
    assert 'seconds{check="canonical-commerce"} 1.0' in first[1]
    assert 'seconds{check="canonical-commerce"} 2.0' in second[1]


def test_exporter_server_loopback_only(exporter):
    server = exporter.create_server("public", port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_agent_addition_keeps_native_routes_and_uses_separate_buffer():
    assert (HERE / "render_agent.py").exists(), "agent renderer missing"
    renderer = load("render_agent")
    native = {
        "routes": [
            {"input": {"plugin": "agent_metrics"}, "channel": {"channel_ref": {"name": "native"}}}
        ],
        "channels": [{"name": "native"}],
        "storages": [{"name": "native"}],
    }
    result = renderer.merge_agent(native, role="canonical", workspace_id="workspace1")
    assert result["routes"][0] == native["routes"][0]
    assert native["routes"] != result["routes"]
    new = result["channels"][-1]
    assert new["channel"]["output"]["config"]["iam"] == {"cloud_meta": {}}
    assert new["channel"]["pipe"][0]["storage_ref"]["name"] != "native"
    assert len(result["routes"]) == 4


def test_agent_reconciliation_is_idempotent():
    if not (HERE / "render_agent.py").exists():
        pytest.skip("renderer not implemented")
    renderer = load("render_agent")
    first = renderer.merge_agent({}, role="public", workspace_id="workspace1")
    assert renderer.merge_agent(first, role="public", workspace_id="workspace1") == first
    updated = renderer.merge_agent(first, role="public", workspace_id="workspace2")
    assert "workspace2" in updated["channels"][0]["channel"]["output"]["config"]["url"]


def test_bounded_installer_exists():
    assert (HERE / "install.py").exists(), "bounded transactional installer missing"


@pytest.fixture
def installer():
    if not (HERE / "install.py").exists():
        pytest.skip("installer not implemented")
    return load("install")


class Commands:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    def __call__(self, arguments):
        import subprocess

        self.calls.append(arguments)
        code, output = 0, ""
        if arguments[0] == "/usr/bin/unified_agent" and "--svnrevision" in arguments:
            output = "26.09.10/21106904"
        elif "is-enabled" in arguments or "is-active" in arguments:
            code = 0 if arguments[-1] == "unified_agent.service" else 1
        if self.failure and self.failure(arguments):
            code = 1
            self.failure = None
        return subprocess.CompletedProcess(arguments, code, output, "")


def setup_install(installer, tmp_path):
    config = tmp_path / "etc/yc/unified_agent/config.yml"
    config.parent.mkdir(parents=True)
    config.write_text("routes: []\n")
    rendered = tmp_path / "rendered.yml"
    rendered.write_text("routes: [new]\n")
    return config, rendered, installer.sha256(config.read_bytes())


@pytest.mark.parametrize("phase", ["daemon-reload", "enable", "restart"])
def test_install_failure_rolls_back_files_and_native_service(installer, tmp_path, phase):
    config, rendered, digest = setup_install(installer, tmp_path)
    commands = Commands(lambda args: phase in args)
    with pytest.raises(installer.InstallError, match="rolled back"):
        installer.install(
            ROOT,
            rendered,
            role="public",
            expected_current_sha256=digest,
            expected_source_sha256=installer.source_hash(ROOT),
            expected_rendered_sha256=installer.sha256(rendered.read_bytes()),
            expected_instance_id="vm1",
            revision="a" * 40,
            root=tmp_path,
            runner=commands,
            instance_id=lambda: "vm1",
        )
    assert config.read_text() == "routes: []\n"
    assert not (tmp_path / "etc/systemd/system/findme-prometheus-public.service").exists()
    assert not (tmp_path / "usr/local/lib/findme-prometheus/exporter.py").exists()
    assert any("unified_agent.service" in args and "restart" in args for args in commands.calls)
    assert not any("docker" in args or "web" in args for args in commands.calls)


def test_installer_guards_identity_and_config_hash_before_mutation(installer, tmp_path):
    config, rendered, digest = setup_install(installer, tmp_path)
    commands = Commands()
    with pytest.raises(installer.InstallError, match="instance"):
        installer.install(
            ROOT,
            rendered,
            role="canonical",
            expected_current_sha256=digest,
            expected_source_sha256=installer.source_hash(ROOT),
            expected_rendered_sha256=installer.sha256(rendered.read_bytes()),
            expected_instance_id="different",
            revision="a" * 40,
            root=tmp_path,
            runner=commands,
            instance_id=lambda: "vm1",
        )
    assert config.read_text() == "routes: []\n"
    assert commands.calls == []


def test_installer_validates_agent_before_changes(installer, tmp_path):
    config, rendered, digest = setup_install(installer, tmp_path)
    commands = Commands(lambda args: "check-config" in args)
    with pytest.raises(installer.InstallError, match="validation"):
        installer.install(
            ROOT,
            rendered,
            role="canonical",
            expected_current_sha256=digest,
            expected_source_sha256=installer.source_hash(ROOT),
            expected_rendered_sha256=installer.sha256(rendered.read_bytes()),
            expected_instance_id="vm1",
            revision="a" * 40,
            root=tmp_path,
            runner=commands,
            instance_id=lambda: "vm1",
        )
    assert config.read_text() == "routes: []\n"
    assert not any("systemctl" in args[0] for args in commands.calls)


def test_installer_success_changes_only_owned_host_files(installer, tmp_path):
    config, rendered, digest = setup_install(installer, tmp_path)
    commands = Commands()
    original = commands.__call__
    active = set()

    def run(args):
        import subprocess

        result = original(args)
        if "enable" in args:
            active.add(args[-1])
        if "is-active" in args and args[-1] in active:
            return subprocess.CompletedProcess(args, 0, "active", "")
        return result

    backup = installer.install(
        ROOT,
        rendered,
        role="public",
        expected_current_sha256=digest,
        expected_source_sha256=installer.source_hash(ROOT),
        expected_rendered_sha256=installer.sha256(rendered.read_bytes()),
        expected_instance_id="vm1",
        revision="a" * 40,
        root=tmp_path,
        runner=run,
        instance_id=lambda: "vm1",
    )
    assert config.read_bytes() == rendered.read_bytes()
    assert ["systemctl", "enable", "findme-prometheus-public.service"] in commands.calls
    assert ["systemctl", "restart", "findme-prometheus-public.service"] in commands.calls
    assert (backup / "manifest.json").is_file()
    assert (tmp_path / "usr/local/lib/findme-prometheus/monitor_public_health.py").is_file()
    installer._restore(backup, tmp_path, run)
    assert config.read_text() == "routes: []\n"
    assert not (tmp_path / "etc/systemd/system/findme-prometheus-public.service").exists()


def test_http_endpoint_has_no_file_browsing_or_query_paths(exporter, monkeypatch):
    import threading
    from urllib.error import HTTPError
    from urllib.request import urlopen

    monkeypatch.setattr(exporter, "collect", lambda role: (200, "fresh\n"))
    server = exporter.create_server("public", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = "http://127.0.0.1:" + str(server.server_address[1])
        with urlopen(origin + "/metrics", timeout=2) as response:
            assert response.read() == b"fresh\n"
        for path in ("/", "/etc/passwd", "/metrics?file=secret"):
            with pytest.raises(HTTPError) as error:
                urlopen(origin + path, timeout=2)
            assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("restart_fails", [False, True])
def test_existing_active_exporter_loads_new_code_or_restores_previous_state(
    installer, tmp_path, enabled, restart_fails
):
    import subprocess

    config, rendered, digest = setup_install(installer, tmp_path)
    unit = "findme-prometheus-public.service"
    old_files = {
        installer.LIBRARY / "exporter.py": b"old exporter\n",
        installer.LIBRARY / "monitor_commerce.py": b"old commerce\n",
        installer.LIBRARY / "monitor_public_health.py": b"old probe\n",
        installer.UNIT_DIRECTORY / unit: b"old unit\n",
    }
    for relative, content in old_files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    state = {
        "enabled": enabled,
        "active": True,
        "loaded": old_files[installer.LIBRARY / "exporter.py"],
    }
    calls = []
    fail_next_restart = restart_fails

    def run(args):
        nonlocal fail_next_restart
        calls.append(args)
        code, output = 0, ""
        if "--svnrevision" in args:
            output = "26.09.10/21106904"
        elif args[-1] == unit:
            if "is-enabled" in args:
                code = 0 if state["enabled"] else 1
            elif "is-active" in args:
                code = 0 if state["active"] else 1
            elif "enable" in args:
                state["enabled"] = True
                # systemctl start/enable --now does not reload an active process.
            elif "disable" in args:
                state["enabled"] = False
            elif "restart" in args:
                state["active"] = False
                if fail_next_restart:
                    fail_next_restart = False
                    code = 1
                else:
                    state["active"] = True
                    state["loaded"] = (tmp_path / installer.LIBRARY / "exporter.py").read_bytes()
        return subprocess.CompletedProcess(args, code, output, "")

    kwargs = dict(
        role="public",
        expected_current_sha256=digest,
        expected_source_sha256=installer.source_hash(ROOT),
        expected_rendered_sha256=installer.sha256(rendered.read_bytes()),
        expected_instance_id="vm1",
        revision="a" * 40,
        root=tmp_path,
        runner=run,
        instance_id=lambda: "vm1",
    )
    if restart_fails:
        with pytest.raises(installer.InstallError, match="rolled back"):
            installer.install(ROOT, rendered, **kwargs)
        assert config.read_text() == "routes: []\n"
        for relative, content in old_files.items():
            assert (tmp_path / relative).read_bytes() == content
        assert state == {"enabled": enabled, "active": True, "loaded": b"old exporter\n"}
    else:
        installer.install(ROOT, rendered, **kwargs)
        assert state["loaded"] == (ROOT / "deploy/monitoring/prometheus/exporter.py").read_bytes()
        assert calls.index(["systemctl", "restart", unit]) < calls.index(
            ["systemctl", "restart", "unified_agent.service"]
        )
