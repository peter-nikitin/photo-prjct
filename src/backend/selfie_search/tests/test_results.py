import hashlib
from datetime import date
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo
from processing.models import (
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    EventProcessingRun,
    FaceProcessingAttemptArtifact,
    PhotoDerivative,
    PhotoFaceDetection,
    PhotoProcessingState,
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

    def add_result(
        self,
        *,
        photo_id: str,
        rank: int,
        gallery_media_policy: str = Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED,
    ) -> Photo:
        generation = (
            Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1
            if gallery_media_policy == Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
            else Photo.ProcessingGeneration.LEGACY_ORIGINAL_V1
        )
        photo = Photo.objects.create(
            id=photo_id,
            event=self.event,
            uploaded_by=self.owner,
            original_key=f"originals/{uuid4().hex}",
            original_filename=f"{photo_id}.jpg",
            original_size=5,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=generation,
            gallery_media_policy=gallery_media_policy,
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

    def publish_watermark(self, photo: Photo) -> None:
        configuration = {"generate_watermarked_preview": {"variant": "preview-watermarked-v1"}}
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=2,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash="b" * 64,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash=run.configuration_hash,
            input_fingerprint={},
            status=ProcessingJob.Status.SUCCEEDED,
            completed_at=timezone.now(),
        )
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        state, _ = PhotoProcessingState.objects.get_or_create(
            photo=photo,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
        )
        state.status = PhotoProcessingState.Status.SUCCEEDED
        state.current_run = run
        state.current_job = job
        state.current_attempt = attempt
        state.accepted_attempt = attempt
        state.succeeded_at = timezone.now()
        state.save()
        PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-watermarked-v1",
            final_key=f"derivatives/previews/{photo.pk}/preview-watermarked-v1/{uuid4().hex}.jpg",
            byte_size=10,
            content_type="image/jpeg",
            width=10,
            height=10,
            oriented_source_width=10,
            oriented_source_height=10,
            sha256="b" * 64,
            accepted_attempt=attempt,
        )

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

    def test_paid_saved_results_keep_legacy_members_but_gate_new_watermarked_members(self) -> None:
        self.event.access_type = Event.AccessType.PAID
        self.event.save(update_fields=["access_type"])
        legacy = self.add_result(photo_id="paid-legacy-saved", rank=1)
        watermarked = self.add_result(
            photo_id="paid-watermarked-saved",
            rank=2,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        self.publish_watermark(watermarked)

        gate_off = saved_ready_result_page(
            search=self.search,
            page_number=None,
            paid_watermarked_previews_enabled=False,
        )
        gate_on = saved_ready_result_page(
            search=self.search,
            page_number=None,
            paid_watermarked_previews_enabled=True,
        )

        self.assertEqual(tuple(row.photo_id for row in gate_off.object_list), (legacy.pk,))
        self.assertEqual(
            tuple(row.photo_id for row in gate_on.object_list),
            (legacy.pk, watermarked.pk),
        )

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
