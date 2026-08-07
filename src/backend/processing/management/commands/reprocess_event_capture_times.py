from django.core.management.base import BaseCommand, CommandError
from picflow.models import Event

from processing.services.enrollment import (
    CAPTURE_METADATA_PROCESSOR_VERSION,
    CaptureTimeReprocessingTarget,
    enroll_event_capture_time_reprocessing,
    validate_capture_time_reprocessing_enrollment,
)

EXPECTED_EVENT_ID = 9
EXPECTED_EVENT_NAME = "Cyclingrace Вечернее Садовое"
EXPECTED_EVENT_TIMEZONE = "Europe/Moscow"
EXPECTED_EVENT_PHOTO_COUNT = 17_043
EXPECTED_CAPTURE_METADATA_CONFIGURATION: dict[str, object] = {
    "retry_policy": {
        "max_attempts": 3,
        "base_backoff_seconds": 30,
        "max_backoff_seconds": 300,
        "jitter_seconds": 5,
        "lease_max_seconds": 300,
    },
    "max_cohort_size": 20,
    "report_max_bytes": 262_144,
    "report_row_limits": {"max_warnings": 8, "max_warning_chars": 32},
    "worker": {
        "api_response_max_bytes": 16_384,
        "concurrency": 1,
        "heartbeat_interval_seconds": 30,
        "lease_duration_seconds": 120,
        "max_input_bytes": 52_428_800,
        "max_pixels": 100_000_000,
        "poll_min_delay_seconds": 5,
        "terminal_result_max_bytes": 8_192,
    },
    "capture_metadata": {
        "date_field_precedence": ["DateTimeOriginal", "DateTimeDigitized", "DateTime"],
        "normalization": "utc_explicit_offset_or_event_timezone",
        "event_timezone": "Europe/Moscow",
    },
}


class Command(BaseCommand):
    help = "Dry-run or enroll the fixed event-9 capture-time reprocessing cohort."

    def add_arguments(self, parser):
        parser.add_argument("--event-id", required=True, type=int)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        target = _approved_target()
        event_id = options["event_id"]
        if event_id != target.event_id:
            raise CommandError("this command only permits event ID 9")
        try:
            event = Event.objects.get(pk=target.event_id)
        except Event.DoesNotExist as error:
            raise CommandError("approved event 9 does not exist") from error
        photo_count = _validate_event(event, target=target)
        if not options["apply"]:
            self.stdout.write(
                "dry_run "
                f"event_id={event.id} photo_count={photo_count} "
                f"processor_version={CAPTURE_METADATA_PROCESSOR_VERSION}"
            )
            return
        try:
            enrollment = enroll_event_capture_time_reprocessing(event, target=target)
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            "applied "
            f"event_id={event.id} photo_count={enrollment.photo_count} "
            f"processor_version={CAPTURE_METADATA_PROCESSOR_VERSION} "
            f"created_jobs={enrollment.created_job_count} "
            f"existing_jobs={enrollment.existing_job_count} runs={enrollment.run_count}"
        )


def _approved_target() -> CaptureTimeReprocessingTarget:
    return CaptureTimeReprocessingTarget(
        event_id=EXPECTED_EVENT_ID,
        event_name=EXPECTED_EVENT_NAME,
        timezone_name=EXPECTED_EVENT_TIMEZONE,
        photo_count=EXPECTED_EVENT_PHOTO_COUNT,
        configuration=EXPECTED_CAPTURE_METADATA_CONFIGURATION,
    )


def _validate_event(event: Event, *, target: CaptureTimeReprocessingTarget) -> int:
    try:
        validate_capture_time_reprocessing_enrollment(event, target=target)
    except ValueError as error:
        raise CommandError(str(error)) from error
    return target.photo_count
