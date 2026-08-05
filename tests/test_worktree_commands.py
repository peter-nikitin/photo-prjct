from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_WRAPPER = ROOT / "scripts" / "run-in-test-env.sh"
REQUIRED_ENVIRONMENT = (
    "SECRET_KEY",
    "DEBUG",
    "ALLOWED_HOSTS",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "DB_HOST",
    "DB_PORT",
    "TEST_DB_NAME",
)


def test_environment_wrapper_supplies_ci_defaults_and_preserves_overrides() -> None:
    environment = {
        key: value for key, value in os.environ.items() if key not in REQUIRED_ENVIRONMENT
    }
    environment["DB_NAME"] = "explicit-database"
    environment["TEST_DB_NAME"] = "explicit-test-database"
    command = (
        "import json, os; "
        f"print(json.dumps({{key: os.environ[key] for key in {REQUIRED_ENVIRONMENT!r}}}))"
    )

    result = subprocess.run(
        ["sh", str(ENV_WRAPPER), sys.executable, "-c", command],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "SECRET_KEY": "test-not-a-secret",
        "DEBUG": "False",
        "ALLOWED_HOSTS": "localhost,127.0.0.1",
        "DB_NAME": "explicit-database",
        "DB_USER": "app",
        "DB_PASSWORD": "app",
        "DB_HOST": "localhost",
        "DB_PORT": "5432",
        "TEST_DB_NAME": "explicit-test-database",
    }


def test_environment_wrapper_assigns_each_process_an_isolated_django_database() -> None:
    environment = {
        key: value for key, value in os.environ.items() if key not in REQUIRED_ENVIRONMENT
    }
    command = (
        "import os, sys; "
        "sys.path.insert(0, 'src/backend'); "
        "from config.settings import DATABASES; "
        "print(os.environ['TEST_DB_NAME']); "
        "print(DATABASES['default']['TEST']['NAME'])"
    )

    names = []
    for _ in range(2):
        result = subprocess.run(
            ["sh", str(ENV_WRAPPER), sys.executable, "-c", command],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        environment_name, django_name = result.stdout.splitlines()
        assert django_name == environment_name
        names.append(environment_name)

    assert names[0] != names[1]
    assert all(name.startswith("findme_test_") for name in names)


def test_django_database_defaults_remain_unchanged_without_test_override() -> None:
    environment = {
        **os.environ,
        "SECRET_KEY": "test-not-a-secret",
        "DEBUG": "False",
        "ALLOWED_HOSTS": "localhost",
        "DB_NAME": "production-database",
        "DB_USER": "app",
        "DB_PASSWORD": "app",
        "DB_HOST": "localhost",
        "DB_PORT": "5432",
    }
    environment.pop("TEST_DB_NAME", None)
    command = (
        "import json, sys; "
        "sys.path.insert(0, 'src/backend'); "
        "from config.settings import DATABASES; "
        "print(json.dumps(DATABASES['default']))"
    )

    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    database = json.loads(result.stdout)
    assert database["NAME"] == "production-database"
    assert database["TEST"]["NAME"] is None


def test_make_test_renders_requested_selection_with_project_runner() -> None:
    result = subprocess.run(
        [
            "make",
            "--dry-run",
            "test",
            "TESTS=tests/test_create_worktree.py::test_rejects_unsafe_name_before_creating_git_state",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == (
        "sh scripts/run-in-test-env.sh .venv/bin/pytest "
        '-m "not clone_staging_slow" '
        "tests/test_create_worktree.py::test_rejects_unsafe_name_before_creating_git_state\n"
    )


def test_make_hooks_uses_project_pre_commit() -> None:
    result = subprocess.run(
        ["make", "--dry-run", "hooks"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == ".venv/bin/pre-commit install\n"


def test_make_worktree_requires_name_without_changing_git_state() -> None:
    branches_before = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    result = subprocess.run(
        ["make", "worktree"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    branches_after = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert result.returncode != 0
    assert "NAME is required" in result.stderr
    assert branches_after == branches_before


def test_bootstrapped_venv_link_is_ignored_by_git() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".venv"],
        cwd=ROOT,
        check=False,
    )

    assert result.returncode == 0
    assert (
        subprocess.run(
            ["git", "status", "--short", "--", ".venv"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )
