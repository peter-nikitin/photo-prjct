from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable

from django.core.management.base import BaseCommand, CommandError
from picflow.models import Event, Photo

from processing.models import BIB_RECOGNITION_PROCESSOR, PhotoProcessingState, ProcessingAttempt

MAX_IDENTITIES = 32


class Command(BaseCommand):
    help = "Report bounded privacy-safe bib-processing aggregates for exactly one event."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--event-slug", required=True)

    def handle(self, *args, **options) -> None:
        try:
            event = Event.objects.get(slug=options["event_slug"])
        except Event.DoesNotExist as error:
            raise CommandError("event does not exist") from error
        self.stdout.write(
            json.dumps(
                _build_report(event), ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
        )


def _build_report(event: Event) -> dict[str, object]:
    photos = Photo.objects.filter(event=event)
    applicable = photos.filter(bib_processing_policy=Photo.BibProcessingPolicy.ORIGINAL_V1).count()
    states = list(
        PhotoProcessingState.objects.filter(
            photo__event=event, processor_type=BIB_RECOGNITION_PROCESSOR
        ).select_related("accepted_attempt")
    )
    state_counts = Counter(state.status for state in states)
    attempts = list(
        ProcessingAttempt.objects.filter(
            event=event, processor_type=BIB_RECOGNITION_PROCESSOR
        ).select_related("job")
    )
    attempts_by_job: defaultdict[object, int] = defaultdict(int)
    error_codes: Counter[str] = Counter()
    for attempt in attempts:
        attempts_by_job[attempt.job_id] += 1
        if attempt.error_code:
            error_codes[attempt.error_code] += 1

    current_attempts = [
        state.accepted_attempt
        for state in states
        if state.status == PhotoProcessingState.Status.SUCCEEDED
        and state.accepted_attempt is not None
    ]
    decisions: Counter[str] = Counter()
    zero_candidates = 0
    zero_accepted = 0
    with_accepted = 0
    ocr_durations: list[float] = []
    visual_durations: list[float] = []
    validation_durations: list[float] = []
    for attempt in current_attempts:
        recognition = attempt.result.get("recognition")
        validation = attempt.result.get("validation")
        if not isinstance(recognition, dict) or not isinstance(validation, dict):
            continue
        candidates = recognition.get("candidates")
        candidate_rows = candidates if isinstance(candidates, list) else []
        if not candidate_rows:
            zero_candidates += 1
        decision_rows = validation.get("decisions")
        accepted_count = 0
        if isinstance(decision_rows, list):
            for row in decision_rows:
                if not isinstance(row, dict) or row.get("status") not in {
                    "accepted",
                    "rejected",
                    "uncertain",
                }:
                    continue
                status = str(row["status"])
                decisions[status] += 1
                accepted_count += status == "accepted"
        if accepted_count:
            with_accepted += 1
        else:
            zero_accepted += 1
        ocr = _number(recognition.get("ocr_preparation_ms")) + _number(
            recognition.get("ocr_inference_ms")
        )
        ocr_durations.append(ocr)
        visual_durations.append(
            sum(
                _number(row.get("visual", {}).get("inference_ms"))
                for row in candidate_rows
                if isinstance(row, dict) and isinstance(row.get("visual"), dict)
            )
        )
        validation_durations.append(_number(validation.get("duration_ms")))

    identities = sorted(
        {
            (
                attempt.contract_version,
                attempt.processor_version,
                attempt.job.configuration_hash,
            )
            for attempt in attempts
        }
    )
    total_identity_count = len(identities)
    identities = identities[:MAX_IDENTITIES]
    return {
        "event": {
            "id": event.id,
            "slug": event.slug,
            "photos": photos.count(),
            "applicable_photos": applicable,
            "non_applicable_photos": photos.count() - applicable,
        },
        "states": {status: state_counts[status] for status in PhotoProcessingState.Status.values},
        "attempts": {
            "total": len(attempts),
            "retries": sum(max(count - 1, 0) for count in attempts_by_job.values()),
            "error_codes": dict(sorted(error_codes.items())),
        },
        "successes": {
            "total": len(current_attempts),
            "with_zero_candidates": zero_candidates,
            "with_zero_accepted_numbers": zero_accepted,
            "with_accepted_numbers": with_accepted,
        },
        "candidates": {
            "accepted": decisions["accepted"],
            "rejected": decisions["rejected"],
            "uncertain": decisions["uncertain"],
        },
        "durations_ms": {
            "download": _distribution(attempt.download_duration_ms for attempt in current_attempts),
            "ocr": _distribution(ocr_durations),
            "visual": _distribution(visual_durations),
            "validation": _distribution(validation_durations),
            "total": _distribution(attempt.total_duration_ms for attempt in current_attempts),
        },
        "identities": [
            {
                "contract_version": contract_version,
                "processor_type": BIB_RECOGNITION_PROCESSOR,
                "processor_version": processor_version,
                "configuration_hash": configuration_hash,
            }
            for contract_version, processor_version, configuration_hash in identities
        ],
        "identity_count": total_identity_count,
        "identities_truncated": total_identity_count > MAX_IDENTITIES,
    }


def _number(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return 0.0


def _distribution(values: Iterable[object]) -> dict[str, float | int | None]:
    numbers = sorted(_number(value) for value in values if value is not None)
    if not numbers:
        return {"count": 0, "min": None, "p50": None, "p95": None, "max": None}
    p95_index = max(0, math.ceil(len(numbers) * 0.95) - 1)
    return {
        "count": len(numbers),
        "min": numbers[0],
        "p50": float(statistics.median(numbers)),
        "p95": numbers[p95_index],
        "max": numbers[-1],
    }
