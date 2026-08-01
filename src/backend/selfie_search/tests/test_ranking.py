from __future__ import annotations

from datetime import date
from math import sqrt

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo
from processing.models import (
    EventProcessingRun,
    FaceEmbedding,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    ProcessingAttempt,
    ProcessingJob,
)
from selfie_search.models import SelfieSearch, SelfieSearchCandidate
from selfie_search.services.ranking import QueryVectorError, RankingError, rank_search


class RankingTests(TestCase):
    """The production break caught here is a foreign, malformed, or nondeterministic result."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="ranking-owner")
        self.event = self.make_event("ranking")
        self.other_event = self.make_event("other")
        self.search = self.make_search(self.event)

    def make_event(self, suffix: str) -> Event:
        return Event.objects.create(
            name=f"Ranking {suffix}",
            slug=f"ranking-{suffix}",
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 30),
            city="Moscow",
        )

    def make_search(self, event: Event) -> SelfieSearch:
        ordinal = SelfieSearch.objects.count()
        return SelfieSearch.objects.create(
            event=event,
            public_token_digest=f"{event.slug}-{ordinal:0>48}"[:64],
            temporary_object_key="selfie-search/0123456789abcdef0123456789abcdef",
            configuration={
                "embedding_model": "sface",
                "embedding_dimensions": 128,
                "cosine_distance_threshold": 0.363,
            },
        )

    def add_candidate(
        self,
        *,
        photo_id: str,
        distance: float,
        event: Event | None = None,
        model: str = "sface",
        dimensions: int = 128,
    ) -> FaceEmbedding:
        event = event or self.event
        photo, _ = Photo.objects.get_or_create(
            id=photo_id,
            defaults={
                "event": event,
                "uploaded_by": self.user,
                "original_key": f"originals/{photo_id:0>32}"[-42:],
                "original_filename": f"{photo_id}.jpg",
                "original_size": 1,
                "original_content_type": "image/jpeg",
                "uploaded_at": timezone.now(),
            },
        )
        run = EventProcessingRun.objects.create(
            event=event,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash="a" * 64,
        )
        job = ProcessingJob.objects.create(
            event=event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash="a" * 64,
            input_fingerprint={},
        )
        attempt = ProcessingAttempt.objects.create(
            event=event,
            run=run,
            job=job,
            photo=photo,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
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
        if dimensions == 128:
            vector = [1.0 - distance, sqrt(1 - (1.0 - distance) ** 2)] + [0.0] * 126
        else:
            vector = [1.0] + [0.0] * (dimensions - 1)
        embedding = FaceEmbedding.objects.create(
            detection=detection,
            model_version=model,
            vector=vector,
            metadata={},
        )
        SelfieSearchCandidate.objects.create(search=self.search, embedding=embedding, photo=photo)
        return embedding

    def test_rank_search_keeps_boundary_match_best_face_and_stable_photo_order(self) -> None:
        self.add_candidate(photo_id="z-best", distance=0.20)
        self.add_candidate(photo_id="z-best", distance=0.10)
        self.add_candidate(photo_id="a-tie", distance=0.20)
        self.add_candidate(photo_id="boundary", distance=0.363)
        self.add_candidate(photo_id="too-far", distance=0.364)

        ranked = rank_search(self.search, [1.0] + [0.0] * 127)

        self.assertEqual([row.photo_id for row in ranked], ["z-best", "a-tie", "boundary"])
        self.assertAlmostEqual(ranked[0].cosine_distance, 0.10)
        self.assertAlmostEqual(ranked[1].cosine_distance, 0.20)
        self.assertAlmostEqual(ranked[2].cosine_distance, 0.363)
        self.assertEqual(len({row.photo_id for row in ranked}), len(ranked))

    def test_rank_search_rejects_non_finite_or_non_normalized_query_vectors(self) -> None:
        self.add_candidate(photo_id="valid", distance=0.1)

        with self.assertRaises(QueryVectorError):
            rank_search(self.search, [float("nan")] + [0.0] * 127)
        with self.assertRaises(QueryVectorError):
            rank_search(self.search, [1.0] * 128)
        with self.assertRaises(QueryVectorError):
            rank_search(self.search, [1.0] + [0.0] * 126)

    def test_rank_search_requires_the_v1_sface_128_dimension_contract(self) -> None:
        self.add_candidate(photo_id="valid", distance=0.1)
        self.search.configuration = self.search.configuration | {"embedding_dimensions": 127}
        self.search.save(update_fields=["configuration"])

        with self.assertRaises(RankingError):
            rank_search(self.search, [1.0] + [0.0] * 126)

    def test_rank_search_fails_closed_for_incompatible_candidate_model_or_dimension(self) -> None:
        self.add_candidate(photo_id="model", distance=0.1, model="other")

        with self.assertRaises(RankingError):
            rank_search(self.search, [1.0] + [0.0] * 127)

        self.search = self.make_search(self.event)
        self.add_candidate(photo_id="dimension", distance=0.1, dimensions=127)
        with self.assertRaises(RankingError):
            rank_search(self.search, [1.0] + [0.0] * 127)

    def test_rank_search_never_accepts_a_frozen_candidate_from_another_event(self) -> None:
        self.add_candidate(photo_id="foreign", distance=0.1, event=self.other_event)

        with self.assertRaises(RankingError):
            rank_search(self.search, [1.0] + [0.0] * 127)
