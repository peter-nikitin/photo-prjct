#!/usr/bin/env python3
"""Write a reviewable patch for all tracked and untracked working-tree changes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: review-package.py OUTPUT", file=sys.stderr)
        return 2

    output = Path(sys.argv[1]).resolve()
    repository = Path(git("rev-parse", "--show-toplevel").stdout.strip()).resolve()
    untracked = git("ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0")
    untracked = [path for path in untracked if path and (repository / path).resolve() != output]

    sections = [
        "# Working-tree review package\n",
        "## Status\n\n```text\n",
        git("status", "--short").stdout,
        "```\n\n## Tracked changes\n\n```diff\n",
        git("diff", "--no-ext-diff", "--binary", "--", ".").stdout,
        "```\n",
    ]
    for path in untracked:
        patch = git("diff", "--no-index", "--binary", "--", "/dev/null", path, check=False)
        if patch.returncode not in (0, 1):
            print(patch.stderr, file=sys.stderr, end="")
            return patch.returncode
        sections.extend((f"\n## Untracked: `{path}`\n\n```diff\n", patch.stdout, "```\n"))

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(sections), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
