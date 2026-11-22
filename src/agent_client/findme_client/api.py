import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, BinaryIO
from urllib.parse import urlsplit

import httpx

Json = dict[str, Any]
RETRYABLE = {408, 429, 500, 502, 503, 504}


class ClientError(Exception):
    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status

    def payload(self) -> Json:
        return {"error": {"code": self.code, "message": str(self), "status": self.status}}


class Api:
    def __init__(self, server: str, client: httpx.Client) -> None:
        self.server = server
        self.client = client

    @classmethod
    @contextmanager
    def connect(cls, server: str, token: str) -> Iterator["Api"]:
        with httpx.Client(
            base_url=server + "/api/agent/v1/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30, connect=10),
        ) as client:
            yield cls(server, client)

    def request(
        self,
        path: str,
        *,
        data: Json | None = None,
        page: int | None = None,
        attempts: int = 3,
    ) -> Json:
        for attempt in range(attempts):
            try:
                response = (
                    self.client.get(path, params={"page": page} if page is not None else None)
                    if data is None
                    else self.client.post(path, json=data)
                )
            except httpx.TransportError:
                failure = ClientError("network_error", "Cannot reach the FindMe Photo API.")
            else:
                result = json_object(response)
                if response.is_success:
                    if result is not None:
                        return result
                    raise ClientError("invalid_response", "The API returned invalid JSON.")
                raw = (result or {}).get("error")
                code = raw.get("code") if isinstance(raw, dict) else None
                if not isinstance(code, str) or not re.fullmatch(r"[a-z_]{1,64}", code):
                    code = "api_error"
                failure = ClientError(
                    code,
                    f"FindMe Photo API returned HTTP {response.status_code} ({code}).",
                    status=response.status_code,
                )
            if not retryable(failure) or attempt + 1 == attempts:
                raise failure
            backoff(attempt)
        raise AssertionError("At least one attempt is required")


def retryable(failure: ClientError) -> bool:
    return failure.status is None or failure.status in RETRYABLE


def secure_destination(value: str) -> bool:
    url = urlsplit(value)
    return (
        url.scheme == "https"
        or url.scheme == "http"
        and url.hostname in {"localhost", "127.0.0.1", "::1"}
    )


def json_object(response: httpx.Response) -> Json | None:
    try:
        value = response.json()
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def backoff(attempt: int) -> None:
    time.sleep(0.5 * 2**attempt)


def send_original(client: httpx.Client, grant: Json, filename: str, file: BinaryIO) -> None:
    if not secure_destination(grant["url"]):
        raise ClientError("insecure_grant", "Upload storage must use HTTPS.")
    try:
        response = client.post(
            grant["url"],
            data=grant["fields"],
            files={"file": (filename, file, "image/jpeg")},
            timeout=httpx.Timeout(90, connect=10),
        )
    except httpx.TransportError:
        raise ClientError("storage_network_error", "Cannot reach upload storage.") from None
    if not response.is_success:
        raise ClientError(
            "storage_rejected",
            f"Upload storage returned HTTP {response.status_code}.",
            status=response.status_code,
        )
