from __future__ import annotations

import json
from datetime import date
from io import StringIO
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.http import HttpResponse
from django.test import TestCase, modify_settings, override_settings
from django.utils import timezone
from processing.models import (
    GENERATE_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)

from picflow.gallery_media_projection import publish_gallery_media
from picflow.models import Event, Photo


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
@modify_settings(MIDDLEWARE={"remove": "whitenoise.middleware.WhiteNoiseMiddleware"})
class GalleryMediaProjectionSmokeCommandTests(TestCase):
    def setUp(self) -> None:
        Event.objects.update(publication_status=Event.PublicationStatus.DRAFT)
        self.user = get_user_model().objects.create_user(username="projection-smoke-owner")

    def event(self, suffix: str, *, photo_count: int) -> Event:
        event = Event.objects.create(
            name=f"Projection smoke {suffix}",
            slug=f"projection-smoke-{suffix}",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        for index in range(photo_count):
            self.projected_photo(event, suffix=f"{suffix}-{index}")
        return event

    def projected_photo(self, event: Event, *, suffix: str) -> Photo:
        photo = Photo.objects.create(
            id=f"smk-{uuid4().hex[:24]}",
            event=event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/private-{suffix}",
            original_filename=f"private-{suffix}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )
        configuration = {GENERATE_PREVIEW_PROCESSOR: {"variant": "preview-small-v1"}}
        run = EventProcessingRun.objects.create(
            event=event,
            contract_version=2,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash=uuid4().hex + uuid4().hex,
        )
        job = ProcessingJob.objects.create(
            event=event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash=run.configuration_hash,
            input_fingerprint={},
            status=ProcessingJob.Status.SUCCEEDED,
            completed_at=timezone.now(),
        )
        attempt = ProcessingAttempt.objects.create(
            event=event,
            run=run,
            job=job,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        PhotoProcessingState.objects.update_or_create(
            photo=photo,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            defaults={
                "status": PhotoProcessingState.Status.SUCCEEDED,
                "current_run": run,
                "current_job": job,
                "current_attempt": attempt,
                "accepted_attempt": attempt,
                "succeeded_at": timezone.now(),
            },
        )
        derivative = PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-small-v1",
            final_key=f"derivatives/previews/{photo.pk}/preview-small-v1/accepted.jpg",
            byte_size=10,
            content_type="image/jpeg",
            width=10,
            height=10,
            oriented_source_width=10,
            oriented_source_height=10,
            sha256="a" * 64,
            accepted_attempt=attempt,
        )
        publish_gallery_media(derivative)
        return photo

    @staticmethod
    def run_command() -> tuple[dict[str, object], str]:
        output = StringIO()
        call_command("smoke_gallery_media_projection", stdout=output)
        raw = output.getvalue()
        return json.loads(raw), raw

    def test_explicitly_skips_when_no_published_site_visible_event_exists(self) -> None:
        """The break caught here would turn an empty deployment into an ambiguous success."""
        report, _ = self.run_command()

        self.assertEqual(
            report,
            {"reason": "no_published_site_visible_event", "status": "skipped"},
        )

    @patch(
        "config.views.gallery_search_faces_by_photo",
        side_effect=AssertionError("face lookup must be isolated from gallery-media smoke"),
    )
    @patch("config.views.PrivateUploadStorage")
    def test_smokes_largest_event_page_and_exact_photo_without_row_level_output(
        self, storage_class, _face_lookup
    ) -> None:
        """The break caught here would include face SQL or leak selected media identity."""
        self.event("smaller-private-name", photo_count=1)
        self.event("largest-private-name", photo_count=2)
        signer = Mock()
        signer.sign_accepted_preview.return_value = "https://storage.invalid/signed"
        storage_class.return_value = signer

        explain_sql: list[str] = []

        def capture_explain(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith("EXPLAIN"):
                explain_sql.append(sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(capture_explain):
            report, raw = self.run_command()

        self.assertEqual(report["status"], "ok")
        for path in ("gallery_page", "exact_photo"):
            measurement = report[path]
            assert isinstance(measurement, dict)
            elapsed_ms = measurement["elapsed_ms"]
            query_count = measurement["query_count"]
            plan_node_names = measurement["plan_node_names"]
            assert isinstance(elapsed_ms, (int, float))
            assert isinstance(query_count, int)
            assert isinstance(plan_node_names, list)
            self.assertGreaterEqual(elapsed_ms, 0)
            self.assertGreater(query_count, 0)
            self.assertTrue(plan_node_names)
        self.assertTrue(explain_sql)
        self.assertTrue(
            all(
                sql.lstrip().upper().startswith("EXPLAIN (ANALYZE, FORMAT JSON)")
                for sql in explain_sql
            )
        )
        self.assertNotIn("EXPLAIN", raw)
        self.assertNotIn("processing_photoprocessingstate", raw)
        for private_value in (
            "smaller-private-name",
            "largest-private-name",
            "originals/private-largest-private-name-0",
            "derivatives/previews/",
            "accepted.jpg",
        ):
            self.assertNotIn(private_value, raw)

    def test_fails_with_a_sanitized_error_when_scoped_gallery_sql_reads_processing(
        self,
    ) -> None:
        """The break caught here would let customer-facing gallery SQL regain evidence joins."""
        self.event("forbidden-relation", photo_count=1)

        def processing_read(*_args, **_kwargs) -> HttpResponse:
            PhotoProcessingState.objects.exists()
            return HttpResponse(status=200)

        output = StringIO()
        with patch(
            "picflow.management.commands.smoke_gallery_media_projection.event_detail",
            side_effect=processing_read,
        ):
            with self.assertRaisesRegex(CommandError, "processing relation") as error:
                call_command("smoke_gallery_media_projection", stdout=output)

        self.assertEqual(output.getvalue(), "")
        self.assertNotIn("processing_photoprocessingstate", str(error.exception))
        self.assertNotIn("projection-smoke-forbidden-relation", str(error.exception))

    def test_fails_with_a_sanitized_error_when_gallery_page_is_not_successful(self) -> None:
        """The break caught here would accept an unhealthy candidate gallery response."""
        self.event("non-200-private-name", photo_count=1)

        output = StringIO()
        with patch(
            "picflow.management.commands.smoke_gallery_media_projection.event_detail",
            return_value=HttpResponse(status=503),
        ):
            with self.assertRaisesRegex(CommandError, "non-200") as error:
                call_command("smoke_gallery_media_projection", stdout=output)

        self.assertEqual(output.getvalue(), "")
        self.assertNotIn("non-200-private-name", str(error.exception))
