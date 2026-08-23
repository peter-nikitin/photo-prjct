# Feature-flag Definition Reconciliation Implementation Plan

- Date: 2026-08-22
- Status: Approved
- Owner: project maintainer
- Related specification: [Feature-flag definition reconciliation design](../superpowers/specs/2026-08-22-feature-flag-definition-reconciliation-design.md)
- Related architecture: [Current architecture](../architecture.md)
- Related ADRs: [ADR 0028](../adr/0028-operate-one-canonical-deployment.md), [ADR 0032](../adr/0032-reconcile-code-owned-feature-flags-at-startup.md)
- ADR impact: Conforms to ADR 0028 and implements accepted ADR 0032.

## Goal

Deliver the approved code-owned feature-definition registry and reconcile it automatically into the canonical database without activating newly introduced features.

## Scope

Implements the approved specification without scope changes. Execute this plan with `$execute-implementation-plan`.

## Acceptance criteria

The specification's acceptance criteria apply. Delivery is complete only after automatic deployment succeeds and read-only live verification proves the deployed registry exactly matches the database, pre-existing registered states are preserved, new definitions are `off`, and only the pre-reviewed stale set was deleted.

## Implementation

### Task 1: Authoritative registry and definition-based evaluation

**Files:** `src/backend/feature_flags/registry.py`, `src/backend/feature_flags/services.py`, `src/backend/feature_flags/testing.py`, production feature-gate consumers under `src/backend/picflow/`, `src/backend/config/`, and `src/backend/commerce/`, their affected tests, and a repository contract test when needed.

- **Specification:** Registry and evaluation contract; acceptance criteria 4, 5.
- **Depends on:** None.
- **Produces:** Validated immutable `FeatureDefinition` values, the deterministic complete registry tuple, and evaluation services that accept definitions.

- [ ] Add focused failing tests for definition validation, existing `off`/`staff`/`on` semantics, explicit test-only definitions, and rejection of raw-string production calls.
- [ ] Run the targeted tests and confirm failures are caused by the missing registry/interface.
- [ ] Implement the five current production definitions and migrate every production call site to named definitions without changing exposure or authorization semantics.
- [ ] Run `make test TESTS="src/backend/feature_flags/tests src/backend/picflow/tests/test_models.py src/backend/picflow/tests/test_views.py src/backend/commerce/tests src/backend/config/tests"` and expect zero failures.

### Task 2: Transactional reconciliation, immutable Admin definitions, and local bootstrap reuse

**Files:** `src/backend/feature_flags/management/commands/sync_feature_flags.py`, `src/backend/feature_flags/management/commands/bootstrap_local_purchase_review.py`, `src/backend/feature_flags/admin.py`, and focused tests under `src/backend/feature_flags/tests/`.

- **Specification:** Reconciliation contract; Admin and lifecycle; Existing data and first rollout; acceptance criteria 1-4, 6, 8.
- **Depends on:** Task 1 registry and definition interface.
- **Produces:** Idempotent `sync_feature_flags`, state-only Admin editing, and registry-backed local purchase enablement.

- [ ] Add focused failing tests for create-off, state preservation, description refresh, stale deletion, idempotence, validation-before-write rollback, sanitized counts, Admin add/delete/definition-edit denial, and exact local bootstrap behavior.
- [ ] Run the targeted tests and confirm the expected behavioral failures.
- [ ] Implement the smallest transactional command and Admin/bootstrap changes; do not add a schema migration or activation option.
- [ ] Run `make test TESTS="src/backend/feature_flags/tests"` and expect zero failures.

### Task 3: Fail-closed startup ordering and implemented architecture

**Files:** `src/backend/entrypoint.sh`, deployment/startup contract tests under `tests/deployment/` and affected backend tests, `docs/architecture.md`, `docs/product-jobs.md`, and `docs/engineering-jobs.md` when their current evidence rows require synchronization.

