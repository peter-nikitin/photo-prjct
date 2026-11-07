"""Transfer the current release and private environment over pinned SSH."""

import base64
import io
import ipaddress
import json
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REMOTE = r"""import base64, fcntl, io, json, os, subprocess, sys, tarfile, tempfile
from pathlib import Path
payload = json.load(sys.stdin)
root = Path('/opt/photo-prjct')
root.mkdir(mode=0o700, parents=True, exist_ok=True)
os.umask(0o077)
with (root / '.deployment.lock').open('w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(payload['archive']))) as archive:
        archive.extractall(root, filter='data')
    env = payload['environment']
    password = env.pop('GHCR_READ_TOKEN')
    username = env.pop('GHCR_USERNAME')
    with tempfile.TemporaryDirectory(prefix='findme-registry-') as config:
        child_env = {**os.environ, 'DOCKER_CONFIG': config, 'DEPLOY_ROOT': str(root)}
        subprocess.run(['docker', 'login', 'ghcr.io', '-u', username, '--password-stdin'],
                       input=password, text=True, check=True, env=child_env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def quote(value):
            return (
                '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$')
                .replace('\n', '\\n').replace('\r', '\\r') + '"')
        candidate = root / '.env.candidate'
        candidate.write_text(''.join(f'{k}={quote(v)}\n' for k, v in env.items()))
        candidate.chmod(0o600)
        candidate.replace(root / '.env')
        result = subprocess.run(['sh', str(root / 'deploy/apply-deployment.sh')], env=child_env,
                                capture_output=True, text=True)
        stages = {'stop-workers', 'pull-images', 'commerce-check',
                  'database', 'migrate', 'static', 'web'}
        for line in result.stdout.splitlines():
            if line.startswith('DEPLOY_STAGE=') and line.removeprefix('DEPLOY_STAGE=') in stages:
                print(line, flush=True)
        if result.returncode:
            raise SystemExit(1)
"""


def decode(value: str) -> str:
    if len(value) < 2 or value[0] != '"' or value[-1] != '"':
        raise ValueError("invalid environment")
    result = []
    i = 1
    while i < len(value) - 1:
        c = value[i]
        if c == "\\":
            i += 1
            c = {"n": "\n", "r": "\r", "t": "\t"}.get(value[i], value[i])
        result.append(c)
        i += 1
    return "".join(result)


def main() -> None:
    host = str(ipaddress.ip_address(os.environ["VM_HOST"]))
    user = os.environ["VM_USER"]
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise ValueError("invalid SSH user")
    private = Path(os.environ["FINDME_ENV_FILE"])
    if private.stat().st_mode & 0o077:
        raise ValueError("environment is not private")
    secrets = dict(line.split("=", 1) for line in private.read_text().splitlines() if "=" in line)
    secrets = {key: decode(value) for key, value in secrets.items()}
    key = Path(secrets.pop("VM_SSH_KEY_FILE"))
    if key.stat().st_mode & 0o077:
        raise ValueError("SSH key is not private")
    compose = (ROOT / "docker-compose.deployment.yml").read_text()
    names = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", compose)) | {"GHCR_USERNAME"}
    environment = {name: os.environ[name] for name in names if name in os.environ}
    environment.update(secrets)
    for name in ("APP_IMAGE", "PHOTO_WORKER_IMAGE", "IMPORT_WORKER_IMAGE"):
        if not re.fullmatch(
            r"ghcr\.io/[a-z0-9_.\-/]+@sha256:[0-9a-f]{64}", environment.get(name, "")
        ):
            raise ValueError("immutable image required")
    environment["ALLOWED_HOSTS"] = ",".join(
        filter(None, [environment.get("ALLOWED_HOSTS", ""), host, "localhost", "web"])
    )
    environment["DEBUG"] = "False"
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        for name in (
            "docker-compose.deployment.yml",
            "deploy/apply-deployment.sh",
            "deploy/nginx/http.conf",
            "deploy/nginx/https.conf",
            "deploy/nginx/edge-start.sh",
            "deploy/certbot/renew-certificates.sh",
            "deploy/certbot/reconcile-certificate.sh",
        ):
            tar.add(ROOT / name, arcname=name, recursive=False)
    payload = json.dumps(
        {"archive": base64.b64encode(archive.getvalue()).decode(), "environment": environment}
    )
    with tempfile.TemporaryDirectory(prefix="findme-ssh-") as directory:
        known = Path(directory) / "known_hosts"
        known.write_text(os.environ["VM_SSH_KNOWN_HOSTS"] + "\n")
        known.chmod(0o600)
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known}",
            "-i",
            str(key),
            f"{user}@{host}",
            "sudo",
            "-n",
            "python3",
            "-c",
            __import__("shlex").quote(REMOTE),
        ]
        result = subprocess.run(command, input=payload, text=True, capture_output=True)
        for line in result.stdout.splitlines():
            if line in {
                "DEPLOY_STAGE=stop-workers",
                "DEPLOY_STAGE=pull-images",
                "DEPLOY_STAGE=commerce-check",
                "DEPLOY_STAGE=database",
                "DEPLOY_STAGE=migrate",
                "DEPLOY_STAGE=static",
                "DEPLOY_STAGE=web",
            }:
                print(line)
        if result.returncode:
            raise RuntimeError("remote deployment failed")
    print("[remote] stage=deploy status=ok")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError):
        raise SystemExit("[remote] stage=deploy status=error") from None
