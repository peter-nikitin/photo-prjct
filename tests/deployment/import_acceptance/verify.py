"""Verify real private object, enrollment and bounded manifest persistence; close gate."""

import json
import os
from datetime import timedelta

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


def verify():
    from django.utils import timezone
    from feature_flags.models import FeatureFlag
    from ingestion.models import ImportAttempt, ImportBatch
    from ingestion.storage import PrivateUploadStorage

    batch = ImportBatch.objects.get(submission_key="fixture")
    assert batch.jpeg_count == 100 and batch.items.count() == 100
    assert batch.manifest_pages.count() == 2
    item = batch.items.get(status="imported")
    photo = item.photo
    assert photo.original_size == 52428800
    assert PrivateUploadStorage().inspect(key=photo.original_key).size == 52428800
    assert set(photo.processing_states.values_list("processor_type", flat=True)) == {
        "capture_metadata",
        "generate_preview",
    }
    FeatureFlag.objects.filter(key="yandex-disk-import").update(state="off")
    pending = batch.items.filter(status="pending").first()
    now = timezone.now()
    ImportAttempt.objects.create(
        batch=batch,
        item=pending,
        kind="file",
        claimed_at=now - timedelta(minutes=3),
        heartbeat_at=now - timedelta(minutes=3),
        lease_expires_at=now - timedelta(minutes=1),
    )
    print(
        json.dumps(
            dict(
                persisted_manifest_items=batch.items.count(),
                persisted_manifest_pages=batch.manifest_pages.count(),
                imported_photos=1,
                private_original_bytes=photo.original_size,
                processing_enrolled=True,
                gate="off",
                interrupted_attempt_retained=True,
            )
        )
    )


verify()
