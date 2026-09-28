"""Run the independently packaged, single-concurrency photo worker."""

from __future__ import annotations

import logging

from photo_worker.lifecycle import FleetLifecycle, HostIdentity, install_signal_handlers
from photo_worker.runner import Worker, WorkerConfig


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config, client = WorkerConfig.from_env()
    worker = Worker(client, config)
    install_signal_handlers(worker)
    try:
        if config.remote_pool is not None:
            identity = HostIdentity.read(pool=config.remote_pool, build=config.worker_build)
            worker.fleet = FleetLifecycle(client, identity, worker.drain)
            worker.fleet.start()
        worker.run_forever()
    finally:
        try:
            if worker.fleet is not None:
                try:
                    if worker.draining.is_set():
                        worker.fleet.pulse()
                finally:
                    worker.fleet.close()
        finally:
            worker.drain.completed.set()


if __name__ == "__main__":
    main()
