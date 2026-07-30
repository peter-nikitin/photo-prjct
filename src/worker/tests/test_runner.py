from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from photo_worker.client import ApiError, DownloadError
from photo_worker.contracts import (
    CaptureMetadataResult,
    Claim,
    FaceEmbeddingFace,
    FaceEmbeddingResult,
    PROCESSOR_TYPE,
    PROCESSOR_TYPE_FACE_EMBEDDING,
)
from photo_worker.face_embedding import FaceEmbeddingError
from photo_worker.runner import Worker, WorkerConfig, _LeaseKeeper, _lifecycle
from PIL import Image


def configuration(
    *, processor_type: str = PROCESSOR_TYPE, heartbeat_interval_seconds: int = 30
) -> dict[str, object]:
    return {
        "retry_policy": {
            "max_attempts": 3,
            "base_backoff_seconds": 30,
            "max_backoff_seconds": 300,
            "jitter_seconds": 5,
            "lease_max_seconds": 300,
        },
        "max_cohort_size": 20,
        "report_max_bytes": 262_144,
        "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
        **(
            {
                "capture_metadata": {
                    "date_field_precedence": ["DateTimeOriginal", "DateTimeDigitized", "DateTime"],
                    "normalization": "utc_assume_utc_if_missing",
                }
            }
            if processor_type == PROCESSOR_TYPE
            else {"face_embedding": {"max_faces": 2, "detection_threshold": 0.75}}
        ),
        "worker": {
            "concurrency": 1,
            "api_response_max_bytes": 16_384,
            "heartbeat_interval_seconds": heartbeat_interval_seconds,
            "lease_duration_seconds": 120,
            "max_input_bytes": 52_428_800,
            "max_pixels": 100_000_000,
            "poll_min_delay_seconds": 5,
            "terminal_result_max_bytes": 8_192,
        },
    }


def make_claim(
    *, processor_type: str = PROCESSOR_TYPE, heartbeat_interval_seconds: int = 30
) -> Claim:
    return Claim.from_response(
        {
            "empty": False,
            "job": {
                "id": "00000000-0000-0000-0000-000000000011",
                "attempt_id": "00000000-0000-0000-0000-000000000012",
                "contract_version": 1,
                "processor_type": processor_type,
                "processor_version": 1,
                "configuration": configuration(
                    processor_type=processor_type,
                    heartbeat_interval_seconds=heartbeat_interval_seconds
                ),
                "photo_id": "photo-1",
                "event_id": "00000000-0000-0000-0000-000000000013",
                "run_id": "00000000-0000-0000-0000-000000000014",
                "input_fingerprint": {
                    "original_key": "originals/0123456789abcdef0123456789abcdef",
                    "original_size": 1024,
                    "original_content_type": "image/jpeg",
                    "verified_source_etag": None,
                    "version_evidence": "unavailable",
                },
                "input_limits": {"max_bytes": 1024, "content_type": "image/jpeg"},
                "lease_expires_at": "2026-07-29T10:03:00+00:00",
                "download_url": "https://storage.example.test/x?signature=secret",
                "download_expires_at": "2026-07-29T10:01:00+00:00",
            },
        }
    )


class Client:
    def __init__(self, claim: Claim) -> None:
        self.claim = claim
        self.completed: list[dict[str, object]] = []
        self.failed: list[dict[str, object]] = []
        self.heartbeats: list[str] = []

    def claim_job(self, **_: object) -> Claim:
        return self.claim

    def download(
        self,
        _url: str,
        destination: Path,
        *,
        max_bytes: int,
        expected_size: int,
        expected_etag: str | None = None,
    ) -> int:
        assert expected_etag is None
        image = Image.new("RGB", (2, 2), "white")
        image.save(destination, "JPEG")
        image.close()
        assert max_bytes == expected_size == 1024
        return expected_size

    def heartbeat(self, attempt_id: str, **_: object) -> None:
        self.heartbeats.append(attempt_id)

    def refresh_download(self, _attempt_id: str) -> str:
        return "https://storage.example.test/refreshed?secret"

    def complete(self, attempt_id: str, payload: dict[str, object], **_: object) -> None:
        assert attempt_id == "00000000-0000-0000-0000-000000000012"
        self.completed.append(payload)

    def fail(self, _attempt_id: str, payload: dict[str, object], **_: object) -> None:
        self.failed.append(payload)


