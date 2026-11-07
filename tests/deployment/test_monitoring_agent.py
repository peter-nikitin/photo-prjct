import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _copy_script_for_host_test(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path]:
    deploy_dir = tmp_path / "deploy"
    monitoring_dir = deploy_dir / "monitoring"
    monitoring_dir.mkdir(parents=True)
    deb_config_dir = tmp_path / "etc" / "yandex" / "unified_agent"
    managed_config_dir = tmp_path / "etc" / "yc" / "unified_agent"
    os_release = tmp_path / "os-release"
    os_release.write_text("ID=ubuntu\nVERSION_ID=20.04\n", encoding="utf-8")
    (monitoring_dir / "unified-agent.yml.template").write_text(
        (ROOT / "deploy/monitoring/unified-agent.yml.template").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (monitoring_dir / "merge_native_agent.py").write_text(
        (ROOT / "deploy/monitoring/merge_native_agent.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (deploy_dir / "run-commerce-worker-health.sh").write_text(
        (ROOT / "deploy/run-commerce-worker-health.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    source = (ROOT / "deploy/configure-monitoring-agent.sh").read_text(encoding="utf-8")
    source = (
        source.replace(
            "DEB_CONFIG_DIR=/etc/yandex/unified_agent", f"DEB_CONFIG_DIR={deb_config_dir}"
        )
        .replace(
            "DEB_CONFIG_PATH=/etc/yandex/unified_agent/config.yml",
            f"DEB_CONFIG_PATH={deb_config_dir / 'config.yml'}",
        )
        .replace(
            "MANAGED_CONFIG_DIR=/etc/yc/unified_agent",
            f"MANAGED_CONFIG_DIR={managed_config_dir}",
        )
        .replace(
            "MANAGED_CONFIG_PATH=/etc/yc/unified_agent/config.yml",
            f"MANAGED_CONFIG_PATH={managed_config_dir / 'config.yml'}",
        )
        .replace(
            "COMMERCE_PROBE_PATH=/usr/local/lib/findme-commerce-worker-health/run-commerce-worker-health.sh",
            f"COMMERCE_PROBE_PATH={tmp_path / 'commerce-probe' / 'run-commerce-worker-health.sh'}",
        )
    )
    source = source.replace("/etc/os-release", str(os_release))
    script = deploy_dir / "configure-monitoring-agent.sh"
    script.write_text(source, encoding="utf-8")
    script.chmod(0o755)
    return script, deb_config_dir, managed_config_dir, tmp_path / "commands.log"


def _host_test_env(tmp_path: Path, config_dir: Path, command_log: Path) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "id", 'printf "%s\\n" 0')
    _write_executable(fake_bin / "uname", 'printf "%s\\n" x86_64')
    _write_executable(fake_bin / "python3", 'exec "$TEST_PYTHON" "$@"')
    _write_executable(
        fake_bin / "curl",
        """
case "$*" in
  *latest-version*) printf '%s\\n' 26.03.01 ;;
  *)
    previous=''
    output=''
    for argument in "$@"; do
      if [ "$previous" = --output ]; then output=$argument; fi
      previous=$argument
    done
    : > "$output"
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "dpkg",
        """
printf 'dpkg %s\\n' "$*" >> "$COMMAND_LOG"
case "$*" in
  *--remove*) exit 0 ;;
esac
if [ -n "${DEB_PACKAGE_MARKER_FILE:-}" ]; then
  : > "$DEB_PACKAGE_MARKER_FILE"
fi
mkdir -p "$DEB_CONFIG_DIR"
printf 'package-default\\n' > "$DEB_CONFIG_DIR/config.yml"
cat > "$(dirname "$0")/unified_agent" <<'EOF'
#!/bin/sh
set -eu
printf 'agent %s\n' "$*" >> "$AGENT_COMMAND_LOG"
case "$*" in
  *check-config*) exit "${CHECK_CONFIG_STATUS:-0}" ;;
  *--version*) printf '%s\\n' test-agent ;;
esac
EOF
chmod +x "$(dirname "$0")/unified_agent"
""",
    )
    _write_executable(
        fake_bin / "dpkg-query",
        """
case "$*" in
  *yandex-unified-agent*)
    if [ -n "${DEB_PACKAGE_MARKER_FILE:-}" ] && [ -f "$DEB_PACKAGE_MARKER_FILE" ]; then
      printf '%s\\n' installed
      exit 0
    fi
    exit 1
    ;;
