"""Single-concurrency polling loop for the private photo worker processors."""

from __future__ import annotations

import logging
import math
import os
import random
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol

from photo_worker.client import ApiError, DownloadError, HttpClient, UploadError
from photo_worker.contracts import (
    FAILURE_RETRYABLE,
    PREVIEW_CONTRACT_VERSION,
    PROCESSOR_TYPE,
    PROCESSOR_TYPE_FACE_EMBEDDING,
    PROCESSOR_TYPE_GENERATE_PREVIEW,
    CaptureMetadataResult,
    Claim,
    ClaimedJob,
    FaceEmbeddingResult,
    redact,
)
from photo_worker.face_embedding import FaceEmbeddingError, extract_face_embeddings
from photo_worker.metadata import InputTooLarge, MetadataError, extract_capture_metadata
from photo_worker.preview import PreviewError, PreviewResult, generate_preview

LOGGER = logging.getLogger(__name__)
_PREVIEW_FAILURE_RETRYABLE = {
    "invalid_dimensions": False,
    "normalization_failed": False,
    "output_contract_violation": False,
}
_IDENTITY_PARTS = 3
_SUPPORTED_IDENTITIES = {
    (1, PROCESSOR_TYPE, 1),
    (1, PROCESSOR_TYPE_FACE_EMBEDDING, 1),
    (2, PROCESSOR_TYPE_GENERATE_PREVIEW, 1),
    (2, PROCESSOR_TYPE_FACE_EMBEDDING, 2),
}


class WorkerClient(Protocol):
    def claim_job(
        self,
        *,
        worker_build: str,
        lease_seconds: int,
        processor_type: str = PROCESSOR_TYPE,
        processor_version: int | None = None,
        contract_version: int = 1,
    ) -> Claim: ...

    def download(
        self,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
        expected_size: int,
        expected_etag: str | None = None,
    ) -> int: ...

    def upload_preview(
        self,
        url: str,
        source: Path,
        *,
        content_type: str,
        expected_size: int,
        max_bytes: int,
        response_max_bytes: int,
    ) -> None: ...

    def heartbeat(
        self, attempt_id: str, *, lease_seconds: int, response_max_bytes: int
    ) -> None: ...

    def refresh_download(self, attempt_id: str, *, response_max_bytes: int) -> str: ...

    def complete(
        self, attempt_id: str, payload: dict[str, object], *, response_max_bytes: int
    ) -> None: ...

    def fail(
        self, attempt_id: str, payload: dict[str, object], *, response_max_bytes: int
    ) -> None: ...


@dataclass(frozen=True)
class WorkerConfig:
    worker_build: str
    lease_seconds: int
    processor_type: str = PROCESSOR_TYPE
    processor_identities: tuple[str, ...] = ()
    concurrency: int = 1
    temp_dir: Path | None = None
    minimum_delay_seconds: float = 1.0
    maximum_backoff_seconds: float = 30.0
    log_secrets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.concurrency != 1:
            raise ValueError("worker concurrency must be exactly 1")
        if self.processor_type not in {
            PROCESSOR_TYPE,
            PROCESSOR_TYPE_FACE_EMBEDDING,
            PROCESSOR_TYPE_GENERATE_PREVIEW,
        }:
            raise ValueError("unsupported processor type")
        if self.processor_identities:
            identities = tuple(
                _parse_processor_identity(value) for value in self.processor_identities
            )
            if len(set(identities)) != len(identities):
                raise ValueError("processor identities must not repeat")
        if not (
            _finite_positive(self.minimum_delay_seconds)
            and _finite_positive(self.maximum_backoff_seconds)
        ) or (self.minimum_delay_seconds > self.maximum_backoff_seconds):
            raise ValueError("backoff delays must be finite, positive, and ordered")

    @classmethod
    def from_env(cls) -> tuple[WorkerConfig, HttpClient]:
        api_url = os.environ["PHOTO_WORKER_API_URL"]
        token = os.environ["PHOTO_WORKER_TOKEN"]
        build = os.environ.get("PHOTO_WORKER_BUILD", "capture-metadata-v1")
        lease = int(os.environ.get("PHOTO_WORKER_LEASE_SECONDS", "120"))
        processor_type = os.environ.get("PHOTO_WORKER_PROCESSOR_TYPE", PROCESSOR_TYPE)
        raw_identities = os.environ.get("PHOTO_WORKER_PROCESSOR_IDENTITIES", "")
        identities = tuple(item for item in raw_identities.split(",") if item)
        return (
            cls(
                worker_build=build,
                lease_seconds=lease,
                processor_type=processor_type,
                processor_identities=identities,
                log_secrets=(token,),
            ),
            HttpClient(api_url, token),
        )


