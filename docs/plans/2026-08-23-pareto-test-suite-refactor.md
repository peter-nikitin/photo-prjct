# Pareto Test Suite Refactor Implementation Plan

- Date: 2026-08-23
- Status: Approved
- Owner: project maintainer
- Related specification:
  [Pareto test suite refactor design](../superpowers/specs/2026-08-23-pareto-test-suite-refactor-design.md)
- Related architecture: [Architecture](../architecture.md)
- Related ADRs:
  [ADR 0001](../adr/0001-django-modular-monolith.md),
  [ADR 0002](../adr/0002-postgresql-system-of-record.md),
  [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0028](../adr/0028-operate-one-canonical-deployment.md), and
  [ADR 0032](../adr/0032-keep-durable-knowledge-test-executable-contracts.md)
- ADR impact: implements accepted ADR 0032 and does not change runtime architecture.

> **For agentic workers:** execute this plan through the project
> `$execute-implementation-plan` skill.

## Goal

Implement the approved specification as a smaller layered test system whose core runs on every
change, whose expensive suites are selected deterministically from changed paths, and whose tests
primarily protect product, persistence, privacy, and operational invariants.

## Architecture

`tests/suite-selection.toml` is the single machine-readable mapping from repository paths to the
`core`, `operational`, `migrations`, and `visual` suites. `scripts/select_test_suites.py` is a pure,
network-free adapter over that mapping for Make, CI, and agents. Pytest markers separate expensive
layers while core remains the default; tests inside each layer are consolidated around one primary
owner per invariant rather than duplicated across model, service, view, and flow surfaces.

The refactor does not add a fake repository abstraction for PostgreSQL. Pure policy is moved or
kept behind dependency-free functions where that deepens an existing module; PostgreSQL
constraints, transactions, leases, and representative concurrency remain real database tests.

## Global constraints

- Core is `unit + db + product_flow` and runs on every pull request.
- `operational`, `migrations`, and `visual` run only when the selector chooses them; an unknown
  production, workflow, build, or infrastructure path selects all expensive suites.
- The selector is deterministic, has no network access, accepts either `base/head` or explicit
  changed files, and reports both booleans and human-readable reasons.
- Coverage includes every production Python package, including `commerce` and `feature_flags`, and
  retains branch `fail_under = 75`.
- Each invariant has one primary test surface. A higher layer tests only its integration
  responsibility and does not repeat all lower-layer state transitions.
- Retain unique security, privacy, authorization, immutable-snapshot, transaction, lease,
  migration, deployment fail-before-mutation, cutover, rollback, and recovery coverage.
- Retain one representative concurrency witness per locking or fencing mechanism; remove only
  witnesses that prove the same mechanism and outcome.
- Do not add compatibility paths, speculative abstractions, SQLite substitutes, or fake
  PostgreSQL repositories.
- The observed 20–30% test-LOC reduction is directional, not a deletion quota. A test is removed
  only after its invariant is mapped to retained evidence or classified as non-product prose/style.
- The default deployment topology, migration behavior, runtime feature gates, and product behavior
  must not change.
- Root runs complete `make check` once after all reviewed task commits. Expensive selected suites
  run once on the unchanged final package; CI owns the post-push repetition.

## Scope

Implements the approved specification without scope changes. Production modules may be refactored
only to expose an existing pure policy seam; observable product behavior is unchanged.

## Acceptance criteria

- `make test`, `make test-operational`, `make test-migrations`, and `make test-all` expose the four
  documented Python verification interfaces.
- `make check` runs static analysis, core coverage, Django checks, and migration drift without
  running operational or migration-rehearsal tests.
- Pull-request CI has stable core, operational, migration, and visual job names. Optional jobs run
  the complete selected layer or succeed with an explicit selector reason.
- The selector's fixture matrix proves core-always, each expensive trigger, combined triggers,
  documentation-only changes, explicit changed-file input, base/head input, and fail-closed unknown
  paths.
