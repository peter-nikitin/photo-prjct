# Local import release acceptance

This is local evidence, not a deployment or live Yandex JPEG smoke. The fixture source and bucket
are disposable and hold no customer photos or credentials. The accepted real-source activation
boundary remains open until an authorized JPEG folder and canonical operational approval exist.

## Reproducible container exercise

The harness is `tests/deployment/import_acceptance/compose.yml`. It runs PostgreSQL 16, the pinned
MinIO release already used by the local purchase environment, candidate web under Gunicorn, an
Nginx HTTP proxy, a fixture HTTP source and the standalone import worker image. No host ports or
persistent volumes are created. `seed.py` only seeds this disposable environment. `run.py` uses the
real API client, transport, runner, Django JSON parser, model services and S3 publication; its
explicit test transport injects only the local source and MinIO connections. Production host/TLS
validation is unchanged.

```sh
docker build -t findme-import-task6-web -f Dockerfile .
docker build -t findme-import-task6-worker -f Dockerfile.import-worker .
docker compose -p findme-import-task6 -f tests/deployment/import_acceptance/compose.yml up -d db minio web proxy source
docker compose -p findme-import-task6 -f tests/deployment/import_acceptance/compose.yml run --no-deps --name findme-import-task6-worker-evidence worker
docker compose -p findme-import-task6 -f tests/deployment/import_acceptance/compose.yml exec -T web python /fixtures/verify.py
```

The fixture manifest contains 100 entries with 1,024-character source paths, 255-character JPEG
names and 255-character versions, using astral Unicode requiring surrogate-pair JSON escapes.
SHA-256/MD5 metadata on the untransferred entries exercises their full field lengths. The runner
splits the callback by the complete serialized envelope, retaining stable page numbers and hashes.
The harness pads one otherwise valid manifest JSON with whitespace to exactly 1,048,576 bytes and
persists it; one byte over is rejected both by Nginx and directly by Django before that page is
accepted. The complete accepted manifest persists as two pages and 100 items.

The transferred JPEG is exactly 52,428,800 bytes, with 4,000 × 3,000 decoded pixels. Valid COM
segments supply the exact byte length. This demonstrates the maximum byte envelope and a 12 MP
pixel decode together, **not resource safety for every possible JPEG under 50 MiB**. Verification
checks the resulting private original, one imported Photo, immutable attempt checkpoint and
standard metadata/preview enrollment. Worker tmpfs/memory/CPU/PID limits match the shipped service.

Final local run (2026-09-07): 1,034,974 bytes was the largest client-generated manifest;
the accepted padded manifest was 1,048,576 bytes. The deliberately rejected request was
1,048,577 bytes (Nginx 413, Django 400). Largest private response: 18,659 bytes. Peak temporary
JPEG: 52,428,800 bytes. Process peak RSS: 131,148 KiB; cgroup memory peak: 176,758,784 bytes,
under the configured 268,435,456-byte limit. The two worker iterations took 0.827 seconds with
six successful lease renewals. The container completed without OOM or restart. These fast local
transfers do not establish live Yandex throughput or slow-network acceptance.

Final image configuration IDs: web `sha256:b62eab3b63bd1a205908781aa6b48e0257229063d2e364f6b0fb851204a2f193`;
worker `sha256:0ca1754055fdcca789fe25e8089b8f8aaded7e82f888440c3f322538a571d001`. Transfer deadlines
remain 45 seconds per request, 10-second I/O, 20-second heartbeat and 120-second worker leases;
all accepted server leases are bounded by 300 seconds for deployment recovery.

## Upgrade and rollback matrix

`test_import_upgrade.py` migrates a disposable PostgreSQL test database back to the previous
schema leaf before adding fixtures, then applies the new migration. The previous ingestion leaf is
`0003_uploaditem_folder`; other app leaves are unchanged from `899e9cc` by this feature. Fixtures
include succeeded/failed/expired/stale/in-progress active and expired leases, queued/retry states,
a never-enrolled Photo, browser upload rows, processing relationships and a derivative. Exact row
values, identifiers, keys and references compare equal after upgrade. Nothing is re-enrolled.

After the container publication, `verify.py` closes the gate and leaves an interrupted expired
import attempt. Extract the real previous backend without changing Git state:

```sh
mkdir -p /tmp/findme-import-task6-old
git archive 899e9cc src/backend | tar -x -C /tmp/findme-import-task6-old
docker compose -p findme-import-task6 -f tests/deployment/import_acceptance/compose.yml run --rm --no-deps --entrypoint python -e PYTHONPATH=/old -w /old -v /tmp/findme-import-task6-old/src/backend:/old:ro web /fixtures/old_probe.py
```

The probe asserts that the loaded backend has no ImportBatch model. It confirms an ordinary
browser upload into MinIO, claims the existing photo-processing protocol, and verifies the new
import attempts are unchanged in the retained tables. No old import worker exists and no new
import worker runs against this old backend. The one-shot fixture worker has already exited.

After capturing evidence, remove only this disposable project:

```sh
docker compose -p findme-import-task6 -f tests/deployment/import_acceptance/compose.yml down --remove-orphans
```

## Other executable safeguards

- Deployment shell tests cover disabled/missing credentials, stop-before-switch, readiness-before-
  start, candidate failure/previous image recovery and secret projection without reconciliation.
- Cleanup tests cover dry-run, live leases, a concurrent publication row lock, paused inactivity,
  confirmed originals, interrupted final copies, partial storage failure and fresh-key restart.
- The real standalone runner/API product fixture covers browser submit/disconnect/revisit, free and
  paid policy/enrollment, duplicate repeat, four source failures and owner retry, plus lost callback
  responses. The source and S3 fixture boundaries in this test are distinct from the container run.
- The suite manifest explicitly classifies the new operational, migration and product-flow paths.
