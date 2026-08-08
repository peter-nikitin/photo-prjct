import json
from collections.abc import Iterable
from datetime import UTC, date, datetime
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    EventProcessingRun,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import CONTRACT_VERSION

from picflow.models import Event, Photo


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
@patch("picflow.capture_time_projection.EXPECTED_EVENT_PHOTO_COUNT", 201)
@patch("processing.management.commands.report_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT", 201)
class EventGalleryTimeFilterBenchmarkCommandTests(TestCase):
    """Catch a benchmark that measures another cohort or leaks rows."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="gallery-benchmark-owner")
        self.event = Event.objects.create(
            id=9,
            name="Cyclingrace Вечернее Садовое",
            slug="gallery-benchmark-event",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 1),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            timezone_name="Europe/Moscow",
        )
        self._create_current_v2_corpus(range(201))

    def _create_current_v2_corpus(self, numbers: Iterable[int]) -> None:
        photos = [
            Photo(
                id=f"benchmark-{number:03d}",
                event=self.event,
                src="",
                uploaded_by=self.user,
                original_key=f"private/originals/benchmark-{number:03d}",
                original_filename=f"private-{number:03d}.jpg",
                original_size=10,
                original_content_type="image/jpeg",
                uploaded_at=timezone.now(),
            )
            for number in numbers
        ]
        Photo.objects.bulk_create(photos)
        configuration = {"capture_metadata": {"event_timezone": "Europe/Moscow"}}
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=CONTRACT_VERSION,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=2,
            configuration=configuration,
            configuration_hash="b" * 64,
        )
        ProcessingJob.objects.bulk_create(
            [
                ProcessingJob(
                    event=self.event,
                    run=run,
                    photo=photo,
                    contract_version=CONTRACT_VERSION,
                    processor_type=CAPTURE_METADATA_PROCESSOR,
                    processor_version=2,
                    configuration=configuration,
                    configuration_hash=run.configuration_hash,
                    input_fingerprint={},
                    status=ProcessingJob.Status.SUCCEEDED,
                    completed_at=timezone.now(),
                )
                for photo in photos
            ]
        )
        jobs = list(ProcessingJob.objects.filter(run=run).order_by("photo_id"))
        ProcessingAttempt.objects.bulk_create(
            [
                ProcessingAttempt(
                    event=self.event,
                    run=run,
                    job=job,
                    photo_id=job.photo_id,
                    contract_version=CONTRACT_VERSION,
                    processor_type=CAPTURE_METADATA_PROCESSOR,
                    processor_version=2,
                    configuration=configuration,
                    input_fingerprint={},
                    status=ProcessingAttempt.Status.SUCCEEDED,
                    terminal_at=timezone.now(),
                    accepted=True,
                    result={
                        "capture_time": "2025-12-31T21:01:00Z",
                        "source_value": "2026:01:01 00:01:00",
                        "timezone_state": "event_timezone",
                        "warnings": [],
                    },
                )
                for job in jobs
            ]
        )
        attempts_by_job = {
            attempt.job_id: attempt
            for attempt in ProcessingAttempt.objects.filter(run=run).order_by("job_id")
        }
        PhotoProcessingState.objects.bulk_create(
            [
                PhotoProcessingState(
                    photo_id=job.photo_id,
                    processor_type=CAPTURE_METADATA_PROCESSOR,
                    status=PhotoProcessingState.Status.SUCCEEDED,
                    current_run=run,
                    current_job=job,
                    current_attempt=attempts_by_job[job.pk],
                    accepted_attempt=attempts_by_job[job.pk],
                    succeeded_at=timezone.now(),
                )
                for job in jobs
            ]
        )
        for photo, job in zip(photos, jobs, strict=True):
            photo.capture_time = datetime(2025, 12, 31, 21, 1, tzinfo=UTC)
            photo.capture_time_source_attempt = attempts_by_job[job.pk]
        Photo.objects.bulk_update(photos, ["capture_time", "capture_time_source_attempt"])

    def test_rejects_any_scope_other_than_event_nine(self) -> None:
        """A benchmark widened beyond the accepted cohort must fail this test."""
        with self.assertRaisesRegex(CommandError, "event ID 9"):
            call_command("benchmark_event_gallery_time_filter", event_id=10)

    @patch(
        "picflow.management.commands.benchmark_event_gallery_time_filter._build_report",
        return_value={"checks": {"event_timezone": False}, "status": "incomplete"},
    )
    def test_rejects_an_incomplete_current_v2_or_event_timezone_cohort(self, _build_report) -> None:
        """A benchmark without accepted v2 and event-timezone evidence must fail this test."""
        with self.assertRaisesRegex(CommandError, "current-v2 capture-time precondition"):
            call_command("benchmark_event_gallery_time_filter", event_id=9)

    @patch(
        "picflow.capture_time_projection.report_events",
        return_value={"clean": False},
    )
    def test_rejects_projection_drift_before_emitting_a_benchmark_success(
        self, _report_events
    ) -> None:
        """A candidate benchmark must not measure or report success on a dirty projection."""
        output = StringIO()

        with self.assertRaisesRegex(CommandError, "global projection reconciliation"):
            call_command("benchmark_event_gallery_time_filter", event_id=9, stdout=output)

        self.assertEqual(output.getvalue(), "")

    @patch(
        "processing.management.commands.report_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
        201,
    )
    @patch(
        "picflow.management.commands.benchmark_event_gallery_time_filter.Command._measure_page",
        side_effect=[
            {"plan_shape": ["Limit"], "database_execution_ms": 10, "rendered_response_ms": 10},
            {"plan_shape": ["Limit"], "database_execution_ms": 11, "rendered_response_ms": 12},
            {"plan_shape": ["Limit"], "database_execution_ms": 12, "rendered_response_ms": 12},
            {"plan_shape": ["Limit"], "database_execution_ms": 13, "rendered_response_ms": 12},
            {"plan_shape": ["Limit"], "database_execution_ms": 13, "rendered_response_ms": 13},
            {"plan_shape": ["Limit"], "database_execution_ms": 14, "rendered_response_ms": 14},
        ],
    )
    def test_outputs_sanitized_measurements_for_first_midpoint_and_last_without_writes(
        self, _measure_page
    ) -> None:
        """A row-level result or a command write must fail this test."""
        before_counts = {
            "events": Event.objects.count(),
            "photos": Photo.objects.count(),
            "runs": EventProcessingRun.objects.count(),
            "jobs": ProcessingJob.objects.count(),
            "attempts": ProcessingAttempt.objects.count(),
            "states": PhotoProcessingState.objects.count(),
        }
        output = StringIO()

        call_command(
            "benchmark_event_gallery_time_filter",
            event_id=9,
            pages="1,mid,last",
            stdout=output,
        )

        report = json.loads(output.getvalue())
        self.assertEqual(report["event_id"], 9)
        self.assertEqual(
            report["corpus"],
            {"accepted_current_v2_capture_times": 201, "event_photo_count": 201},
        )
        self.assertEqual([page["page"] for page in report["pages"]], ["first", "midpoint", "last"])
        self.assertEqual(report["gate"], "passed")
        for page in report["pages"]:
            self.assertEqual(page["gate"], "passed")
            self.assertIn("plan_shape", page["unfiltered"])
            self.assertIn("database_execution_ms", page["unfiltered"])
            self.assertIn("rendered_response_ms", page["unfiltered"])
            self.assertIn("database_execution_ms", page["filtered"])
            self.assertIn("rendered_response_ms", page["filtered"])
            self.assertLessEqual(page["ratios"]["database_execution"], 2)
            self.assertLessEqual(page["ratios"]["rendered_response"], 2)
        self.assertEqual(_measure_page.call_count, 6)
        calls = [call.kwargs for call in _measure_page.call_args_list]
        for first, filtered, expected_page in zip(calls[::2], calls[1::2], (1, 2, 3), strict=True):
            self.assertEqual(first["event"], self.event)
            self.assertEqual(filtered["event"], self.event)
            self.assertEqual(first["page_number"], expected_page)
            self.assertEqual(filtered["page_number"], expected_page)
            self.assertEqual(first["filter_data"], {})
            self.assertEqual(filtered["filter_data"], {"from": "2026-01-01T00:00"})
            self.assertIsNone(first["capture_time_start"])
            self.assertIsNone(first["capture_time_end"])
            self.assertIsNotNone(filtered["capture_time_start"])
            self.assertIsNotNone(filtered["capture_time_end"])
        self.assertEqual(
            {
                "events": Event.objects.count(),
                "photos": Photo.objects.count(),
                "runs": EventProcessingRun.objects.count(),
                "jobs": ProcessingJob.objects.count(),
                "attempts": ProcessingAttempt.objects.count(),
                "states": PhotoProcessingState.objects.count(),
            },
            before_counts,
        )
        for private_value in (
            "private-000.jpg",
            "private/originals/benchmark-000",
            "2026:01:01 00:01:00",
            "2025-12-31T21:01:00Z",
            "benchmark-000",
        ):
            self.assertNotIn(private_value, output.getvalue())

    def test_measurement_uses_the_real_page_query_and_render_path(self) -> None:
        """Replacing EXPLAIN or the rendered response with a synthetic value must fail this test."""
        from picflow.management.commands.benchmark_event_gallery_time_filter import Command

        measurement = Command()._measure_page(
            event=self.event,
            page_number=1,
            filter_data={},
            capture_time_start=None,
            capture_time_end=None,
        )

        self.assertTrue(measurement["plan_shape"])
        self.assertGreaterEqual(measurement["database_execution_ms"], 0)
        self.assertGreaterEqual(measurement["rendered_response_ms"], 0)

    def test_render_survives_a_missing_production_manifest(self) -> None:
        """The one-off benchmark must not require the production collectstatic manifest."""
        from django.contrib.staticfiles.storage import staticfiles_storage
        from whitenoise.storage import CompressedManifestStaticFilesStorage

        from picflow.management.commands.benchmark_event_gallery_time_filter import Command

        manifest_storage = CompressedManifestStaticFilesStorage()
        with patch.object(staticfiles_storage, "_wrapped", manifest_storage):
            measurement = Command()._rendered_response_ms(
                event=self.event,
                page_number=1,
                filter_data={},
            )

        self.assertGreaterEqual(measurement, 0)

    @patch(
        "picflow.management.commands.benchmark_event_gallery_time_filter.event_detail",
        return_value=HttpResponse(status=503),
    )
    def test_rejects_a_non_successful_rendered_response(self, _event_detail) -> None:
        """A benchmark that accepts an error page as a timing must fail this test."""
        from picflow.management.commands.benchmark_event_gallery_time_filter import Command

        with self.assertRaisesRegex(CommandError, "non-200"):
            Command()._rendered_response_ms(event=self.event, page_number=1, filter_data={})

    @patch(
        "picflow.management.commands.benchmark_event_gallery_time_filter.event_detail",
        side_effect=TimeoutError("candidate request timed out"),
    )
    def test_rejects_a_timed_out_rendered_response_before_emitting_success(
        self, _event_detail
    ) -> None:
        """A request timeout cannot become a measured candidate success."""
        output = StringIO()

        with self.assertRaisesRegex(CommandError, "rendered event-detail request failed"):
            call_command(
                "benchmark_event_gallery_time_filter", event_id=9, pages="1", stdout=output
            )

        self.assertEqual(output.getvalue(), "")

    @patch(
        "processing.management.commands.report_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
        201,
    )
    def test_full_unmocked_command_path_issues_no_mutation_queries(self) -> None:
        """A benchmark write, including an update, must fail this test."""
        output = StringIO()

        with CaptureQueriesContext(connection) as queries:
            try:
                call_command(
                    "benchmark_event_gallery_time_filter", event_id=9, pages="1", stdout=output
                )
            except CommandError as error:
                self.assertEqual(str(error), "performance gate failed")

        mutation_prefixes = ("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "DROP", "TRUNCATE")
        self.assertTrue(queries.captured_queries)
        for query in queries.captured_queries:
            self.assertFalse(query["sql"].lstrip().upper().startswith(mutation_prefixes))

    @patch(
        "picflow.management.commands.benchmark_event_gallery_time_filter.Command._measure_page",
        side_effect=[
            {"plan_shape": ["Limit"], "database_execution_ms": 10, "rendered_response_ms": 10},
            {"plan_shape": ["Limit"], "database_execution_ms": 21, "rendered_response_ms": 10},
        ],
    )
    @patch(
        "processing.management.commands.report_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
        201,
    )
    def test_fails_after_emitting_a_report_when_a_ratio_exceeds_two(self, _measure_page) -> None:
        """A filtered page more than twice its matching baseline must block delivery."""
        output = StringIO()

        with self.assertRaisesRegex(CommandError, "performance gate failed"):
            call_command(
                "benchmark_event_gallery_time_filter", event_id=9, pages="1", stdout=output
            )

        report = json.loads(output.getvalue())
        self.assertEqual(report["gate"], "failed")
        self.assertEqual(report["pages"][0]["gate"], "failed")
        self.assertEqual(report["pages"][0]["ratios"]["database_execution"], 2.1)

    @patch(
        "picflow.management.commands.benchmark_event_gallery_time_filter.Command._measure_page",
        side_effect=[
            {"plan_shape": ["Limit"], "database_execution_ms": 10, "rendered_response_ms": 10},
            {
                "plan_shape": ["Limit"],
                "database_execution_ms": 20.004,
                "rendered_response_ms": 10,
            },
        ],
    )
    @patch(
        "processing.management.commands.report_event_capture_times.EXPECTED_EVENT_PHOTO_COUNT",
        201,
    )
    def test_rejects_an_unrounded_ratio_just_above_two(self, _measure_page) -> None:
        """Rounding a 2.0004 ratio before applying the gate must fail this test."""
        output = StringIO()

        with self.assertRaisesRegex(CommandError, "performance gate failed"):
            call_command(
                "benchmark_event_gallery_time_filter", event_id=9, pages="1", stdout=output
            )

        report = json.loads(output.getvalue())
        self.assertEqual(report["pages"][0]["ratios"]["database_execution"], 2.0)
        self.assertEqual(report["pages"][0]["gate"], "failed")

    def test_real_measurement_boundary_gates_before_rounding_timings(self) -> None:
        """Full-precision database and rendered ratios just above 2 must both fail."""
        from picflow.management.commands.benchmark_event_gallery_time_filter import Command

        explain_results = [
            json.dumps([{"Execution Time": execution_ms, "Plan": {"Node Type": "Limit"}}])
            for execution_ms in (10.0001, 20.0004)
        ]
        with (
            patch("django.db.models.query.QuerySet.explain", side_effect=explain_results),
            patch(
                "picflow.management.commands.benchmark_event_gallery_time_filter.perf_counter",
                side_effect=(0.0, 0.0100001, 0.0, 0.0200004),
            ),
        ):
            comparison = Command()._measure_comparison(
                event=self.event,
                filter_data={"from": "2026-01-01T00:00"},
                capture_time_start=datetime(2025, 12, 31, 21, 0, tzinfo=UTC),
                capture_time_end=datetime(2026, 1, 1, 21, 10, tzinfo=UTC),
                label="first",
                page_number=1,
            )

        self.assertEqual(comparison["gate"], "failed")
        self.assertEqual(
            comparison["ratios"], {"database_execution": 2.0, "rendered_response": 2.0}
        )
        unfiltered = comparison["unfiltered"]
        filtered = comparison["filtered"]
        self.assertIsInstance(unfiltered, dict)
        self.assertIsInstance(filtered, dict)
        assert isinstance(unfiltered, dict)
        assert isinstance(filtered, dict)
        self.assertEqual(unfiltered["database_execution_ms"], 10.0)
        self.assertEqual(filtered["database_execution_ms"], 20.0)
        self.assertEqual(unfiltered["rendered_response_ms"], 10.0)
        self.assertEqual(filtered["rendered_response_ms"], 20.0)
