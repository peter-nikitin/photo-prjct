from __future__ import annotations

import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REMOTE = ROOT / "deploy/image-origin/run-remote.sh"
ORIGIN_AGENT_TEMPLATE = ROOT / "deploy/image-origin/monitoring/unified-agent.yml.template"


def _private_projection(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    key = tmp_path / "id_ed25519"
    key.write_text("private-key-sentinel", encoding="utf-8")
    key.chmod(0o600)
    values = {
        "GALLERY_IMGPROXY_KEY": "11" * 32,
        "GALLERY_IMGPROXY_SALT": "22" * 32,
        "IMAGE_ORIGIN_HEADER_SECRET": "origin-header-private-sentinel",
        "IMAGE_ORIGIN_S3_ACCESS_KEY_ID": "access-private-sentinel",
        "IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY": "secret-private-sentinel",
        "VM_SSH_KEY_FILE": str(key),
    }
    projection = tmp_path / "projection.env"
    projection.write_text(
        "".join(f'{name}="{value}"\n' for name, value in values.items()), encoding="utf-8"
    )
    projection.chmod(0o600)
    return projection, values


def _fake_transport(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "capture"
    capture.mkdir()
    scp = fake_bin / "scp"
    scp.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, shutil, sys\n"
        "args = sys.argv[1:]\n"
        "log = pathlib.Path(os.environ['TRANSPORT_LOG'])\n"
        "log.open('a').write('scp ' + ' '.join(args) + '\\n')\n"
        "source = pathlib.Path(args[-2])\n"
        "destination = args[-1].split(':', 1)[1]\n"
        "target = pathlib.Path(os.environ['CAPTURE_ROOT']) / pathlib.Path(destination).name\n"
        "shutil.copyfile(source, target)\n",
        encoding="utf-8",
    )
    scp.chmod(0o755)
    ssh = fake_bin / "ssh"
    ssh.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "body = sys.stdin.read()\n"
        "log = pathlib.Path(os.environ['TRANSPORT_LOG'])\n"
        "log.open('a').write('ssh ' + ' '.join(args) + '\\n')\n"
        "counter = pathlib.Path(os.environ['CAPTURE_ROOT'], 'ssh-count')\n"
        "count = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "counter.write_text(str(count))\n"
        "config = pathlib.Path(args[args.index('-F') + 1])\n"
        "if count == 1:\n"
        "    capture_root = pathlib.Path(os.environ['CAPTURE_ROOT'])\n"
        "    config_text = config.read_text()\n"
        "    (capture_root / 'ssh-config').write_text(config_text)\n"
        "    known_hosts = next(\n"
        "        line.split(maxsplit=1)[1]\n"
        "        for line in config_text.splitlines()\n"
        "        if line.strip().startswith('UserKnownHostsFile ')\n"
        "    )\n"
        "    (capture_root / 'known-hosts').write_text(pathlib.Path(known_hosts).read_text())\n"
        "    (capture_root / 'preflight-program').write_text(body)\n"
        "    status = int(os.environ.get('PREFLIGHT_STATUS', '0'))\n"
        "    if status:\n"
        "        print('raw-ssh-secret-sentinel', file=sys.stderr)\n"
        "        raise SystemExit(status)\n"
        "    raise SystemExit(0)\n"
        "pathlib.Path(os.environ['CAPTURE_ROOT'], 'remote-program').write_text(body)\n"
        "sha = os.environ['RELEASE_SHA']\n"
        "print(f'IMAGE_ORIGIN_DEPLOYED_SHA={sha}')\n"
        "print('IMAGE_ORIGIN_HEALTH=green')\n"
        "print('IMAGE_ORIGIN_MONITORING=green')\n",
        encoding="utf-8",
    )
    ssh.chmod(0o755)
    return fake_bin, capture


