"""Atomic changes to a photo's current bib-number projection."""

from collections.abc import Mapping, Sequence

from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ValidationError
from django.db import transaction
from processing.models import (
    BIB_RECOGNITION_PROCESSOR,
    BibReading,
    BibReadingChange,
    PhotoProcessingState,
    ProcessingAttempt,
)

from picflow.forms import BibSearchForm
from picflow.models import Photo


def _number(value: str) -> str:
    form = BibSearchForm({"bib": value})
    if not form.is_valid() or not form.cleaned_data["bib"]:
        raise ValidationError("Введите номер от 1 до 16 цифр.")
    return form.cleaned_data["bib"]


@transaction.atomic
def save_event_photo_bibs(
    *,
    event_id: int,
    photo_id: str,
    existing: Mapping[int, str],
    added: Sequence[str],
    changed_by: AbstractBaseUser,
) -> list[BibReading]:
    """Save one complete event-photo edit, then return its current rows by number."""
    photo = Photo.objects.select_for_update().filter(pk=photo_id, event_id=event_id).first()
    if photo is None or photo.bib_processing_policy != Photo.BibProcessingPolicy.ORIGINAL_V1:
        raise ValidationError("Bib editing is unavailable for this photo.")
    state = (
        PhotoProcessingState.objects.select_related("accepted_attempt")
        .filter(photo=photo, processor_type=BIB_RECOGNITION_PROCESSOR)
        .first()
    )
    attempt = state.accepted_attempt if state is not None else None
    if (
        state is None
        or state.status != PhotoProcessingState.Status.SUCCEEDED
        or attempt is None
        or attempt.photo_id != photo.pk
        or attempt.processor_type != BIB_RECOGNITION_PROCESSOR
        or attempt.status != ProcessingAttempt.Status.SUCCEEDED
        or not attempt.accepted
    ):
        raise ValidationError("A successful accepted bib attempt is required.")

    rows = list(BibReading.objects.select_for_update().filter(photo=photo).order_by("pk"))
    by_id = {row.pk: row for row in rows}
    if set(existing) != set(by_id):
        raise ValidationError("Bib readings changed; reload this photo and try again.")

    desired: dict[int, str | None] = {}
    for row in rows:
        submitted = existing[row.pk]
        if not isinstance(submitted, str):
            raise ValidationError("Bib number must be text.")
        value = submitted.strip()
        desired[row.pk] = _number(value) if value else None
    additions = []
    for raw in added:
        if not isinstance(raw, str):
            raise ValidationError("Bib number must be text.")
        if value := raw.strip():
            additions.append(_number(value))
    if len(additions) != len(set(additions)):
        raise ValidationError("Duplicate bib numbers are not allowed.")

    retained = {row.number: row for row in rows if desired[row.pk] == row.number}
    replacements = [row for row in rows if desired[row.pk] not in (None, row.number)]
    replacement_numbers = [
        desired[row.pk] for row in replacements if desired[row.pk] not in retained
    ]
    if len(replacement_numbers) != len(set(replacement_numbers)):
        raise ValidationError("Duplicate bib numbers are not allowed.")
    if set(additions) & (set(retained) | set(replacement_numbers)):
        raise ValidationError("Duplicate bib numbers are not allowed.")

    changes: list[BibReadingChange] = []
    for row in rows:
        target = desired[row.pk]
        if target != row.number:
            changes.append(
                BibReadingChange(
                    photo=photo,
                    source_attempt=attempt,
                    before_number=row.number,
                    after_number=target,
                    changed_by=changed_by,
                )
            )
    for number in additions:
        changes.append(
            BibReadingChange(
                photo=photo,
                source_attempt=attempt,
                before_number=None,
                after_number=number,
                changed_by=changed_by,
            )
        )

    # Remove changed rows before inserting their replacements so swaps and merges
    # cannot trip the unique (photo, number) constraint midway through the edit.
    changed_rows = [(row, row.pk, desired[row.pk]) for row in rows if desired[row.pk] != row.number]
    for row, _, _ in changed_rows:
        row.delete()
    for row in replacements:
        original_id = next(row_id for changed_row, row_id, _ in changed_rows if changed_row is row)
        target = desired[original_id]
        if target not in retained:
            row.pk = original_id
            row.number = target
            row.save(force_insert=True)
    for number in additions:
        BibReading.objects.create(
            photo=photo,
            source_attempt=attempt,
            number=number,
            evidence={"source": "manual"},
        )
    BibReadingChange.objects.bulk_create(changes)
    return list(BibReading.objects.filter(photo=photo).order_by("number", "pk"))