- Every removed or merged test is accounted for in the task report by retained invariant owner,
  representative retained test, and classification (`duplicate`, `prose/style`, or `superseded`).
- Pure invariant tests do not request database access; retained database tests name the PostgreSQL
  behavior they prove.
- Operational coverage retains fail-before-mutation, secret cleanup, candidate validation,
  successful cutover, and one rollback/recovery witness for each current critical phase.
- Agent pressure scenarios show the project skill chooses the minimum complete suite set, reuses
  valid unchanged evidence, and invalidates evidence after a package-changing fix.
- Final branch metrics report test cases, test LOC, layer runtimes, coverage scope, and wall time
  against the 2026-08-23 baseline without treating the LOC range as a hard gate.

## Worker/state/artifact release safeguards

Not applicable. This plan changes developer verification and tests only. It does not change a
worker contract, durable processing state, generated artifact identity, database schema, deployment
topology, or rollout order.

## Implementation

### Task 1: Executable suite taxonomy and changed-path selector

**Files:**

- Create `tests/suite-selection.toml`.
- Create `scripts/select_test_suites.py`.
- Create `tests/test_select_test_suites.py`.
- Create root `conftest.py` for manifest-driven collection-time path marking.
- Modify `pyproject.toml`.
- Modify `Makefile`.
- Modify `.github/workflows/ci.yml`.
- Modify `tests/test_repository_foundation.py` only for stable workflow/Make structure assertions.

**Specification:** Test Layers, Changed-Path Suite Selector, Make and Local Interfaces, CI Shape,
Coverage Policy, and Failure Semantics.

**Depends on:** accepted ADR 0032.

**Produces:**

- `load_config(path: Path) -> SuiteSelectionConfig` rejects missing suites, unsupported keys,
  invalid patterns, and paths owned by no known repository category.
- `select_suites(config: SuiteSelectionConfig, changed_files: Sequence[str]) -> Selection` returns
  `core=True` plus booleans and ordered reasons for `operational`, `migrations`, and `visual`.
- CLI `select --changed-file PATH... --format json|github` and
  `select --base REF --head REF --format json|github`.
- CLI `fingerprint --base REF` returns one SHA-256 for the base revision plus tracked and untracked
  working-tree task content; changing any package file changes the result.
- Make targets `test`, `test-operational`, `test-migrations`, `test-all`, and `check` with the
  semantics in the acceptance criteria.

- [ ] Write selector unit tests first using temporary TOML fixtures and an isolated temporary Git
  repository. The first run must fail because the module and manifest do not exist.
- [ ] Implement strict TOML loading with `tomllib`, POSIX-normalized paths, `fnmatch`, sorted unique
  reasons, and no third-party dependency.
- [ ] Implement explicit-file and Git `diff --name-only --diff-filter=ACMRD` inputs. Treat Git
  failure, empty base/head, malformed output, absolute paths, and unmatched known-repository paths
  as fail-closed exhaustive selection.
- [ ] Implement the fingerprint from `git rev-parse`, `git diff --binary`, and sorted untracked file
  paths plus bytes. Never include ignored files or `.git` metadata.
- [ ] Add strict pytest markers `unit`, `db`, `product_flow`, `operational`, and `migration`. The
  collection hook assigns exactly one: manifest-owned `operational`, `migration`, or
  `product_flow` takes precedence; remaining Django DB-enabled items become `db`; remaining items
  become `unit`. Collection fails on conflicting explicit ownership.
- [ ] Change coverage `source` to include `src/backend/commerce` and
  `src/backend/feature_flags` while retaining branch coverage and `fail_under = 75`.
- [ ] Add Make targets. `make test` and the coverage phase of `make check` exclude
  `operational`, `migration`, and `clone_deployed_slow`; `make test-all` is the exhaustive local
  pytest entry point.
