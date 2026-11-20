"""Versioned person appearance job and result contract."""

from __future__ import annotations

import math
from typing import Any

PERSON_APPEARANCE_PROCESSOR = "person_appearance"
APPEARANCE_PROFILE = {
    "input_variant": "preview-small-v1",
    "detector_sha256": "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063",
    "encoder_sha256": "70c3a02efef82911fad69997894e978eea1a842792f9e2182ad36cb0cb1f01e6",
    "encoder_xml_sha256": "d9669dabfa73717a78da9e3b78374571e346cf1c9edf6cb5ff01ed296c878c30",
    "head_detector_sha256": "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
    "max_detections": 32,
    "max_descriptors": 8,
    "feature_version": 1,
    "focus_target_height": 192,
    "focus_min_height": 192,
    "focus_min_width": 48,
    "focus_min_luminance_std": 8,
    "focus_min_laplacian_energy": 400,
    "head_detection_threshold": 0.50,
}

APPEARANCE_CONFIGURATION: dict[str, object] = {
    "retry_policy": {
        "max_attempts": 3,
        "base_backoff_seconds": 30,
        "max_backoff_seconds": 300,
        "jitter_seconds": 5,
        "lease_max_seconds": 300,
    },
    "max_cohort_size": 16,
    "report_max_bytes": 262144,
    "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
    "person_appearance": APPEARANCE_PROFILE,
    "worker": {
        "api_response_max_bytes": 384 * 1024,
        "concurrency": 1,
        "heartbeat_interval_seconds": 30,
        "lease_duration_seconds": 120,
        "max_input_bytes": 10_485_760,
        "max_pixels": 2_560_000,
        "poll_min_delay_seconds": 5,
        "terminal_result_max_bytes": 384 * 1024,
    },
}
_VECTOR_DIMS = {"reid": 256, "color": 384, "helmet": 1764}
_ROW_KEYS = {"bbox", "confidence", "reason", "focus", "head"}
_REJECTION_REASONS = {"lack_details", "strong_blur", "mixed_sharpness", "descriptor_limit"}


def _numeric(value: object, low: float, high: float) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and low <= value <= high


def _box(value: object, width: int, height: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(
            _numeric(a, 0, limit)
            for a, limit in zip(value, (width, height, width, height), strict=True)
        )
        and value[0] < value[2]
        and value[1] < value[3]
    )


def _unit_vector(value: object, dimensions: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == dimensions
        and all(_numeric(coordinate, -1, 1) for coordinate in value)
        and math.isclose(sum(coordinate**2 for coordinate in value), 1, abs_tol=0.002)
    )


def valid_appearance_result(value: object, *, width: int = 1600, height: int = 1600) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "people",
        "head_count",
        "detections_truncated",
        "descriptors_truncated",
    }:
        return False
    rows = value["people"]
    if (
        not isinstance(rows, list)
        or len(rows) > 32
        or type(value["head_count"]) is not int
        or not 0 <= value["head_count"] <= 8400
        or type(value["detections_truncated"]) is not bool
        or (value["detections_truncated"] and len(rows) != 32)
        or type(value["descriptors_truncated"]) is not bool
    ):
        return False
    descriptors = head_count = 0
    last = 1.0
    limited = False
    for row in rows:
        if not isinstance(row, dict) or not _ROW_KEYS <= row.keys():
            return False
        reason = row["reason"]
        if not isinstance(reason, str) or (reason and reason not in _REJECTION_REASONS):
            return False
        if (
            not _box(row["bbox"], width, height)
            or not _numeric(row["confidence"], 0.35, last)
            or not isinstance(row["focus"], list)
            or len(row["focus"]) != 4
            or not all(_numeric(item, 0, 1040400) for item in row["focus"])
        ):
            return False
        if row["head"] is not None:
            if not _box(row["head"], width, height):
                return False
            head_count += 1
        last = row["confidence"]
        if reason:
            if set(row) != _ROW_KEYS:
                return False
            limited |= reason == "descriptor_limit"
            continue
        if set(row) != _ROW_KEYS | _VECTOR_DIMS.keys():
            return False
        for key, dimensions in _VECTOR_DIMS.items():
            if key == "helmet" and row[key] is None:
                continue
            if not _unit_vector(row[key], dimensions):
                return False
        if row["helmet"] is not None and row["head"] is None:
            return False
        descriptors += 1
    return (
        descriptors <= 8
        and head_count <= value["head_count"]
        and value["descriptors_truncated"] == limited
        and (not limited or descriptors == 8)
    )


def appearance_metadata(result: dict[str, Any]) -> dict[str, Any]:
    return result | {"people": [{key: row[key] for key in _ROW_KEYS} for row in result["people"]]}
