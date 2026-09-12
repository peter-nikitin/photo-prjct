# Yandex Disk import planning inventory

- Observed at: 2026-09-07 02:31 UTC.
- Purpose: read-only baseline for the import implementation plan, not release acceptance evidence.
- Method: existing SSH connection; Docker names/images only; Django aggregate queries in a
  read-only PostgreSQL transaction with a five-second statement timeout.
- Deployed web and both photo-worker images: `899e9cc96ba6eb586d2df5646a7195d1079bcb18`.
- Current import models and import-worker containers: none.
- Public source URLs, personal filenames, object keys, credentials, and images were not collected.

## Existing durable state

| State | Count |
| --- | ---: |
| Photos | 48,702 |
| Active processing leases | 0 |
| Expired leases still in progress | 0 |
| Succeeded processing attempts | 173,599 |
| Failed processing attempts | 6,484 |
| Expired processing attempts | 237 |
| Closed event processing runs | 50,092 |
| Completed / failed / partial upload batches | 17 / 1 / 19 |
| Uploaded / failed upload items | 48,689 / 8,048 |
| `preview-small-v1` derivatives | 48,586 |
| `preview-watermarked-v1` derivatives | 4 |

Photos with no processing jobs exist; only existence was checked. No queued or in-progress jobs
appeared in the job grouping. No stale attempts appeared in the attempt grouping. These are
point-in-time observations, not guarantees about the state at deployment.

## Stored job identities

| Contract / processor / version | Status | Count |
| --- | --- | ---: |
| `1/capture_metadata/1` | failed | 6,483 |
| `1/capture_metadata/1` | succeeded | 10,711 |
| `1/capture_metadata/2` | succeeded | 48,435 |
| `1/face_embedding/1` | succeeded | 6 |
| `2/face_embedding/2` | succeeded | 17,194 |
| `2/face_embedding/3` | succeeded | 73 |
| `2/generate_preview/1` | succeeded | 48,586 |
| `2/generate_watermarked_preview/1` | succeeded | 4 |
| `3/face_embedding/4` | succeeded | 17,043 |
| `3/face_embedding/5` | succeeded | 31,319 |
| `3/face_embedding_benchmark/1` | succeeded | 228 |

These are persisted identities, not the current worker capability list. The proposed import does
not reinterpret or requeue any of them. Existing live uploads, processors, and accepted results
continue under their own contracts.

## Storage and release boundary

The inspected repository's ingestion storage validates `incoming/<uuid>/<uuid>` and
`originals/<32-hex-id>`. Reuse these key shapes with import-owned IDs; do not rename the bucket
or broaden the browser's grants. This was a code-level prefix check, not an Object Storage listing.

The only supported old import state is absence: the new schema starts empty. Existing uploads,
Photos, processing state, derivatives, and final objects must be preserved. There is no approved
backfill, re-enrollment, reset, purge, or conversion of browser batches into import batches.

An initial full count of never-enrolled photos exceeded the read-only query timeout and was
canceled. The follow-up used a bounded existence query and succeeded. No live data was changed.

Refresh aggregates before actual migration or activation. The implementation must produce the
bounded inspection command described in the [implementation plan](2026-09-07-yandex-disk-photo-import.md).
