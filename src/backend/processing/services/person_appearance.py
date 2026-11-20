"""Enroll and persist appearance attempts for the current accepted clean preview."""

from typing import Any

from django.db import transaction
from feature_flags.registry import PERSON_APPEARANCE_PROCESSING
from feature_flags.services import is_enabled_for_background_task
from picflow.models import Event, Photo

from processing.appearance import APPEARANCE_CONFIGURATION
from processing.contracts import PERSON_APPEARANCE_CONTRACT
from processing.models import (
    PersonAppearanceDetection,
    PhotoDerivative,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    _accepted_preview,
    _derivative_fingerprint,
    request_processor,
)
from processing.services.previews import preview_final_key


def request_person_appearance(
    photo_id: str, preview_key: str, *, staff_rehearsal: bool = False
) -> bool:
    if not is_enabled_for_background_task(
        PERSON_APPEARANCE_PROCESSING, staff_eligible=staff_rehearsal
    ):
        return False
    with transaction.atomic():
        photo = Photo.objects.get(pk=photo_id)
        event = Event.objects.select_for_update().get(pk=photo.event_id)
        preview = _accepted_preview(photo)
        if preview is None or preview.final_key != preview_key:
            return False
        contract = PERSON_APPEARANCE_CONTRACT
        request_processor(
            photo,
            event=event,
            processor_type=contract.processor_type,
            contract_version=contract.contract_version,
            processor_version=contract.processor_version,
            configuration=APPEARANCE_CONFIGURATION,
            input_fingerprint=_derivative_fingerprint(preview),
            replace_terminal_generation=True,
        )
        return True


def current_appearance_input(job: ProcessingJob | ProcessingAttempt) -> PhotoDerivative | None:
    preview = _accepted_preview(job.photo)
    if (
        preview is None
        or job.input_fingerprint != _derivative_fingerprint(preview)
        or preview.final_key
        != preview_final_key(
            photo_id=preview.photo_id, attempt_id=preview.accepted_attempt_id, sha256=preview.sha256
        )
    ):
        return None
    return preview


def persist_appearance(attempt: ProcessingAttempt, result: dict[str, Any]) -> None:
    PersonAppearanceDetection.objects.bulk_create(
        [
            PersonAppearanceDetection(
                attempt=attempt,
                ordinal=ordinal,
                bbox=row["bbox"],
                head=row["head"],
                confidence=row["confidence"],
                reason=row["reason"],
                reid=row.get("reid"),
                color=row.get("color"),
                helmet=row.get("helmet"),
            )
            for ordinal, row in enumerate(result["people"])
        ]
    )
