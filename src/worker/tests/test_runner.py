from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from photo_worker.client import ApiError, CallbackResult, DownloadError
from photo_worker.contracts import (
    FACE_EMBEDDING_BENCHMARK_CONFIGURATION,
    PROCESSOR_TYPE,
    PROCESSOR_TYPE_FACE_EMBEDDING,
    PROCESSOR_TYPE_FACE_EMBEDDING_BENCHMARK,
    PROCESSOR_TYPE_GENERATE_PREVIEW,
    PROCESSOR_TYPE_SELFIE_QUERY,
    V2_FACE_EMBEDDING_CONFIGURATION,
    V2_GENERATE_PREVIEW_CONFIGURATION,
    CaptureMetadataResult,
    Claim,
    FaceEmbeddingFace,
    FaceEmbeddingResult,
    SelfieEmbeddingResult,
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
            else (
                {
                    "selfie_query": {
                        "detection_threshold": 0.75,
                        "embedding_dimensions": 128,
                        "min_face_px": 32,
                        "model": "sface",
                    }
                }
                if processor_type == PROCESSOR_TYPE_SELFIE_QUERY
                else {"face_embedding": {"max_faces": 2, "detection_threshold": 0.75}}
            )
        ),
        "worker": {
            "concurrency": 1,
            "api_response_max_bytes": 16_384,
            "heartbeat_interval_seconds": heartbeat_interval_seconds,
            "lease_duration_seconds": 120,
            "max_input_bytes": 20 * 1024 * 1024
            if processor_type == PROCESSOR_TYPE_SELFIE_QUERY
            else 52_428_800,
            "max_pixels": 25_000_000
            if processor_type == PROCESSOR_TYPE_SELFIE_QUERY
            else 100_000_000,
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
                    heartbeat_interval_seconds=heartbeat_interval_seconds,
                ),
                **(
                    {
                        "event_id": "17",
                        "search_id": "00000000-0000-0000-0000-000000000013",
                        "input_fingerprint": {
                            "temporary_key": "selfie-search/0123456789abcdef0123456789abcdef",
                            "temporary_size": 1024,
                            "temporary_content_type": "image/jpeg",
                        },
                        "input_limits": {"max_bytes": 1024, "content_type": "image/jpeg"},
                    }
                    if processor_type == PROCESSOR_TYPE_SELFIE_QUERY
                    else {
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
                    }
                ),
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
        self.claim_identities: list[tuple[int, str, int]] = []

    def claim_job(self, **kwargs: object) -> Claim:
        self.claim_identities.append(
            (
                int(kwargs["contract_version"]),
                str(kwargs["processor_type"]),
                int(kwargs["processor_version"]),
            )
        )
        return self.claim

    def download(
        self,
        _url: str,
        destination: Path,
        *,
        max_bytes: int,
        expected_size: int,
        expected_content_type: str,
        expected_etag: str | None = None,
    ) -> int:
        assert expected_etag is None
        image = Image.new("RGB", (2, 2), "white")
        image.save(destination, "JPEG")
        image.close()
        assert max_bytes == expected_size == 1024
        assert expected_content_type == "image/jpeg"
        return expected_size

    def heartbeat(self, attempt_id: str, **_: object) -> None:
        self.heartbeats.append(attempt_id)

    def refresh_download(self, _attempt_id: str) -> str:
        return "https://storage.example.test/refreshed?secret"

    def complete(self, attempt_id: str, payload: dict[str, object], **_: object) -> CallbackResult:
        assert attempt_id == "00000000-0000-0000-0000-000000000012"
        self.completed.append(payload)
        return CallbackResult(attempt_id, "succeeded", idempotent=False, stale=False)

    def fail(self, _attempt_id: str, payload: dict[str, object], **_: object) -> CallbackResult:
        self.failed.append(payload)
        return CallbackResult(
            "00000000-0000-0000-0000-000000000012",
            "failed",
            idempotent=False,
            stale=False,
        )


class SchedulingClient(Client):
    """Expose the worker's claim order while keeping processor execution out of scheduler tests."""

    def __init__(self, nonempty_identities: set[tuple[int, str, int]]) -> None:
        super().__init__(Claim.empty(1))
        self._nonempty_identities = nonempty_identities

    def claim_job(self, **kwargs: object) -> Claim:
        identity = (
            int(kwargs["contract_version"]),
            str(kwargs["processor_type"]),
            int(kwargs["processor_version"]),
        )
        self.claim_identities.append(identity)
        return make_claim() if identity in self._nonempty_identities else Claim.empty(1)


