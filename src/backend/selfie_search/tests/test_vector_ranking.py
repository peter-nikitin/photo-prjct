from __future__ import annotations

import hashlib
import json
from math import sqrt

from django.db import connection
from django.test.utils import CaptureQueriesContext
from processing.models import FaceEmbeddingVector, PhotoFaceDetection
from processing.services.face_quality import adaface_face_embedding_generations
from processing.tests.test_face_cohort import FaceEmbeddingProjectionCohortTests
from selfie_search.models import SelfieSearch
from selfie_search.services.ranking import QueryVectorError, RankingError
from selfie_search.services.vector_ranking import rank_vector_direct


class VectorRankingTests(FaceEmbeddingProjectionCohortTests):
    def setUp(self) -> None:
        super().setUp()
        self.frozen_generation = self.generation("native")
        self.search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="vector-ranking".ljust(64, "0"),
            temporary_object_key="selfie-search/0123456789abcdef0123456789abcdef",
            configuration={
                "embedding_model": "sface",
                "embedding_dimensions": 128,
                "cosine_distance_threshold": 0.363,
                "gallery_face_embedding_generations": [self.frozen_generation],
            },
        )
        self.query = [1.0] + [0.0] * 127

    def add_face(self, vector: list[float]):
        embedding = self.make_projected_embedding(self.frozen_generation, vector=vector)
        return embedding

    def test_marked_adaface_native_cohort_needs_no_parallel_json_row(self) -> None:
        generation = adaface_face_embedding_generations()[0]
        configuration = generation["configuration"]
        assert isinstance(configuration, dict)
        configuration["embedding_storage"] = "vector_only"
        generation["configuration_hash"] = hashlib.sha256(
            json.dumps(generation["configuration"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.search.configuration = {
            "embedding_model": "adaface-ir18-webface4m",
            "embedding_dimensions": 512,
            "cosine_distance_threshold": 0.42,
            "gallery_face_embedding_generations": [generation],
        }
        vector = [1.0] + [0.0] * 511
        embedding = self.make_projected_embedding(generation, vector=vector, vector_only=True)
        result = rank_vector_direct(self.search, vector)
        self.assertEqual(result.eligible_face_count, 1)
        self.assertEqual(result.photos[0].detection_id, embedding.detection_id)
        self.assertEqual(result.photos[0].cosine_distance, 0)
        PhotoFaceDetection.objects.create(
            attempt=embedding.detection.attempt,
            artifact=embedding.detection.artifact,
            face_index=1,
            status="kept",
        )
        with self.assertRaises(RankingError):
            rank_vector_direct(self.search, vector)

    def test_unmarked_native_face_is_eligible_without_json(self) -> None:
        self.make_projected_embedding(self.frozen_generation, vector=self.query, vector_only=True)
        result = rank_vector_direct(self.search, self.query)
        self.assertEqual(result.eligible_face_count, 1)
        self.assertEqual(len(result.photos), 1)

    def test_empty_and_self_match_return_scalar_contract(self) -> None:
        empty = rank_vector_direct(self.search, self.query)
        self.assertEqual(empty.photos, ())
        self.assertEqual(empty.eligible_face_count, 0)
        embedding = self.add_face(self.query)
        native = rank_vector_direct(self.search, self.query)
        self.assertEqual(native.eligible_photo_count, 1)
        self.assertEqual(native.photos[0].detection_id, embedding.detection_id)
        self.assertEqual(native.photos[0].cosine_distance, 0)

    def test_native_reader_uses_one_statement_and_never_hydrates_gallery_vectors(self) -> None:
        self.add_face([0.8, 0.6] + [0.0] * 126)
        with CaptureQueriesContext(connection) as queries:
            result = rank_vector_direct(self.search, self.query)
        self.assertEqual(len(queries), 1)
        self.assertEqual(len(result.photos), 1)
        sql = queries[0]["sql"]
        self.assertNotIn('"processing_faceembedding"."vector"', sql)
        self.assertIn("MATERIALIZED", sql)
        self.assertNotIn("LIMIT", sql.upper())

    def test_best_photo_reduction_cannot_be_rescanned_per_cohort_face(self) -> None:
        # The restored large cohort estimates one eligible row even after ANALYZE.
        # An inline DISTINCT best-photo relation was then rescanned by a nested loop.
        self.add_face(self.query)
        captured = []

        def capture(execute, sql, params, many, context):
            captured.append((sql, params))
            return execute(sql, params, many, context)

        with connection.execute_wrapper(capture):
            rank_vector_direct(self.search, self.query)
        with connection.cursor() as cursor:
            cursor.execute("EXPLAIN (FORMAT JSON) " + captured[0][0], captured[0][1])
            plan = cursor.fetchone()[0][0]["Plan"]

        def has_rescanned_reduction(node, inside_loop=False):
            inside_loop = inside_loop or node["Node Type"] == "Nested Loop"
            return (inside_loop and node["Node Type"] == "Unique") or any(
                has_rescanned_reduction(child, inside_loop) for child in node.get("Plans", ())
            )

        self.assertFalse(has_rescanned_reduction(plan))

    def test_missing_vector_fails_closed_even_when_other_face_matches(self) -> None:
        self.add_face(self.query)
        detection = PhotoFaceDetection.objects.create(
            artifact=PhotoFaceDetection.objects.first().artifact,
            attempt=PhotoFaceDetection.objects.first().attempt,
            face_index=1,
            status="kept",
        )
        with self.assertRaises(RankingError):
            rank_vector_direct(self.search, self.query)
        self.assertIsNotNone(detection)

    def test_visibility_generation_and_foreign_event_exclude_faces(self) -> None:
        self.add_face(self.query)
        self.photo.is_hidden = True
        self.photo.save(update_fields=["is_hidden"])
        self.assertEqual(rank_vector_direct(self.search, self.query).eligible_face_count, 0)
        self.photo.is_hidden = False
        self.photo.save(update_fields=["is_hidden"])
        self.search.configuration["gallery_face_embedding_generations"] = [self.generation("stale")]
        self.assertEqual(rank_vector_direct(self.search, self.query).eligible_face_count, 0)
        self.search.configuration["gallery_face_embedding_generations"] = [self.frozen_generation]
        self.search.event = type(self.event).objects.create(
            name="Foreign",
            slug="foreign-native",
            start_date=self.event.start_date,
            end_date=self.event.end_date,
            city="Moscow",
        )
        self.assertEqual(rank_vector_direct(self.search, self.query).eligible_face_count, 0)

    def test_query_and_frozen_identity_validation(self) -> None:
        for query in ([], [float("nan")] + self.query[1:], [True] + self.query[1:], [0.0] * 128):
            with self.assertRaises(QueryVectorError):
                rank_vector_direct(self.search, query)
        for change in ({"embedding_model": "other"}, {"embedding_dimensions": 512}):
            original = self.search.configuration
            self.search.configuration = original | change
            with self.assertRaises(RankingError):
                rank_vector_direct(self.search, self.query)
            self.search.configuration = original

    def test_best_face_and_deterministic_native_ties(self) -> None:
        embedding = self.add_face([0.8, 0.6] + [0.0] * 126)
        faces = []
        for index in (1, 2):
            detection = PhotoFaceDetection.objects.create(
                artifact=embedding.detection.artifact,
                attempt=embedding.detection.attempt,
                face_index=index,
                status="kept",
            )
            faces.append(detection)
            FaceEmbeddingVector.objects.create(
                detection=detection, model_version="sface", vector=self.query
            )
        result = rank_vector_direct(self.search, self.query)
        self.assertEqual(result.photos[0].detection_id, min(face.id for face in faces))
        self.assertEqual(result.eligible_face_count, 3)
        self.assertEqual(result.eligible_photo_count, 1)

    def test_native_arithmetic_excludes_boundary_candidate(self) -> None:
        self.add_face([0.6370000243186951, 0.7708638310432434] + [0.0] * 126)
        native = rank_vector_direct(self.search, self.query)
        self.assertEqual(native.photos, ())

    def test_adaface_inclusive_threshold(self) -> None:
        generation = self.generation("adaface")
        generation["model"] = "adaface-ir18-webface4m"
        self.search.configuration = {
            "embedding_model": "adaface-ir18-webface4m",
            "embedding_dimensions": 512,
            "cosine_distance_threshold": 0.43,
            "gallery_face_embedding_generations": [generation],
        }
        vector = [0.64, sqrt(1 - 0.64**2)] + [0.0] * 510
        embedding = self.make_projected_embedding(generation, vector=vector)
        query = [1.0] + [0.0] * 511
        native = rank_vector_direct(self.search, query)
        self.assertEqual(native.photos[0].detection_id, embedding.detection_id)
        threshold = native.photos[0].cosine_distance
        self.search.configuration["cosine_distance_threshold"] = threshold
        self.assertEqual(len(rank_vector_direct(self.search, query).photos), 1)
        self.search.configuration["cosine_distance_threshold"] = threshold - 1e-10
        self.assertEqual(rank_vector_direct(self.search, query).photos, ())

    def test_all_threshold_matches_have_deterministic_photo_order(self) -> None:
        for photo_id in ("z-native", "a-native", "outside-native"):
            self.photo.pk = photo_id
            self.photo.original_key = f"originals/{photo_id}.jpg"
            self.photo.save(force_insert=True)
            self.add_face(
                [0.8, 0.6] + [0.0] * 126 if photo_id != "outside-native" else [-1.0] + [0.0] * 127
            )
        native = rank_vector_direct(self.search, self.query)
        self.assertEqual([row.photo_id for row in native.photos], ["a-native", "z-native"])
        self.assertEqual(native.eligible_face_count, 3)

    def test_divergent_model_blocks_partial_cohort(self) -> None:
        other_generation = self.frozen_generation | {"model": "adaface-ir18-webface4m"}
        self.make_projected_embedding(other_generation, vector=[1.0] + [0.0] * 511)
        with self.assertRaises(RankingError):
            rank_vector_direct(self.search, self.query)
