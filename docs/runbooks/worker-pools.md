# Isolated worker pools

This runbook describes the worker-pool contract and its read-only state report.
Code preparation does not provision paid resources, deploy a worker release, or cut over
the current workers. Provisioning and live cutover require the separate approved rollout.
The example contract is [contract.env.example](../../deploy/worker-pools/contract.env.example);
it is design data, not an executable provisioning script.
The [2026-09-30 worker-folder amendment](../superpowers/specs/2026-09-30-isolated-worker-folder-activation-design.md)
replaces the earlier same-folder provisioning assumption. The
[dated operational handoff](../operations/2026-09-30-worker-folder-operational-handoff.md)
lists known IDs separately from resources that have yet to be created.

## Scope and dated starting point

The read-only inventory on 2026-09-27 found deployed revision
`7bfb0598dbfe81e715aeb608150d61b936a26e79`. This is evidence about that observation,
not a claim that subsequent deployments still run this revision.

The refreshed integration inventory on 2026-09-28 records deployed revision
`866a894adf6b5ac1bba5bda2a4920cf88b661bbf`, the deployed `processing/0011` pgvector
schema and `selfie_search/0006` reader context, and `pgvector-face-search-read=on`.
This worker package preserves that baseline, its pinned pgvector 0.8.6 database image,
accepted numerical policy (ADR 0041) and operator gate state. Worker coordination adds
`processing/0012`; no vector backfill, reader change or gate activation is part of relocation.
Those inventories record local worker placement; refresh runtime state before activation.
Remote activation and telemetry delivery still require separate live evidence.

The 2026-10-01 launch attempt failed and was aborted; the original local environment,
package, workers and public health were restored. The remote launch is not accepted.
Both new worker VMs failed bootstrap at about 335 seconds of uptime, with cloud-init final
running for about 304 seconds. A Docker operation timeout is possible, but the failing
phase and root cause are unresolved. Bootstrap now reports only a safe phase and error
category for a future, separately approved attempt; do not infer readiness from that log
or change timeouts based on this timing alone.

The main VM retains Django, PostgreSQL, media services, Yandex Disk imports and commerce.
Only photo processing and selfie query workers move into their own pools. The import worker
and commerce worker remain unchanged. Bulk backfill remains dependent on the accepted
pgvector capability; adding bulk VMs does not implement or approve that backfill dependency.

Both pools use Intel Ice Lake (`standard-v3`), two cores at 100%, 8 GiB memory and a
32 GiB `network-ssd` boot disk. Each worker processes one job at a time and preserves
the existing container limit of two CPUs and `5g` memory (5 GiB), leaving VM headroom.
The approved initial autoscaling ceiling is one per pool: regular selfie instances use
bounds 1..1, while preemptible bulk instances use bounds 0..1. Bulk scale-to-zero and wakeup
remain mandatory live acceptance checks. The separately activated ceiling of two preserves
selfie bounds 1..2 and bulk bounds 0..2; activation of the second live selfie instance waits
for functional acceptance. These bounds do not authorize provisioning or changes to the
current live instance count.
The [20/24 GiB disk diagnosis](../operations/2026-09-29-worker-disk-sizing.md) is sizing
evidence only; the approved shape retains 32 GiB. One bulk VM replaces two local bulk
processes, so reduced bulk concurrency and longer queue time are accepted at this stage.

## Fixed, disjoint capabilities

Bulk identities are `1/capture_metadata/2`, `2/generate_preview/1`,
`2/generate_watermarked_preview/1`, `2/face_embedding/3`, `3/face_embedding/5`,
and `1/bib_recognition/1`. Selfie has only `1/selfie_query/2`.
The three parts are contract version, processor type and processor version.
Workers receive their own list through existing `PHOTO_WORKER_PROCESSOR_IDENTITIES`.
An identity in this allowlist does not enable an experiment, enroll photos, publish results,
or turn on an operator-controlled feature gate. Preserve disabled identities and gates.

## Private connectivity and TLS

Workers have no public IP or inbound listener. The private API uses
`https://findme-photo.ru:8443/internal/photo-processing/v1`, with the actual private IPv4
address supplied explicitly at deployment time. Worker-local hostname mapping resolves the
canonical hostname to that address. There is no baked-in fallback to the public IP.
Normal TLS verification checks `findme-photo.ru` against the existing Certbot certificate;
the existing renewal mechanism remains authoritative. There is no new CA or public API route.
The private listener remains an explicit canonical Deploy overlay. Setting placement to remote
requires a reviewed per-release manifest and successful attached-SG allow-union preflight.

## Read-only queue observation

On a configured backend checkout, use its explicit Python runtime:

```sh
.venv/bin/python src/backend/manage.py report_worker_pool_state --json
```

The command emits one JSON object with explicit zero counts, `empty`, an observation time,
and per-pool totals and per-identity rows. Optional `--bulk-identities` and
`--selfie-identities` accept only nonempty, duplicate-free subsets of the fixed corresponding
pool allowlist. Unknown identities and cross-pool identities are rejected. Processing rows
outside the chosen allowlist appear only as aggregate unassigned counts, never as arbitrary
processor names.

`jobs` and `attempts` contain every durable status, including terminal statuses.
`retries.due` counts retry-wait jobs available at the observation time;
`retries.future` counts those scheduled later. These are durable retry counts, so a due retry
may still fail a claim prerequisite. `claimable` counts due queued/retry-wait rows using the
existing claim services' durable predicates. Bulk requires a collecting/sealed run and the
matching current photo-processing-state job. Selfie uses `SelfieSearchJob`, requires a queued
`SelfieSearch` and a nonempty temporary object key, and does not count `ProcessingJob` rows.
Claimability does not check external object storage, grant creation, input geometry or network
reachability; failures there remain API failures and must be checked during live acceptance.

`endpoint_enabled` separately reports whether the processing API is enabled and has a configured
worker credential, without revealing that credential. `enrollment_flags` reports face/preview
enrollment settings. Disabling enrollment does not revoke already durable work. Disabling the
endpoint does not erase backlog: `claimable` remains the durable candidate count, and a controller
must handle endpoint unavailability separately. This distinction prevents stranded durable jobs.

`leases` counts in-progress current attempts with active, expired or missing expiry values.
Bulk current ownership uses the photo-processing-state job and attempt pointers; selfie requires
the job and search to remain processing. The command does not recover or expire an attempt.
Accepted-attempt, current accepted-attempt, derivative, face artifact/detection/embedding and bib
aggregates accompany bulk counts. Selfie includes succeeded-attempt, result, published-result
and direct/cluster evidence aggregates. Selfie has no persisted `accepted` flag, so its
`accepted_attempts` is the count of succeeded attempts, not a new inferred acceptance state.

The report emits counts and bounded identities only: no event/photo/person identifiers,
vectors, object keys, bearer tokens, passwords, raw URLs or result payloads. It runs SELECTs;
there is no recovery, enrollment, publication, expiration, schema change or other mutation.
Counts are observations across SELECTs while workers may progress, not a frozen transactional
snapshot. For cutover preservation checks, compare observations after the approved drain or
with workers stopped, and distinguish expected processing progress from lost data.

## Optional worker diagnostics

Phase-one repository capability provides a separate 16 KiB diagnostic receipt and private
canonical scrape. It does not authorize paid activation or establish cloud delivery. Enable
the compatible receiver/schema first, then explicitly opt in a reviewed worker's
`telemetry_enabled=true` bootstrap input, then separately approve canonical agent delivery.
The host image must already contain `/opt/findme-worker-telemetry/bin/python` with the pinned
dependencies from `deploy/worker-pools/telemetry-requirements.txt`; bootstrap installs no packages.
The host oneshot timer runs every 30 seconds independently of worker startup. It keeps one
private latest snapshot, uses immutable reviewed zone and the existing fleet token, and has
no processing or cloud mutation authority. Default worker/runtime and sender collection are off.

On the configured backend, inspect aggregates through SELECT-only diagnostics:

```sh
.venv/bin/python src/backend/manage.py report_worker_pool_telemetry --json
```

