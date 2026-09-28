"""Strict, bounded canonical cloud transport. Never installed on a worker container."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

COMPUTE = "https://compute.api.cloud.yandex.net/compute/v1"
MONITORING = "https://monitoring.api.cloud.yandex.net/monitoring/v2/data/write"
METADATA = "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token"
MAX_BODY = 1_048_576
TIMEOUT = 10


def identifier(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value) is None:
        raise ValueError("invalid resource identifier")
    return value


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_bytes(request: Request, *, max_body: int = MAX_BODY) -> bytes:
    # Default TLS context; no environment proxy or redirect can move bearer credentials.
    with build_opener(ProxyHandler({}), RejectRedirects()).open(
        request, timeout=TIMEOUT
    ) as response:
        if response.status != 200:
            raise ValueError("cloud request failed")
        raw = response.read(max_body + 1)
    if len(raw) > max_body:
        raise ValueError("cloud response too large")
    return raw


def request_json(request: Request) -> dict[str, Any]:
    raw = request_bytes(request)
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("cloud response invalid")
    return result


def metadata_token() -> str:
    payload = request_json(Request(METADATA, headers={"Metadata-Flavor": "Google"}))
    token = payload.get("access_token")
    if (
        not isinstance(token, str)
        or not token
        or not token.isascii()
        or any(c.isspace() for c in token)
        or type(payload.get("expires_in")) is not int
        or not 0 < payload["expires_in"] <= 86_400
    ):
        raise ValueError("metadata identity unavailable")
    return token


class CloudReader:
    def __init__(self, token: str):
        self.token = token

    def get(self, path: str, **parameters: str) -> dict[str, Any]:
        return request_json(
            Request(
                f"{COMPUTE}/{path}?{urlencode(parameters)}",
                headers={"Authorization": f"Bearer {self.token}"},
            )
        )

    def pages(self, path: str, key: str, **parameters: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        seen: set[str] = set()
        for _ in range(10):
            payload = self.get(path, **parameters, pageSize="100", pageToken=token)
            if set(payload) - {key, "nextPageToken"}:
                raise ValueError("invalid cloud page envelope")
            items = payload.get(key, [])
            if not isinstance(items, list) or any(not isinstance(row, dict) for row in items):
                raise ValueError("invalid cloud page")
            rows.extend(items)
            token = payload.get("nextPageToken", "")
            if not isinstance(token, str) or len(token) > 1024 or token in seen:
                raise ValueError("invalid cloud pagination")
            if not token:
                return rows
            seen.add(token)
        raise ValueError("cloud pagination incomplete")


def write_metrics(folder_id: str, metrics: list[dict[str, object]]) -> None:
    token = metadata_token()
    result = request_json(
        Request(
            f"{MONITORING}?{urlencode({'folderId': identifier(folder_id), 'service': 'custom'})}",
            data=json.dumps({"metrics": metrics}, separators=(",", ":")).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
    )
    if result.get("writtenMetricsCount") != str(len(metrics)) or result.get("errorMessage", ""):
        raise ValueError("incomplete metric publication")
