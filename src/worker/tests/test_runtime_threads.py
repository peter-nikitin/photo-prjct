"""Thread ceilings protect the real single-worker 64-PID deployment boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_import_caps_native_library_pools_before_they_start():
    script = (
        "import photo_worker, os, json\n"
        "names = ['OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', "
        "'OPENCV_FOR_THREADS_NUM']\n"
        "print(json.dumps({k: os.environ[k] for k in names}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env=os.environ
        | {"PYTHONPATH": str(Path(__file__).parents[1]), "OPENBLAS_NUM_THREADS": "64"},
    )
    limits = json.loads(result.stdout)
    assert set(limits.values()) <= {"1", "2"}


def test_bulk_parallel_slots_each_use_one_native_cpu_thread():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from photo_worker.runtime_threads import CPU_THREADS; print(CPU_THREADS)",
        ],
        capture_output=True,
        text=True,
        check=True,
        env=os.environ
        | {
            "PYTHONPATH": str(Path(__file__).parents[1]),
            "PHOTO_WORKER_TRANSPORT": "remote",
            "PHOTO_WORKER_POOL": "bulk",
        },
    )
    assert result.stdout.strip() == "1"


@pytest.mark.parametrize(
    ("quota", "types", "identities", "expected_slots", "expected_threads"),
    [
        ("200000 100000", "selfie_query", "1/selfie_query/2", 2, 1),
        ("100000 100000", "selfie_query", "1/selfie_query/2", 1, 1),
        ("max 100000", "selfie_query", "1/selfie_query/2", 1, 1),
        ("max 100000", "bib_recognition", "1/bib_recognition/1", 1, 1),
        (
            "max 100000",
            "bib_recognition,capture_metadata,generate_preview",
            "1/bib_recognition/1,1/capture_metadata/2,2/generate_preview/1",
            2,
            1,
        ),
        (
            "200000 100000",
            "capture_metadata,selfie_query",
            "1/capture_metadata/2,1/selfie_query/2",
            1,
            2,
        ),
    ],
)
def test_local_worker_slots_and_native_pools_share_the_cpu_budget(
    quota, types, identities, expected_slots, expected_threads
):
    script = """
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

read_text = Path.read_text

def read_with_cpu_quota(path, *args, **kwargs):
    if path == Path("/sys/fs/cgroup/cpu.max"):
        return sys.argv[1]
    return read_text(path, *args, **kwargs)

with patch.object(Path, "read_text", read_with_cpu_quota):
    from photo_worker.runner import WorkerConfig
    from photo_worker.runtime_threads import CPU_THREADS

config, _ = WorkerConfig.from_env()
names = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENCV_FOR_THREADS_NUM")
native_threads = [int(os.environ[key]) for key in names]
print(json.dumps([config.concurrency, CPU_THREADS, native_threads]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, quota],
        capture_output=True,
        text=True,
        check=True,
        env=os.environ
        | {
            "PYTHONPATH": str(Path(__file__).parents[1]),
            "PHOTO_WORKER_API_URL": "http://web:8000/internal/photo-processing/v1",
            "PHOTO_WORKER_TOKEN": "worker-token",
            "PHOTO_WORKER_TRANSPORT": "local",
            "PHOTO_WORKER_POOL": "",
            "PHOTO_WORKER_PROCESSOR_TYPES": types,
            "PHOTO_WORKER_PROCESSOR_IDENTITIES": identities,
        },
    )
    assert json.loads(result.stdout) == [expected_slots, expected_threads, [expected_threads] * 4]


@pytest.mark.parametrize(
    ("quota", "expected"),
    [("200000 100000", 2), ("100000 100000", 1), ("max 100000", 1), ("400000 100000", 2)],
)
def test_cpu_budget_is_bounded_by_container_quota(tmp_path, quota, expected):
    from photo_worker.runtime_threads import cpu_thread_budget

    path = tmp_path / "cpu.max"
    path.write_text(quota)
    assert cpu_thread_budget(path) == expected


def test_scrfd_session_uses_bounded_sequential_pools(monkeypatch):
    from photo_worker.scrfd import _load_session

    captured = {}

    class Options:
        pass

    def session(path, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(
            SessionOptions=Options,
            ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="sequential"),
            InferenceSession=session,
        ),
    )
    _load_session(Path("/models/det.onnx"))
    options = captured["sess_options"]
    assert options.intra_op_num_threads in {1, 2}
    assert options.inter_op_num_threads == 1
    assert options.execution_mode == "sequential"


def test_torch_pools_are_bounded_once_before_loading_models(monkeypatch):
    from photo_worker import runtime_threads

    calls = []
    torch = SimpleNamespace(
        set_num_threads=lambda n: calls.append(("intra", n)),
        set_num_interop_threads=lambda n: calls.append(("inter", n)),
    )
    monkeypatch.setattr(runtime_threads, "_TORCH_CONFIGURED", False)
    runtime_threads.configure_torch(torch)
    runtime_threads.configure_torch(torch)
    assert calls == [("intra", runtime_threads.CPU_THREADS), ("inter", 1)]
