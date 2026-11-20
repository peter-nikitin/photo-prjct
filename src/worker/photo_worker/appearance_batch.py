"""Run only the claims supplied by the bounded operator session."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection, wait
from time import monotonic
from types import TracebackType
from typing import Any, cast

MAX_LINE_BYTES = 384 * 1024
START_TIMEOUT = 90
DRAIN_TIMEOUT = 60
JOB_TIMEOUT = 300


class BatchError(ValueError):
    """A fixed failure code for the operator channel."""


def run_slot(pipe: Connection) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())

    import cv2

    from photo_worker.contracts import PERSON_APPEARANCE_IDENTITY, Claim
    from photo_worker.runner import Worker, WorkerConfig
    from photo_worker.runtime_threads import CPU_THREADS

    cv2.setNumThreads(0)  # Disable OpenCV's parallel regions inside each process.
    config, client = WorkerConfig.from_env()
    if config.remote_pool is not None or CPU_THREADS != 1:
        raise BatchError("invalid_worker_runtime")
    worker = Worker(client, config)
    pid = os.getpid()
    pipe.send(
        {
            "pid": pid,
            "native_threads": CPU_THREADS,
            "opencv_threads": cv2.getNumThreads(),
        }
    )
    try:
        while (payload := pipe.recv()) is not None:
            started = monotonic()
            result: dict[str, Any] = {"attempt_id": payload["job"]["attempt_id"], "pid": pid}
            try:
                job = Claim.from_response(payload).job
                if (
                    job is None
                    or (job.contract_version, job.processor_type, job.processor_version)
                    != PERSON_APPEARANCE_IDENTITY
                ):
                    raise BatchError("invalid_appearance_claim")
                worker._process(job)
            except Exception:
                result["error"] = "worker_execution_failed"
            result["wall_ms"] = round((monotonic() - started) * 1000)
            pipe.send(result)
    except EOFError:
        pass
    finally:
        pipe.close()


@dataclass
class Slot:
    process: Any
    pipe: Connection
    attempt_id: str | None = None
    deadline: float = 0.0


class AppearancePool:
    """A fixed set of spawn processes, with at most one leased photo per process."""

    def __init__(self, processes: int, target: Callable[[Connection], None] = run_slot) -> None:
        if not 1 <= processes <= 6:
            raise BatchError("invalid_process_count")
        self.processes = processes
        self.target = target
        self.slots: list[Slot] = []
        self.runtime: list[dict[str, Any]] = []

    def __enter__(self) -> AppearancePool:
        context = multiprocessing.get_context("spawn")
        deadline = monotonic() + START_TIMEOUT
        try:
            for _ in range(self.processes):
                parent, child = context.Pipe()
                with child:
                    process = context.Process(target=self.target, args=(child,))
                    try:
                        process.start()
                    except BaseException:
                        parent.close()
                        raise
                self.slots.append(Slot(process, parent))
            for slot in self.slots:
                if not slot.pipe.poll(max(0, deadline - monotonic())):
                    raise BatchError("worker_start_timeout")
                self.runtime.append(slot.pipe.recv())
        except BaseException:
            self.close()
            raise
        return self

    def submit(self, payload: dict[str, Any]) -> None:
        job = payload.get("job")
        attempt_id = job.get("attempt_id") if isinstance(job, dict) else None
        if not isinstance(attempt_id, str) or any(
            slot.attempt_id == attempt_id for slot in self.slots
        ):
            raise BatchError("invalid_appearance_claim")
        for slot in self.slots:
            if slot.attempt_id is None and slot.process.is_alive():
                slot.pipe.send(payload)
                slot.attempt_id = attempt_id
                slot.deadline = monotonic() + JOB_TIMEOUT
                return
        raise BatchError("pool_full")

    def next_result(self) -> dict[str, Any]:
        busy = {slot.pipe: slot for slot in self.slots if slot.attempt_id is not None}
        if not busy:
            raise BatchError("pool_empty")
        oldest = min(busy.values(), key=lambda slot: slot.deadline)
        ready = wait(busy, timeout=max(0, oldest.deadline - monotonic()))
        if not ready:
            raise BatchError("worker_job_timeout")
        slot = busy[cast(Connection, ready[0])]
        try:
            result = slot.pipe.recv()
        except (EOFError, OSError):
            result = {
                "attempt_id": slot.attempt_id,
                "pid": slot.process.pid,
                "error": "worker_exited",
            }
        slot.attempt_id = None
        return result

    def close(self) -> None:
        deadline = monotonic() + DRAIN_TIMEOUT
        for slot in self.slots:
            try:
                slot.pipe.send(None)
            except (BrokenPipeError, EOFError, OSError):
                pass
        for slot in self.slots:
            slot.process.join(timeout=max(0, deadline - monotonic()))
        for slot in self.slots:
            if slot.process.is_alive():
                slot.process.terminate()
        for slot in self.slots:
            slot.process.join(timeout=5)
            if slot.process.is_alive():
                slot.process.kill()
                slot.process.join()
            slot.pipe.close()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def _interrupt(_signum: int, _frame: object) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise BatchError("worker_interrupted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processes", required=True, type=int, choices=range(1, 7))
    args = parser.parse_args()

    # Spawn children import the package afresh, setting native thread limits before models load.
    os.environ["PHOTO_WORKER_APPEARANCE_BATCH"] = "1"
    signal.signal(signal.SIGINT, _interrupt)
    signal.signal(signal.SIGTERM, _interrupt)
    pool = AppearancePool(args.processes)
    try:
        with pool:
            emit({"runtime": pool.runtime})
            while raw := sys.stdin.buffer.readline(MAX_LINE_BYTES + 1):
                if len(raw) > MAX_LINE_BYTES or not raw.endswith(b"\n"):
                    raise BatchError("invalid_command")
                command = json.loads(raw)
                if command == {"op": "next"}:
                    emit(pool.next_result())
                elif (
                    isinstance(command, dict)
                    and command.keys() == {"op", "claim"}
                    and command["op"] == "submit"
                    and isinstance(command["claim"], dict)
                ):
                    pool.submit(command["claim"])
                else:
                    raise BatchError("invalid_command")
    except BatchError as error:
        emit({"error": str(error)})
        return 1
    except Exception:
        emit({"error": "worker_protocol_failed"})
        return 1
    finally:
        if all(slot.process.exitcode is not None for slot in pool.slots):
            emit({"drained": True})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
