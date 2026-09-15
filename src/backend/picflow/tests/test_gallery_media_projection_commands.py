from __future__ import annotations

import json
from datetime import date
from io import StringIO
from typing import cast
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.models.query import QuerySet
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from processing.models import (
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)

from picflow.gallery_media_projection import (
    rebuild_gallery_media_projection,
    verify_gallery_media_projection,
)
from picflow.models import Event, GalleryMediaProjection, Photo


class GalleryMediaProjectionCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="gallery-rebuild-owner")
        self.event = Event.objects.create(
            name="Gallery rebuild",
            slug="gallery-rebuild",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

    def photo(self, suffix: str) -> Photo:
        return Photo.objects.create(
            id=f"gallery-rebuild-{suffix}",
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/private-{suffix}",
            original_filename=f"private-{suffix}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def derivative(
        self,
        photo: Photo,
        *,
        variant: str = "preview-small-v1",
        processor_type: str = GENERATE_PREVIEW_PROCESSOR,
        state_status: str = cast(str, PhotoProcessingState.Status.SUCCEEDED),
        state_accepts_attempt: bool = True,
    ) -> PhotoDerivative:
        configuration = {processor_type: {"variant": variant}}
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=2,
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            configuration_hash=uuid4().hex + uuid4().hex,
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=processor_type,
            processor_version=1,
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
            contract_version=2,
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        PhotoProcessingState.objects.create(
            photo=photo,
            processor_type=processor_type,
            status=state_status,
            current_run=run,
            current_job=job,
            current_attempt=attempt,
            accepted_attempt=attempt if state_accepts_attempt else None,
            succeeded_at=(
                timezone.now() if state_status == PhotoProcessingState.Status.SUCCEEDED else None
            ),
        )
        return PhotoDerivative.objects.create(
            photo=photo,
            variant=variant,
            final_key=f"derivatives/private-{suffix_for(photo)}/{variant}/{uuid4()}.jpg",
            byte_size=10,
            content_type="image/jpeg",
            width=10,
            height=10,
            oriented_source_width=10,
            oriented_source_height=10,
            sha256="a" * 64,
            accepted_attempt=attempt,
        )

    def command_json(self, command: str, *arguments: str) -> dict[str, object]:
        output = StringIO()
        call_command(command, *arguments, stdout=output)
        return json.loads(output.getvalue())

    def test_commands_require_explicit_all_events_scope_before_work(self) -> None:
        """The break caught here would let an operator run an ambiguously scoped repair."""
        for command in (
            "rebuild_gallery_media_projection",
            "verify_gallery_media_projection",
        ):
            with self.subTest(command=command):
                output = StringIO()
                with CaptureQueriesContext(connection) as queries:
                    with self.assertRaisesRegex(CommandError, "--all-events"):
                        call_command(command, stdout=output)

                self.assertEqual(output.getvalue(), "")
                self.assertEqual(list(queries), [])

    def test_empty_database_reports_clean_aggregate_counts(self) -> None:
        """The break caught here would make an empty projection unsafe to preflight."""
        rebuild = self.command_json("rebuild_gallery_media_projection", "--all-events")
        verification = self.command_json("verify_gallery_media_projection", "--all-events")

        self.assertEqual(
            rebuild,
            {
                "action": "dry_run",
                "changed": 0,
                "inserted": 0,
                "removed": 0,
                "scope": "all_events",
            },
        )
        self.assertEqual(
            verification,
            {
                "clean": True,
                "expected_count": 0,
                "mismatch_count": 0,
                "projected_count": 0,
                "scope": "all_events",
            },
        )

    def test_rebuild_defaults_to_dry_run_and_derives_both_slots(self) -> None:
        """The break caught here would mutate implicitly or lose one supported preview slot."""
        photo = self.photo("both-slots")
        clean = self.derivative(photo)
        watermark = self.derivative(
            photo,
            variant="preview-watermarked-v1",
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
        )

        report = self.command_json("rebuild_gallery_media_projection", "--all-events")

        self.assertEqual(report["action"], "dry_run")
        self.assertEqual(report["inserted"], 1)
        self.assertEqual(report["changed"], 0)
        self.assertEqual(report["removed"], 0)
        self.assertFalse(GalleryMediaProjection.objects.filter(photo=photo).exists())
        rendered = json.dumps(report, sort_keys=True)
        for private_value in (
            photo.pk,
            photo.original_filename,
            clean.final_key,
            str(clean.accepted_attempt_id),
            watermark.final_key,
            str(watermark.accepted_attempt_id),
        ):
            self.assertNotIn(private_value, rendered)

        applied = self.command_json("rebuild_gallery_media_projection", "--all-events", "--apply")
        projection = GalleryMediaProjection.objects.get(photo=photo)
        self.assertEqual(applied["action"], "applied")
        self.assertEqual(projection.clean_preview_final_key, clean.final_key)
        self.assertEqual(projection.watermarked_preview_final_key, watermark.final_key)

    def test_apply_is_set_oriented_and_an_exact_repeat_is_idempotent(self) -> None:
        """The break caught here would rebuild row-by-row or rewrite already exact rows."""
        first_photo = self.photo("apply-one")
        first = self.derivative(first_photo)
        second_photo = self.photo("apply-two")
        second = self.derivative(second_photo)

        with CaptureQueriesContext(connection) as queries:
            applied = rebuild_gallery_media_projection(apply=True)
        repeated = rebuild_gallery_media_projection(apply=True)

        writes = [
            query["sql"]
            for query in queries
            if 'insert into "picflow_gallerymediaprojection"' in query["sql"].lower()
            or 'delete from "picflow_gallerymediaprojection"' in query["sql"].lower()
        ]
        self.assertEqual(len(writes), 2)
        self.assertTrue(all("gallery-rebuild-" not in statement for statement in writes))
        self.assertEqual((applied.inserted, applied.changed, applied.removed), (2, 0, 0))
        self.assertEqual((repeated.inserted, repeated.changed, repeated.removed), (0, 0, 0))
        self.assertEqual(GalleryMediaProjection.objects.count(), 2)
        self.assertEqual(
            GalleryMediaProjection.objects.get(photo=first_photo).clean_preview_final_key,
            first.final_key,
        )
        self.assertEqual(
            GalleryMediaProjection.objects.get(photo=second_photo).clean_preview_final_key,
            second.final_key,
        )

    def test_failed_and_unaccepted_current_evidence_is_ignored(self) -> None:
        """The break caught here would publish a preview without accepted successful evidence."""
        self.derivative(
            self.photo("failed"),
            state_status=cast(str, PhotoProcessingState.Status.FAILED),
        )
        self.derivative(self.photo("unaccepted"), state_accepts_attempt=False)

        report = rebuild_gallery_media_projection(apply=True)

        self.assertEqual((report.inserted, report.changed, report.removed), (0, 0, 0))
        self.assertFalse(GalleryMediaProjection.objects.exists())

    def test_rebuild_repairs_missing_changed_and_extra_rows(self) -> None:
        """The break caught here would leave any side of the expected/actual drift unrepaired."""
        missing_photo = self.photo("missing")
        missing = self.derivative(missing_photo)
        changed_photo = self.photo("changed")
        changed = self.derivative(changed_photo)
        extra_photo = self.photo("extra")
        GalleryMediaProjection.objects.create(
            photo=changed_photo,
            clean_preview_final_key=changed.final_key,
            clean_preview_source_attempt=missing.accepted_attempt,
        )
        GalleryMediaProjection.objects.create(
            photo=extra_photo,
            clean_preview_final_key="derivatives/private-extra.jpg",
            clean_preview_source_attempt=missing.accepted_attempt,
        )

        dry_run = rebuild_gallery_media_projection(apply=False)
        before = verify_gallery_media_projection()
        applied = rebuild_gallery_media_projection(apply=True)
        after = verify_gallery_media_projection()

        self.assertEqual((dry_run.inserted, dry_run.changed, dry_run.removed), (1, 1, 1))
        self.assertEqual((before.expected_count, before.projected_count), (2, 2))
        self.assertEqual(before.mismatch_count, 4)
        self.assertFalse(before.clean)
        self.assertEqual(applied, dry_run)
        self.assertTrue(after.clean)
        self.assertEqual(after.mismatch_count, 0)
        self.assertFalse(GalleryMediaProjection.objects.filter(photo=extra_photo).exists())
        repaired = GalleryMediaProjection.objects.get(photo=changed_photo)
        self.assertEqual(repaired.clean_preview_source_attempt_id, changed.accepted_attempt_id)

    def test_verifier_is_one_symmetric_difference_query_without_model_loading(self) -> None:
        """The break caught here would regress verification to private per-photo inspection."""
        self.derivative(self.photo("fixed-shape-one"))
        self.derivative(self.photo("fixed-shape-two"))

        with (
            patch.object(
                QuerySet,
                "_fetch_all",
                side_effect=AssertionError("verification loaded model rows"),
            ),
            CaptureQueriesContext(connection) as queries,
        ):
            report = verify_gallery_media_projection()

        self.assertFalse(report.clean)
        self.assertEqual(len(queries), 1)
        statement = queries[0]["sql"].lower()
        self.assertIn("except", statement)
        self.assertIn("union all", statement)
        self.assertNotIn("gallery-rebuild-fixed-shape", statement)

    def test_require_clean_prints_privacy_safe_aggregate_json_before_failure(self) -> None:
        """The break caught here would hide drift or leak media evidence during deployment."""
        photo = self.photo("require-clean")
        derivative = self.derivative(photo)
        output = StringIO()

        with self.assertRaisesRegex(CommandError, "projection reconciliation is not clean"):
            call_command(
                "verify_gallery_media_projection",
                "--all-events",
                "--require-clean",
                stdout=output,
            )

        report = json.loads(output.getvalue())
        self.assertFalse(report["clean"])
        self.assertEqual(report["mismatch_count"], 1)
        rendered = json.dumps(report, sort_keys=True)
        for private_value in (
            photo.pk,
            photo.original_filename,
            derivative.final_key,
            str(derivative.accepted_attempt_id),
        ):
            self.assertNotIn(private_value, rendered)


def suffix_for(photo: Photo) -> str:
    return str(photo.pk).removeprefix("gallery-rebuild-")
