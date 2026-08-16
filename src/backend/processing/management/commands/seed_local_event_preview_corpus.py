"""Seed only the validated local preview corpus into the isolated MinIO store."""

from __future__ import annotations

import hashlib
import json
import re
from argparse import ArgumentParser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from picflow.models import Event, Photo

from processing.models import PhotoDerivative

EVENT_ID = 9
EVENT_SLUG = "cyclingrace-vechernee-sadovoe"
EXPECTED_PHOTO_COUNT = 17_043
_SHA256_LENGTH = 64
APPROVED_SOURCE_MANIFEST_SHA256 = "72e0166419cab804288de9f78ad045ba5fedfbb59b73b60dd49efa1e5f9d462b"
_PHOTO_ID = re.compile(r"[0-9a-f]{32}")
_PREVIEW_CONTRACT = {
    "apply_exif_orientation": True,
    "checksum_algorithm": "sha256",
    "color_space": "srgb",
    "contract_version": 2,
    "jpeg_quality": 85,
    "max_input_bytes": 52_428_800,
    "max_long_edge": 1600,
    "max_output_bytes": 10_485_760,
    "max_output_height": 1600,
    "max_output_width": 1600,
    "max_pixels": 24_000_000,
    "output_format": "jpeg",
    "processor_type": "generate_preview",
    "processor_version": 1,
    "strip_metadata": True,
    "upscale": False,
    "variant": "preview-small-v1",
    "watermark": "none",
}
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "source_manifest_sha256",
        "event",
        "preview_contract",
        "production_contract_sha256",
        "photos",
        "unresolved",
        "complete",
        "manifest_sha256",
    }
)
_PHOTO_FIELDS = frozenset(
    {
        "photo_id",
        "source_filename",
        "source_sha256",
        "source_byte_size",
        "preview_filename",
        "byte_size",
        "width",
        "height",
        "oriented_source_width",
        "oriented_source_height",
        "sha256",
        "warnings",
    }
)


@dataclass(frozen=True)
class _ManifestPhoto:
    photo_id: str
    preview_path: Path
    byte_size: int
    sha256: str


@dataclass(frozen=True)
class _ValidatedCorpus:
    manifest_sha256: str
    production_contract_sha256: str
    source_manifest_sha256: str
    photos: tuple[_ManifestPhoto, ...]


