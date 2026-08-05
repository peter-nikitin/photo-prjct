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
    GalleryFaceCrop,
    GalleryMedia,
    GalleryPhoto,
    GalleryPhotoFactory,
    PublicMediaResolver,
    ResolvedPublicMedia,
    gallery_face_crop,
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
            download_url="/events/city-run/photos/photo-42/download/",
            faces=(
                GalleryFaceCrop(
                    detection_id="face-42",
                    face_number=1,
                    left_percent=10,
                    top_percent=20,
                    size_percent=30,
                    search_url="/events/city-run/photos/photo-42/similar-search/face-42/",
                ),
            ),
            alt="Фото photo-42 с события City Run",
        )

        self.assertEqual(gallery_photo.photo_id, "photo-42")
        self.assertEqual(gallery_photo.preview_media_small, small)
        self.assertEqual(gallery_photo.preview_media_large, large)
        self.assertEqual(gallery_photo.download_url, "/events/city-run/photos/photo-42/download/")
        self.assertEqual(
            gallery_photo.faces,
            (
                GalleryFaceCrop(
                    detection_id="face-42",
                    face_number=1,
                    left_percent=10,
                    top_percent=20,
                    size_percent=30,
                    search_url="/events/city-run/photos/photo-42/similar-search/face-42/",
                ),
            ),
        )
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
            "/events/city-run/photos/photo-42/download/",
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
        self.assertEqual(gallery_photo.download_url, "/events/city-run/photos/photo-42/download/")
        self.assertEqual(gallery_photo.faces, ())
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
                (
                    ("photo_download",),
                    {"kwargs": {"slug": "city-run", "photo_id": "photo-42"}},
                ),
            ],
        )
        boto3_client.assert_not_called()

    @patch("boto3.client")
    def test_factory_uses_scoped_media_and_download_url_builders_without_storage(
        self, boto3_client
    ) -> None:
        event = Event(name="City Run", slug="city-run")
        photo = Photo(id="photo-42", event=event)
        media_calls: list[tuple[str, str]] = []
        download_calls: list[str] = []

        def result_media_url(photo: Photo, variant: str) -> str:
            media_calls.append((photo.id, variant))
            return f"/events/city-run/selfie-search/bearer-token/photos/{photo.id}/media/{variant}/"

        def result_download_url(photo: Photo) -> str:
            download_calls.append(photo.id)
            return f"/events/city-run/selfie-search/bearer-token/photos/{photo.id}/download/"

        gallery_photo = GalleryPhotoFactory.from_photo(
            photo=photo,
            event_slug=event.slug,
            media_url_builder=result_media_url,
            download_url_builder=result_download_url,
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
            gallery_photo.download_url,
            "/events/city-run/selfie-search/bearer-token/photos/photo-42/download/",
        )
        self.assertEqual(gallery_photo.faces, ())
        self.assertEqual(
            media_calls,
            [("photo-42", "preview-small"), ("photo-42", "preview-large")],
        )
        self.assertEqual(download_calls, ["photo-42"])
        boto3_client.assert_not_called()

    @patch("boto3.client")
    def test_factory_preserves_prepared_face_urls_without_storage(self, boto3_client) -> None:
        event = Event(name="City Run", slug="city-run")
        photo = Photo(id="photo-42", event=event)
        face = GalleryFaceCrop(
            detection_id="face-42",
            face_number=1,
            left_percent=10,
            top_percent=20,
            size_percent=30,
            search_url="/events/city-run/photos/photo-42/similar-search/face-42/",
        )

        gallery_photo = GalleryPhotoFactory.from_photo(
            photo=photo,
            event_slug=event.slug,
            faces=(face,),
        )

        self.assertEqual(gallery_photo.faces, (face,))
        boto3_client.assert_not_called()


