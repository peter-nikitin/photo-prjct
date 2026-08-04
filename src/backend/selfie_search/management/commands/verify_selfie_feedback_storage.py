"""Verify the dedicated private feedback-storage contract with one scratch object."""

from __future__ import annotations

import secrets
from contextlib import closing
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from selfie_search.feedback_lifecycle import build_feedback_lifecycle_configuration


class Command(BaseCommand):
    help = "Verify the dedicated feedback bucket and exact-object grant contract."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--confirm-real-storage",
            action="store_true",
            help="Permit one generated private feedback scratch object to be put and deleted.",
        )

    def handle(self, *args: object, **options: object) -> None:  # noqa: ARG002
        if options["confirm_real_storage"] is not True:
            raise CommandError("Pass --confirm-real-storage to run the scratch-object preflight.")

        client = boto3.client(
            "s3",
            aws_access_key_id=settings.SELFIE_FEEDBACK_S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY,
            endpoint_url=settings.SELFIE_FEEDBACK_S3_ENDPOINT_URL,
            region_name=settings.SELFIE_FEEDBACK_S3_REGION,
            config=Config(signature_version="s3v4"),
        )
        _verify_unversioned_bucket(client)
        self.stdout.write("selfie-feedback-storage-preflight-versioning-unversioned-ok")
        _verify_object_lock_disabled(client)
        self.stdout.write("selfie-feedback-storage-preflight-object-lock-disabled-ok")
        _verify_default_encryption(client)
        self.stdout.write("selfie-feedback-storage-preflight-encryption-ok")
        _verify_private_acl(client)
        self.stdout.write("selfie-feedback-storage-preflight-private-acl-ok")
        _verify_lifecycle(client)
        self.stdout.write("selfie-feedback-storage-preflight-lifecycle-ok")

        key = secrets.token_hex(16)
        try:
            client.put_object(
                Bucket=settings.SELFIE_FEEDBACK_S3_BUCKET,
                Key=key,
                Body=b"selfie-feedback-storage-preflight",
                ContentType="image/jpeg",
                ContentLength=len(b"selfie-feedback-storage-preflight"),
                ACL="private",
                ServerSideEncryption="aws:kms",
                SSEKMSKeyId=settings.SELFIE_FEEDBACK_KMS_KEY_ID,
            )
            self.stdout.write("selfie-feedback-storage-preflight-put-ok")
            head = client.head_object(Bucket=settings.SELFIE_FEEDBACK_S3_BUCKET, Key=key)
            if not isinstance(head, dict) or head.get("ContentType") != "image/jpeg":
                raise TypeError
            self.stdout.write("selfie-feedback-storage-preflight-head-ok")
            ranged = client.get_object(
                Bucket=settings.SELFIE_FEEDBACK_S3_BUCKET,
                Key=key,
                Range="bytes=0-0",
            )
            if not isinstance(ranged, dict) or "Body" not in ranged:
                raise TypeError
            with closing(ranged["Body"]) as body:
                if not body.read(1):
                    raise TypeError
            self.stdout.write("selfie-feedback-storage-preflight-range-ok")
            _verify_anonymous_denial(key)
            self.stdout.write("selfie-feedback-storage-preflight-anonymous-denied-ok")
            grant = client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": settings.SELFIE_FEEDBACK_S3_BUCKET, "Key": key},
                ExpiresIn=60,
                HttpMethod="GET",
            )
            if not isinstance(grant, str) or not grant:
                raise TypeError
            self.stdout.write("selfie-feedback-storage-preflight-grant-ok")
        except (BotoCoreError, ClientError, TypeError, AttributeError):
            raise CommandError("Selfie-feedback storage preflight failed.") from None
        finally:
            try:
                client.delete_object(Bucket=settings.SELFIE_FEEDBACK_S3_BUCKET, Key=key)
            except (BotoCoreError, ClientError, TypeError):
                raise CommandError("Selfie-feedback scratch cleanup failed.") from None
            self.stdout.write("selfie-feedback-storage-preflight-delete-ok")


def _verify_unversioned_bucket(client: Any) -> None:
    try:
        response = client.get_bucket_versioning(Bucket=settings.SELFIE_FEEDBACK_S3_BUCKET)
    except (BotoCoreError, ClientError, TypeError):
        raise CommandError("Selfie-feedback versioning preflight failed.") from None
    if not isinstance(response, dict) or "Status" in response:
        raise CommandError("Selfie-feedback versioning preflight failed.")


