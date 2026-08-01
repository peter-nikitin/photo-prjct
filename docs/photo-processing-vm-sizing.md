# Sizing the preview-first photo-processing worker VM

This document defines the evidence required before a supervised preview-first worker check. It does
not authorize a Yandex Cloud resize, VM creation, production capacity decision, or real-environment
worker activation beyond the staged configuration below.

## What is known, and what is not

The first worker processes exactly one job at a time. `generate_preview` downloads an original of
at most 50 MiB and 24,000,000 pixels, applies orientation, decodes and re-encodes a bounded JPEG,
temporarily holds input and output files, uploads an attempt-scoped staging object, and then Django
verifies/publishes it. Preview-backed `face_embedding` separately decodes that published preview.
Django and PostgreSQL hold only short queue transactions.

The deployed Compose stack contains Nginx, Django/Gunicorn, and PostgreSQL. The worker is opt-in
and resource-bounded in staging configuration; the production workflow does not forward worker
activation inputs. Read-only Yandex Cloud discovery recorded the current staging VM as **8 vCPU and
32 GiB RAM**. That inventory fact does not establish disk headroom, sustainable throughput, or a
production capacity decision.

## Staged staging configuration

| Use | VM | Disk | Worker container limits | Scope |
| --- | --- | --- | --- | --- |
| Initial face-worker baseline | Verified staging host: 8 vCPU, 32 GiB RAM | Verify free space before activation; no disk-capacity claim is made here. | `cpus: 1.0`, `mem_limit: 2g`, `pids_limit: 64` | `PHOTO_WORKER_REPLICAS=1`, one representative event and the frozen benchmark cohort. |
| Staged second replica | Same verified staging host | Re-check free space and Docker image growth during the two-worker run. | Two independent workers, each `cpus: 1.0`, `mem_limit: 2g`, `pids_limit: 64`. | Set `PHOTO_WORKER_REPLICAS=2` only after the gate below passes. |

These are staged measurement configurations, not a capacity decision or a promise of performance.
The deployment default is one worker; setting a staging variable to two is a deliberate follow-up
operation, not an automatic consequence of a 32-GiB host.

The limit values leave memory and CPU for the existing stack while containing a face-model OOM to
one worker. The 50 MiB temporary input limit does not by itself set disk size: the disk must also
accommodate Docker images, PostgreSQL's volume, logs, and deployment headroom. The staging profile
declares these limits, but an operator must not enable it before the measurements and gates below
are satisfied.

## Required preview-first measurement procedure

Use the local runbook's preview-first settings and one representative event at total worker
concurrency one. Record the exact event identifier, UTC interval, image count, worker image digest,
processor identities, and the immutable `generate_preview` and `face_embedding` reports. Do not
write Object Storage keys, signed grants, image bytes, EXIF, or credentials into the evidence.

Measure two finite phases, each at total concurrency one. First start the worker with only
`2/generate_preview/1`; do not include a face identity in that phase. Take at most 300 one-second
samples (five minutes) and stop early only when the PostgreSQL count of non-succeeded preview rows
reaches zero:

```sh
PHOTO_WORKER_PROCESSOR_IDENTITIES=2/generate_preview/1 docker compose --profile worker up --scale worker=1 --build -d worker
worker_container="$(docker compose --profile worker ps -q worker)"
[ -n "$worker_container" ] || exit 1
for sample in $(seq 1 300); do
  date -u +%FT%TZ
  docker stats --no-stream --format 'worker cpu={{.CPUPerc}} mem={{.MemUsage}} net={{.NetIO}} block={{.BlockIO}}' "$worker_container"
  docker exec "$worker_container" sh -c 'grep -E "VmHWM|VmRSS" /proc/1/status; df -B1 /tmp'
  remaining="$(docker compose exec -T -e CHECK_EVENT_SLUG=manual-processing web python manage.py shell --no-imports -c 'import os; from picflow.models import Event; from processing.models import PhotoProcessingState; event=Event.objects.get(slug=os.environ["CHECK_EVENT_SLUG"]); print(PhotoProcessingState.objects.filter(photo__event=event, processor_type="generate_preview").exclude(status="succeeded").count())')"
  [ "$remaining" = 0 ] && break
  sleep 1
done | tee media/manual-processing/preview-worker-metrics.txt
docker compose --profile worker stop worker
```

