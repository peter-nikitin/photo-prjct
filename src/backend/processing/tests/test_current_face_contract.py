from datetime import date
from typing import cast

from django.test import TestCase, override_settings
from picflow.models import Event, Photo

from processing import contracts
from processing.models import (
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services import face_quality
from processing.services.enrollment import (
    FACE_EMBEDDING_CONFIGURATION,
    GENERATE_PREVIEW_CONFIGURATION,
    request_face_embedding_enqueue,
    request_processor,
)
from processing.services.face_cohort import load_compatible_face_embeddings
from processing.views import _claim, _valid_face_embedding_result


class CurrentFaceContractTests(TestCase):
    def test_only_current_face_contract_is_declared(self) -> None:
        self.assertEqual(
            contracts.FACE_EMBEDDING_CONTRACT,
            contracts.ProcessorContract("face_embedding", 3, 5),
        )
        self.assertFalse(hasattr(contracts, "FACE_EMBEDDING_BENCHMARK_CONTRACT"))
        self.assertFalse(hasattr(contracts, "PREVIEW_FACE_EMBEDDING_CONTRACT"))
        self.assertFalse(hasattr(contracts, "QUALITY_FACE_EMBEDDING_CONTRACT"))

    def setUp(self) -> None:
        self.event = Event.objects.create(
            name="Current face event",
            slug="current-face-event",
            start_date=date(2026, 10, 4),
            end_date=date(2026, 10, 4),
            city="Moscow",
        )

    def test_unavailable_event_has_empty_adaface_cohort(self) -> None:
        self.assertEqual(self.event.publication_status, Event.PublicationStatus.UNAVAILABLE)
        generations = face_quality.active_face_embedding_generations(self.event)
        self.assertEqual(len(generations), 2)
        self.assertEqual({item["model"] for item in generations}, {"adaface-ir18-webface4m"})
        self.assertEqual(load_compatible_face_embeddings(self.event, generations, 512), ())

    @override_settings(PHOTO_PROCESSING_FACE_ENABLED=True)
    def test_new_face_job_uses_pinned_adaface_v5(self) -> None:
        from django.contrib.auth import get_user_model
        from django.utils import timezone

        user = get_user_model().objects.create_user(username="current-face-owner")
        photo = Photo.objects.create(
            id="current-face-photo",
            event=self.event,
            src="",
            uploaded_by=user,
            original_key="originals/current-face-photo",
            original_filename="current-face-photo.jpg",
            original_size=100,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )
        preview_state = request_processor(
            photo,
            processor_type="generate_preview",
            contract_version=2,
            processor_version=1,
            configuration=GENERATE_PREVIEW_CONFIGURATION,
            input_fingerprint={
                "object_key": photo.original_key,
                "object_size": photo.original_size,
                "object_content_type": "image/jpeg",
                "object_etag": None,
                "media_kind": "original",
                "pixel_width": 3200,
                "pixel_height": 2000,
            },
        )
        assert preview_state.current_job is not None
        preview_attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=preview_state.current_job.run,
            job=preview_state.current_job,
            photo=photo,
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            configuration=GENERATE_PREVIEW_CONFIGURATION,
            input_fingerprint=preview_state.current_job.input_fingerprint,
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-small-v1",
            final_key=(
                f"derivatives/previews/{photo.pk}/preview-small-v1/"
                f"{preview_attempt.id}-{'a' * 64}.jpg"
            ),
            byte_size=1024,
            content_type="image/jpeg",
            width=1600,
            height=1000,
            oriented_source_width=3200,
            oriented_source_height=2000,
            sha256="a" * 64,
            accepted_attempt=preview_attempt,
        )
        preview_state.status = PhotoProcessingState.Status.SUCCEEDED
        preview_state.accepted_attempt = preview_attempt
        preview_state.succeeded_at = timezone.now()
        preview_state.save(
            update_fields=["status", "accepted_attempt", "succeeded_at", "updated_at"]
        )
        state = request_face_embedding_enqueue(photo)
        self.assertIsNotNone(state.current_job)
        assert state.current_job is not None
        self.assertTrue(hasattr(face_quality, "current_face_embedding_generation"))
        generation = face_quality.current_face_embedding_generation()
        self.assertEqual(
            (state.current_job.contract_version, state.current_job.processor_version), (3, 5)
        )
        self.assertEqual(state.current_job.configuration_hash, generation["configuration_hash"])
        self.assertEqual(
            state.current_job.configuration["face_embedding"]["model"], "adaface-ir18-webface4m"
        )

    def test_event_has_no_runtime_model_selector(self) -> None:
        self.assertFalse(hasattr(Event, "FaceSearchGeneration"))
        self.assertFalse(
            any(field.name == "face_search_generation" for field in Event._meta.fields)
        )

    def test_old_face_callback_schema_is_rejected(self) -> None:
        old_result = {"model": "sface", "face_count": 0, "faces": [], "warnings": []}
        self.assertFalse(_valid_face_embedding_result(old_result, contract_version=1))

    def test_old_face_job_is_not_claimed(self) -> None:
        from copy import deepcopy

        from django.contrib.auth import get_user_model
        from django.utils import timezone

        user = get_user_model().objects.create_user(username="old-face-owner")
        photo = Photo.objects.create(
            id="old-face-photo",
            event=self.event,
            src="",
            uploaded_by=user,
            original_key="originals/old-face-photo",
            original_filename="old-face-photo.jpg",
            original_size=100,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        configuration = deepcopy(FACE_EMBEDDING_CONFIGURATION)
        cast(dict[str, object], configuration["face_embedding"])["model"] = "sface"
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration=configuration,
            configuration_hash="0" * 64,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration=configuration,
            configuration_hash="0" * 64,
            input_fingerprint={},
        )
        PhotoProcessingState.objects.update_or_create(
            photo=photo,
            processor_type="face_embedding",
            defaults={
                "status": PhotoProcessingState.Status.QUEUED,
                "current_run": run,
                "current_job": job,
                "queued_at": timezone.now(),
            },
        )
        with self.assertRaisesRegex(ValueError, "unsupported processor contract"):
            _claim(
                {
                    "contract_version": 1,
                    "processor_type": "face_embedding",
                    "processor_version": 1,
                    "lease_seconds": 120,
                }
            )
        job.refresh_from_db()
        self.assertEqual(job.status, ProcessingJob.Status.QUEUED)
