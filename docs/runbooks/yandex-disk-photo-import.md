# Yandex Disk photo import

Status: implemented and exercised with disposable local HTTP/PostgreSQL/MinIO fixtures. The
import worker is part of the canonical deployment topology. The local fixture result does not
establish live Yandex transfer acceptance; record a separate real-source check before claiming it.

## Release inputs and order

Use the existing Deploy workflow and `deploy/apply-deployment.sh` for the one canonical deployment.
The workflow builds `<repository>-import-worker:<release SHA>` alongside the same-tag web image.
The dedicated non-empty `PHOTO_IMPORT_WORKER_TOKEN` projects only to web and the import worker.
The worker API rejects missing or invalid bearer tokens. Never pass the photo-processing token as
the import credential.

1. Refresh the read-only deployment/processing inventory and compare the accepted migration plan
   against the actual deployed release. Preserve existing DB and private objects.
2. Deliver the dedicated worker credential through the established secret projection. Review
   [local acceptance evidence](../plans/2026-09-07-yandex-disk-import-acceptance.md), CI, and the
   host's available capacity. Do not resize a VM implicitly.
3. Deploy matching `IMPORT_WORKER_IMAGE` and `PHOTO_IMPORT_BUILD` with the web image. The authenticated
   `/internal/photo-import/v1/readiness` request verifies API v1 before the worker starts; it has no
   claims or source calls.
4. For a real public JPEG source, verify direct children, subfolder warning, original publication,
   processing, duplicate repeat, retry, and status revisit. Record this as live acceptance evidence
   only after observing the deployed path.

The import worker is non-root, read-only, has no published ports, DB/storage credentials or shared
application mounts, and receives a 256 MiB memory limit, 0.5 CPU, 32 PIDs and one 64 MiB tmpfs.
Source/S3 traffic uses strict public TLS in production. Public Nginx denies the entire internal
import prefix. Local acceptance transport injection is contained in the test harness.

## Inspect and clean

Run these inside the current web container through the ordinary canonical Compose invocation:

```sh
python manage.py inspect_photo_imports --limit 100
python manage.py inspect_photo_imports --batch <uuid> --limit 100
python manage.py cleanup_stale_imports --limit 100
```

Inspection emits bounded IDs, states and aggregate counts. No source keys, paths, filenames,
download URLs, credentials or original keys appear. Limits accept 1–1,000.

Cleanup defaults to dry-run. Review its eligible count and operational scope before explicitly
running `cleanup_stale_imports --limit 100 --apply`. It selects attempt-owned objects inactive for
at least 24 hours, locks item/batch/attempt, rechecks activity and all live batch leases, and fences
expired work before deleting. It skips published Photo keys and content winners. On storage failure
it leaves a durable expired attempt with retryable cleanup metadata; the command exits nonzero.
Successful deletion clears only the removed object checkpoints. It never deletes an import manifest,
source path, attempt history, confirmed Photo or processing enrollment. A restarted item receives
new attempt keys. No purge, broad requeue, backfill or reset operation is provided.

## Pause and recovery

The owner can inspect current status after disconnect and retry only failed items. To pause import
computation during an incident, stop only `import-worker`; durable queued work and retry budgets
remain intact. Existing browser uploads and the photo/Commerce workers retain their own contracts.

Before replacing web, Deploy removes the import worker and queries PostgreSQL for live import
attempt leases. If none remain, it continues immediately; otherwise it checks again until they
expire, with a 300-second limit. A failed or invalid database probe blocks replacement. Candidate
failure repeats that stop before restoring the previous environment and images. Failure to stop
blocks image recovery rather than running new import code against old web. If the previous release
supported import, restoration verifies its web/readiness before restarting its worker. Imports
submitted while the worker is stopped remain queued until it restarts.
Database tables and immutable objects are retained in every ordinary rollback.

Do not reverse import migrations or restore a snapshot as routine rollback. A subsequent upgrade
resumes durable work through the new code under current permissions, owner scope, worker credential,
and lease rules. Stop the affected worker or roll back the application image on publication
duplicates, privacy leakage, unsupported protocol, preservation failure, or exhaustion.
