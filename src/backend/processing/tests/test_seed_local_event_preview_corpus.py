from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import (
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    FACE_EMBEDDING_QUALITY_APPROVAL,
    accepted_preview_cohort_hash,
)
from processing.services.previews import preview_final_key

PREVIEW_CONTRACT = {
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
SOURCE_MANIFEST_SHA256 = "72e0166419cab804288de9f78ad045ba5fedfbb59b73b60dd49efa1e5f9d462b"


class _LocalS3:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.head_calls = 0
        self.put_calls = 0

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:  # noqa: N803
        self.head_calls += 1
        try:
            content, metadata = self.objects[Key]
        except KeyError as error:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                "HeadObject",
            ) from error
        return {"ContentLength": len(content), "Metadata": metadata}

    def put_object(
        self,
        *,
        Bucket: str,  # noqa: N803
        Key: str,  # noqa: N803
        Body: bytes,  # noqa: N803
        ContentType: str,  # noqa: N803
        Metadata: dict[str, str],  # noqa: N803
    ) -> None:
        self.put_calls += 1
        assert Bucket == "adaface-private"
        assert ContentType == "image/jpeg"
        self.objects[Key] = (Body, Metadata)


class SeedLocalEventPreviewCorpusTests(TestCase):
    event_slug = "cyclingrace-vechernee-sadovoe"
    photo_id = "0" * 32

    def setUp(self) -> None:
        self.event = Event.objects.create(
            id=9,
            name="Cyclingrace Вечернее Садовое",
            slug=self.event_slug,
            start_date=date(2026, 8, 8),
            end_date=date(2026, 8, 8),
            city="Moscow",
        )
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.files_root = Path(self.temporary_directory.name) / "corpus"
        self.files_root.mkdir()
        self.manifest_path = self.files_root / "manifest.json"
        self.client = _LocalS3()

    def test_dry_run_refuses_a_manifest_that_does_not_cover_the_database_join(self) -> None:
        self._write_manifest(photos=[])

        with self.assertRaisesMessage(CommandError, "manifest photo count"):
            self._run_command()

        self._assert_no_s3_calls()

    @patch("processing.management.commands.seed_local_event_preview_corpus.EXPECTED_PHOTO_COUNT", 1)
    def test_apply_uploads_each_verified_manifest_file_to_its_accepted_preview_key(self) -> None:
        derivative = self._make_valid_preview(photo_id=self.photo_id, derivative_sha256="f" * 64)
        content = self._preview_content(self.photo_id)
        sha256 = hashlib.sha256(content).hexdigest()
        self._write_manifest(photos=[self._photo_row(photo_id=self.photo_id)])

        output = self._run_command(apply=True)

        self.assertEqual(
            self.client.objects,
            {derivative.final_key: (content, {"sha256": sha256})},
        )
        report = json.loads(output)
        self.assertEqual(report["mode"], "apply")
        self.assertEqual(report["validated_photo_count"], 1)
        self.assertEqual(report["uploaded_photo_count"], 1)
        self.assertNotIn(f"{self.photo_id}.jpg", output)
        self.assertNotIn(derivative.final_key, output)

        repeated_report = json.loads(self._run_command(apply=True))
        self.assertEqual(repeated_report["uploaded_photo_count"], 0)
        self.assertEqual(repeated_report["existing_photo_count"], 1)

    @patch("processing.management.commands.seed_local_event_preview_corpus.EXPECTED_PHOTO_COUNT", 1)
    def test_refuses_approved_crosswalk_with_an_unexpected_sha_match_before_inspecting_local_s3(
        self,
    ) -> None:
        self._make_valid_preview(photo_id=self.photo_id)
        self._write_manifest(photos=[self._photo_row(photo_id=self.photo_id)])

        with self.assertRaisesMessage(CommandError, "approved crosswalk"):
            self._run_command(apply=True)

        self._assert_no_s3_calls()

    @patch("processing.management.commands.seed_local_event_preview_corpus.EXPECTED_PHOTO_COUNT", 2)
    def test_refuses_approved_crosswalk_with_mixed_sha_matches_before_inspecting_local_s3(
        self,
    ) -> None:
        other_photo_id = "1" * 32
        self._make_valid_preview(photo_id=self.photo_id, derivative_sha256="f" * 64)
        self._make_valid_preview(photo_id=other_photo_id)
        self._write_manifest(
            photos=[
                self._photo_row(photo_id=self.photo_id),
                self._photo_row(photo_id=other_photo_id),
            ]
        )

        with self.assertRaisesMessage(CommandError, "approved crosswalk"):
            self._run_command(apply=True)

        self._assert_no_s3_calls()

    @patch("processing.management.commands.seed_local_event_preview_corpus.EXPECTED_PHOTO_COUNT", 1)
    def test_refuses_approved_crosswalk_with_an_accepted_sha_mutation_before_inspecting_local_s3(
        self,
    ) -> None:
        self._make_valid_preview(photo_id=self.photo_id, derivative_sha256="e" * 64)
        self._write_manifest(photos=[self._photo_row(photo_id=self.photo_id)])

        with self.assertRaisesMessage(CommandError, "approved crosswalk"):
            self._run_command(
                apply=True,
                accepted_preview_cohort_hash_override=_canonical_sha256(
                    [
                        {
                            "byte_size": len(self._preview_content(self.photo_id)),
                            "height": 1000,
                            "oriented_source_height": 1000,
                            "oriented_source_width": 1600,
                            "photo_id": self.photo_id,
                            "sha256": "f" * 64,
                            "width": 1600,
                        }
                    ]
                ),
            )

        self._assert_no_s3_calls()

    @patch("processing.management.commands.seed_local_event_preview_corpus.EXPECTED_PHOTO_COUNT", 1)
    def test_refuses_approved_crosswalk_with_an_accepted_key_mutation_before_inspecting_local_s3(
        self,
    ) -> None:
        self._make_valid_preview(
            photo_id=self.photo_id,
            derivative_sha256="f" * 64,
            final_key="derivatives/previews/tampered.jpg",
        )
        self._write_manifest(photos=[self._photo_row(photo_id=self.photo_id)])

        with self.assertRaisesMessage(CommandError, "approved crosswalk"):
            self._run_command(apply=True)

        self._assert_no_s3_calls()

    @patch("processing.management.commands.seed_local_event_preview_corpus.EXPECTED_PHOTO_COUNT", 1)
    def test_refuses_approved_crosswalk_size_mismatch_before_inspecting_local_s3(self) -> None:
        self._make_valid_preview(
            photo_id=self.photo_id,
            derivative_sha256="f" * 64,
            byte_size=len(self._preview_content(self.photo_id)) + 1,
        )
        self._write_manifest(photos=[self._photo_row(photo_id=self.photo_id)])

        with self.assertRaisesMessage(CommandError, "approved crosswalk"):
            self._run_command(apply=True)

        self._assert_no_s3_calls()

    @patch("processing.management.commands.seed_local_event_preview_corpus.EXPECTED_PHOTO_COUNT", 1)
    def test_refuses_approved_crosswalk_geometry_mismatch_before_inspecting_local_s3(self) -> None:
        self._make_valid_preview(
            photo_id=self.photo_id,
            derivative_sha256="f" * 64,
            width=1599,
        )
        self._write_manifest(photos=[self._photo_row(photo_id=self.photo_id)])

        with self.assertRaisesMessage(CommandError, "approved crosswalk"):
            self._run_command(apply=True)

        self._assert_no_s3_calls()

    def test_command_requires_the_exact_event_slug_and_absolute_corpus_paths(self) -> None:
        self._write_manifest(photos=[])

        with self.assertRaisesMessage(CommandError, "exactly"):
            self._run_command(event_slug="another-event")
        with self.assertRaisesMessage(CommandError, "absolute"):
            self._run_command(manifest="manifest.json")

        self._assert_no_s3_calls()

    @patch("processing.management.commands.seed_local_event_preview_corpus.EXPECTED_PHOTO_COUNT", 1)
    def test_refuses_tampered_complete_manifest_before_inspecting_local_s3(self) -> None:
        self._make_valid_preview(photo_id=self.photo_id)
        manifest = self._manifest_payload(photos=[self._photo_row(photo_id=self.photo_id)])
        manifest["manifest_sha256"] = "0" * 64
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesMessage(CommandError, "manifest SHA-256"):
            self._run_command(apply=True)

        self._assert_no_s3_calls()

    @patch("processing.management.commands.seed_local_event_preview_corpus.EXPECTED_PHOTO_COUNT", 1)
    def test_refuses_noncanonical_preview_contract_before_inspecting_local_s3(self) -> None:
        self._make_valid_preview(photo_id=self.photo_id)
        manifest = self._manifest_payload(photos=[self._photo_row(photo_id=self.photo_id)])
        contract = manifest["preview_contract"]
        assert isinstance(contract, dict)
        contract["watermark"] = "tampered"
        self._write_payload(manifest)

        with self.assertRaisesMessage(CommandError, "preview contract"):
            self._run_command(apply=True)

        self._assert_no_s3_calls()

    @patch("processing.management.commands.seed_local_event_preview_corpus.EXPECTED_PHOTO_COUNT", 1)
    def test_refuses_incomplete_or_unresolved_manifest_before_inspecting_local_s3(self) -> None:
        cases: tuple[tuple[bool, list[dict[str, str]], str], ...] = (
            (False, [], "manifest is not a complete preview corpus"),
            (True, [{"photo_id": self.photo_id, "error": "failed"}], "unresolved rows"),
        )
        for complete, unresolved, message in cases:
            with self.subTest(complete=complete, unresolved=unresolved):
                self._write_preview_file(photo_id=self.photo_id)
                self._write_manifest(
                    photos=[self._photo_row(photo_id=self.photo_id)],
                    complete=complete,
                    unresolved=unresolved,
                )

                with self.assertRaisesMessage(CommandError, message):
                    self._run_command(apply=True)

        self._assert_no_s3_calls()

    @patch("processing.management.commands.seed_local_event_preview_corpus.EXPECTED_PHOTO_COUNT", 1)
    def test_refuses_exact_database_join_mismatch_before_inspecting_local_s3(self) -> None:
        self._write_preview_file(photo_id=self.photo_id)
        self._write_manifest(photos=[self._photo_row(photo_id=self.photo_id)])

        with self.assertRaisesMessage(CommandError, "database join"):
            self._run_command(apply=True)

        self._assert_no_s3_calls()

    @patch("processing.management.commands.seed_local_event_preview_corpus.EXPECTED_PHOTO_COUNT", 1)
    def test_refuses_size_or_checksum_mismatch_before_inspecting_local_s3(self) -> None:
        for field, value in (("byte_size", 1), ("sha256", "0" * 64)):
            with self.subTest(field=field):
                self._write_preview_file(photo_id=self.photo_id)
                row = self._photo_row(photo_id=self.photo_id)
                row[field] = value
                self._write_manifest(photos=[row])

                with self.assertRaisesMessage(CommandError, "size or SHA-256"):
                    self._run_command(apply=True)

        self._assert_no_s3_calls()

    def _run_command(
        self,
        *,
        event_slug: str | None = None,
        manifest: str | None = None,
        apply: bool = False,
        accepted_preview_cohort_hash_override: str | None = None,
    ) -> str:
        output = StringIO()
        arguments: list[object] = [
            "seed_local_event_preview_corpus",
            "--event-slug",
            event_slug or self.event_slug,
            "--manifest",
            manifest or str(self.manifest_path),
            "--files-root",
            str(self.files_root),
        ]
        if apply:
            arguments.append("--apply")
        manifest_payload = cast(
            dict[str, object], json.loads(self.manifest_path.read_text(encoding="utf-8"))
        )
        photos = cast(list[dict[str, object]], manifest_payload["photos"])
        approval = replace(
            FACE_EMBEDDING_QUALITY_APPROVAL,
            photo_count=len(photos),
            preview_manifest_hash=cast(str, manifest_payload["manifest_sha256"]),
            local_preview_projection_hash=_canonical_sha256(
                [
                    {
                        field: row[field]
                        for field in (
                            "byte_size",
                            "height",
                            "oriented_source_height",
                            "oriented_source_width",
                            "photo_id",
                            "sha256",
                            "width",
                        )
                    }
                    for row in photos
                ]
            ),
            accepted_preview_cohort_hash=accepted_preview_cohort_hash(self.event),
            accepted_preview_crosswalk_entry_count=len(photos),
            accepted_preview_crosswalk_sha_mismatch_count=len(photos),
        )
        if accepted_preview_cohort_hash_override is not None:
            approval = replace(
                approval,
                accepted_preview_cohort_hash=accepted_preview_cohort_hash_override,
            )
        with (
            override_settings(
                PRIVATE_MEDIA_S3_BUCKET="adaface-private",
                PRIVATE_MEDIA_S3_ENDPOINT_URL="http://minio:9000",
                PRIVATE_MEDIA_S3_REGION="us-east-1",
                PRIVATE_MEDIA_S3_ACCESS_KEY_ID="adaface-access",
                PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY="adaface-secret",
            ),
            patch("boto3.client", return_value=self.client),
            patch(
                "processing.management.commands.seed_local_event_preview_corpus."
                "FACE_EMBEDDING_QUALITY_APPROVAL",
                approval,
                create=True,
            ),
        ):
            call_command(*arguments, stdout=output)
        return output.getvalue()

    def _write_manifest(
        self,
        *,
        photos: list[dict[str, object]],
        complete: bool = True,
        unresolved: list[dict[str, str]] | None = None,
    ) -> None:
        self._write_payload(
            self._manifest_payload(
                photos=photos,
                complete=complete,
                unresolved=[] if unresolved is None else unresolved,
            )
        )

    def _write_payload(self, manifest: dict[str, object]) -> None:
        frozen = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        manifest["manifest_sha256"] = _canonical_sha256(frozen)
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def _manifest_payload(
        self,
        *,
        photos: list[dict[str, object]],
        complete: bool = True,
        unresolved: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "artifact_type": "preview-corpus",
            "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
            "event": {"id": "9", "slug": self.event_slug},
            "preview_contract": PREVIEW_CONTRACT.copy(),
            "production_contract_sha256": _canonical_sha256(PREVIEW_CONTRACT),
            "photos": photos,
            "unresolved": [] if unresolved is None else unresolved,
            "complete": complete,
            "manifest_sha256": "",
        }

    def _photo_row(self, *, photo_id: str) -> dict[str, object]:
        content = self._preview_content(photo_id)
        return {
            "photo_id": photo_id,
            "source_filename": f"photo-{photo_id}.jpg",
            "source_sha256": "a" * 64,
            "source_byte_size": 1,
            "preview_filename": f"{photo_id}.jpg",
            "byte_size": len(content),
            "width": 1600,
            "height": 1000,
            "oriented_source_width": 1600,
            "oriented_source_height": 1000,
            "sha256": hashlib.sha256(content).hexdigest(),
            "warnings": [],
        }

    def _write_preview_file(self, *, photo_id: str) -> None:
        (self.files_root / f"{photo_id}.jpg").write_bytes(self._preview_content(photo_id))

    def _preview_content(self, photo_id: str) -> bytes:
        return f"validated preview bytes for {photo_id}".encode()

    def _make_valid_preview(
        self,
        *,
        photo_id: str,
        derivative_sha256: str | None = None,
        byte_size: int | None = None,
        width: int = 1600,
        final_key: str | None = None,
    ) -> PhotoDerivative:
        self._write_preview_file(photo_id=photo_id)
        content = self._preview_content(photo_id)
        return self._accepted_preview(
            photo_id=photo_id,
            byte_size=len(content) if byte_size is None else byte_size,
            sha256=(
                hashlib.sha256(content).hexdigest()
                if derivative_sha256 is None
                else derivative_sha256
            ),
            width=width,
            final_key=final_key,
        )

    def _assert_no_s3_calls(self) -> None:
        self.assertEqual(self.client.head_calls, 0)
        self.assertEqual(self.client.put_calls, 0)

    def _accepted_preview(
        self,
        *,
        photo_id: str,
        byte_size: int,
        sha256: str,
        width: int = 1600,
        final_key: str | None = None,
    ) -> PhotoDerivative:
        user, _ = get_user_model().objects.get_or_create(username="local-corpus-owner")
        photo = Photo.objects.create(
            id=photo_id,
            event=self.event,
            uploaded_by=user,
            original_key=f"originals/{photo_id}",
            original_filename="preview-source.jpg",
            original_size=1,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            configuration={},
            configuration_hash="a" * 64,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            configuration={},
            configuration_hash="a" * 64,
        )
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            configuration={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        state, _ = PhotoProcessingState.objects.get_or_create(
            photo=photo,
            processor_type="generate_preview",
            defaults={
                "status": PhotoProcessingState.Status.SUCCEEDED,
                "current_run": run,
                "current_job": job,
                "current_attempt": attempt,
                "accepted_attempt": attempt,
                "succeeded_at": timezone.now(),
            },
        )
        PhotoProcessingState.objects.filter(pk=state.pk).update(
            status=PhotoProcessingState.Status.SUCCEEDED,
            current_run=run,
            current_job=job,
            current_attempt=attempt,
            accepted_attempt=attempt,
            succeeded_at=timezone.now(),
        )
        assert state.pk
        return PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-small-v1",
            final_key=final_key
            or preview_final_key(photo_id=photo_id, attempt_id=attempt.id, sha256=sha256),
            byte_size=byte_size,
            content_type="image/jpeg",
            width=width,
            height=1000,
            oriented_source_width=1600,
            oriented_source_height=1000,
            sha256=sha256,
            accepted_attempt=attempt,
        )


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
