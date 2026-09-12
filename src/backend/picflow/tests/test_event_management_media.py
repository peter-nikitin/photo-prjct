from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from ingestion.storage import ObjectMissing, PrivateUploadStorage, StorageUnavailable
from processing.models import PhotoProcessingState

from picflow.models import Event, Photo
from picflow.tests.event_management_helpers import (
    accepted_preview,
    event,
    private_photo,
    user_with_permissions,
)


@override_settings(ROOT_URLCONF="picflow.tests.event_management_urlconf", PHOTO_UPLOAD_ENABLED=True)
class EventManagementMediaTests(TestCase):
    def setUp(self):
        self.event = event()
        self.admin = user_with_permissions(
            "admin", staff=True, permissions=("picflow.view_event", "picflow.view_photo")
        )
        self.uploader = user_with_permissions("owner", permissions=("ingestion.upload_photos",))
        self.photo = private_photo(self.event, self.uploader, is_hidden=True)
        self.s3 = Mock()
        self.s3.head_object.return_value = {
            "ETag": '"etag"',
            "ContentLength": 4,
            "ContentType": "image/jpeg",
        }
        self.s3.generate_presigned_url.side_effect = lambda **kwargs: (
            f"https://storage.example/{kwargs['Params']['Key']}?signature=private"
        )
        self.storage = PrivateUploadStorage(client=self.s3)
        self.factory = patch(
            "picflow.event_management_media.PrivateUploadStorage", return_value=self.storage
        )

    def url(self, *, photo=None, event_id=None, variant="original"):
        return reverse(
            "event_management_media",
            kwargs={
                "event_id": event_id or self.event.pk,
                "photo_id": (photo or self.photo).pk,
                "variant": variant,
            },
        )

    def test_hidden_paid_original_can_be_inspected_without_upload_or_publication(self):
        Event.objects.filter(pk=self.event.pk).update(
            access_type=Event.AccessType.PAID, price_per_photo_kopecks=100
        )
        self.client.force_login(self.admin)
        with self.settings(PHOTO_UPLOAD_ENABLED=False), self.factory:
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url, f"https://storage.example/{self.photo.original_key}?signature=private"
        )
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_uploader_even_owner_and_partial_admin_never_reach_storage(self):
        for user in (
            self.uploader,
            user_with_permissions("staff", staff=True, permissions=("picflow.view_photo",)),
        ):
            with self.subTest(user=user.pk):
                self.client.force_login(user)
                with self.factory as factory:
                    for variant in ("original", "thumbnail"):
                        response = self.client.get(self.url(variant=variant))
                        self.assertEqual(response.status_code, 403)
                        self.assertIn("no-store", response["Cache-Control"])
                    factory.assert_not_called()

    def test_cross_event_missing_photo_and_unknown_variant_fail_before_signing(self):
        self.client.force_login(self.admin)
        other = event("Other")
        with self.factory as factory:
            responses = [
                self.client.get(self.url(event_id=other.pk)),
                self.client.get(self.url(variant="secret")),
                self.client.get(
                    reverse("event_management_media", args=[self.event.pk, "missing", "original"])
                ),
                self.client.post(self.url()),
            ]
            self.assertEqual([r.status_code for r in responses], [404, 404, 404, 405])
            factory.assert_not_called()

    def test_thumbnail_requires_current_accepted_preview_and_never_falls_back_to_original(self):
        self.client.force_login(self.admin)
        with self.factory as factory:
            self.assertEqual(self.client.get(self.url(variant="thumbnail")).status_code, 404)
            factory.assert_not_called()
        derivative = accepted_preview(self.photo)
        with self.factory:
            response = self.client.get(self.url(variant="thumbnail"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url, f"https://storage.example/{derivative.final_key}?signature=private"
        )
        PhotoProcessingState.objects.filter(photo=self.photo).update(
            status=PhotoProcessingState.Status.FAILED
        )
        with self.factory as factory:
            self.assertEqual(self.client.get(self.url(variant="thumbnail")).status_code, 404)
            factory.assert_not_called()

    def test_missing_objects_and_storage_errors_are_sanitized_private_responses(self):
        self.client.force_login(self.admin)
        for error, status in (
            (ObjectMissing(), 404),
            (StorageUnavailable(), 503),
            (ValueError("secret storage config"), 503),
        ):
            with (
                self.subTest(error=type(error)),
                patch("picflow.event_management_media.PrivateUploadStorage", side_effect=error),
            ):
                response = self.client.get(self.url())
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.content, b"")
                self.assertIn("no-store", response["Cache-Control"])
        Photo.objects.filter(pk=self.photo.pk).update(original_key="invalid-secret-key")
        with self.factory:
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"invalid-secret-key", response.content)

    def test_legacy_file_is_streamed_privately_with_no_public_redirect(self):
        with TemporaryDirectory() as directory, self.settings(MEDIA_ROOT=directory):
            photo = Photo(id="legacy", event=self.event, is_hidden=True)
            photo.src.save("private.jpg", ContentFile(b"jpeg-original"), save=True)
            self.client.force_login(self.uploader)
            denied = self.client.get(self.url(photo=photo))
            self.assertEqual(denied.status_code, 403)
            self.client.force_login(self.admin)
            with self.factory as factory:
                response = self.client.get(self.url(photo=photo))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(b"".join(response.streaming_content), b"jpeg-original")
                self.assertNotIn("Location", response)
                self.assertEqual(response["Content-Type"], "image/jpeg")
                self.assertIn("no-store", response["Cache-Control"])
                self.assertEqual(
                    self.client.get(self.url(photo=photo, variant="thumbnail")).status_code, 404
                )
                factory.assert_not_called()
            photo.src.storage.delete(photo.src.name)
            self.assertEqual(self.client.get(self.url(photo=photo)).status_code, 404)

    def test_anonymous_media_never_reaches_storage(self):
        with self.factory as factory:
            response = self.client.get(self.url())
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse("photographer_login")))
            factory.assert_not_called()