class GalleryFaceCropTests(SimpleTestCase):
    """The production break caught here is exposing an invalid or distorted face crop."""

    def test_returns_a_padded_normalized_square_for_a_centered_face(self) -> None:
        geometry = {
            "coordinate_space": "preview-small-v1",
            "pixel_width": 100,
            "pixel_height": 100,
            "bbox": [40, 40, 20, 20],
        }

        crop = gallery_face_crop(detection_id="face-1", face_index=0, geometry=geometry)

        self.assertEqual(
            crop,
            GalleryFaceCrop(
                detection_id="face-1",
                face_number=1,
                left_percent=38.0,
                top_percent=38.0,
                size_percent=24.0,
            ),
        )
        self.assertEqual(
            geometry,
            {
                "coordinate_space": "preview-small-v1",
                "pixel_width": 100,
                "pixel_height": 100,
                "bbox": [40, 40, 20, 20],
            },
        )

    def test_clips_a_padded_square_to_the_preview_edge_without_distortion(self) -> None:
        crop = gallery_face_crop(
            detection_id="face-1",
            face_index=2,
            geometry={
                "coordinate_space": "preview-small-v1",
                "pixel_width": 100,
                "pixel_height": 60,
                "bbox": [85, 45, 15, 15],
            },
        )

        self.assertEqual(
            crop,
            GalleryFaceCrop(
                detection_id="face-1",
                face_number=3,
                left_percent=82.0,
                top_percent=70.0,
                size_percent=18.0,
            ),
        )

    def test_pads_a_rectangular_face_from_its_largest_dimension(self) -> None:
        crop = gallery_face_crop(
            detection_id="face-1",
            face_index=0,
            geometry={
                "coordinate_space": "preview-small-v1",
                "pixel_width": 200,
                "pixel_height": 100,
                "bbox": [80, 20, 20, 40],
            },
        )
        self.assertEqual(
            crop,
            GalleryFaceCrop(
                detection_id="face-1",
                face_number=1,
                left_percent=33.0,
                top_percent=16.0,
                size_percent=24.0,
            ),
        )

    def test_clamps_an_oversized_padded_square_to_the_largest_containing_crop(self) -> None:
        crop = gallery_face_crop(
            detection_id="face-1",
            face_index=0,
            geometry={
                "coordinate_space": "preview-small-v1",
                "pixel_width": 100,
                "pixel_height": 60,
                "bbox": [10, 0, 50, 60],
            },
        )

        self.assertEqual(
            crop,
            GalleryFaceCrop(
                detection_id="face-1",
                face_number=1,
                left_percent=5.0,
                top_percent=0.0,
                size_percent=60.0,
            ),
        )

    def test_rejects_malformed_nonfinite_wrong_space_and_nonpositive_geometry(self) -> None:
        valid = {
            "coordinate_space": "preview-small-v1",
            "pixel_width": 100,
            "pixel_height": 100,
            "bbox": [40, 40, 20, 20],
        }
        invalid_geometries: tuple[object, ...] = (
            {},
            valid | {"bbox": [40, 40, 20]},
            valid | {"bbox": [40, 40, float("nan"), 20]},
            valid | {"coordinate_space": "original-v1"},
            valid | {"pixel_width": 0},
            valid | {"pixel_height": -1},
            valid | {"bbox": [-1, 40, 20, 20]},
            valid | {"bbox": [40, 40, 61, 20]},
        )

        for geometry in invalid_geometries:
            with self.subTest(geometry=geometry):
                self.assertIsNone(
                    gallery_face_crop(detection_id="face-1", face_index=0, geometry=geometry)
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

    def sign_final(self, *, key: str, attachment_filename: str | None = None) -> str:  # noqa: ARG002
        raise AssertionError("inline resolver tests must not sign media")


class _SignedFinalObjectStorage:
    def __init__(self) -> None:
        self.signed_keys: list[str] = []

    def sign_final(self, *, key: str, attachment_filename: str | None = None) -> str:  # noqa: ARG002
        self.signed_keys.append(key)
        return f"https://storage.example.test/{key}?signature=secret"


class _DownloadFinalObjectStorage:
    def __init__(self) -> None:
        self.signed_requests: list[tuple[str, str | None]] = []

    def open_final(self, *, key: str) -> OpenedObject:  # noqa: ARG002
        raise AssertionError("download resolver tests must not open media")

    def sign_final(self, *, key: str, attachment_filename: str | None = None) -> str:
        self.signed_requests.append((key, attachment_filename))
        return f"https://storage.example.test/{key}?signature=secret"


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

    def test_download_resolver_signs_the_original_with_a_jpeg_attachment_name(self) -> None:
        storage = _DownloadFinalObjectStorage()
        photo = Photo(
            id="photo-42",
            original_key="originals/0123456789abcdef0123456789abcdef",
            original_content_type="image/jpeg",
        )

        url = PublicMediaResolver(storage).resolve_download(photo=photo)

        self.assertEqual(
            url,
            "https://storage.example.test/originals/0123456789abcdef0123456789abcdef?signature=secret",
        )
        self.assertEqual(
            storage.signed_requests,
            [(photo.original_key, "findme-photo-photo-42.jpg")],
        )

    def test_download_resolver_derives_a_png_attachment_name(self) -> None:
        storage = _DownloadFinalObjectStorage()
        photo = Photo(
            id="photo-42",
            original_key="originals/0123456789abcdef0123456789abcdef",
            original_content_type="image/png",
        )

        PublicMediaResolver(storage).resolve_download(photo=photo)

        self.assertEqual(
            storage.signed_requests,
            [(photo.original_key, "findme-photo-photo-42.png")],
        )

    def test_download_resolver_rejects_a_missing_original_key_before_signing(self) -> None:
        storage = _DownloadFinalObjectStorage()
        photo = Photo(id="photo-42", original_key=None, original_content_type="image/jpeg")

        with self.assertRaisesMessage(ValueError, "ineligible original download"):
            PublicMediaResolver(storage).resolve_download(photo=photo)

        self.assertEqual(storage.signed_requests, [])

    def test_download_resolver_rejects_an_unsupported_original_type_before_signing(self) -> None:
        storage = _DownloadFinalObjectStorage()
        photo = Photo(
            id="photo-42",
            original_key="originals/0123456789abcdef0123456789abcdef",
            original_content_type="image/webp",
        )

        with self.assertRaisesMessage(ValueError, "ineligible original download"):
            PublicMediaResolver(storage).resolve_download(photo=photo)

        self.assertEqual(storage.signed_requests, [])


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

    def test_signed_resolver_selects_preview_for_tile_and_original_for_lightbox(self) -> None:
        photo = self.make_preview_required_photo(photo_id="preview-photo")
        derivative = self.publish_preview(photo)
        storage = _SignedFinalObjectStorage()
        resolver = PublicMediaResolver(storage)  # type: ignore[arg-type]

        self.assertEqual(
            resolver.resolve_signed(photo=photo, variant="preview-small"),
            f"https://storage.example.test/{derivative.final_key}?signature=secret",
        )
        self.assertEqual(
            resolver.resolve_signed(photo=photo, variant="preview-large"),
            f"https://storage.example.test/{photo.original_key}?signature=secret",
        )
        self.assertEqual(storage.signed_keys, [derivative.final_key, photo.original_key])

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
