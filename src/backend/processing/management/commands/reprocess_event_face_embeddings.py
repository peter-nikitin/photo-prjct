import json

from django.core.management.base import BaseCommand, CommandError
from picflow.models import Event

from processing.services.enrollment import (
    FACE_EMBEDDING_QUALITY_APPROVAL,
    FaceEmbeddingGenerationApproval,
    enroll_event_face_embedding_candidate_reprocessing,
    enroll_local_adaface_reprocessing,
    validate_face_embedding_candidate_enrollment,
    validate_local_adaface_enrollment,
)
from processing.services.face_quality import (
    candidate_face_embedding_status,
    local_adaface_face_embedding_status,
)


class Command(BaseCommand):
    help = "Dry-run or enroll the reviewed preview-backed face-embedding v4 event cohort."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--local-adaface", action="store_true")
        parser.add_argument("--event-slug", default="")
        parser.add_argument("--manifest-sha256", default="")

    def handle(self, *args, **options) -> None:
        if options["local_adaface"]:
            self._handle_local_adaface(options)
            return
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

    def _handle_local_adaface(self, options) -> None:
        event_slug = options["event_slug"]
        manifest_sha256 = options["manifest_sha256"]
        if not isinstance(event_slug, str) or not event_slug:
            raise CommandError("--event-slug is required for local AdaFace")
        if not isinstance(manifest_sha256, str) or not manifest_sha256:
            raise CommandError("--manifest-sha256 is required for local AdaFace")
        try:
            event = Event.objects.get(slug=event_slug)
        except Event.DoesNotExist as error:
            raise CommandError("local AdaFace event does not exist") from error
        try:
            validate_local_adaface_enrollment(event, manifest_sha256=manifest_sha256)
            enrollment = (
                enroll_local_adaface_reprocessing(event, manifest_sha256=manifest_sha256)
                if options["apply"]
                else None
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        report: dict[str, object] = {
            "counts": local_adaface_face_embedding_status(event),
            "event_slug": event.slug,
            "manifest_sha256": manifest_sha256,
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
