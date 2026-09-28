from __future__ import annotations

import socket
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from photo_worker.client import ApiError, HttpClient
from photo_worker.runner import WorkerConfig

API = "https://findme-photo.ru:8443/internal/photo-processing/v1"


@pytest.mark.parametrize(
    "url",
    [
        "http://findme-photo.ru:8443/internal/photo-processing/v1",
        "https://other.test:8443/internal/photo-processing/v1",
        "https://findme-photo.ru/internal/photo-processing/v1",
        API + "?token=x",
        API + "/extra",
        "https://user@findme-photo.ru:8443/internal/photo-processing/v1",
    ],
)
def test_remote_client_rejects_any_noncanonical_endpoint(url):
    with pytest.raises(ValueError, match="remote"):
        HttpClient(url, "fleet-secret", transport="remote")


def test_remote_pool_startup_uses_literal_pool_identities_and_rejects_invalid_configuration(
    monkeypatch,
):
    values = {
        "PHOTO_WORKER_API_URL": API,
        "PHOTO_WORKER_TOKEN": "fleet-secret",
        "PHOTO_WORKER_TRANSPORT": "remote",
        "PHOTO_WORKER_POOL": "selfie",
        "WORKER_POOL_PRIVATE_API_IPV4": "10.20.30.40",
        "PHOTO_WORKER_BUILD": "a" * 40,
        "PHOTO_WORKER_IMAGE": "ghcr.io/example/photo-prjct-worker@sha256:" + "a" * 64,
        "PHOTO_WORKER_PROCESSOR_IDENTITIES": "1/selfie_query/2",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    config, _client = WorkerConfig.from_env()
    assert config.processor_identities == ("1/selfie_query/2",)
    for key, value in (
        ("PHOTO_WORKER_POOL", "other"),
        ("WORKER_POOL_PRIVATE_API_IPV4", "111.88.151.64"),
        ("WORKER_POOL_PRIVATE_API_IPV4", "127.0.0.1"),
        ("WORKER_POOL_PRIVATE_API_IPV4", "10.0.0.999"),
        ("WORKER_POOL_PRIVATE_API_IPV4", ""),
        ("PHOTO_WORKER_BUILD", "latest"),
        ("PHOTO_WORKER_IMAGE", "registry.test/photo-worker:latest"),
        ("PHOTO_WORKER_IMAGE", "cr.yandex/registry/worker@sha256:" + "a" * 64),
        ("PHOTO_WORKER_IMAGE", "ghcr.io/example/photo-prjct-worker@sha256:wrong"),
        ("PHOTO_WORKER_IMAGE", "ghcr.io/example/photo-prjct-worker:" + "a" * 40),
        ("PHOTO_WORKER_PROCESSOR_IDENTITIES", "1/capture_metadata/2"),
        ("PHOTO_WORKER_TOKEN", ""),
        ("PHOTO_WORKER_TOKEN", "fleet secret"),
        ("PHOTO_WORKER_TOKEN", "fleet\nsecret"),
    ):
        with monkeypatch.context() as patch:
            patch.setenv(key, value)
            with pytest.raises(ValueError):
                WorkerConfig.from_env()


@pytest.mark.parametrize(
    "scenario", ["trusted", "untrusted", "wrong-hostname", "redirect", "unauthorized"]
)
def test_remote_api_real_tls_verification_and_no_alternate_attempt(scenario, monkeypatch, tmp_path):
    """Removing trust/hostname verification or following a redirect leaks the bearer."""
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    name = "other.test" if scenario == "wrong-hostname" else "findme-photo.ru"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            f"/CN={name}",
            "-addext",
            f"subjectAltName=DNS:{name}",
        ],
        check=True,
        capture_output=True,
    )
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.headers.get("Authorization")))
            self.send_response(
                302 if scenario == "redirect" else 401 if scenario == "unauthorized" else 200
            )
            if scenario == "redirect":
                self.send_header("Location", "http://alternate.test/credential-sink")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_resolve = socket.getaddrinfo
    resolutions = []

    def resolve(host, port, *args, **kwargs):
        resolutions.append(host)
        assert host == "findme-photo.ru"
        return original_resolve("127.0.0.1", server.server_port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    for variable in (
        "https_proxy",
        "HTTPS_PROXY",
        "http_proxy",
        "HTTP_PROXY",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.delenv(variable, raising=False)
    if scenario != "untrusted":
        monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    else:
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    try:
        client = HttpClient(API, "fleet-secret", transport="remote", timeout_seconds=3)
        if scenario == "trusted":
            assert client.post_json("claim", {}) == {"ok": True}
        else:
            with pytest.raises(ApiError):
                client.post_json("claim", {})
        assert resolutions == ["findme-photo.ru"]
        assert requests == (
            []
            if scenario in {"untrusted", "wrong-hostname"}
            else [("/internal/photo-processing/v1/claim", "Bearer fleet-secret")]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
