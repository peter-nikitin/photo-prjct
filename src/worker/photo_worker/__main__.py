"""Run the independently packaged photo worker."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Barrier

from photo_worker.face_embedding import warm_models
from photo_worker.lifecycle import FleetLifecycle, HostIdentity, install_signal_handlers
from photo_worker.runner import Worker, WorkerConfig
from photo_worker.telemetry import RuntimeTelemetry, start_runtime_server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config, client = WorkerConfig.from_env()
    worker = Worker(client, config)
    install_signal_handlers(worker)
    runtime_server = None
    executor = ThreadPoolExecutor(max_workers=2) if config.concurrency == 2 else None

    def warm_parallel_models() -> None:
        assert executor is not None
        barrier = Barrier(2)

        def warm_slot() -> None:
            try:
                warm_models()
                barrier.wait(timeout=300)
            except BaseException:
                barrier.abort()
                raise

        futures = [executor.submit(warm_slot) for _ in range(2)]
        for future in futures:
            future.result()

    try:
        if config.remote_pool is not None:
            identity = HostIdentity.read(pool=config.remote_pool, build=config.worker_build)
            worker.fleet = FleetLifecycle(
                client,
                identity,
                worker.drain,
                warmup=warm_parallel_models if executor is not None else None,
            )
            if config.runtime_telemetry_enabled:
                try:
                    worker.telemetry = RuntimeTelemetry(
                        lambda: (
                            worker.fleet.registration_generation
                            if worker.fleet is not None
                            else None
                        ),
                        ready=lambda: worker.fleet is not None and worker.fleet.can_claim,
                    )
                    # Container namespace only; compose publishes solely to host loopback.
                    runtime_server = start_runtime_server(worker.telemetry, host="0.0.0.0")
                except Exception:
                    logging.getLogger(__name__).warning("worker_runtime_endpoint_unavailable")
            worker.fleet.start()
        if executor is None:
            worker.run_forever()
        else:
            workers = [worker]
            for _ in range(config.concurrency - 1):
                slot = Worker(client, config, drain=worker.drain, telemetry=worker.telemetry)
                slot.fleet = worker.fleet
                workers.append(slot)
            futures = [executor.submit(slot.run_forever) for slot in workers]
            for future in as_completed(futures):
                try:
                    future.result()
                except BaseException:
                    worker.request_drain()
                    raise
                if not worker.draining.is_set():
                    worker.request_drain()
                    raise RuntimeError("worker_slot_stopped")
    finally:
        if executor is not None:
            if not worker.draining.is_set():
                worker.request_drain()
            executor.shutdown(wait=True)
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