def make_face_embedding_result() -> FaceEmbeddingResult:
    return FaceEmbeddingResult(
        model="sface",
        faces=(
            FaceEmbeddingFace(
                index=0,
                bbox=(1.0, 2.0, 32.0, 32.0),
                confidence=0.96,
                landmarks=((1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0), (9.0, 10.0)),
                embedding=tuple(float(i) / 128 for i in range(128)),
            ),
        ),
        has_single_query_face_usable=True,
        warnings=(),
        timings={"decode_ms": 1, "model_load_ms": 2, "detect_ms": 3, "embed_ms": 4, "total_ms": 10},
    )


def test_worker_processes_one_claim_then_submits_typed_result_and_removes_temp_file(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    client = Client(make_claim())
    worker = Worker(
        client,
        WorkerConfig(worker_build="worker-test", lease_seconds=60, temp_dir=tmp_path),
    )

    delay = worker.run_once()

    assert delay is None
    assert client.heartbeats == []
    assert client.completed[0]["outcome"] == "success"
    assert client.completed[0]["result"] == CaptureMetadataResult.missing().as_payload()
    assert {
        "job_id",
        "attempt_id",
        "contract_version",
        "processor_type",
        "processor_version",
        "worker_build",
        "started_at",
        "finished_at",
        "download_ms",
        "compute_ms",
        "total_ms",
        "outcome",
        "result",
    } == set(client.completed[0])
    assert client.completed[0]["job_id"] == "00000000-0000-0000-0000-000000000011"
    assert client.completed[0]["attempt_id"] == "00000000-0000-0000-0000-000000000012"
    assert str(client.completed[0]["started_at"]).endswith("Z")
    assert str(client.completed[0]["finished_at"]).endswith("Z")
    assert len(json.dumps(client.completed[0], separators=(",", ":")).encode()) <= 8_192
    assert list(tmp_path.iterdir()) == []
    assert "phase=succeeded" in caplog.text
    assert "signature=secret" not in caplog.text


def test_worker_processes_face_embedding_claim_and_submits_typed_result(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level("INFO")
    client = Client(make_claim(processor_type=PROCESSOR_TYPE_FACE_EMBEDDING))
    monkeypatch.setattr(
        "photo_worker.runner.extract_face_embeddings",
        lambda *_args, **_kwargs: make_face_embedding_result(),
    )
    worker = Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            temp_dir=tmp_path,
            processor_type=PROCESSOR_TYPE_FACE_EMBEDDING,
        ),
    )

    delay = worker.run_once()

    assert delay is None
    assert client.completed[0]["outcome"] == "success"
    assert client.completed[0]["processor_type"] == PROCESSOR_TYPE_FACE_EMBEDDING
    assert client.completed[0]["result"]["faces"][0]["index"] == 0
    assert client.completed[0]["result"]["has_single_query_face_usable"] is True
    assert len(json.dumps(client.completed[0], separators=(",", ":")).encode()) <= 8_192
    assert list(tmp_path.iterdir()) == []
    assert "phase=succeeded" in caplog.text


