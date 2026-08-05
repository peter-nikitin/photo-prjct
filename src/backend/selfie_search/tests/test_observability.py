from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from selfie_search.observability import (
    OBSERVABILITY_FAILURE_MARKER,
    SelfieEventContractError,
    SelfieEventName,
    declared_type_label,
    emit_selfie_event,
    source_size_bucket,
)


class _CaptureLogger(logging.Logger):
    def __init__(self) -> None:
        super().__init__("capture", logging.DEBUG)
        self.calls: list[tuple[int, str]] = []

    def log(self, level: int, message: str) -> None:  # type: ignore[override]
        self.calls.append((level, message))


def _submission_fields(**overrides: object) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "event_id": 17,
        "outcome": "accepted",
        "reason_code": "",
        "search_id": uuid4(),
        "actual_format": "jpeg",
        "declared_type": "jpeg",
        "source_size_bucket": "le_1mib",
        "duration_ms": 31,
    }
    fields.update(overrides)
    return fields


def _worker_fields(**overrides: object) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "event_id": 17,
        "search_id": uuid4(),
        "job_id": uuid4(),
        "attempt_id": uuid4(),
        "outcome": "succeeded",
        "reason_code": "",
        "retryable": False,
        "download_ms": 4,
        "compute_ms": 7,
        "total_ms": 11,
    }
    fields.update(overrides)
    return fields


def _ranking_fields(**overrides: object) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "event_id": 17,
        "search_id": uuid4(),
        "attempt_id": uuid4(),
        "outcome": "succeeded",
        "eligible_photo_count": 2,
        "eligible_face_count": 3,
        "matched_photo_count": 3,
        "load_ms": 4,
        "rank_ms": 7,
        "configuration_hash": "a" * 64,
        "direct_matched_photo_count": 1,
        "cluster_expanded_photo_count": 2,
        "final_matched_photo_count": 3,
        "strong_anchor_count": 1,
        "expanded_cluster_count": 1,
        "cluster_corpus_version": 7,
        "cluster_configuration_hash": "b" * 64,
        "cluster_expansion_ms": 12,
        "cluster_expansion_outcome": "expanded",
    }
    fields.update(overrides)
    return fields


def _terminal_fields(**overrides: object) -> dict[str, Any]:
    status = overrides.get("status", "ready")
    fields: dict[str, Any] = {
        "event_id": 17,
        "search_id": uuid4(),
        "status": "ready",
        "matched_photo_count": 3 if status == "ready" else 0,
        "attempt_count": 1,
        "elapsed_ms": 20,
        "failure_code": "",
        "cleanup_confirmed": True,
        "direct_matched_photo_count": 1 if status == "ready" else 0,
        "cluster_expanded_photo_count": 2 if status == "ready" else 0,
        "cluster_corpus_version": 7 if status == "ready" else None,
        "cluster_configuration_hash": "b" * 64 if status == "ready" else None,
    }
    fields.update(overrides)
    return fields


def _ranking_v2_fields(**overrides: object) -> dict[str, Any]:
    fields = _ranking_fields()
    fields.update(overrides)
    return fields


def _terminal_v2_fields(**overrides: object) -> dict[str, Any]:
    fields = _terminal_fields()
    fields.update(overrides)
    return fields


def test_ranking_v2_emits_only_bounded_expansion_fields_and_reconciles_counts() -> None:
    logger = _CaptureLogger()

    emit_selfie_event(
        logger,
        event=SelfieEventName.RANKING_FINISHED,
        **_ranking_v2_fields(),
    )

    payload = json.loads(logger.calls[0][1])
    assert payload["schema_version"] == 2
    assert payload["final_matched_photo_count"] == 3
    assert payload["cluster_expansion_outcome"] == "expanded"
    assert set(payload) == {
        "schema_version",
        "event",
        "occurred_at",
        "service",
        "environment",
        *(_ranking_v2_fields().keys()),
    }


def test_terminal_v2_emits_source_counts_and_reconciles_final_count() -> None:
    logger = _CaptureLogger()

    emit_selfie_event(
        logger,
        event=SelfieEventName.SEARCH_TERMINAL,
        **_terminal_v2_fields(),
    )

    payload = json.loads(logger.calls[0][1])
    assert payload["schema_version"] == 2
    assert payload["matched_photo_count"] == 3
    assert payload["direct_matched_photo_count"] == 1
    assert payload["cluster_expanded_photo_count"] == 2