If the loop reaches sample 300, if any preview is not `succeeded`, or if the worker restarts, mark
the preview phase failed and keep preview processing disabled. Inspect and record published
derivatives and queued face `2/2` states before continuing, as required by the local runbook.

Then start a new worker with only `2/face_embedding/2`. Stop it after the final face row succeeds;
do not allow the preview worker to keep polling during this phase:

```sh
PHOTO_WORKER_PROCESSOR_IDENTITIES=2/face_embedding/2 docker compose --profile worker up --scale worker=1 --build -d worker
worker_container="$(docker compose --profile worker ps -q worker)"
[ -n "$worker_container" ] || exit 1
for sample in $(seq 1 300); do
  date -u +%FT%TZ
  docker stats --no-stream --format 'worker cpu={{.CPUPerc}} mem={{.MemUsage}} net={{.NetIO}} block={{.BlockIO}}' "$worker_container"
  docker exec "$worker_container" sh -c 'grep -E "VmHWM|VmRSS" /proc/1/status; df -B1 /tmp'
  remaining="$(docker compose exec -T -e CHECK_EVENT_SLUG=manual-processing web python manage.py shell --no-imports -c 'import os; from picflow.models import Event; from processing.models import PhotoProcessingState; event=Event.objects.get(slug=os.environ["CHECK_EVENT_SLUG"]); print(PhotoProcessingState.objects.filter(photo__event=event, processor_type="face_embedding").exclude(status="succeeded").count())')"
  [ "$remaining" = 0 ] && break
  sleep 1
done | tee media/manual-processing/face-worker-metrics.txt
docker compose --profile worker stop worker
```

Record each measure with its source:

| Measure | Source |
| --- | --- |
| Worker CPU | `docker stats` `cpu` field in the phase metrics file. |
| Worker RSS | maximum `VmHWM` (and observed `VmRSS`) from `/proc/1/status` in the phase metrics file. |
| Temporary disk | maximum used bytes from `df -B1 /tmp` in the phase metrics file, less the pre-phase baseline. |
| Original input bytes | `Photo.original_size` queried by photo ID from PostgreSQL; preview reports intentionally do not store original input bytes. |
| Preview output bytes | `preview.output_bytes` and per-photo `preview.byte_size` in the closed `generate_preview` report. |
| Preview download/compute/upload/total latency | `preview.download_durations_ms`, `preview.compute_durations_ms`, `preview.upload_durations_ms`, and `durations_ms` in the closed preview report. |
| Face download/compute/total latency | accepted face `ProcessingAttempt` duration fields and the closed face report's `durations_ms`; upload is not applicable. |

Use this read-only query for the input-byte and face-attempt evidence; it deliberately excludes
object keys, grants, image data, EXIF, embeddings, and result payloads:

```sh
docker compose exec -e CHECK_EVENT_SLUG=manual-processing web python manage.py shell -c 'import json, os; from picflow.models import Event, Photo; from processing.models import ProcessingAttempt; event=Event.objects.get(slug=os.environ["CHECK_EVENT_SLUG"]); photos=list(Photo.objects.filter(event=event).order_by("id").values("id", "original_size")); faces=list(ProcessingAttempt.objects.filter(event=event, processor_type="face_embedding", accepted=True).order_by("photo_id").values("photo_id", "download_duration_ms", "compute_duration_ms", "total_duration_ms")); print(json.dumps({"photos": photos, "face_attempts": faces}, default=str, indent=2))'
```

