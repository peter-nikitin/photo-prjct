# Worker memory requirement diagnosis

Date: 2026-09-29. Scope: read-only source/runtime diagnosis; no workload submitted,
no models warmed on production, no restart, resource change or cloud mutation.
Repository evidence uses the worker-pool worktree at the merged `454e46a` tree,
not the older main checkout. Production processes are still locally placed workers.

## Finding

The proposed 8 GiB VM is not a measured inference requirement. The isolation inventory
preserved the existing 5 GiB container ceiling and selected the larger VM with OS headroom.
The 5 GiB ceiling has a separate historical origin in bib OCR/VLM acceptance, and a single
Compose parameter currently applies that ceiling to both bulk and selfie.

Evidence:

- [Sizing inventory](2026-09-27-worker-isolation-inventory.md) explicitly distinguishes
  configured 5 GiB from measured consumption.
- [Bib activation runbook](../runbooks/bib-number-recognition.md) records local cgroup peak
  3,607,793,664 bytes (3.36 GiB), selecting 5120 MiB under the 70%-of-limit contract.
  The same artifact was capacity RED due to Docker Desktop swap. This is historical local
  evidence, not a current production Linux peak. The runbook's old live topology description
  is superseded by the fresh container snapshot below, not silently treated as current.
- `docker-compose.deployment.yml` uses the same `PHOTO_WORKER_MEMORY_LIMIT` for both roles.
  Selfie allows only `selfie_query`; bib recognition is in the bulk allowlist.

## Fresh idle snapshot

| Local container | Docker displayed memory | Python RSS | Cgroup peak since current container start | Limit |
| --- | ---: | ---: | ---: | ---: |
| worker-bulk-1 | 48.97 MiB | 68.74 MiB | 53.03 MiB | 5 GiB |
| worker-bulk-2 | 46.75 MiB | 68.68 MiB | 50.66 MiB | 5 GiB |
| worker-selfie-1 | 38.65 MiB | 68.81 MiB | 43.22 MiB | 5 GiB |

All three had `OOMKilled=false`, restart count zero and zero cgroup OOM events.
Host MemAvailable was approximately 13 GiB, with no configured swap.
Different figures are different measurements: process RSS includes mapped shared file pages;
cgroup charge and Docker's cache-adjusted display are not interchangeable with summed RSS.
These tiny peaks do not include a demonstrated inference workload; do not size a warm fleet
from empty local polling processes.

Read-only commands used: `docker stats --no-stream`, selected `docker inspect` fields,
`docker top ... -eo pid,ppid,rss,vsz,comm`, cgroup `memory.current/peak/max/events/stat`,
selected `/proc/1/status` fields, `free -h`, model-file sizes and `pip show torch`.
No container environment or customer inputs/results were printed.

## What actually needs memory

1. Bulk bib inference starts a child Python OCR process plus a CPU `llama-server` for
   Qwen3-VL-2B. Actual packaged weights are 1,107,409,952 bytes for the quantized VLM
   and 445,053,216 bytes for its projector. Together that is about 1.45 GiB on disk,
   not a claim about resident RAM. OCR, context/inference buffers, images and libraries
   add memory while the parent worker may retain previously loaded face runtimes.
   The child/server process tree is cleaned up at the job boundary.
2. Face inference uses SCRFD/ONNX Runtime, SFace/OpenCV and AdaFace/PyTorch. Actual
   artifacts are approximately 16.1 / 36.9 / 91.7 MiB respectively, but runtime
   allocations exceed artifact sizes. Cached runtimes persist in the worker process.
3. The face-runtime cache key includes the recognizer; loading both SFace and AdaFace
   constructs two SCRFD sessions. Remote readiness currently warms both configurations,
   so idle post-warmup is not equivalent to today's approximately 69 MiB local Python RSS.
   Duplication is proven by construction, but its memory delta has not been measured here.
4. Preview/watermark permit 24 MP source images. Decode, orientation, normalization,
   overlay RGBA and final RGB representations can coexist. A 24 MP RGB buffer alone
   is roughly 69 MiB (RGBA roughly 92 MiB), before libraries and additional copies.
   Selfie accepts up to 25 MP before resizing to 1600 px; the initial decode still costs memory.
5. Production has `torch==2.8.0+cpu`; the old accidental CUDA packaging is not this
   runtime's explanation. Image size is disk storage, not resident process memory.

## Conclusions and next boundary

- Thin orchestration does not mean thin native inference; bib VLM is the strongest existing
  explanation for bulk's 5 GiB budget, not evidence of a polling-loop leak.
- No separate evidence currently justifies applying the bib budget to selfie. Preserve the
  existing budget until a focused warm/inference check establishes a safe smaller ceiling;
  neither 2 GiB nor 4 GiB is certified safe by the idle snapshot.
- Changing role-specific limits/VM shapes is a subsequent approved change, not performed
  by this diagnosis. A bounded offline check should distinguish cold import, warm SFace/AdaFace,
  representative maximum-input decode and bulk bib inference, recording cgroup peak including
  children alongside RSS. This is not a new event-scale benchmark project.
- Do not delete models or disable processors to claim savings: existing event generations
  still determine required face models, and bulk's accepted allowlist includes bib recognition.

Measurement references: [Linux cgroup v2](https://cdn.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html),
[ONNX Runtime memory allocation](https://onnxruntime.ai/docs/performance/tune-performance/memory.html).
