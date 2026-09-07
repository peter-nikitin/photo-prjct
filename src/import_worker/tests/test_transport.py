import socket
from unittest.mock import Mock, patch

import pytest
from import_worker.transport import Transport, TransportError, validate_source_url


@pytest.mark.parametrize(
    "url",
    [
        "http://downloader.disk.yandex.ru/a",
        "https://downloader.disk.yandex.ru.evil.test/a",
        "https://evil.test/a",
        "https://user@downloader.disk.yandex.ru/a",
        "https://downloader.disk.yandex.ru:444/a",
        "https://127.0.0.1/a",
        "https://storage.yandex.net.evil.test/a",
    ],
)
def test_rejects_untrusted_source_url(url):
    with pytest.raises(TransportError):
        validate_source_url(url)


def test_accepts_observed_downloader_redirect_host():
    validate_source_url("https://downloader.disk.yandex.ru/a")
    validate_source_url("https://s566klg.storage.yandex.net/a")


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "fc00::1", "fec0::1"]
)
def test_connection_refuses_nonpublic_dns_answers(address):
    with (
        patch(
            "socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
        ),
        patch("socket.socket") as sock,
    ):
        with pytest.raises(TransportError):
            Transport().request("GET", "https://downloader.disk.yandex.ru/a", audience="source")
        sock.assert_not_called()


def test_public_connection_pins_resolved_ip_and_keeps_tls_hostname():
    raw = Mock()
    context = Mock()
    wrapped = context.wrap_socket.return_value
    with (
        patch(
            "socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("77.88.21.90", 443))],
        ) as dns,
        patch("socket.socket", return_value=raw),
        patch("ssl.create_default_context", return_value=context),
    ):
        connection = Transport().connection(
            "https://downloader.disk.yandex.ru/a", public=True, timeout=5
        )
        connection.connect()
    dns.assert_called_once()
    raw.connect.assert_called_once_with(("77.88.21.90", 443))
    context.wrap_socket.assert_called_once_with(raw, server_hostname="downloader.disk.yandex.ru")
    assert connection.sock == wrapped


class Reply:
    def __init__(self, status=200, chunks=(), headers=()):
        self.status = status
        self.chunks = iter(chunks)
        self.headers = headers

    def getheaders(self):
        return self.headers

    def read1(self, size):
        return next(self.chunks, b"")


def connection_for(reply):
    conn = Mock()
    conn.getresponse.return_value = reply
    return conn


def test_each_redirect_is_validated_before_next_connection():
    transport = Transport()
    conn = connection_for(Reply(302, headers=[("Location", "https://evil.test/secret")]))
    with patch.object(transport, "connection", return_value=conn) as connect:
        with pytest.raises(TransportError):
            transport.request("GET", "https://downloader.disk.yandex.ru/a", audience="source")
    assert connect.call_count == 1
    conn.close.assert_called_once()


def test_observed_redirect_is_followed_without_tokens_or_cookies(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://secret-proxy:9000")
    transport = Transport()
    first = connection_for(
        Reply(
            302,
            headers=[
                ("Location", "https://s566klg.storage.yandex.net/a"),
                ("Set-Cookie", "secret=yes"),
            ],
        )
    )
    second = connection_for(Reply(chunks=[b"jpeg"]))
    with patch.object(transport, "connection", side_effect=[first, second]):
        assert (
            transport.request("GET", "https://downloader.disk.yandex.ru/a", audience="source").body
            == b"jpeg"
        )
    assert second.request.call_args.kwargs["headers"] == {}


@pytest.mark.parametrize("audience", ["storage", "source"])
def test_external_transport_refuses_private_credentials(audience):
    with pytest.raises(TransportError):
        Transport().request(
            "GET",
            "https://downloader.disk.yandex.ru/a",
            audience=audience,
            headers={"Authorization": "Bearer secret"},
        )


def test_s3_never_follows_redirect():
    transport = Transport()
    with patch.object(
        transport,
        "connection",
        return_value=connection_for(Reply(307, headers=[("Location", "https://evil.test/a")])),
    ):
        with pytest.raises(TransportError):
            transport.request("POST", "https://storage.yandexcloud.net/a", audience="storage")


def test_streamed_limit_stops_before_writing_extra_bytes():
    transport = Transport()
    received = []
    with patch.object(
        transport, "connection", return_value=connection_for(Reply(chunks=[b"123", b"456"]))
    ):
        with pytest.raises(TransportError):
            transport.request(
                "GET",
                "https://downloader.disk.yandex.ru/a",
                audience="source",
                limit=5,
                sink=received.append,
            )
    assert received == [b"123"]


def test_throttling_carries_retry_after_and_is_retryable():
    transport = Transport()
    with patch.object(
        transport,
        "connection",
        return_value=connection_for(Reply(429, headers=[("Retry-After", "120")])),
    ):
        with pytest.raises(TransportError) as error:
            transport.request("GET", "https://downloader.disk.yandex.ru/a", audience="source")
    assert error.value.retryable
    assert error.value.retry_after == 120


def test_api_conflict_exposes_only_safe_code_and_retryability():
    transport = Transport()
    body = (
        b'{"contract_version":1,"error":{"code":"invalid_jpeg",'
        b'"retryable":false,"message":"secret storage location"}}'
    )
    with patch.object(
        transport, "connection", return_value=connection_for(Reply(409, chunks=[body]))
    ):
        with pytest.raises(TransportError) as error:
            transport.json("POST", "http://web/internal/photo-import/v1/claim", audience="api")
    assert error.value.code == "invalid_jpeg"
    assert not error.value.retryable
    assert "secret" not in str(error.value)


def test_dns_resolution_has_a_wall_clock_deadline():
    import time

    from import_worker.contracts import Config

    with (
        patch.object(Config, "io_seconds", 0.02),
        patch("socket.getaddrinfo", side_effect=lambda *args, **kwargs: time.sleep(0.2)),
    ):
        started = time.monotonic()
        with pytest.raises(TransportError):
            Transport().connection("https://downloader.disk.yandex.ru/a", public=True, timeout=0.02)
        assert time.monotonic() - started < 0.15


def test_whole_response_deadline_interrupts_slow_drip_headers():
    import threading
    import time

    from import_worker.contracts import Config

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve():
        conn, _ = listener.accept()
        try:
            conn.recv(4096)
            for byte in b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n":
                conn.send(bytes([byte]))
                time.sleep(0.03)
        except OSError:
            pass
        finally:
            conn.close()
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    with patch.object(Config, "request_seconds", 0.15):
        started = time.monotonic()
        with pytest.raises(TransportError):
            Transport().request("GET", f"http://127.0.0.1:{port}/", audience="api")
        assert time.monotonic() - started < 0.6
    thread.join(timeout=1)


def test_download_byte_limit_has_terminal_file_error():
    transport = Transport()
    with patch.object(
        transport, "connection", return_value=connection_for(Reply(chunks=[b"123456"]))
    ):
        with pytest.raises(TransportError) as error:
            transport.request(
                "GET",
                "https://downloader.disk.yandex.ru/a",
                audience="source",
                limit=5,
                sink=lambda chunk: None,
            )
    assert error.value.code == "file_too_large"
    assert not error.value.retryable
