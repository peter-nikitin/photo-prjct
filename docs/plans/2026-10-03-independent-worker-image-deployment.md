# Independent worker-image deployment implementation plan

- Date: 2026-10-03
- Status: Approved for execution by the maintainer's instruction to implement and deploy
- Owner: project maintainer
- Related specification: [Independent worker-image deployment](../superpowers/specs/2026-10-03-independent-worker-image-deployment-design.md)
- Related architecture: [Current worker placement and accepted constraints](../architecture.md)
- Related ADRs: [0050](../adr/0050-decouple-processing-queue-from-worker-builds.md),
  [0051](../adr/0051-release-photo-worker-images-independently.md)
- ADR impact: ADR 0051 is explicitly accepted; it supersedes only the coupled web/worker
  release language in ADRs 0028, 0042 and 0049. Reconcile implemented facts at the end.

## Goal

Deliver the [approved specification](../superpowers/specs/2026-10-03-independent-worker-image-deployment-design.md#outcome-and-boundary):
ordinary web releases do not touch photo workers, documentation-only merges do not deploy,
and worker releases update running containers or the image used by future VMs.

## Scope

No change to the approved specification. The one-time existing-selfie-VM template transition
uses the current one-VM ceiling and accepts a processing pause; it does not add a VM or alter
pool shapes. Subsequent worker releases do not update the VM template.

## Acceptance criteria

The [specification acceptance criteria](../superpowers/specs/2026-10-03-independent-worker-image-deployment-design.md#acceptance-criteria)
are binding. Additionally, the first production transition must show a committed web release,
one warm serving selfie member after the template update, bulk remaining idle-zero, and a
new-image container switch on that same VM without a second template update.

## Worker/state/artifact release safeguards

- [x] **Live-state inventory.** At 2026-10-03 read-only canonical status: bulk target 0,
  selfie target 1 with one warm serving member; both pools fresh and both report zero live
  attempts. PostgreSQL aggregates: 306,570 succeeded and 6,483 failed photo jobs; 306,570
  succeeded, 8,736 failed and 285 expired photo attempts; 4,020 succeeded and 2,100 failed
  selfie jobs; 4,020 succeeded, 2,023 failed and 402 expired selfie attempts; no active or
  expired in-progress lease. There are 241,987 vector rows. Existing accepted media and
  Object Storage prefixes are preserved, not enumerated or migrated by this release. Re-read
  this aggregate and pool status immediately before cutover; pause if live work appears.
- [x] **Compatibility matrix.** Old web/old worker continues before the transition. Because
  `processing.0016` removes the obsolete build field from the private claim protocol, old and new
  protocols do not overlap: pause both pools, drain the sole selfie worker, and verify zero live
  attempts before the combined worker publication and web migration. New worker accepts existing
  semantic job versions and results. Old succeeded/failed/expired attempts, queued jobs and saved
  results remain readable because image identity is removed from their processing semantics. An
  incompatible combination blocks deployment rather than causing queue-side build routing.
- [x] **Reviewed data-state migration or reset semantics.** Drop only the non-semantic
  `ProcessingAttempt.worker_build` column and its request plumbing; preserve all other job,
  attempt, vector, derivative, search and media rows. No requeue, backfill, purge or reset.
  Live leases, if any emerge, retain normal completion/expiry across migration.
- [x] **End-to-end contract sizing.** Preserve the existing per-contract bounds, including
  the 384 KiB AdaFace v5 terminal callback and smaller limits for other contracts. Exercise
  a maximum-sized valid v5 callback through worker serialization, private HTTP, Django
  validation and persistence. Existing selfie request bounds remain unchanged. No new result
  schema or artifact size is introduced.
- [x] **Previous-snapshot upgrade rehearsal.** Migration-layer test starts from the prior
  processing schema with succeeded, failed, retryable, stale and in-progress/expired lease
  attempts plus published derivatives/vectors and a never-enrolled photo. It applies the new
  migration and verifies every row and status is preserved except the removed build column;
  normal lease recovery remains possible. Run this before production migration.
- [x] **Staged activation and rollback order.** Pause both pools and verify zero live attempts;
  merge the release so Deploy publishes worker `latest` and migrates the web protocol while
  claims remain paused. Patch the two existing templates once to install updater/bootstrap. Their
  `OPPORTUNISTIC` policy does not restart the live selfie VM, so invoke the exact managed-instance
  `rollingRecreate` for the sole selfie member. Do not create a second VM; keep idle bulk at zero.
  Wait for one warm serving selfie member, fresh private API/metrics evidence, then unpause claims.
  Verify a later worker-only image switch on the same VM.
  A later backend-only release validates no worker restart. Stop on failed model warm-up,
  private API failure, unexpected live leases or loss of the warm selfie member. Before the
  column drop, restore a previous web image only after its schema probe passes. After the drop,
  recover forward with a compatible new-protocol web candidate; use a prior worker digest only
  when it supports the active contract. Preserve all rows and artifacts.
- [x] **Supported bounded operational commands.** Use existing `control_worker_pools`
  read-only status, Django aggregate counts, canonical Deploy workflow and Instance Groups
  group/instance read-back. No requeue, backfill or purge command is required or authorized.
  The one-time group-template change is bounded to the existing bulk/selfie group IDs and
  current cap one; it needs exact pre-mutation read-back and post-mutation verification.

## Implementation

### Task 1: Remove release identity from processing work

**Files:** `src/backend/processing/models.py`, new processing migration,
`src/backend/processing/services/jobs.py`, `src/backend/processing/views.py`, processing
contracts/client and focused tests under `src/backend/processing/tests/`, `src/worker/tests/`
and `tests/processing/`; update affected report limits.

- **Specification:** [release classification and processing boundary](../superpowers/specs/2026-10-03-independent-worker-image-deployment-design.md#decide-what-to-publish-from-the-change-itself).
- **Depends on:** ADR 0050; no later task.
- **Produces:** claim/lease/result behavior independent of worker build, with unchanged
  semantic processor/model identity and historical row preservation.
- [x] Add focused failing tests for two compatible worker builds claiming the same work and
  for preservation of prior attempts across migration.
- [x] Run focused tests RED; make the smallest code/migration change; rerun GREEN.

### Task 2: Decouple fleet admission and container handoff

**Files:** `src/backend/processing/services/worker_pool_lifecycle.py`, observation and
telemetry services, `src/worker/photo_worker/lifecycle.py`, runtime status and client,
associated backend/worker tests.

- **Specification:** [running and newly started VMs](../superpowers/specs/2026-10-03-independent-worker-image-deployment-design.md#running-and-newly-started-vms).
- **Depends on:** Task 1 contract.
- **Produces:** a warmed replacement can register and become ready while the previous
  process stops claiming; cloud membership remains a VM/boot safety signal, not a build gate.
- [x] Add failing tests for warm-before-handoff, old-process drain, stale/duplicate callback
  rejection, and no claim admission by image identity.
- [x] Run focused tests RED; implement the narrow handoff; rerun GREEN.

### Task 3: Install the host-owned image updater and reusable model base

**Files:** `Dockerfile.worker`, new worker base Dockerfile, `deploy/worker-pools/bootstrap.py`,
`compose.yml`, `provision.py`, host updater/service/timer, retirement and telemetry helpers,
focused tests under `tests/deployment/`.

- **Specification:** [image publication](../superpowers/specs/2026-10-03-independent-worker-image-deployment-design.md#decide-what-to-publish-from-the-change-itself)
  and [running/new VMs](../superpowers/specs/2026-10-03-independent-worker-image-deployment-design.md#running-and-newly-started-vms).
- **Depends on:** Task 2 handoff interface.
- **Produces:** boot pulls current `latest`; host timer compares the current image and
  safely warms/switches a replacement on the same VM; failed candidate leaves old serving.
- [x] Add failing host-script tests for unchanged tag, changed tag, empty bulk, healthy switch,
  failed pull/warm-up and host retirement during replacement.
- [x] Run focused tests RED; implement only the host operations needed; rerun GREEN.
- [x] Verify unchanged heavy image layers are reusable across a code-only image build.
- [x] Provide a bounded one-time update command for the two existing group templates at cap one, using
  canonical VM cloud identity and a fresh exact-target read-back; do not make template mutation
  part of ordinary worker-image releases. The command and `rollingRecreate` were not run against
  production as part of implementation.

### Task 4: Make canonical Deploy component-aware and remove obsolete fleet release path

**Files:** `.github/workflows/deploy.yml`, `deploy/apply-deployment.sh`,
`deploy/run-remote.sh`, obsolete `deploy/worker-pools/release.py` and related manifest/receipt
plumbing, deployment tests and runbook.

- **Specification:** [decide what to publish](../superpowers/specs/2026-10-03-independent-worker-image-deployment-design.md#decide-what-to-publish-from-the-change-itself).
- **Depends on:** Tasks 1-3.
- **Produces:** docs-only no-op; backend-only web deploy; worker-input-only image publish;
  no shared web/worker SHA or release manifest in the steady path. Existing canonical web
  rollback, migration, health and secret-projection protections remain.
- [x] Add failing workflow/deployment tests for the three change classes and a failed
  worker publish or handoff.
- [x] Run focused tests RED; implement classification and remove superseded branches;
  rerun GREEN.

### Task 5: Final package verification and architecture reconciliation

**Files:** `README.md`, `docs/architecture.md`, `docs/engineering-jobs.md`, ADR 0028/0042/0049
and new ADR 0051, the approved spec/plan, `docs/runbooks/{deployment,worker-pools,historical-adaface-backfill}.md`,
`tests/suite-selection.toml`, `tests/test_select_test_suites.py`, and task evidence.

- **Specification:** all acceptance criteria.
- **Depends on:** Tasks 1-4.
- **Produces:** reviewed package with no contradictory release instructions.
- [x] Reconcile the operational docs and selector; run the exact selected suites for this package.
- [x] Compare implementation with ADRs 0050/0051 and update architecture facts against task
  reports and review evidence.
- [ ] Root runs `make static` and `make check` on the final reviewed package.
- [ ] Root review and final branch review remain separate gates; production cutover and live proof
  are not implied by implementation or this plan's local evidence.

## Verification

- Focused RED/GREEN: `sh scripts/run-in-test-env.sh .venv/bin/pytest -q` with the exact
  changed processing/worker/deployment test modules for each task; expected new test fails
  before code and passes afterward.
- Python normalization/type check: `.venv/bin/pre-commit run --files <changed Python files>`;
  expected Ruff/mypy success, then `make static` after integration.
- Final selector: `.venv/bin/python scripts/select_test_suites.py select --base origin/main --head HEAD`
  after the final commit, or pass every changed working-tree path as `--changed-file` before
  commit. A base-only selection is not valid evidence. Run
  `.venv/bin/python scripts/select_test_suites.py fingerprint --base origin/main` and every
  selected Make target; record the exact fingerprint.
- Root `make check`; expected Ruff, mypy, core tests, Django checks and migration drift pass.
- CI on the pushed PR commit repeats selected suites. Merge only a synchronized green PR.
- Live: read back canonical web image, worker registry pointer, group template and running
  member/host image before and after first transition; exercise one existing-compatible
  selfie request and a bounded bulk wake-up only when work exists. No synthetic customer
  data or backfill is required to publish this release.

## Operational impact and rollout

The first host-updater installation is one cap-one template patch followed by a targeted restart
of the existing selfie managed instance. Re-read group IDs, templates, deployment policy and
workload immediately beforehand. `OPPORTUNISTIC` does not restart its live VM after the patch, so
the exact `rollingRecreate` is required. Pause/drain before the combined worker/web release; wait
for the recreated member to warm and serve before unpausing. Bulk remains at zero while idle. No
second VM, disk, IAM, network or quota is introduced. Subsequent worker changes publish `latest`
and are picked up in place; backend-only and docs-only changes do not restart workers or deploy.

## Rollback

Before `processing.0016` drops `ProcessingAttempt.worker_build`, canonical automatic recovery may
restore the previous web only after its read-only schema probe succeeds. Once the drop commits,
the old web SHA is not a safe rollback, even if the migration command reports failure afterward.
The guard retains the candidate package and mode-0600 `.deployment-recovery/candidate.env`, stops
web, and leaves claims paused; recover forward with a schema-compatible new-protocol candidate or
code fix. Worker recovery may select a prior immutable digest only when it supports the active web,
processing protocol and semantic model contract; pause claims and the updater timer as required by
the [worker-pool runbook](../runbooks/worker-pools.md). Preserve all durable work and artifacts.

## Open questions

- None. Re-read cloud authority and exact template immediately before any live mutation;
  inability to obtain that read-back blocks the mutation, not local implementation.
