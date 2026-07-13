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

    def test_event_detail_and_draft_404(self) -> None:
        event = self.make_event(description="Race description")
        response = self.client.get(reverse("event_detail", kwargs={"slug": event.slug}))
        self.assertContains(response, "Race description")
        draft = self.make_event(
            name="Draft", slug="draft", publication_status=Event.PublicationStatus.DRAFT
        )
        self.assertEqual(
            self.client.get(reverse("event_detail", kwargs={"slug": draft.slug})).status_code, 404
        )

    def test_legacy_events_url_redirects_and_demo_routes_are_removed(self) -> None:
        response = self.client.get("/events/")
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        for path in ("/dashboard/", "/upload/", "/orders/", "/promos/", "/purchased/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
