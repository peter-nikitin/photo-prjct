from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest
from photo_worker.client import ApiError, DownloadError, HttpClient, UploadError


class Response:
    def __init__(
        self, body: bytes, *, headers: dict[str, str] | None = None, status: int = 200
    ) -> None:
        self._body = io.BytesIO(body)
        self.headers = headers or {}
        self.status = status
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self._body.read(size)

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_: object) -> None:
        self._body.close()


class PartialResponse(Response):
    def __init__(self) -> None:
        super().__init__(b"abcdef", headers={"Content-Type": "image/jpeg"})
        self._calls = 0

    def read(self, size: int = -1) -> bytes:
        self._calls += 1
        if self._calls == 1:
            return b"abc"
        raise OSError("interrupted")


def test_json_requests_use_bearer_auth_and_do_not_put_token_in_url() -> None:
    requests = []

    def opener(request, *, timeout: float):
        requests.append(request)
        return Response(b'{"empty": true, "suggested_delay_seconds": 5}')

    client = HttpClient(
        "https://worker.example.test/internal/photo-processing/v1", "worker-secret", opener=opener
    )

    assert client.post_json("claim", {"contract_version": 1})["empty"] is True
    request = requests[0]
    assert request.full_url == "https://worker.example.test/internal/photo-processing/v1/claim"
    assert request.get_header("Authorization") == "Bearer worker-secret"
    assert json.loads(request.data) == {"contract_version": 1}


def test_api_response_read_is_limited_to_configured_bound_plus_one() -> None:
    response = Response(b"x" * 7)

    with pytest.raises(ApiError, match="invalid_api_response"):
        HttpClient(
            "https://worker.example.test/v1",
            "worker-secret",
            opener=lambda *_args, **_kwargs: response,
        ).post_json("claim", {}, response_max_bytes=6)

    assert response.read_sizes == [7]


def test_claim_contract_error_retains_only_the_static_parser_diagnostic() -> None:
    def opener(_request, *, timeout: float):
        return Response(b'{"empty":false,"job":{}}')

    with pytest.raises(ApiError) as raised:
        HttpClient("https://worker.example.test/v1", "worker-secret", opener=opener).claim_job(
            worker_build="worker-build",
            lease_seconds=120,
        )

    assert (raised.value.code, raised.value.retryable) == ("invalid_api_response", False)
    assert raised.value.diagnostic == "ContractError: invalid claimed job"


def test_download_streams_no_more_than_declared_bound_and_validates_content_type(
    tmp_path: Path,
) -> None:
    calls = []

    def opener(request, *, timeout: float):
        calls.append(request)
        return Response(b"abcdef", headers={"Content-Type": "image/jpeg", "Content-Length": "6"})

    destination = tmp_path / "input.jpg"
    client = HttpClient("https://worker.example.test/v1", "worker-secret", opener=opener)

    downloaded = client.download(
        "https://storage.example.test/x?signature=secret",
        destination,
        max_bytes=6,
        expected_size=6,
    )

    assert downloaded == 6
    assert destination.read_bytes() == b"abcdef"
    assert calls[0].get_header("Authorization") is None


