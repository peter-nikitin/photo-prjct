> Historical record: its switches, temporary image labels and commands were retired by ADR 0054. See the [completed face vector retirement](legacy-face-vector-retirement.md).

# Historical AdaFace backfill (completed rollout record)

This is the operator path governed by [ADR 0049](../adr/0049-retire-local-photo-worker-recovery-after-remote-acceptance.md).
The independent-image release under [ADR 0051](../adr/0051-release-photo-worker-images-independently.md)
must be deployed and read back before historical enrollment. Merging readiness code does not
authorize enrollment, event activation or a recovery rehearsal. Each live step still needs its
own approval of the exact release, scope, impact, evidence and recovery method. Keep four states
separate: **code-ready** means reviewed code and required CI checks; **deployed** means current web
and worker images are independently identified and live readiness is verified; **backfill-not-started**
remains true until a separately approved bounded enrollment; **live-verified** needs actual
historical processing, activation and sustained remote recovery evidence. None implies the next.

## Starting state and responsibilities

The initial remote-only fleet receipt was read back `committed` on 2026-10-03. That records the
earlier fleet acceptance; it does not prove the independent updater, new claim protocol or current
worker image is deployed. Verify those through Gate 1 and the [worker-pool runbook](worker-pools.md).
The recorded 13 photos missing original metadata and 116 photos without accepted previews are
unresolved source gaps, not accepted omissions; counts may overlap and must be refreshed. The
historical enrollment has not started.

What the operator does manually: approve the concrete live scope and cost, resolve/disposition
source blockers, review recognition quality, and approve each event switch.
Any root-owned host/bootstrap change uses the separately approved worker-pool
runbook; no new IAM, network, capacity or credential grant is implicit here.

What the agent can do after authorization: collect read-only scalar inventories, prepare the
exact command/evidence package, run only its approved bounded operation, monitor to its terminal
outcome and report fresh read-back. This package does not run those operations. Never print
`.env`, full Compose config, secret values, embeddings, object keys, raw selfies or bearer links.
Store any private quality examples in the existing authorized private review boundary.

## Fresh read-only inventory

Use the established canonical VM access (`ssh -l petrnikitin 111.88.151.64`). Read the deployed
web marker and scalar pool state:

```sh
sudo cat /opt/photo-prjct/deployed-image
printf '%s\n' '{"operation":"status"}' | sudo docker compose --project-name photo-prjct --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py control_worker_pools
sudo docker compose --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py report_worker_pool_state --json
sudo docker compose --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py shell --verbosity=0 -c \
  "import json; from picflow.models import Event; print(json.dumps(list(Event.objects.filter(face_search_generation=Event.FaceSearchGeneration.SFACE_V3).order_by('id').values('id','slug','publication_status'))))"
```

Refresh actual provider membership, the worker updater's active immutable image digest and complete
boot-disk inventory using the supported read-only interfaces in [worker-pools](worker-pools.md);
retain observation times and exact selectors for fresh metrics. Inspect claims, live/expired leases,
queued searches, attempts, collector freshness, the deployment recovery gate and database backup/
restore evidence. Include published and unavailable events and hidden photos. A prior count, zero
queue, `RUNNING` VM, or human waiver is not acceptance telemetry.

## Gate 1: deploy and prove the readiness code

