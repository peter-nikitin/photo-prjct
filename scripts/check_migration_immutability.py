#!/usr/bin/env python3
"""Reject changes to Django migration files that already exist on a base revision."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence

MIGRATION_PATH = re.compile(r"src/backend/[^/]+/migrations/[0-9][^/]*\.py\Z")


def _changed_migration_paths(output: str) -> list[str] | None:
    changed_paths: list[str] = []
    for line in output.splitlines():
        try:
            status, path = line.split("\t")
        except ValueError:
            return None
        if status not in {"A", "M", "D"}:
            return None
        if status in {"M", "D"} and MIGRATION_PATH.fullmatch(path):
            changed_paths.append(path)
    return sorted(changed_paths)


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

    changed_paths = _changed_migration_paths(diff.stdout)
    if changed_paths is None:
        print("Malformed git diff output.", file=sys.stderr)
        return 2
    if changed_paths:
        print("\n".join(changed_paths))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
