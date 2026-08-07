from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_migration_immutability.py"


def _run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def _commit(repository: Path, message: str) -> str:
    _run("git", "add", ".", cwd=repository)
    _run("git", "commit", "-qm", message, cwd=repository)
    return _run("git", "rev-parse", "HEAD", cwd=repository).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    migrations = repository / "src/backend/catalog/migrations"
    migrations.mkdir(parents=True)
    _run("git", "init", "-q", cwd=repository)
    _run("git", "config", "user.name", "Migration Test", cwd=repository)
    _run("git", "config", "user.email", "migration-test@example.com", cwd=repository)
    (migrations / "__init__.py").write_text("", encoding="utf-8")
    (migrations / "0001_initial.py").write_text("BASE = True\n", encoding="utf-8")
    (repository / "src/backend/catalog/models.py").write_text("MODEL = True\n", encoding="utf-8")
    return repository, _commit(repository, "base migrations")


def _check(repository: Path, base: str, head: str) -> subprocess.CompletedProcess[str]:
    return _run(
        sys.executable,
        str(SCRIPT),
        "--base",
        base,
        "--head",
        head,
        cwd=repository,
        check=False,
    )


def test_allows_new_leaf_merge_and_non_migration_python_files(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    migrations = repository / "src/backend/catalog/migrations"
    (migrations / "0002_add_caption.py").write_text("LEAF = True\n", encoding="utf-8")
    (migrations / "0003_merge_0002_add_caption_0002_other.py").write_text(
        "MERGE = True\n", encoding="utf-8"
    )
    (migrations / "__init__.py").write_text("# package\n", encoding="utf-8")
    (repository / "src/backend/catalog/models.py").write_text("MODEL = False\n", encoding="utf-8")
    head = _commit(repository, "add migrations")

    result = _check(repository, base, head)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_rejects_modified_base_migration_with_its_path(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    migration = repository / "src/backend/catalog/migrations/0001_initial.py"
    migration.write_text("BASE = False\n", encoding="utf-8")
    head = _commit(repository, "modify base migration")

    result = _check(repository, base, head)

    assert result.returncode == 1
    assert result.stdout == "src/backend/catalog/migrations/0001_initial.py\n"
    assert result.stderr == ""


def test_rejects_deleted_base_migration_with_its_path(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    (repository / "src/backend/catalog/migrations/0001_initial.py").unlink()
    head = _commit(repository, "delete base migration")

    result = _check(repository, base, head)

    assert result.returncode == 1
    assert result.stdout == "src/backend/catalog/migrations/0001_initial.py\n"
    assert result.stderr == ""


def test_rejects_renamed_base_migration_as_a_deletion(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path)
    _run(
        "git",
        "mv",
        "src/backend/catalog/migrations/0001_initial.py",
        "src/backend/catalog/migrations/0001_renamed.py",
        cwd=repository,
    )
    head = _commit(repository, "rename base migration")

    assert _run("git", "diff", "--name-status", base, head, cwd=repository).stdout == (
        "R100\tsrc/backend/catalog/migrations/0001_initial.py\t"
        "src/backend/catalog/migrations/0001_renamed.py\n"
    )

    result = _check(repository, base, head)

    assert result.returncode == 1
    assert result.stdout == "src/backend/catalog/migrations/0001_initial.py\n"
    assert result.stderr == ""
