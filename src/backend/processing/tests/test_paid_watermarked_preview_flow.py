from __future__ import annotations

import hashlib
from datetime import date, timedelta
from io import BytesIO
from typing import cast
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, modify_settings, override_settings
from django.urls import reverse
from django.utils import timezone
from feature_flags.states import FEATURE_FLAG_STAFF, FeatureFlagState
from feature_flags.testing import override_feature_flags
from ingestion.models import UploadItem
from ingestion.services.batches import (
    AuthorizationReason,
    BatchInput,
    ItemInput,
    authorize_item,
    create_batch,
    register_items,
)
from ingestion.services.confirmation import confirm_upload_item
from ingestion.storage import ObjectChanged, ObjectIdentity, ObjectMissing, UploadGrant
from picflow.gallery import PublicMediaResolver, gallery_photo_queryset
from picflow.models import Event, Photo
from PIL import Image
from selfie_search.models import SelfieSearch, SelfieSearchDirectEvidence, SelfieSearchResult

from processing.contracts import ClaimedJob
from processing.models import (
    PhotoDerivative,
    PhotoFaceDetection,
    PhotoProcessingState,
    ProcessingJob,
)
from processing.services.jobs import claim_job, complete_attempt, fail_attempt
from processing.services.previews import complete_preview_attempt
from processing.storage import ObjectConflict, PreviewObject


