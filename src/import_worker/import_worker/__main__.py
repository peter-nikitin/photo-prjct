"""Run with only an internal endpoint, dedicated token and temporary-directory mount."""

import os
import sys
from pathlib import Path

from import_worker.client import APIClient
from import_worker.contracts import Config
from import_worker.runner import Runner
from import_worker.source import DiskSource
from import_worker.transport import Transport


def main() -> None:
    config = Config(
        os.environ["PHOTO_IMPORT_API_URL"],
        os.environ["PHOTO_IMPORT_WORKER_TOKEN"],
        Path(os.environ.get("PHOTO_IMPORT_TEMP_DIR", "/tmp/photo-import")),
    )
    transport = Transport()
    client = APIClient(config, transport)
    if client.call("readiness").get("ready") is not True:
        raise SystemExit("Import API is not ready.")
    if sys.argv[1:] == ["--check-ready"]:
        return
    if sys.argv[1:]:
        raise SystemExit("Unsupported import worker arguments.")
    Runner(config, client, DiskSource(transport), transport).run()


if __name__ == "__main__":
    main()
