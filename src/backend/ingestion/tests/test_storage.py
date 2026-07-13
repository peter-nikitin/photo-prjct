from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone
from ingestion.storage import (
    ObjectChanged,
    ObjectIdentity,
    ObjectMismatch,
    ObjectMissing,
    PrivateUploadStorage,
    StorageError,
    StorageUnavailable,
)
from ingestion.tests.fakes import FakeObject, FakeS3Client, client_error

BUCKET = "private-bucket"
INCOMING_KEY = "incoming/123e4567-e89b-12d3-a456-426614174000/123e4567-e89b-12d3-a456-426614174001"
FINAL_KEY = "originals/123e4567e89b12d3a456426614174001"
MISSING_FINAL_KEY = "originals/123e4567e89b12d3a456426614174002"


@pytest.fixture
def client() -> FakeS3Client:
    return FakeS3Client()


@pytest.fixture
def storage(client: FakeS3Client) -> PrivateUploadStorage:
    with override_settings(
        PRIVATE_MEDIA_S3_BUCKET=BUCKET,
        PHOTO_UPLOAD_MAX_FILE_BYTES=100,
        PHOTO_UPLOAD_GRANT_TTL_SECONDS=600,
    ):
        return PrivateUploadStorage(client=client)


def calls(client: FakeS3Client, operation: str) -> list[dict[str, Any]]:
    return [kwargs for name, kwargs in client.calls if name == operation]


@override_settings(
    PRIVATE_MEDIA_S3_BUCKET=BUCKET,
    PRIVATE_MEDIA_S3_ACCESS_KEY_ID="private-access",
    PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY="private-secret",
    PRIVATE_MEDIA_S3_ENDPOINT_URL="https://storage.example.test",
    PRIVATE_MEDIA_S3_REGION="test-region",
)
def test_constructs_signature_v4_client_from_private_settings() -> None:
    with patch("ingestion.storage.boto3.client") as create_client:
        PrivateUploadStorage()

    kwargs = create_client.call_args.kwargs
    assert create_client.call_args.args == ("s3",)
    assert kwargs["aws_access_key_id"] == "private-access"
    assert kwargs["aws_secret_access_key"] == "private-secret"
    assert kwargs["endpoint_url"] == "https://storage.example.test"
    assert kwargs["region_name"] == "test-region"
    assert kwargs["config"].signature_version == "s3v4"


def test_presigned_post_has_exact_private_upload_policy(
    storage: PrivateUploadStorage, client: FakeS3Client
) -> None:
    before = timezone.now()
    grant = storage.create_presigned_post(incoming_key=INCOMING_KEY, max_bytes=37)
    after = timezone.now()

    assert grant.url == "https://upload.example.test/private"
    assert grant.fields == {"key": "signed-key", "policy": "signed-policy"}
    assert timezone.is_aware(grant.expires_at)
    assert before + timedelta(seconds=600) <= grant.expires_at
    assert grant.expires_at <= after + timedelta(seconds=600)
    assert calls(client, "generate_presigned_post") == [
        {
            "Bucket": BUCKET,
            "Key": INCOMING_KEY,
            "Fields": {"Content-Type": "image/jpeg"},
            "Conditions": [
                {"bucket": BUCKET},
                {"key": INCOMING_KEY},
                {"Content-Type": "image/jpeg"},
                ["content-length-range", 1, 37],
            ],
            "ExpiresIn": 600,
        }
    ]
    policy = calls(client, "generate_presigned_post")[0]
    assert "acl" not in str(policy).lower()


@pytest.mark.parametrize(
    "key",
    [
        "uploads/123e4567-e89b-12d3-a456-426614174000/123e4567-e89b-12d3-a456-426614174001",
        "incoming/not-a-uuid/123e4567-e89b-12d3-a456-426614174001",
        "incoming/123e4567-e89b-12d3-a456-426614174000/not-a-uuid",
        f"{INCOMING_KEY}/extra",
    ],
)
def test_presign_rejects_non_incoming_keys_before_client_call(
    storage: PrivateUploadStorage, client: FakeS3Client, key: str
) -> None:
    with pytest.raises(ValueError):
        storage.create_presigned_post(incoming_key=key, max_bytes=10)

    assert client.calls == []


@pytest.mark.parametrize("max_bytes", [0, 101])
def test_presign_rejects_size_outside_configured_cap_before_client_call(
    storage: PrivateUploadStorage, client: FakeS3Client, max_bytes: int
) -> None:
    with pytest.raises(ValueError):
        storage.create_presigned_post(incoming_key=INCOMING_KEY, max_bytes=max_bytes)

    assert client.calls == []