class AttemptLost(Exception):
    """The current attempt ended elsewhere; abandon it without stopping the daemon."""


class _LeaseKeeper:
    def __init__(
        self,
        worker: WorkerClient,
        job: ClaimedJob,
        lease_seconds: int,
        *,
        wait_for_interval: Callable[[float], bool] | None = None,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        self._worker = worker
        self._job = job
        self._lease_seconds = lease_seconds
        self._stop = threading.Event()
        self._wait_for_interval = wait_for_interval or self._stop.wait
        self._error: ApiError | None = None
        self._thread = thread_factory(target=self._run, daemon=True)
        self._start_attempted = False
        self._joined = False

    def start(self) -> None:
        self._start_attempted = True
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._start_attempted and not self._joined:
            # ``Thread.start`` can fail after allocating thread state.  Joining an actually
            # unstarted thread raises RuntimeError, so cleanup must tolerate either outcome.
            try:
                self._thread.join()
            except RuntimeError:
                self._joined = True
                return
            self._joined = True

    def raise_if_lost(self) -> None:
        if self._error is None:
            return
        if self._error.code == "lease_not_current":
            raise AttemptLost() from self._error
        raise self._error

    def _run(self) -> None:
        while not self._wait_for_interval(self._job.configuration.heartbeat_interval_seconds):
            try:
                self._worker.heartbeat(
                    self._job.attempt_id,
                    lease_seconds=self._lease_seconds,
                    response_max_bytes=self._job.configuration.api_response_max_bytes,
                )
            except ApiError as error:
                self._error = error
                self._stop.set()
                return


class LeaseKeeper(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def raise_if_lost(self) -> None: ...


class Worker:
    """Processes at most one claimed job per ``run_once`` call."""

    def __init__(
        self,
        client: WorkerClient,
        config: WorkerConfig,
        *,
        lease_keeper_factory: Callable[[WorkerClient, ClaimedJob, int], LeaseKeeper] = _LeaseKeeper,
    ) -> None:
        if config.lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self._client = client
        self._config = config
        self._lease_keeper_factory = lease_keeper_factory
        self._next_poll_delay_seconds = config.minimum_delay_seconds
        self._identity_index = 0

    def run_once(self) -> int | None:
        contract_version, processor_type, processor_version = self._next_identity()
        claim = self._client.claim_job(
            worker_build=self._config.worker_build,
            lease_seconds=self._config.lease_seconds,
            processor_type=processor_type,
            processor_version=processor_version,
            contract_version=contract_version,
        )
        if claim.job is None:
            return claim.suggested_delay_seconds
        if claim.job.configuration.heartbeat_interval_seconds >= self._config.lease_seconds:
            raise ApiError("invalid_api_response", retryable=False)
        self._next_poll_delay_seconds = claim.job.configuration.poll_min_delay_seconds
        _lifecycle("claimed", claim.job, secrets=self._config.log_secrets)
        self._process(claim.job)
        return None

    def _next_identity(self) -> tuple[int, str, int]:
        if self._config.processor_identities:
            identities = tuple(
                _parse_processor_identity(value) for value in self._config.processor_identities
            )
            identity = identities[self._identity_index]
            self._identity_index = (self._identity_index + 1) % len(identities)
            return identity
        return (
            PREVIEW_CONTRACT_VERSION
            if self._config.processor_type == PROCESSOR_TYPE_GENERATE_PREVIEW
            else 1,
            self._config.processor_type,
            1,
        )

    def run_forever(self) -> None:
        failures = 0
        while True:
            try:
                idle_delay = self.run_once()
                failures = 0
                time.sleep(
                    float(idle_delay) if idle_delay is not None else self._next_poll_delay_seconds
                )
            except ApiError as error:
                if error.code == "lease_not_current":
                    time.sleep(self._next_poll_delay_seconds)
                    continue
                if not error.retryable:
                    LOGGER.error("worker_stopped code=%s", error.code)
                    return
                failures += 1
                backoff_delay = self._backoff_delay(failures)
                LOGGER.warning(
                    "worker_api_error code=%s delay_seconds=%.2f", error.code, backoff_delay
                )
                time.sleep(backoff_delay)

    def _process(self, job: ClaimedJob) -> None:
        _lifecycle("started", job, secrets=self._config.log_secrets)
        started_at = _timestamp()
        total_started = monotonic()
        download_ms = 0
        compute_ms = 0
        input_path: Path | None = None
        preview_path: Path | None = None
        keeper: LeaseKeeper | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".jpg", dir=self._config.temp_dir, delete=False
            ) as temporary:
                input_path = Path(temporary.name)
            with tempfile.NamedTemporaryFile(
                suffix=".jpg", dir=self._config.temp_dir, delete=False
            ) as temporary:
                preview_path = Path(temporary.name)
            assert input_path is not None and preview_path is not None
            keeper = self._lease_keeper_factory(self._client, job, self._config.lease_seconds)
            keeper.start()
            download_started = monotonic()
            try:
                self._download_current(job, input_path)
            finally:
                download_ms = _milliseconds(download_started)
            keeper.raise_if_lost()
            compute_started = monotonic()
            try:
                result = self._run_processor(job, input_path, preview_path)
            finally:
                compute_ms = _milliseconds(compute_started)
            if isinstance(result, PreviewResult):
                keeper.raise_if_lost()
                upload_started = monotonic()
                try:
                    output_slot = job.output_slots[0]
                    self._client.upload_preview(
                        output_slot.upload_url,
                        preview_path,
                        content_type=output_slot.content_type,
                        expected_size=result.byte_size,
                        max_bytes=output_slot.max_bytes,
                        response_max_bytes=job.configuration.api_response_max_bytes,
                    )
                finally:
                    upload_ms = _milliseconds(upload_started)
                keeper.raise_if_lost()
                result_payload = result.as_payload(upload_ms=upload_ms)
            elif isinstance(result, dict):
                result_payload = result
            else:
                result_payload = result.as_payload()
            keeper.stop()
            keeper.raise_if_lost()
            payload = _success_payload(
                job,
                self._config.worker_build,
                started_at,
                _timestamp(),
                result_payload,
                download_ms,
                compute_ms,
                _milliseconds(total_started),
            )
            _assert_terminal_size(payload, job.configuration.terminal_result_max_bytes)
            self._client.complete(
                job.attempt_id,
                payload,
                response_max_bytes=job.configuration.api_response_max_bytes,
            )
            _lifecycle(
                "succeeded",
                job,
                download_ms=download_ms,
                compute_ms=compute_ms,
                secrets=self._config.log_secrets,
            )
        except (
            DownloadError,
            InputTooLarge,
            MetadataError,
            FaceEmbeddingError,
            PreviewError,
            UploadError,
        ) as error:
            assert keeper is not None
            keeper.stop()
            try:
                keeper.raise_if_lost()
            except AttemptLost:
                _lifecycle("lease_lost", job, secrets=self._config.log_secrets)
                return
            code = getattr(error, "code", None)
            if not isinstance(code, str):
                code = "decode_failed"
            retryable = FAILURE_RETRYABLE.get(code)
            if retryable is None and job.processor_type == PROCESSOR_TYPE_GENERATE_PREVIEW:
                retryable = _PREVIEW_FAILURE_RETRYABLE.get(code)
            if retryable is None:
                code = "decode_failed"
                retryable = False
            payload = _failure_payload(
                job,
                self._config.worker_build,
                started_at,
                _timestamp(),
                code,
                retryable,
                download_ms,
                compute_ms,
                _milliseconds(total_started),
            )
            _assert_terminal_size(payload, job.configuration.terminal_result_max_bytes)
            try:
                self._client.fail(
                    job.attempt_id,
                    payload,
                    response_max_bytes=job.configuration.api_response_max_bytes,
                )
            except ApiError as submission_error:
                if submission_error.code == "lease_not_current":
                    _lifecycle("lease_lost", job, secrets=self._config.log_secrets)
                    return
                raise
            _lifecycle(
                "failed",
                job,
                code=code,
                download_ms=download_ms,
                compute_ms=compute_ms,
                secrets=self._config.log_secrets,
            )
        except AttemptLost:
            _lifecycle("lease_lost", job, secrets=self._config.log_secrets)
            return
        except ApiError as error:
            if error.code == "lease_not_current":
                _lifecycle("lease_lost", job, secrets=self._config.log_secrets)
                return
            raise
        finally:
            if keeper is not None:
                keeper.stop()
            _unlink_temporary(input_path)
            _unlink_temporary(preview_path)

    def _run_processor(self, job: ClaimedJob, input_path: Path, preview_path: Path):
        if job.processor_type == PROCESSOR_TYPE:
            return extract_capture_metadata(
                input_path,
                max_bytes=job.input_limits.max_bytes,
                max_pixels=job.configuration.max_pixels,
                date_field_precedence=job.configuration.date_field_precedence,
            )
        if job.processor_type == PROCESSOR_TYPE_FACE_EMBEDDING:
            result = extract_face_embeddings(
                input_path,
                max_bytes=job.input_limits.max_bytes,
                max_pixels=job.configuration.max_pixels,
                max_faces=job.configuration.max_faces,
                detection_threshold=job.configuration.face_detection_threshold,
                model=job.configuration.model,
            )
            if job.contract_version == PREVIEW_CONTRACT_VERSION:
                if job.input_geometry is None:
                    raise ValueError("preview face claim is missing input geometry")
                return result.as_payload() | {"input_geometry": job.input_geometry}
            return result
        if job.processor_type == PROCESSOR_TYPE_GENERATE_PREVIEW:
            return generate_preview(
                input_path,
                preview_path,
                max_input_bytes=job.input_limits.max_bytes,
                max_pixels=job.configuration.max_pixels,
                slot=job.output_slots[0],
            )
        raise ValueError("unsupported processor type")

    def _download_current(self, job: ClaimedJob, path: Path) -> None:
        expected_etag = (
            job.input_fingerprint.object_etag
            if job.contract_version == PREVIEW_CONTRACT_VERSION
            else job.input_fingerprint.verified_source_etag
        )
        expected_size = (
            job.input_fingerprint.object_size
            if job.contract_version == PREVIEW_CONTRACT_VERSION
            else job.input_fingerprint.original_size
        )
        assert isinstance(expected_size, int)
        try:
            self._client.download(
                job.download_url,
                path,
                max_bytes=job.input_limits.max_bytes,
                expected_size=expected_size,
                expected_etag=expected_etag,
            )
        except DownloadError as error:
            if error.code != "download_authorization_expired":
                raise
            refreshed_url = self._client.refresh_download(
                job.attempt_id,
                response_max_bytes=job.configuration.api_response_max_bytes,
            )
            self._client.download(
                refreshed_url,
                path,
                max_bytes=job.input_limits.max_bytes,
                expected_size=expected_size,
                expected_etag=expected_etag,
            )

    def _backoff_delay(self, failures: int) -> float:
        ceiling = min(self._config.maximum_backoff_seconds, 2 ** min(failures, 10))
        return max(self._config.minimum_delay_seconds, ceiling * random.uniform(0.5, 1.0))


def _success_payload(
    job: ClaimedJob,
    worker_build: str,
    started_at: str,
    finished_at: str,
    result: CaptureMetadataResult | FaceEmbeddingResult | dict[str, object],
    download_ms: int,
    compute_ms: int,
    total_ms: int,
) -> dict[str, object]:
    return _envelope(
        job, worker_build, started_at, finished_at, download_ms, compute_ms, total_ms
    ) | {
        "outcome": "success",
        "result": result if isinstance(result, dict) else result.as_payload(),
    }


def _failure_payload(
    job: ClaimedJob,
    worker_build: str,
    started_at: str,
    finished_at: str,
    code: str,
    retryable: bool,
    download_ms: int,
    compute_ms: int,
    total_ms: int,
) -> dict[str, object]:
    return _envelope(
        job, worker_build, started_at, finished_at, download_ms, compute_ms, total_ms
    ) | {
        "outcome": "failure",
        "error_code": code,
        "retryable": retryable,
        "error_detail": redact(code),
    }


def _envelope(
    job: ClaimedJob,
    worker_build: str,
    started_at: str,
    finished_at: str,
    download_ms: int,
    compute_ms: int,
    total_ms: int,
) -> dict[str, object]:
    return {
        "job_id": job.id,
        "attempt_id": job.attempt_id,
        "contract_version": job.contract_version,
        "processor_type": job.processor_type,
        "processor_version": job.processor_version,
        "worker_build": worker_build,
        "started_at": started_at,
        "finished_at": finished_at,
        "download_ms": download_ms,
        "compute_ms": compute_ms,
        "total_ms": max(total_ms, download_ms + compute_ms),
    }


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _milliseconds(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))