The bounded report gives bulk/selfie expected, cloud-fresh, missing, host-fresh and runtime-fresh
counts and emitted scalar sample counts, without payloads, product IDs or individual source IDs.
`remote_write=unverified` and `alerts=deferred` explicitly delimit local evidence. Expected nodes
without a receipt use `zone_id="unknown"` in exposition; zone is never guessed from cloud data.
An empty fresh membership has no missing-node series. Readiness/serving/heartbeat derive from
the existing coordinator and cannot be changed by a receipt.

Host RAM/root bytes describe the VM; Docker usage/limits describe the one fixed container.
Failed groups are omitted. Source and receipt freshness must both be <=90 seconds; runtime
also requires current registration generation and fresh scrape time. Duplicate/replayed sources
cannot renew freshness. Rate/delta and duration consumers must exclude windows containing a
freshness gap or a change of `worker_runtime_reset_timestamp_seconds`; ordinary `rate` cannot
detect every process reset when its first new counter exceeds the preceding old counter.
Apply the analogous guard using `worker_container_reset_timestamp_seconds` for restarts.
Container/boot/process IDs are protocol fences only, never metric labels. Docker retrospective
events return at most the last 256 global events, so positive OOM/restart values are lower-bound
evidence, `events_available=false` expresses unknown completeness, and omitted/empty values do
not prove zero incidents. Runtime outcomes describe delivered callbacks, not accepted results.

For a local synthetic TLS/container rehearsal using the existing reviewed local fixture image:

```sh
DB_PORT=5432 sh scripts/run-in-test-env.sh .venv/bin/pytest tests/deployment/test_worker_telemetry_delivery.py -n 0 -s
```

The fixture skips if the fixed `findme-photo-worker` container exists, uses a unique ownership
label and exact returned container ID, and cleans only its synthetic container. It exercises
real runtime HTTP scrape, the real probe's verified HTTPS client and actual Django receipt,
duplicate/reset/staleness and lease renewal during ingestion failure. Fixture CA, resolution
and alternate local ports exist only in the test process; production keeps canonical hostname,
system CA, rejected redirects and ignored proxies. No production DB, private snapshot DB,
Object Storage, cloud writes or broad container/image/volume cleanup belongs to this rehearsal.

Maximum-valued measured synthetic envelopes with all permitted pairs, a 64-character instance
ID and a fresh coordinator heartbeat emitted 255 scalar samples for bulk (20 pairs, 5274 compact
JSON bytes) and 79 for selfie (4 pairs, 2075 bytes). These measured fixture sizes fit 16 KiB;
identity/timestamp lengths affect actual bytes.
Each pair adds 11 samples: execution counter, 8 buckets, duration sum and count. At four
concurrent VMs, two per pool, that measured ceiling is 668 samples per 30-second scrape,
1336 samples/minute. It is a cost input, not a production measurement or price approval.
Historical cardinality also grows with distinct instance IDs over retention; each new maximum
bulk/selfie source can add 255/79 series, and an unknown→reviewed zone receipt may retain an
additional missing-source label set. Include measured source churn, retention, scrape gaps,
active kind/outcome pairs and actual ingested samples in a separately approved cost estimate.

