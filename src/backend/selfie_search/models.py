import json
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from picflow.models import Event, Photo
from processing.models import FaceEmbedding, PhotoFaceDetection

JSON_MAX_BYTES = 16_384


def validate_bounded_json(value: object) -> None:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(serialized.encode()) > JSON_MAX_BYTES:
        raise ValidationError(f"JSON payload must not exceed {JSON_MAX_BYTES} bytes.")


class SelfieSearch(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        CLEANUP_PENDING = "cleanup_pending", "Cleanup pending"
        READY = "ready", "Ready"
        NO_FACE = "no_face", "No face"
        MULTIPLE_FACES = "multiple_faces", "Multiple faces"
        QUALITY_REJECTED = "quality_rejected", "Quality rejected"
        SEARCH_UNAVAILABLE = "search_unavailable", "Search unavailable"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="selfie_searches")
    public_token_digest = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=24, choices=Status, default=Status.QUEUED)
    temporary_object_key = models.CharField(max_length=255)
    temporary_object_etag = models.CharField(max_length=128, blank=True, default="")
    configuration = models.JSONField(default=dict, validators=[validate_bounded_json])
    configuration_hash = models.CharField(max_length=64, blank=True, default="")
    eligible_photo_count = models.PositiveIntegerField(default=0)
    eligible_face_count = models.PositiveIntegerField(default=0)
    matched_photo_count = models.PositiveIntegerField(default=0)
    failure_code = models.CharField(max_length=64, blank=True, default="")
    intended_terminal_status = models.CharField(max_length=24, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    state_changed_at = models.DateTimeField(default=timezone.now)
    terminal_at = models.DateTimeField(null=True, blank=True)
    cleanup_confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=(
                        "queued",
                        "processing",
                        "cleanup_pending",
                        "ready",
                        "no_face",
                        "multiple_faces",
                        "quality_rejected",
                        "search_unavailable",
                        "failed",
                    )
                ),
                name="selfie_search_status_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    intended_terminal_status__in=(
                        "",
                        "ready",
                        "no_face",
                        "multiple_faces",
                        "quality_rejected",
                        "search_unavailable",
                        "failed",
                    )
                ),
                name="selfie_search_intended_status_chk",
            ),
        ]
        indexes = [models.Index(fields=["event", "status"], name="selfie_search_event_status_idx")]


class SelfieSearchCandidate(models.Model):  # noqa: DJ008
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    search = models.ForeignKey(SelfieSearch, on_delete=models.PROTECT, related_name="candidates")
    embedding = models.ForeignKey(
        FaceEmbedding, on_delete=models.PROTECT, related_name="selfie_candidates"
    )
    photo = models.ForeignKey(Photo, on_delete=models.PROTECT, related_name="selfie_candidates")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("search", "embedding"), name="selfie_candidate_embedding_uniq"
            )
        ]

    def save(self, *args, **kwargs) -> None:
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Selfie search candidates are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> None:
        raise ValidationError("Selfie search candidates are append-only.")

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.search_id and self.photo_id and self.search.event_id != self.photo.event_id:
            errors["photo"] = "The candidate photo must belong to the search event."
        if self.search_id and self.embedding_id:
            embedding_photo_id = self.embedding.detection.attempt.photo_id
            embedding_event_id = self.embedding.detection.attempt.event_id
            if embedding_event_id != self.search.event_id:
                errors["embedding"] = "The candidate embedding must belong to the search event."
            if self.photo_id and embedding_photo_id != self.photo_id:
                errors["embedding"] = "The candidate embedding must belong to the candidate photo."
        if errors:
            raise ValidationError(errors)


class SelfieSearchJob(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        RETRY_WAIT = "retry_wait", "Retry wait"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    search = models.OneToOneField(SelfieSearch, on_delete=models.PROTECT, related_name="job")
    status = models.CharField(max_length=16, choices=Status, default=Status.QUEUED)
    configuration = models.JSONField(default=dict, validators=[validate_bounded_json])
    available_at = models.DateTimeField(default=timezone.now)
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=("queued", "processing", "retry_wait", "succeeded", "failed")
                ),
                name="selfie_job_status_chk",
            )
        ]
        indexes = [models.Index(fields=["status", "available_at"], name="selfie_job_claim_idx")]


class SelfieSearchAttempt(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"
        STALE = "stale", "Stale"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    job = models.ForeignKey(SelfieSearchJob, on_delete=models.PROTECT, related_name="attempts")
    status = models.CharField(max_length=16, choices=Status, default=Status.IN_PROGRESS)
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    terminal_at = models.DateTimeField(null=True, blank=True)
    result_hash = models.CharField(max_length=64, blank=True, default="")
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_detail = models.CharField(max_length=512, blank=True, default="")
    download_duration_ms = models.PositiveIntegerField(null=True, blank=True)
    compute_duration_ms = models.PositiveIntegerField(null=True, blank=True)
    total_duration_ms = models.PositiveIntegerField(null=True, blank=True)
    worker_started_at = models.DateTimeField(null=True, blank=True)
    worker_finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=("in_progress", "succeeded", "failed", "expired", "stale")
                ),
                name="selfie_attempt_status_chk",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="in_progress", terminal_at__isnull=True)
                    | models.Q(
                        status__in=("succeeded", "failed", "expired", "stale"),
                        terminal_at__isnull=False,
                    )
                ),
                name="selfie_attempt_terminal_at_chk",
            ),
        ]
        indexes = [
            models.Index(fields=["job", "status"], name="selfie_attempt_job_status_idx"),
            models.Index(fields=["lease_expires_at"], name="selfie_attempt_lease_idx"),
        ]


class SelfieSearchResult(models.Model):  # noqa: DJ008
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    search = models.ForeignKey(SelfieSearch, on_delete=models.PROTECT, related_name="results")
    photo = models.ForeignKey(Photo, on_delete=models.PROTECT, related_name="selfie_results")
    detection = models.ForeignKey(
        PhotoFaceDetection, on_delete=models.PROTECT, related_name="selfie_results"
    )
    rank = models.PositiveIntegerField()
    cosine_distance = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("search", "photo"), name="selfie_result_photo_uniq"),
            models.UniqueConstraint(fields=("search", "rank"), name="selfie_result_rank_uniq"),
        ]
        indexes = [models.Index(fields=["search", "rank"], name="selfie_result_search_rank_idx")]

    def save(self, *args, **kwargs) -> None:
        if self.pk and self.__class__.objects.filter(pk=self.pk, search__status="ready").exists():
            raise ValidationError("Ready selfie search results are immutable.")
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.search_id and self.photo_id and self.search.event_id != self.photo.event_id:
            errors["photo"] = "The result photo must belong to the search event."
        if self.photo_id and self.detection_id and self.detection.attempt.photo_id != self.photo_id:
            errors["detection"] = "The result detection must belong to the result photo."
        if self.search_id and self.detection_id:
            if self.detection.attempt.event_id != self.search.event_id:
                errors["detection"] = "The result detection must belong to the search event."
        if errors:
            raise ValidationError(errors)