esac
exit 1
""",
    )
    _write_executable(
        fake_bin / "systemctl",
        """
printf 'systemctl %s\\n' "$*" >> "$COMMAND_LOG"
case "$1" in
  is-enabled)
    [ "${INITIAL_ENABLED:-0}" = 1 ]
    ;;
  is-active)
    [ "${INITIAL_ACTIVE:-0}" = 1 ]
    ;;
  restart)
    count=0
    if [ -f "$RESTART_COUNT_FILE" ]; then count=$(cat "$RESTART_COUNT_FILE"); fi
    count=$((count + 1))
    printf '%s\\n' "$count" > "$RESTART_COUNT_FILE"
    if [ "${FAIL_FIRST_RESTART:-0}" = 1 ] && [ "$count" -eq 1 ]; then exit 1; fi
    ;;
  cat)
    case "$*" in
      *unified-agent*) [ "${DEB_UNIT_PRESENT:-0}" = 1 ] ;;
      *unified_agent*) [ "${MANAGED_UNIT_PRESENT:-0}" = 1 ] ;;
    esac
    ;;
esac
""",
    )
    return {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COMMAND_LOG": str(command_log),
        "CONFIG_DIR": str(config_dir),
        "DEB_CONFIG_DIR": str(config_dir),
        "DEB_PACKAGE_MARKER_FILE": str(tmp_path / "deb-package-installed"),
        "DEB_UNIT_PRESENT": "1",
        "AGENT_COMMAND_LOG": str(tmp_path / "agent-commands.log"),
        "RESTART_COUNT_FILE": str(tmp_path / "restart-count"),
        "TEST_PYTHON": sys.executable,
    }


def _valid_existing_agent_config() -> str:
    return yaml.safe_dump(
        {
            "status": {"port": 16241},
            "storages": [
                {"name": "metrics_buffer", "plugin": "fs", "config": {"directory": "/old"}}
            ],
            "channels": [
                {
                    "name": "cloud_monitoring",
                    "channel": {"output": {"plugin": "yc_metrics", "config": {}}},
                }
            ],
            "routes": [
                {
                    "input": {"plugin": "agent_metrics", "config": {}},
                    "channel": {"channel_ref": {"name": "cloud_monitoring"}},
                }
            ],
        },
        sort_keys=False,
    )


def test_agent_uses_instance_identity_and_loopback_app_metrics() -> None:
    from urllib.parse import urlparse

    config = yaml.safe_load((ROOT / "deploy/monitoring/unified-agent.yml.template").read_text())
    for channel in config["channels"]:
        output = channel["channel"]["output"]
        assert set(output["config"]["iam"]) == {"cloud_meta"}
    for route in config["routes"]:
        source = route["input"]
        assert source["plugin"] in {"linux_metrics", "agent_metrics", "metrics_pull"}
        if source["plugin"] == "metrics_pull":
            endpoint = urlparse(source["config"]["url"])
            assert endpoint.hostname in {"127.0.0.1", "localhost", "::1"}
            assert endpoint.username is None and endpoint.password is None


def test_fresh_install_validation_failure_removes_only_the_agent_attempt(tmp_path: Path) -> None:
    script, config_dir, _, command_log = _copy_script_for_host_test(tmp_path)
    env = _host_test_env(tmp_path, config_dir, command_log)
    env["CHECK_CONFIG_STATUS"] = "1"

    result = subprocess.run(
        ["sh", script, "--folder-id", "b1g2qttgfhb4gdunvlge"],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert not (config_dir / "config.yml").exists()
    assert command_log.exists(), result.stderr
    commands = command_log.read_text(encoding="utf-8")
    assert "systemctl stop unified-agent" in commands
    assert "systemctl disable unified-agent" in commands
    assert "dpkg --remove yandex-unified-agent" in commands


def test_fresh_deb_agent_keeps_its_long_version_flag(tmp_path: Path) -> None:
    script, config_dir, _, command_log = _copy_script_for_host_test(tmp_path)
    env = _host_test_env(tmp_path, config_dir, command_log)
    env["INITIAL_ACTIVE"] = "1"

    result = subprocess.run(
        ["sh", script, "--folder-id", "b1g2qttgfhb4gdunvlge"],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Unified Agent version: test-agent" in result.stdout
    assert Path(env["AGENT_COMMAND_LOG"]).read_text(encoding="utf-8").splitlines()[-1] == (
        "agent --version"
    )


def test_existing_agent_restart_failure_restores_config_and_service_state(tmp_path: Path) -> None:
    script, config_dir, _, command_log = _copy_script_for_host_test(tmp_path)
    env = _host_test_env(tmp_path, config_dir, command_log)
    fake_agent = tmp_path / "bin" / "unified_agent"
    _write_executable(
        fake_agent,
        """
