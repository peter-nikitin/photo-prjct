from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from picflow.models import Event, Photo
from selfie_search.models import SelfieSearch

from processing.models import (
    EventFaceEmbeddingActivation,
    FaceEmbedding,
    FaceEmbeddingVector,
    FaceProcessingAttemptArtifact,
    PhotoDerivative,
    PhotoFaceDetection,
    PhotoFaceEmbeddingProjection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services import enrollment
from processing.services.face_quality import active_face_embedding_generations


class HistoricalAdaFaceBackfillTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="historical-adaface")
        self.event = Event.objects.create(
            name="Historical event",
            slug="historical-event",
            city="Moscow",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 8),
            publication_status=Event.PublicationStatus.UNAVAILABLE,
            face_search_generation=Event.FaceSearchGeneration.SFACE_V3,
        )

    def photo(self, identifier: str) -> Photo:
        photo = Photo.objects.create(
            id=identifier,
            event=self.event,
            uploaded_by=self.user,
            original_key="originals/" + hashlib.sha256(identifier.encode()).hexdigest()[:32],
            original_filename=f"{identifier}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )
        state = enrollment.request_processor(
            photo,
            processor_type="generate_preview",
            contract_version=2,
            processor_version=1,
            configuration=enrollment.GENERATE_PREVIEW_CONFIGURATION,
        )
        job = state.current_job
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=job.run,
            job=job,
            photo=photo,
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            configuration=job.configuration,
            input_fingerprint=job.input_fingerprint,
            status="succeeded",
            accepted=True,
            terminal_at=timezone.now(),
        )
        PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-small-v1",
            final_key=(
                f"derivatives/previews/{identifier}/preview-small-v1/{attempt.pk}-{'a' * 64}.jpg"
            ),
            byte_size=8,
            content_type="image/jpeg",
            width=1600,
            height=1000,
            oriented_source_width=1600,
            oriented_source_height=1000,
            sha256="a" * 64,
            accepted_attempt=attempt,
        )
        PhotoProcessingState.objects.filter(pk=state.pk).update(
            status="succeeded", accepted_attempt=attempt
        )
        return photo

    def command(self, **options):
        output = StringIO()
        call_command(
            "backfill_historical_adaface", event_slug=self.event.slug, stdout=output, **options
        )
        return json.loads(output.getvalue())

    def apply(self, limit: int = 1):
        receipt = self.command(limit=limit)
        return self.command(apply=True, limit=limit, cohort_sha256=receipt["cohort_sha256"])

    def terminal(
        self, photo: Photo, *, kept: bool = True, rejected: bool = False, vector: bool = True
    ):
        job = ProcessingJob.objects.get(photo=photo, processor_version=5)
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=job.run,
            job=job,
            photo=photo,
            contract_version=3,
            processor_type="face_embedding",
            processor_version=5,
            configuration=job.configuration,
            input_fingerprint=job.input_fingerprint,
            accepted=True,
            status="succeeded",
            terminal_at=timezone.now(),
        )
        artifact = FaceProcessingAttemptArtifact.objects.create(attempt=attempt)
        detection = None
        if kept or rejected:
            detection = PhotoFaceDetection.objects.create(
                attempt=attempt,
                artifact=artifact,
                face_index=0,
                status="kept" if kept else "quality_rejected",
            )
        if kept and vector:
            FaceEmbeddingVector.objects.create(
                detection=detection,
                model_version="adaface-ir18-webface4m",
                vector=[1.0] + [0.0] * 511,
            )
        PhotoFaceEmbeddingProjection.objects.create(
            photo=photo,
            contract_version=3,
            processor_version=5,
            configuration_hash=job.configuration_hash,
            accepted_attempt=attempt,
        )
        ProcessingJob.objects.filter(pk=job.pk).update(status="succeeded")
        PhotoProcessingState.objects.filter(photo=photo, processor_type="face_embedding").update(
            status="succeeded",
            accepted_attempt=attempt,
            current_attempt=attempt,
        )
        return attempt, detection

    def activate(self):
        return self.command(
            activate=True,
            confirm_reviewed=True,
            quality_review_sha256="c" * 64,
            cohort_sha256=self.command()["cohort_sha256"],
        )

    def test_separate_operator_command_is_available(self) -> None:
        self.assertIn("backfill_historical_adaface", get_commands())

    def test_dry_run_and_bounded_resume_include_hidden_unavailable_photos(self) -> None:
        self.photo("historical-a")
        hidden = self.photo("historical-b")
        Photo.objects.filter(pk=hidden.pk).update(is_hidden=True)
        receipt = self.command(limit=1)
        self.assertEqual(receipt["photo_count"], 2)
        self.assertEqual(receipt["not_enrolled_count"], 2)
        self.assertEqual(ProcessingJob.objects.filter(processor_version=5).count(), 0)
        self.assertEqual(self.apply()["created_job_count"], 1)
        self.assertEqual(self.apply()["created_job_count"], 1)
        self.assertEqual(self.apply()["created_job_count"], 0)
        self.assertEqual(ProcessingJob.objects.filter(processor_version=5).count(), 2)
        for job in ProcessingJob.objects.filter(processor_version=5):
            self.assertEqual(job.configuration["embedding_storage"], "vector_only")
            self.assertEqual(job.run.configuration_hash, job.configuration_hash)
        serialized = json.dumps(self.command())
        for secret in ("historical-a", "historical-b", "originals/", "derivatives/", "vector"):
            self.assertNotIn(secret, serialized)
        self.event.refresh_from_db()
        self.assertEqual(self.event.face_search_generation, Event.FaceSearchGeneration.SFACE_V3)

    def test_positive_bound_and_explicit_snapshot_are_required(self) -> None:
        self.photo("historical-a")
        for limit in (0, -1, 17):
            with self.assertRaises(CommandError):
                self.command(apply=True, limit=limit, cohort_sha256="a" * 64)
        with self.assertRaises(CommandError):
            self.command(apply=True, limit=1)
        self.assertEqual(ProcessingJob.objects.filter(processor_version=5).count(), 0)

    def test_missing_source_or_preview_remains_backlog_blocker(self) -> None:
        photo = self.photo("historical-a")
        Photo.objects.filter(pk=photo.pk).update(original_key="")
        self.assertEqual(self.command()["source_blocker_count"], 1)
        self.assertEqual(self.command()["not_enrolled_count"], 1)
        self.assertEqual(self.command()["paused_photo_count"], 0)
        self.assertEqual(self.apply()["created_job_count"], 0)
        Photo.objects.filter(pk=photo.pk).update(original_key="originals/" + "b" * 32)
        PhotoProcessingState.objects.filter(photo=photo, processor_type="generate_preview").update(
            status="failed"
        )
        self.assertEqual(self.command()["source_blocker_count"], 1)
        with self.assertRaises(CommandError):
            self.activate()

    def test_inventory_and_source_change_block_resume_without_additional_jobs(self) -> None:
        self.photo("historical-a")
        self.apply()
        self.photo("historical-b")
        with self.assertRaises(CommandError):
            self.apply()
        self.assertEqual(ProcessingJob.objects.filter(processor_version=5).count(), 1)

    def test_active_old_job_is_not_replaced(self) -> None:
        photo = self.photo("historical-a")
        old = enrollment.request_processor(
            photo,
            processor_type="face_embedding",
            contract_version=3,
            processor_version=4,
            configuration=enrollment.FACE_EMBEDDING_QUALITY_CONFIGURATION,
        )
        self.assertIsNotNone(old.current_job)
        receipt = self.command()
        self.assertEqual(receipt["active_processing_count"], 1)
        self.assertEqual(self.apply()["created_job_count"], 0)
        old.refresh_from_db()
        self.assertEqual(old.current_job.processor_version, 4)

    def test_terminal_native_cohort_switches_event_and_blocks_rollback(self) -> None:
        photo = self.photo("historical-a")
        self.apply()
        self.terminal(photo)
        self.assertEqual(self.command()["accepted_photo_count"], 1)
        self.activate()
        self.event.refresh_from_db()
        self.assertEqual(self.event.face_search_generation, Event.FaceSearchGeneration.ADAFACE_V5)
        configuration = active_face_embedding_generations(self.event)[0]["configuration"]
        assert isinstance(configuration, dict)
        self.assertEqual(
            configuration["embedding_storage"],
            "vector_only",
        )
        self.assertEqual(FaceEmbedding.objects.count(), 0)
        call_command_result = self.activate()
        self.assertEqual(call_command_result["mode"], "activate")
        self.assertEqual(EventFaceEmbeddingActivation.objects.count(), 1)
        from processing.services.face_quality import (
            activate_face_embedding_generation,
            baseline_face_embedding_generations,
        )

        with self.assertRaises(ValueError):
            activate_face_embedding_generation(
                event=self.event,
                generations=baseline_face_embedding_generations(),
                approved_configuration_hash="",
                evaluation_report_hash="",
                review_confirmed=True,
            )

    def test_failed_job_missing_native_evidence_and_queued_search_block_activation(self) -> None:
        photo = self.photo("historical-a")
        self.apply()
        with self.assertRaises(CommandError):
            self.activate()
        attempt, detection = self.terminal(photo, vector=False)
        with self.assertRaises(CommandError):
            self.activate()
        FaceEmbeddingVector.objects.create(
            detection=detection, model_version="adaface-ir18-webface4m", vector=[1.0] + [0.0] * 511
        )
        search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="a" * 64,
            temporary_object_key="private-selfie",
            configuration={"model": "sface"},
        )
        with self.assertRaises(CommandError):
            self.activate()
        search.refresh_from_db()
        self.assertEqual(search.configuration, {"model": "sface"})
        self.event.refresh_from_db()
        self.assertEqual(self.event.face_search_generation, Event.FaceSearchGeneration.SFACE_V3)
        SelfieSearch.objects.filter(pk=search.pk).update(status="ready")
        ProcessingJob.objects.filter(pk=attempt.job_id).update(status="failed")
        with self.assertRaises(CommandError):
            self.activate()

    def test_no_face_and_quality_rejection_are_explicit_terminal_outcomes(self) -> None:
        first = self.photo("historical-a")
        second = self.photo("historical-b")
        self.apply(limit=2)
        self.terminal(first, kept=False)
        self.terminal(second, kept=False, rejected=True)
        report = self.command()
        self.assertEqual(report["no_face_photo_count"], 1)
        self.assertEqual(report["quality_rejected_photo_count"], 1)
        self.activate()

    def test_closed_batch_retains_exact_receipt_and_resume(self) -> None:
        first = self.photo("historical-a")
        self.photo("historical-b")
        receipt = self.command()
        self.apply()
        attempt, _ = self.terminal(first)
        from processing.services.reports import close_run_report

        type(attempt.run).objects.filter(pk=attempt.run_id).update(status="sealed")
        closed = close_run_report(attempt.run_id)
        assert closed is not None
        self.assertEqual(
            closed.report["historical_adaface_backfill"],
            {"cohort_sha256": receipt["cohort_sha256"], "photo_count": 2},
        )
        self.assertEqual(self.apply()["created_job_count"], 1)

    def test_source_change_and_conflicting_receipt_fail_closed(self) -> None:
        photo = self.photo("historical-a")
        self.apply()
        run = ProcessingJob.objects.get(photo=photo, processor_version=5).run
        run.report = {"historical_adaface_backfill": {"cohort_sha256": "f" * 64, "photo_count": 1}}
        run.save(update_fields=["report"])
        with self.assertRaises(CommandError):
            self.command()
        run.report = {
            "historical_adaface_backfill": {"cohort_sha256": "f" * 64, "photo_count": True}
        }
        run.save(update_fields=["report"])
        with self.assertRaises(CommandError):
            self.command()

    def test_old_searchable_photo_losing_its_faces_is_a_blocker(self) -> None:
        photo = self.photo("historical-a")
        state = enrollment.request_processor(
            photo,
            processor_type="face_embedding",
            contract_version=3,
            processor_version=4,
            configuration=enrollment.FACE_EMBEDDING_QUALITY_CONFIGURATION,
        )
        job = state.current_job
        old = ProcessingAttempt.objects.create(
            event=self.event,
            run=job.run,
            job=job,
            photo=photo,
            contract_version=3,
            processor_type="face_embedding",
            processor_version=4,
            configuration=job.configuration,
            input_fingerprint=job.input_fingerprint,
            status="succeeded",
            accepted=True,
            terminal_at=timezone.now(),
        )
        artifact = FaceProcessingAttemptArtifact.objects.create(attempt=old)
        detection = PhotoFaceDetection.objects.create(
            attempt=old, artifact=artifact, face_index=0, status="kept"
        )
        FaceEmbedding.objects.create(
            detection=detection, model_version="sface", vector=[1.0] + [0.0] * 127
        )
        PhotoProcessingState.objects.filter(pk=state.pk).update(
            status="succeeded", accepted_attempt=old
        )
        self.apply()
        self.terminal(photo, kept=False)
        self.assertEqual(self.command()["replacement_blocker_count"], 1)
        with self.assertRaises(CommandError):
            self.activate()

    def test_live_photo_lease_and_unsafe_reader_prevent_activation(self) -> None:
        photo = self.photo("historical-a")
        self.apply()
        accepted, _ = self.terminal(photo)
        lease = ProcessingAttempt.objects.create(
            event=self.event,
            run=accepted.run,
            job=accepted.job,
            photo=photo,
            contract_version=3,
            processor_type="face_embedding",
            processor_version=5,
            configuration=accepted.configuration,
            input_fingerprint=accepted.input_fingerprint,
            status="in_progress",
            lease_expires_at=timezone.now() + timedelta(minutes=1),
        )
        with self.assertRaises(CommandError):
            self.activate()
        ProcessingAttempt.objects.filter(pk=lease.pk).update(
            status="expired", terminal_at=timezone.now()
        )
        with patch("selfie_search.services.read_selection.select_reader", return_value="legacy"):
            with self.assertRaises(CommandError):
                self.activate()
        self.event.refresh_from_db()
        self.assertEqual(self.event.face_search_generation, Event.FaceSearchGeneration.SFACE_V3)

    def test_new_photos_after_activation_use_the_native_generation(self) -> None:
        first = self.photo("historical-a")
        self.apply()
        self.terminal(first)
        self.activate()
        self.event.refresh_from_db()
        second = self.photo("historical-b")
        with self.settings(PHOTO_PROCESSING_FACE_ENABLED=True):
            state = enrollment.request_face_embedding_enqueue(second)
        self.assertEqual(state.current_job.configuration.get("embedding_storage"), "vector_only")
        self.assertNotIn("historical_adaface_backfill", state.current_job.run.report)

    def test_selfie_submission_refreshes_generation_after_storage_put(self) -> None:
        from selfie_search.images import PreparedSelfie
        from selfie_search.services.submission import submit_selfie_search
        from selfie_search.tests.test_submission import RecordingStorage

        photo = self.photo("historical-a")
        self.apply()
        self.terminal(photo)
        Event.objects.filter(pk=self.event.pk).update(
            publication_status=Event.PublicationStatus.PUBLISHED
        )
        self.event.refresh_from_db()
        storage = RecordingStorage()
        original_put = storage.put

        def activate_during_upload(**kwargs):
            self.activate()
            return original_put(**kwargs)

        with patch.object(storage, "put", side_effect=activate_during_upload):
            created = submit_selfie_search(
                event=self.event,
                selfie=PreparedSelfie(
                    content=b"image", content_type="image/jpeg", source_size=5, source_format="jpeg"
                ),
                storage=storage,
                user=self.user,
            )
        self.assertEqual(
            created.search.configuration["gallery_face_embedding_generations"][0][
                "configuration"
            ].get("embedding_storage"),
            "vector_only",
        )

    def test_status_reads_scalar_evidence_in_bounded_queries(self) -> None:
        photos = [self.photo(f"bounded-{index}") for index in range(8)]
        self.apply(limit=8)
        for photo in photos:
            self.terminal(photo)
        with CaptureQueriesContext(connection) as queries:
            report = self.command()
        self.assertEqual(report["accepted_photo_count"], 8)
        self.assertLessEqual(len(queries), 24)
        self.assertFalse(
            any('"processing_faceembeddingvector"."vector",' in q["sql"] for q in queries)
        )
        for field in (
            "result",
            "error_detail",
            "download_duration_ms",
            "compute_duration_ms",
            "total_duration_ms",
        ):
            self.assertFalse(
                any(f'"processing_processingattempt"."{field}"' in q["sql"] for q in queries),
                f"scalar reconciliation must not select attempt {field}",
            )

    def test_native_selection_rejects_divergent_event_model(self) -> None:
        photo = self.photo("historical-a")
        self.apply()
        self.terminal(photo)
        self.activate()
        # A stale Event instance must not silently mix a native cohort and an old query model.
        self.event.refresh_from_db()
        self.event.face_search_generation = Event.FaceSearchGeneration.SFACE_V3
        with self.assertRaises(ValueError):
            active_face_embedding_generations(self.event)

    def test_activation_preserves_ready_membership_rank_and_frozen_configuration(self) -> None:
        from selfie_search.models import SelfieSearchResult

        photo = self.photo("historical-a")
        self.apply()
        self.terminal(photo)
        configuration = {"embedding_model": "sface", "legacy_snapshot": True}
        search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="d" * 64,
            temporary_object_key="",
            configuration=configuration,
        )
        result = SelfieSearchResult.objects.create(search=search, photo=photo, rank=1)
        SelfieSearch.objects.filter(pk=search.pk).update(status="ready", terminal_at=timezone.now())
        self.activate()
        search.refresh_from_db()
        result.refresh_from_db()
        self.assertEqual(search.status, "ready")
        self.assertEqual(search.configuration, configuration)
        self.assertEqual(
            (result.photo_id, result.rank, result.primary_source), (photo.pk, 1, "direct")
        )

    def test_failed_batch_pauses_following_enrollment(self) -> None:
        first = self.photo("historical-a")
        self.photo("historical-b")
        self.apply()
        ProcessingJob.objects.filter(photo=first, processor_version=5).update(status="failed")
        self.assertEqual(self.command()["failure_photo_count"], 1)
        with self.assertRaises(CommandError):
            self.apply()
        self.assertEqual(ProcessingJob.objects.filter(processor_version=5).count(), 1)
        self.event.refresh_from_db()
        self.assertEqual(self.event.face_search_generation, Event.FaceSearchGeneration.SFACE_V3)

    def test_changed_private_source_requires_action_instead_of_a_new_cohort(self) -> None:
        photo = self.photo("historical-a")
        self.apply()
        Photo.objects.filter(pk=photo.pk).update(original_size=11)
        with self.assertRaises(CommandError):
            self.command()

    def test_photo_enrollment_refreshes_stale_event_generation_under_lock(self) -> None:
        first = self.photo("historical-a")
        self.apply()
        self.terminal(first)
        self.activate()
        # Keep the SFace Event instance retained by ingestion before the activation boundary.
        second = self.photo("historical-b")
        with self.settings(PHOTO_PROCESSING_FACE_ENABLED=True):
            state = enrollment.request_face_embedding_enqueue(second)
        self.assertEqual(state.current_job.configuration.get("embedding_storage"), "vector_only")
