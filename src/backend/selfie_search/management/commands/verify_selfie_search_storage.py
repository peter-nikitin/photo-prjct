"""Verify the narrow selfie-search temporary-storage contract."""

from __future__ import annotations

import secrets
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from ingestion.storage import StorageUnavailable

from selfie_search.storage import TemporarySelfieStorage


class Command(BaseCommand):
    help = "Verify the configured selfie-search lifecycle and exact-object grant contract."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--confirm-real-storage",
            action="store_true",
            help="Permit one generated private scratch object to be put and deleted.",
        )

    def handle(self, *args: object, **options: object) -> None:  # noqa: ARG002
        if options["confirm_real_storage"] is not True:
            raise CommandError("Pass --confirm-real-storage to run the scratch-object preflight.")

        client = boto3.client(
            "s3",
            aws_access_key_id=settings.PRIVATE_MEDIA_S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY,
            endpoint_url=settings.PRIVATE_MEDIA_S3_ENDPOINT_URL,
            region_name=settings.PRIVATE_MEDIA_S3_REGION,
            config=Config(signature_version="s3v4"),
        )
        _verify_unversioned_bucket(client)
        self.stdout.write("selfie-search-storage-preflight-versioning-unversioned-ok")
        _verify_lifecycle(client)
        self.stdout.write("selfie-search-storage-preflight-lifecycle-ok")

        storage = TemporarySelfieStorage(client=client)
        key = f"{settings.SELFIE_SEARCH_TEMPORARY_PREFIX}{secrets.token_hex(16)}"
        try:
            storage.put(
                key=key, content=b"selfie-search-storage-preflight", content_type="image/jpeg"
            )
            self.stdout.write("selfie-search-storage-preflight-put-ok")
            storage.inspect(key=key)
            self.stdout.write("selfie-search-storage-preflight-head-ok")
            storage.create_download_grant(key=key)
            self.stdout.write("selfie-search-storage-preflight-grant-ok")
        except (StorageUnavailable, ValueError):
            raise CommandError("Selfie-search storage preflight failed.") from None
        finally:
            try:
                storage.delete(key=key)
            except (StorageUnavailable, ValueError):
                raise CommandError("Selfie-search scratch cleanup failed.") from None
            self.stdout.write("selfie-search-storage-preflight-delete-ok")


def _verify_lifecycle(client: Any) -> None:
    try:
        response = client.get_bucket_lifecycle_configuration(
            Bucket=settings.PRIVATE_MEDIA_S3_BUCKET
        )
    except (BotoCoreError, ClientError, TypeError):
        raise CommandError("Selfie-search lifecycle preflight failed.") from None
    if not _has_required_lifecycle_rule(response):
        raise CommandError("Selfie-search lifecycle preflight failed.")


def _verify_unversioned_bucket(client: Any) -> None:
    try:
        response = client.get_bucket_versioning(Bucket=settings.PRIVATE_MEDIA_S3_BUCKET)
    except (BotoCoreError, ClientError, TypeError):
        raise CommandError("Selfie-search versioning preflight failed.") from None
    if not isinstance(response, dict) or "Status" in response:
        raise CommandError("Selfie-search versioning preflight failed.")


def _has_required_lifecycle_rule(response: object) -> bool:
    if not isinstance(response, dict):
        return False
    rules = response.get("Rules")
    if not isinstance(rules, list):
        return False
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("Status") != "Enabled":
            continue
        prefix = _lifecycle_prefix(rule)
        expiration = rule.get("Expiration")
        if prefix != settings.SELFIE_SEARCH_TEMPORARY_PREFIX or not isinstance(expiration, dict):
            continue
        days = expiration.get("Days")
        if isinstance(days, int) and not isinstance(days, bool) and days * 24 <= 24:
            return True
    return False


def _lifecycle_prefix(rule: dict[str, object]) -> object:
    filter_value = rule.get("Filter")
    if isinstance(filter_value, dict):
        return filter_value.get("Prefix")
    return rule.get("Prefix")
