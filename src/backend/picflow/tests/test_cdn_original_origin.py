from io import BytesIO
from unittest.mock import patch

import pytest
from django.test import override_settings

PATH = "/delivery/originals/" + "a" * 32 + "/"
SECRET = "cdn-upstream-secret"


@override_settings(IMAGE_ORIGIN_HEADER_SECRET=SECRET)
@pytest.mark.parametrize("header", ["", "wrong"])
def test_origin_rejects_direct_access_before_storage(client, header):
    with patch("picflow.cdn_origin.PrivateUploadStorage") as storage:
        response = client.get(PATH + "inline", headers={"X-FindMe-Origin-Auth": header})
    assert response.status_code == 403
    storage.assert_not_called()


@override_settings(IMAGE_ORIGIN_HEADER_SECRET=SECRET, ORIGINALS_S3_BUCKET="private-originals")
def test_origin_streams_original_range_with_attachment(client):
    with patch("ingestion.storage.boto3.client") as create_client:
        get_object = create_client.return_value.get_object
        get_object.return_value = {
            "Body": BytesIO(b"abc"),
            "ContentLength": 3,
            "ContentType": "image/jpeg",
            "ContentRange": "bytes 2-4/9",
            "ETag": '"object-etag"',
        }
        response = client.get(
            PATH + "findme-photo-id.jpg",
            headers={"X-FindMe-Origin-Auth": SECRET, "Range": "bytes=2-4"},
        )
    assert response.status_code == 206
    assert response["Content-Range"] == "bytes 2-4/9"
    assert response["ETag"] == '"object-etag"'
    assert response["Content-Disposition"] == 'attachment; filename="findme-photo-id.jpg"'
    assert b"".join(response.streaming_content) == b"abc"
    get_object.assert_called_once_with(
        Bucket="private-originals", Key="originals/" + "a" * 32, Range="bytes=2-4"
    )


@override_settings(IMAGE_ORIGIN_HEADER_SECRET=SECRET)
def test_unmanaged_origin_paths_and_invalid_range_do_not_reach_storage(client):
    headers = {"X-FindMe-Origin-Auth": SECRET}
    with patch("picflow.cdn_origin.PrivateUploadStorage") as storage:
        assert client.get(PATH + "arbitrary.txt", headers=headers).status_code == 404
        assert (
            client.get("/delivery/originals/other-bucket/inline", headers=headers).status_code
            == 404
        )
        assert (
            client.get(PATH + "inline", headers=headers | {"Range": "bytes=1-2,4-5"}).status_code
            == 416
        )
    storage.assert_not_called()
