from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/deploy-image-origin.yml"
REMOTE = ROOT / "deploy/image-origin/run-remote.sh"
CONFIGURE_CDN = ROOT / "deploy/image-origin/configure-cdn.sh"
MANIFEST = ROOT / "deploy/environment-secrets.json"


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    steps = [step for step in job["steps"] if step.get("name") == name]
    assert len(steps) == 1
    return steps[0]


def test_manual_workflow_pins_one_commit_and_one_secret_projection() -> None:
    workflow = _workflow()
    assert workflow[True] == {
        "workflow_dispatch": {
            "inputs": {
                "deployment_sha": {
                    "description": "Exact 40-character repository commit to deploy",
                    "required": True,
                    "type": "string",
                }
            }
        }
    }
    job = workflow["jobs"]["deploy-image-origin"]
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert job["concurrency"] == {
        "group": "deploy-image-origin",
        "cancel-in-progress": False,
    }
    checkout = _step(job, "Check out reviewed commit")
    assert checkout["with"] == {
        "ref": "${{ inputs.deployment_sha }}",
        "fetch-depth": 1,
        "persist-credentials": False,
    }
    deploy = _step(job, "Deploy isolated image origin")
    command = deploy["run"]
    assert deploy["env"] | {
        "IMAGE_ORIGIN_RELEASE": "${{ inputs.deployment_sha }}",
        "PRIVATE_MEDIA_S3_BUCKET": "${{ vars.PRIVATE_MEDIA_S3_BUCKET }}",
        "IMAGE_ORIGIN_PROBE_PATH": "${{ vars.IMAGE_ORIGIN_PROBE_PATH }}",
        "YANDEX_CLOUD_FOLDER_ID": "${{ vars.YANDEX_CLOUD_FOLDER_ID }}",
    } == {
        "IMAGE_ORIGIN_RELEASE": "${{ inputs.deployment_sha }}",
        "VM_HOST": "${{ vars.VM_HOST }}",
        "VM_USER": "${{ vars.VM_USER }}",
        "VM_SSH_KNOWN_HOSTS": "${{ vars.VM_SSH_KNOWN_HOSTS }}",
        "IMAGE_ORIGIN_VM_HOST": "${{ vars.IMAGE_ORIGIN_VM_HOST }}",
        "IMAGE_ORIGIN_VM_USER": "${{ vars.IMAGE_ORIGIN_VM_USER }}",
        "IMAGE_ORIGIN_SSH_KNOWN_HOSTS": "${{ vars.IMAGE_ORIGIN_SSH_KNOWN_HOSTS }}",
        "PRIVATE_MEDIA_S3_BUCKET": "${{ vars.PRIVATE_MEDIA_S3_BUCKET }}",
        "IMAGE_ORIGIN_PROBE_PATH": "${{ vars.IMAGE_ORIGIN_PROBE_PATH }}",
        "YANDEX_CLOUD_FOLDER_ID": "${{ vars.YANDEX_CLOUD_FOLDER_ID }}",
    }
    assert "--consumer image-origin" in command
    assert "--identity github-oidc" in command
    assert "deploy/image-origin/run-remote.sh" in command
    assert "${{ secrets." not in json.dumps(workflow)
    assert "deploy.yml" not in json.dumps(job)
    forbidden = ("docker-compose.deployment.yml", "apply-deployment.sh", "worker", "postgres")
    assert all(value not in command.lower() for value in forbidden)


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
            "PRIVATE_MEDIA_S3_BUCKET": "canonical-media",
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
            "PRIVATE_MEDIA_S3_BUCKET": "canonical-media",
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
            "PRIVATE_MEDIA_S3_BUCKET": "canonical-media",
            "IMAGE_ORIGIN_PROBE_PATH": "/" + "A" * 43 + "/gallery-v1/czM6Ly9h.jpg",
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


