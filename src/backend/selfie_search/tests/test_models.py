from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo
from selfie_search.models import (
    SelfieSearch,
    SelfieSearchAttempt,
    SelfieSearchCandidate,
    SelfieSearchJob,
    SelfieSearchResult,
)


class SelfieSearchModelTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="selfie-search-owner")
        self.event = self.make_event("one")
        self.other_event = self.make_event("two")
        self.photo = self.make_photo("one", self.event)
        self.other_photo = self.make_photo("two", self.other_event)
        self.search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="a" * 64,
            temporary_object_key="selfie-search/one.jpg",
            configuration={"embedding_model": "sface-v1"},
        )

    def make_event(self, suffix: str) -> Event:
        return Event.objects.create(
            name=f"Selfie search {suffix}",
            slug=f"selfie-search-{suffix}",
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 30),
            city="Moscow",
        )

    def make_photo(self, suffix: str, event: Event) -> Photo:
        return Photo.objects.create(
            id=f"selfie-{suffix}",
            event=event,
            uploaded_by=self.user,
            original_key=f"private/{suffix}.jpg",
            original_filename=f"{suffix}.jpg",
            original_size=1,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def test_search_state_is_limited_to_public_states(self) -> None:
        self.search.status = "invented"

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.search.save()

    def test_one_job_is_allowed_per_search(self) -> None:
        SelfieSearchJob.objects.create(search=self.search, configuration={})

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SelfieSearchJob.objects.create(search=self.search, configuration={})

    def test_candidate_rejects_photo_from_another_event(self) -> None:
        candidate = SelfieSearchCandidate(
            search=self.search,
            embedding_id=self.make_embedding_id(),
            photo=self.other_photo,
        )

        with self.assertRaises(ValidationError):
            candidate.full_clean()

    def test_candidate_rejects_embedding_from_another_event(self) -> None:
        candidate = SelfieSearchCandidate(
            search=self.search,
            embedding_id=self.make_embedding_id(other_event=True),
            photo=self.photo,
        )

        with self.assertRaises(ValidationError):
            candidate.full_clean()

    def test_candidate_embedding_is_unique_within_a_search(self) -> None:
        embedding_id = self.make_embedding_id()
        SelfieSearchCandidate.objects.create(
            search=self.search, embedding_id=embedding_id, photo=self.photo
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SelfieSearchCandidate.objects.create(
                    search=self.search, embedding_id=embedding_id, photo=self.photo
                )

    def test_persisted_candidate_identity_is_immutable(self) -> None:
        candidate = SelfieSearchCandidate.objects.create(
            search=self.search,
            embedding_id=self.make_embedding_id(),
            photo=self.photo,
        )
        candidate.photo = self.other_photo

        with self.assertRaises(ValidationError):
            candidate.save()

    def test_persisted_candidate_cannot_be_deleted_through_the_model(self) -> None:
        candidate = SelfieSearchCandidate.objects.create(
            search=self.search,
            embedding_id=self.make_embedding_id(),
            photo=self.photo,
        )

        with self.assertRaises(ValidationError):
            candidate.delete()

    def test_result_photo_and_rank_are_unique_per_search(self) -> None:
        detection_id = self.make_detection_id()
        SelfieSearchResult.objects.create(
            search=self.search,
            photo=self.photo,
            detection_id=detection_id,
            rank=1,
            cosine_distance=0.1,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SelfieSearchResult.objects.create(
                    search=self.search,
                    photo=self.photo,
                    detection_id=detection_id,
                    rank=2,
                    cosine_distance=0.2,
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SelfieSearchResult.objects.create(
                    search=self.search,
                    photo=self.other_photo,
                    detection_id=detection_id,
                    rank=1,
                    cosine_distance=0.2,
                )

    def test_result_rejects_cross_event_evidence(self) -> None:
        result = SelfieSearchResult(
            search=self.search,
            photo=self.photo,
            detection_id=self.make_detection_id(other_event=True),
            rank=1,
            cosine_distance=0.1,
        )

        with self.assertRaises(ValidationError):
            result.full_clean()

    def test_ready_result_rows_are_immutable(self) -> None:
        result = SelfieSearchResult.objects.create(
            search=self.search,
            photo=self.photo,
            detection_id=self.make_detection_id(),
            rank=1,
            cosine_distance=0.1,
        )
        self.search.status = SelfieSearch.Status.READY
        self.search.save()
        result.cosine_distance = 0.2

        with self.assertRaises(ValidationError):
            result.save()

    def test_product_models_do_not_persist_a_query_vector(self) -> None:
        for model in (SelfieSearch, SelfieSearchJob, SelfieSearchAttempt, SelfieSearchResult):
            fields = {field.name for field in model._meta.fields}
            self.assertNotIn("query_vector", fields)
            self.assertNotIn("query_embedding", fields)
            self.assertNotIn("vector", fields)

    def make_embedding_id(self, *, other_event: bool = False):
        from processing.models import (
            EventProcessingRun,
            FaceEmbedding,
            FaceProcessingAttemptArtifact,
            PhotoFaceDetection,
            ProcessingAttempt,
            ProcessingJob,
        )

        event = self.other_event if other_event else self.event
        photo = self.other_photo if other_event else self.photo
        run = EventProcessingRun.objects.create(
            event=event,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash="b" * 64,
        )
        job = ProcessingJob.objects.create(
            event=event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash="b" * 64,
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
        )
        artifact = FaceProcessingAttemptArtifact.objects.create(attempt=attempt)
        detection = PhotoFaceDetection.objects.create(
            artifact=artifact, attempt=attempt, face_index=0
        )
        return FaceEmbedding.objects.create(detection=detection).id

    def make_detection_id(self, *, other_event: bool = False):
        from processing.models import FaceEmbedding

        return FaceEmbedding.objects.get(
            pk=self.make_embedding_id(other_event=other_event)
        ).detection_id
