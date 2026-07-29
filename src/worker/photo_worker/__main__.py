"""Run the independently packaged, single-concurrency photo worker."""

from __future__ import annotations

import logging

from photo_worker.runner import Worker, WorkerConfig


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config, client = WorkerConfig.from_env()
    Worker(client, config).run_forever()


if __name__ == "__main__":
    main()
