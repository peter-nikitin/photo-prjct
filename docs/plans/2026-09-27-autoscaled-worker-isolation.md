# Autoscaled Worker Isolation Implementation Plan

- Date: 2026-09-27
- Status: Approved for repository implementation on 2026-09-27; paid provisioning and live cutover require separate approval and release evidence
- Owner: project maintainer
- Related specification: [Worker pools](../superpowers/specs/2026-09-23-autoscaled-photo-worker-pools-design.md)
- Related architecture: [Current deployment](../architecture.md#current-architecture--implemented), [Accepted constraints](../architecture.md#accepted-constraints)
- Related ADRs: [0041](../adr/0041-isolate-autoscaled-photo-worker-pools.md), [0017](../adr/0017-use-django-polled-photo-processing-jobs.md), [0018](../adr/0018-use-managed-yandex-monitoring.md), [0028](../adr/0028-operate-one-canonical-deployment.md)
- External dependency: neighboring pgvector task and its ADR 0040, not included in this package
- ADR impact: Conforms to accepted ADR 0041; record only delivered topology as implemented

## Goal

Deliver the specification's [intent](../superpowers/specs/2026-09-23-autoscaled-photo-worker-pools-design.md#intent-and-current-boundary): isolate bulk/selfie compute and scale by durable queue demand.

## Scope

Implement the specification's [scope](../superpowers/specs/2026-09-23-autoscaled-photo-worker-pools-design.md#scope).
No separate benchmark, sizing experiment, database move, main-VM downsize, recognition-model
activation, historical replay or legacy cleanup. Do not change pgvector schema, reader or gate.
Keep import/commerce where they are. Prepare the [blocked backfill task](../future-work/2026-09-27-new-only-adaface-event-backfill.md), but do not run it.

Implementation code can be prepared independently of pgvector. Serialize actual deployment with
the pgvector task: use one current-main image and the existing canonical Deploy workflow; never
deploy an older checkout over its schema changes. No second deployment pipeline may race Deploy.

## Acceptance criteria

Meet all seven [specification criteria](../superpowers/specs/2026-09-23-autoscaled-photo-worker-pools-design.md#acceptance-criteria).
Record code/CI evidence separately from deployed SHA, VM configuration, private TLS, real tasks,
scale events and public HTTPS evidence. Code delivery alone is not completed relocation.

## Worker/state/artifact release safeguards

Unknown release evidence blocks live cutover, not the repository work that produces that evidence.
No checkbox below asserts a completed live check.

- [ ] **Live-state inventory.** Before cutover, use the Task 1 aggregate report and read-only
  Compose/cloud inspection to record actual enabled identities, image SHA, pools/replicas, job and
  attempt states, retry availability, current/expired leases, accepted artifact counts and current
  prefixes. The refreshed base and dated deployment inventory define `worker-bulk` and
  `worker-selfie`; recheck them before cutover. Do not remove an unclassified live bib worker.
  Owner: release controller. This is state inspection, not performance measurement.
- [x] **Compatibility matrix.** Old web/old workers: unchanged. New web/old workers: existing
  wire contract remains accepted during drain. New web/new remote workers at the same compatible
  SHA: supported. Old web/new remote workers: claims blocked until compatibility is proved or
  workers reverted. Existing successful/failed/retryable rows and artifacts remain readable;
  active attempts finish or expire through the existing lease path. Unknown identities fail readiness.
- [x] **Reviewed data-state migration or reset semantics.** Compatible drain only. No processing
  row reset, artifact purge, automatic requeue, generation switch or vector backfill. Any schema
  needed for fleet coordination must be reviewed separately before implementation; it is not
  implied by placement approval. Existing pgvector migration ownership stays with its task.
- [ ] **End-to-end contract sizing.** Task 2 must pass one maximum valid AdaFace callback:
  32 faces, 512 dimensions, existing 384 KiB terminal JSON limit, through serialization/client,
  private TLS proxy, Django validation and database persistence. Cover the existing 16 KiB selfie
  callback and oversize rejection. No worker/model limit is raised. Owner: transport implementer.
- [ ] **Previous-snapshot upgrade rehearsal.** Task 5 acceptance fixture contains successful,
  failed, retry-wait, stale, active/expired lease and never-enrolled cases plus accepted derivative
  evidence. Compare identifiers and terminal outcomes before/after the candidate release;
  restart/drain must preserve them. No replay of real historical events. Owner: release implementer.
- [x] **Staged activation and rollback order.** Publish compatible web/private endpoint first,
  verify remote workers with claims paused, enable bounded work, drain on-host workers, enable
  autoscaling, verify zero/warm-minimum/recovery drills. On failure pause remote claims, drain or
  await bounded lease expiry, restore prior compatible fleet/on-host placement; preserve rows.
  Do not revert an unrelated pgvector deployment or drop its extension.
- [ ] **Supported bounded operational commands.** Task 1 supplies read-only report; Task 4 supplies
  reviewed dry-run/apply/status fleet commands; Task 5 supplies pause/drain/rollback and acceptance
  commands. They reject missing identity, out-of-range capacity and implicit destructive work.
  Until those commands and their tests exist, no ad-hoc production mutation substitutes for them.

Rationale: [Processing-state reset postmortem](../postmortems/2026-07-31-staging-processing-state-reset.md).

## Implementation

### Task 1: Freeze operational configuration and inspect durable state

**Files:** update the specification's private-transport/cost sections; create
`deploy/worker-pools/contract.env.example`, `docs/runbooks/worker-pools.md`,
`src/backend/processing/management/commands/report_worker_pool_state.py`,
`src/backend/processing/tests/test_worker_pool_state_command.py`.

- **Specification:** Scope, Selected topology, Cost and availability limits.
- **Depends on:** maintainer decisions in Open questions.
- **Produces:** reviewed non-secret endpoint, VM shapes, pool identity allowlists, scaling timings,
  service-account permissions, spend ceiling, certificate ownership and aggregate state-report interface.
- [ ] Resolve operational choices in the specification before coding; freeze enabled identities
  from actual inventory rather than enabling every identity packaged in the image.
- [ ] Add RED tests for `report_worker_pool_state --json`: read-only aggregate status/lease/retry
  counts, disjoint pool identities, no vectors, object keys, tokens or event/person identifiers.
- [ ] Implement the command without calling enrollment, recovery or mutation services.
- [ ] Run `make test TESTS="src/backend/processing/tests/test_worker_pool_state_command.py"`.
  Expected: GREEN and database snapshot unchanged.

### Task 2: Private TLS transport and isolated worker packaging

**Files:** modify `src/worker/photo_worker/client.py`, `src/worker/photo_worker/runner.py`,
`src/worker/tests/test_client.py`, `tests/processing/test_worker_container_contract.py`,
`docker-compose.https.yml`, `deploy/nginx/https.conf.template`, `deploy/apply-deployment.sh`;
create `deploy/worker-pools/compose.yml`, `tests/deployment/test_worker_pool_transport.py`.

- **Specification:** Selected topology and authority; Failure and recovery behavior.
- **Depends on:** Task 1 approved transport/certificate contract.
- **Produces:** one worker-only Compose package per pool, private authenticated TLS listener,
  trust/renewal integration, compatible API health check; public denial remains unchanged.
- [ ] Add RED tests for private bind and SG restrictions, public route denial, certificate/hostname
  failure, authorization failure, pool allowlists, forbidden credentials and maximum callback path.
- [ ] Preserve current local Compose HTTP behavior only inside the local private deployment network;
  remote workers require HTTPS. Never add a plaintext or public-route retry fallback.
- [ ] Implement approved transport and bounded container configuration, then run
  `make test TESTS="src/worker/tests/test_client.py tests/processing/test_worker_container_contract.py tests/deployment/test_worker_pool_transport.py"`.
- [ ] In the Task 5 container fixture verify trusted request and full callback persistence;
  invalid TLS and oversize requests fail closed, without secret-bearing logs.

### Task 3: Worker lifecycle, readiness and coordinated retirement

**Files:** modify `src/worker/photo_worker/runner.py`, `src/worker/photo_worker/__main__.py`,
`src/worker/tests/test_runner.py`; create `src/worker/photo_worker/lifecycle.py`,
`src/worker/tests/test_lifecycle.py`, `deploy/worker-pools/retire.py`.

- **Specification:** Queue-driven scaling contract and Failure and recovery behavior.
- **Depends on:** Task 1 reviewed retirement coordination; Task 2 package.
- **Produces:** stop-claim/drain/exit lifecycle; warm model readiness; reviewed idle-only retirement
  integration that never sacrifices the sole healthy selfie instance.
- [ ] Add RED tests for signal before claim, signal during task, heartbeat through drain, lease
  loss, interrupted callback, wrong image/identity, cold readiness and simultaneous idle retirees.
- [ ] Implement the lifecycle with one active job. Coordinate retirement using the approved
  mechanism; a simple independent count-and-stop check is not sufficient for the selfie minimum.
- [ ] Run `make test TESTS="src/worker/tests/test_runner.py src/worker/tests/test_lifecycle.py"`.
  Expected: no new claims after drain; live attempts finish or use existing recovery; one warm survivor.

### Task 4: Queue publisher and bounded Instance Groups

**Files:** create `src/backend/processing/services/worker_pool_metrics.py`,
`src/backend/processing/management/commands/publish_worker_pool_metrics.py`,
`src/backend/processing/tests/test_worker_pool_metrics.py`,
`deploy/worker-pools/provision.py`, `deploy/worker-pools/metrics.service`,
`deploy/worker-pools/metrics.timer`, `tests/deployment/test_worker_pool_provisioning.py`;
modify `deploy/monitoring/alerts.md`, `deploy/monitoring/dashboard.json`,
`deploy/environment-secrets.json` as required by the reviewed credential projection.

- **Specification:** Queue-driven scaling contract.
- **Depends on:** Tasks 1-3; approved IAM, networking and actual autoscaler zero-capacity support.
- **Produces:** independent per-pool workload/freshness publication and dry-run-first bounded
  fleet provisioning/status interface. New VMs boot the approved immutable image; no embedded secrets.
- [ ] Add RED tests for empty explicit zero, due retries, future retry exclusion, active leases,
  disabled/foreign identities, publishing failure, stale observations and ceiling enforcement.
- [ ] Add dry-run provisioning tests: no cloud mutations, no public IPs/inbound worker listeners,
  bulk 0..2, selfie minimum one, no changes to main-VM resources, deterministic managed targets.
- [ ] Implement publisher and fleet package; do not deploy/install them during repository tests.
- [ ] Run `make test TESTS="src/backend/processing/tests/test_worker_pool_metrics.py tests/deployment/test_worker_pool_provisioning.py"`.
  Expected: GREEN; publishing failure cannot masquerade as queue zero or block web startup.

### Task 5: Canonical fleet release, recovery fixture and live cutover

**Files:** modify `.github/workflows/deploy.yml`, `deploy/apply-deployment.sh`,
`deploy/run-remote.sh`, `docker-compose.deployment.yml`,
`tests/deployment/test_deployment_scripts.py`; create `deploy/worker-pools/release.py`,
`deploy/worker-pools/acceptance.py`, `tests/deployment/test_worker_pool_release.py`,
`tests/deployment/worker_pool_acceptance/compose.yml`,
`tests/deployment/worker_pool_acceptance/run.sh`; update worker-pool runbook.

- **Specification:** Selected topology, Failure/recovery, Acceptance criteria.
- **Depends on:** Tasks 1-4 and release-safeguard evidence. Paid provisioning/cutover needs
  explicit approval and must not race the pgvector release.
- **Produces:** sole Deploy release/rollback authority; fixture acceptance; actual isolated pools.
- [ ] Add RED tests for prior-version rehearsal, paused incompatible claims, stale successful-image
  marker, partial pool failure, zero bulk at release, drain timeout and image/state-preserving rollback.
- [ ] Implement fleet deployment without restarting PostgreSQL/public edge for worker-only updates.
  A zero-size bulk group still verifies the launch-template image/configuration and receives one
  bounded acceptance instance before initial cutover is marked successful.
- [ ] Run `make test TESTS="tests/deployment/test_deployment_scripts.py tests/deployment/test_worker_pool_release.py"`.
- [ ] Run `bash tests/deployment/worker_pool_acceptance/run.sh`; expected trusted bulk/selfie end-to-end,
  previous-state preservation, drain, forced loss and stale-metric drills all GREEN.
- [ ] After approval, execute reviewed provisioning dry run, inspect exact resources/cost, then apply.
  Deploy compatible API, start workers paused, verify readiness and actual image digest.
- [ ] Run one bounded bulk task and one selfie query; drain actual on-host photo workers. Do not
  use a broad Compose down. Enable bulk autoscaling and verify wakeup/retirement; enable the second
  selfie only after both concurrent searches finish with public web/API healthy.
- [ ] Record public HTTPS, fresh errors, active leases, accepted results and SHA on every active VM;
  verify future scale-out template matches. Stop and use declared rollback on any failed check.

### Final task: Architecture and ADR reconciliation

**Files:** `docs/architecture.md`, `docs/engineering-jobs.md`, worker-pool runbook and cloud inventory
in `.agents/skills/manage-yandex-cloud/references/inventory.md` when actual deployment changes.

- [ ] Compare exact delivered package with the specification and ADR 0041.
- [ ] Keep proposed/pending status until live proof exists; record deployed multi-VM facts only then.
- [ ] Confirm import/commerce, primary VM size, pgvector gate/schema and existing feature exposure
  are unchanged by relocation. Record conformance and any unresolved operational evidence.
- [ ] Complete final verification below before push; use `$execute-implementation-plan` if the
  maintainer selects subagent execution. Repository implementation was approved; live activation
  still requires its separate approval and evidence.

## Verification

Use [Testing](../testing.md) and `$select-verification-suites`; selector/manifest remain authority.

- Focused RED/GREEN commands are listed per task; add newly created tests to executable discovery.
- Run `.venv/bin/pre-commit run --files <exact changed Python paths>` after final Python edits.
- Run `make static` after integration, then root-controller `make check` on the final package.
- Preparation used `b28d8b7` and review base `9b2409e`. The delivery worktree is based on
  refreshed `origin/main` at `7e3212d780dac346f0b73e2ff92d1034b1c5989a`; neighboring pgvector
  preparation commits are preserved separately and are not part of this package. For an unstaged package run
  `.venv/bin/python scripts/select_test_suites.py select --changed-file <path> ... --format json`
  with every final changed path, including untracked task files; a base without a head does not
  select an unstaged package. For committed comparisons supply both `--base` and `--head`.
  Run `.venv/bin/python scripts/select_test_suites.py fingerprint --base <recorded-base>` on the
  final package; run every required expensive target, including `make test-operational` when
  selected. Record exact resolved commands and fingerprint.
- Container acceptance and the later live cutover are separate evidence. CI repeats repository
  verification and does not establish private TLS, cloud IAM, autoscaling or production recovery.

## Operational impact and rollout

New paid groups, boot disks, network egress path and least-privilege service accounts; private edge
configuration and canonical metric publisher; fleet-aware Deploy. No main-VM downsize, database
move or recognition backfill. The maintainer approves configuration/cost and charged actions;
the controller implements, verifies and performs only the approved cutover. Task 5 owns ordering.

## Rollback

Pause remote claims; drain active attempts or preserve them for existing bounded lease recovery.
Restore previous compatible worker image/configuration or on-host placement. Stop/delete only
reviewed worker-group resources after capacity recovery and a final state check. Do not delete
jobs, attempts, vectors, originals or accepted artifacts; do not revert pgvector or public feature
states as a shortcut. Abort on any unknown active identity instead of silently dropping capacity.

## Open questions

[Read-only preparation inventory](../operations/2026-09-27-worker-isolation-inventory.md) confirms
current pool names/allowlists and configured 2 CPU / 5 GiB container limits. It is partial state
evidence, not a performance report or completion of the live-state release safeguard.

Maintainer approved 2 vCPU / 8 GiB with 32 GiB SSD per worker, reuse of the existing certificate on
a private listener, and centralized retirement permission on 2026-09-27. These are now recorded in
the specification. Remaining live gates (not permission to create resources) are:

1. Exact resource IDs, IAM/SG/network dry-run and current cost approval immediately before creation.
2. Verified certificate renewal/reload and private-only listener using the approved certificate.
3. Reviewed coordinated-retirement implementation and actual Instance Groups lifecycle behavior,
   especially scale-from-zero, simultaneous selfie retirement and permissions for self-stop.
4. Complete current-state inventory and evidence for unchecked release-safeguard slots.

The pgvector task does not block repository preparation of isolation. It does block later
new-only model backfill and requires serialized canonical deployment coordination.
