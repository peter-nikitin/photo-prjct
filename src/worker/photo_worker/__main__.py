"""Run the independently packaged, single-concurrency photo worker."""

from __future__ import annotations

import logging

from photo_worker.lifecycle import FleetLifecycle, HostIdentity, install_signal_handlers
from photo_worker.runner import Worker, WorkerConfig
from photo_worker.telemetry import RuntimeTelemetry, start_runtime_server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config, client = WorkerConfig.from_env()
    worker = Worker(client, config)
    install_signal_handlers(worker)
    runtime_server = None
    try:
        if config.remote_pool is not None:
            identity = HostIdentity.read(pool=config.remote_pool, build=config.worker_build)
            worker.fleet = FleetLifecycle(client, identity, worker.drain)
            if config.runtime_telemetry_enabled:
                try:
                    worker.telemetry = RuntimeTelemetry(
                        lambda: (
                            worker.fleet.registration_generation
                            if worker.fleet is not None
                            else None
                        )
                    )
                    # Container namespace only; compose publishes solely to host loopback.
                    runtime_server = start_runtime_server(worker.telemetry, host="0.0.0.0")
                except Exception:
                    logging.getLogger(__name__).warning("worker_runtime_endpoint_unavailable")
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
            if runtime_server is not None:
                runtime_server.shutdown()
                runtime_server.server_close()


if __name__ == "__main__":
    main()
