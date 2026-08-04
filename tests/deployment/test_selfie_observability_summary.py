from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "deploy" / "selfie-observability" / "summarize.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("selfie_summary", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _event(name: str, occurred_at: str, **fields: object) -> str:
    service = "worker" if name == "selfie_worker_attempt_finished" else "web"
    return json.dumps(
        {
            "schema_version": 1,
            "event": name,
            "occurred_at": occurred_at,
            "service": service,
            "environment": "staging",
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
