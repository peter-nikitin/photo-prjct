from uuid import uuid4

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from picflow.models import Event, Photo


class UploadBatch(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        CREATED = "created", "Created"
        UPLOADING = "uploading", "Uploading"
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="upload_batches")
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="upload_batches",
    )
    expected_item_count = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(10_000)]
    )
    status = models.CharField(max_length=16, choices=Status, default=Status.CREATED)
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        permissions = [("upload_photos", "Can upload photos")]
        indexes = [
            models.Index(fields=["uploader", "-created_at"], name="ing_batch_uploader_created_idx"),
            models.Index(
                fields=["status", "last_activity_at"], name="ing_batch_status_activity_idx"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(expected_item_count__gte=1, expected_item_count__lte=10_000),
                name="ing_batch_expected_count_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=("created", "uploading", "completed", "partial", "failed")
                ),
                name="ing_batch_status_chk",
            ),
        ]


class UploadItem(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        AUTHORIZED = "authorized", "Authorized"
        UPLOADED = "uploaded", "Uploaded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    batch = models.ForeignKey(UploadBatch, on_delete=models.CASCADE, related_name="items")
    client_item_id = models.UUIDField()
    original_filename = models.CharField(max_length=255)
    declared_content_type = models.CharField(max_length=100)
    expected_size = models.BigIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(52_428_800)]
    )
    incoming_key = models.CharField(max_length=255, unique=True)
    final_key = models.CharField(max_length=255, unique=True)
    verified_source_etag = models.CharField(  # noqa: DJ001
        max_length=128, null=True, blank=True
    )
    status = models.CharField(max_length=16, choices=Status, default=Status.PENDING)
    error_code = models.CharField(max_length=64, blank=True, default="")
    sanitized_error_message = models.CharField(max_length=255, blank=True, default="")
    authorization_expires_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(default=timezone.now)
    upload_attempts = models.PositiveSmallIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)
    photo = models.OneToOneField(
        Photo,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="upload_item",
    )

    class Meta:
        indexes = [
            models.Index(fields=["batch", "status"], name="ing_item_batch_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "client_item_id"], name="ing_item_batch_client_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(declared_content_type="image/jpeg"),
                name="ing_item_jpeg_content_type_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(expected_size__gte=1, expected_size__lte=52_428_800),
                name="ing_item_expected_size_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=("pending", "authorized", "uploaded", "failed")),
                name="ing_item_status_chk",
            ),
        ]
