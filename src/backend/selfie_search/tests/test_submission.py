import hashlib
import json
from datetime import date
from io import BytesIO
from pathlib import Path
from struct import pack
from unittest.mock import patch
from uuid import UUID
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
    EventProcessingRun,
    FaceEmbedding,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    CONTRACT_VERSION,
    FACE_EMBEDDING_CONFIGURATION,
    FACE_EMBEDDING_PROCESSOR_VERSION,
    PREVIEW_CONTRACT_VERSION,
    PREVIEW_FACE_EMBEDDING_PROCESSOR_VERSION,
)
from selfie_search.images import PreparedSelfie, prepare_selfie_image
from selfie_search.models import (
    SelfieSearch,
    SelfieSearchAttempt,
    SelfieSearchCandidate,
    SelfieSearchJob,
    SelfieSearchResult,
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
    gallery_search_eligible_photo_ids,
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
@override_settings(SELFIE_SEARCH_ENABLED=True, SELFIE_SEARCH_EMBEDDING_MODEL="sface")
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
            input_fingerprint={},
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
            input_fingerprint={},
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
        )
        return FaceEmbedding.objects.create(
            detection=detection,
            model_version=model,
            vector=vector if vector is not None else [0.0] * dimensions,
            metadata={},
        )

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
        self.assertFalse(SelfieSearchCandidate.objects.filter(search=created.search).exists())
        self.assertFalse(SelfieSearchCandidate.objects.filter(search=paid.search).exists())
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
        self.assertFalse(search.candidates.exists())
        self.assertEqual(
            list(search.results.values_list("detection__embedding", flat=True)),
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
            configuration=submission_configuration(content_type="image/jpeg", content_size=1),
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
@override_settings(SELFIE_SEARCH_ENABLED=True, SELFIE_SEARCH_EMBEDDING_MODEL="sface")
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
    ) -> FaceEmbedding:
        detection_kwargs = {"id": detection_id} if detection_id is not None else {}
        detection = PhotoFaceDetection.objects.create(
            **detection_kwargs,
            artifact=embedding.detection.artifact,
            attempt=embedding.detection.attempt,
            face_index=embedding.detection.face_index + 1,
            status=PhotoFaceDetection.Status.KEPT,
        )
        return FaceEmbedding.objects.create(
            detection=detection,
            model_version=embedding.model_version,
            vector=vector,
            metadata={},
        )

    def test_eligibility_requires_one_current_compatible_accepted_face(self) -> None:
        zero = self.make_photo(event=self.event, photo_id="zero")
        one_embedding = self.make_eligible_embedding(
            event=self.event,
            photo_id="one",
            vector=[1.0] + [0.0] * 127,
        )
        one = one_embedding.detection.attempt.photo
        two = self.make_photo(event=self.event, photo_id="two")
        two_embedding = self.make_eligible_embedding(event=self.event, photo_id="two", photo=two)
        self.make_additional_face(embedding=two_embedding, vector=[1.0] + [0.0] * 127)
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
        malformed_embedding = self.make_eligible_embedding(
            event=self.event, photo_id="malformed", dimensions=127
        )
        malformed = malformed_embedding.detection.attempt.photo
        foreign_embedding = self.make_eligible_embedding(event=self.other_event, photo_id="foreign")
        foreign = foreign_embedding.detection.attempt.photo

        eligible = gallery_search_eligible_photo_ids(
            event=self.event,
            photos=(
                zero,
                one,
                two,
                rejected,
                stale,
                stale_generation,
                malformed,
                foreign,
            ),
        )

        self.assertEqual(eligible, frozenset({one.id}))

    def test_unavailable_source_evidence_creates_no_search(self) -> None:
        zero = self.make_photo(event=self.event, photo_id="zero")
        two = self.make_photo(event=self.event, photo_id="two")
        two_embedding = self.make_eligible_embedding(event=self.event, photo_id="two", photo=two)
        self.make_additional_face(embedding=two_embedding, vector=[1.0] + [0.0] * 127)
        rejected = self.make_eligible_embedding(
            event=self.event, photo_id="rejected", accepted=False
        ).detection.attempt.photo
        stale = self.make_eligible_embedding(
            event=self.event, photo_id="stale"
        ).detection.attempt.photo
        PhotoProcessingState.objects.filter(photo=stale).update(accepted_attempt=None)
        stale_generation = self.make_eligible_embedding(
            event=self.event,
            photo_id="stale-generation",
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION + 1,
        ).detection.attempt.photo
        malformed = self.make_eligible_embedding(
            event=self.event, photo_id="malformed", vector=[0.0] * 128
        ).detection.attempt.photo
        foreign = self.make_eligible_embedding(
            event=self.other_event, photo_id="foreign"
        ).detection.attempt.photo

        for source in (zero, two, rejected, stale, stale_generation, malformed, foreign):
            with self.subTest(source=source.id):
                with self.assertRaises(GallerySearchUnavailable):
                    submit_gallery_photo_search(event=self.event, photo=source)
                self.assertEqual(SelfieSearch.objects.count(), 0)

    def test_creates_an_immediately_ready_event_scoped_immutable_result(self) -> None:
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

        created = submit_gallery_photo_search(event=self.event, photo=source, now=now)
        search = created.search
        rows = list(search.results.order_by("rank"))

        self.assertEqual(search.status, SelfieSearch.Status.READY)
        self.assertEqual(search.temporary_object_key, "")
        self.assertEqual(search.eligible_photo_count, 3)
        self.assertEqual(search.eligible_face_count, 4)
        self.assertEqual(search.matched_photo_count, 3)
        self.assertEqual(search.terminal_at, now)
        self.assertEqual(search.cleanup_confirmed_at, now)
        self.assertEqual(
            search.configuration["query_source"], {"kind": "gallery_photo", "photo_id": source.id}
        )
        configuration = json.dumps(search.configuration)
        self.assertNotIn("vector", configuration)
        self.assertNotIn(source.original_filename, configuration)
        self.assertNotIn(source.original_key, configuration)
        self.assertEqual(
            [(row.rank, row.photo_id, row.detection_id) for row in rows],
            [
                (1, source.id, source_embedding.detection_id),
                (2, a_embedding.detection.attempt.photo_id, a_embedding.detection_id),
                (3, b_embedding.detection.attempt.photo_id, b_best_embedding.detection_id),
            ],
        )
        self.assertAlmostEqual(rows[0].cosine_distance, 0.0)
        self.assertAlmostEqual(rows[1].cosine_distance, 0.01)
        self.assertAlmostEqual(rows[2].cosine_distance, 0.01)
        self.assertNotEqual(rows[2].detection_id, b_embedding.detection_id)
        self.assertFalse(SelfieSearchJob.objects.filter(search=search).exists())
        self.assertFalse(SelfieSearchAttempt.objects.exists())
        self.assertFalse(SelfieSearchCandidate.objects.filter(search=search).exists())
        rows[0].cosine_distance = 0.1
        with self.assertRaises(ValidationError):
            rows[0].save()

    def test_equal_distance_faces_select_the_lowest_detection_id_deterministically(self) -> None:
        source = self.make_eligible_embedding(
            event=self.event,
            photo_id="source",
            vector=[1.0] + [0.0] * 127,
        ).detection.attempt.photo
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

        search = submit_gallery_photo_search(event=self.event, photo=source).search

        self.assertEqual(
            search.results.get(photo_id="equal-distance").detection_id,
            expected_detection_id,
        )

    def test_ranking_or_result_persistence_failure_rolls_back(self) -> None:
        source = self.make_eligible_embedding(
            event=self.event,
            photo_id="source",
            vector=[1.0] + [0.0] * 127,
        ).detection.attempt.photo

        for target, error in (
            ("selfie_search.services.submission.rank_embeddings", RankingError("broken ranking")),
            (
                "selfie_search.services.submission.SelfieSearchResult.objects.bulk_create",
                IntegrityError,
            ),
        ):
            with self.subTest(target=target):
                with patch(target, side_effect=error):
                    with self.assertRaises(GallerySearchFailed):
                        submit_gallery_photo_search(event=self.event, photo=source)
                self.assertEqual(SelfieSearch.objects.count(), 0)
                self.assertEqual(SelfieSearchResult.objects.count(), 0)
