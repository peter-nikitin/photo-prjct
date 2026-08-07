import json
from collections import defaultdict
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Count, DateTimeField, F, IntegerField, Max, Min, Q
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, ExtractHour, Substr
from picflow.models import Event, Photo

from processing.management.commands.reprocess_event_capture_times import (
    EXPECTED_EVENT_ID,
    EXPECTED_EVENT_NAME,
    EXPECTED_EVENT_PHOTO_COUNT,
    EXPECTED_EVENT_TIMEZONE,
)
from processing.models import PhotoProcessingState, ProcessingAttempt, ProcessingJob
from processing.services.enrollment import CONTRACT_VERSION

_CAPTURE_METADATA_PROCESSOR = "capture_metadata"
_CAPTURE_TIME_REPORT_PROCESSOR_VERSION = 2
_TERMINAL_JOB_STATUSES = (
    ProcessingJob.Status.SUCCEEDED,
    ProcessingJob.Status.FAILED,
    ProcessingJob.Status.CANCELLED,
)
_KNOWN_TIMEZONE_STATES = ("explicit", "event_timezone", "not_applicable", "inferred_none")
_KNOWN_WARNINGS = (
    "capture_time_conflicting",
    "capture_time_malformed",
    "capture_time_malformed_offset",
    "capture_time_missing",
    "capture_time_timezone_ambiguous",
)


class Command(BaseCommand):
    help = "Report privacy-safe completion aggregates for the fixed event-9 capture-time cohort."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--event-id", required=True, type=int)
        parser.add_argument("--processor-version", required=True, type=int)

    def handle(self, *args, **options) -> None:
        if options["event_id"] != EXPECTED_EVENT_ID:
            raise CommandError("this command only permits event ID 9")
        if options["processor_version"] != _CAPTURE_TIME_REPORT_PROCESSOR_VERSION:
            raise CommandError("this command only permits processor version 2")
        try:
            event = Event.objects.get(pk=EXPECTED_EVENT_ID)
        except Event.DoesNotExist as error:
            raise CommandError("approved event 9 does not exist") from error

        report = _build_report(event)
        self.stdout.write(
            json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )


def _build_report(event: Event) -> dict[str, object]:
    jobs = ProcessingJob.objects.filter(
        event=event,
        contract_version=CONTRACT_VERSION,
        processor_type=_CAPTURE_METADATA_PROCESSOR,
        processor_version=_CAPTURE_TIME_REPORT_PROCESSOR_VERSION,
    )
    job_counts = jobs.aggregate(
        version_job_count=Count("id"),
        terminal_jobs=Count("id", filter=Q(status__in=_TERMINAL_JOB_STATUSES)),
        terminal_failures=Count(
            "id", filter=Q(status__in=(ProcessingJob.Status.FAILED, ProcessingJob.Status.CANCELLED))
        ),
    )
    event_photo_count = Photo.objects.filter(event=event).count()
    accepted = _current_accepted_attempts(jobs)
    capture_time = KeyTextTransform("capture_time", "result")
    missing_capture_time = Q(result__capture_time=None) | Q(result__capture_time__isnull=True)
    accepted_counts = accepted.aggregate(
        accepted_results=Count("id"),
        missing_capture_times=Count("id", filter=missing_capture_time),
        utc_min=Min(capture_time),
        utc_max=Max(capture_time),
    )
    counts = {
        "event_photo_count": event_photo_count,
        "version_job_count": job_counts["version_job_count"],
        "terminal_jobs": job_counts["terminal_jobs"],
        "accepted_results": accepted_counts["accepted_results"],
        "accepted_non_null_capture_times": (
            accepted_counts["accepted_results"] - accepted_counts["missing_capture_times"]
        ),
        "missing_capture_times": accepted_counts["missing_capture_times"],
        "terminal_failures": job_counts["terminal_failures"],
    }
    timezone_states = _timezone_state_counts(accepted)
    hourly_distribution, source_mode_comparison = _hour_comparison(
        accepted, timezone_name=event.timezone_name
    )
    warning_counts = _warning_counts(event_id=event.id)
    expected = EXPECTED_EVENT_PHOTO_COUNT
    no_three_hour_split = all(
        mode["hour_offset_counts"].get("3", 0) == 0 and mode["hour_offset_counts"].get("21", 0) == 0
        for mode in source_mode_comparison.values()
    )
    source_comparison_count = sum(mode["result_count"] for mode in source_mode_comparison.values())
    checks = {
        "event_name": event.name == EXPECTED_EVENT_NAME,
        "event_published": event.publication_status == Event.PublicationStatus.PUBLISHED,
        "event_timezone": event.timezone_name == EXPECTED_EVENT_TIMEZONE,
        "event_photo_count": counts["event_photo_count"] == expected,
        "version_job_count": counts["version_job_count"] == expected,
        "terminal_jobs": counts["terminal_jobs"] == expected,
        "accepted_non_null_capture_times": counts["accepted_non_null_capture_times"] == expected,
        "accepted_results": counts["accepted_results"] == expected,
        "missing_capture_times": counts["missing_capture_times"] == 0,
        "terminal_failures": counts["terminal_failures"] == 0,
        "inferred_none": timezone_states["inferred_none"] == 0,
        "known_timezone_states": timezone_states["unexpected"] == 0,
        "known_warnings": warning_counts["unexpected_warning"] == 0,
        "source_hour_comparison_complete": source_comparison_count
        == counts["accepted_non_null_capture_times"],
        "no_three_hour_split": no_three_hour_split,
    }
    status = "accepted" if all(checks.values()) else "incomplete"
    return {
        "checks": checks,
        "counts": counts,
        "event_id": EXPECTED_EVENT_ID,
        "event_local_hourly_distribution": hourly_distribution,
        "event_timezone": event.timezone_name,
        "processor_version": _CAPTURE_TIME_REPORT_PROCESSOR_VERSION,
        "source_mode_comparison": source_mode_comparison,
        "status": status,
        "timezone_states": timezone_states,
        "utc_range": {"min": accepted_counts["utc_min"], "max": accepted_counts["utc_max"]},
        "warning_counts": warning_counts,
    }


