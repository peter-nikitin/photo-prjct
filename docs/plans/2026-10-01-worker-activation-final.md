# Final Worker Activation Plan

- Date: 2026-10-01
- Status: Execution approved 2026-10-01; Task 1 in progress, paid execution blocked until its pinned command package is admitted.
- Owner: root controller; maintainer supplies real-work and notification evidence.
- Specifications: [isolated folder](../superpowers/specs/2026-09-30-isolated-worker-folder-activation-design.md), [cap one](../superpowers/specs/2026-09-29-capped-worker-pool-activation-design.md), [worker pools](../superpowers/specs/2026-09-23-autoscaled-photo-worker-pools-design.md).
- Architecture: [worker topology and deployment](../architecture.md).
- ADR impact: conforms to accepted ADR 0042, 0046 and 0048; no new architecture decision.
- Execution: use `$execute-implementation-plan` for any implementation changes; the root alone performs live operations.

## Goal

Finish the approved cap-one activation with verified real processing, monitoring and bulk wakeup, so the next separate task is historical-event backfill into the new pgvector table.

## Scope

No design expansion. Initial bulk1 + selfie1; each 2 vCPU, 8 GiB RAM, 32 GiB SSD. Bulk is preemptible 0..1; selfie regular 1..1. At most one serial replacement and three worker disks. Preserve canonical VM, database, existing pgvector reader configuration and all accepted results. No backfill, re-enrollment, token rotation, new credentials, model changes or state purge in this window.

One candidate, one four-hour acceptance window including cleanup reserve, no automatic second attempt. Success retains the accepted steady fleet. Canonical deployment can restart web/nginx; zero downtime is not promised. Activation opens all eligible production jobs, not a test-only event.

## Acceptance criteria

The specification remains authoritative. Completion additionally requires an evidence ledger tying the same candidate to: deployed sources/images; terminal provider operations; warm registered workers; fresh native and Prometheus metrics; Git-managed rules and actual firing/resolved delivery; paused replacement with old disk absent; accepted real upload and selfie; bulk zero with its disk deleted; second upload waking bulk and completing; final release marker with no recovery gate.

A green CI, Deploy, cloud-init, or VM RUNNING alone is not completion.

## Baseline and known failure closure

Read-only observation at approximately 19:16 UTC: main `92a136ccbd02583a5b071676a6391080b55f6cc9`; PR #255 head `64e43d59a5d502422a54c507dd89dbb4c42f638a` open with all five CI jobs green. Canonical local2bulk+1selfie serving, public health OK, no private8443 listener or deployment recovery directory. Worker-folder group/VM/disk inventories empty. Journal is terminal `receiver-aborted`, previous/pending null. Corrected package-only import on the actual canonical host passed. This is not a successful staged eligibility result.

| Observed failure | Closure already available | Gate still needed |
|---|---|---|
| API omits zero maxExpansion | PR #249 normalizes provider defaults | Actual provider identity/cap read-back |
| Abort loses PUBLIC_DOMAIN | PR #249 restores correct environment | Same-candidate abort path retained |
| Cleanup leaves a blocking journal | Supported terminal-state handling | Classify current journal; never blindly delete it |
| SG ANY rule rejected twice | Correct CLI syntax known | Single ledger must contain from-port=0,to-port=65535 |
| 300-second cold pull timeout | PR #252 isolates 900-second pull; later bootstrap succeeded | New pinned image cold start within deadline |
| Old coordinator group IDs | PR #254 guarded eligibility and compare-and-set rebind | Eligibility before each create, rebind during stage |
| Installed package cannot import processing | PR #255 package-layout regression and real-host import pass | Merge/deploy and actual receiver-staged eligibility |
| Privileged import writes root-owned bytecode, preventing deploy-user package cleanup | Found during Task 1, after #255 application commit; minimal `python3 -B` correction and no-bytecode regression required | Corrected generated/direct commands, green ordinary Deploy and installed package read-back |

The first attempt's bootstrap failure cause remains unknown; later pull timeout evidence does not retroactively identify it.

## Worker/state/artifact release safeguards