def test_download_rejects_oversized_and_wrong_content_type_without_leaving_output(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "input.jpg"

    def oversized(_request, *, timeout: float):
        return Response(b"abcdef", headers={"Content-Type": "image/jpeg", "Content-Length": "7"})

    with pytest.raises(DownloadError, match="input_too_large"):
        HttpClient("https://worker.example.test/v1", "worker-secret", opener=oversized).download(
            "https://storage.example.test/x", destination, max_bytes=6, expected_size=6
        )
    assert not destination.exists()

    def wrong_type(_request, *, timeout: float):
        return Response(b"abc", headers={"Content-Type": "image/png"})

    with pytest.raises(DownloadError, match="unsupported_input"):
        HttpClient("https://worker.example.test/v1", "worker-secret", opener=wrong_type).download(
            "https://storage.example.test/x", destination, max_bytes=6, expected_size=3
        )


def test_download_rejects_a_mismatched_available_source_fingerprint(tmp_path: Path) -> None:
    destination = tmp_path / "input.jpg"

    def opener(_request, *, timeout: float):
        return Response(
            b"abc",
            headers={"Content-Type": "image/jpeg", "ETag": '"different"'},
        )

    with pytest.raises(DownloadError, match="fingerprint_mismatch"):
        HttpClient("https://worker.example.test/v1", "worker-secret", opener=opener).download(
            "https://storage.example.test/x",
            destination,
            max_bytes=6,
            expected_size=3,
            expected_etag="expected",
        )
    assert not destination.exists()


def test_download_requires_etag_when_the_immutable_fingerprint_supplies_one(tmp_path: Path) -> None:
    destination = tmp_path / "input.jpg"

    def missing(_request, *, timeout: float):
        return Response(b"abc", headers={"Content-Type": "image/jpeg"})

    with pytest.raises(DownloadError, match="fingerprint_mismatch"):
        HttpClient("https://worker.example.test/v1", "worker-secret", opener=missing).download(
            "https://storage.example.test/x",
            destination,
            max_bytes=3,
            expected_size=3,
            expected_etag="expected",
        )
    assert not destination.exists()

    def quoted(_request, *, timeout: float):
        return Response(b"abc", headers={"Content-Type": "image/jpeg", "ETag": '"expected"'})

    assert (
        HttpClient("https://worker.example.test/v1", "worker-secret", opener=quoted).download(
            "https://storage.example.test/x",
            destination,
            max_bytes=3,
            expected_size=3,
            expected_etag='"expected"',
        )
        == 3
    )


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "download_authorization_expired", True),
        (403, "download_authorization_expired", True),
        (404, "storage_unavailable", True),
        (408, "network_interruption", True),
        (429, "network_interruption", True),
        (503, "storage_unavailable", True),
    ],
)
def test_download_http_failures_use_only_django_accepted_pairs(
    tmp_path: Path, status: int, code: str, retryable: bool
) -> None:
    closed = []

    def opener(_request, *, timeout: float):
        error = HTTPError("https://storage.example.test/x?secret", status, "x", {}, io.BytesIO())
        closed.append(error)
        raise error

    with pytest.raises(DownloadError) as raised:
        HttpClient("https://worker.example.test/v1", "worker-secret", opener=opener).download(
            "https://storage.example.test/x",
            tmp_path / "input.jpg",
            max_bytes=6,
            expected_size=6,
        )

    assert (raised.value.code, raised.value.retryable) == (code, retryable)
    assert closed[0].fp is not None and closed[0].fp.closed


def test_download_rejects_short_or_partial_response_and_unlinks_destination(tmp_path: Path) -> None:
    destination = tmp_path / "input.jpg"

    def short(_request, *, timeout: float):
        return Response(b"abc", headers={"Content-Type": "image/jpeg", "Content-Length": "3"})

    with pytest.raises(DownloadError, match="fingerprint_mismatch"):
        HttpClient("https://worker.example.test/v1", "worker-secret", opener=short).download(
            "https://storage.example.test/x", destination, max_bytes=6, expected_size=6
        )
    assert not destination.exists()

    with pytest.raises(DownloadError, match="network_interruption"):
        HttpClient(
            "https://worker.example.test/v1",
            "worker-secret",
            opener=lambda *_args, **_kwargs: PartialResponse(),
        ).download("https://storage.example.test/x", destination, max_bytes=6, expected_size=6)
    assert not destination.exists()


def test_refresh_rejects_any_payload_except_the_exact_django_shape() -> None:
    def opener(_request, *, timeout: float):
        return Response(b'{"download_url":"https://storage.example.test/x?secret"}')

    with pytest.raises(ApiError, match="invalid_api_response") as raised:
        HttpClient(
            "https://worker.example.test/v1", "worker-secret", opener=opener
        ).refresh_download("attempt-1")

    assert raised.value.diagnostic == "api:refresh_download_contract_mismatch"


def test_refresh_accepts_the_exact_django_response_with_utc_offset() -> None:
    def opener(_request, *, timeout: float):
        return Response(
            b"""{
                "attempt": {
                    "id": "attempt-1",
                    "status": "in_progress",
                    "lease_expires_at": "2026-07-29T10:03:00+00:00"
                },
                "download_url": "https://storage.example.test/x?secret",
                "download_expires_at": "2026-07-29T10:01:00+00:00"
            }"""
        )

    assert (
        HttpClient(
            "https://worker.example.test/v1", "worker-secret", opener=opener
        ).refresh_download("attempt-1")
        == "https://storage.example.test/x?secret"
    )


