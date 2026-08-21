# Documentation reality remediation plan

**Date:** 2026-08-21

**Status:** Complete

**Owner:** Maintainer

**Related analysis:** [Documentation reality audit](../reviews/2026-08-21-documentation-reality-audit.md)

**Related specification:** None — this plan reconciles existing repository contracts; it does not
define a new product capability.

**Related architecture:** [System architecture](../architecture.md)

**Related ADRs:** [ADR 0028](../adr/0028-operate-one-canonical-deployment.md),
[ADR 0029](../adr/0029-use-watermarked-previews-for-paid-photos.md),
[ADR 0030](../adr/0030-use-anonymous-server-side-event-carts.md)

## Goal

Make the repository's current-state documentation and supported developer commands agree with
`origin/main` at `be22bdd0118fbc6f416b96cc31683890ec930540`. Preserve the important evidence
boundary: code, CI, and a successful deployment workflow prove delivery, but do not by themselves
prove a live feature-gate value, host cron state, customer outcome, or recovery readiness.

## Non-goals

- Do not enable paid-watermark or cart gates, upload commercial assets, or implement checkout,
  payment, entitlement, or original delivery.
- Do not mutate the canonical VM, cloud resources, credentials, DNS, buckets, databases, or cron.
- Do not design or implement backup/recovery or runtime-credential cleanup in this change.
- Do not rewrite append-only job history or historical postmortem facts.

## Architecture and decision impact

The repair conforms to accepted ADRs 0028–0030. It does not introduce a new durable decision.
ADR 0016's staging-only seed proposal conflicts with the already accepted one-deployment topology;
record it as Rejected and point to ADR 0028 instead of inventing a replacement ADR. Recovery and
runtime-credential hygiene remain separate architecture projects because their open choices and
operational risk exceed a documentation reconciliation.

## Delivery sequence

### Task 1 — Restore the supported local command contracts

**Files:**

- `scripts/create-worktree.py`
- `tests/test_create_worktree.py`
- `src/worker/pytest.ini`
- `tests/test_repository_foundation.py`

**Changes:**

1. Add a failing worktree-bootstrap test that proves `manage.py check` receives the same safe test
   values as `scripts/run-in-test-env.sh`: `PHOTO_PROCESSING_ENABLED=True` and
   `PHOTO_PROCESSING_FACE_ENABLED=True`.
2. Put those values in `TEST_ENVIRONMENT`, or invoke the existing shared test wrapper if that keeps
   the bootstrap simpler. Do not copy root secrets or change `.env.example` production defaults.
3. Add `pythonpath = .` to the nested worker pytest configuration so the documented literal command
   can import the local `photo_worker` package without virtual-environment activation or packaging
   changes.
4. Extend the repository-foundation regression to protect the nested worker import-path contract.
5. Run the focused tests and the literal worker selector. Create one disposable worktree through
   `make worktree`, confirm bootstrap completes, then remove only that named disposable worktree.

**Acceptance:**

- A fresh `make worktree NAME=<disposable-name> BASE=be22bdd` reaches a successful Django check.
- `make test TESTS="src/worker/tests/test_runner.py src/worker/tests/test_contracts.py"` passes.
- No root `.env` value appears in the disposable worktree.

### Task 2 — Make the job registries structurally self-consistent

**Files:**

- `docs/product-jobs.md`
- `docs/engineering-jobs.md`
- `tests/test_repository_foundation.py`

**Changes:**

1. Add a small structural parser regression requiring exactly one detail per current-state job ID,
   unique detail IDs, an allowed current status, and agreement of row/detail status and last-updated
   date. History remains append-only and is not used as the current-state source.
2. Rename only the operational detail currently mislabeled EJ-014 to EJ-015.
3. Reconcile EJ-019 to `Delivered`: repository and deployment evidence prove delivery, while this
   audit did not establish customer outcome validation. Update the current row/detail to 2026-08-21
   and append one correction row.
4. Align stale dates for EJ-003, EJ-004, EJ-005, and the repaired EJ-013 with their detail evidence.
   Mark EJ-013 `Validated` only after Task 1's fresh worktree smoke passes.
5. Reconcile the product registry:
   - PJ-004 row date becomes 2026-08-15.
   - PJ-006 detail becomes `Candidate`, matching its evidence and current row.
   - PJ-009 becomes `Delivered` based on its merged route, signed resolver, regression, and current
     deployment; do not claim customer validation.
   - PJ-012 becomes `Delivered` and replaces the false open-PR claim with merge/current-deployment
     evidence.
   - PJ-015 becomes `Delivered`, matching EJ-019, while keeping customer outcome unvalidated.
6. Append exactly one dated history correction for each actual status change. Preserve all earlier
   rows verbatim.

**Acceptance:**

- The structural test reports 16 unique product jobs and 25 unique engineering jobs, with one
  current row and one detail per ID.
- No registry uses `Validated` when the cited evidence proves only merge/deployment.

### Task 3 — Reconcile commerce and deployment evidence

**Files:**

- `docs/architecture.md`
- `docs/product-jobs.md`
- `docs/engineering-jobs.md`

**Changes:**

1. Replace pre-merge statements in PJ-005, PJ-010, PJ-016, EJ-024, EJ-025, and the architecture with
   the precise boundary: paid watermark/cart code is merged, CI passed, and automatic deployment
   run `32457775668` succeeded; the gates and real paid assets were not observed as active.
