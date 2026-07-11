from datetime import date

from django.test import TestCase

from picflow.models import Event, Photo


class EventModelTests(TestCase):
    def test_string_representation_uses_name(self) -> None:
        event = Event(name="Test Run", date_from=date.today(), date_to=date.today())

        self.assertEqual(str(event), "Test Run")


class PhotoModelTests(TestCase):
    def test_string_representation_uses_identifier(self) -> None:
        event = Event.objects.create(
            name="Test Run",
            date_from=date.today(),
            date_to=date.today(),
            location="Moscow",
        )
        photo = Photo(id="TEST-001", event=event, src="photos/test.jpg")

        self.assertEqual(str(photo), "TEST-001")