@pytest.mark.parametrize(
    ("status", "code", "retryable", "diagnostic"),
    [
        (401, "worker_unauthorized", False, "http:unauthorized"),
        (403, "worker_unauthorized", False, "http:unauthorized"),
        (404, "invalid_api_response", False, "http:unexpected_client_status"),
        (409, "lease_not_current", False, "http:lease_conflict"),
        (503, "storage_unavailable", True, "http:server_error"),
    ],
)
def test_api_http_classification_has_only_an_allowlisted_category(
    status: int, code: str, retryable: bool, diagnostic: str
) -> None:
    def opener(_request, *, timeout: float):
        raise HTTPError(
            "https://worker.example.test/internal?token=secret", status, "x", {}, io.BytesIO()
        )

    with pytest.raises(ApiError) as raised:
        HttpClient("https://worker.example.test/v1", "worker-secret", opener=opener).post_json(
            "claim", {}
        )

    assert (raised.value.code, raised.value.retryable, raised.value.diagnostic) == (
        code,
        retryable,
        diagnostic,
    )


def test_preview_upload_uses_exact_put_content_type_and_bounded_response(tmp_path: Path) -> None:
    source = tmp_path / "preview.jpg"
    source.write_bytes(b"preview-bytes")
    requests = []
    response = Response(b"", headers={})

    def opener(request, *, timeout: float):
        requests.append(request)
        return response

    HttpClient("https://worker.example.test/v1", "worker-secret", opener=opener).upload_preview(
        "https://storage.example.test/put?signature=secret",
        source,
        content_type="image/jpeg",
        expected_size=len(b"preview-bytes"),
        max_bytes=100,
        response_max_bytes=8,
    )

    request = requests[0]
    assert request.get_method() == "PUT"
    assert request.get_header("Content-type") == "image/jpeg"
    assert request.get_header("Content-length") == str(len(b"preview-bytes"))
    assert request.get_header("Authorization") is None
    assert response.read_sizes == [9]


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "download_authorization_expired", True),
        (403, "download_authorization_expired", True),
        (503, "storage_unavailable", True),
    ],
)
def test_preview_upload_maps_grant_and_storage_failures_to_allowed_pairs(
    tmp_path: Path, status: int, code: str, retryable: bool
) -> None:
    source = tmp_path / "preview.jpg"
    source.write_bytes(b"preview")

    def opener(_request, *, timeout: float):
        raise HTTPError(
            "https://storage.example.test/put?signature=secret", status, "hostile", {}, io.BytesIO()
        )

    with pytest.raises(UploadError) as raised:
        HttpClient("https://worker.example.test/v1", "worker-secret", opener=opener).upload_preview(
            "https://storage.example.test/put?signature=secret",
            source,
            content_type="image/jpeg",
            expected_size=7,
            max_bytes=100,
        )

    assert (raised.value.code, raised.value.retryable) == (code, retryable)


def test_preview_upload_rejects_interruption_and_oversized_response_without_exposing_url(
    tmp_path: Path,
) -> None:
    source = tmp_path / "preview.jpg"
    source.write_bytes(b"preview")

    with pytest.raises(UploadError, match="network_interruption"):
        HttpClient(
            "https://worker.example.test/v1",
            "worker-secret",
            opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("hostile https://storage.example.test/put?secret")
            ),
        ).upload_preview(
            "https://storage.example.test/put?signature=secret",
            source,
            content_type="image/jpeg",
            expected_size=7,
            max_bytes=100,
        )

    with pytest.raises(UploadError, match="network_interruption"):
        HttpClient(
            "https://worker.example.test/v1",
            "worker-secret",
            opener=lambda *_args, **_kwargs: Response(b"too-long"),
        ).upload_preview(
            "https://storage.example.test/put?signature=secret",
            source,
            content_type="image/jpeg",
            expected_size=7,
            max_bytes=100,
            response_max_bytes=3,
        )
