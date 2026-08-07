import json
from datetime import date
from importlib import import_module, reload
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import (
    EventProcessingRun,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    capture_metadata_configuration,
    enroll_event_capture_time_reprocessing,
)


class CaptureTimeCommandTests(TestCase):
    expected_event_name = "Cyclingrace Вечернее Садовое"

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="capture-time-command-owner")
        self.event = Event.objects.create(
            id=9,
            name=self.expected_event_name,
            slug="cyclingrace-evening-garden",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 1),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            timezone_name="Europe/Moscow",
        )
        self.photos = [self.private_photo(f"capture-{index}") for index in range(2)]
        self.other_event = Event.objects.create(
            id=10,
            name="Another event",
            slug="another-event",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 1),
            city="Moscow",
            timezone_name="Europe/Moscow",
        )
        self.other_photo = self.private_photo("other-event", event=self.other_event)

    def private_photo(self, identifier: str, *, event: Event | None = None) -> Photo:
        return Photo.objects.create(
            id=identifier,
            event=event or self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/private/{identifier}",
            original_filename=f"{identifier}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def _create_accepted_version_one_attempt(self, photo: Photo) -> ProcessingAttempt:
        configuration = {"capture_metadata": {"normalization": "utc_assume_utc_if_missing"}}
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=1,
            configuration=configuration,
            configuration_hash="1" * 64,
            report={"old": "report"},
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=1,
            configuration=configuration,
            configuration_hash="1" * 64,
            input_fingerprint={"old": "fingerprint"},
            status=ProcessingJob.Status.SUCCEEDED,
        )
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=1,
            configuration=configuration,
            input_fingerprint={"old": "fingerprint"},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            result={"capture_time": "2026-01-01T12:00:00Z", "timezone_state": "inferred_none"},
            result_hash="old-result-hash",
            accepted=True,
        )
        state = PhotoProcessingState.objects.get(photo=photo, processor_type="capture_metadata")
        state.status = PhotoProcessingState.Status.SUCCEEDED
        state.current_run = run
        state.current_job = job
        state.current_attempt = attempt
        state.accepted_attempt = attempt
        state.succeeded_at = timezone.now()
        state.save()
        return attempt

    def _enroll_version_two_jobs(self) -> list[ProcessingJob]:
        with patch(
            "processing.management.commands.reprocess_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
            len(self.photos),
        ):
            call_command("reprocess_event_capture_times", event_id=9, apply=True)
        return list(
            ProcessingJob.objects.filter(
                event=self.event,
                contract_version=1,
                processor_type="capture_metadata",
                processor_version=2,
            ).order_by("photo_id")
        )

    def _complete_version_two_job(
        self,
        job: ProcessingJob,
        *,
        result: dict[str, object],
        failed: bool = False,
    ) -> None:
        now = timezone.now()
        attempt = ProcessingAttempt.objects.create(
            event=job.event,
            run=job.run,
            job=job,
            photo=job.photo,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=2,
            configuration=job.configuration,
            input_fingerprint=job.input_fingerprint,
            status=(
                ProcessingAttempt.Status.FAILED if failed else ProcessingAttempt.Status.SUCCEEDED
            ),
            terminal_at=now,
            result=result,
            accepted=not failed,
        )
        job.status = ProcessingJob.Status.FAILED if failed else ProcessingJob.Status.SUCCEEDED
        job.completed_at = now
        job.save(update_fields=["status", "completed_at"])
        state, _ = PhotoProcessingState.objects.get_or_create(
            photo=job.photo,
            processor_type="capture_metadata",
            defaults={"current_run": job.run, "current_job": job},
        )
        state.status = (
            PhotoProcessingState.Status.FAILED if failed else PhotoProcessingState.Status.SUCCEEDED
        )
        state.current_attempt = attempt
        state.accepted_attempt = None if failed else attempt
        state.failed_at = now if failed else None
        state.succeeded_at = None if failed else now
        state.save(
            update_fields=[
                "status",
                "current_attempt",
                "accepted_attempt",
                "failed_at",
                "succeeded_at",
                "updated_at",
            ]
        )

    def _capture_result(
        self,
        *,
        capture_time: str | None,
        timezone_state: str,
        source_value: str | None,
        source_offset: str | None,
        warnings: list[str],
    ) -> dict[str, object]:
        return {
            "capture_time": capture_time,
            "source_field": "DateTimeOriginal" if capture_time is not None else None,
            "timezone_state": timezone_state,
            "source_value": source_value,
            "source_offset": source_offset,
            "event_timezone": "Europe/Moscow",
            "warnings": warnings,
        }

    def test_missing_event_id_is_rejected_by_the_reprocessing_command(self) -> None:
        """A production change that drops the required scope guard must fail this test."""
        with self.assertRaisesRegex(CommandError, "--event-id"):
            call_command("reprocess_event_capture_times")

    @patch(
        "processing.management.commands.reprocess_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
        2,
    )
    def test_default_dry_run_reports_only_bounded_values_and_writes_nothing(self) -> None:
        """A production change that performs enrollment without --apply must fail this test."""
        output = StringIO()

        call_command("reprocess_event_capture_times", event_id=9, stdout=output)

        self.assertEqual(EventProcessingRun.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.count(), 0)
        self.assertEqual(ProcessingAttempt.objects.count(), 0)
        self.assertEqual(
            output.getvalue(),
            "dry_run event_id=9 photo_count=2 processor_version=2\n",
        )
        self.assertNotIn(self.photos[0].original_key, output.getvalue())
        self.assertNotIn(self.photos[0].original_filename, output.getvalue())

    def test_non_event_nine_scope_is_rejected_before_event_lookup(self) -> None:
        """A production change that permits a different event ID must fail this test."""
        with self.assertRaisesRegex(CommandError, "event ID 9"):
            call_command("reprocess_event_capture_times", event_id=self.other_event.id)

        self.assertEqual(ProcessingJob.objects.count(), 0)

    @patch(
        "processing.management.commands.reprocess_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
        2,
    )
    def test_invalid_event_gates_refuse_apply_before_writes(self) -> None:
        """A production change that bypasses any approved event-9 guard must fail this test."""
        invalidations = (
            ("name", "different event"),
            ("publication_status", Event.PublicationStatus.DRAFT),
            ("timezone_name", "Europe/London"),
        )
        for field, value in invalidations:
            with self.subTest(field=field):
                setattr(self.event, field, value)
                self.event.save(update_fields=[field])
                with self.assertRaises(CommandError):
                    call_command("reprocess_event_capture_times", event_id=9, apply=True)
                self.assertEqual(ProcessingJob.objects.count(), 0)
                setattr(
                    self.event,
                    field,
                    {
                        "name": self.expected_event_name,
                        "publication_status": Event.PublicationStatus.PUBLISHED,
                        "timezone_name": "Europe/Moscow",
                    }[field],
                )
                self.event.save(update_fields=[field])

        with patch(
            "processing.management.commands.reprocess_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
            3,
        ):
            with self.assertRaises(CommandError):
                call_command("reprocess_event_capture_times", event_id=9, apply=True)
        self.assertEqual(ProcessingJob.objects.count(), 0)

    @patch(
        "processing.management.commands.reprocess_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
        2,
    )
    def test_existing_wrong_version_two_configuration_is_rejected_before_writes(self) -> None:
        """A production change that accepts a mixed v2 configuration must fail this test."""
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=2,
            configuration={"wrong": "configuration"},
            configuration_hash="2" * 64,
        )
        ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=self.photos[0],
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=2,
            configuration=run.configuration,
            configuration_hash=run.configuration_hash,
            input_fingerprint={"wrong": "fingerprint"},
        )

        with self.assertRaisesRegex(CommandError, "configuration"):
            call_command("reprocess_event_capture_times", event_id=9, apply=True)

        self.assertEqual(ProcessingJob.objects.count(), 1)

    def test_divergent_capture_configuration_builder_is_refused_before_writes(self) -> None:
        """A target copied from the mutable builder would allow an unapproved v2 identity."""
        command_module = import_module(
            "processing.management.commands.reprocess_event_capture_times"
        )
        divergent_configuration = capture_metadata_configuration("Europe/Moscow")
        worker = divergent_configuration["worker"]
        assert isinstance(worker, dict)
        worker["lease_duration_seconds"] = 121
        try:
            with patch(
                "processing.services.enrollment.capture_metadata_configuration",
                return_value=divergent_configuration,
            ):
                reload(command_module)
                with patch.object(command_module, "EXPECTED_EVENT_PHOTO_COUNT", 2):
                    with self.assertRaisesRegex(CommandError, "configuration"):
                        call_command("reprocess_event_capture_times", event_id=9, apply=True)
        finally:
            reload(command_module)

        self.assertEqual(ProcessingJob.objects.count(), 0)

    @patch(
        "processing.management.commands.reprocess_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
        2,
    )
    def test_apply_rechecks_every_gate_after_the_event_row_is_locked(self) -> None:
        """A preflight-only gate could enroll after the approved event identity changes."""

        def diverge_after_preflight(event: Event, *args, **kwargs):
            Event.objects.filter(pk=event.pk).update(name="changed after preflight")
            return enroll_event_capture_time_reprocessing(event, *args, **kwargs)

        with patch(
            "processing.management.commands.reprocess_event_capture_times"
            ".enroll_event_capture_time_reprocessing",
            side_effect=diverge_after_preflight,
        ):
            with self.assertRaisesRegex(CommandError, "name"):
                call_command("reprocess_event_capture_times", event_id=9, apply=True)

        self.assertEqual(ProcessingJob.objects.count(), 0)

    @patch(
        "processing.management.commands.reprocess_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
        2,
    )
    def test_apply_counts_the_materialized_locked_cohort_before_writing(self) -> None:
        """A count before cohort locking could miss a deletion and enqueue an incomplete target."""
        original_select_for_update = Photo.objects.select_for_update
        deleted = False

        def delete_before_cohort_materialization(*args, **kwargs):
            nonlocal deleted
            if not deleted:
                deleted = True
                PhotoProcessingState.objects.filter(photo=self.photos[1]).delete()
                Photo.objects.filter(pk=self.photos[1].pk).delete()
            return original_select_for_update(*args, **kwargs)

        with patch.object(
            Photo.objects,
            "select_for_update",
            side_effect=delete_before_cohort_materialization,
        ):
            with self.assertRaisesRegex(CommandError, "photo count"):
                call_command("reprocess_event_capture_times", event_id=9, apply=True)

        self.assertEqual(ProcessingJob.objects.count(), 0)

    @patch(
        "processing.management.commands.reprocess_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
        2,
    )
    def test_apply_enrolls_the_exact_cohort_idempotently_and_keeps_v1_attempt_immutable(
        self,
    ) -> None:
        """A production change that duplicates jobs or mutates v1 evidence must fail this test."""
        old_attempt = self._create_accepted_version_one_attempt(self.photos[0])
        old_attempt_values = ProcessingAttempt.objects.filter(pk=old_attempt.pk).values().get()
        old_report = old_attempt.run.report.copy()

        call_command("reprocess_event_capture_times", event_id=9, apply=True)

        version_two_jobs = ProcessingJob.objects.filter(
            event=self.event,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=2,
        )
        self.assertEqual(version_two_jobs.count(), 2)
        self.assertEqual(
            set(version_two_jobs.values_list("photo_id", flat=True)),
            {photo.id for photo in self.photos},
        )
        self.assertEqual(
            set(
                ProcessingJob.objects.filter(run__in=version_two_jobs.values("run_id"))
                .filter(processor_type="capture_metadata", processor_version=2)
                .values_list("photo_id", flat=True)
            ),
            {photo.id for photo in self.photos},
        )
        for photo in self.photos:
            state = PhotoProcessingState.objects.get(photo=photo, processor_type="capture_metadata")
            self.assertEqual(state.status, PhotoProcessingState.Status.QUEUED)
            self.assertEqual(state.current_job.processor_version, 2)
            self.assertIsNone(state.current_attempt)
            self.assertIsNone(state.accepted_attempt)
        old_attempt.refresh_from_db()
        self.assertEqual(
            ProcessingAttempt.objects.filter(pk=old_attempt.pk).values().get(), old_attempt_values
        )
        self.assertEqual(old_attempt.run.report, old_report)

        job_count = version_two_jobs.count()
        run_count = EventProcessingRun.objects.filter(
            event=self.event,
            processor_type="capture_metadata",
            processor_version=2,
        ).count()
        call_command("reprocess_event_capture_times", event_id=9, apply=True)

        self.assertEqual(version_two_jobs.count(), job_count)
        self.assertEqual(
            EventProcessingRun.objects.filter(
                event=self.event,
                processor_type="capture_metadata",
                processor_version=2,
            ).count(),
            run_count,
        )
        self.assertEqual(
            ProcessingJob.objects.filter(photo=self.other_photo, processor_version=2).count(),
            0,
        )

    def test_report_rejects_any_scope_other_than_event_nine_capture_metadata_version_two(
        self,
    ) -> None:
        """A report widened beyond the approved immutable cohort must fail this test."""
        with self.assertRaisesRegex(CommandError, "--event-id"):
            call_command("report_event_capture_times", processor_version=2)
        with self.assertRaisesRegex(CommandError, "event ID 9"):
            call_command(
                "report_event_capture_times", event_id=self.other_event.id, processor_version=2
            )
        with self.assertRaisesRegex(CommandError, "version 2"):
            call_command("report_event_capture_times", event_id=9, processor_version=1)

    def test_report_scope_remains_version_two_after_the_current_processor_version_changes(
        self,
    ) -> None:
        """A future processor bump must not widen this historical acceptance report."""
        command_module = import_module("processing.management.commands.report_event_capture_times")
        try:
            with patch("processing.services.enrollment.CAPTURE_METADATA_PROCESSOR_VERSION", 3):
                reload(command_module)
                with self.assertRaisesRegex(CommandError, "version 2"):
                    call_command("report_event_capture_times", event_id=9, processor_version=3)
                output = StringIO()
                call_command(
                    "report_event_capture_times", event_id=9, processor_version=2, stdout=output
                )
        finally:
            reload(command_module)

        self.assertEqual(json.loads(output.getvalue())["processor_version"], 2)

    @patch(
        "processing.management.commands.report_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
        2,
    )
    def test_report_is_deterministic_bounded_and_omits_private_values(self) -> None:
        """A report that exposes a row-level source value or unstable aggregate order must fail."""
        jobs = self._enroll_version_two_jobs()
        source_value = "2026:01:01 10:00:00"
        self._complete_version_two_job(
            jobs[0],
            result=self._capture_result(
                capture_time="2026-01-01T07:00:00Z",
                timezone_state="explicit",
                source_value=source_value,
                source_offset="+03:00",
                warnings=["capture_time_conflicting"],
            ),
        )
        self._complete_version_two_job(
            jobs[1], result={"error_detail": self.photos[1].original_filename}, failed=True
        )
        other_job = ProcessingJob.objects.create(
            event=self.other_event,
            run=EventProcessingRun.objects.create(
                event=self.other_event,
                contract_version=1,
                processor_type="capture_metadata",
                processor_version=2,
                configuration={},
                configuration_hash="3" * 64,
            ),
            photo=self.other_photo,
            contract_version=1,
            processor_type="capture_metadata",
            processor_version=2,
            configuration={},
            configuration_hash="3" * 64,
            input_fingerprint={"original_key": self.other_photo.original_key},
        )
        self._complete_version_two_job(
            other_job,
            result=self._capture_result(
                capture_time="2026-01-01T07:00:00Z",
                timezone_state="explicit",
                source_value=source_value,
                source_offset="+03:00",
                warnings=[],
            ),
        )

        first_output = StringIO()
        second_output = StringIO()
        before_counts = {
            "runs": EventProcessingRun.objects.count(),
            "jobs": ProcessingJob.objects.count(),
            "attempts": ProcessingAttempt.objects.count(),
            "states": PhotoProcessingState.objects.count(),
        }
        call_command(
            "report_event_capture_times", event_id=9, processor_version=2, stdout=first_output
        )
        call_command(
            "report_event_capture_times", event_id=9, processor_version=2, stdout=second_output
        )

        output = first_output.getvalue()
        report = json.loads(output)
        self.assertEqual(output, second_output.getvalue())
        self.assertEqual(
            {
                "runs": EventProcessingRun.objects.count(),
                "jobs": ProcessingJob.objects.count(),
                "attempts": ProcessingAttempt.objects.count(),
                "states": PhotoProcessingState.objects.count(),
            },
            before_counts,
        )
        self.assertLessEqual(len(output.encode()), 262_144)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(
            report["counts"],
            {
                "accepted_non_null_capture_times": 1,
                "accepted_results": 1,
                "event_photo_count": 2,
                "missing_capture_times": 0,
                "terminal_failures": 1,
                "terminal_jobs": 2,
                "version_job_count": 2,
            },
        )
        self.assertEqual(
            report["utc_range"], {"max": "2026-01-01T07:00:00Z", "min": "2026-01-01T07:00:00Z"}
        )
        self.assertEqual(report["timezone_states"]["explicit"], 1)
        self.assertEqual(report["warning_counts"]["capture_time_conflicting"], 1)
        for private_value in (
            self.photos[0].original_key,
            self.photos[0].original_filename,
            self.other_photo.original_key,
            self.other_photo.original_filename,
            source_value,
            "+03:00",
        ):
            self.assertNotIn(private_value, output)

    @patch(
        "processing.management.commands.report_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
        2,
    )
    def test_report_marks_the_exact_terminal_non_null_cohort_accepted(self) -> None:
        """A report that accepts a missing, failed, or three-hour-shifted cohort must fail."""
        jobs = self._enroll_version_two_jobs()
        for job, source_hour, timezone_state, source_offset in (
            (jobs[0], "10", "explicit", "+03:00"),
            (jobs[1], "11", "event_timezone", None),
        ):
            self._complete_version_two_job(
                job,
                result=self._capture_result(
                    capture_time=f"2026-01-01T{int(source_hour) - 3:02d}:00:00Z",
                    timezone_state=timezone_state,
                    source_value=f"2026:01:01 {source_hour}:00:00",
                    source_offset=source_offset,
                    warnings=[],
                ),
            )

        output = StringIO()
        call_command("report_event_capture_times", event_id=9, processor_version=2, stdout=output)

        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(report["counts"]["accepted_non_null_capture_times"], 2)
        self.assertEqual(report["counts"]["terminal_failures"], 0)
        self.assertEqual(report["counts"]["missing_capture_times"], 0)
        self.assertEqual(report["timezone_states"]["inferred_none"], 0)
        self.assertEqual(
            report["source_mode_comparison"],
            {
                "event_timezone": {"hour_offset_counts": {"0": 1}, "result_count": 1},
                "explicit": {"hour_offset_counts": {"0": 1}, "result_count": 1},
            },
        )

    @patch(
        "processing.management.commands.report_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
        2,
    )
    def test_report_marks_a_terminal_missing_time_result_incomplete(self) -> None:
        """A report that treats a null accepted capture time as complete must fail."""
        jobs = self._enroll_version_two_jobs()
        self._complete_version_two_job(
            jobs[0],
            result=self._capture_result(
                capture_time="2026-01-01T07:00:00Z",
                timezone_state="event_timezone",
                source_value="2026:01:01 10:00:00",
                source_offset=None,
                warnings=[],
            ),
        )
        self._complete_version_two_job(
            jobs[1],
            result=self._capture_result(
                capture_time=None,
                timezone_state="not_applicable",
                source_value=None,
                source_offset=None,
                warnings=["capture_time_missing"],
            ),
        )

        output = StringIO()
        call_command("report_event_capture_times", event_id=9, processor_version=2, stdout=output)

        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["counts"]["missing_capture_times"], 1)
        self.assertEqual(report["warning_counts"]["capture_time_missing"], 1)

    @patch(
        "processing.management.commands.report_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
        2,
    )
    def test_report_rejects_a_three_hour_source_to_event_local_split(self) -> None:
        """A report that accepts the historic JPEG/MPO offset split must fail."""
        jobs = self._enroll_version_two_jobs()
        outcomes = ((jobs[0], "2026-01-01T07:00:00Z"), (jobs[1], "2026-01-01T10:00:00Z"))
        for job, capture_time in outcomes:
            self._complete_version_two_job(
                job,
                result=self._capture_result(
                    capture_time=capture_time,
                    timezone_state="event_timezone",
                    source_value="2026:01:01 10:00:00",
                    source_offset=None,
                    warnings=[],
                ),
            )

        output = StringIO()
        call_command("report_event_capture_times", event_id=9, processor_version=2, stdout=output)

        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "incomplete")
        self.assertFalse(report["checks"]["no_three_hour_split"])
        self.assertEqual(
            report["source_mode_comparison"]["event_timezone"]["hour_offset_counts"],
            {"0": 1, "3": 1},
        )