Canonical opt-in uses the existing agent/channel and metadata IAM described in the
[monitoring README](../../deploy/monitoring/prometheus/README.md#optional-isolated-worker-diagnostics).
Keep native demand/capacity, public health, HTTP and Commerce controls active. Validate actual
ingested timestamps, allowed labels, source freshness/reset boundaries and measured volume
before declaring delivery; retained buffered data or a retained `fresh=1` sample is insufficient.
The phase-one diagnostic receipt does not activate ADR 0048's Git-owned rules, routing or
notification firing/no-data/recovery; these still require separate Monitoring apply and live
acceptance. On loss,
inspect exact timer/receiver/sender status and preserve unknown state and existing lease authority.
Rollback disables worker probe/runtime opt-in and re-renders canonical config without
`--worker-telemetry`, using the existing reviewed host installer/rollback only after approval.
Retain additive schema and current product/vector/gate state; no migration reversal or old-image
restore over the deployed pgvector contract.

## Canonical demand publisher and cloud reader

`publish_worker_pool_metrics --zone <zone>` is a local read-only dry run by default. It emits
explicit gauges for both queues using only `pool` and `zone_id` labels. Workload is due/current
claim candidates plus current active leases plus current expired recoverable leases. Historical
attempts and future retries do not create demand. The expired-lease term wakes capacity after
bulk preemption at zero; the existing claim endpoint performs recovery when that capacity wakes.
The publisher performs no enrollment, recovery, attempt/job changes or feature activation.
Missing lease expiry or an unavailable endpoint is a fault, with no fake zero publication.

Only `--publish --folder-id <canonical_folder_id>` writes Monitoring. The worker folder is not
the native metric namespace. It fetches a fresh short-lived metadata
IAM identity, verifies TLS, refuses redirects and proxy fallback, limits the response body, and
requires the documented full `writtenMetricsCount` with no `errorMessage`. The coordinator's
queue timestamp is recorded only after that complete success, atomically for both configured
pools. A failed publication leaves the last success intact and becomes stale after the
coordinator's existing 90-second limit. Web startup does not invoke the publisher.

`observe_worker_pool_cloud --config <path>` validates and shows exact configured group IDs
without a cloud request or coordinator write. `--record` performs the trusted canonical read
and submits complete snapshots through the existing coordinator service. Its nonsecret JSON
has exactly `folder_id` (worker), `canonical_folder_id`, `zone`, `groups` (bulk/selfie IDs),
`boot_image_id` and `releases`
(one or two SHA-to-digest-image mappings). It reads Get with `view=FULL`, completes every member
page in the worker folder, validates exact group/folder/zone/target and actual per-instance
metadata, and reads each running machine's worker-folder boot disk source image. Same-folder,
missing-folder and older single-folder inputs reject. Unknown states, duplicates, over-capacity, missing
actual image proof, partial pages or a changing group reject the whole observation.

Running old nodes retain their own `findme-worker-build` and `findme-worker-image` evidence;
the current template never relabels them. Known in-flight slots can have no physical instance ID
or build and count towards the hard maximum; they cannot register or claim. Stopped/deleted
evidence and complete post-grant snapshots reconcile retirement through the existing service.
The reader does not create synthetic machine IDs or new retirement authority. Its timestamps and
sequence use the existing ordered observation API; failed reads do not erase reservations.

Prepared canonical units are `metrics.service` and `metrics.timer`, installed under names
`findme-worker-pool-metrics.service` and `.timer`. Their host collector runs independently of
worker count every 30 seconds, first cloud observation, then queue publication. Installation
and identity grants belong to the separately approved activation. The `stage` Deploy action
calls the preinstalled, root-owned `/usr/local/sbin/findme-worker-pool-metrics install`, which
installs and starts the timer and performs one collection. It fails closed when the helper,
reviewed root-owned source package, observation document, or first collection is missing.
The helper accepts only `install`, `verify`, and `remove`; it compares the root-owned package
to the exact deployed candidate before installation. The operator must first verify the source
commit and file hashes, install `deploy/worker-pools/metrics-root-helper.sh` as that root-owned
helper and `metrics.py`, `metrics.service`, `metrics.timer` as mode 0644 files in
`/usr/local/lib/findme-worker-pool-metrics-package`, and grant the deploy user sudo access to
only that helper. This bootstrap and its sudo rule require the separate operational approval;
Deploy does not install arbitrary root code from its mutable package. The collector's
`/etc/findme-worker-pools/metrics.json` is:

```json
{"deploy_root":"/opt/photo-prjct","cloud":"/opt/photo-prjct/worker-pools-observation.json"}
```

The observation JSON is the current release journal's two-folder document. The collector
rejects a wrong path or invalid/equal folder IDs before running either command. After a cloud
read failure it still attempts authoritative demand publication to `canonical_folder_id`,
reports an incomplete collection, and leaves capacity unknown. Release-time publication uses
that same canonical folder. Neither publisher may substitute the worker folder or CLI default.

Use the existing Monitoring dashboard and alert manifest, preserving the public-health,
host and Commerce streams. Worker alerts distinguish no observation from observed backlog.

## Saturation, stalled work and missing-telemetry diagnosis

`worker_pool_running_instances` counts only RUNNING_ACTUAL/RUNNING_OUTDATED rows in the
coordinator's current complete ordered cloud snapshot, fresh within 90 seconds. A fresh empty
snapshot explicitly reports zero. Missing, rejected partial or stale evidence instead reports
`worker_pool_capacity_fresh=0` and omits the running gauge: unknown is not zero. Pending slots
still occupy the hard rollout ceiling but are not running capacity. Neither target size nor
workload nor historical process registrations are a capacity source. The collector attempts
demand publication even if its cloud read fails, then exits nonzero for the incomplete collection;
it never invents fresh cloud evidence. Existing trusted per-pool snapshots remain authoritative
until stale. Failed Monitoring publication still cannot advance successful queue freshness.

Worker alerting uses the existing Managed Prometheus package under
[ADR 0048](../adr/0048-reuse-managed-prometheus-for-worker-alerts.md), not a separate native
alert lifecycle. Native publication remains the autoscaler's input. The additional private
diagnostics scrape exposes read-only pool observations and numeric source timestamps.

The worker alert profile is enabled in Git for reviewed activation but not yet proven
live-applied. It targets ceiling one: fresh actual running capacity >=1,
claimable age above 300 seconds, and positive recent age growth sustained for five minutes.
Queue progress/reset or missing/stale sources breaks saturation. Separate queue, cloud and
native-publisher diagnostics identify unknown demand/capacity; retained positive history is not
current evidence. Fresh actual zero members in idle bulk is valid and requires no node metrics.
No alert changes limits, claims, jobs, releases or placement.

Before **any ceiling-one customer cutover**, complete the
[live acceptance gate](../future-work/2026-09-29-cap-one-worker-saturation-alert.md):
fresh raw source values, evaluator read-back, controlled sustained firing, missing-series and
retained-stale-source behavior, recovery and delivered notifications for both pools. Generic
email drills, local fixtures and a successful deployment do not clear this gate. Use the
[existing package runbook](../../deploy/monitoring/prometheus/README.md) for exact-SHA apply and
known-Git-revision rollback. Do not infer spare capacity from a quiet rule with unknown sources.

Worker VMs do not own a Unified Agent configuration: their telemetry timer sends host diagnostics
to the private canonical backend. The optional canonical scrape is owned by
`deploy/monitoring/prometheus/render_agent.py` (`--worker-telemetry`); omitting that option removes
the worker route. Repeated configuration and subsequent deployments must preserve all required
native and Prometheus routes in `/etc/yc/unified_agent/config.yml`. Validate `check-config`, active
`unified_agent.service`, and fresh cloud-ingested native autoscaler and Prometheus worker samples,
including source timestamps and expected-node identity. Repeat the cloud proof after deployment;
a running service or successful scrape alone does not prove delivery.

Only deploy the route-preserving `deploy/configure-monitoring-agent.sh` revision before activating
the worker scrape; older revisions replace the configuration and can remove Prometheus routes.
Offline repeated-refresh tests do not replace live configuration read-back and fresh cloud samples.
Coordinate deployment with observability; this package does not modify `deploy/image-origin/**`.

### Queue and processing-speed dashboard

The Git-owned Prometheus dashboard separates queue size, oldest waiting age, workload and actual
running capacity from worker execution throughput and duration. Runtime operations/minute and
duration distributions/p50/p95 are split by operation kind and outcome, with fresh current-node
evidence. A successful callback delivery is not a count of accepted photos; retries and separate
stages must not be summed into photo throughput. Runtime duration includes delivery and cleanup,
not queue waiting. Quantiles are estimates from the existing histogram boundaries.

"Фото с принятым превью/мин" measures clean `preview-small-v1` publications accepted by the backend
after transaction commit, averaged over five minutes. It uses the private application metrics
counter, not worker callbacks or historical database scans. Rollback, duplicate callbacks, failed
attempts and watermarked derivatives do not increment it. This is operational telemetry since
instrumentation was deployed, not historical accounting or completion of every assigned stage.
Clean preview acceptance alone also does not imply public availability under watermark policy.

Prepare and validate the dashboard before provisioning worker VMs. Deploy compatible backend
metrics and route-preserving configuration, then separately approve scrape/dashboard activation.
Fresh accepted-preview and queue data can be verified before remote workers exist. Missing
cloud/runtime data stays "no data", not zero. After approved provisioning, verify current-node
runtime graphs and controlled alert firing/recovery/delivery before customer cutover. Idle bulk
zero is valid only when fresh cloud observation confirms zero members.

On the canonical VM, use the actual Compose project, its reviewed overlays and existing .env;
the following read-only commands emit bounded queue/status data, not credential files:

```sh
docker compose --project-name photo-prjct --env-file /opt/photo-prjct/.env -f /opt/photo-prjct/docker-compose.deployment.yml -f /opt/photo-prjct/docker-compose.https.yml exec -T web python manage.py report_worker_pool_state --json
printf '%s\n' '{"operation":"status"}' | docker compose --project-name photo-prjct --env-file /opt/photo-prjct/.env -f /opt/photo-prjct/docker-compose.deployment.yml -f /opt/photo-prjct/docker-compose.https.yml exec -T web python manage.py control_worker_pools
systemctl status findme-worker-pool-metrics.timer findme-worker-pool-metrics.service --no-pager
journalctl -u findme-worker-pool-metrics.service --since '10 minutes ago' --no-pager
```

Compare two queue reports five minutes apart: `claimable`, oldest age, active/expired/missing
lease expiry, `jobs.succeeded/failed` and `attempts.succeeded/failed/expired` distinguish progress
from stalled work (cumulative outcomes are not a failure rate without a time delta). Status gives
`fresh`, `observed_members`, `target_size`, `claims_paused`, active/staged build and members'
`warm`, `serving`, `draining`, grants and reconciliation. Warm means a fresh ready heartbeat
matched to current running evidence; serving additionally requires unpaused active-build
admission. Two cloud VMs alone do not prove two healthy workers or two accepted selfie slots.

On each **exact observed isolated worker VM**, through already-approved private operator access,
inspect only the named worker container. No new SSH/IAM access is granted by this recipe:

```sh
docker inspect --format 'id={{.Id}} restart_count={{.RestartCount}} status={{.State.Status}} running={{.State.Running}} oom={{.State.OOMKilled}} exit={{.State.ExitCode}} started={{.State.StartedAt}} finished={{.State.FinishedAt}}' findme-photo-worker
docker events --since 10m --until "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --filter container=findme-photo-worker --filter event=restart --filter event=die --filter event=oom --format '{{.Time}} {{.Action}}'
journalctl CONTAINER_NAME=findme-photo-worker --since '10 minutes ago' --no-pager
journalctl -u docker -u cloud-final -u findme-worker-retire.service --since '10 minutes ago' --no-pager
```

Take RestartCount at start/end of the interval for the **same container ID**; delta divided by
elapsed minutes is restart rate. Recreated containers reset that counter, so record recreation
time and Docker die/restart/OOM event counts separately. Docker's recent event buffer is bounded;
journald and coordinator registration-generation changes corroborate it, not a promise of
durable historical telemetry. OOMKilled/ExitCode, timed failure events and queue failed-outcome
deltas are the failure sources for these isolated hosts; canonical VM CPU/container monitoring
does not cover them. Review worker journals locally and redact URLs/object keys/identifiers and
credentials before sharing; never dump full Docker inspect, environment or Lockbox payload.

- Missing/stale observation: inspect timer/service and publication outcome, canonical DB/API
  availability and exact cloud snapshot read/ownership/release allowlist first. Preserve unknown
  status; do not clear members/grants or treat absence as zero. Restore only approved collector
  configuration/transport, then obtain fresh observations and recovery notification.
- Stalled/overdue work: correlate the reviewed ceiling with warm/serving state,
  claims_paused, build mismatch, draining/grants, expired leases and worker restart/OOM evidence.
  Check private TLS/API and storage failures. Preserve existing lease recovery and release
  authority; do not manually expire attempts, grant retirement or auto-restart a serving worker.
- At-ceiling saturation: if the reviewed capacity is warm/serving, outcomes progress but age still rises,
  record the arrival/completion deltas and acknowledge bounded throughput. If progress stops,
  follow the stalled-work diagnosis instead of declaring capacity shortage. Communicate backlog
  impact and request a separate reviewed capacity/claim-policy decision if needed. Preserve
  reviewed steady maximum one (two only after separate approval) and selfie claim cap one;
  do not raise the limit, enable the second claim
  slot, enroll backfill or change feature gates as an incident workaround.

Record the bounded incident window, queue/status snapshots, exact VM/build/container identity,
restart/failure deltas and observed recovery. Alert creation/wiring, controlled failure drills,
worker-host access and notification proof remain separate approved activation work.

## Bounded fleet preparation and prerequisite boundary

`deploy/worker-pools/provision.py --config <nonsecret-json>` prepares a deterministic dry-run
package and SHA256 without contacting cloud APIs. It embeds checksum-bound repository host code,
Compose and nonsecret bootstrap configuration. Exactly two managed names are supported:
`findme-photo-worker-bulk` and `findme-photo-worker-selfie`, with repository ownership labels.
No generic resource reconciler, deletion/recreation command or main-VM mutation is provided.

The input requires these explicit reviewed values: `pool_max_size`, `cloud_id`, `folder_id`,
`canonical_folder_id`,
`zone`, `network_id`, `subnet_id`, `worker_sg_id`, `canonical_vm_id`, `private_api_ipv4`,
`worker_sa_id`, `manager_sa_id`,
`bootstrap_secret_id`, `bootstrap_version_id`, `application_secret_id`,
`boot_image_id`, `docker_version`, `compose_version`, `worker_build`, `worker_image`,
`egress_gateway_id`, `route_table_id`, and `groups`. Unknown input keys fail closed.
`folder_id` owns only new worker resources; `canonical_folder_id` owns the existing VM,
application secret and native Monitoring namespace. They must be distinct and in the reviewed
`cloud_id`. The existing VPC remains in the canonical folder and only the new subnet extends it
into the worker folder. No fallback to a configured CLI folder or legacy single-folder JSON is
accepted. Both IDs are immutable normal-release and rollback inputs; either mismatch aborts
before journal or provider mutation.
`groups` has exactly bulk/selfie entries, each with `id` and `baseline`; both null mean reviewed
expected absence for initial creation. For updates, use the exact existing group ID and the
managed-configuration baseline obtained by `--status`. Image is an immutable GHCR worker digest;
Docker/Compose versions refer to binaries already installed in the explicitly reviewed OS image.
For reactivation after a completed local abort and deletion of both former groups, add the
optional checksum-bound `predecessors` object to the **null-ID creation** config. It names each
old coordinator `group_id` and `active_build` exactly, for example
`{"bulk":{"group_id":"<OLD_BULK_GROUP_ID>","active_build":"<OLD_40_HEX_SHA>"},"selfie":{"group_id":"<OLD_SELFIE_GROUP_ID>","active_build":"<OLD_40_HEX_SHA>"}}`.
These are inspection inputs, never defaults; obtain them from fresh canonical status. Omit
`predecessors` only when neither coordinator row exists. A changed or partially populated
predecessor is not eligible for this path.
The following is the **complete nonsecret JSON shape**, with deliberately invalid placeholders
for resources that do not yet exist. Materialize it only after exact creation outputs and
fresh read-back; never run cloud commands with placeholders. The listed canonical VM/VPC/folder
IDs and address are from the [2026-09-30 preflight](../operations/2026-09-30-worker-folder-activation-preflight.md);
the application-secret ID is from the older [2026-09-28 proposal](../operations/2026-09-28-worker-pool-activation-approval.md).
All require a fresh check. The current deployed SHA/digest are not a future image pin.

```json
{
  "pool_max_size": 1,
  "cloud_id": "b1gmcsmr51o5kvp86l55",
  "folder_id": "<NEW_WORKER_FOLDER_ID>",
  "canonical_folder_id": "b1g2qttgfhb4gdunvlge",
  "zone": "ru-central1-b",
  "network_id": "enpevjgdgdavmrv9ahb8",
  "subnet_id": "<NEW_WORKER_SUBNET_ID>",
  "worker_sg_id": "<NEW_WORKER_SG_ID>",
  "canonical_vm_id": "epdr5g3p24tdns9890nr",
  "private_api_ipv4": "10.129.0.34",
  "worker_sa_id": "<NEW_RUNTIME_SA_ID>",
  "manager_sa_id": "<NEW_MANAGER_SA_ID>",
  "bootstrap_secret_id": "<NEW_WORKER_SECRET_ID>",
  "bootstrap_version_id": "<NEW_WORKER_SECRET_VERSION_ID>",
  "application_secret_id": "e6q85jjl76r45maigtfb",
  "boot_image_id": "<NEW_CLEAN_WORKER_IMAGE_ID>",
  "docker_version": "<REVIEWED_X.Y.Z>",
  "compose_version": "<REVIEWED_X.Y.Z>",
  "worker_build": "<CURRENT_REVIEWED_40_CHARACTER_SHA>",
  "worker_image": "<CURRENT_REVIEWED_GHCR_WORKER_SHA256_DIGEST>",
  "egress_gateway_id": "<NEW_WORKER_NAT_GATEWAY_ID>",
  "route_table_id": "<NEW_WORKER_ROUTE_TABLE_ID>",
  "groups": {
    "bulk": {"id": null, "baseline": null},
    "selfie": {"id": null, "baseline": null}
  }
}
```
`pool_max_size` must be an integer exactly 1 or 2; missing values, booleans, strings,
other ceilings and unknown policy inputs fail closed. It is part of the reviewed checksum.
The approved `pool_max_size=1` renders `scalePolicy.autoScale.maxSize="1"` for both groups,
with bulk `minZoneSize="0"` and selfie `minZoneSize="1"`. The WORKLOAD rule and observation
cadence remain unchanged; its `folderId` is `canonical_folder_id` even though both groups
belong to `folder_id`. `pool_max_size=2` preserves the accepted bulk 0..2 and selfie 1..2
policy for separately approved activation. Raising the ceiling is an explicit, baseline-bound
cloud change requiring separate review, including after quota approval.

`--inspect --profile <yc-profile>` performs read-only prerequisite checks and returns the same
prepared checksum. It verifies both folders' cloud membership; exact worker ownership of
group manager/runtime accounts, image, subnet, SG, NAT, route and bootstrap secret; canonical
ownership of VM, application secret and VPC; direct manager `compute.editor` only on the worker
folder and canonical cross-folder `vpc.user` plus `monitoring.viewer` (for native WORKLOAD
metric reads) without canonical Compute management; exact runtime
`lockbox.payloadViewer` on the worker bootstrap secret with no ancestor grant or application-
secret access; exact payload-key metadata; private
subnet/NAT route/gateway, worker SG ingress absence, and ALL canonical NIC SGs, including resolved
default groups. Any SG in the allow union that permits 8443 beyond the exact worker SG rejects
activation. Adding a restrictive group alongside a broad group cannot pass. It never changes
canonical SGs, routes, SSH, public 80/443, imports or monitoring.

Creating the prerequisite accounts, IAM bindings, separate secret/version, subnet, SG rules,
NAT/route and reviewed OS image is **not implemented** by this command and is not authorized by
code preparation. An operator must review effective identity authority, including organization
inheritance and grants on other resources; the automated direct cloud/folder/exact-resource
checks are not a claim of an exhaustive organization IAM audit. Before activation, prove private
TLS success, public 8443 denial, permitted outbound DNS/HTTPS, and the intended identity isolation.
The operator must also read back the manager's exact `iam.serviceAccounts.user` binding on the
runtime account before group creation; folder grants are not a substitute. Canonical inspect
does not query that account's access-binding list because the required API read itself needs
service-account admin authority outside the canonical deployer's scope.
Cloud API inspection does not replace these live proofs.

After those prerequisites and current cost are reviewed and separately approved, explicit
`--apply <reviewed-sha256> --profile <yc-profile> --receipt <fresh-private-path>` can submit only
the two exact worker-folder group creates or baseline-bound updates. It rechecks expected absence/ownership/drift
and writes a durable private receipt **before** each mutation. Receipts record returned operation
and group IDs plus both folder IDs; submission is not rollout success. Existing receipt paths
refuse resubmission. Changing either folder changes the configuration checksum. Keep private
config, receipt, release journal and any token out of Git and shared logs.
For a null-ID create, `--apply` additionally requires `--canonical-ssh-target <user@host>`,
`--canonical-root <absolute-deploy-root>` and
`--canonical-manifest <absolute-path-to-the-exact-receiver-creation-manifest>`. The supported
order after `receiver` is: inspect the reviewed creation config, read the canonical eligibility
below, then run the operator-owned `--apply` with those three arguments. For example, with
private paths and a reviewed checksum already substituted:

```sh
python3 deploy/worker-pools/provision.py --config '<creation.json>' --inspect --profile '<yc-profile>'
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes '<user@canonical-host>' 'sudo -n env PYTHONPATH=/opt/photo-prjct/deploy/worker-pools/_canonical python3 -B /opt/photo-prjct/deploy/worker-pools/release.py eligibility --root /opt/photo-prjct --manifest /opt/photo-prjct/<creation.json> --checksum <reviewed-sha256>'
python3 deploy/worker-pools/provision.py --config '<creation.json>' --apply '<reviewed-sha256>' --profile '<yc-profile>' --receipt '<fresh-private-receipt>' --canonical-ssh-target '<user@canonical-host>' --canonical-root /opt/photo-prjct --canonical-manifest '/opt/photo-prjct/<creation.json>'
```

The canonical `eligibility` operation is read-only and requires the exact `receiver-staged`
receipt, manifest/checksum, actual metadata VM identity, and current coordinator state.
For a reactivation, both old groups must match `predecessors`, remote claims must be paused,
local claims enabled, no release staged, no member rows and no current unfinished attempts.
This conservative attempt check may require a bounded quiet period for local processing.
The operator-owned apply runs that fresh canonical check before each create and reads complete
group, VM and disk inventory in the explicit worker folder. The folder must be empty before
the first create, including the old group IDs and any retained disks. Before the second create,
only the first receipt-owned group and its proven members/disks may exist; the script waits up
to two minutes for its asynchronous inventory to settle. Unknown resources, incomplete or
forbidden reads, or an unavailable canonical SSH check stop before the next POST. A check made
earlier by hand is useful inspection, not authority for a later create.
For a lost response or partial two-group creation, keep the receipt, use read-only `--status`
to resolve the exact names/IDs, and inspect the recorded provider operations. Do not invent a new
receipt or retry an uncertain create until the original operation has been reconciled. No
undocumented idempotency header or automatic recreation hides that uncertainty.
After submission, use read-only `--status` to read each group's actual `scale_policy` and
its managed baseline; verify the expected autoscaling policy after the provider operation
completes. A submitted receipt alone does not prove the applied policy or successful rollout.

When both capped pools have a VM, the canonical 100 GiB SSD and two 32 GiB worker disks
total 164 GiB allocated SSD; one temporary replacement reaches 196 GiB.
Fresh disk/quota evidence must confirm this budget before activation. Complete builder disk
cleanup before release replacement; a stopped instance's retained disk still consumes quota.
The [2026-09-30 read-only preflight](../operations/2026-09-30-worker-folder-activation-preflight.md)
reported 256 GiB SSD quota and 100 GiB allocated in the existing cloud, replacing the
2026-09-28 200 GiB snapshot as dated evidence. Worker-folder separation does not exempt
the shared quota. An unrelated retained SSD invalidates this arithmetic.
Bulk idle-zero savings depend on separately proven provider scale-down and disk removal.
Live lifecycle, serial release and cleanup proof remain separate acceptance work; this
provisioning command does not delete disks or authorize expansion.

Autoscaled initial creation uses size one for both pools because provider `initialSize` is at
least one, including bulk minimum zero. Quote that temporary VM and disk cost in activation
approval. Groups retain OPPORTUNISTIC deployment, unavailable/deleting/creating maximum one
and expansion zero, with the explicit configured ceiling. Their WORKLOAD GAUGE target is one,
measured over 60 seconds, with 300-second provider warmup/stabilization and 600-second provider
startup. Coordinator idle and freshness remain the existing 600/90-second constants.
These are initial policies, not
performance promises; native scale-to-zero, floor retention and update ceilings need live proof.
The backend selfie claim cap stays one until separately accepted two-worker functional evidence.

## Worker bootstrap authority

The worker instance account has only payload read on a **separate** worker
bootstrap secret containing exactly `PHOTO_PROCESSING_FLEET_TOKEN` and `IMAGE_PULL_AUTH`.
The latter is the Docker auth field: base64 of a reviewed GHCR read-only username/credential pair.
The existing canonical GHCR registry is preserved; no YCR resource or pull-IAM grant is required.
Unknown, duplicate, missing, wrong-version or malformed payload entries reject bootstrap. The
account must not read the application's Lockbox secret, DB/Django/S3 credentials or Compute
management. Group-manager and canonical observer/writer identities are separate prerequisites.

### Clean worker OS image recipe

The Git-owned [image recipe](../../deploy/worker-pools/image.py) prepares only an explicitly
identified, disposable Ubuntu 24.04 amd64 builder. It follows the isolation in
[ADR 0042](../adr/0042-isolate-autoscaled-photo-worker-pools.md), the optional telemetry
boundary in [ADR 0043](../adr/0043-observe-isolated-workers-with-git-managed-alerts.md),
and the separate worker folder in
[ADR 0046](../adr/0046-isolate-worker-pool-management-in-a-separate-folder.md).
The observed official Ubuntu source image `fd84a0ma316h9ddtvdoi` is an example base,
not a reviewed worker `boot_image_id`. The recipe does not create a VM/image, install worker
services, fetch Lockbox, pull an application image, change network rules, or activate a pool.

After separately approving a paid disposable builder and its outbound access, copy
`image.py` and the adjacent `telemetry-requirements.txt` onto that builder. Review its actual
instance ID from Yandex metadata independently and supply that exact ID on every invocation.
The default command prints a JSON plan without mutation. Only run these commands as root on
that builder after reviewing its filesystem and Docker inventory:

```sh
python3 image.py --expected-instance-id <DISPOSABLE_BUILDER_INSTANCE_ID>
python3 image.py prepare --expected-instance-id <DISPOSABLE_BUILDER_INSTANCE_ID>
python3 image.py verify --expected-instance-id <DISPOSABLE_BUILDER_INSTANCE_ID>
python3 image.py seal --expected-instance-id <DISPOSABLE_BUILDER_INSTANCE_ID> --confirm-seal <DISPOSABLE_BUILDER_INSTANCE_ID>
```

Each non-plan step checks root, exact metadata identity, OS/architecture, known production
IDs, worker/application credentials and runtime paths, and empty Docker objects before changing
anything. `prepare` checks all four official Docker package SHA256 digests before apt runs,
installs Docker 29.6.0, Compose 5.1.4 and containerd 2.2.5, holds those packages, and installs
the existing pinned telemetry requirements in `/opt/findme-worker-telemetry`. Ubuntu dependency
resolution uses its signed system apt repositories and is not a bit-reproducible repository
snapshot. Package and pip download access still needs separately approved builder egress; the
existing runtime security group is not builder network approval.

`verify` checks the actual Docker server, Compose, package versions, active services and
telemetry imports. `seal` requires the repeated instance ID, verifies again, stops Docker and
containerd, removes only builder SSH authorization and host keys, and runs `cloud-init clean
--logs --machine-id --seed`. It leaves cloud-init and worker runtime boot services available for
first boot. A success receipt at `/var/lib/findme-worker-image/receipt.json` records the builder
ID, recipe and requirement hashes, and exact package hashes/versions. Failure does not write a
sealed receipt. Refused Docker objects or credentials require inspecting or rebuilding the
disposable builder; the recipe never prunes them.

Stop the sealed builder before image capture. Capturing a paid image, configuring the worker
folder and network/IAM, booting a disposable validation VM, checking first-boot metadata/SSH
identity and bootstrap/runtime readiness, and deleting approved temporary resources are a
separate operator-approved workflow. Local recipe tests do not establish a reusable or live
accepted image.

Bootstrap retrieves the pinned payload at boot with metadata IAM, materializes root-private
600 runtime/Docker credential files and a nonsecret 644 container-readable instance-ID file,
checks installed Docker/Compose versions, pulls the digest
and requires OCI `org.opencontainers.image.revision` equal to the configured SHA. Missing/mismatched
labels block worker startup. Canonical builds now set a late OCI revision label after the cached
packaged-model checks; unchanged sources still produce a candidate-specific image, never a retag
of an older revision. The worker-only
Compose exposes no listener or public IP and retains two CPU/5g/concurrency-one limits. The
host retirement helper and timer use the same root-owned private hostname mapping and existing
coordinator grants. No secret value appears in userdata, dry-run output, argv or sanitized logs.

Policy/API sources: [Create](https://yandex.cloud/en/docs/compute/instancegroup/api-ref/InstanceGroup/create),
[Update](https://yandex.cloud/en/docs/compute/instancegroup/api-ref/InstanceGroup/update),
[ListInstances](https://yandex.cloud/en/docs/compute/instancegroup/api-ref/InstanceGroup/listInstances),
[Monitoring write](https://yandex.cloud/en/docs/monitoring/api-ref/MetricsData/write),
[Lockbox payload](https://yandex.cloud/en/docs/lockbox/api-ref/Payload/get).

## Canonical release and recovery

`PHOTO_WORKER_PLACEMENT` defaults to `local`. It is independent of
`PHOTO_PROCESSING_ENABLED`; leave the latter true for the remote API. No pool, IAM grant,
private listener, timer or feature/model gate is activated by installing this code.

First activation is a persistent five-step canonical Deploy protocol. First run the candidate
SHA through ordinary **local** Deploy, with `PHOTO_WORKER_PLACEMENT=local`, so its exact web,
worker and import images are built and published. Prepare a reviewed null-ID create manifest
(`groups.bulk.id` and `groups.selfie.id` both null) using that worker's immutable digest,
and set its canonical path/checksum in the Deploy variables before receiver. Every subsequent
manual run uses the **same exact** `deployment_sha` and
`worker_pool_worker_digest=sha256:<64 lowercase hex>` from that initial build. Receiver,
stage, activate, complete, and abort reuse these exact images without rebuilding or pushing; any tag
digest drift rejects.
Set approved Deploy variables `PHOTO_WORKER_PLACEMENT=remote` and
`WORKER_POOL_PRIVATE_API_IPV4` to the inspected private address. The existing application
Lockbox projection's deploy-only `PHOTO_PROCESSING_FLEET_TOKEN` must match the worker payload's
fleet token and differ from the local token. Creation/rotation of payloads, IAM, private
networking, and collector bootstrap remain separate approved operator actions.

1. Run `gh workflow run deploy.yml --ref main -f deployment_sha=<SHA> -f worker_pool_activation=receiver -f worker_pool_worker_digest=sha256:<DIGEST>`.
   This installs the compatible web, private HTTPS receiver, coordinator, and fleet token while
   local workers continue serving. Preflight checks and pins the null-ID manifest, its worker
   folder and all immutable configuration fields, candidate SHA and actual web/worker digests
   as `receiver-staged`; no group ID or remote-success marker is invented. The retained
   recovery gate stores the prior local package, environment, and image marker.
2. Create the separately approved worker groups in the worker folder using the reviewed image,
   secret and network prerequisites. Their first members may boot only after the receiver is
   reachable; coordinator configuration keeps remote claims paused. Prepare the existing-group
   manifest with `provision.py` using the receiver's exact worker digest and
   otherwise identical immutable configuration, bind its exact IDs,
   both folder IDs and checksum, and set `WORKER_POOL_RELEASE_MANIFEST` and
   `WORKER_POOL_RELEASE_CHECKSUM` to its reviewed canonical path and checksum.
   `bind-stage` rereads canonical eligibility after creation and validates exact new group
   ownership before binding. `stage` then changes only the eligible pool infrastructure
   identity under coordinator locks, clears old cloud/queue/endpoint observations, and observes
   the new group before any remote admission. Ordinary same-group releases retain their
   staged-build path. Repeating the exact candidate does not clear later valid observations
   or members. Jobs, terminal attempts, results, artifacts and pgvector rows remain durable,
   and local processing continues during this step.
3. Run `gh workflow run deploy.yml --ref main -f deployment_sha=<SHA> -f worker_pool_activation=stage -f worker_pool_worker_digest=sha256:<DIGEST>`.
   This binds the manifest to the receiver receipt, verifies provider ownership and private-edge
   prerequisites, warms both paused pools, starts the native collector, and leaves local workers
   serving. Status reports `local-staged`; `worker-pools-current.json` remains absent. Complete
   private TLS, diagnostics, native observations, and Managed Prometheus evaluator/routing
   acceptance before step 4.
4. Run `gh workflow run deploy.yml --ref main -f deployment_sha=<SHA> -f worker_pool_activation=activate -f worker_pool_worker_digest=sha256:<DIGEST>`.
   The same Deploy health gates repeat. Only then does the controller pause local claims, wait
   up to 900 seconds for current local attempts, stop exact local containers, open remote claims,
   and verify both pools. It leaves the receipt `verified`/`remote-pending-acceptance`, with the
   original package and recovery gate intact and no committed fleet marker. Opening remote claims
   can process any eligible queued job, not just a chosen photo. A failed drain never stops local.
   On failure, preserve the compatible receiver and recovery gate until ownership is resolved.
5. After the explicitly approved, bounded real-job acceptance below, run
   `gh workflow run deploy.yml --ref main -f deployment_sha=<SHA> -f worker_pool_activation=complete -f worker_pool_worker_digest=sha256:<DIGEST>`.
   This repeats candidate, collector, fleet, public HTTPS and application-observability checks
   under the canonical lock before writing the committed fleet marker and clearing the original
   recovery gate/package. If acceptance fails, use the same pinned `abort` instead. Ordinary
   Deploy and another `activate` remain fenced throughout this pending-acceptance window.

Before step 3, an authorized operator must inspect the exact release SHA and SHA256 hashes,
then install only the reviewed `metrics-root-helper.sh` as root-owned mode 0755 at
`/usr/local/sbin/findme-worker-pool-metrics`, and `metrics.py`, `metrics.service`, and
`metrics.timer` as root-owned mode 0644 files at
`/usr/local/lib/findme-worker-pool-metrics-package/`. The package directory is root-owned
mode 0755. The approved sudoers entry must bind the exact deploy user and only the helper's
three fixed subcommands (`install`, `verify`, `remove`); validate it with `visudo -c` and read
back file owner/mode. The helper checks its package against the deployed source before it
copies any collector file, and `stage` fails if this bootstrap or sudo permission is absent.
Do not grant sudo to `metrics.py`, the mutable deployment directory, or an arbitrary shell.
After comparing each `sha256sum` with the reviewed exact-SHA source, the operator's narrowly
approved host bootstrap is:

```sh
cd /opt/photo-prjct
sha256sum deploy/worker-pools/metrics-root-helper.sh deploy/worker-pools/metrics.py deploy/worker-pools/metrics.service deploy/worker-pools/metrics.timer
sudo install -d -o root -g root -m 0755 /usr/local/lib/findme-worker-pool-metrics-package
sudo install -o root -g root -m 0755 deploy/worker-pools/metrics-root-helper.sh /usr/local/sbin/findme-worker-pool-metrics
for name in metrics.py metrics.service metrics.timer; do sudo install -o root -g root -m 0644 "deploy/worker-pools/$name" "/usr/local/lib/findme-worker-pool-metrics-package/$name"; done
sudo visudo -c
sudo stat -c '%U:%G:%a %n' /usr/local/sbin/findme-worker-pool-metrics /usr/local/lib/findme-worker-pool-metrics-package /usr/local/lib/findme-worker-pool-metrics-package/*
```

The separately reviewed sudoers file is edited with `visudo` by the operator; it lists only
`<DEPLOY_USER> ALL=(root) NOPASSWD: /usr/local/sbin/findme-worker-pool-metrics install,
/usr/local/sbin/findme-worker-pool-metrics verify,
/usr/local/sbin/findme-worker-pool-metrics remove`. After a bound `abort`, the Deploy action
stops/disables the timer and service and removes only those installed collector files. Removal
of the root-owned bootstrap and sudoers entry is a separate operator rollback after confirming
no staged receipt or collector remains; ordinary Deploy never edits sudoers.

For a bound stage, `worker_pool_activation=abort` with the same SHA and digest fences remote
claims, waits for attempts to finish or recover, restores the original local package,
environment, workers, public endpoint and image marker, removes the private collector, and only
then clears the recovery gate. A receiver-only receipt may also be aborted without creating
missing paid groups: after the operator resolves every create/delete operation in the provisioning
receipt, `abort` rechecks the pinned null-ID manifest and requires complete read-only worker-folder
group, VM, and disk listings all empty. An unresolved operation, failed/partial inventory read,
or any remaining worker resource forbids receiver abort. This includes a partial one-group create:
reconcile and remove that exact receipt-owned group through a separately approved operation,
prove all three inventories empty, then rerun `abort`. Receiver abort restores the original
local package/environment and closes its journal under the Deploy lock. Ordinary unrelated Deploy
runs reject while a receiver or stage receipt is pending, before package replacement. The staged
`.env` says `PHOTO_WORKER_PLACEMENT=remote` to enable the private receiver, while receipt/status
is authoritative for serving placement; until activation it remains **local serving**. After
abort, restore the GitHub Deploy variable to its prior local/default state (remove it if it
was absent before activation) before the next ordinary
push. Independently of that operator step, ordinary remote Deploy refuses to recreate first
activation when no committed fleet marker exists.
After a completed local abort, remote claims stay paused and local claims enabled. Repeating
activation after both groups and their VMs/disks are fully deleted requires a new reviewed
null-ID manifest with the then-current exact predecessor identities and a fresh provision
receipt; old readiness observations are never restored as authority. This repository path
does not authorize a live retry, deletion, new IAM grant or paid create.
The three live inventory reads reject unknown/partial results; they do not certify an outstanding
provider operation has finished. The operator must reconcile every provisioning-receipt operation
to a terminal provider result before the absence-proven receiver abort.
If receiver preflight failed before its recovery gate was created, the same absence-proven `abort`
uses the installer's local package/environment snapshot and closes that prepared receipt. If a
receiver failed after package mutation, the original backup/gate remain available for the same
abort or a pinned receiver retry, including when its private receiver reached `receiver-staged`
but a later public-health gate failed. The retry must use the original null-ID manifest, SHA
and digest; it does not recreate groups or clear an uncertain provider operation.

Worker claims are not scoped to one test event. Opening remote claims can process every eligible
queued job. The supported bounded real-job acceptance therefore requires a fresh queue/lease
snapshot, explicitly approved cutover, a short monitored observation window, and immediate
`abort` if the agreed result or latency guard fails; only a successful observation authorizes
`complete`. There is no one-photo remote canary control.
Cap-one saturation firing cannot be demonstrated while claims remain paused and locals serve;
the operator must use a separately reviewed synthetic evaluator drill or approved claim pause.
Warm-stage fixtures, a green Deploy, and a VM `RUNNING` state do not prove live alert delivery.

### Bounded synthetic worker-alert evaluator rehearsal

After the Git-enabled worker profile is explicitly applied to the existing Managed Prometheus
workspace and live source preflight is green, a separately approved Monitoring workflow
`drill-run` may rehearse the reviewed worker alert predicates without pausing customer jobs.
Use the exact merged `main` SHA, the existing protected `monitoring` environment, and the
existing email/Telegram receiver. This action creates one temporary
`findme-worker-activation-drill.yml` file in that workspace; it never writes synthetic
samples to production metric names or edits the production `findme-photo.yml` rule file.
The source rule content is hash-checked before and after. The receipt is uploaded as a
workflow artifact **before** the temporary PUT, so an interrupted or uncertain PUT has an
exact cleanup identity.

```sh
sha="$(git rev-parse origin/main)"
gh workflow run monitoring.yml --ref main -f revision="$sha" -f action=drill-run
# Record the resulting Monitoring workflow run ID from GitHub Actions as run_id.
sha="$(git rev-parse origin/main)" # refresh origin/main first if main advanced
gh workflow run monitoring.yml --ref main -f revision="$sha" -f action=drill-status -f drill_run_id="$run_id"
sha="$(git rev-parse origin/main)" # refresh origin/main first if main advanced again
gh workflow run monitoring.yml --ref main -f revision="$sha" -f action=drill-cleanup -f drill_run_id="$run_id"
```

`drill-run` has a hard 29-minute observation deadline inside a 45-minute workflow timeout.
The synthetic predicates themselves stop matching after 32 minutes even if the runner dies;
the exact-content cleanup deletes the temporary evaluator file and confirms absence. The
finite timeline is four minutes healthy, nine minutes with both pools' cap-one growing
backlog, four minutes with bulk source missing and selfie node diagnostics missing while
selfie cloud membership stays fresh, four minutes with selfie sources retained-stale and
bulk node diagnostics stale while bulk membership stays fresh, then four minutes recovered.
The 5-minute production saturation `for` is unchanged; queries/snapshots allow the
provider's two-minute evaluator delay and record actual evaluation times. The source
preflight independently requires current pool/node samples and 90-second freshness, but
that 90-second bound is **not** applied to delayed `ALERTS` results.

Download `worker-activation-drill-receipt-<run_id>` and
`worker-activation-drill-report-<run_id>` artifacts for the reviewed acceptance ledger.
The report requires fresh, pending, firing, missing, stale, and recovery observations for
both pool labels as appropriate; `delivery` remains `unverified` until the intended
recipient supplies exact firing and resolved notification receipts with timestamps.
The drill demonstrates server evaluation/routing of synthetic predicates, **not** real
queue saturation, autoscaling, processed photos, or production source delivery. Actual
source point timestamps/current node identities require separate live read-back; a
separately approved short collector outage/recovery is needed for real missing/stale
proof. Temporary rule evaluation incurs usage-dependent, presently unquoted cost and
channel noise. If the workflow is interrupted, run `drill-cleanup` with the same source
run ID and exact SHA; a foreign file, altered production hash, wrong workspace, or
missing receipt fails closed. Ordinary Monitoring routing apply remains blocked while
the temporary file exists. Recovery dispatch uses the **current** main SHA, but validates
the source run's repository, workflow, main-branch dispatch and run ID, then checks out
that trusted source SHA solely to interpret its exact receipt/content for read-only status
or exact-owned cleanup. A new main commit during the 29-minute drill does not make the
prior receipt orphaned; it does not authorize arbitrary historical writes.

The existing Deploy workflow is the sole release pipeline. Its concurrency group and the
canonical host `/opt/photo-prjct/.deployment.lock` cover application/fleet coordination. The
archive includes the exact existing Python cloud transport, under one explicit canonical
`PYTHONPATH`; it does not require the source checkout or copy application credentials.

Receiver preflight pins the actual pulled web/worker digests and OCI revision before groups
exist. Stage binding checks the manifest checksum, group baselines, folder ownership, attached
canonical SG union and the same digests. Warm verification requires both groups paused and local
claims open. Activation repeats image, private and public health checks before its bounded
local drain. PostgreSQL is not restarted by remote reconciliation; no Compose down or data reset
is part of cutover.
The shared deployment's vector preflight retains capability/collation checks and reconciles
the same pinned DB image with `up --wait --no-deps`; an already matching service is unchanged.
Worker rollout itself neither reconciles PostgreSQL nor pauses compatible remote workers for
database work. Application-package rollback retains the vector-capable DB image after successful
capability reconciliation, including fleet recovery; it never downgrades vector-bearing data.

For `pool_max_size=1`, remote forward release and rollback use the same reviewed ceiling
in candidate and previous manifests. Mixed ceilings are rejected; capacity changes are
separate reviewed operations. Only one pool temporarily raises maxSize to two and its warm
floor to two. After promotion, maxSize/floor return to one before fresh survivor observation
and durable retirement grants. Bulk subsequently returns to floor zero, selfie to one.
During initial local cutover, paused bulk retains floor one until remote claims open.
Each pool settles before the next expands; at most three worker boot disks may be allocated,
including stopped/transitional instances. The other pool keeps its reviewed ceiling.
The per-pool image transition allows 30 minutes for provider provisioning, worker bootstrap
and readiness: the observed initial creation took about 17 minutes to reach `RUNNING_ACTUAL`.
This bounded allowance does not change the existing 900-second attempt/local drain limits.

`worker-pools-release.json` records `expanded_pool` before expansion and the identified
`worker_disks` with owner identities before VM retirement. Complete stable group, folder-VM
and folder-disk listings must prove old boot disks absent independently of member disappearance.
The controller rejects retained/deleting or unexplained worker-image disks, unsafe VM states,
incomplete/changing inventory and excess allocations; it performs no arbitrary disk deletion.
Re-entry reconciles pending cloud read-back first and settles the recorded expanded pool
first in either direction. An uncertain unapplied submission is not retried and cannot permit
the other pool to expand. Final verification checks both steady policies and disk settlement.
Actual provider scale-down, survivor safety, recovery and disk lifetime require a charged
resource rehearsal before customer cutover; these controller fences alone do not prove them.

For the separately approved ceiling two, staged releases hold a warm spare within each
pool's hard maximum two. At one selfie node,
the candidate warms alongside the old node. At two, the existing template is changed
OPPORTUNISTICALLY, one exact old boot receives a canonical retirement grant while a fresh old
active survivor remains, and its replacement warms before promotion. Remaining old capacity
retires only behind a fresh new active survivor. Old current attempts continue occupying claim
slots after promotion; selfie claim cap remains one. Floor returns to bulk0/selfie1 after
verification. Initial bulk always warms an acceptance candidate even if the durable queue is
empty; subsequent zero-member verification also checks the exact future launch template.

`worker-pools-release.json` is the durable write-ahead receipt. An uncertain cloud submission is
recorded before sending it; retries inspect its desired configuration and do not resubmit it.
Partial group failure never advances the remote fleet marker. Receiver and stage may advance
`deployed-image` for a healthy compatible web while local workers still serve. Both enabled
pools and running web must verify before `complete` commits the remote fleet marker. A pending
or failed release remains explicit; do not delete its receipt to bypass reconciliation.
An explicit initial `stage`, `activate` or `complete` failure retains the current compatible
application, fleet, observability files, release receipt and original `.deployment-recovery`.
It does not automatically roll back the application or reopen local claims. Inspect actual
claims and leases after a partial activation before resuming the same pinned action. Explicit
`abort` remains the separately authorized recovery path. Fleet-only `rollout`
rejects a local predecessor and cannot bypass canonical Deploy health gates.

An uncommitted initial `staged` or `rolled-back-local` release can accept a reviewed forward
SHA through canonical Deploy `stage`. Both remote pools must remain paused, local claims
must be open, no remote attempt may be live, and no cloud operation may be pending. The
candidate retains the same group IDs, folders, network, IAM, runtime, image shape, processing
contracts and ceilings. Only the app/worker revision and freshly inspected group baselines
change. The provider baseline must match either the prior reviewed configuration or its
paused warm floor of one; an arbitrary resource change cannot be authorized by a new hash.
`previous` remains null for the original local predecessor. `staged_predecessor` records the
prior staged candidate separately, and observation accepts exactly the old and new immutable
worker images while existing disk receipts and original local recovery remain retained.
Re-entry with the same forward candidate preserves these receipts and resumes the recorded
expanded pool first. A changed candidate is rejected during an unfinished transition.

The sole `staging` exception is a web-only candidate whose worker transition never started:
there is no pending operation or expanded pool, both active builds still equal the retained
`staged_predecessor`, and both staged builds are null. The prior running web must match the
current candidate proof. Complete, coherent provider membership and actual running-instance
metadata must prove every worker still has the predecessor's exact build and digest. Any
intermediate candidate member, staging, promotion or provider drift rejects supersession.
The web-only receipt is retained as `superseded_candidate`; `staged_predecessor` remains the
worker origin, and observation permits only that origin and the new corrective build.

Cloud observation treats an omitted `targetSize` as zero only inside a present valid
`managedInstancesState` object, as required by the provider's int64 ProtoJSON zero default.
Actual running members are still fully checked; a zero target does not imply an empty fleet.

Paused replacement temporarily warms two members in one pool, promotes the exact candidate,
restores its cap/floor to one, and then retires the old boot before expanding the other pool.
A fresh ready new-build member can protect release retirement while remote claims are paused,
local claims are open and no remote work is live. Ordinary serving retirement still requires
serving capacity. No claims are opened just to replace a paused worker, and no success marker
is written until the existing health and acceptance gates pass.

The bounded fleet-only inspection interface uses the same host lock. Through the existing narrow
remote-check secret wrapper, set `WORKER_POOL_OPERATION=status` and invoke
`deploy/run-remote.sh worker-pools`. For an established remote image release, `rollout` resumes
and `verify` checks the fleet; `rollback` is the existing compatible remote-release recovery.
Initial activation and abort are **Deploy** actions so application image/public health gates and
package/environment recovery remain coupled. An authorized operator can inspect status directly:

```sh
PYTHONPATH=/opt/photo-prjct/deploy/worker-pools/_canonical \
  python3 -B /opt/photo-prjct/deploy/worker-pools/release.py status --root /opt/photo-prjct
```

Read status/receipt and provider operation evidence first after a lost response. Re-run the same
receiver/stage Deploy action with the same SHA and digest to resume; the write-ahead
cloud receipt never blindly resubmits an uncertain mutation. After `verified`, use only the same
pinned `complete` or `abort`; do not repeat `activate` after remote claims have opened.
Fleet-only commands do not advance the application successful-image marker independently of
Deploy's health gates.

Before promotion, rollback restores the verified prior template, retires staged candidates,
waits for fresh complete post-grant reconciliation, then invokes guarded cancellation. After
promotion, rollback stages fresh capacity from the recorded prior compatible digest. It never
reactivates a drained/granted boot. A first cutover has no previous fleet-compatible production
release: legacy local images are not relabelled. Explicit abort fences both remote pools and waits
for durable attempt recovery/drain before restoring local claims and the previous local web.
If fencing/drain fails, retain compatible candidate web and report failed recovery. Initially
created VMs may remain fenced and billable; stopping/reprovisioning them is a separate approved
operation. Coordination tables, attempt history and accepted artifacts are preserved in all cases.

Before remote mutation, `.deployment-recovery/` (mode0700) preserves `previous.env` (mode0600),
the previous image marker and the exact package-backup path. An interrupted or failed recovery
keeps that directory, the candidate tooling and the previous package; ordinary Deploy fails closed.
Only the pinned receiver/stage/activate/complete/abort action may resume this first activation,
with `activate` limited to the staged/pre-verification transition.
Use the fleet status operation to inspect ownership first. For an explicitly authorized abort, an operator
must reconcile the preserved prior app package/environment under the same host lock and verify
health before clearing this recovery gate. Do not delete the gate or restore old web while
remote drain remains uncertain. Resume the same candidate after interruption; a reviewed forward
candidate is allowed only from the settled paused initial states described above. Successful
repository tests or CI do not establish live worker acceptance.

## Functional evidence versus live acceptance

From the repository, `.venv/bin/python deploy/worker-pools/acceptance.py --functional-fixture`
runs the isolated PostgreSQL/Django/Nginx HTTPS fixture plus existing provisioning, release
and retirement operational modules. Both supported ceilings are exercised; cap-one forward,
rollback and interrupted second-pool recovery use the release module's provider fixtures,
including complete inventories, retained disks and uncertain mutations. Disk evidence comes
from those fixtures, not the TLS test. The HTTPS fixture uses a temporary CA and verified
hostname with the real remote `HttpClient`; only fixture connection routing, external object
storage, cloud and host actions are injected. The fixture persists a maximum 32×512 face result
and a 512-value selfie result within its 16KiB envelope, checks CA/hostname/auth/redirect/body
rejections, preserves existing durable histories/artifacts and exercises bounded drain and
expired-work recovery. Synthetic deterministic vectors are protocol data, not a production fake
model, recognition benchmark or historical-event replay. Existing image packaged-model smoke is
still a separate build gate.

Running `acceptance.py` without the flag prints the outstanding live checklist for the approved
ceiling one and performs no cloud actions. `--pool-max-size 1` selects bulk0..1/selfie1..1
policy/wakeup and serial release/disk gates. `--pool-max-size 2` lists the separately approved
second-instance demand and two-independent-selfie gates; it grants no capacity or claim change.
`--functional-fixture` covers both ceilings regardless of that checklist option.
Fixture success does not prove native autoscaling, private networking/IAM,
billable hard ceilings, real-model memory/startup, guest shutdown, or production cutover/recovery.
Those checks require the explicit charged/live approval. No backfill, second selfie claim slot,
or changes to the already enabled pgvector gate are authorized by these results.
