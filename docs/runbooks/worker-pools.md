# Isolated photo-worker pools

Bulk processing and selfie inference run in the existing isolated Yandex Instance Groups. The
canonical VM retains Django, PostgreSQL, Nginx, private worker API, queue/coordinator, collector,
imports and Commerce. [ADR 0051](../adr/0051-release-photo-worker-images-independently.md) defines
independent publication and host-owned container replacement. The initial remote-only fleet receipt
was read back `committed` on 2026-10-03; that earlier acceptance does not prove the new updater is
installed.

## Fresh inventory

Use canonical access `ssh -l petrnikitin 111.88.151.64`. Read current identities and bounded state:

```sh
cat /opt/photo-prjct/deployed-image
printf '%s\n' '{"operation":"status"}' | sudo docker compose --project-name photo-prjct --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py control_worker_pools
sudo docker compose --project-name photo-prjct --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py report_worker_pool_state --json
systemctl show findme-worker-pool-metrics.service \
  --property=Result --property=ExecMainExitTimestamp --property=ExecMainStatus
journalctl -u findme-worker-pool-metrics.service --since '10 minutes ago' --no-pager
```

Compare exact group IDs, caps/floors, complete VM and boot-disk inventory, pending provider
operations, claim state and live/expired leases. Bulk stays 0..1, idle zero; selfie stays warm at
1..1. A running VM or green workflow alone does not prove serving. Preserve jobs, attempts, vectors,
media and accepted results. Stop on drift, lease anomalies, stale collector, unsettled cloud
operations or missing private/public health. Keep credentials and customer data out of logs.

