import json
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from photo_worker.contracts import Claim, FaceEmbeddingFace, FaceEmbeddingResult
from photo_worker.runner import Worker, WorkerConfig
from picflow.models import Event, Photo

from processing.management.commands.run_face_embedding_benchmark import _validated_source_jobs
from processing.models import (
    EventProcessingRun,
    FaceEmbedding,
    PhotoFaceDetection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import request_face_embedding_enqueue


class FaceEmbeddingBenchmarkCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="benchmark-owner")
        self.event = Event.objects.create(
            name="Benchmark event",
            slug="benchmark-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

    def photo(self, number: int) -> Photo:
        return Photo.objects.create(
            id=f"benchmark-{number:03d}",
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{number:03d}.jpg",
            original_filename=f"{number:03d}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    @override_settings(
        PHOTO_PROCESSING_ENABLED=True,
        PHOTO_PROCESSING_WORKER_TOKEN="benchmark-worker-token",
    )
    def test_claim_worker_completion_persists_only_benchmark_metrics_without_face_rows(
        self,
    ) -> None:
        photo = self.photo(999)
        photo.original_key = "originals/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        photo.save(update_fields=["original_key"])
        call_command(
            "run_face_embedding_benchmark",
            event=self.event.slug,
            limit=1,
            label="baseline",
        )
        grant = SimpleNamespace(
            url="https://storage.example.test/object?signature=secret",
            expires_at=timezone.now() + timedelta(seconds=30),
        )
        with patch(
            "processing.views.ExactObjectDownloadStorage.create_download_grant",
            return_value=grant,
        ):
            claim_response = self.client.post(
                "/internal/photo-processing/v1/claim",
                data=json.dumps(
                    {
                        "contract_version": 3,
                        "processor_type": "face_embedding_benchmark",
                        "processor_version": 1,
                        "worker_build": "benchmark-worker",
                        "lease_seconds": 120,
                    }
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer benchmark-worker-token",
            )
        self.assertEqual(claim_response.status_code, 200)
        claimed = Claim.from_response(claim_response.json())

        class Client:
            def claim_job(self, **_kwargs: object) -> Claim:
                return claimed

            def download(
                self,
                _url: str,
                destination: Path,
                *,
                max_bytes: int,
                expected_size: int,
                expected_content_type: str,
                expected_etag: str | None = None,
            ) -> int:
                from PIL import Image

                image = Image.new("RGB", (2, 2), "white")
                image.save(destination, "JPEG")
                image.close()
                return expected_size

            def heartbeat(
                self, _attempt_id: str, *, lease_seconds: int, response_max_bytes: int
            ) -> None:
                pass

            def refresh_download(self, _attempt_id: str, *, response_max_bytes: int) -> str:
                raise AssertionError("benchmark download grant should not need refresh")

            def upload_preview(
                self,
                _url: str,
                _source: Path,
                *,
                content_type: str,
                expected_size: int,
                max_bytes: int,
                response_max_bytes: int,
            ) -> None:
                raise AssertionError("benchmark worker does not upload previews")

            def complete(
                self, attempt_id: str, payload: dict[str, object], **_kwargs: object
            ) -> None:
                response = self_case.client.post(
                    f"/internal/photo-processing/v1/attempts/{attempt_id}/complete",
                    data=json.dumps(payload),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer benchmark-worker-token",
                )
                self_case.assertEqual(response.status_code, 200)

            def fail(
                self, _attempt_id: str, _payload: dict[str, object], *, response_max_bytes: int
            ) -> None:
                raise AssertionError("benchmark worker should complete successfully")

        class LeaseKeeper:
            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def raise_if_lost(self) -> None:
                pass

        self_case = self
        result = FaceEmbeddingResult(
            model="sface",
            faces=(
                FaceEmbeddingFace(
                    index=0,
                    bbox=(1.0, 2.0, 32.0, 32.0),
                    confidence=0.9,
                    landmarks=((1.0, 2.0),) * 5,
                    embedding=(0.1,) * 128,
                ),
            ),
            has_single_query_face_usable=True,
            warnings=(),
            timings={
                "decode_ms": 1,
                "model_load_ms": 2,
                "detect_ms": 3,
                "embed_ms": 4,
                "total_ms": 10,
            },
        )
        with (
            TemporaryDirectory() as temporary,
            patch("photo_worker.runner.extract_face_embeddings", return_value=result) as extract,
        ):
            with self.assertLogs("photo_worker.runner", level="INFO") as logs:
                Worker(
                    Client(),
                    WorkerConfig(
                        worker_build="benchmark-worker",
                        lease_seconds=120,
                        temp_dir=Path(temporary),
                        processor_identities=("3/face_embedding_benchmark/1",),
                    ),
                    lease_keeper_factory=lambda *_args: LeaseKeeper(),
                ).run_once()

        extract.assert_called_once()
        attempt = ProcessingAttempt.objects.get(processor_type="face_embedding_benchmark")
        self.assertEqual(
            attempt.result,
            {
                "model": "sface",
                "face_count": 1,
                "warnings": [],
                "timings": result.timings,
            },
        )
        self.assertFalse(PhotoFaceDetection.objects.filter(attempt=attempt).exists())
        self.assertFalse(FaceEmbedding.objects.filter(detection__attempt=attempt).exists())
        run = EventProcessingRun.objects.get(pk=attempt.run_id)
        self.assertNotIn(photo.id, json.dumps(run.report))
        self.assertNotIn("photo_id", json.dumps(run.report))
        self.assertNotIn(photo.id, "\n".join(logs.output))

    @override_settings(PHOTO_PROCESSING_FACE_ENABLED=True)
    def test_event_command_creates_exact_ordered_benchmark_cohort_without_mutating_face_state(
        self,
    ) -> None:
        photos = [self.photo(number) for number in range(114)]
        ordinary_state = request_face_embedding_enqueue(photos[0])
        assert ordinary_state.current_job is not None
        ordinary_attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=ordinary_state.current_job.run,
            job=ordinary_state.current_job,
            photo=photos[0],
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration=ordinary_state.current_job.configuration,
            input_fingerprint=ordinary_state.current_job.input_fingerprint,
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            result={"existing": "face-result"},
        )
        ordinary_state.status = PhotoProcessingState.Status.SUCCEEDED
        ordinary_state.accepted_attempt = ordinary_attempt
        ordinary_state.save(update_fields=["status", "accepted_attempt", "updated_at"])

        call_command(
            "run_face_embedding_benchmark",
            event=self.event.slug,
            limit=114,
            label="baseline",
        )

        run = EventProcessingRun.objects.get(processor_type="face_embedding_benchmark")
        self.assertEqual(run.status, EventProcessingRun.Status.COLLECTING)
        self.assertEqual(
            list(run.jobs.order_by("photo_id").values_list("photo_id", flat=True)),
            [photo.id for photo in photos],
        )
        self.assertEqual(run.jobs.count(), 114)
        ordinary_state.refresh_from_db()
        ordinary_attempt.refresh_from_db()
        self.assertEqual(ordinary_state.processor_type, "face_embedding")
        self.assertEqual(ordinary_state.status, PhotoProcessingState.Status.SUCCEEDED)
        self.assertEqual(ordinary_attempt.result, {"existing": "face-result"})

    def test_replay_copies_closed_benchmark_membership_and_rejects_other_processors(self) -> None:
        photos = [self.photo(number) for number in range(2)]
        source = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=3,
            processor_type="face_embedding_benchmark",
            processor_version=1,
            configuration={"label": "baseline", "source_mode": "event"},
            configuration_hash="a" * 64,
            status=EventProcessingRun.Status.COLLECTING,
        )
        for photo in reversed(photos):
            source.jobs.create(
                event=self.event,
                photo=photo,
                contract_version=3,
                processor_type="face_embedding_benchmark",
                processor_version=1,
                configuration=source.configuration,
                configuration_hash=source.configuration_hash,
                input_fingerprint={
                    "original_key": photo.original_key,
                    "original_size": photo.original_size,
                    "original_content_type": photo.original_content_type,
                    "verified_source_etag": None,
                    "version_evidence": "unavailable",
                },
            )
        source.status = EventProcessingRun.Status.CLOSED
        source.closed_at = timezone.now()
        source.save(update_fields=["status", "closed_at"])

        call_command("run_face_embedding_benchmark", source_run=str(source.id), label="two-workers")

        replay = EventProcessingRun.objects.exclude(pk=source.pk).get()
        self.assertEqual(
            list(replay.jobs.order_by("created_at").values_list("photo_id", flat=True)),
            list(source.jobs.order_by("created_at").values_list("photo_id", flat=True)),
        )
        ordinary = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash="b" * 64,
            status=EventProcessingRun.Status.CLOSED,
            closed_at=timezone.now(),
        )
        with self.assertRaisesMessage(CommandError, "benchmark"):
            call_command(
                "run_face_embedding_benchmark", source_run=str(ordinary.id), label="reject"
            )
        self.assertEqual(EventProcessingRun.objects.count(), 3)

    def test_replay_validation_rejects_source_job_that_does_not_match_benchmark_run(self) -> None:
        source = SimpleNamespace(
            event_id=self.event.id,
            contract_version=3,
            processor_type="face_embedding_benchmark",
            processor_version=1,
            configuration={"benchmark": "baseline"},
            configuration_hash="a" * 64,
        )
        job = SimpleNamespace(
            event_id=self.event.id,
            contract_version=3,
            processor_type="face_embedding",
            processor_version=1,
            configuration=source.configuration,
            configuration_hash=source.configuration_hash,
            photo=SimpleNamespace(
                event_id=self.event.id,
                original_key="originals/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                original_size=10,
                original_content_type="image/jpeg",
            ),
        )

        with self.assertRaisesMessage(CommandError, "source job"):
            _validated_source_jobs(cast(EventProcessingRun, source), [cast(ProcessingJob, job)])