class Command(BaseCommand):
    help = "Validate and optionally seed the exact local cyclingrace preview corpus into MinIO."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--event-slug", required=True)
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--files-root", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: object, **options: object) -> None:
        event_slug = options["event_slug"]
        manifest_option = options["manifest"]
        files_root_option = options["files_root"]
        if event_slug != EVENT_SLUG:
            raise CommandError("--event-slug must exactly equal cyclingrace-vechernee-sadovoe")
        if not isinstance(manifest_option, str) or not isinstance(files_root_option, str):
            raise CommandError("--manifest and --files-root must be absolute paths")
        manifest_path = Path(manifest_option)
        files_root = Path(files_root_option)
        if not manifest_path.is_absolute() or not files_root.is_absolute():
            raise CommandError("--manifest and --files-root must be absolute paths")
        if not manifest_path.is_file() or not files_root.is_dir():
            raise CommandError("manifest or files root is unavailable")
        if manifest_path.parent.resolve() != files_root.resolve():
            raise CommandError("manifest must be directly inside --files-root")

        try:
            event = Event.objects.get(pk=EVENT_ID, slug=EVENT_SLUG)
        except Event.DoesNotExist:
            raise CommandError("the exact event does not exist in the cloned database") from None
        corpus = _validate_manifest(manifest_path=manifest_path, files_root=files_root, event=event)

        uploaded_count = 0
        existing_count = 0
        if options["apply"]:
            client = _local_s3_client()
            derivatives = {
                derivative.photo_id: derivative
                for derivative in PhotoDerivative.objects.filter(
                    photo__event=event,
                    variant="preview-small-v1",
                    accepted_attempt_id__isnull=False,
                )
            }
            for item in corpus.photos:
                derivative = derivatives[item.photo_id]
                existing = _existing_object_matches(
                    client, key=derivative.final_key, byte_size=item.byte_size, sha256=item.sha256
                )
                if existing:
                    existing_count += 1
                    continue
                try:
                    client.put_object(
                        Bucket=settings.PRIVATE_MEDIA_S3_BUCKET,
                        Key=derivative.final_key,
                        Body=item.preview_path.read_bytes(),
                        ContentType="image/jpeg",
                        Metadata={"sha256": item.sha256},
                    )
                except (BotoCoreError, ClientError, OSError) as error:
                    raise CommandError("local preview seed upload failed") from error
                uploaded_count += 1

        self.stdout.write(
            json.dumps(
                {
                    "existing_photo_count": existing_count,
                    "manifest_sha256": corpus.manifest_sha256,
                    "mode": "apply" if options["apply"] else "dry_run",
                    "production_contract_sha256": corpus.production_contract_sha256,
                    "source_manifest_sha256": corpus.source_manifest_sha256,
                    "uploaded_photo_count": uploaded_count,
                    "validated_photo_count": len(corpus.photos),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )


def _validate_manifest(*, manifest_path: Path, files_root: Path, event: Event) -> _ValidatedCorpus:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CommandError("manifest is not valid JSON") from error
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_FIELDS:
        raise CommandError("manifest schema is invalid")
    if manifest["artifact_type"] != "preview-corpus" or manifest["schema_version"] != 1:
        raise CommandError("manifest schema is invalid")
    if manifest["source_manifest_sha256"] != APPROVED_SOURCE_MANIFEST_SHA256:
        raise CommandError("manifest source identity is not approved")
    if manifest["event"] != {"id": str(EVENT_ID), "slug": EVENT_SLUG}:
        raise CommandError("manifest event does not match the exact cloned event")
    contract = manifest["preview_contract"]
    if not isinstance(contract, dict) or contract != _PREVIEW_CONTRACT:
        raise CommandError("manifest preview contract is not the production-equivalent 1600px JPEG")
    manifest_sha256 = _sha256_value(manifest["manifest_sha256"], "manifest SHA-256")
    source_manifest_sha256 = _sha256_value(
        manifest["source_manifest_sha256"], "source manifest SHA-256"
    )
    production_contract_sha256 = _sha256_value(
        manifest["production_contract_sha256"], "production contract SHA-256"
    )
    if _canonical_sha256(contract) != production_contract_sha256:
        raise CommandError("manifest production contract SHA-256 does not match")
    rows = manifest["photos"]
    if not isinstance(rows, list) or len(rows) != EXPECTED_PHOTO_COUNT:
        raise CommandError(f"manifest photo count must exactly equal {EXPECTED_PHOTO_COUNT}")
    unresolved = manifest["unresolved"]
    if not isinstance(unresolved, list) or not isinstance(manifest["complete"], bool):
        raise CommandError("manifest schema is invalid")
    if not _valid_unresolved_rows(unresolved):
        raise CommandError("manifest unresolved rows are invalid")
    if manifest["complete"] is not True:
        raise CommandError("manifest is not a complete preview corpus")
    if unresolved:
        raise CommandError("manifest unresolved rows are invalid")
    frozen = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if _canonical_sha256(frozen) != manifest_sha256:
        raise CommandError("manifest SHA-256 does not match")

    roots = files_root.resolve()
    photos: list[_ManifestPhoto] = []
    seen_photo_ids: set[str] = set()
    seen_filenames: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != _PHOTO_FIELDS:
            raise CommandError("manifest contains an invalid photo row")
        photo_id = row["photo_id"]
        preview_filename = row["preview_filename"]
        byte_size = row["byte_size"]
        sha256 = row["sha256"]
        if (
            not isinstance(photo_id, str)
            or _PHOTO_ID.fullmatch(photo_id) is None
            or row["source_filename"] != f"photo-{photo_id}.jpg"
            or preview_filename != f"{photo_id}.jpg"
            or not _positive_ints(
                row,
                "source_byte_size",
                "byte_size",
                "width",
                "height",
                "oriented_source_width",
                "oriented_source_height",
            )
            or not _is_sha256(row["source_sha256"])
            or not _is_sha256(sha256)
            or not isinstance(row["warnings"], list)
            or any(not isinstance(warning, str) for warning in row["warnings"])
        ):
            raise CommandError("manifest contains an invalid preview identity")
        assert isinstance(byte_size, int)
        assert isinstance(preview_filename, str)
        assert isinstance(sha256, str)
        preview_path = (roots / preview_filename).resolve()
        if roots not in preview_path.parents or preview_path == roots:
            raise CommandError("manifest preview path escapes --files-root")
        if photo_id in seen_photo_ids or preview_filename in seen_filenames:
            raise CommandError("manifest contains duplicate preview rows")
        seen_photo_ids.add(photo_id)
        seen_filenames.add(preview_filename)
        try:
            content = preview_path.read_bytes()
        except OSError as error:
            raise CommandError("manifest preview file is unavailable") from error
        if len(content) != byte_size or hashlib.sha256(content).hexdigest() != sha256:
            raise CommandError("manifest preview file size or SHA-256 does not match")
        photos.append(
            _ManifestPhoto(
                photo_id=photo_id,
                preview_path=preview_path,
                byte_size=byte_size,
                sha256=sha256,
            )
        )

    if [item.photo_id for item in photos] != sorted(item.photo_id for item in photos):
        raise CommandError("manifest photo rows are not in canonical order")
    database_photo_ids = set(Photo.objects.filter(event=event).values_list("id", flat=True))
    if database_photo_ids != seen_photo_ids or len(database_photo_ids) != EXPECTED_PHOTO_COUNT:
        raise CommandError(
            f"manifest does not exactly cover the {EXPECTED_PHOTO_COUNT}-photo database join"
        )
    derivatives = {
        derivative.photo_id: derivative
        for derivative in PhotoDerivative.objects.filter(
            photo__event=event,
            variant="preview-small-v1",
            accepted_attempt_id__isnull=False,
        )
    }
    if set(derivatives) != seen_photo_ids:
        raise CommandError("manifest does not exactly cover accepted preview projections")
    for item in photos:
        derivative = derivatives[item.photo_id]
        if derivative.byte_size != item.byte_size or derivative.sha256 != item.sha256:
            raise CommandError("manifest preview facts do not match the accepted projection")
    return _ValidatedCorpus(
        manifest_sha256=manifest_sha256,
        source_manifest_sha256=source_manifest_sha256,
        production_contract_sha256=production_contract_sha256,
        photos=tuple(photos),
    )


def _valid_unresolved_rows(rows: list[object]) -> bool:
    return all(
        isinstance(row, dict)
        and set(row) == {"photo_id", "error"}
        and all(isinstance(value, str) and value for value in row.values())
        for row in rows
    )


def _positive_ints(row: dict[str, object], *fields: str) -> bool:
    return all(_positive_int(row[field]) for field in fields)


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _sha256_value(value: object, label: str) -> str:
    if not _is_sha256(value):
        raise CommandError(f"{label} is invalid")
    return cast(str, value)


def _local_s3_client() -> Any:
    endpoint = settings.PRIVATE_MEDIA_S3_ENDPOINT_URL
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname != "minio" or parsed.port != 9000:
        raise CommandError(
            "PRIVATE_MEDIA_S3_ENDPOINT_URL must be the internal local MinIO endpoint"
        )
    if not all(
        isinstance(value, str) and value
        for value in (
            settings.PRIVATE_MEDIA_S3_BUCKET,
            settings.PRIVATE_MEDIA_S3_ACCESS_KEY_ID,
            settings.PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY,
        )
    ):
        raise CommandError("local MinIO credentials and bucket must be configured")
    return boto3.client(
        "s3",
        aws_access_key_id=settings.PRIVATE_MEDIA_S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY,
        endpoint_url=endpoint,
        region_name=settings.PRIVATE_MEDIA_S3_REGION,
        config=Config(signature_version="s3v4"),
    )


def _existing_object_matches(client: Any, *, key: str, byte_size: int, sha256: str) -> bool:
    try:
        existing = client.head_object(Bucket=settings.PRIVATE_MEDIA_S3_BUCKET, Key=key)
    except ClientError as error:
        response = error.response.get("ResponseMetadata", {})
        if response.get("HTTPStatusCode") == 404:
            return False
        raise CommandError("local preview seed inspection failed") from error
    except BotoCoreError as error:
        raise CommandError("local preview seed inspection failed") from error
    metadata = existing.get("Metadata") if isinstance(existing, dict) else None
    if (
        not isinstance(existing, dict)
        or existing.get("ContentLength") != byte_size
        or not isinstance(metadata, dict)
        or metadata.get("sha256") != sha256
    ):
        raise CommandError("an existing local preview object conflicts with the manifest")
    return True
