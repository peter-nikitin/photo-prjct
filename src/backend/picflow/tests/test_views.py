from datetime import date, timedelta

from django.test import TestCase, modify_settings, override_settings
from django.urls import reverse

from picflow.models import Event


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
        unfinished_targets = (
            "/upload/",
            "/orders/",
            "/promos/",
            "/promotions/",
            "/purchased/",
            "/search/",
        )

        for path in public_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "unpkg.com")
                for target in unfinished_targets:
                    self.assertNotContains(response, f'href="{target}"')
                self.assertNotContains(response, 'type="search"')
