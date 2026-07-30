import json
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from picflow.models import Event, Photo

JSON_MAX_BYTES = 16_384
# Worst-case report rows contain JSON-escaped control characters (six bytes each).  The 256 KiB
# report-only ceiling leaves a safety margin over the configured cohort upper-bound calculation.
REPORT_JSON_MAX_BYTES = 262_144
CAPTURE_METADATA_PROCESSOR = "capture_metadata"
FACE_EMBEDDING_PROCESSOR = "face_embedding"
GENERATE_PREVIEW_PROCESSOR = "generate_preview"
_TERMINAL_ATTEMPT_STATUSES = ("succeeded", "failed", "expired", "stale")


def validate_bounded_json(value: object) -> None:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(serialized.encode()) > JSON_MAX_BYTES:
        raise ValidationError(f"JSON payload must not exceed {JSON_MAX_BYTES} bytes.")


def validate_bounded_report_json(value: object) -> None:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(serialized.encode()) > REPORT_JSON_MAX_BYTES:
        raise ValidationError(f"Report payload must not exceed {REPORT_JSON_MAX_BYTES} bytes.")


class EventProcessingRun(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        COLLECTING = "collecting", "Collecting"
        SEALED = "sealed", "Sealed"
        CLOSED = "closed", "Closed"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="processing_runs")
    contract_version = models.PositiveSmallIntegerField()
    processor_type = models.CharField(max_length=64)
    processor_version = models.PositiveSmallIntegerField()
    configuration = models.JSONField(default=dict, validators=[validate_bounded_json])
    configuration_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status, default=Status.COLLECTING)
    created_at = models.DateTimeField(auto_now_add=True)
    sealed_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    report = models.JSONField(default=dict, validators=[validate_bounded_report_json])

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=("collecting", "sealed", "closed")),
                name="proc_run_status_chk",
            ),
        ]
        indexes = [
            models.Index(
                fields=["event", "status", "processor_type"], name="proc_run_event_status_idx"
            ),
        ]