def preview_claim() -> Claim:
    return Claim.from_response(
        {
            "empty": False,
            "job": {
                "id": "00000000-0000-0000-0000-000000000011",
                "attempt_id": "00000000-0000-0000-0000-000000000012",
                "contract_version": 2,
                "processor_type": PROCESSOR_TYPE_GENERATE_PREVIEW,
                "processor_version": 1,
                "configuration": V2_GENERATE_PREVIEW_CONFIGURATION,
                "photo_id": "photo-1",
                "event_id": "00000000-0000-0000-0000-000000000013",
                "run_id": "00000000-0000-0000-0000-000000000014",
                "input_fingerprint": {
                    "object_key": "originals/0123456789abcdef0123456789abcdef",
                    "object_size": 1_000_000,
                    "object_content_type": "image/jpeg",
                    "object_etag": "etag-1",
                    "media_kind": "original",
                    "pixel_width": 3200,
                    "pixel_height": 2000,
                },
                "input_limits": {"max_bytes": 1_000_000, "content_type": "image/jpeg"},
                "lease_expires_at": "2026-07-30T10:03:00Z",
                "download_url": "https://storage.example.test/download?signature=download-secret",
                "download_expires_at": "2026-07-30T10:01:00Z",
                "output_slots": [
                    {
                        "variant": "preview-small-v1",
                        "upload_url": "https://storage.example.test/upload?signature=upload-secret",
                        "upload_expires_at": "2026-07-30T10:01:00Z",
                        "content_type": "image/jpeg",
                        "staging_key": (
                            "processing-staging/previews/00000000-0000-0000-0000-000000000012/"
                            "preview-small-v1.jpg"
                        ),
                        "max_bytes": 10_485_760,
                        "max_width": 1600,
                        "max_height": 1600,
                        "checksum_algorithm": "sha256",
                    }
                ],
            },
        }
    )


def preview_face_claim() -> Claim:
    return Claim.from_response(
        {
            "empty": False,
            "job": {
                "id": "00000000-0000-0000-0000-000000000021",
                "attempt_id": "00000000-0000-0000-0000-000000000012",
                "contract_version": 2,
                "processor_type": PROCESSOR_TYPE_FACE_EMBEDDING,
                "processor_version": 2,
                "configuration": V2_FACE_EMBEDDING_CONFIGURATION,
                "photo_id": "photo-2",
                "event_id": "00000000-0000-0000-0000-000000000013",
                "run_id": "00000000-0000-0000-0000-000000000014",
                "input_fingerprint": {
                    "object_key": "derivatives/previews/photo-2/preview-small-v1/"
                    "00000000-0000-0000-0000-000000000012-"
                    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
                    "object_size": 1024,
                    "object_content_type": "image/jpeg",
                    "object_etag": None,
                    "media_kind": "preview-small-v1",
                    "pixel_width": 1600,
                    "pixel_height": 1000,
                },
                "input_geometry": {
                    "coordinate_space": "preview-small-v1",
                    "pixel_width": 1600,
                    "pixel_height": 1000,
                    "oriented_source_width": 3200,
                    "oriented_source_height": 2000,
                },
                "input_limits": {"max_bytes": 1024, "content_type": "image/jpeg"},
                "lease_expires_at": "2026-07-30T10:03:00Z",
                "download_url": "https://storage.example.test/download?signature=download-secret",
                "download_expires_at": "2026-07-30T10:01:00Z",
            },
        }
    )


class PreviewClient(Client):
    def __init__(self) -> None:
        super().__init__(preview_claim())
        self.calls: list[str] = []

    def download(self, _url: str, destination: Path, **_: object) -> int:
        image = Image.new("RGB", (3200, 2000), "white")
        try:
            image.save(destination, "JPEG")
        finally:
            image.close()
        self.calls.append("download")
        return 1_000_000

    def upload_preview(self, _url: str, source: Path, **_: object) -> None:
        assert source.is_file()
        self.calls.append("upload")

    def complete(self, attempt_id: str, payload: dict[str, object], **_: object) -> None:
        self.calls.append("complete")
        super().complete(attempt_id, payload)


def test_preview_is_uploaded_before_its_typed_completion_and_all_temp_files_are_removed(
    tmp_path: Path,
) -> None:
    client = PreviewClient()

    Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            temp_dir=tmp_path,
            processor_type=PROCESSOR_TYPE_GENERATE_PREVIEW,
        ),
    ).run_once()

    assert client.calls == ["download", "upload", "complete"]
    result = client.completed[0]["result"]
    assert result["variant"] == "preview-small-v1"
    assert result["content_type"] == "image/jpeg"
    assert (result["width"], result["height"]) == (1600, 1000)
    assert result["upload_ms"] >= 0
    assert list(tmp_path.iterdir()) == []


