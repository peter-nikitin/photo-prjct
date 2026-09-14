from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import connection
from django.http import QueryDict
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from ingestion.models import UploadBatch, UploadItem
from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoProcessingState,
    ProcessingJob,
)

from picflow.event_management import (
    EventManagementCapabilities,
    EventPhotoSelection,
    apply_event_photo_action,
    create_event_folder,
    delete_event_folder,
    event_management_capabilities,
    event_photo_page,
    event_photo_queryset,
    rename_event_folder,
)
from picflow.event_management_forms import (
    EventFolderCreateForm,
    EventFolderDeleteForm,
    EventFolderRenameForm,
    EventPhotoFilterForm,
)
from picflow.models import Event, EventFolder, Photo
from picflow.tests.event_management_helpers import (
    accepted_attempt,
    captured_at,
    private_photo,
    user_with_permissions,
)


class EventManagementTestCase(TestCase):
    def setUp(self) -> None:
        self.event = Event.objects.create(
            name="City race",
            slug="city-race",
            start_date=date(2026, 9, 6),
            end_date=date(2026, 9, 7),
            city="Moscow",
            timezone_name="Europe/Moscow",
        )
        self.alice = user_with_permissions("alice")
        self.bob = user_with_permissions("bob")

    def form(self, query: str = "") -> EventPhotoFilterForm:
        return EventPhotoFilterForm(self.event, QueryDict(query))

    def legacy_photo(self, photo_id: str, **overrides: object) -> Photo:
        values: dict[str, object] = {
            "id": photo_id,
            "event": self.event,
            "src": f"photos/{photo_id}.jpg",
        }
        values.update(overrides)
        return Photo.objects.create(**values)


class EventPhotoFilterFormTests(EventManagementTestCase):
    """These tests catch broadened or unstable administrative filter input."""

    def test_rejects_unknown_or_foreign_folder_uploader_and_processing_values(self) -> None:
        folder = EventFolder.objects.create(event=self.event, name="Start")
        private_photo(self.event, self.alice, folder=folder)
        other_event = Event.objects.create(
            name="Other race",
            slug="other-race",
            start_date=self.event.start_date,
            end_date=self.event.end_date,
            city="Moscow",
            timezone_name="Europe/Moscow",
        )
        foreign_folder = EventFolder.objects.create(event=other_event, name="Foreign")
        foreign_uploader = user_with_permissions("foreign")
        private_photo(other_event, foreign_uploader, folder=foreign_folder)

        cases = (
            f"folder={foreign_folder.pk}",
            "folder=999999",
            f"uploader={foreign_uploader.pk}",
            "uploader=999999",
            "processing=unknown",
            "visibility=unknown",
            "unfiled=maybe",
            "uploader_unknown=maybe",
            "without_capture_time=maybe",
        )
        for query in cases:
            with self.subTest(query=query):
                self.assertFalse(self.form(query).is_valid())

    def test_choices_are_stable_event_scoped_and_include_empty_folders(self) -> None:
        start = EventFolder.objects.create(event=self.event, name="Start")
        empty = EventFolder.objects.create(event=self.event, name="empty")
        private_photo(self.event, self.alice, folder=start)
        self.legacy_photo("legacy-unknown", is_hidden=True)
        other_event = Event.objects.create(
            name="Other event",
            slug="other-event",
            start_date=self.event.start_date,
            end_date=self.event.end_date,
            city="Moscow",
            timezone_name="Europe/Moscow",
        )
        foreign_folder = EventFolder.objects.create(event=other_event, name="Foreign")
        private_photo(other_event, self.bob, folder=foreign_folder)

        unfiltered = self.form()
        filtered = self.form(f"folder={start.pk}&uploader={self.alice.pk}&visibility=hidden")
        self.assertTrue(unfiltered.is_valid())
        self.assertTrue(filtered.is_valid())
        expected_folders = [(empty.pk, "empty"), (start.pk, "Start")]
        expected_uploaders = [(self.alice.pk, "alice")]
        self.assertEqual(list(unfiltered.fields["folder"].choices), expected_folders)
        self.assertEqual(list(filtered.fields["folder"].choices), expected_folders)
        self.assertEqual(list(unfiltered.fields["uploader"].choices), expected_uploaders)
        self.assertEqual(list(filtered.fields["uploader"].choices), expected_uploaders)

    def test_reuses_event_local_time_validation_and_missing_time_is_separate(self) -> None:
        valid = self.form("from=2026-09-06T12:00&to=2026-09-06T13:00")
        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertEqual(
            valid.filters.capture_time_bounds,
            (
                datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
                datetime(2026, 9, 6, 10, 0, tzinfo=UTC),
            ),
        )

        for query in (
            "from=2026-09-05T23:59",
            "from=2026-09-06T13:00&to=2026-09-06T12:00",
            "from=2026-09-06T12:00&without_capture_time=1",
            "from=2026-09-06T12:00&from=2026-09-06T12:01",
        ):
            with self.subTest(query=query):
                self.assertFalse(self.form(query).is_valid())

    def test_timezone_less_unavailable_event_rejects_only_requested_time_range(self) -> None:
        self.event.timezone_name = None
        self.event.save(update_fields=["timezone_name"])

        requested_range = self.form("from=2026-09-06T12:00")
        self.assertFalse(requested_range.is_valid())
        self.assertIn("from", requested_range.errors)

        empty_range = self.form("from=&to=")
        self.assertTrue(empty_range.is_valid(), empty_range.errors)
        self.assertIsNone(empty_range.filters.capture_time_bounds)

        missing_time = self.form("without_capture_time=1")
        self.assertTrue(missing_time.is_valid(), missing_time.errors)
        self.assertTrue(missing_time.filters.without_capture_time)


