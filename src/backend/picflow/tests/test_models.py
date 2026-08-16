from datetime import date, timedelta
from uuid import uuid4

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone

from picflow.models import Event, EventFolder, Photo


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

    def test_new_event_defaults_to_adaface_v5(self) -> None:
        event = self.make_event(name="AdaFace default", slug="adaface-default")

        self.assertEqual(event.face_search_generation, Event.FaceSearchGeneration.ADAFACE_V5)

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

    def test_draft_event_accepts_no_timezone(self) -> None:
        self.make_event().full_clean()

    def test_published_event_rejects_no_timezone(self) -> None:
        event = Event(
            name="Published without timezone",
            slug="published-without-timezone",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )

        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_published_event_rejects_invalid_timezone_identifier(self) -> None:
        event = Event(
            name="Published invalid timezone",
            slug="published-invalid-timezone",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            timezone_name="Europe/Not-A-Timezone",
        )

        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_published_event_rejects_pathlike_timezone_identifier(self) -> None:
        event = Event(
            name="Published path timezone",
            slug="published-path-timezone",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            timezone_name="../UTC",
        )

        with self.assertRaises(ValidationError):
            event.full_clean()

    @override_settings(TIME_ZONE="Pacific/Auckland")
    def test_published_event_accepts_iana_timezone_independent_of_server_timezone(self) -> None:
        event = Event(
            name="Published Moscow timezone",
            slug="published-moscow-timezone",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            timezone_name="Europe/Moscow",
        )

        event.full_clean()

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

    def test_explicit_index_names_fit_postgresql_limit(self) -> None:
        index_names = [
            index.name
            for model in apps.get_app_config("picflow").get_models()
            for index in model._meta.indexes
            if index.name
        ]

        self.assertTrue(index_names)
        self.assertTrue(
            all(len(name) <= 30 for name in index_names),
            f"Picflow index names must be at most 30 characters: {index_names}",
        )

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

    def test_processing_policy_defaults_to_the_legacy_pair(self) -> None:
        photo = self.private_photo()

        self.assertEqual(photo.processing_generation, "legacy_original_v1")
        self.assertEqual(photo.gallery_media_policy, "legacy_original_allowed")

    def test_processing_policy_rejects_an_invalid_generation_policy_pair(self) -> None:
        photo = self.private_photo()
        photo.processing_generation = "preview_first_v1"

        with self.assertRaises(ValidationError):
            photo.full_clean()

        photo.processing_generation = "legacy_original_v1"
        photo.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Photo.objects.filter(pk=photo.pk).update(
                processing_generation="preview_first_v1",
            )

    def test_capture_time_projection_is_nullable_and_requires_a_complete_pair(self) -> None:
        """Catch a partial read projection becoming representable in PostgreSQL."""
        capture_time = Photo._meta.get_field("capture_time")
        source_attempt = Photo._meta.get_field("capture_time_source_attempt")

        self.assertTrue(capture_time.null)
        self.assertTrue(source_attempt.null)
        self.assertFalse(capture_time.editable)
        self.assertFalse(source_attempt.editable)
        self.assertEqual(source_attempt.remote_field.on_delete.__name__, "PROTECT")
        self.assertIn(
            "picflow_photo_capture_time_pair_chk",
            {constraint.name for constraint in Photo._meta.constraints},
        )
        self.assertIn(
            ("event", "capture_time"),
            {tuple(index.fields) for index in Photo._meta.indexes},
        )

        photo = self.private_photo()
        photo.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Photo.objects.filter(pk=photo.pk).update(capture_time=timezone.now())

    def test_folder_is_optional_and_protects_its_folder_from_deletion(self) -> None:
        folder = EventFolder.objects.create(event=self.event, name="Finish")
        field = Photo._meta.get_field("folder")

        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertEqual(field.remote_field.related_name, "photos")
        self.assertEqual(field.remote_field.on_delete.__name__, "PROTECT")

        photo = self.private_photo(folder=folder)
        photo.full_clean()
        photo.save()
        with self.assertRaises(ProtectedError):
            folder.delete()


class EventFolderModelTests(TestCase):
    def setUp(self) -> None:
        self.event = Event.objects.create(
            name="Folder Run",
            slug="folder-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

    def test_string_representation_uses_name(self) -> None:
        folder = EventFolder(event=self.event, name="Finish")

        self.assertEqual(str(folder), "Finish")

    def test_name_is_trimmed_before_validation_and_persistence(self) -> None:
        folder = EventFolder(event=self.event, name="  Финиш  ")

        folder.full_clean()
        folder.save()

        ordinary_write = EventFolder.objects.create(event=self.event, name="  Старт  ")

        self.assertEqual(folder.name, "Финиш")
        self.assertEqual(EventFolder.objects.get(pk=folder.pk).name, "Финиш")
        self.assertEqual(ordinary_write.name, "Старт")

    def test_blank_after_trimming_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            EventFolder(event=self.event, name="   ").full_clean()

    def test_case_insensitive_name_is_unique_within_an_event(self) -> None:
        EventFolder.objects.create(event=self.event, name="Старт")

        with self.assertRaises(IntegrityError), transaction.atomic():
            EventFolder.objects.create(event=self.event, name="старт")

    def test_folders_are_ordered_by_case_insensitive_name(self) -> None:
        EventFolder.objects.create(event=self.event, name="zebra")
        EventFolder.objects.create(event=self.event, name="Apple")

        self.assertEqual(
            list(self.event.folders.values_list("name", flat=True)), ["Apple", "zebra"]
        )