class LeaseLostAfterUpload:
    def __init__(self) -> None:
        self.checks = 0

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def raise_if_lost(self) -> None:
        self.checks += 1
        if self.checks == 3:
            from photo_worker.runner import AttemptLost

            raise AttemptLost()


def test_preview_lease_loss_after_upload_stops_completion_and_redacts_all_secrets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    client = PreviewClient()
    keeper = LeaseLostAfterUpload()

    Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            temp_dir=tmp_path,
            processor_type=PROCESSOR_TYPE_GENERATE_PREVIEW,
            log_secrets=("worker-token", "download-secret", "upload-secret"),
        ),
        lease_keeper_factory=lambda *_args: keeper,
    ).run_once()

    assert client.calls == ["download", "upload"]
    assert client.completed == []
    assert client.failed == []
    assert list(tmp_path.iterdir()) == []
    for secret in (
        "worker-token",
        "download-secret",
        "upload-secret",
        "processing-staging/previews",
        "hostile response",
        "DateTimeOriginal",
    ):
        assert secret not in caplog.text


def test_preview_normalization_failure_uses_the_declared_permanent_failure_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from photo_worker.preview import PreviewError

    client = PreviewClient()
    monkeypatch.setattr(
        "photo_worker.runner.generate_preview",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PreviewError("normalization_failed")),
    )

    Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            temp_dir=tmp_path,
            processor_type=PROCESSOR_TYPE_GENERATE_PREVIEW,
        ),
    ).run_once()

    assert client.calls == ["download"]
    assert (client.failed[0]["error_code"], client.failed[0]["retryable"]) == (
        "normalization_failed",
        False,
    )
    assert list(tmp_path.iterdir()) == []


def test_second_temporary_allocation_failure_removes_first_file_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Client(make_claim())
    import photo_worker.runner as runner_module

    original = runner_module.tempfile.NamedTemporaryFile
    calls = 0

    def fail_second(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second temporary allocation failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(runner_module.tempfile, "NamedTemporaryFile", fail_second)

    with pytest.raises(OSError, match="second temporary allocation failed"):
        Worker(
            client, WorkerConfig(worker_build="worker-test", lease_seconds=60, temp_dir=tmp_path)
        ).run_once()

    assert client.completed == []
    assert client.failed == []
    assert list(tmp_path.iterdir()) == []


def test_second_temporary_context_close_failure_removes_all_files_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Client(make_claim())
    import photo_worker.runner as runner_module

    original = runner_module.tempfile.NamedTemporaryFile
    calls = 0

    class FailingClose:
        def __init__(self, temporary: object) -> None:
            self._temporary = temporary
            self.name = temporary.name

        def __enter__(self) -> FailingClose:
            return self

        def __exit__(self, *_: object) -> None:
            self._temporary.close()
            raise OSError("second temporary close failed")

    def fail_second_close(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        temporary = original(*args, **kwargs)
        return FailingClose(temporary) if calls == 2 else temporary

    monkeypatch.setattr(runner_module.tempfile, "NamedTemporaryFile", fail_second_close)

    with pytest.raises(OSError, match="second temporary close failed"):
        Worker(
            client, WorkerConfig(worker_build="worker-test", lease_seconds=60, temp_dir=tmp_path)
        ).run_once()

    assert client.completed == []
    assert client.failed == []
    assert list(tmp_path.iterdir()) == []


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


def maximum_face_embedding_result() -> FaceEmbeddingResult:
    """Representative maximum v2 output: 32 SFace vectors plus every typed field."""
    return FaceEmbeddingResult(
        model="sface",
        faces=tuple(
            FaceEmbeddingFace(
                index=index,
                bbox=(1600.0, 1000.0, 1600.0, 1000.0),
                confidence=0.9876543,
                landmarks=(
                    (100.1234567, 200.1234567),
                    (300.1234567, 400.1234567),
                    (500.1234567, 600.1234567),
                    (700.1234567, 800.1234567),
                    (900.1234567, 999.1234567),
                ),
                # ``float(np.float32(1 / 128))`` uses this full wire representation.
                embedding=tuple(0.007812500465661287 for _ in range(128)),
            )
            for index in range(32)
        ),
        has_single_query_face_usable=False,
        warnings=("faces_truncated", "face_embedding_failed"),
        timings={
            "decode_ms": 86_400_000,
            "model_load_ms": 86_400_000,
            "detect_ms": 86_400_000,
            "embed_ms": 86_400_000,
            "total_ms": 345_600_000,
        },
    )


def make_selfie_embedding_result() -> SelfieEmbeddingResult:
    return SelfieEmbeddingResult(
        model="sface",
        embedding=tuple(1.0 / 128**0.5 for _ in range(128)),
        bbox=(1.0, 2.0, 32.0, 32.0),
        confidence=0.96,
        landmarks=((1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0), (9.0, 10.0)),
        timings={"decode_ms": 1, "model_load_ms": 2, "detect_ms": 3, "embed_ms": 4, "total_ms": 10},
    )


def test_worker_polls_selfie_first_then_keeps_existing_processors_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    selfie_claim = make_claim(processor_type=PROCESSOR_TYPE_SELFIE_QUERY)
    face_claim = make_claim(processor_type=PROCESSOR_TYPE_FACE_EMBEDDING)

    class OrderedClient(Client):
        def __init__(self) -> None:
            super().__init__(selfie_claim)
            self.requested: list[str] = []

        def claim_job(self, *, processor_type: str, **_: object) -> Claim:
            self.requested.append(processor_type)
            return Claim.empty(7) if processor_type == PROCESSOR_TYPE_SELFIE_QUERY else face_claim

    client = OrderedClient()
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
            processor_types=(
                PROCESSOR_TYPE_SELFIE_QUERY,
                PROCESSOR_TYPE_FACE_EMBEDDING,
                PROCESSOR_TYPE,
            ),
        ),
    )

    assert worker.run_once() is None
    assert client.requested == [PROCESSOR_TYPE_SELFIE_QUERY, PROCESSOR_TYPE_FACE_EMBEDDING]
    assert client.completed[0]["processor_type"] == PROCESSOR_TYPE_FACE_EMBEDDING
    assert "signature=secret" not in caplog.text


