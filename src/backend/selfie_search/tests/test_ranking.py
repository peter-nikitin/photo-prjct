from __future__ import annotations

from datetime import date
from math import sqrt
from unittest.mock import patch
from uuid import uuid4

import numpy as np
from django.test import TestCase
from picflow.models import Event
from selfie_search.models import SelfieSearch
from selfie_search.services import ranking
from selfie_search.services.cohort_cache import CohortCacheEntry, CohortFace, _CohortKey
from selfie_search.services.ranking import (
    CandidateEmbedding,
    QueryVectorError,
    RankingError,
    rank_embeddings,
)


class RankingTests(TestCase):
    """The production break caught here is a foreign, malformed, or nondeterministic result."""

    def setUp(self) -> None:
        self.event = self.make_event("ranking")
        self.other_event = self.make_event("other")
        self.search = self.make_search(self.event)
        self.candidates: list[CandidateEmbedding] = []

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
    ) -> None:
        event = event or self.event
        if dimensions == 128:
            vector = [1.0 - distance, sqrt(1 - (1.0 - distance) ** 2)] + [0.0] * 126
        else:
            vector = [1.0] + [0.0] * (dimensions - 1)
        self.candidates.append(
            CandidateEmbedding(
                model_version=model,
                vector=vector,
                detection_id=uuid4(),
                photo_id=photo_id,
                photo_event_id=event.id,
                attempt_event_id=event.id,
                attempt_photo_id=photo_id,
            )
        )

    def test_rank_search_keeps_boundary_match_best_face_and_stable_photo_order(self) -> None:
        self.add_candidate(photo_id="z-best", distance=0.20)
        self.add_candidate(photo_id="z-best", distance=0.10)
        self.add_candidate(photo_id="a-tie", distance=0.20)
        self.add_candidate(photo_id="boundary", distance=0.363)
        self.add_candidate(photo_id="too-far", distance=0.364)

        ranked = rank_embeddings(self.search, [1.0] + [0.0] * 127, self.candidates)

        self.assertEqual([row.photo_id for row in ranked], ["z-best", "a-tie", "boundary"])
        self.assertAlmostEqual(ranked[0].cosine_distance, 0.10)
        self.assertAlmostEqual(ranked[1].cosine_distance, 0.20)
        self.assertAlmostEqual(ranked[2].cosine_distance, 0.363)
        self.assertEqual(len({row.photo_id for row in ranked}), len(ranked))

    def test_equal_distance_faces_use_detection_id_as_the_stable_tie_breaker(self) -> None:
        """Reversing database row order must not change the accepted evidence detection."""
        later_detection = uuid4()
        earlier_detection = uuid4()
        if earlier_detection.int > later_detection.int:
            earlier_detection, later_detection = later_detection, earlier_detection
        vector = [0.8, 0.6] + [0.0] * 126
        candidates = [
            CandidateEmbedding(
                model_version="sface",
                vector=vector,
                detection_id=detection_id,
                photo_id="same-photo",
                photo_event_id=self.event.id,
                attempt_event_id=self.event.id,
                attempt_photo_id="same-photo",
            )
            for detection_id in (later_detection, earlier_detection)
        ]

        ranked = rank_embeddings(self.search, [1.0] + [0.0] * 127, candidates)

        self.assertEqual(ranked[0].detection_id, earlier_detection)

    def test_rank_search_clamps_floating_point_distance_to_cosine_bounds(self) -> None:
        vector = [float((index % 17) - 8) for index in range(128)]
        norm = sqrt(sum(item * item for item in vector))
        normalized = [item / norm for item in vector]
        self.candidates.append(
            CandidateEmbedding(
                model_version="sface",
                vector=normalized,
                detection_id=uuid4(),
                photo_id="identical",
                photo_event_id=self.event.id,
                attempt_event_id=self.event.id,
                attempt_photo_id="identical",
            )
        )

        ranked = rank_embeddings(self.search, normalized, self.candidates)

        self.assertEqual(ranked[0].cosine_distance, 0.0)

    def test_rank_embeddings_rejects_non_finite_or_non_normalized_query_vectors(self) -> None:
        self.add_candidate(photo_id="valid", distance=0.1)

        with self.assertRaises(QueryVectorError):
            rank_embeddings(self.search, [float("nan")] + [0.0] * 127, self.candidates)
        with self.assertRaises(QueryVectorError):
            rank_embeddings(self.search, [1.0] * 128, self.candidates)
        with self.assertRaises(QueryVectorError):
            rank_embeddings(self.search, [1.0] + [0.0] * 126, self.candidates)

    def test_rank_embeddings_requires_the_v1_sface_128_dimension_contract(self) -> None:
        self.add_candidate(photo_id="valid", distance=0.1)
        self.search.configuration = self.search.configuration | {"embedding_dimensions": 127}
        self.search.save(update_fields=["configuration"])

        with self.assertRaises(RankingError):
            rank_embeddings(self.search, [1.0] + [0.0] * 126, self.candidates)

    def test_rank_embeddings_uses_the_frozen_adaface_512_threshold_contract(self) -> None:
        """A local AdaFace search must not silently fall back to the SFace ranking identity."""
        search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="adaface-ranking".ljust(64, "0"),
            temporary_object_key="selfie-search/0123456789abcdef0123456789abcdef",
            configuration={
                "embedding_model": "adaface-ir18-webface4m",
                "embedding_dimensions": 512,
                "cosine_distance_threshold": 0.43,
            },
        )
        candidate = CandidateEmbedding(
            model_version="adaface-ir18-webface4m",
            vector=[0.58, sqrt(1 - 0.58**2)] + [0.0] * 510,
            detection_id=uuid4(),
            photo_id="adaface-photo",
            photo_event_id=self.event.id,
            attempt_event_id=self.event.id,
            attempt_photo_id="adaface-photo",
        )

        ranked = rank_embeddings(search, [1.0] + [0.0] * 511, [candidate])

        self.assertEqual([row.photo_id for row in ranked], ["adaface-photo"])
        self.assertAlmostEqual(ranked[0].cosine_distance, 0.42)

    def test_rank_search_fails_closed_for_incompatible_candidate_model_or_dimension(self) -> None:
        self.add_candidate(photo_id="model", distance=0.1, model="other")

        with self.assertRaises(RankingError):
            rank_embeddings(self.search, [1.0] + [0.0] * 127, self.candidates)

        self.search = self.make_search(self.event)
        self.candidates = []
        self.add_candidate(photo_id="dimension", distance=0.1, dimensions=127)
        with self.assertRaises(RankingError):
            rank_embeddings(self.search, [1.0] + [0.0] * 127, self.candidates)

    def test_rank_search_never_accepts_a_frozen_candidate_from_another_event(self) -> None:
        self.add_candidate(photo_id="foreign", distance=0.1, event=self.other_event)

        with self.assertRaises(RankingError):
            rank_embeddings(self.search, [1.0] + [0.0] * 127, self.candidates)

    def test_cached_shortlist_matches_exact_baseline_for_both_models_and_threshold_edges(
        self,
    ) -> None:
        for model, dimensions, threshold in (
            ("sface", 128, 0.363),
            ("adaface-ir18-webface4m", 512, 0.43),
        ):
            with self.subTest(model=model):
                self.search.configuration = {
                    "embedding_model": model,
                    "embedding_dimensions": dimensions,
                    "cosine_distance_threshold": threshold,
                }
                distances = [
                    0.1,
                    0.1,
                    0.2,
                    threshold - 5e-11,
                    threshold,
                    threshold + 5e-11,
                    threshold + 2e-10,
                    1.0,
                ]
                candidates = [
                    CandidateEmbedding(
                        vector=[1 - distance, sqrt(1 - (1 - distance) ** 2)]
                        + [0.0] * (dimensions - 2),
                        model_version=model,
                        detection_id=uuid4(),
                        photo_id="same" if index < 2 else str(index),
                        photo_event_id=self.event.id,
                        attempt_event_id=self.event.id,
                        attempt_photo_id="same" if index < 2 else str(index),
                    )
                    for index, distance in enumerate(distances)
                ]
                matrix = np.asarray([row.vector for row in candidates], dtype=np.float64)
                matrix.setflags(write=False)
                entry = CohortCacheEntry(
                    _CohortKey(self.event.id, model, dimensions, "[]"),
                    (),
                    tuple(CohortFace(row.detection_id, row.photo_id) for row in candidates),
                    matrix,
                )
                query = [1.0] + [0.0] * (dimensions - 1)
                expected = rank_embeddings(self.search, query, candidates)
                result = ranking.rank_cached_embeddings(self.search, query, entry)
                self.assertEqual(result.photos, expected)
                self.assertEqual(result.shortlist_count, 6)
                self.assertNotIn("5", [row.photo_id for row in result.photos])
                with patch(
                    "selfie_search.services.ranking.np.dot",
                    return_value=1 - np.asarray(distances) - 2e-12,
                ):
                    perturbed = ranking.rank_cached_embeddings(self.search, query, entry)
                self.assertEqual(perturbed.photos, expected)
                for invalid in (
                    [float("nan")] + query[1:],
                    [True] + query[1:],
                    [0.0] * dimensions,
                    query[:-1],
                ):
                    with self.assertRaises(QueryVectorError):
                        ranking.rank_cached_embeddings(self.search, invalid, entry)

    def test_cached_shortlist_preserves_clamped_adaface_upper_threshold(self) -> None:
        model = "adaface-ir18-webface4m"
        self.search.configuration = {
            "embedding_model": model,
            "embedding_dimensions": 512,
            "cosine_distance_threshold": 2.0,
        }
        query = [1.0000005] + [0.0] * 511
        gallery = [-1.0000005] + [0.0] * 511
        candidate = CandidateEmbedding(
            vector=gallery,
            model_version=model,
            detection_id=uuid4(),
            photo_id="antiparallel",
            photo_event_id=self.event.id,
            attempt_event_id=self.event.id,
            attempt_photo_id="antiparallel",
        )
        entry = CohortCacheEntry(
            _CohortKey(self.event.id, model, 512, "[]"),
            (),
            (CohortFace(candidate.detection_id, candidate.photo_id),),
            np.asarray([gallery], dtype=np.float64),
        )
        expected = rank_embeddings(self.search, query, [candidate])
        self.assertEqual(len(expected), 1)
        self.assertEqual(expected[0].cosine_distance, 2.0)

        result = ranking.rank_cached_embeddings(self.search, query, entry)

        self.assertEqual(result.photos, expected)
        self.assertEqual(result.shortlist_count, 1)

    def test_dense_cached_distances_are_bit_exact_with_python_baseline(self) -> None:
        rng = np.random.default_rng(17)
        for model, dimensions, threshold in (
            ("sface", 128, 0.363),
            ("adaface-ir18-webface4m", 512, 0.43),
        ):
            with self.subTest(model=model):
                self.search.configuration = {
                    "embedding_model": model,
                    "embedding_dimensions": dimensions,
                    "cosine_distance_threshold": threshold,
                }
                query = rng.normal(size=dimensions)
                query /= np.linalg.norm(query)
                orthogonal = rng.normal(size=(60, dimensions))
                orthogonal -= np.outer(np.einsum("ij,j->i", orthogonal, query), query)
                orthogonal /= np.linalg.norm(orthogonal, axis=1)[:, None]
                cosine = 1 - np.concatenate(
                    (rng.uniform(0, 2, 30), threshold + np.linspace(-9e-11, 9e-11, 30))
                )
                matrix = cosine[:, None] * query + np.sqrt(1 - cosine**2)[:, None] * orthogonal
                candidates = [
                    CandidateEmbedding(
                        vector=row.tolist(),
                        model_version=model,
                        detection_id=uuid4(),
                        photo_id=str(index // 2),
                        photo_event_id=self.event.id,
                        attempt_event_id=self.event.id,
                        attempt_photo_id=str(index // 2),
                    )
                    for index, row in enumerate(matrix)
                ]
                entry = CohortCacheEntry(
                    _CohortKey(self.event.id, model, dimensions, "[]"),
                    (),
                    tuple(CohortFace(row.detection_id, row.photo_id) for row in candidates),
                    matrix,
                )
                expected = rank_embeddings(self.search, query.tolist(), candidates)
                actual = ranking.rank_cached_embeddings(self.search, query.tolist(), entry).photos
                self.assertEqual(actual, expected)
                self.assertEqual(
                    [row.cosine_distance.hex() for row in actual],
                    [row.cosine_distance.hex() for row in expected],
                )
