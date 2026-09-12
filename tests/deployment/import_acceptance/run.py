"""Real API/proxy/Postgres/S3 I/O; only the source is an injected local fixture."""

import json
import os
import resource
import time
from pathlib import Path

from import_worker.client import APIClient
from import_worker.contracts import Config
from import_worker.runner import Runner
from import_worker.transport import Transport


class FixtureTransport(Transport):
    maximum_request = 0
    maximum_response = 0
    maximum_temp = 0
    renewal_count = 0
    maximum_manifest_raw = 0
    maximum_manifest_wire = 0
    checked_manifest_limit = False

    def request(self, method, url, **kwargs):
        if kwargs["audience"] == "source":
            # Explicit test injection, never a production hostname/TLS exception.
            assert url == "https://downloader.disk.yandex.ru/fixture.jpg"
            url = "http://source:8081/fixture.jpg"
            kwargs["audience"] = "api"
        if kwargs["audience"] == "storage":
            assert url == "http://minio:9000/local-private"
            kwargs["audience"] = "api"
        if url.endswith("manifest/pages"):
            raw = kwargs["body"]
            self.maximum_manifest_raw = max(self.maximum_manifest_raw, len(raw))
            if not self.checked_manifest_limit:
                oversized = dict(kwargs, body=raw + b" " * (Config.json_bytes + 1 - len(raw)))
                assert super().request(method, url, **oversized).status == 413
                direct = url.replace("proxy:8080", "web:8000")
                assert super().request(method, direct, **oversized).status == 400
                kwargs["body"] = raw + b" " * (Config.json_bytes - len(raw))
                self.checked_manifest_limit = True
            self.maximum_manifest_wire = max(self.maximum_manifest_wire, len(kwargs["body"]))
        if kwargs.get("audience") == "api" and isinstance(kwargs.get("body"), bytes):
            self.maximum_request = max(self.maximum_request, len(kwargs["body"]))
        if url.endswith("/renew"):
            self.renewal_count += 1
        result = super().request(method, url, **kwargs)
        self.maximum_response = max(self.maximum_response, len(result.body))
        path = Path("/tmp/photo-import/current.jpg")
        if path.exists():
            self.maximum_temp = max(self.maximum_temp, path.stat().st_size)
        return result


class FixtureSource:
    def pages(self, key, *, heartbeat):
        # 100 maximum valid escaped-Unicode metadata entries force byte-aware splitting.
        entries = [
            dict(
                kind="jpeg",
                path="/" + ("😀" * 1018) + f"{i:05}",
                name="😀" * 251 + ".jpg",
                size=52428800,
                sha256=None if i == 0 else "a" * 64,
                md5=None if i == 0 else "b" * 32,
                version="😀" * 255,
            )
            for i in range(100)
        ]
        yield "fixture-canonical", entries

    def download_url(self, *args, **kwargs):
        return "https://downloader.disk.yandex.ru/fixture.jpg"


config = Config(os.environ["PHOTO_IMPORT_API_URL"], os.environ["PHOTO_IMPORT_WORKER_TOKEN"])
transport = FixtureTransport()
client = APIClient(config, transport)
for retry in range(60):
    try:
        assert client.call("readiness")["ready"] is True
        break
    except Exception:
        if retry == 59:
            raise
        time.sleep(1)
# Exact accepted HTTP JSON envelope plus one-byte-over rejection at the proxy.
headers = {"Authorization": "Bearer " + config.token, "Content-Type": "application/json"}
body = b'{"contract_version":1}'
response = transport.request(
    "POST",
    config.api_url + "readiness",
    audience="api",
    headers=headers,
    body=body + b" " * (Config.json_bytes - len(body)),
)
assert response.status == 200
response = transport.request(
    "POST",
    config.api_url + "readiness",
    audience="api",
    headers=headers,
    body=body + b" " * (Config.json_bytes + 1 - len(body)),
)
assert response.status == 413
started = time.monotonic()
runner = Runner(config, client, FixtureSource(), transport)
try:
    assert runner.run_once()  # manifest through real proxy, parser and PostgreSQL
    assert runner.run_once()  # one 50MiB JPEG through streaming transport + MinIO
finally:
    runner.close()
print(
    json.dumps(
        dict(
            lease_renewals=transport.renewal_count,
            cgroup_memory_peak_bytes=int(Path("/sys/fs/cgroup/memory.peak").read_text()),
            largest_sent_request_bytes=transport.maximum_request,
            accepted_manifest_wire_bytes=transport.maximum_manifest_wire,
            largest_client_manifest_bytes=transport.maximum_manifest_raw,
            max_response_bytes=transport.maximum_response,
            max_temp_bytes=transport.maximum_temp,
            peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            elapsed_seconds=round(time.monotonic() - started, 3),
        )
    )
)