def test_worker_maps_model_inference_timeout_to_retryable_failure_for_face_embedding(
    tmp_path: Path,
) -> None:
    client = Client(make_claim(processor_type=PROCESSOR_TYPE_FACE_EMBEDDING))
    worker = Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            temp_dir=tmp_path,
            processor_type=PROCESSOR_TYPE_FACE_EMBEDDING,
        ),
    )

    import photo_worker.runner as runner_module

    original = runner_module.extract_face_embeddings
    runner_module.extract_face_embeddings = (  # type: ignore[assignment]
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FaceEmbeddingError("model_inference_timeout")
        )
    )
    try:
        worker.run_once()
    finally:
        runner_module.extract_face_embeddings = original  # type: ignore[assignment]

    assert client.failed[0]["error_code"] == "model_inference_timeout"
    assert client.failed[0]["retryable"] is True
    assert list(tmp_path.iterdir()) == []


def test_worker_returns_server_delay_for_an_empty_claim() -> None:
    client = Client(Claim.empty(7))
    worker = Worker(client, WorkerConfig(worker_build="worker-test", lease_seconds=60))

    assert worker.run_once() == 7


def test_claimed_configuration_sets_the_next_poll_delay(tmp_path: Path) -> None:
    client = Client(make_claim())
    worker = Worker(
        client,
        WorkerConfig(worker_build="worker-test", lease_seconds=60, temp_dir=tmp_path),
    )

    worker.run_once()

    assert worker._next_poll_delay_seconds == 5


def test_worker_configuration_rejects_parallel_concurrency() -> None:
    with pytest.raises(ValueError, match="concurrency"):
        WorkerConfig(worker_build="worker-test", lease_seconds=60, concurrency=2)


def test_environment_configuration_rejects_an_empty_worker_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOTO_WORKER_API_URL", "http://web:8000/internal/photo-processing/v1")
    monkeypatch.setenv("PHOTO_WORKER_TOKEN", "")

    with pytest.raises(ValueError, match="worker API URL and token are required"):
        WorkerConfig.from_env()


@pytest.mark.parametrize("minimum,maximum", [(0, 1), (1, 0), (2, 1), (float("nan"), 1), ("bad", 1)])
def test_worker_configuration_rejects_invalid_backoff_bounds(
    minimum: float, maximum: float
) -> None:
    with pytest.raises(ValueError, match="backoff"):
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            minimum_delay_seconds=minimum,
            maximum_backoff_seconds=maximum,
        )


def test_lifecycle_log_redacts_hostile_external_values_and_worker_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    job = make_claim().job
    assert job is not None

    _lifecycle(
        "started",
        replace(job, photo_id="https://storage.example.test/x?token=worker-secret"),
        secrets=("worker-secret",),
    )

    assert "storage.example" not in caplog.text
    assert "worker-secret" not in caplog.text


class ExpiringClient(Client):
    def __init__(self, claim: Claim) -> None:
        super().__init__(claim)
        self.download_calls = 0
        self.refreshed: list[str] = []

    def download(self, *_: object, **__: object) -> int:
        self.download_calls += 1
        raise DownloadError("download_authorization_expired", retryable=True)

    def refresh_download(self, attempt_id: str, **_: object) -> str:
        self.refreshed.append(attempt_id)
        return "https://storage.example.test/refreshed?secret"


def test_second_expired_download_is_submitted_with_django_accepted_failure_pair(
    tmp_path: Path,
) -> None:
    client = ExpiringClient(make_claim())

    Worker(
        client, WorkerConfig(worker_build="worker-test", lease_seconds=60, temp_dir=tmp_path)
    ).run_once()

    assert client.download_calls == 2
    assert client.refreshed == ["00000000-0000-0000-0000-000000000012"]
    assert client.heartbeats == []
    assert (client.failed[0]["error_code"], client.failed[0]["retryable"]) == (
        "download_authorization_expired",
        True,
    )
    assert client.failed[0]["download_ms"] >= 0
    assert list(tmp_path.iterdir()) == []


class DownloadFailureClient(Client):
    def download(self, _url: str, destination: Path, **_: object) -> int:
        destination.write_bytes(b"partial")
        raise DownloadError("network_interruption", retryable=True)


