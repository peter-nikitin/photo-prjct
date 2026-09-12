"""Disposable fixture setup only, never a deployment entrypoint."""

import os
import time

import boto3

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()


def seed():
    from django.contrib.auth import get_user_model
    from feature_flags.models import FeatureFlag
    from ingestion.models import ImportBatch
    from picflow.models import Event

    user = get_user_model().objects.create_superuser("acceptance", password="disposable")
    event = Event.objects.create(
        name="Disposable acceptance",
        slug="disposable",
        city="Moscow",
        start_date="2026-09-07",
        end_date="2026-09-07",
    )
    FeatureFlag.objects.filter(key="yandex-disk-import").update(state="on")
    # Equivalent durable result of an owned browser submission; browser/API flow is
    # separately exercised by test_import_flow. No external source is authorized here.
    ImportBatch.objects.create(
        owner=user, event=event, submitted_source_key="fixture", submission_key="fixture"
    )
    client = boto3.client(
        "s3",
        endpoint_url="http://minio:9000",
        aws_access_key_id="local-minio",
        aws_secret_access_key="local-minio-password",
        region_name="us-east-1",
    )
    for retry in range(30):
        try:
            client.create_bucket(Bucket="local-private")
            break
        except Exception:
            if retry == 29:
                raise
            time.sleep(1)


seed()
