from django.db import models


class FeatureFlag(models.Model):
    class State(models.TextChoices):
        OFF = "off", "Off"
        STAFF = "staff", "Staff"
        ON = "on", "On"

    key = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255)
    state = models.CharField(max_length=5, choices=State, default=State.OFF)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("key",)

    def __str__(self) -> str:
        return self.key
