import hashlib
import json
from datetime import date
from io import BytesIO
from pathlib import Path
from struct import pack
from typing import cast
from unittest.mock import patch
from uuid import UUID, uuid4
from zlib import crc32

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from ingestion.storage import StorageUnavailable
from picflow.models import Event, Photo
from PIL import Image
from processing.models import (
    EventFaceEmbeddingActivation,
    EventProcessingRun,
    FaceEmbedding,
    FaceProcessingAttemptArtifact,
    PhotoDerivative,
    PhotoFaceDetection,
    PhotoFaceEmbeddingProjection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    CONTRACT_VERSION,
    FACE_EMBEDDING_CONFIGURATION,
    FACE_EMBEDDING_PROCESSOR_VERSION,
    GENERATE_PREVIEW_CONFIGURATION,
    PREVIEW_CONTRACT_VERSION,
    PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
    QUALITY_FACE_PROCESSOR_VERSION,
    FaceEmbeddingGenerationApproval,
    request_processor,
)
from processing.services.face_cohort import load_compatible_face_embeddings
from processing.services.face_quality import (
    activate_face_embedding_generation,
    candidate_face_embedding_generations,
)
from selfie_search.images import PreparedSelfie, prepare_selfie_image
from selfie_search.models import (
    SelfieSearch,
    SelfieSearchAttempt,
    SelfieSearchDirectEvidence,
    SelfieSearchJob,
)
from selfie_search.services.jobs import (
    ClaimedSearchJob,
    claim_search_job,
    complete_search_attempt,
)
from selfie_search.services.ranking import RankingError
from selfie_search.services.submission import (
    GallerySearchFailed,
    GallerySearchUnavailable,
    compatible_search_candidates,
    gallery_search_faces_by_photo,
    process_gallery_photo_search,
    resolve_public_search,
    submit_gallery_photo_search,
    submit_selfie_search,
)
from selfie_search.services.submission import (
    _configuration as submission_configuration,
)


class RecordingStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put(self, *, key: str, content: bytes, content_type: str):
        self.objects[key] = content
        return type(
            "Stored", (), {"key": key, "size": len(content), "content_type": content_type}
        )()

    def delete(self, *, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


class FailingStorage:
    def put(self, *, key: str, content: bytes, content_type: str):  # noqa: ARG002
        raise StorageUnavailable()


def valid_upload() -> SimpleUploadedFile:
    content = BytesIO()
    Image.new("RGB", (8, 8), color="white").save(content, format="JPEG")
    return SimpleUploadedFile("selfie.jpg", content.getvalue(), content_type="image/jpeg")


def valid_selfie() -> PreparedSelfie:
    return prepare_selfie_image(valid_upload())


def heic_selfie() -> PreparedSelfie:
    content = Path(__file__).parent.joinpath("fixtures", "iphone-oriented.heic").read_bytes()
    return prepare_selfie_image(
        SimpleUploadedFile("iphone.heic", content, content_type="image/heic")
    )


def decompression_bomb_upload() -> SimpleUploadedFile:
    ihdr = pack(">IIBBBBB", 100_000, 100_000, 8, 2, 0, 0, 0)
    content = b"\x89PNG\r\n\x1a\n" + pack(">I", len(ihdr)) + b"IHDR" + ihdr
    content += pack(">I", crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
    content += pack(">I", 0) + b"IEND" + pack(">I", crc32(b"IEND") & 0xFFFFFFFF)
    return SimpleUploadedFile("selfie.png", content, content_type="image/png")


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
@override_settings(SELFIE_SEARCH_EMBEDDING_MODEL="sface")
class SubmissionTests(TestCase):
    """The production break caught here is unaccepted or foreign data entering a search."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="selfie-submit-owner")
        self.event = self.make_event("main", "free")
        self.paid_event = self.make_event("paid", "paid")
        self.draft = self.make_event("draft", "free", published=False)

    def make_event(self, suffix: str, access_type: str, *, published: bool = True) -> Event:
        return Event.objects.create(
            name=f"Event {suffix}",
            slug=f"event-{suffix}",
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 30),
            city="Moscow",
            access_type=access_type,
            publication_status=(
                Event.PublicationStatus.PUBLISHED if published else Event.PublicationStatus.DRAFT
            ),
        )

    def make_eligible_embedding(
        self,
        *,
        event: Event,
        photo_id: str,
        photo: Photo | None = None,
        model: str = "sface",
        dimensions: int = 128,
        vector: list[float] | None = None,
        accepted: bool = True,
        contract_version: int = CONTRACT_VERSION,
        processor_version: int = FACE_EMBEDDING_PROCESSOR_VERSION,
        configuration: dict[str, object] | None = None,
        configuration_hash: str | None = None,
        detection_id: UUID | None = None,
        geometry: dict[str, object] | None = None,
        input_fingerprint: dict[str, int | str | None] | None = None,
    ) -> FaceEmbedding:
        configuration = configuration if configuration is not None else FACE_EMBEDDING_CONFIGURATION
        configuration_hash = (
            configuration_hash
            or hashlib.sha256(
                json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        photo = photo or Photo.objects.create(
            id=photo_id,
            event=event,
            uploaded_by=self.user,
            original_key=f"originals/{photo_id:0>32}"[-42:],
            original_filename=f"{photo_id}.jpg",
            original_size=1,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        run = EventProcessingRun.objects.create(
            event=event,
            contract_version=contract_version,
            processor_type="face_embedding",
            processor_version=processor_version,
            configuration=configuration,
            configuration_hash=configuration_hash,
        )
        job = ProcessingJob.objects.create(
            event=event,
            run=run,
            photo=photo,
            contract_version=contract_version,
            processor_type="face_embedding",
            processor_version=processor_version,
            configuration=configuration,
            configuration_hash=configuration_hash,
            input_fingerprint=input_fingerprint or {},
        )
        attempt = ProcessingAttempt.objects.create(
            event=event,
            run=run,
            job=job,
            photo=photo,
            contract_version=contract_version,
            processor_type="face_embedding",
            processor_version=processor_version,
            configuration=configuration,
            input_fingerprint=input_fingerprint or {},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=accepted,
        )
        PhotoProcessingState.objects.create(
            photo=photo,
            processor_type="face_embedding",
            status=PhotoProcessingState.Status.SUCCEEDED,
            current_run=run,
            current_job=job,
            current_attempt=attempt,
            accepted_attempt=attempt if accepted else None,
        )
        artifact = FaceProcessingAttemptArtifact.objects.create(attempt=attempt)
        detection_kwargs = {"id": detection_id} if detection_id is not None else {}
        detection = PhotoFaceDetection.objects.create(
            **detection_kwargs,
            artifact=artifact,
            attempt=attempt,
            face_index=0,
            status=PhotoFaceDetection.Status.KEPT,
            geometry=(
                geometry
                if geometry is not None
                else {
                    "coordinate_space": "preview-small-v1",
                    "pixel_width": 100,
                    "pixel_height": 100,
                    "bbox": [20, 20, 40, 40],
                }
            ),
        )
        embedding = FaceEmbedding.objects.create(
            detection=detection,
            model_version=model,
            vector=vector if vector is not None else [0.0] * dimensions,
            metadata={},
        )
        if accepted:
            PhotoFaceEmbeddingProjection.objects.create(
                photo=photo,
                contract_version=contract_version,
                processor_version=processor_version,
                configuration_hash=configuration_hash,
                accepted_attempt=attempt,
            )
        return embedding

    def test_published_free_and_paid_events_queue_without_freezing_face_candidates(self) -> None:
        self.make_eligible_embedding(event=self.event, photo_id="legacy")
        self.make_eligible_embedding(
            event=self.event,
            photo_id="preview",
            contract_version=PREVIEW_CONTRACT_VERSION,
            processor_version=PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
        )
        self.make_eligible_embedding(event=self.event, photo_id="stale")
        PhotoProcessingState.objects.filter(
            photo_id="stale", processor_type="face_embedding"
        ).update(accepted_attempt=None)
        self.make_eligible_embedding(event=self.event, photo_id="b", model="other")
        self.make_eligible_embedding(event=self.event, photo_id="c", dimensions=127)
        self.make_eligible_embedding(event=self.event, photo_id="d", accepted=False)
        self.make_eligible_embedding(
            event=self.event,
            photo_id="gen-version",
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION + 1,
        )
        self.make_eligible_embedding(
            event=self.event,
            photo_id="gen-config",
            configuration={**FACE_EMBEDDING_CONFIGURATION, "generation": "other"},
        )
        self.make_eligible_embedding(
            event=self.event,
            photo_id="hash-mismatch",
            configuration_hash="0" * 64,
        )
        self.make_eligible_embedding(
            event=self.event,
            photo_id="preview-version",
            contract_version=PREVIEW_CONTRACT_VERSION,
            processor_version=PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION + 1,
        )
        self.make_eligible_embedding(event=self.paid_event, photo_id="e")
        storage = RecordingStorage()

        created = submit_selfie_search(event=self.event, selfie=valid_selfie(), storage=storage)
        paid = submit_selfie_search(event=self.paid_event, selfie=valid_selfie(), storage=storage)

        self.assertEqual(SelfieSearchJob.objects.filter(search=created.search).count(), 1)
        self.assertEqual(created.search.eligible_photo_count, 0)
        self.assertEqual(created.search.eligible_face_count, 0)
        generations = created.search.configuration["gallery_face_embedding_generations"]
        self.assertEqual(
            [
                (
                    generation["contract_version"],
                    generation["processor_type"],
                    generation["processor_version"],
                )
                for generation in generations
            ],
            [
                (CONTRACT_VERSION, "face_embedding", FACE_EMBEDDING_PROCESSOR_VERSION),
                (
                    PREVIEW_CONTRACT_VERSION,
                    "face_embedding",
                    PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
                ),
            ],
        )
        self.assertTrue(
            all(
                generation["configuration"] == FACE_EMBEDDING_CONFIGURATION
                for generation in generations
            )
        )
        self.assertTrue(
            all(len(generation["configuration_hash"]) == 64 for generation in generations)
        )
        self.assertEqual(created.search.configuration["embedding_model"], "sface")
        self.assertEqual(created.search.configuration["embedding_dimensions"], 128)
        self.assertEqual(paid.search.event_id, self.paid_event.id)

    def test_new_search_freezes_the_events_exact_active_generation_set(self) -> None:
        generations = list(candidate_face_embedding_generations())
        generation = generations[0]
        configuration = generation["configuration"]
        configuration_hash = generation["configuration_hash"]
        assert isinstance(configuration, dict)
        assert isinstance(configuration_hash, str)
        photo = Photo.objects.create(
            id="active-v4",
            event=self.event,
            uploaded_by=self.user,
            original_key="originals/active-v4",
            original_filename="active-v4.jpg",
            original_size=1,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
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
                "object_content_type": photo.original_content_type,
                "object_etag": None,
                "media_kind": "original",
                "pixel_width": 1600,
                "pixel_height": 1000,
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
        derivative = PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-small-v1",
            final_key="previews/active-v4.jpg",
            byte_size=8,
            content_type="image/jpeg",
            width=1600,
            height=1000,
            oriented_source_width=1600,
            oriented_source_height=1000,
            sha256="a" * 64,
            accepted_attempt=preview_attempt,
        )
        preview_state.status = PhotoProcessingState.Status.SUCCEEDED
        preview_state.current_attempt = preview_attempt
        preview_state.accepted_attempt = preview_attempt
        preview_state.succeeded_at = timezone.now()
        preview_state.save(
            update_fields=[
                "status",
                "current_attempt",
                "accepted_attempt",
                "succeeded_at",
                "updated_at",
            ]
        )
        expected_candidate_fingerprint = {
            "object_key": derivative.final_key,
            "object_size": derivative.byte_size,
            "object_content_type": derivative.content_type,
            "object_etag": None,
            "media_kind": derivative.variant,
            "pixel_width": derivative.width,
            "pixel_height": derivative.height,
        }
        embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="active-v4",
            photo=photo,
            contract_version=3,
            processor_version=QUALITY_FACE_PROCESSOR_VERSION,
            configuration=configuration,
            configuration_hash=configuration_hash,
            input_fingerprint=expected_candidate_fingerprint,
            geometry={
                "coordinate_space": derivative.variant,
                "pixel_width": derivative.width,
                "pixel_height": derivative.height,
                "bbox": [320, 200, 640, 400],
            },
        )
        candidate_job = embedding.detection.attempt.job
        candidate_job.status = ProcessingJob.Status.SUCCEEDED
        candidate_job.completed_at = timezone.now()
        candidate_job.save(update_fields=["status", "completed_at"])
        self.assertEqual(candidate_job.input_fingerprint, expected_candidate_fingerprint)
        self.assertEqual(embedding.detection.geometry["pixel_width"], derivative.width)
        self.assertEqual(embedding.detection.geometry["pixel_height"], derivative.height)
        approval = FaceEmbeddingGenerationApproval(
            event_slug=self.event.slug,
            photo_count=1,
            configuration_hash=configuration_hash,
            preview_manifest_hash="a" * 64,
            comparison_manifest_hash="d" * 64,
            yunet_model_hash="b" * 64,
            sface_model_hash="c" * 64,
            job_count=1,
            attempt_count=1,
            projection_count=1,
            technical_failure_count=0,
            kept_face_count=1,
            quality_rejected_face_count=0,
            approved=True,
        )
        with patch("processing.services.enrollment.FACE_EMBEDDING_QUALITY_APPROVAL", approval):
            activate_face_embedding_generation(
                event=self.event,
                generations=generations,
                approved_configuration_hash=approval.configuration_hash,
                evaluation_report_hash=approval.comparison_manifest_hash,
                review_confirmed=True,
            )

            created = submit_selfie_search(
                event=self.event, selfie=valid_selfie(), storage=RecordingStorage()
            )

        self.assertEqual(
            created.search.configuration["gallery_face_embedding_generations"], generations
        )

    def test_new_search_fails_closed_without_storing_a_selfie_for_a_direct_unapproved_row(
        self,
    ) -> None:
        generations = list(candidate_face_embedding_generations())
        EventFaceEmbeddingActivation.objects.create(
            event=self.event,
            generations=generations,
            generation_set_hash=hashlib.sha256(
                json.dumps(generations, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            approved_configuration_hash=generations[0]["configuration_hash"],
            approved_evaluation_report_hash="d" * 64,
        )
        storage = RecordingStorage()

        with self.assertRaisesRegex(ValueError, "approved benchmark evidence"):
            submit_selfie_search(event=self.event, selfie=valid_selfie(), storage=storage)

        self.assertEqual(storage.objects, {})
        self.assertFalse(SelfieSearch.objects.filter(event=self.event).exists())

    def test_successful_worker_callback_ranks_without_persisting_candidates(self) -> None:
        embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="async-candidate",
            vector=[1.0] + [0.0] * 127,
        )
        storage = RecordingStorage()
        search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="a" * 64,
            temporary_object_key="selfie-search/async-callback",
            configuration=submission_configuration(
                event=self.event,
                content_type="image/jpeg",
                content_size=1024,
            ),
        )
        SelfieSearchJob.objects.create(search=search, configuration=search.configuration)
        claimed = claim_search_job(
            contract_version=1,
            processor_type="selfie_query",
            processor_version=1,
            worker_build="worker-test",
        )
        self.assertIsInstance(claimed, ClaimedSearchJob)
        assert isinstance(claimed, ClaimedSearchJob)

        with self.assertLogs("selfie_search.services.jobs", level="INFO") as logs:
            complete_search_attempt(
                claimed.attempt.id,
                result={"model": "sface", "embedding": [1.0] + [0.0] * 127},
                storage=storage,
            )
        search.refresh_from_db()

        self.assertEqual(search.status, SelfieSearch.Status.READY)
        self.assertEqual(search.eligible_photo_count, 1)
        self.assertEqual(search.eligible_face_count, 1)
        self.assertEqual(
            list(search.results.values_list("direct_evidence__detection__embedding", flat=True)),
            [embedding.id],
        )
        self.assertEqual(search.matched_photo_count, 1)
        events = [json.loads(line.split(":", 2)[2]) for line in logs.output]
        ranking = next(event for event in events if event["event"] == "selfie_ranking_finished")
        self.assertEqual(ranking["eligible_photo_count"], 1)
        self.assertEqual(ranking["eligible_face_count"], 1)
        self.assertEqual(ranking["matched_photo_count"], 1)

    def test_stores_only_prepared_canonical_bytes_and_type_for_a_heic_source(self) -> None:
        selfie = heic_selfie()
        source = Path(__file__).parent.joinpath("fixtures", "iphone-oriented.heic").read_bytes()
        storage = RecordingStorage()

        created = submit_selfie_search(event=self.event, selfie=selfie, storage=storage)

        self.assertEqual(storage.objects[next(iter(storage.objects))], selfie.content)
        self.assertNotEqual(storage.objects[next(iter(storage.objects))], source)
        self.assertEqual(created.search.configuration["content_type"], "image/jpeg")
        self.assertEqual(created.search.configuration["content_size"], len(selfie.content))
        configuration = json.dumps(created.search.configuration)
        self.assertNotIn("source_format", configuration)
        self.assertNotIn("source_size", configuration)
        self.assertNotIn("image/heic", configuration)

    def test_compatible_cohort_selects_only_fields_needed_for_ranking(self) -> None:
        self.make_eligible_embedding(
            event=self.event,
            photo_id="lightweight-candidate",
            vector=[1.0] + [0.0] * 127,
        )
        search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="f" * 64,
            temporary_object_key="selfie-search/lightweight",
            configuration=submission_configuration(
                event=self.event, content_type="image/jpeg", content_size=1
            ),
            configuration_hash="f" * 64,
        )

        with CaptureQueriesContext(connection) as queries:
            candidates = compatible_search_candidates(search)

        cohort_sql = next(
            query["sql"] for query in queries if 'FROM "processing_faceembedding"' in query["sql"]
        )
        self.assertNotIn('"processing_faceembedding"."metadata"', cohort_sql)
        self.assertNotIn('"processing_photofacedetection"."geometry"', cohort_sql)
        self.assertNotIn('"processing_processingattempt"."input_fingerprint"', cohort_sql)
        self.assertEqual(candidates[0].photo_id, "lightweight-candidate")

    def test_direct_cohort_uses_the_shared_processing_eligibility_loader(self) -> None:
        self.make_eligible_embedding(
            event=self.event,
            photo_id="shared-loader-candidate",
            vector=[1.0] + [0.0] * 127,
        )
        search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="g" * 64,
            temporary_object_key="selfie-search/shared-loader",
            configuration=submission_configuration(
                event=self.event, content_type="image/jpeg", content_size=1
            ),
            configuration_hash="g" * 64,
        )

        expected = load_compatible_face_embeddings(
            self.event,
            search.configuration["gallery_face_embedding_generations"],
            128,
        )

        candidates = compatible_search_candidates(search)

        self.assertEqual(
            [candidate.detection_id for candidate in candidates],
            [row.detection_id for row in expected],
        )

    def test_draft_event_is_rejected_without_upload_or_search(self) -> None:
        storage = RecordingStorage()

        with self.assertRaises(ValueError):
            submit_selfie_search(event=self.draft, selfie=valid_selfie(), storage=storage)

        self.assertEqual(storage.objects, {})
        self.assertFalse(SelfieSearch.objects.filter(event=self.draft).exists())

    def test_stores_only_a_sha256_digest_of_a_random_bearer_token(self) -> None:
        storage = RecordingStorage()

        created = submit_selfie_search(event=self.event, selfie=valid_selfie(), storage=storage)

        self.assertEqual(len(created.public_token), 43)
        self.assertEqual(len(created.search.public_token_digest), 64)
        self.assertNotEqual(created.search.public_token_digest, created.public_token)
        self.assertNotIn(created.public_token, str(created.search.__dict__))
        self.assertEqual(
            resolve_public_search(self.event.slug, created.public_token).pk, created.search.pk
        )

    def test_database_failure_removes_the_exact_uploaded_object(self) -> None:
        storage = RecordingStorage()

        with patch(
            "selfie_search.services.submission.SelfieSearch.objects.create",
            side_effect=IntegrityError,
        ):
            with self.assertRaises(IntegrityError):
                submit_selfie_search(event=self.event, selfie=valid_selfie(), storage=storage)

        self.assertEqual(storage.objects, {})
        self.assertEqual(len(storage.deleted), 1)

    def test_post_redirects_to_the_event_scoped_bearer_url(self) -> None:
        storage = RecordingStorage()
        with patch("selfie_search.views.TemporarySelfieStorage", return_value=storage):
            response = self.client.post(
                reverse("selfie_search:submit", kwargs={"event_slug": self.event.slug}),
                {"selfie": valid_upload()},
            )

        self.assertEqual(response.status_code, 302)
        self.assertRegex(
            response["Location"], rf"^/events/{self.event.slug}/selfie-search/[A-Za-z0-9_-]{{43}}/$"
        )

    @override_settings(SELFIE_FEEDBACK_ENABLED=True)
    def test_simultaneous_tabs_keep_independent_browser_correlations_without_session_state(
        self,
    ) -> None:
        correlations = ("a" * 32, "b" * 32)
        storage = RecordingStorage()

        with patch("selfie_search.views.TemporarySelfieStorage", return_value=storage):
            responses = tuple(
                self.client.post(
                    reverse("selfie_search:submit", kwargs={"event_slug": self.event.slug}),
                    {"selfie": valid_upload(), "feedback_correlation": correlation},
                )
                for correlation in correlations
            )

        for response, correlation in zip(responses, correlations, strict=True):
            self.assertEqual(response.status_code, 302)
            self.assertRegex(
                response["Location"],
                rf"^/events/{self.event.slug}/selfie-search/[A-Za-z0-9_-]{{43}}/"
                rf"\?feedback_correlation={correlation}$",
            )
        self.assertNotIn("selfie_feedback_correlations", self.client.session)
        self.assertNotIn("selfie_feedback_result_correlations", self.client.session)

    def test_invalid_post_stays_on_the_published_event_page(self) -> None:
        response = self.client.post(
            reverse("selfie_search:submit", kwargs={"event_slug": self.event.slug}),
            {
                "selfie": SimpleUploadedFile(
                    "selfie.jpg", b"not-an-image", content_type="image/jpeg"
                )
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertContains(response, self.event.name, status_code=422)
        self.assertContains(
            response,
            "Фотография повреждена. Выберите другой файл.",
            status_code=422,
        )
        self.assertContains(response, 'name="selfie"', status_code=422)

    def test_decompression_bomb_post_creates_no_search_job_or_temporary_object(self) -> None:
        storage = RecordingStorage()
        with patch("selfie_search.views.TemporarySelfieStorage", return_value=storage):
            response = self.client.post(
                reverse("selfie_search:submit", kwargs={"event_slug": self.event.slug}),
                {"selfie": decompression_bomb_upload()},
            )

        self.assertEqual(response.status_code, 422)
        self.assertContains(
            response,
            "Изображение слишком большое. Уменьшите его так, чтобы "
            "ширина × высота были не больше 25 млн пикселей — "
            "например, 5000 × 5000.",
            status_code=422,
        )
        self.assertFalse(SelfieSearch.objects.filter(event=self.event).exists())
        self.assertFalse(SelfieSearchJob.objects.exists())
        self.assertEqual(storage.objects, {})

    def test_storage_failure_stays_on_the_published_event_page(self) -> None:
        with patch("selfie_search.views.TemporarySelfieStorage", return_value=FailingStorage()):
            response = self.client.post(
                reverse("selfie_search:submit", kwargs={"event_slug": self.event.slug}),
                {"selfie": valid_upload()},
            )

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, self.event.name, status_code=503)
        self.assertContains(
            response,
            "Не удалось загрузить фотографию. Попробуйте ещё раз.",
            status_code=503,
        )
        self.assertContains(response, 'name="selfie"', status_code=503)


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
@override_settings(SELFIE_SEARCH_EMBEDDING_MODEL="sface")
class GalleryPhotoSubmissionTests(TestCase):
    """The production break caught here is accepting stale or ambiguous gallery face evidence."""

    make_event = SubmissionTests.make_event
    make_eligible_embedding = SubmissionTests.make_eligible_embedding

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="gallery-search-owner")
        self.event = self.make_event("gallery", "free")
        self.other_event = self.make_event("other-gallery", "free")

    def make_photo(self, *, event: Event, photo_id: str) -> Photo:
        return Photo.objects.create(
            id=photo_id,
            event=event,
            uploaded_by=self.user,
            original_key=f"originals/{photo_id:0>32}"[-42:],
            original_filename=f"{photo_id}.jpg",
            original_size=1,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def make_additional_face(
        self,
        *,
        embedding: FaceEmbedding,
        vector: list[float],
        detection_id: UUID | None = None,
        geometry: dict[str, object] | None = None,
    ) -> FaceEmbedding:
        detection_kwargs = {"id": detection_id} if detection_id is not None else {}
        detection = PhotoFaceDetection.objects.create(
            **detection_kwargs,
            artifact=embedding.detection.artifact,
            attempt=embedding.detection.attempt,
            face_index=embedding.detection.face_index + 1,
            status=PhotoFaceDetection.Status.KEPT,
            geometry=(
                geometry
                if geometry is not None
                else {
                    "coordinate_space": "preview-small-v1",
                    "pixel_width": 100,
                    "pixel_height": 100,
                    "bbox": [20, 20, 40, 40],
                }
            ),
        )
        return FaceEmbedding.objects.create(
            detection=detection,
            model_version=embedding.model_version,
            vector=vector,
            metadata={},
        )

    def test_gallery_presentation_returns_all_current_compatible_faces_for_page_photos(
        self,
    ) -> None:
        zero = self.make_photo(event=self.event, photo_id="zero")
        one_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="one",
            vector=[1.0] + [0.0] * 127,
        )
        one = one_embedding.detection.attempt.photo
        two = self.make_photo(event=self.event, photo_id="two")
        two_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="two",
            photo=two,
            vector=[1.0] + [0.0] * 127,
        )
        second = self.make_additional_face(embedding=two_embedding, vector=[1.0] + [0.0] * 127)
        rejected_embedding = self.make_eligible_embedding(
            event=self.event, photo_id="rejected", accepted=False
        )
        rejected = rejected_embedding.detection.attempt.photo
        stale_embedding = self.make_eligible_embedding(event=self.event, photo_id="stale")
        stale = stale_embedding.detection.attempt.photo
        PhotoProcessingState.objects.filter(photo=stale).update(accepted_attempt=None)
        stale_generation_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="stale-generation",
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION + 1,
        )
        stale_generation = stale_generation_embedding.detection.attempt.photo
        legacy_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="legacy-coordinates",
            geometry={
                "coordinate_space": "original-v1",
                "pixel_width": 100,
                "pixel_height": 100,
                "bbox": [20, 20, 40, 40],
            },
        )
        legacy = legacy_embedding.detection.attempt.photo
        malformed_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="malformed",
            geometry={
                "coordinate_space": "preview-small-v1",
                "pixel_width": 100,
                "pixel_height": 100,
                "bbox": [20, 20, 40],
            },
        )
        malformed = malformed_embedding.detection.attempt.photo
        foreign_embedding = self.make_eligible_embedding(event=self.other_event, photo_id="foreign")
        foreign = foreign_embedding.detection.attempt.photo
        wrong_length = self.make_eligible_embedding(
            event=self.event,
            photo_id="wrong-length",
            vector=[1.0] + [0.0] * 126,
        ).detection.attempt.photo
        nonnumeric = self.make_eligible_embedding(
            event=self.event,
            photo_id="nonnumeric",
            vector=cast(list[float], ["not-a-number"] + [0.0] * 127),
        ).detection.attempt.photo
        non_normalized = self.make_eligible_embedding(
            event=self.event,
            photo_id="non-normalized",
            vector=[0.5] + [0.0] * 127,
        ).detection.attempt.photo
        zero_vector = self.make_eligible_embedding(
            event=self.event,
            photo_id="zero-vector",
            vector=[0.0] * 128,
        ).detection.attempt.photo
        off_page = self.make_eligible_embedding(event=self.event, photo_id="off-page")

        with CaptureQueriesContext(connection) as queries:
            faces_by_photo = gallery_search_faces_by_photo(
                event=self.event,
                photos=(
                    zero,
                    one,
                    two,
                    rejected,
                    stale,
                    stale_generation,
                    legacy,
                    malformed,
                    foreign,
                    wrong_length,
                    nonnumeric,
                    non_normalized,
                    zero_vector,
                ),
            )

        self.assertEqual(len(queries), 2)
        cohort_query = next(
            query["sql"] for query in queries if 'FROM "processing_faceembedding"' in query["sql"]
        )
        select_clause = cohort_query.lower().split(" from ", maxsplit=1)[0]
        self.assertNotIn('"vector"', select_clause)
        self.assertEqual(set(faces_by_photo), {one.id, two.id})
        self.assertEqual(len(faces_by_photo[one.id]), 1)
        self.assertEqual(
            [face.detection_id for face in faces_by_photo[two.id]],
            [str(two_embedding.detection_id), str(second.detection_id)],
        )
        self.assertEqual([face.face_number for face in faces_by_photo[two.id]], [1, 2])
        self.assertNotIn(off_page.detection.attempt.photo_id, faces_by_photo)

    def test_unavailable_selected_detection_creates_no_search(self) -> None:
        zero = self.make_photo(event=self.event, photo_id="zero")
        selected = self.make_eligible_embedding(
            event=self.event, photo_id="selected", vector=[1.0] + [0.0] * 127
        )
        source = selected.detection.attempt.photo
        rejected_embedding = self.make_eligible_embedding(
            event=self.event, photo_id="rejected", accepted=False
        )
        rejected = rejected_embedding.detection.attempt.photo
        stale_embedding = self.make_eligible_embedding(event=self.event, photo_id="stale")
        stale = stale_embedding.detection.attempt.photo
        PhotoProcessingState.objects.filter(photo=stale).update(accepted_attempt=None)
        stale_generation_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="stale-generation",
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION + 1,
        )
        stale_generation = stale_generation_embedding.detection.attempt.photo
        legacy_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="legacy-coordinates",
            geometry={
                "coordinate_space": "original-v1",
                "pixel_width": 100,
                "pixel_height": 100,
                "bbox": [20, 20, 40, 40],
            },
        )
        legacy = legacy_embedding.detection.attempt.photo
        malformed_geometry_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="malformed-geometry",
            geometry={
                "coordinate_space": "preview-small-v1",
                "pixel_width": 100,
                "pixel_height": 100,
                "bbox": [20, 20, 40],
            },
        )
        malformed_geometry = malformed_geometry_embedding.detection.attempt.photo
        malformed_embedding = self.make_eligible_embedding(
            event=self.event, photo_id="malformed", vector=[0.0] * 128
        )
        malformed = malformed_embedding.detection.attempt.photo
        foreign_embedding = self.make_eligible_embedding(event=self.other_event, photo_id="foreign")

        for invalid_source, detection_id in (
            (zero, uuid4()),
            (rejected, rejected_embedding.detection_id),
            (stale, stale_embedding.detection_id),
            (stale_generation, stale_generation_embedding.detection_id),
            (legacy, legacy_embedding.detection_id),
            (malformed_geometry, malformed_geometry_embedding.detection_id),
            (malformed, malformed_embedding.detection_id),
            (source, foreign_embedding.detection_id),
        ):
            with self.subTest(source=invalid_source.id, detection_id=detection_id):
                with self.assertRaises(GallerySearchUnavailable):
                    submit_gallery_photo_search(
                        event=self.event,
                        photo=invalid_source,
                        detection_id=detection_id,
                    )
                self.assertEqual(SelfieSearch.objects.count(), 0)

    def test_submission_queues_gallery_search_without_results_or_worker_job(self) -> None:
        source_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="source",
            vector=[1.0] + [0.0] * 127,
        )
        source = source_embedding.detection.attempt.photo

        search = submit_gallery_photo_search(
            event=self.event,
            photo=source,
            detection_id=source_embedding.detection_id,
        ).search

        self.assertEqual(search.status, SelfieSearch.Status.QUEUED)
        self.assertEqual(search.temporary_object_key, "")
        self.assertEqual(search.results.count(), 0)
        self.assertFalse(SelfieSearchJob.objects.filter(search=search).exists())
        self.assertEqual(
            search.configuration["query_source"],
            {
                "kind": "gallery_photo",
                "photo_id": source.id,
                "detection_id": str(source_embedding.detection_id),
            },
        )

    def test_processing_queued_gallery_search_publishes_exact_immutable_result_once(self) -> None:
        source_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="source",
            vector=[1.0] + [0.0] * 127,
        )
        source = source_embedding.detection.attempt.photo
        a_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="a-match",
            vector=[0.99, 0.14106735979665894] + [0.0] * 126,
        )
        b_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="b-match",
            vector=[0.98, 0.198997487421324] + [0.0] * 126,
        )
        b_best_embedding = self.make_additional_face(
            embedding=b_embedding,
            vector=[0.99, 0.14106735979665894] + [0.0] * 126,
        )
        self.make_eligible_embedding(
            event=self.other_event,
            photo_id="other-event",
            vector=[1.0] + [0.0] * 127,
        )
        now = timezone.now()

        search = submit_gallery_photo_search(
            event=self.event,
            photo=source,
            detection_id=source_embedding.detection_id,
            now=now,
        ).search
        processed = process_gallery_photo_search(search=search, now=now)
        replay = process_gallery_photo_search(search=search, now=now)
        search.refresh_from_db()
        rows = list(search.results.order_by("rank"))

        self.assertEqual(processed.pk, search.pk)
        self.assertEqual(replay.pk, search.pk)
        self.assertEqual(search.status, SelfieSearch.Status.READY)
        self.assertEqual(search.temporary_object_key, "")
        self.assertEqual(search.eligible_photo_count, 3)
        self.assertEqual(search.eligible_face_count, 4)
        self.assertEqual(search.matched_photo_count, 3)
        self.assertEqual(search.terminal_at, now)
        self.assertEqual(search.cleanup_confirmed_at, now)
        self.assertEqual(
            search.configuration["query_source"],
            {
                "kind": "gallery_photo",
                "photo_id": source.id,
                "detection_id": str(source_embedding.detection_id),
            },
        )
        configuration = json.dumps(search.configuration)
        self.assertNotIn("vector", configuration)
        self.assertNotIn(source.original_filename, configuration)
        self.assertNotIn(source.original_key, configuration)
        self.assertEqual(
            [(row.rank, row.photo_id, row.direct_evidence.detection_id) for row in rows],
            [
                (1, source.id, source_embedding.detection_id),
                (2, a_embedding.detection.attempt.photo_id, a_embedding.detection_id),
                (3, b_embedding.detection.attempt.photo_id, b_best_embedding.detection_id),
            ],
        )
        self.assertAlmostEqual(rows[0].direct_evidence.cosine_distance, 0.0)
        self.assertAlmostEqual(rows[1].direct_evidence.cosine_distance, 0.01)
        self.assertAlmostEqual(rows[2].direct_evidence.cosine_distance, 0.01)
        self.assertNotEqual(rows[2].direct_evidence.detection_id, b_embedding.detection_id)
        self.assertFalse(SelfieSearchJob.objects.filter(search=search).exists())
        self.assertFalse(SelfieSearchAttempt.objects.exists())
        self.assertEqual(
            SelfieSearchDirectEvidence.objects.filter(result__search=search).count(), 3
        )
        rows[0].direct_evidence.cosine_distance = 0.1
        with self.assertRaises(ValidationError):
            rows[0].direct_evidence.save()

    def test_each_selected_face_uses_its_own_query_embedding(self) -> None:
        first = self.make_eligible_embedding(
            event=self.event,
            photo_id="source",
            vector=[1.0] + [0.0] * 127,
        )
        second = self.make_additional_face(
            embedding=first,
            vector=[0.0, 1.0] + [0.0] * 126,
        )
        source = first.detection.attempt.photo

        first_search = submit_gallery_photo_search(
            event=self.event,
            photo=source,
            detection_id=first.detection_id,
        ).search
        second_search = submit_gallery_photo_search(
            event=self.event,
            photo=source,
            detection_id=second.detection_id,
        ).search
        process_gallery_photo_search(search=first_search)
        process_gallery_photo_search(search=second_search)

        self.assertEqual(
            first_search.results.get(photo=source).direct_evidence.detection_id,
            first.detection_id,
        )
        self.assertEqual(
            second_search.results.get(photo=source).direct_evidence.detection_id,
            second.detection_id,
        )
        self.assertEqual(
            first_search.configuration["query_source"]["detection_id"], str(first.detection_id)
        )
        self.assertEqual(
            second_search.configuration["query_source"]["detection_id"], str(second.detection_id)
        )

    def test_equal_distance_faces_select_the_lowest_detection_id_deterministically(self) -> None:
        source_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="source",
            vector=[1.0] + [0.0] * 127,
        )
        source = source_embedding.detection.attempt.photo
        first_detection_id = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
        expected_detection_id = UUID("00000000-0000-0000-0000-000000000001")
        match_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="equal-distance",
            vector=[0.99, 0.14106735979665894] + [0.0] * 126,
            detection_id=first_detection_id,
        )
        self.make_additional_face(
            embedding=match_embedding,
            vector=[0.99, 0.14106735979665894] + [0.0] * 126,
            detection_id=expected_detection_id,
        )

        search = submit_gallery_photo_search(
            event=self.event,
            photo=source,
            detection_id=source_embedding.detection_id,
        ).search
        process_gallery_photo_search(search=search)

        self.assertEqual(
            search.results.get(photo_id="equal-distance").direct_evidence.detection_id,
            expected_detection_id,
        )

    def test_ranking_failure_transitions_gallery_search_to_terminal_failure(self) -> None:
        source_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="source",
            vector=[1.0] + [0.0] * 127,
        )
        source = source_embedding.detection.attempt.photo

        with patch(
            "selfie_search.services.submission.rank_embeddings",
            side_effect=RankingError("broken ranking"),
        ):
            search = submit_gallery_photo_search(
                event=self.event, photo=source, detection_id=source_embedding.detection_id
            ).search
            process_gallery_photo_search(search=search)

        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.FAILED)
        self.assertEqual(search.results.count(), 0)

    def test_database_failure_leaves_gallery_search_queued_for_retry(self) -> None:
        source_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="source",
            vector=[1.0] + [0.0] * 127,
        )
        source = source_embedding.detection.attempt.photo
        search = submit_gallery_photo_search(
            event=self.event, photo=source, detection_id=source_embedding.detection_id
        ).search

        with patch(
            "selfie_search.services.submission.SelfieSearchResult.objects.bulk_create",
            side_effect=IntegrityError,
        ):
            with self.assertRaises(GallerySearchFailed):
                process_gallery_photo_search(search=search)

        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.QUEUED)
        self.assertEqual(search.results.count(), 0)

    def test_processing_uses_the_frozen_projection_after_mutable_state_changes(self) -> None:
        source_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="source",
            vector=[1.0] + [0.0] * 127,
        )
        source = source_embedding.detection.attempt.photo
        search = submit_gallery_photo_search(
            event=self.event,
            photo=source,
            detection_id=source_embedding.detection_id,
        ).search
        PhotoProcessingState.objects.filter(photo=source).update(accepted_attempt=None)

        process_gallery_photo_search(search=search)

        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.READY)
        self.assertGreater(search.results.count(), 0)
