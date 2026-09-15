from __future__ import annotations

from datetime import date
from typing import cast
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from ingestion.storage import ObjectMissing
from processing.models import (
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)

from picflow.gallery_media_projection import publish_gallery_media
from picflow.gallery_preview_grants import issue_gallery_preview_urls
from picflow.models import Event, Photo

LEGACY_ORIGINAL_POLICY = cast(str, Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED)
PREVIEW_REQUIRED_POLICY = cast(str, Photo.GalleryMediaPolicy.PREVIEW_REQUIRED)
WATERMARKED_PREVIEW_POLICY = cast(str, Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED)


class _Signer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def sign_accepted_preview(self, *, key: str, expires_in: int) -> str:
        self.calls.append((key, expires_in))
        return f"https://storage.example.test/{key}?signature=secret"


class GalleryPreviewGrantTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="preview-grant-reader")
        self.event = Event.objects.create(
            name="Preview grants",
            slug="preview-grants",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

    def photo(self, *, photo_id: str, policy: str) -> Photo:
        generation_by_policy = {
            PREVIEW_REQUIRED_POLICY: Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            WATERMARKED_PREVIEW_POLICY: (Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1),
            LEGACY_ORIGINAL_POLICY: (Photo.ProcessingGeneration.LEGACY_ORIGINAL_V1),
        }
        return Photo.objects.create(
            id=photo_id,
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{photo_id}",
            original_filename=f"{photo_id}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=generation_by_policy[policy],
            gallery_media_policy=policy,
        )

    def accept_derivative(
        self, *, photo: Photo, processor_type: str, variant: str
    ) -> PhotoDerivative:
        configuration = {processor_type: {"variant": variant}}
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=1,
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            configuration_hash=uuid4().hex + uuid4().hex,
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            configuration_hash=run.configuration_hash,
            input_fingerprint={},
            status=ProcessingJob.Status.SUCCEEDED,
            completed_at=timezone.now(),
        )
        attempt = ProcessingAttempt.objects.create(
            event=photo.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=1,
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        state, _ = PhotoProcessingState.objects.get_or_create(
            photo=photo,
            processor_type=processor_type,
        )
        state.status = PhotoProcessingState.Status.SUCCEEDED
        state.current_run = run
        state.current_job = job
        state.current_attempt = attempt
        state.accepted_attempt = attempt
        state.succeeded_at = timezone.now()
        state.save()
        derivative = PhotoDerivative.objects.create(
            photo=photo,
            variant=variant,
            final_key=(f"derivatives/previews/{photo.pk}/{variant}/{uuid4()}-{'a' * 64}.jpg"),
            byte_size=10,
            content_type="image/jpeg",
            width=10,
            height=10,
            oriented_source_width=10,
            oriented_source_height=10,
            sha256="a" * 64,
            accepted_attempt=attempt,
        )
        photo.gallery_media_projection = publish_gallery_media(derivative)
        return derivative

    def test_issues_only_policy_selected_accepted_derivative_urls(self) -> None:
        """The break caught here would sign a legacy original or the wrong preview variant."""
        free = self.photo(
            photo_id="free-preview",
            policy=PREVIEW_REQUIRED_POLICY,
        )
        free_derivative = self.accept_derivative(
            photo=free,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            variant="preview-small-v1",
        )
        paid = self.photo(
            photo_id="paid-preview",
            policy=WATERMARKED_PREVIEW_POLICY,
        )
        paid_derivative = self.accept_derivative(
            photo=paid,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            variant="preview-watermarked-v1",
        )
        legacy = self.photo(
            photo_id="legacy-preview",
            policy=LEGACY_ORIGINAL_POLICY,
        )
        signer = _Signer()

        urls = issue_gallery_preview_urls(photos=[free, paid, legacy], signer=signer)

        assert urls == {
            free.pk: f"https://storage.example.test/{free_derivative.final_key}?signature=secret",
            paid.pk: f"https://storage.example.test/{paid_derivative.final_key}?signature=secret",
        }
        assert signer.calls == [
            (free_derivative.final_key, 21_600),
            (paid_derivative.final_key, 21_600),
        ]

    def test_uses_no_database_query_for_one_or_one_hundred_projected_photos(self) -> None:
        """The break caught here would reconstruct published evidence during a page read."""
        one = self.photo(
            photo_id="one-preview",
            policy=PREVIEW_REQUIRED_POLICY,
        )
        self.accept_derivative(
            photo=one,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            variant="preview-small-v1",
        )
        signer = _Signer()
        with CaptureQueriesContext(connection) as one_queries:
            issue_gallery_preview_urls(photos=[one], signer=signer)

        many = [
            self.photo(
                photo_id=f"many-preview-{number}",
                policy=PREVIEW_REQUIRED_POLICY,
            )
            for number in range(100)
        ]
        for photo in many:
            self.accept_derivative(
                photo=photo,
                processor_type=GENERATE_PREVIEW_PROCESSOR,
                variant="preview-small-v1",
            )
        with CaptureQueriesContext(connection) as many_queries:
            many_urls = issue_gallery_preview_urls(photos=many, signer=signer)

        assert len(one_queries) == 0
        assert len(many_queries) == 0
        assert len(many_urls) == 100
        assert len(set(many_urls.values())) == 100

    def test_rejects_duplicate_photo_identities_before_signing(self) -> None:
        legacy = self.photo(photo_id="duplicate-preview", policy=LEGACY_ORIGINAL_POLICY)
        signer = _Signer()

        with self.assertRaisesMessage(ValueError, "duplicate photo identities"):
            issue_gallery_preview_urls(photos=[legacy, legacy], signer=signer)

        assert signer.calls == []

    def test_fails_closed_when_required_projection_slot_is_missing(self) -> None:
        missing = self.photo(photo_id="missing-preview", policy=PREVIEW_REQUIRED_POLICY)
        signer = _Signer()

        with self.assertRaises(ObjectMissing):
            issue_gallery_preview_urls(photos=[missing], signer=signer)

        assert signer.calls == []
