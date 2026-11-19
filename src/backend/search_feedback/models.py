from django.db import models


class SearchFeedback(models.Model):  # noqa: DJ008
    class SearchType(models.TextChoices):
        SELFIE = "selfie", "Selfie"
        GALLERY = "gallery", "Gallery face"
        BIB = "bib", "Bib"

    class Value(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"

    id = models.BigAutoField(primary_key=True)
    submission_id = models.UUIDField()
    event_id = models.PositiveBigIntegerField()
    photo_id = models.CharField(max_length=32)
    search_type = models.CharField(max_length=7, choices=SearchType)
    value = models.CharField(max_length=7, choices=Value)
    search_context = models.JSONField()
    legacy_attachment_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