def test_download_failure_keeps_elapsed_phase_duration_and_cleans_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = DownloadFailureClient(make_claim())
    times = iter([0.0, 1.0, 2.5, 3.0])
    monkeypatch.setattr("photo_worker.runner.monotonic", lambda: next(times))

    Worker(
        client, WorkerConfig(worker_build="worker-test", lease_seconds=60, temp_dir=tmp_path)
    ).run_once()

    assert client.failed[0]["download_ms"] == 1500
    assert client.failed[0]["compute_ms"] == 0
    assert (client.failed[0]["error_code"], client.failed[0]["retryable"]) == (
        "network_interruption",
        True,
    )
    assert list(tmp_path.iterdir()) == []


def test_decode_failure_keeps_elapsed_phase_duration_and_cleans_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Client(make_claim())
    times = iter([0.0, 1.0, 2.0, 3.0, 4.25, 5.0])
    monkeypatch.setattr("photo_worker.runner.monotonic", lambda: next(times))
    monkeypatch.setattr(
        "photo_worker.runner.extract_capture_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            DownloadError("decode_failed", retryable=False)
        ),
    )

    Worker(
        client, WorkerConfig(worker_build="worker-test", lease_seconds=60, temp_dir=tmp_path)
    ).run_once()

    assert client.failed[0]["download_ms"] == 1000
    assert client.failed[0]["compute_ms"] == 1250
    assert client.failed[0]["error_code"] == "decode_failed"
    assert list(tmp_path.iterdir()) == []


def test_non_retryable_api_error_stops_polling_without_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = Worker(
        Client(Claim.empty(1)), WorkerConfig(worker_build="worker-test", lease_seconds=60)
    )
    monkeypatch.setattr(
        worker,
        "run_once",
        lambda: (_ for _ in ()).throw(ApiError("worker_unauthorized", retryable=False)),
    )
    sleeps: list[float] = []
    monkeypatch.setattr("photo_worker.runner.time.sleep", sleeps.append)

    worker.run_forever()

    assert sleeps == []


def test_transient_api_error_sleeps_then_repolls_before_fatal_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = Worker(
        Client(Claim.empty(1)), WorkerConfig(worker_build="worker-test", lease_seconds=60)
    )
    calls = 0

    def run_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ApiError("storage_unavailable", retryable=True)
        raise ApiError("worker_unauthorized", retryable=False)

    sleeps: list[float] = []
    monkeypatch.setattr(worker, "run_once", run_once)
    monkeypatch.setattr("photo_worker.runner.time.sleep", sleeps.append)
    monkeypatch.setattr(worker, "_backoff_delay", lambda _failures: 2.0)

    worker.run_forever()

    assert calls == 2
    assert sleeps == [2.0]


def test_backoff_stays_inside_configured_jitter_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = Worker(
        Client(Claim.empty(1)),
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            minimum_delay_seconds=1,
            maximum_backoff_seconds=10,
        ),
    )
    monkeypatch.setattr("photo_worker.runner.random.uniform", lambda _low, _high: 0.5)
    lower = worker._backoff_delay(3)
    monkeypatch.setattr("photo_worker.runner.random.uniform", lambda _low, _high: 1.0)
    upper = worker._backoff_delay(3)

    assert (lower, upper) == (4, 8)


class InlineThread:
    """A deterministic thread substitute for lease-keeper behavior tests."""

    def __init__(self, *, target: Callable[[], None], daemon: bool) -> None:
        self.target = target
        self.daemon = daemon
        self.started = False
        self.join_count = 0

    def start(self) -> None:
        self.started = True
        self.target()

    def join(self) -> None:
        self.join_count += 1