def _verify_object_lock_disabled(client: Any) -> None:
    try:
        response = client.get_object_lock_configuration(Bucket=settings.SELFIE_FEEDBACK_S3_BUCKET)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {
            "ObjectLockConfigurationNotFoundError",
            "NoSuchObjectLockConfiguration",
        }:
            return
        raise CommandError("Selfie-feedback Object Lock preflight failed.") from None
    except (BotoCoreError, TypeError):
        raise CommandError("Selfie-feedback Object Lock preflight failed.") from None
    if not isinstance(response, dict) or response.get("ObjectLockConfiguration") not in ({}, None):
        raise CommandError("Selfie-feedback Object Lock preflight failed.")


def _verify_default_encryption(client: Any) -> None:
    try:
        response = client.get_bucket_encryption(Bucket=settings.SELFIE_FEEDBACK_S3_BUCKET)
    except (BotoCoreError, ClientError, TypeError):
        raise CommandError("Selfie-feedback encryption preflight failed.") from None
    if not isinstance(response, dict):
        raise CommandError("Selfie-feedback encryption preflight failed.")
    configuration = response.get("ServerSideEncryptionConfiguration")
    if not isinstance(configuration, dict):
        raise CommandError("Selfie-feedback encryption preflight failed.")
    rules = configuration.get("Rules")
    if not isinstance(rules, list) or len(rules) != 1 or not isinstance(rules[0], dict):
        raise CommandError("Selfie-feedback encryption preflight failed.")
    default = rules[0].get("ApplyServerSideEncryptionByDefault")
    if (
        not isinstance(default, dict)
        or default.get("SSEAlgorithm") != "aws:kms"
        or default.get("KMSMasterKeyID") != settings.SELFIE_FEEDBACK_KMS_KEY_ID
    ):
        raise CommandError("Selfie-feedback encryption preflight failed.")


def _verify_private_acl(client: Any) -> None:
    try:
        acl_response = client.get_bucket_acl(Bucket=settings.SELFIE_FEEDBACK_S3_BUCKET)
    except (BotoCoreError, ClientError, TypeError):
        raise CommandError("Selfie-feedback public access preflight failed.") from None
    if not _has_no_anonymous_acl_grant(acl_response):
        raise CommandError("Selfie-feedback public access preflight failed.")


def _has_no_anonymous_acl_grant(response: object) -> bool:
    if not isinstance(response, dict):
        return False
    grants = response.get("Grants")
    if not isinstance(grants, list):
        return False
    return not any(
        isinstance(grant, dict)
        and isinstance(grant.get("Grantee"), dict)
        and grant["Grantee"].get("URI")
        in {
            "http://acs.amazonaws.com/groups/global/AllUsers",
            "http://acs.amazonaws.com/groups/global/AuthenticatedUsers",
        }
        for grant in grants
    )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:  # noqa: ARG002
        return None


def _verify_anonymous_denial(key: str) -> None:
    bucket_url = _anonymous_bucket_url()
    object_url = f"{bucket_url}{quote(key, safe='')}"
    requests = (
        Request(object_url, method="GET"),
        Request(object_url, method="HEAD"),
        Request(f"{bucket_url}?list-type=2", method="GET"),
    )
    opener = build_opener(_NoRedirect())
    for request in requests:
        try:
            response = opener.open(request, timeout=10)
        except HTTPError as error:
            denied = error.code in {401, 403}
            error.close()
            if denied:
                continue
        except (URLError, OSError, TypeError, ValueError):
            pass
        else:
            try:
                if response.getcode() in {401, 403}:
                    continue
            finally:
                response.close()
        raise CommandError("Selfie-feedback anonymous access preflight failed.")


def _anonymous_bucket_url() -> str:
    endpoint = urlsplit(settings.SELFIE_FEEDBACK_S3_ENDPOINT_URL)
    if (
        endpoint.scheme != "https"
        or endpoint.netloc != "storage.yandexcloud.net"
        or endpoint.path not in {"", "/"}
        or endpoint.query
        or endpoint.fragment
    ):
        raise CommandError("Selfie-feedback anonymous access preflight failed.")
    return f"https://{settings.SELFIE_FEEDBACK_S3_BUCKET}.storage.yandexcloud.net/"


def _verify_lifecycle(client: Any) -> None:
    try:
        response = client.get_bucket_lifecycle_configuration(
            Bucket=settings.SELFIE_FEEDBACK_S3_BUCKET
        )
    except (BotoCoreError, ClientError, TypeError):
        raise CommandError("Selfie-feedback lifecycle preflight failed.") from None
    try:
        intended = build_feedback_lifecycle_configuration(None)
    except ValueError:  # pragma: no cover - a literal builder cannot fail
        raise CommandError("Selfie-feedback lifecycle preflight failed.") from None
    if not isinstance(response, dict) or response.get("Rules") != intended["Rules"]:
        raise CommandError("Selfie-feedback lifecycle preflight failed.")
