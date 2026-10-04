"""Exact current AdaFace pgvector search and failure behavior."""

from __future__ import annotations

from django.db import connection
from django.test.utils import CaptureQueriesContext
from processing.services.face_quality import active_face_embedding_generations
from processing.tests.test_face_cohort import CurrentFaceCohortTests
from selfie_search.models import SelfieSearch
from selfie_search.services.ranking import QueryVectorError, RankingError
from selfie_search.services.vector_ranking import rank_vector_direct


class VectorRankingTests(CurrentFaceCohortTests):
    def setUp(self) -> None:
        super().setUp()
        self.search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="vector-ranking".ljust(64, "0"),
            temporary_object_key="selfie-search/0123456789abcdef0123456789abcdef",
            configuration={
                "embedding_model": "adaface-ir18-webface4m",
                "embedding_dimensions": 512,
                "cosine_distance_threshold": 0.42,
                "gallery_face_embedding_generations": list(
                    active_face_embedding_generations(self.event)
                ),
            },
        )
        self.query = [1.0] + [0.0] * 511

    def test_exact_reader_selects_current_vector_without_hydrating_it(self) -> None:
        generation = active_face_embedding_generations(self.event)[0]
        detection = self.make_projection(generation, current=True)
        with CaptureQueriesContext(connection) as queries:
            result = rank_vector_direct(self.search, self.query)
        self.assertEqual(len(queries), 1)
        self.assertEqual(result.eligible_face_count, 1)
        self.assertEqual(result.photos[0].detection_id, detection.id)
        self.assertEqual(result.photos[0].cosine_distance, 0)
        self.assertIn("MATERIALIZED", queries[0]["sql"])
        self.assertNotIn('"processing_faceembedding"."vector"', queries[0]["sql"])

    def test_vector_only_current_identity_is_searchable(self) -> None:
        generation = active_face_embedding_generations(self.event)[1]
        detection = self.make_projection(generation, current=True)
        result = rank_vector_direct(self.search, self.query)
        self.assertEqual(result.photos[0].detection_id, detection.id)

    def test_missing_current_vector_fails_closed(self) -> None:
        generation = active_face_embedding_generations(self.event)[0]
        self.make_projection(generation, current=True, vector=False)
        with self.assertRaises(RankingError):
            rank_vector_direct(self.search, self.query)

    def test_stale_state_and_foreign_event_are_not_ranked(self) -> None:
        generation = active_face_embedding_generations(self.event)[0]
        self.make_projection(generation, current=False)
        self.assertEqual(rank_vector_direct(self.search, self.query).photos, ())

    def test_rejects_invalid_query_and_old_model_configuration(self) -> None:
        for query in ([], [float("nan")] + self.query[1:], [True] + self.query[1:], [0.0] * 512):
            with self.assertRaises(QueryVectorError):
                rank_vector_direct(self.search, query)
        self.search.configuration["embedding_model"] = "sface"
        with self.assertRaises(RankingError):
            rank_vector_direct(self.search, self.query)
