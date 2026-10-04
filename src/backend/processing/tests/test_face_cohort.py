"""Current AdaFace cohort requires matching immutable evidence and accepted state."""

from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import (
    EventProcessingRun,
    FaceEmbeddingVector,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    PhotoFaceEmbeddingProjection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.face_cohort import load_compatible_face_embeddings
from processing.services.face_quality import active_face_embedding_generations


class CurrentFaceCohortTests(TestCase):
    def setUp(self) -> None:
        user = get_user_model().objects.create_user(username="current-cohort-owner")
        self.event = Event.objects.create(
            name="Current cohort event",
            slug="current-cohort-event",
            start_date=date(2026, 10, 4),
            end_date=date(2026, 10, 4),
            city="Moscow",
        )
        self.photo = Photo.objects.create(
            id="current-cohort-photo",
            event=self.event,
            src="",
            uploaded_by=user,
            original_key="originals/current-cohort-photo",
            original_filename="current-cohort-photo.jpg",
            original_size=100,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def make_projection(
        self, generation: dict[str, object], *, current: bool, vector: bool = True
    ) -> PhotoFaceDetection:
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=generation["contract_version"],
            processor_type="face_embedding",
            processor_version=5,
            configuration=generation["configuration"],
            configuration_hash=generation["configuration_hash"],
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=self.photo,
            contract_version=3,
            processor_type="face_embedding",
            processor_version=5,
            configuration=generation["configuration"],
            configuration_hash=generation["configuration_hash"],
            input_fingerprint={},
            status=ProcessingJob.Status.SUCCEEDED,
        )
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=self.photo,
            contract_version=3,
            processor_type="face_embedding",
            processor_version=5,
            configuration=generation["configuration"],
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        artifact = FaceProcessingAttemptArtifact.objects.create(attempt=attempt)
        detection = PhotoFaceDetection.objects.create(
            artifact=artifact,
            attempt=attempt,
            face_index=0,
            status=PhotoFaceDetection.Status.KEPT,
        )
        if vector:
            FaceEmbeddingVector.objects.create(
                detection=detection,
                model_version="adaface-ir18-webface4m",
                vector=[1.0] + [0.0] * 511,
                metadata={},
            )
        PhotoFaceEmbeddingProjection.objects.create(
            photo=self.photo,
            contract_version=3,
            processor_version=5,
            configuration_hash=generation["configuration_hash"],
            accepted_attempt=attempt,
        )
        if current:
            PhotoProcessingState.objects.update_or_create(
                photo=self.photo,
                processor_type="face_embedding",
                defaults={
                    "status": PhotoProcessingState.Status.SUCCEEDED,
                    "current_run": run,
                    "current_job": job,
                    "current_attempt": attempt,
                    "accepted_attempt": attempt,
                    "succeeded_at": timezone.now(),
                },
            )
        return detection

    def test_projected_history_without_current_state_is_excluded(self) -> None:
        generation = active_face_embedding_generations(self.event)[0]
        self.make_projection(generation, current=False)
        self.assertEqual(
            load_compatible_face_embeddings(
                self.event, active_face_embedding_generations(self.event), 512
            ),
            (),
        )

    def test_current_original_adaface_identity_is_loaded(self) -> None:
        generation = active_face_embedding_generations(self.event)[0]
        detection = self.make_projection(generation, current=True)
        rows = load_compatible_face_embeddings(
            self.event, active_face_embedding_generations(self.event), 512
        )
        self.assertEqual([row.detection_id for row in rows], [detection.id])
        self.assertEqual(rows[0].vector, (1.0,) + (0.0,) * 511)

    def test_current_vector_only_adaface_identity_is_loaded(self) -> None:
        generation = active_face_embedding_generations(self.event)[1]
        detection = self.make_projection(generation, current=True)
        rows = load_compatible_face_embeddings(
            self.event, active_face_embedding_generations(self.event), 512
        )
        self.assertEqual([row.detection_id for row in rows], [detection.id])

    def test_current_projection_missing_vector_fails_closed(self) -> None:
        generation = active_face_embedding_generations(self.event)[0]
        self.make_projection(generation, current=True, vector=False)
        with self.assertRaises(ValueError):
            load_compatible_face_embeddings(
                self.event, active_face_embedding_generations(self.event), 512
            )
