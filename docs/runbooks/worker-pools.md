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

These commands address the existing `web` service before first activation. Once the two-slot
release is live, read the selected slot with
`sudo python3 /opt/photo-prjct/deploy/web-slot.py --root /opt/photo-prjct selected` and replace
`exec -T web` with that selected service in subsequent read-only checks.

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

## One-time CI push cutover at cap one

Follow [ADR 0051](../adr/0051-release-photo-worker-images-independently.md) and the steps below.
Repository support does not prove that this cloud cutover has happened. Re-read exact group and
managed-instance IDs, effective security-group rules, canonical VM security-group ID, current key
fingerprint, pending operations, scale/deploy policies and workloads before each cloud mutation.
Any previously recorded IDs are dated evidence; re-read them before use. Review old/new
configuration, cost (one selfie recreation without expansion), validation and rollback and obtain
the separate operational approval before any cloud or VM mutation. Then pause both remote claim
pools and verify zero live attempts before the new web protocol/migration:

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
attempt remains, or bulk is not idle at target zero. Keep the old web protocol running until the
existing worker templates, private access and sole selfie VM are ready. Do not merge a worker
release while the running worker still depends on the previous protocol.

The existing canonical config at
`/opt/photo-prjct/worker-pool-candidate-d8c1f6e-config.json` records the correct worker folder,
group IDs and cap one, but predates the SSH field and contains historical managed baselines. Read it
without printing secrets and prepare a local, ignored reviewed copy with freshly inspected
baselines and `worker_ssh_public_key` set to the public half of the existing Lockbox-backed CI
`VM_SSH_KEY`; its private half stays in CI. Allow worker port 22 only from the canonical VM's
security-group ID and read back the effective rule. A public CIDR or canonical `/32` rule is not
the intended source. The template creates `findme-deploy` with sudo limited to the one-shot updater.
Before editing the reviewed copy, derive the public key inside the existing temporary secret
projection. Set `REVIEWED_CONFIG` to the absolute path of the local ignored config copy (mode
`0600`) before this command. It updates only that copy and prints no private key or key path:

```sh
REVIEWED_CONFIG=/absolute/local/ignored/reviewed-config.json \
  .venv/bin/python scripts/run-with-environment-secrets.py \
    --consumer remote-check --identity yc -- .venv/bin/python -c '
import json, os, pathlib, subprocess
lines = pathlib.Path(os.environ["FINDME_ENV_FILE"]).read_text().splitlines()
record = next(line.split("=", 1)[1] for line in lines if line.startswith("VM_SSH_KEY_FILE="))
key = pathlib.Path(json.loads(record))
public = subprocess.run(["ssh-keygen", "-y", "-f", str(key)], check=True,
                        capture_output=True, text=True).stdout.strip()
config_path = pathlib.Path(os.environ["REVIEWED_CONFIG"])
if config_path.stat().st_mode & 0o777 != 0o600:
    raise SystemExit("reviewed config must be mode 0600")
config = json.loads(config_path.read_text())
config["worker_ssh_public_key"] = public
config_path.write_text(json.dumps(config, indent=2) + "\n")
print("CI public key added to local reviewed config")
'
```

Compare the derived public-key fingerprint with the existing CI deployment identity before the
cloud change. Re-inspect managed baselines, update the reviewed config, and verify the exact source
security-group rule. Use the reviewed local package and exact group/config identities. Replace
`REVIEWED_CONFIG` below with the same absolute local ignored path:

```sh
.venv/bin/python -B deploy/worker-pools/provision.py \
  --config REVIEWED_CONFIG --profile default --inspect
.venv/bin/python -B deploy/worker-pools/provision.py \
  --config REVIEWED_CONFIG --profile default --install-updater
```

This patches only existing template metadata to install updater/bootstrap and restricted SSH. It
preserves scale/deploy policies, resources, networking and cap one; bulk remains zero. The current
`OPPORTUNISTIC` deployment policy does not restart an already-running VM after a template change.
Read back the operation and full group baselines. Template installation does not prove the running
selfie host ran cloud-init.
With a fresh reviewed baseline and the exact sole **managed instance ID**:

```sh
.venv/bin/python -B deploy/worker-pools/provision.py \
  --config REVIEWED_CONFIG --profile default \
  --replace-selfie EXACT_MANAGED_INSTANCE_ID
```

This separate one-time command issues `POST instanceGroups/{group_id}:rollingRecreate` with only
that exact `managedInstanceId`. It recreates the sole selfie VM at cap one; it does not create a
second VM or recreate bulk. Inspect an uncertain submission before retrying. Wait for settled cloud
state, the new updater boot on the current worker image, exactly one warm serving selfie member,
private API health and fresh metrics. Check that
`findme-worker-updater.timer` is absent or disabled on the new host; retirement and telemetry timers
are separate. The recreate command does not assert runtime verification. If activation or serving
proof fails, keep claims paused, retain the observed running container and reconcile the exact
operation and template before another attempt.

