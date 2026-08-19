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
EVENT_SCHEMA_VERSIONS = {
    "selfie_submission_finished": frozenset({1}),
    "selfie_worker_attempt_finished": frozenset({1}),
    "selfie_ranking_finished": frozenset({1, 2}),
    "selfie_search_terminal": frozenset({1, 2}),
}
PROBE_EVENT = "selfie_observability_probe"
OBSERVABILITY_FAILURE_MARKER = "selfie_observability_emit_failed"
LOG_LEVEL_PREFIXES = ("DEBUG ", "INFO ", "WARNING ", "ERROR ", "CRITICAL ")
MAX_BOUNDED_INTEGER = 2**31 - 1
COMMON_FIELDS = {"schema_version", "event", "occurred_at", "service"}
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
_SUBMISSION_FIELDS = {
    "event_id",
    "outcome",
    "reason_code",
    "search_id",
    "actual_format",
    "declared_type",
    "source_size_bucket",
    "duration_ms",
}
_WORKER_FIELDS = {
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
}
_RANKING_FIELDS_V1 = {
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
}
_RANKING_FIELDS_V2 = _RANKING_FIELDS_V1 | {
    "direct_matched_photo_count",
    "cluster_expanded_photo_count",
    "final_matched_photo_count",
    "strong_anchor_count",
    "expanded_cluster_count",
    "cluster_corpus_version",
    "cluster_configuration_hash",
    "cluster_expansion_ms",
    "cluster_expansion_outcome",
}
_TERMINAL_FIELDS_V1 = {
    "event_id",
    "search_id",
    "status",
    "matched_photo_count",
    "attempt_count",
    "elapsed_ms",
    "failure_code",
    "cleanup_confirmed",
}
_TERMINAL_FIELDS_V2 = _TERMINAL_FIELDS_V1 | {
    "direct_matched_photo_count",
    "cluster_expanded_photo_count",
    "cluster_corpus_version",
    "cluster_configuration_hash",
}
EVENT_FIELDS_BY_VERSION = {
    "selfie_submission_finished": {1: COMMON_FIELDS | _SUBMISSION_FIELDS},
    "selfie_worker_attempt_finished": {1: COMMON_FIELDS | _WORKER_FIELDS},
    "selfie_ranking_finished": {
        1: COMMON_FIELDS | _RANKING_FIELDS_V1,
        2: COMMON_FIELDS | _RANKING_FIELDS_V2,
    },
    "selfie_search_terminal": {
        1: COMMON_FIELDS | _TERMINAL_FIELDS_V1,
        2: COMMON_FIELDS | _TERMINAL_FIELDS_V2,
    },
}
# Kept as a descriptive alias for operators/tests that inspect the parser contract directly.
EVENT_FIELDS = EVENT_FIELDS_BY_VERSION
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
EXPANSION_OUTCOMES = (
    "expanded",
    "no_strong_anchor",
    "no_new_photos",
    "corpus_unavailable",
    "corpus_incompatible",
    "disabled",
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
    expansion: dict[str, Any]
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
        self.expansion_rankings: dict[str, dict[str, Any]] = {}
        self.expansion_terminals: dict[str, dict[str, Any]] = {}
        self.v2_ranking_count = 0
        self.v2_terminal_count = 0
        self.historical_v1_ranking_count = 0
        self.historical_v1_terminal_count = 0
        self.expansion_durations: list[int] = []
        self.expansion_outcomes = _zeroes(EXPANSION_OUTCOMES)
        self.expansion_corpus_versions: dict[str, int] = {}
        self.expansion_configuration_hashes: dict[str, int] = {}
        self.expansion_integrity_seen = False
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
            "ranking_without_terminal": 0,
            "terminal_without_ranking": 0,
            "ranking_terminal_mismatches": 0,
        }

    def summary(
        self, *, report_date: date, window_start: datetime, window_end: datetime
    ) -> DailySummary:
        self.integrity["accepted_without_terminal"] = len(self.accepted_ids - self.terminal_ids)
        self.integrity["terminal_without_accepted"] = len(self.terminal_ids - self.accepted_ids)
        self._reconcile_expansion()
        durations = {name: _percentiles(values) for name, values in self.duration_samples.items()}
        cohort = {
            "eligible_photo_min": min(self.eligible_photos) if self.eligible_photos else None,
            "eligible_photo_max": max(self.eligible_photos) if self.eligible_photos else None,
            "eligible_face_min": min(self.eligible_faces) if self.eligible_faces else None,
            "eligible_face_max": max(self.eligible_faces) if self.eligible_faces else None,
        }
        expansion = self._expansion_summary()
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
            expansion=expansion,
            durations_ms=durations,
            cohort=cohort,
            integrity=self.integrity,
            complete=not any(self.integrity.values()),
        )

    def _reconcile_expansion(self) -> None:
        if not self.expansion_integrity_seen:
            return
        ranking_without_terminal = sum(
            search_id not in self.expansion_terminals for search_id in self.expansion_rankings
        )
        terminal_without_ranking = sum(
            search_id not in self.expansion_rankings
            and (
                terminal["status"] == "ready"
                or terminal["matched_photo_count"]
                or terminal["direct_matched_photo_count"]
                or terminal["cluster_expanded_photo_count"]
            )
            for search_id, terminal in self.expansion_terminals.items()
        )
        mismatches = 0
        for search_id, ranking in self.expansion_rankings.items():
            terminal = self.expansion_terminals.get(search_id)
            if terminal is None:
                continue
            if (
                terminal["event_id"] != ranking["event_id"]
                or terminal["matched_photo_count"] != ranking["final_matched_photo_count"]
                or terminal["direct_matched_photo_count"] != ranking["direct_matched_photo_count"]
                or terminal["cluster_expanded_photo_count"]
                != ranking["cluster_expanded_photo_count"]
                or terminal["cluster_corpus_version"] != ranking["cluster_corpus_version"]
                or terminal["cluster_configuration_hash"] != ranking["cluster_configuration_hash"]
            ):
                mismatches += 1
        self.integrity["ranking_without_terminal"] = ranking_without_terminal
        self.integrity["terminal_without_ranking"] = terminal_without_ranking
        self.integrity["ranking_terminal_mismatches"] = mismatches

    def _expansion_summary(self) -> dict[str, Any]:
        if self.v2_ranking_count == 0:
            if self.historical_v1_ranking_count or self.historical_v1_terminal_count:
                return _not_available_expansion()
            return {
                "eligible_searches": 0,
                "searches_with_cluster_photos": 0,
                "direct_matched_photo_count": 0,
                "cluster_expanded_photo_count": 0,
                "final_matched_photo_count": 0,
                "strong_anchor_count": 0,
                "expanded_cluster_count": 0,
                "added_photos": _percentiles([]),
                "expansion_ms": _percentiles([]),
                "outcomes": _zeroes(EXPANSION_OUTCOMES),
                "corpus_versions": {},
                "configuration_hashes": {},
                "searches_helped_rate": _rate(0, 0),
                "incremental_photo_rate": _rate(0, 0),
            }
        eligible = [
            ranking
            for ranking in self.expansion_rankings.values()
            if ranking["ranking_outcome"] == "succeeded"
            and ranking["outcome"] in {"expanded", "no_strong_anchor", "no_new_photos"}
            and ranking["cluster_corpus_version"] is not None
            and ranking["cluster_configuration_hash"] is not None
        ]
        direct_total = sum(row["direct_matched_photo_count"] for row in eligible)
        expanded_total = sum(row["cluster_expanded_photo_count"] for row in eligible)
        final_total = sum(row["final_matched_photo_count"] for row in eligible)
        strong_anchor_total = sum(row["strong_anchor_count"] for row in eligible)
        cluster_total = sum(row["expanded_cluster_count"] for row in eligible)
        helped = sum(row["cluster_expanded_photo_count"] > 0 for row in eligible)
        return {
            "eligible_searches": len(eligible),
            "searches_with_cluster_photos": helped,
            "direct_matched_photo_count": direct_total,
            "cluster_expanded_photo_count": expanded_total,
            "final_matched_photo_count": final_total,
            "strong_anchor_count": strong_anchor_total,
            "expanded_cluster_count": cluster_total,
            "added_photos": _percentiles([row["cluster_expanded_photo_count"] for row in eligible]),
            "expansion_ms": _percentiles(self.expansion_durations),
            "outcomes": dict(self.expansion_outcomes),
            "corpus_versions": dict(self.expansion_corpus_versions),
            "configuration_hashes": dict(self.expansion_configuration_hashes),
            "searches_helped_rate": _rate(helped, len(eligible)),
            "incremental_photo_rate": _rate(expanded_total, final_total),
        }