def test_remote_transport_verifies_host_key_keeps_secrets_out_of_arguments_and_packages_only_origin(
    tmp_path: Path,
) -> None:
    projection, values = _private_projection(tmp_path)
    fake_bin, capture = _fake_transport(tmp_path)
    release = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    log = tmp_path / "transport.log"
    result = subprocess.run(
        ["sh", str(REMOTE)],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FINDME_ENV_FILE": str(projection),
            "IMAGE_ORIGIN_VM_HOST": "198.51.100.44",
            "IMAGE_ORIGIN_VM_USER": "origin-deploy",
            "IMAGE_ORIGIN_SSH_KNOWN_HOSTS": "198.51.100.44 ssh-ed25519 host-key",
            "VM_HOST": "203.0.113.10",
            "VM_USER": "deployer",
            "VM_SSH_KNOWN_HOSTS": "203.0.113.10 ssh-ed25519 bastion-host-key",
            "IMAGE_ORIGIN_RELEASE": release,
            "EVENT_MEDIA_S3_BUCKET": "canonical-public",
            "IMAGE_ORIGIN_PROBE_PATH": "/" + "A" * 43 + "/gallery-v1/czM6Ly9h.jpg",
            "YANDEX_CLOUD_FOLDER_ID": "folder-contract-id",
            "TRANSPORT_LOG": str(log),
            "CAPTURE_ROOT": str(capture),
            "RELEASE_SHA": release,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"IMAGE_ORIGIN_DEPLOYED_SHA={release}\n"
        "IMAGE_ORIGIN_HEALTH=green\n"
        "IMAGE_ORIGIN_MONITORING=green\n"
    )
    transport = log.read_text(encoding="utf-8")
    assert transport.splitlines()[0].startswith("ssh ")
    assert transport.index("ssh ") < transport.index("scp ")
    config = (capture / "ssh-config").read_text(encoding="utf-8")
    assert "HostName 203.0.113.10" in config
    assert "User deployer" in config
    assert "HostName 198.51.100.44" in config
    assert "User origin-deploy" in config
    assert "ProxyJump findme-image-origin-bastion" in config
    assert config.count("BatchMode yes") == 2
    assert config.count("StrictHostKeyChecking yes") == 2
    assert config.count("IdentitiesOnly yes") == 2
    assert config.count(f"IdentityFile {values['VM_SSH_KEY_FILE']}") == 2
    known_hosts = capture / "known-hosts"
    assert known_hosts.read_text(encoding="utf-8").splitlines() == [
        "203.0.113.10 ssh-ed25519 bastion-host-key",
        "198.51.100.44 ssh-ed25519 host-key",
    ]
    for secret in values.values():
        if secret != values["VM_SSH_KEY_FILE"]:
            assert secret not in result.stdout + result.stderr + transport
    remote_env = (capture / f"findme-image-origin-{release}.env").read_text(encoding="utf-8")
    assert "VM_SSH_KEY_FILE" not in remote_env
    assert 'EVENT_MEDIA_S3_BUCKET="canonical-public"\n' in remote_env
    assert stat.S_IMODE(projection.stat().st_mode) == 0o600
    archive = capture / f"findme-image-origin-{release}.tar"
    with tarfile.open(archive) as package:
        names = package.getnames()
    assert names
    assert all(name == "image-origin" or name.startswith("image-origin/") for name in names)
    remote_program = (capture / "remote-program").read_text(encoding="utf-8")
    assert "apply.sh" in remote_program
    assert "check.sh" in remote_program
    assert "unified-agent.yml.template" in remote_program
    assert "docker-compose.deployment.yml" not in remote_program
    assert "apply-deployment.sh" not in remote_program
    preflight = (capture / "preflight-program").read_text(encoding="utf-8")
    assert "cloud-init status --wait" in preflight
    assert "/var/lib/findme-image-origin/bootstrap-ready" in preflight
    assert "docker compose version --short" in preflight
    assert "dpkg --compare-versions" in preflight
    assert "systemctl is-active --quiet unified-agent" in preflight


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (30, "cloud_init_failed"),
        (31, "bootstrap_marker_missing"),
        (32, "docker_compose_unsupported"),
        (33, "unified_agent_unsupported"),
    ],
)
def test_remote_preflight_failures_are_sanitized_and_stop_before_transport(
    tmp_path: Path, status: int, code: str
) -> None:
    projection, _ = _private_projection(tmp_path)
    fake_bin, capture = _fake_transport(tmp_path)
    release = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    log = tmp_path / "transport.log"
    result = subprocess.run(
        ["sh", str(REMOTE)],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FINDME_ENV_FILE": str(projection),
            "VM_HOST": "203.0.113.10",
            "VM_USER": "deployer",
            "VM_SSH_KNOWN_HOSTS": "203.0.113.10 ssh-ed25519 bastion-host-key",
            "IMAGE_ORIGIN_VM_HOST": "10.0.0.4",
            "IMAGE_ORIGIN_VM_USER": "origin-deploy",
            "IMAGE_ORIGIN_SSH_KNOWN_HOSTS": "10.0.0.4 ssh-ed25519 origin-host-key",
            "IMAGE_ORIGIN_RELEASE": release,
            "EVENT_MEDIA_S3_BUCKET": "canonical-public",
            "IMAGE_ORIGIN_PROBE_PATH": "/" + "A" * 43 + "/gallery-v1/czM6Ly9h.jpg",
            "YANDEX_CLOUD_FOLDER_ID": "folder-contract-id",
            "TRANSPORT_LOG": str(log),
            "CAPTURE_ROOT": str(capture),
            "RELEASE_SHA": release,
            "PREFLIGHT_STATUS": str(status),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == f"IMAGE_ORIGIN_DEPLOY=error code={code}\n"
    assert "raw-ssh-secret-sentinel" not in result.stdout + result.stderr
    assert not any(line.startswith("scp ") for line in log.read_text().splitlines())


def test_remote_transport_rejects_unpinned_release_before_network(tmp_path: Path) -> None:
    projection, _ = _private_projection(tmp_path)
    result = subprocess.run(
        ["sh", str(REMOTE)],
        cwd=ROOT,
        env={**os.environ, "FINDME_ENV_FILE": str(projection), "IMAGE_ORIGIN_RELEASE": "main"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "invalid_release_sha" in result.stderr


def test_remote_transport_mode_check_does_not_use_ambiguous_gnu_stat_fallback(
    tmp_path: Path,
) -> None:
    projection, _ = _private_projection(tmp_path)
    fake_bin, capture = _fake_transport(tmp_path)
    stat_log = tmp_path / "stat.log"
    fake_stat = fake_bin / "stat"
    fake_stat.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >>"$STAT_LOG"\n'
        'if [ "$1" = -f ]; then\n'
        "  printf '  File: %s\\n  Type: overlayfs\\n' \"$3\"\n"
        "  exit 1\n"
        "fi\n"
        "printf '600\\n'\n",
        encoding="utf-8",
    )
    fake_stat.chmod(0o755)
    release = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    result = subprocess.run(
        ["sh", str(REMOTE)],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FINDME_ENV_FILE": str(projection),
            "IMAGE_ORIGIN_VM_HOST": "198.51.100.44",
            "IMAGE_ORIGIN_VM_USER": "origin-deploy",
            "IMAGE_ORIGIN_SSH_KNOWN_HOSTS": "198.51.100.44 ssh-ed25519 host-key",
            "VM_HOST": "203.0.113.10",
            "VM_USER": "deployer",
            "VM_SSH_KNOWN_HOSTS": "203.0.113.10 ssh-ed25519 bastion-host-key",
            "IMAGE_ORIGIN_RELEASE": release,
            "IMAGE_ORIGIN_PROBE_PATH": "/" + "A" * 43 + "/gallery-v1/czM6Ly9h.jpg",
            "EVENT_MEDIA_S3_BUCKET": "canonical-public",
            "YANDEX_CLOUD_FOLDER_ID": "folder-contract-id",
            "TRANSPORT_LOG": str(tmp_path / "transport.log"),
            "CAPTURE_ROOT": str(capture),
            "RELEASE_SHA": release,
            "STAT_LOG": str(stat_log),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not stat_log.exists()


@pytest.mark.parametrize("monitoring_only", ["true", "false"])
def test_remote_agent_repair_and_deploy_do_not_require_retired_public_exporter(
    tmp_path: Path, monitoring_only: str
) -> None:
    release = "a" * 40
    stage = tmp_path / "stage"
    package = stage / "image-origin"
    (package / "monitoring").mkdir(parents=True)
    (package / "monitoring/unified-agent.yml.template").write_text(
        ORIGIN_AGENT_TEMPLATE.read_text()
    )
    marker = tmp_path / "applied"
    (package / "apply.sh").write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    (package / "check.sh").write_text("#!/bin/sh\nexit 0\n")
    archive = tmp_path / f"findme-image-origin-{release}.tar"
    with tarfile.open(archive, "w") as bundle:
        bundle.add(package, arcname="image-origin")
    environment = tmp_path / f"findme-image-origin-{release}.env"
    environment.write_text("")
    environment.chmod(0o600)
    config_dir = tmp_path / "etc/yandex/unified_agent"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.yml"
    config_path.write_text("old configuration\n")
    check_dir = tmp_path / "current"
    check_dir.mkdir()
    (check_dir / "check.sh").write_text("#!/bin/sh\nexit 0\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  "cat unified-agent"|"is-active --quiet unified-agent"|'
        '"enable unified-agent"|"restart unified-agent") exit 0 ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    agent = fake_bin / "unified_agent"
    agent.write_text(
        f"#!{sys.executable}\n"
        "import sys, yaml\n"
        "config = yaml.safe_load(open(sys.argv[2]))\n"
        "routes = [route['input'] for route in config['routes']]\n"
        "urls = {route.get('config', {}).get('url') for route in routes}\n"
        "assert 'http://127.0.0.1:18081/metrics' in urls\n"
        "assert 'http://127.0.0.1:18081/imgproxy-metrics' in urls\n"
        "assert any(route['plugin'] == 'linux_metrics' for route in routes)\n"
        "assert all(url is None or ':19091/' not in url for url in urls)\n"
    )
    stat_command = fake_bin / "stat"
    stat_command.write_text("#!/bin/sh\nprintf '600\\n'\n")
    curl = fake_bin / "curl"
    curl.write_text("#!/bin/sh\nexit 1\n")
    for executable in (systemctl, agent, stat_command, curl):
        executable.chmod(0o755)
    program = REMOTE.read_text().split("<<'REMOTE'\n", 1)[1].split("\nREMOTE\n", 1)[0]
    program = program.replace("/tmp/findme-image-origin", f"{tmp_path}/findme-image-origin")
    program = program.replace("/etc/yandex/unified_agent", str(config_dir))
    program = program.replace("/opt/photo-prjct-image-origin/current", str(check_dir))
    result = subprocess.run(
        ["sh", "-s", "--", release, "folder1", "workspace1", monitoring_only],
        input=program,
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "IMAGE_ORIGIN_MONITORING=green" in result.stdout
    assert marker.exists() == (monitoring_only == "false")
    assert (config_dir / "config.yml.pre-image-origin").read_text() == "old configuration\n"
