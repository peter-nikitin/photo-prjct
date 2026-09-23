# Yandex Disk photo import

Status: implemented and exercised with disposable local HTTP/PostgreSQL/MinIO fixtures. The
canonical deployment has not been changed or activated. No authorized real JPEG source was
provided; fixture success does not establish live Yandex transfer acceptance.

## Release inputs and order

Use the existing Deploy workflow and `deploy/apply-deployment.sh` for the one canonical deployment.
The workflow builds `<repository>-import-worker:<release SHA>` alongside the same-tag web image.
`PHOTO_IMPORT_ENABLED=False` is the default capability; the `yandex-disk-import` database gate
remains an independent exposure control. The optional `PHOTO_IMPORT_WORKER_TOKEN` Lockbox entry
projects only to web and the import worker. Disabled deployments do not require it. Never pass the
photo-processing token as the import credential.

1. Refresh the read-only deployment/processing inventory and compare the accepted migration plan
   against deployed `899e9cc` or the actual current release. Preserve existing DB and private objects.
2. Deliver the optional credential through the established secret projection. Deploy schema/new web
   with the product gate off and `PHOTO_IMPORT_ENABLED=False`. Only additive import tables are new.
3. Review [local acceptance evidence](../plans/2026-09-07-yandex-disk-import-acceptance.md), CI and the
   target host's existing available capacity. Do not resize a VM implicitly.
4. With explicit operational authorization, set capability `PHOTO_IMPORT_ENABLED=True`, use the
   matching release `IMPORT_WORKER_IMAGE` and `PHOTO_IMPORT_BUILD`, and run Deploy. Its authenticated
   `/internal/photo-import/v1/readiness` request verifies API v1 before starting the worker. This
   readiness request has no claims, source calls, gate writes or reconciliation.
5. Activate the database gate to `staff` in Admin for the explicitly selected event/folder and real
   public JPEG source. Verify direct children, subfolder warning, original, processing, duplicate
   repeat, retry and pause/revisit. Only then explicitly authorize `on`.

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

The owner can inspect current status after disconnect and retry only failed items. Turning the
product gate off pauses authoritative work without exhausting file retry budgets. Existing browser
uploads and the photo/Commerce workers retain their own startup and processing contracts.

Before replacing web, Deploy removes the import worker and, if import was enabled, waits the
maximum accepted API lease window (300 seconds). Candidate failure repeats that stop before
restoring the previous environment and images. Failure to stop blocks image recovery rather than
running new import code against old web. If the previous release supported import and enabled it,
restoration verifies its web/readiness before restarting its worker. Deploy does not change the
operator-selected `off`, `staff`, or `on` gate state, including during rollback. Imports submitted
while the worker is stopped remain queued until it restarts. Database tables and immutable objects
are retained in every ordinary rollback.

Do not reverse import migrations or restore a snapshot as routine rollback. Actual `899e9cc` code
has been exercised against retained new tables locally. A subsequent upgrade resumes durable work
through the new code after current permissions and the gate have been checked. Abort activation on
publication duplicates, privacy leakage, unsupported protocol, preservation failure or exhaustion.
