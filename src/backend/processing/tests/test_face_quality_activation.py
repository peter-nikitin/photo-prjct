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
    PhotoFaceEmbeddingProjection,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    FACE_EMBEDDING_QUALITY_CONFIGURATION,
    QUALITY_FACE_CONTRACT_VERSION,
    QUALITY_FACE_PROCESSOR_VERSION,
    FaceEmbeddingGenerationApproval,
)
from processing.services.face_quality import (
    activate_face_embedding_generation,
    active_face_embedding_generations,
    baseline_face_embedding_generations,
    candidate_face_embedding_generations,
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

    def publish_candidate_projection(self, *, event: Event, photo_id: str) -> None:
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

    def approval(
        self,
        *,
        event: Event | None = None,
        photo_count: int = 1,
        complete: bool = True,
        approved: bool = True,
        clear_loss_count: int = 0,
        relevant_result_loss_count: int = 0,
        unresolved_count: int = 0,
    ) -> FaceEmbeddingGenerationApproval:
        generation = candidate_face_embedding_generations()[0]
        configuration_hash = generation["configuration_hash"]
        assert isinstance(configuration_hash, str)
        return FaceEmbeddingGenerationApproval(
            event_slug=(event or self.event).slug,
            photo_count=photo_count,
            configuration_hash=configuration_hash,
            evaluation_report_hash="d" * 64,
            complete=complete,
            approved=approved,
            clear_loss_count=clear_loss_count,
            relevant_result_loss_count=relevant_result_loss_count,
            unresolved_count=unresolved_count,
        )

    def test_event_without_activation_resolves_only_the_frozen_baseline(self) -> None:
        self.assertEqual(
            active_face_embedding_generations(self.event),
            baseline_face_embedding_generations(),
        )
        self.assertFalse(EventFaceEmbeddingActivation.objects.exists())

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
                evaluation_report_hash=approval.evaluation_report_hash,
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
            approved_evaluation_report_hash=mismatched_approval.evaluation_report_hash,
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
            approved_evaluation_report_hash=incomplete_approval.evaluation_report_hash,
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
            self.approval(complete=False),
            self.approval(approved=False),
            self.approval(clear_loss_count=1),
            self.approval(relevant_result_loss_count=1),
            self.approval(unresolved_count=1),
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
                            evaluation_report_hash=approval.evaluation_report_hash,
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
                    evaluation_report_hash=approval.evaluation_report_hash,
                    review_confirmed=True,
                )

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
