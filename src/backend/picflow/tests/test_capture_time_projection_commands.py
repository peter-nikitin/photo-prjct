import json
import re
from datetime import UTC, date, datetime
from io import StringIO
from queue import Queue
from threading import Barrier, Thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection
from django.db.models.query import QuerySet
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    EventProcessingRun,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    CAPTURE_METADATA_PROCESSOR_VERSION,
    request_capture_metadata,
)
from processing.services.jobs import claim_job, complete_attempt

from picflow.models import Event, Photo


def capture_result(capture_time: object) -> dict[str, object]:
    if capture_time is None:
        return {
            "capture_time": None,
            "source_field": None,
            "timezone_state": "not_applicable",
            "source_value": None,
            "source_offset": None,
            "event_timezone": "Europe/Moscow",
            "warnings": ["capture_time_missing"],
        }
    return {
        "capture_time": capture_time,
        "source_field": "DateTimeOriginal",
        "timezone_state": "event_timezone",
        "source_value": "2026:08:08 15:34:56",
        "source_offset": None,
        "event_timezone": "Europe/Moscow",
        "warnings": [],
    }


class CaptureTimeProjectionCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="projection-command-owner")
        self.event = self.event_with("projection-event", event_id=100)
        self.other_event = self.event_with("projection-other", event_id=101)

    def event_with(self, suffix: str, *, event_id: int | None = None) -> Event:
        return Event.objects.create(
            id=event_id,
            name=f"Projection {suffix}",
            slug=f"projection-{suffix}",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 8),
            city="Moscow",
            timezone_name="Europe/Moscow",
        )

    def photo(self, suffix: str, *, event: Event | None = None) -> Photo:
        return Photo.objects.create(
            id=f"projection-{suffix}",
            event=event or self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/projection/{suffix}.jpg",
            original_filename=f"{suffix}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def current_capture_attempt(
        self,
        photo: Photo,
        *,
        capture_time: object = "2026-08-08T12:34:56Z",
        processor_version: int = 2,
        attempt_status: str = ProcessingAttempt.Status.SUCCEEDED,
        accepted: bool = True,
        state_status: str = PhotoProcessingState.Status.SUCCEEDED,
    ) -> ProcessingAttempt:
        configuration = {"capture_metadata": {"event_timezone": photo.event.timezone_name}}
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=1,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=processor_version,
            configuration=configuration,
            configuration_hash=f"{photo.id:0<64}"[:64],
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=processor_version,
            configuration=configuration,
            configuration_hash=run.configuration_hash,
            input_fingerprint={},
            status=ProcessingJob.Status.SUCCEEDED,
            completed_at=timezone.now(),
        )
        attempt = ProcessingAttempt.objects.create(
            event=photo.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=1,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=processor_version,
            configuration=configuration,
            input_fingerprint={},
            status=attempt_status,
            terminal_at=timezone.now(),
            result=capture_result(capture_time),
            accepted=accepted,
        )
        PhotoProcessingState.objects.update_or_create(
            photo=photo,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            defaults={
                "status": state_status,
                "current_run": run,
                "current_job": job,
                "current_attempt": attempt,
                "accepted_attempt": attempt if accepted else None,
                "succeeded_at": (
                    timezone.now()
                    if state_status == PhotoProcessingState.Status.SUCCEEDED
                    else None
                ),
            },
        )
        return attempt

    def command_json(self, command: str, *arguments: object) -> dict[str, object]:
        output = StringIO()
        call_command(command, *(str(argument) for argument in arguments), stdout=output)
        return json.loads(output.getvalue())

    def test_scope_defaults_to_all_events_and_rejects_conflicting_selection(self) -> None:
        report = self.command_json("rebuild_photo_capture_time_projection")
        self.assertEqual(report["scope"], "all_events")
        self.assertEqual(report["action"], "dry_run")
        with self.assertRaisesRegex(CommandError, "not allowed with argument"):
            call_command(
                "report_photo_capture_time_projection",
                "--event-id",
                str(self.event.id),
                "--all-events",
            )

    def test_rebuild_is_dry_run_by_default_and_emits_deterministic_aggregate_json(self) -> None:
        photo = self.photo("dry-run")
        attempt = self.current_capture_attempt(photo)
        first = self.command_json(
            "rebuild_photo_capture_time_projection", "--event-id", self.event.id
        )
        second = self.command_json(
            "rebuild_photo_capture_time_projection", "--event-id", self.event.id
        )

        photo.refresh_from_db()
        self.assertIsNone(photo.capture_time)
        self.assertIsNone(photo.capture_time_source_attempt_id)
        self.assertEqual(first, second)
        self.assertEqual(first["action"], "dry_run")
        self.assertEqual(first["would_change"], 1)
        rendered = json.dumps(first, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(photo.id, rendered)
        self.assertNotIn(str(attempt.id), rendered)
        self.assertNotIn("2026-08-08T12:34:56", rendered)

    def test_rebuild_rejects_parseable_but_noncanonical_capture_times(self) -> None:
        fixtures = {
            "space": "2026-08-08 12:34:56Z",
            "overprec": "2026-08-08T12:34:56.1234567Z",
        }
        attempts = {
            suffix: self.current_capture_attempt(
                self.photo(f"noncanonical-{suffix}"), capture_time=value
            )
            for suffix, value in fixtures.items()
        }

        dry_run = self.command_json(
            "rebuild_photo_capture_time_projection", "--event-id", self.event.id
        )
        applied = self.command_json(
            "rebuild_photo_capture_time_projection", "--event-id", self.event.id, "--apply"
        )

        self.assertEqual(dry_run["would_change"], 0)
        self.assertEqual(applied["changed"], 0)
        for suffix, attempt in attempts.items():
            with self.subTest(suffix=suffix):
                photo = Photo.objects.get(pk=attempt.photo_id)
                self.assertIsNone(photo.capture_time)
                self.assertIsNone(photo.capture_time_source_attempt_id)
                self.assertEqual(
                    ProcessingAttempt.objects.get(pk=attempt.pk).result,
                    capture_result(attempt.result["capture_time"]),
                )

    def test_dry_run_and_report_issue_no_dml_or_authoritative_writes(self) -> None:
        photo = self.photo("read-only")
        attempt = self.current_capture_attempt(photo)
        before = self.authoritative_snapshot()

        with CaptureQueriesContext(connection) as dry_run_queries:
            self.command_json("rebuild_photo_capture_time_projection", "--event-id", self.event.id)
        with CaptureQueriesContext(connection) as report_queries:
            self.command_json("report_photo_capture_time_projection", "--event-id", self.event.id)

        self.assertEqual(self.authoritative_snapshot(), before)
        self.assertEqual(self.dml_queries(dry_run_queries), [])
        self.assertEqual(self.dml_queries(report_queries), [])
        photo.refresh_from_db()
        self.assertIsNone(photo.capture_time)
        self.assertIsNone(photo.capture_time_source_attempt_id)
        self.assertEqual(
            ProcessingAttempt.objects.get(pk=attempt.pk).result,
            before["attempts"][0]["result"],
        )

    @staticmethod
    def dml_queries(queries: CaptureQueriesContext) -> list[str]:
        return [
            query["sql"]
            for query in queries.captured_queries
            if query["sql"].startswith(("INSERT", "UPDATE", "DELETE"))
        ]

    def assert_projection_pair_update(self, sql: str) -> None:
        self.assertIn('UPDATE "picflow_photo"', sql)
        set_clause = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
        self.assertEqual(
            re.findall(r'"([^"]+)"\s*=', set_clause),
            ["capture_time", "capture_time_source_attempt_id"],
        )

    def authoritative_snapshot(self) -> dict[str, list[dict[str, object]]]:
        return {
            "runs": list(
                EventProcessingRun.objects.order_by("pk").values(
                    "id", "status", "report", "configuration", "configuration_hash"
                )
            ),
            "jobs": list(
                ProcessingJob.objects.order_by("pk").values(
                    "id", "status", "run_id", "photo_id", "configuration", "input_fingerprint"
                )
            ),
            "states": list(
                PhotoProcessingState.objects.order_by("pk").values(
                    "id",
                    "status",
                    "current_run_id",
                    "current_job_id",
                    "current_attempt_id",
                    "accepted_attempt_id",
                )
            ),
            "attempts": list(
                ProcessingAttempt.objects.order_by("pk").values(
                    "id", "status", "accepted", "result", "result_hash", "run_id", "job_id"
                )
            ),
        }

    def test_apply_projects_exact_current_v2_evidence_and_is_idempotent(self) -> None:
        photo = self.photo("apply")
        attempt = self.current_capture_attempt(photo)
        before = self.authoritative_snapshot()

        with CaptureQueriesContext(connection) as queries:
            applied = self.command_json(
                "rebuild_photo_capture_time_projection", "--event-id", self.event.id, "--apply"
            )
        with CaptureQueriesContext(connection) as repeated_queries:
            repeated = self.command_json(
                "rebuild_photo_capture_time_projection", "--event-id", self.event.id, "--apply"
            )

        photo.refresh_from_db()
        self.assertEqual(photo.capture_time, datetime(2026, 8, 8, 12, 34, 56, tzinfo=UTC))
        self.assertEqual(photo.capture_time_source_attempt_id, attempt.id)
        self.assertEqual(applied["changed"], 1)
        self.assertEqual(repeated["changed"], 0)
        self.assertEqual(repeated["unchanged"], 1)
        self.assertEqual(self.authoritative_snapshot(), before)
        dml = self.dml_queries(queries)
        self.assertEqual(len(dml), 1)
        self.assert_projection_pair_update(dml[0])
        self.assertEqual(self.dml_queries(repeated_queries), [])

    def test_all_events_clears_an_extra_projection_for_an_event_without_evidence(self) -> None:
        source_photo = self.photo("source")
        source_attempt = self.current_capture_attempt(source_photo)
        extra_photo = self.photo("extra", event=self.other_event)
        Photo.objects.filter(pk=extra_photo.pk).update(
            capture_time=datetime(2026, 8, 8, 12, 34, 56, tzinfo=UTC),
            capture_time_source_attempt=source_attempt,
        )

        report = self.command_json(
            "rebuild_photo_capture_time_projection", "--all-events", "--apply"
        )

        extra_photo.refresh_from_db()
        self.assertIsNone(extra_photo.capture_time)
        self.assertIsNone(extra_photo.capture_time_source_attempt_id)
        self.assertGreaterEqual(report["events"], 2)
        self.assertEqual(report["changed"], 2)

    def test_apply_clear_and_idempotent_paths_update_only_the_projection_pair(self) -> None:
        source_photo = self.photo("clear-source")
        source_attempt = self.current_capture_attempt(source_photo)
        stale_photo = self.photo("clear-stale", event=self.other_event)
        Photo.objects.filter(pk=stale_photo.pk).update(
            capture_time=datetime(2026, 8, 8, 12, 34, 56, tzinfo=UTC),
            capture_time_source_attempt=source_attempt,
        )
        before = self.authoritative_snapshot()

        with CaptureQueriesContext(connection) as clear_queries:
            cleared = self.command_json(
                "rebuild_photo_capture_time_projection",
                "--event-id",
                self.other_event.id,
                "--apply",
            )
        with CaptureQueriesContext(connection) as repeated_queries:
            repeated = self.command_json(
                "rebuild_photo_capture_time_projection",
                "--event-id",
                self.other_event.id,
                "--apply",
            )

        self.assertEqual(cleared["changed"], 1)
        self.assertEqual(repeated["changed"], 0)
        self.assertEqual(self.authoritative_snapshot(), before)
        updates = self.dml_queries(clear_queries)
        self.assertEqual(len(updates), 1)
        self.assert_projection_pair_update(updates[0])
        self.assertEqual(self.dml_queries(repeated_queries), [])

    def test_report_classifies_drift_without_mutating_authoritative_evidence(self) -> None:
        current = self.photo("current")
        current_attempt = self.current_capture_attempt(current)
        missing = self.photo("missing")
        self.current_capture_attempt(missing)
        stale = self.photo("stale")
        stale_attempt = self.current_capture_attempt(stale)
        replacement = self.current_capture_attempt(stale, capture_time="2026-08-08T13:34:56Z")
        partial = self.photo("partial")
        self.current_capture_attempt(partial)
        connection.check_constraints()
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE picflow_photo DROP CONSTRAINT picflow_photo_capture_time_pair_chk"
            )
        Photo.objects.filter(pk=partial.pk).update(
            capture_time=datetime(2026, 8, 8, 12, 34, 56, tzinfo=UTC)
        )
        unsupported = self.photo("unsupported")
        unsupported_attempt = self.current_capture_attempt(unsupported, processor_version=3)
        Photo.objects.filter(pk=unsupported.pk).update(
            capture_time=datetime(2026, 8, 8, 12, 34, 56, tzinfo=UTC),
            capture_time_source_attempt=unsupported_attempt,
        )
        mismatching = self.photo("mismatching")
        mismatching_attempt = self.current_capture_attempt(mismatching)
        Photo.objects.filter(pk=mismatching.pk).update(
            capture_time=datetime(2026, 8, 8, 13, 34, 56, tzinfo=UTC),
            capture_time_source_attempt=mismatching_attempt,
        )
        null_success = self.photo("null-success")
        self.current_capture_attempt(null_success, capture_time=None)
        extra = self.photo("extra")
        Photo.objects.filter(pk=extra.pk).update(
            capture_time=datetime(2026, 8, 8, 12, 34, 56, tzinfo=UTC),
            capture_time_source_attempt=current_attempt,
        )
        Photo.objects.filter(pk=current.pk).update(
            capture_time=datetime(2026, 8, 8, 12, 34, 56, tzinfo=UTC),
            capture_time_source_attempt=current_attempt,
        )
        Photo.objects.filter(pk=stale.pk).update(
            capture_time=datetime(2026, 8, 8, 12, 34, 56, tzinfo=UTC),
            capture_time_source_attempt=stale_attempt,
        )
        before = ProcessingAttempt.objects.get(pk=replacement.pk).result.copy()

        report = self.command_json(
            "report_photo_capture_time_projection", "--event-id", self.event.id
        )

        self.assertFalse(report["clean"])
        counts = report["counts"]
        self.assertEqual(counts["missing"], 1)
        self.assertEqual(counts["stale"], 1)
        self.assertEqual(counts["mismatching"], 1)
        self.assertEqual(counts["extra"], 1)
        self.assertEqual(counts["partial_pair"], 1)
        self.assertEqual(counts["unsupported_version"], 1)
        self.assertEqual(counts["qualifying_null"], 1)
        self.assertEqual(ProcessingAttempt.objects.get(pk=replacement.pk).result, before)

    def test_report_does_not_treat_malformed_non_null_evidence_as_qualifying_null(self) -> None:
        malformed = self.photo("malformed-non-null")
        self.current_capture_attempt(malformed, capture_time="2026-08-08 12:34:56Z")

        report = self.command_json(
            "report_photo_capture_time_projection", "--event-id", self.event.id
        )

        self.assertEqual(report["counts"]["qualifying_non_null"], 0)
        self.assertEqual(report["counts"]["qualifying_null"], 0)

    def test_report_require_clean_prints_aggregate_before_nonzero_exit(self) -> None:
        self.current_capture_attempt(self.photo("require-clean"))
        output = StringIO()

        with self.assertRaisesRegex(CommandError, "projection reconciliation is not clean"):
            call_command(
                "report_photo_capture_time_projection",
                "--event-id",
                self.event.id,
                "--require-clean",
                stdout=output,
            )

        report = json.loads(output.getvalue())
        self.assertFalse(report["clean"])
        self.assertEqual(report["counts"]["missing"], 1)

    def test_event_nine_accepts_a_complete_exact_small_cohort(self) -> None:
        from picflow import capture_time_projection

        event_nine = Event.objects.create(
            id=9,
            name=capture_time_projection.EXPECTED_EVENT_NAME,
            slug="projection-event-nine",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 8),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            timezone_name=capture_time_projection.EXPECTED_EVENT_TIMEZONE,
        )
        for suffix in ("nine-one", "nine-two"):
            self.current_capture_attempt(self.photo(suffix, event=event_nine))

        with patch.object(capture_time_projection, "EXPECTED_EVENT_PHOTO_COUNT", 2):
            self.command_json(
                "rebuild_photo_capture_time_projection", "--event-id", event_nine.id, "--apply"
            )
            report = self.command_json(
                "report_photo_capture_time_projection", "--event-id", event_nine.id
            )

        self.assertTrue(report["clean"])
        self.assertTrue(report["event_9"]["accepted"])
        self.assertEqual(report["event_9"]["exact_source_value_pairs"], 2)

    def test_event_nine_preconditions_fail_independently(self) -> None:
        from picflow import capture_time_projection

        event_nine = Event.objects.create(
            id=9,
            name=capture_time_projection.EXPECTED_EVENT_NAME,
            slug="projection-event-nine-preconditions",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 8),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            timezone_name=capture_time_projection.EXPECTED_EVENT_TIMEZONE,
        )
        attempt = self.current_capture_attempt(self.photo("nine-precondition", event=event_nine))
        Photo.objects.filter(pk=attempt.photo_id).update(
            capture_time=datetime(2026, 8, 8, 12, 34, 56, tzinfo=UTC),
            capture_time_source_attempt=attempt,
        )

        with patch.object(capture_time_projection, "EXPECTED_EVENT_PHOTO_COUNT", 1):
            for field, value in (
                ("name", "Unexpected"),
                ("publication_status", Event.PublicationStatus.DRAFT),
                ("timezone_name", "Europe/London"),
            ):
                with self.subTest(field=field):
                    original = getattr(event_nine, field)
                    setattr(event_nine, field, value)
                    event_nine.save(update_fields=[field])
                    report = self.command_json(
                        "report_photo_capture_time_projection", "--event-id", event_nine.id
                    )
                    self.assertFalse(report["clean"])
                    self.assertFalse(report["event_9"]["accepted"])
                    setattr(event_nine, field, original)
                    event_nine.save(update_fields=[field])

    def test_event_nine_rejects_short_and_nonexact_cohorts_with_valid_preconditions(self) -> None:
        from picflow import capture_time_projection

        event_nine = Event.objects.create(
            id=9,
            name=capture_time_projection.EXPECTED_EVENT_NAME,
            slug="projection-event-nine-cohort",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 8),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            timezone_name=capture_time_projection.EXPECTED_EVENT_TIMEZONE,
        )
        self.current_capture_attempt(self.photo("nine-cohort-one", event=event_nine))

        with patch.object(capture_time_projection, "EXPECTED_EVENT_PHOTO_COUNT", 2):
            self.command_json(
                "rebuild_photo_capture_time_projection", "--event-id", event_nine.id, "--apply"
            )
            short_report = self.command_json(
                "report_photo_capture_time_projection", "--event-id", event_nine.id
            )
            second = self.current_capture_attempt(self.photo("nine-cohort-two", event=event_nine))
            self.command_json(
                "rebuild_photo_capture_time_projection", "--event-id", event_nine.id, "--apply"
            )
            Photo.objects.filter(pk=second.photo_id).update(
                capture_time=datetime(2026, 8, 8, 13, 34, 56, tzinfo=UTC),
                capture_time_source_attempt=second,
            )
            nonexact_report = self.command_json(
                "report_photo_capture_time_projection", "--event-id", event_nine.id
            )

        self.assertFalse(short_report["event_9"]["accepted"])
        self.assertEqual(short_report["event_9"]["exact_source_value_pairs"], 1)
        self.assertFalse(nonexact_report["event_9"]["accepted"])
        self.assertEqual(nonexact_report["event_9"]["exact_source_value_pairs"], 1)
        self.assertEqual(nonexact_report["counts"]["mismatching"], 1)

    def test_global_report_fails_when_event_nine_is_absent(self) -> None:
        output = StringIO()

        with self.assertRaisesRegex(CommandError, "projection reconciliation is not clean"):
            call_command("report_photo_capture_time_projection", "--require-clean", stdout=output)

        report = json.loads(output.getvalue())
        self.assertFalse(report["clean"])
        self.assertFalse(report["event_9"]["accepted"])

    def test_rebuild_retries_when_current_identity_changes_after_discovery(self) -> None:
        photo = self.photo("identity-race")
        first_attempt = self.current_capture_attempt(photo)
        successor = self.current_capture_attempt(photo, capture_time="2026-08-08T13:34:56Z")
        state = PhotoProcessingState.objects.get(
            photo=photo, processor_type=CAPTURE_METADATA_PROCESSOR
        )
        state.current_run = first_attempt.run
        state.current_job = first_attempt.job
        state.current_attempt = first_attempt
        state.accepted_attempt = first_attempt
        state.save()
        from picflow import capture_time_projection

        original_discover = capture_time_projection.discover_photo_identity
        calls = 0

        def rotate_after_first_discovery(photo_id: str):
            nonlocal calls
            discovery = original_discover(photo_id)
            calls += 1
            if calls == 1:
                PhotoProcessingState.objects.filter(pk=state.pk).update(
                    current_run=successor.run,
                    current_job=successor.job,
                    current_attempt=successor,
                    accepted_attempt=successor,
                )
            return discovery

        with patch.object(
            capture_time_projection, "discover_photo_identity", rotate_after_first_discovery
        ):
            report = self.command_json(
                "rebuild_photo_capture_time_projection", "--event-id", self.event.id, "--apply"
            )

        photo.refresh_from_db()
        self.assertGreaterEqual(calls, 2)
        self.assertEqual(photo.capture_time_source_attempt_id, successor.id)
        self.assertEqual(photo.capture_time, datetime(2026, 8, 8, 13, 34, 56, tzinfo=UTC))
        self.assertGreaterEqual(report["retries"], 1)

    def test_rebuild_uses_bounded_photo_batches(self) -> None:
        self.current_capture_attempt(self.photo("batch-one"))
        self.current_capture_attempt(self.photo("batch-two"))
        from picflow import capture_time_projection

        original_atomic = capture_time_projection.transaction.atomic
        original_iterator = QuerySet.iterator
        photo_iterator_chunks: list[int | None] = []

        def record_iterator(queryset, *args, **kwargs):
            if queryset.model is Photo:
                photo_iterator_chunks.append(kwargs.get("chunk_size"))
            return original_iterator(queryset, *args, **kwargs)

        with (
            patch.object(capture_time_projection, "BATCH_SIZE", 1),
            patch.object(
                capture_time_projection.transaction, "atomic", side_effect=original_atomic
            ) as atomic,
            patch.object(QuerySet, "iterator", record_iterator),
        ):
            report = self.command_json(
                "rebuild_photo_capture_time_projection", "--event-id", self.event.id, "--apply"
            )

        self.assertEqual(report["photos"], 2)
        self.assertEqual(report["batches"], 2)
        self.assertEqual(atomic.call_count, 2)
        self.assertEqual(photo_iterator_chunks, [1])

    def test_rebuild_prints_aggregate_before_failing_after_identity_retry_exhaustion(self) -> None:
        from picflow.management.commands import rebuild_photo_capture_time_projection

        output = StringIO()
        totals = {
            "batches": 0,
            "changed": 0,
            "events": 1,
            "exhausted": 1,
            "photos": 1,
            "retries": 3,
            "skipped": 0,
            "unchanged": 0,
        }
        with patch.object(
            rebuild_photo_capture_time_projection, "rebuild_events", return_value=totals
        ):
            with self.assertRaisesRegex(CommandError, "did not converge"):
                call_command(
                    "rebuild_photo_capture_time_projection",
                    "--event-id",
                    self.event.id,
                    "--apply",
                    stdout=output,
                )

        self.assertEqual(json.loads(output.getvalue())["exhausted"], 1)