def test_inspect_returns_wire_and_comparison_etags(
    storage: PrivateUploadStorage, client: FakeS3Client
) -> None:
    client.put_object(INCOMING_KEY, b"jpeg", '"abc-2"', "IMAGE/JPEG; Charset=Binary")

    identity = storage.inspect(key=INCOMING_KEY)

    assert identity == ObjectIdentity('"abc-2"', "abc-2", 4, "image/jpeg; charset=binary")
    assert calls(client, "head_object") == [{"Bucket": BUCKET, "Key": INCOMING_KEY}]


@pytest.mark.parametrize(
    "response",
    [
        {"ETag": "abc", "ContentLength": 4, "ContentType": "image/jpeg"},
        {"ETag": '""', "ContentLength": 4, "ContentType": "image/jpeg"},
        {"ETag": '"   "', "ContentLength": 4, "ContentType": "image/jpeg"},
        {"ETag": '"abc"', "ContentLength": -1, "ContentType": "image/jpeg"},
        {"ETag": '"abc"', "ContentLength": "4", "ContentType": "image/jpeg"},
        {"ETag": '"abc"', "ContentLength": 4, "ContentType": None},
    ],
)
def test_inspect_rejects_unusable_metadata(response: dict[str, Any]) -> None:
    client = FakeS3Client()
    client.head_object = lambda **kwargs: response  # type: ignore[method-assign]
    with override_settings(PRIVATE_MEDIA_S3_BUCKET=BUCKET):
        storage = PrivateUploadStorage(client=client)

    with pytest.raises(ObjectMismatch):
        storage.inspect(key=INCOMING_KEY)


def test_read_range_passes_exact_etag_and_closes_body(
    storage: PrivateUploadStorage, client: FakeS3Client
) -> None:
    client.put_object(INCOMING_KEY, b"abcdef", '"wire-etag"')

    assert storage.read_range(key=INCOMING_KEY, etag_wire='"wire-etag"', start=0, end=0) == b"a"
    assert storage.read_range(key=INCOMING_KEY, etag_wire='"wire-etag"', start=5, end=5) == b"f"

    assert calls(client, "get_object") == [
        {"Bucket": BUCKET, "Key": INCOMING_KEY, "Range": "bytes=0-0", "IfMatch": '"wire-etag"'},
        {"Bucket": BUCKET, "Key": INCOMING_KEY, "Range": "bytes=5-5", "IfMatch": '"wire-etag"'},
    ]
    assert client.last_body is not None and client.last_body.closed


@pytest.mark.parametrize("start,end", [(-1, 0), (1, 0)])
def test_read_range_rejects_invalid_bounds_before_client_call(
    storage: PrivateUploadStorage, client: FakeS3Client, start: int, end: int
) -> None:
    with pytest.raises(ValueError):
        storage.read_range(key=INCOMING_KEY, etag_wire='"abc"', start=start, end=end)
    assert client.calls == []


@pytest.mark.parametrize(
    "operation,status,exception",
    [
        ("head_object", 404, ObjectMissing),
        ("get_object", 404, ObjectMissing),
        ("get_object", 412, ObjectChanged),
        ("get_object", 409, ObjectChanged),
        ("copy_object", 412, ObjectChanged),
        ("copy_object", 409, ObjectChanged),
        ("head_object", 500, StorageUnavailable),
    ],
)
def test_client_failures_map_to_sanitized_stable_errors(
    storage: PrivateUploadStorage,
    client: FakeS3Client,
    operation: str,
    status: int,
    exception: type[StorageError],
) -> None:
    client.put_object(INCOMING_KEY, b"jpeg", '"secret-etag"')
    client.failures[operation] = client_error(status, message="raw secret body")

    with pytest.raises(exception) as caught:
        if operation == "head_object":
            storage.inspect(key=INCOMING_KEY)
        elif operation == "get_object":
            storage.read_range(key=INCOMING_KEY, etag_wire='"secret-etag"', start=0, end=1)
        else:
            storage.promote(
                incoming_key=INCOMING_KEY,
                final_key=FINAL_KEY,
                etag_wire='"secret-etag"',
            )

    rendered = str(caught.value)
    assert caught.value.code
    assert "secret" not in rendered
    assert "raw" not in rendered
    assert INCOMING_KEY not in rendered


def test_promote_conditionally_copies_and_verifies_final_identity(
    storage: PrivateUploadStorage, client: FakeS3Client
) -> None:
    client.put_object(INCOMING_KEY, b"jpeg-data", '"source-etag"', "IMAGE/JPEG")

    result = storage.promote(
        incoming_key=INCOMING_KEY, final_key=FINAL_KEY, etag_wire='"source-etag"'
    )

    assert result == ObjectIdentity('"source-etag"', "source-etag", 9, "image/jpeg")
    assert calls(client, "head_object") == [
        {"Bucket": BUCKET, "Key": INCOMING_KEY, "IfMatch": '"source-etag"'},
        {"Bucket": BUCKET, "Key": FINAL_KEY},
    ]
    assert calls(client, "copy_object") == [
        {
            "Bucket": BUCKET,
            "Key": FINAL_KEY,
            "CopySource": {"Bucket": BUCKET, "Key": INCOMING_KEY},
            "CopySourceIfMatch": '"source-etag"',
        }
    ]
    assert INCOMING_KEY in client.objects


