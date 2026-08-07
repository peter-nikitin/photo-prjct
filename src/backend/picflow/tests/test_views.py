import json
from datetime import date, timedelta
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
from uuid import uuid4

from config import views
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import (
    RequestFactory,
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    modify_settings,
    override_settings,
)
from django.urls import reverse
from django.utils import timezone
from ingestion.storage import ObjectMissing, PrivateUploadStorage, StorageError
from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    FACE_EMBEDDING_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    EventProcessingRun,
    FaceEmbedding,
    FaceProcessingAttemptArtifact,
    PhotoDerivative,
    PhotoFaceDetection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    CONTRACT_VERSION as FACE_EMBEDDING_CONTRACT_VERSION,
)
from processing.services.enrollment import (
    FACE_EMBEDDING_CONFIGURATION,
    FACE_EMBEDDING_PROCESSOR_VERSION,
)
from selfie_search.models import SelfieSearch

from picflow.models import Event, Photo


class NavigationMarkupParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchor_hrefs: set[str] = set()
        self.form_actions: set[str] = set()
        self.input_types: set[str] = set()
        self.form_inside_anchor = False
        self._anchor_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a" and (href := attributes.get("href")):
            self.anchor_hrefs.add(href)
            self._anchor_depth += 1
        elif tag == "form" and (action := attributes.get("action")):
            self.form_actions.add(action)
            self.form_inside_anchor = self.form_inside_anchor or self._anchor_depth > 0
        elif tag == "input" and (input_type := attributes.get("type")):
            self.input_types.add(input_type.lower())

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._anchor_depth -= 1


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
@modify_settings(MIDDLEWARE={"remove": "whitenoise.middleware.WhiteNoiseMiddleware"})
class PublicShellTests(SimpleTestCase):
    def test_public_shell_includes_one_metrika_counter_and_cookie_notice(self) -> None:
        response = self.client.get(reverse("legal"))

        self.assertEqual(response.content.count(b'ym(111239706, "init", {'), 1)
        self.assertContains(response, "https://mc.yandex.ru/metrika/tag.js")
        self.assertContains(response, "https://mc.yandex.ru/watch/111239706")
        self.assertContains(
            response,
            (
                "Мы используем файлы cookie, чтобы обеспечить работу нашего сайта и "
                "проанализировать его"
            ),
        )
        self.assertContains(response, "использование. Продолжая использовать этот сайт, вы даете")
        self.assertContains(response, "согласие на использование файлов cookie.")
        self.assertContains(response, "data-cookie-notice")
        self.assertContains(response, "data-cookie-notice-accept")
        self.assertContains(response, 'href="/static/ui/legal/personal-data-policy.pdf"')
        self.assertContains(response, 'src="/static/ui/cookie-notice.js" defer')

    @override_settings(YANDEX_METRIKA_COUNTER_ID=None)
    def test_public_shell_suppresses_metrika_when_counter_is_disabled(self) -> None:
        response = self.client.get(reverse("legal"))

        self.assertNotContains(response, "mc.yandex.ru")
        self.assertNotContains(response, 'ym(111239706, "init", {')

    def test_legal_page_uses_shared_accessible_shell(self) -> None:
        response = self.client.get(reverse("legal"))

        self.assertTemplateUsed(response, "ui/base.html")
        self.assertTemplateUsed(response, "ui/legal.html")
        self.assertContains(response, '<html lang="ru">')
        self.assertContains(response, 'href="#main-content"')
        self.assertContains(response, 'id="main-content"')
        self.assertContains(response, 'href="/static/ui/design-system.css"')
        self.assertContains(response, 'href="/static/ui/catalog.css"')
        self.assertContains(response, f'href="{reverse("event_catalog")}"')
        self.assertContains(response, f'href="{reverse("legal")}"')
        self.assertNotContains(response, f'href="{reverse("admin:index")}"')
        self.assertNotContains(response, "Прототип")
        self.assertContains(response, 'href="tel:+79031275766"')
        self.assertNotContains(response, "mailto:")
        for document_name in (
            "public-offer.pdf",
            "user-agreement.pdf",
            "personal-data-policy.pdf",
        ):
            self.assertContains(response, f'href="/static/ui/legal/{document_name}"')
        for section_id in ("offer", "terms", "personal", "cookies"):
            self.assertNotContains(response, f'id="{section_id}"')

    def test_packaged_legal_documents_match_accepted_sources(self) -> None:
        static_directory = Path(__file__).resolve().parents[2] / "static" / "ui" / "legal"
        expected_hashes = {
            "public-offer.pdf": "33a64514790b8193ad1704cbfaa606504ba73f71d2aaf4c0331480895d494371",
            "user-agreement.pdf": (
                "8da40d74391781495753c14d380ba43ea60d6e510da727ac98e428b7e035a07d"
            ),
            "personal-data-policy.pdf": (
                "7b8be1e72e3d8f939b48cf1458375a8b7635942a06fed918967476e22a77c68d"
            ),
        }

        for document_name, expected_hash in expected_hashes.items():
            with self.subTest(document_name=document_name):
                document = static_directory / document_name
                self.assertTrue(document.is_file())
                self.assertEqual(sha256(document.read_bytes()).hexdigest(), expected_hash)


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
@modify_settings(MIDDLEWARE={"remove": "whitenoise.middleware.WhiteNoiseMiddleware"})
class PageTests(TestCase):
    def setUp(self) -> None:
        Event.objects.update(publication_status=Event.PublicationStatus.DRAFT)

    def make_event(self, **overrides):
        values = {
            "name": "City Run",
            "slug": "city-run",
            "start_date": date.today(),
            "end_date": date.today(),
            "city": "Moscow",
            "publication_status": Event.PublicationStatus.PUBLISHED,
        }
        values.update(overrides)
        return Event.objects.create(**values)

    def test_health_check(self) -> None:
        response = self.client.get(reverse("health"))
        self.assertEqual(response.json(), {"status": "ok"})

    def test_production_page_routes_remain_canonical(self) -> None:
        event = self.make_event()

        self.assertEqual(reverse("event_catalog"), "/")
        self.assertEqual(reverse("event_detail", kwargs={"slug": event.slug}), "/events/city-run/")
        self.assertEqual(reverse("legal"), "/legal/")
        self.assertEqual(reverse("admin:index"), "/admin/")

    def test_catalog_has_empty_state(self) -> None:
        response = self.client.get(reverse("event_catalog"))
        self.assertContains(response, "Событий пока нет")

    def test_catalog_only_shows_published_events(self) -> None:
        self.make_event()
        self.make_event(
            name="Secret", slug="secret", publication_status=Event.PublicationStatus.DRAFT
        )
        response = self.client.get(reverse("event_catalog"))
        self.assertContains(response, "City Run")
        self.assertNotContains(response, "Secret")

    def test_catalog_orders_upcoming_then_past(self) -> None:
        today = date.today()
        near = self.make_event(
            name="Near",
            slug="near",
            start_date=today + timedelta(days=1),
            end_date=today + timedelta(days=1),
        )
        far = self.make_event(
            name="Far",
            slug="far",
            start_date=today + timedelta(days=10),
            end_date=today + timedelta(days=10),
        )
        past = self.make_event(
            name="Past",
            slug="past",
            start_date=today - timedelta(days=2),
            end_date=today - timedelta(days=1),
        )
        self.assertEqual(
            list(self.client.get(reverse("event_catalog")).context["events"]), [near, far, past]
        )

    def test_event_detail_renders_published_event(self) -> None:
        event = self.make_event(description="Race description")
        response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))
        self.assertContains(response, "Race description")

    def test_public_pages_use_shared_accessible_shell(self) -> None:
        event = self.make_event()

        for path in (
            reverse("event_catalog"),
            reverse("event_detail", kwargs={"slug": event.slug}),
            reverse("legal"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertTemplateUsed(response, "ui/base.html")
                self.assertContains(response, '<html lang="ru">')
                self.assertContains(response, 'href="#main-content"')
                self.assertContains(response, 'id="main-content"')
                self.assertContains(response, 'href="/static/ui/design-system.css"')
                self.assertContains(response, 'href="/static/ui/catalog.css"')
                self.assertContains(response, f'href="{reverse("event_catalog")}"')
                self.assertContains(response, f'href="{reverse("legal")}"')
                self.assertNotContains(response, f'href="{reverse("admin:index")}"')

    @override_settings(PHOTO_UPLOAD_ENABLED=True)
    def test_service_navigation_matches_user_capabilities(self) -> None:
        upload_permission = Permission.objects.get(
            content_type__app_label="ingestion", codename="upload_photos"
        )
        users = get_user_model().objects
        ordinary_user = users.create_user(username="ordinary")
        photographer = users.create_user(username="photographer")
        staff_user = users.create_user(username="staff", is_staff=True)
        staff_photographer = users.create_user(username="staff-photographer", is_staff=True)
        photographer.user_permissions.add(upload_permission)
        staff_photographer.user_permissions.add(upload_permission)

        cases = (
            (ordinary_user, False, False),
            (photographer, True, False),
            (staff_user, False, True),
            (staff_photographer, True, True),
        )

        for user, sees_upload, sees_admin in cases:
            with self.subTest(username=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse("event_catalog"))

                if sees_upload:
                    self.assertContains(response, f'href="{reverse("upload_page")}"')
                else:
                    self.assertNotContains(response, f'href="{reverse("upload_page")}"')

                if sees_admin:
                    self.assertContains(response, f'href="{reverse("admin:index")}"')
                else:
                    self.assertNotContains(response, f'href="{reverse("admin:index")}"')

                self.client.logout()

    def test_event_detail_returns_404_for_draft_event(self) -> None:
        draft = self.make_event(
            name="Draft", slug="draft", publication_status=Event.PublicationStatus.DRAFT
        )
        self.assertEqual(
            self.client.get(reverse("event_detail", kwargs={"slug": draft.slug})).status_code, 404
        )

    def test_legacy_events_url_redirects(self) -> None:
        response = self.client.get("/events/")
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_demo_routes_are_removed(self) -> None:
        for path in (
            "/dashboard/",
            "/upload/",
            "/orders/",
            "/promos/",
            "/promotions/",
            "/purchased/",
            "/search/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_public_pages_have_no_external_ui_cdn_or_unfinished_navigation(self) -> None:
        event = self.make_event()
        public_paths = (
            reverse("event_catalog"),
            reverse("event_detail", kwargs={"slug": event.slug}),
            reverse("legal"),
        )
        unfinished_targets = {
            "/upload/",
            "/orders/",
            "/promos/",
            "/promotions/",
            "/purchased/",
            "/search/",
        }

        for path in public_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "unpkg.com")

                markup = NavigationMarkupParser()
                markup.feed(response.content.decode(response.charset))
                navigation_paths = {
                    urlsplit(value).path for value in markup.anchor_hrefs | markup.form_actions
                }

                self.assertTrue(unfinished_targets.isdisjoint(navigation_paths))
                self.assertNotIn("search", markup.input_types)


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
@modify_settings(MIDDLEWARE={"remove": "whitenoise.middleware.WhiteNoiseMiddleware"})
class GalleryPageTests(TestCase):
    def setUp(self) -> None:
        Event.objects.update(publication_status=Event.PublicationStatus.DRAFT)
        self.photographer = get_user_model().objects.create_user(username="gallery-photo")

    def make_event(self, **overrides):
        values = {
            "name": "City Run",
            "slug": "city-run",
            "start_date": date.today(),
            "end_date": date.today(),
            "city": "Moscow",
            "publication_status": Event.PublicationStatus.PUBLISHED,
        }
        values.update(overrides)
        return Event.objects.create(**values)

    def make_private_photo(self, event: Event, **overrides) -> Photo:
        values = {
            "id": uuid4().hex,
            "event": event,
            "src": "",
            "uploaded_by": self.photographer,
            "original_key": f"originals/{uuid4().hex}",
            "original_filename": "race.jpg",
            "original_size": 4,
            "original_content_type": "image/jpeg",
            "uploaded_at": timezone.now(),
        }
        values.update(overrides)
        return Photo.objects.create(**values)

    def publish_preview(self, photo: Photo, *, final_key: str) -> PhotoDerivative:
        configuration = {"generate_preview": {"variant": "preview-small-v1"}}
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=2,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash="a" * 64,
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash="a" * 64,
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
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        state = PhotoProcessingState.objects.get(
            photo=photo, processor_type=GENERATE_PREVIEW_PROCESSOR
        )
        state.status = PhotoProcessingState.Status.SUCCEEDED
        state.current_run = run
        state.current_job = job
        state.current_attempt = attempt
        state.accepted_attempt = attempt
        state.succeeded_at = timezone.now()
        state.save()
        return PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-small-v1",
            final_key=final_key,
            byte_size=10,
            content_type="image/jpeg",
            width=10,
            height=10,
            oriented_source_width=10,
            oriented_source_height=10,
            sha256="a" * 64,
            accepted_attempt=attempt,
        )

    def publish_current_compatible_faces(self, photo: Photo, *, count: int) -> None:
        configuration_hash = sha256(
            json.dumps(FACE_EMBEDDING_CONFIGURATION, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=FACE_EMBEDDING_CONTRACT_VERSION,
            processor_type=FACE_EMBEDDING_PROCESSOR,
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION,
            configuration=FACE_EMBEDDING_CONFIGURATION,
            configuration_hash=configuration_hash,
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=FACE_EMBEDDING_CONTRACT_VERSION,
            processor_type=FACE_EMBEDDING_PROCESSOR,
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION,
            configuration=FACE_EMBEDDING_CONFIGURATION,
            configuration_hash=configuration_hash,
            input_fingerprint={},
        )
        attempt = ProcessingAttempt.objects.create(
            event=photo.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=FACE_EMBEDDING_CONTRACT_VERSION,
            processor_type=FACE_EMBEDDING_PROCESSOR,
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION,
            configuration=FACE_EMBEDDING_CONFIGURATION,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        PhotoProcessingState.objects.create(
            photo=photo,
            processor_type=FACE_EMBEDDING_PROCESSOR,
            status=PhotoProcessingState.Status.SUCCEEDED,
            current_run=run,
            current_job=job,
            current_attempt=attempt,
            accepted_attempt=attempt,
        )
        artifact = FaceProcessingAttemptArtifact.objects.create(attempt=attempt)
        for face_index in range(count):
            detection = PhotoFaceDetection.objects.create(
                artifact=artifact,
                attempt=attempt,
                face_index=face_index,
                status=PhotoFaceDetection.Status.KEPT,
                geometry={
                    "coordinate_space": "preview-small-v1",
                    "pixel_width": 100,
                    "pixel_height": 100,
                    "bbox": [10 + face_index, 20, 20, 20],
                },
            )
            FaceEmbedding.objects.create(
                detection=detection,
                model_version="sface",
                vector=[1.0] + [0.0] * 127,
                metadata={},
            )

    @patch("config.views.PrivateUploadStorage")
    def test_event_detail_builds_filename_ordered_gallery_without_storage(
        self, storage_class
    ) -> None:
        event = self.make_event()
        later = self.make_private_photo(event, id="photo-1", original_filename="z-last.jpg")
        earlier = self.make_private_photo(event, id="photo-2", original_filename="a-first.jpg")

        response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            tuple(item.photo_id for item in response.context["gallery_photos"]),
            (earlier.id, later.id),
        )
        self.assertEqual(
            response.context["gallery_photos"][0].preview_media_small.url,
            reverse(
                "photo_media",
                kwargs={"slug": event.slug, "photo_id": earlier.id, "variant": "preview-small"},
            ),
        )
        storage_class.assert_not_called()

    def test_event_detail_uses_numbered_pages_in_filename_order(self) -> None:
        event = self.make_event()
        for index in range(101):
            self.make_private_photo(
                event,
                id=f"photo-{index:03}",
                original_filename=f"image-{index:03}.jpg",
            )

        first_response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        self.assertEqual(first_response.status_code, 200)
        first_page_ids = tuple(item.photo_id for item in first_response.context["gallery_photos"])
        self.assertEqual(first_page_ids, tuple(f"photo-{index:03}" for index in range(100)))
        self.assertContains(first_response, "Страница 1 из 2")
        self.assertContains(first_response, "?page=2")
        self.assertContains(first_response, '<form class="gallery-pagination-form" method="get">')
        self.assertContains(first_response, 'name="page"')
        self.assertContains(first_response, 'type="number"')
        self.assertContains(first_response, 'min="1"')
        self.assertContains(first_response, 'max="2"')
        self.assertContains(first_response, 'value="1"')
        self.assertContains(first_response, "Перейти")

        second_response = self.client.get(
            reverse("event_detail", kwargs={"slug": event.slug}), {"page": 2}
        )

        second_page_ids = tuple(item.photo_id for item in second_response.context["gallery_photos"])
        self.assertEqual(second_page_ids, ("photo-100",))
        self.assertContains(second_response, "Страница 2 из 2")
        self.assertContains(second_response, "?page=1")
        self.assertContains(second_response, '<form class="gallery-pagination-form" method="get">')
        self.assertContains(second_response, 'name="page"')
        self.assertContains(second_response, 'type="number"')
        self.assertContains(second_response, 'min="1"')
        self.assertContains(second_response, 'max="2"')
        self.assertContains(second_response, 'value="2"')
        self.assertContains(second_response, "Перейти")
        self.assertTrue(set(first_page_ids).isdisjoint(second_page_ids))

        for invalid_page in ("bad", "0", "3"):
            with self.subTest(page=invalid_page):
                response = self.client.get(
                    reverse("event_detail", kwargs={"slug": event.slug}), {"page": invalid_page}
                )
                self.assertEqual(response.status_code, 404)

    def test_event_detail_renders_only_one_page_for_20000_eligible_photos(self) -> None:
        event = self.make_event()
        now = timezone.now()
        Photo.objects.bulk_create(
            [
                Photo(
                    id=f"photo-{index:05}",
                    event=event,
                    src="",
                    uploaded_by=self.photographer,
                    original_key=f"originals/page-{index:05}",
                    original_filename="race.jpg",
                    original_size=4,
                    original_content_type="image/jpeg",
                    uploaded_at=now,
                )
                for index in range(20_000)
            ]
        )

        response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["gallery_photos"]), 100)
        self.assertContains(response, "Страница 1 из 200")

    def test_event_detail_excludes_legacy_other_event_and_paid_originals(self) -> None:
        event = self.make_event()
        included = self.make_private_photo(event, id="included")
        Photo.objects.create(id="legacy", event=event, src="photos/legacy.jpg")
        other_event = self.make_event(name="Other", slug="other")
        self.make_private_photo(other_event, id="other")
        paid_event = self.make_event(name="Paid", slug="paid", access_type=Event.AccessType.PAID)
        self.make_private_photo(paid_event, id="paid")

        response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))
        paid_response = self.client.get(reverse("event_detail", kwargs={"slug": paid_event.slug}))

        self.assertEqual(
            tuple(item.photo_id for item in response.context["gallery_photos"]), (included.id,)
        )
        self.assertEqual(paid_response.context["gallery_photos"], ())

    @patch("config.views.PrivateUploadStorage")
    def test_event_detail_keeps_legacy_and_requires_accepted_preview_for_new_photos(
        self, storage_class
    ) -> None:
        event = self.make_event()
        legacy = self.make_private_photo(event, id="gallery-1")
        preview_states = (
            PhotoProcessingState.Status.NOT_REQUESTED,
            PhotoProcessingState.Status.QUEUED,
            PhotoProcessingState.Status.PROCESSING,
            PhotoProcessingState.Status.RETRY_WAIT,
            PhotoProcessingState.Status.FAILED,
            PhotoProcessingState.Status.CANCELLED,
            PhotoProcessingState.Status.SUCCEEDED,
        )
        for index, status in enumerate(preview_states, start=2):
            photo = self.make_private_photo(
                event,
                id=f"gallery-{index}",
                processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
                gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
            )
            state = PhotoProcessingState.objects.get(
                photo=photo, processor_type=GENERATE_PREVIEW_PROCESSOR
            )
            state.status = status
            state.save(update_fields=["status"])
        published = self.make_private_photo(
            event,
            id="gallery-9",
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )
        derivative = self.publish_preview(
            published,
            final_key="derivatives/previews/gallery-9/preview-small-v1/private-preview.jpg",
        )

        response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            tuple(item.photo_id for item in response.context["gallery_photos"]),
            (legacy.id, published.id),
        )
        markup = response.content.decode(response.charset)
        for secret in (
            legacy.original_key,
            published.original_key,
            derivative.final_key,
            derivative.sha256,
        ):
            self.assertNotIn(secret, markup)
        storage_class.assert_not_called()

    def test_event_detail_gallery_markup_and_loading_policy(self) -> None:
        event = self.make_event()
        photos = [self.make_private_photo(event, id=f"gallery-{index}") for index in range(1, 6)]

        response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        self.assertContains(response, "Фотографии")
        self.assertContains(response, 'class="event-gallery"')
        self.assertContains(response, 'class="event-gallery-count">5 фото</span>')
        self.assertContains(response, "/static/ui/glightbox.min.css")
        self.assertContains(response, "/static/ui/glightbox.min.js")
        self.assertContains(response, "/static/ui/event-gallery.js")
        for index, photo in enumerate(photos, start=1):
            small_url = reverse(
                "photo_media",
                kwargs={"slug": event.slug, "photo_id": photo.id, "variant": "preview-small"},
            )
            large_url = reverse(
                "photo_media",
                kwargs={"slug": event.slug, "photo_id": photo.id, "variant": "preview-large"},
            )
            download_url = reverse(
                "photo_download",
                kwargs={"slug": event.slug, "photo_id": photo.id},
            )
            alt = f"Фото {photo.id} с события {event.name}"
            lightbox_download = (
                f'<a class="gallery-lightbox-download" href="{download_url}" '
                'aria-label="Скачать оригинал" title="Скачать оригинал">'
                '<svg class="icon" aria-hidden="true"><use '
                'href="/static/ui/icons.svg#download"></use></svg></a>'
            )
            self.assertContains(
                response,
                f'class="gallery-card-link glightbox" href="{large_url}" '
                f'data-gallery="event-photos" data-type="image" '
                f"data-description='{lightbox_download}' "
                f'aria-label="Открыть: {alt}"',
            )
            self.assertContains(response, f'src="{small_url}"')
            self.assertContains(response, f'alt="{alt}"')
            self.assertContains(
                response,
                f'<a class="gallery-download" href="{download_url}" '
                'aria-label="Скачать оригинал" title="Скачать оригинал">',
            )
            if index <= 4:
                self.assertContains(
                    response, f'src="{small_url}" loading="eager" fetchpriority="high"'
                )
            else:
                self.assertContains(response, f'src="{small_url}" loading="lazy"')
        self.assertNotContains(response, "gallery-photo-id")

    @override_settings(SELFIE_SEARCH_ENABLED=True)
    def test_event_detail_maps_all_usable_faces_to_exact_submission_urls(self) -> None:
        """The production break caught here is losing a selectable face or addressing it vaguely."""
        event = self.make_event()
        zero_face = self.make_private_photo(event, id="zero-face")
        one_face = self.make_private_photo(event, id="one-face")
        two_faces = self.make_private_photo(event, id="two-faces")
        three_faces = self.make_private_photo(event, id="three-faces")
        four_faces = self.make_private_photo(event, id="four-faces")
        self.publish_current_compatible_faces(one_face, count=1)
        self.publish_current_compatible_faces(two_faces, count=2)
        self.publish_current_compatible_faces(three_faces, count=3)
        self.publish_current_compatible_faces(four_faces, count=4)

        response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        self.assertEqual(response.status_code, 200)
        gallery_photos = response.context["gallery_photos"]
        self.assertEqual(
            [photo.photo_id for photo in gallery_photos],
            [four_faces.pk, one_face.pk, three_faces.pk, two_faces.pk, zero_face.pk],
        )
        expected_counts = {
            zero_face.pk: 0,
            one_face.pk: 1,
            two_faces.pk: 2,
            three_faces.pk: 3,
            four_faces.pk: 4,
        }
        for gallery_photo in gallery_photos:
            self.assertEqual(len(gallery_photo.faces), expected_counts[gallery_photo.photo_id])
            for face in gallery_photo.faces:
                self.assertEqual(
                    face.search_url,
                    reverse(
                        "selfie_search:submit_gallery_face",
                        kwargs={
                            "event_slug": event.slug,
                            "photo_id": gallery_photo.photo_id,
                            "detection_id": face.detection_id,
                        },
                    ),
                )
        markup = response.content.decode(response.charset)
        self.assertNotIn("gallery-similar-search", markup)
        self.assertNotIn("gallery-similar-search-button", markup)
        for photo in (zero_face, one_face, two_faces, three_faces, four_faces):
            self.assertNotIn(photo.original_key, markup)
        self.assertNotIn("vector", markup)
        for photo in (zero_face, one_face, two_faces, three_faces, four_faces):
            download_url = reverse(
                "photo_download", kwargs={"slug": event.slug, "photo_id": photo.pk}
            )
            self.assertContains(response, f'href="{download_url}"')
            self.assertContains(
                response,
                'data-gallery="event-photos" data-type="image"',
                count=5,
            )

    @override_settings(SELFIE_SEARCH_ENABLED=True)
    def test_event_detail_renders_gallery_face_controls(self) -> None:
        """The production break caught here is an ambiguous face starting a direct search."""
        event = self.make_event()
        no_face = self.make_private_photo(event, id="no-face")
        one_face = self.make_private_photo(event, id="one-face")
        two_faces = self.make_private_photo(event, id="two-faces")
        three_faces = self.make_private_photo(event, id="three-faces")
        four_faces = self.make_private_photo(event, id="four-faces")
        self.publish_current_compatible_faces(one_face, count=1)
        self.publish_current_compatible_faces(two_faces, count=2)
        self.publish_current_compatible_faces(three_faces, count=3)
        self.publish_current_compatible_faces(four_faces, count=4)

        response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        self.assertEqual(response.status_code, 200)
        markup = response.content.decode(response.charset)
        self.assertNotIn(no_face.pk + "/similar-search/", markup)
        self.assertContains(
            response,
            'class="gallery-face-button gallery-face-button--direct"',
            count=1,
        )
        self.assertContains(
            response,
            'aria-label="Найти похожие фото этого человека"',
            count=1,
        )
        self.assertContains(
            response,
            '<details class="gallery-face-chooser" data-face-chooser>',
            count=3,
        )
        self.assertContains(
            response,
            'data-face-chooser-trigger aria-label="Выбрать человека для поиска"',
            count=3,
        )
        self.assertContains(response, 'role="dialog" aria-label="Кого искать?"', count=3)
        self.assertContains(response, "+ 2", count=1)
        self.assertContains(response, 'class="gallery-face-grid"', count=3)
        self.assertContains(
            response,
            'style="--face-left: 8.0; --face-top: 18.0; --face-size: 24.0;"',
        )
        self.assertContains(
            response,
            'aria-label="Найти похожие фото человека 1"',
            count=3,
        )
        self.assertContains(
            response,
            'aria-label="Найти похожие фото человека 4"',
            count=1,
        )
        self.assertContains(response, 'name="csrfmiddlewaretoken"', count=11)
        self.assertContains(response, 'class="gallery-face-search"', count=10)
        self.assertContains(response, 'target="_blank"', count=10)
        for photo, face_count in ((one_face, 1), (two_faces, 2), (three_faces, 3), (four_faces, 4)):
            for detection in PhotoFaceDetection.objects.filter(attempt__photo=photo).order_by(
                "face_index"
            ):
                search_url = reverse(
                    "selfie_search:submit_gallery_face",
                    kwargs={
                        "event_slug": event.slug,
                        "photo_id": photo.pk,
                        "detection_id": detection.pk,
                    },
                )
                self.assertContains(response, f'action="{search_url}"', count=1)
            self.assertEqual(
                PhotoFaceDetection.objects.filter(attempt__photo=photo).count(), face_count
            )
        self.assertNotIn("gallery-similar-search", markup)
        markup_parser = NavigationMarkupParser()
        markup_parser.feed(markup)
        self.assertFalse(markup_parser.form_inside_anchor)

    def test_event_detail_empty_gallery_is_accessible(self) -> None:
        event = self.make_event()

        response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        self.assertContains(response, "Фотографии")
        self.assertContains(response, 'class="event-gallery"')
        self.assertContains(response, 'aria-labelledby="gallery-title"')
        self.assertContains(response, 'id="gallery-title"')
        self.assertContains(response, 'class="event-gallery-count">0 фото</span>')
        self.assertContains(response, "Фотографии пока не опубликованы")


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
@modify_settings(MIDDLEWARE={"remove": "whitenoise.middleware.WhiteNoiseMiddleware"})
class EventDetailManualTimeFilterTests(TestCase):
    """The production break caught here is an invalid filter quietly rendering a broad gallery."""

    def setUp(self) -> None:
        Event.objects.update(publication_status=Event.PublicationStatus.DRAFT)
        self.user = get_user_model().objects.create_user(username="manual-time-view")
        self.event = Event.objects.create(
            name="Manual time event",
            slug="manual-time-event",
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 12),
            city="London",
            timezone_name="Europe/London",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )

    def photo(self, photo_id: str, *, filename: str) -> Photo:
        return Photo.objects.create(
            id=photo_id,
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{photo_id}",
            original_filename=filename,
            original_size=4,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def capture_evidence(self, photo: Photo, *, capture_time: str) -> None:
        configuration = {"capture_metadata": {"event_timezone": self.event.timezone_name}}
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=2,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=2,
            configuration=configuration,
            configuration_hash=uuid4().hex + uuid4().hex,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=2,
            configuration=configuration,
            configuration_hash=run.configuration_hash,
            input_fingerprint={},
            status=ProcessingJob.Status.SUCCEEDED,
            completed_at=timezone.now(),
        )
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=2,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=2,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
            result={"capture_time": capture_time},
        )
        state = PhotoProcessingState.objects.get(
            photo=photo, processor_type=CAPTURE_METADATA_PROCESSOR
        )
        state.status = PhotoProcessingState.Status.SUCCEEDED
        state.current_run = run
        state.current_job = job
        state.current_attempt = attempt
        state.accepted_attempt = attempt
        state.save()

    def test_no_manual_parameters_keeps_the_existing_unfiltered_gallery(self) -> None:
        photo = self.photo("unfiltered", filename="unfiltered.jpg")

        response = self.client.get(reverse("event_detail", kwargs={"slug": self.event.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item.photo_id for item in response.context["gallery_photos"]], [photo.pk])
        self.assertFalse(response.context["manual_time_filter_form"].is_requested)
        self.assertFalse(response.context["manual_time_filter_invalid"])

    def test_valid_manual_filter_uses_only_matching_current_evidence_before_paging(self) -> None:
        matching = self.photo("matching", filename="a.jpg")
        outside = self.photo("outside", filename="b.jpg")
        self.capture_evidence(matching, capture_time="2026-06-10T09:00:00Z")
        self.capture_evidence(outside, capture_time="2026-06-10T10:00:00Z")

        response = self.client.get(
            reverse("event_detail", kwargs={"slug": self.event.slug}),
            {"from": "2026-06-10T10:00", "to": "2026-06-10T10:01", "page": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item.photo_id for item in response.context["gallery_photos"]], [matching.pk]
        )
        self.assertTrue(response.context["manual_time_filter_form"].is_valid())
        self.assertFalse(response.context["manual_time_filter_invalid"])

    def test_invalid_manual_values_return_200_without_gallery_or_saved_search_side_effects(
        self,
    ) -> None:
        photo = self.photo("would-be-unfiltered", filename="would-be-unfiltered.jpg")
        selfies_before = SelfieSearch.objects.count()
        jobs_before = ProcessingJob.objects.count()

        response = self.client.get(
            reverse("event_detail", kwargs={"slug": self.event.slug}),
            [("from", ""), ("from", "2026-06-10T10:00")],
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("from", response.context["manual_time_filter_form"].errors)
        self.assertTrue(response.context["manual_time_filter_invalid"])
        self.assertEqual(response.context["gallery_photos"], ())
        self.assertIsNone(response.context["gallery_page"])
        self.assertEqual(SelfieSearch.objects.count(), selfies_before)
        self.assertEqual(ProcessingJob.objects.count(), jobs_before)
        self.assertTrue(Photo.objects.filter(pk=photo.pk).exists())


class GalleryMediaViewTests(TransactionTestCase):
    def setUp(self) -> None:
        Event.objects.update(publication_status=Event.PublicationStatus.DRAFT)
        self.photographer = get_user_model().objects.create_user(username="gallery-media")

    def make_event(self, **overrides):
        values = {
            "name": "City Run",
            "slug": "city-run",
            "start_date": date.today(),
            "end_date": date.today(),
            "city": "Moscow",
            "publication_status": Event.PublicationStatus.PUBLISHED,
        }
        values.update(overrides)
        return Event.objects.create(**values)

    def make_private_photo(self, event: Event, **overrides) -> Photo:
        values = {
            "id": uuid4().hex,
            "event": event,
            "src": "",
            "uploaded_by": self.photographer,
            "original_key": f"originals/{uuid4().hex}",
            "original_filename": "race.jpg",
            "original_size": 4,
            "original_content_type": "image/jpeg",
            "uploaded_at": timezone.now(),
        }
        values.update(overrides)
        return Photo.objects.create(**values)

    def publish_preview(self, photo: Photo, *, final_key: str) -> PhotoDerivative:
        configuration = {"generate_preview": {"variant": "preview-small-v1"}}
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=2,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash="a" * 64,
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash="a" * 64,
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
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        state = PhotoProcessingState.objects.get(
            photo=photo, processor_type=GENERATE_PREVIEW_PROCESSOR
        )
        state.status = PhotoProcessingState.Status.SUCCEEDED
        state.current_run = run
        state.current_job = job
        state.current_attempt = attempt
        state.accepted_attempt = attempt
        state.succeeded_at = timezone.now()
        state.save()
        return PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-small-v1",
            final_key=final_key,
            byte_size=10,
            content_type="image/jpeg",
            width=10,
            height=10,
            oriented_source_width=10,
            oriented_source_height=10,
            sha256="a" * 64,
            accepted_attempt=attempt,
        )

    def media_url(self, *, event: Event, photo: Photo, variant: str = "preview-small") -> str:
        return reverse(
            "photo_media",
            kwargs={"slug": event.slug, "photo_id": photo.id, "variant": variant},
        )

    def download_url(self, *, event: Event, photo: Photo) -> str:
        return reverse(
            "photo_download",
            kwargs={"slug": event.slug, "photo_id": photo.id},
        )

    def test_photo_download_redirects_to_a_signed_original_without_a_body(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(event, id="photo-download")
        resolver = Mock()
        resolver.resolve_download.return_value = (
            "https://storage.example.test/original?signature=secret"
        )

        with patch("config.views._public_media_resolver", return_value=resolver):
            response = self.client.get(self.download_url(event=event, photo=photo))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], resolver.resolve_download.return_value)
        self.assertEqual(response.content, b"")
        self.assertFalse(response.streaming)
        resolver.resolve_download.assert_called_once_with(photo=photo)

    def test_photo_download_maps_storage_failures_to_existing_responses(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(event, id="download-storage")
        resolver = Mock()

        with patch("config.views._public_media_resolver", return_value=resolver):
            resolver.resolve_download.side_effect = ObjectMissing()
            missing_response = self.client.get(self.download_url(event=event, photo=photo))
            resolver.resolve_download.side_effect = StorageError()
            unavailable_response = self.client.get(self.download_url(event=event, photo=photo))

        self.assertEqual(missing_response.status_code, 404)
        self.assertEqual(missing_response.content, b"")
        self.assertEqual(unavailable_response.status_code, 503)
        self.assertEqual(unavailable_response.content, b"")

    def test_photo_download_returns_404_for_paid_event_before_signing(self) -> None:
        event = self.make_event(name="Paid", slug="paid", access_type=Event.AccessType.PAID)
        photo = self.make_private_photo(event, id="paid-download")

        with patch("config.views._public_media_resolver") as resolver_factory:
            response = self.client.get(self.download_url(event=event, photo=photo))

        self.assertEqual(response.status_code, 404)
        resolver_factory.assert_not_called()

    def test_photo_media_redirects_to_signed_preview_without_streaming_a_body(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(event, id="photo-42")
        resolver = Mock()
        resolver.resolve_signed.return_value = (
            "https://storage.example.test/preview?signature=secret"
        )

        with patch("config.views._public_media_resolver", return_value=resolver):
            response = views.photo_media(
                RequestFactory().get(self.media_url(event=event, photo=photo)),
                event.slug,
                photo.id,
                "preview-small",
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], resolver.resolve_signed.return_value)
        self.assertEqual(response.content, b"")
        self.assertFalse(response.streaming)
        resolver.resolve_signed.assert_called_once_with(photo=photo, variant="preview-small")

    def test_photo_media_returns_404_for_unknown_variant_before_storage(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(event)

        with patch("config.views._public_media_resolver") as resolver_factory:
            response = self.client.get(self.media_url(event=event, photo=photo, variant="original"))

        self.assertEqual(response.status_code, 404)
        resolver_factory.assert_not_called()

    def test_photo_media_returns_404_for_unpublished_or_paid_event(self) -> None:
        for event in (
            self.make_event(
                name="Draft", slug="draft", publication_status=Event.PublicationStatus.DRAFT
            ),
            self.make_event(name="Paid", slug="paid", access_type=Event.AccessType.PAID),
        ):
            with self.subTest(event=event.slug):
                photo = self.make_private_photo(event)
                with patch("config.views._public_media_resolver") as resolver_factory:
                    response = self.client.get(self.media_url(event=event, photo=photo))

                self.assertEqual(response.status_code, 404)
                resolver_factory.assert_not_called()

    def test_photo_media_hides_new_photo_until_its_preview_is_accepted(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(
            event,
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )

        for variant in ("preview-small", "preview-large"):
            with (
                self.subTest(variant=variant),
                patch("config.views._public_media_resolver") as resolver_factory,
            ):
                response = self.client.get(
                    self.media_url(event=event, photo=photo, variant=variant)
                )

            self.assertEqual(response.status_code, 404)
            resolver_factory.assert_not_called()

    def test_photo_media_redirects_an_accepted_new_preview_through_existing_routes(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(
            event,
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )
        self.publish_preview(
            photo,
            final_key=f"derivatives/previews/{photo.id}/preview-small-v1/accepted.jpg",
        )
        resolver = Mock()
        resolver.resolve_signed.return_value = (
            "https://storage.example.test/preview?signature=secret"
        )

        with patch("config.views._public_media_resolver", return_value=resolver):
            response = self.client.get(self.media_url(event=event, photo=photo))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], resolver.resolve_signed.return_value)
        resolver.resolve_signed.assert_called_once_with(photo=photo, variant="preview-small")

    def test_photo_media_returns_404_for_legacy_or_other_event_photo(self) -> None:
        event = self.make_event()
        other_event = self.make_event(name="Other", slug="other")
        other_photo = self.make_private_photo(other_event, id="other")
        legacy = Photo.objects.create(id="legacy", event=event, src="photos/legacy.jpg")

        for photo in (other_photo, legacy):
            with self.subTest(photo=photo.id):
                with patch("config.views._public_media_resolver") as resolver_factory:
                    response = self.client.get(self.media_url(event=event, photo=photo))

                self.assertEqual(response.status_code, 404)
                resolver_factory.assert_not_called()

    def test_photo_media_returns_404_when_storage_object_is_missing(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(event)
        resolver = Mock()
        resolver.resolve_signed.side_effect = ObjectMissing()

        with patch("config.views._public_media_resolver", return_value=resolver):
            response = self.client.get(self.media_url(event=event, photo=photo))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b"")

    def test_photo_media_returns_503_for_storage_error(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(event)
        resolver = Mock()
        resolver.resolve_signed.side_effect = StorageError()

        with patch("config.views._public_media_resolver", return_value=resolver):
            response = self.client.get(self.media_url(event=event, photo=photo))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"")

    def test_photo_media_returns_sanitized_503_for_storage_constructor_value_error(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(event)
        private_detail = "invalid endpoint: https://private.example.test"

        with (
            patch(
                "config.views.PrivateUploadStorage", side_effect=ValueError(private_detail)
            ) as storage_class,
            patch("config.views.PublicMediaResolver") as resolver_class,
        ):
            response = self.client.get(self.media_url(event=event, photo=photo))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"")
        self.assertNotIn(private_detail, str(response.headers))
        storage_class.assert_called_once_with()
        resolver_class.assert_not_called()

    def test_photo_media_returns_sanitized_503_for_invalid_final_key(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(event, original_key="private/final-key-detail")
        s3_client = Mock()
        storage = PrivateUploadStorage(client=s3_client)

        with patch("config.views.PrivateUploadStorage", return_value=storage) as storage_class:
            response = self.client.get(self.media_url(event=event, photo=photo))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"")
        self.assertNotIn("private/final-key-detail", str(response.headers))
        storage_class.assert_called_once_with()
        s3_client.get_object.assert_not_called()

    def test_photo_media_does_not_hide_unrelated_resolver_value_error(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(event)
        resolver = Mock()
        resolver.resolve_signed.side_effect = ValueError("programmer bug")

        with (
            patch("config.views._public_media_resolver", return_value=resolver),
            self.assertRaisesRegex(ValueError, "programmer bug"),
        ):
            views.photo_media(
                RequestFactory().get(self.media_url(event=event, photo=photo)),
                event.slug,
                photo.id,
                "preview-small",
            )

        resolver.resolve_signed.assert_called_once_with(photo=photo, variant="preview-small")

    def test_photo_media_rejects_non_get_methods_before_storage(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(event)
        url = self.media_url(event=event, photo=photo)

        for method in ("head", "post", "put", "patch", "delete", "options"):
            with (
                self.subTest(method=method),
                patch("config.views._public_media_resolver") as resolver_factory,
                patch("config.views.PrivateUploadStorage") as storage_class,
            ):
                response = getattr(self.client, method)(url)

                self.assertEqual(response.status_code, 405)
                self.assertEqual(response["Allow"], "GET")
                resolver_factory.assert_not_called()
                storage_class.assert_not_called()
