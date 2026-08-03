#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

WORKTREE_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
LOCAL_ENV_OVERRIDES = {
    "SECRET_KEY": "test-not-a-secret",
    "DB_HOST": "localhost",
}
TEST_ENVIRONMENT = {
    "SECRET_KEY": "test-not-a-secret",
    "DEBUG": "False",
    "ALLOWED_HOSTS": "localhost,127.0.0.1",
    "DB_NAME": "app",
    "DB_USER": "app",
    "DB_PASSWORD": "app",
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
}


def repository_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()).resolve()


def validate_preconditions(root: Path, name: str) -> None:
    if not WORKTREE_NAME.fullmatch(name):
        raise ValueError("NAME must contain only lowercase letters, digits, and hyphens")
    if not (root / ".venv" / "bin" / "python").is_file():
        raise RuntimeError(f"root virtual environment is missing: {root / '.venv'}")
    if not (root / ".venv" / "bin" / "pytest").is_file():
        raise RuntimeError(f"pytest is missing from the root virtual environment: {root / '.venv'}")
    if not (root / ".env.example").is_file():
        raise RuntimeError(f"tracked environment template is missing: {root / '.env.example'}")


def local_environment(template: str) -> str:
    output: list[str] = []
    found: set[str] = set()
    for line in template.splitlines():
        key, separator, _value = line.partition("=")
        if separator and key in LOCAL_ENV_OVERRIDES:
            output.append(f"{key}={LOCAL_ENV_OVERRIDES[key]}")
            found.add(key)
        else:
            output.append(line)
    for key, value in LOCAL_ENV_OVERRIDES.items():
        if key not in found:
            output.append(f"{key}={value}")
    return "\n".join(output) + "\n"


def create_worktree(root: Path, name: str, base: str) -> Path:
    target = root / ".worktrees" / name
    branch = f"codex/{name}"
    subprocess.run(
        ["git", "worktree", "add", str(target), "-b", branch, base],
        cwd=root,
        check=True,
    )

    venv_link = target / ".venv"
    venv_link.symlink_to(os.path.relpath(root / ".venv", target))
    template = (root / ".env.example").read_text(encoding="utf-8")
    (target / ".env").write_text(local_environment(template), encoding="utf-8")

    subprocess.run([str(venv_link / "bin" / "python"), "--version"], cwd=target, check=True)
    subprocess.run([str(venv_link / "bin" / "pytest"), "--version"], cwd=target, check=True)
    check_environment = {**os.environ, **TEST_ENVIRONMENT}
    subprocess.run(
        [str(venv_link / "bin" / "python"), "src/backend/manage.py", "check"],
        cwd=target,
        env=check_environment,
        check=True,
    )
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a test-ready project worktree")
    parser.add_argument("name", metavar="NAME")
    parser.add_argument("base", metavar="BASE", nargs="?", default="origin/main")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        root = repository_root()
        validate_preconditions(root, args.name)
        target = create_worktree(root, args.name, args.base)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Worktree ready: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
