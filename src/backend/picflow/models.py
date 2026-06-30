from django.db import models


class Event(models.Model):
    name = models.CharField(max_length=255, unique=True)
    date_from = models.DateField()
    date_to = models.DateField()
    location = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["date_from", "name"]

    def __str__(self) -> str:
        return self.name


class Photo(models.Model):
    id = models.CharField(max_length=32, primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="photos")
    src = models.FileField(upload_to="photos/")

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return self.id