class EventPhotoQuerysetTests(EventManagementTestCase):
    """These tests catch OR/AND mistakes, time inference, duplicate rows, and unstable pages."""

    def set_processing_state(
        self,
        photo: Photo,
        status: object,
        *,
        processor_type: str = CAPTURE_METADATA_PROCESSOR,
    ) -> None:
        contract_version = 1 if processor_type == CAPTURE_METADATA_PROCESSOR else 2
        processor_version = 2 if processor_type == CAPTURE_METADATA_PROCESSOR else 1
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=contract_version,
            processor_type=processor_type,
            processor_version=processor_version,
            configuration={},
            configuration_hash=uuid4().hex * 2,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=contract_version,
            processor_type=processor_type,
            processor_version=processor_version,
            configuration={},
            configuration_hash=run.configuration_hash,
            input_fingerprint={},
            status=status,
        )
        PhotoProcessingState.objects.update_or_create(
            photo=photo,
            processor_type=processor_type,
            defaults={"status": status, "current_run": run, "current_job": job},
        )

    def test_combines_or_within_groups_and_and_across_groups(self) -> None:
        start = EventFolder.objects.create(event=self.event, name="Start")
        finish = EventFolder.objects.create(event=self.event, name="Finish")
        outside = EventFolder.objects.create(event=self.event, name="Outside")
        captured = datetime(2026, 9, 6, 12, 30, tzinfo=UTC)
        wanted = private_photo(self.event, self.alice, folder=start)
        also_wanted = private_photo(self.event, self.bob, folder=finish)
        wrong_folder = private_photo(self.event, self.alice, folder=outside)
        wrong_uploader = private_photo(self.event, user_with_permissions("charlie"), folder=start)
        wrong_visibility = private_photo(self.event, self.alice, folder=start, is_hidden=True)
        wrong_time = private_photo(self.event, self.alice, folder=start)
        for photo in (wanted, also_wanted, wrong_folder, wrong_uploader, wrong_visibility):
            captured_at(photo, captured)
        captured_at(wrong_time, captured - timedelta(days=1))

        form = self.form(
            f"folder={start.pk}&folder={finish.pk}"
            f"&uploader={self.alice.pk}&uploader={self.bob.pk}"
            "&from=2026-09-06T15:00&to=2026-09-06T16:00"
            "&visibility=visible&processing=not_started"
        )
        self.assertTrue(form.is_valid(), form.errors)

        self.assertEqual(
            list(event_photo_queryset(self.event, form.filters).values_list("pk", flat=True)),
            sorted((wanted.pk, also_wanted.pk)),
        )

    def test_missing_capture_time_never_uses_upload_time(self) -> None:
        missing = private_photo(
            self.event,
            self.alice,
            uploaded_at=datetime(2026, 9, 6, 12, 30, tzinfo=UTC),
        )
        present = private_photo(self.event, self.alice)
        captured_at(present, datetime(2026, 9, 6, 12, 30, tzinfo=UTC))
        form = self.form("without_capture_time=1")
        self.assertTrue(form.is_valid(), form.errors)

        self.assertEqual(
            list(event_photo_queryset(self.event, form.filters).values_list("pk", flat=True)),
            [missing.pk],
        )

    def test_processing_filter_returns_each_photo_once_with_multiple_states(self) -> None:
        photo = private_photo(self.event, self.alice)
        captured_at(photo, datetime(2026, 9, 6, 12, 30, tzinfo=UTC))
        accepted_attempt(photo, processor_type=GENERATE_PREVIEW_PROCESSOR)
        form = self.form("processing=not_started")
        self.assertTrue(form.is_valid(), form.errors)

        queryset = event_photo_queryset(self.event, form.filters)
        self.assertEqual(queryset.count(), 1)
        self.assertEqual(list(queryset.values_list("pk", flat=True)), [photo.pk])

    def test_active_processing_filters_avoid_full_category_projection(self) -> None:
        queued = private_photo(self.event, self.alice)
        retry_wait = private_photo(self.event, self.alice)
        processing = private_photo(self.event, self.alice)
        queued_but_processing = private_photo(self.event, self.alice)
        self.set_processing_state(queued, PhotoProcessingState.Status.QUEUED)
        self.set_processing_state(retry_wait, PhotoProcessingState.Status.RETRY_WAIT)
        self.set_processing_state(processing, PhotoProcessingState.Status.PROCESSING)
        self.set_processing_state(queued_but_processing, PhotoProcessingState.Status.QUEUED)
        self.set_processing_state(
            queued_but_processing,
            PhotoProcessingState.Status.PROCESSING,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
        )

        queued_form = self.form("processing=queued")
        self.assertTrue(queued_form.is_valid(), queued_form.errors)
        queued_photos = event_photo_queryset(self.event, queued_form.filters)

        self.assertEqual(
            list(queued_photos.values_list("pk", flat=True)),
            sorted((queued.pk, retry_wait.pk)),
        )
        sql = str(queued_photos.values("pk").query).lower()
        self.assertNotIn("case when", sql)
        self.assertLessEqual(sql.count("select"), 3)

        processing_form = self.form("processing=processing")
        self.assertTrue(processing_form.is_valid(), processing_form.errors)
        self.assertEqual(
            list(
                event_photo_queryset(self.event, processing_form.filters).values_list(
                    "pk", flat=True
                )
            ),
            sorted((processing.pk, queued_but_processing.pk)),
        )

        active_form = self.form("processing=queued&processing=processing")
        self.assertTrue(active_form.is_valid(), active_form.errors)
        active_photos = event_photo_queryset(self.event, active_form.filters)
        self.assertEqual(
            list(active_photos.values_list("pk", flat=True)),
            sorted((queued.pk, retry_wait.pk, processing.pk, queued_but_processing.pk)),
        )
        self.assertNotIn("case when", str(active_photos.values("pk").query).lower())

    def test_processing_projection_is_only_added_for_a_processing_filter(self) -> None:
        photo = private_photo(self.event, self.alice)
        unfiltered_form = self.form()
        processing_form = self.form("processing=not_started")
        self.assertTrue(unfiltered_form.is_valid(), unfiltered_form.errors)
        self.assertTrue(processing_form.is_valid(), processing_form.errors)

        with CaptureQueriesContext(connection) as unfiltered_queries:
            self.assertEqual(
                list(
                    event_photo_queryset(self.event, unfiltered_form.filters).values_list(
                        "pk", flat=True
                    )
                ),
                [photo.pk],
            )
        with CaptureQueriesContext(connection) as processing_queries:
            self.assertEqual(
                list(
                    event_photo_queryset(self.event, processing_form.filters).values_list(
                        "pk", flat=True
                    )
                ),
                [photo.pk],
            )

        processing_table = "processing_photoprocessingstate"
        self.assertNotIn(processing_table, unfiltered_queries.captured_queries[0]["sql"].lower())
        self.assertIn(processing_table, processing_queries.captured_queries[0]["sql"].lower())

    def test_page_is_100_photos_in_capture_time_then_id_order(self) -> None:
        photos = [self.legacy_photo(f"legacy-{index:03d}") for index in range(101)]
        capture = datetime(2026, 9, 6, 12, 30, tzinfo=UTC)
        for photo in photos[-2:]:
            captured_at(photo, capture)
        form = self.form()
        self.assertTrue(form.is_valid())

        first = event_photo_page(self.event, form.filters, page=1)
        second = event_photo_page(self.event, form.filters, page=2)
        self.assertEqual(first.paginator.per_page, 100)
        self.assertEqual(
            [photo.pk for photo in first][:3], ["legacy-099", "legacy-100", "legacy-000"]
        )
        self.assertEqual([photo.pk for photo in second], ["legacy-098"])


