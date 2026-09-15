from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path


def publish_immutable(output: Path, write: Callable[[Path], None]) -> None:
    """Atomically publish a complete bundle without ever replacing an existing one."""
    if os.path.lexists(output):
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        write(staging)
        if os.path.lexists(output):
            raise FileExistsError(output)
        os.replace(staging, output)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