def test_search_unavailable_terminal_clears_corpus_identity() -> None:
    logger = _CaptureLogger()

    emit_selfie_event(
        logger,
        event=SelfieEventName.SEARCH_TERMINAL,
        **_terminal_v2_fields(
            status="search_unavailable",
            matched_photo_count=0,
            direct_matched_photo_count=0,
            cluster_expanded_photo_count=0,
            cluster_corpus_version=None,
            cluster_configuration_hash=None,
        ),
    )

    payload = json.loads(logger.calls[0][1])
    assert payload["status"] == "search_unavailable"
    assert payload["matched_photo_count"] == 0
    assert payload["direct_matched_photo_count"] == 0
    assert payload["cluster_expanded_photo_count"] == 0
    assert payload["cluster_corpus_version"] is None
    assert payload["cluster_configuration_hash"] is None


def test_ranking_v2_allows_empty_cohort_no_anchor_without_identity() -> None:
    logger = _CaptureLogger()

    emit_selfie_event(
        logger,
        event=SelfieEventName.RANKING_FINISHED,
        **_ranking_v2_fields(
            eligible_photo_count=0,
            eligible_face_count=0,
            matched_photo_count=0,
            direct_matched_photo_count=0,
            cluster_expanded_photo_count=0,
            final_matched_photo_count=0,
            strong_anchor_count=0,
            expanded_cluster_count=0,
            cluster_corpus_version=None,
            cluster_configuration_hash=None,
            cluster_expansion_ms=None,
            cluster_expansion_outcome="no_strong_anchor",
        ),
    )

    payload = json.loads(logger.calls[0][1])
    assert payload["cluster_expansion_outcome"] == "no_strong_anchor"
    assert payload["cluster_corpus_version"] is None
    assert payload["cluster_configuration_hash"] is None
    assert payload["cluster_expansion_ms"] is None


def test_empty_cohort_no_anchor_rejects_corpus_identity_without_duration() -> None:
    with pytest.raises(SelfieEventContractError):
        emit_selfie_event(
            _CaptureLogger(),
            event=SelfieEventName.RANKING_FINISHED,
            **_ranking_v2_fields(
                eligible_photo_count=0,
                eligible_face_count=0,
                matched_photo_count=0,
                direct_matched_photo_count=0,
                cluster_expanded_photo_count=0,
                final_matched_photo_count=0,
                strong_anchor_count=0,
                expanded_cluster_count=0,
                cluster_corpus_version=7,
                cluster_configuration_hash="b" * 64,
                cluster_expansion_ms=None,
                cluster_expansion_outcome="no_strong_anchor",
            ),
        )


@pytest.mark.parametrize(
    "fields",
    [
        _ranking_v2_fields(final_matched_photo_count=4),
        _ranking_v2_fields(cluster_expanded_photo_count=0),
        _ranking_v2_fields(cluster_expansion_outcome="no_strong_anchor", strong_anchor_count=1),
        _ranking_v2_fields(cluster_expansion_outcome="expanded", cluster_expanded_photo_count=0),
    ],
)
def test_ranking_v2_rejects_invalid_count_or_outcome_relationships(
    fields: dict[str, Any],
) -> None:
    with pytest.raises(SelfieEventContractError):
        emit_selfie_event(_CaptureLogger(), event=SelfieEventName.RANKING_FINISHED, **fields)


@pytest.mark.parametrize(
    "fields",
    [
        _terminal_v2_fields(matched_photo_count=4),
        _terminal_v2_fields(status="no_face", failure_code="no_face"),
        _terminal_v2_fields(
            status="no_face",
            failure_code="no_face",
            direct_matched_photo_count=1,
            cluster_expanded_photo_count=0,
        ),
        _terminal_v2_fields(
            status="search_unavailable",
            matched_photo_count=0,
            direct_matched_photo_count=0,
            cluster_expanded_photo_count=0,
            cluster_corpus_version=7,
            cluster_configuration_hash="b" * 64,
        ),
    ],
)
def test_terminal_v2_rejects_mismatched_or_non_ready_source_counts(
    fields: dict[str, Any],
) -> None:
    with pytest.raises(SelfieEventContractError):
        emit_selfie_event(_CaptureLogger(), event=SelfieEventName.SEARCH_TERMINAL, **fields)