class CaptureTimeProjectionConcurrencyTests(TransactionTestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="projection-concurrency-owner")
        self.event = Event.objects.create(
            name="Projection concurrency",
            slug="projection-concurrency",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 8),
            city="Moscow",
            timezone_name="Europe/Moscow",
        )
        self.photo = Photo.objects.create(
            id="projection-concurrency-photo",
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key="originals/projection/concurrency.jpg",
            original_filename="concurrency.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def test_rebuild_retries_against_completion_without_deadlock_and_publishes_successor(
        self,
    ) -> None:
        from threading import Event as ThreadEvent

        from picflow import capture_time_projection

        request_capture_metadata(self.photo)
        claimed = claim_job(
            contract_version=1,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=CAPTURE_METADATA_PROCESSOR_VERSION,
            worker_build="projection-concurrency-worker",
        )
        barrier = Barrier(3)
        discovered = ThreadEvent()
        completed = ThreadEvent()
        failures: Queue[BaseException] = Queue()
        acquired: list[str] = []
        original_discover = capture_time_projection.discover_photo_identity
        original_lock = capture_time_projection._lock_repair_row

        def discover_then_allow_completion(photo_id: str):
            identity = original_discover(photo_id)
            discovered.set()
            if not completed.wait(timeout=5):
                raise TimeoutError("completion did not publish before repair lock acquisition")
            return identity

        def record_lock(model, **lookup):
            acquired.append(model.__name__)
            return original_lock(model, **lookup)

        def rebuild() -> None:
            close_old_connections()
            try:
                barrier.wait()
                capture_time_projection.rebuild_photo(self.photo.id, apply=True)
            except BaseException as error:  # noqa: BLE001
                failures.put(error)
            finally:
                close_old_connections()

        def complete() -> None:
            close_old_connections()
            try:
                barrier.wait()
                if not discovered.wait(timeout=5):
                    raise TimeoutError("repair did not discover the pre-completion identity")
                complete_attempt(claimed.attempt.id, result=capture_result("2026-08-08T12:34:56Z"))
                completed.set()
            except BaseException as error:  # noqa: BLE001
                failures.put(error)
            finally:
                close_old_connections()

        with (
            patch.object(
                capture_time_projection,
                "discover_photo_identity",
                side_effect=discover_then_allow_completion,
            ),
            patch.object(capture_time_projection, "_lock_repair_row", side_effect=record_lock),
        ):
            workers = [Thread(target=rebuild), Thread(target=complete)]
            for worker in workers:
                worker.start()
            barrier.wait()
            for worker in workers:
                worker.join(timeout=5)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertTrue(failures.empty())
        self.photo.refresh_from_db()
        self.assertEqual(self.photo.capture_time_source_attempt_id, claimed.attempt.id)
        self.assertEqual(self.photo.capture_time, datetime(2026, 8, 8, 12, 34, 56, tzinfo=UTC))
        self.assertEqual(
            acquired[-6:],
            [
                "Event",
                "EventProcessingRun",
                "ProcessingJob",
                "Photo",
                "PhotoProcessingState",
                "ProcessingAttempt",
            ],
        )
