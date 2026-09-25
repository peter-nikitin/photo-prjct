#!/usr/bin/env python3
"""Build the probe only from git objects in an exact reviewed repository commit."""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCES = {
    "install.py": "deploy/monitoring/probe-vm/install.py",
    "findme-public-probe.service": "deploy/monitoring/probe-vm/findme-public-probe.service",
    "findme-public-probe.timer": "deploy/monitoring/probe-vm/findme-public-probe.timer",
    "monitor_public_health.py": "scripts/monitor_public_health.py",
}


def build(release: str, output: Path) -> None:
    if not re.fullmatch("[0-9a-f]{40}", release):
        raise ValueError("requires exact commit SHA")
    resolved = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", f"{release}^{{commit}}"], text=True
    ).strip()
    if resolved != release:
        raise ValueError("commit mismatch")
    contents = {
        name: subprocess.check_output(["git", "-C", str(ROOT), "show", f"{release}:{source}"])
        for name, source in SOURCES.items()
    }
    contents["manifest.json"] = json.dumps(
        {
            "release": release,
            "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
        },
        sort_keys=True,
    ).encode()
    with tarfile.open(output, "w") as archive:
        for name, data in contents.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))


if __name__ == "__main__":
    build(sys.argv[1], Path(sys.argv[2]))
