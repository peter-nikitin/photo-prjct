"""Exercise the real Nginx image in bootstrap and certificate modes."""

import http.client
import ssl
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10"


def request(port, path, host, tls=False, headers=None):
    conn = (
        http.client.HTTPSConnection(
            "127.0.0.1", port, context=ssl._create_unverified_context(), timeout=5
        )
        if tls
        else http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    )
    conn.request("GET", path, headers={"Host": host, **(headers or {})})
    response = conn.getresponse()
    result = response.status, response.getheader("Location"), response.read()
    conn.close()
    return result


def private_response(port, path, host, tls=False):
    conn = (
        http.client.HTTPSConnection(
            "127.0.0.1", port, context=ssl._create_unverified_context(), timeout=5
        )
        if tls
        else http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    )
    conn.request("GET", path, headers={"Host": host})
    response = conn.getresponse()
    result = response.status, dict(response.getheaders())
    response.read()
    conn.close()
    return result


@pytest.mark.parametrize("tls", [False, True])
def test_edge_bootstrap_and_tls_routing(tmp_path: Path, tls: bool) -> None:
    certs = tmp_path / "certs"
    challenge = tmp_path / "acme"
    (certs / "live/photo-prjct").mkdir(parents=True)
    (challenge / ".well-known/acme-challenge").mkdir(parents=True)
    (challenge / ".well-known/acme-challenge/token").write_text("challenge")
    stub = tmp_path / "stub.conf"
    stub.write_text(
        "server { listen 8000; access_log off; "
        "if ($host != localhost) { return 400; } "
        "location = /health/ { if ($arg_down = 1) { return 503; } return 200; } }"
    )
    if tls:
        directory = certs / "live/photo-prjct"
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
                "-subj",
                "/CN=findme-photo.ru",
                "-keyout",
                str(directory / "privkey.pem"),
                "-out",
                str(directory / "fullchain.pem"),
            ],
            check=True,
            capture_output=True,
        )
    command = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--add-host",
        "web:127.0.0.1",
        "-p",
        "127.0.0.1::80",
        "-p",
        "127.0.0.1::443",
        "-p",
        "127.0.0.1::8081",
        "-v",
        f"{ROOT / 'deploy/nginx'}:/opt/edge:ro",
        "-v",
        f"{certs}:/etc/letsencrypt:ro",
        "-v",
        f"{challenge}:/var/www/certbot:ro",
        "-v",
        f"{stub}:/etc/nginx/conf.d/stub.conf:ro",
        "--entrypoint",
        "/bin/sh",
        IMAGE,
        "/opt/edge/edge-start.sh",
    ]
    container = subprocess.check_output(command, text=True).strip()
    try:

        def port(n):
            mapping = subprocess.check_output(["docker", "port", container, f"{n}/tcp"], text=True)
            return int(mapping.strip().rsplit(":", 1)[1])

        http_port, https_port, health_port = port(80), port(443), port(8081)
        for _ in range(40):
            try:
                status = request(http_port, "/.well-known/acme-challenge/token", "findme-photo.ru")
                break
            except OSError:
                time.sleep(0.1)
        assert status == (200, None, b"challenge")
        assert (
            request(http_port, "/internal/photo-processing/v1/claim", "findme-photo.ru")[0] == 404
        )
        assert request(health_port, "/", "localhost")[0] == 404
        assert request(health_port, "/health/", "nlb.invalid")[0] == 200
        assert request(health_port, "/health/?down=1", "nlb.invalid")[0] == 503
        if not tls:
            assert request(http_port, "/health/", "findme-photo.ru")[0] == 400
        if tls:
            assert request(http_port, "/", "findme-photo.ru")[1] == "https://findme-photo.ru/"
            assert request(https_port, "/health/", "findme-photo.ru", True)[0] == 400
            assert request(https_port, "/internal/x", "findme-photo.ru", True)[0] == 404
            for path in (
                "/orders/1/access/private-token/secret-value/",
                "/orders/1/download/",
            ):
                for port_number, hostname, use_tls in (
                    (http_port, "findme-photo.ru", False),
                    (https_port, "www.findme-photo.ru", True),
                ):
                    code, headers = private_response(port_number, path, hostname, use_tls)
                    assert code == 308
                    assert headers["Cache-Control"] == "private, no-store"
                    assert headers["Referrer-Policy"] == "no-referrer"
                    assert headers["Vary"] == "Cookie"
            code, headers = private_response(
                https_port, "/orders/1/access/private-token/secret-value/", "findme-photo.ru", True
            )
            assert code == 400  # The local stub rejects the canonical Host.
            assert headers["Cache-Control"] == "private, no-store"
            assert headers["Referrer-Policy"] == "no-referrer"
            assert headers["Vary"] == "Cookie"
            assert (
                request(
                    https_port,
                    "/orders/1/access/private-token/secret-value/",
                    "www.findme-photo.ru",
                    True,
                )[0]
                == 308
            )
            logs = subprocess.check_output(["docker", "logs", container], stderr=subprocess.STDOUT)
            assert b"private-token" not in logs and b"secret-value" not in logs
            assert request(https_port, "/photos/", "www.findme-photo.ru", True)[1] == (
                "https://findme-photo.ru/photos/"
            )
    finally:
        subprocess.run(["docker", "stop", container], capture_output=True, check=False)