def _consume_line(
    raw_line: str, *, state: _State, window_start: datetime, window_end: datetime
) -> None:
    line = raw_line.strip()
    if not line:
        return
    for prefix in LOG_LEVEL_PREFIXES:
        if line.startswith(prefix):
            line = line[len(prefix) :]
            break
    if line == OBSERVABILITY_FAILURE_MARKER:
        state.integrity["malformed_events"] += 1
        return
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        if line.startswith("{") and ("selfie_" in line or "schema_version" in line):
            state.integrity["malformed_events"] += 1
        return
    if not isinstance(value, dict):
        return
    event = value.get("event")
    claims_selfie = isinstance(event, str) and event.startswith("selfie_")
    if not claims_selfie:
        return
    if event == PROBE_EVENT:
        expected = COMMON_FIELDS | {"probe_id"}
        if (
            value.get("schema_version") == 1
            and set(value) == expected
            and value.get("service") == "web"
        ):
            try:
                _timestamp(value.get("occurred_at"))
                UUID(str(value["probe_id"]))
            except (TypeError, ValueError):
                pass
            else:
                return None
    schema_version = value.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or event not in EVENT_SCHEMA_VERSIONS
        or schema_version not in EVENT_SCHEMA_VERSIONS[event]
    ):
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
    schema_version = value.get("schema_version")
    expected_by_version = EVENT_FIELDS_BY_VERSION[event]
    if (
        schema_version not in expected_by_version
        or set(value) != expected_by_version[schema_version]
    ):
        raise ValueError("event fields do not match the contract")
    _required_string(value, "occurred_at")
    expected_service = "worker" if event == "selfie_worker_attempt_finished" else "web"
    if value.get("service") != expected_service:
        raise ValueError("invalid service")