class ProcessingJob(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        RETRY_WAIT = "retry_wait", "Retry wait"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="processing_jobs")
    run = models.ForeignKey(EventProcessingRun, on_delete=models.PROTECT, related_name="jobs")
    photo = models.ForeignKey(Photo, on_delete=models.PROTECT, related_name="processing_jobs")
    contract_version = models.PositiveSmallIntegerField()
    processor_type = models.CharField(max_length=64)
    processor_version = models.PositiveSmallIntegerField()
    configuration = models.JSONField(default=dict, validators=[validate_bounded_json])
    configuration_hash = models.CharField(max_length=64)
    input_fingerprint = models.JSONField(default=dict, validators=[validate_bounded_json])
    status = models.CharField(max_length=16, choices=Status, default=Status.QUEUED)
    available_at = models.DateTimeField(default=timezone.now)
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=(
                        "queued",
                        "processing",
                        "retry_wait",
                        "succeeded",
                        "failed",
                        "cancelled",
                    )
                ),
                name="proc_job_status_chk",
            ),
            models.UniqueConstraint(
                fields=(
                    "run",
                    "photo",
                    "contract_version",
                    "processor_type",
                    "processor_version",
                    "configuration_hash",
                ),
                name="proc_job_exact_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "available_at"], name="proc_job_claim_idx"),
            models.Index(
                fields=["contract_version", "processor_type", "processor_version", "status"],
                name="proc_job_contract_claim_idx",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.run_id and self.event_id and self.run.event_id != self.event_id:
            errors["run"] = "The run must belong to the job event."
        if self.photo_id and self.event_id and self.photo.event_id != self.event_id:
            errors["photo"] = "The photo must belong to the job event."
        if self.run_id and (
            self.run.contract_version != self.contract_version
            or self.run.processor_type != self.processor_type
            or self.run.processor_version != self.processor_version
            or self.run.configuration != self.configuration
            or self.run.configuration_hash != self.configuration_hash
        ):
            errors["run"] = "The job processor identity must match the run."
        if errors:
            raise ValidationError(errors)


class ProcessingAttempt(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"
        STALE = "stale", "Stale"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="processing_attempts")
    run = models.ForeignKey(EventProcessingRun, on_delete=models.PROTECT, related_name="attempts")
    job = models.ForeignKey(ProcessingJob, on_delete=models.PROTECT, related_name="attempts")
    photo = models.ForeignKey(Photo, on_delete=models.PROTECT, related_name="processing_attempts")
    contract_version = models.PositiveSmallIntegerField()
    processor_type = models.CharField(max_length=64)
    processor_version = models.PositiveSmallIntegerField()
    configuration = models.JSONField(default=dict, validators=[validate_bounded_json])
    input_fingerprint = models.JSONField(default=dict, validators=[validate_bounded_json])
    worker_build = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status, default=Status.IN_PROGRESS)
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    terminal_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(default=dict, validators=[validate_bounded_json])
    result_hash = models.CharField(max_length=64, blank=True, default="")
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_detail = models.CharField(max_length=512, blank=True, default="")
    download_duration_ms = models.PositiveIntegerField(null=True, blank=True)
    compute_duration_ms = models.PositiveIntegerField(null=True, blank=True)
    total_duration_ms = models.PositiveIntegerField(null=True, blank=True)
    worker_started_at = models.DateTimeField(null=True, blank=True)
    worker_finished_at = models.DateTimeField(null=True, blank=True)
    accepted = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=("in_progress", "succeeded", "failed", "expired", "stale")
                ),
                name="proc_attempt_status_chk",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="in_progress", terminal_at__isnull=True)
                    | models.Q(
                        status__in=("succeeded", "failed", "expired", "stale"),
                        terminal_at__isnull=False,
                    )
                ),
                name="proc_attempt_terminal_timestamp_chk",
            ),
        ]
        indexes = [
            models.Index(fields=["job", "status"], name="proc_attempt_job_status_idx"),
            models.Index(fields=["lease_expires_at"], name="proc_attempt_lease_idx"),
        ]

    def save(self, *args, **kwargs) -> None:
        if (
            self.pk
            and self.__class__.objects.filter(pk=self.pk, terminal_at__isnull=False).exists()
        ):
            raise ValidationError("Terminal processing attempts are immutable.")
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.run_id and self.event_id and self.run.event_id != self.event_id:
            errors["run"] = "The run must belong to the attempt event."
        if self.job_id and self.event_id and self.job.event_id != self.event_id:
            errors["job"] = "The job must belong to the attempt event."
        if self.photo_id and self.event_id and self.photo.event_id != self.event_id:
            errors["photo"] = "The photo must belong to the attempt event."
        if self.job_id and (
            self.job.contract_version != self.contract_version
            or self.job.processor_type != self.processor_type
            or self.job.processor_version != self.processor_version
            or self.job.configuration != self.configuration
            or self.job.input_fingerprint != self.input_fingerprint
        ):
            errors["job"] = "The attempt processor identity must match the job."
        if errors:
            raise ValidationError(errors)


