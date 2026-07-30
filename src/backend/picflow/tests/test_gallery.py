from dataclasses import FrozenInstanceError
from datetime import date
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from ingestion.storage import ObjectMissing, OpenedObject
from processing.models import (
    GENERATE_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)

from picflow.gallery import (
    CloseableMediaIterator,
    GalleryMedia,
    GalleryPhoto,
    GalleryPhotoFactory,
    PublicMediaResolver,
    ResolvedPublicMedia,
)
from picflow.models import Event, Photo


class GalleryPresentationContractTests(SimpleTestCase):
    def test_gallery_values_are_frozen(self) -> None:
        small = GalleryMedia(
            url="/events/city-run/photos/photo-42/media/preview-small/",
            variant="preview-small",
        )
        large = GalleryMedia(
            url="/events/city-run/photos/photo-42/media/preview-large/",
            variant="preview-large",
        )
        gallery_photo = GalleryPhoto(
            photo_id="photo-42",
            preview_media_small=small,
            preview_media_large=large,
            alt="Фото photo-42 с события City Run",
        )

        self.assertEqual(gallery_photo.photo_id, "photo-42")
        self.assertEqual(gallery_photo.preview_media_small, small)
        self.assertEqual(gallery_photo.preview_media_large, large)
        self.assertEqual(gallery_photo.alt, "Фото photo-42 с события City Run")
        alt_field = "alt"
        with self.assertRaises(FrozenInstanceError):
            setattr(gallery_photo, alt_field, "changed")
        url_field = "url"
        with self.assertRaises(FrozenInstanceError):
            setattr(small, url_field, "/changed/")

    @patch("boto3.client")
    @patch("picflow.gallery.reverse")
    def test_factory_builds_stable_variant_urls_without_storage(
        self, reverse, boto3_client
    ) -> None:
        event = Event(name="City Run", slug="city-run")
        photo = Photo(id="photo-42", event=event)
        reverse.side_effect = [
            "/events/city-run/photos/photo-42/media/preview-small/",
            "/events/city-run/photos/photo-42/media/preview-large/",
        ]

        gallery_photo = GalleryPhotoFactory.from_photo(photo=photo, event_slug="city-run")

        self.assertEqual(gallery_photo.photo_id, "photo-42")
        self.assertEqual(
            gallery_photo.preview_media_small,
            GalleryMedia(
                url="/events/city-run/photos/photo-42/media/preview-small/",
                variant="preview-small",
            ),
        )
        self.assertEqual(
            gallery_photo.preview_media_large,
            GalleryMedia(
                url="/events/city-run/photos/photo-42/media/preview-large/",
                variant="preview-large",
            ),
        )
        self.assertEqual(gallery_photo.alt, "Фото photo-42 с события City Run")
        self.assertEqual(
            reverse.call_args_list,
            [
                (
                    ("photo_media",),
                    {
                        "kwargs": {
                            "slug": "city-run",
                            "photo_id": "photo-42",
                            "variant": "preview-small",
                        }
                    },
                ),
                (
                    ("photo_media",),
                    {
                        "kwargs": {
                            "slug": "city-run",
                            "photo_id": "photo-42",
                            "variant": "preview-large",
                        }
                    },
                ),
            ],
        )
        boto3_client.assert_not_called()

    def test_factory_uses_a_scoped_media_url_builder_without_storage(self) -> None:
        event = Event(name="City Run", slug="city-run")
        photo = Photo(id="photo-42", event=event)
        calls: list[tuple[str, str]] = []

        def result_media_url(photo: Photo, variant: str) -> str:
            calls.append((photo.id, variant))
            return f"/events/city-run/selfie-search/bearer-token/photos/{photo.id}/media/{variant}/"

        gallery_photo = GalleryPhotoFactory.from_photo(
            photo=photo,
            event_slug=event.slug,
            media_url_builder=result_media_url,
        )

        self.assertEqual(
            gallery_photo.preview_media_small.url,
            "/events/city-run/selfie-search/bearer-token/photos/photo-42/media/preview-small/",
        )
        self.assertEqual(
            gallery_photo.preview_media_large.url,
            "/events/city-run/selfie-search/bearer-token/photos/photo-42/media/preview-large/",
        )
        self.assertEqual(
            calls,
            [("photo-42", "preview-small"), ("photo-42", "preview-large")],
        )


