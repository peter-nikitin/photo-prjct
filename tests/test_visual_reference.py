from pathlib import Path

from django.test import Client, override_settings
from django.urls import reverse

from tests.visual import settings as visual_settings

ROOT = Path(__file__).resolve().parents[1]

VISUAL_ROUTES = {
    "visual_catalog_populated": "/__visual__/catalog/populated/",
    "visual_catalog_empty": "/__visual__/catalog/empty/",
    "visual_event_covered": "/__visual__/event/covered/",
    "visual_event_uncovered": "/__visual__/event/uncovered/",
    "visual_event_gallery_populated": "/__visual__/event/gallery-populated/",
    "visual_event_gallery_empty": "/__visual__/event/gallery-empty/",
    "visual_legal": "/__visual__/legal/",
    "visual_reference_search": "/__visual__/reference/search/",
    "visual_reference_dashboard": "/__visual__/reference/dashboard/",
    "visual_reference_events": "/__visual__/reference/events/",
    "visual_reference_orders": "/__visual__/reference/orders/",
    "visual_reference_promotions": "/__visual__/reference/promotions/",
    "visual_reference_purchased": "/__visual__/reference/purchased/",
}

REFERENCE_TEMPLATES = {
    "base.html",
    "dashboard.html",
    "events.html",
    "orders.html",
    "promotions.html",
    "purchased.html",
    "search.html",
}


@override_settings(ROOT_URLCONF="tests.visual.urls")
def test_visual_routes_have_deterministic_names_and_paths() -> None:
    for route_name, expected_path in VISUAL_ROUTES.items():
        assert reverse(route_name) == expected_path


def test_reference_templates_are_complete_and_script_free() -> None:
    template_dir = ROOT / "tests/visual/templates/design_reference"

    assert {path.name for path in template_dir.glob("*.html")} == REFERENCE_TEMPLATES
    for template in template_dir.glob("*.html"):
        source = template.read_text(encoding="utf-8")
        assert "<script" not in source
        assert "localStorage" not in source


def test_visual_fixture_module_has_no_orm_dependency() -> None:
    source = (ROOT / "tests/visual/views.py").read_text(encoding="utf-8")

    assert "picflow.models" not in source
    assert ".objects" not in source
    assert "django.db" not in source


@override_settings(ROOT_URLCONF="tests.visual.urls")
def test_event_header_routes_preserve_their_pre_gallery_visual_contract() -> None:
    client = Client()

    for path in ("/__visual__/event/covered/", "/__visual__/event/uncovered/"):
        response = client.get(path)
        assert response.status_code == 200
        assert b'data-visual-header-only="true"' in response.content

    for path in (
        "/__visual__/event/gallery-populated/",
        "/__visual__/event/gallery-empty/",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert b'data-visual-header-only="true"' not in response.content


def test_visual_settings_allow_local_test_clients() -> None:
    assert {"localhost", "127.0.0.1", "testserver"} <= set(visual_settings.ALLOWED_HOSTS)
