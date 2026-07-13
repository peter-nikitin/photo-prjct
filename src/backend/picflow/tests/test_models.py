from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from picflow.models import Event, Photo


class EventModelTests(TestCase):
    def setUp(self) -> None:
        Event.objects.update(publication_status=Event.PublicationStatus.DRAFT)

    def make_event(self, **overrides):
        values = {
            "name": "Test Run",
            "slug": "test-run",
            "start_date": date.today(),
            "end_date": date.today(),
            "city": "Moscow",
        }
        values.update(overrides)
        return Event.objects.create(**values)

    def test_string_representation_uses_name(self) -> None:
        self.assertEqual(str(self.make_event()), "Test Run")

    def test_slug_is_stable_when_name_changes(self) -> None:
        event = self.make_event()
        event.name = "Renamed Run"
        event.save()
        event.refresh_from_db()
        self.assertEqual(event.slug, "test-run")

    def test_published_queryset_excludes_drafts(self) -> None:
        published = self.make_event(publication_status=Event.PublicationStatus.PUBLISHED)
        self.make_event(name="Draft", slug="draft")
        self.assertEqual(list(Event.objects.published()), [published])

    def test_invalid_date_range_fails_validation_and_database_constraint(self) -> None:
        event = Event(
            name="Invalid",
            slug="invalid",
            start_date=date.today(),
            end_date=date.today() - timedelta(days=1),
            city="Moscow",
        )
        with self.assertRaises(ValidationError):
            event.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Event.objects.create(
                name="Invalid DB",
                slug="invalid-db",
                start_date=date.today(),
                end_date=date.today() - timedelta(days=1),
                city="Moscow",
            )

    def test_cover_key_is_immutable_and_uuid_based(self) -> None:
        event = self.make_event()
        key = Event._meta.get_field("cover").upload_to(event, "Race Banner.JPG")
        self.assertRegex(key, r"^event-covers/[0-9a-f-]{36}\.jpg$")


class PhotoModelTests(TestCase):
    def test_string_representation_uses_identifier(self) -> None:
        event = Event.objects.create(
            name="Test Run",
            slug="test-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        photo = Photo(id="TEST-001", event=event, src="photos/test.jpg")
        self.assertEqual(str(photo), "TEST-001")
