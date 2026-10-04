import hashlib
import json
from datetime import date
from io import BytesIO
from pathlib import Path
from queue import Queue
from struct import pack
from threading import Event as ThreadEvent
from threading import Thread
from unittest.mock import patch
from uuid import UUID, uuid4
from zlib import crc32

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from feature_flags.models import FeatureFlag
from feature_flags.registry import PAID_EVENTS
from feature_flags.states import FEATURE_FLAG_ON
from feature_flags.testing import override_feature_flags
from ingestion.storage import StorageUnavailable
from picflow.gallery_media_projection import publish_gallery_media
from picflow.models import Event, Photo
from PIL import Image
from processing.models import (
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    EventProcessingRun,
    FaceEmbeddingVector,
    FaceProcessingAttemptArtifact,
    PhotoDerivative,
    PhotoFaceDetection,
    PhotoFaceEmbeddingProjection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    FACE_EMBEDDING_CONFIGURATION,
    QUALITY_FACE_CONTRACT_VERSION,
    QUALITY_FACE_PROCESSOR_VERSION,
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
from selfie_search.services.read_selection import rank_selected_direct
from selfie_search.services.submission import (
    GallerySearchFailed,
    GallerySearchUnavailable,
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
class SubmissionTests(TestCase):
    """The production break caught here is unaccepted or foreign data entering a search."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="selfie-submit-owner")
        self.event = self.make_event("main", "free")
        self.paid_event = self.make_event("paid", "paid")
        self.draft = self.make_event("draft", "free", published=False)

    def make_event(self, suffix: str, access_type: str, *, published: bool = True) -> Event:
        values: dict[str, object] = {
            "name": f"Event {suffix}",
            "slug": f"event-{suffix}",
            "start_date": date(2026, 7, 30),
            "end_date": date(2026, 7, 30),
            "city": "Moscow",
            "access_type": access_type,
            "publication_status": (
                Event.PublicationStatus.PUBLISHED if published else Event.PublicationStatus.DRAFT
            ),
        }
        if access_type == Event.AccessType.PAID:
            values["price_per_photo_kopecks"] = 30000
        return Event.objects.create(**values)

    def make_eligible_embedding(
        self,
        *,
        event: Event,
        photo_id: str,
        photo: Photo | None = None,
        model: str = "adaface-ir18-webface4m",
        dimensions: int = 512,
        vector: list[float] | None = None,
        accepted: bool = True,
        contract_version: int = QUALITY_FACE_CONTRACT_VERSION,
        processor_version: int = QUALITY_FACE_PROCESSOR_VERSION,
        configuration: dict[str, object] | None = None,
        configuration_hash: str | None = None,
        detection_id: UUID | None = None,
        geometry: dict[str, object] | None = None,
        input_fingerprint: dict[str, int | str | None] | None = None,
    ) -> FaceEmbeddingVector:
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
        embedding = FaceEmbeddingVector.objects.create(
            detection=detection,
            model_version=model,
            vector=vector if vector is not None else [1.0] + [0.0] * (dimensions - 1),
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

    def test_vector_only_gallery_source_presentation_submission_and_completion(self) -> None:
        from processing.services.face_quality import active_face_embedding_generations

        generation = active_face_embedding_generations(self.event)[1]
        configuration = generation["configuration"]
        assert isinstance(configuration, dict)
        configuration["embedding_storage"] = "vector_only"
        generation["configuration_hash"] = hashlib.sha256(
            json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        configuration_hash = generation["configuration_hash"]
        assert isinstance(configuration_hash, str)
        native = self.make_eligible_embedding(
            event=self.event,
            photo_id="vector-only-source",
            model="adaface-ir18-webface4m",
            dimensions=512,
            vector=[1.0] + [0.0] * 511,
            contract_version=3,
            processor_version=5,
            configuration=configuration,
            configuration_hash=configuration_hash,
        )
        photo = native.detection.attempt.photo
        # Exercise the real event generation selector through presentation and submission.
        faces = gallery_search_faces_by_photo(event=self.event, photos=(photo,))
        self.assertEqual(
            [crop.detection_id for crop in faces.get(photo.pk, ())], [str(native.detection_id)]
        )
        created = submit_gallery_photo_search(
            event=self.event, photo=photo, detection_id=native.detection_id, user=self.user
        )
        result = process_gallery_photo_search(search=created.search)
        self.assertEqual(result.status, SelfieSearch.Status.READY)
        self.assertEqual(result.results.get().photo_id, photo.pk)

    def test_staff_context_is_server_only_frozen_and_callback_uses_native_reader(self) -> None:
        from selfie_search.services.vector_ranking import rank_vector_direct

        self.user.is_staff = True
        selfie = PreparedSelfie(
            content=b"prepared", content_type="image/jpeg", source_size=8, source_format="jpeg"
        )
        search = submit_selfie_search(
            event=self.event, selfie=selfie, storage=RecordingStorage(), user=self.user
        ).search
        self.assertEqual(search.job.configuration, search.configuration)
        self.assertEqual(
            search.configuration_hash,
            hashlib.sha256(
                json.dumps(search.configuration, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        )
        claimed = claim_search_job(
            contract_version=1,
            processor_type="selfie_query",
            processor_version=2,
        )
        assert isinstance(claimed, ClaimedSearchJob)
        with (
            patch(
                "selfie_search.services.read_selection.rank_vector_direct", wraps=rank_vector_direct
            ) as native,
        ):
            complete_search_attempt(
                claimed.attempt.id,
                result={"model": "adaface-ir18-webface4m", "embedding": [1.0] + [0.0] * 511},
                storage=RecordingStorage(),
            )
        native.assert_called_once()
        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.SEARCH_UNAVAILABLE)

    def test_active_staff_submission_routes_callback_without_worker_review_context(self) -> None:
        from selfie_search.services.vector_ranking import rank_vector_direct

        self.user.is_staff = True
        search = submit_selfie_search(
            event=self.event,
            selfie=PreparedSelfie(
                content=b"prepared", content_type="image/jpeg", source_size=8, source_format="jpeg"
            ),
            storage=RecordingStorage(),
            user=self.user,
        ).search
        claimed = claim_search_job(
            contract_version=1,
            processor_type="selfie_query",
            processor_version=2,
        )
        assert isinstance(claimed, ClaimedSearchJob)
        with (
            patch(
                "selfie_search.services.read_selection.rank_vector_direct", wraps=rank_vector_direct
            ) as native,
        ):
            complete_search_attempt(
                claimed.attempt.id,
                result={"model": "adaface-ir18-webface4m", "embedding": [1.0] + [0.0] * 511},
                storage=RecordingStorage(),
            )
        native.assert_called_once()
        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.SEARCH_UNAVAILABLE)

    def test_native_gallery_source_uses_native_vector_without_legacy_hydration(self) -> None:
        embedding = self.make_eligible_embedding(
            event=self.event, photo_id="source", vector=[1.0] + [0.0] * 511
        )
        with CaptureQueriesContext(connection) as queries:
            search = submit_gallery_photo_search(
                event=self.event,
                photo=embedding.detection.attempt.photo,
                detection_id=embedding.detection_id,
                user=self.user,
            ).search
            process_gallery_photo_search(search=search)
        self.assertTrue(any("processing_faceembeddingvector" in row["sql"] for row in queries))
        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.READY)
        self.assertEqual(search.results.get().photo_id, "source")
        with patch(
            "selfie_search.services.submission.rank_selected_direct", side_effect=AssertionError
        ):
            process_gallery_photo_search(search=search)

    def test_native_gallery_database_failure_preserves_queued_atomic_retry(self) -> None:
        embedding = self.make_eligible_embedding(
            event=self.event, photo_id="source", vector=[1.0] + [0.0] * 511
        )
        search = submit_gallery_photo_search(
            event=self.event,
            photo=embedding.detection.attempt.photo,
            detection_id=embedding.detection_id,
            user=self.user,
        ).search
        with (
            patch(
                "selfie_search.services.read_selection.rank_vector_direct",
                side_effect=DatabaseError,
            ),
            self.assertRaises(GallerySearchFailed),
        ):
            process_gallery_photo_search(search=search)
        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.QUEUED)
        self.assertEqual(search.results.count(), 0)

    def test_published_free_and_paid_events_queue_without_freezing_face_candidates(self) -> None:
        self.make_eligible_embedding(event=self.event, photo_id="current")
        self.make_eligible_embedding(event=self.event, photo_id="stale")
        PhotoProcessingState.objects.filter(
            photo_id="stale", processor_type="face_embedding"
        ).update(accepted_attempt=None)
        self.make_eligible_embedding(
            event=self.event,
            photo_id="gen-version",
            processor_version=QUALITY_FACE_PROCESSOR_VERSION + 1,
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
        self.make_eligible_embedding(event=self.paid_event, photo_id="e")
        storage = RecordingStorage()

        with override_feature_flags({PAID_EVENTS: FEATURE_FLAG_ON}):
            created = submit_selfie_search(
                event=self.event, selfie=valid_selfie(), storage=storage, user=self.user
            )
            paid = submit_selfie_search(
                event=self.paid_event, selfie=valid_selfie(), storage=storage, user=self.user
            )

        self.assertEqual(SelfieSearchJob.objects.filter(search=created.search).count(), 1)
        self.assertEqual(created.search.eligible_photo_count, 0)
        self.assertEqual(created.search.eligible_face_count, 0)
        generations = created.search.configuration["gallery_face_embedding_generations"]
        self.assertEqual(created.search.configuration["embedding_model"], "adaface-ir18-webface4m")
        self.assertEqual(created.search.configuration["embedding_dimensions"], 512)
        self.assertEqual(created.search.configuration["cosine_distance_threshold"], 0.42)
        self.assertEqual(len(generations), 2)
        self.assertEqual(paid.search.event_id, self.paid_event.id)

    def test_new_search_freezes_both_accepted_current_adaface_identities(self) -> None:
        from processing.services.face_quality import active_face_embedding_generations

        created = submit_selfie_search(
            event=self.event, selfie=valid_selfie(), storage=RecordingStorage(), user=self.user
        )
        self.assertEqual(created.search.configuration["embedding_model"], "adaface-ir18-webface4m")
        self.assertEqual(created.search.configuration["embedding_dimensions"], 512)
        self.assertEqual(created.search.configuration["cosine_distance_threshold"], 0.42)
        self.assertEqual(
            created.search.configuration["gallery_face_embedding_generations"],
            list(active_face_embedding_generations(self.event)),
        )

    def test_successful_worker_callback_ranks_without_persisting_candidates(self) -> None:
        embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="async-candidate",
            vector=[1.0] + [0.0] * 511,
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
            processor_version=2,
        )
        self.assertIsInstance(claimed, ClaimedSearchJob)
        assert isinstance(claimed, ClaimedSearchJob)

        with self.assertLogs("selfie_search.services.jobs", level="INFO") as logs:
            complete_search_attempt(
                claimed.attempt.id,
                result={"model": "adaface-ir18-webface4m", "embedding": [1.0] + [0.0] * 511},
                storage=storage,
            )
        search.refresh_from_db()

        self.assertEqual(search.status, SelfieSearch.Status.READY)
        self.assertEqual(search.eligible_photo_count, 1)
        self.assertEqual(search.eligible_face_count, 1)
        self.assertEqual(
            list(
                search.results.values_list(
                    "direct_evidence__detection__embedding_vector", flat=True
                )
            ),
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

        created = submit_selfie_search(
            event=self.event, selfie=selfie, storage=storage, user=self.user
        )

        self.assertEqual(storage.objects[next(iter(storage.objects))], selfie.content)
        self.assertNotEqual(storage.objects[next(iter(storage.objects))], source)
        self.assertEqual(created.search.configuration["content_type"], "image/jpeg")
        self.assertEqual(created.search.configuration["content_size"], len(selfie.content))
        configuration = json.dumps(created.search.configuration)
        self.assertNotIn("source_format", configuration)
        self.assertNotIn("source_size", configuration)
        self.assertNotIn("image/heic", configuration)

    def test_draft_event_is_rejected_without_upload_or_search(self) -> None:
        storage = RecordingStorage()

        with self.assertRaises(ValueError):
            submit_selfie_search(
                event=self.draft, selfie=valid_selfie(), storage=storage, user=self.user
            )

        self.assertEqual(storage.objects, {})
        self.assertFalse(SelfieSearch.objects.filter(event=self.draft).exists())

    def test_active_staff_can_submit_draft_but_not_unavailable_selfie_searches(self) -> None:
        staff_user = get_user_model().objects.create_user(
            username="selfie-service-staff", is_staff=True
        )
        draft_storage = RecordingStorage()

        created = submit_selfie_search(
            event=self.draft,
            selfie=valid_selfie(),
            storage=draft_storage,
            user=staff_user,
        )
        self.draft.publication_status = Event.PublicationStatus.UNAVAILABLE
        self.draft.save(update_fields=["publication_status"])
        unavailable_storage = RecordingStorage()
        with self.assertRaises(ValueError):
            submit_selfie_search(
                event=self.draft,
                selfie=valid_selfie(),
                storage=unavailable_storage,
                user=staff_user,
            )

        self.assertEqual(created.search.event_id, self.draft.pk)
        self.assertTrue(draft_storage.objects)
        self.assertEqual(unavailable_storage.objects, {})

    def test_stores_only_a_sha256_digest_of_a_random_bearer_token(self) -> None:
        storage = RecordingStorage()

        created = submit_selfie_search(
            event=self.event, selfie=valid_selfie(), storage=storage, user=self.user
        )

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
                submit_selfie_search(
                    event=self.event, selfie=valid_selfie(), storage=storage, user=self.user
                )

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
class GalleryPhotoSubmissionTests(TestCase):
    """The production break caught here is accepting stale or ambiguous gallery face evidence."""

    make_event = SubmissionTests.make_event
    make_eligible_embedding = SubmissionTests.make_eligible_embedding

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="gallery-search-owner")
        self.event = self.make_event("gallery", "free")
        self.other_event = self.make_event("other-gallery", "free")

    def test_gallery_processing_expansion_gate_uses_only_global_on_state(self) -> None:
        source = self.make_eligible_embedding(
            event=self.event, photo_id="global-gate-source", vector=[1.0] + [0.0] * 511
        )
        for state, expected in (
            (None, "disabled"),
            (FeatureFlag.State.OFF, "disabled"),
            (FeatureFlag.State.STAFF, "disabled"),
            (FeatureFlag.State.ON, "corpus_unavailable"),
        ):
            with self.subTest(state=state):
                FeatureFlag.objects.filter(key="selfie-search-cluster-expansion").delete()
                if state is not None:
                    FeatureFlag.objects.create(
                        key="selfie-search-cluster-expansion",
                        description="Expand selfie results through face clusters",
                        state=state,
                    )
                created = submit_gallery_photo_search(
                    event=self.event,
                    photo=source.detection.attempt.photo,
                    detection_id=source.detection_id,
                    user=self.user,
                )
                processed = process_gallery_photo_search(search=created.search)
                self.assertEqual(processed.status, SelfieSearch.Status.READY)
                self.assertEqual(processed.cluster_expansion_outcome, expected)
                self.assertEqual(processed.cluster_expanded_photo_count, 0)
                self.assertEqual(
                    list(processed.results.values_list("photo_id", flat=True)),
                    [source.detection.attempt.photo_id],
                )

    def test_gallery_submission_locks_event_before_freezing_generation(self) -> None:
        embedding = self.make_eligible_embedding(
            event=self.event, photo_id="event-lock-source", vector=[1.0] + [0.0] * 511
        )
        with CaptureQueriesContext(connection) as queries:
            created = submit_gallery_photo_search(
                event=self.event,
                photo=embedding.detection.attempt.photo,
                detection_id=embedding.detection_id,
                user=self.user,
            )
        self.assertEqual(created.search.status, "queued")
        self.assertTrue(
            any(
                '"picflow_event"' in query["sql"] and "FOR UPDATE" in query["sql"]
                for query in queries
            )
        )

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
        embedding: FaceEmbeddingVector,
        vector: list[float],
        detection_id: UUID | None = None,
        geometry: dict[str, object] | None = None,
    ) -> FaceEmbeddingVector:
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
        embedding = FaceEmbeddingVector.objects.create(
            detection=detection,
            model_version=embedding.model_version,
            vector=vector,
            metadata={},
        )
        return embedding

    def publish_watermark(self, photo: Photo) -> None:
        configuration = {"generate_watermarked_preview": {"variant": "preview-watermarked-v1"}}
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=2,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash="d" * 64,
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash=run.configuration_hash,
            input_fingerprint={},
            status=ProcessingJob.Status.SUCCEEDED,
            completed_at=timezone.now(),
        )
        attempt = ProcessingAttempt.objects.create(
            event=photo.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        PhotoProcessingState.objects.create(
            photo=photo,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            status=PhotoProcessingState.Status.SUCCEEDED,
            current_run=run,
            current_job=job,
            current_attempt=attempt,
            accepted_attempt=attempt,
            succeeded_at=timezone.now(),
        )
        derivative = PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-watermarked-v1",
            final_key=f"derivatives/previews/{photo.pk}/preview-watermarked-v1/accepted.jpg",
            byte_size=10,
            content_type="image/jpeg",
            width=10,
            height=10,
            oriented_source_width=10,
            oriented_source_height=10,
            accepted_attempt=attempt,
        )
        photo.gallery_media_projection = publish_gallery_media(derivative)

    def test_gallery_presentation_returns_all_current_compatible_faces_for_page_photos(
        self,
    ) -> None:
        zero = self.make_photo(event=self.event, photo_id="zero")
        one_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="one",
            vector=[1.0] + [0.0] * 511,
        )
        one = one_embedding.detection.attempt.photo
        two = self.make_photo(event=self.event, photo_id="two")
        two_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="two",
            photo=two,
            vector=[1.0] + [0.0] * 511,
        )
        second = self.make_additional_face(embedding=two_embedding, vector=[1.0] + [0.0] * 511)
        stale_embedding = self.make_eligible_embedding(event=self.event, photo_id="stale")
        stale = stale_embedding.detection.attempt.photo
        PhotoProcessingState.objects.filter(photo=stale).update(accepted_attempt=None)
        stale_generation_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="stale-generation",
            processor_version=QUALITY_FACE_PROCESSOR_VERSION + 1,
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
        off_page = self.make_eligible_embedding(event=self.event, photo_id="off-page")

        with CaptureQueriesContext(connection) as queries:
            faces_by_photo = gallery_search_faces_by_photo(
                event=self.event,
                photos=(
                    zero,
                    one,
                    two,
                    stale,
                    stale_generation,
                    legacy,
                    malformed,
                    foreign,
                ),
            )

        self.assertEqual(len(queries), 1)
        cohort_query = next(
            query["sql"]
            for query in queries
            if 'FROM "processing_faceembeddingvector"' in query["sql"]
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
            event=self.event, photo_id="selected", vector=[1.0] + [0.0] * 511
        )
        source = selected.detection.attempt.photo
        stale_embedding = self.make_eligible_embedding(event=self.event, photo_id="stale")
        stale = stale_embedding.detection.attempt.photo
        PhotoProcessingState.objects.filter(photo=stale).update(accepted_attempt=None)
        stale_generation_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="stale-generation",
            processor_version=QUALITY_FACE_PROCESSOR_VERSION + 1,
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
        foreign_embedding = self.make_eligible_embedding(event=self.other_event, photo_id="foreign")

        for invalid_source, detection_id in (
            (zero, uuid4()),
            (stale, stale_embedding.detection_id),
            (stale_generation, stale_generation_embedding.detection_id),
            (legacy, legacy_embedding.detection_id),
            (malformed_geometry, malformed_geometry_embedding.detection_id),
            (source, foreign_embedding.detection_id),
        ):
            with self.subTest(source=invalid_source.id, detection_id=detection_id):
                with self.assertRaises(GallerySearchUnavailable):
                    submit_gallery_photo_search(
                        event=self.event,
                        photo=invalid_source,
                        detection_id=detection_id,
                        user=self.user,
                    )
                self.assertEqual(SelfieSearch.objects.count(), 0)

    def test_submission_queues_gallery_search_without_results_or_worker_job(self) -> None:
        source_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="source",
            vector=[1.0] + [0.0] * 511,
        )
        source = source_embedding.detection.attempt.photo

        search = submit_gallery_photo_search(
            event=self.event,
            photo=source,
            detection_id=source_embedding.detection_id,
            user=self.user,
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

    def test_paid_watermarked_source_requires_explicit_gate_through_submit_and_process(
        self,
    ) -> None:
        paid_event = self.make_event("paid-gallery-source", "paid")
        photo = self.make_photo(event=paid_event, photo_id="paid-gallery-source")
        photo.processing_generation = Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1
        photo.gallery_media_policy = Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
        photo.save(update_fields=["processing_generation", "gallery_media_policy"])
        source_embedding = self.make_eligible_embedding(
            event=paid_event,
            photo_id=photo.pk,
            photo=photo,
            vector=[1.0] + [0.0] * 511,
        )
        self.publish_watermark(photo)

        with self.assertRaises(GallerySearchUnavailable):
            submit_gallery_photo_search(
                event=paid_event,
                photo=photo,
                detection_id=source_embedding.detection_id,
                user=self.user,
                paid_watermarked_previews_enabled=False,
            )
        with override_feature_flags({PAID_EVENTS: FEATURE_FLAG_ON}):
            created = submit_gallery_photo_search(
                event=paid_event,
                photo=photo,
                detection_id=source_embedding.detection_id,
                user=self.user,
                paid_watermarked_previews_enabled=True,
            )
        processed = process_gallery_photo_search(
            search=created.search,
            paid_watermarked_previews_enabled=True,
        )

        self.assertEqual(processed.status, SelfieSearch.Status.READY)
        self.assertEqual(list(processed.results.values_list("photo_id", flat=True)), [photo.pk])

    def test_staff_preview_gallery_search_can_finish_after_visibility_is_removed(self) -> None:
        draft = self.make_event("draft-gallery-worker", "free", published=False)
        source_embedding = self.make_eligible_embedding(
            event=draft,
            photo_id="draft-gallery-worker-source",
            vector=[1.0] + [0.0] * 511,
        )
        staff_user = get_user_model().objects.create_user(
            username="gallery-worker-staff", is_staff=True
        )
        with self.assertRaises(GallerySearchUnavailable):
            submit_gallery_photo_search(
                event=draft,
                photo=source_embedding.detection.attempt.photo,
                detection_id=source_embedding.detection_id,
                user=self.user,
            )
        search = submit_gallery_photo_search(
            event=draft,
            photo=source_embedding.detection.attempt.photo,
            detection_id=source_embedding.detection_id,
            user=staff_user,
        ).search
        draft.publication_status = Event.PublicationStatus.UNAVAILABLE
        draft.save(update_fields=["publication_status"])

        processed = process_gallery_photo_search(search=search)
        with self.assertRaises(GallerySearchUnavailable):
            submit_gallery_photo_search(
                event=draft,
                photo=source_embedding.detection.attempt.photo,
                detection_id=source_embedding.detection_id,
                user=staff_user,
            )

        self.assertEqual(processed.status, SelfieSearch.Status.READY)
        self.assertGreater(processed.results.count(), 0)
        self.assertEqual(SelfieSearch.objects.filter(event=draft).count(), 1)

    def test_processing_queued_gallery_search_publishes_exact_immutable_result_once(self) -> None:
        source_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="source",
            vector=[1.0] + [0.0] * 511,
        )
        source = source_embedding.detection.attempt.photo
        a_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="a-match",
            vector=[0.99, 0.14106735979665894] + [0.0] * 510,
        )
        b_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="b-match",
            vector=[0.98, 0.198997487421324] + [0.0] * 510,
        )
        b_best_embedding = self.make_additional_face(
            embedding=b_embedding,
            vector=[0.99, 0.14106735979665894] + [0.0] * 510,
        )
        self.make_eligible_embedding(
            event=self.other_event,
            photo_id="other-event",
            vector=[1.0] + [0.0] * 511,
        )
        now = timezone.now()

        search = submit_gallery_photo_search(
            event=self.event,
            photo=source,
            detection_id=source_embedding.detection_id,
            now=now,
            user=self.user,
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
        self.assertNotIn('"vector":', configuration)
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

    def test_gallery_completion_loads_projection_cohort_without_wide_sort(self) -> None:
        embedding = self.make_eligible_embedding(
            event=self.event, photo_id="projection-source", vector=[1.0] + [0.0] * 511
        )
        search = submit_gallery_photo_search(
            event=self.event,
            photo=embedding.detection.attempt.photo,
            detection_id=embedding.detection_id,
            user=self.user,
        ).search
        with CaptureQueriesContext(connection) as queries:
            processed = process_gallery_photo_search(search=search)
        cohort = next(
            item["sql"] for item in queries if "WITH eligible AS MATERIALIZED" in item["sql"]
        )
        self.assertEqual(processed.status, SelfieSearch.Status.READY)
        self.assertIn('FROM "processing_photofaceembeddingprojection"', cohort)

    def test_each_selected_face_uses_its_own_query_embedding(self) -> None:
        first = self.make_eligible_embedding(
            event=self.event,
            photo_id="source",
            vector=[1.0] + [0.0] * 511,
        )
        second = self.make_additional_face(
            embedding=first,
            vector=[0.0, 1.0] + [0.0] * 510,
        )
        source = first.detection.attempt.photo

        first_search = submit_gallery_photo_search(
            event=self.event,
            photo=source,
            detection_id=first.detection_id,
            user=self.user,
        ).search
        second_search = submit_gallery_photo_search(
            event=self.event,
            photo=source,
            detection_id=second.detection_id,
            user=self.user,
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
            vector=[1.0] + [0.0] * 511,
        )
        source = source_embedding.detection.attempt.photo
        first_detection_id = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
        expected_detection_id = UUID("00000000-0000-0000-0000-000000000001")
        match_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="equal-distance",
            vector=[0.99, 0.14106735979665894] + [0.0] * 510,
            detection_id=first_detection_id,
        )
        self.make_additional_face(
            embedding=match_embedding,
            vector=[0.99, 0.14106735979665894] + [0.0] * 510,
            detection_id=expected_detection_id,
        )

        search = submit_gallery_photo_search(
            event=self.event,
            photo=source,
            detection_id=source_embedding.detection_id,
            user=self.user,
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
            vector=[1.0] + [0.0] * 511,
        )
        source = source_embedding.detection.attempt.photo

        with patch(
            "selfie_search.services.submission.rank_selected_direct",
            side_effect=RankingError("broken ranking"),
        ):
            search = submit_gallery_photo_search(
                event=self.event,
                photo=source,
                detection_id=source_embedding.detection_id,
                user=self.user,
            ).search
            process_gallery_photo_search(search=search)

        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.FAILED)
        self.assertEqual(search.results.count(), 0)

    def test_database_failure_leaves_gallery_search_queued_for_retry(self) -> None:
        source_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="source",
            vector=[1.0] + [0.0] * 511,
        )
        source = source_embedding.detection.attempt.photo
        search = submit_gallery_photo_search(
            event=self.event,
            photo=source,
            detection_id=source_embedding.detection_id,
            user=self.user,
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

    def test_processing_fails_closed_after_current_state_is_removed(self) -> None:
        source_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="source",
            vector=[1.0] + [0.0] * 511,
        )
        source = source_embedding.detection.attempt.photo
        search = submit_gallery_photo_search(
            event=self.event,
            photo=source,
            detection_id=source_embedding.detection_id,
            user=self.user,
        ).search
        PhotoProcessingState.objects.filter(photo=source).update(accepted_attempt=None)

        process_gallery_photo_search(search=search)

        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.SEARCH_UNAVAILABLE)
        self.assertEqual(search.results.count(), 0)


class GalleryCompletionConcurrencyTests(TransactionTestCase):
    """Gallery cohort work must release rows and revalidate before publication."""

    setUp = GalleryPhotoSubmissionTests.setUp
    make_event = SubmissionTests.make_event
    make_eligible_embedding = SubmissionTests.make_eligible_embedding

    def test_paused_gallery_cohort_and_ranking_leave_event_and_search_unlocked(self) -> None:
        for phase in ("cohort", "ranking"):
            with self.subTest(phase=phase):
                self._complete_while_paused(phase=phase)

    def test_gallery_source_hidden_during_ranking_is_not_published(self) -> None:
        self._complete_while_paused(phase="ranking", change="hide_source")

    def test_gallery_configuration_changed_during_ranking_is_not_published(self) -> None:
        self._complete_while_paused(phase="ranking", change="configuration")

    def test_gallery_terminal_result_wins_over_inflight_ranking(self) -> None:
        self._complete_while_paused(phase="ranking", change="terminal")

    def _complete_while_paused(self, *, phase: str, change: str = "") -> None:
        embedding = self.make_eligible_embedding(
            event=self.event, photo_id=f"paused-{phase}-{change}", vector=[1.0] + [0.0] * 511
        )
        search = submit_gallery_photo_search(
            event=self.event,
            photo=embedding.detection.attempt.photo,
            detection_id=embedding.detection_id,
            user=self.user,
        ).search
        started = ThreadEvent()
        resume = ThreadEvent()
        errors: Queue[BaseException] = Queue()

        def pause():
            started.set()
            if not resume.wait(timeout=10):
                raise TimeoutError("gallery test did not resume computation")

        def query_wrapper(execute, sql, params, many, context):
            if (
                "processing_faceembeddingvector" in sql
                and '"vector"' in sql.split(" FROM ")[0]
                and "LIMIT 1" in sql
            ):
                pause()
            return execute(sql, params, many, context)

        def paused_rank(*args, **kwargs):
            pause()
            return rank_selected_direct(*args, **kwargs)

        def complete() -> None:
            close_old_connections()
            try:
                if phase == "cohort":
                    with connection.execute_wrapper(query_wrapper):
                        process_gallery_photo_search(search=search)
                else:
                    with patch(
                        "selfie_search.services.submission.rank_selected_direct", paused_rank
                    ):
                        process_gallery_photo_search(search=search)
            except BaseException as error:
                errors.put(error)
            finally:
                close_old_connections()

        thread = Thread(target=complete)
        thread.start()
        lock_errors = []
        try:
            self.assertTrue(started.wait(timeout=10))
            for model, pk in ((Event, self.event.pk), (SelfieSearch, search.pk)):
                try:
                    with transaction.atomic():
                        model.objects.select_for_update(nowait=True).get(pk=pk)
                except DatabaseError as error:
                    lock_errors.append(str(error))
            if not lock_errors:
                if change == "hide_source":
                    Photo.objects.filter(pk=embedding.detection.attempt.photo_id).update(
                        is_hidden=True
                    )
                elif change == "configuration":
                    configuration = {**search.configuration, "cosine_distance_threshold": 0.1}
                    SelfieSearch.objects.filter(pk=search.pk).update(configuration=configuration)
                elif change == "terminal":
                    SelfieSearch.objects.filter(pk=search.pk).update(
                        status=SelfieSearch.Status.FAILED, failure_code="concurrent_failure"
                    )
        finally:
            resume.set()
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(lock_errors, [])
        self.assertTrue(errors.empty(), list(errors.queue))
        search.refresh_from_db()
        if change:
            expected = (
                SelfieSearch.Status.FAILED
                if change == "terminal"
                else SelfieSearch.Status.SEARCH_UNAVAILABLE
            )
            self.assertEqual(search.status, expected)
            self.assertEqual(search.results.count(), 0)
            if change == "terminal":
                self.assertEqual(search.failure_code, "concurrent_failure")
        else:
            self.assertEqual(search.status, SelfieSearch.Status.READY)
            self.assertGreater(search.results.count(), 0)
