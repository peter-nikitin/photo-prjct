from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower, Trim
from feature_flags import services as feature_flag_services

PAID_EVENTS_FLAG = "paid-events"


def event_cover_key(instance, filename: str) -> str:  # noqa: ARG001
    extension = Path(filename).suffix.lower()
    return f"event-covers/{uuid4()}{extension}"


class EventQuerySet(models.QuerySet):
    def published(self):
        return self.filter(publication_status=Event.PublicationStatus.PUBLISHED)

    def site_visible_to(self, user):
        if (
            getattr(user, "is_authenticated", False)
            and getattr(user, "is_active", False)
            and getattr(user, "is_staff", False)
        ):
            visible = self.filter(
                publication_status__in=(
                    Event.PublicationStatus.DRAFT,
                    Event.PublicationStatus.PUBLISHED,
                )
            )
        else:
            visible = self.published()
        if feature_flag_services.is_enabled(PAID_EVENTS_FLAG, user):
            return visible
        return visible.exclude(access_type=Event.AccessType.PAID)


class Event(models.Model):
    class FaceSearchGeneration(models.TextChoices):
        SFACE_V3 = "sface_v3", "SFace v3"
        ADAFACE_V5 = "adaface_v5", "AdaFace v5"

    class AccessType(models.TextChoices):
        FREE = "free", "Free"
        PAID = "paid", "Paid"

    class PublicationStatus(models.TextChoices):
        UNAVAILABLE = "unavailable", "Недоступно"
        DRAFT = "draft", "Черновик"
        PUBLISHED = "published", "Опубликовано"

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
    price_per_photo_kopecks = models.PositiveIntegerField(null=True, blank=True)
    publication_status = models.CharField(
        max_length=12,
        choices=PublicationStatus,
        default=PublicationStatus.UNAVAILABLE,
        db_default=PublicationStatus.UNAVAILABLE,
    )
    timezone_name = models.CharField(max_length=255, null=True, blank=True)  # noqa: DJ001
    face_search_generation = models.CharField(
        max_length=16,
        choices=FaceSearchGeneration,
        default=FaceSearchGeneration.ADAFACE_V5,
        db_default=FaceSearchGeneration.ADAFACE_V5,
    )

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ["start_date", "name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="event_end_date_gte_start_date",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(access_type="free", price_per_photo_kopecks__isnull=True)
                    | models.Q(
                        access_type="paid",
                        price_per_photo_kopecks__isnull=False,
                        price_per_photo_kopecks__gt=0,
                    )
                ),
                name="picflow_event_access_price_chk",
            ),
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
        elif self.publication_status in {
            self.PublicationStatus.DRAFT,
            self.PublicationStatus.PUBLISHED,
        }:
            errors["timezone_name"] = f"Timezone is required for {self.publication_status} events."
        if self.access_type == self.AccessType.FREE and self.price_per_photo_kopecks is not None:
            errors["access_type"] = "Free events cannot have a photo price."
        elif self.access_type == self.AccessType.PAID and (
            self.price_per_photo_kopecks is None or self.price_per_photo_kopecks <= 0
        ):
            errors["access_type"] = "Paid events require a positive photo price."
        if errors:
            raise ValidationError(errors)


class EventFolder(models.Model):
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="folders")
    name = models.CharField(max_length=255)

    class Meta:
        ordering = [Lower("name"), "id"]
        constraints = [
            models.UniqueConstraint(
                Lower(Trim("name")),
                "event",
                name="picflow_folder_event_name_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(name=Trim("name")) & ~models.Q(name=""),
                name="picflow_folder_name_trimmed_chk",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        self.name = self.name.strip()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError({"name": "Folder name cannot be empty."})


class Photo(models.Model):
    class ProcessingGeneration(models.TextChoices):
        LEGACY_ORIGINAL_V1 = "legacy_original_v1", "Legacy original v1"
        PREVIEW_FIRST_V1 = "preview_first_v1", "Preview first v1"
        PREVIEW_FIRST_WATERMARKED_V1 = (
            "preview_first_watermarked_v1",
            "Preview first watermarked v1",
        )

    class GalleryMediaPolicy(models.TextChoices):
        LEGACY_ORIGINAL_ALLOWED = "legacy_original_allowed", "Legacy original allowed"
        PREVIEW_REQUIRED = "preview_required", "Preview required"
        WATERMARKED_PREVIEW_REQUIRED = (
            "watermarked_preview_required",
            "Watermarked preview required",
        )

    id = models.CharField(max_length=32, primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="photos")
    folder = models.ForeignKey(
        EventFolder,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="photos",
    )
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
                    | models.Q(
                        processing_generation="preview_first_watermarked_v1",
                        gallery_media_policy="watermarked_preview_required",
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

    def save(self, *args, **kwargs) -> None:
        self._require_paid_order_original_identity_unchanged()
        super().save(*args, **kwargs)

    def _require_paid_order_original_identity_unchanged(self) -> None:
        if self._state.adding:
            return
        persisted = (
            self.__class__.objects.filter(pk=self.pk)
            .values("original_key", "original_content_type")
            .first()
        )
        if persisted is None or not self.order_items.filter(order__status="paid").exists():
            return

        errors = {}
        if persisted["original_key"] != self.original_key:
            errors["original_key"] = (
                "Original key cannot be changed after the photo has a paid order item."
            )
        if persisted["original_content_type"] != self.original_content_type:
            errors["original_content_type"] = (
                "Original content type cannot be changed after the photo has a paid order item."
            )
        if errors:
            raise ValidationError(errors)

    def clean(self) -> None:
        super().clean()
        self._require_paid_order_original_identity_unchanged()
        valid_pairs = {
            (
                self.ProcessingGeneration.LEGACY_ORIGINAL_V1,
                self.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED,
            ),
            (
                self.ProcessingGeneration.PREVIEW_FIRST_V1,
                self.GalleryMediaPolicy.PREVIEW_REQUIRED,
            ),
            (
                self.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
                self.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
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