`report_worker_pool_state --json` is SELECT-only; due claimable work and leases are separate.
`report_worker_pool_telemetry --json` reports bounded host/runtime freshness. Missing evidence is
unknown. Inspect `worker_pool_queue_observation_timestamp_seconds`,
`worker_pool_cloud_observation_timestamp_seconds`, `worker_pool_native_publisher_success_timestamp_seconds`,
`worker_pool_running_instances` and `worker_pool_oldest_claimable_age_seconds` together with actual
sample timestamps. Groups/boot disks belong to the worker folder; demand metrics target the
canonical folder. Collector interval is 30 seconds, freshness 90 seconds. Read-only
`observe_worker_pool_cloud --config <path>` validates configuration; `--record` writes trusted
coordinator evidence. Operator-owned observation JSON remains needed for cloud scope. See
[worker alerts](../../deploy/monitoring/alerts.md#worker-pool-alerts); rule evaluation is not
notification-delivery proof.

## One-time updater installation at cap one

Follow the [accepted plan](../plans/2026-10-03-independent-worker-image-deployment.md). Re-read
live inventory and scoped config/baselines immediately before each cloud mutation. Pause both
remote claim pools and verify zero live attempts before the new web protocol/migration:

```sh
printf '%s\n' '{"operation":"pause","pool":"bulk","paused":true,"local":false}' | sudo docker compose --project-name photo-prjct --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py control_worker_pools
printf '%s\n' '{"operation":"pause","pool":"selfie","paused":true,"local":false}' | sudo docker compose --project-name photo-prjct --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py control_worker_pools
```

Read status and `report_worker_pool_state --json` again. Stop if claims are not paused, any live
attempt remains, or bulk is not idle at target zero. Merge the approved package so canonical Deploy
publishes worker `latest` and deploys the new web protocol/migration while claims remain paused.
The old worker may lack the updater and must not speak the removed claim protocol.

After web commits, use the reviewed installed package and exact existing group/config identities:

```sh
PYTHONPATH=/opt/photo-prjct/deploy/worker-pools/_canonical \
  python3 -B /opt/photo-prjct/deploy/worker-pools/provision.py \
  --config /absolute/path/to/reviewed-config.json --canonical-identity --install-updater
```

This patches only existing template metadata to install updater/bootstrap. It preserves scale/deploy
policies, resources, networking and cap one; bulk remains zero. The current
`OPPORTUNISTIC` deployment policy does not restart an already-running VM after a template change.
Read back the operation and full group baselines. Template installation does not prove the running
selfie host ran cloud-init.
With a fresh reviewed baseline and the exact sole **managed instance ID**:

```sh
PYTHONPATH=/opt/photo-prjct/deploy/worker-pools/_canonical \
  python3 -B /opt/photo-prjct/deploy/worker-pools/provision.py \
  --config /absolute/path/to/fresh-reviewed-config.json --canonical-identity \
  --replace-selfie EXACT_MANAGED_INSTANCE_ID
```

This separate one-time command issues `POST instanceGroups/{group_id}:rollingRecreate` with only
that exact `managedInstanceId`. It recreates the sole selfie VM at cap one; it does not create a
second VM or recreate bulk. Inspect an uncertain submission before retrying. Wait for settled cloud
state, the new updater boot, exactly one warm serving selfie member, private API health and fresh
metrics before unpausing. The command deliberately does not assert runtime verification.
Unpause only after those checks:

```sh
printf '%s\n' '{"operation":"pause","pool":"bulk","paused":false,"local":false}' | sudo docker compose --project-name photo-prjct --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py control_worker_pools
printf '%s\n' '{"operation":"pause","pool":"selfie","paused":false,"local":false}' | sudo docker compose --project-name photo-prjct --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T web python manage.py control_worker_pools
```

## Ordinary releases and compatible recovery

[Canonical Deploy](deployment.md) classifies each push. Backend-only deploy leaves the worker
pointer, containers and group templates untouched. Worker-only publication advances
`ghcr.io/peter-nikitin/photo-prjct-worker:latest` after runtime/model smoke. It contacts no canonical
VM and invokes no provisioning or fleet rollout. A new VM resolves latest; an existing host's timer
pulls it, starts a candidate alongside the predecessor and waits for warm readiness before changing
claim ownership and draining the predecessor. Bulk zero stays zero.

On the affected worker host, inspect updater service/timer and bounded journal output, root-owned
active state (`active.json`), replacement journal (`replacement.json`) and immutable slot references
under `/etc/findme-worker`.
Compare actual Docker image/build and process generation to coordinator status; launch metadata
may be stale after replacement. Inspect Docker `RestartCount`, `OOMKilled` and bounded `docker events`
alongside `journalctl`; failed or stale runtime evidence is unknown. The selfie claim cap one
remains enforced during overlap. Failure before admission leaves the old container serving.
Interrupted admitted replacement is reconciled before another pull. Observe overlap memory/disk
and customer-serving behavior live on the approved host shape.

For an explicitly selected compatible prior image, stop the updater timer first and run the
installed updater on that worker host:

```sh
sudo systemctl stop findme-worker-updater.timer
sudo python3 -B /usr/local/lib/findme-worker/updater.py \
  --image ghcr.io/peter-nikitin/photo-prjct-worker@sha256:EXACT_COMPATIBLE_DIGEST
```

If the result is `recovered`, rerun the override after admitted journal reconciliation. Read back
active image, readiness, serving generation and lease behavior. Keep the timer stopped while
latest differs from the intended recovery image; restarting it follows latest. Web recovery follows
the [schema compatibility boundary](deployment.md#migration-preflight-or-deployment-failure): an
old web SHA is not safe after `processing.0016` drops `ProcessingAttempt.worker_build`. Active native AdaFace requires selected images
to carry `ru.findme-photo.historical-adaface-contract=vector-only-v1`; the web guard checks DB and
candidate web without fetching workers. Protocol-breaking rollback needs explicit pause/drain and
compatible sequencing. Never rewrite durable work or restore queue-side build checks.

## Fixed boundaries

Bulk supports `1/capture_metadata/2`, `2/generate_preview/1`, `2/generate_watermarked_preview/1`,
`2/face_embedding/3`, `3/face_embedding/5`, `1/bib_recognition/1`; selfie supports `1/selfie_query/2`.
These semantic allowlists do not enroll photos or activate features. Transport stays private
authenticated TLS at `https://findme-photo.ru:8443/internal/photo-processing/v1`. Workers have no
public IP, cloud credentials or Docker socket; runtime status is loopback-only. Each container
processes one job at a time with two CPU and 5 GiB limits on the approved two-core, 8 GiB, 32 GiB
SSD VM. Capacity, IAM, network, secrets and paid-resource changes need their own reviewed operation.
Historical backfill remains a separate bounded enrollment after current deployment/runtime proof.