class _UploadStorage:
    """In-memory object boundary; upload authorization and confirmation remain real services."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str, str]] = {}

    def add(self, key: str, content: bytes) -> None:
        etag_value = hashlib.sha256(key.encode()).hexdigest()[:24]
        self.objects[key] = (content, f'"{etag_value}"', "image/jpeg")

    def create_presigned_post(self, *, incoming_key: str, max_bytes: int) -> UploadGrant:
        del incoming_key, max_bytes
        return UploadGrant("https://upload.example.test", {}, timezone.now() + timedelta(minutes=5))

    def inspect(self, *, key: str) -> ObjectIdentity:
        try:
            content, etag_wire, content_type = self.objects[key]
        except KeyError:
            raise ObjectMissing() from None
        return ObjectIdentity(
            etag_wire=etag_wire,
            etag_value=etag_wire.strip('"'),
            size=len(content),
            content_type=content_type,
        )

    def read_range(self, *, key: str, etag_wire: str, start: int, end: int) -> bytes:
        content, actual_etag, _ = self.objects[key]
        if etag_wire != actual_etag:
            raise ObjectChanged()
        return content[start : end + 1]

    def promote(self, *, incoming_key: str, final_key: str, etag_wire: str) -> ObjectIdentity:
        content, actual_etag, content_type = self.objects[incoming_key]
        if etag_wire != actual_etag:
            raise ObjectChanged()
        self.objects[final_key] = (bytes(content), actual_etag, content_type)
        return self.inspect(key=final_key)

    def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


class _PublicationStorage:
    """The external byte store is fake; Django verification and publication are not."""

    def __init__(self, object: PreviewObject) -> None:
        self.object = object
        self.final_object: PreviewObject | None = None

    def verify(self, *, key: str, max_bytes: int) -> PreviewObject:
        del max_bytes
        if key.startswith("derivatives/"):
            if self.final_object is None:
                raise ObjectMissing()
            return self.final_object
        return self.object

    def promote(self, *, staging_key: str, final_key: str, source_etag: str) -> PreviewObject:
        del staging_key, final_key, source_etag
        if self.final_object is not None:
            raise ObjectConflict()
        self.final_object = self.object
        return self.final_object


class _SigningStorage:
    """Records the exact private object for which the real resolver asks a signed grant."""

    def __init__(self) -> None:
        self.signed_requests: list[tuple[str, str | None]] = []

    def sign_final(self, *, key: str, attachment_filename: str | None = None) -> str:
        self.signed_requests.append((key, attachment_filename))
        return f"https://storage.example.test/{key}?signature=secret"

    def open_final(self, *, key: str):
        raise AssertionError(f"redirect routes must not stream {key}")


@override_settings(
    PHOTO_PROCESSING_PREVIEW_ENABLED=True,
    PHOTO_PROCESSING_FACE_ENABLED=True,
    SELFIE_FEEDBACK_ENABLED=False,
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
@modify_settings(MIDDLEWARE={"remove": "whitenoise.middleware.WhiteNoiseMiddleware"})
class PaidWatermarkedPreviewFlowTests(TestCase):
    """Human-auditable proof across upload, processing, presentation, and authorization."""

    def setUp(self) -> None:
        self.feature_flag_states: dict[str, FeatureFlagState] = {}
        self.enterContext(override_feature_flags(self.feature_flag_states))
        self.staff = get_user_model().objects.create_user(
            username="watermark-flow-staff",
            is_staff=True,
        )
        self.client.force_login(self.staff)
        self.upload_storage = _UploadStorage()
        encoded = BytesIO()
        image = Image.new("RGB", (64, 40), "white")
        try:
            image.save(encoded, "JPEG")
        finally:
            image.close()
        self.jpeg = encoded.getvalue()

    def event(self, slug: str, *, access_type: str) -> Event:
        values: dict[str, object] = {
            "name": f"Watermark flow {slug}",
            "slug": slug,
            "start_date": date(2026, 8, 20),
            "end_date": date(2026, 8, 20),
            "city": "Moscow",
            "timezone_name": "Europe/Moscow",
            "access_type": access_type,
            "publication_status": Event.PublicationStatus.PUBLISHED,
            "face_search_generation": Event.FaceSearchGeneration.SFACE_V3,
        }
        if access_type == Event.AccessType.PAID:
            values["price_per_photo_kopecks"] = 30000
        return Event.objects.create(**values)

    def enable_paid_gate_for_staff(self) -> None:
        self.feature_flag_states.update(
            {
                "paid-events": FEATURE_FLAG_STAFF,
                "paid-watermarked-previews": FEATURE_FLAG_STAFF,
            }
        )

    def confirm_jpeg(self, event: Event, *, filename: str) -> Photo:
        batch = create_batch(
            uploader=self.staff,
            event=event,
            data=BatchInput(expected_item_count=1),
        )
        registered = register_items(
            uploader=self.staff,
            batch_id=batch.id,
            items=[ItemInput(uuid4(), filename, "image/jpeg", len(self.jpeg), None)],
        )
        item_id = registered.items[0].id
        authorize_item(
            uploader=self.staff,
            batch_id=batch.id,
            item_id=item_id,
            reason=AuthorizationReason.DATA_ATTEMPT,
            storage=self.upload_storage,
        )
        item = UploadItem.objects.get(pk=item_id)
        self.upload_storage.add(item.incoming_key, self.jpeg)
        photo = confirm_upload_item(
            uploader=self.staff,
            batch_id=batch.id,
            item_id=item_id,
            storage=self.upload_storage,
        )
        assert photo is not None
        return photo

    def claim(self, photo: Photo, processor_type: str) -> ClaimedJob:
        state = PhotoProcessingState.objects.get(photo=photo, processor_type=processor_type)
        assert state.current_job is not None
        claimed = claim_job(
            contract_version=state.current_job.contract_version,
            processor_type=processor_type,
            processor_version=state.current_job.processor_version,
            worker_build=f"integration-{processor_type}",
            event_id=photo.event_id,
            configuration_hash=state.current_job.configuration_hash,
        )
        self.assertIsInstance(claimed, ClaimedJob)
        assert isinstance(claimed, ClaimedJob)
        self.assertEqual(claimed.job.id, state.current_job_id)
        return claimed

    def publish_clean_preview(self, photo: Photo, *, label: str) -> PhotoDerivative:
        claimed = self.claim(photo, "generate_preview")
        object = self.preview_object(label, width=64, height=40)
        completion = complete_preview_attempt(
            claimed.attempt.id,
            result={
                "variant": "preview-small-v1",
                "content_type": "image/jpeg",
                "byte_size": object.byte_size,
                "width": 64,
                "height": 40,
                "oriented_source_width": 64,
                "oriented_source_height": 40,
                "sha256": object.sha256,
                "upload_ms": 3,
                "warnings": [],
            },
            storage=_PublicationStorage(object),
        )
        self.assertEqual(completion.attempt.status, completion.attempt.Status.SUCCEEDED)
        return PhotoDerivative.objects.get(photo=photo, variant="preview-small-v1")

    def publish_watermark(self, photo: Photo, *, label: str) -> PhotoDerivative:
        claimed = self.claim(photo, "generate_watermarked_preview")
        clean = PhotoDerivative.objects.get(photo=photo, variant="preview-small-v1")
        object = self.preview_object(label, width=clean.width, height=clean.height)
        completion = complete_preview_attempt(
            claimed.attempt.id,
            result={
                "variant": "preview-watermarked-v1",
                "content_type": "image/jpeg",
                "byte_size": object.byte_size,
                "width": clean.width,
                "height": clean.height,
                "sha256": object.sha256,
                "upload_ms": 4,
                "warnings": [],
            },
            storage=_PublicationStorage(object),
        )
        self.assertEqual(completion.attempt.status, completion.attempt.Status.SUCCEEDED)
        return PhotoDerivative.objects.get(photo=photo, variant="preview-watermarked-v1")

    @staticmethod
    def preview_object(label: str, *, width: int, height: int) -> PreviewObject:
        content = f"integration-{label}".encode()
        return PreviewObject(
            etag_wire=f'"{label}"',
            etag_value=label,
            byte_size=len(content),
            content_type="image/jpeg",
            sha256=hashlib.sha256(content).hexdigest(),
            width=width,
            height=height,
        )

    def complete_face(self, photo: Photo) -> PhotoFaceDetection:
        clean = PhotoDerivative.objects.get(photo=photo, variant="preview-small-v1")
        claimed = self.claim(photo, "face_embedding")
        complete_attempt(
            claimed.attempt.id,
            result={
                "model": "sface",
                "face_count": 1,
                "faces": [
                    {
                        "index": 0,
                        "bbox": [8, 6, 20, 20],
                        "confidence": 0.95,
                        "landmarks": [[10, 10], [20, 10], [15, 15], [11, 22], [19, 22]],
                        "embedding": [0.6, 0.8],
                    }
                ],
                "warnings": [],
                "input_geometry": {
                    "coordinate_space": "preview-small-v1",
                    "pixel_width": clean.width,
                    "pixel_height": clean.height,
                    "oriented_source_width": clean.oriented_source_width,
                    "oriented_source_height": clean.oriented_source_height,
                },
            },
        )
        return PhotoFaceDetection.objects.get(attempt=claimed.attempt)

    def ready_result(
        self,
        *,
        event: Event,
        photo: Photo,
        detection: PhotoFaceDetection,
    ) -> tuple[SelfieSearch, str]:
        token = f"watermark-flow-{uuid4().hex}"
        search = SelfieSearch.objects.create(
            event=event,
            public_token_digest=hashlib.sha256(token.encode("ascii")).hexdigest(),
            status=SelfieSearch.Status.READY,
            temporary_object_key="",
            configuration={"public-contract": 1},
            eligible_photo_count=1,
            matched_photo_count=1,
        )
        result = SelfieSearchResult.objects.create(search=search, photo=photo, rank=1)
        SelfieSearchDirectEvidence.objects.create(
            result=result,
            detection=detection,
            cosine_distance=0.1,
        )
        return search, token

    def test_new_paid_upload_uses_clean_ml_evidence_and_only_watermark_public_bytes(self) -> None:
        self.enable_paid_gate_for_staff()
        event = self.event(
            "paid-critical-path",
            access_type=cast(str, Event.AccessType.PAID),
        )

        photo = self.confirm_jpeg(event, filename="new-paid.jpg")

        self.assertEqual(
            (photo.processing_generation, photo.gallery_media_policy),
            (
                Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
                Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
            ),
        )
        self.assertFalse(
            ProcessingJob.objects.filter(
                photo=photo,
                processor_type__in=("face_embedding", "generate_watermarked_preview"),
            ).exists()
        )

        clean = self.publish_clean_preview(photo, label="paid-clean")
        face = PhotoProcessingState.objects.get(photo=photo, processor_type="face_embedding")
        watermark = PhotoProcessingState.objects.get(
            photo=photo,
            processor_type="generate_watermarked_preview",
        )
        expected_clean_fingerprint = {
            "object_key": clean.final_key,
            "object_size": clean.byte_size,
            "object_content_type": "image/jpeg",
            "object_etag": None,
            "media_kind": "preview-small-v1",
            "pixel_width": 64,
            "pixel_height": 40,
        }
        self.assertEqual(face.current_job.input_fingerprint, expected_clean_fingerprint)
        self.assertEqual(watermark.current_job.input_fingerprint, expected_clean_fingerprint)
        self.assertIn(str(clean.accepted_attempt_id), clean.final_key)
        self.assertNotEqual(clean.final_key, photo.original_key)

        detection = self.complete_face(photo)
        watermarked = self.publish_watermark(photo, label="paid-watermarked")

        self.assertEqual(
            list(PhotoDerivative.objects.filter(photo=photo).values_list("variant", flat=True)),
            ["preview-small-v1", "preview-watermarked-v1"],
        )
        self.assertEqual(detection.geometry["coordinate_space"], "preview-small-v1")
        self.assertEqual(
            list(
                gallery_photo_queryset(
                    event=event,
                    paid_watermarked_previews_enabled=True,
                )
            ),
            [photo],
        )

        gallery_response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))
        gallery_dto = gallery_response.context["gallery_photos"][0]
        self.assertEqual(gallery_dto.photo_id, photo.pk)
        self.assertIsNone(gallery_dto.download_url)
        expected_gallery_media_urls = tuple(
            reverse(
                "photo_media",
                kwargs={"slug": event.slug, "photo_id": photo.pk, "variant": variant},
            )
            for variant in ("preview-small", "preview-large")
        )
        self.assertEqual(
            (
                gallery_dto.preview_media_small.variant,
                gallery_dto.preview_media_small.url,
                gallery_dto.preview_media_large.variant,
                gallery_dto.preview_media_large.url,
            ),
            (
                "preview-small",
                expected_gallery_media_urls[0],
                "preview-large",
                expected_gallery_media_urls[1],
            ),
        )

        _, token = self.ready_result(event=event, photo=photo, detection=detection)
        result_response = self.client.get(
            reverse(
                "selfie_search:result",
                kwargs={"event_slug": event.slug, "public_token": token},
            )
        )
        result_dto = result_response.context["gallery_photos"][0]
        self.assertEqual(result_dto.photo_id, photo.pk)
        self.assertIsNone(result_dto.download_url)
        expected_result_media_urls = tuple(
            reverse(
                "selfie_search:result_media",
                kwargs={
                    "event_slug": event.slug,
                    "public_token": token,
                    "photo_id": photo.pk,
                    "variant": variant,
                },
            )
            for variant in ("preview-small", "preview-large")
        )
        self.assertEqual(
            (
                result_dto.preview_media_small.variant,
                result_dto.preview_media_small.url,
                result_dto.preview_media_large.variant,
                result_dto.preview_media_large.url,
            ),
            (
                "preview-small",
                expected_result_media_urls[0],
                "preview-large",
                expected_result_media_urls[1],
            ),
        )
        for response in (gallery_response, result_response):
            body = response.content.decode(response.charset)
            self.assertNotIn(photo.original_key, body)
            self.assertNotIn(clean.final_key, body)
            self.assertNotIn(watermarked.final_key, body)
            self.assertNotIn('class="gallery-download"', body)
            self.assertNotIn('class="gallery-lightbox-download"', body)

        signing_storage = _SigningStorage()
        resolver = PublicMediaResolver(signing_storage)
        gallery_media_urls = [
            gallery_dto.preview_media_small.url,
            gallery_dto.preview_media_large.url,
        ]
        result_media_urls = [
            result_dto.preview_media_small.url,
            result_dto.preview_media_large.url,
        ]
        gallery_download_url = reverse(
            "photo_download", kwargs={"slug": event.slug, "photo_id": photo.pk}
        )
        result_download_url = reverse(
            "selfie_search:result_download",
            kwargs={"event_slug": event.slug, "public_token": token, "photo_id": photo.pk},
        )
        with (
            patch("config.views._public_media_resolver", return_value=resolver),
            patch("selfie_search.views._public_media_resolver", return_value=resolver),
        ):
            media_responses = [
                *(self.client.get(url) for url in gallery_media_urls),
                *(self.client.get(url) for url in result_media_urls),
            ]
            download_responses = [
                self.client.get(gallery_download_url),
                self.client.get(result_download_url),
            ]

        self.assertEqual([response.status_code for response in media_responses], [302] * 4)
        self.assertTrue(
            all(watermarked.final_key in response["Location"] for response in media_responses)
        )
        self.assertEqual([response.status_code for response in download_responses], [404, 404])
        self.assertEqual(
            signing_storage.signed_requests,
            [(watermarked.final_key, None)] * 4,
        )

    def test_free_upload_keeps_original_download_and_existing_paid_photo_never_backfills(
        self,
    ) -> None:
        free_event = self.event(
            "free-sibling",
            access_type=cast(str, Event.AccessType.FREE),
        )
        existing_paid_event = self.event(
            "existing-paid",
            access_type=cast(str, Event.AccessType.PAID),
        )
        free = self.confirm_jpeg(free_event, filename="free.jpg")
        existing_paid = self.confirm_jpeg(existing_paid_event, filename="existing-paid.jpg")

        free_clean = self.publish_clean_preview(free, label="free-clean")
        existing_clean = self.publish_clean_preview(existing_paid, label="existing-paid-clean")
        self.enable_paid_gate_for_staff()

        for photo in (free, existing_paid):
            self.assertEqual(
                (photo.processing_generation, photo.gallery_media_policy),
                (
                    Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
                    Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
                ),
            )
            self.assertFalse(
                ProcessingJob.objects.filter(
                    photo=photo,
                    processor_type="generate_watermarked_preview",
                ).exists()
            )
            self.assertFalse(
                PhotoProcessingState.objects.filter(
                    photo=photo,
                    processor_type="generate_watermarked_preview",
                ).exists()
            )
        self.assertEqual(
            list(PhotoDerivative.objects.filter(photo=free).values_list("variant", flat=True)),
            ["preview-small-v1"],
        )
        self.assertEqual(
            list(
                PhotoDerivative.objects.filter(photo=existing_paid).values_list(
                    "variant", flat=True
                )
            ),
            ["preview-small-v1"],
        )
        self.assertTrue(free_clean.final_key)
        self.assertTrue(existing_clean.final_key)
        self.assertEqual(
            list(
                gallery_photo_queryset(
                    event=existing_paid_event,
                    paid_watermarked_previews_enabled=True,
                )
            ),
            [],
        )

        free_response = self.client.get(reverse("event_detail", kwargs={"slug": free_event.slug}))
        existing_paid_response = self.client.get(
            reverse("event_detail", kwargs={"slug": existing_paid_event.slug})
        )
        free_dto = free_response.context["gallery_photos"][0]
        self.assertEqual(free_dto.photo_id, free.pk)
        self.assertEqual(
            free_dto.download_url,
            reverse("photo_download", kwargs={"slug": free_event.slug, "photo_id": free.pk}),
        )
        self.assertEqual(existing_paid_response.context["gallery_photos"], ())

        signing_storage = _SigningStorage()
        resolver = PublicMediaResolver(signing_storage)
        with patch("config.views._public_media_resolver", return_value=resolver):
            free_download = self.client.get(free_dto.download_url)
            existing_paid_media = self.client.get(
                reverse(
                    "photo_media",
                    kwargs={
                        "slug": existing_paid_event.slug,
                        "photo_id": existing_paid.pk,
                        "variant": "preview-small",
                    },
                )
            )

        self.assertEqual(free_download.status_code, 302)
        self.assertEqual(existing_paid_media.status_code, 404)
        self.assertEqual(
            signing_storage.signed_requests,
            [(free.original_key, f"findme-photo-{free.pk}.jpg")],
        )

    def test_watermark_retry_and_terminal_failure_keep_clean_and_face_siblings_unchanged(
        self,
    ) -> None:
        self.enable_paid_gate_for_staff()
        event = self.event(
            "paid-watermark-failure",
            access_type=cast(str, Event.AccessType.PAID),
        )
        photo = self.confirm_jpeg(event, filename="paid-failure.jpg")
        clean = self.publish_clean_preview(photo, label="paid-failure-clean")
        clean_identity = (
            clean.accepted_attempt_id,
            clean.final_key,
            clean.sha256,
            clean.byte_size,
            clean.width,
            clean.height,
        )
        face = PhotoProcessingState.objects.get(photo=photo, processor_type="face_embedding")
        face_identity = (face.status, face.current_job_id, face.current_job.input_fingerprint)
        watermark_claim = self.claim(photo, "generate_watermarked_preview")

        fail_attempt(
            watermark_claim.attempt.id,
            error_code="storage_unavailable",
            retryable=True,
            jitter=lambda _low, _high: 0,
        )
        watermark = PhotoProcessingState.objects.get(
            photo=photo,
            processor_type="generate_watermarked_preview",
        )
        self.assertEqual(watermark.status, PhotoProcessingState.Status.RETRY_WAIT)
        self.assertEqual(
            list(
                gallery_photo_queryset(
                    event=event,
                    paid_watermarked_previews_enabled=True,
                )
            ),
            [],
        )

        assert watermark.next_attempt_at is not None
        retry = claim_job(
            contract_version=watermark.current_job.contract_version,
            processor_type="generate_watermarked_preview",
            processor_version=watermark.current_job.processor_version,
            worker_build="integration-watermark-retry",
            now=watermark.next_attempt_at,
            event_id=event.id,
            configuration_hash=watermark.current_job.configuration_hash,
        )
        self.assertIsInstance(retry, ClaimedJob)
        assert isinstance(retry, ClaimedJob)
        self.assertEqual(retry.job.id, watermark_claim.job.id)
        fail_attempt(
            retry.attempt.id,
            error_code="watermark_asset_invalid",
            retryable=False,
        )

        watermark.refresh_from_db()
        clean.refresh_from_db()
        face.refresh_from_db()
        self.assertEqual(watermark.status, PhotoProcessingState.Status.FAILED)
        self.assertFalse(
            PhotoDerivative.objects.filter(
                photo=photo,
                variant="preview-watermarked-v1",
            ).exists()
        )
        self.assertEqual(
            (
                clean.accepted_attempt_id,
                clean.final_key,
                clean.sha256,
                clean.byte_size,
                clean.width,
                clean.height,
            ),
            clean_identity,
        )
        self.assertEqual(
            (face.status, face.current_job_id, face.current_job.input_fingerprint),
            face_identity,
        )
        self.assertEqual(
            list(
                gallery_photo_queryset(
                    event=event,
                    paid_watermarked_previews_enabled=True,
                )
            ),
            [],
        )

        signing_storage = _SigningStorage()
        resolver = PublicMediaResolver(signing_storage)
        with patch("config.views._public_media_resolver", return_value=resolver):
            response = self.client.get(
                reverse(
                    "photo_media",
                    kwargs={
                        "slug": event.slug,
                        "photo_id": photo.pk,
                        "variant": "preview-small",
                    },
                )
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(signing_storage.signed_requests, [])