@pytest.mark.parametrize(
    "fields",
    [
        _ranking_v2_fields(
            matched_photo_count=1,
            direct_matched_photo_count=1,
            cluster_expanded_photo_count=0,
            final_matched_photo_count=1,
            strong_anchor_count=0,
            expanded_cluster_count=0,
            cluster_expansion_outcome="no_strong_anchor",
        ),
        _ranking_v2_fields(
            matched_photo_count=1,
            direct_matched_photo_count=1,
            cluster_expanded_photo_count=0,
            final_matched_photo_count=1,
            strong_anchor_count=1,
            expanded_cluster_count=0,
            cluster_expansion_outcome="no_new_photos",
        ),
        _ranking_v2_fields(
            matched_photo_count=1,
            direct_matched_photo_count=1,
            cluster_expanded_photo_count=0,
            final_matched_photo_count=1,
            strong_anchor_count=0,
            expanded_cluster_count=0,
            cluster_corpus_version=None,
            cluster_configuration_hash=None,
            cluster_expansion_ms=None,
            cluster_expansion_outcome="disabled",
        ),
        _ranking_v2_fields(
            outcome="incompatible",
            matched_photo_count=0,
            direct_matched_photo_count=0,
            cluster_expanded_photo_count=0,
            final_matched_photo_count=0,
            strong_anchor_count=0,
            expanded_cluster_count=0,
            cluster_corpus_version=None,
            cluster_configuration_hash=None,
            cluster_expansion_ms=None,
            cluster_expansion_outcome="corpus_incompatible",
        ),
    ],
)
def test_ranking_v2_accepts_each_bounded_expansion_outcome(fields: dict[str, Any]) -> None:
    logger = _CaptureLogger()
    emit_selfie_event(logger, event=SelfieEventName.RANKING_FINISHED, **fields)
    assert json.loads(logger.calls[0][1])["schema_version"] == 2


def test_probe_has_only_the_common_envelope_and_random_non_secret_id() -> None:
    logger = _CaptureLogger()
    probe_id = uuid4()

    emit_selfie_event(
        logger,
        event=SelfieEventName.OBSERVABILITY_PROBE,
        probe_id=probe_id,
    )

    payload = json.loads(logger.calls[0][1])
    assert set(payload) == {
        "schema_version",
        "event",
        "occurred_at",
        "service",
        "environment",
        "probe_id",
    }
    assert payload["event"] == "selfie_observability_probe"
    assert payload["service"] == "web"
    assert payload["probe_id"] == str(probe_id)


@pytest.mark.parametrize("probe_id", ["not-a-uuid", 1, None])
def test_probe_rejects_invalid_ids(probe_id: object) -> None:
    with pytest.raises(SelfieEventContractError):
        emit_selfie_event(
            _CaptureLogger(),
            event=SelfieEventName.OBSERVABILITY_PROBE,
            probe_id=probe_id,
        )


_PRIVACY_SENTINELS = {
    "bearer": "Bearer SECRET-BEARER",
    "url": "https://storage.example/signed?token=SECRET-URL",
    "key": "selfie-search/SECRET-OBJECT-KEY",
    "filename": "SECRET-FILENAME.jpg",
    "ip": "203.0.113.44",
    "ua": "SECRET-USER-AGENT",
    "vector": "[0.1,0.2,0.3]",
    "photo": "SECRET-PHOTO-ID",
    "face": "SECRET-FACE-ID",
    "exception": "SECRET-EXCEPTION-TEXT",
}

_BACKEND_PRIVACY_CASES = [
    (event, fields, field)
    for event, fields in (
        (SelfieEventName.SUBMISSION_FINISHED, _submission_fields()),
        (SelfieEventName.RANKING_FINISHED, _ranking_fields()),
        (SelfieEventName.SEARCH_TERMINAL, _terminal_fields()),
    )
    for field in fields
]