def _finite_positive(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _parse_processor_identity(value: str) -> tuple[int, str, int]:
    """Parse one exact, ordered worker identity without accepting aliases or whitespace."""
    if not isinstance(value, str) or value.strip() != value:
        raise ValueError("invalid processor identity")
    parts = value.split("/")
    if len(parts) != _IDENTITY_PARTS:
        raise ValueError("invalid processor identity")
    try:
        identity = (int(parts[0]), parts[1], int(parts[2]))
    except ValueError as error:
        raise ValueError("invalid processor identity") from error
    if identity not in _SUPPORTED_IDENTITIES:
        raise ValueError("unsupported processor identity")
    return identity


def _assert_terminal_size(payload: dict[str, object], maximum: int) -> None:
    import json

    if len(json.dumps(payload, separators=(",", ":")).encode()) > maximum:
        raise ApiError("invalid_api_response", retryable=False)


def _unlink_temporary(path: Path | None) -> None:
    """Best-effort cleanup without making a second path depend on the first one."""
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _lifecycle(
    phase: str,
    job: ClaimedJob,
    *,
    code: str | None = None,
    download_ms: int | None = None,
    compute_ms: int | None = None,
    secrets: tuple[str, ...] = (),
) -> None:
    """Log only opaque identifiers and bounded stable values, never source metadata or URLs."""
    LOGGER.info(
        "worker_lifecycle phase=%s event_id=%s run_id=%s photo_id=%s job_id=%s attempt_id=%s "
        "code=%s download_ms=%s compute_ms=%s",
        phase,
        redact(job.event_id, secrets=secrets),
        redact(job.run_id, secrets=secrets),
        redact(job.photo_id, secrets=secrets),
        redact(job.id, secrets=secrets),
        redact(job.attempt_id, secrets=secrets),
        code or "",
        download_ms if download_ms is not None else "",
        compute_ms if compute_ms is not None else "",
    )