- [ ] Split CI into stable jobs named `Quality core`, `Operational tests`, `Migration tests`, and
  `Visual tests`, preceded by `Select test suites`. Each optional job always concludes successfully
  when unselected and prints the selector reason; setup and test steps are conditional.
- [ ] Run selector tests, focused repository-foundation tests, `make static`, a no-op documentation
  selection, each single-suite trigger, and an unknown-path fail-closed selection.

### Task 2: Agent verification skill developed with process TDD

**Files:**

- Create `.agents/skills/select-verification-suites/SKILL.md`.
- Create `.agents/skills/select-verification-suites/agents/openai.yaml`.
- Create `.agents/skills/select-verification-suites/scenarios/pressure-tests.md`.
- Modify `AGENTS.md`.
- Modify `.agents/skills/execute-implementation-plan/SKILL.md` only to point execution and review
  roles to the selector/evidence skill.
- Modify `tests/test_repository_foundation.py` only for generic skill metadata discovery and schema
  validation; do not assert skill prose.

**Specification:** Agent Verification Workflow and Engineering-Knowledge Decision.

**Depends on:** Task 1 CLI, Make targets, and fingerprint interface.

**Produces:** one short project skill that tells agents when to run focused red-green checks, when
to invoke the selector, how to fingerprint evidence, when evidence is invalid, and which final
root/CI runs own exhaustive repetition.

- [ ] Before creating the skill, run the three pressure scenarios recorded in
  `scenarios/pressure-tests.md` against an agent that may read `AGENTS.md`, Make, CI, and the
  selector but not the new skill: a pure policy edit with valid core evidence, a deploy-script edit,
  and a template plus migration edit followed by a review fix. Record whether it reruns complete
  suites, omits an expensive suite, or reuses evidence with a mismatched fingerprint.
- [ ] Write the minimal skill with frontmatter `name: select-verification-suites` and a description
  that triggers for test selection, plan implementation/review, and handoff. Keep executable values
  in the selector and Make targets; the skill links to them instead of copying path rules.
- [ ] Require reports to contain exact command, exit status, result summary, selector reasons,
  fingerprint, and confirmation that the final GREEN followed the last affected task-file change.
- [ ] Define evidence reuse as equality of fingerprint plus coverage of every selector-required
  suite. A changed fingerprint invalidates affected suite evidence; a reviewer reruns only for a
  named gap, reproduction concern, or invalid package.
- [ ] Repeat the same pressure scenarios with the skill available. Require the minimum complete
  suite set, no duplicate full run across implementer/reviewer/root, operational selection for the
  deploy edit, visual+migrations selection for the combined edit, and invalidation after the fix.
- [ ] Validate skill metadata through generic discovery/schema tests and manually inspect the
  pressure comparison. Do not add tests for exact sentences, headings, or copied commands.

### Task 3: Repository, documentation, and visual-contract consolidation

**Files:**

- Modify `tests/test_repository_foundation.py`.
- Modify `tests/test_branding.py`.
- Modify `tests/test_visual_reference.py`.
- Modify `tests/test_visual_test_runner.py`.
- Modify `tests/test_create_worktree.py` and `tests/test_worktree_commands.py` where they duplicate
  one worktree outcome.
- Modify `tests/test_test_database_cleanup.py` to retain its unique PostgreSQL cleanup contract and
  classify it as `db`.
- Modify project documentation only when deleting an obsolete duplicated source, not to preserve
  wording expected by a test.

**Specification:** Engineering-Knowledge Decision, Documentation and Script Test Policy, and Test
Removal Rule.

**Depends on:** Task 1 markers and Task 2 agent workflow.

**Produces:** structural repository tests limited to ADR file/index/status/link consistency,
supported skill metadata, local links, and directly executable worktree/visual-runner contracts.

- [ ] Inventory every test in the named files in the task report. For each deletion or merge, name
  its retained owner or classify it as exact prose, copied command, CSS fragment, historical count,
  or duplicate configuration claim.
