# Isolated photo-worker pools

Bulk photo processing and selfie inference run in the existing isolated Yandex Instance Groups. The canonical VM retains Django, PostgreSQL, Nginx, the private worker API, queue/coordinator, collector, imports and Commerce. The supported production recovery path is remote fleet repair or a compatible remote release. See [ADR 0049](../adr/0049-retire-local-photo-worker-recovery-after-remote-acceptance.md) and the [approved transition plan](../plans/2026-10-02-remote-only-photo-worker-operations.md).

## Current transition and authority

The last 2026-10-02 read found the initial remote receipt `verified`, not `committed`: the remote fleet had served real work, local photo/selfie containers were absent, both remote claims were open, selfie had one warm member and bulk had zero at idle. The pinned web build was `18b8c27eb18b65fcf663b0e713df6142ebd39321`, worker digest `sha256:1b4073b781995e89b0ee0025a98a7739f4e98b776ad91c77c643a483ef2f1bbc`, bulk group `cl13d5ffaml0f42s0s0s`, and selfie group `cl1136g0efv0pl7u3d9a`. These are last-observed identities, not fresh production proof. Refresh them before acting. Code review and CI make the finalizer code-ready; they do not commit the receipt, deploy the new package or establish live verification. Historical backfill has not started under this plan.

The initial finalizer needs separate approval of its exact reviewed branch, script checksum, four pins, fresh state, impact and recovery. It uses the canonical deployment lock and installed release journal. It does not reinstall the pinned old application, rebuild images, alter group policies or enroll historical work. No new cloud resources or billing limit are created; existing pool VM/disk uptime continues. An ordinary Deploy remains fenced until the initial receipt is committed and cleanup is read back.

## Fresh read-only inventory

Use canonical access `ssh -l petrnikitin 111.88.151.64`. On the host, inspect the receipt and deployment identity without editing them:

```sh
PYTHONPATH=/opt/photo-prjct/deploy/worker-pools/_canonical \
  python3 -B /opt/photo-prjct/deploy/worker-pools/release.py status --root /opt/photo-prjct
cat /opt/photo-prjct/deployed-image
sudo docker compose --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py report_worker_pool_state --json
systemctl show findme-worker-pool-metrics.service \
  --property=Result --property=ExecMainExitTimestamp --property=ExecMainStatus
journalctl -u findme-worker-pool-metrics.service --since '10 minutes ago' --no-pager
```

Read back both exact group IDs, actual member build/digest, caps/floors, complete VM and boot-disk inventory, pending provider operations, claim state and current/expired leases. Inspect public and private API health, web/nginx logging and a fresh web log probe; check collector and native metric timestamps with exact pool/zone selectors. A `RUNNING` VM, empty queue, stale graph or green workflow does not prove serving. Bulk zero is valid at idle only when the accepted 0..1 policy and absence of an unexplained retained boot disk are proven; selfie remains warm at 1..1. Stop on identity drift, unhealthy member, unsettled provider/disk operation, stale collector, live lease anomaly or missing health/probe evidence. Keep secrets, raw object keys and customer data out of shared logs.

