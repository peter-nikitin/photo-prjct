from io import BytesIO
from typing import cast
from uuid import UUID

import boto3
import pytest
from botocore.stub import Stubber
from django.test import override_settings
from ingestion.storage import PrivateUploadStorage, StorageUnavailable
from ingestion.tests.fakes import FakeS3Client

_OBJECT_ID = UUID("123e4567-e89b-12d3-a456-426614174001")
_KEY = f"archives/{_OBJECT_ID}.zip"
_BUCKET = "archive-operational-test"
_FILENAME = "findme-photo-гонка.zip"


@override_settings(OPERATIONAL_S3_BUCKET=_BUCKET)
def test_uploads_zip_with_attachment_metadata_to_the_private_operational_bucket() -> None:
    client = FakeS3Client()
    storage = PrivateUploadStorage(client=client)
    storage.upload_archive(object_id=_OBJECT_ID, body=BytesIO(b"zip"), filename=_FILENAME)
    assert client.objects[_BUCKET, _KEY].content == b"zip"
    _, upload = client.calls[-1]
    assert upload["ExtraArgs"]["ContentType"] == "application/zip"
    assert "CacheControl" not in upload["ExtraArgs"]
    assert upload["ExtraArgs"]["ContentDisposition"].startswith("attachment; filename*=utf-8''")


@override_settings(OPERATIONAL_S3_BUCKET=_BUCKET)
def test_failed_multipart_upload_aborts_without_completing_or_exposing_error() -> None:
    client = boto3.client("s3", aws_access_key_id="test", aws_secret_access_key="test")
    storage = PrivateUploadStorage(client=client)
    with Stubber(client) as stubber:
        stubber.add_response("create_multipart_upload", {"UploadId": "test-upload"})
        stubber.add_response("upload_part", {"ETag": '"part-1"'})
        stubber.add_client_error("upload_part", http_status_code=503, service_message="secret")
        stubber.add_response(
            "abort_multipart_upload",
            {},
            {"Bucket": _BUCKET, "Key": _KEY, "UploadId": "test-upload"},
        )
        with pytest.raises(StorageUnavailable, match=r"^Object storage is unavailable\.$"):
            storage.upload_archive(
                object_id=_OBJECT_ID,
                body=BytesIO(bytes(8 * 1024 * 1024 + 1)),
                filename=_FILENAME,
            )
        stubber.assert_no_pending_responses()


def test_refuses_archive_identifier_that_cannot_form_an_archive_key() -> None:
    client = FakeS3Client()
    storage = PrivateUploadStorage(client=client)
    unsafe = cast(UUID, "../uploads/original")
    with pytest.raises(ValueError, match="invalid archive object identifier"):
        storage.upload_archive(object_id=unsafe, body=BytesIO(b"zip"), filename=_FILENAME)
    assert not client.calls