class EventPhotoActionTests(EventManagementTestCase):
    """These tests catch broadened selection, partial writes, and inflated changed counts."""

    def test_all_filtered_is_reevaluated_when_the_action_runs(self) -> None:
        folder = EventFolder.objects.create(event=self.event, name="Start")
        first = self.legacy_photo("first", folder=folder)
        form = self.form(f"folder={folder.pk}&visibility=visible")
        self.assertTrue(form.is_valid())
        selection = EventPhotoSelection.all_filtered(form.filters)
        second = self.legacy_photo("second", folder=folder)

        changed = apply_event_photo_action(self.event, selection, "hide", None)

        self.assertEqual(changed, 2)
        self.assertEqual(
            set(Photo.objects.filter(is_hidden=True).values_list("pk", flat=True)),
            {first.pk, second.pk},
        )

    def test_explicit_selection_is_fixed_and_cross_event_ids_reject_atomically(self) -> None:
        local = self.legacy_photo("local")
        unselected = self.legacy_photo("unselected")
        selection = EventPhotoSelection.explicit((local.pk,))
        self.assertEqual(apply_event_photo_action(self.event, selection, "hide", None), 1)
        unselected.refresh_from_db()
        self.assertFalse(unselected.is_hidden)

        other_event = Event.objects.create(
            name="Other race",
            slug="other-race",
            start_date=self.event.start_date,
            end_date=self.event.end_date,
            city="Moscow",
            timezone_name="Europe/Moscow",
        )
        foreign = Photo.objects.create(id="foreign", event=other_event, src="photos/foreign.jpg")
        local.is_hidden = False
        local.save(update_fields=["is_hidden"])
        invalid = EventPhotoSelection.explicit((local.pk, foreign.pk))

        with self.assertRaises(ValidationError):
            apply_event_photo_action(self.event, invalid, "hide", None)
        local.refresh_from_db()
        foreign.refresh_from_db()
        self.assertFalse(local.is_hidden)
        self.assertFalse(foreign.is_hidden)

    def test_move_supports_no_folder_and_counts_only_changed_rows(self) -> None:
        source = EventFolder.objects.create(event=self.event, name="Source")
        target = EventFolder.objects.create(event=self.event, name="Target")
        photo = private_photo(self.event, self.alice, folder=source, is_hidden=True)
        original = (photo.original_key, photo.uploaded_by_id, photo.is_hidden)
        selection = EventPhotoSelection.explicit((photo.pk,))

        self.assertEqual(apply_event_photo_action(self.event, selection, "move", target), 1)
        self.assertEqual(apply_event_photo_action(self.event, selection, "move", target), 0)
        self.assertEqual(apply_event_photo_action(self.event, selection, "move", None), 1)
        self.assertEqual(apply_event_photo_action(self.event, selection, "move", None), 0)
        photo.refresh_from_db()
        self.assertIsNone(photo.folder_id)
        self.assertEqual((photo.original_key, photo.uploaded_by_id, photo.is_hidden), original)

    def test_foreign_target_folder_rejects_without_changing_selection(self) -> None:
        photo = self.legacy_photo("local")
        other_event = Event.objects.create(
            name="Foreign target event",
            slug="foreign-target-event",
            start_date=self.event.start_date,
            end_date=self.event.end_date,
            city="Moscow",
            timezone_name="Europe/Moscow",
        )
        foreign_folder = EventFolder.objects.create(event=other_event, name="Foreign")

        with self.assertRaises(ValidationError):
            apply_event_photo_action(
                self.event,
                EventPhotoSelection.explicit((photo.pk,)),
                "move",
                foreign_folder,
            )
        photo.refresh_from_db()
        self.assertIsNone(photo.folder_id)

    def test_unknown_explicit_photo_rejects_without_changing_valid_photos(self) -> None:
        photo = self.legacy_photo("known")
        selection = EventPhotoSelection.explicit((photo.pk, "missing"))

        with self.assertRaises(ValidationError):
            apply_event_photo_action(self.event, selection, "hide", None)
        photo.refresh_from_db()
        self.assertFalse(photo.is_hidden)

    def test_hide_show_noops_report_zero(self) -> None:
        photo = self.legacy_photo("photo")
        selection = EventPhotoSelection.explicit((photo.pk,))
        self.assertEqual(apply_event_photo_action(self.event, selection, "show", None), 0)
        self.assertEqual(apply_event_photo_action(self.event, selection, "hide", None), 1)
        self.assertEqual(apply_event_photo_action(self.event, selection, "hide", None), 0)
        self.assertEqual(apply_event_photo_action(self.event, selection, "show", None), 1)


