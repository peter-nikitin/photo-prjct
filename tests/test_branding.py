from html.parser import HTMLParser
from xml.etree import ElementTree

from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from django.urls import reverse


class _CatalogBrandParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.favicon_sources: list[str] = []
        self.header_logo_sources: list[str] = []
        self.header_logo_accessibility: list[tuple[str | None, str | None]] = []
        self.text: list[str] = []
        self._link_labels: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a":
            self._link_labels.append(attributes.get("aria-label"))
        if tag == "link" and attributes.get("rel") == "icon":
            source = attributes.get("href")
            if source:
                self.favicon_sources.append(source)
        if tag == "img" and "brand-mark" in (attributes.get("class") or "").split():
            source = attributes.get("src")
            if source:
                self.header_logo_sources.append(source)
                self.header_logo_accessibility.append(
                    (self._link_labels[-1] if self._link_labels else None, attributes.get("alt"))
                )

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._link_labels.pop()

    def handle_data(self, data: str) -> None:
        self.text.append(data.strip())


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
class BrandingTests(TestCase):
    def test_catalog_shares_one_production_svg_between_header_and_favicon(self) -> None:
        response = self.client.get(reverse("event_catalog"))

        self.assertEqual(response.status_code, 200)
        parser = _CatalogBrandParser()
        parser.feed(response.content.decode())
        self.assertEqual(parser.favicon_sources, ["/static/ui/logo.svg"])
        self.assertEqual(parser.header_logo_sources, ["/static/ui/logo.svg"])
        self.assertEqual(
            parser.header_logo_accessibility,
            [("FindMe Photo — каталог событий", "")],
        )
        self.assertIn("найди моё фото", parser.text)

        logo_path = finders.find("ui/logo.svg")
        self.assertIsNotNone(logo_path)
        self.assertTrue(ElementTree.parse(logo_path).getroot().tag.endswith("svg"))
