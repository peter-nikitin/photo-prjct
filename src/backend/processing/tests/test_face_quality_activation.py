from __future__ import annotations

import hashlib
import json
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import (
    EventFaceEmbeddingActivation,
    EventProcessingRun,
    FaceProcessingAttemptArtifact,
    PhotoDerivative,
    PhotoFaceDetection,
    PhotoFaceEmbeddingProjection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    FACE_EMBEDDING_QUALITY_CONFIGURATION,
    GENERATE_PREVIEW_CONFIGURATION,
    HISTORICAL_QUALITY_FACE_PROCESSOR_VERSION,
    QUALITY_FACE_CONTRACT_VERSION,
    QUALITY_FACE_PROCESSOR_VERSION,
    FaceEmbeddingGenerationApproval,
    request_processor,
)
from processing.services.face_quality import (
    activate_face_embedding_generation,
    active_face_embedding_generations,
    baseline_face_embedding_generations,
    candidate_face_embedding_generations,
    historical_quality_face_embedding_generations,
)


class FaceEmbeddingActivationTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="quality-activation-owner")
        self.event = self.make_event("main")
        self.other_event = self.make_event("other")

    def make_event(self, suffix: str) -> Event:
        return Event.objects.create(
            name=f"Quality activation {suffix}",
            slug=f"quality-activation-{suffix}",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 8),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )

    def publish_candidate_projection(self, *, event: Event, photo_id: str) -> ProcessingAttempt:
        generation = candidate_face_embedding_generations()[0]
        photo = Photo.objects.create(
            id=photo_id,
            event=event,
            uploaded_by=self.user,
            original_key=f"originals/{hashlib.sha256(photo_id.encode()).hexdigest()[:32]}",
            original_filename=f"{photo_id}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        run = EventProcessingRun.objects.create(
            event=event,
            contract_version=QUALITY_FACE_CONTRACT_VERSION,
            processor_type="face_embedding",
            processor_version=QUALITY_FACE_PROCESSOR_VERSION,
            configuration=FACE_EMBEDDING_QUALITY_CONFIGURATION,
            configuration_hash=generation["configuration_hash"],
        )
        job = ProcessingJob.objects.create(
            event=event,
            run=run,
            photo=photo,
            contract_version=QUALITY_FACE_CONTRACT_VERSION,
            processor_type="face_embedding",
            processor_version=QUALITY_FACE_PROCESSOR_VERSION,
            configuration=FACE_EMBEDDING_QUALITY_CONFIGURATION,
            configuration_hash=generation["configuration_hash"],
            input_fingerprint={},
            status=ProcessingJob.Status.SUCCEEDED,
        )
        attempt = ProcessingAttempt.objects.create(
            event=event,
            run=run,
            job=job,
            photo=photo,
            contract_version=QUALITY_FACE_CONTRACT_VERSION,
            processor_type="face_embedding",
            processor_version=QUALITY_FACE_PROCESSOR_VERSION,
            configuration=FACE_EMBEDDING_QUALITY_CONFIGURATION,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        PhotoFaceEmbeddingProjection.objects.create(
            photo=photo,
            contract_version=QUALITY_FACE_CONTRACT_VERSION,
            processor_version=QUALITY_FACE_PROCESSOR_VERSION,
            configuration_hash=generation["configuration_hash"],
            accepted_attempt=attempt,
        )
        preview_state = request_processor(
            photo,
            processor_type="generate_preview",
            contract_version=2,
            processor_version=1,
            configuration=GENERATE_PREVIEW_CONFIGURATION,
            input_fingerprint={
                "object_key": photo.original_key,
                "object_size": photo.original_size,
                "object_content_type": photo.original_content_type,
                "object_etag": None,
                "media_kind": "original",
                "pixel_width": 1600,
                "pixel_height": 1000,
            },
        )
        assert preview_state.current_job is not None
        preview_attempt = ProcessingAttempt.objects.create(
            event=event,
            run=preview_state.current_job.run,
            job=preview_state.current_job,
            photo=photo,
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            configuration=GENERATE_PREVIEW_CONFIGURATION,
            input_fingerprint=preview_state.current_job.input_fingerprint,
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-small-v1",
            final_key=f"previews/{photo_id}.jpg",
            byte_size=8,
            content_type="image/jpeg",
            width=1600,
            height=1000,
            oriented_source_width=1600,
            oriented_source_height=1000,
            sha256="a" * 64,
            accepted_attempt=preview_attempt,
        )
        preview_state.status = PhotoProcessingState.Status.SUCCEEDED
        preview_state.current_attempt = preview_attempt
        preview_state.accepted_attempt = preview_attempt
        preview_state.succeeded_at = timezone.now()
        preview_state.save(
            update_fields=[
                "status",
                "current_attempt",
                "accepted_attempt",
                "succeeded_at",
                "updated_at",
            ]
        )
        return attempt

    def approval(
        self,
        *,
        event: Event | None = None,
        photo_count: int = 1,
        approved: bool = True,
        technical_failure_count: int = 0,
        kept_face_count: int = 0,
        quality_rejected_face_count: int = 0,
    ) -> FaceEmbeddingGenerationApproval:
        generation = candidate_face_embedding_generations()[0]
        configuration_hash = generation["configuration_hash"]
        assert isinstance(configuration_hash, str)
        return FaceEmbeddingGenerationApproval(
            event_slug=(event or self.event).slug,
            photo_count=photo_count,
            configuration_hash=configuration_hash,
            preview_manifest_hash="a" * 64,
            comparison_manifest_hash="d" * 64,
            yunet_model_hash="b" * 64,
            sface_model_hash="c" * 64,
            job_count=photo_count,
            attempt_count=photo_count,
            projection_count=photo_count,
            technical_failure_count=technical_failure_count,
            kept_face_count=kept_face_count,
            quality_rejected_face_count=quality_rejected_face_count,
            approved=approved,
        )

    def test_event_without_activation_resolves_only_the_frozen_baseline(self) -> None:
        self.assertEqual(
            active_face_embedding_generations(self.event),
            baseline_face_embedding_generations(),
        )
        self.assertFalse(EventFaceEmbeddingActivation.objects.exists())

    def test_v4_is_current_candidate_and_v3_remains_a_distinct_historical_generation(self) -> None:
        candidate = candidate_face_embedding_generations()[0]
        historical = historical_quality_face_embedding_generations()[0]

        self.assertEqual(candidate["processor_version"], QUALITY_FACE_PROCESSOR_VERSION)
        self.assertEqual(historical["processor_version"], HISTORICAL_QUALITY_FACE_PROCESSOR_VERSION)
        self.assertEqual(candidate["configuration"], historical["configuration"])
        self.assertEqual(candidate["configuration_hash"], historical["configuration_hash"])
        self.assertNotEqual(candidate, historical)

    def test_existing_historical_v3_activation_resolves_exactly(self) -> None:
        generations = list(historical_quality_face_embedding_generations())
        EventFaceEmbeddingActivation.objects.create(
            event=self.event,
            generations=generations,
            generation_set_hash=hashlib.sha256(
                json.dumps(generations, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            approved_configuration_hash=generations[0]["configuration_hash"],
            approved_evaluation_report_hash="d" * 64,
        )

        self.assertEqual(
            active_face_embedding_generations(self.event),
            historical_quality_face_embedding_generations(),
        )

    def test_invalid_latest_v4_activation_fails_closed_without_historical_fallback(self) -> None:
        activate_face_embedding_generation(
            event=self.event,
            generations=historical_quality_face_embedding_generations(),
            approved_configuration_hash="",
            evaluation_report_hash="",
            review_confirmed=True,
        )
        generations = list(candidate_face_embedding_generations())
        EventFaceEmbeddingActivation.objects.create(
            event=self.event,
            generations=generations,
            generation_set_hash=hashlib.sha256(
                json.dumps(generations, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        )

        with self.assertRaisesRegex(ValueError, "approved benchmark evidence"):
            active_face_embedding_generations(self.event)

    def test_explicit_rollback_appends_and_selects_the_historical_v3_generation(self) -> None:
        baseline = activate_face_embedding_generation(
            event=self.event,
            generations=baseline_face_embedding_generations(),
            approved_configuration_hash="",
            evaluation_report_hash="",
            review_confirmed=True,
        )
        rollback = activate_face_embedding_generation(
            event=self.event,
            generations=historical_quality_face_embedding_generations(),
            approved_configuration_hash="",
            evaluation_report_hash="",
            review_confirmed=True,
        )

        self.assertNotEqual(baseline.pk, rollback.pk)
        self.assertEqual(EventFaceEmbeddingActivation.objects.count(), 2)
        self.assertEqual(
            active_face_embedding_generations(self.event),
            historical_quality_face_embedding_generations(),
        )

    def test_exact_baseline_replay_is_idempotent(self) -> None:
        first = activate_face_embedding_generation(
            event=self.event,
            generations=baseline_face_embedding_generations(),
            approved_configuration_hash="",
            evaluation_report_hash="",
            review_confirmed=True,
        )
        replay = activate_face_embedding_generation(
            event=self.event,
            generations=baseline_face_embedding_generations(),
            approved_configuration_hash="",
            evaluation_report_hash="",
            review_confirmed=True,
        )

        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(EventFaceEmbeddingActivation.objects.count(), 1)

    def test_activation_rows_are_append_only_at_model_and_database_boundaries(self) -> None:
        activation = activate_face_embedding_generation(
            event=self.event,
            generations=baseline_face_embedding_generations(),
            approved_configuration_hash="",
            evaluation_report_hash="",
            review_confirmed=True,
        )

        activation.approved_evaluation_report_hash = "d" * 64
        with self.assertRaisesRegex(ValidationError, "append-only"):
            activation.save(update_fields=["approved_evaluation_report_hash"])
        with self.assertRaises(ValidationError):
            activation.delete()
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                EventFaceEmbeddingActivation.objects.filter(pk=activation.pk).update(
                    approved_evaluation_report_hash="e" * 64
                )

        activation.refresh_from_db()
        self.assertEqual(activation.approved_evaluation_report_hash, "")

    def test_candidate_activation_and_baseline_rollback_append_history(self) -> None:
        self.publish_candidate_projection(event=self.event, photo_id="candidate")
        baseline = activate_face_embedding_generation(
            event=self.event,
            generations=baseline_face_embedding_generations(),
            approved_configuration_hash="",
            evaluation_report_hash="",
            review_confirmed=True,
        )
        approval = self.approval()
        with patch("processing.services.enrollment.FACE_EMBEDDING_QUALITY_APPROVAL", approval):
            candidate = activate_face_embedding_generation(
                event=self.event,
                generations=candidate_face_embedding_generations(),
                approved_configuration_hash=approval.configuration_hash,
                evaluation_report_hash=approval.comparison_manifest_hash,
                review_confirmed=True,
            )
        rollback = activate_face_embedding_generation(
            event=self.event,
            generations=baseline_face_embedding_generations(),
            approved_configuration_hash="",
            evaluation_report_hash="",
            review_confirmed=True,
        )

        self.assertEqual(EventFaceEmbeddingActivation.objects.count(), 3)
        self.assertNotEqual(baseline.pk, candidate.pk)
        self.assertNotEqual(candidate.pk, rollback.pk)
        self.assertEqual(
            active_face_embedding_generations(self.event), baseline_face_embedding_generations()
        )

    def test_candidate_is_non_activatable_while_approval_is_provisional(self) -> None:
        self.publish_candidate_projection(event=self.event, photo_id="candidate-unapproved")
        configuration_hash = candidate_face_embedding_generations()[0]["configuration_hash"]
        assert isinstance(configuration_hash, str)

        self.assertEqual(
            active_face_embedding_generations(self.event),
            baseline_face_embedding_generations(),
        )

        with self.assertRaisesRegex(ValueError, "approved benchmark evidence"):
            activate_face_embedding_generation(
                event=self.event,
                generations=candidate_face_embedding_generations(),
                approved_configuration_hash=configuration_hash,
                evaluation_report_hash="d" * 64,
                review_confirmed=True,
            )

        self.assertFalse(EventFaceEmbeddingActivation.objects.exists())

    def test_explicit_invalid_activation_fails_closed_without_baseline_fallback(self) -> None:
        EventFaceEmbeddingActivation.objects.create(
            event=self.event,
            generations=[],
            generation_set_hash="f" * 64,
        )

        with self.assertRaisesRegex(ValueError, "generation set"):
            active_face_embedding_generations(self.event)

    def test_direct_baseline_row_with_candidate_approval_shape_fails_closed(self) -> None:
        generations = list(baseline_face_embedding_generations())
        EventFaceEmbeddingActivation.objects.create(
            event=self.event,
            generations=generations,
            generation_set_hash=hashlib.sha256(
                json.dumps(generations, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            approved_configuration_hash="c" * 64,
            approved_evaluation_report_hash="d" * 64,
        )

        with self.assertRaisesRegex(ValueError, "baseline activation"):
            active_face_embedding_generations(self.event)

    def test_direct_candidate_rows_cannot_bypass_stored_approval_or_event_completeness(
        self,
    ) -> None:
        generations = list(candidate_face_embedding_generations())
        generation_set_hash = hashlib.sha256(
            json.dumps(generations, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        configuration_hash = generations[0]["configuration_hash"]
        assert isinstance(configuration_hash, str)

        missing = self.make_event("direct-missing")
        EventFaceEmbeddingActivation.objects.create(
            event=missing,
            generations=generations,
            generation_set_hash=generation_set_hash,
            approved_configuration_hash=configuration_hash,
            approved_evaluation_report_hash="d" * 64,
        )
        with self.assertRaisesRegex(ValueError, "approved benchmark evidence"):
            active_face_embedding_generations(missing)

        mismatched = self.make_event("direct-mismatched")
        self.publish_candidate_projection(event=mismatched, photo_id="direct-mismatch-projection")
        mismatched_approval = self.approval(event=mismatched)
        EventFaceEmbeddingActivation.objects.create(
            event=mismatched,
            generations=generations,
            generation_set_hash=generation_set_hash,
            approved_configuration_hash="e" * 64,
            approved_evaluation_report_hash=mismatched_approval.comparison_manifest_hash,
        )
        with patch(
            "processing.services.enrollment.FACE_EMBEDDING_QUALITY_APPROVAL",
            mismatched_approval,
        ):
            with self.assertRaisesRegex(ValueError, "approved benchmark evidence"):
                active_face_embedding_generations(mismatched)

        cross_event = self.make_event("direct-cross-event")
        self.publish_candidate_projection(event=cross_event, photo_id="direct-cross-projection")
        EventFaceEmbeddingActivation.objects.create(
            event=cross_event,
            generations=generations,
            generation_set_hash=generation_set_hash,
            approved_configuration_hash=configuration_hash,
            approved_evaluation_report_hash="d" * 64,
        )
        with patch(
            "processing.services.enrollment.FACE_EMBEDDING_QUALITY_APPROVAL",
            self.approval(event=self.event),
        ):
            with self.assertRaisesRegex(ValueError, "approved benchmark evidence"):
                active_face_embedding_generations(cross_event)

        incomplete = self.make_event("direct-incomplete")
        self.publish_candidate_projection(event=incomplete, photo_id="direct-complete-projection")
        Photo.objects.create(
            id="direct-missing-projection",
            event=incomplete,
            uploaded_by=self.user,
            original_key=f"originals/{'e' * 32}",
            original_filename="missing.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        incomplete_approval = self.approval(event=incomplete, photo_count=2)
        EventFaceEmbeddingActivation.objects.create(
            event=incomplete,
            generations=generations,
            generation_set_hash=generation_set_hash,
            approved_configuration_hash=configuration_hash,
            approved_evaluation_report_hash=incomplete_approval.comparison_manifest_hash,
        )
        with patch(
            "processing.services.enrollment.FACE_EMBEDDING_QUALITY_APPROVAL",
            incomplete_approval,
        ):
            with self.assertRaisesRegex(ValueError, "incomplete candidate evidence"):
                active_face_embedding_generations(incomplete)

    def test_candidate_rejects_cross_event_or_incomplete_approval(self) -> None:
        self.publish_candidate_projection(event=self.event, photo_id="candidate-cross-event")
        for approval in (
            self.approval(event=self.other_event),
            self.approval(approved=False),
            self.approval(photo_count=2),
            self.approval(technical_failure_count=1),
        ):
            with self.subTest(approval=approval):
                with patch(
                    "processing.services.enrollment.FACE_EMBEDDING_QUALITY_APPROVAL", approval
                ):
                    with self.assertRaises(ValueError):
                        activate_face_embedding_generation(
                            event=self.event,
                            generations=candidate_face_embedding_generations(),
                            approved_configuration_hash=approval.configuration_hash,
                            evaluation_report_hash=approval.comparison_manifest_hash,
                            review_confirmed=True,
                        )
        self.assertFalse(EventFaceEmbeddingActivation.objects.exists())

    def test_candidate_rejects_incomplete_event_projection_coverage(self) -> None:
        Photo.objects.create(
            id="missing-candidate",
            event=self.event,
            uploaded_by=self.user,
            original_key=f"originals/{'b' * 32}",
            original_filename="missing.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        approval = self.approval()

        with patch("processing.services.enrollment.FACE_EMBEDDING_QUALITY_APPROVAL", approval):
            with self.assertRaisesRegex(ValueError, "incomplete candidate evidence"):
                activate_face_embedding_generation(
                    event=self.event,
                    generations=candidate_face_embedding_generations(),
                    approved_configuration_hash=approval.configuration_hash,
                    evaluation_report_hash=approval.comparison_manifest_hash,
                    review_confirmed=True,
                )

    def test_candidate_rejects_technical_face_failure_in_successful_projection(self) -> None:
        """Ignoring a failed face result in an accepted v4 attempt must fail activation."""
        attempt = self.publish_candidate_projection(event=self.event, photo_id="technical-face")
        artifact = FaceProcessingAttemptArtifact.objects.create(attempt=attempt)
        PhotoFaceDetection.objects.create(
            artifact=artifact,
            attempt=attempt,
            face_index=0,
            status=PhotoFaceDetection.Status.FAILED,
        )
        approval = self.approval()

        with patch("processing.services.enrollment.FACE_EMBEDDING_QUALITY_APPROVAL", approval):
            with self.assertRaisesRegex(ValueError, "incomplete candidate evidence"):
                activate_face_embedding_generation(
                    event=self.event,
                    generations=candidate_face_embedding_generations(),
                    approved_configuration_hash=approval.configuration_hash,
                    evaluation_report_hash=approval.comparison_manifest_hash,
                    review_confirmed=True,
                )

    def test_candidate_rejects_kept_or_quality_rejected_face_count_mismatch(self) -> None:
        """Changing reviewed kept or rejected face totals must fail activation."""
        attempt = self.publish_candidate_projection(event=self.event, photo_id="count-mismatch")
        artifact = FaceProcessingAttemptArtifact.objects.create(attempt=attempt)
        PhotoFaceDetection.objects.create(
            artifact=artifact,
            attempt=attempt,
            face_index=0,
            status=PhotoFaceDetection.Status.KEPT,
        )
        approval = self.approval()

        with patch("processing.services.enrollment.FACE_EMBEDDING_QUALITY_APPROVAL", approval):
            with self.assertRaisesRegex(ValueError, "incomplete candidate evidence"):
                activate_face_embedding_generation(
                    event=self.event,
                    generations=candidate_face_embedding_generations(),
                    approved_configuration_hash=approval.configuration_hash,
                    evaluation_report_hash=approval.comparison_manifest_hash,
                    review_confirmed=True,
                )

    def test_candidate_revalidates_the_cohort_after_acquiring_the_event_lock(self) -> None:
        """A preview accepted between validation and activation must prevent the append."""
        initial_attempt = self.publish_candidate_projection(
            event=self.event, photo_id="before-lock"
        )
        initial_artifact = FaceProcessingAttemptArtifact.objects.create(attempt=initial_attempt)
        PhotoFaceDetection.objects.create(
            artifact=initial_artifact,
            attempt=initial_attempt,
            face_index=0,
            status=PhotoFaceDetection.Status.KEPT,
        )
        approval = self.approval(kept_face_count=1)
        original_select_for_update = Event.objects.select_for_update
        transition_applied = False

        def lock_then_publish():
            nonlocal transition_applied
            if not transition_applied:
                transition_applied = True
                self.publish_candidate_projection(event=self.event, photo_id="after-lock")
            return original_select_for_update()

        with patch.object(Event.objects, "select_for_update", side_effect=lock_then_publish):
            with patch("processing.services.enrollment.FACE_EMBEDDING_QUALITY_APPROVAL", approval):
                with self.assertRaisesRegex(ValueError, "incomplete candidate evidence"):
                    activate_face_embedding_generation(
                        event=self.event,
                        generations=candidate_face_embedding_generations(),
                        approved_configuration_hash=approval.configuration_hash,
                        evaluation_report_hash=approval.comparison_manifest_hash,
                        review_confirmed=True,
                    )
        self.assertFalse(EventFaceEmbeddingActivation.objects.exists())

    def test_activation_rejects_mismatched_or_mixed_generation_sets(self) -> None:
        candidate = candidate_face_embedding_generations()[0]
        mismatched = ({**candidate, "configuration_hash": "f" * 64},)
        mixed = (baseline_face_embedding_generations()[0], candidate)

        for generations in (mismatched, mixed):
            with self.subTest(generations=generations):
                with self.assertRaisesRegex(ValueError, "generation set"):
                    activate_face_embedding_generation(
                        event=self.event,
                        generations=generations,
                        approved_configuration_hash="f" * 64,
                        evaluation_report_hash="d" * 64,
                        review_confirmed=True,
                    )

    def test_guarded_command_records_only_a_confirmed_baseline_selection(self) -> None:
        with self.assertRaisesMessage(Exception, "review confirmation is required"):
            call_command(
                "activate_face_embedding_generation",
                event=self.event.slug,
                generation="baseline",
            )

        call_command(
            "activate_face_embedding_generation",
            event=self.event.slug,
            generation="baseline",
            confirm_reviewed=True,
        )
        self.assertEqual(EventFaceEmbeddingActivation.objects.count(), 1)