def test_worker_submits_typed_selfie_result_without_logging_vector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    client = Client(make_claim(processor_type=PROCESSOR_TYPE_SELFIE_QUERY))
    monkeypatch.setattr(
        "photo_worker.runner.extract_selfie_embedding",
        lambda *_args, **_kwargs: make_selfie_embedding_result(),
    )
    worker = Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            temp_dir=tmp_path,
            processor_types=(PROCESSOR_TYPE_SELFIE_QUERY,),
        ),
    )

    assert worker.run_once() is None
    assert client.completed[0]["result"]["model"] == "sface"
    assert len(client.completed[0]["result"]["embedding"]) == 128
    assert "0.088388" not in caplog.text


def test_selfie_success_callback_emits_one_bounded_attempt_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    client = Client(make_claim(processor_type=PROCESSOR_TYPE_SELFIE_QUERY))
    monkeypatch.setattr(
        "photo_worker.runner.extract_selfie_embedding",
        lambda *_args, **_kwargs: make_selfie_embedding_result(),
    )

    Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            temp_dir=tmp_path,
            processor_types=(PROCESSOR_TYPE_SELFIE_QUERY,),
        ),
    ).run_once()

    events = [
        json.loads(record.message) for record in caplog.records if record.message.startswith("{")
    ]
    assert len(events) == 1
    event = events[0]
    assert event["event"] == "selfie_worker_attempt_finished"
    assert event["search_id"] == "00000000-0000-0000-0000-000000000013"
    assert event["job_id"] == "00000000-0000-0000-0000-000000000011"
    assert event["attempt_id"] == "00000000-0000-0000-0000-000000000012"
    assert event["outcome"] == "succeeded"
    assert "photo_id" not in event


def test_selfie_idempotent_success_callback_does_not_duplicate_the_attempt_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class IdempotentClient(Client):
        def complete(
            self, attempt_id: str, payload: dict[str, object], **kwargs: object
        ) -> CallbackResult:
            super().complete(attempt_id, payload, **kwargs)
            return CallbackResult(attempt_id, "succeeded", idempotent=True, stale=False)

    caplog.set_level("INFO")
    client = IdempotentClient(make_claim(processor_type=PROCESSOR_TYPE_SELFIE_QUERY))
    monkeypatch.setattr(
        "photo_worker.runner.extract_selfie_embedding",
        lambda *_args, **_kwargs: make_selfie_embedding_result(),
    )

    Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            temp_dir=tmp_path,
            processor_types=(PROCESSOR_TYPE_SELFIE_QUERY,),
        ),
    ).run_once()

    assert client.completed
    assert not [record for record in caplog.records if record.message.startswith("{")]


