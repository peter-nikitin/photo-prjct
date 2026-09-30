#!/usr/bin/env python3
"""Prepare, verify and seal a disposable Ubuntu 24.04 amd64 worker image builder.

The default plan is read only. Copy this file and telemetry-requirements.txt to
the identified builder; invoke each mutating step explicitly as root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

BASE_URL = "https://download.docker.com/linux/ubuntu/"
METADATA_URL = "http://169.254.169.254/computeMetadata/v1/instance/id"
FORBIDDEN_IDS = {"epdr5g3p24tdns9890nr", "epdf6696opq3ock91pih"}
PACKAGES = (
    (
        "docker-ce",
        "dists/noble/pool/stable/amd64/docker-ce_29.6.0-1~ubuntu.24.04~noble_amd64.deb",
        "4f07c2989b7a0467925b7c395434978d4884df224144d3664233e03f541469c2",
        "5:29.6.0-1~ubuntu.24.04~noble",
    ),
    (
        "docker-ce-cli",
        "dists/noble/pool/stable/amd64/docker-ce-cli_29.6.0-1~ubuntu.24.04~noble_amd64.deb",
        "90c8f487f844467d9bba75c15b8c4f3abbdfe6bb95af4d1878efcb176d2f3ccc",
        "5:29.6.0-1~ubuntu.24.04~noble",
    ),
    (
        "docker-compose-plugin",
        "dists/noble/pool/stable/amd64/docker-compose-plugin_5.1.4-1~ubuntu.24.04~noble_amd64.deb",
        "45ef136eeb23e2cfdef0e06592d3c2d8566172a5874169b35b446cf251080ecb",
        "5.1.4-1~ubuntu.24.04~noble",
    ),
    (
        "containerd.io",
        "dists/noble/pool/stable/amd64/containerd.io_2.2.5-1~ubuntu.24.04~noble_amd64.deb",
        "759a9fc24e761c965385ff24233e868c8082b7d5f80549ea7525b82defff729a",
        "2.2.5-1~ubuntu.24.04~noble",
    ),
)
REQUIREMENTS = Path(__file__).with_name("telemetry-requirements.txt")
RECEIPT = "var/lib/findme-worker-image/receipt.json"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("redirect refused")


def fetch(url: str) -> bytes:
    opener = build_opener(ProxyHandler({}), NoRedirect())
    headers = {"Metadata-Flavor": "Google"} if url == METADATA_URL else {}
    with opener.open(Request(url, headers=headers), timeout=15) as response:
        if response.status != 200:
            raise ValueError("unexpected download status")
        limit = 128 if url == METADATA_URL else 100 * 1024 * 1024
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError("download exceeds size limit")
        return body


def command(run, argv: list[str]) -> str:
    result = run(argv, check=True, text=True, capture_output=True, timeout=600)
    return result.stdout.strip()


def path_at(root: Path, path: str) -> Path:
    return root / path.lstrip("/")


def os_release(root: Path) -> dict[str, str]:
    lines = path_at(root, "/etc/os-release").read_text().splitlines()
    return {
        key: value.strip('"')
        for line in lines
        if "=" in line
        for key, value in [line.split("=", 1)]
    }


def reject_runtime_evidence(root: Path) -> None:
    if {"DOCKER_HOST", "DOCKER_CONTEXT"}.intersection(os.environ):
        raise ValueError("Docker endpoint override on builder")
    fixed = (
        "etc/findme-worker",
        "usr/local/lib/findme-worker",
        "opt/photo-prjct",
        "var/lib/findme-worker",
        "root/.docker/config.json",
        "root/.config/containers/auth.json",
        "etc/docker/config.json",
        RECEIPT,
    )
    if any(path_at(root, item).exists() or path_at(root, item).is_symlink() for item in fixed):
        raise ValueError("application or worker runtime evidence on builder")
    if any(path_at(root, "/etc/systemd/system").glob("findme-worker-*")):
        raise ValueError("worker service evidence on builder")
    for home in path_at(root, "/home").glob("*"):
        if home.is_symlink():
            raise ValueError("unexpected home path on builder")
        if (home / ".docker/config.json").exists() or (
            home / ".config/containers/auth.json"
        ).exists():
            raise ValueError("Docker credential evidence on builder")
    credential_names = {
        "PHOTO_PROCESSING_FLEET_TOKEN",
        "IMAGE_PULL_AUTH",
        "DATABASE_URL",
        "DJANGO_SECRET_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "DOCKER_AUTH_CONFIG",
        "DOCKER_CONFIG",
        "YC_TOKEN",
        "YANDEX_CLOUD_OAUTH_TOKEN",
    }
    if credential_names.intersection(os.environ):
        raise ValueError("application credential in builder environment")


def empty_docker(root: Path, run) -> None:
    for argv in (
        ["docker", "ps", "-aq"],
        ["docker", "image", "ls", "-q"],
        ["docker", "volume", "ls", "-q"],
    ):
        try:
            if command(run, argv):
                raise ValueError("Docker objects present on builder")
        except FileNotFoundError:
            if path_at(root, "/var/lib/docker").exists():
                raise ValueError("Docker data exists but Docker is unavailable") from None
            return


def guard(expected_instance_id: str, *, root: Path, run, get, euid, machine) -> None:
    if euid() != 0:
        raise ValueError("root required")
    if not re.fullmatch(r"[a-z0-9]{20}", expected_instance_id):
        raise ValueError("invalid expected instance ID")
    if expected_instance_id in FORBIDDEN_IDS:
        raise ValueError("canonical or image-origin instance forbidden")
    release = os_release(root)
    if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != "24.04":
        raise ValueError("Ubuntu 24.04 required")
    if machine() != "x86_64":
        raise ValueError("amd64 required")
    actual = get(METADATA_URL).decode("ascii").strip()
    if actual != expected_instance_id:
        raise ValueError("builder instance ID mismatch")
    reject_runtime_evidence(root)
    empty_docker(root, run)


def prepare(root: Path, run, get) -> None:
    telemetry = path_at(root, "/opt/findme-worker-telemetry")
    if telemetry.exists() or telemetry.is_symlink():
        raise ValueError("telemetry environment already exists")
    # Download all artifacts and check every digest before the first apt mutation.
    artifacts = []
    for name, relative_url, sha256, _ in PACKAGES:
        body = get(BASE_URL + relative_url)
        if hashlib.sha256(body).hexdigest() != sha256:
            raise ValueError(f"SHA256 mismatch for {name}")
        artifacts.append((Path(relative_url).name, body))
    with tempfile.TemporaryDirectory(
        prefix="findme-worker-image-", dir=path_at(root, "/var/tmp")
    ) as temp:
        local_packages = []
        for filename, body in artifacts:
            target = Path(temp) / filename
            target.write_bytes(body)
            local_packages.append(str(target))
        command(run, ["apt-get", "update"])
        command(
            run,
            [
                "apt-get",
                "install",
                "-y",
                "--no-install-recommends",
                "python3",
                "python3-venv",
                "ca-certificates",
            ],
        )
        command(run, ["apt-get", "install", "-y", "--no-install-recommends", *local_packages])
    command(run, ["apt-mark", "hold", *(name for name, _, _, _ in PACKAGES)])
    command(run, ["python3", "-m", "venv", str(telemetry)])
    command(
        run,
        [
            str(telemetry / "bin/python"),
            "-m",
            "pip",
            "install",
            "--no-input",
            "-r",
            str(REQUIREMENTS),
        ],
    )


def verify(root: Path, run) -> None:
    if command(run, ["docker", "version", "--format", "{{.Server.Version}}"]) != "29.6.0":
        raise ValueError("Docker server version mismatch")
    if command(run, ["docker", "compose", "version", "--short"]).removeprefix("v") != "5.1.4":
        raise ValueError("Docker Compose version mismatch")
    for name, _, _, version in PACKAGES:
        if command(run, ["dpkg-query", "-W", "-f=${Version}", name]) != version:
            raise ValueError(f"{name} package version mismatch")
    for service in ("docker.service", "containerd.service"):
        if command(run, ["systemctl", "is-active", service]) != "active":
            raise ValueError(f"{service} is not active")
    if not command(run, ["python3", "--version"]).startswith("Python 3."):
        raise ValueError("Python 3 unavailable")
    telemetry = path_at(root, "/opt/findme-worker-telemetry/bin/python")
    if not telemetry.exists():
        raise ValueError("telemetry venv unavailable")
    requirements = [
        line.strip()
        for line in REQUIREMENTS.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(requirements) != 2 or any("==" not in line for line in requirements):
        raise ValueError("unexpected telemetry requirements")
    pins = dict(line.split("==", 1) for line in requirements)
    if set(pins) != {"psutil", "prometheus-client"}:
        raise ValueError("unexpected telemetry requirements")
    versions = command(
        run,
        [
            str(telemetry),
            "-c",
            "import psutil, prometheus_client; "
            "from importlib.metadata import version; "
            "print(version('psutil'), version('prometheus-client'))",
        ],
    )
    if versions != f"{pins['psutil']} {pins['prometheus-client']}":
        raise ValueError("telemetry dependency version mismatch")
    command(run, ["cloud-init", "--version"])


def safe_unlink(root: Path, relative: str) -> None:
    target = path_at(root, relative)
    if not target.exists() and not target.is_symlink():
        return
    if target.is_symlink() or any(
        parent.is_symlink() for parent in target.parents if parent != root
    ):
        raise ValueError("unsafe builder identity path")
    if not target.is_file():
        raise ValueError("unexpected builder identity path")
    target.unlink()


def receipt(expected_instance_id: str) -> dict[str, object]:
    return {
        "status": "sealed",
        "builder_instance_id": expected_instance_id,
        "recipe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "telemetry_requirements_sha256": hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest(),
        "packages": {
            name: {"sha256": sha256, "version": version} for name, _, sha256, version in PACKAGES
        },
        "docker_server": "29.6.0",
        "compose": "5.1.4",
    }


def seal(root: Path, run, expected_instance_id: str) -> dict[str, object]:
    # Validate cleanup targets before stopping services or removing any identity.
    if path_at(root, RECEIPT).parent.exists():
        raise ValueError("receipt directory already exists on builder")
    targets = ["root/.ssh/authorized_keys"]
    targets += [
        str(path.relative_to(root) / ".ssh/authorized_keys")
        for path in path_at(root, "/home").glob("*")
    ]
    targets += [
        str(path.relative_to(root)) for path in path_at(root, "/etc/ssh").glob("ssh_host_*")
    ]
    for relative in targets:
        target = path_at(root, relative)
        if target.is_symlink() or any(
            parent.is_symlink() for parent in target.parents if parent != root
        ):
            raise ValueError("unsafe builder identity path")
        if target.exists() and not target.is_file():
            raise ValueError("unexpected builder identity path")
    command(run, ["systemctl", "stop", "docker.service", "docker.socket", "containerd.service"])
    for relative in targets:
        safe_unlink(root, relative)
    command(run, ["cloud-init", "clean", "--logs", "--machine-id", "--seed"])
    record = receipt(expected_instance_id)
    location = path_at(root, RECEIPT)
    location.parent.mkdir(parents=True, exist_ok=False)
    with tempfile.NamedTemporaryFile(mode="w", dir=location.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(record, stream, sort_keys=True)
        stream.write("\n")
    try:
        os.chmod(temporary, 0o644)
        temporary.replace(location)
    finally:
        temporary.unlink(missing_ok=True)
    return record


def execute(
    step: str,
    expected_instance_id: str,
    *,
    confirm_seal: str | None = None,
    root: Path = Path("/"),
    run=subprocess.run,
    get=fetch,
    euid=os.geteuid,
    machine=platform.machine,
) -> dict[str, object]:
    if step == "plan":
        return {"steps": ["prepare", "verify", "seal"], "builder_instance_id": expected_instance_id}
    if step not in {"prepare", "verify", "seal"}:
        raise ValueError("unknown recipe step")
    if step == "seal" and confirm_seal != expected_instance_id:
        raise ValueError("exact instance ID confirmation required for seal")
    guard(expected_instance_id, root=root, run=run, get=get, euid=euid, machine=machine)
    if step == "prepare":
        prepare(root, run, get)
        return {"status": "prepared", "builder_instance_id": expected_instance_id}
    verify(root, run)
    if step == "verify":
        return {"status": "verified", "builder_instance_id": expected_instance_id}
    return seal(root, run, expected_instance_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "step", nargs="?", choices=("plan", "prepare", "verify", "seal"), default="plan"
    )
    parser.add_argument("--expected-instance-id", required=True)
    parser.add_argument("--confirm-seal")
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                execute(args.step, args.expected_instance_id, confirm_seal=args.confirm_seal),
                sort_keys=True,
            )
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"worker image recipe failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
