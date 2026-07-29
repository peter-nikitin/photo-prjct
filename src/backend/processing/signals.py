from django.db.models.signals import post_save
from django.dispatch import receiver
from picflow.models import Photo

from processing.models import CAPTURE_METADATA_PROCESSOR, PhotoProcessingState


@receiver(post_save, sender=Photo)
def create_capture_metadata_state(sender, instance: Photo, created: bool, **kwargs) -> None:  # noqa: ARG001
    if created:
        PhotoProcessingState.objects.get_or_create(
            photo=instance,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            defaults={"status": PhotoProcessingState.Status.NOT_REQUESTED},
        )