def test_selfie_callback_without_completion_metadata_is_rejected_without_an_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class MissingMetadataClient(Client):
        def complete(self, attempt_id: str, payload: dict[str, object], **kwargs: object) -> None:
            super().complete(attempt_id, payload, **kwargs)

    caplog.set_level("INFO")
    client = MissingMetadataClient(make_claim(processor_type=PROCESSOR_TYPE_SELFIE_QUERY))
    monkeypatch.setattr(
        "photo_worker.runner.extract_selfie_embedding",
        lambda *_args, **_kwargs: make_selfie_embedding_result(),
    )

    with pytest.raises(ApiError, match="invalid_api_response"):
        Worker(
            client,
            WorkerConfig(
                worker_build="worker-test",
                lease_seconds=60,
                temp_dir=tmp_path,
                processor_types=(PROCESSOR_TYPE_SELFIE_QUERY,),
            ),
        ).run_once()

    assert client.completed
    assert not [record for record in caplog.records if record.message.startswith("{")]


def test_selfie_transport_retry_then_idempotent_acceptance_emits_no_duplicate_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class RetryingClient(Client):
        def __init__(self, claim: Claim) -> None:
            super().__init__(claim)
            self.callback_calls = 0

        def complete(
            self, attempt_id: str, payload: dict[str, object], **kwargs: object
        ) -> CallbackResult:
            self.callback_calls += 1
            if self.callback_calls == 1:
                raise ApiError("network_interruption", retryable=True)
            super().complete(attempt_id, payload, **kwargs)
            return CallbackResult(attempt_id, "succeeded", idempotent=True, stale=False)

    caplog.set_level("INFO")
    client = RetryingClient(make_claim(processor_type=PROCESSOR_TYPE_SELFIE_QUERY))
    monkeypatch.setattr(
        "photo_worker.runner.extract_selfie_embedding",
        lambda *_args, **_kwargs: make_selfie_embedding_result(),
    )
    worker = Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            temp_dir=tmp_path,
            processor_types=(PROCESSOR_TYPE_SELFIE_QUERY,),
        ),
    )

    with pytest.raises(ApiError, match="network_interruption"):
        worker.run_once()
    worker.run_once()

    assert client.callback_calls == 2
    assert not [record for record in caplog.records if record.message.startswith("{")]


def test_selfie_idempotent_failure_callback_does_not_duplicate_the_attempt_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class IdempotentFailureClient(Client):
        def fail(
            self, attempt_id: str, payload: dict[str, object], **kwargs: object
        ) -> CallbackResult:
            super().fail(attempt_id, payload, **kwargs)
            return CallbackResult(attempt_id, "failed", idempotent=True, stale=False)

    caplog.set_level("INFO")
    client = IdempotentFailureClient(make_claim(processor_type=PROCESSOR_TYPE_SELFIE_QUERY))
    monkeypatch.setattr(
        "photo_worker.runner.extract_selfie_embedding",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FaceEmbeddingError("no_face_detected")),
    )

    Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            temp_dir=tmp_path,
            processor_types=(PROCESSOR_TYPE_SELFIE_QUERY,),
        ),
    ).run_once()

    assert client.failed
    assert not [record for record in caplog.records if record.message.startswith("{")]


def test_selfie_logging_failure_preserves_the_retryable_callback_disposition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Client(make_claim(processor_type=PROCESSOR_TYPE_SELFIE_QUERY))
    monkeypatch.setattr(
        "photo_worker.runner.extract_selfie_embedding",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FaceEmbeddingError("model_inference_timeout")
        ),
    )
    monkeypatch.setattr(
        "photo_worker.runner.LOGGER.log",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("logger unavailable")),
    )

    Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            temp_dir=tmp_path,
            processor_types=(PROCESSOR_TYPE_SELFIE_QUERY,),
        ),
    ).run_once()

    assert len(client.failed) == 1
    assert client.failed[0]["error_code"] == "model_inference_timeout"
    assert client.failed[0]["retryable"] is True


@pytest.mark.parametrize(
    ("error_code", "retryable"),
    [
        ("decode_failed", False),
        ("fingerprint_mismatch", False),
        ("input_too_large", False),
        ("model_inference_error", False),
        ("model_inference_timeout", True),
        ("network_interruption", True),
        ("no_face_detected", False),
        ("multiple_faces_detected", False),
        ("quality_rejected", False),
        ("storage_unavailable", True),
        ("unsupported_input", False),
    ],
)
def test_selfie_failure_families_emit_one_bounded_event_at_the_disposition_level(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error_code: str,
    retryable: bool,
) -> None:
    caplog.set_level("INFO")
    client = Client(make_claim(processor_type=PROCESSOR_TYPE_SELFIE_QUERY))
    monkeypatch.setattr(
        "photo_worker.runner.extract_selfie_embedding",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FaceEmbeddingError(error_code)),
    )

    Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            temp_dir=tmp_path,
            processor_types=(PROCESSOR_TYPE_SELFIE_QUERY,),
        ),
    ).run_once()

    records = [record for record in caplog.records if record.message.startswith("{")]
    assert len(records) == 1
    event = json.loads(records[0].message)
    assert event["outcome"] == "failed"
    assert event["reason_code"] == error_code
    assert event["retryable"] is retryable
    assert records[0].levelname == ("WARNING" if retryable else "INFO")
    assert set(event).isdisjoint({"photo_id", "download_url", "embedding"})