def deterministic_keeper_factory(
    wait_results: list[bool], threads: list[InlineThread]
) -> Callable[[object, object, int], _LeaseKeeper]:
    waits = iter(wait_results)

    def factory(client: object, job: object, lease_seconds: int) -> _LeaseKeeper:
        def thread_factory(*, target: Callable[[], None], daemon: bool) -> InlineThread:
            thread = InlineThread(target=target, daemon=daemon)
            threads.append(thread)
            return thread

        return _LeaseKeeper(  # type: ignore[arg-type]
            client,
            job,
            lease_seconds,
            wait_for_interval=lambda _interval: next(waits),
            thread_factory=thread_factory,  # type: ignore[arg-type]
        )

    return factory


def test_lease_keeper_sends_one_deterministic_heartbeat_and_joins(tmp_path: Path) -> None:
    client = Client(make_claim())
    threads: list[InlineThread] = []

    Worker(
        client,
        WorkerConfig(worker_build="worker-test", lease_seconds=60, temp_dir=tmp_path),
        lease_keeper_factory=deterministic_keeper_factory([False, True], threads),
    ).run_once()

    assert client.heartbeats == ["00000000-0000-0000-0000-000000000012"]
    assert [(thread.started, thread.join_count) for thread in threads] == [(True, 1)]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("boundary", ["heartbeat", "refresh", "complete", "fail"])
def test_lease_loss_abandons_only_current_attempt_and_cleans_temp_file(
    tmp_path: Path,
    boundary: str,
) -> None:
    class LeaseLossClient(Client):
        def __init__(self, claim: Claim) -> None:
            super().__init__(claim)
            self.complete_attempts = 0
            self.fail_attempts = 0

        def complete(self, *_: object, **__: object) -> None:
            self.complete_attempts += 1
            if boundary == "complete":
                raise ApiError("lease_not_current", retryable=False)
            super().complete("00000000-0000-0000-0000-000000000012", {})

        def fail(self, *_: object, **__: object) -> None:
            self.fail_attempts += 1
            if boundary == "fail":
                raise ApiError("lease_not_current", retryable=False)
            super().fail("00000000-0000-0000-0000-000000000012", {})

        def refresh_download(self, *_: object, **__: object) -> str:
            raise ApiError("lease_not_current", retryable=False)

        def download(self, *args: object, **kwargs: object) -> int:
            if boundary == "refresh":
                raise DownloadError("download_authorization_expired", retryable=True)
            if boundary == "fail":
                raise DownloadError("unsupported_input", retryable=False)
            return super().download(*args, **kwargs)

        def heartbeat(self, *_: object, **__: object) -> None:
            super().heartbeat("00000000-0000-0000-0000-000000000012")
            if boundary == "heartbeat":
                raise ApiError("lease_not_current", retryable=False)

    client = LeaseLossClient(make_claim())
    threads: list[InlineThread] = []
    Worker(
        client,
        WorkerConfig(worker_build="worker-test", lease_seconds=60, temp_dir=tmp_path),
        lease_keeper_factory=deterministic_keeper_factory(
            [False] if boundary == "heartbeat" else [True], threads
        ),
    ).run_once()

    assert client.heartbeats == (
        ["00000000-0000-0000-0000-000000000012"] if boundary == "heartbeat" else []
    )
    assert (client.complete_attempts, client.fail_attempts) == {
        "heartbeat": (0, 0),
        "refresh": (0, 0),
        "complete": (1, 0),
        "fail": (0, 1),
    }[boundary]
    assert client.completed == []
    assert client.failed == []
    assert [(thread.started, thread.join_count) for thread in threads] == [(True, 1)]
    assert list(tmp_path.iterdir()) == []


