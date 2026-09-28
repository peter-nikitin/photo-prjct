from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from photo_worker.contracts import FaceEmbeddingFace, FaceEmbeddingResult, SelfieEmbeddingResult
from photo_worker.face_quality import FaceQualityEvidence

ROOT = Path(__file__).resolve().parents[2]
ENV = {
    "WORKER_IMAGE": "ghcr.io/example/photo-prjct-worker@sha256:" + "a" * 64,
    "PHOTO_WORKER_BUILD": "a" * 40,
    "PHOTO_PROCESSING_FLEET_TOKEN": "fleet-only",
    "PHOTO_WORKER_POOL": "selfie",
    "PHOTO_WORKER_PROCESSOR_IDENTITIES": "1/selfie_query/2",
    "WORKER_POOL_PRIVATE_API_IPV4": "10.20.30.40",
    "PHOTO_WORKER_API_URL": "https://findme-photo.ru:8443/internal/photo-processing/v1",
}


def test_worker_only_compose_projects_no_host_ports_or_backend_credentials():
    result = subprocess.run(
        ["docker", "compose", "-f", "deploy/worker-pools/compose.yml", "config"],
        cwd=ROOT,
        env={
            **os.environ,
            **ENV,
            "DB_PASSWORD": "forbidden",
            "SECRET_KEY": "forbidden",
            "PHOTO_PROCESSING_WORKER_TOKEN": "forbidden",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    services = yaml.safe_load(result.stdout)["services"]
    assert set(services) == {"photo-worker"}
    worker = services["photo-worker"]
    assert not worker.get("ports")
    assert {
        (mount["source"], mount["target"], mount["read_only"]) for mount in worker["volumes"]
    } == {
        ("/proc/sys/kernel/random/boot_id", "/run/findme-worker/boot-id", True),
        ("/etc/findme-worker/instance-id", "/run/findme-worker/instance-id", True),
    }
    assert worker["container_name"] == "findme-photo-worker"
    assert worker["stop_grace_period"] == "16m0s"
    assert worker["cpus"] == 2.0
    assert worker["mem_limit"] == "5368709120"
    assert worker["environment"]["PHOTO_WORKER_TOKEN"] == "fleet-only"
    assert worker["extra_hosts"] == ["findme-photo.ru=10.20.30.40"]
    assert set(worker["environment"]) == {
        "PHOTO_WORKER_TOKEN",
        "PHOTO_WORKER_BUILD",
        "PHOTO_WORKER_IMAGE",
        "PHOTO_WORKER_POOL",
        "PHOTO_WORKER_TRANSPORT",
        "PHOTO_WORKER_API_URL",
        "WORKER_POOL_PRIVATE_API_IPV4",
        "PHOTO_WORKER_PROCESSOR_IDENTITIES",
        "PHOTO_WORKER_LEASE_SECONDS",
        "PHOTO_WORKER_HTTP_TIMEOUT_SECONDS",
    }


@pytest.mark.parametrize(
    "private_ip", ["0.0.0.0", "111.88.151.64", "::", "127.0.0.1", "169.254.1.1", "10.0.0.999"]
)
def test_private_edge_renderer_rejects_non_rfc1918_bind(private_ip, tmp_path):
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-e",
            "PUBLIC_DOMAIN=findme-photo.ru",
            "-e",
            f"WORKER_POOL_PRIVATE_API_IPV4={private_ip}",
            "-v",
            f"{ROOT / 'deploy/nginx'}:/opt/nginx:ro",
            "nginx:1.27-alpine",
            "sh",
            "/opt/nginx/reload-nginx.sh",
            "--render",
            "/tmp/output.conf",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stderr


def test_rendered_private_nginx_enforces_tls_routes_marker_and_body_boundary(tmp_path):
    """The private proxy must overwrite client authority and never expose public routes."""
    certificates = tmp_path / "letsencrypt/live/photo-prjct"
    certificates.mkdir(parents=True)
    cert = certificates / "fullchain.pem"
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
            str(certificates / "privkey.pem"),
            "-out",
            str(cert),
            "-subj",
            "/CN=findme-photo.ru",
            "-addext",
            "subjectAltName=DNS:findme-photo.ru",
        ],
        check=True,
        capture_output=True,
    )
    rendered = tmp_path / "rendered"
    rendered.mkdir()
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-e",
            "PUBLIC_DOMAIN=findme-photo.ru",
            "-e",
            "WORKER_POOL_PRIVATE_API_IPV4=10.20.30.40",
            "-v",
            f"{ROOT / 'deploy/nginx'}:/opt/nginx:ro",
            "-v",
            f"{rendered}:/rendered",
            "nginx:1.27-alpine",
            "sh",
            "/opt/nginx/reload-nginx.sh",
            "--render",
            "/rendered/default.conf",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    with (rendered / "default.conf").open("a") as output:
        output.write("""\nserver {
            listen 8000;
            location / {
                add_header X-Received-Transport $http_x_findme_worker_transport;
                add_header X-Received-Length $http_content_length;
                return 200 '{"ok":true}';
            }
        }\n""")
    run = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--add-host",
            "web:127.0.0.1",
            "-p",
            "127.0.0.1::8443",
            "-p",
            "127.0.0.1::443",
            "-v",
            f"{rendered}:/etc/nginx/conf.d:ro",
            "-v",
            f"{tmp_path / 'letsencrypt'}:/etc/letsencrypt:ro",
            "nginx:1.27-alpine",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    container = run.stdout.strip()
    try:

        def port(number):
            value = subprocess.run(
                ["docker", "port", container, str(number)],
                check=True,
                capture_output=True,
                text=True,
            )
            return value.stdout.strip().rsplit(":", 1)[1]

        private_port, public_port = port(8443), port(443)

        def request(path, body=None, public=False):
            selected_port = public_port if public else private_port
            command = [
                "curl",
                "--silent",
                "--show-error",
                "--max-time",
                "5",
                "--noproxy",
                "*",
                "--cacert",
                str(cert),
                "--resolve",
                f"findme-photo.ru:{selected_port}:127.0.0.1",
                "-i",
                "-H",
                "X-FindMe-Worker-Transport: spoofed",
                "-H",
                "Authorization: Bearer private-test-secret",
            ]
            if body is not None:
                command.extend(["--data-binary", "@-"])
            command.append(f"https://findme-photo.ru:{selected_port}{path}")
            return subprocess.run(command, input=body, capture_output=True).stdout.decode()

        for _ in range(30):
            response = request("/health/")
            if "404 Not Found" in response:
                break
            time.sleep(0.1)
        assert "404 Not Found" in response
        assert "404 Not Found" in request("/internal/photo-processing/v1-other/claim")
        assert "404 Not Found" in request("/internal/photo-import/v1/claim")
        assert "404 Not Found" in request("/internal/photo-processing/v1/claim", public=True)
        response = request("/internal/photo-processing/v1/claim", b"{}")
        assert "200 OK" in response and "X-Received-Transport: private-tls" in response
        # Actual maximum32x512 worker result with quality evidence and complete envelope.
        vector = (0.04419417382415922,) * 512
        landmarks = ((10.0, 10.0),) * 5
        quality = FaceQualityEvidence(
            algorithm_version="normalized-laplacian-v1",
            crop_size=112,
            confidence=0.99,
            minimum_side_px=64.0,
            relative_area=0.2,
            sharpness=30.0,
            decision="accepted",
            reasons=(),
        )
        result = FaceEmbeddingResult(
            model="adaface-ir18-webface4m-v1",
            faces=tuple(
                FaceEmbeddingFace(
                    index=index,
                    bbox=(0.0, 0.0, 64.0, 64.0),
                    confidence=0.99,
                    landmarks=landmarks,
                    embedding=vector,
                    quality=quality,
                )
                for index in range(32)
            ),
            has_single_query_face_usable=False,
            warnings=(),
            timings={"total_ms": 100},
            input_geometry={"width": 1024, "height": 1024, "orientation": "normalized"},
        )
        envelope = {
            "job_id": "12345678-1234-1234-1234-123456789012",
            "attempt_id": "12345678-1234-1234-1234-123456789013",
            "worker_build": "a" * 40,
            "started_at": "2026-09-27T12:00:00.000Z",
            "finished_at": "2026-09-27T12:00:01.000Z",
            "download_ms": 100,
            "compute_ms": 900,
            "total_ms": 1000,
            "outcome": "success",
        }
        callback = json.dumps(
            {
                **envelope,
                "contract_version": 3,
                "processor_type": "face_embedding",
                "processor_version": 5,
                "result": result.as_payload(),
            },
            separators=(",", ":"),
        ).encode()
        assert 256 * 1024 < len(callback) < 384 * 1024
        response = request("/internal/photo-processing/v1/attempts/test/complete", callback)
        assert "200 OK" in response and f"X-Received-Length: {len(callback)}" in response
        selfie = SelfieEmbeddingResult(
            model="adaface-ir18-webface4m-v1",
            embedding=vector,
            bbox=(0.0, 0.0, 64.0, 64.0),
            confidence=0.99,
            landmarks=landmarks,
            timings={"total_ms": 100},
        )
        selfie_callback = json.dumps(
            {
                **envelope,
                "contract_version": 1,
                "processor_type": "selfie_query",
                "processor_version": 2,
                "result": selfie.as_payload(),
            },
            separators=(",", ":"),
        ).encode()
        assert len(selfie_callback) < 16 * 1024
        assert "200 OK" in request(
            "/internal/photo-processing/v1/attempts/test/complete", selfie_callback
        )
        assert "200 OK" in request(
            "/internal/photo-processing/v1/attempts/test/complete", b"x" * (16 * 1024)
        )
        assert "200 OK" in request(
            "/internal/photo-processing/v1/attempts/test/complete", b"x" * (384 * 1024)
        )
        assert "413 Request Entity Too Large" in request(
            "/internal/photo-processing/v1/attempts/test/complete", b"x" * (384 * 1024 + 1)
        )
        logs = subprocess.run(
            ["docker", "logs", container], check=True, capture_output=True, text=True
        )
        assert "private-test-secret" not in logs.stdout + logs.stderr
        temp_files = subprocess.run(
            ["docker", "exec", container, "sh", "-c", "find /var/cache/nginx/client_temp -type f"],
            capture_output=True,
            text=True,
        )
        assert not temp_files.stdout.strip()
    finally:
        subprocess.run(["docker", "rm", "-f", container], check=True, capture_output=True)
