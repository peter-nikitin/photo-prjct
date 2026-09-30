# Explicit worker and canonical folder support

- Date: 2026-09-30
- Status: Ready for repository implementation; cloud activation remains separately gated
- Owner: project maintainer
- Related specification: [approved separate-folder design](../superpowers/specs/2026-09-30-isolated-worker-folder-activation-design.md)
- Related architecture: [accepted constraints](../architecture.md#accepted-constraints)
- Related ADRs: [0046](../adr/0046-isolate-worker-pool-management-in-a-separate-folder.md), [0042](../adr/0042-isolate-autoscaled-photo-worker-pools.md), [0043](../adr/0043-observe-isolated-workers-with-git-managed-alerts.md)
- ADR impact: Conforms to accepted ADR 0046; preserves ADRs 0042/0043

## Goal

Implement the specification's [selected boundary](../superpowers/specs/2026-09-30-isolated-worker-folder-activation-design.md#selected-boundary) so the first worker fleet can reside outside the canonical VM's folder while native autoscaling still reads the canonical metric namespace. This milestone supplies repository support and a reviewed operational handoff.

## Scope

No design delta. Native cap-one alert application and Managed Prometheus lifecycle remain the specification's [activation prerequisites](../superpowers/specs/2026-09-30-isolated-worker-folder-activation-design.md#alert-and-activation-boundary), not implicit workarounds in folder-support code. No cloud creation or live cutover is part of executing this plan.

## Acceptance criteria

The specification's [repository acceptance](../superpowers/specs/2026-09-30-isolated-worker-folder-activation-design.md#acceptance-and-authority) applies. Final fixtures must use genuinely distinct folder IDs and fail if either the autoscaler, periodic publisher or release-time publisher addresses worker-folder metrics. Final handoff identifies supported commands and remaining live gates without claiming deployed topology.

## Worker/state/artifact release safeguards

- [x] **Live-state inventory.** The [2026-09-30 preflight](../operations/2026-09-30-worker-folder-activation-preflight.md) proves local placement and absent remote groups; this package changes host configuration only. Before future cutover, refresh `report_worker_pool_state` and exact enabled identities, claims/leases, current rows/results/artifacts and vector counts under the existing runbook. Dated inventory cannot clear live acceptance.
- [x] **Compatibility matrix.** Existing local processing continues on both application versions. Existing single-folder remote configuration rejects under the new validator; no active remote fleet exists to migrate. New provisioning and observation files require distinct `folder_id`/`canonical_folder_id`; previous/candidate release manifests must agree on both. No compatibility fallback or automatic folder move.
- [x] **Reviewed data-state migration or reset semantics.** Compatible local serving and eventual bounded drain; no schema migration, durable-row mutation, vector/model change, result/artifact rewrite, requeue, purge or backfill.
- [x] **End-to-end contract sizing.** Preserve current 384 KiB processing terminal JSON and 16 KiB selfie response bounds; folder IDs stay in trusted cloud configuration, not customer result payloads. Existing maximum-result/private-TLS acceptance fixture remains the end-to-end guard.
- [x] **Previous-snapshot upgrade rehearsal.** Run the existing functional worker fixture and serial-release/rollback tests against distinct-folder configuration, retaining successful/failed/retryable/expired processing evidence. Assert older/mixed-folder manifests fail before mutation and leave the prior journal/serving state intact.
- [x] **Staged activation and rollback order.** Deliver and verify repository support first. Future exact paid prerequisites and provider rehearsals precede warm-paused staging, local drain and remote claims. Initial rollback fences/drains remote claims then restores local workers through canonical Deploy; accepted rows, artifacts and pgvector persist.
- [x] **Supported bounded operational commands.** Preserve provisioning `--inspect`/`--apply`/`--status`, release preflight/rollout/status/verify/commit/rollback and cap-one acceptance. New fields are bound to checksum and journals; failure stops before cloud mutation. No ad-hoc disk deletion, DB restore or worker-data reset command is introduced.

## Implementation

Execute approved implementation through `$execute-implementation-plan`.

### Task 1: Separate provisioning ownership and metric namespace

**Files:** `deploy/worker-pools/provision.py`, `tests/deployment/test_worker_pool_provisioning.py`.

- **Specification:** selected boundary and explicit folder inputs.
- **Depends on:** None.
- **Produces:** reviewed manifests with worker `folder_id`, required distinct `canonical_folder_id`, and canonical WORKLOAD metric selectors, all checksum-bound.
- Add RED tests for missing/equal/swapped folders; generated group ownership versus WORKLOAD `folderId`; changing either folder changes checksum; canonical VM/application-secret ownership; both folders' cloud membership; shared VPC ownership; worker subnet/SG/route/gateway/image/SA ownership; runtime grants inherited through either folder or cloud; manager Compute grants on canonical ancestors.
- Implement the smallest checks using supported existing resource/binding reads. Check exact direct cloud/folder/resource scope; keep organization/effective-policy review an explicit operator gate rather than claiming an exhaustive automated IAM audit. Manager authority confined to the worker folder and exact cross-folder VPC access must be visible in inspection evidence.
- Preserve shape, cap, bootstrap version pinning, private SG union checks, drift checks and create receipts. Inspect the canonical app-secret metadata without retrieving its payload. Existing images/identities must not be guessed or moved.
- Run `make test TESTS="-m operational tests/deployment/test_worker_pool_provisioning.py"`; all distinct-folder, scope-failure and existing cap cases must pass.

### Task 2: Carry both folders through observation, publication and recovery

**Files:** `deploy/worker-pools/release.py`, `deploy/worker-pools/metrics.py`, `src/backend/processing/services/worker_pool_observation.py`, `tests/deployment/test_worker_pool_release.py`, collector cases in `tests/deployment/test_worker_pool_provisioning.py`, `src/backend/processing/tests/test_worker_pool_cloud.py`.

- **Specification:** explicit trusted observation inputs and unchanged release/retirement authority.
- **Depends on:** Task 1 manifests.
- **Produces:** observation document containing both folders; group/instance/disk reads in worker scope and every native write in canonical scope.
- Add RED tests proving periodic collection and `Host.observe()` publish with `canonical_folder_id` while observing `folder_id`; release generation retains both fields; normal release and rollback reject either-folder drift before journal changes; worker observation rejects wrong instance/disk ownership and absent/equal folder inputs.
- Extend the trusted observation schema and its validation. Preserve periodic collector behavior when cloud collection fails: durable queue publication still runs in the canonical namespace, absent/stale capacity remains unknown, collector reports failure. Keep lifecycle inventories and disk fences in worker scope and existing trusted freshness/build checks intact.
- Run `make test TESTS="tests/deployment/test_worker_pool_provisioning.py tests/deployment/test_worker_pool_release.py src/backend/processing/tests/test_worker_pool_cloud.py"` and the existing transport/retirement regressions selected for the final package.

### Task 3: Rehearse the contract and prepare operational handoff

**Files:** `deploy/worker-pools/acceptance.py` and `tests/deployment/test_worker_pool_acceptance.py` where the new input contract affects the fixture; `docs/runbooks/worker-pools.md`, `deploy/worker-pools/contract.env.example`, the dated operational handoff. Existing release/provision tests may need final integration assertions.

- **Specification:** acceptance and authority; alert/activation boundary.
- **Depends on:** Tasks 1/2.
- **Produces:** fixture evidence, complete nonsecret config example and separately gated cloud handoff for a worker folder in the same VPC.
- Rehearse cap-one forward/rollback and interrupted recovery with distinct folders, including original durable processing/result contracts, maximum responses and no fourth worker disk. No paid or production workload is submitted by fixture execution.
- Document the exact meaning of the two folder inputs, native metric namespace, periodic collector path, cross-folder ownership and grant checks, strict rejection of older inputs, configuration checksum and receipt handling. Update dated same-folder assumptions by linking the accepted amendment; retain historical quota/pricing snapshots as dated evidence.
- Prepare the next operational package around actual discovered IDs and selected roles/commands, with private credential handling, SG rollback and paid approval checkpoints. IDs returned only by future creation remain unresolved targets and must not appear as guessed executable commands. Record the cap-one Git-application/notification and ADR 0043 workspace/API gates explicitly.
- Run `make test TESTS="-m operational tests/deployment/test_worker_pool_acceptance.py"` and `.venv/bin/python deploy/worker-pools/acceptance.py --functional-fixture`; expected successful synthetic/private-TLS evidence with no cloud action.

### Final task: Architecture and ADR reconciliation

After behavior verification, compare the package with ADRs 0045/0042/0043 and the approved specification. Update implemented **repository support** in `docs/architecture.md` and `docs/engineering-jobs.md`; keep actual provisioned topology/alerts/cutover pending. Record conformance and the exact remaining operational gates before push. Stop if implementation requires moving canonical resources or broad canonical-folder management; those are outside this design.

## Verification

Use `$select-verification-suites` with final changed paths and the actual base. Normalize/type-check all changed Python files with `.venv/bin/pre-commit run --files <exact changed Python paths>`, then `make static` after integration. Run focused commands above, root `make check`, and every selector-required expensive suite with exact final fingerprint evidence; expected no regression in local processing, cap-one release, private transport or telemetry. Run one full suite at a time. `git diff --check` and link/status review cover documentation. CI repeats the verified package after push.

## Operational impact and rollout

Repository delivery alone keeps current local placement. A later approval package must supply exact new worker-folder IDs, IAM and VPC scopes, clean image/digests, bootstrap versions, current cost and quota, builder cleanup and actual provider lifecycle evidence. Canonical Deploy remains the application release entrypoint. Customer cutover additionally requires the cap-one Git-owned alert lifecycle and live private processing/metric/notification acceptance. Approval of this plan is not approval to run chargeable or access-changing cloud commands.

## Rollback

Before paid activation, revert the repository change while preserving local serving. Once an explicitly approved remote fleet exists, use compatible manifests and the existing journaled serial rollback; a folder change is not an image rollback. Initial-local restoration fences remote claims and drains/recovers leases before canonical Deploy restores local workers. Preserve product rows, accepted results/artifacts and pgvector; never reset the DB or move existing disks as recovery.

## Open questions

None for repository folder-support implementation. Exact paid resource IDs, effective-access/live network proof and a supported cap-one alert apply/read-back/notification route remain separately owned activation blockers; they do not authorize speculative implementation or UI configuration in this package.
