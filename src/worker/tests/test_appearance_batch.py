import os
import signal
import time


def run_test_slot(pipe):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    pipe.send({"pid": os.getpid(), "native_threads": 1})
    while (payload := pipe.recv()) is not None:
        job = payload["job"]
        if job["attempt_id"] == "slow":
            time.sleep(0.8)
        if job["attempt_id"] == "crash":
            os._exit(4)
        pipe.send({"attempt_id": job["attempt_id"], "pid": os.getpid(), "wall_ms": 1})


def test_spawn_pool_keeps_one_in_flight_per_process_and_refills_ready_slot():
    import pytest
    from photo_worker.appearance_batch import AppearancePool, BatchError

    with AppearancePool(2, run_test_slot) as pool:
        assert len({runtime["pid"] for runtime in pool.runtime}) == 2
        pool.submit({"job": {"attempt_id": "slow"}})
        pool.submit({"job": {"attempt_id": "fast"}})
        with pytest.raises(BatchError, match="pool_full"):
            pool.submit({"job": {"attempt_id": "overflow"}})
        fast = pool.next_result()
        assert fast["attempt_id"] == "fast"
        pool.submit({"job": {"attempt_id": "refill"}})
        assert pool.next_result() == {"attempt_id": "refill", "pid": fast["pid"], "wall_ms": 1}
        assert pool.next_result()["pid"] != fast["pid"]


def test_spawn_pool_reports_crashed_attempt_and_drains_other_slot():
    from photo_worker.appearance_batch import AppearancePool

    with AppearancePool(2, run_test_slot) as pool:
        pool.submit({"job": {"attempt_id": "crash"}})
        pool.submit({"job": {"attempt_id": "slow"}})
        assert pool.next_result()["error"] == "worker_exited"
        assert pool.next_result()["attempt_id"] == "slow"
    assert all(slot.process.exitcode is not None for slot in pool.slots)


def test_worker_cli_starts_native_thread_limited_children_and_drains_on_eof():
    import json
    import subprocess
    import sys
    from pathlib import Path

    result = subprocess.run(
        [sys.executable, "-m", "photo_worker.appearance_batch", "--processes", "2"],
        input="",
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            "PHOTO_WORKER_API_URL": "http://web:8000/internal/photo-processing/v1",
            "PHOTO_WORKER_TOKEN": "local-test-token",
            "PHOTO_WORKER_BUILD": "test",
            "PHOTO_WORKER_TRANSPORT": "local",
            "OPENBLAS_NUM_THREADS": "4",
            "OPENCV_FOR_THREADS_NUM": "4",
        },
        text=True,
        capture_output=True,
        timeout=25,
    )
    assert result.returncode == 0, result.stderr
    ready, drained = [json.loads(row) for row in result.stdout.splitlines()]
    runtime = ready["runtime"]
    assert {row["native_threads"] for row in runtime} == {1}
    assert {row["opencv_threads"] for row in runtime} == {1}
    assert len({row["pid"] for row in runtime}) == 2
    assert drained == {"drained": True}