def _fake_yc(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    log = tmp_path / "yc.jsonl"
    executable = fake_bin / "yc"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """
import json, os, pathlib, sys
args = sys.argv[1:]
with pathlib.Path(os.environ["YC_LOG"]).open("a") as stream: stream.write(json.dumps(args) + "\\n")
state = pathlib.Path(os.environ["FAKE_PROVIDER_STATE"])
provider = json.loads(state.read_text()) if state.exists() else {}
def origin_group():
    origin = {
        "source": os.environ.get("FAKE_ORIGIN_SOURCE", "img-origin.findme-photo.ru"),
        "enabled": os.environ.get("FAKE_ORIGIN_ENABLED", "1") == "1",
    }
    if os.environ.get("FAKE_ORIGIN_BACKUP", "0") == "1":
        origin["backup"] = True
    return {
        "id": "321",
        "name": "findme-gallery-image-origin",
        "folder_id": "folder-contract-id",
        "origins": [origin],
    }
if args[:3] == ["config", "get", "cloud-id"]:
    print("cloud-contract-id"); raise SystemExit(0)
if args[:3] == ["config", "get", "folder-id"]:
    print("folder-contract-id"); raise SystemExit(0)
if args[:3] == ["cdn", "origin-group", "list"]:
    value = ([origin_group()] if provider.get("origin") else [])
elif args[:3] == ["cdn", "origin-group", "get"]:
    value = origin_group()
elif args[:3] == ["cdn", "origin-group", "create"]:
    provider["origin"] = True
    state.write_text(json.dumps(provider))
    value = {"id": "321"}
elif args[:3] == ["cdn", "resource", "list"]:
    value = ([{
        "id": "cdn-resource-id",
        "cname": "img.findme-photo.ru",
        "folder_id": "folder-contract-id",
    }] if provider.get("resource") else [])
elif args[:3] == ["cdn", "resource", "get"]:
    value = {
        "id": "cdn-resource-id",
        "cname": "img.findme-photo.ru",
        "folder_id": "folder-contract-id",
        "origin_group_id": "321",
        "ssl_certificate": provider.get("ssl_certificate", {"type": "DONT_USE"}),
    }
    if provider.get("active") is True:
        value["active"] = True
elif args[:3] == ["cdn", "resource", "create"]:
    provider["resource"] = True
    provider["ssl_certificate"] = {"type": "DONT_USE"}
    state.write_text(json.dumps(provider))
    value = {"id": "cdn-resource-id"}
elif args[:3] == ["cdn", "resource", "update"]:
    if "--dont-use-ssl-cert" in args:
        provider["ssl_certificate"] = {"type": "DONT_USE"}
        state.write_text(json.dumps(provider))
    value = {"id": "cdn-resource-id"}
else:
    raise SystemExit(9)
print(json.dumps(value))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return fake_bin, log


def _run_cdn(
    tmp_path: Path, *arguments: str, overrides: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    fake_bin, log = _fake_yc(tmp_path)
    projection = tmp_path / "cdn.env"
    projection.write_text(
        'GALLERY_CDN_TOKEN_SECRET="cdn-private-sentinel"\n'
        'IMAGE_ORIGIN_HEADER_SECRET="origin-private-sentinel"\n'
        'PRIVATE_MEDIA_S3_ACCESS_KEY_ID="application-static-key-id"\n',
        encoding="utf-8",
    )
    projection.chmod(0o600)
    result = subprocess.run(
        ["sh", str(CONFIGURE_CDN), *arguments],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FINDME_ENV_FILE": str(projection),
            "IMAGE_ORIGIN_CONTRACT_STATE": str(tmp_path / "contract-state.json"),
            "FAKE_PROVIDER_STATE": str(tmp_path / "provider.json"),
            "YC_LOG": str(log),
            **(overrides or {}),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, commands


def test_cdn_default_is_secret_free_read_only_plan_with_inactive_cache_contract(
    tmp_path: Path,
) -> None:
    result, commands = _run_cdn(tmp_path)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["mode"] == "dry-run"
    assert plan["desired"] == {
        "origin_host": "img-origin.findme-photo.ru",
        "cname": "img.findme-photo.ru",
        "active": False,
        "origin_protocol": "https",
        "ignore_query_string": True,
        "ignore_cookie": True,
        "edge_ttl_seconds": 2592000,
        "browser_ttl_seconds": 21600,
        "static_request_header": "X-FindMe-Origin-Auth",
        "secure_key": "projected",
        "origin_shielding": False,
        "logs": False,
        "dedicated_ip": False,
        "slicing": False,
        "compression": False,
        "cache_warming": False,
    }
    assert plan["approval_nonce"]
    assert all("create" not in command and "update" not in command for command in commands)
    assert "private-sentinel" not in result.stdout + result.stderr


def test_cdn_apply_requires_exact_nonce_and_creates_inactive_resource_without_optional_features(
    tmp_path: Path,
) -> None:
    dry, _ = _run_cdn(tmp_path)
    nonce = json.loads(dry.stdout)["approval_nonce"]
    rejected, rejected_commands = _run_cdn(tmp_path, "--apply", "--approval-nonce", "wrong")
    assert rejected.returncode == 2
    assert all("create" not in command and "update" not in command for command in rejected_commands)

    result, commands = _run_cdn(tmp_path, "--apply", "--approval-nonce", nonce)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["mode"] == "applied"
    origin = next(
        command for command in commands if command[:3] == ["cdn", "origin-group", "create"]
    )
    resource = next(command for command in commands if command[:3] == ["cdn", "resource", "create"])
    assert "source=img-origin.findme-photo.ru,enabled=true,backup=false" in origin
    rendered = " ".join(resource)
    for expected in (
        "--origin-protocol https",
        "--active=false",
        "--ignore-query-string",
        "--ignore-cookie",
        "--cache-expiration-time 2592000",
        "--browser-cache-expiration-time 21600",
        "--host-header img-origin.findme-photo.ru",
        "--static-request-headers X-FindMe-Origin-Auth=origin-private-sentinel",
        "--secure-key cdn-private-sentinel",
    ):
        assert expected in rendered
    for forbidden in ("shield", "logs", "dedicated", "slice", "gzip", "brotli", "warming"):
        assert forbidden not in rendered
    assert "private-sentinel" not in result.stdout + result.stderr


def test_cdn_existing_origin_requires_exact_sanitized_source_contract(tmp_path: Path) -> None:
    (tmp_path / "provider.json").write_text(json.dumps({"origin": True}))
    result, commands = _run_cdn(tmp_path)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["resolved"]["origin_group_id"] == "321"
    assert plan["actual"]["origin_group"] == {
        "origins": [
            {
                "source": "img-origin.findme-photo.ru",
                "enabled": True,
                "backup": False,
            }
        ]
    }
    assert all("create" not in command and "update" not in command for command in commands)

    drifted, drifted_commands = _run_cdn(
        tmp_path,
        overrides={"FAKE_ORIGIN_SOURCE": "different-origin.example"},
    )
    assert drifted.returncode == 2
    assert "origin_group_drift" in drifted.stderr
    assert all("create" not in command and "update" not in command for command in drifted_commands)


def test_cdn_update_preserves_attached_certificate(tmp_path: Path) -> None:
    certificate = {
        "type": "CM",
        "status": "READY",
        "data": {"cm": {"id": "certificate-contract-id"}},
    }
    (tmp_path / "provider.json").write_text(
        json.dumps({"origin": True, "resource": True, "ssl_certificate": certificate})
    )
    dry, _ = _run_cdn(tmp_path)
    plan = json.loads(dry.stdout)
    assert plan["actual"]["cdn_resource"]["certificate"] == {
        "type": "CM",
        "status": "READY",
        "certificate_manager_id": "certificate-contract-id",
    }

    result, commands = _run_cdn(
        tmp_path,
        "--apply",
        "--approval-nonce",
        plan["approval_nonce"],
    )
    assert result.returncode == 0, result.stderr
    update = next(command for command in commands if command[:3] == ["cdn", "resource", "update"])
    assert "--dont-use-ssl-cert" not in update
    assert "--cert-manager-ssl-cert-id" not in update
    after, _ = _run_cdn(tmp_path)
    assert (
        json.loads(after.stdout)["actual"]["cdn_resource"]["certificate"]
        == plan["actual"]["cdn_resource"]["certificate"]
    )


def test_cdn_adopts_existing_origin_id_before_resource_mutation_and_round_trips(
    tmp_path: Path,
) -> None:
    (tmp_path / "provider.json").write_text(json.dumps({"origin": True}))
    dry, _ = _run_cdn(tmp_path)
    plan = json.loads(dry.stdout)

    applied, commands = _run_cdn(
        tmp_path,
        "--apply",
        "--approval-nonce",
        plan["approval_nonce"],
    )
    assert applied.returncode == 0, applied.stderr
    assert any(command[:3] == ["cdn", "resource", "create"] for command in commands)
    state = json.loads((tmp_path / "contract-state.json").read_text())
    assert state == {"origin_group_id": "321", "cdn_resource_id": "cdn-resource-id"}

    next_dry, next_commands = _run_cdn(tmp_path)
    assert next_dry.returncode == 0, next_dry.stderr
    next_plan = json.loads(next_dry.stdout)
    assert next_plan["resolved"] == {
        "origin_group_id": "321",
        "cdn_resource_id": "cdn-resource-id",
    }
    assert any(command[:3] == ["cdn", "origin-group", "get"] for command in next_commands)
    assert any(command[:3] == ["cdn", "resource", "get"] for command in next_commands)


def test_manifest_allows_workflow_and_keeps_transport_key_out_of_runtime_contract() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert (
        "peter-nikitin/photo-prjct/.github/workflows/deploy-image-origin.yml@refs/heads/main"
        in manifest["github_oidc"]["allowed_workflows"]
    )
    assert set(manifest["consumers"]["image-origin"]) == {
        "GALLERY_IMGPROXY_KEY",
        "GALLERY_IMGPROXY_SALT",
        "IMAGE_ORIGIN_HEADER_SECRET",
        "IMAGE_ORIGIN_S3_ACCESS_KEY_ID",
        "IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY",
        "VM_SSH_KEY",
    }
    compose = (ROOT / "deploy/image-origin/compose.yml").read_text(encoding="utf-8")
    apply = (ROOT / "deploy/image-origin/apply.sh").read_text(encoding="utf-8")
    assert "VM_SSH_KEY" not in compose + apply