class _ReadableBody:
    def __init__(self, reads: list[bytes | Exception]) -> None:
        self._reads = iter(reads)
        self.close_calls = 0

    def read(self, amt: int | None = None) -> bytes:  # noqa: ARG002
        result = next(self._reads)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self) -> None:
        self.close_calls += 1


class _FinalObjectStorage:
    def __init__(self, opened_objects: list[OpenedObject]) -> None:
        self._opened_objects = iter(opened_objects)
        self.opened_keys: list[str] = []

    def open_final(self, *, key: str) -> OpenedObject:
        self.opened_keys.append(key)
        return next(self._opened_objects)


class PublicGalleryMediaTests(SimpleTestCase):
    def test_resolver_maps_legacy_small_and_large_variants_to_original(self) -> None:
        jpeg_body = _ReadableBody([])
        png_body = _ReadableBody([])
        storage = _FinalObjectStorage(
            [
                OpenedObject(body=jpeg_body, size=123, content_type="image/jpeg"),
                OpenedObject(body=png_body, size=456, content_type="image/png"),
            ]
        )
        resolver = PublicMediaResolver(storage)
        photo = Photo(id="photo-42", original_key="originals/0123456789abcdef0123456789abcdef")

        small = resolver.resolve(photo=photo, variant="preview-small")
        large = resolver.resolve(photo=photo, variant="preview-large")

        self.assertEqual(
            small,
            ResolvedPublicMedia(
                body=jpeg_body,
                content_length=123,
                content_type="image/jpeg",
                extension="jpg",
            ),
        )
        self.assertEqual(
            large,
            ResolvedPublicMedia(
                body=png_body,
                content_length=456,
                content_type="image/png",
                extension="png",
            ),
        )
        self.assertEqual(storage.opened_keys, [photo.original_key, photo.original_key])

    def test_resolver_rejects_unknown_variant_before_storage(self) -> None:
        storage = _FinalObjectStorage([])
        resolver = PublicMediaResolver(storage)
        photo = Photo(id="photo-42", original_key="originals/0123456789abcdef0123456789abcdef")

        with self.assertRaisesMessage(ValueError, "ineligible gallery media"):
            resolver.resolve(photo=photo, variant="original")  # type: ignore[arg-type]

        self.assertEqual(storage.opened_keys, [])


