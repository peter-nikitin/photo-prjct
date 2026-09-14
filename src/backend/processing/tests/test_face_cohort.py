from __future__ import annotations

import hashlib
import json
from datetime import date

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
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
from processing.services import face_cohort
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

        mismatched_generation = {
            **candidate_generation,
            "contract_version": 9,
            "configuration": {"face_embedding": {"model": "other"}},
            "configuration_hash": "0" * 64,
            "model": "other",
        }
        mismatched = self.make_projected_embedding(mismatched_generation, vector=[0.5, 0.5])

        rows = load_compatible_face_embeddings(
            self.event,
            (baseline_generation, candidate_generation),
            2,
        )

        self.assertEqual(
            {row.detection_id for row in rows},
            {baseline.detection_id, candidate.detection_id},
        )
        self.assertNotIn(mismatched.detection_id, [row.detection_id for row in rows])

    def test_hot_cohort_query_starts_from_projection_scalar_identity_without_wide_sort(
        self,
    ) -> None:
        """The hot path must not regress to JSON equality or globally sort embedding vectors."""
        generation = self.generation("projection-first")
        embedding = self.make_projected_embedding(generation, vector=[1.0, 0.0])

        with CaptureQueriesContext(connection) as queries:
            rows = load_compatible_face_embeddings(self.event, (generation,), 2)

        cohort_sql = next(
            query["sql"]
            for query in queries
            if 'FROM "processing_photofaceembeddingprojection"' in query["sql"]
        )
        self.assertEqual([row.detection_id for row in rows], [embedding.detection_id])
        self.assertNotIn('"processing_processingattempt"."configuration"', cohort_sql)
        self.assertNotIn('"processing_processingjob"."configuration"', cohort_sql)
        self.assertNotIn('"processing_eventprocessingrun"."configuration"', cohort_sql)
        self.assertNotIn("ORDER BY", cohort_sql.upper())

    def test_identity_projection_is_ordered_and_never_reads_vectors(self) -> None:
        generation = self.generation("identity")
        embedding = self.make_projected_embedding(generation, vector=[1.0, 0.0])
        with CaptureQueriesContext(connection) as queries:
            identities = face_cohort.load_compatible_face_identities(self.event, (generation,))
        self.assertEqual(len(identities), 1)
        self.assertEqual(identities[0].detection_id, embedding.detection_id)
        self.assertEqual(identities[0].embedding_id, embedding.id)
        self.assertEqual(identities[0].attempt_photo_id, self.photo.id)
        for query in queries:
            self.assertNotIn('"vector"', query["sql"])
            self.assertNotIn('"configuration"', query["sql"])
        full_rows = tuple(face_cohort.iter_compatible_face_cohort(self.event, (generation,)))
        self.assertEqual(tuple(identity for identity, _vector in full_rows), identities)
        self.assertEqual(full_rows[0][1], [1.0, 0.0])

    def test_identity_changes_for_visibility_and_same_count_embedding_replacement(self) -> None:
        generation = self.generation("identity-change")
        self.make_projected_embedding(generation, vector=[1.0, 0.0])
        before = face_cohort.load_compatible_face_identities(self.event, (generation,))
        self.photo.is_hidden = True
        self.photo.save(update_fields=["is_hidden"])
        self.assertEqual(face_cohort.load_compatible_face_identities(self.event, (generation,)), ())
        self.photo.pk = "replacement-photo"
        self.photo.original_key = "originals/replacement-photo.jpg"
        self.photo.is_hidden = False
        self.photo.save(force_insert=True)
        replacement = self.make_projected_embedding(generation, vector=[0.0, 1.0])
        after = face_cohort.load_compatible_face_identities(self.event, (generation,))
        self.assertEqual(len(before), len(after))
        self.assertNotEqual(before, after)
        self.assertEqual(after[0].embedding_id, replacement.id)

    def test_warm_cache_validates_current_membership_without_vector_query(self) -> None:
        from selfie_search.services.cohort_cache import CohortCache

        generation = self.generation("warm-cache")
        self.make_projected_embedding(generation, vector=[1.0, 0.0])
        cache = CohortCache()
        cold = cache.get(event=self.event, generations=(generation,), model="sface", dimensions=2)
        with CaptureQueriesContext(connection) as queries:
            warm = cache.get(
                event=self.event, generations=(generation,), model="sface", dimensions=2
            )
        self.assertTrue(warm.cache_hit)
        self.assertIs(cold.entry, warm.entry)
        self.assertEqual(warm.entry.faces[0].photo_id, self.photo.pk)
        self.assertTrue(
            any("processing_photofaceembeddingprojection" in query["sql"] for query in queries)
        )
        self.assertTrue(all('"vector"' not in query["sql"] for query in queries))