- [ ] Add or retain one failing structural test for each stable contract before deleting the
  brittle variants: ADR index matches file status and link, skill manifests parse, local links
  resolve, visual runner propagates success/failure, and worktree creation produces an isolated
  test-safe environment.
- [ ] Remove assertions for exact Markdown sections, exact customer-independent prose, copied shell
  snippets, CSS fragments, historical terminology/counts, and semantic equality between docs and
  executable configuration.
- [ ] Consolidate worktree tests around observable directory, branch, venv link, ignored local
  `.env`, and failure cleanup outcomes rather than duplicated subprocess call sequences.
- [ ] Run all six focused modules and `make static`. Record before/after cases, LOC, and runtime.

### Task 4: Operational and deployment invariant consolidation

**Files:**

- Modify `tests/deployment/test_*.py`.
- Modify `tests/monitoring/test_public_health.py`.
- Modify `tests/test_migration_immutability.py` and `tests/test_reconcile_deploy_issue.py` only when
  an operational invariant has a more direct owner.
- Modify deployment/operational production scripts only to expose an existing outcome-oriented
  seam; do not change rollout behavior, phases, or external side effects.

**Specification:** Operational Tests, Migration Tests, Test Removal Rule, and Failure Semantics.

**Depends on:** Task 1 operational/migration selection.

**Produces:** a minimized operational layer centered on phase outcomes and forbidden side effects,
not exhaustive fake command order.

- [ ] Inventory all operational tests by current critical phase. For every current deployment
  phase retain: validation before mutation, secret cleanup, candidate validation, successful
  cutover, and one rollback or recovery witness. Retain migration identity/immutability separately.
- [ ] Parameterize same-shape input validation and failure-before-mutation cases only when the
  expected outcome and forbidden side effects are identical.
- [ ] Replace exact full command-order assertions with required command, forbidden mutation, final
  state, and cleanup assertions. Keep exact ordering only where order is itself the rollback or
  security contract.
- [ ] Collapse repeated concurrency/timing witnesses for the same shell lock or phase fence to one
  representative deterministic case.
- [ ] Preserve one success and one realistic failure/recovery path for Compose identity cutover,
  environment-secret replacement, candidate deployment, monitoring reconciliation, local web,
  Object Storage copy, and deployed-database clone.
- [ ] Run `make test-operational`, `make test-migrations`, and static checks for any changed Python
  script. Record before/after cases, LOC, summed test durations, and wall time.

### Task 5: Commerce, ingestion, gallery, and feature-flag invariant consolidation

**Files:**

- Modify `src/backend/commerce/tests/test_*.py`.
- Modify `src/backend/ingestion/tests/test_*.py`.
- Modify `src/backend/picflow/tests/test_*.py`.
- Modify `src/backend/feature_flags/tests/test_*.py`.
- Modify `src/backend/config/tests/test_*.py`.
- Modify corresponding production modules only when extracting an existing pure pricing,
  capability, eligibility, state-transition, or pagination seam.

**Specification:** Unit Tests, Database Tests, Product-Flow Tests, Core Test Composition, and Test
Removal Rule.

**Depends on:** Task 1 core markers and coverage scope.

**Produces:** one primary owner for pricing/order capability, cart/order snapshots, ingestion
confirmation/resume, gallery authorization/pagination, and feature-gate policy; plus one critical
product flow per ingestion, gallery, cart, purchase, and delivery path.

- [ ] Build the task report inventory before deletion. Map model/service/view/flow repetitions to
  one retained owner and name the unique integration responsibility of every remaining higher-layer
  test.
- [ ] Move or keep pure pricing, DTO, capability, feature-gate, and pagination policy tests outside
  database-enabled classes. A pure test must fail if it accidentally requests database access.
- [ ] Retain real PostgreSQL tests for unique/foreign-key/check constraints, immutable commercial
  snapshots, transaction rollback, cart/order identity, and one representative locking witness per
  commerce or ingestion lock.
