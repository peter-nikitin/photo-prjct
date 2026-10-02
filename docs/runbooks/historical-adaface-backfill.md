# Historical AdaFace backfill

This is the operator path for the [approved specification](../superpowers/specs/2026-10-02-historical-adaface-backfill-and-local-worker-retirement-design.md)
and [ADR 0049](../adr/0049-retire-local-photo-worker-recovery-after-remote-acceptance.md).
The [remote-only transition](worker-pools.md) must be committed, deployed and read back before
historical enrollment. The code package is preparation; no production enrollment, event
activation or recovery rehearsal is authorized by merging it. Each live step below needs
its own explicit approval of the exact release, scope, impact, evidence, and recovery method.
Keep five evidence states separate: **code-ready** means the reviewed package passed local/CI
checks; **committed** means the initial fleet receipt and exact marker were read back after
cleanup; **deployed** means the new SHA and compatible images are read back from the canonical
application and both remote pools; **backfill-not-started** remains true until a separately
approved first bounded enrollment; **live-verified** needs actual historical processing,
activation and sustained remote recovery evidence. None implies the next.

## Starting state and responsibilities

The last 2026-10-02 observation found the initial fleet receipt `verified`, not
`committed`. Its original recovery inputs and package remain until the exact one-time
finalizer commits and cleans them. The recorded
13 photos missing original metadata and 116 photos without accepted previews are unresolved
source gaps, not accepted omissions; these counts may overlap and must be refreshed.
Push/merge to `main` automatically triggers normal Deploy. While the initial receipt remains
`verified`, do not merge this readiness PR or trigger that automatic deployment; finish the
separately approved pinned finalizer in Gate 1a first. Local/CI readiness is not permission to bypass this
pending-acceptance fence.

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

Use the established canonical VM access (`ssh -l petrnikitin 111.88.151.64`). On the host,
read the deployed revision/image receipts and scalar fleet state. Do not edit receipts:

