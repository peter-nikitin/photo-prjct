"""Run with the actual 899e9cc backend on a DB retaining new import tables."""

import io
import json
import os
from uuid import uuid4

import boto3
import django
from PIL import Image

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


def probe():
    import ingestion.models
    from django.contrib.auth import get_user_model
    from django.db import connection
    from django.utils import timezone
    from feature_flags.models import FeatureFlag
    from ingestion.models import UploadBatch, UploadItem
    from ingestion.services.confirmation import confirm_upload_item
    from ingestion.storage import PrivateUploadStorage
    from picflow.models import Event
    from processing.services.jobs import claim_job

    assert not hasattr(ingestion.models, "ImportBatch"), "Must run actual previous application"
    assert FeatureFlag.objects.get(key="yandex-disk-import").state == "off"
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, status FROM ingestion_importattempt ORDER BY id")
        before = cursor.fetchall()
    owner = get_user_model().objects.get(username="acceptance")
    event = Event.objects.create(
        name="Old web retained tables",
        slug="old-web-retained",
        city="Moscow",
        start_date="2026-09-07",
        end_date="2026-09-07",
        timezone_name="Europe/Moscow",
    )
    batch = UploadBatch.objects.create(event=event, uploader=owner, expected_item_count=1)
    buffer = io.BytesIO()
    Image.new("RGB", (12, 8), "green").save(buffer, "JPEG")
    jpeg = buffer.getvalue()
    identity = uuid4()
    item = UploadItem.objects.create(
        id=identity,
        batch=batch,
        client_item_id=uuid4(),
        original_filename="browser.jpg",
        declared_content_type="image/jpeg",
        expected_size=len(jpeg),
        incoming_key=f"incoming/{batch.pk}/{identity}",
        final_key=f"originals/{identity.hex}",
        status="authorized",
        authorization_expires_at=timezone.now(),
    )
    client = boto3.client(
        "s3",
        endpoint_url="http://minio:9000",
        aws_access_key_id="local-minio",
        aws_secret_access_key="local-minio-password",
        region_name="us-east-1",
    )
    client.put_object(
        Bucket="local-private", Key=item.incoming_key, Body=jpeg, ContentType="image/jpeg"
    )
    photo = confirm_upload_item(
        uploader=owner, batch_id=batch.pk, item_id=item.pk, storage=PrivateUploadStorage()
    )
    assert photo is not None and photo.original_key == item.final_key
    claimed = claim_job(
        contract_version=1,
        processor_type="capture_metadata",
        processor_version=2,
        worker_build="previous899e9cc",
        event_id=event.pk,
    )
    assert not claimed.empty and claimed.job.photo_id == photo.pk
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, status FROM ingestion_importattempt ORDER BY id")
        assert cursor.fetchall() == before
    print(
        json.dumps(
            dict(
                application="899e9cc",
                browser_confirmation="passed",
                photo_processing_claim="passed",
                retained_import_attempts=len(before),
            )
        )
    )


probe()
