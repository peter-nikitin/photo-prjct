from datetime import date
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone
from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)

from picflow.models import Event, Photo


def user_with_permissions(username, *, staff=False, permissions=()):
    user = get_user_model().objects.create_user(username=username, is_staff=staff)
    for permission in permissions:
        app, codename = permission.split(".")
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app, codename=codename)
        )
    return user


def event(name="Private race"):
    return Event.objects.create(
        name=name,
        slug=name.lower().replace(" ", "-"),
        start_date=date.today(),
        end_date=date.today(),
        city="Moscow",
    )


def private_photo(event, uploader, **overrides):
    values = {
        "id": uuid4().hex,
        "event": event,
        "uploaded_by": uploader,
        "original_key": f"originals/{uuid4().hex}",
        "original_filename": "private.jpg",
        "original_size": 4,
        "original_content_type": "image/jpeg",
        "uploaded_at": timezone.now(),
        "processing_generation": Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
        "gallery_media_policy": Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
    }
    values.update(overrides)
    return Photo.objects.create(**values)


def accepted_attempt(photo, *, processor_type=GENERATE_PREVIEW_PROCESSOR, processor_version=1):
    identity = {
        "event": photo.event,
        "contract_version": 2,
        "processor_type": processor_type,
        "processor_version": processor_version,
        "configuration": {},
    }
    run = EventProcessingRun.objects.create(**identity, configuration_hash="a" * 64)
    job = ProcessingJob.objects.create(
        **identity,
        run=run,
        photo=photo,
        configuration_hash="a" * 64,
        input_fingerprint={},
        status=ProcessingJob.Status.SUCCEEDED,
        completed_at=timezone.now(),
    )
    attempt = ProcessingAttempt.objects.create(
        **identity,
        run=run,
        job=job,
        photo=photo,
        input_fingerprint={},
        status=ProcessingAttempt.Status.SUCCEEDED,
        terminal_at=timezone.now(),
        accepted=True,
    )
    PhotoProcessingState.objects.update_or_create(
        photo=photo,
        processor_type=processor_type,
        defaults={
            "status": PhotoProcessingState.Status.SUCCEEDED,
            "current_run": run,
            "current_job": job,
            "current_attempt": attempt,
            "accepted_attempt": attempt,
            "succeeded_at": timezone.now(),
        },
    )
    return attempt


def captured_at(photo, capture_time):
    attempt = accepted_attempt(
        photo, processor_type=CAPTURE_METADATA_PROCESSOR, processor_version=2
    )
    Photo.objects.filter(pk=photo.pk).update(
        capture_time=capture_time, capture_time_source_attempt=attempt
    )


def accepted_preview(photo):
    attempt = accepted_attempt(photo)
    return PhotoDerivative.objects.create(
        photo=photo,
        variant="preview-small-v1",
        final_key=f"derivatives/previews/{photo.pk}/preview-small-v1/{uuid4()}-{'a' * 64}.jpg",
        byte_size=10,
        content_type="image/jpeg",
        width=10,
        height=10,
        oriented_source_width=10,
        oriented_source_height=10,
        sha256="a" * 64,
        accepted_attempt=attempt,
    )
