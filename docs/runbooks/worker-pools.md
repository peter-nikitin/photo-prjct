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
Diagnostic rules, routing and notification firing/no-data/recovery are deferred. On loss,
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
and identity grants belong to the separately approved activation; no repository command installs
or enables these units by default. The collector's `/etc/findme-worker-pools/metrics.json` is:

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
[ADR 0047](../adr/0047-reuse-managed-prometheus-for-worker-alerts.md), not a separate native
alert lifecycle. Native publication remains the autoscaler's input. The additional private
diagnostics scrape exposes read-only pool observations and numeric source timestamps.

The default-off worker profile targets ceiling one: fresh actual running capacity >=1,
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

Activation is blocked until the legacy manual `deploy/configure-monitoring-agent.sh` full-config
replacement is reconciled with this ownership contract. Do not use it to reconfigure an activated
worker scrape: it can remove Prometheus routes. Coordinate the shared configuration remediation
with observability before activation; this package does not modify `deploy/image-origin/**`.

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
folder and canonical cross-folder `vpc.user` without canonical Compute management; exact runtime
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
Cloud API inspection does not replace these live proofs.

After those prerequisites and current cost are reviewed and separately approved, explicit
`--apply <reviewed-sha256> --profile <yc-profile> --receipt <fresh-private-path>` can submit only
the two exact worker-folder group creates or baseline-bound updates. It rechecks expected absence/ownership/drift
and writes a durable private receipt **before** each mutation. Receipts record returned operation
and group IDs plus both folder IDs; submission is not rollout success. Existing receipt paths
refuse resubmission. Changing either folder changes the configuration checksum. Keep private
config, receipt, release journal and any token out of Git and shared logs.
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

After the separate infrastructure/live approval, prepare an existing-group manifest with
`provision.py` for the exact candidate SHA and GHCR digest. Store its JSON at a reviewed
canonical path, set Deploy variables `WORKER_POOL_RELEASE_MANIFEST` to that path,
`WORKER_POOL_RELEASE_CHECKSUM` to its checksum, `WORKER_POOL_PRIVATE_API_IPV4` to the inspected
private address and `PHOTO_WORKER_PLACEMENT=remote`. The existing application Lockbox projection
has an optional, deploy-only `PHOTO_PROCESSING_FLEET_TOKEN`; it must match the separate worker
payload's fleet token and differ from the local token. Creation/rotation of these payloads and
their permissions is a separately approved operator action. Workers never read the app secret.

The existing Deploy workflow is the sole release pipeline. Its concurrency group and the
canonical host `/opt/photo-prjct/.deployment.lock` cover application/fleet coordination. The
archive includes the exact existing Python cloud transport, under one explicit canonical
`PYTHONPATH`; it does not require the source checkout or copy application credentials.

Preflight verifies the reviewed template checksum, exact group baselines, effective attached
canonical SG union and actual pulled web/worker image digest plus OCI revision. After compatible
web/private edge is running, the controller checks the actual container image ID, configures
initial remote claims paused, observes/warm-verifies pools, pauses local claims and waits for
authoritative current ownership to empty. Drain failure aborts local container stop. Only then
are the exact canonical photo-worker containers stopped and remote claims opened. PostgreSQL is
not restarted by the remote reconciliation; no Compose down or data reset is part of cutover.
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
Partial group failure never advances `deployed-image`. Both enabled pools and running web must
verify before the existing successful-image marker commits. A pending or failed release remains
explicit; do not delete its receipt to bypass reconciliation.

The bounded fleet-only recovery interface uses the same host lock and never restarts unrelated
services. Through the existing narrow remote-check secret wrapper, set `WORKER_POOL_OPERATION`
to `status`, `rollout` (resume), `verify` or `rollback`, then invoke `deploy/run-remote.sh worker-pools`.
Equivalently, an already-authorized canonical operator selects one appropriate phase below,
starting with status after an interruption:

```sh
PYTHONPATH=/opt/photo-prjct/deploy/worker-pools/_canonical \
  python3 /opt/photo-prjct/deploy/worker-pools/release.py status --root /opt/photo-prjct
PYTHONPATH=/opt/photo-prjct/deploy/worker-pools/_canonical \
  python3 /opt/photo-prjct/deploy/worker-pools/release.py rollout --root /opt/photo-prjct
PYTHONPATH=/opt/photo-prjct/deploy/worker-pools/_canonical \
  python3 /opt/photo-prjct/deploy/worker-pools/release.py verify --root /opt/photo-prjct
PYTHONPATH=/opt/photo-prjct/deploy/worker-pools/_canonical \
  python3 /opt/photo-prjct/deploy/worker-pools/release.py rollback --root /opt/photo-prjct
```

Read status/receipt and provider operation evidence first after a lost response. Resume uses the
same exact manifest and boot/grant CAS; it does not synthesize a new release. Fleet-only commands
do not advance the application successful-image marker independently of Deploy's health gates.

Before promotion, rollback restores the verified prior template, retires staged candidates,
waits for fresh complete post-grant reconciliation, then invokes guarded cancellation. After
promotion, rollback stages fresh capacity from the recorded prior compatible digest. It never
reactivates a drained/granted boot. A first cutover has no previous fleet-compatible production
release: legacy local images are not relabelled. Its failure fences both remote pools and waits
for durable attempt recovery/drain before restoring local claims and the previous local web.
If fencing/drain fails, retain compatible candidate web and report failed recovery. Initially
created VMs may remain fenced and billable; stopping/reprovisioning them is a separate approved
operation. Coordination tables, attempt history and accepted artifacts are preserved in all cases.

Before remote mutation, `.deployment-recovery/` (mode0700) preserves `previous.env` (mode0600),
the previous image marker and the exact package-backup path. An interrupted or failed recovery
keeps that directory, the candidate tooling and the previous package; another Deploy fails closed.
Use the fleet status/rollback operation to resolve ownership first. Then an authorized operator
must reconcile the preserved prior app package/environment under the same host lock and verify
health before clearing this recovery gate. Do not delete the gate or restore old web while
remote drain remains uncertain. A failed initial cutover must be retried with the same reviewed
candidate or separately reconcile its fenced VMs/coordinator before reviewing another build.

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