- [ ] Consolidate HTTP tests around authorization boundary, accepted request, realistic validation
  failure, redirect/response contract, and committed state. Remove exhaustive intermediate state
  combinations already owned by pure or DB tests.
- [ ] Retain one end-to-end product flow each for upload confirmation/resume, numbered gallery and
  authorized media, anonymous paid cart, paid purchase, payment callback, and original delivery.
- [ ] Run focused tests for all four apps, then core coverage for those packages. Record
  before/after cases, LOC, DB-enabled cases, coverage, and runtime.

### Task 6: Processing, selfie-search, and worker invariant consolidation

**Files:**

- Modify `src/backend/processing/tests/test_*.py`.
- Modify `src/backend/selfie_search/tests/test_*.py`.
- Modify `src/worker/tests/test_*.py`.
- Modify `tests/processing/test_*.py`.
- Modify corresponding production modules only when extracting an existing pure ranking, quality,
  eligibility, DTO, wire-contract, or state-transition seam.

**Specification:** Unit Tests, Database Tests, Product-Flow Tests, Core Test Composition, and Test
Removal Rule.

**Depends on:** Task 1 core markers and coverage scope.

**Produces:** one primary owner for processing state transitions, leases, preview publication,
selfie privacy/ranking/result authorization, and worker wire contracts; plus one critical processing
and selfie-search flow.

- [ ] Inventory repetitions across model, service, callback/view, command, and E2E surfaces before
  deletion. Preserve detector, embedding, threshold, privacy, and publication boundaries as
  distinct invariants.
- [ ] Keep pure ranking, clustering, face-quality, DTO serialization, result filtering, and worker
  wire-contract tests database-free. Do not replace PostgreSQL job/retry/lease behavior with mocks.
- [ ] Retain real PostgreSQL tests for job enrollment, claim/lease/fence, retry/terminal state,
  accepted-result idempotency, preview publication, cleanup-before-publication, consented feedback,
  and result/event isolation.
- [ ] Retain one representative concurrency witness per claim, callback fence, cluster update, and
  cleanup mechanism. Remove repetitions that prove the same lock and final state.
- [ ] Consolidate view/command tests around authorization, accepted input, realistic failure,
  forbidden publication, and committed outcome; remove intermediate permutations already owned by
  pure or DB tests.
- [ ] Retain one critical processing pipeline flow, one paid-watermarked preview flow, and one
  selfie submission-to-authorized-result flow. Keep worker container packaging separate from
  product behavior.
- [ ] Run focused processing, selfie-search, worker, and processing-E2E tests, then core coverage for
  those packages. Record before/after cases, LOC, DB-enabled cases, coverage, and runtime.

### Task 7: Final architecture, workflow, and evidence reconciliation

**Files:**

- Create or modify `docs/testing.md` as the short human entry point to Make targets, selector, layer
  ownership, and evidence reuse; link rather than duplicate manifest rules.
- Modify `docs/architecture.md` only for the delivered verification boundary.
- Modify `docs/engineering-jobs.md` for EJ-002/EJ-005 evidence and status if delivery satisfies it.
- Modify `AGENTS.md` to reconcile final command and evidence guidance with the delivered selector.
- Modify this plan's verification table with measured final results.

**Specification:** all sections and acceptance criteria.

**Depends on:** reviewed Tasks 1–6.

**Produces:** discoverable delivered workflow, final metrics, and no stale claim that every suite or
visual test runs on every pull request.

- [ ] Compare delivered behavior with the specification and ADRs 0001, 0002, 0017, 0028, and 0032.
  Stop rather than contradicting an accepted ADR.
- [ ] Run selector fixtures for documentation-only, backend policy, DB model, deploy script,
  migration, visual, combined, and unknown-path changes; record selected suites and reasons.