Refresh the exact web revision, worker digest, both group IDs and serving health. If the one-time
updater transition is still pending, use the [cap-one procedure](worker-pools.md#one-time-updater-installation-at-cap-one):
pause both claim pools, drain the sole selfie worker, merge/publish the worker image and deploy the
new web migration, patch the existing templates, then invoke `rollingRecreate` for the exact sole
selfie managed-instance ID. The `OPPORTUNISTIC` policy does not restart that live VM after a
template patch. Keep bulk at zero and do not add a VM. Unpause only after the recreated worker is
warm and serving with fresh private API and metric evidence.

Separately approve canonical Deploy of the exact reviewed commit containing this package. A merge
to `main` runs Deploy; for an exact retry, use this dispatch and do not run a concurrent Deploy:

```sh
gh workflow run deploy.yml --ref main -f deployment_sha=<APPROVED_READINESS_SHA>
```

For a worker-input change, Deploy builds the worker image and advances `latest` only after image
smoke; a backend-only retry leaves worker images and hosts alone. There is no worker manifest,
checksum/receipt workflow or shared web/worker SHA gate. Record the web revision and active worker
digest independently. Wait for this exact run and the host updater to finish; this gate does not
enroll or activate an event. If the first updater transition is in scope, keep both pools paused
until the separate template and sole-selfie recreation steps above complete.

On the canonical host, read back the web marker and pool state, not secrets:

```sh
cat /opt/photo-prjct/deployed-image
printf '%s\n' '{"operation":"status"}' | sudo docker compose --project-name photo-prjct --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py control_worker_pools
sudo docker image inspect <READ_BACK_WEB_IMAGE> --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}} {{index .Config.Labels "ru.findme-photo.historical-adaface-contract"}}'
sudo docker compose --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml ps -q web
sudo docker inspect --format '{{.Image}}' <READ_BACK_WEB_CONTAINER_ID>
sudo docker compose --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py backfill_historical_adaface --help
```

Require the deployed web marker and running web image revision to match the approved web commit,
and the web image to carry `vector-only-v1`. Independently read the active worker's immutable
digest and inspect that actual image's revision and `vector-only-v1` label on its worker host. The
worker revision need not equal the web SHA. Verify both pools are fresh, selfie is warm/serving,
bulk is zero while idle, no live attempts remain, and public/private health succeeds. The canonical
web guard checks the database and candidate web label; it does not fetch or validate the worker
image. Confirm the deployed command exposes the dry-run, bounded enrollment and activation options:

```sh
sudo docker compose --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py report_worker_pool_state --json
sudo docker compose --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py backfill_historical_adaface --help
```

Read back settled provider operations, the active worker digest and fresh coordinator/collector
evidence for **both** pools; a local cached image or marker alone is not live proof. No historical
job may be enrolled yet. Only this evidence establishes **deployed** readiness; historical live
verification remains Gates 2–4.

Now, for each explicit event, run the supported dry-run without mutation flags:

```sh
sudo docker compose --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py backfill_historical_adaface --event-id <EVENT_ID>
```

Record `cohort_sha256`, `photo_count`, `configuration_hash`, all terminal and blocker counts,
`not_enrolled_count`, `paused_photo_count`, `pending_job_count`, `active_processing_count` and
`active_lease_count`. Freeze the exact event inventory before starting; if source or membership
changes, stop and review the new cohort hash. The private source evidence must be restored or
explicitly resolved before enrollment; the command does not waive source/replacement blockers.

## Gate 2: approve historical enrollment

Approve the explicit event scope, current cohort hashes and positive per-invocation limits. Review expected worker time,
preemptible bulk VM/disk uptime and object-transfer/model-inference impact. The accepted ceiling
remains bulk 0..1 and warm selfie 1..1; no extra pool/VM or canonical-host downsizing is included.
There is no current price quote in this package; resolve cost against the current billing context
before paid work. Foreground work has claim priority, but cannot interrupt an already running
historical attempt. An operator may enqueue an entire eligible event; use smaller invocations
when a large transaction or foreground queue impact warrants them. The limit is an operator
choice, not a product-wide ceiling on historical photos.

All mutating event commands use the same canonical deployment lock so a native event cannot
activate between an image compatibility preflight and a package change. This is a host invocation
rule; the Django command does not acquire that file lock by itself. Do not schedule concurrent
Deploy, activation or manual bypass of this lock.

```sh
sudo flock -n /opt/photo-prjct/.deployment.lock \
  docker compose --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py backfill_historical_adaface --event-id <EVENT_ID> \
  --apply --cohort-sha256 <CURRENT_COHORT_SHA256> --limit <POSITIVE_BATCH_SIZE>
```

Each invocation ends after its batch. Pausing means issuing no further `--apply`; it does not
delete jobs, revoke leases or reset attempts. Rerun dry-run and the pool report until all enrolled
jobs have terminal evidence. A failed/divergent batch blocks further enrollment; investigate its
existing durable failure/retry path and refresh status before a new approval. There is no
unbounded all-event scheduler or synthetic queue demand from not-enrolled work.

Connect each accepted batch to the pinned remote build, lease/queue movement and accepted
attempts. Candidate vectors are AdaFace 512D under `3/face_embedding/5`, threshold 0.42, stored
only in native vectors; verify no new candidate JSON embedding rows. No preview/watermark/bib,
original object or saved search-result snapshot is replayed by this operation.

## Gate 3: reconcile and activate one event

Finish all photos in the frozen cohort, including hidden and unavailable-event photos. Require
zero source/evidence/replacement/failure blockers, not-enrolled/pending/active work and live
leases. Review accepted, no-face and quality-rejected outcomes against old searchable photos;
SFace-versus-AdaFace recognition quality is not pgvector's same-model `1e-6` tolerance.
Drain queued/processing/cleanup-pending searches under their frozen generation. Retain a SHA256
of the approved private quality review and approve this exact event's switch:

```sh
sudo flock -n /opt/photo-prjct/.deployment.lock \
  docker compose --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py backfill_historical_adaface --event-id <EVENT_ID> \
  --activate --cohort-sha256 <CURRENT_COHORT_SHA256> \
  --quality-review-sha256 <APPROVED_REVIEW_SHA256> --confirm-reviewed
```

Read back the immutable activation, Event AdaFace selection and native generation identities.
Prove new uploaded-selfie and gallery-face searches on published events, old ready result links,
and private processing for unavailable events. Publication/media authorization still applies.
Rebuild/accept clusters separately if required; old-generation pointers must not contribute.

Before activation, stop enrollment on failure and keep SFace serving. After the first switch,
recovery must retain native evidence with compatible web/worker images. Do not switch reader
to an incomplete JSON cohort, reactivate SFace, delete old vectors or restore a database snapshot
as an image rollback. Unused candidate rows can remain on a compatible release rollback.

## Gate 4: sustained remote proof and recovery rehearsal

Across the complete fresh SFace inventory, require reconciled terminal outcomes and accepted
activation for every event. Record sustained bulk processing with foreground progress, a real
bulk zero-to-one wake, idle return to zero and complete provider proof that the associated boot
disk was deleted. Prove a new warm selfie result under the accepted AdaFace cohort. Relate
provider timestamps, fresh metrics, active worker digest and durable attempts/results; graphs missing
the first counter sample are not absence-of-work proof.

Separately approve a compatible remote recovery rehearsal, including loss/replacement of the only
warm selfie worker and bulk wake with preserved jobs/leases. Use current group/member operations
and the [worker-pool runbook](worker-pools.md) for status and digest recovery; the old
`release.py`, manifest, initial-finalizer and receipt-based rollout/rollback commands are retired.
For a worker image, pause the updater timer and select an explicitly compatible immutable digest.
For web recovery, use the [canonical deployment path](deployment.md); after `processing.0016`
drops `ProcessingAttempt.worker_build`, an incompatible previous web SHA cannot be restored.
Preserve caps, durable work and artifacts. On uncertain provider response, inspect the exact
operation and full group/disk state before retrying; stop enrollment and allow normal lease
recovery. Record actual recovery and service impact; a plan is not a completed rehearsal.

## Remaining model-data boundary

The repository removes local photo/selfie execution and implements the independent worker-image
path. The initial remote-only receipt being committed proves the earlier fleet acceptance, not
that the updater or new processing protocol is live. Verify the new package through canonical
Deploy and fresh web/worker read-back before Gate 2; code readiness alone does not prove rollout.

Both Docker images declare `ru.findme-photo.historical-adaface-contract=vector-only-v1`. Before
web deployment, the canonical guard reads active native-generation state and checks the candidate
web image; it does not fetch or validate the worker. Separately inspect the active worker digest
and its label on the worker host. Web revision and worker revision are independent and need not
match. A failed database probe fails closed. After event activation, an image without native
capability is not a compatible recovery candidate. SFace support, historical JSON rows, Python
readers, old-generation evidence and the temporary reader gate remain for later coordinated
model-data retirement; this runbook does not authorize their deletion.