- **Live-state inventory.** Task 1 records fresh `report_worker_pool_state --json`, pool identities/builds/members, claimable/active/expired/missing leases and accepted counts. Read-only predecessor IDs must match the creation config; inspect existing artifact identities/prefixes, do not enumerate or rewrite customer objects unnecessarily. The 18:17 counts are historical, not a new admission result.
- **Compatibility matrix.** Local workers plus compatible receiver remain supported before activation. New remote workers stay paused until stage verifies the exact build/digest and predecessor binding. Deleted predecessor groups are not revived. Old pool readiness is cleared by the supported rebind; accepted photo/selfie histories and published artifacts remain readable and unchanged. No mixed model or pgvector contract change is allowed. Abort restores local ownership only through the supported drain/recovery path.
- **Reviewed data-state migration or reset semantics.** Use PR #254's locked, predecessor-bound compare-and-set rebind only. No manual SQL update/delete, retry-all, purge, or historical enrollment. Existing work follows the existing drain/lease recovery protocol. Nonmatching predecessor, live member or unfinished authority blocks creation/stage.
- **End-to-end contract sizing.** Existing `tests/deployment/test_worker_pool_acceptance.py` real HTTPS fixture covers 32x512 face result and 512-value selfie result within 16 KiB, client/proxy/callback validation and persistence. Use exact-package evidence, not a fabricated live model result. Real photos remain a separate Task 4 gate.
- **Previous-snapshot upgrade rehearsal.** Existing acceptance/control/release fixtures preserve old successful/failed/retryable and terminal histories/artifacts, cover active/expired lease recovery, nonempty predecessor rebind and interrupted rollback. No old failed or never-enrolled photo is implicitly enrolled by this release. Confirm these named cases remain in final selected-suite evidence; production state is inspected, not reset to manufacture a clean rehearsal.
- **Staged activation and rollback order.** Tasks 1–4 below. Local serving is retained until activate; complete is last. Unknown ownership/provider outcome stops forward progress. Rollback preserves accepted data and recovery receipts.
- **Supported bounded operational commands.** Only the pinned runbook/validated ledger commands for receiver, eligibility, provision inspect/apply/status, bind-stage, stage, activate, complete and abort; fresh receipts for each mutation. No backfill/requeue/purge command belongs to this release. Bounds and cleanup are below. Any failed prerequisite blocks the next task.

## Implementation

### Task 1: Admit one complete release package before paid creation

