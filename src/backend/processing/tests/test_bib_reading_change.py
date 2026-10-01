from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from picflow.tests.event_management_helpers import (
    accepted_attempt,
    event,
    private_photo,
    user_with_permissions,
)

from processing.models import BIB_RECOGNITION_PROCESSOR, BibReadingChange


class BibReadingChangeTests(TestCase):
    def setUp(self) -> None:
        self.actor = user_with_permissions("bib-auditor")
        self.photo = private_photo(
            event("Bib audit"), self.actor, bib_processing_policy="original_v1"
        )
        self.attempt = accepted_attempt(self.photo, processor_type=BIB_RECOGNITION_PROCESSOR)

    def change(self, before: str | None, after: str | None) -> BibReadingChange:
        return BibReadingChange(
            photo=self.photo,
            source_attempt=self.attempt,
            before_number=before,
            after_number=after,
            changed_by=self.actor,
        )

    def test_add_replace_delete_shapes_and_leading_zeroes_validate(self) -> None:
        for before, after in ((None, "0012"), ("12", "0012"), ("12", None)):
            with self.subTest(before=before, after=after):
                change = self.change(before, after)
                change.full_clean()
                change.save()
        self.assertEqual(BibReadingChange.objects.count(), 3)

    def test_invalid_shapes_and_non_ascii_digits_fail_validation(self) -> None:
        for before, after in (
            (None, None),
            ("12", "12"),
            ("１２", None),
            (None, "12a"),
            (None, "1" * 17),
        ):
            with self.subTest(before=before, after=after), self.assertRaises(ValidationError):
                self.change(before, after).full_clean()

    def test_database_constraints_reject_invalid_shapes(self) -> None:
        for before, after in ((None, None), ("12", "12"), (None, "１２")):
            with (
                self.subTest(before=before, after=after),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                self.change(before, after).save()

    def test_source_attempt_must_match_photo_and_bib_recognition(self) -> None:
        other_photo = private_photo(self.photo.event, self.actor)
        change = self.change(None, "12")
        change.source_attempt = accepted_attempt(
            other_photo, processor_type=BIB_RECOGNITION_PROCESSOR
        )
        with self.assertRaises(ValidationError):
            change.full_clean()

    def test_change_is_immutable_and_protects_references(self) -> None:
        change = self.change(None, "12")
        change.save()
        change.after_number = "34"
        with self.assertRaises(ValidationError):
            change.save()
        with self.assertRaises(ValidationError):
            change.delete()
        with self.assertRaises(ProtectedError):
            self.attempt.delete()

    def test_bulk_update_and_delete_cannot_rewrite_audit_evidence(self) -> None:
        change = self.change(None, "12")
        change.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            BibReadingChange.objects.filter(pk=change.pk).update(after_number="34")
        with self.assertRaises(IntegrityError), transaction.atomic():
            BibReadingChange.objects.filter(pk=change.pk).delete()
        self.assertEqual(BibReadingChange.objects.get(pk=change.pk).after_number, "12")