class EventFolderServiceTests(EventManagementTestCase):
    """These tests catch unnormalized names, partial conflicts, and bypassed FK protection."""

    def test_create_and_rename_normalize_names_and_reject_event_local_conflicts(self) -> None:
        folder = create_event_folder(self.event, "  Start  ")
        self.assertEqual(folder.name, "Start")
        renamed = rename_event_folder(self.event, folder, "  Finish  ")
        self.assertEqual(renamed.name, "Finish")
        create_event_folder(self.event, "Start")

        with self.assertRaises(ValidationError):
            rename_event_folder(self.event, renamed, " start ")
        renamed.refresh_from_db()
        self.assertEqual(renamed.name, "Finish")

    def test_delete_empty_folder_and_reject_hidden_photo_or_upload_item_references(self) -> None:
        empty = EventFolder.objects.create(event=self.event, name="Empty")
        delete_event_folder(self.event, empty)
        self.assertFalse(EventFolder.objects.filter(pk=empty.pk).exists())

        hidden_folder = EventFolder.objects.create(event=self.event, name="Hidden")
        self.legacy_photo("hidden", folder=hidden_folder, is_hidden=True)
        with self.assertRaises(ValidationError):
            delete_event_folder(self.event, hidden_folder)
        self.assertTrue(EventFolder.objects.filter(pk=hidden_folder.pk).exists())

        upload_folder = EventFolder.objects.create(event=self.event, name="Uploading")
        batch = UploadBatch.objects.create(
            event=self.event,
            uploader=self.alice,
            expected_item_count=1,
        )
        UploadItem.objects.create(
            batch=batch,
            client_item_id=uuid4(),
            original_filename="pending.jpg",
            declared_content_type="image/jpeg",
            expected_size=1,
            incoming_key=f"incoming/{uuid4()}",
            final_key=f"final/{uuid4()}",
            folder=upload_folder,
        )
        with self.assertRaises(ValidationError):
            delete_event_folder(self.event, upload_folder)
        self.assertTrue(EventFolder.objects.filter(pk=upload_folder.pk).exists())

    def test_foreign_folder_cannot_be_renamed_or_deleted(self) -> None:
        other_event = Event.objects.create(
            name="Other",
            slug="other",
            start_date=self.event.start_date,
            end_date=self.event.end_date,
            city="Moscow",
            timezone_name="Europe/Moscow",
        )
        folder = EventFolder.objects.create(event=other_event, name="Foreign")
        with self.assertRaises(ValidationError):
            rename_event_folder(self.event, folder, "Changed")
        with self.assertRaises(ValidationError):
            delete_event_folder(self.event, folder)
        folder.refresh_from_db()
        self.assertEqual(folder.name, "Foreign")

    def test_folder_forms_return_expected_conflicts_as_field_errors(self) -> None:
        existing = EventFolder.objects.create(event=self.event, name="Start")
        create_form = EventFolderCreateForm(self.event, {"name": " start "})
        self.assertTrue(create_form.is_valid())
        self.assertIsNone(create_form.save())
        self.assertIn("name", create_form.errors)

        renamed = EventFolder.objects.create(event=self.event, name="Finish")
        rename_form = EventFolderRenameForm(
            self.event,
            {"folder": renamed.pk, "name": " START "},
        )
        self.assertTrue(rename_form.is_valid())
        self.assertIsNone(rename_form.save())
        self.assertIn("name", rename_form.errors)
        renamed.refresh_from_db()
        self.assertEqual(renamed.name, "Finish")

        self.legacy_photo("hidden-reference", folder=existing, is_hidden=True)
        delete_form = EventFolderDeleteForm(self.event, {"folder": existing.pk})
        self.assertTrue(delete_form.is_valid())
        self.assertFalse(delete_form.delete())
        self.assertIn("folder", delete_form.errors)


class EventManagementCapabilityTests(EventManagementTestCase):
    """This catches granting mutation permissions without the private admin read boundary."""

    def test_mutation_capabilities_require_admin_read_and_the_specific_permission(self) -> None:
        admin = user_with_permissions(
            "admin",
            staff=True,
            permissions=(
                "picflow.view_event",
                "picflow.view_photo",
                "picflow.change_photo",
                "picflow.add_eventfolder",
                "picflow.change_eventfolder",
                "picflow.delete_eventfolder",
            ),
        )
        photographer = user_with_permissions(
            "photographer-with-change",
            permissions=("picflow.change_photo", "picflow.add_eventfolder"),
        )

        self.assertEqual(
            event_management_capabilities(admin),
            EventManagementCapabilities(
                can_change_photos=True,
                can_add_folders=True,
                can_change_folders=True,
                can_delete_folders=True,
            ),
        )
        self.assertEqual(
            event_management_capabilities(photographer),
            EventManagementCapabilities(
                can_change_photos=False,
                can_add_folders=False,
                can_change_folders=False,
                can_delete_folders=False,
            ),
        )
