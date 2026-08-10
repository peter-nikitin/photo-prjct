# Preview Face Quality V4 Rollout Implementation Plan

- Date: 2026-08-10
- Status: Approved for execution
- Owner: project maintainer
- Related specification:
  [`2026-08-10-preview-face-quality-v4-rollout-design.md`](../superpowers/specs/2026-08-10-preview-face-quality-v4-rollout-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md), preview-first processing,
  immutable attempts, event-scoped face search, and immutable-image promotion
- Related ADRs: [ADR 0005](../adr/0005-promote-images-through-staging.md),
  [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md),
  [ADR 0020](../adr/0020-use-signed-direct-object-storage-media-delivery.md),
  [ADR 0024](../adr/0024-use-gallery-face-as-search-query.md), and
  [ADR 0025](../adr/0025-expand-selfie-search-with-face-clusters.md)
- ADR impact: Conforms to the listed accepted ADRs; no new or superseding ADR is required.

> Execute this plan with `$execute-implementation-plan` and its required subagent review loop.

## Goal

Deliver the approved version-4 preview-backed face generation, replay it only for
`cyclingrace-vechernee-sadovoe`, and activate it only after complete verified environment evidence.

## Scope

Implement the approved specification without a scope delta. Production provisioning, VM resizing,
new worker replicas, and any other pricing-affecting cloud action remain a separate approval gate.

## Acceptance criteria

Use the specification's acceptance criteria. Delivery additionally requires one reviewed task
commit for each implementation task, a clean merge with current `origin/main`, a green pull request,
successful staging deployment of the merge SHA, and exact environment reports captured before and
after enrollment and activation.

## Implementation

### Task 1: Production approval and bounded event replay command

**Files:**

- Modify `src/backend/processing/services/enrollment.py`.
- Modify `src/backend/processing/services/face_quality.py`.
- Add `src/backend/processing/management/commands/reprocess_event_face_embeddings.py`.
- Add or modify focused tests under `src/backend/processing/tests/`.

- **Specification:** Approval evidence; Processing and activation contract; Acceptance criteria.
- **Depends on:** Existing version-4 enrollment, projection, and activation services.
- **Produces:** Tracked exact approval evidence for the separate local preview projection, accepted
  runtime preview cohort, and reviewed immutable crosswalk plus a dry-run-by-default,
  `--apply`-guarded, event-scoped, idempotent enrollment/status command whose machine-readable
  report can gate activation.

- [ ] Add failing tests showing that approval uses the exact reviewed artifacts without fabricated
  loss counters or local/runtime byte-equivalence claims, another event is rejected, dry run writes
  nothing, apply enrolls only accepted preview-backed photos, replay is idempotent, any accepted
  derivative SHA-256/byte-size/geometry change fails closed even at the same count, and status
  exposes all terminal/nonterminal/failure/projection counts.
- [ ] Run the focused tests and confirm each fails because the production approval/command behavior
  is absent.
- [ ] Implement the smallest approval record and command on the existing services. Do not copy the
  local helper or add a compatibility path.
- [ ] Run `make test TESTS="src/backend/processing/tests/test_face_quality_activation.py
  src/backend/processing/tests/test_face_quality_reprocessing_command.py
  src/backend/processing/tests/test_enrollment.py"` and require zero failures.

### Task 2: Dark-deploy worker identity and deployment contract

**Files:**

- Modify `.env.example`, `docker-compose.yml`, and `docker-compose.prod.yml` only where tracked
  defaults must recognize the supported identity.
- Modify `deploy/apply-deployment.sh`.
- Modify `.github/workflows/deploy.yml` and `.github/workflows/promote-production.yml` only if the
  immutable-image workflow must transport the reviewed identity unchanged.
- Add or modify the focused deployment and worker configuration tests already used by the project.

- **Specification:** Scope; Deployment and failure semantics; Acceptance criteria.
- **Depends on:** Task 1's exact processor identity and activation boundary.
- **Produces:** A deployable worker that may claim `3/face_embedding/4` while deployment itself
  neither enrolls jobs nor activates the candidate, with the same configured identities promoted
  through staging and production.

- [ ] Add failing behavioral tests that render or run the deployment entrypoint and prove version 4
  is accepted only as an exact supported identity, required when explicitly requested, preserved
  through workflow configuration, and does not imply enrollment or activation.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Implement the smallest configuration change; retain existing services, resource limits,
  credentials, worker scheduling, and rollback marker behavior.