`report_worker_pool_state --json` is SELECT-only. Its `claimable` count covers due jobs satisfying durable claim predicates; active and expired leases are separate. It does not prove object storage, private network, grant creation or worker health. `report_worker_pool_telemetry --json` gives bounded host/runtime freshness and outcome aggregates. Missing, stale or partial source evidence is unknown, never healthy zero. A fresh complete empty cloud snapshot explicitly reports zero; `worker_pool_capacity_fresh=0` with no running gauge means capacity is unknown. Inspect `worker_pool_queue_observation_timestamp_seconds`, `worker_pool_cloud_observation_timestamp_seconds`, `worker_pool_native_publisher_success_timestamp_seconds`, `worker_pool_running_instances` and `worker_pool_oldest_claimable_age_seconds` together, using configured `pool` and `zone_id` labels and actual sample timestamps. The native demand publisher targets the canonical folder, while groups and their boot disks belong to the worker folder. The collector runs every 30 seconds and freshness is bounded to 90 seconds. For diagnostic selectors and alerts, see the [monitoring controls](../../deploy/monitoring/prometheus/README.md#optional-isolated-worker-diagnostics) and [worker alerts](../../deploy/monitoring/alerts.md#worker-pool-alerts). Alert rule evaluation alone does not prove recipient delivery.

On an approved worker host, compare the canonical `{"operation":"status"}` coordinator receipt with fresh cloud membership, then inspect only the affected member's `journalctl` for cloud-init, Docker and worker service, and Docker `RestartCount`, `OOMKilled` and bounded `docker events`. Treat failed or stale host/runtime evidence as unknown. A warm member is not necessarily serving; compare private claim and attempt movement. The selfie claim cap one remains even if a later reviewed pool ceiling increases.

The configured read-only `observe_worker_pool_cloud --config <path>` validates the exact group IDs without a cloud request; its `--record` mode submits a complete cloud snapshot to the coordinator and must be treated as a state write. A partial page, changed group, unknown member or missing actual image proof rejects the observation. The following commands require the approved `remote-check` secret projection and configured `VM_HOST`, `VM_USER` and `VM_SSH_KNOWN_HOSTS`; execute them through that existing wrapper:

```sh
WORKER_POOL_OPERATION=status deploy/run-remote.sh worker-pools
WORKER_POOL_OPERATION=verify deploy/run-remote.sh worker-pools
```

## One-time initial receipt finalization

Resolve `<REVIEWED_BRANCH>` to the reviewed PR head. For its immutable commit `<REVIEWED_COMMIT_SHA>`, obtain the script digest with `git show <REVIEWED_COMMIT_SHA>:deploy/worker-pools/finalize_initial.py | shasum -a 256` and use that value as `<REVIEWED_FINALIZER_SHA256>`. Compare the four pins to the fresh receipt, deployed web image, immutable worker digest and actual group IDs. Review the exact dispatch with the operator before execution:

```sh
gh workflow run deploy.yml --ref <REVIEWED_BRANCH> \
  -f finalize_initial_workers=true \
  -f finalizer_source_sha256=<REVIEWED_FINALIZER_SHA256> \
  -f finalizer_web_sha=<FRESH_PINNED_WEB_SHA> \
  -f finalizer_worker_digest=sha256:<FRESH_PINNED_WORKER_DIGEST> \
  -f finalizer_bulk_group=<FRESH_BULK_GROUP_ID> \
  -f finalizer_selfie_group=<FRESH_SELFIE_GROUP_ID>
```

The dispatch must run from a reviewed non-`main` branch. The workflow checks out that branch's exact commit and sends only the checksum-bound finalizer through `deploy/run-remote.sh finalize-initial-workers`. It acquires `/opt/photo-prjct/.deployment.lock`, verifies the installed journal and both pools, then uses the existing release commit. It removes only the original `.deployment-recovery` files and the exact package path named by that snapshot. It does not remove jobs, vectors, media, DB data, worker groups or disks.

If a gate fails before commit, leave `verified` and the original recovery inputs intact. Diagnose and retry only the same reviewed pinned action after a fresh inventory and approval. If cleanup fails after journal commit, preserve the `committed` receipt and run the same exact finalizer to reconcile only the remaining receipt-owned recovery files. Do not deploy an old package or edit the marker. A successful workflow is followed by fresh read-back of `committed`, the exact `worker-pools-current.json` marker, both group/claim/lease states, settled provider disks, health and metric timestamps, and absence of the original recovery inputs.

## Ordinary remote-only Deploy and recovery

Only after the committed marker and cleanup are read back, deploy the reviewed new SHA through the normal Deploy workflow. Merge/push to `main` triggers it; an exact manual dispatch can use `gh workflow run deploy.yml --ref main -f deployment_sha=<APPROVED_NEW_SHA>`. Use the reviewed manifest/checksum with the existing fixed groups and 0..1 bulk / 1..1 selfie policy. This is a new application and remote worker rollout, with the existing bounded VM/disk usage; it is not historical enrollment. Wait for all Deploy gates and read back the deployed web SHA/digest, fleet build/digest, actual members, provider/disk settlement, claims/leases, collector, web/private/public health, import and Commerce paths. The new package is deployed only when those identities and gates match; live-verified processing needs actual fresh work and metrics. The [historical backfill runbook](historical-adaface-backfill.md) starts separately approved bounded enrollment only after this read-back.

Normal fleet operations accept `status`, `verify`, `rollout` and `rollback` through `WORKER_POOL_OPERATION`. `rollout` resumes an established receipt, and `rollback` uses its previous compatible remote image. Neither chooses arbitrary images or advances the application marker independently. For a lost response, inspect the release journal, pending provider operation and complete disk inventory before resuming the same candidate. One pool may temporarily expand during cap-one replacement; the controller settles that pool and proves the old boot disk absent before touching the other. Do not reset durable jobs, leases, vectors, media or accepted results. If remote service fails, pause affected claims and repair the remote pool or deploy a compatible remote image. Once a native AdaFace event is active, both web and worker images must carry `ru.findme-photo.historical-adaface-contract=vector-only-v1`; an older image without that capability is not a rollback candidate.

## Fixed pool and provisioning facts

Bulk has `1/capture_metadata/2`, `2/generate_preview/1`, `2/generate_watermarked_preview/1`, `2/face_embedding/3`, `3/face_embedding/5` and `1/bib_recognition/1`; selfie has `1/selfie_query/2`. These allowlists do not enroll photos or activate feature gates. Each pool uses the private authenticated TLS endpoint `https://findme-photo.ru:8443/internal/photo-processing/v1`, worker-folder group ownership, and canonical-folder native Monitoring publication. Workers have no public IP or inbound listener. Each worker processes one job at a time, with two CPU and 5 GiB container limits on the approved two-core, 8 GiB, 32 GiB SSD VM shape. Capacity changes, provisioning, IAM, network, secret and paid resource work need their own reviewed operation. The [dated worker-folder handoff](../operations/2026-09-30-worker-folder-operational-handoff.md) is historical evidence, not a current provisioning order.

For a future reviewed release, `deploy/worker-pools/provision.py --config <nonsecret-json>` prepares a checksum-bound dry run; `--inspect --profile <yc-profile>` reads exact worker/canonical folder and IAM/network prerequisites. `--apply` is a separately approved cloud write against a reviewed checksum and private receipt. Existing group IDs and baselines, folders, network, account authority, image digest and ceiling must be explicit; no CLI default folder or guessed predecessor is accepted. An uncertain provider submission must be reconciled from its receipt and complete group/VM/disk reads before another action. Boot image preparation is described by [the image recipe](../../deploy/worker-pools/image.py); it is not part of an ordinary Deploy.