def test_run_forever_claims_again_after_attempt_local_lease_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SequenceClient(Client):
        def __init__(self) -> None:
            super().__init__(make_claim())
            self.claims = 0
            self.complete_attempts = 0

        def claim_job(self, **_: object) -> Claim:
            self.claims += 1
            if self.claims == 1:
                return self.claim
            raise ApiError("worker_unauthorized", retryable=False)

        def complete(self, *_: object, **__: object) -> None:
            self.complete_attempts += 1
            raise ApiError("lease_not_current", retryable=False)

    client = SequenceClient()
    threads: list[InlineThread] = []
    sleeps: list[float] = []
    monkeypatch.setattr("photo_worker.runner.time.sleep", sleeps.append)

    Worker(
        client,
        WorkerConfig(worker_build="worker-test", lease_seconds=60, temp_dir=tmp_path),
        lease_keeper_factory=deterministic_keeper_factory([True], threads),
    ).run_forever()

    assert client.claims == 2
    assert client.complete_attempts == 1
    assert client.completed == []
    assert client.failed == []
    assert sleeps == [5]
    assert [(thread.started, thread.join_count) for thread in threads] == [(True, 1)]
    assert list(tmp_path.iterdir()) == []


def test_lease_keeper_start_failure_cleans_temp_file_without_terminal_submission(
    tmp_path: Path,
) -> None:
    class StartFailingKeeper:
        def __init__(self) -> None:
            self.stop_calls = 0

        def start(self) -> None:
            raise RuntimeError("no thread capacity")

        def stop(self) -> None:
            self.stop_calls += 1

        def raise_if_lost(self) -> None:
            raise AssertionError("must not inspect a keeper that did not start")

    client = Client(make_claim())
    keeper = StartFailingKeeper()
    worker = Worker(
        client,
        WorkerConfig(worker_build="worker-test", lease_seconds=60, temp_dir=tmp_path),
        lease_keeper_factory=lambda *_args: keeper,
    )

    with pytest.raises(RuntimeError, match="no thread capacity"):
        worker.run_once()

    assert keeper.stop_calls == 1
    assert client.completed == []
    assert client.failed == []
    assert list(tmp_path.iterdir()) == []


def test_lease_keeper_stop_is_safe_after_a_partially_started_thread() -> None:
    class PartiallyStartedThread:
        def __init__(self, *, target: Callable[[], None], daemon: bool) -> None:
            self.target = target
            self.daemon = daemon
            self.join_count = 0

        def start(self) -> None:
            raise RuntimeError("thread start failed")

        def join(self) -> None:
            self.join_count += 1
            raise RuntimeError("cannot join before start")

    claim = make_claim()
    assert claim.job is not None
    thread: PartiallyStartedThread | None = None

    def thread_factory(*, target: Callable[[], None], daemon: bool) -> PartiallyStartedThread:
        nonlocal thread
        thread = PartiallyStartedThread(target=target, daemon=daemon)
        return thread

    keeper = _LeaseKeeper(
        Client(claim),
        claim.job,
        60,
        thread_factory=thread_factory,  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="thread start failed"):
        keeper.start()

    keeper.stop()
    keeper.stop()

    assert thread is not None
    assert thread.join_count == 1


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_terminal_result_bound_prevents_submission_and_cleans_temp_file(
    tmp_path: Path, outcome: str
) -> None:
    class BoundedClient(Client):
        def download(self, _url: str, destination: Path, **kwargs: object) -> int:
            if outcome == "failure":
                raise DownloadError("unsupported_input", retryable=False)
            return super().download(_url, destination, **kwargs)

    claim = make_claim()
    assert claim.job is not None
    tiny_job = replace(
        claim.job,
        configuration=replace(claim.job.configuration, terminal_result_max_bytes=1),
    )
    client = BoundedClient(replace(claim, job=tiny_job))
    threads: list[InlineThread] = []
    worker = Worker(
        client,
        WorkerConfig(worker_build="worker-test", lease_seconds=60, temp_dir=tmp_path),
        lease_keeper_factory=deterministic_keeper_factory([True], threads),
    )

    with pytest.raises(ApiError, match="invalid_api_response"):
        worker.run_once()

    assert client.completed == []
    assert client.failed == []
    assert [(thread.started, thread.join_count) for thread in threads] == [(True, 1)]
    assert list(tmp_path.iterdir()) == []
