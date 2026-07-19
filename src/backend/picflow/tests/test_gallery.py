from dataclasses import FrozenInstanceError
from unittest.mock import patch

from django.test import SimpleTestCase
from ingestion.storage import OpenedObject

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
    def test_resolver_maps_both_variants_to_original(self) -> None:
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
