"""Worker-side validation for the versioned private processing contract."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from photo_worker.adaface import ADAFACE_EMBEDDING_DIMENSIONS, ADAFACE_MODEL_NAME
from photo_worker.face_quality import FaceQualityError, FaceQualityEvidence, FaceQualityThresholds
from photo_worker.watermark import WATERMARK_ASSET_SHA256S

CONTRACT_VERSION = 1
PROCESSOR_TYPE = "capture_metadata"
CAPTURE_METADATA_PROCESSOR_VERSION = 2
PROCESSOR_VERSION = CAPTURE_METADATA_PROCESSOR_VERSION
PROCESSOR_TYPE_FACE_EMBEDDING = "face_embedding"
PROCESSOR_TYPE_FACE_EMBEDDING_BENCHMARK = "face_embedding_benchmark"
PROCESSOR_VERSION_FACE_EMBEDDING = 1
HISTORICAL_PROCESSOR_VERSION_FACE_EMBEDDING_QUALITY = 3
PROCESSOR_VERSION_FACE_EMBEDDING_QUALITY = 4
PROCESSOR_VERSION_FACE_EMBEDDING_ADAFACE_QUALITY = 5
PREVIEW_CONTRACT_VERSION = 2
PROCESSOR_TYPE_GENERATE_PREVIEW = "generate_preview"
PROCESSOR_VERSION_GENERATE_PREVIEW = 1
PROCESSOR_TYPE_GENERATE_WATERMARKED_PREVIEW = "generate_watermarked_preview"
PROCESSOR_VERSION_GENERATE_WATERMARKED_PREVIEW = 1
PROCESSOR_VERSION_FACE_EMBEDDING_PREVIEW = 3
PROCESSOR_TYPE_SELFIE_QUERY = "selfie_query"
PROCESSOR_VERSION_SELFIE_QUERY = 2
MAX_FACE_EMBEDDINGS_PER_JOB = 32
MAX_FACE_EMBEDDING_DIMENSIONS = ADAFACE_EMBEDDING_DIMENSIONS
SFACE_FACE_EMBEDDING_TERMINAL_PAYLOAD_MAX_BYTES = 128 * 1024
FACE_EMBEDDING_TERMINAL_PAYLOAD_MAX_BYTES = 384 * 1024
SELFIE_MAX_INPUT_BYTES = 20 * 1024 * 1024
SELFIE_MAX_PIXELS = 25_000_000
SELFIE_TERMINAL_PAYLOAD_MAX_BYTES = 16 * 1024
DEFAULT_FACE_DETECTION_THRESHOLD = 0.75
SFACE_MODEL_NAME = "sface"
SFACE_EMBEDDING_DIMENSIONS = 128
_PINNED_ADAFACE_IDENTITY = {
    "alignment": "scrfd-five-landmark-112x112",
    "input_normalization": "rgb-value-over-255-minus-0.5-over-0.5",
    "model_artifact_sha256": "3a416518b11ece107b43385fc3678aad1d4f2405fde9f58f0be7f530230e368b",
    "model_revision": "0dd53f188fa27968b0a1326970ebf4aeb37ce2ca",
}
_PINNED_SCRFD_IDENTITY = {
    "input_size": [640, 640],
    "model": "scrfd-10g-kps",
    "model_artifact_sha256": "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
    "nms_threshold": 0.4,
}
MAX_JSON_FIELD_BYTES = FACE_EMBEDDING_TERMINAL_PAYLOAD_MAX_BYTES
MAX_INPUT_BYTES_CAP = 50 * 1024 * 1024
MAX_PIXELS_CAP = 100_000_000
# Preview normalization may retain a 4 B/px decoded CMYK source, a 4 B/px orientation copy,
# and a 3 B/px sRGB output. At 24 MP those full-frame buffers total about 252 MiB. Doubling that
# for decoder/LCMS transients, then reserving 128 MiB of runtime margin and the 10 MiB output bound,
# stays near 642 MiB under the 768 MiB container limit. This is a static safety guard, not capacity
# evidence; activation still requires the documented concurrency-one RSS measurement.
MAX_PREVIEW_PIXELS_CAP = 24_000_000
V2_GENERATE_PREVIEW_CONFIGURATION: dict[str, object] = {
    "retry_policy": {
        "max_attempts": 3,
        "base_backoff_seconds": 30,
        "max_backoff_seconds": 300,
        "jitter_seconds": 5,
        "lease_max_seconds": 300,
    },
    "max_cohort_size": 16,
    "report_max_bytes": 262_144,
    "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
    "generate_preview": {
        "variant": "preview-small-v1",
        "output_format": "jpeg",
        "max_long_edge": 1600,
        "jpeg_quality": 85,
        "color_space": "srgb",
        "upscale": False,
        "apply_exif_orientation": True,
        "strip_metadata": True,
        "watermark": "none",
        "max_output_bytes": 10_485_760,
        "max_output_width": 1600,
        "max_output_height": 1600,
        "checksum_algorithm": "sha256",
    },
    "worker": {
        "api_response_max_bytes": 16_384,
        "concurrency": 1,
        "heartbeat_interval_seconds": 30,
        "lease_duration_seconds": 120,
        "max_input_bytes": 50 * 1024 * 1024,
        "max_pixels": MAX_PREVIEW_PIXELS_CAP,
        "poll_min_delay_seconds": 5,
        "terminal_result_max_bytes": 8_192,
    },
}
V2_GENERATE_WATERMARKED_PREVIEW_CONFIGURATION: dict[str, object] = {
    "retry_policy": {
        "max_attempts": 3,
        "base_backoff_seconds": 30,
        "max_backoff_seconds": 300,
        "jitter_seconds": 5,
        "lease_max_seconds": 300,
    },
    "max_cohort_size": 16,
    "report_max_bytes": 262_144,
    "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
    "generate_watermarked_preview": {
        "variant": "preview-watermarked-v1",
        "input_variant": "preview-small-v1",
        "output_format": "jpeg",
        "jpeg_quality": 85,
        "color_space": "srgb",
        "strip_metadata": True,
        "checksum_algorithm": "sha256",
        "landscape_asset_sha256": WATERMARK_ASSET_SHA256S["landscape"],
        "portrait_asset_sha256": WATERMARK_ASSET_SHA256S["portrait"],
    },
    "worker": {
        "api_response_max_bytes": 16_384,
        "concurrency": 1,
        "heartbeat_interval_seconds": 30,
        "lease_duration_seconds": 120,
        "max_input_bytes": 10_485_760,
        "max_pixels": 2_560_000,
        "poll_min_delay_seconds": 5,
        "terminal_result_max_bytes": 8_192,
    },
}
V2_FACE_EMBEDDING_CONFIGURATION: dict[str, object] = {
    "retry_policy": {
        "max_attempts": 3,
        "base_backoff_seconds": 30,
        "max_backoff_seconds": 300,
        "jitter_seconds": 5,
        "lease_max_seconds": 300,
    },
    "max_cohort_size": 16,
    "report_max_bytes": 262_144,
    "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
    "face_embedding": {
        "model": SFACE_MODEL_NAME,
        "min_face_px": 32,
        "max_faces_per_photo": 32,
        "normalize_embeddings": True,
    },
    "worker": {
        "api_response_max_bytes": SFACE_FACE_EMBEDDING_TERMINAL_PAYLOAD_MAX_BYTES,
        "concurrency": 1,
        "heartbeat_interval_seconds": 30,
        "lease_duration_seconds": 120,
        "max_input_bytes": 50 * 1024 * 1024,
        "max_pixels": 100_000_000,
        "poll_min_delay_seconds": 5,
        "terminal_result_max_bytes": SFACE_FACE_EMBEDDING_TERMINAL_PAYLOAD_MAX_BYTES,
    },
}
SCRFD_FACE_EMBEDDING_CONFIGURATION: dict[str, object] = {
    **V2_FACE_EMBEDDING_CONFIGURATION,
    "face_embedding": {
        **cast(dict[str, object], V2_FACE_EMBEDDING_CONFIGURATION["face_embedding"]),
        "detection_threshold": 0.5,
    },
}
FACE_EMBEDDING_BENCHMARK_CONFIGURATION: dict[str, object] = {
    **V2_FACE_EMBEDDING_CONFIGURATION,
    "max_cohort_size": 500,
    "benchmark": {
        "label": "baseline",
        "source_mode": "event",
        "source_run_id": None,
        "requested_count": 1,
    },
}
_URL = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_SELFIE_KEY = re.compile(r"selfie-search/[0-9a-f]{32}")
_ORIGINAL_KEY = re.compile(r"originals/[0-9a-f]{32}")
_PUBLISHED_PREVIEW_KEY = re.compile(
    r"derivatives/previews/(?P<photo_id>[A-Za-z0-9_-]{1,32})/preview-small-v1/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-"
    r"[0-9a-f]{64}\.jpg"
)
_EXIF_FIELDS = ("DateTimeOriginal", "DateTimeDigitized", "DateTime")
FAILURE_RETRYABLE = {
    "decode_failed": False,
    "download_authorization_expired": True,
    "fingerprint_mismatch": True,
    "input_too_large": False,
    "model_inference_error": False,
    "model_inference_timeout": True,
    "network_interruption": True,
    "no_face_detected": False,
    "multiple_faces_detected": False,
    "quality_rejected": False,
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
    original_key: str | None = None
    original_size: int | None = None
    original_content_type: str | None = None
    verified_source_etag: str | None = None
    version_evidence: str | None = None
    object_key: str | None = None
    object_size: int | None = None
    object_content_type: str | None = None
    object_etag: str | None = None
    media_kind: str | None = None
    pixel_width: int | None = None
    pixel_height: int | None = None

    @classmethod
    def from_value(cls, value: object, *, contract_version: int) -> InputFingerprint:
        original_fields = {
            "original_key",
            "original_size",
            "original_content_type",
            "verified_source_etag",
            "version_evidence",
        }
        generic_fields = {
            "object_key",
            "object_size",
            "object_content_type",
            "object_etag",
            "media_kind",
            "pixel_width",
            "pixel_height",
        }
        if not isinstance(value, dict) or not _bounded_json(value):
            raise ContractError("invalid input fingerprint")
        if set(value) == original_fields:
            if contract_version not in {CONTRACT_VERSION, 3}:
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
                and (
                    contract_version != 3
                    or (isinstance(key, str) and _ORIGINAL_KEY.fullmatch(key) is not None)
                )
            ):
                raise ContractError("invalid input fingerprint")
            return cls(
                original_key=key,
                original_size=size,
                original_content_type=content_type,
                verified_source_etag=etag,
                version_evidence=evidence,
            )
        if set(value) != generic_fields or contract_version not in {PREVIEW_CONTRACT_VERSION, 3}:
            raise ContractError("invalid input fingerprint")
        key = value["object_key"]
        size = value["object_size"]
        content_type = value["object_content_type"]
        etag = value["object_etag"]
        media_kind = value["media_kind"]
        width = value["pixel_width"]
        height = value["pixel_height"]
        if not (
            _safe_string(key, maximum=512)
            and _positive_int(size)
            and content_type == "image/jpeg"
            and (etag is None or _safe_string(etag, maximum=256))
            and media_kind in {"original", "preview-small-v1"}
            and _positive_int(width)
            and _positive_int(height)
            and (
                contract_version != 3
                or (
                    isinstance(key, str)
                    and _PUBLISHED_PREVIEW_KEY.fullmatch(key) is not None
                    and etag is None
                    and media_kind == "preview-small-v1"
                )
            )
        ):
            raise ContractError("invalid input fingerprint")
        return cls(
            object_key=key,
            object_size=size,
            object_content_type=content_type,
            object_etag=etag,
            media_kind=media_kind,
            pixel_width=width,
            pixel_height=height,
        )


@dataclass(frozen=True)
class SelfieInputFingerprint:
    temporary_key: str
    temporary_size: int
    temporary_content_type: str

    @classmethod
    def from_value(cls, value: object) -> SelfieInputFingerprint:
        fields = {"temporary_key", "temporary_size", "temporary_content_type"}
        if not isinstance(value, dict) or set(value) != fields or not _bounded_json(value):
            raise ContractError("invalid selfie input fingerprint")
        key = value["temporary_key"]
        size = value["temporary_size"]
        content_type = value["temporary_content_type"]
        if not (
            isinstance(key, str)
            and _SELFIE_KEY.fullmatch(key) is not None
            and _positive_int(size)
            and size <= SELFIE_MAX_INPUT_BYTES
            and content_type in {"image/jpeg", "image/png"}
        ):
            raise ContractError("invalid selfie input fingerprint")
        return cls(key, size, content_type)


@dataclass(frozen=True)
class ProcessorConfiguration:
    configuration_kind: str
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
    model: str = SFACE_MODEL_NAME
    preview_variant: str | None = None
    embedding_dimensions: int = SFACE_EMBEDDING_DIMENSIONS
    minimum_face_px: int = 1
    event_timezone: str | None = None
    quality_thresholds: FaceQualityThresholds | None = None
    watermark_asset_sha256s: dict[str, str] | None = None

    @classmethod
    def from_value(cls, value: object) -> ProcessorConfiguration:
        max_faces: object = 1
        face_threshold: object = DEFAULT_FACE_DETECTION_THRESHOLD
        model: object = SFACE_MODEL_NAME
        event_timezone: str | None = None
        quality_thresholds: FaceQualityThresholds | None = None
        normalization = "utc_assume_utc_if_missing"
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
        expected_adaface_face = expected_face | {"adaface", "scrfd"}
        expected_benchmark = expected_face | {"benchmark"}
        expected_preview = {
            "retry_policy",
            "max_cohort_size",
            "report_max_bytes",
            "report_row_limits",
            "generate_preview",
            "worker",
        }
        expected_watermarked_preview = {
            "retry_policy",
            "max_cohort_size",
            "report_max_bytes",
            "report_row_limits",
            "generate_watermarked_preview",
            "worker",
        }
        expected_selfie = {
            "retry_policy",
            "max_cohort_size",
            "report_max_bytes",
            "report_row_limits",
            "selfie_query",
            "worker",
        }
        expected_adaface_selfie = expected_selfie | {"adaface", "scrfd"}
        if not isinstance(value, dict) or not _bounded_json(value):
            raise ContractError("invalid processor configuration")
        if set(value) == expected_capture:
            capture = value["capture_metadata"]
            face_config = None
            preview_config = None
            watermark_config = None
            selfie_config = None
            configuration_kind = "capture_metadata"
        elif set(value) == expected_face or set(value) == expected_adaface_face:
            capture = None
            face_config = value["face_embedding"]
            preview_config = None
            watermark_config = None
            selfie_config = None
            configuration_kind = "face_embedding"
        elif set(value) == expected_benchmark:
            capture = None
            face_config = value["face_embedding"]
            preview_config = None
            watermark_config = None
            selfie_config = None
            configuration_kind = "face_embedding_benchmark"
        elif set(value) == expected_preview:
            capture = None
            face_config = None
            preview_config = value["generate_preview"]
            watermark_config = None
            selfie_config = None
            configuration_kind = "generate_preview"
        elif set(value) == expected_watermarked_preview:
            capture = None
            face_config = None
            preview_config = None
            watermark_config = value["generate_watermarked_preview"]
            selfie_config = None
            configuration_kind = "generate_watermarked_preview"
        elif set(value) in (expected_selfie, expected_adaface_selfie):
            capture = None
            face_config = None
            preview_config = None
            watermark_config = None
            selfie_config = value["selfie_query"]
            configuration_kind = "selfie_query"
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
        ):
            raise ContractError("invalid processor configuration")

        benchmark = value.get("benchmark")
        if configuration_kind == PROCESSOR_TYPE_FACE_EMBEDDING_BENCHMARK and not (
            isinstance(benchmark, dict)
            and set(benchmark) == {"label", "source_mode", "source_run_id", "requested_count"}
            and _safe_string(benchmark["label"], maximum=64)
            and benchmark["source_mode"] in {"event", "replay"}
            and (benchmark["source_run_id"] is None or _uuid_string(benchmark["source_run_id"]))
            and _positive_int(benchmark["requested_count"])
            and benchmark["requested_count"] <= 500
        ):
            raise ContractError("invalid processor configuration")

        if selfie_config is not None:
            is_adaface = (
                isinstance(selfie_config, dict)
                and selfie_config.get("model") == ADAFACE_MODEL_NAME
                and selfie_config.get("embedding_dimensions") == ADAFACE_EMBEDDING_DIMENSIONS
                and value.get("adaface") == _PINNED_ADAFACE_IDENTITY
                and value.get("scrfd") == _PINNED_SCRFD_IDENTITY
            )
            is_sface = (
                isinstance(selfie_config, dict)
                and selfie_config.get("model") == SFACE_MODEL_NAME
                and selfie_config.get("embedding_dimensions") == SFACE_EMBEDDING_DIMENSIONS
                and "adaface" not in value
                and "scrfd" not in value
            )
            if not (
                isinstance(selfie_config, dict)
                and set(selfie_config)
                == {"detection_threshold", "embedding_dimensions", "min_face_px", "model"}
                and (is_adaface or is_sface)
                and selfie_config["min_face_px"] == 32
                and _bounded_probability(selfie_config["detection_threshold"])
                and worker["max_input_bytes"] == SELFIE_MAX_INPUT_BYTES
                and worker["max_pixels"] == SELFIE_MAX_PIXELS
                and worker["terminal_result_max_bytes"] == SELFIE_TERMINAL_PAYLOAD_MAX_BYTES
            ):
                raise ContractError("invalid processor configuration")
            max_faces = 1
            face_threshold = selfie_config["detection_threshold"]
            model = selfie_config["model"]
            embedding_dimensions = selfie_config["embedding_dimensions"]
            minimum_face_px = 32
        elif capture is not None:
            if not (
                isinstance(capture, dict)
                and set(capture) == {"date_field_precedence", "normalization", "event_timezone"}
                and isinstance(capture["date_field_precedence"], list)
                and tuple(capture["date_field_precedence"]) == _EXIF_FIELDS
                and capture["normalization"] == "utc_explicit_offset_or_event_timezone"
                and _valid_event_timezone(capture["event_timezone"])
            ):
                raise ContractError("invalid processor configuration")
            event_timezone = cast(str, capture["event_timezone"])
            normalization = cast(str, capture["normalization"])
            max_faces = 1
            face_threshold = DEFAULT_FACE_DETECTION_THRESHOLD
            model = SFACE_MODEL_NAME
            embedding_dimensions = SFACE_EMBEDDING_DIMENSIONS
            minimum_face_px = 1
        elif face_config is not None:
            if not isinstance(face_config, dict):
                raise ContractError("invalid processor configuration")
            has_quality = "quality" in face_config
            allowed_face_fields = {
                "max_faces",
                "detection_threshold",
                "model",
                "embedding_dimensions",
                "max_faces_per_photo",
                "min_face_px",
                "normalize_embeddings",
            } | ({"quality"} if has_quality else set())
            if set(face_config) - allowed_face_fields:
                raise ContractError("invalid processor configuration")
            if has_quality:
                if face_config.get("model") == SFACE_MODEL_NAME:
                    if set(face_config) != {
                        "max_faces",
                        "detection_threshold",
                        "model",
                        "normalize_embeddings",
                        "quality",
                    }:
                        raise ContractError("invalid processor configuration")
                    model = SFACE_MODEL_NAME
                    embedding_dimensions = SFACE_EMBEDDING_DIMENSIONS
                elif face_config.get("model") == ADAFACE_MODEL_NAME:
                    if (
                        set(face_config)
                        != {
                            "max_faces",
                            "detection_threshold",
                            "model",
                            "embedding_dimensions",
                            "normalize_embeddings",
                            "quality",
                        }
                        or face_config["embedding_dimensions"] != MAX_FACE_EMBEDDING_DIMENSIONS
                    ):
                        raise ContractError("invalid processor configuration")
                    model = ADAFACE_MODEL_NAME
                    embedding_dimensions = MAX_FACE_EMBEDDING_DIMENSIONS
                else:
                    raise ContractError("invalid processor configuration")
                if face_config["normalize_embeddings"] is not True:
                    raise ContractError("invalid processor configuration")
                quality = face_config["quality"]
                if not isinstance(quality, dict) or set(quality) != {
                    "algorithm_version",
                    "crop_size",
                    "minimum_face_px",
                    "severe_blur_threshold",
                    "borderline_blur_threshold",
                    "minimum_relative_area",
                    "minimum_confidence",
                }:
                    raise ContractError("invalid processor configuration")
                try:
                    quality_thresholds = FaceQualityThresholds(**quality)
                except (FaceQualityError, TypeError) as error:
                    raise ContractError("invalid processor configuration") from error
            adaface = value.get("adaface")
            scrfd = value.get("scrfd")
            if adaface is not None and (
                adaface != _PINNED_ADAFACE_IDENTITY or scrfd != _PINNED_SCRFD_IDENTITY
            ):
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
            if (
                not has_quality
                and face_config
                not in (
                    V2_FACE_EMBEDDING_CONFIGURATION["face_embedding"],
                    SCRFD_FACE_EMBEDDING_CONFIGURATION["face_embedding"],
                )
                and (
                    face_config.get("model") != ADAFACE_MODEL_NAME
                    or face_config.get("embedding_dimensions") != ADAFACE_EMBEDDING_DIMENSIONS
                    or face_config.get("normalize_embeddings") is not True
                )
            ):
                raise ContractError("invalid processor configuration")
            if not has_quality:
                model = cast(str, face_config["model"])
            if (
                not _positive_int(configured_max_faces)
                or cast(int, configured_max_faces) > MAX_FACE_EMBEDDINGS_PER_JOB
            ):
                raise ContractError("invalid processor configuration")
            if not _bounded_probability(configured_face_threshold):
                raise ContractError("invalid processor configuration")
            max_faces = cast(int, configured_max_faces)
            required_payload_bytes = (
                FACE_EMBEDDING_TERMINAL_PAYLOAD_MAX_BYTES
                if model == ADAFACE_MODEL_NAME
                else SFACE_FACE_EMBEDDING_TERMINAL_PAYLOAD_MAX_BYTES
            )
            if worker["terminal_result_max_bytes"] < required_payload_bytes:
                raise ContractError("terminal_result_max_bytes cannot carry face result")
            if worker["api_response_max_bytes"] < required_payload_bytes:
                raise ContractError("api_response_max_bytes cannot carry face result")
            face_threshold = cast(float, configured_face_threshold)
            if not has_quality:
                embedding_dimensions = (
                    ADAFACE_EMBEDDING_DIMENSIONS
                    if model == ADAFACE_MODEL_NAME
                    else SFACE_EMBEDDING_DIMENSIONS
                )
            minimum_face_px = (
                quality_thresholds.minimum_face_px
                if quality_thresholds is not None
                else int(face_config.get("min_face_px", 1))
            )
        elif preview_config is not None:
            if not (
                isinstance(preview_config, dict)
                and preview_config == V2_GENERATE_PREVIEW_CONFIGURATION["generate_preview"]
            ):
                raise ContractError("invalid processor configuration")
            max_faces = 1
            face_threshold = DEFAULT_FACE_DETECTION_THRESHOLD
            model = SFACE_MODEL_NAME
            embedding_dimensions = SFACE_EMBEDDING_DIMENSIONS
            minimum_face_px = 1
        else:
            if not (
                isinstance(watermark_config, dict)
                and watermark_config
                == V2_GENERATE_WATERMARKED_PREVIEW_CONFIGURATION["generate_watermarked_preview"]
                and worker["max_input_bytes"] == 10_485_760
                and worker["max_pixels"] == 2_560_000
            ):
                raise ContractError("invalid processor configuration")
            max_faces = 1
            face_threshold = DEFAULT_FACE_DETECTION_THRESHOLD
            model = SFACE_MODEL_NAME
            embedding_dimensions = SFACE_EMBEDDING_DIMENSIONS
            minimum_face_px = 1

        if worker["terminal_result_max_bytes"] > worker["api_response_max_bytes"]:
            raise ContractError("invalid processor configuration")

        return cls(
            configuration_kind=configuration_kind,
            date_field_precedence=_EXIF_FIELDS,
            normalization=normalization,
            max_input_bytes=worker["max_input_bytes"],
            max_pixels=worker["max_pixels"],
            heartbeat_interval_seconds=worker["heartbeat_interval_seconds"],
            lease_duration_seconds=worker["lease_duration_seconds"],
            poll_min_delay_seconds=worker["poll_min_delay_seconds"],
            api_response_max_bytes=worker["api_response_max_bytes"],
            terminal_result_max_bytes=worker["terminal_result_max_bytes"],
            max_faces=max_faces,
            face_detection_threshold=cast(float, face_threshold),
            model=cast(str, model),
            preview_variant=(
                "preview-small-v1"
                if configuration_kind == "generate_preview"
                else (
                    "preview-watermarked-v1"
                    if configuration_kind == "generate_watermarked_preview"
                    else None
                )
            ),
            embedding_dimensions=embedding_dimensions,
            minimum_face_px=minimum_face_px,
            event_timezone=event_timezone,
            quality_thresholds=quality_thresholds,
            watermark_asset_sha256s=(
                {
                    "landscape": cast(dict[str, str], watermark_config)["landscape_asset_sha256"],
                    "portrait": cast(dict[str, str], watermark_config)["portrait_asset_sha256"],
                }
                if configuration_kind == "generate_watermarked_preview"
                else None
            ),
        )


@dataclass(frozen=True)
class OutputSlot:
    variant: str
    upload_url: str
    upload_expires_at: str
    content_type: str
    staging_key: str
    max_bytes: int
    max_width: int
    max_height: int
    checksum_algorithm: str

    @classmethod
    def from_value(cls, value: object, *, attempt_id: str) -> OutputSlot:
        fields = {
            "variant",
            "upload_url",
            "upload_expires_at",
            "content_type",
            "staging_key",
            "max_bytes",
            "max_width",
            "max_height",
            "checksum_algorithm",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ContractError("invalid preview output slot")
        variant = value["variant"]
        if variant not in {"preview-small-v1", "preview-watermarked-v1"}:
            raise ContractError("invalid preview output slot")
        expected_key = f"processing-pending/previews/{attempt_id}/{variant}.jpg"
        if not (
            _download_url(value["upload_url"])
            and _utc_timestamp(value["upload_expires_at"])
            and value["content_type"] == "image/jpeg"
            and value["staging_key"] == expected_key
            and value["max_bytes"] == 10_485_760
            and value["max_width"] == value["max_height"] == 1600
            and value["checksum_algorithm"] == "sha256"
        ):
            raise ContractError("invalid preview output slot")
        return cls(**value)


@dataclass(frozen=True)
class ClaimedJob:
    id: str
    attempt_id: str
    contract_version: int
    processor_type: str
    processor_version: int
    configuration: ProcessorConfiguration
    photo_id: str | None
    event_id: str | None
    run_id: str | None
    search_id: str | None
    input_fingerprint: InputFingerprint | SelfieInputFingerprint
    input_limits: InputLimits
    lease_expires_at: str
    download_url: str
    download_expires_at: str
    input_geometry: dict[str, int | str] | None = None
    output_slots: tuple[OutputSlot, ...] = ()

    @classmethod
    def from_value(cls, value: object) -> ClaimedJob:
        photo_fields = {
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
        selfie_fields = photo_fields - {"photo_id", "run_id"} | {"search_id"}
        if not isinstance(value, dict):
            raise ContractError("invalid claimed job")
        version = value.get("contract_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise ContractError("invalid claimed job")
        processor_type = value.get("processor_type")
        processor_version = value.get("processor_version")
        identity = (version, processor_type, processor_version)
        if processor_type == PROCESSOR_TYPE_SELFIE_QUERY:
            if set(value) != selfie_fields:
                raise ContractError("invalid claimed job")
            configuration = ProcessorConfiguration.from_value(value["configuration"])
            selfie_fingerprint = SelfieInputFingerprint.from_value(value["input_fingerprint"])
            limits = _input_limits(value["input_limits"], content_types={"image/jpeg", "image/png"})
            output_slots: tuple[OutputSlot, ...] = ()
            input_geometry: dict[str, int | str] | None = None
            valid = (
                identity
                == (CONTRACT_VERSION, PROCESSOR_TYPE_SELFIE_QUERY, PROCESSOR_VERSION_SELFIE_QUERY)
                and configuration.configuration_kind == PROCESSOR_TYPE_SELFIE_QUERY
                and _uuid_string(value["id"])
                and _uuid_string(value["attempt_id"])
                and _uuid_string(value["search_id"])
                and _photo_identifier(value["event_id"])
                and limits.max_bytes == selfie_fingerprint.temporary_size
                and limits.content_type == selfie_fingerprint.temporary_content_type
                and limits.max_bytes <= SELFIE_MAX_INPUT_BYTES
                and limits.max_bytes <= configuration.max_input_bytes
                and _utc_timestamp(value["lease_expires_at"])
                and _utc_timestamp(value["download_expires_at"])
                and _download_url(value["download_url"])
            )
            photo_id = run_id = None
            event_id = cast(str, value["event_id"])
            search_id = cast(str, value["search_id"])
            fingerprint: InputFingerprint | SelfieInputFingerprint = selfie_fingerprint
        else:
            preview = identity == (
                PREVIEW_CONTRACT_VERSION,
                PROCESSOR_TYPE_GENERATE_PREVIEW,
                PROCESSOR_VERSION_GENERATE_PREVIEW,
            )
            watermarked_preview = identity == (
                PREVIEW_CONTRACT_VERSION,
                PROCESSOR_TYPE_GENERATE_WATERMARKED_PREVIEW,
                PROCESSOR_VERSION_GENERATE_WATERMARKED_PREVIEW,
            )
            preview_face = identity == (
                PREVIEW_CONTRACT_VERSION,
                PROCESSOR_TYPE_FACE_EMBEDDING,
                PROCESSOR_VERSION_FACE_EMBEDDING_PREVIEW,
            )
            quality_face = identity in {
                (
                    3,
                    PROCESSOR_TYPE_FACE_EMBEDDING,
                    HISTORICAL_PROCESSOR_VERSION_FACE_EMBEDDING_QUALITY,
                ),
                (3, PROCESSOR_TYPE_FACE_EMBEDDING, PROCESSOR_VERSION_FACE_EMBEDDING_QUALITY),
                (
                    3,
                    PROCESSOR_TYPE_FACE_EMBEDDING,
                    PROCESSOR_VERSION_FACE_EMBEDDING_ADAFACE_QUALITY,
                ),
            }
            preview_only_quality_face = identity == (
                3,
                PROCESSOR_TYPE_FACE_EMBEDDING,
                PROCESSOR_VERSION_FACE_EMBEDDING_QUALITY,
            )
            preview_only_quality_face = preview_only_quality_face or identity == (
                3,
                PROCESSOR_TYPE_FACE_EMBEDDING,
                PROCESSOR_VERSION_FACE_EMBEDDING_ADAFACE_QUALITY,
            )
            quality_face_with_geometry = quality_face and "input_geometry" in value
            fields = (
                photo_fields
                | ({"output_slots"} if preview or watermarked_preview else set())
                | ({"input_geometry"} if preview_face or quality_face_with_geometry else set())
            )
            if set(value) != fields:
                raise ContractError("invalid claimed job")
            configuration = ProcessorConfiguration.from_value(value["configuration"])
            quality_identity_matches = (
                identity
                in {
                    (
                        3,
                        PROCESSOR_TYPE_FACE_EMBEDDING,
                        HISTORICAL_PROCESSOR_VERSION_FACE_EMBEDDING_QUALITY,
                    ),
                    (3, PROCESSOR_TYPE_FACE_EMBEDDING, PROCESSOR_VERSION_FACE_EMBEDDING_QUALITY),
                }
                and _is_sface_quality_configuration(value["configuration"])
            ) or (
                identity
                == (
                    3,
                    PROCESSOR_TYPE_FACE_EMBEDDING,
                    PROCESSOR_VERSION_FACE_EMBEDDING_ADAFACE_QUALITY,
                )
                and _is_pinned_adaface_quality_configuration(value["configuration"])
            )
            photo_fingerprint = InputFingerprint.from_value(
                value["input_fingerprint"], contract_version=version
            )
            limits = _input_limits(value["input_limits"], content_types={"image/jpeg"})
            output_slots = tuple(
                OutputSlot.from_value(slot, attempt_id=cast(str, value["attempt_id"]))
                for slot in value.get("output_slots", [])
            )
            input_geometry = cast(dict[str, int | str] | None, value.get("input_geometry"))
            expected_size = (
                photo_fingerprint.object_size
                if photo_fingerprint.object_key is not None
                else photo_fingerprint.original_size
            )
            expected_content_type = (
                photo_fingerprint.object_content_type
                if photo_fingerprint.object_key is not None
                else photo_fingerprint.original_content_type
            )
            supported_identity = identity in {
                (CONTRACT_VERSION, PROCESSOR_TYPE, PROCESSOR_VERSION),
                (
                    CONTRACT_VERSION,
                    PROCESSOR_TYPE_FACE_EMBEDDING,
                    PROCESSOR_VERSION_FACE_EMBEDDING,
                ),
                (
                    3,
                    PROCESSOR_TYPE_FACE_EMBEDDING,
                    HISTORICAL_PROCESSOR_VERSION_FACE_EMBEDDING_QUALITY,
                ),
                (3, PROCESSOR_TYPE_FACE_EMBEDDING, PROCESSOR_VERSION_FACE_EMBEDDING_QUALITY),
                (
                    3,
                    PROCESSOR_TYPE_FACE_EMBEDDING,
                    PROCESSOR_VERSION_FACE_EMBEDDING_ADAFACE_QUALITY,
                ),
                (3, PROCESSOR_TYPE_FACE_EMBEDDING_BENCHMARK, 1),
                (
                    PREVIEW_CONTRACT_VERSION,
                    PROCESSOR_TYPE_GENERATE_PREVIEW,
                    PROCESSOR_VERSION_GENERATE_PREVIEW,
                ),
                (
                    PREVIEW_CONTRACT_VERSION,
                    PROCESSOR_TYPE_GENERATE_WATERMARKED_PREVIEW,
                    PROCESSOR_VERSION_GENERATE_WATERMARKED_PREVIEW,
                ),
                (
                    PREVIEW_CONTRACT_VERSION,
                    PROCESSOR_TYPE_FACE_EMBEDDING,
                    PROCESSOR_VERSION_FACE_EMBEDDING_PREVIEW,
                ),
            }
            identity_matches_contract = (
                (
                    identity == (CONTRACT_VERSION, PROCESSOR_TYPE, PROCESSOR_VERSION)
                    and configuration.configuration_kind == PROCESSOR_TYPE
                )
                or (
                    identity == (3, PROCESSOR_TYPE_FACE_EMBEDDING_BENCHMARK, 1)
                    and configuration.configuration_kind == PROCESSOR_TYPE_FACE_EMBEDDING_BENCHMARK
                    and photo_fingerprint.original_key is not None
                    and photo_fingerprint.object_key is None
                    and input_geometry is None
                )
                or (
                    identity
                    == (
                        CONTRACT_VERSION,
                        PROCESSOR_TYPE_FACE_EMBEDDING,
                        PROCESSOR_VERSION_FACE_EMBEDDING,
                    )
                    and configuration.configuration_kind == PROCESSOR_TYPE_FACE_EMBEDDING
                    and configuration.quality_thresholds is None
                )
                or (
                    quality_face
                    and quality_identity_matches
                    and configuration.configuration_kind == PROCESSOR_TYPE_FACE_EMBEDDING
                    and configuration.quality_thresholds is not None
                    and (
                        (
                            not preview_only_quality_face
                            and photo_fingerprint.original_key is not None
                            and input_geometry is None
                        )
                        or (
                            photo_fingerprint.media_kind == "preview-small-v1"
                            and _preview_key_matches_photo(photo_fingerprint, value["photo_id"])
                            and _valid_preview_input_geometry(input_geometry, photo_fingerprint)
                        )
                    )
                )
                or (
                    preview
                    and configuration.configuration_kind == PROCESSOR_TYPE_GENERATE_PREVIEW
                    and value["configuration"] == V2_GENERATE_PREVIEW_CONFIGURATION
                    and photo_fingerprint.media_kind == "original"
                    and len(output_slots) == 1
                    and output_slots[0].variant == "preview-small-v1"
                )
                or (
                    watermarked_preview
                    and configuration.configuration_kind
                    == PROCESSOR_TYPE_GENERATE_WATERMARKED_PREVIEW
                    and value["configuration"] == V2_GENERATE_WATERMARKED_PREVIEW_CONFIGURATION
                    and photo_fingerprint.media_kind == "preview-small-v1"
                    and _preview_key_matches_photo(photo_fingerprint, value["photo_id"])
                    and photo_fingerprint.object_etag is None
                    and len(output_slots) == 1
                    and output_slots[0].variant == "preview-watermarked-v1"
                )
                or (
                    preview_face
                    and configuration.configuration_kind == PROCESSOR_TYPE_FACE_EMBEDDING
                    and value["configuration"] == SCRFD_FACE_EMBEDDING_CONFIGURATION
                    and photo_fingerprint.media_kind == "preview-small-v1"
                    and not output_slots
                    and _valid_preview_input_geometry(input_geometry, photo_fingerprint)
                )
            )
            expects_output_slot = preview or watermarked_preview
            valid = (
                supported_identity
                and all(_uuid_string(value[name]) for name in ("id", "attempt_id", "run_id"))
                and _photo_identifier(value["event_id"])
                and _photo_identifier(value["photo_id"])
                and limits.max_bytes == expected_size
                and limits.content_type == expected_content_type
                and limits.max_bytes <= configuration.max_input_bytes
                and _utc_timestamp(value["lease_expires_at"])
                and _utc_timestamp(value["download_expires_at"])
                and _download_url(value["download_url"])
                and ((len(output_slots) == 1) if expects_output_slot else not output_slots)
                and identity_matches_contract
            )
            photo_id = cast(str, value["photo_id"])
            event_id = cast(str, value["event_id"])
            run_id = cast(str, value["run_id"])
            search_id = None
            fingerprint = photo_fingerprint
        if not valid:
            raise ContractError("unsupported claimed job")
        return cls(
            id=value["id"],
            attempt_id=value["attempt_id"],
            contract_version=cast(int, value["contract_version"]),
            processor_type=cast(str, value["processor_type"]),
            processor_version=cast(int, value["processor_version"]),
            configuration=configuration,
            photo_id=photo_id,
            event_id=event_id,
            run_id=run_id,
            search_id=search_id,
            input_fingerprint=fingerprint,
            input_limits=limits,
            lease_expires_at=value["lease_expires_at"],
            download_url=value["download_url"],
            download_expires_at=value["download_expires_at"],
            input_geometry=input_geometry,
            output_slots=output_slots,
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
    source_offset: str | None
    event_timezone: str | None
    warnings: tuple[str, ...]

    @classmethod
    def missing(
        cls,
        warnings: tuple[str, ...] = ("capture_time_missing",),
        *,
        event_timezone: str | None = None,
    ) -> CaptureMetadataResult:
        return cls(None, None, "not_applicable", None, None, event_timezone, warnings)

    def as_payload(self) -> dict[str, object]:
        return {
            "capture_time": self.capture_time,
            "source_field": self.source_field,
            "timezone_state": self.timezone_state,
            "source_value": self.source_value,
            "source_offset": self.source_offset,
            "event_timezone": self.event_timezone,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class FaceEmbeddingFace:
    index: int
    bbox: tuple[float, float, float, float]
    confidence: float
    landmarks: tuple[tuple[float, float], ...]
    embedding: tuple[float, ...] | None
    status: str = "kept"
    quality: FaceQualityEvidence | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        has_technical_error = (
            isinstance(self.error_code, str)
            and bool(self.error_code)
            and len(self.error_code) <= 64
        )
        legacy_kept = (
            self.status == "kept"
            and self.quality is None
            and self.embedding is not None
            and self.error_code is None
        )
        accepted_kept = (
            self.status == "kept"
            and self.quality is not None
            and self.quality.decision == "accepted"
            and self.embedding is not None
            and self.error_code is None
        )
        quality_rejected = (
            self.status == "quality_rejected"
            and self.quality is not None
            and self.quality.decision == "quality_rejected"
            and self.embedding is None
            and self.error_code is None
        )
        technical_failed = (
            self.status == "technical_failed"
            and self.embedding is None
            and has_technical_error
            and (self.quality is None or self.quality.decision == "accepted")
        )
        if not (legacy_kept or accepted_kept or quality_rejected or technical_failed):
            raise ValueError("invalid face embedding record")

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "index": self.index,
            "bbox": list(self.bbox),
            "confidence": self.confidence,
            "landmarks": [list(point) for point in self.landmarks],
        }
        if self.quality is None and self.status == "kept":
            assert self.embedding is not None
            payload["embedding"] = list(self.embedding)
            return payload
        payload["status"] = self.status
        if self.quality is not None:
            payload["quality"] = self.quality.as_payload()
        if self.embedding is not None:
            payload["embedding"] = list(self.embedding)
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        return payload


@dataclass(frozen=True)
class FaceEmbeddingResult:
    model: str
    faces: tuple[FaceEmbeddingFace, ...]
    has_single_query_face_usable: bool
    warnings: tuple[str, ...]
    timings: dict[str, int]
    input_geometry: dict[str, int | str] | None = None

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "face_count": len(self.faces),
            "faces": [face.as_payload() for face in self.faces],
            "has_single_query_face_usable": self.has_single_query_face_usable,
            "warnings": list(self.warnings),
            "timings": dict(self.timings),
        }
        if self.input_geometry is not None:
            payload["input_geometry"] = dict(self.input_geometry)
        return payload


@dataclass(frozen=True)
class SelfieEmbeddingResult:
    model: str
    embedding: tuple[float, ...]
    bbox: tuple[float, float, float, float]
    confidence: float
    landmarks: tuple[tuple[float, float], ...]
    timings: dict[str, int]

    def as_payload(self) -> dict[str, object]:
        return {
            "model": self.model,
            "embedding": list(self.embedding),
            "bbox": list(self.bbox),
            "confidence": self.confidence,
            "landmarks": [list(point) for point in self.landmarks],
            "timings": dict(self.timings),
        }


def _input_limits(value: object, *, content_types: set[str]) -> InputLimits:
    if (
        not isinstance(value, dict)
        or set(value) != {"max_bytes", "content_type"}
        or not _positive_int(value["max_bytes"])
        or value["content_type"] not in content_types
    ):
        raise ContractError("invalid input limits")
    return InputLimits(max_bytes=value["max_bytes"], content_type=value["content_type"])


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _valid_preview_input_geometry(value: object, fingerprint: InputFingerprint) -> bool:
    fields = {
        "coordinate_space",
        "pixel_width",
        "pixel_height",
        "oriented_source_width",
        "oriented_source_height",
    }
    return (
        isinstance(value, dict)
        and set(value) == fields
        and value["coordinate_space"] == "preview-small-v1"
        and value["pixel_width"] == fingerprint.pixel_width
        and value["pixel_height"] == fingerprint.pixel_height
        and _positive_int(value["oriented_source_width"])
        and _positive_int(value["oriented_source_height"])
    )


def _preview_key_matches_photo(fingerprint: InputFingerprint, photo_id: object) -> bool:
    if not isinstance(fingerprint.object_key, str) or not isinstance(photo_id, str):
        return False
    match = _PUBLISHED_PREVIEW_KEY.fullmatch(fingerprint.object_key)
    return match is not None and match.group("photo_id") == photo_id


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


def _is_sface_quality_configuration(value: object) -> bool:
    if not isinstance(value, dict) or "adaface" in value:
        return False
    face = value.get("face_embedding")
    return (
        isinstance(face, dict)
        and face.get("model") == SFACE_MODEL_NAME
        and face.get("embedding_dimensions") is None
    )


def _is_pinned_adaface_quality_configuration(value: object) -> bool:
    if (
        not isinstance(value, dict)
        or value.get("adaface") != _PINNED_ADAFACE_IDENTITY
        or value.get("scrfd") != _PINNED_SCRFD_IDENTITY
    ):
        return False
    face = value.get("face_embedding")
    return (
        isinstance(face, dict)
        and face.get("model") == ADAFACE_MODEL_NAME
        and face.get("embedding_dimensions") == MAX_FACE_EMBEDDING_DIMENSIONS
    )


def _valid_event_timezone(value: object) -> bool:
    if not _safe_string(value, maximum=255):
        return False
    try:
        ZoneInfo(cast(str, value))
    except (ValueError, ZoneInfoNotFoundError):
        return False
    return True


def _processor_version(processor_type: str, contract_version: int = CONTRACT_VERSION) -> int:
    if processor_type == PROCESSOR_TYPE:
        return PROCESSOR_VERSION
    if processor_type == PROCESSOR_TYPE_FACE_EMBEDDING:
        if contract_version == PREVIEW_CONTRACT_VERSION:
            return PROCESSOR_VERSION_FACE_EMBEDDING_PREVIEW
        if contract_version == 3:
            return PROCESSOR_VERSION_FACE_EMBEDDING_ADAFACE_QUALITY
        return PROCESSOR_VERSION_FACE_EMBEDDING
    if processor_type == PROCESSOR_TYPE_FACE_EMBEDDING_BENCHMARK:
        return 1
    if (
        processor_type == PROCESSOR_TYPE_GENERATE_PREVIEW
        and contract_version == PREVIEW_CONTRACT_VERSION
    ):
        return PROCESSOR_VERSION_GENERATE_PREVIEW
    if (
        processor_type == PROCESSOR_TYPE_GENERATE_WATERMARKED_PREVIEW
        and contract_version == PREVIEW_CONTRACT_VERSION
    ):
        return PROCESSOR_VERSION_GENERATE_WATERMARKED_PREVIEW
    if processor_type == PROCESSOR_TYPE_SELFIE_QUERY:
        return PROCESSOR_VERSION_SELFIE_QUERY
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
    if not isinstance(value, str) or len(value) > 8_192 or re.search(r"\s", value):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        return False
    if parsed.scheme == "https" and value.startswith("https://"):
        return True
    local_minio = (
        os.environ.get("PHOTO_WORKER_ALLOW_INSECURE_LOCAL_MINIO") == "true"
        and parsed.scheme == "http"
        and value.startswith("http://")
        and parsed.netloc == "minio:9000"
        and parsed.hostname == "minio"
        and port == 9000
        and bool(parsed.path.strip("/"))
    )
    if not local_minio:
        return False
    query = parse_qsl(parsed.query, keep_blank_values=True)
    signatures = [query_value for key, query_value in query if key == "X-Amz-Signature"]
    literal_signature_fields = [
        field for field in parsed.query.split("&") if field.partition("=")[0] == "X-Amz-Signature"
    ]
    return (
        len(signatures) == 1 and bool(signatures[0].strip()) and len(literal_signature_fields) == 1
    )
