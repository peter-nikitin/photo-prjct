from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "create-worktree.py"


def _run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _repository(tmp_path: Path, *, with_venv: bool = True) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _run("git", "init", "-q", cwd=repository)
    _run("git", "config", "user.name", "Worktree Test", cwd=repository)
    _run("git", "config", "user.email", "worktree-test@example.com", cwd=repository)
    (repository / ".gitignore").write_text(".env\n.venv\n.worktrees/\n", encoding="utf-8")
    (repository / ".env.example").write_text(
        "SECRET_KEY=change-me\n"
        "DEBUG=False\n"
        "ALLOWED_HOSTS=localhost,127.0.0.1,web\n"
        "DB_NAME=app\n"
        "DB_USER=app\n"
        "DB_PASSWORD=app\n"
        "DB_HOST=db\n"
        "DB_PORT=5432\n",
        encoding="utf-8",
    )
    if with_venv:
        bin_directory = repository / ".venv" / "bin"
        bin_directory.mkdir(parents=True)
        _write_executable(bin_directory / "python", "#!/bin/sh\necho 'Python 3.12.test'\n")
        _write_executable(bin_directory / "pytest", "#!/bin/sh\necho 'pytest test'\n")
        _write_executable(
            bin_directory / "pre-commit",
            "#!/bin/sh\n"
            "set -eu\n"
            'test "$1" = install\n'
            "hook_path=$(git rev-parse --git-path hooks/pre-commit)\n"
            'mkdir -p "$(dirname "$hook_path")"\n'
            "printf '#!/bin/sh\\nexit 0\\n' > \"$hook_path\"\n"
            'chmod +x "$hook_path"\n',
        )
    _run("git", "add", ".gitignore", ".env.example", cwd=repository)
    _run("git", "commit", "-qm", "initial", cwd=repository)
    return repository


def test_creates_test_ready_worktree_without_copying_root_secrets(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / ".env").write_text("SECRET_KEY=root-secret-must-not-leak\n", encoding="utf-8")

    result = _run(sys.executable, str(SCRIPT), "example", "HEAD", cwd=repository)

    worktree = repository / ".worktrees" / "example"
    assert result.stdout.splitlines()[-1] == f"Worktree ready: {worktree}"
    assert _run("git", "branch", "--show-current", cwd=worktree).stdout.strip() == "codex/example"
    assert (worktree / ".venv").is_symlink()
    assert os.readlink(worktree / ".venv") == "../../.venv"
    assert (worktree / ".venv" / "bin" / "pytest").is_file()
    assert (worktree / ".env").read_text(encoding="utf-8") == (
        "SECRET_KEY=test-not-a-secret\n"
        "DEBUG=False\n"
        "ALLOWED_HOSTS=localhost,127.0.0.1,web\n"
        "DB_NAME=app\n"
        "DB_USER=app\n"
        "DB_PASSWORD=app\n"
        "DB_HOST=localhost\n"
        "DB_PORT=5432\n"
    )
    assert "root-secret-must-not-leak" not in (worktree / ".env").read_text(encoding="utf-8")
    assert _run("git", "status", "--short", cwd=worktree).stdout == ""
    assert (repository / ".git" / "hooks" / "pre-commit").stat().st_mode & 0o111


def test_rejects_unsafe_name_before_creating_git_state(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    result = _run(
        sys.executable,
        str(SCRIPT),
        "../escape",
        "HEAD",
        cwd=repository,
        check=False,
    )

    assert result.returncode == 2
    assert "lowercase letters, digits, and hyphens" in result.stderr
    assert _run("git", "branch", "--list", "codex/escape", cwd=repository).stdout == ""
    assert not (repository.parent / "escape").exists()


def test_missing_root_venv_fails_before_git_worktree_add(tmp_path: Path) -> None:
    repository = _repository(tmp_path, with_venv=False)

    result = _run(
        sys.executable,
        str(SCRIPT),
        "example",
        "HEAD",
        cwd=repository,
        check=False,
    )

    assert result.returncode == 1
    assert "root virtual environment is missing" in result.stderr
    assert _run("git", "branch", "--list", "codex/example", cwd=repository).stdout == ""
    assert not (repository / ".worktrees" / "example").exists()
