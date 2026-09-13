# One-event Linux bib acceptance

Run from this checkout after building the current worker image:

```sh
docker build -f Dockerfile.worker -t findme-photo-worker:bib-local .
scripts/local-bib-acceptance.sh \
  --event-slug local-istra-bib \
  --source-root '/Users/petrnikitin/Documents/Projects/findme-photo-images/bibs/2026-04-19_Истра' \
  --baseline experiments/bib_search/worker_acceptance/baseline_manifest.json \
  --output var/bib-search/production-istra-001
```

Choose a new output directory for each run. The command refuses to overwrite any artifact,
checks the exact 37 JPEG names and SHA-256 hashes before starting Docker, and creates one event
in an isolated Compose project. PostgreSQL and MinIO are disposable tmpfs services. The migration
catalog fixtures remain present; they are outside the upload cohort. Only the loopback web port
18210 is exposed. The script never reads the checkout `.env`, modifies a live deployment, enrolls
old photos, or exposes a retry/backfill/purge operation. Local credentials are fixed disposable
values on the isolated Docker network.

The script builds the current web image, logs in through the photographer HTTP login, then calls
batch/register/authorize/presigned S3 POST/confirm/finalize APIs. One Linux worker executes ordinary
metadata, preview, face and bib identities. It waits up to four hours for all stages of all photos
to become terminal. Public HTTP exact-number queries include leading-zero counterqueries. A
rolled-back transaction invokes the public view against a different event to check isolation,
without leaving another event or fabricating a bib projection.

`baseline_manifest.json` adapts the checksum-verified Istra corpus and artifact references from
commit `4102ab0`. It embeds the normalized user-confirmed and user-rejected pairs separately from
41 model-accepted baseline pairs. `00011_Vlad.jpg · 67` remains explicitly excluded as unreadable.
Original images and raw reviews stay outside Git. Changed unreviewed pairs block acceptance for
manual inspection; the script never converts a model assessment into a user decision.

The output contains `baseline.json`, `runtime.json`, `aggregate.json`, `evidence.json`,
`comparison.json`, raw resource samples and public-probe timings. A failed setup/runtime instead
seals `failure.json` with the last available snapshot. All final JSON files use exclusive creation.
The command exits nonzero for malformed or missing evidence, any confirmed miss, rejected junk,
changed unreviewed pair, publication/terminal failure, exact-query/leading-zero/event leakage, or
capacity gate failure. Images, signed URLs, original storage keys, credentials and raw inference
text are never written to these reports.

The measurement run uses one worker, two CPUs and the maximum candidate limit of 6 GiB. A separate read-only observer container starts before upload. It shares the worker PID namespace
for measurement but uses its own cgroup, CPU, memory and PID limits; it therefore does not consume
the worker's existing 64-PID budget. The observer has no network or capabilities. Every two seconds it records the resident pages of the complete worker
process tree, cgroup peak memory/CPU/OOM, and Linux-host MemAvailable, swap counters, CPU, iowait
and disk headroom. The capacity estimate conservatively uses the larger of sampled RSS and the
cgroup lifetime memory peak (which also includes charged file cache); the report retains sampled
RSS separately. This avoids overlooking a between-sample peak and may select a higher limit than
RSS alone. It calculates `ceil(peak / 0.70)` rounded up to 512 MiB (minimum 2 GiB); a result above
6 GiB blocks activation. It also requires at least 1 GiB host headroom, no swap I/O, OOM, restarts,
lease expiry or failed public probes, no CPU >85% or iowait >10% for over five consecutive minutes,
and public web p95 within twice the pre-upload baseline. Per-stage p50/p95 and event wall time are
mandatory. Host values describe the Linux Docker VM, not macOS memory.

The script stops only its own Compose services when finished. Failed artifacts are retained.
A GREEN local comparison is a prerequisite for canonical activation, not live evidence. Production
stays disabled until the separate deployment and first-event VM measurement pass.
