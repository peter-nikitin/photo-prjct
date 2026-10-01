from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from processing.models import (
    BIB_RECOGNITION_PROCESSOR,
    BibReading,
    BibReadingChange,
    PhotoProcessingState,
)

from picflow.models import Photo
from picflow.tests.event_management_helpers import (
    accepted_attempt,
    event,
    private_photo,
    user_with_permissions,
)


@override_settings(
    ROOT_URLCONF="config.urls",
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class EventManagementBibViewTests(TestCase):
    def setUp(self) -> None:
        self.event = event("Bib workspace")
        self.other_event = event("Other bib workspace")
        self.admin = user_with_permissions(
            "bib-view-admin",
            staff=True,
            permissions=("picflow.view_event", "picflow.view_photo", "picflow.change_photo"),
        )
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.admin)
        self.photo = self.bib_photo(self.event)
        self.url = reverse("event_management_bib_save", args=[self.event.pk, self.photo.pk])
        self.client.get(reverse("event_management", args=[self.event.pk]))
        self.csrf = self.client.cookies["csrftoken"].value

    def bib_photo(self, event_obj):
        photo = private_photo(
            event_obj,
            self.admin,
            bib_processing_policy=Photo.BibProcessingPolicy.ORIGINAL_V1,
        )
        accepted_attempt(photo, processor_type=BIB_RECOGNITION_PROCESSOR)
        return photo

    def reading(self, photo, number: str) -> BibReading:
        state = PhotoProcessingState.objects.get(
            photo=photo, processor_type=BIB_RECOGNITION_PROCESSOR
        )
        return BibReading.objects.create(
            photo=photo, source_attempt=state.accepted_attempt, number=number
        )

    def post(self, data, *, url=None, csrf=True):
        return self.client.post(
            url or self.url,
            data,
            HTTP_X_CSRFTOKEN=self.csrf if csrf else "",
        )

    def test_cards_show_zero_one_and_many_current_numbers_and_editable_state(self) -> None:
        one = self.bib_photo(self.event)
        many = self.bib_photo(self.event)
        self.reading(one, "0012")
        self.reading(many, "34")
        self.reading(many, "56")
        disabled = private_photo(self.event, self.admin)
        failed = self.bib_photo(self.event)
        PhotoProcessingState.objects.filter(
            photo=failed, processor_type=BIB_RECOGNITION_PROCESSOR
        ).update(status=PhotoProcessingState.Status.FAILED)

        response = self.client.get(reverse("event_management", args=[self.event.pk]))

        self.assertEqual(response.status_code, 200)
        cards = {card.id: card for card in response.context_data["photo_page"]}
        self.assertEqual([row.number for row in cards[self.photo.pk].bib_readings], [])
        self.assertEqual([row.number for row in cards[one.pk].bib_readings], ["0012"])
        self.assertEqual([row.number for row in cards[many.pk].bib_readings], ["34", "56"])
        self.assertTrue(cards[self.photo.pk].bib_editable)
        self.assertFalse(cards[disabled.pk].bib_editable)
        self.assertFalse(cards[failed.pk].bib_editable)
        self.assertContains(response, "Номера не найдены")
        self.assertContains(response, "0012")
        self.assertContains(response, "data-photo-bib-editor hidden")
        self.assertContains(response, f'action="{self.url}"')
        self.assertNotContains(response, self.photo.original_key)

    def test_read_only_inspector_sees_numbers_but_cannot_edit(self) -> None:
        self.reading(self.photo, "007")
        inspector = user_with_permissions(
            "bib-view-inspector",
            staff=True,
            permissions=("picflow.view_event", "picflow.view_photo"),
        )
        self.client.force_login(inspector)

        page = self.client.get(reverse("event_management", args=[self.event.pk]))
        denied = self.post({"added_number": ["88"]})

        self.assertContains(page, "007")
        self.assertNotContains(page, "data-photo-bib-editor")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(list(BibReading.objects.values_list("number", flat=True)), ["007"])

    def test_save_returns_complete_current_rows_and_never_cache(self) -> None:
        first = self.reading(self.photo, "12")
        second = self.reading(self.photo, "34")

        response = self.post(
            {
                "existing_id": [str(first.pk), str(second.pk)],
                "existing_number": ["56", "34"],
                "added_number": [" 007 ", ""],
            }
        )

        self.assertEqual(response.status_code, 200)
        numbers = response.json()["numbers"]
        self.assertEqual([row["number"] for row in numbers], ["007", "34", "56"])
        self.assertEqual(
            list(
                BibReading.objects.filter(photo=self.photo)
                .order_by("number")
                .values_list("number", flat=True)
            ),
            ["007", "34", "56"],
        )
        self.assertEqual(BibReadingChange.objects.filter(photo=self.photo).count(), 2)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn("evidence", response.json())

    def test_bad_values_and_malformed_paired_arrays_return_422_without_changes(self) -> None:
        first = self.reading(self.photo, "12")
        cases = (
            {"existing_id": [str(first.pk)], "existing_number": ["bad"]},
            {"existing_id": [str(first.pk)], "existing_number": []},
            {"existing_id": [str(first.pk), str(first.pk)], "existing_number": ["12", "34"]},
            {"existing_id": ["not-an-id"], "existing_number": ["34"]},
            {"added_number": ["34"]},
        )
        for data in cases:
            with self.subTest(data=data):
                response = self.post(data)
                self.assertEqual(response.status_code, 422)
                self.assertTrue(response.json()["errors"])
                self.assertEqual(BibReading.objects.get(pk=first.pk).number, "12")
                self.assertFalse(BibReadingChange.objects.exists())

    def test_oversized_ascii_reading_id_returns_422_before_service_call(self) -> None:
        with patch("picflow.event_management_views.save_event_photo_bibs") as save:
            response = self.post({"existing_id": ["1" * 4301], "existing_number": ["12"]})

        self.assertEqual(response.status_code, 422)
        self.assertIn("existing", response.json()["errors"])
        save.assert_not_called()
        self.assertFalse(BibReading.objects.exists())
        self.assertFalse(BibReadingChange.objects.exists())

    def test_post_requires_csrf_and_does_not_allow_get(self) -> None:
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.post({"added_number": ["12"]}, csrf=False).status_code, 403)
        self.assertFalse(BibReading.objects.exists())

    def test_foreign_photo_and_uploader_only_user_cannot_read_or_write(self) -> None:
        foreign = self.bib_photo(self.other_event)
        foreign_url = reverse("event_management_bib_save", args=[self.event.pk, foreign.pk])
        self.assertEqual(self.post({"added_number": ["12"]}, url=foreign_url).status_code, 404)
        uploader = user_with_permissions(
            "bib-only-uploader", permissions=("ingestion.upload_photos",)
        )
        self.client.force_login(uploader)
        self.assertEqual(
            self.client.get(reverse("event_management_results", args=[self.event.pk])).status_code,
            403,
        )
        self.assertEqual(self.post({"added_number": ["12"]}).status_code, 403)
        self.assertFalse(BibReading.objects.exists())
