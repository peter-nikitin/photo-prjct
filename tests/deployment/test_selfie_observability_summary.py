from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "deploy" / "selfie-observability" / "summarize.py"


class _WorkerEventLogger(logging.Logger):
    def __init__(self) -> None:
        super().__init__("worker-summary-contract")
        self.lines: list[str] = []

    def log(self, level: int, message: str) -> None:  # type: ignore[override]
        self.lines.append(message)


def _load_module():
    spec = importlib.util.spec_from_file_location("selfie_summary", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _event(name: str, occurred_at: str, *, schema_version: int = 1, **fields: object) -> str:
    service = "worker" if name == "selfie_worker_attempt_finished" else "web"
    return json.dumps(
        {
            "schema_version": schema_version,
            "event": name,
            "occurred_at": occurred_at,
            "service": service,
            **fields,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _submission(
    search_id: str | None,
    *,
    outcome: str = "accepted",
    reason: str = "",
    actual: str = "jpeg",
    declared: str = "jpeg",
    bucket: str = "le_1mib",
    duration: int = 10,
    occurred_at: str = "2026-08-02T21:00:00.000Z",
) -> str:
    return _event(
        "selfie_submission_finished",
        occurred_at,
        event_id="17",
        outcome=outcome,
        reason_code=reason,
        search_id=search_id,
        actual_format=actual,
        declared_type=declared,
        source_size_bucket=bucket,
        duration_ms=duration,
    )


def _terminal(
    search_id: str, *, status: str, matches: int, occurred_at: str = "2026-08-03T10:00:00Z"
) -> str:
    failure = "failed" if status == "failed" else ""
    return _event(
        "selfie_search_terminal",
        occurred_at,
        event_id="17",
        search_id=search_id,
        status=status,
        matched_photo_count=matches,
        attempt_count=1,
        elapsed_ms=100,
        failure_code=failure,
        cleanup_confirmed=True,
    )


def _ranking_v2(
    search_id: str,
    *,
    outcome: str = "succeeded",
    direct: int = 1,
    expanded: int = 0,
    final: int | None = None,
    anchors: int = 0,
    clusters: int = 0,
    eligible_photo_count: int = 5,
    eligible_face_count: int = 7,
    corpus_version: int | None = None,
    configuration_hash: str | None = None,
    expansion_ms: int | None = None,
    cluster_outcome: str | None = None,
    attempt_id: str | None = None,
    occurred_at: str = "2026-08-03T10:00:00Z",
) -> str:
    if cluster_outcome is None:
        if outcome == "incompatible":
            cluster_outcome = "corpus_incompatible"
        elif expanded > 0:
            cluster_outcome = "expanded"
        elif anchors > 0:
            cluster_outcome = "no_new_photos"
        else:
            cluster_outcome = "no_strong_anchor" if corpus_version is not None else "disabled"
    return _event(
        "selfie_ranking_finished",
        occurred_at,
        schema_version=2,
        event_id="17",
        search_id=search_id,
        attempt_id=attempt_id or search_id,
        outcome=outcome,
        eligible_photo_count=eligible_photo_count,
        eligible_face_count=eligible_face_count,
        matched_photo_count=direct + expanded if final is None else final,
        load_ms=5,
        rank_ms=9,
        direct_matched_photo_count=direct,
        cluster_expanded_photo_count=expanded,
        final_matched_photo_count=direct + expanded if final is None else final,
        strong_anchor_count=anchors,
        expanded_cluster_count=clusters,
        cluster_corpus_version=corpus_version,
        cluster_configuration_hash=configuration_hash,
        cluster_expansion_ms=expansion_ms,
        cluster_expansion_outcome=cluster_outcome,
        configuration_hash="a" * 64,
    )


def _terminal_v2(
    search_id: str,
    *,
    status: str = "ready",
    direct: int = 1,
    expanded: int = 0,
    matched: int | None = None,
    corpus_version: int | None = None,
    configuration_hash: str | None = None,
    occurred_at: str = "2026-08-03T10:01:00Z",
) -> str:
    return _event(
        "selfie_search_terminal",
        occurred_at,
        schema_version=2,
        event_id="17",
        search_id=search_id,
        status=status,
        matched_photo_count=direct + expanded if matched is None else matched,
        direct_matched_photo_count=direct,
        cluster_expanded_photo_count=expanded,
        cluster_corpus_version=corpus_version,
        cluster_configuration_hash=configuration_hash,
        attempt_count=1,
        elapsed_ms=100,
        failure_code="failed"
        if status == "failed"
        else (status if status in {"no_face", "multiple_faces", "quality_rejected"} else ""),
        cleanup_confirmed=True,
    )


def _search_id(number: int) -> str:
    return f"00000000-0000-0000-0000-{number:012d}"


def test_worker_event_is_accepted_by_the_canonical_summary_envelope() -> None:
    from photo_worker.observability import emit_selfie_worker_event

    logger = _WorkerEventLogger()
    emit_selfie_worker_event(
        logger,
        event="selfie_worker_attempt_finished",
        event_id=17,
        search_id=_search_id(1),
        job_id=_search_id(2),
        attempt_id=_search_id(3),
        outcome="succeeded",
        reason_code="",
        retryable=False,
        download_ms=4,
        compute_ms=7,
        total_ms=11,
    )

    payload = json.loads(logger.lines[0])
    assert "environment" not in payload
    report_date = (
        datetime.fromisoformat(payload["occurred_at"]).astimezone(ZoneInfo("Europe/Moscow")).date()
    )
    summary = _load_module().summarize_jsonl(logger.lines, report_date=report_date)
    assert summary.worker_attempts["total"] == 1
    assert summary.integrity["malformed_events"] == 0


def test_daily_summary_counts_the_critical_funnel_and_integrity_boundaries() -> None:
    summarize = _load_module()
    accepted = [
        _submission("00000000-0000-0000-0000-000000000001", duration=1),
        _submission(
            "00000000-0000-0000-0000-000000000002",
            actual="png",
            declared="png",
            bucket="le_5mib",
            duration=2,
        ),
        _submission(
            "00000000-0000-0000-0000-000000000003",
            actual="heic",
            declared="heic",
            bucket="le_10mib",
            duration=3,
        ),
    ]
    rejected_cases = [
        ("missing_or_empty", "unknown", "missing", "empty"),
        ("unsupported_format", "unknown", "other", "le_1mib"),
        ("corrupt_image", "jpeg", "jpeg", "le_5mib"),
        ("source_too_large", "heif", "heif", "gt_20mib"),
        ("normalized_too_large", "png", "octet_stream", "le_20mib"),
        ("pixel_limit_exceeded", "heic", "heic", "le_10mib"),
    ]
    rejected = [
        _submission(
            None,
            outcome="rejected",
            reason=reason,
            actual=actual,
            declared=declared,
            bucket=bucket,
            duration=10 + index,
        )
        for index, (reason, actual, declared, bucket) in enumerate(rejected_cases)
    ]
    storage_failure = _submission(
        None,
        outcome="storage_unavailable",
        reason="storage_unavailable",
        actual="unknown",
        declared="octet_stream",
        bucket="le_20mib",
        duration=20,
    )
    duplicate_accepted = accepted[0]
    terminal_zero = _terminal("00000000-0000-0000-0000-000000000001", status="ready", matches=0)
    terminal_positive = _terminal("00000000-0000-0000-0000-000000000002", status="ready", matches=3)
    terminal_orphan = _terminal("00000000-0000-0000-0000-000000000004", status="failed", matches=0)
    worker_success = _event(
        "selfie_worker_attempt_finished",
        "2026-08-03T09:00:00Z",
        event_id="17",
        search_id="00000000-0000-0000-0000-000000000001",
        job_id="00000000-0000-0000-0000-000000000011",
        attempt_id="00000000-0000-0000-0000-000000000021",
        outcome="succeeded",
        reason_code="",
        retryable=False,
        download_ms=4,
        compute_ms=8,
        total_ms=12,
    )
    worker_retry = _event(
        "selfie_worker_attempt_finished",
        "2026-08-03T09:01:00Z",
        event_id="17",
        search_id="00000000-0000-0000-0000-000000000002",
        job_id="00000000-0000-0000-0000-000000000012",
        attempt_id="00000000-0000-0000-0000-000000000022",
        outcome="failed",
        reason_code="network_interruption",
        retryable=True,
        download_ms=6,
        compute_ms=0,
        total_ms=7,
    )
    ranking = _event(
        "selfie_ranking_finished",
        "2026-08-03T09:02:00Z",
        event_id="17",
        search_id="00000000-0000-0000-0000-000000000001",
        attempt_id="00000000-0000-0000-0000-000000000021",
        outcome="succeeded",
        eligible_photo_count=5,
        eligible_face_count=7,
        matched_photo_count=0,
        load_ms=5,
        rank_ms=9,
        configuration_hash="a" * 64,
    )
    zero_ranking = _event(
        "selfie_ranking_finished",
        "2026-08-03T09:03:00Z",
        event_id="17",
        search_id="00000000-0000-0000-0000-000000000002",
        attempt_id="00000000-0000-0000-0000-000000000023",
        outcome="succeeded",
        eligible_photo_count=0,
        eligible_face_count=0,
        matched_photo_count=3,
        load_ms=None,
        rank_ms=None,
        configuration_hash="a" * 64,
    )
    malformed = '{"schema_version":1,"event":"selfie_submission_finished"'
    unknown_schema = _event(
        "selfie_submission_finished",
        "2026-08-03T10:00:00Z",
        event_id="17",
        outcome="accepted",
        reason_code="",
        search_id="00000000-0000-0000-0000-000000000099",
        actual_format="jpeg",
        declared_type="jpeg",
        source_size_bucket="le_1mib",
        duration_ms=1,
    ).replace('"schema_version":1', '"schema_version":2')
    unknown_event = _event("selfie_future_event", "2026-08-03T10:00:00Z", event_id="17")
    late = _submission(
        "00000000-0000-0000-0000-000000000100",
        occurred_at="2026-08-03T21:00:00.000Z",
    )

    summary = summarize.summarize_jsonl(
        [
            "ordinary application output",
            *accepted,
            *rejected,
            storage_failure,
            duplicate_accepted,
            terminal_zero,
            terminal_positive,
            terminal_orphan,
            terminal_zero,
            worker_success,
            worker_retry,
            worker_retry,
            ranking,
            zero_ranking,
            malformed,
            unknown_schema,
            unknown_event,
            late,
        ],
        report_date=date(2026, 8, 3),
    )
    payload = summary.to_dict()

    assert payload["report_date"] == "2026-08-03"
    assert payload["window_start"] == "2026-08-03T00:00:00+03:00"
    assert payload["window_end"] == "2026-08-04T00:00:00+03:00"
    assert payload["submissions"]["total"] == 10
    assert payload["submissions"]["outcomes"] == {
        "accepted": 3,
        "rejected": 6,
        "storage_unavailable": 1,
    }
    assert payload["submissions"]["rejection_reasons"] == {
        "missing_or_empty": 1,
        "unsupported_format": 1,
        "corrupt_image": 1,
        "source_too_large": 1,
        "normalized_too_large": 1,
        "pixel_limit_exceeded": 1,
        "storage_unavailable": 1,
    }
    assert payload["terminals"]["total"] == 3
    assert payload["terminals"]["statuses"]["ready"] == 2
    assert payload["terminals"]["statuses"]["failed"] == 1
    assert payload["terminals"]["ready_zero"] == 1
    assert payload["terminals"]["ready_positive"] == 1
    assert payload["worker_attempts"] == {
        "total": 2,
        "succeeded": 1,
        "failed": 1,
        "retryable_failed": 1,
        "failure_reasons": {"network_interruption": 1},
    }
    assert payload["durations_ms"]["submission"] == {"count": 10, "p50": 11, "p95": 20}
    assert payload["durations_ms"]["worker_total"] == {"count": 2, "p50": 7, "p95": 12}
    assert payload["durations_ms"]["cohort_load"] == {"count": 1, "p50": 5, "p95": 5}
    assert payload["cohort"] == {
        "eligible_photo_min": 0,
        "eligible_photo_max": 5,
        "eligible_face_min": 0,
        "eligible_face_max": 7,
    }
    assert payload["integrity"] == {
        "accepted_without_terminal": 1,
        "terminal_without_accepted": 1,
        "duplicate_logical_events": 3,
        "malformed_events": 1,
        "unknown_schema_or_event": 2,
        "late_events": 1,
        "ranking_without_terminal": 0,
        "terminal_without_ranking": 0,
        "ranking_terminal_mismatches": 0,
    }
    assert payload["complete"] is False
    serialized = json.dumps(payload, sort_keys=True)
    for identifier in (
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000021",
    ):
        assert identifier not in serialized


def test_empty_day_is_complete_and_recomputation_is_semantically_deterministic() -> None:
    summarize = _load_module()

    first = summarize.summarize_jsonl([], report_date=date(2026, 8, 3))
    second = summarize.summarize_jsonl([], report_date=date(2026, 8, 3))
    first_payload = asdict(first)
    second_payload = asdict(second)
    first_payload.pop("generated_at")
    second_payload.pop("generated_at")

    assert first_payload == second_payload
    assert first.complete is True
    assert first.submissions["total"] == 0


def test_valid_probe_is_ignored_but_malformed_claimed_probe_marks_integrity() -> None:
    summarize = _load_module()
    valid = _event(
        "selfie_observability_probe",
        "2026-08-03T10:00:00Z",
        probe_id="00000000-0000-0000-0000-000000000001",
    )
    malformed = json.loads(valid)
    malformed["service"] = "worker"

    summary = summarize.summarize_jsonl(
        [valid, json.dumps(malformed)], report_date=date(2026, 8, 3)
    )

    assert summary.integrity["unknown_schema_or_event"] == 1
    assert summary.submissions["total"] == 0


def test_migration_output_with_selfie_search_name_is_not_counted_as_malformed() -> None:
    summarize = _load_module()
    malformed_json = '{"schema_version":1,"event":"selfie_submission_finished"'

    summary = summarize.summarize_jsonl(
        ["Apply all migrations: admin, auth, selfie_search", malformed_json],
        report_date=date(2026, 8, 3),
    )

    assert summary.integrity["malformed_events"] == 1


def test_worker_level_prefix_is_normalized_and_truncated_json_stays_malformed() -> None:
    summarize = _load_module()
    worker = _event(
        "selfie_worker_attempt_finished",
        "2026-08-03T10:00:00Z",
        event_id="17",
        search_id="00000000-0000-0000-0000-000000000001",
        job_id="00000000-0000-0000-0000-000000000002",
        attempt_id="00000000-0000-0000-0000-000000000003",
        outcome="succeeded",
        reason_code="",
        retryable=False,
        download_ms=4,
        compute_ms=7,
        total_ms=11,
    )
    truncated = 'INFO {"schema_version":1,"event":"selfie_worker_attempt_finished"'

    summary = summarize.summarize_jsonl(
        [f"INFO {worker}", truncated],
        report_date=date(2026, 8, 3),
    )

    assert summary.worker_attempts["total"] == 1
    assert summary.integrity["malformed_events"] == 1


def test_observability_failure_markers_are_counted_but_migration_prose_is_ignored() -> None:
    summarize = _load_module()

    summary = summarize.summarize_jsonl(
        [
            "selfie_observability_emit_failed",
            "ERROR selfie_observability_emit_failed",
            "Apply all migrations: admin, auth, selfie_search",
        ],
        report_date=date(2026, 8, 3),
    )

    assert summary.integrity["malformed_events"] == 2


def test_claimed_events_with_unbounded_reason_id_or_fields_are_malformed_and_never_leak() -> None:
    summarize = _load_module()
    arbitrary_reason = _submission(
        None,
        outcome="rejected",
        reason="SECRET-ARBITRARY-REASON",
    )
    arbitrary_id = _submission("SECRET-ARBITRARY-ID")
    extra_field = json.loads(_submission("00000000-0000-0000-0000-000000000001"))
    extra_field["SECRET-EXTRA-FIELD"] = "SECRET-EXTRA-VALUE"

    summary = summarize.summarize_jsonl(
        [arbitrary_reason, arbitrary_id, json.dumps(extra_field)],
        report_date=date(2026, 8, 3),
    )
    serialized = json.dumps(summary.to_dict(), sort_keys=True)

    assert summary.integrity["malformed_events"] == 3
    assert summary.submissions["total"] == 0
    assert summary.complete is False
    assert "SECRET-" not in serialized


def test_cli_emits_one_compact_recomputed_line_and_rejects_an_invalid_date() -> None:
    valid = subprocess.run(
        [sys.executable, MODULE_PATH, "--date", "2026-08-03", "--recomputed"],
        input="ordinary output\n",
        text=True,
        capture_output=True,
        check=False,
    )
    invalid = subprocess.run(
        [sys.executable, MODULE_PATH, "--date", "not-a-date"],
        input="",
        text=True,
        capture_output=True,
        check=False,
    )

    assert valid.returncode == 0, valid.stderr
    assert valid.stdout.count("\n") == 1
    assert ": " not in valid.stdout
    assert json.loads(valid.stdout)["recomputed"] is True
    assert invalid.returncode != 0


def test_daily_summary_aggregates_expansion_and_reconciles_v2_pairs() -> None:
    summarize = _load_module()
    expanded = _search_id(101)
    no_anchor = _search_id(102)
    no_new = _search_id(103)
    disabled = _search_id(104)
    incompatible = _search_id(105)
    corpus_hash = "b" * 64
    lines = [
        _submission(expanded),
        _submission(no_anchor),
        _submission(no_new),
        _submission(disabled),
        _submission(incompatible),
        _ranking_v2(
            expanded,
            direct=1,
            expanded=2,
            final=3,
            anchors=1,
            clusters=1,
            corpus_version=7,
            configuration_hash=corpus_hash,
            expansion_ms=12,
            attempt_id=_search_id(201),
        ),
        _terminal_v2(
            expanded,
            direct=1,
            expanded=2,
            corpus_version=7,
            configuration_hash=corpus_hash,
        ),
        _ranking_v2(
            no_anchor,
            direct=1,
            anchors=0,
            clusters=0,
            corpus_version=7,
            configuration_hash=corpus_hash,
            expansion_ms=4,
            attempt_id=_search_id(202),
        ),
        _terminal_v2(
            no_anchor,
            direct=1,
            corpus_version=7,
            configuration_hash=corpus_hash,
        ),
        _ranking_v2(
            no_new,
            direct=1,
            anchors=1,
            clusters=0,
            corpus_version=7,
            configuration_hash=corpus_hash,
            expansion_ms=5,
            attempt_id=_search_id(203),
        ),
        _terminal_v2(
            no_new,
            direct=1,
            corpus_version=7,
            configuration_hash=corpus_hash,
        ),
        _ranking_v2(
            disabled,
            direct=1,
            anchors=0,
            clusters=0,
            expansion_ms=None,
            attempt_id=_search_id(204),
        ),
        _terminal_v2(disabled, direct=1),
        _ranking_v2(
            incompatible,
            outcome="incompatible",
            direct=0,
            expanded=0,
            final=0,
            anchors=0,
            clusters=0,
            attempt_id=_search_id(205),
        ),
        _terminal_v2(incompatible, status="failed", direct=0, expanded=0),
    ]

    summary = summarize.summarize_jsonl(lines, report_date=date(2026, 8, 3))
    expansion = summary.to_dict()["expansion"]

    assert expansion["eligible_searches"] == 3
    assert expansion["searches_with_cluster_photos"] == 1
    assert expansion["direct_matched_photo_count"] == 3
    assert expansion["cluster_expanded_photo_count"] == 2
    assert expansion["final_matched_photo_count"] == 5
    assert expansion["strong_anchor_count"] == 2
    assert expansion["expanded_cluster_count"] == 1
    assert expansion["added_photos"] == {"count": 3, "p50": 0, "p95": 2}
    assert expansion["expansion_ms"] == {"count": 3, "p50": 5, "p95": 12}
    assert expansion["outcomes"] == {
        "expanded": 1,
        "no_strong_anchor": 1,
        "no_new_photos": 1,
        "corpus_unavailable": 0,
        "corpus_incompatible": 1,
        "disabled": 1,
    }
    assert expansion["corpus_versions"] == {"7": 3}
    assert expansion["configuration_hashes"] == {corpus_hash: 3}
    assert expansion["searches_helped_rate"] == {"numerator": 1, "denominator": 3, "rate": 1 / 3}
    assert expansion["incremental_photo_rate"] == {"numerator": 2, "denominator": 5, "rate": 2 / 5}
    assert summary.complete is True


def test_search_unavailable_pair_clears_identity_without_mismatch() -> None:
    summarize = _load_module()
    search_id = _search_id(250)
    lines = [
        _submission(search_id),
        _ranking_v2(
            search_id,
            direct=0,
            expanded=0,
            final=0,
            eligible_photo_count=0,
            eligible_face_count=0,
            corpus_version=None,
            configuration_hash=None,
            expansion_ms=None,
            cluster_outcome="no_strong_anchor",
            attempt_id=_search_id(251),
        ),
        _terminal_v2(
            search_id,
            status="search_unavailable",
            direct=0,
            expanded=0,
            corpus_version=None,
            configuration_hash=None,
        ),
    ]

    summary = summarize.summarize_jsonl(lines, report_date=date(2026, 8, 3))

    assert summary.complete is True
    assert summary.expansion["eligible_searches"] == 0
    assert summary.expansion["outcomes"]["no_strong_anchor"] == 1
    assert summary.integrity["ranking_terminal_mismatches"] == 0
    assert summary.integrity["ranking_without_terminal"] == 0
    assert summary.integrity["terminal_without_ranking"] == 0


def test_historical_v1_ranking_and_terminal_metrics_are_not_available() -> None:
    summarize = _load_module()
    search_id = _search_id(301)
    ranking = _event(
        "selfie_ranking_finished",
        "2026-08-03T10:00:00Z",
        event_id="17",
        search_id=search_id,
        attempt_id=_search_id(302),
        outcome="succeeded",
        eligible_photo_count=5,
        eligible_face_count=7,
        matched_photo_count=1,
        load_ms=5,
        rank_ms=8,
        configuration_hash="a" * 64,
    )
    terminal = _terminal(search_id, status="ready", matches=1)

    expansion = summarize.summarize_jsonl(
        [_submission(search_id), ranking, terminal],
        report_date=date(2026, 8, 3),
    ).to_dict()["expansion"]

    assert expansion["eligible_searches"] == "not_available"
    assert expansion["cluster_expanded_photo_count"] == "not_available"
    assert expansion["searches_helped_rate"] == "not_available"


def test_v2_reconciliation_mismatch_duplicate_and_malformed_event_make_summary_incomplete() -> None:
    summarize = _load_module()
    search_id = _search_id(401)
    ranking = _ranking_v2(
        search_id,
        direct=1,
        expanded=2,
        final=3,
        anchors=1,
        clusters=1,
        corpus_version=7,
        configuration_hash="b" * 64,
        expansion_ms=12,
        attempt_id=_search_id(402),
    )
    malformed = json.loads(ranking)
    malformed["cluster_configuration_hash"] = "SECRET-SENTINEL"
    mismatch = _terminal_v2(
        search_id,
        direct=1,
        expanded=1,
        matched=2,
        corpus_version=7,
        configuration_hash="b" * 64,
    )
    summary = summarize.summarize_jsonl(
        [
            _submission(search_id),
            ranking,
            ranking,
            mismatch,
            json.dumps(malformed),
        ],
        report_date=date(2026, 8, 3),
    )

    assert summary.complete is False
    assert summary.integrity["duplicate_logical_events"] == 1
    assert summary.integrity["ranking_terminal_mismatches"] == 1
    assert summary.integrity["malformed_events"] == 1
    assert "SECRET-SENTINEL" not in json.dumps(summary.to_dict(), sort_keys=True)
