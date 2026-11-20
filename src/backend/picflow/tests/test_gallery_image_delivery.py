from datetime import UTC, datetime
from time import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from ingestion.storage import ObjectMismatch, ObjectMissing, StorageUnavailable, s3_object_key
from ingestion.tests.fakes import FakeS3Client, client_error

from picflow.gallery_image_delivery import (
    CdnFinalObjectStorage,
    GalleryImageDeliverySettings,
    GalleryImageUrlSigner,
    sign_private_archive,
)

PREVIEW_KEY = (
    "derivatives/previews/photo-42/preview-small-v1/"
    "00000000-0000-0000-0000-000000000001-" + "a" * 64 + ".jpg"
)
VALID_SETTINGS = {
    "GALLERY_CDN_ORIGIN": "https://img.findme-photo.ru",
    "GALLERY_CDN_TOKEN_SECRET": "cdn-secret",
    "EVENT_MEDIA_S3_BUCKET": "gallery-media",
    "PRIVATE_MEDIA_S3_ACCESS_KEY_ID": "PUBLICEXAMPLE",
    "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": "secret-example",
    "PRIVATE_MEDIA_S3_ENDPOINT_URL": "https://storage.yandexcloud.net",
    "PRIVATE_MEDIA_S3_REGION": "ru-central1",
}


@override_settings(**VALID_SETTINGS)
def test_accepted_preview_gets_the_golden_six_hour_cdn_capability() -> None:
    signer = GalleryImageUrlSigner(
        GalleryImageDeliverySettings.from_django_settings(), clock=lambda: 1_700_000_000.75
    )

    with patch(
        "botocore.auth.get_current_datetime",
        return_value=datetime.fromtimestamp(1_700_000_000, UTC),
    ):
        url = urlsplit(signer.sign_accepted_preview(key=PREVIEW_KEY, expires_in=21_600))
    assert url.path == "/gallery-media/" + PREVIEW_KEY.removeprefix("derivatives/")
    query = parse_qs(url.query)
    # Independent SigV4 vector includes the origin Host and both CDN query parameters.
    assert query["md5"] == ["MLbk0XAWlDXZyqTlKHYwPQ"]
    assert query["X-Amz-Signature"] == [
        "bdd0a11912436ddb7966a1cb40b2e0c8d75249f6a0421d265a97a86459c4bb12"
    ]
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-SignedHeaders"] == ["host"]
    assert query["expires"] == ["1700021600"]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        *[
            (name, "")
            for name in ("GALLERY_CDN_ORIGIN", "GALLERY_CDN_TOKEN_SECRET", "EVENT_MEDIA_S3_BUCKET")
        ],
        ("GALLERY_CDN_ORIGIN", "http://img.findme-photo.ru"),
        ("GALLERY_CDN_ORIGIN", "https://img.findme-photo.ru/path"),
        ("GALLERY_CDN_ORIGIN", "https://user:password@img.findme-photo.ru"),
        ("GALLERY_CDN_ORIGIN", "https://img.findme-photo.ru?query=1"),
        ("GALLERY_CDN_ORIGIN", "https://img.findme-photo.ru#fragment"),
        ("GALLERY_CDN_ORIGIN", "https://img.findme-photo.ru\n"),
        ("GALLERY_CDN_ORIGIN", "https://"),
        ("GALLERY_CDN_TOKEN_SECRET", "short"),
        ("GALLERY_CDN_TOKEN_SECRET", "x" * 33),
        ("EVENT_MEDIA_S3_BUCKET", "gallery-media/originals"),
        ("EVENT_MEDIA_S3_BUCKET", "user@other-bucket"),
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
    assert parse_qs(first.query)["expires"] == ["1700021600"]
    assert parse_qs(refreshed.query)["expires"] == ["1700021660"]
    assert parse_qs(refreshed.query)["md5"] != parse_qs(first.query)["md5"]


@override_settings(**VALID_SETTINGS)
def test_original_disposition_is_part_of_cache_identity_and_token():
    signer = GalleryImageUrlSigner(
        GalleryImageDeliverySettings.from_django_settings(), clock=lambda: 1_700_000_000
    )
    key = "originals/" + "a" * 32
    inline = urlsplit(signer.original_url(key=key, attachment_filename=None))
    attachment = urlsplit(signer.original_url(key=key, attachment_filename="findme-photo-id.jpg"))
    assert inline.path == f"/delivery/{key}/inline"
    assert attachment.path == f"/delivery/{key}/findme-photo-id.jpg"
    assert parse_qs(inline.query)["md5"] != parse_qs(attachment.query)["md5"]


@override_settings(**VALID_SETTINGS)
@pytest.mark.parametrize("ttl", [37, 21_601])
def test_preview_redirect_uses_configured_download_lifetime_for_both_signatures(ttl):
    client = FakeS3Client()
    client.put_object("gallery-media", s3_object_key(PREVIEW_KEY), b"jpeg", '"etag"')
    with override_settings(PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS=ttl):
        before = int(time())
        url = CdnFinalObjectStorage(client=client).sign_final(key=PREVIEW_KEY)
        after = int(time())

    query = parse_qs(urlsplit(url).query)
    assert query["X-Amz-Expires"] == [str(ttl)]
    assert before + ttl <= int(query["expires"][0]) <= after + ttl


@override_settings(**VALID_SETTINGS)
@pytest.mark.parametrize(
    ("status", "expected"), [(404, ObjectMissing), (503, StorageUnavailable), (200, ObjectMismatch)]
)
def test_preview_redirect_checks_exact_storage_object_before_issuing_grant(status, expected):
    client = FakeS3Client()
    object_key = s3_object_key(PREVIEW_KEY)
    if status == 200:
        client.put_object("gallery-media", object_key, b"invalid", '"etag"', "text/plain")
    else:
        client.failures["head_object"] = client_error(status)
    with pytest.raises(expected):
        CdnFinalObjectStorage(client=client).sign_final(key=PREVIEW_KEY)
    assert client.calls == [("head_object", {"Bucket": "gallery-media", "Key": object_key})]


@override_settings(
    **VALID_SETTINGS,
    OPERATIONAL_S3_BUCKET="test-operational",
)
def test_archive_link_binds_exact_storage_path_origin_signature_and_short_edge_access() -> None:
    object_id = UUID("12345678-1234-1234-1234-123456789abc")
    with (
        patch(
            "picflow.gallery_image_delivery.GalleryImageUrlSigner",
            side_effect=lambda config: GalleryImageUrlSigner(
                config, clock=lambda: 1_700_000_000.75
            ),
        ),
        patch(
            "botocore.auth.get_current_datetime",
            return_value=datetime.fromtimestamp(1_700_000_000, UTC),
        ),
    ):
        url = urlsplit(sign_private_archive(object_id=object_id, expires_in=225))
    assert url.scheme == "https"
    assert url.hostname == "img.findme-photo.ru"
    assert url.path == f"/test-operational/archives/{object_id}.zip"
    query = parse_qs(url.query)
    assert query["expires"] == ["1700000225"]
    assert query["X-Amz-Expires"] == ["225"]
    assert query["X-Amz-SignedHeaders"] == ["host"]
    assert query["md5"] == ["LNLH-gh7BMfFhW1N9UcCbw"]
    assert query["X-Amz-Signature"] == [
        "878a59c909519e6d6dbd8ff33a9709b845eca3ebd6993aedff75acd66a4e59ee"
    ]