def _current_accepted_attempts(jobs):
    return ProcessingAttempt.objects.filter(
        job__in=jobs,
        status=ProcessingAttempt.Status.SUCCEEDED,
        accepted=True,
        accepted_states__processor_type=_CAPTURE_METADATA_PROCESSOR,
        accepted_states__current_job=F("job"),
        accepted_states__accepted_attempt=F("pk"),
    )


def _timezone_state_counts(accepted) -> dict[str, int]:
    counts = {state: 0 for state in _KNOWN_TIMEZONE_STATES}
    counts["unexpected"] = 0
    for row in (
        accepted.annotate(timezone_state=KeyTextTransform("timezone_state", "result"))
        .values("timezone_state")
        .annotate(count=Count("id"))
        .order_by("timezone_state")
    ):
        state = row["timezone_state"]
        key = state if state in _KNOWN_TIMEZONE_STATES else "unexpected"
        counts[key] += row["count"]
    return counts


def _hour_comparison(accepted, *, timezone_name: str | None) -> tuple[list[int], dict[str, dict]]:
    hourly_distribution = [0] * 24
    comparison: defaultdict[str, dict] = defaultdict(
        lambda: {"result_count": 0, "hour_offset_counts": defaultdict(int)}
    )
    if timezone_name != EXPECTED_EVENT_TIMEZONE:
        return hourly_distribution, {}
    capture_time = KeyTextTransform("capture_time", "result")
    source_value = KeyTextTransform("source_value", "result")
    rows = (
        accepted.filter(result__capture_time__isnull=False)
        .annotate(
            timezone_state=KeyTextTransform("timezone_state", "result"),
            source_hour=Cast(Substr(source_value, 12, 2), IntegerField()),
            event_local_hour=ExtractHour(
                Cast(capture_time, DateTimeField()), tzinfo=ZoneInfo(EXPECTED_EVENT_TIMEZONE)
            ),
        )
        .values("timezone_state", "source_hour", "event_local_hour")
        .annotate(count=Count("id"))
        .order_by("timezone_state", "source_hour", "event_local_hour")
    )
    for row in rows:
        source_hour = row["source_hour"]
        event_local_hour = row["event_local_hour"]
        count = row["count"]
        if not isinstance(event_local_hour, int) or not 0 <= event_local_hour < 24:
            continue
        hourly_distribution[event_local_hour] += count
        mode = row["timezone_state"]
        mode_name = mode if mode in {"explicit", "event_timezone"} else "unexpected"
        if not isinstance(source_hour, int) or not 0 <= source_hour < 24:
            continue
        comparison[mode_name]["result_count"] += count
        offset = str((event_local_hour - source_hour) % 24)
        comparison[mode_name]["hour_offset_counts"][offset] += count
    return hourly_distribution, {
        mode: {
            "result_count": values["result_count"],
            "hour_offset_counts": dict(sorted(values["hour_offset_counts"].items())),
        }
        for mode, values in sorted(comparison.items())
    }


def _warning_counts(*, event_id: int) -> dict[str, int]:
    counts = {warning: 0 for warning in _KNOWN_WARNINGS}
    counts["unexpected_warning"] = 0
    attempt_table = ProcessingAttempt._meta.db_table
    job_table = ProcessingJob._meta.db_table
    state_table = PhotoProcessingState._meta.db_table
    sql = f"""
        SELECT warning.code, COUNT(*)
        FROM {attempt_table} AS attempt
        INNER JOIN {job_table} AS job ON job.id = attempt.job_id
        INNER JOIN {state_table} AS state
          ON state.accepted_attempt_id = attempt.id
          AND state.current_job_id = attempt.job_id
          AND state.processor_type = %s
        CROSS JOIN LATERAL jsonb_array_elements_text(
          COALESCE(attempt.result -> 'warnings', '[]'::jsonb)
        ) AS warning(code)
        WHERE job.event_id = %s
          AND job.contract_version = %s
          AND job.processor_type = %s
          AND job.processor_version = %s
          AND attempt.status = %s
          AND attempt.accepted = TRUE
        GROUP BY warning.code
        ORDER BY warning.code
    """
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            [
                _CAPTURE_METADATA_PROCESSOR,
                event_id,
                CONTRACT_VERSION,
                _CAPTURE_METADATA_PROCESSOR,
                _CAPTURE_TIME_REPORT_PROCESSOR_VERSION,
                ProcessingAttempt.Status.SUCCEEDED,
            ],
        )
        for warning, count in cursor.fetchall():
            key = warning if warning in _KNOWN_WARNINGS else "unexpected_warning"
            counts[key] += count
    return counts
