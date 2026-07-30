"""Worker-side validation for the versioned private processing contract."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

CONTRACT_VERSION = 1
PROCESSOR_TYPE = "capture_metadata"
PROCESSOR_VERSION = 1
PROCESSOR_TYPE_FACE_EMBEDDING = "face_embedding"
PROCESSOR_VERSION_FACE_EMBEDDING = 1
MAX_FACE_EMBEDDINGS_PER_JOB = 64
MAX_FACE_EMBEDDING_DIMENSIONS = 128
DEFAULT_FACE_DETECTION_THRESHOLD = 0.75
MAX_JSON_FIELD_BYTES = 16_384
MAX_INPUT_BYTES_CAP = 50 * 1024 * 1024
MAX_PIXELS_CAP = 100_000_000
_URL = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_EXIF_FIELDS = ("DateTimeOriginal", "DateTimeDigitized", "DateTime")
FAILURE_RETRYABLE = {
    "decode_failed": False,
    "download_authorization_expired": True,
    "fingerprint_mismatch": False,
    "input_too_large": False,
    "model_inference_error": False,
    "model_inference_timeout": True,
    "network_interruption": True,
    "storage_unavailable": True,
    "unsupported_input": False,
}


class ContractError(ValueError):
    """The API returned a payload incompatible with this worker build."""


def redact(value: object, *, secrets: tuple[str, ...] = ()) -> str:
    """Return bounded text that is safe to put in worker logs."""
    text = _URL.sub("<redacted>", str(value))
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    return text[:512]


@dataclass(frozen=True)
class InputLimits:
    max_bytes: int
    content_type: str


@dataclass(frozen=True)
class InputFingerprint:
    original_key: str
    original_size: int
    original_content_type: str
    verified_source_etag: str | None
    version_evidence: str

    @classmethod
    def from_value(cls, value: object) -> InputFingerprint:
        fields = {
            "original_key",
            "original_size",
            "original_content_type",
            "verified_source_etag",
            "version_evidence",
        }
        if not isinstance(value, dict) or set(value) != fields or not _bounded_json(value):
            raise ContractError("invalid input fingerprint")
        key = value["original_key"]
        size = value["original_size"]
        content_type = value["original_content_type"]
        etag = value["verified_source_etag"]
        evidence = value["version_evidence"]
        if not (
            _safe_string(key, maximum=512)
            and _positive_int(size)
            and content_type == "image/jpeg"
            and evidence in {"verified_source_etag", "unavailable"}
            and (etag is None or _safe_string(etag, maximum=256))
            and ((evidence == "verified_source_etag") == isinstance(etag, str))
        ):
            raise ContractError("invalid input fingerprint")
        return cls(key, size, content_type, etag, evidence)


@dataclass(frozen=True)
class ProcessorConfiguration:
    date_field_precedence: tuple[str, ...]
    normalization: str
    max_input_bytes: int
    max_pixels: int
    heartbeat_interval_seconds: int
    lease_duration_seconds: int
    poll_min_delay_seconds: int
    api_response_max_bytes: int
    terminal_result_max_bytes: int
    max_faces: int = 1
    face_detection_threshold: float = DEFAULT_FACE_DETECTION_THRESHOLD
    model: str = "sface"

    @classmethod
    def from_value(cls, value: object) -> ProcessorConfiguration:
        expected_capture = {
            "retry_policy",
            "max_cohort_size",
            "report_max_bytes",
            "report_row_limits",
            "capture_metadata",
            "worker",
        }
        expected_face = {
            "retry_policy",
            "max_cohort_size",
            "report_max_bytes",
            "report_row_limits",
            "face_embedding",
            "worker",
        }
        if not isinstance(value, dict) or not _bounded_json(value):
            raise ContractError("invalid processor configuration")
        if set(value) == expected_capture:
            capture = value["capture_metadata"]
            face_config = None
        elif set(value) == expected_face:
            capture = None
            face_config = value["face_embedding"]
        else:
            raise ContractError("invalid processor configuration")

        retry = value["retry_policy"]
        rows = value["report_row_limits"]
        worker = value["worker"]

        if not (
            isinstance(retry, dict)
            and set(retry)
            == {
                "max_attempts",
                "base_backoff_seconds",
                "max_backoff_seconds",
                "jitter_seconds",
                "lease_max_seconds",
            }
            and all(_positive_int(item) for item in retry.values())
            and isinstance(rows, dict)
            and set(rows) == {"max_warnings", "max_warning_chars"}
            and all(_positive_int(item) for item in rows.values())
            and _positive_int(value["max_cohort_size"])
            and _positive_int(value["report_max_bytes"])
            and isinstance(worker, dict)
            and set(worker)
            == {
                "concurrency",
                "api_response_max_bytes",
                "heartbeat_interval_seconds",
                "lease_duration_seconds",
                "max_input_bytes",
                "max_pixels",
                "poll_min_delay_seconds",
                "terminal_result_max_bytes",
            }
            and worker["concurrency"] == 1
            and _positive_int(worker["api_response_max_bytes"])
            and worker["api_response_max_bytes"] <= MAX_JSON_FIELD_BYTES
            and _positive_int(worker["heartbeat_interval_seconds"])
            and _positive_int(worker["lease_duration_seconds"])
            and worker["heartbeat_interval_seconds"] < worker["lease_duration_seconds"]
            and _positive_int(worker["poll_min_delay_seconds"])
            and _positive_int(worker["max_input_bytes"])
            and worker["max_input_bytes"] <= MAX_INPUT_BYTES_CAP
            and _positive_int(worker["max_pixels"])
            and worker["max_pixels"] <= MAX_PIXELS_CAP
            and _positive_int(worker["terminal_result_max_bytes"])
            and worker["terminal_result_max_bytes"] <= worker["api_response_max_bytes"]
        ):
            raise ContractError("invalid processor configuration")

        if capture is not None:
            if not (
                isinstance(capture, dict)
                and set(capture) == {"date_field_precedence", "normalization"}
                and isinstance(capture["date_field_precedence"], list)
                and tuple(capture["date_field_precedence"]) == _EXIF_FIELDS
                and capture["normalization"] == "utc_assume_utc_if_missing"
            ):
                raise ContractError("invalid processor configuration")
            max_faces = 1
            face_threshold = DEFAULT_FACE_DETECTION_THRESHOLD
            model = "sface"
        else:
            if not isinstance(face_config, dict):
                raise ContractError("invalid processor configuration")
            if set(face_config) - {
                "max_faces",
                "detection_threshold",
                "model",
                "max_faces_per_photo",
                "min_face_px",
                "normalize_embeddings",
            }:
                raise ContractError("invalid processor configuration")
            configured_max_faces = face_config.get(
                "max_faces", face_config.get("max_faces_per_photo", 1)
            )
            configured_face_threshold = face_config.get(
                "detection_threshold",
                face_config.get("detection_confidence_threshold", DEFAULT_FACE_DETECTION_THRESHOLD),
            )
            if "min_face_px" in face_config and not _positive_int(face_config["min_face_px"]):
                raise ContractError("invalid processor configuration")
            if "normalize_embeddings" in face_config and not isinstance(
                face_config["normalize_embeddings"], bool
            ):
                raise ContractError("invalid processor configuration")
            if "model" in face_config:
                model = str(face_config["model"])
            else:
                model = "sface"
            if not _positive_int(configured_max_faces):
                raise ContractError("invalid processor configuration")
            max_faces = cast(int, configured_max_faces)
            if max_faces > MAX_FACE_EMBEDDINGS_PER_JOB:
                raise ContractError("invalid processor configuration")
            if not _bounded_probability(configured_face_threshold):
                raise ContractError("invalid processor configuration")
            if not isinstance(model, str) or model not in {"sface", "sface-v1", "sface_v1"}:
                raise ContractError("invalid processor configuration")
            face_threshold = cast(float, configured_face_threshold)

        return cls(
            date_field_precedence=_EXIF_FIELDS,
            normalization="utc_assume_utc_if_missing",
            max_input_bytes=worker["max_input_bytes"],
            max_pixels=worker["max_pixels"],
            heartbeat_interval_seconds=worker["heartbeat_interval_seconds"],
            lease_duration_seconds=worker["lease_duration_seconds"],
            poll_min_delay_seconds=worker["poll_min_delay_seconds"],
            api_response_max_bytes=worker["api_response_max_bytes"],
            terminal_result_max_bytes=worker["terminal_result_max_bytes"],
            max_faces=max_faces,
            face_detection_threshold=face_threshold,
            model=model,
        )


@dataclass(frozen=True)
class ClaimedJob:
    id: str
    attempt_id: str
    contract_version: int
    processor_type: str
    processor_version: int
    configuration: ProcessorConfiguration
    photo_id: str
    event_id: str
    run_id: str
    input_fingerprint: InputFingerprint
    input_limits: InputLimits
    lease_expires_at: str
    download_url: str
    download_expires_at: str

    @classmethod
    def from_value(cls, value: object) -> ClaimedJob:
        fields = {
            "id",
            "attempt_id",
            "contract_version",
            "processor_type",
            "processor_version",
            "configuration",
            "photo_id",
            "event_id",
            "run_id",
            "input_fingerprint",
            "input_limits",
            "lease_expires_at",
            "download_url",
            "download_expires_at",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ContractError("invalid claimed job")
        configuration = ProcessorConfiguration.from_value(value["configuration"])
        fingerprint = InputFingerprint.from_value(value["input_fingerprint"])
        limits = _input_limits(value["input_limits"])
        if not (
            value["contract_version"] == CONTRACT_VERSION
            and value["processor_type"] in {PROCESSOR_TYPE, PROCESSOR_TYPE_FACE_EMBEDDING}
            and value["processor_version"] == _processor_version(value["processor_type"])
            and all(_uuid_string(value[name]) for name in ("id", "attempt_id", "run_id"))
            # Events predate the processing app and currently use Django's numeric primary key.
            # Keep the untrusted transport value bounded, but do not invent a UUID-only event API.
            and _photo_identifier(value["event_id"])
            and _photo_identifier(value["photo_id"])
            and limits.max_bytes == fingerprint.original_size
            and limits.content_type == fingerprint.original_content_type
            and limits.max_bytes <= configuration.max_input_bytes
            and _utc_timestamp(value["lease_expires_at"])
            and _utc_timestamp(value["download_expires_at"])
            and _download_url(value["download_url"])
        ):
            raise ContractError("unsupported claimed job")
        return cls(
            id=value["id"],
            attempt_id=value["attempt_id"],
            contract_version=CONTRACT_VERSION,
            processor_type=value["processor_type"],
            processor_version=value["processor_version"],
            configuration=configuration,
            photo_id=value["photo_id"],
            event_id=value["event_id"],
            run_id=value["run_id"],
            input_fingerprint=fingerprint,
            input_limits=limits,
            lease_expires_at=value["lease_expires_at"],
            download_url=value["download_url"],
            download_expires_at=value["download_expires_at"],
        )


@dataclass(frozen=True)
class Claim:
    job: ClaimedJob | None
    suggested_delay_seconds: int | None

    @classmethod
    def empty(cls, suggested_delay_seconds: int) -> Claim:
        if not _positive_int(suggested_delay_seconds) or suggested_delay_seconds > 300:
            raise ContractError("invalid empty-claim delay")
        return cls(job=None, suggested_delay_seconds=suggested_delay_seconds)

    @classmethod
    def from_response(cls, value: object) -> Claim:
        if not isinstance(value, dict) or "empty" not in value or type(value["empty"]) is not bool:
            raise ContractError("invalid claim response")
        if value["empty"]:
            if set(value) != {"empty", "suggested_delay_seconds"}:
                raise ContractError("invalid empty claim response")
            return cls.empty(value["suggested_delay_seconds"])
        if set(value) != {"empty", "job"}:
            raise ContractError("invalid claimed response")
        return cls(job=ClaimedJob.from_value(value["job"]), suggested_delay_seconds=None)


@dataclass(frozen=True)
class CaptureMetadataResult:
    capture_time: str | None
    source_field: str | None
    timezone_state: str
    source_value: str | None
    warnings: tuple[str, ...]

    @classmethod
    def missing(
        cls, warnings: tuple[str, ...] = ("capture_time_missing",)
    ) -> CaptureMetadataResult:
        return cls(None, None, "not_applicable", None, warnings)

    def as_payload(self) -> dict[str, object]:
        return {
            "capture_time": self.capture_time,
            "source_field": self.source_field,
            "timezone_state": self.timezone_state,
            "source_value": self.source_value,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class FaceEmbeddingFace:
    index: int
    bbox: tuple[float, float, float, float]
    confidence: float
    landmarks: tuple[tuple[float, float], ...]
    embedding: tuple[float, ...]

    def as_payload(self) -> dict[str, object]:
        return {
            "index": self.index,
            "bbox": list(self.bbox),
            "confidence": self.confidence,
            "landmarks": [list(point) for point in self.landmarks],
            "embedding": list(self.embedding),
        }


@dataclass(frozen=True)
class FaceEmbeddingResult:
    model: str
    faces: tuple[FaceEmbeddingFace, ...]
    has_single_query_face_usable: bool
    warnings: tuple[str, ...]
    timings: dict[str, int]

    def as_payload(self) -> dict[str, object]:
        return {
            "model": self.model,
            "faces": [face.as_payload() for face in self.faces],
            "face_count": len(self.faces),
            "has_single_query_face_usable": self.has_single_query_face_usable,
            "warnings": list(self.warnings),
            "timings": dict(self.timings),
        }


def _input_limits(value: object) -> InputLimits:
    if (
        not isinstance(value, dict)
        or set(value) != {"max_bytes", "content_type"}
        or not _positive_int(value["max_bytes"])
        or value["content_type"] != "image/jpeg"
    ):
        raise ContractError("invalid input limits")
    return InputLimits(max_bytes=value["max_bytes"], content_type="image/jpeg")


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _safe_string(value: object, *, maximum: int) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= maximum and "\x00" not in value


def _uuid_string(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _photo_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value) is not None
    )


def _bounded_json(value: object) -> bool:
    return (
        len(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())
        <= MAX_JSON_FIELD_BYTES
    )


def _bounded_probability(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


def _processor_version(processor_type: str) -> int:
    if processor_type == PROCESSOR_TYPE:
        return PROCESSOR_VERSION
    if processor_type == PROCESSOR_TYPE_FACE_EMBEDDING:
        return PROCESSOR_VERSION_FACE_EMBEDDING
    raise ContractError("unsupported processor type")


def _utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == UTC.utcoffset(None)


def _download_url(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= 8_192
        and bool(re.fullmatch(r"https://[^\s]+", value))
    )
