from xml.etree import ElementTree

from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
class BrandingTests(TestCase):
    def test_catalog_uses_the_shared_logo_for_the_favicon_and_decorative_header_mark(self) -> None:
        response = self.client.get(reverse("event_catalog"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        logo_url = "/static/ui/logo.svg"
        self.assertEqual(content.count(logo_url), 2)
        self.assertIn('<link rel="icon" href="/static/ui/logo.svg" type="image/svg+xml">', content)
        self.assertIn(
            '<img class="brand-mark" src="/static/ui/logo.svg" width="42" height="42" alt="">',
            content,
        )
        self.assertIn("найди моё фото", content)
        self.assertIn('aria-label="FindMe Photo — каталог событий"', content)
        self.assertNotIn('<span class="brand-mark" aria-hidden="true">FM</span>', content)
        self.assertNotIn("фотографии событий", content)

        logo_path = finders.find("ui/logo.svg")
        self.assertIsNotNone(logo_path)
        self.assertEqual(ElementTree.parse(logo_path).getroot().attrib["viewBox"], "0 0 1500 1500")