@pytest.mark.parametrize(
    ("event", "fields", "expected_fields"),
    [
        (
            SelfieEventName.SUBMISSION_FINISHED,
            _submission_fields(),
            {
                "event_id",
                "outcome",
                "reason_code",
                "search_id",
                "actual_format",
                "declared_type",
                "source_size_bucket",
                "duration_ms",
            },
        ),
        (
            SelfieEventName.RANKING_FINISHED,
            _ranking_fields(),
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
                "direct_matched_photo_count",
                "cluster_expanded_photo_count",
                "final_matched_photo_count",
                "strong_anchor_count",
                "expanded_cluster_count",
                "cluster_corpus_version",
                "cluster_configuration_hash",
                "cluster_expansion_ms",
                "cluster_expansion_outcome",
            },
        ),
        (
            SelfieEventName.SEARCH_TERMINAL,
            _terminal_fields(),
            {
                "event_id",
                "search_id",
                "status",
                "matched_photo_count",
                "attempt_count",
                "elapsed_ms",
                "failure_code",
                "cleanup_confirmed",
                "direct_matched_photo_count",
                "cluster_expanded_photo_count",
                "cluster_corpus_version",
                "cluster_configuration_hash",
            },
        ),
    ],
)
def test_backend_events_have_exact_compact_envelope_and_event_fields(
    event: SelfieEventName, fields: dict[str, Any], expected_fields: set[str]
) -> None:
    logger = _CaptureLogger()

    emit_selfie_event(logger, event=event, **fields)

    assert len(logger.calls) == 1
    level, line = logger.calls[0]
    assert level == logging.INFO
    assert "\n" not in line
    assert " " not in line
    payload = json.loads(line)
    assert set(payload) == {
        "schema_version",
        "event",
        "occurred_at",
        "service",
        "environment",
        *expected_fields,
    }
    assert payload["schema_version"] == (1 if event is SelfieEventName.SUBMISSION_FINISHED else 2)
    assert payload["event"] == event.value
    assert payload["service"] == "web"
    assert isinstance(payload["environment"], str)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", payload["occurred_at"])
    datetime.strptime(payload["occurred_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    assert payload["event_id"] == "17"
    for key in ("search_id", "attempt_id"):
        if key in payload:
            assert (
                payload[key] == str(fields[key])
                if fields[key] is not None
                else payload[key] is None
            )


def test_submission_storage_failure_and_rejections_require_matching_reason() -> None:
    logger = _CaptureLogger()

    emit_selfie_event(
        logger,
        event=SelfieEventName.SUBMISSION_FINISHED,
        **_submission_fields(
            outcome="storage_unavailable", reason_code="storage_unavailable", search_id=None
        ),
    )
    assert json.loads(logger.calls[0][1])["search_id"] is None

    with pytest.raises(SelfieEventContractError):
        emit_selfie_event(
            logger,
            event=SelfieEventName.SUBMISSION_FINISHED,
            **_submission_fields(outcome="accepted", reason_code="missing_or_empty"),
        )


@pytest.mark.parametrize(
    ("status", "failure_code"),
    [
        ("ready", ""),
        ("no_face", "no_face"),
        ("multiple_faces", "multiple_faces"),
        ("quality_rejected", "quality_rejected"),
        ("search_unavailable", ""),
        ("failed", "failed"),
    ],
)
def test_terminal_event_accepts_exact_durable_status_failure_pairs(
    status: str, failure_code: str
) -> None:
    logger = _CaptureLogger()
    emit_selfie_event(
        logger,
        event=SelfieEventName.SEARCH_TERMINAL,
        **_terminal_fields(status=status, failure_code=failure_code),
    )
    assert json.loads(logger.calls[0][1])["failure_code"] == failure_code


@pytest.mark.parametrize(
    ("status", "failure_code"),
    [
        ("ready", "failed"),
        ("no_face", ""),
        ("no_face", "failed"),
        ("multiple_faces", ""),
        ("multiple_faces", "quality_rejected"),
        ("quality_rejected", "failed"),
        ("search_unavailable", "search_unavailable"),
        ("failed", "quality_rejected"),
    ],
)
def test_terminal_event_rejects_mismatched_durable_status_failure_pairs(
    status: str, failure_code: str
) -> None:
    with pytest.raises(SelfieEventContractError):
        emit_selfie_event(
            _CaptureLogger(),
            event=SelfieEventName.SEARCH_TERMINAL,
            **_terminal_fields(status=status, failure_code=failure_code),
        )


@pytest.mark.parametrize(
    ("event", "fields", "name"),
    [
        (SelfieEventName.SUBMISSION_FINISHED, _submission_fields(), "submission"),
        (SelfieEventName.RANKING_FINISHED, _ranking_fields(), "ranking"),
        (SelfieEventName.SEARCH_TERMINAL, _terminal_fields(), "terminal"),
    ],
)
def test_backend_events_reject_unknown_or_nested_fields(
    event: SelfieEventName, fields: dict[str, Any], name: str
) -> None:
    logger = _CaptureLogger()
    with pytest.raises(SelfieEventContractError):
        emit_selfie_event(logger, event=event, **(fields | {"unexpected": {"nested": name}}))
    assert logger.calls == []


@pytest.mark.parametrize(
    ("event", "fields"),
    [
        (SelfieEventName.SUBMISSION_FINISHED, _submission_fields(duration_ms=-1)),
        (SelfieEventName.SUBMISSION_FINISHED, _submission_fields(duration_ms=2**63)),
        (SelfieEventName.RANKING_FINISHED, _ranking_fields(eligible_face_count=-1)),
        (SelfieEventName.SEARCH_TERMINAL, _terminal_fields(cleanup_confirmed=False)),
    ],
)
def test_backend_events_reject_unbounded_or_invalid_values(
    event: SelfieEventName, fields: dict[str, Any]
) -> None:
    with pytest.raises(SelfieEventContractError):
        emit_selfie_event(_CaptureLogger(), event=event, **fields)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "missing"),
        ("", "missing"),
        ("image/jpeg", "jpeg"),
        ("JPEG", "jpeg"),
        ("image/png", "png"),
        ("image/heic", "heic"),
        ("image/heif", "heif"),
        ("application/octet-stream", "octet_stream"),
        ("application/x-custom", "other"),
        ("Bearer SECRET", "other"),
    ],
)
def test_declared_type_label_is_bounded(value: object, expected: str) -> None:
    assert declared_type_label(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "empty"),
        (1 * 1024 * 1024, "le_1mib"),
        (1 * 1024 * 1024 + 1, "le_5mib"),
        (5 * 1024 * 1024, "le_5mib"),
        (5 * 1024 * 1024 + 1, "le_10mib"),
        (10 * 1024 * 1024, "le_10mib"),
        (10 * 1024 * 1024 + 1, "le_20mib"),
        (20 * 1024 * 1024, "le_20mib"),
        (20 * 1024 * 1024 + 1, "gt_20mib"),
    ],
)
def test_source_size_bucket_has_exact_boundaries(value: int, expected: str) -> None:
    assert source_size_bucket(value) == expected