- **Specification:** Reconciliation contract; Existing data and first rollout; Failure and rollback; acceptance criteria 7, 9.
- **Depends on:** Task 2 command.
- **Produces:** Explicit `migrate -> sync_feature_flags -> remaining bootstrap -> Gunicorn` startup behavior and current documentation.

- [ ] Add a behavioral startup test that proves ordering and fail-closed behavior when reconciliation exits nonzero.
- [ ] Run the targeted deployment/startup tests and confirm the expected failure.
- [ ] Insert reconciliation at the approved startup seam and update implemented architecture facts without changing the worker startup paths.
- [ ] Run `make test TESTS="tests/deployment/test_deployment_scripts.py src/backend/ingestion/tests/test_bootstrap_group.py"` and expect zero failures.

### Task 4: Reusable project skill for feature-gate lifecycle work

**Files:** `.agents/skills/manage-feature-flags/SKILL.md`, `.agents/skills/manage-feature-flags/agents/openai.yaml`, and focused skill/repository contract tests when needed.

- **Specification:** Complete registry/evaluation, reconciliation, Admin/lifecycle, rollout, failure, and rollback contracts.
- **Depends on:** Tasks 1-3 implemented interfaces and operational boundaries.
- **Produces:** A concise discoverable project skill that future agents must use when adding, changing, activating, removing, deploying, or troubleshooting feature flags.

- [ ] Run a no-skill baseline scenario against a fresh agent and record the lifecycle or safety steps it omits.
- [ ] Initialize the project skill with the repository's skill tooling and write the minimum instructions that correct the observed omissions: registered definition and typed call sites, new-row `off`, Admin-only state changes, call-site-plus-definition removal, startup ownership, first-rollout inventory, verification, and rollback semantics.
- [ ] Validate skill metadata and run the same scenario with the skill; confirm the agent follows the complete lifecycle without inventing manual row creation or worker reconciliation.
- [ ] Run the focused repository/skill checks and record their successful outcome after the last skill-file change.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the approved specification, ADR 0028, ADR 0032, and `docs/architecture.md`.
- [ ] Confirm no schema migration, second deployment, request-time writes, worker reconciliation, or automatic activation was introduced.
- [ ] Record conformance and operational rollback in the pull request.

## Verification

- `make test TESTS="src/backend/feature_flags/tests"` — all focused feature-flag tests pass.
- `make test TESTS="src/backend/picflow/tests src/backend/config/tests src/backend/commerce/tests src/backend/ingestion/tests/test_bootstrap_group.py tests/deployment/test_deployment_scripts.py"` — all affected integration and startup contracts pass.
- Run the skill validator and its before/after application scenario — project skill metadata is valid and the skill corrects the observed baseline omissions.
- `make check` — the complete repository quality suite passes after the final reviewed change.
- `git diff --check` — no whitespace errors.
- No visual regression run is required because the change has no customer-visible layout or snapshot effect.

## Operational impact and rollout

Before merge, use a read-only command against the canonical web container to inventory only feature keys and states. Compare that inventory with the five-definition registry and classify every absent-registry row as stale before deployment. Merge through the normal pull-request path. The automatic deployment runs migrations, then transactional reconciliation, then existing bootstrap/static/Gunicorn startup. After deployment, compare the live database and deployed registry read-only, verify pre-existing states against the saved inventory, verify newly created rows are `off`, and inspect the bounded reconciliation/startup result. Do not change any live state during verification.

## Rollback

Use the existing deployment rollback and preserve the database volume. For this first rollout, the
prior image predates reconciliation: it leaves the candidate-mutated rows in place and cannot
recreate deleted rows or their states. The saved live inventory is the recovery record, and an
incorrect mutation requires an approved corrective deployment or then-authoritative operator
action. The reviewed stale set for this rollout is empty. On later rollbacks where the prior image
also owns a registry and reconciler, its registry becomes authoritative: restored definitions are
recreated in `off`, unknown definitions are removed, and operators deliberately restore any prior
exposure through Admin.

## Open questions

None.
