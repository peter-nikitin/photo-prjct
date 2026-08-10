from __future__ import annotations

import hashlib
import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import (
    EventProcessingRun,
    FaceEmbedding,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    PhotoFaceEmbeddingProjection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.face_cohort import load_compatible_face_embeddings


class FaceEmbeddingProjectionCohortTests(TestCase):
    """The production break caught here is a baseline/candidate cohort union or swap."""

    def setUp(self) -> None:
        user = get_user_model().objects.create_user(username="face-cohort-owner")
        self.event = Event.objects.create(
            name="Face cohort event",
            slug="face-cohort-event",
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 8),
            city="Moscow",
        )
        self.photo = Photo.objects.create(
            id="face-cohort-photo",
            event=self.event,
            src="",
            uploaded_by=user,
            original_key="originals/face-cohort-photo.jpg",
            original_filename="face-cohort-photo.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def generation(self, name: str) -> dict[str, object]:
        configuration = {"face_embedding": {"model": "sface"}, "generation": name}
        return {
            "model": "sface",
            "contract_version": 3,
            "processor_type": "face_embedding",
            "processor_version": 3,
            "configuration": configuration,
            "configuration_hash": hashlib.sha256(
                json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }

    def make_projected_embedding(
        self,
        generation: dict[str, object],
        *,
        vector: list[float],
    ) -> FaceEmbedding:
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=generation["contract_version"],
            processor_type=generation["processor_type"],
            processor_version=generation["processor_version"],
            configuration=generation["configuration"],
            configuration_hash=generation["configuration_hash"],
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=self.photo,
            contract_version=generation["contract_version"],
            processor_type=generation["processor_type"],
            processor_version=generation["processor_version"],
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
            contract_version=generation["contract_version"],
            processor_type=generation["processor_type"],
            processor_version=generation["processor_version"],
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
        embedding = FaceEmbedding.objects.create(
            detection=detection,
            model_version="sface",
            vector=vector,
            metadata={},
        )
        PhotoFaceEmbeddingProjection.objects.create(
            photo=self.photo,
            contract_version=generation["contract_version"],
            processor_version=generation["processor_version"],
            configuration_hash=generation["configuration_hash"],
            accepted_attempt=attempt,
        )
        return embedding

    def test_explicit_generation_selects_only_its_projected_embedding(self) -> None:
        baseline_generation = self.generation("baseline")
        candidate_generation = self.generation("candidate")
        baseline = self.make_projected_embedding(baseline_generation, vector=[1.0, 0.0])
        candidate = self.make_projected_embedding(candidate_generation, vector=[0.0, 1.0])
        candidate_attempt = candidate.detection.attempt
        PhotoProcessingState.objects.create(
            photo=self.photo,
            processor_type="face_embedding",
            status=PhotoProcessingState.Status.SUCCEEDED,
            current_run=candidate_attempt.run,
            current_job=candidate_attempt.job,
            current_attempt=candidate_attempt,
            accepted_attempt=candidate_attempt,
            succeeded_at=timezone.now(),
        )

        baseline_rows = load_compatible_face_embeddings(
            self.event,
            (baseline_generation,),
            2,
        )
        candidate_rows = load_compatible_face_embeddings(
            self.event,
            (candidate_generation,),
            2,
        )

        self.assertEqual([row.detection_id for row in baseline_rows], [baseline.detection_id])
        self.assertEqual([row.vector for row in baseline_rows], [(1.0, 0.0)])
        self.assertEqual([row.detection_id for row in candidate_rows], [candidate.detection_id])
        self.assertEqual([row.vector for row in candidate_rows], [(0.0, 1.0)])

        with self.assertRaisesRegex(ValueError, "cannot mix face-embedding configurations"):
            load_compatible_face_embeddings(
                self.event,
                (baseline_generation, candidate_generation),
                2,
            )