Before merge, set and verify the six nonsecret GitHub repository variables used by CI discovery:
`WORKER_POOL_FOLDER_ID`, `WORKER_POOL_BULK_GROUP_ID`, `WORKER_POOL_SELFIE_GROUP_ID`,
`WORKER_POOL_SUBNET_ID`, `WORKER_POOL_SECURITY_GROUP_ID` and `WORKER_POOL_CANONICAL_VM_ID`.
`gh variable list --repo peter-nikitin/photo-prjct` is a read-only way to compare their current
values with the fresh cloud inventory. Also verify `VM_HOST`, `VM_USER` and
`VM_SSH_KNOWN_HOSTS`. With these nine nonsecret values exported to the local shell from their
reviewed settings, prove the current-image path through the canonical VM before publishing a
protocol-changing image:

```sh
.venv/bin/python scripts/run-with-environment-secrets.py \
  --consumer remote-check --identity yc -- \
  .venv/bin/python deploy/worker-pools/activate.py
```

Expect `WORKER_PUSH_RESULT=success` and read back the still-current image and serving generation.
Worker host keys are scanned over the authenticated canonical/private VPC path and pinned for that
run; this is not an independently established durable host identity. Once access, current-image
activation and these settings are proven, merge
the one reviewed package. Its Deploy publishes the compatible worker image and new web protocol,
activates the running worker and must finish green. Confirm selected web image, worker image,
private/public health, serving generation, claim/lease state and bulk target zero. Only then unpause:

```sh
printf '%s\n' '{"operation":"pause","pool":"bulk","paused":false,"local":false}' | sudo docker compose --project-name photo-prjct --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T "$(sudo python3 /opt/photo-prjct/deploy/web-slot.py --root /opt/photo-prjct selected)" python manage.py control_worker_pools
printf '%s\n' '{"operation":"pause","pool":"selfie","paused":false,"local":false}' | sudo docker compose --project-name photo-prjct --env-file /opt/photo-prjct/.env \
  -f /opt/photo-prjct/docker-compose.deployment.yml \
  -f /opt/photo-prjct/docker-compose.https.yml \
  exec -T "$(sudo python3 /opt/photo-prjct/deploy/web-slot.py --root /opt/photo-prjct selected)" python manage.py control_worker_pools
```

## Ordinary releases and compatible recovery

[Canonical Deploy](deployment.md) classifies each push. Web/import-only deploy leaves the worker
pointer, containers and group templates untouched. Worker-only publication advances
`ghcr.io/peter-nikitin/photo-prjct-worker:latest` after runtime/model smoke. CI discovers approved
running members, then uses the existing Lockbox `VM_SSH_KEY` through the canonical VM as
`ProxyJump` to invoke each member's one-shot updater on private SSH. Canonical and worker host
keys are pinned for that run; exact group/network identities and restricted sudo are required. Each running
member pulls the image, warms a candidate beside the predecessor, changes claim ownership only
after readiness and drains the predecessor. Discovery, SSH or warm-up failure fails Deploy;
inspect members separately if activation was partial. A new VM boots from `latest` and CI does
not start the idle bulk group. Bulk zero stays zero.

On the affected worker host, inspect the one-shot updater service, bounded journal output,
root-owned active state (`active.json`), replacement journal (`replacement.json`) and immutable slot
references under `/etc/findme-worker`.
Compare actual Docker image/build and process generation to coordinator status; launch metadata
may be stale after replacement. Inspect Docker `RestartCount`, `OOMKilled` and bounded `docker events`
alongside `journalctl`; failed or stale runtime evidence is unknown. The selfie claim cap one
remains enforced during overlap. Failure before admission leaves the old container serving.
Interrupted admitted replacement is reconciled before another pull. Observe overlap memory/disk
and customer-serving behavior live on the approved host shape.

For exceptional maintenance with an explicitly selected compatible prior image, run the installed
updater on that worker host:

```sh
sudo python3 -B /usr/local/lib/findme-worker/updater.py \
  --image ghcr.io/peter-nikitin/photo-prjct-worker@sha256:EXACT_COMPATIBLE_DIGEST
```

If the result is `recovered`, rerun the override after admitted journal reconciliation. Read back
active image, readiness, serving generation and lease behavior. The next CI worker release follows
the current `latest` pointer. Web recovery follows
the [schema compatibility boundary](deployment.md#migration-preflight-or-deployment-failure): an
old web SHA is not safe after `processing.0016` drops `ProcessingAttempt.worker_build`. After legacy-vector contraction, only an AdaFace-only compatible worker image can be selected;
read back its build and readiness. Protocol-breaking rollback needs explicit pause/drain and
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