- [ ] Measure final Python test cases, test LOC, DB-enabled share, per-layer wall time, slowest files,
  JS cases, and branch coverage over every production package. Compare to the dated baseline.
- [ ] Update architecture and engineering-job evidence only for delivered facts. Do not add prose
  assertions that test the documentation wording.
- [ ] Run focused documentation/link/skill metadata tests and `make static`. Leave full final
  verification to the root completion step so it is not repeated inside this task.

## Verification

Run these commands on the unchanged final reviewed branch, in this order:

1. `git diff --check` — the final reviewed package has no whitespace errors.
2. `make check` — static analysis, core branch coverage at or above 75%, Django system checks, and
   migration drift pass once.
3. `make test-operational` only when the final selector chooses `operational` — complete layer
   passes once.
4. When the selector chooses `migrations`, run both `make test-migrations` and
   `.venv/bin/python scripts/check_migration_immutability.py --base 0b0585a --head HEAD` once —
   the first verifies the migration layer and the second verifies the actual package's migration
   identity.
5. `npm run test:js` and `npm run test:visual` only when selected — JavaScript and visual regression
   pass once. Visual baselines are not updated unless visual behavior intentionally changed.
6. `make test-all` is the manual exhaustive interface; run it for final implementation evidence if
   its coverage is not already the union of steps 2–5, not as an automatic duplicate after every
   role.

### Measured task evidence before final root verification

The dated branch-base comparison below is structural or collection-only evidence from Task 7;
layer runtimes and package coverage are the final GREEN evidence recorded by their owning tasks.
They are directional delivery metrics, not additional acceptance gates. Structural file and LOC
counts cover tracked Python files under `tests/`, test-named Python files under `src/`, and the
root/test-support scripts that participate in collection.

| Metric | 2026-08-23 structural base `0b0585a` | Reconciled task package |
| --- | ---: | ---: |
| Named Python test definitions | 1,911 | 1,863 (-48, -2.51%) |
| Python test/support files | 158 | 161 (+3) |
| Python test/support LOC | 72,583 | 71,550 (-1,033, -1.42%) |
| Collected Python cases | No collected baseline at `0b0585a`; Task 1 post-taxonomy reference: 2,444 | 2,393 (-51, -2.09% from post-taxonomy reference) |
| Layer collection | Not recorded as one baseline | 814 unit, 1,118 db, 27 product-flow, 392 operational, 42 migration |
| PostgreSQL-capable core share | Not recorded as one baseline | 1,145 of 1,959 core cases (58.4%) |
| JavaScript / visual scenarios | Not recorded by this task | 108 / 57 |

Task 5 recorded 74.06 seconds wall time and 88.48% branch coverage for its five core packages;
Task 6 recorded 93.11 seconds and 82.66% for processing, selfie search, and the worker. Task 4
recorded 129.59 seconds for the operational layer and 16.41 seconds for migrations. Its JUnit
measurement reported 273.46 seconds of summed operational case duration; no final branch-wide
per-file duration scan was run in this task. Coverage configuration includes all eight maintained
production Python packages with branch `fail_under = 75`. The 1.42% LOC reduction misses the
non-gating 20–30% directional range. The Task 5 and Task 6 measurements cover disjoint packages
(and Task 6's sample grew), so they do not establish material whole-core acceleration; the final
root-owned core run supplies the comparable wall time and slow-file ranking.

## Operational impact and rollout

None. CI workflow shape and contributor commands change, but runtime configuration, database
schema, deployment topology, feature gates, worker contract, and production operation do not.
Stable required CI job names remain available for branch protection while optional jobs explicitly
report why they did or did not execute.

## Rollback

Revert the task commits in reverse order. The refactor has no durable-data effect. If selector
behavior is suspected, fail closed by running `make test-all`, JavaScript tests, and visual tests
until the selector fix is reviewed; do not weaken branch protection or deployment safety checks.

## Open questions

None.
