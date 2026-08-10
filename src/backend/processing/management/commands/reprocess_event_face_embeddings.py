import json

from django.core.management.base import BaseCommand, CommandError
from picflow.models import Event

from processing.services.enrollment import (
    FACE_EMBEDDING_QUALITY_APPROVAL,
    FaceEmbeddingGenerationApproval,
    enroll_event_face_embedding_candidate_reprocessing,
    validate_face_embedding_candidate_enrollment,
)
from processing.services.face_quality import candidate_face_embedding_status


class Command(BaseCommand):
    help = "Dry-run or enroll the reviewed preview-backed face-embedding v4 event cohort."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options) -> None:
        approval = FACE_EMBEDDING_QUALITY_APPROVAL
        # Keep the command fail-closed if the tracked approval is ever removed.
        if approval is None:  # pragma: no cover
            raise CommandError("candidate approval is unavailable")
        try:
            event = Event.objects.get(slug=approval.event_slug)
        except Event.DoesNotExist as error:
            raise CommandError("approved event does not exist") from error
        try:
            validate_face_embedding_candidate_enrollment(event, approval=approval)
            enrollment = (
                enroll_event_face_embedding_candidate_reprocessing(event, approval=approval)
                if options["apply"]
                else None
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        report: dict[str, object] = {
            "approval": _approval_identity(approval),
            "counts": candidate_face_embedding_status(event),
            "event_slug": event.slug,
            "mode": "apply" if options["apply"] else "dry_run",
        }
        if enrollment is not None:
            report["enrollment"] = {
                "created_job_count": enrollment.created_job_count,
                "existing_job_count": enrollment.existing_job_count,
                "photo_count": enrollment.photo_count,
                "run_count": enrollment.run_count,
            }
        self.stdout.write(json.dumps(report, separators=(",", ":"), sort_keys=True))


def _approval_identity(approval: FaceEmbeddingGenerationApproval) -> dict[str, object]:
    return {
        "comparison_manifest_hash": approval.comparison_manifest_hash,
        "configuration_hash": approval.configuration_hash,
        "accepted_preview_cohort_hash": approval.accepted_preview_cohort_hash,
        "accepted_preview_crosswalk_hash": approval.accepted_preview_crosswalk_hash,
        "event_slug": approval.event_slug,
        "local_preview_projection_hash": approval.local_preview_projection_hash,
        "preview_manifest_hash": approval.preview_manifest_hash,
        "sface_model_hash": approval.sface_model_hash,
        "yunet_model_hash": approval.yunet_model_hash,
    }
