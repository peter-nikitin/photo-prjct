"""Minimal stdlib HTTP client for the private worker API and signed downloads."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from photo_worker.contracts import (
    MAX_JSON_FIELD_BYTES,
    PROCESSOR_TYPE,
    Claim,
    ContractError,
    _download_url,
    _processor_version,
    _utc_timestamp,
)

BOOTSTRAP_RESPONSE_MAX_BYTES = MAX_JSON_FIELD_BYTES
DOWNLOAD_CHUNK_BYTES = 64 * 1024
_CONTRACT_ERROR_DIAGNOSTICS = {
    "invalid claim response": "ContractError: invalid claim response",
    "invalid empty claim response": "ContractError: invalid empty claim response",
    "invalid empty-claim delay": "ContractError: invalid empty-claim delay",
    "invalid claimed response": "ContractError: invalid claimed response",
    "invalid claimed job": "ContractError: invalid claimed job",
    "invalid processor configuration": "ContractError: invalid processor configuration",
    "invalid preview output slot": "ContractError: invalid preview output slot",
    "invalid selfie input fingerprint": "ContractError: invalid selfie input fingerprint",
    "invalid input fingerprint": "ContractError: invalid input fingerprint",
    "invalid input limits": "ContractError: invalid input limits",
    "unsupported claimed job": "ContractError: unsupported claimed job",
    "unsupported processor type": "ContractError: unsupported processor type",
}
_HTTP_ERROR_DIAGNOSTICS = {
    401: "http:unauthorized",
    403: "http:unauthorized",
    409: "http:lease_conflict",
}


class Response(Protocol):
    headers: Any

    def read(self, size: int = -1) -> bytes: ...

    def __enter__(self) -> Response: ...

    def __exit__(self, *args: object) -> None: ...


OpenUrl = Callable[..., Response]


class ApiError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool, diagnostic: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.diagnostic = diagnostic


class DownloadError(ApiError):
    pass


class UploadError(ApiError):
    pass


class HttpClient:
    def __init__(
        self,
        api_url: str,
        token: str,
        *,
        timeout_seconds: float = 20.0,
        opener: OpenUrl = urlopen,
    ) -> None:
        if not api_url.startswith(("http://", "https://")) or not token:
            raise ValueError("worker API URL and token are required")
        self._api_url = api_url.rstrip("/")
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._opener = opener

    def post_json(
        self,
        path: str,
        payload: dict[str, object],
        *,
        response_max_bytes: int = BOOTSTRAP_RESPONSE_MAX_BYTES,
    ) -> dict[str, Any]:
        if not 0 < response_max_bytes <= MAX_JSON_FIELD_BYTES:
            raise ValueError("response_max_bytes must be a bounded positive integer")
        body = json.dumps(payload, separators=(",", ":")).encode()
        request = Request(
            f"{self._api_url}/{path.lstrip('/')}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                raw = _read_bounded(response, response_max_bytes)
        except HTTPError as error:
            error.close()
            raise _api_error(error.code) from None
        except URLError:
            raise ApiError("network_interruption", retryable=True) from None
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError("invalid_api_response", retryable=True) from None
        if not isinstance(value, dict):
            raise ApiError("invalid_api_response", retryable=True)
        return value

    def claim_job(
        self,
        *,
        worker_build: str,
        lease_seconds: int,
        processor_type: str = PROCESSOR_TYPE,
        processor_version: int | None = None,
        contract_version: int = 1,
    ) -> Claim:
        try:
            return Claim.from_response(
                self.post_json(
                    "claim",
                    {
                        "contract_version": contract_version,
                        "processor_type": processor_type,
                        "processor_version": (
                            _processor_version(processor_type, contract_version)
                            if processor_version is None
                            else processor_version
                        ),
                        "worker_build": worker_build,
                        "lease_seconds": lease_seconds,
                    },
                )
            )
        except ContractError as error:
            raise ApiError(
                "invalid_api_response",
                retryable=False,
                diagnostic=_contract_error_diagnostic(error),
            ) from error

    def heartbeat(
        self,
        attempt_id: str,
        *,
        lease_seconds: int,
        response_max_bytes: int = BOOTSTRAP_RESPONSE_MAX_BYTES,
    ) -> None:
        self.post_json(
            f"attempts/{attempt_id}/heartbeat",
            {"lease_seconds": lease_seconds},
            response_max_bytes=response_max_bytes,
        )

    def refresh_download(
        self, attempt_id: str, *, response_max_bytes: int = BOOTSTRAP_RESPONSE_MAX_BYTES
    ) -> str:
        response = self.post_json(
            f"attempts/{attempt_id}/download", {}, response_max_bytes=response_max_bytes
        )
        url = response.get("download_url")
        attempt = response.get("attempt")
        if not (
            set(response) == {"attempt", "download_url", "download_expires_at"}
            and isinstance(attempt, dict)
            and set(attempt) == {"id", "status", "lease_expires_at"}
            and attempt.get("id") == attempt_id
            and attempt.get("status") == "in_progress"
            and _utc_timestamp(attempt.get("lease_expires_at"))
            and _download_url(url)
            and _utc_timestamp(response.get("download_expires_at"))
        ):
            raise ApiError("invalid_api_response", retryable=False)
        assert isinstance(url, str)
        return url

    def complete(
        self,
        attempt_id: str,
        payload: dict[str, object],
        *,
        response_max_bytes: int = BOOTSTRAP_RESPONSE_MAX_BYTES,
    ) -> None:
        self.post_json(
            f"attempts/{attempt_id}/complete", payload, response_max_bytes=response_max_bytes
        )

    def fail(
        self,
        attempt_id: str,
        payload: dict[str, object],
        *,
        response_max_bytes: int = BOOTSTRAP_RESPONSE_MAX_BYTES,
    ) -> None:
        self.post_json(
            f"attempts/{attempt_id}/fail", payload, response_max_bytes=response_max_bytes
        )

    def download(
        self,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
        expected_size: int,
        expected_content_type: str = "image/jpeg",
        expected_etag: str | None = None,
    ) -> int:
        if expected_size < 1 or expected_size > max_bytes:
            raise DownloadError("input_too_large", retryable=False)
        if expected_content_type not in {"image/jpeg", "image/png"}:
            raise DownloadError("unsupported_input", retryable=False)
        request = Request(url, method="GET", headers={"Accept": expected_content_type})
        written = 0
        completed = False
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                content_type = _header(response.headers, "Content-Type").split(";", 1)[0].lower()
                content_length = _header(response.headers, "Content-Length")
                response_etag = _header(response.headers, "ETag").strip('"')
                if content_type != expected_content_type:
                    raise DownloadError("unsupported_input", retryable=False)
                if content_length:
                    if not content_length.isdecimal() or int(content_length) > max_bytes:
                        raise DownloadError("input_too_large", retryable=False)
                    if int(content_length) != expected_size:
                        raise DownloadError("fingerprint_mismatch", retryable=False)
                if expected_etag is not None and (
                    not response_etag or response_etag != expected_etag.strip('"')
                ):
                    raise DownloadError("fingerprint_mismatch", retryable=False)
                with destination.open("wb") as output:
                    while chunk := response.read(
                        min(DOWNLOAD_CHUNK_BYTES, max_bytes + 1 - written)
                    ):
                        written += len(chunk)
                        if written > max_bytes:
                            raise DownloadError("input_too_large", retryable=False)
                        output.write(chunk)
            if written != expected_size:
                raise DownloadError("fingerprint_mismatch", retryable=False)
            completed = True
        except HTTPError as error:
            error.close()
            raise _download_error(error.code) from None
        except URLError:
            raise DownloadError("network_interruption", retryable=True) from None
        except OSError:
            raise DownloadError("network_interruption", retryable=True) from None
        finally:
            if not completed:
                destination.unlink(missing_ok=True)
        return written

    def upload_preview(
        self,
        url: str,
        source: Path,
        *,
        content_type: str,
        expected_size: int,
        max_bytes: int,
        response_max_bytes: int = BOOTSTRAP_RESPONSE_MAX_BYTES,
    ) -> None:
        """PUT exactly one locally-created preview through its short-lived grant."""
        if (
            content_type != "image/jpeg"
            or expected_size < 1
            or expected_size > max_bytes
            or not 0 < response_max_bytes <= MAX_JSON_FIELD_BYTES
        ):
            raise UploadError("output_contract_violation", retryable=False)
        try:
            if source.stat().st_size != expected_size:
                raise UploadError("output_contract_violation", retryable=False)
            with source.open("rb") as body:
                request = Request(
                    url,
                    data=body,
                    method="PUT",
                    headers={
                        "Content-Type": content_type,
                        "Content-Length": str(expected_size),
                    },
                )
                with self._opener(request, timeout=self._timeout_seconds) as response:
                    if len(response.read(response_max_bytes + 1)) > response_max_bytes:
                        raise UploadError("network_interruption", retryable=True)
        except UploadError:
            raise
        except HTTPError as error:
            error.close()
            raise _upload_error(error.code) from None
        except (URLError, OSError):
            raise UploadError("network_interruption", retryable=True) from None


def _read_bounded(response: Response, maximum: int) -> bytes:
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ApiError("invalid_api_response", retryable=True)
    return data


def _header(headers: Any, name: str) -> str:
    value = headers.get(name, "")
    return value if isinstance(value, str) else ""


def _api_error(status: int) -> ApiError:
    if status in {401, 403}:
        return ApiError(
            "worker_unauthorized", retryable=False, diagnostic=_http_error_diagnostic(status)
        )
    if status == 409:
        return ApiError(
            "lease_not_current", retryable=False, diagnostic=_http_error_diagnostic(status)
        )
    if status >= 500:
        return ApiError("storage_unavailable", retryable=True, diagnostic="http:server_error")
    return ApiError(
        "invalid_api_response", retryable=False, diagnostic="http:unexpected_client_status"
    )


def _http_error_diagnostic(status: int) -> str:
    """Return a static HTTP category without exposing a status body or request data."""
    return _HTTP_ERROR_DIAGNOSTICS.get(status, "http:unexpected_client_status")


def _contract_error_diagnostic(error: ContractError) -> str:
    """Expose only an allowlisted parser category, never a server response fragment."""
    return _CONTRACT_ERROR_DIAGNOSTICS.get(
        str(error), "ContractError: unrecognized schema violation"
    )


def _download_error(status: int) -> DownloadError:
    if status in {401, 403}:
        return DownloadError("download_authorization_expired", retryable=True)
    if status in {408, 429}:
        return DownloadError("network_interruption", retryable=True)
    if status >= 500 or status == 404:
        return DownloadError("storage_unavailable", retryable=True)
    return DownloadError("network_interruption", retryable=True)


def _upload_error(status: int) -> UploadError:
    if status in {401, 403}:
        # Version 2's server contract exposes this stable retryable code for any short-lived
        # object-storage grant that expired while the attempt lease remained current.
        return UploadError("download_authorization_expired", retryable=True)
    if status >= 500:
        return UploadError("storage_unavailable", retryable=True)
    return UploadError("network_interruption", retryable=True)
