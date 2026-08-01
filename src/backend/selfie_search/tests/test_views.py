import hashlib
from datetime import date
from typing import cast
from unittest.mock import Mock, call, patch
from urllib.parse import quote
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from ingestion.storage import ObjectMissing, StorageError
from picflow.models import Event, Photo
from processing.models import (
    EventProcessingRun,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    ProcessingAttempt,
    ProcessingJob,
)
from selfie_search.models import SelfieSearch, SelfieSearchResult

type ChoiceValue = str | tuple[str, str]


@override_settings(
    SELFIE_SEARCH_ENABLED=True,
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

    def test_published_free_and_paid_events_offer_the_same_safe_selfie_form(self) -> None:
        for event in (self.free_event, self.paid_event):
            with self.subTest(event=event.slug):
                response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

                self.assertContains(response, 'class="selfie-search"')
                self.assertContains(
                    response,
                    reverse("selfie_search:submit", kwargs={"event_slug": event.slug}),
                )
                self.assertContains(response, 'method="post"')
                self.assertContains(response, 'enctype="multipart/form-data"')
                self.assertContains(response, 'name="csrfmiddlewaretoken"')
                self.assertContains(response, 'name="selfie"')
                self.assertContains(response, 'accept="image/jpeg,image/png"')
                self.assertContains(
                    response,
                    "Мы ищем вероятные совпадения только среди фотографий этого события.",
                )
                self.assertContains(response, "Селфи удаляется после подготовки поиска.")
                self.assertContains(
                    response,
                    "Любой, у кого есть ссылка на результат, сможет его открыть.",
                )

    def test_draft_event_has_no_public_selfie_form(self) -> None:
        response = self.client.get(reverse("event_detail", kwargs={"slug": self.draft_event.slug}))

        self.assertEqual(response.status_code, 404)

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
        return SelfieSearchResult.objects.create(
            search=search,
            photo=photo,
            detection=detection,
            rank=rank,
            cosine_distance=0.1,
        )

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
                f'<a class="gallery-lightbox-download" href="{download_url}">Скачать оригинал</a>'
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

    def test_ready_page_uses_token_bound_cursor_without_expanding_saved_membership(self) -> None:
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
            [photo.pk for photo in photos[:50]],
        )
        self.assertNotContains(first_response, unrelated.pk)
        next_cursor = first_response.context["selfie_search_next_cursor"]
        self.assertIsNotNone(next_cursor)
        self.assertNotContains(first_response, "Показать ещё")
        self.assertContains(first_response, "data-event-gallery")

        later_response = self.client.get(
            self.result_url(event=search.event, token=token), {"cursor": next_cursor}
        )

        self.assertEqual(
            [item.photo_id for item in later_response.context["gallery_photos"]],
            [photo.pk for photo in photos[50:100]],
        )
        self.assertIsNotNone(later_response.context["selfie_search_next_cursor"])

    def test_ready_page_rejects_cursor_for_another_public_token(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.READY)
        other_search, other_token = self.make_search(status=SelfieSearch.Status.READY)
        for index in range(101):
            photo = self.make_private_photo(search.event, photo_id=f"rank-{index:03}")
            self.add_result(search=search, photo=photo, rank=index + 1)

        cursor = self.client.get(self.result_url(event=search.event, token=token)).context[
            "selfie_search_next_cursor"
        ]
        response = self.client.get(
            self.result_url(event=other_search.event, token=other_token), {"cursor": cursor}
        )

        self.assertEqual(response.status_code, 404)

    def test_nonready_page_does_not_expose_pagination(self) -> None:
        search, token = self.make_search(status=SelfieSearch.Status.PROCESSING)

        response = self.client.get(
            self.result_url(event=search.event, token=token), {"cursor": "not-a-cursor"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Показать ещё")
        self.assertIsNone(response.context["selfie_search_next_cursor"])

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
