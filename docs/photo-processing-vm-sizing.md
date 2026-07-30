# Sizing the preview-first photo-processing worker VM

This document defines the evidence required before a supervised preview-first worker check. It does
not assert that any current VM is adequate and does not authorize a Yandex Cloud resize, VM
creation, or real-environment worker activation.

## What is known, and what is not

The first worker processes exactly one job at a time. `generate_preview` downloads an original of
at most 50 MiB and 24,000,000 pixels, applies orientation, decodes and re-encodes a bounded JPEG,
temporarily holds input and output files, uploads an attempt-scoped staging object, and then Django
verifies/publishes it. Preview-backed `face_embedding` separately decodes that published preview.
Django and PostgreSQL hold only short queue transactions.

The deployed Compose stack contains Nginx, Django/Gunicorn, and PostgreSQL. The worker is opt-in
locally and has a disabled-by-default, resource-bounded profile in staging deployment
configuration; the production workflow does not forward worker activation inputs. The inventory
historically describes the current staging host as the weakest, preemptible VM, but its exact CPU,
RAM, disk, platform, and stable resource IDs are unverified.

Live discovery was unavailable for this assessment because the `yc` CLI is not installed in the
execution environment. Do not infer actual VM configuration from this document. Before a later
operation, use read-only discovery with an explicit folder ID and without `yc config list`, which
can expose credentials:

```sh
yc config profile list
yc config get cloud-id
yc config get folder-id
yc compute instance list --folder-id <verified-folder-id> --format json
yc compute disk list --folder-id <verified-folder-id> --format json
```

## Starting configurations

| Use | VM | Disk | Worker container limits | Scope |
| --- | --- | --- | --- | --- |
| Supervised manual measurement hypothesis | 2 vCPU, 4 GiB RAM | 30 GiB network SSD, with at least 15 GiB free after PostgreSQL data and images | `cpus: 0.75`, `mem_limit: 512m`, `pids_limit: 64` | One representative event, concurrency 1, operator present. |
| First preview-first measurement hypothesis | 4 vCPU, 8 GiB RAM | 50 GiB network SSD, with at least 20 GiB free after image, database, and log growth | `cpus: 1.0`, `mem_limit: 768m`, `pids_limit: 64` | One representative event, concurrency 1. |

These are measurement hypotheses, not a capacity decision or a promise of performance. The 2 vCPU
option is only a supervised local/staging measurement option. Do not activate preview processing on
a VM merely because its nominal shape matches either row.

The limit values are deliberately below the VM total so that a worker fault is constrained and
memory/CPU remain for the existing stack. The 50 MiB temporary input limit does not by itself set
disk size: the disk must also accommodate Docker images, PostgreSQL's volume, logs, and deployment
headroom. The disabled-by-default staging profile declares these limits, but an operator must not
enable it before the measurements and gates below are satisfied.

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
PHOTO_WORKER_PROCESSOR_IDENTITIES=2/generate_preview/1 docker compose --profile worker up --build -d worker
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
PHOTO_WORKER_PROCESSOR_IDENTITIES=2/face_embedding/2 docker compose --profile worker up --build -d worker
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
| Worker RSS | Peak stays at or below 70% of its configured memory limit (358 MiB for manual, 538 MiB for recommended); no OOM kill or restart. |
| Host memory | At least 1 GiB `MemAvailable` throughout the run; zero swap-in and swap-out activity. |
| CPU and disk | No sustained (>5 min) host CPU saturation above 85% or iowait above 10%; free disk never falls below the configuration's 15/20 GiB floor. |
| Web path | Health probes keep succeeding and p95 request latency during the run stays no worse than twice the pre-run baseline. |
| Worker path | No lease expiry, retry, or permanent failure for valid representative JPEGs; every preview is accepted, every face `2/2` job is queued afterwards, and both event runs close. |
| Throughput | Record photos/minute and event wall-clock duration; no fixed target is accepted until a real representative cohort establishes a baseline. |

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
