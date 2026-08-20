from datetime import date, timedelta
from uuid import uuid4

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone
from feature_flags.models import FeatureFlag

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

    def test_free_event_has_no_photo_price(self) -> None:
        event = Event(access_type=Event.AccessType.FREE)

        self.assertIsNone(event.price_per_photo_kopecks)

    def test_photo_price_validation_enforces_the_access_type_pair(self) -> None:
        base_values = {
            "name": "Price validation",
            "slug": "price-validation",
            "start_date": date.today(),
            "end_date": date.today(),
            "city": "Moscow",
        }

        for access_type, price in (
            (Event.AccessType.FREE, 1),
            (Event.AccessType.PAID, None),
            (Event.AccessType.PAID, 0),
            (Event.AccessType.PAID, -1),
        ):
            with self.subTest(access_type=access_type, price=price):
                with self.assertRaises(ValidationError):
                    Event(
                        **base_values,
                        access_type=access_type,
                        price_per_photo_kopecks=price,
                    ).full_clean()

        for access_type, price in (
            (Event.AccessType.FREE, None),
            (Event.AccessType.PAID, 1),
        ):
            with self.subTest(access_type=access_type, price=price):
                Event(
                    **base_values,
                    access_type=access_type,
                    price_per_photo_kopecks=price,
                ).full_clean()

    def test_slug_is_stable_when_name_changes(self) -> None:
        event = self.make_event()
        event.name = "Renamed Run"
        event.save()
        event.refresh_from_db()
        self.assertEqual(event.slug, "test-run")

    def test_published_queryset_excludes_nonpublished_events(self) -> None:
        published = self.make_event(publication_status=Event.PublicationStatus.PUBLISHED)
        self.make_event(
            name="Draft",
            slug="draft",
            publication_status=Event.PublicationStatus.DRAFT,
        )
        self.make_event(name="Unavailable", slug="unavailable")
        self.assertEqual(list(Event.objects.published()), [published])

    def test_publication_statuses_have_russian_labels_and_safe_defaults(self) -> None:
        field = Event._meta.get_field("publication_status")

        self.assertEqual(
            Event.PublicationStatus.choices,
            [
                ("unavailable", "Недоступно"),
                ("draft", "Черновик"),
                ("published", "Опубликовано"),
            ],
        )
        self.assertEqual(field.default, Event.PublicationStatus.UNAVAILABLE)
        self.assertEqual(field.db_default, Event.PublicationStatus.UNAVAILABLE)
        self.assertEqual(self.make_event().publication_status, Event.PublicationStatus.UNAVAILABLE)

    def test_site_visible_to_enforces_the_exact_publication_matrix(self) -> None:
        unavailable = self.make_event(name="Unavailable", slug="unavailable")
        draft = self.make_event(
            name="Draft",
            slug="draft",
            publication_status=Event.PublicationStatus.DRAFT,
        )
        published = self.make_event(
            name="Published",
            slug="published",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        ordinary_user = get_user_model().objects.create_user(username="ordinary")
        active_staff = get_user_model().objects.create_user(username="staff", is_staff=True)
        inactive_staff = get_user_model().objects.create_user(
            username="inactive-staff", is_staff=True, is_active=False
        )

        cases = (
            (AnonymousUser(), {published.pk}),
            (ordinary_user, {published.pk}),
            (active_staff, {draft.pk, published.pk}),
            (inactive_staff, {published.pk}),
        )
        fixtures = Event.objects.filter(pk__in=(unavailable.pk, draft.pk, published.pk))
        for user, expected_ids in cases:
            with self.subTest(user=getattr(user, "username", "anonymous")):
                self.assertEqual(
                    set(fixtures.site_visible_to(user).values_list("pk", flat=True)),
                    expected_ids,
                )
                self.assertNotIn(unavailable.pk, expected_ids)

    def test_unavailable_event_accepts_no_timezone(self) -> None:
        self.make_event().full_clean()

    def test_draft_event_rejects_no_timezone(self) -> None:
        event = Event(
            name="Draft without timezone",
            slug="draft-without-timezone",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            publication_status=Event.PublicationStatus.DRAFT,
        )

        with self.assertRaises(ValidationError):
            event.full_clean()

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

    def test_publication_state_migration_is_schema_only(self) -> None:
        migration = MigrationLoader(connection).get_migration(
            "picflow", "0011_event_publication_states"
        )

        self.assertEqual(
            [operation.__class__.__name__ for operation in migration.operations],
            ["AlterField"],
        )


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

    def test_processing_policy_fields_accept_exactly_three_documented_pairs(self) -> None:
        generation_field = Photo._meta.get_field("processing_generation")
        policy_field = Photo._meta.get_field("gallery_media_policy")
        pairs = (
            ("legacy_original_v1", "legacy_original_allowed"),
            ("preview_first_v1", "preview_required"),
            ("preview_first_watermarked_v1", "watermarked_preview_required"),
        )

        self.assertEqual(generation_field.max_length, 32)
        self.assertEqual(policy_field.max_length, 32)
        self.assertEqual(
            [value for value, _label in generation_field.choices],
            ["legacy_original_v1", "preview_first_v1", "preview_first_watermarked_v1"],
        )
        self.assertEqual(
            [value for value, _label in policy_field.choices],
            ["legacy_original_allowed", "preview_required", "watermarked_preview_required"],
        )

        for processing_generation, gallery_media_policy in pairs:
            with self.subTest(
                processing_generation=processing_generation,
                gallery_media_policy=gallery_media_policy,
            ):
                photo = self.private_photo(
                    processing_generation=processing_generation,
                    gallery_media_policy=gallery_media_policy,
                )
                photo.full_clean()
                photo.save()

    def test_paid_watermarked_policy_migration_is_schema_only(self) -> None:
        loader = MigrationLoader(connection)

        self.assertEqual(
            loader.graph.leaf_nodes("picflow"),
            [("picflow", "0013_event_photo_price")],
        )
        migration = loader.get_migration("picflow", "0012_paid_watermarked_photo_policy")
        self.assertEqual(
            [operation.__class__.__name__ for operation in migration.operations],
            ["AlterField", "AlterField", "RemoveConstraint", "AddConstraint"],
        )

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=True)
    def test_free_photo_policy_stays_preview_first_when_paid_gate_is_enabled(self) -> None:
        from picflow.photo_policy import policy_for_new_photo

        FeatureFlag.objects.create(
            key="paid-watermarked-previews",
            description="Paid watermarked previews",
            state=FeatureFlag.State.ON,
        )

        self.assertEqual(
            policy_for_new_photo(self.event, self.photographer),
            ("preview_first_v1", "preview_required"),
        )

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=True)
    def test_paid_photo_policy_stays_preview_first_when_gate_is_missing_or_off(self) -> None:
        from picflow.photo_policy import policy_for_new_photo

        self.event.access_type = Event.AccessType.PAID
        self.event.price_per_photo_kopecks = 30000
        self.event.save(update_fields=["access_type", "price_per_photo_kopecks"])

        self.assertEqual(
            policy_for_new_photo(self.event, self.photographer),
            ("preview_first_v1", "preview_required"),
        )
        FeatureFlag.objects.create(
            key="paid-watermarked-previews",
            description="Paid watermarked previews",
            state=FeatureFlag.State.OFF,
        )
        self.assertEqual(
            policy_for_new_photo(self.event, self.photographer),
            ("preview_first_v1", "preview_required"),
        )

    @override_settings(PHOTO_PROCESSING_PREVIEW_ENABLED=True)
    def test_enabled_paid_caller_receives_the_watermarked_policy(self) -> None:
        from picflow.photo_policy import policy_for_new_photo

        staff = get_user_model().objects.create_user(username="staff", is_staff=True)
        self.event.access_type = Event.AccessType.PAID
        self.event.price_per_photo_kopecks = 30000
        self.event.save(update_fields=["access_type", "price_per_photo_kopecks"])
        FeatureFlag.objects.create(
            key="paid-watermarked-previews",
            description="Paid watermarked previews",
            state=FeatureFlag.State.STAFF,
        )

        self.assertEqual(
            policy_for_new_photo(self.event, staff),
            ("preview_first_watermarked_v1", "watermarked_preview_required"),
        )

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
