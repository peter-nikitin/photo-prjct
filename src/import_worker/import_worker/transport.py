"""Bounded HTTP, no proxies/cookies/automatic redirects, with pinned public TLS peers."""

from __future__ import annotations

import http.client
import ipaddress
import json
import queue
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

from import_worker.contracts import Config

Audience = Literal["source", "storage", "api"]


class TransportError(Exception):
    def __init__(
        self, *, retryable: bool = False, retry_after: float = 0, status: int = 0, code: str = ""
    ):
        super().__init__("Outbound request failed.")
        self.retryable = retryable
        self.retry_after = retry_after
        self.status = status
        self.code = code


def validate_source_url(url: str) -> None:
    parsed = _url(url)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.port not in {None, 443}
        or not (
            host in {"cloud-api.yandex.net", "downloader.disk.yandex.ru"}
            or host.endswith(".storage.yandex.net")
        )
    ):
        raise TransportError()


def _url(url: str):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise TransportError() from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port == 0
        or any(ord(char) <= 32 or ord(char) == 127 for char in url)
    ):
        raise TransportError()
    return parsed


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: tuple[Any, ...], family: int, *, timeout: float):
        self.context = ssl.create_default_context()
        super().__init__(host, port=443, timeout=timeout, context=self.context)
        self.address = address
        self.family = family

    def connect(self) -> None:
        raw = socket.socket(self.family, socket.SOCK_STREAM)
        try:
            self.sock = raw
            raw.settimeout(self.timeout)
            raw.connect(self.address)
            self.sock = self.context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


