"""Apply the reviewed selfie-search lifecycle document with automatic recovery."""

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

from selfie_search.lifecycle import build_selfie_search_lifecycle_configuration


class Command(BaseCommand):
    help = "Apply the approved selfie-search lifecycle document to one unversioned bucket."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--confirm-lifecycle-mutation",
            action="store_true",
            help="Permit the reviewed bucket lifecycle configuration to be changed.",
        )
        parser.add_argument(
            "--expected-bucket-digest",
            required=True,
            help="Approved SHA-256 digest of the configured private bucket name.",
        )

    def handle(self, *args: object, **options: object) -> None:  # noqa: ARG002
        if options["confirm_lifecycle_mutation"] is not True:
            raise CommandError(
                "Pass --confirm-lifecycle-mutation to change the lifecycle configuration."
            )
        expected_digest = options["expected_bucket_digest"]
        if not isinstance(expected_digest, str) or not hmac.compare_digest(
            expected_digest, _configured_bucket_digest()
        ):
            raise CommandError("The approved bucket digest does not match this deployment.")

        client = boto3.client(
            "s3",
            aws_access_key_id=settings.PRIVATE_MEDIA_S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY,
            endpoint_url=settings.PRIVATE_MEDIA_S3_ENDPOINT_URL,
            region_name=settings.PRIVATE_MEDIA_S3_REGION,
            config=Config(signature_version="s3v4"),
        )
        _require_unversioned_bucket(client)
        original = _read_lifecycle_configuration(client)
        try:
            intended = build_selfie_search_lifecycle_configuration(original)
        except ValueError:
            raise CommandError(
                "Selfie-search lifecycle configuration has a rule collision."
            ) from None
        self.stdout.write("selfie-search-lifecycle-mutation-preflight-ok")

        try:
            client.put_bucket_lifecycle_configuration(
                Bucket=settings.PRIVATE_MEDIA_S3_BUCKET,
                LifecycleConfiguration=intended,
            )
        except (BotoCoreError, ClientError, TypeError):
            raise CommandError("Selfie-search lifecycle mutation failed.") from None
        self.stdout.write("selfie-search-lifecycle-mutation-put-ok")

        try:
            readback = _read_lifecycle_configuration(client)
        except CommandError:
            readback = None
        if _matches_intended_lifecycle(readback, intended):
            self.stdout.write("selfie-search-lifecycle-mutation-readback-ok")
            return

        if not _restore_lifecycle_configuration(client, original):
            raise CommandError("Selfie-search lifecycle recovery failed.") from None
        self.stdout.write("selfie-search-lifecycle-mutation-recovery-ok")
        raise CommandError("Selfie-search lifecycle readback verification failed.")


def _configured_bucket_digest() -> str:
    return hashlib.sha256(settings.PRIVATE_MEDIA_S3_BUCKET.encode("utf-8")).hexdigest()


def _require_unversioned_bucket(client: Any) -> None:
    try:
        response = client.get_bucket_versioning(Bucket=settings.PRIVATE_MEDIA_S3_BUCKET)
    except (BotoCoreError, ClientError, TypeError):
        raise CommandError("Selfie-search lifecycle versioning check failed.") from None
    if not isinstance(response, dict) or "Status" in response:
        raise CommandError("Selfie-search lifecycle versioning check failed.")


def _read_lifecycle_configuration(client: Any) -> dict[str, object] | None:
    try:
        response = client.get_bucket_lifecycle_configuration(
            Bucket=settings.PRIVATE_MEDIA_S3_BUCKET
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "NoSuchLifecycleConfiguration":
            return None
        raise CommandError("Selfie-search lifecycle readback failed.") from None
    except (BotoCoreError, TypeError):
        raise CommandError("Selfie-search lifecycle readback failed.") from None
    if not isinstance(response, dict):
        raise CommandError("Selfie-search lifecycle readback failed.")
    rules = response.get("Rules")
    if not isinstance(rules, list):
        raise CommandError("Selfie-search lifecycle readback failed.")
    # botocore adds transport metadata to responses; the lifecycle document itself is Rules.
    return {"Rules": deepcopy(rules)}


def _matches_intended_lifecycle(readback: object, intended: object) -> bool:
    return isinstance(readback, dict) and readback == intended


def _restore_lifecycle_configuration(client: Any, original: dict[str, object] | None) -> bool:
    try:
        if original is None:
            client.delete_bucket_lifecycle(Bucket=settings.PRIVATE_MEDIA_S3_BUCKET)
        else:
            client.put_bucket_lifecycle_configuration(
                Bucket=settings.PRIVATE_MEDIA_S3_BUCKET,
                LifecycleConfiguration=original,
            )
    except (BotoCoreError, ClientError, TypeError):
        return False
    return True
