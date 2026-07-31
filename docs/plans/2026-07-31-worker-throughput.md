# Worker Throughput Implementation Plan

- Date: 2026-07-31
- Status: Draft
- Owner: project maintainer
- Related specification: [Face-embedding throughput benchmark design](../superpowers/specs/2026-07-31-face-embedding-throughput-benchmark-design.md)
- Related architecture: [photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing), [accepted constraints](../architecture.md#accepted-constraints), and [operational readiness](../architecture.md#evolution-stages)
- Related ADRs: [ADR 0003](../adr/0003-docker-compose-yandex-cloud.md) and [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md)
- ADR impact: Conforms to accepted ADR 0017. Django/PostgreSQL remain the authority and each worker process still claims at most one leased job at a time. No broker, VM resize, GPU, public endpoint, credential change, or new queue is introduced.

## Goal

Increase sustained face-processing throughput on the existing staging VM without weakening job/lease semantics or competing unboundedly with web and PostgreSQL.

## Scope

- Remove the fixed post-success polling delay when compatible queued work remains; retain the server-provided delay after an empty claim and existing error backoff.
- Add the approved isolated `3/face_embedding_benchmark/1` processor and exact 114-photo event cohort replay before changing performance behaviour.
- Reuse YuNet/SFace runtime objects within one worker process and retain the existing per-job input-size, threshold, and result semantics.
- Make the number of single-concurrency worker containers an explicit, validated staging deployment input, starting at one and increasing only after measured acceptance gates.
- Record the approved benchmark's immutable event-run timings before and after each rollout step.

Out of scope: VM resize, GPU provisioning, Celery/Redis/RabbitMQ, modifying existing face-processing state or result data, automatic backfill, changing face-model quality thresholds, or enabling preview processing. A preview-backed ML benchmark may be run only through its existing separate activation gates; it is not an authorization to activate that feature.

## Acceptance criteria

- With a non-empty compatible queue, one worker issues its next claim immediately after terminal submission; it does not sleep for `poll_min_delay_seconds`.
- The benchmark baseline creates exactly 114 new `face_embedding_benchmark` jobs for the selected event, and every candidate run replays exactly the baseline's ordered photo IDs without altering any existing `face_embedding` row.
- With an empty queue, transient API error, lease loss, or non-retryable API error, existing delay/backoff/stop behavior remains unchanged.
- A warm worker constructs each configured YuNet/SFace runtime once per distinct model-path/threshold combination and uses it for later jobs without retaining decoded images or face vectors beyond the job.
- The deployment accepts only `PHOTO_WORKER_REPLICAS=1` or `2`; it uses that value consistently for candidate reconciliation, health verification, diagnostics, and rollback.
- Each enabled replica remains limited to `1.0` CPU, `2g` memory, and 64 PIDs. Staging begins at one replica; two is the only rollout gate in this delivery.
- For a representative bounded event run, attempt data proves: no OOM/restart/lease expiry; all eligible photos terminal; web health remains good; at least 1 GiB host `MemAvailable`; no sustained host CPU above 85% or iowait above 10%; and throughput improves at every accepted rollout step.

## Implementation

### Task 0: Create the isolated event-scoped face benchmark

**Files:**

- Create: `src/backend/processing/management/__init__.py`
- Create: `src/backend/processing/management/commands/__init__.py`
- Create: `src/backend/processing/management/commands/run_face_embedding_benchmark.py`
- Modify: `src/backend/processing/models.py`
- Modify: `src/backend/processing/contracts.py`
- Modify: `src/backend/processing/services/enrollment.py`
- Modify: `src/backend/processing/views.py`
- Modify: `src/backend/processing/tests/test_enrollment.py`
- Modify: `src/backend/processing/tests/test_jobs.py`
- Create: `src/backend/processing/tests/test_face_embedding_benchmark.py`
- Modify: `src/worker/photo_worker/contracts.py`
- Modify: `src/worker/photo_worker/runner.py`
- Modify: `src/worker/tests/test_contracts.py`
- Modify: `src/worker/tests/test_runner.py`
- Modify: `docker-compose.prod.yml`
- Modify: `.github/workflows/deploy.yml`
- Modify: `deploy/apply-deployment.sh`
- Modify: `tests/deployment/test_deployment_scripts.py`
- Modify: `tests/processing/test_worker_container_contract.py`

- **Specification:** [all sections](../superpowers/specs/2026-07-31-face-embedding-throughput-benchmark-design.md).
- **Depends on:** None.
- **Produces:** the supported `3/face_embedding_benchmark/1` identity, bounded event command, immutable baseline/replay runs, redacted metrics-only terminal payload, and deployment support for the identity.

- [ ] Add one Django integration test proving `run_face_embedding_benchmark --event <slug> --limit 114 --label baseline` creates exactly 114 benchmark-only jobs in stable photo-ID order and leaves existing `face_embedding` state/results untouched; it must fail before implementation.
- [ ] Add one replay test proving a closed baseline run creates a second run with exactly the same ordered photo IDs; reject a source run of another processor so production face work cannot be reused accidentally.
- [ ] Add one worker/API contract test for `3/face_embedding_benchmark/1`: it invokes real face extraction but terminally submits metrics only, without vectors or geometry.
- [ ] Add one deployment/container-contract test that accepts the benchmark identity and retains the no-database/no-permanent-storage-credentials boundary.
- [ ] Run `.venv/bin/pytest -q src/backend/processing/tests/test_face_embedding_benchmark.py src/worker/tests/test_runner.py tests/deployment/test_deployment_scripts.py tests/processing/test_worker_container_contract.py` and confirm the new critical-path cases fail for the missing benchmark contract.
- [ ] Implement a benchmark processor constant/configuration and an event-scoped enrollment service. It must create one collecting `EventProcessingRun` plus every job inside one transaction, use a separate `PhotoProcessingState` keyed by `face_embedding_benchmark`, and record bounded label/source-run metadata in run configuration. It must never route through generic reconciliation.
- [ ] Implement the management command with exactly one of `--event` or `--source-run`, plus `--label`; require `--limit` only with `--event`, constrain it to `1..500`, and require an exact eligible count. On replay, copy and validate source-run membership rather than re-querying the event.
- [ ] Extend Django validation, worker contracts, allowed identity lists, and the deployment allowlist for the new identity. Reuse `extract_face_embeddings`, but submit only model, count, warnings, and phase timings. Do not call the face-result persistence path or include biometrics, input identity, or credentials in attempt result/report/logs.
- [ ] Re-run the targeted command and expect all selected tests to pass. Run the normal Django migration-drift check and expect `No changes detected`.

### Task 1: Preserve queued-work momentum in the polling loop

**Files:**

- Modify: `src/worker/photo_worker/runner.py`
- Modify: `src/worker/tests/test_runner.py`

- **Specification:** none; preserves ADR 0017 polling and lease semantics.
- **Depends on:** Task 0 benchmark contract and baseline run must be available before staging deployment.
- **Produces:** a worker loop whose delay is used exclusively for an empty claim and retryable API failure.

- [ ] Add a failing `run_forever` regression test with two successive claimed jobs followed by an empty claim. Stub `time.sleep` and assert that it receives only the empty-claim suggested delay, never the `poll_min_delay_seconds` between the two jobs.
- [ ] Run `.venv/bin/pytest -q src/worker/tests/test_runner.py -k 'run_forever or next_poll_delay'` and confirm the new assertion fails because the current loop sleeps after every successful claim.
- [ ] Change only `Worker.run_forever`: after `run_once()` returns `None` (a job was claimed and submitted), immediately iterate; when it returns an integer, sleep that server-provided empty-queue delay. Preserve the current retryable-error exponential backoff, `lease_not_current` handling, and non-retryable stop.
- [ ] Keep the existing empty-claim, retryable-error, and non-retryable-error tests green; do not broaden this task with additional state combinations.
- [ ] Re-run `.venv/bin/pytest -q src/worker/tests/test_runner.py`; expected: all worker-runner tests pass.

### Task 2: Cache the face-model runtime without retaining photo data

**Files:**

- Modify: `src/worker/photo_worker/face_embedding.py`
- Modify: `src/worker/tests/test_face_embedding.py`
- Modify: `src/worker/tests/test_runner.py` only if integration coverage needs the injected runtime.

- **Specification:** none; output schema and model identifier stay unchanged.
- **Depends on:** Task 0 benchmark contract and baseline run must be available before staging deployment. The implementation may be reviewed independently of Task 1.
- **Produces:** a worker-process-scoped runtime cache keyed by resolved YuNet path, SFace path, and detection threshold.

- [ ] Add failing tests that run two face-extraction calls with the same model configuration and assert creation of `FaceDetectorYN` and `FaceRecognizerSF` once, while `setInputSize` still receives each image's current dimensions.
- [ ] Run `.venv/bin/pytest -q src/worker/tests/test_face_embedding.py`; expected before implementation: the new reuse assertion fails.
- [ ] Introduce a small runtime holder that owns detector/recognizer objects only. Keep JPEG reading, `cv2.imdecode`, detections, aligned crops, embeddings, and temporary arrays local to one call; retain the existing `finally` cleanup and error-code mapping.
- [ ] Use the cached runtime after validating model files, and continue calling `detector.setInputSize((width, height))` before every detection. Do not cache a decoded image, image dimensions, input bytes, result payload, or exception.
- [ ] Re-run `.venv/bin/pytest -q src/worker/tests/test_face_embedding.py src/worker/tests/test_runner.py`; expected: all pass and existing face payload/timing tests remain valid.

### Task 3: Add bounded replica configuration to the deployment transaction

**Files:**

- Modify: `docker-compose.prod.yml`
- Modify: `.github/workflows/deploy.yml`
- Modify: `deploy/apply-deployment.sh`
- Modify: `tests/deployment/test_deployment_scripts.py`
- Modify: `tests/test_repository_foundation.py`
- Modify: `docs/photo-processing-vm-sizing.md`

- **Specification:** none; operational configuration conforms to ADR 0003 and ADR 0017.
- **Depends on:** Tasks 0–2 must pass local verification; replica rollout must use their immutable worker image.
- **Produces:** `PHOTO_WORKER_REPLICAS` from the GitHub staging variable into the protected candidate `.env`, `docker compose up --scale worker=<n>`, all-replica health checks, and rollback to the previous replica count.

- [ ] Add failing deployment-script tests for replicas defaulting to one, `2` reaching candidate reconciliation, and a missing/restarting second worker failing runtime verification. Keep existing input-validation cases as regression coverage.
- [ ] Run `.venv/bin/pytest -q tests/deployment/test_deployment_scripts.py tests/test_repository_foundation.py`; expected before implementation: the new replica and rollback assertions fail.
- [ ] Add `PHOTO_WORKER_REPLICAS` to the workflow environment forwarding and allow only `1` or `2` in `apply-deployment.sh`. Persist the validated requested value in `.env`; read and validate the previous value before any mutation, defaulting legacy deployments to one.
- [ ] Pass `--scale worker="$requested_worker_replicas"` to every worker-profile reconciliation: candidate deploy, recovery, and any profile-aware stop/down path that must preserve the previous state. Keep processing-disabled deployments at zero worker containers.
- [ ] Replace single-container selection with an exact worker-container count check. Verify every expected container is running, not restarting, and not OOM-killed; diagnostics must include all worker IDs and logs.
- [ ] Change the worker memory limit from the obsolete `768m` EXIF value to `2g`, preserving `cpus: "1.0"` and `pids_limit: 64`. This reflects the proven face-worker OOM boundary; it does not increase the number of replicas by itself.
- [ ] Update the sizing document with the actual 8-vCPU/32-GiB staging VM discovery, the 2-GiB per-face-worker limit, and the staged 1 → 2 replica acceptance gate. Do not state a production capacity decision.
- [ ] Re-run `.venv/bin/pytest -q tests/deployment/test_deployment_scripts.py tests/test_repository_foundation.py`; expected: all deployment-contract tests pass, including prior candidate recovery cases.

### Task 4: Establish and execute the staged throughput benchmark

**Files:**

- Modify: `docs/local-photo-processing-check.md`
- Create: `docs/future-work/2026-07-31-preview-ml-throughput-benchmark.md` only if the preview comparison cannot use the already approved preview activation checklist.

- **Specification:** [Measurement and comparison](../superpowers/specs/2026-07-31-face-embedding-throughput-benchmark-design.md#measurement-and-comparison).
- **Depends on:** Task 0 baseline contract, then Tasks 1–3 deployed one at a time through the normal staging workflow.
- **Produces:** a repeatable, read-only event-run measurement procedure and an operational acceptance record in the implementation pull request.

- [ ] Extend the check document with one read-only Django query for a selected benchmark run that reports: job creation-to-claim delay; `download_duration_ms`, `compute_duration_ms`, and `total_duration_ms`; terminal error/retry/lease outcomes; `decode_ms`, `model_load_ms`, `detect_ms`, and `embed_ms` percentiles/counts; wall-clock; and photos/minute. It must not print signed URLs, tokens, embeddings, geometry, or original keys.
- [ ] Add commands for host/container observation during the run: CPU by container, RSS, restart/OOM state, `MemAvailable`, disk free space, and iowait. Specify a fixed representative cohort and record its photo count, image-size distribution, image dimensions, and wall-clock duration.
- [ ] Deploy the Task 0-compatible image at one replica, create the exact 114-photo `baseline` run, and save its closed run UUID and output as the immutable comparison source. Accept only if every operational acceptance criterion holds; otherwise revert to the previous image/configuration and investigate the measured dominant phase before changing scale.
- [ ] Deploy the Task 1–3 candidate image at one replica and replay the baseline UUID. Then repeat the identical source run at two replicas. Stop after this comparison unless it is clearly CPU-bound, host-health gates remain green, and the two-replica result is materially faster; four replicas are a follow-up decision.
- [ ] If `decode_ms` or `detect_ms` dominates after the warm-runtime change, create the future-work note with the measured evidence and trigger: reopen preview-backed ML activation only after its Object Storage lifecycle/preflight and original-versus-preview quality comparison pass. If `download_ms` dominates, investigate storage/network separately; do not compensate with more CPU replicas.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behaviour with ADR 0003, ADR 0017, the architecture worker boundary, and this plan.
- [ ] Update `docs/architecture.md` only to replace an inaccurate deployed worker/replica fact with measured evidence; retain the one-job-per-process boundary.
- [ ] Record `Conforms to ADR 0017` in the pull request, including the chosen accepted staging replica count and benchmark evidence. Stop for a new ADR if the work requires a broker, a different trust boundary, VM resize, GPU, or a durable production capacity commitment.

## Verification

Run with the repository's CI-like Django environment where required:

```sh
.venv/bin/pytest -q src/worker/tests/test_runner.py src/worker/tests/test_face_embedding.py src/worker/tests/test_contracts.py
DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=dev-secret \
  .venv/bin/pytest -q src/backend/processing/tests/test_face_embedding_benchmark.py tests/processing/test_worker_container_contract.py
.venv/bin/pytest -q tests/deployment/test_deployment_scripts.py tests/test_repository_foundation.py
.venv/bin/ruff check src/worker src/backend/processing tests/deployment tests/processing
.venv/bin/mypy src/worker/photo_worker src/backend/processing
```

Expected: all selected critical-path tests pass; Ruff and mypy report no new errors. The benchmark's 114-photo staging run is the acceptance test for performance. After each deployment, use Task 4 cohort evidence rather than host-average CPU alone to accept or reject the next scale step.

## Operational impact and rollout

The deployment is staging-only and uses the existing GitHub Actions → immutable GHCR image → Docker Compose path. Set the GitHub staging variable `PHOTO_WORKER_REPLICAS` to `1` for the first rollout, then `2` only after the baseline comparison. Further scaling is explicitly outside this delivery.

Each face-worker replica receives the existing API token and short-lived exact-object grants only; it receives no PostgreSQL, Django-secret, or permanent Object Storage credentials. PostgreSQL leases and `skip_locked` claims prevent two replicas from accepting the same current job. No database migration, media rewrite, or backfill is part of the rollout.

## Rollback

For an application or runtime failure, redeploy the previously verified immutable image and its saved `.env`; `apply-deployment.sh` must restore the prior `PHOTO_WORKER_REPLICAS` during its own failed-candidate recovery. For capacity degradation after a successful deployment, set the staging variable to the last accepted replica count and redeploy through the same workflow. For a code regression, revert the single performance commit and deploy the prior image. Existing attempts remain immutable evidence; no job/result rows or stored media are deleted.

## Open questions

None for implementation. The operational rollout deliberately stops at its evidence gates; a VM resize, GPU, broker, or preview-backed ML activation requires a separate decision after the benchmark results.
