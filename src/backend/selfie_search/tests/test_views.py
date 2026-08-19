import hashlib
import json
import logging
from dataclasses import replace
from datetime import date
from io import BytesIO
from pathlib import Path
from struct import pack
from types import SimpleNamespace
from typing import cast
from unittest.mock import ANY, Mock, call, patch
from urllib.parse import quote
from uuid import uuid4
from zlib import crc32

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.http import HttpResponse
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from ingestion.storage import ObjectMissing, StorageError, StorageUnavailable
from picflow.gallery import GalleryPhotoFactory
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
    SelfieSearchDirectEvidence,
    SelfieSearchFeedback,
    SelfieSearchJob,
    SelfieSearchResult,
)
from selfie_search.observability import OBSERVABILITY_FAILURE_MARKER
from selfie_search.services.submission import GallerySearchFailed, GallerySearchUnavailable

type ChoiceValue = str | tuple[str, str]


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class PublicSelfieSearchMarkupTests(TestCase):
    """The production break caught here is hiding or weakening the public search entry path."""

    def setUp(self) -> None:
        self.free_event = self.make_event(slug="free-run", access_type=Event.AccessType.FREE)
        self.paid_event = self.make_event(slug="paid-run", access_type=Event.AccessType.PAID)
        self.draft_event = self.make_event(
            slug="draft-run",
            access_type=Event.AccessType.FREE,
            publication_status=Event.PublicationStatus.DRAFT,
        )

    def make_event(
        self,
        *,
        slug: str,
        access_type: ChoiceValue,
        publication_status: ChoiceValue = "published",
    ) -> Event:
        return Event.objects.create(
            name=slug.replace("-", " ").title(),
            slug=slug,
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 30),
            city="Moscow",
            access_type=access_type,
            publication_status=publication_status,
        )

    def test_published_free_event_offers_the_safe_selfie_form(self) -> None:
        event = self.free_event
        response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        self.assertContains(response, 'id="selfie-search"')
        self.assertContains(
            response, reverse("selfie_search:submit", kwargs={"event_slug": event.slug})
        )
        self.assertContains(response, 'method="post"')
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'name="selfie"')
        self.assertContains(
            response, 'accept="image/jpeg,image/png,image/heic,image/heif,.heic,.heif"'
        )
        self.assertContains(
            response, "Мы ищем вероятные совпадения только среди фотографий этого события."
        )
        self.assertContains(
            response,
            (
                "Загрузите чёткую фотографию, где лицо хорошо видно. Лучше использовать фото с дня "
                "мероприятия, особенно если на мероприятии вы были в очках или головном уборе."
            ),
        )
        self.assertContains(response, "Селфи удаляется после подготовки поиска.")
        self.assertContains(response, "Любой, у кого есть ссылка на результат, сможет его открыть.")

    def test_published_events_expose_only_empty_nonsecret_saved_history_markup(self) -> None:
        token = "saved-history-bearer-token"
        result_path = f"/events/{self.free_event.slug}/selfie-search/{token}/"
        for event in (self.free_event,):
            with self.subTest(event=event.slug):
                response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

                self.assertContains(response, "data-selfie-search-history")
                self.assertContains(response, "data-selfie-search-history-list")
                self.assertContains(response, f'data-event-slug="{event.slug}"')
                self.assertContains(response, 'src="/static/ui/selfie-search-history.js"')
                self.assertContains(response, "hidden")
                self.assertNotContains(response, token)
                self.assertNotContains(response, result_path)
                self.assertNotContains(response, "Открыть результат")
                self.assertNotContains(response, "Удалить с устройства")

    def test_every_public_result_state_exposes_only_event_slug_for_saved_history(self) -> None:
        for status in SelfieSearch.Status.values:
            with self.subTest(status=status):
                token = f"history-{status}-token"
                SelfieSearch.objects.create(
                    event=self.free_event,
                    public_token_digest=hashlib.sha256(token.encode("ascii")).hexdigest(),
                    status=status,
                    temporary_object_key="",
                    configuration={"public-contract": 1},
                )
                response = self.client.get(
                    reverse(
                        "selfie_search:result",
                        kwargs={"event_slug": self.free_event.slug, "public_token": token},
                    )
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "data-selfie-search-result")
                self.assertContains(response, f'data-event-slug="{self.free_event.slug}"')
                self.assertContains(response, 'src="/static/ui/selfie-search-history.js"')
                self.assertNotContains(
                    response, f'data-result-path="/events/{self.free_event.slug}'
                )
                self.assertNotContains(response, f'data-public-token="{token}"')
                self.assertNotContains(response, "data-selfie-search-history-list")

    def test_draft_event_has_no_public_selfie_form(self) -> None:
        response = self.client.get(reverse("event_detail", kwargs={"slug": self.draft_event.slug}))

        self.assertEqual(response.status_code, 404)

    @override_settings(SELFIE_FEEDBACK_ENABLED=False)
    def test_disabled_feedback_entry_exposes_no_preservation_marker_or_correlation(
        self,
    ) -> None:
        response = self.client.get(reverse("event_detail", kwargs={"slug": self.free_event.slug}))

        self.assertContains(response, 'data-selfie-feedback-enabled="false"')
        self.assertNotContains(response, 'name="feedback_correlation"')

    @override_settings(SELFIE_FEEDBACK_ENABLED=True)
    def test_enabled_feedback_search_entry_accepts_a_browser_correlation(self) -> None:
        response = self.client.get(reverse("event_detail", kwargs={"slug": self.free_event.slug}))

        self.assertContains(response, 'data-selfie-feedback-enabled="true"')
        self.assertContains(
            response,
            '<input type="hidden" name="feedback_correlation" value="">',
            html=True,
        )

    def test_unicode_published_event_has_reversible_selfie_urls(self) -> None:
        event = self.make_event(slug="cyclingrace-олимпия", access_type=Event.AccessType.FREE)
        token = "unicode-search-token"
        search = SelfieSearch.objects.create(
            event=event,
            public_token_digest=hashlib.sha256(token.encode("ascii")).hexdigest(),
            status=SelfieSearch.Status.NO_FACE,
            temporary_object_key="",
            configuration={"public-contract": 1},
        )

        submit_url = reverse("selfie_search:submit", kwargs={"event_slug": event.slug})
        result_url = reverse(
            "selfie_search:result",
            kwargs={"event_slug": event.slug, "public_token": token},
        )
        status_url = reverse(
            "selfie_search:status",
            kwargs={"event_slug": event.slug, "public_token": token},
        )
        media_url = reverse(
            "selfie_search:result_media",
            kwargs={
                "event_slug": event.slug,
                "public_token": token,
                "photo_id": "photo-42",
                "variant": "preview-small",
            },
        )

        encoded_slug = quote(event.slug)
        self.assertIn(encoded_slug, submit_url)
        self.assertIn(encoded_slug, result_url)
        self.assertIn(encoded_slug, status_url)
        self.assertIn(encoded_slug, media_url)
        event_response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))
        self.assertEqual(event_response.status_code, 200)
        self.assertContains(event_response, submit_url)
        result_response = self.client.get(result_url)
        self.assertEqual(result_response.status_code, 200)
        self.assertEqual(result_response.context["search"].pk, search.pk)

    def test_terminal_result_offers_a_new_search_action(self) -> None:
        event = self.free_event
        token = "terminal-search-token"
        search = SelfieSearch.objects.create(
            event=event,
            public_token_digest=hashlib.sha256(token.encode("ascii")).hexdigest(),
            status=SelfieSearch.Status.NO_FACE,
            temporary_object_key="",
            configuration={"public-contract": 1},
        )

        response = self.client.get(
            reverse(
                "selfie_search:result",
                kwargs={"event_slug": event.slug, "public_token": token},
            )
        )

        self.assertEqual(response.context["search"].pk, search.pk)
        self.assertContains(response, "Искать по другому селфи")
        self.assertContains(response, reverse("event_detail", kwargs={"slug": event.slug}))

    @override_settings(SELFIE_FEEDBACK_ENABLED=True)
    def test_result_exposes_a_valid_redirect_correlation_without_session_binding(self) -> None:
        token = "correlated-search-token"
        SelfieSearch.objects.create(
            event=self.free_event,
            public_token_digest=hashlib.sha256(token.encode("ascii")).hexdigest(),
            status=SelfieSearch.Status.NO_FACE,
            temporary_object_key="",
            configuration={"public-contract": 1},
        )
        correlation = "a" * 32

        response = self.client.get(
            reverse(
                "selfie_search:result",
                kwargs={"event_slug": self.free_event.slug, "public_token": token},
            ),
            {"feedback_correlation": correlation},
        )

        self.assertContains(response, f'data-feedback-correlation="{correlation}"')
        invalid = self.client.get(
            reverse(
                "selfie_search:result",
                kwargs={"event_slug": self.free_event.slug, "public_token": token},
            ),
            {"feedback_correlation": "not valid"},
        )
        self.assertNotContains(invalid, 'data-feedback-correlation="not valid"')