class PhotoDerivative(models.Model):  # noqa: DJ008
    photo = models.ForeignKey(Photo, on_delete=models.PROTECT, related_name="derivatives")
    variant = models.CharField(max_length=64)
    final_key = models.CharField(max_length=255, unique=True)
    byte_size = models.PositiveBigIntegerField()
    content_type = models.CharField(max_length=100)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    oriented_source_width = models.PositiveIntegerField()
    oriented_source_height = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    accepted_attempt = models.ForeignKey(
        ProcessingAttempt,
        on_delete=models.PROTECT,
        related_name="published_derivatives",
    )
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(content_type="image/jpeg"),
                name="proc_photo_derivative_jpeg_chk",
            ),
            models.UniqueConstraint(
                fields=("photo", "variant"),
                name="proc_photo_derivative_photo_variant_uniq",
            ),
        ]

    def save(self, *args, **kwargs) -> None:
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Published photo derivatives are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> None:
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Published photo derivatives are immutable.")
        super().delete(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if not self.accepted_attempt_id:
            return
        attempt = self.accepted_attempt
        if (
            attempt.photo_id != self.photo_id
            or attempt.processor_type != GENERATE_PREVIEW_PROCESSOR
            or attempt.status != ProcessingAttempt.Status.SUCCEEDED
            or not attempt.accepted
        ):
            raise ValidationError(
                {
                    "accepted_attempt": (
                        "The accepted attempt must be an accepted successful "
                        "preview for this photo."
                    )
                }
            )


class FaceProcessingAttemptArtifact(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    attempt = models.ForeignKey(
        ProcessingAttempt,
        on_delete=models.PROTECT,
        related_name="face_artifacts",
    )
    status = models.CharField(max_length=16, choices=Status, default=Status.COMPLETE)
    feature_payload = models.JSONField(default=dict, validators=[validate_bounded_json])
    quality_payload = models.JSONField(default=dict, validators=[validate_bounded_json])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=("complete", "failed"),
                ),
                name="proc_face_artifact_status_chk",
            ),
            models.UniqueConstraint(fields=("attempt",), name="proc_face_artifact_attempt_uniq"),
        ]
        indexes = [
            models.Index(fields=["attempt"], name="proc_face_artifact_attempt_idx"),
            models.Index(fields=["status"], name="proc_face_artifact_status_idx"),
        ]

    def save(self, *args, **kwargs) -> None:
        if (
            self.pk
            and self.__class__.objects.filter(pk=self.pk)
            .filter(attempt__status__in=_TERMINAL_ATTEMPT_STATUSES)
            .exists()
        ):
            raise ValidationError("Face attempt artifacts are immutable.")
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.attempt_id and self.attempt.status not in _TERMINAL_ATTEMPT_STATUSES:
            errors["attempt"] = "Face attempt artifacts are only recorded for terminal attempts."
        if self.pk and self.attempt_id:
            previous = (
                self.__class__.objects.filter(pk=self.pk)
                .values_list("attempt_id", flat=True)
                .first()
            )
            if previous and previous != self.attempt_id:
                errors["attempt"] = "Face attempt identity is immutable."
        if errors:
            raise ValidationError(errors)


class PhotoFaceDetection(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        DETECTED = "detected", "Detected"
        KEPT = "kept", "Kept"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    artifact = models.ForeignKey(
        FaceProcessingAttemptArtifact,
        on_delete=models.PROTECT,
        related_name="detections",
    )
    attempt = models.ForeignKey(
        ProcessingAttempt,
        on_delete=models.PROTECT,
        related_name="face_detections",
    )
    face_index = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=16, choices=Status, default=Status.DETECTED)
    geometry = models.JSONField(default=dict, validators=[validate_bounded_json])
    features = models.JSONField(default=dict, validators=[validate_bounded_json])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=("detected", "kept", "failed")),
                name="proc_photo_face_detection_status_chk",
            ),
            models.UniqueConstraint(
                fields=("attempt", "face_index"),
                name="proc_photo_face_detection_attempt_face_idx_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["attempt", "face_index"], name="proc_face_det_attempt_idx"),
            models.Index(fields=["status"], name="proc_face_detection_status_idx"),
        ]

    def save(self, *args, **kwargs) -> None:
        if (
            self.pk
            and self.__class__.objects.filter(pk=self.pk)
            .filter(attempt__status__in=_TERMINAL_ATTEMPT_STATUSES)
            .exists()
        ):
            raise ValidationError("Face detections are immutable after terminal attempts.")
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.artifact_id and self.attempt_id and self.artifact.attempt_id != self.attempt_id:
            errors["artifact"] = "Face detection must belong to the same attempt artifact."
        if self.attempt_id and self.attempt.status not in _TERMINAL_ATTEMPT_STATUSES:
            errors["attempt"] = "Face detections are only allowed for terminal attempts."
        if self.pk and self.attempt_id:
            previous_attempt = (
                self.__class__.objects.filter(pk=self.pk)
                .values_list("attempt_id", flat=True)
                .first()
            )
            if previous_attempt and previous_attempt != self.attempt_id:
                errors["attempt"] = "Face detection identity is immutable."
        if errors:
            raise ValidationError(errors)


