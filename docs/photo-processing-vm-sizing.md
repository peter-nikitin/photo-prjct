# Sizing the first photo-processing worker VM

This document selects a starting point for a supervised photo-processing worker check.
It does not authorize a Yandex Cloud resize, VM creation, or real-environment worker activation.

## What is known, and what is not

The first worker processes exactly one job at a time. Its immutable v1 configuration limits an
input to 50 MiB and 100,000,000 pixels, records a 120-second lease, and releases each temporary
file and decoded image before claiming the next job. It downloads originals directly from Object
Storage; Django and PostgreSQL hold only short queue transactions.

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
| Supervised manual check | 2 vCPU, 4 GiB RAM | 30 GiB network SSD, with at least 15 GiB free after PostgreSQL data and images | `cpus: 0.75`, `mem_limit: 512m`, `pids_limit: 64` | One representative event, concurrency 1, operator present. |
| Recommended first deployed ML worker | 4 vCPU, 8 GiB RAM | 50 GiB network SSD, with at least 20 GiB free after image, database, and log growth | `cpus: 1.0`, `mem_limit: 2g`, `pids_limit: 64` | Initial staging or low-volume production-like check, concurrency 1. |

Choose **4 vCPU / 8 GiB / 50 GiB SSD** for the first deployment that includes the ML worker. It keeps
one bounded ML process from competing without limit with the colocated web application,
PostgreSQL, Nginx, Docker, and operating system. The 2 vCPU / 4 GiB option is a cost-saving manual
validation option, not a production capacity decision. These are starting estimates, not measured
capacity or a promise of performance.

### Face-embedding activation evidence

On the 4 vCPU / 8 GiB staging VM, the worker with `mem_limit: 768m` was OOM-killed twice
(`exitCode 137`) while processing one 4.2 MiB JPEG with `face_embedding`. The production worker
limit is therefore `2g`, still with one CPU and concurrency 1. This is a container-bound increase,
not a VM resize; repeat the supervised acceptance gates below before increasing concurrency or
enabling a larger workload.

The limit values are deliberately below the VM total so that a worker fault is constrained and
memory/CPU remain for the existing stack. The 50 MiB temporary input limit does not by itself set
disk size: the disk must also accommodate Docker images, PostgreSQL's volume, logs, and deployment
headroom. The disabled-by-default staging profile declares these limits, but an operator must not
enable it before the measurements and gates below are satisfied.

## Manual-check measurements and acceptance gates

Run one representative event with worker concurrency 1 and record the event report plus host and
container metrics while it is active. The following are proposed gates for accepting either
starting configuration:

| Measure | Acceptance threshold |
| --- | --- |
| Worker RSS | Peak stays at or below 70% of its configured memory limit (358 MiB for manual, 1.4 GiB for ML); no OOM kill or restart. |
| Host memory | At least 1 GiB `MemAvailable` throughout the run; zero swap-in and swap-out activity. |
| CPU and disk | No sustained (>5 min) host CPU saturation above 85% or iowait above 10%; free disk never falls below the configuration's 15/20 GiB floor. |
| Web path | Health probes keep succeeding and p95 request latency during the run stays no worse than twice the pre-run baseline. |
| Worker path | No lease expiry, retry, or permanent failure for valid representative JPEGs; every job reaches a terminal state and the event run closes. |
| Throughput | Record photos/minute and event wall-clock duration; no fixed target is accepted until a real representative cohort establishes a baseline. |

Also record worker CPU/RSS, temporary-disk high-water mark, Django/Gunicorn CPU/RSS, PostgreSQL
connections/RSS/IO, Nginx request latency, retry/failure codes, and free disk. If a gate fails,
keep the worker disabled: do not use swap, increase concurrency, or add a broker to hide a capacity
problem. Resize or separate the worker only through a new approved operational change.

## Explicit rescope point

This sizing applies to bounded EXIF extraction and face embeddings at concurrency 1. Preview
generation, vector search, a different image decode path, or increased concurrency each require a
fresh workload measurement and a new sizing/activation decision before they run on a real VM.

## Cost and approval

No price is asserted here. Obtain an official Yandex Cloud calculator or console estimate for the
chosen zone, platform, disk, public IP, snapshots/backups, and billing terms, then obtain explicit
approval immediately before any resize or creation.

## Sources

- [ADR 0017](adr/0017-use-django-polled-photo-processing-jobs.md)
- [Worker specification](superpowers/specs/2026-07-29-event-photo-processing-worker-design.md)
- [Local manual check](local-photo-processing-check.md)
- `.agents/skills/manage-yandex-cloud/references/inventory.md`