A worker restart, OOM kill, lease expiry, retry for a valid representative JPEG, missing face
enqueue, or non-terminal row at the bounded phase end is a failed measurement, not a successful
sizing result.

## Manual-check measurements and acceptance gates

Run the required procedure above. The following are proposed gates for considering either
measurement hypothesis; they are not evidence until actual values and artifacts are recorded:

| Measure | Acceptance threshold |
| --- | --- |
| Worker RSS | Peak stays at or below 70% of the 2 GiB per-worker limit (1.4 GiB); no OOM kill or restart. |
| Host memory | At least 1 GiB `MemAvailable` throughout the run; zero swap-in and swap-out activity. |
| CPU and disk | No sustained (>5 min) host CPU saturation above 85% or iowait above 10%; free disk never falls below the configuration's 15/20 GiB floor. |
| Web path | Health probes keep succeeding and p95 request latency during the run stays no worse than twice the pre-run baseline. |
| Worker path | No lease expiry, retry, or permanent failure for valid representative JPEGs; every preview is accepted, every face `2/2` job is queued afterwards, and both event runs close. |
| Throughput | Record photos/minute and event wall-clock duration; no fixed target is accepted until a real representative cohort establishes a baseline. |

### Replica 1 to 2 acceptance gate

Keep `PHOTO_WORKER_REPLICAS=1` for the initial staging measurement. Move to
`PHOTO_WORKER_REPLICAS=2` only after the immutable worker image completes the frozen benchmark
cohort and its replay at one replica, then completes the same pair at two replicas with all of the
following evidence: every expected worker remains running without restart or OOM kill, web health
probes stay healthy, host memory/disk gates above hold, and the two-worker result records an
improvement in cohort wall-clock time. If any condition fails, return the staging variable to `1`;
the deployment transaction reconciles the previous replica count on a failed rollout.

Also record worker CPU/RSS, temporary-disk high-water mark, Django/Gunicorn CPU/RSS, PostgreSQL
connections/RSS/IO, Nginx request latency, original/preview bytes, per-stage latencies,
retry/failure codes, and free disk. If a gate fails, keep the worker disabled: do not use swap,
increase concurrency, or add a broker to hide a capacity problem. Resize or separate the worker
only through a new approved operational change.

## Explicit rescope point

This sizing applies only to preview generation plus preview-backed face detection/embedding at
concurrency 1. A different decoder, model, image limit, quality setting, vector search, or increased
concurrency requires a fresh workload measurement and a new sizing/activation decision before it
runs on a real VM.

## Recognition-quality activation evidence

Activation also requires a representative original-versus-preview comparison using the same face
model and thresholds. The repository contains the local `experiments/face_recognition_spike`, but
does not contain a checked-in representative photo cohort or model artifacts that can make this
comparison reproducible here. No detection coverage, embedding, or search delta is claimed by this
change.

Before an operator enables `PHOTO_PROCESSING_PREVIEW_ENABLED=True`, create two immutable,
operator-local experiment outputs from the same private representative cohort: one from originals
and one from the generated `preview-small-v1` files. Record photo denominator, detector successes
and misses, accepted face count, embedding failures, and the existing holdout retrieval/search
metrics and deltas. Preserve the commands, model hashes, input manifests, and machine-readable
results beside the private experiment artifacts, not in Git. Any material regression blocks
activation; absence of this comparison means preview processing remains disabled.

## Cost and approval

No price is asserted here. Obtain an official Yandex Cloud calculator or console estimate for the
chosen zone, platform, disk, public IP, snapshots/backups, and billing terms, then obtain explicit
approval immediately before any resize or creation.

## Sources

- [ADR 0017](adr/0017-use-django-polled-photo-processing-jobs.md)
- [Worker specification](superpowers/specs/2026-07-29-event-photo-processing-worker-design.md)
- [Local manual check](local-photo-processing-check.md)
- `.agents/skills/manage-yandex-cloud/references/inventory.md`
