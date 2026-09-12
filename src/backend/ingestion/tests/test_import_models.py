from datetime import date

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from ingestion.models import ImportBatch, ImportItem, ImportScope
from picflow.models import Event, EventFolder


class ImportModelTests(TestCase):
    def setUp(self) -> None:
        self.owner = get_user_model().objects.create_user(username="import-model-owner")
        self.event = Event.objects.create(
            name="Import models",
            slug="import-models",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

    def test_scope_is_unique_when_destination_folder_is_null(self) -> None:
        ImportScope.objects.create(
            owner=self.owner,
            event=self.event,
            folder=None,
            canonical_source_key="public-folder",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            ImportScope.objects.create(
                owner=self.owner,
                event=self.event,
                folder=None,
                canonical_source_key="public-folder",
            )

    def test_import_relations_are_protected_and_do_not_use_upload_items(self) -> None:
        folder = EventFolder.objects.create(event=self.event, name="Finish")
        batch = ImportBatch.objects.create(
            owner=self.owner,
            event=self.event,
            folder=folder,
            submitted_source_key="submitted-key",
            submission_key="request-1",
        )
        item = ImportItem.objects.create(
            batch=batch,
            source_path="/finish.jpg",
            filename="finish.jpg",
            byte_size=123,
        )

        self.assertEqual(item.batch, batch)
        self.assertEqual(batch.folder, folder)
        for model, field in (
            (ImportBatch, "owner"),
            (ImportBatch, "event"),
            (ImportBatch, "folder"),
        ):
            self.assertEqual(
                model._meta.get_field(field).remote_field.on_delete.__name__,
                "PROTECT",
            )

    def test_source_path_is_idempotent_within_one_manifest(self) -> None:
        batch = ImportBatch.objects.create(
            owner=self.owner,
            event=self.event,
            submitted_source_key="submitted-key",
            submission_key="request-2",
        )
        ImportItem.objects.create(
            batch=batch,
            source_path="/photo.jpg",
            filename="photo.jpg",
            byte_size=123,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            ImportItem.objects.create(
                batch=batch,
                source_path="/photo.jpg",
                filename="renamed.jpg",
                byte_size=123,
            )
