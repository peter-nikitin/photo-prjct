#!/usr/bin/env python3
"""Aggregate bounded selfie-search JSON events for one Moscow calendar day."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

EVENT_NAMES = {
    "selfie_submission_finished",
    "selfie_worker_attempt_finished",
    "selfie_ranking_finished",
    "selfie_search_terminal",
}
MAX_BOUNDED_INTEGER = 2**31 - 1
ENVIRONMENTS = {"local", "test", "staging", "production"}
COMMON_FIELDS = {"schema_version", "event", "occurred_at", "service", "environment"}
SUBMISSION_OUTCOMES = ("accepted", "rejected", "storage_unavailable")
REJECTION_REASONS = (
    "missing_or_empty",
    "unsupported_format",
    "corrupt_image",
    "source_too_large",
    "normalized_too_large",
    "pixel_limit_exceeded",
    "storage_unavailable",
)
ACTUAL_FORMATS = ("jpeg", "png", "heic", "heif", "unknown")
DECLARED_TYPES = ("jpeg", "png", "heic", "heif", "octet_stream", "missing", "other")
SIZE_BUCKETS = ("empty", "le_1mib", "le_5mib", "le_10mib", "le_20mib", "gt_20mib")
TERMINAL_STATUSES = (
    "ready",
    "no_face",
    "multiple_faces",
    "quality_rejected",
    "search_unavailable",
    "failed",
)
TERMINAL_FAILURE_BY_STATUS = {
    "ready": "",
    "no_face": "no_face",
    "multiple_faces": "multiple_faces",
    "quality_rejected": "quality_rejected",
    "search_unavailable": "",
    "failed": "failed",
}
WORKER_ERROR_CODES = {
    "decode_failed",
    "download_authorization_expired",
    "fingerprint_mismatch",
    "input_too_large",
    "model_inference_error",
    "model_inference_timeout",
    "network_interruption",
    "no_face_detected",
    "multiple_faces_detected",
    "quality_rejected",
    "storage_unavailable",
    "unsupported_input",
}
WORKER_RETRYABLE_CODES = {
    "download_authorization_expired",
    "model_inference_timeout",
    "network_interruption",
    "storage_unavailable",
}
EVENT_FIELDS = {
    "selfie_submission_finished": COMMON_FIELDS
    | {
        "event_id",
        "outcome",
        "reason_code",
        "search_id",
        "actual_format",
        "declared_type",
        "source_size_bucket",
        "duration_ms",
    },
    "selfie_worker_attempt_finished": COMMON_FIELDS
    | {
        "event_id",
        "search_id",
        "job_id",
        "attempt_id",
        "outcome",
        "reason_code",
        "retryable",
        "download_ms",
        "compute_ms",
        "total_ms",
    },
    "selfie_ranking_finished": COMMON_FIELDS
    | {
        "event_id",
        "search_id",
        "attempt_id",
        "outcome",
        "eligible_photo_count",
        "eligible_face_count",
        "matched_photo_count",
        "load_ms",
        "rank_ms",
        "configuration_hash",
    },
    "selfie_search_terminal": COMMON_FIELDS
    | {
        "event_id",
        "search_id",
        "status",
        "matched_photo_count",
        "attempt_count",
        "elapsed_ms",
        "failure_code",
        "cleanup_confirmed",
    },
}
HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
INTEGER_PATTERN = re.compile(r"^[1-9][0-9]{0,9}$")
DURATION_NAMES = (
    "submission",
    "worker_download",
    "worker_compute",
    "worker_total",
    "cohort_load",
    "ranking",
    "search_lifetime",
)


@dataclass(frozen=True)
class DailySummary:
    schema_version: int
    event: str
    generated_at: str
    recomputed: bool
    report_date: str
    window_start: str
    window_end: str
    submissions: dict[str, Any]
    terminals: dict[str, Any]
    worker_attempts: dict[str, Any]
    durations_ms: dict[str, dict[str, int | None]]
    cohort: dict[str, int | None]
    integrity: dict[str, int]
    complete: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_jsonl(
    lines: Iterable[str], *, report_date: date, timezone_name: str = "Europe/Moscow"
) -> DailySummary:
    timezone = ZoneInfo(timezone_name)
    window_start = datetime.combine(report_date, time.min, tzinfo=timezone)
    window_end = window_start + timedelta(days=1)
    state = _State()
    for raw_line in lines:
        _consume_line(raw_line, state=state, window_start=window_start, window_end=window_end)
    return state.summary(report_date=report_date, window_start=window_start, window_end=window_end)


class _State:
    def __init__(self) -> None:
        self.submissions: dict[str, Any] = {
            "total": 0,
            "accepted": 0,
            "outcomes": _zeroes(SUBMISSION_OUTCOMES),
            "rejection_reasons": _zeroes(REJECTION_REASONS),
            "actual_formats": _zeroes(ACTUAL_FORMATS),
            "declared_types": _zeroes(DECLARED_TYPES),
            "source_size_buckets": _zeroes(SIZE_BUCKETS),
        }
        self.terminals: dict[str, Any] = {
            "total": 0,
            "statuses": _zeroes(TERMINAL_STATUSES),
            "ready_zero": 0,
            "ready_positive": 0,
        }
        self.worker_attempts: dict[str, Any] = {
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "retryable_failed": 0,
            "failure_reasons": {},
        }
        self.duration_samples: dict[str, list[int]] = {name: [] for name in DURATION_NAMES}
        self.eligible_photos: list[int] = []
        self.eligible_faces: list[int] = []
        self.accepted_ids: set[str] = set()
        self.terminal_ids: set[str] = set()
        self.logical_events: set[tuple[str, str]] = set()
        self.integrity = {
            "accepted_without_terminal": 0,
            "terminal_without_accepted": 0,
            "duplicate_logical_events": 0,
            "malformed_events": 0,
            "unknown_schema_or_event": 0,
            "late_events": 0,
        }

    def summary(
        self, *, report_date: date, window_start: datetime, window_end: datetime
    ) -> DailySummary:
        self.integrity["accepted_without_terminal"] = len(self.accepted_ids - self.terminal_ids)
        self.integrity["terminal_without_accepted"] = len(self.terminal_ids - self.accepted_ids)
        durations = {name: _percentiles(values) for name, values in self.duration_samples.items()}
        cohort = {
            "eligible_photo_min": min(self.eligible_photos) if self.eligible_photos else None,
            "eligible_photo_max": max(self.eligible_photos) if self.eligible_photos else None,
            "eligible_face_min": min(self.eligible_faces) if self.eligible_faces else None,
            "eligible_face_max": max(self.eligible_faces) if self.eligible_faces else None,
        }
        return DailySummary(
            schema_version=1,
            event="selfie_search_daily_summary",
            generated_at=_utc_now(),
            recomputed=False,
            report_date=report_date.isoformat(),
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
            submissions=self.submissions,
            terminals=self.terminals,
            worker_attempts=self.worker_attempts,
            durations_ms=durations,
            cohort=cohort,
            integrity=self.integrity,
            complete=not any(self.integrity.values()),
        )


def _consume_line(
    raw_line: str, *, state: _State, window_start: datetime, window_end: datetime
) -> None:
    line = raw_line.strip()
    if not line:
        return
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        if "selfie_" in line or "schema_version" in line:
            state.integrity["malformed_events"] += 1
        return
    if not isinstance(value, dict):
        return
    event = value.get("event")
    claims_selfie = isinstance(event, str) and event.startswith("selfie_")
    if not claims_selfie:
        return
    if value.get("schema_version") != 1 or event not in EVENT_NAMES:
        state.integrity["unknown_schema_or_event"] += 1
        return
    try:
        occurred_at = _timestamp(value.get("occurred_at"))
        _validate_envelope(value)
        _validate_event(value)
    except (TypeError, ValueError):
        state.integrity["malformed_events"] += 1
        return
    if not window_start <= occurred_at.astimezone(window_start.tzinfo) < window_end:
        state.integrity["late_events"] += 1
        return
    logical_key = _logical_key(value)
    if logical_key is not None:
        if logical_key in state.logical_events:
            state.integrity["duplicate_logical_events"] += 1
            return
        state.logical_events.add(logical_key)
    if event == "selfie_submission_finished":
        _submission_event(value, state)
    elif event == "selfie_worker_attempt_finished":
        _worker_event(value, state)
    elif event == "selfie_ranking_finished":
        _ranking_event(value, state)
    else:
        _terminal_event(value, state)


def _validate_envelope(value: dict[str, Any]) -> None:
    event = _required_string(value, "event")
    if set(value) != EVENT_FIELDS[event]:
        raise ValueError("event fields do not match the contract")
    _required_string(value, "occurred_at")
    expected_service = "worker" if event == "selfie_worker_attempt_finished" else "web"
    if value.get("service") != expected_service:
        raise ValueError("invalid service")
    _choice(value, "environment", tuple(ENVIRONMENTS))


def _validate_event(value: dict[str, Any]) -> None:
    event = value["event"]
    if event == "selfie_submission_finished":
        _opaque_id(value.get("event_id"))
        outcome = _choice(value, "outcome", SUBMISSION_OUTCOMES)
        _choice(value, "actual_format", ACTUAL_FORMATS)
        _choice(value, "declared_type", DECLARED_TYPES)
        _choice(value, "source_size_bucket", SIZE_BUCKETS)
        _duration(value, "duration_ms")
        reason = value.get("reason_code")
        search_id = value.get("search_id")
        if outcome == "accepted":
            if reason != "" or not _uuid_id(search_id):
                raise ValueError("invalid accepted submission")
        elif outcome == "rejected" and (
            reason not in set(REJECTION_REASONS) - {"storage_unavailable"} or search_id is not None
        ):
            raise ValueError("invalid rejected submission")
        elif outcome == "storage_unavailable" and (
            reason != "storage_unavailable" or search_id is not None
        ):
            raise ValueError("invalid storage failure")
    elif event == "selfie_worker_attempt_finished":
        _opaque_id(value.get("event_id"))
        for field in ("search_id", "job_id", "attempt_id"):
            if not _uuid_id(value.get(field)):
                raise ValueError(f"invalid {field}")
        outcome = _choice(value, "outcome", ("succeeded", "failed"))
        retryable = value.get("retryable")
        if not isinstance(retryable, bool):
            raise ValueError("invalid retryable")
        reason = value.get("reason_code")
        if not isinstance(reason, str):
            raise ValueError("invalid worker reason")
        if outcome == "succeeded" and (reason != "" or retryable):
            raise ValueError("invalid successful worker disposition")
        if outcome == "failed" and (
            reason not in WORKER_ERROR_CODES or retryable != (reason in WORKER_RETRYABLE_CODES)
        ):
            raise ValueError("invalid failed worker disposition")
        for field in ("download_ms", "compute_ms", "total_ms"):
            _duration(value, field, nullable=True)
    elif event == "selfie_ranking_finished":
        _opaque_id(value.get("event_id"))
        for field in ("search_id", "attempt_id"):
            if not _uuid_id(value.get(field)):
                raise ValueError(f"invalid {field}")
        _choice(value, "outcome", ("succeeded", "incompatible"))
        for field in ("eligible_photo_count", "eligible_face_count", "matched_photo_count"):
            _duration(value, field)
        _duration(value, "load_ms", nullable=True)
        _duration(value, "rank_ms", nullable=True)
        configuration_hash = value.get("configuration_hash")
        if not isinstance(configuration_hash, str) or not HASH_PATTERN.fullmatch(
            configuration_hash
        ):
            raise ValueError("invalid configuration hash")
    else:
        _opaque_id(value.get("event_id"))
        if not _uuid_id(value.get("search_id")):
            raise ValueError("invalid search_id")
        status = _choice(value, "status", TERMINAL_STATUSES)
        _duration(value, "matched_photo_count")
        _duration(value, "attempt_count")
        _duration(value, "elapsed_ms")
        if (
            value.get("cleanup_confirmed") is not True
            or value.get("failure_code") != TERMINAL_FAILURE_BY_STATUS[status]
        ):
            raise ValueError("invalid terminal event")


def _logical_key(value: dict[str, Any]) -> tuple[str, str] | None:
    event = value["event"]
    if event == "selfie_submission_finished":
        identity = value.get("search_id")
    elif event in {"selfie_worker_attempt_finished", "selfie_ranking_finished"}:
        identity = value.get("attempt_id")
    else:
        identity = value.get("search_id")
    return (event, identity) if isinstance(identity, str) and identity else None


def _submission_event(value: dict[str, Any], state: _State) -> None:
    outcome = value["outcome"]
    state.submissions["total"] += 1
    state.submissions["outcomes"][outcome] += 1
    state.submissions["actual_formats"][value["actual_format"]] += 1
    state.submissions["declared_types"][value["declared_type"]] += 1
    state.submissions["source_size_buckets"][value["source_size_bucket"]] += 1
    state.duration_samples["submission"].append(value["duration_ms"])
    if outcome == "accepted":
        state.submissions["accepted"] += 1
        state.accepted_ids.add(value["search_id"])
    else:
        state.submissions["rejection_reasons"][value["reason_code"]] += 1


def _worker_event(value: dict[str, Any], state: _State) -> None:
    outcome = value["outcome"]
    state.worker_attempts["total"] += 1
    state.worker_attempts[outcome] += 1
    if outcome == "failed":
        reason = value["reason_code"]
        reasons = state.worker_attempts["failure_reasons"]
        reasons[reason] = reasons.get(reason, 0) + 1
        if value["retryable"]:
            state.worker_attempts["retryable_failed"] += 1
    _append_duration(state, "worker_download", value["download_ms"])
    _append_duration(state, "worker_compute", value["compute_ms"])
    _append_duration(state, "worker_total", value["total_ms"])


def _ranking_event(value: dict[str, Any], state: _State) -> None:
    state.eligible_photos.append(value["eligible_photo_count"])
    state.eligible_faces.append(value["eligible_face_count"])
    _append_duration(state, "cohort_load", value["load_ms"])
    _append_duration(state, "ranking", value["rank_ms"])


def _terminal_event(value: dict[str, Any], state: _State) -> None:
    status = value["status"]
    matches = value["matched_photo_count"]
    state.terminals["total"] += 1
    state.terminals["statuses"][status] += 1
    if status == "ready":
        state.terminals["ready_positive" if matches else "ready_zero"] += 1
    state.terminal_ids.add(value["search_id"])
    state.duration_samples["search_lifetime"].append(value["elapsed_ms"])


def _append_duration(state: _State, name: str, value: int | None) -> None:
    if value is not None:
        state.duration_samples[name].append(value)


def _percentiles(values: list[int]) -> dict[str, int | None]:
    if not values:
        return {"count": 0, "p50": None, "p95": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "p50": ordered[math.ceil(0.50 * len(ordered)) - 1],
        "p95": ordered[math.ceil(0.95 * len(ordered)) - 1],
    }


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone aware")
    return parsed


def _required_string(value: dict[str, Any], field: str) -> str:
    candidate = value.get(field)
    if not isinstance(candidate, str) or not candidate:
        raise ValueError(f"invalid {field}")
    return candidate


def _choice(value: dict[str, Any], field: str, choices: tuple[str, ...]) -> str:
    candidate = value.get(field)
    if not isinstance(candidate, str) or candidate not in choices:
        raise ValueError(f"invalid {field}")
    return candidate


def _duration(value: dict[str, Any], field: str, *, nullable: bool = False) -> int | None:
    candidate = value.get(field)
    if nullable and candidate is None:
        return None
    if (
        not isinstance(candidate, int)
        or isinstance(candidate, bool)
        or candidate < 0
        or candidate > MAX_BOUNDED_INTEGER
    ):
        raise ValueError(f"invalid {field}")
    return candidate


def _uuid_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value.lower()
    except ValueError:
        return False


def _opaque_id(value: object) -> None:
    if _uuid_id(value):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        valid = 0 < value <= MAX_BOUNDED_INTEGER
    elif isinstance(value, str) and INTEGER_PATTERN.fullmatch(value):
        valid = 0 < int(value) <= MAX_BOUNDED_INTEGER
    else:
        valid = False
    if not valid:
        raise ValueError("invalid opaque id")


def _zeroes(names: tuple[str, ...]) -> dict[str, int]:
    return dict.fromkeys(names, 0)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--timezone", default="Europe/Moscow")
    parser.add_argument("--recomputed", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary = summarize_jsonl(sys.stdin, report_date=args.date, timezone_name=args.timezone)
    payload = summary.to_dict()
    payload["recomputed"] = args.recomputed
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
