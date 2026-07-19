from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import SimpleTestCase, TestCase, modify_settings, override_settings
from django.urls import reverse

from picflow.models import Event


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
        self.assertNotContains(response, f'href="{reverse("admin:index")}"')
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
