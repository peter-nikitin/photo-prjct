from datetime import date
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from ingestion.models import UploadBatch, UploadItem
from picflow.models import Event


class UploadModelTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="photographer")
        self.event = Event.objects.create(
            name="Test Run",
            slug="test-run",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

    def make_batch(self, **overrides):
        values = {"event": self.event, "uploader": self.user, "expected_item_count": 1}
        values.update(overrides)
        return UploadBatch(**values)

    def make_item(self, batch, **overrides):
        values = {
            "batch": batch,
            "client_item_id": uuid4(),
            "original_filename": "race.jpg",
            "declared_content_type": "image/jpeg",
            "expected_size": 1024,
            "incoming_key": f"incoming/{uuid4().hex}",
            "final_key": f"originals/{uuid4().hex}",
        }
        values.update(overrides)
        return UploadItem(**values)

    def test_batch_count_boundaries_are_valid(self) -> None:
        self.make_batch(expected_item_count=1).full_clean()
        self.make_batch(expected_item_count=10_000).full_clean()

    def test_batch_count_outside_range_fails_model_and_database(self) -> None:
        for value in (0, 10_001):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.make_batch(expected_item_count=value).full_clean()
            with self.subTest(value=value), self.assertRaises(IntegrityError), transaction.atomic():
                self.make_batch(expected_item_count=value).save()

    def test_item_size_outside_range_fails_model_and_database(self) -> None:
        batch = self.make_batch()
        batch.save()
        for value in (0, 52_428_801):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.make_item(batch, expected_size=value).full_clean()
            with self.subTest(value=value), self.assertRaises(IntegrityError), transaction.atomic():
                self.make_item(batch, expected_size=value).save()

    def test_declared_content_type_must_be_jpeg(self) -> None:
        batch = self.make_batch()
        batch.save()
        item = self.make_item(batch, declared_content_type="image/png")
        with self.assertRaises(ValidationError):
            item.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            item.save()

    def test_client_item_id_is_unique_within_batch(self) -> None:
        batch = self.make_batch()
        batch.save()
        client_item_id = uuid4()
        self.make_item(batch, client_item_id=client_item_id).save()
        duplicate = self.make_item(batch, client_item_id=client_item_id)
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()

    def test_storage_keys_are_globally_unique(self) -> None:
        batch = self.make_batch()
        batch.save()
        first = self.make_item(batch)
        first.save()
        for field_name in ("incoming_key", "final_key"):
            with self.subTest(field_name=field_name):
                duplicate = self.make_item(batch, **{field_name: getattr(first, field_name)})
                with self.assertRaises(ValidationError):
                    duplicate.full_clean()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    duplicate.save()

    def test_status_choices_and_database_checks_reject_unknown_values(self) -> None:
        batch = self.make_batch(status="unknown")
        with self.assertRaises(ValidationError):
            batch.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            batch.save()

        valid_batch = self.make_batch()
        valid_batch.save()
        item = self.make_item(valid_batch, status="unknown")
        with self.assertRaises(ValidationError):
            item.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            item.save()
