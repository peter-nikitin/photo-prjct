from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from picflow.gallery_image_delivery import GalleryImageDeliverySettings, GalleryImageUrlSigner

PREVIEW_KEY = (
    "derivatives/previews/photo-42/preview-small-v1/"
    "00000000-0000-0000-0000-000000000001-" + "a" * 64 + ".jpg"
)
VALID_SETTINGS = {
    "GALLERY_CDN_ORIGIN": "https://img.findme-photo.ru",
    "GALLERY_CDN_TOKEN_SECRET": "cdn-secret",
    "GALLERY_IMGPROXY_KEY": "736563726574",
    "GALLERY_IMGPROXY_SALT": "68656c6c6f",
    "PRIVATE_MEDIA_S3_BUCKET": "gallery-media",
}
# Independently calculated with OpenSSL using the published imgproxy and Yandex algorithms.
ENCODED_SOURCE = (
    "czM6Ly9nYWxsZXJ5LW1lZGlhL2Rlcml2YXRpdmVzL3ByZXZpZXdzL3Bob3RvLTQyL3By"
    "ZXZpZXctc21hbGwtdjEvMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAx"
    "LWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh"
    "YWFhYWFhYWFhYWFhYWFhYWEuanBn"
)


@override_settings(**VALID_SETTINGS)
def test_accepted_preview_gets_the_golden_six_hour_cdn_capability() -> None:
    signer = GalleryImageUrlSigner(
        GalleryImageDeliverySettings.from_django_settings(), clock=lambda: 1_700_000_000.75
    )

    assert signer.sign_accepted_preview(key=PREVIEW_KEY, expires_in=21_600) == (
        "https://img.findme-photo.ru/yL0SS1lZCc3esLqjZGKScs95SoWIsdIA0MCocDzgNV4"
        f"/gallery-v1/{ENCODED_SOURCE}.jpg?md5=D9YvW3ht33mGUxymPxRgew&expires=1700021600"
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        *[(name, "") for name in VALID_SETTINGS],
        ("GALLERY_CDN_ORIGIN", "http://img.findme-photo.ru"),
        ("GALLERY_CDN_ORIGIN", "https://img.findme-photo.ru/path"),
        ("GALLERY_CDN_ORIGIN", "https://user:password@img.findme-photo.ru"),
        ("GALLERY_CDN_ORIGIN", "https://img.findme-photo.ru?query=1"),
        ("GALLERY_CDN_ORIGIN", "https://img.findme-photo.ru#fragment"),
        ("GALLERY_CDN_ORIGIN", "https://img.findme-photo.ru\n"),
        ("GALLERY_CDN_ORIGIN", "https://"),
        ("GALLERY_CDN_TOKEN_SECRET", "short"),
        ("GALLERY_CDN_TOKEN_SECRET", "x" * 33),
        ("GALLERY_IMGPROXY_KEY", "not-hex"),
        ("GALLERY_IMGPROXY_KEY", "123"),
        ("GALLERY_IMGPROXY_SALT", "not-hex"),
        ("GALLERY_IMGPROXY_SALT", "aa bb"),
        ("PRIVATE_MEDIA_S3_BUCKET", "gallery-media/originals"),
        ("PRIVATE_MEDIA_S3_BUCKET", "user@other-bucket"),
    ],
)
def test_invalid_delivery_configuration_fails_at_construction(name: str, value: str) -> None:
    with override_settings(**(VALID_SETTINGS | {name: value})):
        with pytest.raises(ImproperlyConfigured, match=name):
            GalleryImageDeliverySettings.from_django_settings()


@override_settings(**VALID_SETTINGS)
@pytest.mark.parametrize("expires_in", [0, -1, 21_601, True, 1.5])
def test_capability_rejects_expiry_outside_the_preview_contract(expires_in: int) -> None:
    signer = GalleryImageUrlSigner(GalleryImageDeliverySettings.from_django_settings())

    with pytest.raises(ValueError, match="expires_in"):
        signer.sign_accepted_preview(key=PREVIEW_KEY, expires_in=expires_in)


@override_settings(**VALID_SETTINGS)
@pytest.mark.parametrize(
    "key",
    [
        "originals/" + "a" * 32,
        "incoming/00000000-0000-0000-0000-000000000001/00000000-0000-0000-0000-000000000002",
        PREVIEW_KEY.replace("preview-small-v1", "preview-large-v1"),
        PREVIEW_KEY.replace("photo-42/", "../"),
        PREVIEW_KEY + "?source=original",
        PREVIEW_KEY + "\n",
        "s3://other-bucket/" + PREVIEW_KEY,
    ],
)
def test_only_managed_final_preview_keys_can_be_signed(key: str) -> None:
    signer = GalleryImageUrlSigner(GalleryImageDeliverySettings.from_django_settings())

    with pytest.raises(ValueError, match="accepted preview object key"):
        signer.sign_accepted_preview(key=key, expires_in=21_600)


@override_settings(**VALID_SETTINGS)
def test_page_refresh_changes_authentication_but_preserves_image_identity() -> None:
    config = GalleryImageDeliverySettings.from_django_settings()
    first = urlsplit(
        GalleryImageUrlSigner(config, clock=lambda: 1_700_000_000).sign_accepted_preview(
            key=PREVIEW_KEY, expires_in=21_600
        )
    )
    refreshed = urlsplit(
        GalleryImageUrlSigner(config, clock=lambda: 1_700_000_060).sign_accepted_preview(
            key=PREVIEW_KEY, expires_in=21_600
        )
    )
    watermarked = urlsplit(
        GalleryImageUrlSigner(config, clock=lambda: 1_700_000_000).sign_accepted_preview(
            key=PREVIEW_KEY.replace("preview-small-v1", "preview-watermarked-v1"),
            expires_in=21_600,
        )
    )

    assert first.path == refreshed.path
    assert watermarked.path != first.path
    assert parse_qs(first.query) == {"md5": ["D9YvW3ht33mGUxymPxRgew"], "expires": ["1700021600"]}
    assert parse_qs(refreshed.query)["expires"] == ["1700021660"]
    assert parse_qs(refreshed.query)["md5"] != parse_qs(first.query)["md5"]
