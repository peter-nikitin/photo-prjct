"""Local-only real S3/TLS/transform acceptance. Never prints signed paths."""

import base64
import concurrent.futures
import hmac
import http.client
import http.server
import io
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import boto3
from PIL import Image

BUCKET = "image-origin-contract"
AUTH = "contract-origin-secret-0123456789"
SENTINEL = "private-query-value-must-never-be-logged"


def key(index):
    return (
        "derivatives/previews/contract-event/preview-small-v1/"
        f"00000000-0000-4000-8000-{index:012x}-{'a' * 64}.jpg"
    )


def signed(source=None, preset="gallery-v1", extension="jpg"):
    source = source or f"s3://{BUCKET}/{key(0)}"
    encoded = base64.urlsafe_b64encode(source.encode()).decode().rstrip("=")
    path = f"/{preset}/{encoded}.{extension}"
    signature = (
        base64.urlsafe_b64encode(
            hmac.digest(
                bytes.fromhex("11" * 32), bytes.fromhex("22" * 32) + path.encode(), "sha256"
            )
        )
        .decode()
        .rstrip("=")
    )
    return f"/{signature}{path}"


def request(path, auth=AUTH, host="https://nginx:8443", headers=None):
    req = urllib.request.Request(
        host + path,
        headers={"X-FindMe-Origin-Auth": auth, **(headers or {})},
    )
    started = time.monotonic()
    try:
        response = urllib.request.urlopen(req, context=ssl._create_unverified_context(), timeout=4)
    except urllib.error.HTTPError as error:
        response = error
    body = response.read()
    return response.status, response.headers, body, time.monotonic() - started


def fixture_get(path):
    # Unlike urllib's default opener this observes the actual 3xx without following it.
    connection = http.client.HTTPConnection("seed", 9001, timeout=4)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.headers, response.read()
    finally:
        connection.close()


def verify_redirect_fixture():
    status, headers, _ = fixture_get("/redirect")
    assert status == 302, f"The redirect fixture must really redirect, got {status}"
    assert headers["Location"] == "http://seed:9001/target.jpg"
    status, headers, payload = fixture_get("/target.jpg")
    assert status == 200 and headers["Content-Type"] == "image/jpeg"
    target = Image.open(io.BytesIO(payload))
    target.load()
    assert target.size == (1600, 1067)
    status, _, counters = fixture_get("/counts")
    assert status == 200
    assert json.loads(counters) == {"redirect_requests": 1, "target_requests": 1}
    return json.loads(counters)


