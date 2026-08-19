"""Strict, privacy-bounded JSON events owned by the selfie-search service.

This module intentionally contains no generic logging integration.  Callers provide one of the
small event contracts below; invalid producer input is rejected before the logger is touched and
failures in the logger itself are contained so that observability cannot change product behavior.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

SCHEMA_VERSION = 1
RANKING_SCHEMA_VERSION = 2
TERMINAL_SCHEMA_VERSION = 2
SERVICE = "web"
MAX_BOUNDED_INTEGER = 2**31 - 1
_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_INTEGER_PATTERN = re.compile(r"^[1-9][0-9]{0,9}$")
_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class SelfieEventContractError(ValueError):
    """Raised when an owned event payload does not satisfy its fixed contract."""


# A short alias keeps the local exception discoverable without introducing a shared framework.
ObservabilityContractError = SelfieEventContractError


class SelfieEventName(StrEnum):
    OBSERVABILITY_PROBE = "selfie_observability_probe"
    SUBMISSION_FINISHED = "selfie_submission_finished"
    RANKING_FINISHED = "selfie_ranking_finished"
    SEARCH_TERMINAL = "selfie_search_terminal"


_SUBMISSION_REASONS = frozenset(
    {
        "",
        "missing_or_empty",
        "unsupported_format",
        "corrupt_image",
        "source_too_large",
        "normalized_too_large",
        "pixel_limit_exceeded",
        "storage_unavailable",
    }
)
_ACTUAL_FORMATS = frozenset({"jpeg", "png", "heic", "heif", "unknown"})
_DECLARED_TYPES = frozenset({"jpeg", "png", "heic", "heif", "octet_stream", "missing", "other"})
_SOURCE_SIZE_BUCKETS = frozenset(
    {"empty", "le_1mib", "le_5mib", "le_10mib", "le_20mib", "gt_20mib"}
)
_RANKING_OUTCOMES = frozenset({"succeeded", "incompatible"})
_TERMINAL_STATUSES = frozenset(
    {"ready", "no_face", "multiple_faces", "quality_rejected", "search_unavailable", "failed"}
)
_TERMINAL_FAILURE_CODES = frozenset(
    {"", "no_face", "multiple_faces", "quality_rejected", "search_unavailable", "failed"}
)
_TERMINAL_FAILURE_BY_STATUS = {
    "ready": "",
    "no_face": "no_face",
    "multiple_faces": "multiple_faces",
    "quality_rejected": "quality_rejected",
    "search_unavailable": "",
    "failed": "failed",
}
_CLUSTER_EXPANSION_OUTCOMES = frozenset(
    {
        "expanded",
        "no_strong_anchor",
        "no_new_photos",
        "corpus_unavailable",
        "corpus_incompatible",
        "disabled",
    }
)
OBSERVABILITY_FAILURE_MARKER = "selfie_observability_emit_failed"

_RANKING_FIELDS_V1 = frozenset(
    {
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
)
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
_TERMINAL_FIELDS_V1 = frozenset(
    {
        "event_id",
        "search_id",
        "status",
        "matched_photo_count",
        "attempt_count",
        "elapsed_ms",
        "failure_code",
        "cleanup_confirmed",
    }
)
_TERMINAL_FIELDS_V2 = _TERMINAL_FIELDS_V1 | {
    "direct_matched_photo_count",
    "cluster_expanded_photo_count",
    "cluster_corpus_version",
    "cluster_configuration_hash",
}

_EVENT_FIELDS: dict[SelfieEventName, frozenset[str]] = {
    SelfieEventName.OBSERVABILITY_PROBE: frozenset({"probe_id"}),
    SelfieEventName.SUBMISSION_FINISHED: frozenset(
        {
            "event_id",
            "outcome",
            "reason_code",
            "search_id",
            "actual_format",
            "declared_type",
            "source_size_bucket",
            "duration_ms",
        }
    ),
    SelfieEventName.RANKING_FINISHED: _RANKING_FIELDS_V2,
    SelfieEventName.SEARCH_TERMINAL: _TERMINAL_FIELDS_V2,
}


def emit_selfie_event(
    logger: logging.Logger,
    *,
    event: SelfieEventName,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """Validate and emit one compact JSON event, swallowing logger/serialization failures.

    The payload is fully normalized before ``logger.log`` is called.  In particular, no caller
    supplied value is interpolated into an ordinary log message and no unknown field can make it
    into the serialized object.
    """

    if not isinstance(level, int) or isinstance(level, bool):
        raise SelfieEventContractError("invalid log level")
    event_name = _event_name(event)
    payload = _validated_payload(event_name, fields)
    try:
        serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except Exception:
        emit_selfie_observability_failure(logger)
        return
    try:
        logger.log(level, serialized)
    except Exception:
        emit_selfie_observability_failure(logger)


def declared_type_label(value: object) -> str:
    """Return the bounded declared content-type label used by submission events."""

    if value is None:
        return "missing"
    if not isinstance(value, str):
        return "other"
    normalized = value.strip().lower()
    if not normalized:
        return "missing"
    if normalized in {"jpeg", "jpg", "image/jpeg"}:
        return "jpeg"
    if normalized in {"png", "image/png"}:
        return "png"
    if normalized in {"heic", "image/heic"}:
        return "heic"
    if normalized in {"heif", "image/heif"}:
        return "heif"
    if normalized in {"octet-stream", "application/octet-stream"}:
        return "octet_stream"
    return "other"


def actual_format_label(value: object) -> str:
    """Return the bounded Pillow format label used by submission events."""

    if not isinstance(value, str):
        return "unknown"
    return {
        "JPEG": "jpeg",
        "PNG": "png",
        "HEIC": "heic",
        "HEIF": "heif",
    }.get(value.upper(), "unknown")


def source_size_bucket(value: object) -> str:
    """Return the fixed source-size bucket without exposing a byte count."""

    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return "empty"
    if value <= 1 * 1024 * 1024:
        return "le_1mib"
    if value <= 5 * 1024 * 1024:
        return "le_5mib"
    if value <= 10 * 1024 * 1024:
        return "le_10mib"
    if value <= 20 * 1024 * 1024:
        return "le_20mib"
    return "gt_20mib"


def _event_name(value: object) -> SelfieEventName:
    if isinstance(value, SelfieEventName):
        return value
    if not isinstance(value, str):
        raise SelfieEventContractError("unknown selfie event")
    try:
        return SelfieEventName(value)
    except (TypeError, ValueError) as error:
        raise SelfieEventContractError("unknown selfie event") from error


def _validated_payload(event: SelfieEventName, fields: dict[str, object]) -> dict[str, object]:
    expected = _EVENT_FIELDS[event]
    if set(fields) == expected:
        schema_version = (
            TERMINAL_SCHEMA_VERSION
            if event is SelfieEventName.SEARCH_TERMINAL
            else RANKING_SCHEMA_VERSION
            if event is SelfieEventName.RANKING_FINISHED
            else SCHEMA_VERSION
        )
    else:
        raise SelfieEventContractError("event fields do not match the contract")
    normalized: dict[str, object]
    if event is SelfieEventName.OBSERVABILITY_PROBE:
        normalized = {"probe_id": _opaque_id(fields["probe_id"], allow_integer=False)}
    elif event is SelfieEventName.SUBMISSION_FINISHED:
        normalized = _submission_fields(fields)
    elif event is SelfieEventName.RANKING_FINISHED:
        normalized = _ranking_fields_v2(fields)
    else:
        normalized = _terminal_fields_v2(fields)
    return {
        "schema_version": schema_version,
        "event": event.value,
        "occurred_at": _timestamp(),
        "service": SERVICE,
        **normalized,
    }


def _submission_fields(fields: dict[str, object]) -> dict[str, object]:
    outcome = fields["outcome"]
    reason = fields["reason_code"]
    search_id = fields["search_id"]
    if outcome not in {"accepted", "rejected", "storage_unavailable"}:
        raise SelfieEventContractError("invalid submission outcome")
    if not isinstance(reason, str) or reason not in _SUBMISSION_REASONS:
        raise SelfieEventContractError("invalid submission reason")
    if outcome == "accepted" and (reason != "" or search_id is None):
        raise SelfieEventContractError("accepted submission has an invalid correlation")
    if outcome == "rejected" and (reason not in _SUBMISSION_REASONS - {"", "storage_unavailable"}):
        raise SelfieEventContractError("rejected submission has an invalid reason")
    if outcome == "storage_unavailable" and (
        reason != "storage_unavailable" or search_id is not None
    ):
        raise SelfieEventContractError("storage failure has an invalid correlation")
    if outcome == "rejected" and search_id is not None:
        raise SelfieEventContractError("rejected submission must not have a search id")
    return {
        "event_id": _opaque_id(fields["event_id"]),
        "outcome": outcome,
        "reason_code": reason,
        "search_id": _opaque_id(search_id, nullable=True, allow_integer=False),
        "actual_format": _enum(fields["actual_format"], _ACTUAL_FORMATS, "actual format"),
        "declared_type": _enum(fields["declared_type"], _DECLARED_TYPES, "declared type"),
        "source_size_bucket": _enum(
            fields["source_size_bucket"], _SOURCE_SIZE_BUCKETS, "source size bucket"
        ),
        "duration_ms": _bounded_int(fields["duration_ms"], nullable=False),
    }


def _worker_like_ids(fields: dict[str, object], names: tuple[str, ...]) -> dict[str, object]:
    return {name: _opaque_id(fields[name], allow_integer=name == "event_id") for name in names}


def _ranking_fields_v1(fields: dict[str, object]) -> dict[str, object]:
    outcome = fields["outcome"]
    if outcome not in _RANKING_OUTCOMES:
        raise SelfieEventContractError("invalid ranking outcome")
    return {
        **_worker_like_ids(fields, ("event_id", "search_id", "attempt_id")),
        "outcome": outcome,
        "eligible_photo_count": _bounded_int(fields["eligible_photo_count"], nullable=False),
        "eligible_face_count": _bounded_int(fields["eligible_face_count"], nullable=False),
        "matched_photo_count": _bounded_int(fields["matched_photo_count"], nullable=False),
        "load_ms": _bounded_int(fields["load_ms"], nullable=True),
        "rank_ms": _bounded_int(fields["rank_ms"], nullable=True),
        "configuration_hash": _hash(fields["configuration_hash"]),
    }


def _ranking_fields_v2(fields: dict[str, object]) -> dict[str, object]:
    normalized = _ranking_fields_v1(fields)
    direct = _bounded_int(fields["direct_matched_photo_count"], nullable=False)
    expanded = _bounded_int(fields["cluster_expanded_photo_count"], nullable=False)
    final = _bounded_int(fields["final_matched_photo_count"], nullable=False)
    strong_anchors = _bounded_int(fields["strong_anchor_count"], nullable=False)
    expanded_clusters = _bounded_int(fields["expanded_cluster_count"], nullable=False)
    assert direct is not None and expanded is not None and final is not None
    assert strong_anchors is not None and expanded_clusters is not None
    if normalized["outcome"] == "incompatible":
        if normalized["matched_photo_count"] != 0 or any(
            (direct, expanded, final, strong_anchors, expanded_clusters)
        ):
            raise SelfieEventContractError("incompatible ranking must have zero source counts")
    elif final != direct + expanded or normalized["matched_photo_count"] != final:
        raise SelfieEventContractError("ranking result counts do not reconcile")
    expansion_outcome = _enum(
        fields["cluster_expansion_outcome"],
        _CLUSTER_EXPANSION_OUTCOMES,
        "cluster expansion outcome",
    )
    if expanded > 0 and expansion_outcome != "expanded":
        raise SelfieEventContractError("expanded ranking has an invalid outcome")
    if expansion_outcome == "expanded" and (expanded <= 0 or expanded_clusters <= 0):
        raise SelfieEventContractError("expanded outcome requires added photos and a cluster")
    if expansion_outcome == "no_strong_anchor" and (strong_anchors != 0 or expanded_clusters != 0):
        raise SelfieEventContractError("no-strong-anchor outcome has anchors")
    if expansion_outcome == "no_new_photos" and strong_anchors <= 0:
        raise SelfieEventContractError("no-new outcome requires a strong anchor")
    if expansion_outcome in {"disabled", "corpus_unavailable", "corpus_incompatible"} and (
        expanded != 0 or strong_anchors != 0 or expanded_clusters != 0
    ):
        raise SelfieEventContractError("direct-only outcome has expansion counts")
    version = _positive_bounded_int(fields["cluster_corpus_version"], nullable=True)
    configuration_hash = _nullable_hash(fields["cluster_configuration_hash"])
    expansion_ms = _bounded_int(fields["cluster_expansion_ms"], nullable=True)
    if (version is None) != (configuration_hash is None):
        raise SelfieEventContractError("corpus version and configuration hash must be paired")
    requires_corpus = expansion_outcome in {"expanded", "no_strong_anchor", "no_new_photos"}
    empty_cohort_no_anchor = (
        expansion_outcome == "no_strong_anchor"
        and fields["eligible_photo_count"] == 0
        and fields["eligible_face_count"] == 0
        and fields["matched_photo_count"] == 0
        and version is None
        and configuration_hash is None
        and expansion_ms is None
    )
    if requires_corpus and (
        (version is None or configuration_hash is None or expansion_ms is None)
        and not empty_cohort_no_anchor
    ):
        raise SelfieEventContractError("eligible expansion requires corpus identity and duration")
    if not requires_corpus and (
        version is not None or configuration_hash is not None or expansion_ms is not None
    ):
        raise SelfieEventContractError("unavailable expansion must not expose corpus identity")
    return {
        **normalized,
        "direct_matched_photo_count": direct,
        "cluster_expanded_photo_count": expanded,
        "final_matched_photo_count": final,
        "strong_anchor_count": strong_anchors,
        "expanded_cluster_count": expanded_clusters,
        "cluster_corpus_version": version,
        "cluster_configuration_hash": configuration_hash,
        "cluster_expansion_ms": expansion_ms,
        "cluster_expansion_outcome": expansion_outcome,
    }


def _terminal_fields_v1(fields: dict[str, object]) -> dict[str, object]:
    status = fields["status"]
    if status not in _TERMINAL_STATUSES:
        raise SelfieEventContractError("invalid terminal status")
    failure_code = fields["failure_code"]
    if not isinstance(failure_code, str) or failure_code not in _TERMINAL_FAILURE_CODES:
        raise SelfieEventContractError("invalid terminal failure code")
    if failure_code != _TERMINAL_FAILURE_BY_STATUS[status]:
        raise SelfieEventContractError("terminal status and failure code do not match")
    if fields["cleanup_confirmed"] is not True:
        raise SelfieEventContractError("terminal event requires confirmed cleanup")
    return {
        **_worker_like_ids(fields, ("event_id", "search_id")),
        "status": status,
        "matched_photo_count": _bounded_int(fields["matched_photo_count"], nullable=False),
        "attempt_count": _bounded_int(fields["attempt_count"], nullable=False),
        "elapsed_ms": _bounded_int(fields["elapsed_ms"], nullable=False),
        "failure_code": failure_code,
        "cleanup_confirmed": True,
    }


def _terminal_fields_v2(fields: dict[str, object]) -> dict[str, object]:
    normalized = _terminal_fields_v1(fields)
    direct = _bounded_int(fields["direct_matched_photo_count"], nullable=False)
    expanded = _bounded_int(fields["cluster_expanded_photo_count"], nullable=False)
    assert direct is not None and expanded is not None
    if normalized["status"] == "ready":
        if normalized["matched_photo_count"] != direct + expanded:
            raise SelfieEventContractError("terminal result counts do not reconcile")
    elif normalized["matched_photo_count"] != 0 or direct != 0 or expanded != 0:
        raise SelfieEventContractError("non-ready terminal must have zero source counts")
    version = _positive_bounded_int(fields["cluster_corpus_version"], nullable=True)
    configuration_hash = _nullable_hash(fields["cluster_configuration_hash"])
    if (version is None) != (configuration_hash is None):
        raise SelfieEventContractError("corpus version and configuration hash must be paired")
    if normalized["status"] != "ready" and (version is not None or configuration_hash is not None):
        raise SelfieEventContractError("non-ready terminal must not expose corpus identity")
    if expanded > 0 and (version is None or configuration_hash is None):
        raise SelfieEventContractError("expanded terminal requires corpus identity")
    return {
        **normalized,
        "direct_matched_photo_count": direct,
        "cluster_expanded_photo_count": expanded,
        "cluster_corpus_version": version,
        "cluster_configuration_hash": configuration_hash,
    }


def _enum(value: object, allowed: frozenset[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise SelfieEventContractError(f"invalid {label}")
    return value


def _bounded_int(value: object, *, nullable: bool) -> int | None:
    if value is None and nullable:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > MAX_BOUNDED_INTEGER
    ):
        raise SelfieEventContractError("integer is outside the bounded contract")
    return value


def _positive_bounded_int(value: object, *, nullable: bool) -> int | None:
    normalized = _bounded_int(value, nullable=nullable)
    if normalized is not None and normalized < 1:
        raise SelfieEventContractError("integer must be positive")
    return normalized


def _opaque_id(value: object, *, nullable: bool = False, allow_integer: bool = True) -> str | None:
    if value is None and nullable:
        return None
    if isinstance(value, UUID):
        return str(value)
    if (
        allow_integer
        and isinstance(value, int)
        and not isinstance(value, bool)
        and 0 < value <= MAX_BOUNDED_INTEGER
    ):
        return str(value)
    if isinstance(value, str):
        if _UUID_PATTERN.fullmatch(value):
            return str(UUID(value))
        if allow_integer and _INTEGER_PATTERN.fullmatch(value):
            return str(int(value))
    raise SelfieEventContractError("invalid opaque correlation id")


def _hash(value: object) -> str:
    if not isinstance(value, str) or not _HASH_PATTERN.fullmatch(value):
        raise SelfieEventContractError("invalid configuration hash")
    return value.lower()


def _nullable_hash(value: object) -> str | None:
    if value is None:
        return None
    return _hash(value)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def emit_selfie_observability_failure(logger: logging.Logger) -> None:
    """Emit only the fixed failure marker and contain output failures."""

    try:
        logger.log(logging.ERROR, OBSERVABILITY_FAILURE_MARKER)
    except Exception:
        return
