import hashlib
from datetime import date
from io import BytesIO
from typing import Any
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from ingestion.storage import StorageUnavailable
from picflow.models import Event, Photo
from PIL import Image
from processing.models import (
    EventProcessingRun,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    ProcessingAttempt,
    ProcessingJob,
)
from selfie_search.models import (
    SelfieSearch,
    SelfieSearchFeedback,
    SelfieSearchResult,
)
from selfie_search.services.feedback import feedback_presentation, submit_search_feedback


def selfie_upload() -> SimpleUploadedFile:
    content = BytesIO()
    Image.new("RGB", (8, 8), color="white").save(content, format="JPEG")
    return SimpleUploadedFile("selfie.jpg", content.getvalue(), content_type="image/jpeg")


@override_settings(
    SELFIE_SEARCH_ENABLED=True,
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
        self, *, search: SelfieSearch, photo_id: str = "feedback-photo"
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
        return SelfieSearchResult.objects.create(
            search=search,
            photo=photo,
            detection=detection,
            rank=1,
            cosine_distance=0.1,
        )

    def feedback_url(self, *, token: str) -> str:
        return reverse(
            "selfie_search:feedback",
            kwargs={"event_slug": self.event.slug, "public_token": token},
        )

    def csrf_post(self, url: str, data: dict) -> Any:
        self.client.get(reverse("event_detail", kwargs={"slug": self.event.slug}))
        return self.client.post(url, data, HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)

    def submission_data(self, *, labels: str = "{}") -> dict:
        return {
            "selfie": selfie_upload(),
            "contact": "person@example.test",
            "personal_data_consent": "on",
            "labels": labels,
        }

    def test_presentation_selects_problem_or_labels_from_current_visible_results(self) -> None:
        failed, _ = self.make_search()
        empty, _ = self.make_search(status=SelfieSearch.Status.READY)
        ready, _ = self.make_search(status=SelfieSearch.Status.READY)
        result = self.make_result(search=ready)

        self.assertEqual(feedback_presentation(failed).variant, "problem")
        self.assertEqual(feedback_presentation(empty).variant, "problem")
        presentation = feedback_presentation(ready)
        self.assertEqual(presentation.variant, "result_labels")
        self.assertEqual(presentation.visible_result_ids, frozenset({result.id}))

    def test_post_creates_feedback_and_only_explicit_labels(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        result = self.make_result(search=search)
        storage = Mock()
        storage.put.return_value = Mock(
            key="0123456789abcdef0123456789abcdef", size=100, content_type="image/jpeg"
        )

        with patch("selfie_search.views.FeedbackSelfieStorage", return_value=storage):
            response = self.csrf_post(
                self.feedback_url(token=token),
                self.submission_data(labels=f'{{"{result.id}":"present"}}'),
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {"status": "submitted"})
        feedback = SelfieSearchFeedback.objects.get(search=search)
        self.assertEqual(feedback.variant, SelfieSearchFeedback.Variant.RESULT_LABELS)
        self.assertTrue(feedback.personal_data_consent)
        self.assertEqual(feedback.consent_text_version, "2026-08-04")
        self.assertEqual(
            list(feedback.labels.values_list("result_id", "value")), [(result.id, "present")]
        )

    def test_post_is_idempotent_and_rejects_invalid_changed_or_nonterminal_results(self) -> None:
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
                {"selfie": selfie_upload(), "contact": "", "labels": "{}"},
            )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json(), {"status": "already_submitted"})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(SelfieSearchFeedback.objects.filter(search=search).count(), 1)
        pending, pending_token = self.make_search(status=SelfieSearch.Status.PROCESSING)
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

    def test_changed_visible_result_rejects_the_whole_label_set(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        result = self.make_result(search=search)
        Photo.objects.filter(pk=result.photo_id).update(original_key="")
        storage = Mock()

        with patch("selfie_search.views.FeedbackSelfieStorage", return_value=storage):
            response = self.csrf_post(
                self.feedback_url(token=token),
                self.submission_data(labels=f'{{"{result.id}":"absent"}}'),
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"status": "result_changed"})
        self.assertFalse(SelfieSearchFeedback.objects.filter(search=search).exists())
        storage.put.assert_not_called()

    def test_cross_search_label_and_storage_failure_do_not_create_feedback(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        self.make_result(search=search)
        other_search, _ = self.make_search(status=SelfieSearch.Status.READY)
        foreign_result = self.make_result(search=other_search, photo_id="foreign-result")
        unavailable = Mock()
        unavailable.put.side_effect = StorageUnavailable()

        with patch("selfie_search.views.FeedbackSelfieStorage", return_value=unavailable):
            cross_search = self.csrf_post(
                self.feedback_url(token=token),
                self.submission_data(labels=f'{{"{foreign_result.id}":"present"}}'),
            )
            storage_failure = self.csrf_post(self.feedback_url(token=token), self.submission_data())

        self.assertEqual(cross_search.status_code, 422)
        self.assertEqual(storage_failure.status_code, 503)
        self.assertFalse(SelfieSearchFeedback.objects.filter(search=search).exists())

    def test_nested_json_label_values_return_invalid_without_creating_feedback(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        result = self.make_result(search=search)
        storage = Mock()

        for malformed_value in ("[]", "{}"):
            with self.subTest(malformed_value=malformed_value):
                with patch("selfie_search.views.FeedbackSelfieStorage", return_value=storage):
                    response = self.csrf_post(
                        self.feedback_url(token=token),
                        self.submission_data(labels=f'{{"{result.id}":{malformed_value}}}'),
                    )

                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json(), {"status": "invalid"})
                self.assertFalse(SelfieSearchFeedback.objects.filter(search=search).exists())
                storage.put.assert_not_called()

    def test_database_failure_after_upload_deletes_the_exact_uploaded_object(self) -> None:
        search, _ = self.make_search()
        storage = Mock()
        storage.put.return_value = Mock(
            key="0123456789abcdef0123456789abcdef", size=100, content_type="image/jpeg"
        )

        with patch.object(SelfieSearchFeedback, "save", side_effect=IntegrityError):
            with self.assertRaises(IntegrityError):
                submit_search_feedback(
                    search_id=search.id,
                    upload=selfie_upload(),
                    contact="person@example.test",
                    labels={},
                    storage=storage,
                )

        storage.delete.assert_called_once_with(key="0123456789abcdef0123456789abcdef")
        self.assertFalse(SelfieSearchFeedback.objects.filter(search=search).exists())