def seed():
    certdir = Path("/certificates/live/img-origin.findme-photo.ru")
    certdir.mkdir(parents=True, exist_ok=True)
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
            "/CN=img-origin.findme-photo.ru",
            "-keyout",
            str(certdir / "privkey.pem"),
            "-out",
            str(certdir / "fullchain.pem"),
        ],
        check=True,
        capture_output=True,
    )
    os.chmod(certdir / "privkey.pem", 0o644)  # Ephemeral test-only key shared with non-root nginx.
    s3 = boto3.client(
        "s3",
        endpoint_url="http://minio:9000",
        region_name="us-east-1",
        aws_access_key_id="contract-access",
        aws_secret_access_key="contract-secret-0123456789",
    )
    if BUCKET not in [item["Name"] for item in s3.list_buckets()["Buckets"]]:
        s3.create_bucket(Bucket=BUCKET)
    image = Image.effect_noise((1600, 1067), 35).convert("RGB")
    exif = Image.Exif()
    exif[270] = "contract-private-photo-description"
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=92, exif=exif)
    payload = output.getvalue()
    for index in range(100):
        s3.put_object(Bucket=BUCKET, Key=key(index), Body=payload, ContentType="image/jpeg")
    s3.put_object(
        Bucket=BUCKET, Key=key(100), Body=payload + b"0" * (10 * 1024**2), ContentType="image/jpeg"
    )
    large = io.BytesIO()
    Image.new("RGB", (5001, 5000)).save(large, format="JPEG")
    s3.put_object(Bucket=BUCKET, Key=key(101), Body=large.getvalue(), ContentType="image/jpeg")
    counters = {"redirect_requests": 0, "target_requests": 0}

    class RedirectFixture(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b""
            if self.path == "/redirect":
                counters["redirect_requests"] += 1
                self.send_response(302)
                self.send_header("Location", "http://seed:9001/target.jpg")
            elif self.path == "/target.jpg":
                counters["target_requests"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                body = payload
            elif self.path == "/counts":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                body = json.dumps(counters).encode()
            else:
                self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, pattern, *args):
            pass  # Endpoint counts are sufficient; do not log request data.

    print("FIXTURES_READY accepted=100 oversized_bytes=1 oversized_pixels=1")
    Path("/tmp/ready").touch()
    http.server.ThreadingHTTPServer(("0.0.0.0", 9001), RedirectFixture).serve_forever()


def check():
    status, _, challenge, _ = request(
        "/.well-known/acme-challenge/contract-renewal-token", host="http://nginx:8080"
    )
    assert status == 200, f"Nginx cannot serve the Certbot challenge: status={status}"
    assert challenge == b"contract-acme-key-authorization"
    status, _, _, _ = request(
        "/.well-known/acme-challenge/contract-initial-token", host="http://nginx:8080"
    )
    assert status == 404, "Certbot must remove completed challenges"
    print("ACME_HTTP_READ_BY_UID101=pass")
    status, headers, body, duration = request(signed() + f"?md5={SENTINEL}&expires=123456789")
    assert status == 200, f"Accepted image failed: status={status}"
    assert headers["Content-Type"] == "image/jpeg"
    assert headers["Cache-Control"] == "public, max-age=21600, s-maxage=2592000"
    image = Image.open(io.BytesIO(body))
    assert image.size == (960, 640), image.size
    assert image.info.get("progressive") == 1
    # libvips recreates orientation/DPI/interoperability tags after stripping.
    assert set(image.getexif()).issubset({274, 282, 283, 296, 531, 34665})
    assert b"contract-private-photo-description" not in body
    assert duration < 4
    print(
        json.dumps(
            {
                "image": "jpeg",
                "dimensions": image.size,
                "progressive": True,
                "duration_seconds": round(duration, 3),
                "bytes": len(body),
            }
        )
    )

    fixture_baseline = verify_redirect_fixture()
    failures = {
        "origin_auth": (signed(), "wrong-origin-auth"),
        "signature": ("/" + "A" * 43 + signed()[44:], AUTH),
        "preset": (signed(preset="width:4000"), AUTH),
        "prefix": (signed(f"s3://{BUCKET}/originals/private-photo"), AUTH),
        "bucket": (signed("s3://other-bucket/derivatives/previews/private-photo"), AUTH),
        "scheme": (signed("file:///etc/passwd"), AUTH),
        "http_redirect_source": (signed("http://seed:9001/redirect"), AUTH),
        "extension": (signed(extension="png"), AUTH),
        "source_bytes": (signed(f"s3://{BUCKET}/{key(100)}"), AUTH),
        "source_pixels": (signed(f"s3://{BUCKET}/{key(101)}"), AUTH),
    }
    for name, (path, auth) in failures.items():
        status, rejected_headers, _, duration = request(path, auth=auth)
        assert 400 <= status < 600, f"Unsafe request accepted: {name} status={status}"
        assert "Cache-Control" not in rejected_headers, name
        assert duration < 4, name
    # Verify the imgproxy security boundary itself, even behind Nginx path validation.
    for name in ["preset", "prefix", "bucket", "scheme", "http_redirect_source", "signature"]:
        path, auth = failures[name]
        status, _, _, _ = request(path, auth=auth, host="http://imgproxy:8080")
        assert 400 <= status < 600, f"imgproxy boundary failed: {name}"
    print("REJECTIONS_PASS cases=10 direct_imgproxy_cases=6")
    status, _, counters = fixture_get("/counts")
    assert status == 200 and json.loads(counters) == fixture_baseline, (
        "The protected source must fetch neither the redirect endpoint nor the target JPEG"
    )
    # This proves the production S3 allowlist blocks a real HTTP redirect source before fetch.
    # It does not independently exercise transport redirects for an allowed source.
    print("HTTP_REDIRECT_SOURCE_ALLOWLIST_PASS fixture_status=302 origin_target_fetches=0")

    def transform(index):
        status, _, body, seconds = request(signed(f"s3://{BUCKET}/{key(index)}"))
        assert status == 200, f"Cold transform failed status={status}"
        assert seconds < 4, f"Origin response budget exceeded: {seconds}"
        assert max(Image.open(io.BytesIO(body)).size) <= 960
        return seconds, len(body)

    # A separate source key for every card; no transformed-result cache exists.
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(transform, range(100)))
    print(
        json.dumps(
            {
                "cold_images": len(results),
                "complete_seconds": round(time.monotonic() - started, 3),
                "max_image_seconds": round(max(item[0] for item in results), 3),
                "total_bytes": sum(item[1] for item in results),
            }
        )
    )
    status, _, body, _ = request("/metrics", host="http://nginx:8081")
    assert status == 200
    assert re.search(rb'image_origin_responses_total\{status_class="2xx"\} [1-9][0-9]*', body)
    assert b"image_origin_auth_rejected_total 1" in body
    status, _, image_metrics, _ = request("/imgproxy-metrics", host="http://nginx:8081")
    assert status == 200
    assert b"workers 2" in image_metrics
    memory = float(re.search(rb"process_resident_memory_bytes ([\d.e+]+)", image_metrics)[1])
    assert memory < 3 * 1024**3
    print(json.dumps({"imgproxy_resident_bytes": int(memory)}))
    for private in [AUTH, SENTINEL, key(0), "contract-event", "contract-private-photo-description"]:
        assert private.encode() not in body + image_metrics
    print("AGGREGATE_METRICS_PASS")


if __name__ == "__main__":
    {"seed": seed, "check": check}[sys.argv[1]]()
