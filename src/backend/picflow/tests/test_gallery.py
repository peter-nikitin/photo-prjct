from dataclasses import FrozenInstanceError
from unittest.mock import patch

from django.test import SimpleTestCase

from picflow.gallery import GalleryMedia, GalleryPhoto, GalleryPhotoFactory
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
        with self.assertRaises(FrozenInstanceError):
            gallery_photo.alt = "changed"
        with self.assertRaises(FrozenInstanceError):
            small.url = "/changed/"

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
