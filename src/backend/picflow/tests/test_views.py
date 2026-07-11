from django.test import TestCase, modify_settings, override_settings
from django.urls import reverse


@override_settings(
    STORAGES={
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        }
    }
)
@modify_settings(MIDDLEWARE={"remove": "whitenoise.middleware.WhiteNoiseMiddleware"})
class PageSmokeTests(TestCase):
    def test_public_pages_render_successfully(self) -> None:
        route_names = (
            "index",
            "events",
            "dashboard",
            "upload",
            "orders",
            "promos",
            "purchased",
            "legal",
        )

        for route_name in route_names:
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