- [ ] Run the focused deployment script tests and
  `make test TESTS="src/worker/tests/test_runner.py src/worker/tests/test_contracts.py"`; require
  zero failures, then run `sh -n deploy/apply-deployment.sh`.

### Task 3: Architecture reconciliation and release verification

**Files:**

- Modify `docs/architecture.md` only to describe behavior that is now implemented in the repository,
  while keeping live environment activation explicitly unevidenced until rollout completes.
- Modify `docs/engineering-jobs.md` with code/CI/deployment evidence only after each state exists.
- Modify other documentation only when a test or accepted link requires it.

- **Specification:** Entire approved design, especially ADR impact and Acceptance criteria.
- **Depends on:** Reviewed Tasks 1 and 2.
- **Produces:** An architecture-accurate merge candidate and a release evidence checklist that does
  not confuse code, CI, deployment, processing, or activation states.

- [ ] Compare the complete diff with the specification and ADRs 0005, 0017, 0019, 0020, 0024, and
  0025; stop rather than contradicting an accepted boundary.
- [ ] Update implemented repository facts, retaining explicit statements that staging/production
  activation has not occurred before live evidence exists.
- [ ] Run `git diff --check`, the focused suites from Tasks 1 and 2, and `make check`; require zero
  failures.

## Verification

- `make test TESTS="src/backend/processing/tests/test_face_quality_activation.py
  src/backend/processing/tests/test_face_quality_reprocessing_command.py
  src/backend/processing/tests/test_enrollment.py"` exits 0.
- Focused deployment tests selected from the existing test inventory exit 0 and
  `sh -n deploy/apply-deployment.sh` exits 0.
- `make test TESTS="src/worker/tests/test_runner.py src/worker/tests/test_contracts.py"` exits 0.
- `make check` exits 0 on the current merge candidate.
- GitHub pull-request checks pass for the pushed SHA.
- The merge SHA is the exact image recorded by successful staging deployment.
- The environment replay command reports one complete eligible event cohort, zero active/retry/
  failed/stale/technical states, and exact projection coverage before activation.
- Both pre-enrollment and pre-activation validation recompute the canonical accepted runtime
  `PhotoDerivative` cohort hash as
  `6701b7436e1b00b64e701791983a0c9c1d26bcddd56f93a36dd0923aa6bc1034`; any accepted SHA-256,
  byte-size, or geometry change blocks the rollout. The local preview manifest
  `62f071941cd8281745256ed6906f37cbfdac29996f20fd6a992c7f486783d879` has the distinct canonical
  local projection hash
  `a98b5d13152683419c722a115045037fdf883a1f5cdcc3e47a2bddf5291b7d63`; that projection is linked
  to the accepted runtime cohort only by
  reviewed crosswalk hash `055d7c72614deb3b87b607f467c16365ee6e125be005e9e8f5cf2e910ec56d51`
  with `entries=17043` and `sha_mismatch=17043`; this is not byte-equivalence evidence.
- The ordinary event page, gallery-origin search, and uploaded-selfie search resolve the candidate
  generation after activation.

## Operational impact and rollout

1. Merge only after review, current-main reconciliation, full local verification, and green PR CI.
2. Allow the existing main-push workflow to build and deploy the immutable SHA to staging with
   version-4 worker support but no version-4 jobs or activation.
3. Read-only inspect staging migrations, worker/API identity configuration, current event cohort,
   jobs, attempts, projections, activations, leases, and storage-backed accepted previews.
4. Run a bounded version-4 staging smoke, then the event-only replay. Wait for exact terminal
   success and verify the ordinary site/search surfaces before staging activation.
5. If an already provisioned production environment exists, manually promote the exact recorded
   staging image through the production workflow and repeat the read-only preflight, event-only
   replay, terminal gate, activation, and live verification. If it does not exist, stop before any
   cloud provisioning and request explicit approval for the billable action.
6. Record code, CI, staging deployment, staging processing, staging activation, production
   promotion, production processing, production activation, and live verification as distinct
   states.

## Rollback

Before activation, redeploy the previous recorded image or disable candidate claims; retain queued
and terminal evidence. After activation, append a reviewed activation selecting the preceding
baseline generation, verify new searches use it, and leave all version-4 jobs, attempts,
projections, activations, and existing bearer-result snapshots unchanged.

## Open questions

None. The existence of a provisioned production environment is a discoverable operational fact,
not permission to create one if absent.
