import hashlib
from datetime import date
from io import BytesIO
from typing import Any
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from feature_flags.registry import PAID_EVENTS, PAID_WATERMARKED_PREVIEWS
from feature_flags.states import FEATURE_FLAG_OFF, FEATURE_FLAG_ON
from feature_flags.testing import override_feature_flags
from ingestion.storage import StorageUnavailable
from picflow.gallery_media_projection import publish_gallery_media
from picflow.models import Event, EventFolder, Photo
from PIL import Image
from processing.models import (
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    BibReading,
    EventProcessingRun,
    FaceProcessingAttemptArtifact,
    PhotoDerivative,
    PhotoFaceDetection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from search_feedback.models import SearchFeedback
from search_feedback.services import record_result_report
from selfie_search.images import prepare_selfie_image
from selfie_search.models import (
    SelfieSearch,
    SelfieSearchDirectEvidence,
    SelfieSearchFeedback,
    SelfieSearchResult,
)
from selfie_search.services.feedback import save_feedback_selfie


def selfie_upload() -> SimpleUploadedFile:
    content = BytesIO()
    Image.new("RGB", (8, 8), color="white").save(content, format="JPEG")
    return SimpleUploadedFile("selfie.jpg", content.getvalue(), content_type="image/jpeg")


@override_settings(
    SELFIE_FEEDBACK_ENABLED=True,
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class FeedbackSubmissionTests(TestCase):
    """The production breaks caught here are accepting feedback outside the final public result."""

    def setUp(self) -> None:
        self.owner = get_user_model().objects.create_user(username="feedback-owner")
        self.event = Event.objects.create(
            name="Feedback event",
            slug="feedback-event",
            start_date=date(2026, 8, 4),
            end_date=date(2026, 8, 4),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        self.client = Client(enforce_csrf_checks=True)

    def make_search(self, *, status=SelfieSearch.Status.FAILED) -> tuple[SelfieSearch, str]:
        token = f"feedback-token-{SelfieSearch.objects.count()}"
        return (
            SelfieSearch.objects.create(
                event=self.event,
                public_token_digest=hashlib.sha256(token.encode()).hexdigest(),
                status=status,
                temporary_object_key="",
                configuration={"contract": 1},
                matched_photo_count=1,
            ),
            token,
        )

    def make_result(
        self, *, search: SelfieSearch, photo_id: str = "feedback-photo", rank: int = 1
    ) -> SelfieSearchResult:
        photo = Photo.objects.create(
            id=photo_id,
            event=self.event,
            uploaded_by=self.owner,
            original_key=f"originals/{photo_id}.jpg",
            original_filename=f"{photo_id}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        configuration_hash = "a" * 64
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=1,
            processor_type="face",
            processor_version=1,
            configuration={},
            configuration_hash=configuration_hash,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type="face",
            processor_version=1,
            configuration={},
            configuration_hash=configuration_hash,
            input_fingerprint={},
        )
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=1,
            processor_type="face",
            processor_version=1,
            configuration={},
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        artifact = FaceProcessingAttemptArtifact.objects.create(attempt=attempt)
        detection = PhotoFaceDetection.objects.create(
            artifact=artifact,
            attempt=attempt,
            face_index=0,
            status=PhotoFaceDetection.Status.KEPT,
        )
        result = SelfieSearchResult.objects.create(
            search=search,
            photo=photo,
            rank=rank,
        )
        SelfieSearchDirectEvidence.objects.create(
            result=result, detection=detection, cosine_distance=0.1
        )
        return result

    def make_watermarked_result(self, *, search: SelfieSearch) -> SelfieSearchResult:
        result = self.make_result(search=search, photo_id="feedback-watermarked")
        photo = result.photo
        photo.processing_generation = Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1
        photo.gallery_media_policy = Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
        photo.save(update_fields=["processing_generation", "gallery_media_policy"])
        configuration = {"generate_watermarked_preview": {"variant": "preview-watermarked-v1"}}
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=2,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash="w" * 64,
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
        return result

    def feedback_url(self, *, token: str) -> str:
        return reverse(
            "selfie_search:feedback_selfie",
            kwargs={"event_slug": self.event.slug, "public_token": token},
        )

    def report_url(self) -> str:
        return reverse("search_feedback_submit", args=(self.event.slug,))

    @staticmethod
    def report_data(photo_id: str, **context: str) -> dict[str, str]:
        return {"photo_id": photo_id, "submission_id": str(uuid4()), **context}

    def csrf_post(self, url: str, data: dict) -> Any:
        self.client.get(reverse("event_detail", kwargs={"slug": self.event.slug}))
        return self.client.post(url, data, HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)

    def submission_data(self) -> dict:
        return {
            "selfie": selfie_upload(),
            "contact": "person@example.test",
            "personal_data_consent": "on",
        }

    def test_paid_result_reports_use_the_current_preview_gate(self) -> None:
        self.client.get(reverse("event_detail", args=(self.event.slug,)))
        self.event.access_type = Event.AccessType.PAID
        self.event.price_per_photo_kopecks = 30000
        self.event.save(update_fields=["access_type", "price_per_photo_kopecks"])
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        result = self.make_watermarked_result(search=search)
        states = {
            PAID_EVENTS: FEATURE_FLAG_ON,
            PAID_WATERMARKED_PREVIEWS: FEATURE_FLAG_OFF,
        }
        url = self.report_url()
        with override_feature_flags(states):
            self.assertEqual(
                self.csrf_post(
                    url, self.report_data(result.photo_id, public_token=token)
                ).status_code,
                404,
            )
            states[PAID_WATERMARKED_PREVIEWS] = FEATURE_FLAG_ON
            self.assertEqual(
                self.csrf_post(
                    url, self.report_data(result.photo_id, public_token=token)
                ).status_code,
                201,
            )

    def test_selfie_consent_and_upload_leave_previous_and_future_reports_independent(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        first = self.make_result(search=search)
        second = self.make_result(search=search, photo_id="feedback-second", rank=2)
        report_url = self.report_url()
        selfie_url = self.feedback_url(token=token)
        storage = Mock()
        storage.put.side_effect = StorageUnavailable()
        with patch("selfie_search.views.FeedbackSelfieStorage", return_value=storage):
            first_report = self.csrf_post(
                report_url, self.report_data(first.photo_id, public_token=token)
            )
            self.assertEqual(first_report.status_code, 201)
            self.assertEqual(first_report["Cache-Control"], "private, no-store")
            self.assertEqual(
                self.csrf_post(selfie_url, {"selfie": selfie_upload()}).status_code, 422
            )
            storage.put.assert_not_called()
            self.assertEqual(self.csrf_post(selfie_url, self.submission_data()).status_code, 503)
            self.assertFalse(SelfieSearchFeedback.objects.exists())
            self.assertEqual(SearchFeedback.objects.filter(photo_id=first.photo_id).count(), 1)
            storage.put.side_effect = None
            storage.put.return_value = Mock(
                key="0123456789abcdef0123456789abcdef", size=100, content_type="image/jpeg"
            )
            self.assertEqual(
                self.csrf_post(selfie_url, {**self.submission_data(), "contact": ""}).status_code,
                201,
            )

        feedback = SelfieSearchFeedback.objects.get(search=search)
        self.assertEqual(feedback.variant, SelfieSearchFeedback.Variant.SELFIE)
        self.assertEqual(feedback.source_visible_result_count, 2)
        self.assertTrue(feedback.personal_data_consent)
        self.assertEqual(feedback.consent_text_version, "2026-11-18")
        self.assertEqual(feedback.contact, "")
        self.assertEqual(
            self.csrf_post(
                report_url, self.report_data(second.photo_id, public_token=token)
            ).status_code,
            201,
        )
        self.assertFalse(SearchFeedback.objects.exclude(legacy_attachment_id=None).exists())

    def test_problem_selfie_is_idempotent_and_rejects_missing_consent_or_nonterminal_results(
        self,
    ) -> None:
        search, token = self.make_search()
        storage = Mock()
        storage.put.return_value = Mock(
            key="0123456789abcdef0123456789abcdef", size=100, content_type="image/jpeg"
        )
        with patch("selfie_search.views.FeedbackSelfieStorage", return_value=storage):
            created = self.csrf_post(self.feedback_url(token=token), self.submission_data())
            repeated = self.csrf_post(self.feedback_url(token=token), self.submission_data())
            invalid = self.csrf_post(
                self.feedback_url(token=token),
                {"selfie": selfie_upload(), "contact": ""},
            )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json(), {"status": "already_submitted"})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(SelfieSearchFeedback.objects.filter(search=search).count(), 1)
        _, pending_token = self.make_search(status=SelfieSearch.Status.PROCESSING)
        changed = self.csrf_post(self.feedback_url(token=pending_token), self.submission_data())
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.json(), {"status": "non_terminal"})

    def test_endpoint_rejects_missing_bearer_and_csrf_without_upload(self) -> None:
        _, token = self.make_search()
        storage = Mock()
        with patch("selfie_search.views.FeedbackSelfieStorage", return_value=storage):
            missing = self.csrf_post(self.feedback_url(token="wrong-token"), self.submission_data())
            csrf = self.client.post(self.feedback_url(token=token), self.submission_data())

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(csrf.status_code, 403)
        storage.put.assert_not_called()

    def test_draft_feedback_is_staff_only_and_unavailable_is_hidden_before_storage(self) -> None:
        search, token = self.make_search()
        feedback_url = self.feedback_url(token=token)
        self.client.get(reverse("event_detail", kwargs={"slug": self.event.slug}))
        csrf_token = self.client.cookies["csrftoken"].value
        self.event.publication_status = Event.PublicationStatus.DRAFT
        self.event.timezone_name = "Europe/Moscow"
        self.event.save(update_fields=["publication_status", "timezone_name"])
        ordinary_user = get_user_model().objects.create_user(username="feedback-ordinary")
        staff_user = get_user_model().objects.create_user(
            username="feedback-preview-staff", is_staff=True
        )
        storage = Mock()
        storage.put.return_value = Mock(
            key="0123456789abcdef0123456789abcdef", size=100, content_type="image/jpeg"
        )

        with patch("selfie_search.views.FeedbackSelfieStorage", return_value=storage) as factory:
            anonymous = self.client.post(
                feedback_url,
                self.submission_data(),
                HTTP_X_CSRFTOKEN=csrf_token,
            )
            self.client.force_login(ordinary_user)
            ordinary = self.client.post(
                feedback_url,
                self.submission_data(),
                HTTP_X_CSRFTOKEN=csrf_token,
            )
            factory.assert_not_called()
            self.client.force_login(staff_user)
            staff = self.client.post(
                feedback_url,
                self.submission_data(),
                HTTP_X_CSRFTOKEN=csrf_token,
            )
            self.event.publication_status = Event.PublicationStatus.UNAVAILABLE
            self.event.save(update_fields=["publication_status"])
            unavailable = self.client.post(
                feedback_url,
                self.submission_data(),
                HTTP_X_CSRFTOKEN=csrf_token,
            )

        self.assertEqual([anonymous.status_code, ordinary.status_code], [404, 404])
        self.assertEqual(staff.status_code, 201)
        self.assertEqual(unavailable.status_code, 404)
        self.assertEqual(SelfieSearchFeedback.objects.filter(search=search).count(), 1)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(storage.put.call_count, 1)

    def test_database_failure_after_upload_deletes_the_exact_uploaded_object(self) -> None:
        search, _ = self.make_search()
        storage = Mock()
        storage.put.return_value = Mock(
            key="0123456789abcdef0123456789abcdef", size=100, content_type="image/jpeg"
        )

        with patch.object(SelfieSearchFeedback, "save", side_effect=IntegrityError):
            with self.assertRaises(IntegrityError):
                save_feedback_selfie(
                    search_id=search.id,
                    upload=prepare_selfie_image(selfie_upload()),
                    contact="person@example.test",
                    paid_watermarked_previews_enabled=False,
                    storage=storage,
                )

        storage.delete.assert_called_once_with(key="0123456789abcdef0123456789abcdef")
        self.assertFalse(SelfieSearchFeedback.objects.filter(search=search).exists())

    @override_settings(SELFIE_FEEDBACK_ENABLED=False)
    def test_ready_selfie_result_saves_multiple_reports_without_an_attachment(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        first = self.make_result(search=search)
        second = self.make_result(search=search, photo_id="second-result", rank=2)
        result_url = reverse("selfie_search:result", args=(self.event.slug, token))
        first_photo = self.report_data(first.photo_id, public_token=token)
        url = self.report_url()
        with patch("selfie_search.services.results.SELFIE_SEARCH_RESULT_PAGE_SIZE", 1):
            first_page = self.client.get(result_url)
            self.assertContains(first_page, "data-result-feedback", count=1)
            self.assertEqual(self.client.post(url, first_photo).status_code, 403)
            self.assertEqual(self.csrf_post(url, first_photo).status_code, 201)
            self.assertEqual(self.csrf_post(url, first_photo).status_code, 201)
            second_page = self.client.get(result_url, {"page": 2})
            self.assertContains(second_page, f'value="{second.photo_id}"')
            self.assertEqual(
                self.csrf_post(
                    url, self.report_data(second.photo_id, public_token=token)
                ).status_code,
                201,
            )
            for page in (1, 2):
                self.assertContains(
                    self.client.get(result_url, {"page": page}),
                    'data-feedback-saved role="status">',
                    count=1,
                )
        self.assertEqual(
            SearchFeedback.objects.filter(search_context__search_id=str(search.pk)).count(), 3
        )
        self.assertFalse(SelfieSearchFeedback.objects.exists())

    @override_settings(SELFIE_FEEDBACK_ENABLED=False)
    def test_gallery_face_result_accepts_reports_only_for_visible_members(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        source = {"kind": "gallery_photo", "photo_id": "source-photo", "detection_id": str(uuid4())}
        search.configuration = {"processor": "gallery_photo_query", "query_source": source}
        search.save(update_fields=["configuration"])
        own = self.make_result(search=search)
        other, _ = self.make_search(status=SelfieSearch.Status.READY)
        foreign = self.make_result(search=other, photo_id="foreign-result")
        url = self.report_url()
        self.assertEqual(
            self.csrf_post(url, self.report_data(foreign.photo_id, public_token=token)).status_code,
            404,
        )
        self.assertEqual(
            self.csrf_post(url, self.report_data(own.photo_id, public_token=token)).status_code, 201
        )
        feedback = SearchFeedback.objects.get()
        self.assertEqual(feedback.search_type, SearchFeedback.SearchType.GALLERY)
        self.assertEqual(
            feedback.search_context,
            {
                "search_id": str(search.pk),
                "result_id": str(own.pk),
                "primary_source": own.primary_source,
                "source_photo_id": source["photo_id"],
                "source_detection_id": source["detection_id"],
            },
        )
        Photo.objects.filter(pk=own.photo_id).update(is_hidden=True)
        self.assertEqual(
            self.csrf_post(url, self.report_data(own.photo_id, public_token=token)).status_code, 404
        )
        Photo.objects.filter(pk=own.photo_id).update(is_hidden=False)
        self.event.publication_status = Event.PublicationStatus.UNAVAILABLE
        self.event.save(update_fields=["publication_status"])
        self.assertEqual(
            self.client.post(
                url,
                self.report_data(own.photo_id, public_token=token),
                HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value,
            ).status_code,
            404,
        )

    @override_settings(SELFIE_FEEDBACK_ENABLED=False)
    def test_bib_search_marks_only_numbered_photos_in_current_event(self) -> None:
        self.event.bib_search_enabled = True
        self.event.save(update_fields=["bib_search_enabled"])
        search, _ = self.make_search(status=SelfieSearch.Status.READY)
        numbered = self.make_result(search=search)
        unnumbered = self.make_result(search=search, photo_id="unnumbered", rank=2)
        folder = EventFolder.objects.create(event=self.event, name="Start")
        Photo.objects.filter(pk=numbered.photo_id).update(folder=folder)
        BibReading.objects.create(
            photo=numbered.photo,
            source_attempt=numbered.direct_evidence.detection.attempt,
            number="007",
        )
        gallery_url = reverse("event_detail", args=(self.event.slug,))
        self.assertNotContains(self.client.get(gallery_url), "Это не я")
        self.assertNotContains(self.client.get(gallery_url, {"folder": folder.pk}), "Это не я")
        self.assertContains(
            self.client.get(gallery_url, {"bib": "007", "folder": folder.pk}),
            "Это не я",
            count=1,
        )
        url = self.report_url()
        self.assertEqual(
            self.csrf_post(url, self.report_data(unnumbered.photo_id, bib="007")).status_code, 404
        )
        self.assertEqual(
            self.csrf_post(url, self.report_data(numbered.photo_id, bib="7")).status_code, 404
        )
        self.assertEqual(
            self.csrf_post(url, self.report_data(numbered.photo_id, bib="007")).status_code, 201
        )
        self.assertEqual(
            self.csrf_post(url, self.report_data(numbered.photo_id, bib="007")).status_code, 201
        )
        self.assertEqual(SelfieSearch.objects.count(), 1)
        self.assertEqual(
            list(
                SearchFeedback.objects.values_list(
                    "event_id", "search_type", "photo_id", "search_context"
                )
            ),
            [(self.event.pk, "bib", numbered.photo_id, {"bib_number": "007"})] * 2,
        )
        self.assertContains(
            self.client.get(gallery_url, {"bib": "007", "folder": folder.pk}),
            'data-feedback-saved role="status">',
        )
        Photo.objects.filter(pk=numbered.photo_id).update(is_hidden=True)
        self.assertEqual(
            self.csrf_post(url, self.report_data(numbered.photo_id, bib="007")).status_code, 404
        )

    @override_settings(SELFIE_FEEDBACK_ENABLED=False)
    def test_common_feedback_endpoint_accepts_a_ready_selfie_match(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        result = self.make_result(search=search)
        url = f"/events/{self.event.slug}/search-feedback/"
        data = {
            "photo_id": result.photo_id,
            "public_token": token,
            "submission_id": "3e72a744-2ebc-4dd9-a40f-a83b319f1eb2",
        }
        self.assertEqual(self.csrf_post(url, data).status_code, 201)
        self.assertEqual(self.csrf_post(url, data).status_code, 201)

        expected_context = {
            "search_id": str(search.pk),
            "result_id": str(result.pk),
            "primary_source": result.primary_source,
        }
        self.assertEqual(
            list(
                SearchFeedback.objects.values_list(
                    "event_id", "search_type", "photo_id", "value", "search_context"
                )
            ),
            [(self.event.pk, "selfie", result.photo_id, "absent", expected_context)] * 2,
        )
        self.assertEqual(
            set(SearchFeedback.objects.values_list("submission_id", flat=True)),
            {UUID(data["submission_id"])},
        )
        for invalid in (
            {**data, "submission_id": ""},
            {**data, "submission_id": "invalid"},
            {**data, "bib": "007"},
            {**data, "public_token": ""},
        ):
            self.assertEqual(self.csrf_post(url, invalid).status_code, 422)
        self.assertEqual(SearchFeedback.objects.count(), 2)

    def test_journal_write_executes_one_insert(self) -> None:
        with self.assertNumQueries(1):
            record_result_report(
                submission_id=uuid4(),
                event_id=self.event.pk,
                photo_id="known-photo",
                search_type=SearchFeedback.SearchType("bib"),
                search_context={"bib_number": "007"},
            )
        self.assertEqual(SearchFeedback.objects.get().photo_id, "known-photo")