def _view_jpeg_upload() -> SimpleUploadedFile:
    content = BytesIO()
    Image.new("RGB", (8, 8), color="white").save(content, format="JPEG")
    return SimpleUploadedFile("selfie.jpg", content.getvalue(), content_type="image/jpeg")


def _view_pixel_limit_upload() -> SimpleUploadedFile:
    ihdr = pack(">IIBBBBB", 5_001, 5_000, 8, 2, 0, 0, 0)
    content = b"\x89PNG\r\n\x1a\n" + pack(">I", len(ihdr)) + b"IHDR" + ihdr
    content += pack(">I", crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
    content += pack(">I", 0) + b"IEND" + pack(">I", crc32(b"IEND") & 0xFFFFFFFF)
    return SimpleUploadedFile("selfie.png", content, content_type="image/png")


class FailingSubmissionStorage:
    def put(self, *, key: str, content: bytes, content_type: str):  # noqa: ARG002
        raise StorageUnavailable()


class _CaptureLogger(logging.Logger):
    def __init__(self) -> None:
        super().__init__("selfie-submission-capture", logging.DEBUG)
        self.calls: list[tuple[int, str]] = []

    def log(self, level: int, message: str) -> None:  # type: ignore[override]
        self.calls.append((level, message))


class _FailingLogger(logging.Logger):
    def __init__(self) -> None:
        super().__init__("selfie-submission-failing", logging.DEBUG)

    def log(self, _level: int, _message: str) -> None:  # type: ignore[override]
        raise RuntimeError("logger unavailable")


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class GalleryPhotoSearchViewTests(TestCase):
    """The production break caught here is turning a forged gallery request into a search."""

    def setUp(self) -> None:
        self.owner = get_user_model().objects.create_user(username="gallery-search-owner")
        self.event = self.make_event(slug="gallery-search")
        self.photo = self.make_gallery_photo(event=self.event, photo_id="gallery-source")
        self.detection_id = uuid4()

    def make_event(self, *, slug: str, published: bool = True) -> Event:
        return Event.objects.create(
            name=slug.replace("-", " ").title(),
            slug=slug,
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 30),
            city="Moscow",
            access_type=Event.AccessType.FREE,
            publication_status=(
                Event.PublicationStatus.PUBLISHED if published else Event.PublicationStatus.DRAFT
            ),
        )

    def make_gallery_photo(self, *, event: Event, photo_id: str) -> Photo:
        return Photo.objects.create(
            id=photo_id,
            event=event,
            uploaded_by=self.owner,
            original_key=f"originals/{photo_id}",
            original_filename=f"{photo_id}.jpg",
            original_size=1,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def url(
        self,
        *,
        event: Event | None = None,
        photo_id: str | None = None,
        detection_id=None,
    ) -> str:
        event = event or self.event
        return reverse(
            "selfie_search:submit_gallery_face",
            kwargs={
                "event_slug": event.slug,
                "photo_id": photo_id or self.photo.pk,
                "detection_id": detection_id or self.detection_id,
            },
        )

    def process_url(self, *, token: str) -> str:
        return reverse(
            "selfie_search:process_gallery_search",
            kwargs={"event_slug": self.event.slug, "public_token": token},
        )

    def make_queued_gallery_search(
        self, *, token: str, configuration: dict[str, object]
    ) -> SelfieSearch:
        return SelfieSearch.objects.create(
            event=self.event,
            public_token_digest=hashlib.sha256(token.encode()).hexdigest(),
            temporary_object_key="",
            configuration=configuration,
        )

    @patch("selfie_search.views.submit_gallery_photo_search")
    def test_post_only_and_published_gallery_face_search_are_available(
        self, submit_gallery_photo_search
    ) -> None:
        get_response = self.client.get(self.url())
        csrf_response = Client(enforce_csrf_checks=True).post(self.url())
        submit_gallery_photo_search.return_value = SimpleNamespace(public_token="available-token")
        submitted_response = self.client.post(self.url())

        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(csrf_response.status_code, 403)
        self.assertRedirects(
            submitted_response,
            reverse(
                "selfie_search:result",
                kwargs={"event_slug": self.event.slug, "public_token": "available-token"},
            ),
            fetch_redirect_response=False,
        )
        submit_gallery_photo_search.assert_called_once_with(
            event=self.event,
            photo=self.photo,
            detection_id=self.detection_id,
            user=ANY,
        )

    @patch("selfie_search.views.submit_gallery_photo_search")
    def test_active_staff_can_submit_a_draft_gallery_face(
        self, submit_gallery_photo_search
    ) -> None:
        draft = self.make_event(slug="draft-gallery-preview", published=False)
        draft_photo = self.make_gallery_photo(event=draft, photo_id="draft-gallery-source")
        staff_user = get_user_model().objects.create_user(
            username="gallery-preview-staff", is_staff=True
        )
        submit_gallery_photo_search.return_value = SimpleNamespace(public_token="draft-token")
        self.client.force_login(staff_user)

        response = self.client.post(self.url(event=draft, photo_id=draft_photo.pk))

        self.assertEqual(response.status_code, 302)
        submit_gallery_photo_search.assert_called_once_with(
            event=draft,
            photo=draft_photo,
            detection_id=self.detection_id,
            user=staff_user,
        )

    @patch("selfie_search.views.submit_gallery_photo_search")
    def test_unpublished_cross_event_and_non_gallery_sources_are_not_submitted(
        self, submit_gallery_photo_search
    ) -> None:
        draft = self.make_event(slug="draft-gallery-search", published=False)
        other = self.make_event(slug="other-gallery-search")
        foreign_photo = self.make_gallery_photo(event=other, photo_id="foreign-source")
        non_gallery_photo = Photo.objects.create(
            id="not-gallery-source", event=self.event, src="legacy/photo.jpg"
        )

        responses = (
            self.client.post(self.url(event=draft, photo_id="anything")),
            self.client.post(self.url(photo_id=foreign_photo.pk)),
            self.client.post(self.url(photo_id=non_gallery_photo.pk)),
        )

        self.assertEqual([response.status_code for response in responses], [404, 404, 404])
        submit_gallery_photo_search.assert_not_called()

    @patch("selfie_search.views.submit_gallery_photo_search")
    def test_forged_or_stale_selected_detection_returns_not_found(
        self, submit_gallery_photo_search
    ) -> None:
        submit_gallery_photo_search.side_effect = GallerySearchUnavailable()
        forged_detection_id = uuid4()

        response = self.client.post(self.url(detection_id=forged_detection_id))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b"")
        submit_gallery_photo_search.assert_called_once_with(
            event=self.event,
            photo=self.photo,
            detection_id=forged_detection_id,
            user=ANY,
        )

    @patch("selfie_search.views.submit_gallery_photo_search")
    def test_success_redirects_to_the_existing_bearer_result(
        self, submit_gallery_photo_search
    ) -> None:
        submit_gallery_photo_search.return_value = SimpleNamespace(
            public_token="gallery-bearer-token"
        )

        response = self.client.post(self.url())

        self.assertRedirects(
            response,
            reverse(
                "selfie_search:result",
                kwargs={"event_slug": self.event.slug, "public_token": "gallery-bearer-token"},
            ),
            fetch_redirect_response=False,
        )
        submit_gallery_photo_search.assert_called_once_with(
            event=self.event,
            photo=self.photo,
            detection_id=self.detection_id,
            user=ANY,
        )

    @patch("selfie_search.views.submit_gallery_photo_search")
    def test_service_failure_is_a_sanitized_body_free_503(
        self, submit_gallery_photo_search
    ) -> None:
        submit_gallery_photo_search.side_effect = GallerySearchFailed()

        response = self.client.post(self.url())

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"")

    @patch("selfie_search.views.process_gallery_photo_search")
    def test_queued_gallery_bearer_processes_only_via_csrf_post(
        self, process_gallery_photo_search
    ) -> None:
        token = "queued-gallery-token"
        search = self.make_queued_gallery_search(
            token=token,
            configuration={
                "processor": "gallery_photo_query",
                "query_source": {
                    "kind": "gallery_photo",
                    "photo_id": self.photo.pk,
                    "detection_id": str(self.detection_id),
                },
            },
        )
        process_gallery_photo_search.return_value = search

        get_response = self.client.get(self.process_url(token=token))
        csrf_response = Client(enforce_csrf_checks=True).post(self.process_url(token=token))
        response = self.client.post(self.process_url(token=token))

        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(csrf_response.status_code, 403)
        self.assertRedirects(
            response,
            reverse(
                "selfie_search:result",
                kwargs={"event_slug": self.event.slug, "public_token": token},
            ),
            fetch_redirect_response=False,
        )
        process_gallery_photo_search.assert_called_once_with(search=search)

    @patch("selfie_search.views.process_gallery_photo_search")
    def test_non_gallery_or_nonqueued_bearer_cannot_invoke_processing(
        self, process_gallery_photo_search
    ) -> None:
        selfie_token = "queued-selfie-token"
        ready_token = "ready-gallery-token"
        self.make_queued_gallery_search(
            token=selfie_token, configuration={"processor": "selfie_query"}
        )
        ready = self.make_queued_gallery_search(
            token=ready_token,
            configuration={"processor": "gallery_photo_query"},
        )
        ready.status = SelfieSearch.Status.READY
        ready.save(update_fields=["status"])

        responses = (
            self.client.post(self.process_url(token=selfie_token)),
            self.client.post(self.process_url(token=ready_token)),
        )

        self.assertEqual([response.status_code for response in responses], [404, 404])
        process_gallery_photo_search.assert_not_called()

    def test_only_queued_gallery_result_renders_the_process_form(self) -> None:
        token = "queued-gallery-markup"
        selfie_token = "queued-selfie-markup"
        self.make_queued_gallery_search(
            token=token,
            configuration={
                "processor": "gallery_photo_query",
                "query_source": {"kind": "gallery_photo"},
            },
        )
        self.make_queued_gallery_search(
            token=selfie_token, configuration={"processor": "selfie_query"}
        )

        gallery = self.client.get(
            reverse(
                "selfie_search:result",
                kwargs={"event_slug": self.event.slug, "public_token": token},
            )
        )
        selfie = self.client.get(
            reverse(
                "selfie_search:result",
                kwargs={"event_slug": self.event.slug, "public_token": selfie_token},
            )
        )

        self.assertContains(gallery, "data-gallery-search-process")
        self.assertContains(gallery, 'name="csrfmiddlewaretoken"')
        self.assertContains(gallery, self.process_url(token=token))
        self.assertContains(gallery, "Начать поиск похожих фотографий")
        self.assertNotContains(selfie, "data-gallery-search-process")

    @override_settings(SELFIE_FEEDBACK_ENABLED=True)
    def test_ready_gallery_result_has_no_selfie_copy_feedback_or_feedback_post(self) -> None:
        token = "ready-gallery-feedback"
        search = self.make_queued_gallery_search(
            token=token,
            configuration={
                "processor": "gallery_photo_query",
                "query_source": {"kind": "gallery_photo"},
            },
        )
        search.status = SelfieSearch.Status.READY
        search.terminal_at = timezone.now()
        search.cleanup_confirmed_at = timezone.now()
        search.save(update_fields=["status", "terminal_at", "cleanup_confirmed_at"])
        result_url = reverse(
            "selfie_search:result",
            kwargs={"event_slug": self.event.slug, "public_token": token},
        )
        feedback_url = reverse(
            "selfie_search:feedback",
            kwargs={"event_slug": self.event.slug, "public_token": token},
        )

        response = self.client.get(result_url)
        feedback = self.client.post(feedback_url)

        self.assertContains(
            response, "Поиск показывает вероятные совпадения среди фотографий этого события."
        )
        self.assertContains(response, 'data-gallery-origin="true"')
        self.assertNotContains(response, "Селфи удаляется после подготовки поиска")
        self.assertNotContains(response, "data-selfie-feedback ")
        self.assertNotContains(response, "Я согласен на обработку моего селфи")
        self.assertEqual(feedback.status_code, 404)

    def test_failed_gallery_result_uses_gallery_specific_error_copy(self) -> None:
        token = "failed-gallery-copy"
        search = self.make_queued_gallery_search(
            token=token,
            configuration={
                "processor": "gallery_photo_query",
                "query_source": {"kind": "gallery_photo"},
            },
        )
        search.status = SelfieSearch.Status.FAILED
        search.terminal_at = timezone.now()
        search.cleanup_confirmed_at = timezone.now()
        search.save(update_fields=["status", "terminal_at", "cleanup_confirmed_at"])

        response = self.client.get(
            reverse(
                "selfie_search:result",
                kwargs={"event_slug": self.event.slug, "public_token": token},
            )
        )

        self.assertContains(
            response,
            "Поиск похожих фотографий сейчас недоступен. Попробуйте выбрать другую фотографию.",
        )
        self.assertNotContains(response, "Попробуйте другое селфи")


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class SelfieSubmissionFeedbackTests(TestCase):
    def setUp(self) -> None:
        self.event = Event.objects.create(
            name="Feedback event",
            slug="feedback-event",
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 30),
            city="Moscow",
            access_type=Event.AccessType.FREE,
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        self.url = reverse("selfie_search:submit", kwargs={"event_slug": self.event.slug})

    def post(self, upload: SimpleUploadedFile | None) -> HttpResponse:
        return self.client.post(self.url, {} if upload is None else {"selfie": upload})

    def assert_customer_rejection(
        self,
        *,
        upload: SimpleUploadedFile | None,
        reason: str,
        message: str,
        actual_format: str | None,
        declared_type: str,
    ) -> None:
        before_searches = SelfieSearch.objects.count()
        before_jobs = SelfieSearchJob.objects.count()
        with patch("selfie_search.views.TemporarySelfieStorage") as storage_factory:
            with self.assertLogs("selfie_search.views", level="INFO") as logs:
                response = self.post(upload)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(SelfieSearch.objects.count(), before_searches)
        self.assertEqual(SelfieSearchJob.objects.count(), before_jobs)
        storage_factory.assert_not_called()
        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        payload = json.loads(record.getMessage())
        self.assertEqual(payload["event"], "selfie_submission_finished")
        self.assertEqual(payload["event_id"], str(self.event.pk))
        self.assertEqual(payload["outcome"], "rejected")
        self.assertEqual(payload["reason_code"], reason)
        self.assertEqual(payload["actual_format"], actual_format)
        self.assertEqual(payload["declared_type"], declared_type)
        self.assertContains(response, message, status_code=422)
        self.assertContains(response, 'id="selfie-search"', status_code=422)
        self.assertContains(response, 'role="alert"', status_code=422)
        self.assertContains(
            response,
            (
                f'action="{reverse("selfie_search:submit", kwargs={"event_slug": self.event.slug})}'
                '#selfie-search"'
            ),
            status_code=422,
        )
        self.assertContains(response, ">Найти мои фото</button>", status_code=422)
        self.assertNotContains(response, 'type="submit" disabled', status_code=422)
        self.assertEqual(response.content.count(b'role="alert"'), 1)
        captured = repr(record.__dict__) + "\n".join(logs.output)
        for forbidden in (
            "selfie.jpg",
            "selfie.bin",
            "application/octet-stream",
            "selfie-search/",
            "signed",
            "vector",
            "traceback",
        ):
            self.assertNotIn(forbidden, captured)

    def test_customer_correctable_rejections_have_one_safe_event_and_no_side_effects(self) -> None:
        content = BytesIO()
        Image.new("RGB", (8, 8), color="white").save(content, format="GIF")
        cases = (
            (None, "missing_or_empty", "Выберите фотографию для поиска.", "unknown", "missing"),
            (
                SimpleUploadedFile(
                    "selfie.bin", content.getvalue(), content_type="application/octet-stream"
                ),
                "unsupported_format",
                "Не удалось прочитать фотографию. Выберите JPEG, PNG, HEIC или HEIF.",
                "unknown",
                "octet_stream",
            ),
            (
                SimpleUploadedFile("selfie.jpg", b"\xff\xd8\xff", content_type="image/jpeg"),
                "corrupt_image",
                "Фотография повреждена. Выберите другой файл.",
                "unknown",
                "jpeg",
            ),
            (
                SimpleUploadedFile(
                    "selfie.jpg", b"x" * (20 * 1024 * 1024 + 1), content_type="image/jpeg"
                ),
                "source_too_large",
                "Размер фотографии не должен превышать 20 МиБ.",
                "unknown",
                "jpeg",
            ),
            (
                _view_pixel_limit_upload(),
                "pixel_limit_exceeded",
                "Изображение слишком большое. Уменьшите его так, чтобы "
                "ширина × высота были не больше 25 млн пикселей — "
                "например, 5000 × 5000.",
                "png",
                "png",
            ),
        )
        for upload, reason, message, actual_format, declared_type in cases:
            with self.subTest(reason=reason):
                self.assert_customer_rejection(
                    upload=upload,
                    reason=reason,
                    message=message,
                    actual_format=actual_format,
                    declared_type=declared_type,
                )

    def test_draft_selfie_submission_is_staff_only_before_storage_or_search_write(self) -> None:
        draft = Event.objects.create(
            name="Draft selfie preview",
            slug="draft-selfie-preview",
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 30),
            city="Moscow",
            access_type=Event.AccessType.FREE,
            publication_status=Event.PublicationStatus.DRAFT,
            timezone_name="Europe/Moscow",
        )
        url = reverse("selfie_search:submit", kwargs={"event_slug": draft.slug})
        staff_user = get_user_model().objects.create_user(
            username="selfie-preview-staff", is_staff=True
        )
        created = SimpleNamespace(
            search=SimpleNamespace(pk=uuid4()), public_token="staff-preview-token"
        )

        with (
            patch("selfie_search.views.TemporarySelfieStorage") as storage_factory,
            patch(
                "selfie_search.views.submit_selfie_search", return_value=created
            ) as submit_service,
        ):
            anonymous = self.client.post(url, {"selfie": _view_jpeg_upload()})
            submit_service.assert_not_called()
            storage_factory.assert_not_called()
            self.client.force_login(staff_user)
            staff = self.client.post(url, {"selfie": _view_jpeg_upload()})

        self.assertEqual(anonymous.status_code, 404)
        self.assertEqual(staff.status_code, 302)
        submit_service.assert_called_once_with(
            event=draft,
            selfie=ANY,
            storage=storage_factory.return_value,
            user=staff_user,
        )

    def test_normalized_oversize_rejection_is_safe_and_customer_correctable(self) -> None:
        fixture = Path(__file__).parent.joinpath("fixtures", "iphone-oriented.heic").read_bytes()
        upload = SimpleUploadedFile("iphone.heic", fixture, content_type="image/heic")
        with override_settings(SELFIE_SEARCH_MAX_UPLOAD_BYTES=len(fixture)):
            with patch(
                "selfie_search.images._normalize_heif", return_value=b"x" * (len(fixture) + 1)
            ):
                self.assert_customer_rejection(
                    upload=upload,
                    reason="normalized_too_large",
                    message="Размер фотографии не должен превышать 20 МиБ.",
                    actual_format="heic",
                    declared_type="heic",
                )

    def test_storage_failure_is_503_retryable_and_has_no_search_or_job(self) -> None:
        with patch(
            "selfie_search.views.TemporarySelfieStorage",
            return_value=FailingSubmissionStorage(),
        ):
            with self.assertLogs("selfie_search.views", level="WARNING") as logs:
                response = self.post(_view_jpeg_upload())

        self.assertEqual(response.status_code, 503)
        self.assertFalse(SelfieSearch.objects.exists())
        self.assertFalse(SelfieSearchJob.objects.exists())
        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        payload = json.loads(record.getMessage())
        self.assertEqual(payload["outcome"], "storage_unavailable")
        self.assertEqual(payload["reason_code"], "storage_unavailable")
        self.assertEqual(payload["actual_format"], "jpeg")
        self.assertEqual(payload["declared_type"], "jpeg")
        self.assertContains(
            response,
            "Не удалось загрузить фотографию. Попробуйте ещё раз.",
            status_code=503,
        )
        self.assertContains(response, 'role="alert"', status_code=503)
        self.assertContains(response, ">Найти мои фото</button>", status_code=503)

    def test_accepted_submission_emits_after_success_with_created_search_id(self) -> None:
        logger = _CaptureLogger()
        search_id = uuid4()
        created = SimpleNamespace(
            search=SimpleNamespace(pk=search_id), public_token="opaque-public-token"
        )

        with (
            patch("selfie_search.views.logger", logger),
            patch("selfie_search.views.submit_selfie_search", return_value=created),
        ):
            response = self.post(_view_jpeg_upload())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(logger.calls), 1)
        level, line = logger.calls[0]
        payload = json.loads(line)
        self.assertEqual(level, logging.INFO)
        self.assertEqual(payload["outcome"], "accepted")
        self.assertEqual(payload["search_id"], str(search_id))
        self.assertEqual(payload["actual_format"], "jpeg")
        self.assertNotIn("opaque-public-token", line)

    def test_database_failure_never_claims_an_accepted_submission(self) -> None:
        logger = _CaptureLogger()
        with (
            patch("selfie_search.views.logger", logger),
            patch(
                "selfie_search.views.submit_selfie_search",
                side_effect=IntegrityError("database failed"),
            ),
            self.assertRaises(IntegrityError),
        ):
            self.post(_view_jpeg_upload())

        self.assertEqual(logger.calls, [])

    def test_observability_output_failure_keeps_accepted_redirect(self) -> None:
        created = SimpleNamespace(search=SimpleNamespace(pk=uuid4()), public_token="opaque-token")
        with (
            patch("selfie_search.views.logger", _FailingLogger()),
            patch("selfie_search.views.submit_selfie_search", return_value=created),
        ):
            response = self.post(_view_jpeg_upload())

        self.assertEqual(response.status_code, 302)

    def test_observability_serialization_failure_keeps_accepted_redirect(self) -> None:
        logger = _CaptureLogger()
        created = SimpleNamespace(search=SimpleNamespace(pk=uuid4()), public_token="opaque-token")
        with (
            patch("selfie_search.views.logger", logger),
            patch("selfie_search.views.submit_selfie_search", return_value=created),
            patch(
                "selfie_search.observability.json.dumps",
                side_effect=RuntimeError("serialization failed"),
            ),
        ):
            response = self.post(_view_jpeg_upload())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(logger.calls, [(logging.ERROR, OBSERVABILITY_FAILURE_MARKER)])


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
class PublicSelfieResultViewTests(TestCase):
    """The production break caught here is widening a bearer result beyond its saved evidence."""

    def setUp(self) -> None:
        self.photographer = get_user_model().objects.create_user(username="selfie-result-owner")
        self.event = self.make_event(name="City Run", slug="city-run")

    def make_event(self, *, name: str, slug: str, **overrides) -> Event:
        values = {
            "name": name,
            "slug": slug,
            "start_date": date(2026, 7, 30),
            "end_date": date(2026, 7, 30),
            "city": "Moscow",
            "publication_status": Event.PublicationStatus.PUBLISHED,
        }
        values.update(overrides)
        return Event.objects.create(**values)

    def make_private_photo(
        self,
        event: Event,
        *,
        photo_id: str | None = None,
        preview_required: bool = False,
    ) -> Photo:
        identifier = photo_id or uuid4().hex
        return Photo.objects.create(
            id=identifier,
            event=event,
            uploaded_by=self.photographer,
            original_key=f"originals/{uuid4().hex}",
            original_filename=f"{identifier}.jpg",
            original_size=5,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=(
                Photo.ProcessingGeneration.PREVIEW_FIRST_V1
                if preview_required
                else Photo.ProcessingGeneration.LEGACY_ORIGINAL_V1
            ),
            gallery_media_policy=(
                Photo.GalleryMediaPolicy.PREVIEW_REQUIRED
                if preview_required
                else Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED
            ),
        )

    def make_search(
        self,
        *,
        event: Event | None = None,
        status: ChoiceValue = SelfieSearch.Status.QUEUED,
        eligible_photo_count: int = 0,
        matched_photo_count: int = 0,
    ) -> tuple[SelfieSearch, str]:
        event = event or self.event
        ordinal = SelfieSearch.objects.count()
        token = f"selfie-result-token-{ordinal}"
        search = SelfieSearch.objects.create(
            event=event,
            public_token_digest=hashlib.sha256(token.encode("ascii")).hexdigest(),
            status=status,
            temporary_object_key=(
                ""
                if status == SelfieSearch.Status.READY
                else "selfie-search/0123456789abcdef0123456789abcdef"
            ),
            configuration={"public-contract": 1},
            eligible_photo_count=eligible_photo_count,
            matched_photo_count=matched_photo_count,
        )
        return search, token

    def add_result(self, *, search: SelfieSearch, photo: Photo, rank: int) -> SelfieSearchResult:
        configuration_hash = "a" * 64
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash=configuration_hash,
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash=configuration_hash,
            input_fingerprint={},
        )
        attempt = ProcessingAttempt.objects.create(
            event=photo.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=1,
            processor_type="face_embedding",
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

    def result_url(self, *, event: Event, token: str) -> str:
        return reverse(
            "selfie_search:result",
            kwargs={"event_slug": event.slug, "public_token": token},
        )

    def status_url(self, *, event: Event, token: str) -> str:
        return reverse(
            "selfie_search:status",
            kwargs={"event_slug": event.slug, "public_token": token},
        )

    def process_url(self, *, event: Event, token: str) -> str:
        return reverse(
            "selfie_search:process_gallery_search",
            kwargs={"event_slug": event.slug, "public_token": token},
        )

    def result_media_url(self, *, event: Event, token: str, photo: Photo, variant: str) -> str:
        return reverse(
            "selfie_search:result_media",
            kwargs={
                "event_slug": event.slug,
                "public_token": token,
                "photo_id": photo.id,
                "variant": variant,
            },
        )

    def result_download_url(self, *, event: Event, token: str, photo: Photo) -> str:
        return reverse(
            "selfie_search:result_download",
            kwargs={
                "event_slug": event.slug,
                "public_token": token,
                "photo_id": photo.id,
            },
        )

    def assert_bearer_headers(self, response) -> None:
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_bearer_result_suppresses_metrika_while_catalog_keeps_one_counter(self) -> None:
        search, token = self.make_search()

        bearer_response = self.client.get(self.result_url(event=search.event, token=token))
        catalog_response = self.client.get(reverse("event_catalog"))

        self.assertEqual(bearer_response.status_code, 200)
        self.assertNotContains(bearer_response, "mc.yandex.ru")
        self.assertContains(bearer_response, "data-cookie-notice")
        self.assertEqual(catalog_response.content.count(b'ym(111239706, "init", {'), 1)

    def test_page_exposes_each_public_state_with_bearer_headers(self) -> None:
        state_copy = {
            SelfieSearch.Status.QUEUED: "Ищем ваши фотографии…",
            SelfieSearch.Status.PROCESSING: "Ищем ваши фотографии…",
            SelfieSearch.Status.CLEANUP_PENDING: "Ищем ваши фотографии…",
            SelfieSearch.Status.READY: "Возможные совпадения",
            SelfieSearch.Status.NO_FACE: "На селфи не удалось найти подходящее лицо.",
            SelfieSearch.Status.MULTIPLE_FACES: "На селфи должно быть только одно лицо.",
            SelfieSearch.Status.QUALITY_REJECTED: "Не удалось получить надёжное совпадение.",
            SelfieSearch.Status.SEARCH_UNAVAILABLE: (
                "Для этого события пока нет подходящих фотографий."
            ),
            SelfieSearch.Status.FAILED: "Поиск сейчас недоступен. Попробуйте другое селфи.",
        }

        for status, expected_copy in state_copy.items():
            with self.subTest(status=status):
                search, token = self.make_search(status=status)

                response = self.client.get(self.result_url(event=search.event, token=token))

                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "selfie_search/result.html")
                self.assertEqual(response.context["search"].pk, search.pk)
                self.assertContains(response, search.event.name)
                self.assertContains(response, expected_copy)
                self.assert_bearer_headers(response)

    def test_status_is_bounded_and_omits_sensitive_search_details(self) -> None:
        search, token = self.make_search(
            status=SelfieSearch.Status.READY,
            eligible_photo_count=7,
            matched_photo_count=2,
        )
        private_key = "selfie-search/0123456789abcdef0123456789abcdef"
        search.temporary_object_key = private_key
        search.failure_code = "raw worker callback and signed URL must stay private"
        search.save(update_fields=["temporary_object_key", "failure_code"])

        response = self.client.get(self.status_url(event=search.event, token=token))

        self.assertEqual(
            response.json(),
            {"status": "ready", "eligible_photo_count": 7, "matched_photo_count": 2},
        )
        self.assert_bearer_headers(response)
        body = response.content.decode()
        self.assertNotIn(token, body)
        self.assertNotIn(private_key, body)
        self.assertNotIn(search.failure_code, body)
        self.assertNotIn("configuration", body)

    def test_status_reopens_each_nonready_public_state_without_extra_details(self) -> None:
        for status in (
            SelfieSearch.Status.QUEUED,
            SelfieSearch.Status.PROCESSING,
            SelfieSearch.Status.CLEANUP_PENDING,
            SelfieSearch.Status.NO_FACE,
            SelfieSearch.Status.MULTIPLE_FACES,
            SelfieSearch.Status.QUALITY_REJECTED,
            SelfieSearch.Status.SEARCH_UNAVAILABLE,
            SelfieSearch.Status.FAILED,
        ):
            with self.subTest(status=status):
                search, token = self.make_search(status=status)

                response = self.client.get(self.status_url(event=search.event, token=token))

                self.assertEqual(response.json(), {"status": status})
                self.assert_bearer_headers(response)

    def test_ready_page_reopens_saved_result_order_without_recomputation(self) -> None:
        search, token = self.make_search(
            status=SelfieSearch.Status.READY,
            eligible_photo_count=3,
            matched_photo_count=2,
        )
        first = self.make_private_photo(search.event, photo_id="rank-one")
        second = self.make_private_photo(search.event, photo_id="rank-two")
        self.add_result(search=search, photo=second, rank=2)
        self.add_result(search=search, photo=first, rank=1)
        late_photo = self.make_private_photo(search.event, photo_id="added-later")

        first_open = self.client.get(self.result_url(event=search.event, token=token))
        second_open = self.client.get(self.result_url(event=search.event, token=token))

        for response in (first_open, second_open):
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                [item.photo_id for item in response.context["gallery_photos"]],
                [first.id, second.id],
            )
            self.assertNotContains(response, late_photo.id)
            self.assertContains(response, "2")
            self.assertContains(response, "3")

        for photo in (first, second):
            download_url = self.result_download_url(event=search.event, token=token, photo=photo)
            lightbox_download = (
                f'<a class="gallery-lightbox-download" href="{download_url}" '
                'aria-label="Скачать оригинал" title="Скачать оригинал">'
                '<svg class="icon" aria-hidden="true"><use '
                'href="/static/ui/icons.svg#download"></use></svg></a>'
            )
            self.assertContains(
                first_open,
                f'<a class="gallery-download" href="{download_url}" '
                'aria-label="Скачать оригинал" title="Скачать оригинал">',
            )
            self.assertContains(
                first_open,
                f"data-description='{lightbox_download}'",
            )
        self.assertNotContains(first_open, "gallery-photo-id")

    def test_ready_page_renders_available_capture_time_on_result_card(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        first = self.make_private_photo(search.event, photo_id="timed-result")
        second = self.make_private_photo(search.event, photo_id="untimed-result")
        self.add_result(search=search, photo=first, rank=1)
        self.add_result(search=search, photo=second, rank=2)

        original_factory = GalleryPhotoFactory.from_photo

        def presentation_with_time(**kwargs):
            presentation = original_factory(**kwargs)
            if kwargs["photo"].id == first.id:
                return replace(presentation, capture_time_display="15:34")
            return presentation

        with patch(
            "selfie_search.views.GalleryPhotoFactory.from_photo",
            side_effect=presentation_with_time,
        ):
            response = self.client.get(self.result_url(event=search.event, token=token))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<time class="gallery-card-time">15:34</time>')
        self.assertContains(response, 'class="gallery-card-time"', count=1)

    def test_result_page_uses_the_compact_event_metadata_header(self) -> None:
        search, token = self.make_search()

        response = self.client.get(self.result_url(event=search.event, token=token))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<header class="event-detail-header">', count=1)
        self.assertContains(response, '<h1 id="selfie-search-result-title">City Run</h1>')
        self.assertContains(
            response,
            '<p class="event-detail-meta"><span>Moscow</span><span>30.07.2026</span></p>',
        )
        self.assertContains(response, "Поиск показывает вероятные совпадения.")

    @override_settings(SELFIE_FEEDBACK_ENABLED=True)
    def test_terminal_feedback_markup_uses_the_saved_result_membership_and_exact_consent(
        self,
    ) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        photo = self.make_private_photo(search.event, photo_id="feedback-result")
        result = self.add_result(search=search, photo=photo, rank=1)

        response = self.client.get(self.result_url(event=search.event, token=token))

        self.assertContains(response, "data-selfie-feedback")
        self.assertContains(response, 'data-feedback-variant="result_labels"')
        self.assertContains(response, f'data-feedback-result-id="{result.pk}"')
        self.assertContains(response, 'data-feedback-total="1"')
        self.assertContains(response, "data-feedback-unavailable")
        self.assertContains(
            response,
            "Отзыв об этом поиске можно отправить только из браузера, где он был начат, "
            "пока локальная копия селфи ещё доступна.",
        )
        self.assertContains(response, 'class="selfie-search-terminal-actions"', count=1)
        self.assertContains(response, "data-feedback-form")
        body = response.content.decode()
        self.assertLess(
            body.index("Искать по другому селфи"),
            body.index("Помогите улучшить поиск"),
        )
        self.assertNotContains(response, "Оценить качество поиска")
        self.assertNotContains(response, "Закрыть")
        self.assertNotContains(response, "Не спрашивать больше на этом устройстве")
        self.assertNotContains(response, "data-feedback-open")
        self.assertNotContains(response, "data-feedback-close")
        self.assertNotContains(response, "data-feedback-opt-out")
        self.assertNotContains(response, "data-feedback-open-initial")
        self.assertNotContains(response, "data-feedback-form hidden")
        self.assertContains(response, "Размечено 0 из 1 фотографий")
        self.assertContains(response, "Я есть")
        self.assertContains(response, "Меня нет")
        self.assertContains(
            response,
            '<details class="selfie-search-feedback-contact">',
        )
        self.assertNotContains(
            response,
            '<details class="selfie-search-feedback-contact" open>',
        )
        self.assertContains(response, "Оставить контакт для связи — необязательно")
        self.assertContains(response, '<label for="feedback-contact">Контакт для связи</label>')
        self.assertNotContains(
            response,
            '<input id="feedback-contact" name="contact" type="text" maxlength="254" required',
        )
        self.assertContains(response, "Телефон, Telegram или email")
        self.assertContains(
            response,
            "К отзыву приложим ваше селфи — то самое, которое вы использовали для этого поиска. "
            "Повторно выбирать файл не нужно.",
        )
        self.assertContains(
            response,
            "Я согласен на обработку моего селфи и оценки результатов поиска для анализа качества "
            "поиска, а если оставлю контакт — также контактных данных для связи со мной "
            "в соответствии с ",
        )
        self.assertContains(response, "ui/legal/personal-data-policy.pdf")
        self.assertNotContains(response, 'type="file"')

    @override_settings(SELFIE_FEEDBACK_ENABLED=True)
    def test_terminal_problem_feedback_has_no_result_questions(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.NO_FACE)

        response = self.client.get(self.result_url(event=search.event, token=token))

        self.assertContains(response, 'class="selfie-search-terminal-actions"', count=1)
        body = response.content.decode()
        self.assertLess(
            body.index("Искать по другому селфи"),
            body.index("Помогите улучшить поиск"),
        )
        self.assertNotContains(response, "data-feedback-form hidden")
        self.assertContains(response, 'data-feedback-variant="problem"')
        self.assertContains(response, "Помогите улучшить поиск")
        self.assertNotContains(response, "Оценить качество поиска")
        self.assertNotContains(response, "Закрыть")
        self.assertNotContains(response, "Не спрашивать больше на этом устройстве")
        self.assertNotContains(response, "data-feedback-open")
        self.assertNotContains(response, "data-feedback-close")
        self.assertNotContains(response, "data-feedback-opt-out")
        self.assertNotContains(response, "Я есть")
        self.assertNotContains(response, "Меня нет")

    @override_settings(SELFIE_FEEDBACK_ENABLED=True)
    def test_terminal_result_confirms_already_submitted_feedback_without_new_form(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.NO_FACE)
        SelfieSearchFeedback.objects.create(
            search=search,
            variant=SelfieSearchFeedback.Variant.PROBLEM,
            contact="customer@example.test",
            personal_data_consent=True,
            consent_text_version="2026-08-05",
            consented_at=timezone.now(),
            source_status=search.status,
            source_configuration=search.configuration,
            object_key=f"feedback/{uuid4().hex}",
            object_content_type="image/jpeg",
            object_size=1,
            object_uploaded_at=timezone.now(),
        )

        response = self.client.get(self.result_url(event=search.event, token=token))

        self.assertContains(response, 'class="selfie-search-terminal-actions"', count=1)
        body = response.content.decode()
        self.assertLess(body.index("Искать по другому селфи"), body.index("data-feedback-cleanup"))
        self.assertNotContains(response, "Спасибо, отзыв отправлен.")
        self.assertContains(response, "data-feedback-cleanup")
        self.assertNotContains(response, "data-feedback-cleanup-retry")
        self.assertNotContains(response, "Повторить очистку")
        self.assertNotContains(
            response,
            '<section class="selfie-search-feedback" data-selfie-feedback',
        )

    def test_ready_page_omits_removed_member_without_reranking_remaining_members(self) -> None:
        search, token = self.make_search(
            status=SelfieSearch.Status.READY,
            eligible_photo_count=3,
            matched_photo_count=3,
        )
        first = self.make_private_photo(search.event, photo_id="rank-one")
        removed = self.make_private_photo(search.event, photo_id="rank-two")
        third = self.make_private_photo(search.event, photo_id="rank-three")
        self.add_result(search=search, photo=first, rank=1)
        self.add_result(search=search, photo=removed, rank=2)
        self.add_result(search=search, photo=third, rank=3)
        Photo.objects.filter(pk=removed.pk).update(original_key="")

        response = self.client.get(self.result_url(event=search.event, token=token))

        self.assertEqual(
            [item.photo_id for item in response.context["gallery_photos"]], [first.id, third.id]
        )
        self.assertNotContains(response, removed.id)

    def test_ready_page_omits_preview_required_member_without_accepted_preview(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        photo = self.make_private_photo(
            search.event, photo_id="preview-not-ready", preview_required=True
        )
        self.add_result(search=search, photo=photo, rank=1)

        response = self.client.get(self.result_url(event=search.event, token=token))

        self.assertEqual(response.context["gallery_photos"], ())
        self.assertNotContains(response, photo.pk)

    def test_ready_page_uses_numbered_pages_without_reranking_or_expanding_membership(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        photos = [
            self.make_private_photo(search.event, photo_id=f"rank-{index:03}")
            for index in range(101)
        ]
        for index, photo in enumerate(photos, start=1):
            self.add_result(search=search, photo=photo, rank=index)
        unrelated = self.make_private_photo(search.event, photo_id="not-in-snapshot")

        first_response = self.client.get(self.result_url(event=search.event, token=token))

        self.assertEqual(
            [item.photo_id for item in first_response.context["gallery_photos"]],
            [photo.pk for photo in photos[:100]],
        )
        self.assertNotContains(first_response, unrelated.pk)
        self.assertContains(first_response, "Страница 1 из 2")
        self.assertContains(first_response, "?page=2")
        self.assertContains(first_response, '<form class="gallery-pagination-form" method="get">')
        self.assertNotContains(first_response, "#gallery")
        self.assertContains(first_response, 'name="page"')
        self.assertContains(first_response, 'type="number"')
        self.assertContains(first_response, 'min="1"')
        self.assertContains(first_response, 'max="2"')
        self.assertContains(first_response, 'value="1"')
        self.assertContains(first_response, "Перейти")

        later_response = self.client.get(
            self.result_url(event=search.event, token=token), {"page": 2}
        )

        self.assertEqual(
            [item.photo_id for item in later_response.context["gallery_photos"]],
            [photos[100].pk],
        )
        self.assertContains(later_response, "Страница 2 из 2")
        self.assertContains(later_response, "?page=1")
        self.assertContains(later_response, '<form class="gallery-pagination-form" method="get">')
        self.assertNotContains(later_response, "#gallery")
        self.assertContains(later_response, 'name="page"')
        self.assertContains(later_response, 'type="number"')
        self.assertContains(later_response, 'min="1"')
        self.assertContains(later_response, 'max="2"')
        self.assertContains(later_response, 'value="2"')
        self.assertContains(later_response, "Перейти")

    def test_ready_page_rejects_invalid_page(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        for invalid_page in ("bad", "0", "2"):
            with self.subTest(page=invalid_page):
                response = self.client.get(
                    self.result_url(event=search.event, token=token), {"page": invalid_page}
                )
                self.assertEqual(response.status_code, 404)

    def test_nonready_page_does_not_expose_pagination(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.PROCESSING)

        response = self.client.get(
            self.result_url(event=search.event, token=token), {"page": "not-a-page"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Показать ещё")
        self.assertNotContains(response, "gallery-pagination")

    def test_event_token_mismatch_and_unpublished_event_are_not_resolvable(self) -> None:
        other_event = self.make_event(name="Other Run", slug="other-run")
        main_search, main_token = self.make_search(event=self.event)
        _, other_token = self.make_search(event=other_event)
        draft_event = self.make_event(
            name="Draft Run",
            slug="draft-run",
            publication_status=Event.PublicationStatus.DRAFT,
        )
        _, draft_token = self.make_search(event=draft_event)

        mismatch = self.client.get(self.result_url(event=main_search.event, token=other_token))
        draft = self.client.get(self.result_url(event=draft_event, token=draft_token))
        self.event.publication_status = Event.PublicationStatus.DRAFT
        self.event.save(update_fields=["publication_status"])
        unpublished = self.client.get(self.result_url(event=self.event, token=main_token))

        self.assertEqual(mismatch.status_code, 404)
        self.assertEqual(draft.status_code, 404)
        self.assertEqual(unpublished.status_code, 404)

    def test_draft_bearer_result_and_signed_media_are_staff_only(self) -> None:
        draft_event = self.make_event(
            name="Draft bearer preview",
            slug="draft-bearer-preview",
            publication_status=Event.PublicationStatus.DRAFT,
            timezone_name="Europe/Moscow",
        )
        search, token = self.make_search(
            event=draft_event,
            status=SelfieSearch.Status.READY,
            matched_photo_count=1,
        )
        photo = self.make_private_photo(draft_event, photo_id="draft-bearer-photo")
        self.add_result(search=search, photo=photo, rank=1)
        urls = (
            self.result_url(event=draft_event, token=token),
            self.status_url(event=draft_event, token=token),
            self.result_media_url(
                event=draft_event,
                token=token,
                photo=photo,
                variant="preview-small",
            ),
            self.result_download_url(event=draft_event, token=token, photo=photo),
        )
        ordinary_user = get_user_model().objects.create_user(username="draft-bearer-ordinary")
        staff_user = get_user_model().objects.create_user(
            username="draft-bearer-staff", is_staff=True
        )

        with patch("selfie_search.views._public_media_resolver") as resolver_factory:
            anonymous = tuple(self.client.get(url) for url in urls)
            self.client.force_login(ordinary_user)
            ordinary = tuple(self.client.get(url) for url in urls)
            resolver_factory.assert_not_called()

        resolver = Mock()
        resolver.resolve_signed.return_value = "https://storage.example.test/preview?signed"
        resolver.resolve_download.return_value = "https://storage.example.test/original?signed"
        self.client.force_login(staff_user)
        with patch("selfie_search.views._public_media_resolver", return_value=resolver):
            staff = tuple(self.client.get(url) for url in urls)

        self.assertEqual([response.status_code for response in (*anonymous, *ordinary)], [404] * 8)
        self.assertEqual([response.status_code for response in staff], [200, 200, 302, 302])
        self.assertContains(staff[0], "Черновик — виден только администраторам")
        resolver.resolve_signed.assert_called_once_with(photo=photo, variant="preview-small")
        resolver.resolve_download.assert_called_once_with(photo=photo)

    @patch("selfie_search.views.process_gallery_photo_search")
    def test_draft_gallery_process_requires_current_staff_visibility(
        self, process_gallery_photo_search
    ) -> None:
        draft_event = self.make_event(
            name="Draft process preview",
            slug="draft-process-preview",
            publication_status=Event.PublicationStatus.DRAFT,
            timezone_name="Europe/Moscow",
        )
        search, token = self.make_search(event=draft_event)
        search.configuration = {"processor": "gallery_photo_query"}
        search.save(update_fields=["configuration"])
        process_gallery_photo_search.return_value = search
        url = self.process_url(event=draft_event, token=token)
        ordinary_user = get_user_model().objects.create_user(username="draft-process-ordinary")
        staff_user = get_user_model().objects.create_user(
            username="draft-process-staff", is_staff=True
        )

        anonymous = self.client.post(url)

        self.assertEqual(anonymous.status_code, 404)
        process_gallery_photo_search.assert_not_called()

        self.client.force_login(ordinary_user)
        ordinary = self.client.post(url)

        self.assertEqual(ordinary.status_code, 404)
        process_gallery_photo_search.assert_not_called()

        self.client.force_login(staff_user)
        staff = self.client.post(url)

        self.assertRedirects(
            staff,
            self.result_url(event=draft_event, token=token),
            fetch_redirect_response=False,
        )
        process_gallery_photo_search.assert_called_once_with(search=search)

        process_gallery_photo_search.reset_mock()
        draft_event.publication_status = Event.PublicationStatus.UNAVAILABLE
        draft_event.save(update_fields=["publication_status"])
        unavailable = self.client.post(url)

        self.assertEqual(unavailable.status_code, 404)
        process_gallery_photo_search.assert_not_called()

    def test_unknown_bearer_routes_keep_private_response_headers(self) -> None:
        photo = self.make_private_photo(self.event, photo_id="missing-result-member")

        with self.assertNoLogs("django.request", level="WARNING"):
            responses = (
                self.client.get(self.result_url(event=self.event, token="unknown-token")),
                self.client.get(self.status_url(event=self.event, token="unknown-token")),
                self.client.get(
                    self.result_media_url(
                        event=self.event,
                        token="unknown-token",
                        photo=photo,
                        variant="preview-small",
                    )
                ),
            )

        for response in responses:
            self.assertEqual(response.status_code, 404)
            self.assert_bearer_headers(response)

    def test_bearer_routes_allow_head_as_a_read_only_request(self) -> None:
        photo = self.make_private_photo(self.event, photo_id="head-result-member")

        responses = (
            self.client.head(self.result_url(event=self.event, token="unknown-token")),
            self.client.head(self.status_url(event=self.event, token="unknown-token")),
            self.client.head(
                self.result_media_url(
                    event=self.event,
                    token="unknown-token",
                    photo=photo,
                    variant="preview-small",
                )
            ),
        )

        for response in responses:
            self.assertEqual(response.status_code, 404)
            self.assert_bearer_headers(response)

    def test_missing_trailing_slash_redirects_to_the_original_bearer_url(self) -> None:
        search, token = self.make_search()
        result_url = self.result_url(event=search.event, token=token)

        response = self.client.get(result_url.rstrip("/"))

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], result_url)

    def test_csrf_checked_client_rejects_mutation_before_csrf_for_every_bearer_route(self) -> None:
        search, token = self.make_search()
        photo = self.make_private_photo(self.event, photo_id="csrf-result-member")
        csrf_client = Client(enforce_csrf_checks=True)
        urls = (
            self.result_url(event=search.event, token=token),
            self.status_url(event=search.event, token=token),
            self.result_media_url(
                event=search.event,
                token=token,
                photo=photo,
                variant="preview-small",
            ),
        )

        with self.assertNoLogs("django.security.csrf", level="WARNING"):
            with self.assertNoLogs("django.request", level="WARNING"):
                responses = tuple(csrf_client.post(url) for url in urls)

        for response in responses:
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response["Allow"], "GET, HEAD")
            self.assert_bearer_headers(response)

    @override_settings(DEBUG=False)
    def test_unexpected_bearer_error_keeps_headers_and_sanitizes_request_log(self) -> None:
        search, token = self.make_search()
        client = Client()
        client.raise_request_exception = False

        with patch(
            "selfie_search.views.resolve_public_result",
            side_effect=RuntimeError("forced bearer failure"),
        ):
            with self.assertNoLogs("django.request", level="ERROR"):
                response = client.get(self.result_url(event=search.event, token=token))

        self.assertEqual(response.status_code, 500)
        self.assert_bearer_headers(response)

    def test_bearer_page_rejects_non_read_method_without_logging_the_token(self) -> None:
        search, token = self.make_search()

        with self.assertNoLogs("django.request", level="WARNING"):
            response = self.client.post(self.result_url(event=search.event, token=token))

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "GET, HEAD")
        self.assert_bearer_headers(response)

    def test_saved_result_media_redirects_for_free_and_paid_events_without_streaming(self) -> None:
        for access_type in (Event.AccessType.FREE, Event.AccessType.PAID):
            with self.subTest(access_type=access_type):
                access_type_value = cast(str, access_type)
                event = self.make_event(
                    name=f"{access_type_value.title()} Run",
                    slug=f"{access_type_value}-run",
                    access_type=access_type,
                )
                search, token = self.make_search(event=event, status=SelfieSearch.Status.READY)
                photo = self.make_private_photo(event, photo_id=f"{access_type_value}-result")
                self.add_result(search=search, photo=photo, rank=1)
                resolver = Mock()
                resolver.resolve_signed.return_value = (
                    "https://storage.example.test/photo?signature=secret"
                )

                with patch("selfie_search.views._public_media_resolver", return_value=resolver):
                    response = self.client.get(
                        self.result_media_url(
                            event=event,
                            token=token,
                            photo=photo,
                            variant="preview-small",
                        )
                    )

                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], resolver.resolve_signed.return_value)
                self.assert_bearer_headers(response)
                self.assertFalse(response.streaming)
                resolver.resolve_signed.assert_called_once_with(
                    photo=photo, variant="preview-small"
                )

    def test_saved_result_download_redirects_to_a_signed_original_without_a_body(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        photo = self.make_private_photo(search.event, photo_id="download-result")
        self.add_result(search=search, photo=photo, rank=1)
        resolver = Mock()
        resolver.resolve_download.return_value = (
            "https://storage.example.test/original?signature=secret"
        )

        with patch("selfie_search.views._public_media_resolver", return_value=resolver):
            response = self.client.get(
                self.result_download_url(event=search.event, token=token, photo=photo)
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], resolver.resolve_download.return_value)
        self.assertEqual(response.content, b"")
        self.assertFalse(response.streaming)
        self.assert_bearer_headers(response)
        resolver.resolve_download.assert_called_once_with(photo=photo)

    def test_saved_result_download_maps_storage_failures_to_existing_responses(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        photo = self.make_private_photo(search.event, photo_id="download-storage-result")
        self.add_result(search=search, photo=photo, rank=1)
        resolver = Mock()

        with patch("selfie_search.views._public_media_resolver", return_value=resolver):
            resolver.resolve_download.side_effect = ObjectMissing()
            missing_response = self.client.get(
                self.result_download_url(event=search.event, token=token, photo=photo)
            )
            resolver.resolve_download.side_effect = StorageError()
            unavailable_response = self.client.get(
                self.result_download_url(event=search.event, token=token, photo=photo)
            )

        self.assertEqual(missing_response.status_code, 404)
        self.assertEqual(unavailable_response.status_code, 503)
        self.assert_bearer_headers(missing_response)
        self.assert_bearer_headers(unavailable_response)

    def test_saved_result_media_uses_the_requested_preview_or_original_selection(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        photo = self.make_private_photo(search.event, photo_id="selection-result")
        self.add_result(search=search, photo=photo, rank=1)
        resolver = Mock()
        resolver.resolve_signed.side_effect = (
            "https://storage.example.test/preview?signature=secret",
            "https://storage.example.test/original?signature=secret",
        )

        with patch("selfie_search.views._public_media_resolver", return_value=resolver):
            preview_response = self.client.get(
                self.result_media_url(
                    event=search.event,
                    token=token,
                    photo=photo,
                    variant="preview-small",
                )
            )
            original_response = self.client.get(
                self.result_media_url(
                    event=search.event,
                    token=token,
                    photo=photo,
                    variant="preview-large",
                )
            )

        self.assertEqual(
            preview_response["Location"], "https://storage.example.test/preview?signature=secret"
        )
        self.assertEqual(
            original_response["Location"], "https://storage.example.test/original?signature=secret"
        )
        self.assertEqual(
            resolver.resolve_signed.call_args_list,
            [
                call(photo=photo, variant="preview-small"),
                call(photo=photo, variant="preview-large"),
            ],
        )

    def test_saved_result_media_maps_missing_object_and_signing_failure(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        photo = self.make_private_photo(search.event, photo_id="storage-result")
        self.add_result(search=search, photo=photo, rank=1)
        resolver = Mock()

        with patch("selfie_search.views._public_media_resolver", return_value=resolver):
            resolver.resolve_signed.side_effect = ObjectMissing()
            missing_response = self.client.get(
                self.result_media_url(
                    event=search.event,
                    token=token,
                    photo=photo,
                    variant="preview-small",
                )
            )
            resolver.resolve_signed.side_effect = StorageError()
            unavailable_response = self.client.get(
                self.result_media_url(
                    event=search.event,
                    token=token,
                    photo=photo,
                    variant="preview-small",
                )
            )

        self.assertEqual(missing_response.status_code, 404)
        self.assertEqual(unavailable_response.status_code, 503)
        self.assert_bearer_headers(missing_response)
        self.assert_bearer_headers(unavailable_response)

    def test_saved_result_media_rejects_preview_required_member_before_signing(self) -> None:
        paid_event = self.make_event(
            name="Paid Run", slug="paid-preview-result", access_type=Event.AccessType.PAID
        )
        search, token = self.make_search(event=paid_event, status=SelfieSearch.Status.READY)
        photo = self.make_private_photo(
            search.event, photo_id="preview-not-ready-media", preview_required=True
        )
        self.add_result(search=search, photo=photo, rank=1)
        resolver = Mock()

        with patch("selfie_search.views._public_media_resolver", return_value=resolver):
            responses = tuple(
                self.client.get(
                    self.result_media_url(
                        event=search.event,
                        token=token,
                        photo=photo,
                        variant=variant,
                    )
                )
                for variant in ("preview-small", "preview-large")
            )

        for response in responses:
            self.assertEqual(response.status_code, 404)
            self.assert_bearer_headers(response)
        resolver.resolve_signed.assert_not_called()

    def test_media_rejects_normal_paid_gallery_and_every_nonmember_access(self) -> None:
        paid_event = self.make_event(
            name="Paid Run", slug="paid-run", access_type=Event.AccessType.PAID
        )
        other_event = self.make_event(name="Other Run", slug="other-run")
        search, token = self.make_search(event=paid_event, status=SelfieSearch.Status.READY)
        member = self.make_private_photo(paid_event, photo_id="saved-member")
        unrelated = self.make_private_photo(paid_event, photo_id="unrelated")
        self.add_result(search=search, photo=member, rank=1)
        queued_search, queued_token = self.make_search(event=paid_event)
        resolver = Mock()

        with (
            patch("selfie_search.views._public_media_resolver", return_value=resolver),
            patch("config.views._public_media_resolver") as normal_resolver,
        ):
            normal_paid = self.client.get(
                reverse(
                    "photo_media",
                    kwargs={
                        "slug": paid_event.slug,
                        "photo_id": member.id,
                        "variant": "preview-small",
                    },
                )
            )
            unrelated_response = self.client.get(
                self.result_media_url(
                    event=paid_event,
                    token=token,
                    photo=unrelated,
                    variant="preview-small",
                )
            )
            unrelated_download_response = self.client.get(
                self.result_download_url(event=paid_event, token=token, photo=unrelated)
            )
            wrong_event = self.client.get(
                self.result_media_url(
                    event=other_event,
                    token=token,
                    photo=member,
                    variant="preview-small",
                )
            )
            nonready = self.client.get(
                self.result_media_url(
                    event=paid_event,
                    token=queued_token,
                    photo=member,
                    variant="preview-small",
                )
            )
            invalid_token = self.client.get(
                self.result_media_url(
                    event=paid_event,
                    token="unknown-token",
                    photo=member,
                    variant="preview-small",
                )
            )
            invalid_variant = self.client.get(
                self.result_media_url(
                    event=paid_event,
                    token=token,
                    photo=member,
                    variant="original",
                )
            )

        self.assertEqual(normal_paid.status_code, 404)
        self.assertEqual(unrelated_response.status_code, 404)
        self.assertEqual(unrelated_download_response.status_code, 404)
        self.assertEqual(wrong_event.status_code, 404)
        self.assertEqual(nonready.status_code, 404)
        self.assertEqual(invalid_token.status_code, 404)
        self.assertEqual(invalid_variant.status_code, 404)
        resolver.resolve_signed.assert_not_called()
        resolver.resolve_download.assert_not_called()
        normal_resolver.assert_not_called()
        self.assertFalse(queued_search.results.exists())
