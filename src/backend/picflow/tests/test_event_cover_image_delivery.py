import base64
from datetime import date
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import modify_settings, override_settings
from feature_flags.models import FeatureFlag
from feature_flags.registry import EVENT_COVER_CDN_IMAGES
from feature_flags.testing import override_feature_flags

from picflow.gallery_image_delivery import EventCoverImageDeliverySettings, GalleryImageUrlSigner
from picflow.models import Event

COVER_KEY = "event-covers/00000000-0000-4000-8000-000000000001.jpg"
DELIVERY_SETTINGS = {
    "GALLERY_CDN_ORIGIN": "https://img.example.test",
    "GALLERY_CDN_TOKEN_SECRET": "cdn-secret",
    "GALLERY_IMGPROXY_KEY": "736563726574",
    "GALLERY_IMGPROXY_SALT": "68656c6c6f",
    "PRIVATE_MEDIA_S3_BUCKET": "private-gallery",
    "MEDIA_S3_PUBLIC_BUCKET": "public-covers",
}
COVER_FLAG = EVENT_COVER_CDN_IMAGES


@pytest.fixture(autouse=True)
def local_static_storage():
    with (
        modify_settings(MIDDLEWARE={"remove": "whitenoise.middleware.WhiteNoiseMiddleware"}),
        override_settings(
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            }
        ),
    ):
        yield


class CoverImages(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.images: list[dict[str, str]] = []
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "img" and (values.get("alt") or "").startswith("Обложка события"):
            self.images.append({key: value for key, value in values.items() if value is not None})


@pytest.fixture
def covered_event(db):
    return Event.objects.create(
        name="Covered",
        slug="covered",
        city="Test",
        start_date=date(2026, 9, 29),
        end_date=date(2026, 9, 29),
        publication_status=Event.PublicationStatus.PUBLISHED,
        cover=COVER_KEY,
    )


@pytest.mark.django_db
@override_settings(**DELIVERY_SETTINGS)
@pytest.mark.parametrize(
    ("state", "staff", "cdn"),
    [
        ("off", False, False),
        ("staff", False, False),
        ("staff", True, True),
        ("on", False, True),
    ],
)
def test_catalog_cover_uses_its_own_gate(client, covered_event, state, staff, cdn):
    if staff:
        client.force_login(get_user_model().objects.create_user("staff", is_staff=True))
    with override_feature_flags({COVER_FLAG: state}):
        response = client.get("/")
    images = CoverImages(response.content.decode()).images
    assert response.status_code == 200
    assert len(images) == 1
    assert ("/cover-v1/" in images[0]["src"]) is cdn
    if cdn:
        encoded = urlsplit(images[0]["src"]).path.rsplit("/", 1)[-1][:-4]
        assert base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode() == (
            "https://storage.yandexcloud.net/public-covers/" + COVER_KEY
        )
    assert images[0]["loading"] == "eager"


@pytest.mark.django_db
def test_catalog_missing_cover_gate_preserves_direct_url(client, covered_event):
    FeatureFlag.objects.filter(key=COVER_FLAG.key).delete()
    response = client.get("/")
    assert CoverImages(response.content.decode()).images[0]["src"] == covered_event.cover.url


@pytest.mark.django_db
@override_settings(**(DELIVERY_SETTINGS | {"GALLERY_IMGPROXY_KEY": ""}))
def test_catalog_missing_cdn_prerequisite_preserves_cover_and_placeholder(client, covered_event):
    Event.objects.create(
        name="Empty",
        slug="empty",
        city="Test",
        start_date=date(2026, 9, 29),
        end_date=date(2026, 9, 29),
        publication_status=Event.PublicationStatus.PUBLISHED,
    )
    with override_feature_flags({COVER_FLAG: "on"}):
        response = client.get("/")
    assert response.status_code == 200
    assert CoverImages(response.content.decode()).images[0]["src"] == covered_event.cover.url
    assert "cover-placeholder" in response.content.decode()


@pytest.mark.django_db
def test_catalog_only_first_four_covers_are_eager(client, covered_event):
    for index in range(5):
        Event.objects.create(
            name=f"Extra {index}",
            slug=f"extra-{index}",
            city="Test",
            start_date=date(2026, 9, 29),
            end_date=date(2026, 9, 29),
            publication_status=Event.PublicationStatus.PUBLISHED,
            cover=COVER_KEY,
        )
    images = CoverImages(client.get("/").content.decode()).images
    assert [image["loading"] for image in images] == ["eager"] * 4 + ["lazy"] * 2


@override_settings(**DELIVERY_SETTINGS)
def test_cover_capability_uses_only_the_public_https_managed_key() -> None:
    signer = GalleryImageUrlSigner(
        EventCoverImageDeliverySettings.from_django_settings(), clock=lambda: 1_700_000_000.75
    )
    url = urlsplit(signer.sign_event_cover(key=COVER_KEY))
    signature, preset, encoded = url.path.strip("/").split("/")
    assert signature == "YPUshlEz_rd0ZpUZrTtkY6x2Abopig9tWxsnwnx2SEI"
    assert preset == "cover-v1"
    assert base64.urlsafe_b64decode(encoded[:-4] + "==").decode() == (
        "https://storage.yandexcloud.net/public-covers/" + COVER_KEY
    )
    assert parse_qs(url.query)["expires"] == ["1700021600"]
    assert parse_qs(url.query)["md5"] == ["CqadsrGnlKsVpIzmdRBYig"]


@override_settings(**DELIVERY_SETTINGS)
@pytest.mark.parametrize(
    "key",
    [
        "event-covers/../original.jpg",
        "event-covers/arbitrary.jpg",
        COVER_KEY + "?x=1",
        COVER_KEY + "\n",
        COVER_KEY.replace("event-covers/", "originals/"),
        "https://example.test/" + COVER_KEY,
        COVER_KEY.replace(".jpg", ".jpg/extra"),
    ],
)
def test_cover_capability_rejects_unmanaged_keys(key: str) -> None:
    signer = GalleryImageUrlSigner(EventCoverImageDeliverySettings.from_django_settings())
    with pytest.raises(ValueError, match="event cover object key"):
        signer.sign_event_cover(key=key)


@pytest.mark.parametrize(
    "bucket", ["", "public/other", "public,other", "public..other", "user@bucket"]
)
@override_settings(**DELIVERY_SETTINGS)
def test_cover_signer_rejects_bucket_source_injection(bucket):
    with override_settings(MEDIA_S3_PUBLIC_BUCKET=bucket):
        with pytest.raises(ImproperlyConfigured, match="MEDIA_S3_PUBLIC_BUCKET"):
            EventCoverImageDeliverySettings.from_django_settings()
