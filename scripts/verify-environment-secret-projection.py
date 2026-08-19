#!/usr/bin/env python3
from __future__ import annotations

import os
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

CONSUMERS = {
    "local-web",
    "deploy",
    "remote-check",
    "public-monitor",
}
PRIVATE_FILE_MODE = 0o600
PRIVATE_DIRECTORY_MODE = 0o700


def _error(stage: str, code: str) -> int:
    print(
        f"[environment-secret-projection] stage={stage} status=error code={code}",
        file=sys.stderr,
    )
    return 2


def main(arguments: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if arguments is None else arguments)
    if len(values) != 1 or values[0] not in CONSUMERS:
        return _error("arguments", "invalid_consumer")

    environment_file = os.environ.get("FINDME_ENV_FILE")
    if not environment_file:
        return _error("boundary", "environment_file_invalid")

    path = Path(environment_file)
    try:
        file_status = path.lstat()
        directory_status = path.parent.lstat()
    except OSError:
        return _error("boundary", "environment_file_invalid")
    if (
        not stat.S_ISREG(file_status.st_mode)
        or stat.S_IMODE(file_status.st_mode) != PRIVATE_FILE_MODE
        or not stat.S_ISDIR(directory_status.st_mode)
        or stat.S_IMODE(directory_status.st_mode) != PRIVATE_DIRECTORY_MODE
    ):
        return _error("boundary", "environment_file_invalid")

    print(f"[environment-secret-projection] consumer={values[0]} status=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
