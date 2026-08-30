"""Read-only exporter intended only for the approved staging web container."""

from __future__ import annotations

import argparse
import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

from .snapshot import SnapshotRecord, export_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the approved private detector snapshot")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bootstrap_django()
    export_snapshot(_records(), args.output, expected_count=40)
    return 0


def bootstrap_django() -> None:
    """Apply the repository's canonical Django bootstrap before importing ORM-backed modules."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()


def _records() -> Iterator[SnapshotRecord]:
    """Fetch only the allowed feedback fields and bytes; never emit storage keys or contacts."""
    from selfie_search.models import SelfieSearchFeedback, SelfieSearchFeedbackLabel
    from selfie_search.storage import FeedbackSelfieStorage

    storage = FeedbackSelfieStorage()
    feedbacks = (
        SelfieSearchFeedback.objects.select_related("search")
        .prefetch_related("labels__result__direct_evidence")
        .order_by("pk")
    )
    for feedback in feedbacks:
        cohort = _cohort(feedback)
        if cohort is None:
            continue
        inspected = storage.inspect(key=feedback.object_key)
        response = storage._client.get_object(Bucket=storage._bucket, Key=feedback.object_key)
        content = response["Body"].read()
        if not metadata_matches(
            database_size=feedback.object_size,
            head_size=inspected.size,
            body_size=len(content),
            database_type=feedback.object_content_type,
            head_type=inspected.content_type,
        ):
            raise ValueError("feedback object differs from database metadata")
        labels = list(feedback.labels.all())
        direct_distances = [
            label.result.direct_evidence.cosine_distance
            for label in labels
            if hasattr(label.result, "direct_evidence")
        ]
        yield SnapshotRecord(
            source_id=str(feedback.pk),
            cohort=cohort,
            source_status=feedback.source_status,
            source_counts={
                "matched_photo_count": feedback.source_matched_photo_count,
                "visible_result_count": feedback.source_visible_result_count,
            },
            diagnostics={
                "configuration_hash": feedback.search.configuration_hash,
                "eligible_face_count": feedback.search.eligible_face_count,
                "failure_code": feedback.search.failure_code,
            },
            result_label=_result_label(labels, SelfieSearchFeedbackLabel),
            direct_cosine_distance=min(direct_distances, default=None),
            media_type=feedback.object_content_type,
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
        )


def _cohort(feedback: object) -> str | None:
    from selfie_search.models import SelfieSearchFeedback

    if feedback.source_status == "no_face":
        return "no_face"
    if feedback.source_status == "multiple_faces":
        return "multiple_faces"
    if feedback.variant == SelfieSearchFeedback.Variant.RESULT_LABELS:
        return "successful_control"
    if feedback.source_status == "ready" and feedback.source_visible_result_count == 0:
        return "ready_zero_visible_results"
    return None


def _result_label(labels: list[object], label_model: object) -> str | None:
    if not labels:
        return None
    present = sum(label.value == label_model.Value.PRESENT for label in labels)
    absent = sum(label.value == label_model.Value.ABSENT for label in labels)
    return f"present:{present};absent:{absent}"


def metadata_matches(
    *,
    database_size: object,
    head_size: object,
    body_size: object,
    database_type: object,
    head_type: object,
) -> bool:
    return (
        isinstance(database_size, int)
        and not isinstance(database_size, bool)
        and database_size > 0
        and database_size == head_size == body_size
        and database_type in {"image/jpeg", "image/png"}
        and database_type == head_type
    )


if __name__ == "__main__":
    raise SystemExit(main())