def test_worker_configuration_parses_plural_processors_and_legacy_singular(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOTO_WORKER_API_URL", "http://web:8000/internal/photo-processing/v1")
    monkeypatch.setenv("PHOTO_WORKER_TOKEN", "worker-token")
    monkeypatch.setenv(
        "PHOTO_WORKER_PROCESSOR_TYPES",
        "selfie_query,face_embedding,capture_metadata,generate_preview",
    )
    monkeypatch.setenv(
        "PHOTO_WORKER_PROCESSOR_IDENTITIES",
        "1/capture_metadata/1,1/face_embedding/1,2/generate_preview/1,2/face_embedding/2",
    )
    plural, _client = WorkerConfig.from_env()
    monkeypatch.delenv("PHOTO_WORKER_PROCESSOR_IDENTITIES")
    product, _client = WorkerConfig.from_env()
    monkeypatch.delenv("PHOTO_WORKER_PROCESSOR_TYPES")
    monkeypatch.setenv("PHOTO_WORKER_PROCESSOR_TYPE", PROCESSOR_TYPE_FACE_EMBEDDING)
    singular, _client = WorkerConfig.from_env()

    assert plural.processor_types == (
        PROCESSOR_TYPE_SELFIE_QUERY,
        PROCESSOR_TYPE_FACE_EMBEDDING,
        PROCESSOR_TYPE,
        PROCESSOR_TYPE_GENERATE_PREVIEW,
    )
    assert plural.processor_identities == (
        "1/capture_metadata/1",
        "1/face_embedding/1",
        "2/generate_preview/1",
        "2/face_embedding/2",
    )
    assert product.processor_types == (
        PROCESSOR_TYPE_SELFIE_QUERY,
        PROCESSOR_TYPE_FACE_EMBEDDING,
        PROCESSOR_TYPE,
        PROCESSOR_TYPE_GENERATE_PREVIEW,
    )
    assert singular.processor_types == (PROCESSOR_TYPE_FACE_EMBEDDING,)


def test_environment_benchmark_identity_is_authoritative_over_product_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOTO_WORKER_API_URL", "http://web:8000/internal/photo-processing/v1")
    monkeypatch.setenv("PHOTO_WORKER_TOKEN", "worker-token")
    monkeypatch.setenv(
        "PHOTO_WORKER_PROCESSOR_TYPES",
        "selfie_query,face_embedding,capture_metadata,generate_preview",
    )
    monkeypatch.setenv("PHOTO_WORKER_PROCESSOR_IDENTITIES", "3/face_embedding_benchmark/1")

    config, _client = WorkerConfig.from_env()
    client = Client(Claim.empty(7))

    assert Worker(client, config).run_once() == 7
    assert client.claim_identities == [(3, PROCESSOR_TYPE_FACE_EMBEDDING_BENCHMARK, 1)]


def test_environment_product_identity_preserves_product_type_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOTO_WORKER_API_URL", "http://web:8000/internal/photo-processing/v1")
    monkeypatch.setenv("PHOTO_WORKER_TOKEN", "worker-token")
    monkeypatch.setenv(
        "PHOTO_WORKER_PROCESSOR_TYPES",
        "selfie_query,face_embedding,capture_metadata,generate_preview",
    )
    monkeypatch.setenv("PHOTO_WORKER_PROCESSOR_IDENTITIES", "1/capture_metadata/1")

    config, _client = WorkerConfig.from_env()
    client = Client(Claim.empty(7))

    assert config.processor_types == (
        PROCESSOR_TYPE_SELFIE_QUERY,
        PROCESSOR_TYPE_FACE_EMBEDDING,
        PROCESSOR_TYPE,
        PROCESSOR_TYPE_GENERATE_PREVIEW,
    )
    assert Worker(client, config).run_once() == 7
    assert client.claim_identities == [
        (1, PROCESSOR_TYPE_SELFIE_QUERY, 1),
        (1, PROCESSOR_TYPE_FACE_EMBEDDING, 1),
        (1, PROCESSOR_TYPE, 1),
        (2, PROCESSOR_TYPE_GENERATE_PREVIEW, 1),
    ]


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
    assert client.completed[0]["result"]["face_count"] == 1
    assert client.completed[0]["result"]["faces"][0]["index"] == 0
    assert client.completed[0]["result"]["has_single_query_face_usable"] is True
    assert len(json.dumps(client.completed[0], separators=(",", ":")).encode()) <= 8_192
    assert list(tmp_path.iterdir()) == []
    assert "phase=succeeded" in caplog.text


def test_worker_benchmark_runs_face_extraction_but_submits_metrics_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level("INFO")
    claim = Claim.from_response(
        {
            "empty": False,
            "job": {
                "id": "00000000-0000-0000-0000-000000000011",
                "attempt_id": "00000000-0000-0000-0000-000000000012",
                "contract_version": 3,
                "processor_type": PROCESSOR_TYPE_FACE_EMBEDDING_BENCHMARK,
                "processor_version": 1,
                "configuration": FACE_EMBEDDING_BENCHMARK_CONFIGURATION,
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
    client = Client(claim)
    monkeypatch.setattr(
        "photo_worker.runner.extract_face_embeddings",
        lambda *_args, **_kwargs: make_face_embedding_result(),
    )

    Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            temp_dir=tmp_path,
            processor_identities=("3/face_embedding_benchmark/1",),
        ),
    ).run_once()

    assert client.completed[0]["result"] == {
        "model": "sface",
        "face_count": 1,
        "warnings": [],
        "timings": {
            "decode_ms": 1,
            "model_load_ms": 2,
            "detect_ms": 3,
            "embed_ms": 4,
            "total_ms": 10,
        },
    }
    assert "photo-1" not in caplog.text


def test_worker_submits_preview_face_result_with_declared_geometry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Client(preview_face_claim())
    monkeypatch.setattr(
        "photo_worker.runner.extract_face_embeddings",
        lambda *_args, **_kwargs: make_face_embedding_result(),
    )

    Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            temp_dir=tmp_path,
            processor_identities=("2/face_embedding/2",),
        ),
    ).run_once()

    assert len(client.completed) == 1
    assert client.failed == []
    assert client.completed[0]["result"]["input_geometry"] == {
        "coordinate_space": "preview-small-v1",
        "pixel_width": 1600,
        "pixel_height": 1000,
        "oriented_source_width": 3200,
        "oriented_source_height": 2000,
    }
    assert list(tmp_path.iterdir()) == []