def _validate_event(value: dict[str, Any]) -> None:
    event = value["event"]
    schema_version = value["schema_version"]
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
        if schema_version == 2:
            _validate_ranking_v2(value)
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
        if schema_version == 2:
            _validate_terminal_v2(value)


def _validate_ranking_v2(value: dict[str, Any]) -> None:
    direct = _duration(value, "direct_matched_photo_count")
    expanded = _duration(value, "cluster_expanded_photo_count")
    final = _duration(value, "final_matched_photo_count")
    strong_anchors = _duration(value, "strong_anchor_count")
    expanded_clusters = _duration(value, "expanded_cluster_count")
    assert direct is not None and expanded is not None and final is not None
    assert strong_anchors is not None and expanded_clusters is not None
    if value["outcome"] == "incompatible":
        if value["matched_photo_count"] != 0 or any(
            (direct, expanded, final, strong_anchors, expanded_clusters)
        ):
            raise ValueError("incompatible ranking has source counts")
    elif final != direct + expanded or value["matched_photo_count"] != final:
        raise ValueError("ranking count identity mismatch")
    outcome = _choice(value, "cluster_expansion_outcome", EXPANSION_OUTCOMES)
    if expanded > 0 and outcome != "expanded":
        raise ValueError("expanded count has an invalid outcome")
    if outcome == "expanded" and (expanded <= 0 or expanded_clusters <= 0):
        raise ValueError("expanded outcome has no added photos or selected cluster")
    if outcome == "no_strong_anchor" and (strong_anchors != 0 or expanded_clusters != 0):
        raise ValueError("no-strong-anchor outcome has anchors")
    if outcome == "no_new_photos" and strong_anchors <= 0:
        raise ValueError("no-new outcome has no anchor")
    if outcome in {"disabled", "corpus_unavailable", "corpus_incompatible"} and (
        expanded != 0 or strong_anchors != 0 or expanded_clusters != 0
    ):
        raise ValueError("direct-only outcome has expansion counts")
    version = _positive_version(value.get("cluster_corpus_version"))
    configuration_hash = _nullable_hash(value.get("cluster_configuration_hash"))
    expansion_ms = _duration(value, "cluster_expansion_ms", nullable=True)
    if (version is None) != (configuration_hash is None):
        raise ValueError("corpus version and configuration hash must be paired")
    requires_corpus = outcome in {"expanded", "no_strong_anchor", "no_new_photos"}
    empty_cohort_no_anchor = (
        outcome == "no_strong_anchor"
        and value["eligible_photo_count"] == 0
        and value["eligible_face_count"] == 0
        and value["matched_photo_count"] == 0
        and version is None
        and configuration_hash is None
        and expansion_ms is None
    )
    if requires_corpus and (
        (version is None or configuration_hash is None or expansion_ms is None)
        and not empty_cohort_no_anchor
    ):
        raise ValueError("eligible expansion lacks bounded identity")
    if not requires_corpus and (
        version is not None or configuration_hash is not None or expansion_ms is not None
    ):
        raise ValueError("unavailable expansion exposes bounded identity")


