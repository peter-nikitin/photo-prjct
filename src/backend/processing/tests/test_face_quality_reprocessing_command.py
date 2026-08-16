import hashlib
import json
from datetime import date
from io import StringIO
from typing import TypedDict, cast
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import (
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    FACE_EMBEDDING_QUALITY_APPROVAL,
    GENERATE_PREVIEW_CONFIGURATION,
    QUALITY_FACE_CONTRACT_VERSION,
    QUALITY_FACE_PROCESSOR_VERSION,
    FaceEmbeddingGenerationApproval,
    accepted_preview_cohort_hash,
    request_processor,
)
from processing.services.face_quality import candidate_face_embedding_generations


class CandidateCounts(TypedDict):
    candidate_job_count: int
    candidate_projection_count: int
    candidate_state_counts: dict[str, int]
    eligible_photo_count: int
    failure_job_count: int
    nonterminal_job_count: int
    terminal_job_count: int


class CommandReport(TypedDict):
    counts: CandidateCounts
    mode: str


class FaceQualityReprocessingCommandTests(TestCase):
    event_slug = "cyclingrace-vechernee-sadovoe"

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="face-quality-command-owner")
        self.event = Event.objects.create(
            name="Cyclingrace Вечернее Садовое",
            slug=self.event_slug,
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 8),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        self.other_event = Event.objects.create(
            name="Other event",
            slug="another-event",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 8),
            city="Moscow",
        )

    def accepted_preview_photo(
        self,
        identifier: str,
        *,
        event: Event | None = None,
        byte_size: int = 8,
        sha256: str = "a" * 64,
        width: int = 1600,
    ) -> Photo:
        photo = Photo.objects.create(
            id=identifier,
            event=event or self.event,
            uploaded_by=self.user,
            original_key=f"originals/{identifier}",
            original_filename=f"{identifier}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )
        state = request_processor(
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
        assert state.current_job is not None
        attempt = ProcessingAttempt.objects.create(
            event=photo.event,
            run=state.current_job.run,
            job=state.current_job,
            photo=photo,
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            configuration=GENERATE_PREVIEW_CONFIGURATION,
            input_fingerprint=state.current_job.input_fingerprint,
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-small-v1",
            final_key=f"previews/{identifier}.jpg",
            byte_size=byte_size,
            content_type="image/jpeg",
            width=width,
            height=1000,
            oriented_source_width=1600,
            oriented_source_height=1000,
            sha256=sha256,
            accepted_attempt=attempt,
        )
        state.status = PhotoProcessingState.Status.SUCCEEDED
        state.current_attempt = attempt
        state.accepted_attempt = attempt
        state.succeeded_at = timezone.now()
        state.save(
            update_fields=[
                "status",
                "current_attempt",
                "accepted_attempt",
                "succeeded_at",
                "updated_at",
            ]
        )
        return photo

    def approval(
        self,
        *,
        photo_count: int = 1,
        approved: bool = True,
        cohort_hash: str | None = None,
    ) -> FaceEmbeddingGenerationApproval:
        generation = candidate_face_embedding_generations()[0]
        configuration_hash = generation["configuration_hash"]
        assert isinstance(configuration_hash, str)
        return FaceEmbeddingGenerationApproval(
            event_slug=self.event_slug,
            photo_count=photo_count,
            configuration_hash=configuration_hash,
            preview_manifest_hash="a" * 64,
            local_preview_projection_hash="e" * 64,
            accepted_preview_cohort_hash=(
                cohort_hash if cohort_hash is not None else accepted_preview_cohort_hash(self.event)
            ),
            accepted_preview_crosswalk_hash="f" * 64,
            accepted_preview_crosswalk_entry_count=photo_count,
            accepted_preview_crosswalk_sha_mismatch_count=photo_count,
            comparison_manifest_hash="b" * 64,
            yunet_model_hash="c" * 64,
            sface_model_hash="d" * 64,
            job_count=photo_count,
            attempt_count=photo_count,
            projection_count=photo_count,
            technical_failure_count=0,
            kept_face_count=0,
            quality_rejected_face_count=0,
            approved=approved,
        )

    def reviewed_cohort_hash(self, photo: Photo) -> str:
        projection = (
            {
                "byte_size": 8,
                "height": 1000,
                "oriented_source_height": 1000,
                "oriented_source_width": 1600,
                "photo_id": photo.pk,
                "sha256": "a" * 64,
                "width": 1600,
            },
        )
        return hashlib.sha256(
            json.dumps(projection, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()

    def command(self, *, apply: bool = False) -> CommandReport:
        output = StringIO()
        call_command("reprocess_event_face_embeddings", apply=apply, stdout=output)
        return cast(CommandReport, json.loads(output.getvalue()))

    def test_tracked_approval_binds_exact_reviewed_artifacts_without_loss_counters(self) -> None:
        """Changing reviewed artifacts or inventing recall-loss fields must fail this contract."""
        assert FACE_EMBEDDING_QUALITY_APPROVAL is not None
        self.assertEqual(FACE_EMBEDDING_QUALITY_APPROVAL.event_slug, self.event_slug)
        self.assertEqual(
            FACE_EMBEDDING_QUALITY_APPROVAL.preview_manifest_hash,
            "62f071941cd8281745256ed6906f37cbfdac29996f20fd6a992c7f486783d879",
        )
        self.assertEqual(
            FACE_EMBEDDING_QUALITY_APPROVAL.local_preview_projection_hash,
            "a98b5d13152683419c722a115045037fdf883a1f5cdcc3e47a2bddf5291b7d63",
        )
        self.assertEqual(
            FACE_EMBEDDING_QUALITY_APPROVAL.accepted_preview_cohort_hash,
            "6701b7436e1b00b64e701791983a0c9c1d26bcddd56f93a36dd0923aa6bc1034",
        )
        self.assertEqual(
            FACE_EMBEDDING_QUALITY_APPROVAL.accepted_preview_crosswalk_hash,
            "055d7c72614deb3b87b607f467c16365ee6e125be005e9e8f5cf2e910ec56d51",
        )
        self.assertEqual(
            FACE_EMBEDDING_QUALITY_APPROVAL.accepted_preview_crosswalk_entry_count, 17_043
        )
        self.assertEqual(
            FACE_EMBEDDING_QUALITY_APPROVAL.accepted_preview_crosswalk_sha_mismatch_count,
            17_043,
        )
        self.assertEqual(
            FACE_EMBEDDING_QUALITY_APPROVAL.comparison_manifest_hash,
            "043ce5c02cd6df901f16096c2637c3a26b3b96171a9e9538b439cee12abca0a6",
        )
        self.assertEqual(
            FACE_EMBEDDING_QUALITY_APPROVAL.yunet_model_hash,
            "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        )
        self.assertEqual(
            FACE_EMBEDDING_QUALITY_APPROVAL.sface_model_hash,
            "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        )
        self.assertEqual(FACE_EMBEDDING_QUALITY_APPROVAL.job_count, 17_043)
        self.assertEqual(FACE_EMBEDDING_QUALITY_APPROVAL.attempt_count, 17_043)
        self.assertEqual(FACE_EMBEDDING_QUALITY_APPROVAL.projection_count, 17_043)
        self.assertEqual(FACE_EMBEDDING_QUALITY_APPROVAL.technical_failure_count, 0)
        self.assertEqual(FACE_EMBEDDING_QUALITY_APPROVAL.kept_face_count, 37_573)
        self.assertEqual(FACE_EMBEDDING_QUALITY_APPROVAL.quality_rejected_face_count, 18_610)
        self.assertTrue(FACE_EMBEDDING_QUALITY_APPROVAL.approved)
        self.assertNotIn("clear_loss_count", FACE_EMBEDDING_QUALITY_APPROVAL.__dataclass_fields__)
        self.assertNotIn(
            "relevant_result_loss_count", FACE_EMBEDDING_QUALITY_APPROVAL.__dataclass_fields__
        )
        self.assertNotIn("unresolved_count", FACE_EMBEDDING_QUALITY_APPROVAL.__dataclass_fields__)

    def test_command_rejects_another_event_before_writing(self) -> None:
        """Dropping the fixed event-slug guard must fail this test."""
        self.event.slug = "not-the-approved-event"
        self.event.save(update_fields=["slug"])

        with self.assertRaisesRegex(CommandError, "approved event"):
            self.command()

        self.assertFalse(ProcessingJob.objects.filter(processor_version=4).exists())

    def test_dry_run_reports_counts_and_writes_nothing(self) -> None:
        """Performing candidate enrollment without --apply must fail this test."""
        self.accepted_preview_photo("preview-one")
        with patch(
            "processing.management.commands.reprocess_event_face_embeddings.FACE_EMBEDDING_QUALITY_APPROVAL",
            self.approval(),
        ):
            report = self.command()

        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["counts"]["eligible_photo_count"], 1)
        self.assertEqual(report["counts"]["candidate_job_count"], 0)
        self.assertEqual(report["counts"]["candidate_projection_count"], 0)
        self.assertFalse(ProcessingJob.objects.filter(processor_version=4).exists())

    def test_same_count_with_changed_derivative_sha_fails_before_any_candidate_write(self) -> None:
        """A same-size cohort with changed accepted media identity must fail dry-run validation."""
        photo = self.accepted_preview_photo("changed-sha", sha256="9" * 64)
        approval = self.approval(cohort_hash=self.reviewed_cohort_hash(photo))

        with patch(
            "processing.management.commands.reprocess_event_face_embeddings.FACE_EMBEDDING_QUALITY_APPROVAL",
            approval,
        ):
            with self.assertRaisesRegex(CommandError, "accepted preview cohort"):
                self.command()

        self.assertFalse(
            ProcessingJob.objects.filter(
                contract_version=QUALITY_FACE_CONTRACT_VERSION,
                processor_version=QUALITY_FACE_PROCESSOR_VERSION,
            ).exists()
        )

    def test_changed_derivative_byte_size_fails_with_the_same_photo_count(self) -> None:
        """A byte-size change must alter the approved accepted-preview identity."""
        photo = self.accepted_preview_photo("changed-size", byte_size=9)
        approval = self.approval(cohort_hash=self.reviewed_cohort_hash(photo))
        with patch(
            "processing.management.commands.reprocess_event_face_embeddings.FACE_EMBEDDING_QUALITY_APPROVAL",
            approval,
        ):
            with self.assertRaisesRegex(CommandError, "accepted preview cohort"):
                self.command()

        self.assertFalse(
            ProcessingJob.objects.filter(
                contract_version=QUALITY_FACE_CONTRACT_VERSION,
                processor_version=QUALITY_FACE_PROCESSOR_VERSION,
            ).exists()
        )

    def test_changed_derivative_geometry_fails_with_the_same_photo_count(self) -> None:
        """A geometry change must alter the approved accepted-preview identity."""
        photo = self.accepted_preview_photo("changed-geometry", width=1599)
        approval = self.approval(cohort_hash=self.reviewed_cohort_hash(photo))
        with patch(
            "processing.management.commands.reprocess_event_face_embeddings.FACE_EMBEDDING_QUALITY_APPROVAL",
            approval,
        ):
            with self.assertRaisesRegex(CommandError, "accepted preview cohort"):
                self.command()

        self.assertFalse(
            ProcessingJob.objects.filter(
                contract_version=QUALITY_FACE_CONTRACT_VERSION,
                processor_version=QUALITY_FACE_PROCESSOR_VERSION,
            ).exists()
        )

    def test_apply_rejects_yunet_quality_generation_before_creating_a_job_or_run(self) -> None:
        """The retained operator command cannot enqueue v4 for the SCRFD-only worker."""
        self.accepted_preview_photo("preview-accepted")
        run_count = EventProcessingRun.objects.count()
        job_count = ProcessingJob.objects.count()
        with patch(
            "processing.management.commands.reprocess_event_face_embeddings.FACE_EMBEDDING_QUALITY_APPROVAL",
            self.approval(),
        ):
            with self.assertRaisesRegex(CommandError, "SCRFD quality generation is not approved"):
                self.command(apply=True)

        self.assertEqual(EventProcessingRun.objects.count(), run_count)
        self.assertEqual(ProcessingJob.objects.count(), job_count)
        self.assertFalse(
            ProcessingJob.objects.filter(
                contract_version=QUALITY_FACE_CONTRACT_VERSION,
                processor_version=QUALITY_FACE_PROCESSOR_VERSION,
            ).exists()
        )
