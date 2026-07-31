import hashlib
from datetime import date
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo
from processing.models import (
    EventProcessingRun,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    ProcessingAttempt,
    ProcessingJob,
)
from selfie_search.models import SelfieSearch, SelfieSearchResult
from selfie_search.services.results import saved_ready_result_page


class SavedReadyResultPageTests(TestCase):
    def setUp(self) -> None:
        self.owner = get_user_model().objects.create_user(username="result-page-owner")
        self.event = Event.objects.create(
            name="City Run",
            slug="city-run",
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 30),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        self.search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest=hashlib.sha256(b"result-page-token").hexdigest(),
            status=SelfieSearch.Status.READY,
            temporary_object_key="",
            configuration={"public-contract": 1},
        )

    def add_result(self, *, photo_id: str, rank: int) -> Photo:
        photo = Photo.objects.create(
            id=photo_id,
            event=self.event,
            uploaded_by=self.owner,
            original_key=f"originals/{uuid4().hex}",
            original_filename=f"{photo_id}.jpg",
            original_size=5,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        configuration_hash = "a" * 64
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash=configuration_hash,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash=configuration_hash,
            input_fingerprint={},
        )
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
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
        SelfieSearchResult.objects.create(
            search=self.search,
            photo=photo,
            detection=detection,
            rank=rank,
            cosine_distance=0.1,
        )
        return photo

    def test_page_preserves_saved_rank_after_current_eligibility_filtering(self) -> None:
        first = self.add_result(photo_id="rank-one", rank=1)
        removed = self.add_result(photo_id="rank-two", rank=2)
        third = self.add_result(photo_id="rank-three", rank=3)
        Photo.objects.filter(pk=removed.pk).update(original_key="")

        page = saved_ready_result_page(search=self.search, cursor=None)

        self.assertEqual(tuple(photo.pk for photo in page.photos), (first.pk, third.pk))
        self.assertIsNone(page.next_cursor)

    def test_nonready_search_has_no_page_or_cursor(self) -> None:
        self.search.status = SelfieSearch.Status.PROCESSING
        self.search.save(update_fields=["status"])

        page = saved_ready_result_page(search=self.search, cursor=None)

        self.assertEqual(page.photos, ())
        self.assertIsNone(page.next_cursor)
