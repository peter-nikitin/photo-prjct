"""Run with only an internal endpoint, dedicated token and temporary-directory mount."""

import os
import signal
import sys
from pathlib import Path
from uuid import UUID

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
    target_batch_id = None
    if sys.argv[1:]:
        if len(sys.argv) != 3 or sys.argv[1] != "--batch-id":
            raise SystemExit("Usage: python -m import_worker [--batch-id UUID]")
        try:
            target_batch_id = str(UUID(sys.argv[2]))
        except ValueError:
            raise SystemExit("Batch ID must be a UUID") from None
    runner = Runner(config, client, DiskSource(transport), transport)
    signal.signal(signal.SIGTERM, lambda *_: runner.request_stop())
    signal.signal(signal.SIGINT, lambda *_: runner.request_stop())
    if target_batch_id is not None:
        try:
            if not runner.run_once(batch_id=target_batch_id):
                raise SystemExit("Exact import batch has no eligible work")
        finally:
            runner.close()
        return
    runner.run()


if __name__ == "__main__":
    main()
