import hashlib
from datetime import date
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
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
from selfie_search.models import SelfieSearch, SelfieSearchDirectEvidence, SelfieSearchResult
from selfie_search.services.results import (
    PublicSearchNotFound,
    resolve_public_result,
    saved_ready_result_page,
)


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
        result = SelfieSearchResult.objects.create(
            search=self.search,
            photo=photo,
            rank=rank,
        )
        SelfieSearchDirectEvidence.objects.create(
            result=result, detection=detection, cosine_distance=0.1
        )
        return photo

    def test_page_preserves_saved_rank_after_current_eligibility_filtering(self) -> None:
        first = self.add_result(photo_id="rank-one", rank=1)
        removed = self.add_result(photo_id="rank-two", rank=2)
        third = self.add_result(photo_id="rank-three", rank=3)
        Photo.objects.filter(pk=removed.pk).update(original_key="")

        page = saved_ready_result_page(search=self.search, page_number=None)

        self.assertEqual(tuple(row.photo_id for row in page.object_list), (first.pk, third.pk))
        self.assertFalse(page.has_next())

    def test_nonready_search_has_empty_page(self) -> None:
        self.search.status = SelfieSearch.Status.PROCESSING
        self.search.save(update_fields=["status"])

        page = saved_ready_result_page(search=self.search, page_number=None)

        self.assertEqual(tuple(page.object_list), ())
        self.assertFalse(page.has_next())

    def test_bearer_resolution_uses_the_shared_site_visibility_decision(self) -> None:
        draft = Event.objects.create(
            name="Draft result",
            slug="draft-result",
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 30),
            city="Moscow",
            publication_status=Event.PublicationStatus.DRAFT,
            timezone_name="Europe/Moscow",
        )
        unavailable = Event.objects.create(
            name="Unavailable result",
            slug="unavailable-result",
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 30),
            city="Moscow",
            publication_status=Event.PublicationStatus.UNAVAILABLE,
        )
        draft_token = "draft-result-token"
        unavailable_token = "unavailable-result-token"
        draft_search = SelfieSearch.objects.create(
            event=draft,
            public_token_digest=hashlib.sha256(draft_token.encode()).hexdigest(),
            temporary_object_key="",
            configuration={"public-contract": 1},
        )
        SelfieSearch.objects.create(
            event=unavailable,
            public_token_digest=hashlib.sha256(unavailable_token.encode()).hexdigest(),
            temporary_object_key="",
            configuration={"public-contract": 1},
        )
        ordinary_user = get_user_model().objects.create_user(username="result-ordinary")
        staff_user = get_user_model().objects.create_user(username="result-staff", is_staff=True)

        for user in (AnonymousUser(), ordinary_user):
            with self.subTest(user=getattr(user, "username", "anonymous")):
                with self.assertRaises(PublicSearchNotFound):
                    resolve_public_result(
                        event_slug=draft.slug,
                        public_token=draft_token,
                        user=user,
                    )
        self.assertEqual(
            resolve_public_result(
                event_slug=draft.slug,
                public_token=draft_token,
                user=staff_user,
            ).pk,
            draft_search.pk,
        )
        with self.assertRaises(PublicSearchNotFound):
            resolve_public_result(
                event_slug=unavailable.slug,
                public_token=unavailable_token,
                user=staff_user,
            )
