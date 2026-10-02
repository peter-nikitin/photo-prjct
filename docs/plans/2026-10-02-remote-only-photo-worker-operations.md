# Remote-only Photo Worker Operations Implementation Plan

- Date: 2026-10-02
- Status: Approved for implementation on 2026-10-02; no production operation authorized
- Owner: project maintainer
- Related specification: [remote-only photo-worker operations](../superpowers/specs/2026-10-02-remote-only-photo-worker-operations-design.md)
- Related architecture: [current worker placement](../architecture.md#current-architecture--implemented), [accepted constraints](../architecture.md#accepted-constraints)
- Related ADRs: [0042](../adr/0042-isolate-autoscaled-photo-worker-pools.md), [0046](../adr/0046-isolate-worker-pool-management-in-a-separate-folder.md), [0049](../adr/0049-retire-local-photo-worker-recovery-after-remote-acceptance.md)
- ADR impact: Conforms to accepted ADR 0049. Its remote-only recovery boundary supersedes the local-recovery portions of ADRs 0042/0046 only after the initial remote release is durably committed.

## Goal

Deliver the [approved outcome](../superpowers/specs/2026-10-02-remote-only-photo-worker-operations-design.md#outcome-and-boundary): close the already serving initial remote release once, then make the remote groups the sole production photo/selfie worker placement before historical AdaFace enrollment.

## Scope

Implement the specification without scope changes. The existing groups and their bulk 0..1/selfie 1..1 limits stay unchanged. This plan does not authorize historical backfill, an event activation, image/model change, data purge, new cloud resources or a database move.

## Acceptance criteria

The [specification's criteria](../superpowers/specs/2026-10-02-remote-only-photo-worker-operations-design.md#acceptance-criteria) govern. Report separately: code reviewed, one-time receipt committed, remote-only package deployed, and live state read back. A passing workflow alone does not establish any later state.

## Worker/state/artifact release safeguards

- [x] **Live-state inventory.** The last 2026-10-02 observation found a `verified` initial receipt, the pinned identities in the specification, both remote claims open, no local photo/selfie containers, selfie one warm member and bulk zero observed members at idle. Immediately before finalization, refresh receipt, web image, groups/members, claims, live leases, provider/disk state, collector freshness and public/private health. A drift or unexplained pending operation stops the action. Photo/job/artifact counts and Object Storage prefixes are not migration inputs because this release neither reads nor changes those rows or objects; the separate backfill plan inventories them before enrollment.
- [x] **Compatibility matrix.** Current pinned Django plus pinned remote workers remains serving during receipt finalization. New remote-only Django plus the currently compatible remote workers is allowed only after the marker is committed and the normal fleet rollout verifies its build/contract. Old Django after local retirement is not a rollback target if it can re-enable local claims. Old or new rows, attempts, media, vectors and saved links remain readable in their existing generation; this work drains/retries none of them and does not enroll/backfill/purge any row.
- [x] **Reviewed data-state migration or reset semantics.** None: no schema or data migration, explicit reset, Object Storage write, requeue or derived-state purge. The only durable transition is the existing worker release journal `verified` to `committed` plus its exact fleet marker; remove only the original local deployment recovery inputs after that commit.
- [x] **End-to-end contract sizing.** Not applicable: no callback schema, payload-size budget, proxy limit or persistence path changes. Existing worker-contract tests remain selected by the repository's verification selector, not duplicated for this placement-only release.
- [x] **Previous-snapshot upgrade rehearsal.** The relevant previous snapshot is the worker release journal at `verified` with retained recovery files, not a photo-data snapshot. Test pre-commit failure, successful commit and interrupted post-commit cleanup against that fixture. A prior photo-row snapshot is not needed because no photo/job/derived-artifact path changes; that rehearsal remains in the separate backfill plan.
- [x] **Staged activation and rollback order.** Review and test both code paths before any live action. First run a fresh read-only inventory, then the exact one-time finalizer against the pinned serving release without reinstalling its package. Read back committed marker and recovered space. Only then merge/deploy the remote-only package and verify web/DB/private API/import/commerce, remote claims, collector and public health. Stop on identity drift, unhealthy member, unsettled provider/disk, stale telemetry, live lease anomaly or incomplete cleanup. Before journal commit preserve the current release and recovery snapshot; after commit repair forward or with a compatible remote image, never use local `abort`.
- [x] **Supported bounded operational commands.** Read-only `release.py status`/`verify`, `report_worker_pool_state`, `observe_worker_pool_cloud`, public/private health and collector checks remain bounded to the existing two group IDs. The only new mutation is an exact-SHA/digest/group-bound `workflow_dispatch` finalizer on the reviewed PR ref, under `.deployment.lock`; it may call the existing journal commit once and delete only receipt-identified recovery paths after durable commit. No generic cleanup, local worker restart, job requeue/backfill/purge or direct marker edit is exposed. Present its exact reviewed command, expected billable impact (none beyond existing groups), stop conditions and post-readback for separate operator approval.

## Implementation

Execute approved tasks with `$execute-implementation-plan`; use `$select-verification-suites` for the final package. Tasks 1 and 2 must be reviewed together before Task 1's live operation. Keep one consolidated final commit for the task package.

### Task 1: One-time finalizer for the pending initial receipt

**Files:** `.github/workflows/deploy.yml`, `deploy/run-remote.sh`, new `deploy/worker-pools/finalize_initial.py`, `deploy/worker-pools/release.py` only if an existing read-only/commit interface needs a narrow correction; `tests/deployment/test_worker_pool_release.py`, `tests/deployment/test_deployment_scripts.py`.

- **Specification:** [one-time finalization](../superpowers/specs/2026-10-02-remote-only-photo-worker-operations-design.md#one-time-finalization-of-the-already-serving-fleet).
- **Depends on:** None; the live action additionally depends on reviewed code, a fresh exact-state readback and explicit operator approval.
- **Produces:** A one-time reviewed `workflow_dispatch` action executed from the PR ref, using its own reviewed source but the installed canonical release journal and existing lock; it does not package or deploy the pinned old application. The action accepts exact expected receipt/web SHA, worker digest and both group IDs, refuses any mismatch, runs the existing fleet verification plus web/private/public/collector/log-probe gates, commits the existing journal and cleans only the receipt-identified original recovery inputs. It can reconcile a post-commit cleanup interruption without repeating the commit.

- [ ] Add failing tests for wrong pin/group, absent or non-`verified` receipt, stale/failed fleet or collector, unsettled bulk disk, active lease anomaly, missing web log probe, lock contention, pre-commit failure and post-commit cleanup retry. Assert no old package install, local worker start, scale-policy change or direct marker write outside `release.py`.
- [ ] Run `sh scripts/run-in-test-env.sh .venv/bin/pytest tests/deployment/test_worker_pool_release.py tests/deployment/test_deployment_scripts.py`; observe the intended RED assertions. These files are marked `operational`, so `make test` intentionally excludes them.
- [ ] Implement the one-time workflow branch with the canonical deployment identity and serialization lock. Its remote helper must use a checksum-bound reviewed script and the installed release library; the normal Deploy build/install path is not invoked. Keep the transition action unavailable after the exact receipt is committed and cleaned.
- [ ] Rerun the targeted suites; expect GREEN for both uninterrupted and interrupted commit/cleanup paths.

### Task 2: Remove the local production photo-worker path

**Files:** `deploy/apply-deployment.sh`, `deploy/run-remote.sh`, `deploy/verify-selfie-observability.sh`, `deploy/worker-pools/release.py`, `docker-compose.deployment.yml`, `.github/workflows/deploy.yml`, relevant local worker credential/config projections under `deploy/worker-pools/`; `tests/deployment/test_deployment_scripts.py`, `test_worker_pool_release.py`, `test_adaface_local_compose.py`, and affected workflow/config tests.

- **Specification:** [steady remote-only deployment and recovery](../superpowers/specs/2026-10-02-remote-only-photo-worker-operations-design.md#steady-remote-only-deployment-and-recovery).
- **Depends on:** Task 1's finalizer contract; production deployment waits for Task 1's committed readback.
- **Produces:** One normal remote fleet release path. No production local photo/selfie Compose service, claim credential, placement selector, container-health assertion or restoration/`abort` branch remains. Development worker-protocol fixtures may remain.

- [ ] Add failing tests that ordinary Deploy cannot enable local photo claims or restore local services, fails safely when the remote marker is missing, verifies web/nginx log tags and the web probe without local worker containers, and preserves import/commerce/private API/DB/monitoring plus cap-one remote rollout/compatible rollback.
- [ ] Run `sh scripts/run-in-test-env.sh .venv/bin/pytest tests/deployment/test_deployment_scripts.py tests/deployment/test_worker_pool_release.py tests/deployment/test_adaface_local_compose.py`; observe the intended RED assertions.
- [ ] Remove the obsolete production branches and credentials instead of adding placement fallbacks. Keep immutable identity, provider/disk, queue/lease and remote-health checks that protect real failure paths; eliminate local-container assertions.
- [ ] Rerun focused suites; expect GREEN with no local photo/selfie services in the rendered production Compose configuration.

### Task 3: Operational handoff and release evidence

**Files:** `docs/runbooks/worker-pools.md`, `docs/runbooks/historical-adaface-backfill.md`, `docs/architecture.md`, `docs/engineering-jobs.md` if its status changes, and the PR description/evidence.

- **Specification:** [acceptance and failure boundaries](../superpowers/specs/2026-10-02-remote-only-photo-worker-operations-design.md#acceptance-criteria).
- **Depends on:** Tasks 1–2 reviewed and tested; live status is recorded only after the corresponding operation.
- **Produces:** One concise remote-only recovery/rollout runbook and a clear next step: separately approved bounded historical AdaFace enrollment.

- [ ] Replace stale instructions for initial local `complete`/`abort`, on-host photo-worker recovery and repeated cutover with the exact one-time finalization procedure and then the steady remote-only path. Keep historical evidence in dated operations notes, not in the durable ADR.
- [ ] Record the exact pre/post state, pins, group IDs, member/claim/lease health, fresh metric timestamps and cleanup scope; do not mark deployed or live-accepted based on tests or CI alone.
- [ ] Check all links and compare the runbooks against the actual supported commands; expect no route to local production photo workers.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the specification and ADRs 0042/0046/0049; keep ADR 0049's durable rationale and decision, without a release-incident chronology.
- [ ] Update architecture only for implemented/deployed facts and record the exact code-ready, committed, deployed and backfill-not-started states in the PR before push.

## Verification

Use targeted RED/GREEN commands in Tasks 1–2. On the final package run `.venv/bin/pre-commit run --files <exact changed Python paths>`, `make static`, `.venv/bin/python scripts/select_test_suites.py select --base origin/main`, `.venv/bin/python scripts/select_test_suites.py fingerprint --base origin/main`, `make check`, and every selector-required expensive target for that exact fingerprint. Inspect the rendered Compose config and workflow YAML, then wait for all required CI jobs to finish GREEN. After the separately approved live finalizer and remote-only Deploy, obtain fresh read-only release marker, remote group/claim/lease, collector, web/private/public health and original recovery-path absence; no green CI substitutes for these.

## Operational impact and rollout

Review/push the code to PR #266 without merging while the initial receipt remains `verified`, because a main push would trigger ordinary Deploy behind the receipt fence. Dispatch the reviewed one-time action on the PR ref only after the exact command and fresh state receive separate approval. Confirm `committed` and cleanup; then merge/deploy the remote-only package through normal Deploy, read back its new SHA and remote fleet health, and only afterward unblock the separate backfill plan. No new VM, disk, IAM, network or billing limit is created.

## Rollback

Before journal commit, leave the current serving remote fleet and recovery snapshot intact, fix the cause and retry the exact finalizer. After commit, finish exact-owned cleanup if interrupted; no local `abort` or restore. If the new remote-only deployment fails, pause affected claims and use an older **remote-compatible** image only if it supports every active event's generation and processing contract; otherwise repair forward. Preserve jobs, leases, vectors, media and saved result links. No DB restore or broad image/disk cleanup is in scope.

## Open questions

None for implementation. Fresh production state, exact command review and operator approval are live execution gates, not assumed plan results.
