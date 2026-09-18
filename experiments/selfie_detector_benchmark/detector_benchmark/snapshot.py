from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .artifacts import publish_immutable

_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png"}
_COHORTS = {"no_face", "successful_control", "multiple_faces", "ready_zero_visible_results"}
_STATUSES = {"no_face", "multiple_faces", "ready"}
_STATUS_FAILURE_CODES = {
    ("no_face", "no_face"): "no_face",
    ("multiple_faces", "multiple_faces"): "multiple_faces",
    ("successful_control", "ready"): "",
    ("ready_zero_visible_results", "ready"): "",
}
_CONFIG_HASH = re.compile(r"[0-9a-f]{64}\Z")
_CHECKSUM = re.compile(r"[0-9a-f]{64}\Z")
_RESULT_LABEL = re.compile(r"present:[0-9]+;absent:[0-9]+\Z")
_COUNT_KEYS = {"matched_photo_count", "visible_result_count"}
_DIAGNOSTIC_KEYS = {"configuration_hash", "eligible_face_count", "failure_code"}


@dataclass(frozen=True)
class SnapshotRecord:
    source_id: str
    cohort: str
    source_status: str
    source_counts: Mapping[str, int]
    diagnostics: Mapping[str, object]
    result_label: str | None
    direct_cosine_distance: float | None
    media_type: str
    content: bytes
    sha256: str


def export_snapshot(
    records: Iterable[SnapshotRecord], output: Path, *, expected_count: int = 40
) -> None:
    values = tuple(sorted(records, key=lambda record: record.source_id))
    if len(values) != expected_count:
        raise ValueError(f"expected {expected_count} records")
    if len({record.source_id for record in values}) != len(values):
        raise ValueError("duplicate source IDs")
    payloads = [_record_payload(record, index + 1) for index, record in enumerate(values)]

    def write(stage: Path) -> None:
        objects = stage / "objects"
        objects.mkdir()
        for record, payload in zip(values, payloads, strict=True):
            (objects / str(payload["object_name"])).write_bytes(record.content)
        _write_json(
            stage / "manifest.json",
            {
                "schema_version": SCHEMA_VERSION,
                "artifact_type": "detector-snapshot",
                "record_count": len(payloads),
                "records": payloads,
            },
        )

    publish_immutable(output, write)


def load_snapshot(path: Path, *, expected_count: int | None = None) -> tuple[dict[str, Any], ...]:
    manifest = _load_json(path / "manifest.json")
    if (
        set(manifest) != {"schema_version", "artifact_type", "record_count", "records"}
        or manifest["schema_version"] != SCHEMA_VERSION
        or manifest["artifact_type"] != "detector-snapshot"
        or not isinstance(manifest["records"], list)
        or manifest["record_count"] != len(manifest["records"])
        or (expected_count is not None and manifest["record_count"] != expected_count)
    ):
        raise ValueError("snapshot manifest is invalid")
    return tuple(
        _validate_manifest_record(path, record, number + 1)
        for number, record in enumerate(manifest["records"])
    )


def snapshot_manifest_sha256(path: Path) -> str:
    return hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest()


def _record_payload(record: SnapshotRecord, number: int) -> dict[str, object]:
    if (
        not isinstance(record.source_id, str)
        or not record.source_id
        or not isinstance(record.content, bytes)
    ):
        raise ValueError("snapshot record is invalid")
    extension = _EXTENSIONS.get(record.media_type)
    checksum = hashlib.sha256(record.content).hexdigest()
    payload = {
        "case_id": f"case-{number:03d}",
        "cohort": record.cohort,
        "source_status": record.source_status,
        "source_counts": dict(record.source_counts),
        "diagnostics": dict(record.diagnostics),
        "result_label": record.result_label,
        "direct_cosine_distance": record.direct_cosine_distance,
        "media_type": record.media_type,
        "byte_size": len(record.content),
        "sha256": checksum,
        "object_name": f"case-{number:03d}{extension}" if extension else "",
    }
    if checksum != record.sha256 or not _valid_record_payload(payload, number):
        raise ValueError("snapshot record is invalid or checksum mismatch")
    return payload


def _validate_manifest_record(path: Path, raw: object, number: int) -> dict[str, Any]:
    if not isinstance(raw, dict) or not _valid_record_payload(raw, number):
        raise ValueError("snapshot record is invalid")
    content = path / "objects" / str(raw["object_name"])
    if not content.is_file() or content.stat().st_size != raw["byte_size"]:
        raise ValueError("snapshot object is missing")
    if hashlib.sha256(content.read_bytes()).hexdigest() != raw["sha256"]:
        raise ValueError("snapshot object checksum mismatch")
    return raw


def _valid_record_payload(raw: Mapping[str, object], number: int) -> bool:
    required = {
        "case_id",
        "cohort",
        "source_status",
        "source_counts",
        "diagnostics",
        "result_label",
        "direct_cosine_distance",
        "media_type",
        "byte_size",
        "sha256",
        "object_name",
    }
    if set(raw) != required or raw["case_id"] != f"case-{number:03d}":
        return False
    media_type = raw["media_type"]
    if (
        media_type not in _EXTENSIONS
        or raw["object_name"] != f"case-{number:03d}{_EXTENSIONS[media_type]}"
    ):
        return False
    if raw["cohort"] not in _COHORTS or raw["source_status"] not in _STATUSES:
        return False
    if not _counts_valid(raw["source_counts"]) or not _diagnostics_valid(
        raw["diagnostics"], raw["cohort"], raw["source_status"]
    ):
        return False
    label = raw["result_label"]
    if label is not None and (not isinstance(label, str) or not _RESULT_LABEL.fullmatch(label)):
        return False
    distance = raw["direct_cosine_distance"]
    if distance is not None and (
        not isinstance(distance, (int, float))
        or isinstance(distance, bool)
        or not math.isfinite(distance)
        or not 0 <= distance <= 2
    ):
        return False
    return (
        isinstance(raw["byte_size"], int)
        and not isinstance(raw["byte_size"], bool)
        and raw["byte_size"] > 0
        and isinstance(raw["sha256"], str)
        and _CHECKSUM.fullmatch(raw["sha256"]) is not None
    )


def _counts_valid(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == _COUNT_KEYS
        and all(
            isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 1_000_000
            for count in value.values()
        )
    )


def _diagnostics_valid(value: object, cohort: object, status: object) -> bool:
    pair = (cohort, status)
    return (
        isinstance(value, Mapping)
        and set(value) == _DIAGNOSTIC_KEYS
        and isinstance(value["configuration_hash"], str)
        and _CONFIG_HASH.fullmatch(value["configuration_hash"]) is not None
        and isinstance(value["eligible_face_count"], int)
        and not isinstance(value["eligible_face_count"], bool)
        and 0 <= value["eligible_face_count"] <= 1_000_000
        and pair in _STATUS_FAILURE_CODES
        and isinstance(value["failure_code"], str)
        and _STATUS_FAILURE_CODES[pair] == value["failure_code"]
    )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("snapshot manifest cannot be read") from error
    if not isinstance(payload, dict):
        raise ValueError("snapshot manifest is invalid")
    return payload


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
