# Autoscaled photo worker pools

- **Status:** Scope, ADR and initial operational configuration approved in conversation on 2026-09-27; repository implementation authorized; charged activation not authorized.
- **Date:** 2026-09-23.
- **Owner:** FindMe Photo.
- **Related architecture:** [Current deployment and accepted constraints](../../architecture.md#current-architecture--implemented), [photo ingestion and indexing](../../architecture.md#photo-ingestion-and-indexing), [Operations](../../architecture.md#target-mvp-architecture--proposed), and [open decisions](../../architecture.md#open-decisions).
- **Related ADRs:** [0003](../../adr/0003-docker-compose-yandex-cloud.md), [0017](../../adr/0017-use-django-polled-photo-processing-jobs.md), [0018](../../adr/0018-use-managed-yandex-monitoring.md), [0019](../../adr/0019-use-public-event-selfie-search.md), [0028](../../adr/0028-operate-one-canonical-deployment.md), and [0035](../../adr/0035-use-django-polled-yandex-disk-import.md).
- **ADR impact:** Conforms to accepted [ADR 0042](../../adr/0042-isolate-autoscaled-photo-worker-pools.md): multi-VM placement, private encrypted worker API, queue-driven scaling, and fixed initial sizing without a separate measurement project. It narrowly supersedes ADRs 0003/0028's photo-worker placement, ADR 0017's separate capacity-measurement prerequisite for this relocation, and ADR 0018's observation-only restriction for these worker autoscalers. Existing state, credential, privacy, one-deployment and one-SHA boundaries remain unchanged.
- **Related specifications:** [Event photo processing worker](2026-07-29-event-photo-processing-worker-design.md), [public selfie search](2026-07-30-public-selfie-search-design.md), and [Yandex Disk import](2026-09-07-yandex-disk-photo-import-design.md).

## Intent and current boundary

An event must be able to increase photo-processing capacity without consuming the CPU and memory needed by Django and PostgreSQL. When processing demand falls, the extra machines should disappear. Selfie jobs need predictable response time; bulk photo processing may wait for a machine to start. The user selected automatic scaling by queue demand in this first separation stage.

The refreshed deployed integration base `866a894` (2026-09-28) defines on-host `worker-bulk` and
`worker-selfie` services. The [dated read-only inventory](../../operations/2026-09-27-worker-isolation-inventory.md)
records their then-current identities and replicas; it is not a fresh cutover inventory.
The integration baseline includes deployed pgvector `processing/0011`, selfie reader context
`selfie_search/0006`, ADRs 0040/0041 and `pgvector-face-search-read=on`; relocation preserves them.
Recheck actual names, enabled identities and any separately operated bib worker before
cutover. The local worker polls Django through a Compose-only `http://web:8000` URL; the
public Nginx edge returns 404 for the private worker route. Workers have no database connection
or permanent Object Storage credentials; Django owns claims, leases, retries, results and
short-lived exact-object grants. This design changes placement/scaling, not the processing
protocol or product data model.

## Scope

This stage includes the existing bulk processor identities (capture metadata, previews, face embeddings, and bib recognition) and the existing `selfie_query` identity. Each pool has its own VM shape, scaling policy, worker image, health signal, and maximum concurrency. The canonical VM retains PostgreSQL, Django, public Nginx/Certbot, and its existing import and commerce workers. Customer-facing pages and current feature-gate states do not change.

This stage does not move PostgreSQL or the public web edge, add web replicas or an ALB, move Yandex Disk import or commerce processing, introduce a broker/Kubernetes, change selfie ranking or retention, or promise database failover. It does not activate disabled processor identities or features. The canonical VM remains the public and database availability boundary until later stages.

## Considered approaches

| Approach | Result | Decision |
| --- | --- | --- |
| Keep workers on the canonical VM and increase Compose replicas | Simple release path, but event processing still competes with web and PostgreSQL and cannot reduce the base VM safely. | Rejected. |
| Dedicated fixed-size worker VMs | Isolates CPU and RAM, but pays for peak capacity continuously and needs manual event scaling. | Rejected for the requested automatic first stage. |
| Separate queue-driven Instance Groups for bulk and selfie | Isolates workloads, gives each pool an independent floor and cap, and removes idle bulk machines. Adds private transport, metric publishing, and multi-host release coordination. | Selected. |

## Selected topology and authority

Approved initial configuration: each worker VM has 2 vCPU at 100%, 8 GiB RAM and a 32 GiB SSD
boot disk, with concurrency one and current 2 CPU / 5 GiB container limits preserved. Selfie is
regular; bulk is preemptible. Approve the exact provisioning quote separately before creation.
The private listener uses the existing managed canonical-domain certificate and renewal/reload
path, with worker-local hostname resolution to the canonical VM's private address. TLS hostname
verification remains enabled; the public listener still denies worker routes. Retirement requires
centralized permission so simultaneous idle nodes cannot remove the warm selfie minimum.

```text
                                   canonical VM
Internet -> public HTTPS edge -> Django -> PostgreSQL
                                  |
                                  |   \-> Object Storage grants
                      private TLS worker API
                          /              \
          bulk Instance Group       selfie Instance Group
          0..2 VMs initially        1..2 VMs initially
          one job per VM            one job per VM

PostgreSQL queue -> canonical metric publisher -> Yandex Monitoring
                                               -> Instance Groups scaling
```

The two groups share the existing versioned worker image and private claim/result protocol. A VM runs one worker process with one active job at a time and accepts only its pool's processor identities. It has no public inbound listener, PostgreSQL credentials, Django secret, permanent media keys, or customer session. Job state and temporary media authorization remain in the existing Django/PostgreSQL/Object Storage boundary. Workers need outbound access to the private worker API and exact signed Object Storage URLs.

Selfie VMs are regular, non-preemptible instances because the warm minimum is part of customer response time. Bulk VMs may be preemptible because their jobs already have durable leases and retries. A stopped preemptible VM must be replaceable by its group without manual action; functional acceptance includes forced interruption, lease recovery and cold startup.

The worker API receives a dedicated private HTTPS endpoint on the canonical VM. It is reachable only from the worker groups' private network identities and is absent from the public edge. The worker validates the server certificate; the existing scoped bearer credential remains required. Neither a public allowlisted path nor unencrypted cross-VM HTTP is an accepted equivalent. The certificate, its renewal, trust distribution, endpoint address, and least-privilege security-group rules are part of the new ADR and subsequent plan. A transport or certificate failure stops claims and produces an alert; it never falls back to the public route or plaintext.

The existing **Deploy** workflow remains the only way to publish a canonical release. The one deployed SHA covers the web API and both worker pools. A worker VM must not claim a processor identity until its exact image and protocol are compatible with the currently deployed web image. A release must verify the web API and every enabled pool before updating the successful-image marker; a failed release must restore compatible prior images and stop new incompatible claims. Replacing worker VMs must not restart PostgreSQL or the public edge. The design permits in-flight attempts to finish on the old image or recover through the existing lease expiry; it must not silently delete or rewrite durable attempts.

The worker image remains in canonical GHCR and is addressed by immutable SHA-256 digest.
Its independent full commit SHA must match the actual image's OCI revision label; a tag or
desired configuration alone is not release evidence. Local and fleet credentials remain
separate. Fleet bootstrap reads only its pinned worker-only secret, not the application secret.

Central coordination adds only pool/member state, without rewriting processing or vector data.
Physical instance and kernel boot identity fence retirement; a separate registration generation
fences stale worker processes. A retirement grant remains valid for that boot and is not cleared
by timeout. Only a fresh complete post-grant cloud observation can reconcile termination.
Claims retain current attempt ownership until the existing durable recovery path resolves it.
Release replacements count toward the same two-VM ceiling and retain a verified warm selfie
survivor. Staged workers warm without claiming before promotion; callbacks from old attempts
may still finish. Initial cutover rollback restores local placement only after remote claims
are fenced and attempts drained or recovered; subsequent fleet rollback stages a verified
previous remote-compatible build instead of reactivating a fenced boot.

## Queue-driven scaling contract

Enabled compatible identities mean the deployed pool allowlist, not enrollment/submission
feature flags. Existing queued jobs remain demand when their enrollment flag is disabled.
Claimability follows authoritative claim/current-state/input-dependency rules; endpoint
availability is reported separately and cannot silently erase durable backlog.

Current expired in-progress leases contribute a separate recoverable-demand component to the
workload gauge. Recovery runs in the existing claim endpoint: without this component, a bulk
pool at zero after preemption could never wake to recover its processing jobs. Count current
work once, not historical attempts. The publisher remains read-only; normal claim/recovery
turns this demand into due retries or exhausted terminal outcomes.

A publisher on the canonical VM reads the authoritative PostgreSQL processing state and sends one low-cardinality workload gauge per pool to Yandex Monitoring. It publishes independently of worker count so a bulk group at zero can wake up when jobs arrive. The gauge counts claimable queued jobs plus active leased attempts for only that pool's enabled compatible identities. A retry-wait job is counted when it becomes claimable, not while its delay is in the future. It publishes an explicit zero on an empty queue. Separate observations expose oldest claimable-job age, active leases, completed jobs, failures, and publisher freshness; those observations are for diagnosis and alerting and contain no photo, person, event, object key, or bearer-token identity.

Each group uses the Yandex Monitoring gauge as an Instance Groups `WORKLOAD` rule, with a target of one available or active job per VM. The bulk group starts with a **0..2 VM** bound; the selfie group starts with **1..2 VM**. These are hard initial spend and database-concurrency ceilings, not inferred capacity claims. One warm selfie VM avoids a machine-start delay for the first customer query. A bulk group at zero may add machine-start time before its first job. A higher limit or a larger VM shape requires explicit configuration and revised-cost approval; it is not an automatic reaction to a failed acceptance check.

The metric source and autoscaler must be proved to scale bulk from zero on a newly queued job and return to zero after all jobs and leases clear. Groups use Instance Groups' `OPPORTUNISTIC` stop strategy: after the target count falls, an idle VM stops itself only after a stable idle period and a fresh metric observation; a VM with a live lease does not voluntarily stop. Selfie retirement is coordinated so two idle VMs cannot both stop and at least one healthy warm VM remains. This lets an idle VM, rather than Instance Groups, be the one removed. A missing or stale metric is an operational fault: it alerts and blocks voluntary VM shutdown; if the bulk group is already at zero, the alert requires an operator to restore publishing or start capacity. Missing data is never interpreted as an empty queue. Scale-up has a bounded startup/readiness period before a worker claims work. Forced preemption may still interrupt an attempt; the existing lease recovery and retry contract must restore the job without publishing a stale result. [Instance Groups stop strategies](https://yandex.cloud/ru/docs/compute/concepts/instance-groups/policies/deploy-policy) support this idle-first behavior.

The worker fleet is bounded at four simultaneous jobs initially: at most two bulk and two selfie. Because the previous large-event failure involved synchronous selfie ranking in Django/PostgreSQL, the second selfie worker is not released to claim traffic until a functional two-worker acceptance check completes both searches and the public web/API remains healthy, without database errors or stuck attempts. If that check fails, the live selfie cap stays at one and its age/backlog alarm remains active; increasing VM count alone is not a recovery action. This check is not a capacity benchmark or performance guarantee.

The scaling signal is queue demand, not host CPU alone. CPU and memory remain protective alerts and sizing evidence. Oldest-job age detects queues whose count is small but service time is excessive. A saturation alert fires when a pool is at its VM ceiling while claimable-job age continues rising. The private API's claim rate and PostgreSQL connection/lock observations must show that adding workers does not overload the canonical VM.

## Failure and recovery behavior

- Loss of a worker VM leaves its current job leased until the existing bounded recovery path can retry it. Duplicate and late callbacks retain the current immutable-attempt rules.
- Loss of the canonical VM or its PostgreSQL stops both pools; this stage does not claim end-to-end high availability.
- If the private API is unavailable, workers back off without exposing the route publicly or obtaining direct database access.
- A newly created VM with the wrong image, processor allowlist, credential, or server trust cannot claim jobs and must fail readiness visibly.
- Scheduled and automatic scale-down use the same idle-first drain contract. Forced preemption may interrupt an attempt; the system relies on lease recovery, not completion guarantees from the VM shutdown window.
- Metrics, logs, and alerts use bounded aggregate labels. Existing selfie redaction and temporary-selfie deletion rules apply on every VM. Worker local disk is temporary and contains no durable product state.
- The canonical deployment retains one clear rollback procedure to the prior compatible web/worker image set. A worker relocation can be rolled back to the prior on-VM worker placement without migrating database rows or Object Storage objects.

## Cost and availability limits

The maintainer explicitly excludes a separate capacity-measurement or benchmark workstream for
this relocation. Approve fixed initial VM shapes and spend ceilings before provisioning. Mandatory
functional checks cover real jobs, private TLS, request/result bounds, concurrency, startup,
scale-down, forced interruption and rollback; they do not claim a measured performance SLA.
If a fixed shape cannot complete the functional checks, stop cutover and obtain approval for a
revised shape rather than increasing billed resources silently.

Earlier 2026-09-23 estimates used a hypothetical 4 GiB regular worker and do not price the
approved 8 GiB regular/preemptible configuration. No current billing quote is recorded here.
Before provisioning, approve a fresh estimate covering CPU/RAM, all boot disks, retained disks,
network resources/egress and parallel cutover at minimum and peak capacity.
[Compute Cloud pricing](https://yandex.cloud/ru/docs/compute/pricing) and
[Instance Groups scaling](https://yandex.cloud/ru/docs/compute/concepts/instance-groups/scale)
are the source rules.

Moving workers does not by itself lower the existing VM's charge. Its later downsizing needs a measured CPU/RAM baseline and a separate approval. The bill rises during parallel cutover and while extra worker VMs exist. The pool ceilings and cost forecast must be checked against current cloud quotas and the live billing account before any charged resource is created.

## Acceptance criteria

1. The canonical VM runs the existing public edge, Django, PostgreSQL, import worker, and commerce worker as configured, but no bulk or selfie processing worker after cutover. Public HTTPS and database health remain good through worker scale events.
2. Each pool claims only its approved processor identities through private authenticated TLS. Attempts, exact-object grants, result validation, selfie deletion, and immutable evidence match the existing single-VM behavior.
3. A bulk job starts a group from zero; sustained queue demand increases it no further than two VMs; after the queue and active leases clear it returns to zero. A selfie job is served by the warm instance; load may add a second only after the two-worker database safety gate passes.
4. Worker startup, drain, forced stop, lost API, stale metric, incompatible image, duplicate callback, and rollback drills preserve durable job and attempt evidence, recover retryable work within its configured retry policy, and expose no credential or public worker API.
5. Operators can observe per-pool queue count, oldest claimable age, active leases, VM count, publisher freshness, worker health, restart/failure rate, and the canonical VM's web and PostgreSQL saturation. An alert has an explicit runbook response for stalled queue, autoscaling at ceiling, and missing metric.
6. The final deployed SHA and compatible worker images are independently verified on the canonical VM and each group, along with a real private API request, one representative bulk job, one representative selfie job, and a public HTTPS customer path. Local/CI tests alone are insufficient proof of deployment.
7. Before provisioning, ADR 0042 is accepted, fixed initial VM shapes and hard pool ceilings are approved, and a current Yandex Cloud estimate lists minimum, peak, and cutover costs. No separate capacity report or benchmark is required. Charged cloud mutations require their own explicit approval.

## Subsequent new-only model backfill

Prepare a separate blocked task after relocation; do not execute it in this scope. It depends on
the deployed pgvector baseline with public new-reader activation and verified isolated workers.
The vector schema and enabled gate are recorded on 2026-09-28; worker live acceptance remains
pending. Recompute historical SFace events
with AdaFace and write new vectors only to the independent `FaceEmbeddingVector` table. Do not
dual-write those new vectors to legacy `FaceEmbedding`, regenerate previews/watermarks/capture
metadata, or modify originals or saved result snapshots. Existing durable processing evidence,
face detections and accepted projections still have to identify the new vectors correctly.

Before activating a reprocessed event, explicitly close the old-reader rollback boundary for
that event, verify complete accepted model-compatible evidence, and rebuild or deactivate any
old-generation cluster corpus. The separate task must specify bounded enrollment, resume and
failure recovery and preserve the event's old active generation until new evidence is complete.
Legacy code/table removal is a subsequent all-dependent-readers migration, not a side effect of
worker relocation or partial backfill.

## Documentation impact

Repository implementation and verification evidence are recorded separately from live topology.
After approved deployment, update the canonical deployment and incident runbooks, cloud inventory
and engineering-job evidence with actual resources and acceptance. Until then, isolated pools are
accepted target topology, not a completed operational relocation.
