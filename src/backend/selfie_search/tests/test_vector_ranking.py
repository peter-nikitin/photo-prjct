"""Exact current AdaFace pgvector search and failure behavior."""

from __future__ import annotations

from unittest.mock import patch
from uuid import UUID

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from picflow.models import Event, Photo
from processing.models import FaceEmbeddingVector, PhotoFaceDetection
from processing.services.face_quality import active_face_embedding_generations
from processing.tests.test_face_cohort import CurrentFaceCohortTests
from selfie_search.models import SelfieSearch
from selfie_search.services.ranking import QueryVectorError, RankingError
from selfie_search.services.read_selection import rank_selected_direct
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
        result = rank_vector_direct(self.search, self.query)
        self.assertEqual(result.eligible_face_count, 1)
        self.assertEqual(result.photos[0].detection_id, detection.id)
        self.assertEqual(result.photos[0].cosine_distance, 0)
        self.assertEqual(
            set(vars(result.photos[0])), {"photo_id", "detection_id", "cosine_distance"}
        )

    def test_vector_only_current_identity_is_searchable(self) -> None:
        generation = active_face_embedding_generations(self.event)[1]
        detection = self.make_projection(generation, current=True)
        result = rank_vector_direct(self.search, self.query)
        self.assertEqual(result.photos[0].detection_id, detection.id)

    def test_paid_watermarked_photo_remains_retrievable_before_preview_is_available(self) -> None:
        self.event.access_type = Event.AccessType.PAID
        self.event.price_per_photo_kopecks = 30000
        self.event.publication_status = Event.PublicationStatus.PUBLISHED
        self.event.save(
            update_fields=["access_type", "price_per_photo_kopecks", "publication_status"]
        )
        self.photo.processing_generation = Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1
        self.photo.gallery_media_policy = Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
        self.photo.save(update_fields=["processing_generation", "gallery_media_policy"])
        detection = self.make_projection(
            active_face_embedding_generations(self.event)[0], current=True
        )

        result = rank_vector_direct(self.search, self.query)

        self.assertEqual(result.eligible_photo_count, 1)
        self.assertEqual([row.detection_id for row in result.photos], [detection.id])

    def test_missing_current_vector_is_skipped(self) -> None:
        generation = active_face_embedding_generations(self.event)[0]
        self.make_projection(generation, current=True, vector=False)
        result = rank_vector_direct(self.search, self.query)
        self.assertEqual(result.photos, ())
        self.assertEqual(result.eligible_face_count, 0)

    def test_frozen_generation_history_does_not_gate_current_vector(self) -> None:
        generation = active_face_embedding_generations(self.event)[0]
        detection = self.make_projection(generation, current=True)
        self.search.configuration.pop("gallery_face_embedding_generations")

        result = rank_vector_direct(self.search, self.query)

        self.assertEqual([row.detection_id for row in result.photos], [detection.id])

    def test_rejected_face_in_same_attempt_is_not_ranked_or_counted(self) -> None:
        generation = active_face_embedding_generations(self.event)[0]
        kept = self.make_projection(generation, current=True)
        PhotoFaceDetection.objects.create(
            artifact=kept.artifact,
            attempt=kept.attempt,
            face_index=1,
            status=PhotoFaceDetection.Status.QUALITY_REJECTED,
        )

        result = rank_vector_direct(self.search, self.query)

        self.assertEqual(result.eligible_face_count, 1)
        self.assertEqual([row.detection_id for row in result.photos], [kept.id])

    def test_best_face_and_ties_are_ordered_with_counts_before_threshold(self) -> None:
        generation = active_face_embedding_generations(self.event)[0]
        first = self.make_projection(generation, current=True)
        tied = PhotoFaceDetection.objects.create(
            id=UUID(int=1),
            artifact=first.artifact,
            attempt=first.attempt,
            face_index=1,
            status=PhotoFaceDetection.Status.KEPT,
        )
        FaceEmbeddingVector.objects.create(
            detection=tied,
            model_version="adaface-ir18-webface4m",
            vector=self.query,
            metadata={},
        )
        other_photo = Photo.objects.create(
            id="aaa-current-cohort-photo",
            event=self.event,
            src="",
            uploaded_by=self.photo.uploaded_by,
            original_key="originals/aaa-current-cohort-photo",
            original_filename="aaa-current-cohort-photo.jpg",
            original_size=100,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        self.photo = other_photo
        other = self.make_projection(generation, current=True)

        result = rank_vector_direct(self.search, self.query)

        self.assertEqual((result.eligible_face_count, result.eligible_photo_count), (3, 2))
        self.assertEqual(
            [(row.photo_id, row.detection_id) for row in result.photos],
            [(other_photo.pk, other.pk), (first.attempt.photo_id, tied.pk)],
        )
        self.assertEqual([row.cosine_distance for row in result.photos], [0, 0])

    def test_no_match_retains_prethreshold_counts_and_empty_cohort_has_zero_counts(self) -> None:
        with CaptureQueriesContext(connection) as empty_queries:
            empty = rank_vector_direct(self.search, self.query)
        self.assertEqual(len(empty_queries), 1)
        self.assertFalse(empty.has_vectors)
        self.assertEqual((empty.eligible_face_count, empty.eligible_photo_count), (0, 0))
        self.assertEqual(empty.photos, ())

        self.make_projection(active_face_embedding_generations(self.event)[0], current=True)
        unmatched_query = [0.0, 1.0] + [0.0] * 510
        unmatched = rank_vector_direct(self.search, unmatched_query)
        self.assertTrue(unmatched.has_vectors)
        self.assertEqual((unmatched.eligible_face_count, unmatched.eligible_photo_count), (1, 1))
        self.assertEqual(unmatched.photos, ())

    def test_count_cache_miss_hit_and_expiry_keep_fresh_matches(self) -> None:
        self.make_projection(active_face_embedding_generations(self.event)[0], current=True)
        with patch.object(cache, "set", wraps=cache.set) as set_cached:
            with CaptureQueriesContext(connection) as cold_queries:
                cold = rank_vector_direct(self.search, self.query)
            with CaptureQueriesContext(connection) as warm_queries:
                warm = rank_vector_direct(self.search, self.query)
        self.assertEqual(len(cold_queries), 2)
        self.assertEqual(len(warm_queries), 1)
        self.assertEqual((cold.eligible_face_count, cold.eligible_photo_count), (1, 1))
        self.assertEqual(cold.photos, warm.photos)
        self.assertTrue(cold.has_vectors)
        set_cached.assert_called_once()
        cache_key = set_cached.call_args.args[0]
        self.assertEqual(set_cached.call_args.kwargs["timeout"], 60)
        self.assertIn(str(self.event.pk), cache_key)
        self.assertIn("adaface-ir18-webface4m", cache_key)

        cache.touch(cache_key, timeout=0)
        with CaptureQueriesContext(connection) as expired_queries:
            expired = rank_vector_direct(self.search, self.query)
        self.assertEqual(len(expired_queries), 2)
        self.assertEqual(expired.photos, cold.photos)

    def test_stale_count_does_not_hide_new_match_or_break_reader_event(self) -> None:
        self.make_projection(active_face_embedding_generations(self.event)[0], current=True)
        first = rank_vector_direct(self.search, self.query)
        self.assertEqual((first.eligible_face_count, first.eligible_photo_count), (1, 1))

        other_photo = Photo.objects.create(
            id="new-current-cohort-photo",
            event=self.event,
            src="",
            uploaded_by=self.photo.uploaded_by,
            original_key="originals/new-current-cohort-photo",
            original_filename="new-current-cohort-photo.jpg",
            original_size=100,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        self.photo = other_photo
        self.make_projection(active_face_embedding_generations(self.event)[0], current=True)

        updated = rank_selected_direct(self.search, self.query)

        self.assertTrue(updated.has_vectors)
        self.assertEqual((updated.eligible_face_count, updated.eligible_photo_count), (1, 1))
        self.assertEqual(len(updated.photos), 2)

    def test_count_cache_is_event_scoped(self) -> None:
        self.make_projection(active_face_embedding_generations(self.event)[0], current=True)
        original = rank_vector_direct(self.search, self.query)

        foreign_event = Event.objects.create(
            name="Other count event",
            slug="other-count-event",
            start_date=self.event.start_date,
            end_date=self.event.end_date,
            city="Moscow",
        )
        foreign_photo = Photo.objects.create(
            id="other-count-photo",
            event=foreign_event,
            src="",
            uploaded_by=self.photo.uploaded_by,
            original_key="originals/other-count-photo",
            original_filename="other-count-photo.jpg",
            original_size=100,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        foreign_search = SelfieSearch.objects.create(
            event=foreign_event,
            public_token_digest="other-count-search".ljust(64, "0"),
            temporary_object_key="selfie-search/other-count-search",
            configuration=self.search.configuration,
        )
        self.event = foreign_event
        self.photo = foreign_photo
        first = self.make_projection(
            active_face_embedding_generations(foreign_event)[0], current=True
        )
        second = PhotoFaceDetection.objects.create(
            artifact=first.artifact,
            attempt=first.attempt,
            face_index=1,
            status=PhotoFaceDetection.Status.KEPT,
        )
        FaceEmbeddingVector.objects.create(
            detection=second,
            model_version="adaface-ir18-webface4m",
            vector=self.query,
            metadata={},
        )

        other = rank_vector_direct(foreign_search, self.query)

        self.assertEqual((original.eligible_face_count, original.eligible_photo_count), (1, 1))
        self.assertEqual((other.eligible_face_count, other.eligible_photo_count), (2, 1))

    def test_stale_state_is_not_ranked(self) -> None:
        generation = active_face_embedding_generations(self.event)[0]
        self.make_projection(generation, current=False)
        self.assertEqual(rank_vector_direct(self.search, self.query).photos, ())

    def test_hidden_and_foreign_event_photos_are_not_ranked(self) -> None:
        generation = active_face_embedding_generations(self.event)[0]
        self.make_projection(generation, current=True)
        self.photo.is_hidden = True
        self.photo.save(update_fields=["is_hidden"])

        foreign_event = Event.objects.create(
            name="Foreign cohort event",
            slug="foreign-cohort-event",
            start_date=self.event.start_date,
            end_date=self.event.end_date,
            city="Moscow",
        )
        foreign_photo = Photo.objects.create(
            id="foreign-cohort-photo",
            event=foreign_event,
            src="",
            uploaded_by=self.photo.uploaded_by,
            original_key="originals/foreign-cohort-photo",
            original_filename="foreign-cohort-photo.jpg",
            original_size=100,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        self.event = foreign_event
        self.photo = foreign_photo
        self.make_projection(active_face_embedding_generations(foreign_event)[0], current=True)

        result = rank_vector_direct(self.search, self.query)

        self.assertEqual((result.eligible_face_count, result.eligible_photo_count), (0, 0))
        self.assertEqual(result.photos, ())

    def test_rejects_invalid_query_and_old_model_configuration(self) -> None:
        for query in ([], [float("nan")] + self.query[1:], [True] + self.query[1:], [0.0] * 512):
            with self.assertRaises(QueryVectorError):
                rank_vector_direct(self.search, query)
        self.search.configuration["embedding_model"] = "sface"
        with self.assertRaises(RankingError):
            rank_vector_direct(self.search, self.query)
