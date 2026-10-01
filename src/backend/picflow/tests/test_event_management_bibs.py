from django.core.exceptions import ValidationError
from django.test import TestCase
from processing.models import (
    BIB_RECOGNITION_PROCESSOR,
    BibReading,
    BibReadingChange,
    PhotoProcessingState,
)

from picflow.event_management_bibs import save_event_photo_bibs
from picflow.tests.event_management_helpers import (
    accepted_attempt,
    event,
    private_photo,
    user_with_permissions,
)


class EventPhotoBibEditingTests(TestCase):
    def setUp(self) -> None:
        self.actor = user_with_permissions("bib-editor")
        self.event = event("Bib editing")
        self.photo = private_photo(
            self.event,
            self.actor,
            bib_processing_policy="original_v1",
        )
        self.attempt = accepted_attempt(self.photo, processor_type=BIB_RECOGNITION_PROCESSOR)

    def reading(self, number: str) -> BibReading:
        return BibReading.objects.create(
            photo=self.photo,
            source_attempt=self.attempt,
            number=number,
            evidence={"candidate_id": number},
        )

    def save(self, existing: dict[int, str], added: list[str] | None = None):
        return save_event_photo_bibs(
            event_id=self.event.pk,
            photo_id=self.photo.pk,
            existing=existing,
            added=added or [],
            changed_by=self.actor,
        )

    def test_add_after_empty_success_uses_current_attempt_and_audits_actor(self) -> None:
        readings = self.save({}, [" 0012 ", ""])

        self.assertEqual([reading.number for reading in readings], ["0012"])
        added = readings[0]
        self.assertEqual(added.source_attempt_id, self.attempt.pk)
        self.assertEqual(added.evidence, {"source": "manual"})
        change = BibReadingChange.objects.get()
        self.assertEqual(change.photo_id, self.photo.pk)
        self.assertEqual(change.source_attempt_id, self.attempt.pk)
        self.assertEqual(change.changed_by_id, self.actor.pk)
        self.assertIsNone(change.before_number)
        self.assertEqual(change.after_number, "0012")
        self.assertIsNotNone(change.created_at)

    def test_replace_keeps_original_evidence_and_writes_one_change(self) -> None:
        original = self.reading("12")

        readings = self.save({original.pk: " 0012 "})

        self.assertEqual(
            [(reading.pk, reading.number) for reading in readings], [(original.pk, "0012")]
        )
        original.refresh_from_db()
        self.assertEqual(original.source_attempt_id, self.attempt.pk)
        self.assertEqual(original.evidence, {"candidate_id": "12"})
        self.assertEqual(
            list(BibReadingChange.objects.values_list("before_number", "after_number")),
            [("12", "0012")],
        )

    def test_replace_into_existing_number_retains_target_row(self) -> None:
        source = self.reading("12")
        target = self.reading("34")

        readings = self.save({source.pk: "34", target.pk: "34"})

        self.assertEqual(
            [(reading.pk, reading.number) for reading in readings], [(target.pk, "34")]
        )
        self.assertFalse(BibReading.objects.filter(pk=source.pk).exists())
        self.assertEqual(
            list(BibReadingChange.objects.values_list("before_number", "after_number")),
            [("12", "34")],
        )

    def test_delete_final_number_leaves_empty_projection_and_audit(self) -> None:
        source = self.reading("12")

        self.assertEqual(self.save({source.pk: " "}), [])

        self.assertFalse(BibReading.objects.filter(photo=self.photo).exists())
        self.assertEqual(
            list(BibReadingChange.objects.values_list("before_number", "after_number")),
            [("12", None)],
        )

    def test_unchanged_save_writes_no_change(self) -> None:
        source = self.reading("12")

        self.assertEqual([reading.pk for reading in self.save({source.pk: " 12 "})], [source.pk])
        self.assertFalse(BibReadingChange.objects.exists())

    def test_invalid_digit_or_duplicate_final_value_rolls_back_everything(self) -> None:
        source = self.reading("12")
        for added in (["bad"], ["34", "34"], ["１２"]):
            with self.subTest(added=added), self.assertRaises(ValidationError):
                self.save({source.pk: "99"}, added)
            self.assertEqual(list(BibReading.objects.values_list("number", flat=True)), ["12"])
            self.assertFalse(BibReadingChange.objects.exists())

    def test_malformed_values_are_validation_errors_without_mutation(self) -> None:
        source = self.reading("12")
        with self.assertRaises(ValidationError):
            self.save({source.pk: None})  # type: ignore[dict-item]
        self.assertEqual(BibReading.objects.get(pk=source.pk).number, "12")
        self.assertFalse(BibReadingChange.objects.exists())

    def test_stale_or_foreign_reading_id_rejects_complete_edit(self) -> None:
        source = self.reading("12")
        other_photo = private_photo(self.event, self.actor, bib_processing_policy="original_v1")
        foreign = BibReading.objects.create(
            photo=other_photo,
            source_attempt=accepted_attempt(other_photo, processor_type=BIB_RECOGNITION_PROCESSOR),
            number="90",
        )
        for existing in ({}, {source.pk: "12", 999999: "45"}, {foreign.pk: "90"}):
            with self.subTest(existing=existing), self.assertRaises(ValidationError):
                self.save(existing, ["55"])
        self.assertEqual(
            list(BibReading.objects.filter(photo=self.photo).values_list("number", flat=True)),
            ["12"],
        )
        self.assertFalse(BibReadingChange.objects.exists())

    def test_wrong_event_policy_and_missing_accepted_attempt_are_rejected(self) -> None:
        other_event = event("Other bib event")
        with self.assertRaises(ValidationError):
            save_event_photo_bibs(
                event_id=other_event.pk,
                photo_id=self.photo.pk,
                existing={},
                added=["12"],
                changed_by=self.actor,
            )
        disabled = private_photo(self.event, self.actor)
        accepted_attempt(disabled, processor_type=BIB_RECOGNITION_PROCESSOR)
        with self.assertRaises(ValidationError):
            save_event_photo_bibs(
                event_id=self.event.pk,
                photo_id=disabled.pk,
                existing={},
                added=["12"],
                changed_by=self.actor,
            )
        PhotoProcessingState.objects.filter(
            photo=self.photo, processor_type=BIB_RECOGNITION_PROCESSOR
        ).update(accepted_attempt=None)
        with self.assertRaises(ValidationError):
            self.save({}, ["12"])
        self.assertFalse(BibReading.objects.exists())
        self.assertFalse(BibReadingChange.objects.exists())

    def test_failed_accepted_attempt_is_ineligible(self) -> None:
        PhotoProcessingState.objects.filter(
            photo=self.photo, processor_type=BIB_RECOGNITION_PROCESSOR
        ).update(status=PhotoProcessingState.Status.FAILED)
        with self.assertRaises(ValidationError):
            self.save({}, ["12"])
        self.assertFalse(BibReading.objects.exists())

    def test_domain_save_does_not_depend_on_actor_permissions(self) -> None:
        self.assertEqual([reading.number for reading in self.save({}, ["12"])], ["12"])
