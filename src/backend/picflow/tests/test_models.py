from datetime import date, timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

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
    def setUp(self) -> None:
        self.event = Event.objects.create(
            name="Test Run",
            slug="test-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )
        self.photographer = get_user_model().objects.create_user(username="photographer")

    def test_string_representation_uses_identifier(self) -> None:
        photo = Photo(id="TEST-001", event=self.event, src="photos/test.jpg")
        self.assertEqual(str(photo), "TEST-001")

    def private_photo(self, **overrides):
        values = {
            "id": uuid4().hex,
            "event": self.event,
            "src": "",
            "uploaded_by": self.photographer,
            "original_key": f"originals/{uuid4().hex}",
            "original_filename": "race.jpg",
            "original_size": 30 * 1024 * 1024,
            "original_content_type": "image/jpeg",
            "uploaded_at": timezone.now(),
        }
        values.update(overrides)
        return Photo(**values)

    def test_legacy_photo_shape_is_valid(self) -> None:
        Photo(id="LEGACY", event=self.event, src="photos/legacy.jpg").full_clean()

    def test_complete_private_photo_shape_is_valid(self) -> None:
        self.private_photo().full_clean()

    def test_mixed_photo_shape_is_invalid(self) -> None:
        with self.assertRaises(ValidationError):
            self.private_photo(src="photos/public.jpg").full_clean()

    def test_incomplete_private_photo_shape_is_invalid(self) -> None:
        with self.assertRaises(ValidationError):
            self.private_photo(original_size=None).full_clean()

    def test_original_key_is_unique(self) -> None:
        key = "originals/shared"
        first = self.private_photo(original_key=key)
        first.full_clean()
        first.save()
        with self.assertRaises(ValidationError):
            self.private_photo(original_key=key).full_clean()
