import os
import subprocess
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
    }


def _plugin_count(config: dict[object, object], plugin: str) -> int:
    return sum(1 for route in config["routes"] if route["input"]["plugin"] == plugin)


def test_unified_agent_template_collects_only_host_agent_and_private_app_metrics() -> None:
    template = ROOT / "deploy/monitoring/unified-agent.yml.template"
    source = template.read_text(encoding="utf-8")
    config = yaml.safe_load(source)

    assert _plugin_count(config, "linux_metrics") == 1
    assert _plugin_count(config, "agent_metrics") == 1
    assert _plugin_count(config, "metrics_pull") == 1
    assert config["routes"][0]["input"]["config"] == {
        "poll_period": "60s",
        "namespace": "sys",
    }
    assert config["routes"][1]["input"]["config"] == {
        "poll_period": "60s",
        "namespace": "ua",
    }
    assert config["routes"][1]["channel"]["pipe"] == [
        {"filter": {"plugin": "filter_metrics", "config": {"match": "{scope=health}"}}}
    ]
    assert config["routes"][2]["input"]["config"] == {
        "url": "http://127.0.0.1:8080/metrics/",
        "format": {"prometheus": {}},
        "poll_period": "60s",
        "namespace": "app",
    }
    assert config["storages"] == [
        {
            "name": "metrics_buffer",
            "plugin": "fs",
            "config": {
                "directory": "/var/lib/yandex/unified_agent/metrics_buffer",
                "max_partition_size": "128mb",
                "max_segment_size": "16mb",
            },
        }
    ]
    assert len(config["channels"]) == 1
    output = config["channels"][0]["channel"]["output"]
    assert output == {
        "plugin": "yc_metrics",
        "config": {"folder_id": "__YANDEX_CLOUD_FOLDER_ID__", "iam": {"cloud_meta": {}}},
    }
    assert "token" not in source.lower()
    assert "docker" not in source.lower()
    assert "/var/run/docker.sock" not in source
    assert "container" not in source.lower()


def test_monitoring_agent_script_has_safe_install_and_rollback_contract() -> None:
    script = ROOT / "deploy/configure-monitoring-agent.sh"
    source = script.read_text(encoding="utf-8")

    assert "id -u" in source
    assert '"$FOLDER_ID"' in source
    assert "uname -m" in source
    assert "x86_64" in source
    assert "/etc/os-release" in source
    assert "ID=ubuntu" in source
    assert "storage.yandexcloud.net/yc-unified-agent" in source
    assert "yandex-unified-agent_" in source
    assert "dpkg -i" in source
    assert "/etc/yandex/unified_agent/config.yml" in source
    assert "check-config" in source
    assert "DEB_SERVICE=unified-agent" in source
    assert "MANAGED_CONFIG_PATH=/etc/yc/unified_agent/config.yml" in source
    assert "MANAGED_SERVICE=unified_agent" in source
    assert 'systemctl enable "$SERVICE_NAME"' in source
    assert 'systemctl restart "$SERVICE_NAME"' in source
    assert 'systemctl is-active "$SERVICE_NAME"' in source
    assert "trap cleanup" in source
    assert "Unified Agent version:" in source
    assert "token" not in source.lower()
    assert source.index("check-config") < source.index('mv "$candidate_config" "$CONFIG_PATH"')
    assert 'if [ "$exit_status" -ne 0 ] && [ "$agent_installed_by_attempt" -eq 1 ]' in source
    assert "dpkg --remove yandex-unified-agent" in source
    assert "snapshot_existing_agent" in source
    assert "dpkg-query -W" in source
    assert 'systemctl cat "$DEB_SERVICE"' in source
    assert 'systemctl cat "$MANAGED_SERVICE"' in source
    assert 'systemctl is-enabled --quiet "$SERVICE_NAME"' in source
    assert 'systemctl is-active --quiet "$SERVICE_NAME"' in source
    assert 'mv "$previous_config" "$CONFIG_PATH" || true' in source

    result = subprocess.run(["sh", "-n", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


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
    config_path.write_text("previous-config\\n", encoding="utf-8")
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
    assert config_path.read_text(encoding="utf-8") == "previous-config\\n"
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
    config_path.write_text("managed-previous-config\\n", encoding="utf-8")
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
    assert config_path.read_text(encoding="utf-8") == "managed-previous-config\\n"
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
    (config_dir / "config.yml").write_text("managed-previous-config\\n", encoding="utf-8")
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
    assert (config_dir / "config.yml").read_text(encoding="utf-8") != "managed-previous-config\\n"
    agent_commands = Path(env["AGENT_COMMAND_LOG"]).read_text(encoding="utf-8").splitlines()
    assert agent_commands[0].endswith(" check-config")
    assert agent_commands[-1] == "agent -V"