def test_worker_submits_maximum_v2_face_embedding_payload_within_contract_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Client(preview_face_claim())
    monkeypatch.setattr(
        "photo_worker.runner.extract_face_embeddings",
        lambda *_args, **_kwargs: maximum_face_embedding_result(),
    )

    Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            temp_dir=tmp_path,
            processor_identities=("2/face_embedding/2",),
        ),
    ).run_once()

    assert len(client.completed) == 1
    assert client.completed[0]["result"]["face_count"] == 32
    payload_size = len(json.dumps(client.completed[0], separators=(",", ":")).encode())
    assert 64 * 1024 < payload_size <= 128 * 1024
    assert list(tmp_path.iterdir()) == []


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


def test_worker_polls_configured_exact_identities_round_robin_without_parallel_claims() -> None:
    """One process advances through identities even when an earlier queue is empty."""
    client = Client(Claim.empty(3))
    worker = Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            processor_identities=(
                "1/capture_metadata/1",
                "2/generate_preview/1",
                "2/face_embedding/2",
            ),
        ),
    )

    assert [worker.run_once() for _ in range(4)] == [3, 3, 3, 3]
    assert client.claim_identities == [
        (1, "capture_metadata", 1),
        (2, "generate_preview", 1),
        (2, "face_embedding", 2),
        (1, "capture_metadata", 1),
    ]


def test_worker_keeps_explicit_preview_identities_after_public_priority_processors() -> None:
    client = Client(Claim.empty(3))
    worker = Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            processor_types=(
                PROCESSOR_TYPE_SELFIE_QUERY,
                PROCESSOR_TYPE_FACE_EMBEDDING,
                PROCESSOR_TYPE,
            ),
            processor_identities=(
                "1/capture_metadata/1",
                "1/face_embedding/1",
                "2/generate_preview/1",
                "2/face_embedding/2",
            ),
        ),
    )

    assert worker.run_once() == 3
    assert client.claim_identities == [
        (1, "selfie_query", 1),
        (1, "face_embedding", 1),
        (2, "face_embedding", 2),
        (1, "capture_metadata", 1),
        (2, "generate_preview", 1),
    ]


