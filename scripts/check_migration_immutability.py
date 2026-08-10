#!/usr/bin/env python3
"""Reject changes to Django migration files that already exist on a base revision."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from collections.abc import Sequence

MIGRATION_PATH = re.compile(r"src/backend/[^/]+/migrations/[0-9][^/]*\.py\Z")

# This one-time recovery preserves a migration identity that staging applied before
# it was incorrectly renamed in 2e34b34. The exact file contents make this a
# fail-closed exception rather than a general permission to edit migrations.
_RECOVERY_RENAMED_PATH = "src/backend/selfie_search/migrations/0005_optional_feedback_contact.py"
_RECOVERY_HISTORICAL_PATH = "src/backend/selfie_search/migrations/0003_optional_feedback_contact.py"
_RECOVERY_RENAMED_SHA256 = "2282e1336531d4a98a0266197f5fddaccfb7291374b664c06beff72c2854552c"
_RECOVERY_HISTORICAL_SHA256 = "b80c5f61282499ca7086dda3b8da32a1d109796ab2554bed50938f1d10533c01"
_RECOVERY_MERGE_SHA256 = "c3bdcf6b8ebfec98997e28f3ed35fee3be839e770d3ff469c6553c54deddcfab"


def _changed_migrations(output: str) -> list[tuple[str, str]] | None:
    changed_migrations: list[tuple[str, str]] = []
    for line in output.splitlines():
        try:
            status, path = line.split("\t")
        except ValueError:
            return None
        if status not in {"A", "M", "D"}:
            return None
        if MIGRATION_PATH.fullmatch(path):
            changed_migrations.append((status, path))
    return sorted(changed_migrations)


def _migration_sha256(revision: str, path: str) -> str | None:
    source = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        capture_output=True,
        text=False,
        check=False,
    )
    if source.returncode != 0:
        return None
    return hashlib.sha256(source.stdout).hexdigest()


def _is_audited_optional_contact_recovery(
    changed_migrations: list[tuple[str, str]], base: str, head: str
) -> bool:
    return (
        changed_migrations
        == [
            ("A", _RECOVERY_HISTORICAL_PATH),
            ("M", _RECOVERY_RENAMED_PATH),
        ]
        and _migration_sha256(base, _RECOVERY_RENAMED_PATH) == _RECOVERY_RENAMED_SHA256
        and _migration_sha256(head, _RECOVERY_HISTORICAL_PATH) == _RECOVERY_HISTORICAL_SHA256
        and _migration_sha256(head, _RECOVERY_RENAMED_PATH) == _RECOVERY_MERGE_SHA256
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    arguments = parser.parse_args(argv)

    try:
        diff = subprocess.run(
            [
                "git",
                "diff",
                "--name-status",
                "--no-renames",
                arguments.base,
                arguments.head,
                "--",
                "src/backend",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        print("Could not run git diff.", file=sys.stderr)
        return 2

    if diff.returncode != 0:
        print("Invalid revisions or git diff failed.", file=sys.stderr)
        return 2

    changed_migrations = _changed_migrations(diff.stdout)
    if changed_migrations is None:
        print("Malformed git diff output.", file=sys.stderr)
        return 2
    immutable_paths = [path for status, path in changed_migrations if status in {"M", "D"}]
    if immutable_paths and not _is_audited_optional_contact_recovery(
        changed_migrations, arguments.base, arguments.head
    ):
        print("\n".join(immutable_paths))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
