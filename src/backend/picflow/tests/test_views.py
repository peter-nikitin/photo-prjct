from datetime import date, timedelta
from html.parser import HTMLParser
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
from uuid import uuid4

from config import views
from django.contrib.auth import get_user_model
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
from ingestion.storage import ObjectMissing, StorageError

from picflow.gallery import CloseableMediaIterator, ResolvedPublicMedia
from picflow.models import Event, Photo


class NavigationMarkupParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchor_hrefs: set[str] = set()
        self.form_actions: set[str] = set()
        self.input_types: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a" and (href := attributes.get("href")):
            self.anchor_hrefs.add(href)
        elif tag == "form" and (action := attributes.get("action")):
            self.form_actions.add(action)
        elif tag == "input" and (input_type := attributes.get("type")):
            self.input_types.add(input_type.lower())


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
@modify_settings(MIDDLEWARE={"remove": "whitenoise.middleware.WhiteNoiseMiddleware"})
class PublicShellTests(SimpleTestCase):
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
        self.assertContains(response, f'href="{reverse("admin:index")}"')
        self.assertNotContains(response, "Прототип")
        for section_id in ("offer", "terms", "personal", "cookies"):
            self.assertContains(response, f'id="{section_id}"')


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
                self.assertContains(response, f'href="{reverse("admin:index")}"')

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

    @patch("config.views.PrivateUploadStorage")
    def test_event_detail_builds_ordered_gallery_without_storage(self, storage_class) -> None:
        event = self.make_event()
        later = self.make_private_photo(event, id="photo-2")
        earlier = self.make_private_photo(event, id="photo-1")

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
            alt = f"Фото {photo.id} с события {event.name}"
            self.assertContains(
                response,
                f'class="gallery-card-link glightbox" href="{large_url}" '
                f'data-gallery="event-photos" aria-label="Открыть: {alt}"',
            )
            self.assertContains(response, f'src="{small_url}"')
            self.assertContains(response, f'alt="{alt}"')
            self.assertContains(
                response,
                f'<figcaption class="gallery-photo-id">Фото {photo.id}</figcaption>',
            )
            if index <= 4:
                self.assertContains(
                    response, f'src="{small_url}" loading="eager" fetchpriority="high"'
                )
            else:
                self.assertContains(response, f'src="{small_url}" loading="lazy"')

    def test_event_detail_empty_gallery_is_accessible(self) -> None:
        event = self.make_event()

        response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))

        self.assertContains(response, "Фотографии")
        self.assertContains(response, 'class="event-gallery"')
        self.assertContains(response, 'aria-labelledby="gallery-title"')
        self.assertContains(response, 'id="gallery-title"')
        self.assertContains(response, 'class="event-gallery-count">0 фото</span>')
        self.assertContains(response, "Фотографии пока не опубликованы")


class _ReadableBody:
    def __init__(self, chunks: list[bytes | Exception]) -> None:
        self._chunks = iter(chunks)
        self.close_calls = 0

    def read(self, amt: int | None = None) -> bytes:  # noqa: ARG002
        chunk = next(self._chunks)
        if isinstance(chunk, Exception):
            raise chunk
        return chunk

    def close(self) -> None:
        self.close_calls += 1


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

    def media_url(self, *, event: Event, photo: Photo, variant: str = "preview-small") -> str:
        return reverse(
            "photo_media",
            kwargs={"slug": event.slug, "photo_id": photo.id, "variant": variant},
        )

    def test_photo_media_success_sets_approved_headers(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(event, id="photo-42")
        body = _ReadableBody([b"photo", b""])
        resolver = Mock()
        resolver.resolve.return_value = ResolvedPublicMedia(body, 5, "image/jpeg", "jpg")

        with patch("config.views._public_media_resolver", return_value=resolver):
            response = views.photo_media(
                RequestFactory().get(self.media_url(event=event, photo=photo)),
                event.slug,
                photo.id,
                "preview-small",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response._iterator, CloseableMediaIterator)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertEqual(response["Content-Length"], "5")
        self.assertEqual(response["Content-Disposition"], 'inline; filename="photo-photo-42.jpg"')
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(b"".join(response.streaming_content), b"photo")
        self.assertEqual(body.close_calls, 1)
        resolver.resolve.assert_called_once_with(photo=photo, variant="preview-small")

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
        resolver.resolve.side_effect = ObjectMissing()

        with patch("config.views._public_media_resolver", return_value=resolver):
            response = self.client.get(self.media_url(event=event, photo=photo))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b"")

    def test_photo_media_returns_503_for_storage_error(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(event)
        resolver = Mock()
        resolver.resolve.side_effect = StorageError()

        with patch("config.views._public_media_resolver", return_value=resolver):
            response = self.client.get(self.media_url(event=event, photo=photo))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"")

    def test_photo_media_closes_body_after_mid_stream_failure(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(event)
        body = _ReadableBody([b"first", RuntimeError("S3 secret")])
        resolver = Mock()
        resolver.resolve.return_value = ResolvedPublicMedia(body, 5, "image/jpeg", "jpg")

        with patch("config.views._public_media_resolver", return_value=resolver):
            response = self.client.get(self.media_url(event=event, photo=photo))

        self.assertEqual(list(response.streaming_content), [b"first"])
        self.assertEqual(body.close_calls, 1)

    def test_photo_media_response_close_before_iteration_closes_body(self) -> None:
        event = self.make_event()
        photo = self.make_private_photo(event)
        body = _ReadableBody([])
        resolver = Mock()
        resolver.resolve.return_value = ResolvedPublicMedia(body, 0, "image/jpeg", "jpg")

        with patch("config.views._public_media_resolver", return_value=resolver):
            response = self.client.get(self.media_url(event=event, photo=photo))

        response.close()

        self.assertEqual(body.close_calls, 1)
        self.assertEqual(list(response.streaming_content), [])

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