**Files:** `deploy/worker-pools/provision.py`, `docs/runbooks/worker-pools.md`, `tests/deployment/test_worker_pool_provisioning.py` (already reviewed in PR #255); `docs/operations/2026-10-01-worker-retry-command-ledger.md`; fresh ignored `.superpowers/sdd/final-worker-activation/` config, pins, baselines and receipt files.

**Specification:** deployment, isolation, cap-one and recovery requirements. **Depends on:** plan review. **Produces:** one self-contained, pinned executable ledger, not a chain of historical addenda.

- [ ] Verify #255 exact head, review and all required CI jobs; preserve existing exact-fingerprint evidence. If source/package changes, rerun the selector and missing required suites under repository rules, not an unrecorded subset.
- [x] Reconcile current main and merge #255 with `--match-head-commit 64e43d59a5d502422a54c507dd89dbb4c42f638a`: merged as `3d4749686224e10d1faa961a93e4611b2cc4e3f7`. Ordinary Deploy installed the candidate but failed post-commit package cleanup because an earlier privileged diagnostic import had written root-owned bytecode. No paid window started.
- [ ] Finish the narrowly scoped bytecode correction under Task 1, review/test it and deliver its PR. Require green ordinary Deploy before admitting that corrected candidate; record its actual merge SHA, OCI revision/digest and installed source hashes. The superseded #255 pins are not activation authority. No manual permission broadening or recovery-guard deletion.
- [ ] Consolidate the command ledger: corrected ANY port syntax; `sudo -n env PYTHONPATH=/opt/photo-prjct/deploy/worker-pools/_canonical python3 ...` for direct eligibility; new unique archive/receipt paths; predecessor-bound config; actual pins; exact IAM/network/Monitoring rollback counterparts. Inspect every invoked standalone Python entrypoint in source-free package context, not only the repository import path. Do not execute historical ledger commands verbatim.
- [ ] Inventory actual retained files under deployment lock before drafting their archive command. Current terminal `receiver-aborted` is not `rolled-back-local`; do not reuse the old three-file/archive predicates or assume bound/observation files exist. Preserve receipts; never remove an active recovery guard.
- [ ] Record fresh explicit-scope cloud quotas/inventories, ACL/SG baselines, canonical/local/public health, queue/lease/pool state, Monitoring ownership/routing and no concurrent deployment. Verify package imports on canonical without changing state. Do not repeat unrelated token audits.
- [ ] Independently review the executable ledger against all failure rows above and both success/rollback paths. Confirm maintainer availability for Task 4 and notifications. Present one charged-window approval containing actual pins, commands, resource ceiling and refreshed cost estimate; no per-substep confirmations inside that unchanged envelope.

### Task 2: Prepare receiver and create workers once

**Files:** fresh execution ledger and provider receipts. **Depends on:** Task 1 admission and charged-window approval. **Produces:** two warm paused workers with reconciled provider identities.

- [ ] Apply only approved receipt-tracked IAM/network changes. Verify fresh SSH, public health, origin SSH, private TLS and public8443 denial. Start the pinned receiver while local workers serve.
- [ ] Run config `--inspect`; run corrected live `release.py eligibility` against the exact installed creation manifest/checksum. Require explicit eligible success and expected predecessors. The provisioner must repeat eligibility before each POST.
- [ ] Submit each group create once. On uncertain response, inspect its operation/receipt; never resubmit blindly. Require terminal provider success, Compute RUNNING, successful bootstrap, expected image/build, warm registration and cap read-back. Allow at most 20 minutes from create submission for these gates; pull itself is bounded to 900 seconds.
- [ ] Bind the exact new IDs and stage with supported compare-and-set rebind. Require new IDs in coordinator, paused remote claims, retained local service and fresh warm workers. No manual database repair.

### Task 3: Prove observability and replacement before opening claims

**Files:** Monitoring backups/read-backs, drill artifacts and replacement receipt in the execution ledger. **Depends on:** successful bound stage. **Produces:** telemetry/delivery and lifecycle evidence.

- [ ] Require fresh native autoscaling signals and Prometheus pool/node samples, including queue and processing-rate/duration dashboards. Use configured workspace, exact selectors and timestamps; local scrape alone is insufficient.
- [ ] Back up Git-owned Monitoring state, check and apply the exact pinned revision, read back rules/dashboard and preserve existing routing. Run the supported synthetic evaluator drill, record firing/recovery and obtain real notification receipts. Its existing 29-minute observation deadline/45-minute workflow timeout apply; clean up the exact temporary rule file and verify absence.
- [ ] Rehearse one paused bulk replacement, preserving cap one and no more than three total boot disks. Require terminal operations, fresh warm replacement and obsolete VM/disk absence within 20 minutes. Do not kill a customer job to manufacture a failure test.

### Task 4: Activate, prove real work and finish

**Files:** final execution evidence and architecture status update. **Depends on:** Tasks 1–3 green. **Produces:** accepted remote placement; next task can be backfill.

- [ ] Refresh queues/leases and public health, then pinned activate with supported local drain. Observe all eligible production work, not only the control event.
- [ ] Maintainer uploads a small legitimate photo batch and matching selfie on request. Require accepted remote results, correct output and no new stuck/expired lease or public-health regression; each control operation has a 10-minute acceptance bound after submission.
- [ ] Require bulk target0, actual VM and disk absence within 20 minutes after it becomes idle; selfie remains warm1. Maintainer uploads a second small batch; require bulk wakeup and accepted processing within 20 minutes.
- [ ] Only then pinned complete. Read back remote marker, no recovery gate, exact fleet/cap, fresh metrics, accepted results and healthy public service. Record all remaining VM/disk/group IDs, shapes and expected ongoing charges. Backfill is not started automatically.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered topology and release behavior with specifications and ADR 0042/0046/0048. Update `docs/architecture.md` and operational status only for facts actually proved live.
- [ ] Record conformance and the evidence ledger in the PR/delivery report. If a new architecture decision is needed, stop rather than expanding this window. Any documentation/code delivery uses the normal reviewed PR process.

## Verification

PR #255 currently has reviewed exact-package evidence under fingerprint `ad8450b404fc570a2c0deffe4d67eba1fb2585a427125e5fa95502cafb0e2ea3`: operational 1043 passed/10 skipped; final `make check` 3018 passed/1 skipped; all five CI jobs succeeded. The first root check hit an unchanged TLS BrokenPipeError; focused 12 tests and one full repeat passed. This is disclosed, not silently classified as a first-pass green run.

Use `$select-verification-suites` for any changed final package. Commands include `make static`, `make check`, selector-required expensive targets, and `.venv/bin/python deploy/worker-pools/acceptance.py --functional-fixture` when missing required acceptance evidence. Do not treat the no-flag printed checklist as a test run. Real-host import success is already recorded; actual staged eligibility and Tasks 2–4 remain mandatory live evidence.

## Operational impact and rollout

The four-hour window starts with approved live changes, not during analysis/build preparation. Reserve the final 45 minutes for recovery; do not start a new acceptance phase unless its bound plus recovery reserve fits. Costs are incremental VM/disk runtime plus usage-dependent metrics/traffic; existing canonical/NAT/image/secret charges continue. Task 1 refreshes prices and includes both retained steady-state and temporary replacement cost in the one approval. Do not present an estimate as an all-inclusive billing cap.

## Rollback

On a failed gate, stop forward progress and preserve its first error and exact operation IDs. No code patch, new candidate or second paid attempt inside the same window.

- Before any group exists: supported receiver abort, verify local serving/public health and listener absence, then restore only this window's ACL/network changes.
- After create but before bound stage: reconcile and remove only receipt-owned groups/VMs/disks, prove absence, then receiver abort. Never claim receiver-only absence while a partial create exists.
- After bound stage/activate: same-candidate supported abort fences and drains remote authority and restores local service; follow its recovery receipt before deleting resources. Retain required IAM until recovery and cleanup finish.
- Restore Monitoring from the exact backup if changed and remove the exact drill artifact. Verify private listener absent before original SG restoration; read back normalized ACL/SG baselines.
- Classify the final journal by its actual phase, preserve evidence, and prove the next ordinary local deployment is not blocked. Never delete a guard just to make Deploy pass.
- Cleanup is complete only with terminal operations and empty owned group/VM/disk inventory. On timeout or unknown authority, preserve the recovery gate, report exact remaining resources/charges and request a decision; do not claim a successful rollback or extend the window silently.

## Open questions

No new architecture choice. Execution prerequisites, not discretionary design: actual #255 merge/image pins, consolidated ledger admission and the single refreshed paid-window approval. Until those pass, no new worker resource is created. Maintainer must be present for the two uploads, matching selfie and notification receipts.
