from django.db import models
from django.db.models.fields.json import KeyTextTransform


class SearchFeedback(models.Model):  # noqa: DJ008
    class SearchType(models.TextChoices):
        SELFIE = "selfie", "Selfie"
        GALLERY = "gallery", "Gallery face"
        BIB = "bib", "Bib"

    class Value(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"
        UNMARK = "unmark", "Unmark"

    id = models.BigAutoField(primary_key=True)
    submission_id = models.UUIDField()
    event_id = models.PositiveBigIntegerField()
    photo_id = models.CharField(max_length=32)
    search_type = models.CharField(max_length=7, choices=SearchType)
    value = models.CharField(max_length=7, choices=Value)
    search_context = models.JSONField()
    legacy_attachment_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                "event_id",
                KeyTextTransform("search_id", "search_context"),
                "photo_id",
                condition=models.Q(
                    withdrawn_at__isnull=True,
                    search_type__in=("selfie", "gallery"),
                    value="absent",
                ),
                name="search_active_absent_idx",
            ),
        ]