case "$*" in
  *check-config*) exit 0 ;;
  *--version*) printf '%s\\n' existing-agent ;;
esac
""",
    )
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.yml"
    previous = _valid_existing_agent_config()
    config_path.write_text(previous, encoding="utf-8")
    env.update({"INITIAL_ENABLED": "1", "INITIAL_ACTIVE": "1", "FAIL_FIRST_RESTART": "1"})

    Path(env["DEB_PACKAGE_MARKER_FILE"]).touch()

    result = subprocess.run(
        ["sh", script, "--folder-id", "b1g2qttgfhb4gdunvlge"],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert config_path.read_text(encoding="utf-8") == previous
    commands = command_log.read_text(encoding="utf-8")
    assert "dpkg " not in commands
    assert commands.count("systemctl enable unified-agent") == 2
    assert commands.count("systemctl restart unified-agent") == 2


def test_managed_agent_restart_failure_restores_its_real_layout_without_dpkg_removal(
    tmp_path: Path,
) -> None:
    script, _, config_dir, command_log = _copy_script_for_host_test(tmp_path)
    env = _host_test_env(tmp_path, config_dir, command_log)
    _write_executable(
        tmp_path / "bin" / "unified_agent",
        """
case "$*" in
  *check-config*) exit 0 ;;
  *--version*) printf '%s\\n' managed-agent ;;
esac
""",
    )
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.yml"
    previous = _valid_existing_agent_config()
    config_path.write_text(previous, encoding="utf-8")
    env.update(
        {
            "INITIAL_ENABLED": "1",
            "INITIAL_ACTIVE": "1",
            "FAIL_FIRST_RESTART": "1",
            "MANAGED_UNIT_PRESENT": "1",
        }
    )

    result = subprocess.run(
        ["sh", script, "--folder-id", "b1g2qttgfhb4gdunvlge"],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert config_path.read_text(encoding="utf-8") == previous
    commands = command_log.read_text(encoding="utf-8")
    assert "dpkg " not in commands
    assert commands.count("systemctl enable unified_agent") == 2
    assert commands.count("systemctl restart unified_agent") == 2


def test_managed_agent_uses_short_version_flag_after_successful_activation(
    tmp_path: Path,
) -> None:
    script, _, config_dir, command_log = _copy_script_for_host_test(tmp_path)
    env = _host_test_env(tmp_path, config_dir, command_log)
    _write_executable(
        tmp_path / "bin" / "unified_agent",
        """
printf 'agent %s\\n' "$*" >> "$AGENT_COMMAND_LOG"
case "$*" in
  *check-config*) exit 0 ;;
  -V) printf '%s\\n' managed-agent-26.07.11 ;;
  --version) printf '%s\\n' 'unknown flag: --version' >&2; exit 2 ;;
esac
""",
    )
    config_dir.mkdir(parents=True)
    previous = _valid_existing_agent_config()
    (config_dir / "config.yml").write_text(previous, encoding="utf-8")
    env.update(
        {
            "INITIAL_ENABLED": "1",
            "INITIAL_ACTIVE": "1",
            "MANAGED_UNIT_PRESENT": "1",
        }
    )

    result = subprocess.run(
        ["sh", script, "--folder-id", "b1g2qttgfhb4gdunvlge"],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "Unified Agent version: managed-agent-26.07.11" in result.stdout
    assert (config_dir / "config.yml").read_text(encoding="utf-8") != previous
    agent_commands = Path(env["AGENT_COMMAND_LOG"]).read_text(encoding="utf-8").splitlines()
    assert agent_commands[0].endswith(" check-config")
    assert agent_commands[-1] == "agent -V"


def test_native_reconfigure_twice_preserves_prometheus_and_worker_routes_once(
    tmp_path: Path,
) -> None:
    script, config_dir, _, command_log = _copy_script_for_host_test(tmp_path)
    env = _host_test_env(tmp_path, config_dir, command_log)
    _write_executable(
        tmp_path / "bin" / "unified_agent",
        """