class PreviewRequiredPublicGalleryMediaTests(TestCase):
    """The break caught here would expose an original tile before preview publication."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="preview-gallery-reader")
        self.event = Event.objects.create(
            name="Preview gallery",
            slug="preview-gallery",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

    def make_preview_required_photo(self, *, photo_id: str) -> Photo:
        return Photo.objects.create(
            id=photo_id,
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{photo_id}",
            original_filename="race.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )

    def publish_preview(self, photo: Photo) -> PhotoDerivative:
        configuration = {"generate_preview": {"variant": "preview-small-v1"}}
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=2,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash="a" * 64,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash="a" * 64,
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
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        state = PhotoProcessingState.objects.get(
            photo=photo, processor_type=GENERATE_PREVIEW_PROCESSOR
        )
        state.status = PhotoProcessingState.Status.SUCCEEDED
        state.current_run = run
        state.current_job = job
        state.current_attempt = attempt
        state.accepted_attempt = attempt
        state.succeeded_at = timezone.now()
        state.save()
        return PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-small-v1",
            final_key=f"derivatives/previews/{photo.id}/preview-small-v1/{uuid4().hex}.jpg",
            byte_size=10,
            content_type="image/jpeg",
            width=10,
            height=10,
            oriented_source_width=10,
            oriented_source_height=10,
            sha256="a" * 64,
            accepted_attempt=attempt,
        )

    def test_resolver_reads_new_small_from_derivative_and_large_from_original(self) -> None:
        photo = self.make_preview_required_photo(photo_id="preview-photo")
        derivative = self.publish_preview(photo)
        preview_body = _ReadableBody([])
        original_body = _ReadableBody([])
        storage = _FinalObjectStorage(
            [
                OpenedObject(body=preview_body, size=9, content_type="image/jpeg"),
                OpenedObject(body=original_body, size=10, content_type="image/jpeg"),
            ]
        )

        resolver = PublicMediaResolver(storage)

        self.assertEqual(resolver.resolve(photo=photo, variant="preview-small").body, preview_body)
        self.assertEqual(resolver.resolve(photo=photo, variant="preview-large").body, original_body)
        self.assertEqual(storage.opened_keys, [derivative.final_key, photo.original_key])

    def test_resolver_never_falls_back_to_original_when_new_small_preview_is_missing(self) -> None:
        photo = self.make_preview_required_photo(photo_id="missing-preview")
        storage = _FinalObjectStorage([])

        with self.assertRaises(ObjectMissing):
            PublicMediaResolver(storage).resolve(photo=photo, variant="preview-small")

        self.assertEqual(storage.opened_keys, [])

    def test_resolver_requires_original_key_before_storage(self) -> None:
        storage = _FinalObjectStorage([])
        resolver = PublicMediaResolver(storage)
        photo = Photo(id="photo-42", original_key=None)

        with self.assertRaisesMessage(ValueError, "ineligible gallery media"):
            resolver.resolve(photo=photo, variant="preview-small")

        self.assertEqual(storage.opened_keys, [])

    def test_iterator_closes_after_eof(self) -> None:
        body = _ReadableBody([b"first", b""])
        iterator = CloseableMediaIterator(
            media=ResolvedPublicMedia(body, 5, "image/jpeg", "jpg"),
            event_slug="city-run",
            photo_id="photo-42",
        )

        self.assertEqual(list(iterator), [b"first"])
        self.assertEqual(body.close_calls, 1)

    def test_iterator_closes_and_sanitizes_read_failure(self) -> None:
        body = _ReadableBody([RuntimeError("S3 access token: secret")])
        iterator = CloseableMediaIterator(
            media=ResolvedPublicMedia(body, 5, "image/jpeg", "jpg"),
            event_slug="city-run",
            photo_id="photo-42",
        )

        with self.assertLogs("picflow.gallery", level="ERROR") as logs:
            with self.assertRaises(StopIteration):
                next(iterator)

        self.assertEqual(body.close_calls, 1)
        self.assertEqual(logs.output, ["ERROR:picflow.gallery:Public photo stream ended early"])
        self.assertEqual(logs.records[0].event_slug, "city-run")
        self.assertEqual(logs.records[0].photo_id, "photo-42")

    def test_iterator_close_before_first_next_closes_body(self) -> None:
        body = _ReadableBody([])
        iterator = CloseableMediaIterator(
            media=ResolvedPublicMedia(body, 0, "image/jpeg", "jpg"),
            event_slug="city-run",
            photo_id="photo-42",
        )

        iterator.close()

        self.assertEqual(body.close_calls, 1)
        with self.assertRaises(StopIteration):
            next(iterator)
        self.assertEqual(body.close_calls, 1)

    def test_iterator_close_after_partial_read_closes_body(self) -> None:
        body = _ReadableBody([b"first"])
        iterator = CloseableMediaIterator(
            media=ResolvedPublicMedia(body, 5, "image/jpeg", "jpg"),
            event_slug="city-run",
            photo_id="photo-42",
        )

        self.assertEqual(next(iterator), b"first")
        iterator.close()

        self.assertEqual(body.close_calls, 1)
        with self.assertRaises(StopIteration):
            next(iterator)
        self.assertEqual(body.close_calls, 1)
