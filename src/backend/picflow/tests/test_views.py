import json
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
from uuid import uuid4

from commerce.services import set_photo_selected
from config import views
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.paginator import Paginator
from django.template.base import Variable
from django.test import (
    Client,
    RequestFactory,
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    modify_settings,
    override_settings,
)
from django.urls import reverse
from django.utils import timezone
from django.views.debug import technical_500_response
from feature_flags.models import FeatureFlag
from feature_flags.states import FEATURE_FLAG_OFF, FEATURE_FLAG_ON, FEATURE_FLAG_STAFF
from feature_flags.testing import override_feature_flags
from ingestion.storage import ObjectMissing, PrivateUploadStorage, StorageError
from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    FACE_EMBEDDING_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
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
from processing.services.face_quality import publish_face_embedding_projection
from selfie_search.models import SelfieSearch

from picflow.models import Event, EventFolder, Photo
from picflow.photo_policy import PAID_WATERMARKED_PREVIEWS_FLAG

PAID_PHOTO_CART_FLAG = "paid-photo-cart"


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
            "face_search_generation": Event.FaceSearchGeneration.SFACE_V3,
        }
        values.update(overrides)
        if values.get("access_type", Event.AccessType.FREE) == Event.AccessType.PAID:
            values.setdefault("price_per_photo_kopecks", 30000)
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

    def test_catalog_and_detail_apply_the_shared_publication_matrix(self) -> None:
        published = self.make_event()
        draft = self.make_event(
            name="Draft",
            slug="draft",
            publication_status=Event.PublicationStatus.DRAFT,
            timezone_name="Europe/Moscow",
        )
        unavailable = self.make_event(
            name="Unavailable",
            slug="unavailable",
            publication_status=Event.PublicationStatus.UNAVAILABLE,
        )
        ordinary_user = get_user_model().objects.create_user(username="ordinary-preview")
        staff_user = get_user_model().objects.create_user(username="staff-preview", is_staff=True)

        for user in (None, ordinary_user):
            with self.subTest(user=getattr(user, "username", "anonymous")):
                if user is not None:
                    self.client.force_login(user)
                catalog = self.client.get(reverse("event_catalog"))
                self.assertContains(catalog, published.name)
                self.assertNotContains(catalog, draft.name)
                self.assertNotContains(catalog, unavailable.name)
                self.assertEqual(
                    self.client.get(
                        reverse("event_detail", kwargs={"slug": draft.slug})
                    ).status_code,
                    404,
                )
                self.client.logout()

        self.client.force_login(staff_user)
        staff_catalog = self.client.get(reverse("event_catalog"))
        self.assertContains(staff_catalog, published.name)
        self.assertContains(staff_catalog, draft.name)
        self.assertNotContains(staff_catalog, unavailable.name)
        self.assertEqual(
            self.client.get(reverse("event_detail", kwargs={"slug": draft.slug})).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("event_detail", kwargs={"slug": unavailable.slug})).status_code,
            404,
        )

    def test_catalog_and_direct_detail_hide_paid_events_behind_the_parent_gate(self) -> None:
        paid = self.make_event(
            name="Paid preview",
            slug="paid-preview",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        states = {"paid-events": FEATURE_FLAG_STAFF}
        detail_url = reverse("event_detail", kwargs={"slug": paid.slug})

        with override_feature_flags(states):
            anonymous_catalog = self.client.get(reverse("event_catalog"))
            self.assertNotContains(anonymous_catalog, paid.name)
            self.assertEqual(self.client.get(detail_url).status_code, 404)

            staff = get_user_model().objects.create_user(
                username="paid-preview-staff", is_staff=True
            )
            self.client.force_login(staff)
            self.assertContains(self.client.get(reverse("event_catalog")), paid.name)
            self.assertEqual(self.client.get(detail_url).status_code, 200)

            states["paid-events"] = FEATURE_FLAG_OFF
            self.assertNotContains(self.client.get(reverse("event_catalog")), paid.name)
            self.assertEqual(self.client.get(detail_url).status_code, 404)

            self.client.logout()
            states["paid-events"] = FEATURE_FLAG_ON
            self.assertContains(self.client.get(reverse("event_catalog")), paid.name)
            self.assertEqual(self.client.get(detail_url).status_code, 200)

        self.assertFalse(FeatureFlag.objects.filter(key="paid-events").exists())

    def test_staff_draft_preview_warns_and_emits_no_public_analytics(self) -> None:
        published = self.make_event()
        draft = self.make_event(
            name="Draft",
            slug="draft",
            publication_status=Event.PublicationStatus.DRAFT,
            timezone_name="Europe/Moscow",
        )
        staff_user = get_user_model().objects.create_user(username="warning-staff", is_staff=True)
        self.client.force_login(staff_user)
        warning = "Черновик — виден только администраторам"

        catalog = self.client.get(reverse("event_catalog"))
        draft_detail = self.client.get(reverse("event_detail", kwargs={"slug": draft.slug}))
        published_detail = self.client.get(reverse("event_detail", kwargs={"slug": published.slug}))

        self.assertContains(catalog, warning)
        self.assertContains(draft_detail, warning)
        self.assertNotContains(published_detail, warning)
        for response in (catalog, draft_detail):
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertIsNone(response.context["yandex_metrika_counter_id"])
                self.assertNotContains(response, "mc.yandex.ru/metrika/tag.js")
                self.assertNotContains(response, "mc.yandex.ru/watch/")

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

    def test_event_detail_renders_compact_published_event_header(self) -> None:
        event = self.make_event(description="Race description")
        response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))
        self.assertContains(response, event.name)
        self.assertContains(response, event.city)
        self.assertNotContains(response, "Race description")

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

    def test_event_detail_returns_404_for_draft_event_anonymously(self) -> None:
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
            "face_search_generation": Event.FaceSearchGeneration.SFACE_V3,
        }
        values.update(overrides)
        if values.get("access_type", Event.AccessType.FREE) == Event.AccessType.PAID:
            values.setdefault("price_per_photo_kopecks", 30000)
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

    def capture_evidence(self, photo: Photo, *, capture_time: str) -> ProcessingAttempt:
        configuration = {"capture_metadata": {"event_timezone": photo.event.timezone_name}}
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=2,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=2,
            configuration=configuration,
            configuration_hash=uuid4().hex + uuid4().hex,
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
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
        return ProcessingAttempt.objects.create(
            event=photo.event,
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

    def publish_preview(
        self,
        photo: Photo,
        *,
        final_key: str,
        processor_type: str = GENERATE_PREVIEW_PROCESSOR,
        variant: str = "preview-small-v1",
    ) -> PhotoDerivative:
        configuration = {processor_type: {"variant": variant}}
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=2,
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            configuration_hash="a" * 64,
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=processor_type,
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
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        state, _ = PhotoProcessingState.objects.get_or_create(
            photo=photo, processor_type=processor_type
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
            variant=variant,
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
        publish_face_embedding_projection(attempt)

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
        self.assertContains(
            first_response,
            '<form class="gallery-pagination-form" action="#gallery" method="get">',
        )
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
        self.assertContains(
            second_response,
            '<form class="gallery-pagination-form" action="#gallery" method="get">',
        )
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

        with override_feature_flags({"paid-events": FEATURE_FLAG_ON}):
            response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))
            paid_response = self.client.get(
                reverse("event_detail", kwargs={"slug": paid_event.slug})
            )

        self.assertEqual(
            tuple(item.photo_id for item in response.context["gallery_photos"]), (included.id,)
        )
        self.assertEqual(paid_response.context["gallery_photos"], ())

    def test_enabled_paid_gallery_renders_only_watermarked_semantic_media_without_downloads(
        self,
    ) -> None:
        event = self.make_event(
            name="Paid gallery",
            slug="paid-watermarked-gallery",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        ready = self.make_private_photo(
            event,
            id="paid-watermarked-ready",
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        derivative = self.publish_preview(
            ready,
            final_key=(
                "derivatives/previews/paid-watermarked-ready/preview-watermarked-v1/accepted.jpg"
            ),
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            variant="preview-watermarked-v1",
        )
        self.make_private_photo(
            event,
            id="paid-watermarked-pending",
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        legacy = self.make_private_photo(event, id="paid-legacy-hidden")

        with override_feature_flags(
            {
                "paid-events": FEATURE_FLAG_ON,
                PAID_WATERMARKED_PREVIEWS_FLAG: FEATURE_FLAG_ON,
            }
        ):
            response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            tuple(item.photo_id for item in response.context["gallery_photos"]),
            (ready.pk,),
        )
        presentation = response.context["gallery_photos"][0]
        self.assertEqual(presentation.photo_id, ready.pk)
        self.assertEqual(presentation.preview_media_small.variant, "preview-small")
        self.assertEqual(presentation.preview_media_large.variant, "preview-large")
        self.assertIsNone(presentation.download_url)
        self.assertContains(response, f'<figure class="gallery-card" data-photo-id="{ready.pk}">')
        self.assertContains(response, '<div class="gallery-card-download">')
        self.assertNotContains(response, 'class="gallery-download"')
        self.assertNotContains(response, 'class="gallery-lightbox-download"')
        markup = response.content.decode(response.charset)
        for secret in (ready.original_key, legacy.original_key, derivative.final_key):
            self.assertNotIn(secret, markup)

        self.assertIsNone(response.context["cart_presentation"])

    def test_enabled_paid_gallery_gets_event_scoped_cart_presentation_and_private_cache(
        self,
    ) -> None:
        event = self.make_event(
            name="Paid cart gallery",
            slug="paid-cart-gallery",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=45075,
        )
        selected = self.make_private_photo(
            event,
            id="paid-cart-selected",
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        visible = self.make_private_photo(
            event,
            id="paid-cart-visible",
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        for photo in (selected, visible):
            self.publish_preview(
                photo,
                final_key=f"derivatives/previews/{photo.pk}/watermarked.jpg",
                processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
                variant="preview-watermarked-v1",
            )
        token = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
        set_photo_selected(
            event=event,
            photo_id=selected.pk,
            selected=True,
            browser_token=token,
            watermarked_previews_enabled=True,
        )
        other_event = self.make_event(
            name="Other paid cart gallery",
            slug="other-paid-cart-gallery",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        other_photo = self.make_private_photo(
            other_event,
            id="other-paid-cart-photo",
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        self.publish_preview(
            other_photo,
            final_key="derivatives/previews/other-paid-cart-photo/watermarked.jpg",
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            variant="preview-watermarked-v1",
        )
        set_photo_selected(
            event=other_event,
            photo_id=other_photo.pk,
            selected=True,
            browser_token=token,
            watermarked_previews_enabled=True,
        )
        self.client.cookies["findme_cart"] = token

        with override_feature_flags(
            {
                "paid-events": FEATURE_FLAG_ON,
                PAID_WATERMARKED_PREVIEWS_FLAG: FEATURE_FLAG_ON,
                PAID_PHOTO_CART_FLAG: FEATURE_FLAG_ON,
            }
        ):
            response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        presentation = response.context["cart_presentation"]
        self.assertEqual(
            tuple(item.photo.photo_id for item in presentation.photos),
            (selected.pk, visible.pk),
        )
        self.assertEqual(tuple(item.selected for item in presentation.photos), (True, False))
        self.assertEqual(presentation.item_count, 1)
        self.assertEqual(presentation.unit_price_display, "450,75 ₽")
        self.assertEqual(presentation.total_display, "450,75 ₽")
        self.assertContains(response, "450,75 ₽", count=4)
        self.assertContains(response, "data-cart-count>1</strong>")
        self.assertContains(response, 'data-cart-form data-photo-id="paid-cart-selected"')
        self.assertContains(response, 'data-cart-form data-photo-id="paid-cart-visible"')
        self.assertContains(response, 'data-cart-price data-photo-id="paid-cart-selected"', count=2)
        self.assertContains(response, 'data-cart-price data-photo-id="paid-cart-visible"', count=2)
        self.assertContains(response, 'aria-label="Удалить из корзины"')
        self.assertContains(response, 'aria-label="Добавить в корзину"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"', count=4)
        self.assertContains(
            response, 'name="return_to" value="/events/paid-cart-gallery/"', count=4
        )
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Vary"], "Cookie")

    @override_settings(DEBUG=False)
    def test_paid_gallery_template_exception_report_redacts_cart_context_only(self) -> None:
        event = self.make_event(
            name="Paid report gallery",
            slug="paid-report-gallery",
            access_type=Event.AccessType.PAID,
        )
        selected = self.make_private_photo(
            event,
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        self.publish_preview(
            selected,
            final_key=f"derivatives/previews/{selected.pk}/watermarked.jpg",
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            variant="preview-watermarked-v1",
        )
        token = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
        set_photo_selected(
            event=event,
            photo_id=selected.pk,
            selected=True,
            browser_token=token,
            watermarked_previews_enabled=True,
        )
        exception_client = Client(raise_request_exception=False)
        exception_client.cookies["findme_cart"] = token
        exception_client.cookies["ordinary_cookie"] = "visible-gallery-cookie"
        original_resolve_lookup = Variable._resolve_lookup

        def force_template_exception(variable, context):
            if variable.var == "gallery_photos":
                raise RuntimeError("forced paid gallery template")
            return original_resolve_lookup(variable, context)

        with (
            override_feature_flags(
                {
                    "paid-events": FEATURE_FLAG_ON,
                    PAID_WATERMARKED_PREVIEWS_FLAG: FEATURE_FLAG_ON,
                    PAID_PHOTO_CART_FLAG: FEATURE_FLAG_ON,
                }
            ),
            patch.object(Variable, "_resolve_lookup", new=force_template_exception),
        ):
            response = exception_client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        self.assertEqual(response.status_code, 500)
        self.assertIsNotNone(response.exc_info)
        technical_response = technical_500_response(
            response.wsgi_request,
            *response.exc_info,
        )
        report = technical_response.content.decode(technical_response.charset)
        self.assertNotIn(token, report)
        self.assertNotIn(selected.pk, report)
        self.assertIn("ordinary_cookie", report)
        self.assertIn("visible-gallery-cookie", report)
        self.assertIn("callback_kwargs", report)
        self.assertIn("slug", report)
        self.assertIn("paid-report-gallery", report)
        self.assertIn("forced paid gallery template", report)

    def test_free_gallery_remains_free_of_cart_context_and_route_markup_when_flags_are_on(
        self,
    ) -> None:
        event = self.make_event(name="Free gallery", slug="free-cart-regression")
        self.make_private_photo(event, id="free-cart-regression-photo")

        with override_feature_flags(
            {
                PAID_WATERMARKED_PREVIEWS_FLAG: FEATURE_FLAG_ON,
                PAID_PHOTO_CART_FLAG: FEATURE_FLAG_ON,
            }
        ):
            response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        self.assertIsNone(response.context["cart_presentation"])
        self.assertNotContains(response, f"/events/{event.slug}/cart/")
        self.assertNotEqual(response.headers.get("Cache-Control"), "private, no-store")

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
        event = self.make_event(timezone_name="Europe/London")
        photos = [self.make_private_photo(event, id=f"gallery-{index}") for index in range(1, 6)]
        capture_attempt = self.capture_evidence(photos[0], capture_time="2026-06-10T10:03:00Z")
        Photo.objects.filter(pk=photos[0].pk).update(
            capture_time=datetime(2026, 6, 10, 10, 3, tzinfo=UTC),
            capture_time_source_attempt=capture_attempt,
        )

        response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        self.assertContains(response, "Фотографии")
        self.assertContains(response, 'class="event-gallery"')
        self.assertContains(response, 'class="event-gallery-count">5 фото</span>')
        self.assertContains(response, '<div class="gallery-card-download">')
        self.assertContains(response, '<time class="gallery-card-time">11:03</time>')
        self.assertContains(response, 'class="gallery-card-time"', count=1)
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
        for photo in photos:
            self.assertContains(response, f'data-photo-id="{photo.pk}"')

    def test_event_detail_uses_the_compact_metadata_header_without_hero_content(self) -> None:
        event = self.make_event(description="Подробное описание события")

        response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        self.assertContains(response, 'class="event-detail-header"')
        self.assertContains(response, "City Run")
        self.assertContains(response, "Moscow")
        self.assertNotContains(response, 'class="event-detail-grid"')
        self.assertNotContains(response, "Подробное описание события")

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

    def capture_evidence(self, photo: Photo, *, capture_time: str) -> ProcessingAttempt:
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
        return attempt

    def test_no_manual_parameters_keeps_the_existing_unfiltered_gallery(self) -> None:
        photo = self.photo("unfiltered", filename="unfiltered.jpg")

        response = self.client.get(reverse("event_detail", kwargs={"slug": self.event.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item.photo_id for item in response.context["gallery_photos"]], [photo.pk])
        self.assertFalse(response.context["manual_time_filter_form"].is_requested)
        self.assertFalse(response.context["manual_time_filter_invalid"])

    def test_unfiltered_event_detail_suppresses_metrika(self) -> None:
        response = self.client.get(reverse("event_detail", kwargs={"slug": self.event.slug}))

        self.assertIsNone(response.context["yandex_metrika_counter_id"])
        self.assertNotContains(response, "mc.yandex.ru")
        self.assertNotContains(response, 'ym(111239706, "init", {')

    def test_manual_time_event_detail_suppresses_metrika(self) -> None:
        response = self.client.get(
            reverse("event_detail", kwargs={"slug": self.event.slug}),
            {"from": "2026-06-10T10:00", "to": "2026-06-10T10:01"},
        )

        self.assertIsNone(response.context["yandex_metrika_counter_id"])
        self.assertNotContains(response, "mc.yandex.ru")
        self.assertNotContains(response, 'ym(111239706, "init", {')

    def test_valid_manual_filter_uses_only_matching_capture_time_projection_before_paging(
        self,
    ) -> None:
        matching = self.photo("matching", filename="a.jpg")
        outside = self.photo("outside", filename="b.jpg")
        matching_attempt = self.capture_evidence(matching, capture_time="2026-06-10T10:00:00Z")
        outside_attempt = self.capture_evidence(outside, capture_time="2026-06-10T09:00:00Z")
        Photo.objects.filter(pk=matching.pk).update(
            capture_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            capture_time_source_attempt=matching_attempt,
        )
        Photo.objects.filter(pk=outside.pk).update(
            capture_time=datetime(2026, 6, 10, 10, 0, tzinfo=UTC),
            capture_time_source_attempt=outside_attempt,
        )

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

    def test_start_only_filter_keeps_later_known_times_and_excludes_missing_times(self) -> None:
        included = self.photo("start-included", filename="a.jpg")
        excluded = self.photo("start-excluded", filename="b.jpg")
        self.photo("start-missing", filename="c.jpg")
        included_attempt = self.capture_evidence(included, capture_time="2026-06-10T09:00:00Z")
        excluded_attempt = self.capture_evidence(excluded, capture_time="2026-06-10T08:59:00Z")
        Photo.objects.filter(pk=included.pk).update(
            capture_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            capture_time_source_attempt=included_attempt,
        )
        Photo.objects.filter(pk=excluded.pk).update(
            capture_time=datetime(2026, 6, 10, 8, 59, tzinfo=UTC),
            capture_time_source_attempt=excluded_attempt,
        )

        response = self.client.get(
            reverse("event_detail", kwargs={"slug": self.event.slug}),
            {"from": "2026-06-10T10:00"},
        )

        self.assertEqual(
            [item.photo_id for item in response.context["gallery_photos"]], [included.pk]
        )
        self.assertEqual(
            response.context["gallery_pagination_query_pairs"], (("from", "2026-06-10T10:00"),)
        )

    def test_end_only_filter_keeps_earlier_known_times_and_excludes_missing_times(self) -> None:
        included = self.photo("end-included", filename="a.jpg")
        excluded = self.photo("end-excluded", filename="b.jpg")
        self.photo("end-missing", filename="c.jpg")
        included_attempt = self.capture_evidence(included, capture_time="2026-06-10T09:00:00Z")
        excluded_attempt = self.capture_evidence(excluded, capture_time="2026-06-10T09:01:00Z")
        Photo.objects.filter(pk=included.pk).update(
            capture_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            capture_time_source_attempt=included_attempt,
        )
        Photo.objects.filter(pk=excluded.pk).update(
            capture_time=datetime(2026, 6, 10, 9, 1, tzinfo=UTC),
            capture_time_source_attempt=excluded_attempt,
        )

        response = self.client.get(
            reverse("event_detail", kwargs={"slug": self.event.slug}),
            {"to": "2026-06-10T10:00"},
        )

        self.assertEqual(
            [item.photo_id for item in response.context["gallery_photos"]], [included.pk]
        )
        self.assertEqual(
            response.context["gallery_pagination_query_pairs"], (("to", "2026-06-10T10:00"),)
        )

    def test_folder_and_start_only_filter_combine_with_and(self) -> None:
        folder = EventFolder.objects.create(event=self.event, name="Старт")
        included = self.photo("folder-start-included", filename="a.jpg")
        too_early = self.photo("folder-start-too-early", filename="b.jpg")
        other_folder = self.photo("other-folder-start", filename="c.jpg")
        Photo.objects.filter(pk__in=(included.pk, too_early.pk)).update(folder=folder)
        included_attempt = self.capture_evidence(included, capture_time="2026-06-10T09:00:00Z")
        too_early_attempt = self.capture_evidence(too_early, capture_time="2026-06-10T08:59:00Z")
        other_attempt = self.capture_evidence(other_folder, capture_time="2026-06-10T09:00:00Z")
        Photo.objects.filter(pk=included.pk).update(
            capture_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            capture_time_source_attempt=included_attempt,
        )
        Photo.objects.filter(pk=too_early.pk).update(
            capture_time=datetime(2026, 6, 10, 8, 59, tzinfo=UTC),
            capture_time_source_attempt=too_early_attempt,
        )
        Photo.objects.filter(pk=other_folder.pk).update(
            capture_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            capture_time_source_attempt=other_attempt,
        )

        response = self.client.get(
            reverse("event_detail", kwargs={"slug": self.event.slug}),
            {"folder": str(folder.pk), "from": "2026-06-10T10:00"},
        )

        self.assertEqual(
            [item.photo_id for item in response.context["gallery_photos"]], [included.pk]
        )
        self.assertEqual(
            response.context["gallery_pagination_query_pairs"],
            (("folder", str(folder.pk)), ("from", "2026-06-10T10:00")),
        )

    def test_folder_and_end_only_filter_combine_with_and(self) -> None:
        folder = EventFolder.objects.create(event=self.event, name="Финиш")
        included = self.photo("folder-end-included", filename="a.jpg")
        too_late = self.photo("folder-end-too-late", filename="b.jpg")
        other_folder = self.photo("other-folder-end", filename="c.jpg")
        Photo.objects.filter(pk__in=(included.pk, too_late.pk)).update(folder=folder)
        included_attempt = self.capture_evidence(included, capture_time="2026-06-10T09:00:00Z")
        too_late_attempt = self.capture_evidence(too_late, capture_time="2026-06-10T09:01:00Z")
        other_attempt = self.capture_evidence(other_folder, capture_time="2026-06-10T09:00:00Z")
        Photo.objects.filter(pk=included.pk).update(
            capture_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            capture_time_source_attempt=included_attempt,
        )
        Photo.objects.filter(pk=too_late.pk).update(
            capture_time=datetime(2026, 6, 10, 9, 1, tzinfo=UTC),
            capture_time_source_attempt=too_late_attempt,
        )
        Photo.objects.filter(pk=other_folder.pk).update(
            capture_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            capture_time_source_attempt=other_attempt,
        )

        response = self.client.get(
            reverse("event_detail", kwargs={"slug": self.event.slug}),
            {"folder": str(folder.pk), "to": "2026-06-10T10:00"},
        )

        self.assertEqual(
            [item.photo_id for item in response.context["gallery_photos"]], [included.pk]
        )
        self.assertEqual(
            response.context["gallery_pagination_query_pairs"],
            (("folder", str(folder.pk)), ("to", "2026-06-10T10:00")),
        )

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

    def test_folder_filter_works_when_browser_submits_blank_time_fields(self) -> None:
        folder = EventFolder.objects.create(event=self.event, name="Старт")
        included = self.photo("folder-only", filename="a.jpg")
        self.photo("folder-only-unfiled", filename="b.jpg")
        Photo.objects.filter(pk=included.pk).update(folder=folder)

        response = self.client.get(
            reverse("event_detail", kwargs={"slug": self.event.slug}),
            {"folder": str(folder.pk), "from": "", "to": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item.photo_id for item in response.context["gallery_photos"]],
            [included.pk],
        )
        self.assertFalse(response.context["manual_time_filter_form"].is_requested)
        self.assertFalse(response.context["manual_time_filter_invalid"])
        self.assertEqual(
            response.context["gallery_pagination_query_pairs"],
            (("folder", str(folder.pk)),),
        )

    def test_manual_time_discovery_renders_event_local_controls_and_invalid_errors(self) -> None:
        """Invalid input must retain correction controls, not broad gallery results."""
        response = self.client.get(
            reverse("event_detail", kwargs={"slug": self.event.slug}),
            {"from": "not-a-time"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="gallery"')
        self.assertContains(response, "Найти свои фото")
        self.assertContains(response, "Поиск по селфи")
        self.assertContains(response, "Ручной поиск")
        self.assertContains(response, "Даты события: 10.06.2026–12.06.2026")
        self.assertContains(response, 'name="from"')
        event_url = reverse("event_detail", kwargs={"slug": self.event.slug})
        self.assertContains(
            response,
            f'<form class="manual-time-filter-form" data-manual-time-filter-form '
            f'action="{event_url}" method="get">',
        )
        self.assertContains(response, '<details class="event-discovery" data-event-discovery open>')
        self.assertContains(response, 'min="2026-06-10T00:00"')
        self.assertContains(response, 'max="2026-06-12T23:59"')
        self.assertNotContains(response, 'name="page"')
        self.assertContains(response, "Введите дату и время события.")
        self.assertNotContains(response, 'class="manual-time-filter-reset"')
        self.assertNotContains(response, 'class="event-gallery"')

    def test_folder_controls_are_stable_and_filter_named_and_unfiled_photos(self) -> None:
        start = EventFolder.objects.create(event=self.event, name="Старт")
        finish = EventFolder.objects.create(event=self.event, name="Финиш")
        EventFolder.objects.create(event=self.event, name="Пустая")
        start_photo = self.photo("folder-start", filename="a.jpg")
        finish_photo = self.photo("folder-finish", filename="b.jpg")
        unfiled_photo = self.photo("folder-unfiled", filename="c.jpg")
        hidden = Photo.objects.create(id="folder-hidden", event=self.event, src="hidden.jpg")
        other_event = Event.objects.create(
            name="Foreign folders",
            slug="foreign-folders",
            start_date=self.event.start_date,
            end_date=self.event.end_date,
            city="London",
            timezone_name="Europe/London",
        )
        foreign = EventFolder.objects.create(event=other_event, name="Чужая")
        Photo.objects.filter(pk=start_photo.pk).update(folder=start)
        Photo.objects.filter(pk=finish_photo.pk).update(folder=finish)

        response = self.client.get(reverse("event_detail", kwargs={"slug": self.event.slug}))
        filtered = self.client.get(
            reverse("event_detail", kwargs={"slug": self.event.slug}),
            [
                ("folder", str(start.pk)),
                ("folder", str(foreign.pk)),
                ("folder", "bad"),
                ("unfiled", "1"),
            ],
        )

        self.assertEqual(
            [folder.pk for folder in response.context["gallery_folder_choices"]],
            [start.pk, finish.pk],
        )
        self.assertTrue(response.context["gallery_folder_filter_form"].show_unfiled)
        self.assertContains(response, '<fieldset class="gallery-folder-filter">')
        self.assertContains(response, f'name="folder" value="{start.pk}"')
        self.assertContains(response, f'name="folder" value="{finish.pk}"')
        self.assertContains(response, 'name="unfiled" value="1"')
        self.assertNotContains(response, "Пустая")
        self.assertEqual(
            [item.photo_id for item in filtered.context["gallery_photos"]],
            [start_photo.pk, unfiled_photo.pk],
        )
        self.assertContains(filtered, f'name="folder" value="{start.pk}" checked')
        self.assertContains(filtered, 'name="unfiled" value="1" checked')
        self.assertContains(filtered, '<details class="event-discovery" data-event-discovery>')
        self.assertContains(filtered, "<summary>Фильтры применены</summary>")
        self.assertContains(filtered, 'class="manual-time-filter-reset"')
        self.assertNotContains(filtered, "Чужая")
        self.assertTrue(Photo.objects.filter(pk=hidden.pk).exists())

    def test_folder_control_stays_hidden_when_only_unfiled_photos_are_eligible(self) -> None:
        self.photo("unfiled-only", filename="a.jpg")

        response = self.client.get(reverse("event_detail", kwargs={"slug": self.event.slug}))

        self.assertEqual(response.context["gallery_folder_choices"], ())
        self.assertFalse(response.context["gallery_folder_filter_form"].show_unfiled)
        self.assertNotContains(response, 'class="gallery-folder-filter"')

    def test_folder_filter_preserves_choices_on_zero_time_intersection_and_pagination(
        self,
    ) -> None:
        folder = EventFolder.objects.create(event=self.event, name="Старт")
        photo = self.photo("folder-pagination", filename="a.jpg")
        self.photo("folder-pagination-unfiled", filename="b.jpg")
        Photo.objects.filter(pk=photo.pk).update(folder=folder)
        url = reverse("event_detail", kwargs={"slug": self.event.slug})

        zero = self.client.get(
            url,
            [("folder", str(folder.pk)), ("from", "2026-06-10T10:00"), ("to", "2026-06-10T10:01")],
        )
        self.assertContains(zero, "Старт")
        self.assertContains(zero, f'name="folder" value="{folder.pk}" checked')
        self.assertContains(zero, "По выбранным фильтрам фотографий не найдено.")

        with patch("config.views.gallery_page") as gallery_page_mock:
            gallery_page_mock.side_effect = lambda **kwargs: Paginator((photo, photo), 1).page(
                int(kwargs["page_number"] or 1)
            )
            paged = self.client.get(
                url,
                [
                    ("folder", str(folder.pk)),
                    ("folder", str(folder.pk)),
                    ("unfiled", "1"),
                    ("from", "2026-06-10T10:00"),
                    ("to", "2026-06-10T10:01"),
                    ("page", "1"),
                ],
            )

        self.assertContains(
            paged,
            f'href="?folder={folder.pk}&amp;unfiled=1&amp;from=2026-06-10T10%3A00&amp;'
            'to=2026-06-10T10%3A01&amp;page=2#gallery"',
        )
        self.assertContains(paged, f'name="folder" value="{folder.pk}"')
        self.assertContains(paged, 'name="unfiled" value="1"')

    @patch("config.views.gallery_page")
    def test_filtered_pagination_preserves_valid_times_while_unfiltered_pagination_stays_clean(
        self, gallery_page_mock
    ) -> None:
        """A page turn must neither drop a valid filter nor invent one."""
        photo = self.photo("pagination", filename="pagination.jpg")
        pages = Paginator((photo, photo), 1)
        gallery_page_mock.side_effect = lambda **kwargs: pages.page(int(kwargs["page_number"] or 1))
        url = reverse("event_detail", kwargs={"slug": self.event.slug})

        filtered = self.client.get(
            url,
            {"from": "2026-06-10T10:00", "to": "2026-06-10T10:01", "page": "1"},
        )
        unfiltered = self.client.get(url, {"page": "1"})

        self.assertContains(
            filtered,
            'href="?from=2026-06-10T10%3A00&amp;to=2026-06-10T10%3A01&amp;page=2#gallery"',
        )
        filtered_pager = filtered.content.decode(filtered.charset).split(
            'class="gallery-pagination-form"', 1
        )[1]
        self.assertIn('name="from" value="2026-06-10T10:00"', filtered_pager)
        self.assertIn('name="to" value="2026-06-10T10:01"', filtered_pager)
        unfiltered_pager = unfiltered.content.decode(unfiltered.charset).split(
            'class="gallery-pagination-form"', 1
        )[1]
        self.assertNotIn('name="from"', unfiltered_pager)
        self.assertNotIn('name="to"', unfiltered_pager)

    @patch("config.views.gallery_page")
    def test_one_sided_pagination_preserves_each_active_time_parameter(
        self, gallery_page_mock
    ) -> None:
        photo = self.photo("one-sided-pagination", filename="pagination.jpg")
        pages = Paginator((photo, photo), 1)
        gallery_page_mock.side_effect = lambda **kwargs: pages.page(int(kwargs["page_number"] or 1))
        url = reverse("event_detail", kwargs={"slug": self.event.slug})

        for field_name in ("from", "to"):
            with self.subTest(field_name=field_name):
                response = self.client.get(url, {field_name: "2026-06-10T10:00", "page": "1"})

                self.assertContains(
                    response,
                    f'href="?{field_name}=2026-06-10T10%3A00&amp;page=2#gallery"',
                )
                pager = response.content.decode(response.charset).split(
                    'class="gallery-pagination-form"', 1
                )[1]
                self.assertIn(f'name="{field_name}" value="2026-06-10T10:00"', pager)
                other_field = "to" if field_name == "from" else "from"
                self.assertNotIn(f'name="{other_field}"', pager)

    def test_valid_zero_match_renders_filtered_empty_state(self) -> None:
        """An empty valid filter must not look like unpublished photos."""
        response = self.client.get(
            reverse("event_detail", kwargs={"slug": self.event.slug}),
            {"from": "2026-06-10T10:00", "to": "2026-06-10T10:01"},
        )

        self.assertContains(response, "По выбранным фильтрам фотографий не найдено.")
        self.assertNotContains(response, "Фотографии пока не опубликованы.")


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
        if values.get("access_type", Event.AccessType.FREE) == Event.AccessType.PAID:
            values.setdefault("price_per_photo_kopecks", 30000)
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

    def publish_preview(
        self,
        photo: Photo,
        *,
        final_key: str,
        processor_type: str = GENERATE_PREVIEW_PROCESSOR,
        variant: str = "preview-small-v1",
    ) -> PhotoDerivative:
        configuration = {processor_type: {"variant": variant}}
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=2,
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            configuration_hash="a" * 64,
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=processor_type,
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
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        state, _ = PhotoProcessingState.objects.get_or_create(
            photo=photo, processor_type=processor_type
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
            variant=variant,
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

    def test_enabled_paid_media_signs_both_semantic_roles_but_download_denies_before_resolver(
        self,
    ) -> None:
        event = self.make_event(
            name="Paid watermark",
            slug="paid-watermark-media",
            access_type=Event.AccessType.PAID,
        )
        photo = self.make_private_photo(
            event,
            id="paid-watermark-media",
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        self.publish_preview(
            photo,
            final_key=(
                "derivatives/previews/paid-watermark-media/preview-watermarked-v1/accepted.jpg"
            ),
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            variant="preview-watermarked-v1",
        )
        resolver = Mock()
        resolver.resolve_signed.return_value = "https://storage.example.test/watermark?signed"

        with (
            override_feature_flags(
                {
                    "paid-events": FEATURE_FLAG_ON,
                    PAID_WATERMARKED_PREVIEWS_FLAG: FEATURE_FLAG_ON,
                }
            ),
            patch("config.views._public_media_resolver", return_value=resolver) as factory,
        ):
            media_responses = tuple(
                self.client.get(self.media_url(event=event, photo=photo, variant=variant))
                for variant in ("preview-small", "preview-large")
            )
            factory.reset_mock()
            resolver.reset_mock()
            download_response = self.client.get(self.download_url(event=event, photo=photo))

        self.assertEqual([response.status_code for response in media_responses], [302, 302])
        self.assertEqual(download_response.status_code, 404)
        factory.assert_not_called()
        resolver.resolve_download.assert_not_called()

    def test_photo_media_redirects_to_signed_preview_without_streaming_a_body(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(event, id="photo-42")
        resolver = Mock()
        resolver.resolve_signed.return_value = (
            "https://storage.example.test/preview?signature=secret"
        )
        request = RequestFactory().get(self.media_url(event=event, photo=photo))
        request.user = AnonymousUser()

        with patch("config.views._public_media_resolver", return_value=resolver):
            response = views.photo_media(
                request,
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

    def test_staff_can_sign_draft_media_but_unavailable_and_nonstaff_requests_cannot(self) -> None:
        draft = self.make_event(
            name="Draft",
            slug="draft-signing",
            publication_status=Event.PublicationStatus.DRAFT,
            timezone_name="Europe/Moscow",
        )
        unavailable = self.make_event(
            name="Unavailable",
            slug="unavailable-signing",
            publication_status=Event.PublicationStatus.UNAVAILABLE,
        )
        draft_photo = self.make_private_photo(draft, id="draft-signing-photo")
        unavailable_photo = self.make_private_photo(unavailable, id="unavailable-signing-photo")
        ordinary_user = get_user_model().objects.create_user(username="ordinary-signing")
        staff_user = get_user_model().objects.create_user(username="staff-signing", is_staff=True)
        draft_urls = (
            self.media_url(event=draft, photo=draft_photo),
            self.download_url(event=draft, photo=draft_photo),
        )

        with patch("config.views._public_media_resolver") as resolver_factory:
            anonymous = tuple(self.client.get(url) for url in draft_urls)
            self.client.force_login(ordinary_user)
            ordinary = tuple(self.client.get(url) for url in draft_urls)
            self.client.logout()
            resolver_factory.assert_not_called()

        resolver = Mock()
        resolver.resolve_signed.return_value = "https://storage.example.test/preview?signed"
        resolver.resolve_download.return_value = "https://storage.example.test/original?signed"
        self.client.force_login(staff_user)
        with patch("config.views._public_media_resolver", return_value=resolver):
            staff = tuple(self.client.get(url) for url in draft_urls)
            unavailable_responses = (
                self.client.get(self.media_url(event=unavailable, photo=unavailable_photo)),
                self.client.get(self.download_url(event=unavailable, photo=unavailable_photo)),
            )

        self.assertEqual([response.status_code for response in (*anonymous, *ordinary)], [404] * 4)
        self.assertEqual([response.status_code for response in staff], [302, 302])
        self.assertEqual(
            [response.status_code for response in unavailable_responses],
            [404, 404],
        )
        resolver.resolve_signed.assert_called_once_with(photo=draft_photo, variant="preview-small")
        resolver.resolve_download.assert_called_once_with(photo=draft_photo)

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
        request = RequestFactory().get(self.media_url(event=event, photo=photo))
        request.user = AnonymousUser()

        with (
            patch("config.views._public_media_resolver", return_value=resolver),
            self.assertRaisesRegex(ValueError, "programmer bug"),
        ):
            views.photo_media(
                request,
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
