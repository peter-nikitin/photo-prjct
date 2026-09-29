# Capped worker-pool activation implementation plan

- Date: 2026-09-29
- Status: Repository implementation approved; amended by explicit user direction to retain autoscaling with ceiling one instead of fixed capacity.
- Specification: [capped activation](../superpowers/specs/2026-09-29-capped-worker-pool-activation-design.md), amendment to the approved autoscaled-worker design.
- Architecture/ADRs: [current worker boundary](../architecture.md), [0042](../adr/0042-isolate-autoscaled-photo-worker-pools.md), [0043](../adr/0043-observe-isolated-workers-with-git-managed-alerts.md); conformance, no new ADR.
- Base: `454e46a03634fd5ca55672c41f1fd17ddc052376`, refresh origin/main before execution.
- Execution: `$execute-implementation-plan`; one final consolidated task commit, no live cloud changes.

## Goal and scope

Implement the specification's [selected policy](../superpowers/specs/2026-09-29-capped-worker-pool-activation-design.md#selected-policy) and [serial release](../superpowers/specs/2026-09-29-capped-worker-pool-activation-design.md#serial-release-and-rollback) in existing tooling. Preserve all product/schema/model/gate/runtime contracts. Remove the abandoned fixed-mode patch instead of keeping another configuration path.

## Worker/state/artifact release safeguards

These are selected treatments, not future live GREEN claims.

- [x] **Live-state inventory.** Existing dated activation/memory reports are preparation only. Immediately before cutover inspect report_worker_pool_state, exact images, current claims/leases, existing rows/results and derivative/vector counts. Preserve six enabled bulk identities and 1/selfie_query/2; no empty-queue completeness inference.
- [x] **Compatibility matrix.** Existing local workers remain initial rollback; remote candidates require exact compatible SHA/digest. New nonsecret provisioning config requires pool_max_size; no active remote config exists, so missing input fails with no fallback. No product protocol change.
- [x] **Reviewed data-state migration or reset semantics.** Compatible drain only, no row/result/attempt migration, purge, backfill or requeue. Preserve processing migrations 0012/0013 and pgvector gates/schema.
- [x] **End-to-end contract sizing.** Preserve existing 384 KiB processing terminal JSON and 16 KiB selfie result bounds; rerun exact-final-package worker transport/container acceptance, no increased limits.
- [x] **Previous-snapshot upgrade rehearsal.** Existing fixtures plus capped rollout/rollback preserve successful/failed/retryable/expired attempts and published artifacts; retained disks and interruption have explicit blocking assertions.
- [x] **Staged activation and rollback order.** Repository verification first, separately approved prerequisites and actual provider rehearsal next. Initial warm-paused/drain-local/open-remote claims unchanged; later replacement serial with restore-policy and disk-absence fence before next pool. No Compose down/DB restore.
- [x] **Supported bounded operational commands.** Existing provision inspect/apply/status and release preflight/rollout/status/verify/commit/rollback remain canonical, checksum/journal/lock bound. Unknown disk state aborts without cloud delete or reset commands.

## Implementation

### Task 1: Explicit autoscaling ceiling

Files: deploy/worker-pools/provision.py, contract.env.example, tests/deployment/test_worker_pool_provisioning.py, provisioning sections of docs/runbooks/worker-pools.md.
Produces strict required pool_max_size integer 1|2, checksum-bound WORKLOAD autoScale manifests and actual scale-policy status. Remove abandoned capacity_mode/fixedScale implementation. Preserve all other contract fields.
TDD required/invalid fields, ceiling-one bounds, deterministic checksum and unchanged ceiling-two template/security.
Verification: `make test TESTS="-m operational tests/deployment/test_worker_pool_provisioning.py"` RED then GREEN; exact-file pre-commit and selector/fingerprint.

### Task 2: Serial capped release and durable disk fence

Files: deploy/worker-pools/release.py, tests/deployment/test_worker_pool_release.py; lifecycle/retire tests only where a realistic regression needs coverage.
Depends on Task 1's strict ceiling input. Implements specification serial forward/rollback policy restoration, persisted transitional disk evidence and complete provider disk-absence readback. Existing ceiling-two behavior remains.
TDD manifest validation, sole selfie refusal/bulk idle retirement, no fourth disk, complete/changing membership, retained/deleting disk, interruption/uncertain operation reconciliation and compatible survivor.
Verification: `make test TESTS="-m 'not clone_deployed_slow' tests/deployment/test_worker_pool_release.py src/backend/processing/tests/test_worker_pool_lifecycle.py tests/deployment/test_worker_pool_retire.py"` RED then GREEN.
No speculative backend state; fresh target already governs ordinary retirement. Do not disable timers or bypass durable retire grants.

### Task 3: Acceptance and operational handoff

Files: deploy/worker-pools/acceptance.py, tests/deployment/test_worker_pool_acceptance.py, docs/runbooks/worker-pools.md, docs/operations/2026-09-28-worker-pool-activation-approval.md.
Depends on Tasks 1/2. Separate cap-one policy/wakeup/release acceptance from second-instance demand acceptance. Preserve actual synthetic processing/private TLS/container evidence; provider allocation and production telemetry remain live gates.
TDD cap-one acceptance/interface and interrupted serial recovery; run `make test TESTS="-m operational tests/deployment/test_worker_pool_acceptance.py"` RED/GREEN and `.venv/bin/python deploy/worker-pools/acceptance.py --functional-fixture`.
Handoff exact pool_max_size=1 commands/receipt, 164/196 GiB, builder cleanup, idle bulk-zero cost boundary and unchanged unresolved image/IAM/private-edge/paid approvals.

### Final task: Reconciliation and delivery

Files: docs/architecture.md and docs/engineering-jobs.md only where repository support changed. Do not claim deployed topology, cloud lifecycle or live diagnostics.
Independent whole-package review; final exact Python pre-commit then make static; selector over all tracked/untracked task paths and fingerprint against base. Root runs make check and every selected expensive suite missing exact-final-fingerprint GREEN evidence. No overlapping full Django/visual suites; CI after push is repetition.

## Operational impact and rollback

No live mutation in this package. Subsequent activation requires reviewed clean OS image/pins, exact narrow IAM/secret/SG/private endpoint targets, fresh quota/cost and provider replacement/disk-lifecycle rehearsal. Quota approval does not automatically change ceilings.
Initial rollback restores local workers through the existing journal; remote rollback obeys serial policy/disk gates. Preserve all attempts/artifacts/vectors/gates; no schema reversal. Paid resource creation/deletion and cutover require separate explicit authorization.

## Open questions

None for repository implementation. Clean-image disk sizing and charged prerequisite/provider evidence remain outside this package's authority.
