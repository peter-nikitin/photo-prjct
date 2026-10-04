#!/usr/bin/python3
"""Install the fixed existing-host foundation through an approved operator transport."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

REPOSITORY = "https://github.com/peter-nikitin/photo-prjct.git"
HELPER = "deploy/observability/reconcile.py"
TARGETS = (
    "usr/local/sbin/findme-observability-reconcile",
    "etc/findme-observability-reconcile.json",
    "etc/sudoers.d/findme-observability-reconcile",
)
CONFIGURATION = {
    "role": "public",
    "instance_id": "epdf6696opq3ock91pih",
    "folder_id": "b1g2qttgfhb4gdunvlge",
    "workspace_id": "mon0c97qv2s5uju1ark8",
}
CANONICAL = {**CONFIGURATION, "role": "canonical", "instance_id": "epdr5g3p24tdns9890nr"}


class BootstrapError(RuntimeError):
    """A fixed, safe failure code for the privileged bootstrap boundary."""


def command(*args: str) -> bytes:
    return subprocess.run(
        args,
        check=True,
        capture_output=True,
        timeout=180,
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
    ).stdout


def host_dependencies() -> list[str]:
    missing = []
    if shutil.which("git") is None:
        missing.append("git")
    try:
        command("/usr/bin/python3", "-c", "import yaml")
    except (OSError, subprocess.SubprocessError):
        missing.append("python3-yaml")
    return missing


def ensure_host_dependencies() -> None:
    missing = host_dependencies()
    if not missing:
        return
    try:
        command("apt-get", "update", "--error-on=any")
        command("apt-get", "install", "-y", *missing)
    except (OSError, subprocess.SubprocessError) as error:
        raise BootstrapError("dependency_install_failed") from error
    if host_dependencies():
        raise BootstrapError("dependency_unavailable")


def safe(path: Path, mode: int, directory: bool = False) -> None:
    value = path.lstat()
    correct = stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)
    if (
        not correct
        or value.st_uid != 0
        or (value.st_mode & 0o022 if directory else stat.S_IMODE(value.st_mode) != mode)
    ):
        raise ValueError("unsafe foundation ownership or mode")


def verify_identity(role: str = "public") -> None:
    request = Request(
        "http://169.254.169.254/computeMetadata/v1/instance/id",
        headers={"Metadata-Flavor": "Google"},
    )
    with urlopen(request, timeout=5) as response:
        if (
            response.read(256).decode().strip()
            != (CANONICAL if role == "canonical" else CONFIGURATION)["instance_id"]
        ):
            raise ValueError("VM identity mismatch")


def authenticate(revision: str, directory: Path, existing: bytes | None = None) -> bytes:
    repository = directory / "repository.git"
    command("git", "init", "--bare", str(repository))
    command(
        "git",
        "--git-dir",
        str(repository),
        "-c",
        "core.hooksPath=/dev/null",
        "fetch",
        "--no-tags",
        REPOSITORY,
        "+refs/heads/main:refs/heads/main",
    )
    command(
        "git",
        "--git-dir",
        str(repository),
        "merge-base",
        "--is-ancestor",
        revision,
        "refs/heads/main",
    )
    entry = (
        command("git", "--git-dir", str(repository), "ls-tree", revision, "--", HELPER)
        .decode()
        .strip()
    )
    if not re.fullmatch(r"100(?:644|755) blob [0-9a-f]{40}\t" + re.escape(HELPER), entry):
        raise ValueError("non-regular source helper")
    content = command("git", "--git-dir", str(repository), "show", f"{revision}:{HELPER}")
    if existing is not None:
        revisions = (
            command(
                "git", "--git-dir", str(repository), "rev-list", "refs/heads/main", "--", HELPER
            )
            .decode()
            .splitlines()
        )
        if not any(
            command("git", "--git-dir", str(repository), "show", f"{sha}:{HELPER}") == existing
            for sha in revisions
        ):
            raise ValueError("existing helper is not authenticated main source")
    return content


def validate_sudoers(path: Path) -> None:
    command("visudo", "-cf", str(path))


def install(target: Path, content: bytes, mode: int) -> None:
    candidate = target.with_name(target.name + ".bootstrap")
    try:
        with candidate.open("xb") as output:
            output.write(content)
        candidate.chmod(mode)
        candidate.replace(target)
    finally:
        candidate.unlink(missing_ok=True)


def bootstrap(revision: str, digest: str, root: Path = Path("/"), role: str = "public") -> str:
    if (
        role not in {"public", "canonical"}
        or not re.fullmatch(r"[0-9a-f]{40}", revision)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        raise ValueError("exact reviewed source required")
    verify_identity(role)
    targets = [root / name for name in TARGETS]
    present = [path.exists() or path.is_symlink() for path in targets]
    if any(present) and not all(present):
        raise ValueError("partial foundation requires operator inspection")
    for directory in ("usr", "usr/local", "usr/local/sbin", "etc", "etc/sudoers.d"):
        path = root / directory
        if root != Path("/"):
            path.mkdir(parents=True, exist_ok=True)
        safe(path, 0o755, True)
    modes = (0o755, 0o600, 0o440)
    if all(present):
        for path, mode in zip(targets, modes, strict=True):
            safe(path, mode)
    configuration = CANONICAL if role == "canonical" else CONFIGURATION
    user = "deploy" if role == "canonical" else "yc-user"
    sudoers = (
        f"{user} ALL=(root) NOPASSWD: /usr/local/sbin/findme-observability-reconcile *\n".encode()
    )
    if all(present) and (
        json.loads(targets[1].read_text()) != configuration or targets[2].read_bytes() != sudoers
    ):
        raise ValueError("existing foundation mismatch")
    ensure_host_dependencies()
    with tempfile.TemporaryDirectory(prefix="findme-observability-bootstrap-") as scratch:
        directory = Path(scratch)
        try:
            content = authenticate(
                revision, directory, targets[0].read_bytes() if all(present) else None
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise BootstrapError("source_authentication_failed") from error
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("source checksum mismatch")
        fragment = directory / "sudoers"
        fragment.write_bytes(sudoers)
        fragment.chmod(0o440)
        try:
            validate_sudoers(fragment)
        except (OSError, subprocess.SubprocessError) as error:
            raise BootstrapError("sudoers_validation_failed") from error
        if all(present):
            return "existing"
        installed: list[Path] = []
        try:
            for target, payload, mode in zip(
                targets, (content, json.dumps(configuration).encode(), sudoers), modes, strict=True
            ):
                install(target, payload, mode)
                installed.append(target)
            for path, mode in zip(targets, modes, strict=True):
                safe(path, mode)
        except Exception:
            for target in reversed(installed):
                target.unlink()
            raise
    return "installed"


if __name__ == "__main__":
    if os.geteuid() != 0 or len(sys.argv) != 4:
        raise SystemExit("root bootstrap requires role, exact SHA and source checksum")
    try:
        result = bootstrap(sys.argv[2], sys.argv[3], role=sys.argv[1])
        print(f"OBSERVABILITY_FOUNDATION={result}")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        if isinstance(error, BootstrapError):
            reason = str(error)
        elif isinstance(error, FileNotFoundError):
            reason = "missing_host_command"
        elif isinstance(error, subprocess.SubprocessError):
            reason = "host_command_failed"
        else:
            reason = type(error).__name__
        print(f"OBSERVABILITY_FOUNDATION=failed reason={reason}", file=sys.stderr)
        raise SystemExit(1) from None