@pytest.mark.parametrize(
    "incoming,final", [("bad/key", FINAL_KEY), (INCOMING_KEY, "originals/NOT-HEX")]
)
def test_promote_rejects_malformed_keys_before_client_call(
    storage: PrivateUploadStorage, client: FakeS3Client, incoming: str, final: str
) -> None:
    with pytest.raises(ValueError):
        storage.promote(incoming_key=incoming, final_key=final, etag_wire='"etag"')
    assert client.calls == []


def test_promote_detects_overwrite_between_source_head_and_copy(
    storage: PrivateUploadStorage, client: FakeS3Client
) -> None:
    client.put_object(INCOMING_KEY, b"first", '"first"')
    client.mutate_on("copy_object", INCOMING_KEY, FakeObject(b"second", '"second"'))

    with pytest.raises(ObjectChanged):
        storage.promote(incoming_key=INCOMING_KEY, final_key=FINAL_KEY, etag_wire='"first"')

    assert FINAL_KEY not in client.objects


def test_promote_rejects_final_head_mismatch(
    storage: PrivateUploadStorage, client: FakeS3Client
) -> None:
    client.put_object(INCOMING_KEY, b"first", '"first"')
    client.mutate_on("head_object", FINAL_KEY, FakeObject(b"different", '"different"'))

    with pytest.raises(ObjectMismatch):
        storage.promote(incoming_key=INCOMING_KEY, final_key=FINAL_KEY, etag_wire='"first"')


def test_promote_rejects_unusable_copy_result_etag(
    storage: PrivateUploadStorage, client: FakeS3Client
) -> None:
    client.put_object(INCOMING_KEY, b"first", '"first"')
    client.copy_result_etag = "unquoted"

    with pytest.raises(ObjectMismatch):
        storage.promote(incoming_key=INCOMING_KEY, final_key=FINAL_KEY, etag_wire='"first"')


def test_overwriting_incoming_after_promotion_cannot_change_final(
    storage: PrivateUploadStorage, client: FakeS3Client
) -> None:
    client.put_object(INCOMING_KEY, b"first", '"first"')
    storage.promote(incoming_key=INCOMING_KEY, final_key=FINAL_KEY, etag_wire='"first"')

    client.put_object(INCOMING_KEY, b"second", '"second"')

    assert client.objects[FINAL_KEY].content == b"first"
    assert client.objects[FINAL_KEY].etag == '"first"'


def test_delete_is_separate_and_missing_is_idempotent(
    storage: PrivateUploadStorage, client: FakeS3Client
) -> None:
    storage.delete(key=MISSING_FINAL_KEY)
    assert calls(client, "delete_object") == [{"Bucket": BUCKET, "Key": MISSING_FINAL_KEY}]


def test_delete_sanitizes_failures(storage: PrivateUploadStorage, client: FakeS3Client) -> None:
    client.failures["delete_object"] = client_error(500, message="raw signed fields secret")

    with pytest.raises(StorageUnavailable) as caught:
        storage.delete(key=FINAL_KEY)

    assert str(caught.value) == "Object storage is unavailable."


@pytest.mark.parametrize("method_name", ["inspect", "read_range", "delete"])
def test_managed_object_methods_reject_arbitrary_keys_before_client_call(
    storage: PrivateUploadStorage, client: FakeS3Client, method_name: str
) -> None:
    with pytest.raises(ValueError):
        if method_name == "inspect":
            storage.inspect(key="arbitrary/key")
        elif method_name == "read_range":
            storage.read_range(key="arbitrary/key", etag_wire='"etag"', start=0, end=0)
        else:
            storage.delete(key="arbitrary/key")

    assert client.calls == []


@pytest.mark.parametrize(
    "method_name,args",
    [
        ("create_presigned_post", (INCOMING_KEY, 10)),
        ("inspect", (INCOMING_KEY,)),
        ("read_range", (INCOMING_KEY, '"etag"', 0, 0)),
        ("promote", (INCOMING_KEY, FINAL_KEY, '"etag"')),
        ("delete", (FINAL_KEY,)),
    ],
)
def test_public_storage_contract_is_keyword_only(
    storage: PrivateUploadStorage, method_name: str, args: tuple[object, ...]
) -> None:
    method = getattr(storage, method_name)

    with pytest.raises(TypeError):
        method(*args)
