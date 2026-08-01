# Face-Embedding Throughput Benchmark Design

- Date: 2026-07-31
- Status: Proposed for maintainer review
- Owner: project maintainer
- Related architecture: [photo ingestion and indexing](../../architecture.md#photo-ingestion-and-indexing) and [accepted constraints](../../architecture.md#accepted-constraints)
- Related ADRs: [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md)
- ADR impact: Conforms to ADR 0017. This adds a bounded processor contract inside the existing Django/PostgreSQL control plane; it does not alter the worker trust boundary, job lease semantics, storage authorization, or deployed topology.

## Goal

Provide a supported, event-scoped way to measure face-embedding throughput on the same already-processed photos before and after worker optimizations, without rewriting accepted processing evidence, changing gallery/search data, or touching another event.

## Context

The selected staging event contains 114 photos with successful face-processing attempts. Existing attempts, jobs, states, and derived face rows are immutable and must remain the historical working result. The current generic reconciliation is neither event-scoped nor a supported way to requeue successful states, so it cannot produce a trustworthy A/B comparison.

The benchmark needs real JPEG download, decode, YuNet detection, and SFace embedding work. A synthetic timing loop, cached response, or host-average CPU graph does not prove end-to-end throughput.

## Scope

- Add a benchmark-only processor identity `3/face_embedding_benchmark/1`.
- Add a Django management command that enqueues an exact bounded event cohort and repeats an earlier cohort by source-run ID.
- Run the existing worker inference path but persist only benchmark metrics and terminal outcomes.
- Compare a baseline run with deployments at one and two single-concurrency worker replicas.

## Out of scope

- Modifying, deleting, retrying, or superseding existing `face_embedding` jobs, attempts, states, face detections, embeddings, gallery visibility, or search indexes.
- Automatic backfill of any event or processor.
- Preview generation/activation, input downscaling, model-quality changes, VM resize, GPU, broker introduction, database migration of existing data, or a production rollout.
- Persisting face vectors, boxes, landmarks, image bytes, signed URLs, Object Storage keys, worker tokens, or source-photo metadata in benchmark result payloads.

## Selected design

### Isolated processor and data ownership

The processor identity is exactly:

```text
contract_version = 3
processor_type = face_embedding_benchmark
processor_version = 1
```

It has its own `PhotoProcessingState` per photo because state identity includes `processor_type`. It therefore cannot change the existing `face_embedding` state or its current accepted job. The existing `EventProcessingRun`, `ProcessingJob`, and `ProcessingAttempt` tables hold benchmark evidence; no new face-detection, embedding, derivative, gallery, or search row is created.

The worker accepts the new identity in its existing ordered identity list. It downloads the same exact original under the normal short-lived grant, calls the existing face-extraction routine, and discards faces/embeddings before terminal submission. It uses the same model paths and runtime configuration as the candidate deployment being measured.

### Benchmark terminal contract

A successful benchmark terminal result contains only:

```json
{
  "model": "sface",
  "face_count": 1,
  "warnings": ["no_faces_detected"],
  "timings": {
    "decode_ms": 0,
    "model_load_ms": 0,
    "detect_ms": 0,
    "embed_ms": 0,
    "total_ms": 0
  }
}
```

`face_count` is a non-negative bounded integer and `warnings` uses only existing bounded face warning codes. The worker separately submits existing `download_ms`, `compute_ms`, and `total_ms` attempt fields. The result must not include an embedding, bbox, landmark, confidence, source key, presigned URL, or original-image detail.

Benchmark failures use the existing typed download/decode/model error vocabulary and retry policy. The normal lease, heartbeat, idempotent terminal submission, stale-attempt isolation, and immutable attempt rules apply unchanged.

### Exact cohort creation and replay

The management command interface is:

```text
python manage.py run_face_embedding_benchmark \
  --event <event-slug> --limit 114 --label <baseline|candidate-name>

python manage.py run_face_embedding_benchmark \
  --source-run <baseline-run-uuid> --label <candidate-name>
```

For the first form, Django selects eligible JPEG photos from the exact event in stable primary-key order. It rejects a limit outside `1..500`, a missing event, any non-JPEG/incomplete original, or a cohort smaller than the exact requested limit. It creates one collecting run and all jobs in one database transaction; workers cannot see a partial cohort. The run configuration records the bounded label, requested count, source mode, and processor settings, but never source keys or signed credentials.

For the replay form, the command accepts only a closed `3/face_embedding_benchmark/1` run. It copies its ordered job photo IDs, verifies that every photo still belongs to the source event and is eligible, and creates a new run with exactly that membership. It rejects a non-benchmark, non-closed, missing, or inconsistent source run. The label is bounded operator-facing metadata; the immutable run UUID, not label uniqueness, identifies a comparison run.

The command is intentionally explicit and operator-driven. It never runs at startup, migration, upload confirmation, admin-page load, or generic reconciliation.

### Measurement and comparison

The baseline command first creates the 114-photo cohort while the worker deployment remains at one replica. When its run closes, record the immutable run ID, its job photo IDs, run wall-clock (`closed_at - created_at`), photos/minute, terminal status counts, and p50/p95 of all attempt and ML phase timings.

After a candidate deployment, replay the baseline run. Compare only runs with identical ordered photo IDs. Accept a scale increase only when all photos are terminal, no attempt is expired/stale or OOM/restart-caused, web health remains successful, host gates remain within the throughput plan, and throughput improves over the prior accepted run. A candidate with no material improvement or any gate regression is rolled back to the previous replica count/image rather than scaled further.

## Privacy and safety

The worker continues to receive only its existing API credential and exact-object temporary grant. Django remains the only process with permanent storage credentials. The benchmark stores aggregate processing evidence but no biometric vector or geometry, so it cannot add a searchable identity record. The command performs database writes only for the selected event's new benchmark rows; it never deletes, updates, or purges existing rows or media.

## Compatibility

Existing worker images that do not advertise `3/face_embedding_benchmark/1` simply cannot claim benchmark jobs; deploy the compatible Django and worker image before enqueueing. Existing deployed identities remain supported during the rollout. The benchmark identity is included in the worker identity validation and deployment allowlist, but it is not enabled by default in normal product processing configuration.

## Acceptance criteria

1. A first command for the selected event and `--limit 114` creates one run with exactly 114 jobs, all belonging to that event; it creates no ordinary face job and mutates no pre-existing state.
2. A source-run replay creates a second run with exactly the same ordered photo IDs; a source run from another processor, a non-closed run, or insufficient eligible photos fails without creating any benchmark rows.
3. A successful benchmark attempt records download/compute/total durations plus bounded phase timings and face count, while its result contains no vector, geometry, object key, signed URL, or credential.
4. Benchmark jobs retain ordinary claim, heartbeat, retry, stale-result, idempotency, and immutable report behavior under more than one worker container.
5. The worker/container contract still contains no Django database settings, Django secret, or permanent Object Storage credentials.
6. The report/query for a closed run provides comparable count, terminal outcomes, wall-clock, photos/minute, and p50/p95 timing values without exposing restricted data.

## Rejected alternatives

### Delete or truncate existing processing state

Rejected because it destroys the baseline and immutable evidence, can affect unrelated processing or public-search state, and repeats the staging reset failure mode.

### Reopen the successful `face_embedding` state

Rejected because the state is intentionally idempotent and immutable attempt history must not be rewritten. It would also mix capacity-test data with the active search/gallery processing contract.

### Use generic reconciliation

Rejected because it scans across events, skips already successful states, and cannot guarantee the same 114-photo cohort for each run.