def _validate_terminal_v2(value: dict[str, Any]) -> None:
    direct = _duration(value, "direct_matched_photo_count")
    expanded = _duration(value, "cluster_expanded_photo_count")
    assert direct is not None and expanded is not None
    if value["status"] == "ready":
        if value["matched_photo_count"] != direct + expanded:
            raise ValueError("terminal count identity mismatch")
    elif value["matched_photo_count"] != 0 or direct != 0 or expanded != 0:
        raise ValueError("non-ready terminal has source counts")
    version = _positive_version(value.get("cluster_corpus_version"))
    configuration_hash = _nullable_hash(value.get("cluster_configuration_hash"))
    if (version is None) != (configuration_hash is None):
        raise ValueError("corpus version and configuration hash must be paired")
    if value["status"] != "ready" and (version is not None or configuration_hash is not None):
        raise ValueError("non-ready terminal exposes corpus identity")
    if expanded > 0 and (version is None or configuration_hash is None):
        raise ValueError("expanded terminal lacks corpus identity")


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
    if value["schema_version"] == 1:
        state.historical_v1_ranking_count += 1
        return
    state.v2_ranking_count += 1
    state.expansion_integrity_seen = True
    expansion_outcome = value["cluster_expansion_outcome"]
    state.expansion_outcomes[expansion_outcome] += 1
    eligible_outcome = {
        "expanded",
        "no_strong_anchor",
        "no_new_photos",
    }
    if value["outcome"] == "succeeded" and expansion_outcome in eligible_outcome:
        if value["cluster_corpus_version"] is not None:
            version = str(value["cluster_corpus_version"])
            state.expansion_corpus_versions[version] = (
                state.expansion_corpus_versions.get(version, 0) + 1
            )
        if value["cluster_configuration_hash"] is not None:
            configuration_hash = value["cluster_configuration_hash"].lower()
            state.expansion_configuration_hashes[configuration_hash] = (
                state.expansion_configuration_hashes.get(configuration_hash, 0) + 1
            )
        if value["cluster_expansion_ms"] is not None:
            state.expansion_durations.append(value["cluster_expansion_ms"])
    state.expansion_rankings[value["search_id"]] = {
        "event_id": value["event_id"],
        "ranking_outcome": value["outcome"],
        "outcome": value["cluster_expansion_outcome"],
        "direct_matched_photo_count": value["direct_matched_photo_count"],
        "cluster_expanded_photo_count": value["cluster_expanded_photo_count"],
        "final_matched_photo_count": value["final_matched_photo_count"],
        "strong_anchor_count": value["strong_anchor_count"],
        "expanded_cluster_count": value["expanded_cluster_count"],
        "cluster_corpus_version": value["cluster_corpus_version"],
        "cluster_configuration_hash": (
            value["cluster_configuration_hash"].lower()
            if value["cluster_configuration_hash"] is not None
            else None
        ),
    }


def _terminal_event(value: dict[str, Any], state: _State) -> None:
    status = value["status"]
    matches = value["matched_photo_count"]
    state.terminals["total"] += 1
    state.terminals["statuses"][status] += 1
    if status == "ready":
        state.terminals["ready_positive" if matches else "ready_zero"] += 1
    state.terminal_ids.add(value["search_id"])
    state.duration_samples["search_lifetime"].append(value["elapsed_ms"])
    if value["schema_version"] == 1:
        state.historical_v1_terminal_count += 1
        return
    state.v2_terminal_count += 1
    state.expansion_integrity_seen = True
    state.expansion_terminals[value["search_id"]] = {
        "event_id": value["event_id"],
        "status": value["status"],
        "matched_photo_count": value["matched_photo_count"],
        "direct_matched_photo_count": value["direct_matched_photo_count"],
        "cluster_expanded_photo_count": value["cluster_expanded_photo_count"],
        "cluster_corpus_version": value["cluster_corpus_version"],
        "cluster_configuration_hash": (
            value["cluster_configuration_hash"].lower()
            if value["cluster_configuration_hash"] is not None
            else None
        ),
    }


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


def _rate(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def _not_available_expansion() -> dict[str, Any]:
    unavailable = "not_available"
    return {
        "eligible_searches": unavailable,
        "searches_with_cluster_photos": unavailable,
        "direct_matched_photo_count": unavailable,
        "cluster_expanded_photo_count": unavailable,
        "final_matched_photo_count": unavailable,
        "strong_anchor_count": unavailable,
        "expanded_cluster_count": unavailable,
        "added_photos": unavailable,
        "expansion_ms": unavailable,
        "outcomes": unavailable,
        "corpus_versions": unavailable,
        "configuration_hashes": unavailable,
        "searches_helped_rate": unavailable,
        "incremental_photo_rate": unavailable,
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


def _positive_version(value: object) -> int | None:
    if value is None:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value > MAX_BOUNDED_INTEGER
    ):
        raise ValueError("invalid cluster corpus version")
    return value


def _nullable_hash(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not HASH_PATTERN.fullmatch(value):
        raise ValueError("invalid cluster configuration hash")
    return value.lower()


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