@pytest.mark.parametrize("event,fields,field", _BACKEND_PRIVACY_CASES)
def test_backend_privacy_matrix_rejects_every_sentinel_for_every_field(
    event: SelfieEventName, fields: dict[str, Any], field: str
) -> None:
    logger = _CaptureLogger()
    for sentinel in _PRIVACY_SENTINELS.values():
        candidate = fields | {field: sentinel}
        with pytest.raises(SelfieEventContractError):
            emit_selfie_event(logger, event=event, **candidate)
        assert logger.calls == []


def test_backend_logger_failure_is_contained() -> None:
    class FailingLogger(logging.Logger):
        def __init__(self) -> None:
            super().__init__("failing", logging.DEBUG)

        def log(self, _level: int, _message: str) -> None:  # type: ignore[override]
            raise RuntimeError("logger unavailable")

    emit_selfie_event(
        FailingLogger(), event=SelfieEventName.SUBMISSION_FINISHED, **_submission_fields()
    )


def test_backend_serialization_failure_emits_fixed_marker_without_propagating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_serialization(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("SECRET-EXCEPTION-TEXT")

    monkeypatch.setattr("selfie_search.observability.json.dumps", fail_serialization)
    logger = _CaptureLogger()

    emit_selfie_event(logger, event=SelfieEventName.SUBMISSION_FINISHED, **_submission_fields())

    assert logger.calls == [(logging.ERROR, OBSERVABILITY_FAILURE_MARKER)]