def test_continuous_selfie_claims_poll_every_photo_identity_within_one_photo_opportunity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A permanent interactive queue cannot prevent any configured photo identity being polled."""
    selfie = (1, PROCESSOR_TYPE_SELFIE_QUERY, 1)
    legacy_face = (1, PROCESSOR_TYPE_FACE_EMBEDDING, 1)
    preview_face = (2, PROCESSOR_TYPE_FACE_EMBEDDING, 2)
    capture = (1, PROCESSOR_TYPE, 1)
    preview = (2, PROCESSOR_TYPE_GENERATE_PREVIEW, 1)
    client = SchedulingClient({selfie})
    worker = Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            processor_types=(
                PROCESSOR_TYPE_SELFIE_QUERY,
                PROCESSOR_TYPE_FACE_EMBEDDING,
                PROCESSOR_TYPE,
            ),
            processor_identities=(
                "1/capture_metadata/1",
                "1/face_embedding/1",
                "2/generate_preview/1",
                "2/face_embedding/2",
            ),
        ),
    )
    monkeypatch.setattr(worker, "_process", lambda _job: None)

    assert worker.run_once() is None
    assert worker.run_once() is None

    assert client.claim_identities == [selfie, legacy_face, preview_face, capture, preview, selfie]


def test_continuous_legacy_face_claims_do_not_starve_preview_face_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The photo cursor advances past a claimed legacy identity before the next opportunity."""
    selfie = (1, PROCESSOR_TYPE_SELFIE_QUERY, 1)
    legacy_face = (1, PROCESSOR_TYPE_FACE_EMBEDDING, 1)
    preview_face = (2, PROCESSOR_TYPE_FACE_EMBEDDING, 2)
    client = SchedulingClient({legacy_face, preview_face})
    worker = Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            processor_types=(PROCESSOR_TYPE_SELFIE_QUERY, PROCESSOR_TYPE_FACE_EMBEDDING),
            processor_identities=("1/face_embedding/1", "2/face_embedding/2"),
        ),
    )
    monkeypatch.setattr(worker, "_process", lambda _job: None)

    assert worker.run_once() is None
    assert worker.run_once() is None

    assert client.claim_identities == [selfie, legacy_face, selfie, preview_face]


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


def test_run_forever_immediately_claims_after_successful_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = Worker(
        Client(Claim.empty(1)), WorkerConfig(worker_build="worker-test", lease_seconds=60)
    )
    outcomes = iter(
        [
            None,
            None,
            7,
            ApiError("worker_unauthorized", retryable=False),
        ]
    )

    def run_once() -> int | None:
        outcome = next(outcomes)
        if isinstance(outcome, ApiError):
            raise outcome
        return outcome

    sleeps: list[float] = []
    monkeypatch.setattr(worker, "run_once", run_once)
    monkeypatch.setattr("photo_worker.runner.time.sleep", sleeps.append)

    worker.run_forever()

    assert sleeps == [7.0]


def test_nonretryable_claim_contract_error_logs_exact_identity_without_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = Client(Claim.empty(1))

    def claim_job(**_kwargs: object) -> Claim:
        raise ApiError(
            "invalid_api_response",
            retryable=False,
            diagnostic="ContractError: invalid claimed job",
        )

    client.claim_job = claim_job  # type: ignore[method-assign]
    worker = Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            processor_identities=("1/selfie_query/1",),
        ),
    )

    with caplog.at_level("ERROR"):
        worker.run_forever()

    assert caplog.messages == [
        "worker_stopped code=invalid_api_response contract_version=1 "
        "processor_type=selfie_query processor_version=1 "
        "failure_category=ContractError: invalid claimed job"
    ]


def test_nonretryable_claim_failure_logs_identity_without_a_client_diagnostic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = Client(Claim.empty(1))

    def claim_job(**_kwargs: object) -> Claim:
        raise ApiError("invalid_api_response", retryable=False)

    client.claim_job = claim_job  # type: ignore[method-assign]
    worker = Worker(
        client,
        WorkerConfig(
            worker_build="worker-test",
            lease_seconds=60,
            processor_identities=("1/selfie_query/1",),
        ),
    )

    with caplog.at_level("ERROR"):
        worker.run_forever()

    assert caplog.messages == [
        "worker_stopped code=invalid_api_response contract_version=1 "
        "processor_type=selfie_query processor_version=1 "
        "failure_category=api:unclassified"
    ]


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

    with pytest.raises(ApiError, match="invalid_api_response") as raised:
        worker.run_once()

    assert raised.value.diagnostic == "worker:terminal_payload_exceeds_limit"

    assert client.completed == []
    assert client.failed == []
    assert [(thread.started, thread.join_count) for thread in threads] == [(True, 1)]
    assert list(tmp_path.iterdir()) == []
