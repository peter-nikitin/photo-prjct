# Forward worker activation repair

- Date: 2026-10-01
- Status: Execution authorized by the maintainer's instruction to fix forward without rollback
- Owner: project maintainer
- Related specification: current worker activation scope; maintainer overrides recorded in `.superpowers/sdd/2026-10-01-worker-activation-final/live/execution.md`
- Related architecture: [worker placement](../architecture.md)
- Related ADRs: [0042](../adr/0042-isolate-autoscaled-photo-worker-pools.md), [0046](../adr/0046-isolate-worker-pool-management-in-a-separate-folder.md)
- ADR impact: conforms; retain one immutable SHA for web and workers, existing groups, ceilings and durable processing contracts

## Goal and scope

Finish the existing activation with the corrected Monitoring response parser. Do not tear down
groups, restore an older application automatically, change resource shapes, or reset processing
rows. A failed initial staged candidate must admit a reviewed forward revision through canonical
Deploy, not container hotpatching or manual journal/DB edits. Normal committed release behavior
is unchanged. Replacement of worker members is sequential through the existing bounded release
protocol; it is a forward image update, not group recreation.

## Acceptance criteria

The observed numeric `writtenMetricsCount` succeeds, invalid counts still fail. A corrected
single-SHA candidate can resume the paused initial activation, retaining original local recovery
evidence and group identity. Failure preserves diagnostic/retry state without automatic app/fleet
rollback. No success marker is written before existing health and acceptance gates pass.

## Worker/state/artifact release safeguards

- **Live-state inventory.** Initial candidate a79cbb784058971a79ef5eca7f9321556221939f,
  bulk cl13d5ffaml0f42s0s0s and selfie cl1136g0efv0pl7u3d9a; both warm, remote claims paused,
  local claims enabled, journal rolled-back-local, no pending cloud operation, recovery retained.
  Refresh these facts and active leases at execution. Existing accepted results and artifacts stay
  untouched. No processor/model/storage prefix changes.
- **Compatibility matrix.** Only image revision changes; processor and result contracts remain
  identical. The old image is observed alongside the new image during the bounded transition;
  remote claims remain paused until activation, local work continues normally.
- **Reviewed data-state migration or reset semantics.** No schema migration, purge, backfill or
  manual row reset. Use existing stage/promote/retire coordinator transactions and durable lease
  reconciliation. Preserve original local recovery snapshot independently of staged predecessor.
- **End-to-end contract sizing.** Unchanged: this repair changes transport response validation and
  release admission, not photo/result serialization, request limits or persistence.
- **Previous-snapshot upgrade rehearsal.** Automated tests seed the real rolled-back-local initial
  release with warm paused old members, retain original recovery, forward-revise and retry after
  interruption. Reject active remote claims, live remote leases, unknown pending operations,
  changed resource scope and inconsistent candidate proofs. Existing processing contract tests
  remain part of final core verification; no processing data migration is introduced.
- **Staged activation and rollback order.** Build reviewed immutable revision; admit forward
  candidate only from settled uncommitted initial paused state; reconcile web, sequentially warm
  and promote workers; verify paused fleet and metrics; continue existing alert/real-work/lifecycle
  acceptance before completion. Failure retains current application, resources and recovery;
  explicit abort remains available but is not authorized by this execution.
- **Supported bounded operational commands.** Existing exact-SHA Deploy `stage`, `activate`,
  `complete`, status and cloud read-back remain entrypoints. No extra VM/group creation command,
  quota increase, SQL reset or backfill. Group updates retain approved maximum three worker VMs
  and disks during a sequential replacement, steady cap one per pool.

## Implementation

Use `$execute-implementation-plan` for implementation and independent review.

### 1. Correct provider response validation

Files: `src/backend/processing/services/worker_pool_cloud.py` and its focused test.
Accept exact integer or documented canonical string count; reject boolean, float, partial/missing
counts and provider errors. Numeric-response RED/GREEN is already recorded in the task ledger.

### 2. Support a forward revision of paused initial activation

Files: `deploy/worker-pools/release.py`, `deploy/apply-deployment.sh`, `deploy/run-remote.sh`,
`src/backend/processing/services/worker_pool_lifecycle.py`, their deployment/lifecycle tests,
and the existing worker operations documentation where needed.

First add failing previous-snapshot and shell failure-path tests. Reuse explicit `stage`; no new
workflow option. Permit changed candidate only for uncommitted settled paused initial activation
(`rolled-back-local` or `staged`), exact group/scope/config invariants, no active remote leases or
pending operation. Changes are limited to worker/app revision and freshly observed optimistic-lock
group baselines; validate the actual provider baseline. Save prior staged candidate separately
from committed predecessor, preserve original recovery, and observe both exact image revisions.
Re-entry must resume the same forward candidate without erasing earlier evidence.

Use existing bounded transition, fixing its paused warm-capacity cases: temporary floor two only
while replacing the old revision serially, then restore cap-one floor before retirement. A warm
paused new member may protect retirement only while local claims are enabled, remote claims are
paused and no remote work is live. Do not weaken ordinary serving-capacity retirement admission.

For explicit initial activation phases, failure retains candidate/resources/recovery and reports
failure, rather than invoking automatic previous-deployment recovery. Do not open remote claims
as a workaround. Normal committed deployments and explicit abort semantics remain unchanged.

### 3. Verify and deliver

Run focused RED/GREEN deployment/lifecycle tests, exact Python hooks, selector and fingerprint,
`make static`, `make check`, and selector-required expensive suites. Review the final complete
package independently, commit once, create PR, require green CI, merge and build immutable images.
Prepare the new manifest from a fresh read-only baseline while preserving resource scope. Run
canonical stage and existing acceptance sequence. Report actual live proof separately from CI.

### Final task: Architecture and ADR reconciliation

Confirm one-SHA release, group/cap preservation and existing data contracts after verification.
Record the new supported initial forward-retry behavior in operational documentation; do not
claim successful worker activation before live acceptance. No new service or architecture decision.

## Rollback and open questions

Automatic rollback is explicitly disabled for this initial activation path. Retain original
recovery evidence and explicit abort implementation, but do not execute abort without a new user
instruction. No unresolved design choice; unexpected scope or live state must be diagnosed before
mutation rather than bypassing admission.