class FaceEmbedding(models.Model):  # noqa: DJ008
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    detection = models.OneToOneField(
        PhotoFaceDetection,
        on_delete=models.PROTECT,
        related_name="embedding",
    )
    model_version = models.CharField(max_length=64, blank=True, default="")
    vector = models.JSONField(default=list, validators=[validate_bounded_json])
    metadata = models.JSONField(default=dict, validators=[validate_bounded_json])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["detection"], name="proc_face_embed_det_idx"),
        ]

    def save(self, *args, **kwargs) -> None:
        if (
            self.pk
            and self.__class__.objects.filter(pk=self.pk)
            .filter(detection__attempt__status__in=_TERMINAL_ATTEMPT_STATUSES)
            .exists()
        ):
            raise ValidationError("Face embeddings are immutable after terminal attempts.")
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.detection_id and self.detection.attempt.status not in _TERMINAL_ATTEMPT_STATUSES:
            errors["detection"] = "Face embeddings are only allowed for terminal attempts."
        if errors:
            raise ValidationError(errors)


class ProcessingLateReceipt(models.Model):  # noqa: DJ008
    """Immutable worker receipt that arrived after an attempt lost its lease."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    attempt = models.OneToOneField(
        ProcessingAttempt,
        on_delete=models.PROTECT,
        related_name="late_receipt",
    )
    received_at = models.DateTimeField()
    payload = models.JSONField(default=dict, validators=[validate_bounded_json])
    payload_hash = models.CharField(max_length=64)

    def save(self, *args, **kwargs) -> None:
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Late processing receipts are immutable.")
        super().save(*args, **kwargs)


class ProcessingConflictAudit(models.Model):  # noqa: DJ008
    """Append-only hash-only evidence of a rejected conflicting worker terminal replay."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    attempt = models.ForeignKey(
        ProcessingAttempt, on_delete=models.PROTECT, related_name="conflicts"
    )
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="processing_conflicts")
    job = models.ForeignKey(ProcessingJob, on_delete=models.PROTECT, related_name="conflicts")
    received_at = models.DateTimeField()
    submitted_hash = models.CharField(max_length=64)
    code = models.CharField(max_length=64, default="terminal_conflict")


class PhotoProcessingState(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        NOT_REQUESTED = "not_requested", "Not requested"
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        RETRY_WAIT = "retry_wait", "Retry wait"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    photo = models.ForeignKey(Photo, on_delete=models.PROTECT, related_name="processing_states")
    processor_type = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status, default=Status.NOT_REQUESTED)
    current_run = models.ForeignKey(
        EventProcessingRun,
        on_delete=models.PROTECT,
        related_name="current_states",
        null=True,
        blank=True,
    )
    current_job = models.ForeignKey(
        ProcessingJob,
        on_delete=models.PROTECT,
        related_name="current_states",
        null=True,
        blank=True,
    )
    current_attempt = models.ForeignKey(
        ProcessingAttempt,
        on_delete=models.PROTECT,
        related_name="current_states",
        null=True,
        blank=True,
    )
    accepted_attempt = models.ForeignKey(
        ProcessingAttempt,
        on_delete=models.PROTECT,
        related_name="accepted_states",
        null=True,
        blank=True,
    )
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    queued_at = models.DateTimeField(null=True, blank=True)
    processing_at = models.DateTimeField(null=True, blank=True)
    succeeded_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=(
                        "not_requested",
                        "queued",
                        "processing",
                        "retry_wait",
                        "succeeded",
                        "failed",
                        "cancelled",
                    )
                ),
                name="proc_state_status_chk",
            ),
            models.UniqueConstraint(
                fields=("photo", "processor_type"), name="proc_state_photo_processor_uniq"
            ),
        ]
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="proc_state_claim_idx")]

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.current_run_id and (
            self.current_run.event_id != self.photo.event_id
            or self.current_run.processor_type != self.processor_type
        ):
            errors["current_run"] = "The run must match the photo and processor."
        if self.current_job_id and (
            self.current_job.photo_id != self.photo_id
            or self.current_job.processor_type != self.processor_type
            or (self.current_run_id and self.current_job.run_id != self.current_run_id)
        ):
            errors["current_job"] = "The job must match the state identity."
        for field_name in ("current_attempt", "accepted_attempt"):
            attempt = getattr(self, field_name)
            if attempt is not None and (
                attempt.photo_id != self.photo_id
                or attempt.processor_type != self.processor_type
                or (self.current_job_id and attempt.job_id != self.current_job_id)
                or (self.current_run_id and attempt.run_id != self.current_run_id)
            ):
                errors[field_name] = "The attempt must match the state identity."
        if errors:
            raise ValidationError(errors)
