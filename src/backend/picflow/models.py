from pathlib import Path
from uuid import uuid4

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
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot be earlier than start date."})


class Photo(models.Model):
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

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["uploaded_by"], name="picflow_photo_uploaded_by_idx"),
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
            )
        ]

    def __str__(self) -> str:
        return self.id
