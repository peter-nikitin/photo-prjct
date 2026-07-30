from django.db.models.signals import post_save
from django.dispatch import receiver
from picflow.models import Photo

from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    FACE_EMBEDDING_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    PhotoProcessingState,
)


@receiver(post_save, sender=Photo)
def create_capture_metadata_state(sender, instance: Photo, created: bool, **kwargs) -> None:  # noqa: ARG001
    if created:
        PhotoProcessingState.objects.get_or_create(
            photo=instance,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            defaults={"status": PhotoProcessingState.Status.NOT_REQUESTED},
        )
        if (
            instance.processing_generation == Photo.ProcessingGeneration.PREVIEW_FIRST_V1
            and instance.gallery_media_policy == Photo.GalleryMediaPolicy.PREVIEW_REQUIRED
        ):
            for processor_type in (GENERATE_PREVIEW_PROCESSOR, FACE_EMBEDDING_PROCESSOR):
                PhotoProcessingState.objects.get_or_create(
                    photo=instance,
                    processor_type=processor_type,
                    defaults={"status": PhotoProcessingState.Status.NOT_REQUESTED},
                )
