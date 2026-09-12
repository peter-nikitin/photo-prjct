from uuid import uuid4

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from picflow.models import Event, EventFolder, Photo

MAX_POSTGRES_BIGINT = 9_223_372_036_854_775_807


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
    client_last_modified_ms = models.BigIntegerField(null=True, blank=True)
    ambiguous_sha256 = models.CharField(  # noqa: DJ001
        max_length=64,
        null=True,
        blank=True,
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
    folder = models.ForeignKey(
        EventFolder,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="upload_items",
    )
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


class ImportScope(models.Model):  # noqa: DJ008
    """One durable deduplication boundary for a canonical public folder."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="photo_import_scopes",
    )
    event = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name="photo_import_scopes",
    )
    folder = models.ForeignKey(
        EventFolder,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="photo_import_scopes",
    )
    canonical_source_key = models.CharField(max_length=512)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "canonical_source_key", "event", "folder"],
                name="ing_import_scope_uniq",
                nulls_distinct=False,
            )
        ]


class ImportBatch(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        ENUMERATING = "enumerating", "Enumerating"
        TRANSFERRING = "transferring", "Transferring"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Completed with errors"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="photo_import_batches",
    )
    event = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name="photo_import_batches",
    )
    folder = models.ForeignKey(
        EventFolder,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="photo_import_batches",
    )
    scope = models.ForeignKey(
        ImportScope,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="batches",
    )
    submitted_source_key = models.CharField(max_length=512)
    submission_key = models.CharField(max_length=128)
    status = models.CharField(max_length=16, choices=Status, default=Status.QUEUED)
    manifest_complete = models.BooleanField(default=False)
    manifest_attempts = models.PositiveSmallIntegerField(default=0)
    jpeg_count = models.PositiveIntegerField(default=0)
    directory_count = models.PositiveIntegerField(default=0)
    unsupported_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "submission_key"],
                name="ing_import_owner_submission_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(manifest_attempts__gte=0, manifest_attempts__lte=4),
                name="ing_import_manifest_attempts_chk",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"], name="ing_import_batch_claim_idx"),
            models.Index(fields=["owner", "-created_at"], name="ing_import_owner_created_idx"),
        ]


class ImportManifestPage(models.Model):  # noqa: DJ008
    batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.CASCADE,
        related_name="manifest_pages",
    )
    page_number = models.PositiveIntegerField()
    fingerprint = models.CharField(max_length=64)
    entry_count = models.PositiveIntegerField()
    jpeg_count = models.PositiveIntegerField(default=0)
    directory_count = models.PositiveIntegerField(default=0)
    unsupported_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "page_number"],
                name="ing_import_manifest_page_uniq",
            )
        ]


class ImportItem(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CLAIMED = "claimed", "Claimed"
        UPLOADING = "uploading", "Uploading"
        IMPORTED = "imported", "Imported"
        DUPLICATE = "duplicate", "Already imported"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="items")
    source_path = models.CharField(max_length=1024)
    filename = models.CharField(max_length=255)
    byte_size = models.PositiveBigIntegerField(validators=[MinValueValidator(1)])
    source_sha256 = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    source_md5 = models.CharField(max_length=32, null=True, blank=True)  # noqa: DJ001
    source_version = models.CharField(max_length=255, blank=True, default="")
    content_sha256 = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    status = models.CharField(max_length=16, choices=Status, default=Status.PENDING)
    download_attempts = models.PositiveSmallIntegerField(default=0)
    upload_attempts = models.PositiveSmallIntegerField(default=0)
    retry_generation = models.PositiveSmallIntegerField(default=0)
    error_code = models.CharField(max_length=64, blank=True, default="")
    completed_at = models.DateTimeField(null=True, blank=True)
    photo = models.ForeignKey(
        Photo,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="import_items",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "source_path"],
                name="ing_import_item_path_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(byte_size__gte=1),
                name="ing_import_item_size_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(download_attempts__lte=4, upload_attempts__lte=4),
                name="ing_import_item_attempts_chk",
            ),
        ]
        indexes = [models.Index(fields=["batch", "status"], name="ing_import_item_status_idx")]


class ImportAttempt(models.Model):  # noqa: DJ008
    class Kind(models.TextChoices):
        MANIFEST = "manifest", "Manifest"
        FILE = "file", "File"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="attempts")
    item = models.ForeignKey(
        ImportItem,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="attempts",
    )
    kind = models.CharField(max_length=16, choices=Kind)
    status = models.CharField(max_length=16, choices=Status, default=Status.ACTIVE)
    claimed_at = models.DateTimeField()
    heartbeat_at = models.DateTimeField()
    lease_expires_at = models.DateTimeField()
    terminal_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    content_sha256 = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    byte_size = models.PositiveBigIntegerField(null=True, blank=True)
    incoming_key = models.CharField(max_length=255, null=True, blank=True, unique=True)
    final_key = models.CharField(max_length=255, null=True, blank=True, unique=True)
    verified_source_etag = models.CharField(max_length=128, blank=True, default="")
    verified_final_etag = models.CharField(max_length=128, blank=True, default="")
    oriented_width = models.PositiveIntegerField(null=True, blank=True)
    oriented_height = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(kind="manifest", item__isnull=True)
                    | models.Q(kind="file", item__isnull=False)
                ),
                name="ing_import_attempt_item_chk",
            )
        ]
        indexes = [
            models.Index(fields=["status", "lease_expires_at"], name="ing_import_attempt_lease_idx")
        ]


class ImportedContent(models.Model):  # noqa: DJ008
    """A successfully published content identity inside one import scope."""

    scope = models.ForeignKey(ImportScope, on_delete=models.PROTECT, related_name="contents")
    sha256 = models.CharField(max_length=64)
    byte_size = models.PositiveBigIntegerField()
    photo = models.OneToOneField(Photo, on_delete=models.PROTECT, related_name="imported_content")
    source_item = models.OneToOneField(
        ImportItem,
        on_delete=models.PROTECT,
        related_name="published_content",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "sha256", "byte_size"],
                name="ing_import_content_uniq",
            )
        ]
