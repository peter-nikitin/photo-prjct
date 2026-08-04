"""Apply the reviewed whole-bucket feedback lifecycle with automatic recovery."""

from __future__ import annotations

import hashlib
import hmac
from copy import deepcopy
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from selfie_search.feedback_lifecycle import build_feedback_lifecycle_configuration


class Command(BaseCommand):
    help = "Apply the approved 30-day lifecycle to the dedicated feedback bucket."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--confirm-lifecycle-mutation",
            action="store_true",
            help="Permit the reviewed feedback bucket lifecycle configuration to be changed.",
        )
        parser.add_argument(
            "--expected-storage-digest",
            required=True,
            help="Approved SHA-256 digest of the configured feedback bucket and KMS key ID.",
        )

    def handle(self, *args: object, **options: object) -> None:  # noqa: ARG002
        if options["confirm_lifecycle_mutation"] is not True:
            raise CommandError(
                "Pass --confirm-lifecycle-mutation to change the lifecycle configuration."
            )
        expected_digest = options["expected_storage_digest"]
        if not isinstance(expected_digest, str) or not hmac.compare_digest(
            expected_digest, _configured_storage_digest()
        ):
            raise CommandError(
                "The approved feedback storage digest does not match this deployment."
            )

        client = boto3.client(
            "s3",
            aws_access_key_id=settings.SELFIE_FEEDBACK_S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY,
            endpoint_url=settings.SELFIE_FEEDBACK_S3_ENDPOINT_URL,
            region_name=settings.SELFIE_FEEDBACK_S3_REGION,
            config=Config(signature_version="s3v4"),
        )
        _require_unversioned_bucket(client)
        _require_object_lock_disabled(client)
        original = _read_lifecycle_configuration(client)
        try:
            intended = build_feedback_lifecycle_configuration(original)
        except ValueError:
            raise CommandError(
                "Selfie-feedback lifecycle configuration has a rule collision."
            ) from None
        self.stdout.write("selfie-feedback-lifecycle-mutation-preflight-ok")

        try:
            client.put_bucket_lifecycle_configuration(
                Bucket=settings.SELFIE_FEEDBACK_S3_BUCKET,
                LifecycleConfiguration=intended,
            )
        except (BotoCoreError, ClientError, TypeError):
            raise CommandError("Selfie-feedback lifecycle mutation failed.") from None
        self.stdout.write("selfie-feedback-lifecycle-mutation-put-ok")

        try:
            readback = _read_lifecycle_configuration(client)
        except CommandError:
            readback = None
        if readback == intended:
            self.stdout.write("selfie-feedback-lifecycle-mutation-readback-ok")
            return

        if not _restore_lifecycle_configuration(client, original):
            raise CommandError("Selfie-feedback lifecycle recovery failed.") from None
        self.stdout.write("selfie-feedback-lifecycle-mutation-recovery-ok")
        raise CommandError("Selfie-feedback lifecycle readback verification failed.")


def _configured_storage_digest() -> str:
    configured = f"{settings.SELFIE_FEEDBACK_S3_BUCKET}\x00{settings.SELFIE_FEEDBACK_KMS_KEY_ID}"
    return hashlib.sha256(configured.encode("utf-8")).hexdigest()


def _require_unversioned_bucket(client: Any) -> None:
    try:
        response = client.get_bucket_versioning(Bucket=settings.SELFIE_FEEDBACK_S3_BUCKET)
    except (BotoCoreError, ClientError, TypeError):
        raise CommandError("Selfie-feedback lifecycle versioning check failed.") from None
    if not isinstance(response, dict) or "Status" in response:
        raise CommandError("Selfie-feedback lifecycle versioning check failed.")


def _require_object_lock_disabled(client: Any) -> None:
    try:
        response = client.get_object_lock_configuration(Bucket=settings.SELFIE_FEEDBACK_S3_BUCKET)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {
            "ObjectLockConfigurationNotFoundError",
            "NoSuchObjectLockConfiguration",
        }:
            return
        raise CommandError("Selfie-feedback lifecycle Object Lock check failed.") from None
    except (BotoCoreError, TypeError):
        raise CommandError("Selfie-feedback lifecycle Object Lock check failed.") from None
    if not isinstance(response, dict) or response.get("ObjectLockConfiguration") not in ({}, None):
        raise CommandError("Selfie-feedback lifecycle Object Lock check failed.")


def _read_lifecycle_configuration(client: Any) -> dict[str, object] | None:
    try:
        response = client.get_bucket_lifecycle_configuration(
            Bucket=settings.SELFIE_FEEDBACK_S3_BUCKET
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "NoSuchLifecycleConfiguration":
            return None
        raise CommandError("Selfie-feedback lifecycle readback failed.") from None
    except (BotoCoreError, TypeError):
        raise CommandError("Selfie-feedback lifecycle readback failed.") from None
    if not isinstance(response, dict):
        raise CommandError("Selfie-feedback lifecycle readback failed.")
    rules = response.get("Rules")
    if not isinstance(rules, list):
        raise CommandError("Selfie-feedback lifecycle readback failed.")
    return {"Rules": deepcopy(rules)}


def _restore_lifecycle_configuration(client: Any, original: dict[str, object] | None) -> bool:
    try:
        if original is None:
            client.delete_bucket_lifecycle(Bucket=settings.SELFIE_FEEDBACK_S3_BUCKET)
        else:
            client.put_bucket_lifecycle_configuration(
                Bucket=settings.SELFIE_FEEDBACK_S3_BUCKET,
                LifecycleConfiguration=original,
            )
    except (BotoCoreError, ClientError, TypeError):
        return False
    return True