def retry_after_seconds(value: str) -> float:
    try:
        if value.isascii() and value.isdecimal() and len(value) <= 10:
            return float(value)
        return max(0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return 0


class Transport:
    def connection(self, url: str, *, public: bool, timeout: float) -> http.client.HTTPConnection:
        parsed = _url(url)
        host = parsed.hostname
        assert host is not None
        if not public:
            cls = (
                http.client.HTTPSConnection
                if parsed.scheme == "https"
                else http.client.HTTPConnection
            )
            return cls(host, parsed.port, timeout=timeout)
        if parsed.scheme != "https" or parsed.port not in {None, 443}:
            raise TransportError()
        answers: queue.Queue[Any] = queue.Queue(maxsize=1)

        def resolve() -> None:
            try:
                answers.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
            except OSError as error:
                answers.put(error)

        resolver = threading.Thread(target=resolve, daemon=True)
        resolver.start()
        try:
            addresses = answers.get(timeout=min(timeout, Config.io_seconds))
        except queue.Empty:
            raise TransportError(retryable=True) from None
        if isinstance(addresses, OSError):
            raise TransportError(retryable=True) from None
        if not addresses:
            raise TransportError(retryable=True)
        # Reject mixed public/private answers too; connect to the checked sockaddr directly.
        for _, _, _, _, address in addresses:
            ip = ipaddress.ip_address(address[0])
            if (
                not ip.is_global
                or ip.is_multicast
                or ip.is_unspecified
                or (
                    isinstance(ip, ipaddress.IPv6Address)
                    and (ip.is_site_local or ip.sixtofour is not None or ip.teredo is not None)
                )
            ):
                raise TransportError()
        family, _, _, _, address = addresses[0]
        return PinnedHTTPSConnection(host, address, family, timeout=timeout)

    def request(
        self,
        method: str,
        url: str,
        *,
        audience: Audience,
        headers: Mapping[str, str] | None = None,
        body: bytes | Iterator[bytes] | None = None,
        limit: int = Config.json_bytes,
        sink: Callable[[bytes], object] | None = None,
        heartbeat: Callable[[], None] = lambda: None,
    ) -> Response:
        if (
            audience != "api"
            and headers
            and any(
                name.lower() in {"authorization", "cookie", "proxy-authorization"}
                for name in headers
            )
        ):
            raise TransportError()
        deadline = time.monotonic() + Config.request_seconds
        for hop in range(6):
            heartbeat()
            if audience == "source":
                validate_source_url(url)
            parsed = _url(url)
            connection = self.connection(url, public=audience != "api", timeout=Config.io_seconds)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                connection.close()
                raise TransportError(retryable=True)
            # A whole-exchange watchdog interrupts slow-drip response headers and blocked writes.
            connected_socket: list[socket.socket] = []

            def abort(
                conn: http.client.HTTPConnection = connection,
                sockets: list[socket.socket] = connected_socket,
            ) -> None:
                sock = sockets[0] if sockets else conn.sock
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    sock.close()

            watchdog = threading.Timer(remaining, abort)
            watchdog.daemon = True
            watchdog.start()
            try:
                path = parsed.path or "/"
                if parsed.query:
                    path += "?" + parsed.query
                connection.request(method, path, body=body, headers=dict(headers or {}))
                if connection.sock is not None:
                    connected_socket.append(connection.sock)
                response = connection.getresponse()
                response_headers = {key.lower(): value for key, value in response.getheaders()}
                if response.status in {301, 302, 303, 307, 308}:
                    if audience != "source" or method != "GET" or hop == 5:
                        raise TransportError()
                    location = response_headers.get("location")
                    if not location:
                        raise TransportError()
                    url = urljoin(url, location)
                    validate_source_url(url)
                    continue
                if audience != "api" and (response.status < 200 or response.status >= 300):
                    raise TransportError(
                        retryable=response.status in {408, 429}
                        or response.status >= 500
                        or (audience != "api" and response.status in {401, 403}),
                        retry_after=retry_after_seconds(response_headers.get("retry-after", "")),
                        status=response.status,
                    )
                declared = response_headers.get("content-length")
                if declared is not None:
                    if not declared.isdecimal() or len(declared) > 12:
                        raise TransportError()
                    if int(declared) > limit:
                        raise TransportError(code="file_too_large" if sink is not None else "")
                count = 0
                chunks = []
                while True:
                    heartbeat()
                    if time.monotonic() >= deadline:
                        raise TransportError(retryable=True)
                    chunk = response.read1(min(65536, limit - count + 1))
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > limit:
                        raise TransportError(code="file_too_large" if sink is not None else "")
                    if sink is None:
                        chunks.append(chunk)
                    else:
                        sink(chunk)
                if declared is not None and int(declared) != count:
                    raise TransportError(retryable=True)
                return Response(response.status, response_headers, b"".join(chunks))
            except (OSError, http.client.HTTPException, ValueError):
                raise TransportError(retryable=True) from None
            finally:
                watchdog.cancel()
                connection.close()
        raise TransportError()

    def json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = self.request(method, url, **kwargs)
        try:
            result = json.loads(response.body)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise TransportError() from None
        if not isinstance(result, dict):
            raise TransportError()
        if response.status < 200 or response.status >= 300:
            error = result.get("error", {})
            allowed_codes = {
                "invalid_jpeg",
                "file_too_large",
                "source_changed",
                "hash_mismatch",
                "manifest_changed",
                "manifest_too_large",
                "source_not_directory",
            }
            code = error.get("code") if isinstance(error, dict) else None
            raise TransportError(
                status=response.status,
                retryable=response.status in {408, 429} or response.status >= 500,
                retry_after=retry_after_seconds(response.headers.get("retry-after", "")),
                code=code if code in allowed_codes else "",
            )
        return result

    def upload(
        self, url: str, fields: dict[str, str], path: Path, *, heartbeat: Callable[[], None]
    ) -> None:
        boundary = uuid4().hex
        preamble = bytearray()
        for name, value in fields.items():
            if any(char in name for char in '\r\n"'):
                raise TransportError()
            preamble.extend(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                    f"\r\n\r\n{value}\r\n"
                ).encode()
            )
        preamble.extend(
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="photo.jpg"\r\n'
                "Content-Type: image/jpeg\r\n\r\n"
            ).encode()
        )
        ending = f"\r\n--{boundary}--\r\n".encode()

        def chunks() -> Iterator[bytes]:
            yield bytes(preamble)
            with path.open("rb") as stream:
                while chunk := stream.read(65536):
                    heartbeat()
                    yield chunk
            yield ending

        self.request(
            "POST",
            url,
            audience="storage",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(preamble) + path.stat().st_size + len(ending)),
            },
            body=chunks(),
            heartbeat=heartbeat,
        )