2. Keep PJ-016 and EJ-024 `In progress` because public activation and real assets remain pending.
3. Set EJ-025 to `Delivered` only if its contract is deployment of cleanup automation. Cite that the
   successful deployment executes the committed schedule-install path, and state that live crontab
   presence and an actual cleanup run remain unvalidated.
4. Replace the obsolete “Later cart seam” with the remaining checkout/payment/entitlement/original-
   delivery seam. Remove the duplicated private-media configuration sentence.
5. Phrase DNS/TLS as the accepted topology plus dated deploy/public-monitor evidence, with the
   audit's no-direct-host-check caveat.

**Acceptance:**

- No current-state document says the merged cart has no PR, CI, deployment, or implementation.
- No document implies that checkout/payment exists or that either paid gate is currently on.
- Repository, Actions, and direct-runtime evidence are labeled distinctly.

### Task 4 — Retire or reframe fired future-work and ADR triggers

**Files:**

- `docs/future-work/2026-08-01-paid-photo-cart-action.md` (delete)
- `docs/future-work/2026-08-10-worker-selector-import-path.md` (delete after Task 1 passes)
- `docs/future-work/2026-07-31-direct-media-performance-thresholds.md`
- `docs/future-work/2026-08-07-runtime-credential-hygiene.md`
- `docs/future-work/selfie-search-lifecycle-expiration-sla.md`
- `docs/future-work/selfie-search-missing-temporary-object-reconciliation.md`
- `docs/adr/0016-allow-deterministic-staging-reference-media.md`
- `docs/adr/README.md`
- `docs/engineering-jobs.md`

**Changes:**

1. Delete the paid-cart action finding: ADR 0030 and the merged cart have satisfied its trigger, and
   PJ-016/EJ-025 now own remaining work. Do not leave a second supersession stub.
2. Delete the worker-selector import-path finding only after Task 1's literal selector command
   passes. Its repaired supported-command contract then has no distinct future trigger.
3. Mark ADR 0016 `Rejected`, link ADR 0028 as the superseding topology decision, and update the ADR
   index. Preserve its historical context.
4. Replace former-staging/current-disabled wording in the performance and selfie-lifecycle findings
   with the real missing evidence and existing concrete triggers.
5. Reframe runtime credential hygiene around the canonical deployment and
   `docker-compose.deployment.yml`. Record that EJ-017/ADR 0028 fired the design trigger and route
   the next decision to EJ-018; do not inspect or clean the host in this task.

**Acceptance:**

- Every retained future-work file has an open, concrete trigger.
- No retained future-work finding claims the repaired literal worker selector remains broken.
- No retained finding describes the retired staging topology as current.
- ADR index and record status agree.

### Task 5 — Repair navigation and institutionalize the postmortem lesson

**Files:**

- `docs/engineering-jobs.md`
- `docs/postmortems/2026-08-07-staging-deployment-after-parallel-migrations.md`
- `docs/plans/0000-template.md`
- `.agents/skills/write-plan/SKILL.md`
- `tests/test_repository_foundation.py`

**Changes:**

1. Replace the deleted promotion-workflow history link with a pinned GitHub permalink at commit
   `7fd40983db93029cf1fc21addca12c2a9ed22040`.
2. Fix the postmortem links to `../runbooks/django-migration-conflicts.md` and the current
   `tests/test_reconcile_deploy_issue.py`.
3. Add the seven worker/state/artifact safeguards from the 2026-07-31 postmortem to the plan
   template and project `$write-plan` guidance, conditionally required whenever a plan changes a
   worker contract, durable processing state, or generated artifact:
   - reset semantics;
   - mixed-version compatibility;
   - retry behavior;
   - stale-state behavior;
   - artifact identity/versioning;
   - rollout and rollback order;
   - realistic end-to-end verification.
4. Add a repository structural regression that keeps the template and planning skill aligned. Use
   the `$writing-for-agents` skill when editing agent guidance.

**Acceptance:**

- The audit corpus has zero unresolved local Markdown links; the intentionally historical workflow
  uses a stable external permalink.
- A future worker/state/artifact plan cannot omit the postmortem checklist silently.

### Task 6 — Final verification and handoff

Run from the isolated implementation worktree:

```bash
make test TESTS="tests/test_create_worktree.py tests/test_repository_foundation.py"
make test TESTS="src/worker/tests/test_runner.py src/worker/tests/test_contracts.py"
make check
git diff --check
```

Also run the repository's Markdown-link scan/regression and repeat the disposable `make worktree`
smoke after all changes. Review the final diff for accidental live-state claims, rewritten history,
unrelated documentation edits, and secret material.

## Separate follow-up architecture work

These items are deliberately not implementation tasks in this plan:

1. **EJ-010 recovery:** write and approve a specification/ADR for RPO/RTO, database and
   media-metadata scope, off-host retention, restore authority, and a non-destructive drill before
   any cloud mutation.
2. **EJ-018 runtime credentials:** perform a read-only canonical-host inventory, then write and
   approve the credential-delivery/rotation design before cleanup, rotation, or Compose changes.

## Rollback

The repository changes are local documentation, tests, and developer-command configuration. If a
structural contract is wrong, revert the complete remediation commit range
`869aadc^..7983004` (the six task commits), or revert the delivered
remediation PR/merge as one change set; do not revert only one task commit. The plan authorizes no
runtime or cloud mutation, so it has no service rollback procedure.