```sh
PYTHONPATH=/opt/photo-prjct/deploy/worker-pools/_canonical \
  python3 -B /opt/photo-prjct/deploy/worker-pools/release.py status --root /opt/photo-prjct
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

Refresh actual provider membership, worker build/digest and complete boot-disk inventory using
the supported read-only interfaces in [worker-pools](worker-pools.md); retain observation times
and exact selectors for fresh metrics. Inspect claims, live/expired leases, queued searches,
attempts, collector freshness, existing recovery gate and database backup/restore evidence.
Include published and unavailable events and hidden photos. A prior count, zero queue,
`RUNNING` VM, or human waiver is not acceptance telemetry.

## Gate 1a: finalize the initial remote receipt

Before historical enrollment or native event publication, refresh the exact receipt, web SHA,
worker digest, both group IDs and serving health. Obtain separate operator approval for the
reviewed branch dispatch in [one-time finalization](worker-pools.md#one-time-initial-receipt-finalization).
The workflow's `finalize_initial_workers=true` input invokes the checksum-bound finalizer
without deploying the old package. A pre-commit failure retains `verified` and the original
recovery inputs. A post-commit cleanup interruption is reconciled by the same exact finalizer;
never restore an old package. Read back `committed`, the current fleet marker, both claims and
leases, public/private health, fresh metrics and absence of the original recovery inputs.
Only then may normal Deploy proceed. This commit proves the old remote release, not the new
historical command or image capability; it grants no enrollment.

## Gate 1b: deploy and prove the readiness code

After Gate 1a is read back `committed` with exact cleanup, separately approve a canonical ordinary Deploy of the
exact new 40-character `<APPROVED_READINESS_SHA>` containing this reviewed package. Record its
GREEN CI, expected web digest and worker digest. Prepare the reviewed matching remote manifest
and checksum through the existing worker-pool release configuration, with the same fixed group
identities and caps. Do not reuse the old initial SHA, old worker digest/manifest or arbitrary
moving `main` as the release.
Normal dispatch builds both images for the explicit SHA and performs the established remote
rollout under canonical Deploy. After completion and the reviewed manifest/checksum update,
the approved merge/push trigger can deploy the readiness release through the existing procedure;
record its exact resulting release SHA and build outputs. Alternatively, use this exact manual
dispatch, never a second concurrent Deploy:

```sh
gh workflow run deploy.yml --ref main -f deployment_sha=<APPROVED_READINESS_SHA>
```

Wait for this exact Deploy run to finish all public/private/application health and remote
cap-one rollout gates. This is a readiness-code deployment with **no enrollment** or event
switch. It may replace remote workers and incur their existing bounded VM/disk uptime; use
the existing deployment lease drain, disk fences and compatible remote recovery on failure.
If it fails or rolls back, stop here: healthy old workers do not satisfy this gate.

On the canonical host, read back only release/image identities, not full manifests or secrets:

```sh
cat /opt/photo-prjct/deployed-image
python3 -c 'import json; from pathlib import Path; r=json.loads(Path("/opt/photo-prjct/worker-pools-current.json").read_text()); c=r["manifest"]["configuration"]; print(json.dumps({"worker_build":c["worker_build"],"worker_image":c["worker_image"],"web_image":r["proof"]["web_image"],"web_id":r["proof"]["web_id"],"worker_id":r["proof"]["worker_id"]}))'
sudo docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}} {{index .Config.Labels "ru.findme-photo.historical-adaface-contract"}}' <READ_BACK_WEB_DIGEST> <READ_BACK_WORKER_DIGEST>
sudo docker compose --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml ps -q web
sudo docker inspect --format '{{.Image}}' <READ_BACK_WEB_CONTAINER_ID>
sudo docker compose --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py backfill_historical_adaface --help
```

Require the deployed web tag/revision and fleet `worker_build` to equal the approved readiness
SHA, both immutable digest references to equal the approved build outputs, the running web's
image ID to equal `web_id`, and both actual image labels to show that SHA and `vector-only-v1`.
The command help must expose the deployed dry-run, bounded enrollment and activation options.
Through the existing approved `remote-check` secret wrapper with its configured VM target and
known-hosts values, obtain fresh rollout verification (the commands below are the underlying
calls after that projection is materialized):

```sh
WORKER_POOL_OPERATION=status deploy/run-remote.sh worker-pools
WORKER_POOL_OPERATION=verify deploy/run-remote.sh worker-pools
```

Read back the release receipt `committed`, the matching `worker-pools-current.json`, settled
provider/disk operations and fresh coordinator/collector evidence for **both** pools at the
new worker build/digest. Retain the existing verify proof for paused/warm bulk and warm selfie,
healthy claims/private API and cap-one replacement; a local cached image or marker alone is not
remote rollout proof. No historical job may be enrolled yet. Only this evidence establishes
**deployed** readiness; historical live verification remains Gates 2–4.

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

## Gate 2: approve one bounded enrollment

Approve one event, its current cohort hash and a limit from 1 to 16. Review expected worker time,
preemptible bulk VM/disk uptime and object-transfer/model-inference impact. The accepted ceiling
remains bulk 0..1 and warm selfie 1..1; no extra pool/VM or canonical-host downsizing is included.
There is no current price quote in this package; resolve cost against the current billing context
before paid work. Foreground work has claim priority, but cannot interrupt an already running
historical attempt; bounded batch size limits the backlog introduced by each action.

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
  --apply --cohort-sha256 <CURRENT_COHORT_SHA256> --limit <1_TO_16>
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
provider timestamps, fresh metrics, worker build and durable attempts/results; graphs missing
the first counter sample are not absence-of-work proof.

Separately approve a compatible remote recovery rehearsal, including
loss/replacement of the only warm selfie worker and bulk wake with preserved jobs/leases. Use
existing capped release tooling, fixed group identities and complete disk/uncertain-operation
receipts. Through the existing approved secret wrapper:

```sh
WORKER_POOL_OPERATION=status deploy/run-remote.sh worker-pools
WORKER_POOL_OPERATION=verify deploy/run-remote.sh worker-pools
# Mutating examples: execute only after approval of the exact durable release receipt.
WORKER_POOL_OPERATION=rollout deploy/run-remote.sh worker-pools
WORKER_POOL_OPERATION=rollback deploy/run-remote.sh worker-pools
```

`rollout` resumes an established remote candidate; it does not select arbitrary new images.
`rollback` uses the receipt's previous compatible remote release and the existing serial
cap-one replacement/disk fences. It does not commit the
application marker independently. A prior release without native capability is rejected once
any active event depends on vector-only evidence. Use canonical Deploy with a reviewed new
manifest and matching app/worker SHA for repair forward; do not relabel an old image.

On uncertain response, first inspect status, pending provider operation and disk inventories.
Resume only the same recorded candidate. Fence uncertain members, stop new enrollment and let
durable lease recovery run through existing commands; never reset jobs/vectors or expand caps.
A fleet/private-network failure can pause processing. Record the measured recovery and service
impact; a planning assertion is not a completed rehearsal.

## Remaining model-data boundary

This repository package removes the production local photo/selfie placement and recovery
branches. The one-time finalizer cleans only the original recovery snapshot after the initial
remote receipt is committed. Verify the new package through ordinary Deploy and fresh live
read-back before Gate 2; code readiness alone does not prove production cleanup.

Both images declare `ru.findme-photo.historical-adaface-contract=vector-only-v1`. The canonical
guard reads native-generation state before package mutation and checks actual candidate web
and worker labels; digest/revision checks still bind release identity. A failed DB probe fails
closed. After event activation, an older image without native capability is not a compatible
rollback candidate. SFace support, historical JSON rows, Python readers, old-generation
evidence and the temporary reader gate remain for a later coordinated model-data retirement;
this runbook does not authorize their deletion.
