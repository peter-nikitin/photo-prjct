from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def event_cover_key(instance, filename: str) -> str:  # noqa: ARG001
    extension = Path(filename).suffix.lower()
    return f"event-covers/{uuid4()}{extension}"


class EventQuerySet(models.QuerySet):
    def published(self):
        return self.filter(publication_status=Event.PublicationStatus.PUBLISHED)


class Event(models.Model):
    class AccessType(models.TextChoices):
        FREE = "free", "Free"
        PAID = "paid", "Paid"

    class PublicationStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True, allow_unicode=True)
    start_date = models.DateField()
    end_date = models.DateField()
    city = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    cover = models.ImageField(upload_to=event_cover_key, blank=True)
    access_type = models.CharField(
        max_length=8,
        choices=AccessType,
        default=AccessType.FREE,
        db_default=AccessType.FREE,
    )
    publication_status = models.CharField(
        max_length=12,
        choices=PublicationStatus,
        default=PublicationStatus.DRAFT,
        db_default=PublicationStatus.DRAFT,
    )
    timezone_name = models.CharField(max_length=255, null=True, blank=True)  # noqa: DJ001

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ["start_date", "name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="event_end_date_gte_start_date",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.start_date and self.end_date and self.end_date < self.start_date:
            errors["end_date"] = "End date cannot be earlier than start date."
        if self.timezone_name:
            try:
                ZoneInfo(self.timezone_name)
            except (ValueError, ZoneInfoNotFoundError):
                errors["timezone_name"] = "Timezone must be a valid IANA timezone identifier."
        elif self.publication_status == self.PublicationStatus.PUBLISHED:
            errors["timezone_name"] = "Timezone is required for published events."
        if errors:
            raise ValidationError(errors)


class Photo(models.Model):
    class ProcessingGeneration(models.TextChoices):
        LEGACY_ORIGINAL_V1 = "legacy_original_v1", "Legacy original v1"
        PREVIEW_FIRST_V1 = "preview_first_v1", "Preview first v1"

    class GalleryMediaPolicy(models.TextChoices):
        LEGACY_ORIGINAL_ALLOWED = "legacy_original_allowed", "Legacy original allowed"
        PREVIEW_REQUIRED = "preview_required", "Preview required"

    id = models.CharField(max_length=32, primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="photos")
    src = models.FileField(upload_to="photos/", blank=True, default="")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        db_index=False,
        related_name="uploaded_photos",
    )
    original_key = models.CharField(max_length=255, unique=True, null=True, blank=True)
    original_filename = models.CharField(  # noqa: DJ001
        max_length=255, null=True, blank=True
    )
    original_size = models.BigIntegerField(null=True, blank=True)
    original_content_type = models.CharField(  # noqa: DJ001
        max_length=100, null=True, blank=True
    )
    uploaded_at = models.DateTimeField(null=True, blank=True)
    processing_generation = models.CharField(
        max_length=32,
        choices=ProcessingGeneration,
        default=ProcessingGeneration.LEGACY_ORIGINAL_V1,
        db_default=ProcessingGeneration.LEGACY_ORIGINAL_V1,
    )
    gallery_media_policy = models.CharField(
        max_length=32,
        choices=GalleryMediaPolicy,
        default=GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED,
        db_default=GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED,
    )
    capture_time = models.DateTimeField(null=True, blank=True, editable=False)
    capture_time_source_attempt = models.ForeignKey(
        "processing.ProcessingAttempt",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name="+",
    )

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["uploaded_by"], name="picflow_photo_uploaded_by_idx"),
            models.Index(fields=["event", "capture_time"], name="picflow_photo_event_time_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        src__gt="",
                        uploaded_by__isnull=True,
                        original_key__isnull=True,
                        original_filename__isnull=True,
                        original_size__isnull=True,
                        original_content_type__isnull=True,
                        uploaded_at__isnull=True,
                    )
                    | models.Q(
                        src="",
                        uploaded_by__isnull=False,
                        original_key__isnull=False,
                        original_filename__isnull=False,
                        original_size__isnull=False,
                        original_content_type__isnull=False,
                        uploaded_at__isnull=False,
                    )
                ),
                name="picflow_photo_legacy_or_private_chk",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        processing_generation="legacy_original_v1",
                        gallery_media_policy="legacy_original_allowed",
                    )
                    | models.Q(
                        processing_generation="preview_first_v1",
                        gallery_media_policy="preview_required",
                    )
                ),
                name="picflow_photo_processing_policy_pair_chk",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(capture_time__isnull=True, capture_time_source_attempt__isnull=True)
                    | models.Q(
                        capture_time__isnull=False, capture_time_source_attempt__isnull=False
                    )
                ),
                name="picflow_photo_capture_time_pair_chk",
            ),
        ]

    def __str__(self) -> str:
        return self.id

    def clean(self) -> None:
        super().clean()
        valid_pairs = {
            (
                self.ProcessingGeneration.LEGACY_ORIGINAL_V1,
                self.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED,
            ),
            (
                self.ProcessingGeneration.PREVIEW_FIRST_V1,
                self.GalleryMediaPolicy.PREVIEW_REQUIRED,
            ),
        }
        if (self.processing_generation, self.gallery_media_policy) not in valid_pairs:
            raise ValidationError(
                {
                    "gallery_media_policy": (
                        "The gallery media policy must match the processing generation."
                    )
                }
            )