case "$*" in
  *check-config*) exit 0 ;;
  *--version*) printf '%s\n' existing-agent ;;
esac
""",
    )
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "status": {"port": 1},
                "storages": [
                    {"name": "metrics_buffer", "plugin": "fs", "config": {"x": "old"}},
                    {"name": "findme_prometheus_buffer", "plugin": "fs"},
                ],
                "channels": [
                    {"name": "cloud_monitoring", "channel": {"output": {"plugin": "old"}}},
                    {"name": "findme_prometheus_remote_write", "channel": {}},
                ],
                "routes": [
                    {
                        "input": {"plugin": "linux_metrics", "config": {"poll_period": "5s"}},
                        "channel": {"channel_ref": {"name": "cloud_monitoring"}},
                    },
                    {
                        "input": {
                            "plugin": "metrics_pull",
                            "config": {"url": "http://127.0.0.1:19091/metrics"},
                        },
                        "channel": {"channel_ref": {"name": "findme_prometheus_remote_write"}},
                    },
                    {
                        "input": {
                            "plugin": "metrics_pull",
                            "config": {"url": "http://127.0.0.1:8080/worker-diagnostics/metrics/"},
                        },
                        "channel": {
                            "pipe": [{"filter": {"plugin": "transform_metric_labels"}}],
                            "channel_ref": {"name": "findme_prometheus_remote_write"},
                        },
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    Path(env["DEB_PACKAGE_MARKER_FILE"]).touch()
    env.update({"INITIAL_ENABLED": "1", "INITIAL_ACTIVE": "1"})

    for _ in range(2):
        result = subprocess.run(
            ["sh", script, "--folder-id", "current-folder"],
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["status"] == {"port": 16241}
    assert [item["name"] for item in config["storages"]] == [
        "findme_prometheus_buffer",
        "metrics_buffer",
    ]
    assert [item["name"] for item in config["channels"]] == [
        "findme_prometheus_remote_write",
        "cloud_monitoring",
    ]
    urls = [
        route["input"].get("config", {}).get("url")
        for route in config["routes"]
        if route["input"]["plugin"] == "metrics_pull"
    ]
    assert urls.count("http://127.0.0.1:19091/metrics") == 1
    assert urls.count("http://127.0.0.1:8080/worker-diagnostics/metrics/") == 1
    native = [
        route
        for route in config["routes"]
        if route["channel"].get("channel_ref", {}).get("name") == "cloud_monitoring"
    ]
    assert len(native) == 3
    linux = next(route for route in native if route["input"]["plugin"] == "linux_metrics")
    assert linux["input"]["config"]["poll_period"] == "60s"
    cloud = next(item for item in config["channels"] if item["name"] == "cloud_monitoring")
    assert cloud["channel"]["output"]["config"]["folder_id"] == "current-folder"


def test_existing_agent_invalid_merged_candidate_keeps_working_config(tmp_path: Path) -> None:
    script, config_dir, _, command_log = _copy_script_for_host_test(tmp_path)
    env = _host_test_env(tmp_path, config_dir, command_log)
    _write_executable(
        tmp_path / "bin" / "unified_agent",
        """
printf 'agent %s\n' "$*" >> "$AGENT_COMMAND_LOG"
case "$*" in
  *check-config*) exit 1 ;;
  *--version*) printf '%s\n' existing-agent ;;
esac
""",
    )
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.yml"
    original_config = yaml.safe_load(_valid_existing_agent_config())
    original_config["storages"].append({"name": "findme_prometheus_buffer", "plugin": "fs"})
    original_config["channels"].append({"name": "findme_prometheus_remote_write", "channel": {}})
    original_config["routes"].append(
        {
            "input": {"plugin": "metrics_pull", "config": {"url": "loopback"}},
            "channel": {"channel_ref": {"name": "findme_prometheus_remote_write"}},
        }
    )
    original = yaml.safe_dump(original_config, sort_keys=False)
    config_path.write_text(original, encoding="utf-8")
    Path(env["DEB_PACKAGE_MARKER_FILE"]).touch()
    env.update({"INITIAL_ENABLED": "1", "INITIAL_ACTIVE": "1"})

    result = subprocess.run(
        ["sh", script, "--folder-id", "current-folder"],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert config_path.read_text(encoding="utf-8") == original
    assert "check-config" in Path(env["AGENT_COMMAND_LOG"]).read_text(encoding="utf-8")
    assert not Path(env["RESTART_COUNT_FILE"]).exists()
