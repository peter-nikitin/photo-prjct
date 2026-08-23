# Pareto Test Suite Refactor Design

## Status

Approved in conversation by the maintainer on 2026-08-23. Written review is pending. Implementation
planning remains blocked until proposed ADR 0033 is explicitly accepted.

- Related architecture: [`docs/architecture.md`](../../architecture.md), accepted constraints,
  change rules, current deployment topology, and security, privacy, and legal boundaries.
- Related engineering jobs:
  [`EJ-002 — Contributor — Receive complete CI feedback`](../../engineering-jobs.md#ej-002--contributor--receive-complete-ci-feedback),
  [`EJ-003 — Maintainer — Deploy an immutable image`](../../engineering-jobs.md#ej-003--maintainer--deploy-an-immutable-image-to-the-canonical-deployment),
  and
  [`EJ-005 — Contributor — Reproduce visual regression`](../../engineering-jobs.md#ej-005--contributor--reproduce-visual-regression).
- Related ADRs:
  [ADR 0001](../../adr/0001-django-modular-monolith.md),
  [ADR 0002](../../adr/0002-postgresql-system-of-record.md),
  [ADR 0004](../../adr/0004-repository-engineering-knowledge.md),
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0028](../../adr/0028-operate-one-canonical-deployment.md), and proposed
  [ADR 0033](../../adr/0033-keep-durable-knowledge-test-executable-contracts.md).
- ADR impact: **Supersedes ADR 0004.** Proposed ADR 0033 retains Git-versioned durable decisions
  while removing the requirement to encode narrative wording and duplicated operational guidance
  as executable CI contracts. The remaining test design conforms to ADRs 0001, 0002, 0017, and
  0028 by retaining Django module interfaces, real PostgreSQL witnesses, private-worker contracts,
  and deployment rollback protection.

## Outcome

FindMe Photo has a smaller, faster, explicitly layered test suite that spends routine contributor
time on the highest-value product, persistence, privacy, and deployment invariants. Each invariant
has one primary test surface. Tests at other layers prove only their own integration responsibility
instead of repeating the same internal state transitions.

Every pull request receives a stable required core result. Expensive operational, migration, and
visual suites run when the changed package can affect them. A complete exhaustive suite remains
available manually. Agents and CI use the same machine-readable suite selector and reuse evidence
for an unchanged working-tree package instead of rerunning complete suites at every role boundary.

## Current Evidence

The 2026-08-23 baseline at `0b0585a` collected 2,384 default Python cases from 140 files plus 43
opt-in clone-deployed cases. The repository contained 71,037 lines of Python tests and 35,212 lines
of non-test, non-migration production Python. Of the default cases, 1,059 used Django's real
PostgreSQL test database; 1,051 of 1,560 backend cases used it.

The full default Python run passed 2,381 cases with three skips in 135 seconds. The coverage run
passed in 145 seconds and reported 84.17%, but its configured source list omitted `commerce` and
`feature_flags`. A focused `commerce` branch-coverage run reported 86.90%. The JavaScript suite
passed 108 cases. The visual suite contained 60 Playwright scenarios and was not rerun for this
design-only baseline.

Runtime was concentrated rather than proportional to case count. The deployment-script file used
155 seconds of summed test duration; deployment local-web, clone, secret, monitoring, and Compose
contracts plus migration rehearsals dominated the remaining cost. Many pure worker and product
contract matrices completed in fractions of a second.

## Scope

### Included

- Define explicit `unit`, `db`, `product_flow`, `operational`, and `visual` test layers.
- Assign every retained invariant one primary test surface.
- Add a machine-readable changed-path suite selector shared by local agents and CI.
- Add stable local targets for core, operational, migration, exhaustive, and existing visual runs.
- Include every production Python package, including `commerce` and `feature_flags`, in branch
  coverage.
- Replace duplicated model, application, HTTP, and flow assertions with interface-level tests.
- Extract pure decision modules where existing duplicated logic provides a real seam.
- Move pure validation out of database-backed fixtures.
- Reduce operational scenario matrices while retaining representative mutation, rollback, secret,
  recovery, and concurrency guarantees.
- Remove narrative exact-string, CSS-fragment, command-copy, and duplicated configuration tests.
- Supersede ADR 0004 with a narrower durable-knowledge decision.
- Add a project agent skill for suite selection, evidence reuse, and one-final-package verification.
- Reconcile `AGENTS.md`, Make targets, CI, engineering jobs, and architecture documentation with the
  delivered workflow.

### Excluded

- Product behavior, database schema, migration contents, deployment topology, cloud resources, or
  runtime feature-flag changes.
- Replacing PostgreSQL-authoritative behavior with a fake repository.
- A generic domain framework, test DSL, mutation-testing platform, or new test runner.
- A fixed test-count, line-count, or coverage target that authorizes deleting a unique safety
  guarantee.
- Live deployment, VM mutation, external-service calls, or visual baseline changes.
- Compatibility aliases for retired Make targets or test categories.

## Selected Design

### Test layers

The suite has five layers:

| Layer | Responsibility | External dependency | Routine execution |
| --- | --- | --- | --- |
| `unit` | Pure decisions, normalization, validation, privacy, ranking, and wire contracts | None | Every PR |
| `db` | ORM persistence, PostgreSQL constraints, transactions, leases, and representative concurrency | PostgreSQL | Every PR |
| `product_flow` | Minimal customer and operator critical paths through public module or HTTP interfaces | Django and PostgreSQL | Every PR |
| `operational` | Deployment, Compose, shell, migration rehearsal, recovery, secret handling | Local subprocess and relevant local runtimes | Changed paths or manual exhaustive run |
| `visual` | Browser JavaScript behavior and rendered customer contracts | Node or pinned Playwright container | Changed visual paths |

`unit + db + product_flow` form the required core. A test receives exactly one layer marker. Existing
model-smoke tests that require supplied model artifacts retain their explicit opt-in marker in
addition to their layer.

### Invariant ownership

An invariant has one primary owner:

- a calculation, transition decision, bounded value, or privacy transformation belongs to a pure
  module interface and `unit`;
- a uniqueness rule, durable immutability rule, lock order, lease fence, or transaction effect
  belongs to `db` and uses PostgreSQL;
- request parsing, authorization, status, response shape, and public copy belong to one focused
  HTTP contract;
- the integration of independently owned modules belongs to one `product_flow` scenario;
- cutover ordering, fail-before-mutation, secret cleanup, and rollback belong to `operational`.

Tests at a higher layer assert observable outcomes through that layer's interface. They do not
repeat all intermediate model fields and application transitions already owned below. Tests at a
lower layer do not assert template, HTTP, or deployment wiring.

Pure module extraction is allowed only when existing behavior already contains a decision that is
duplicated, hidden inside an I/O adapter, or used by more than one caller. Persistence stays inside
the Django module; no port is introduced solely to permit a fake database.

### Pareto retention rules

The refactor retains these high-value families:

- pure pricing, DTO, state, privacy, eligibility, ranking, and worker-wire contracts;
- real database constraints, immutable snapshots, transaction ordering, leases, and one
  representative concurrent witness per locking mechanism;
- one primary critical flow for upload, gallery, selfie search, processing, cart, purchase, and
  original delivery;
- fail-before-mutation, secret cleanup, candidate validation, successful cutover, and one rollback
  or recovery witness per critical deployment phase;
- migration identity plus representative forward and reverse rehearsal;
- JavaScript behavior and visual snapshots for changed customer screens.

A test may be deleted only when its invariant is mapped to a retained owner or when review records
that it asserts implementation text, duplicated configuration, obsolete behavior, or a scenario
without a realistic production trigger. Security, privacy, irreversible data loss, realistic
rollback, and current critical-path findings always require a retained owner.

Likely deletion or replacement families are:

- exact Markdown wording, section names, copied commands, and skill prose;
- CSS fragments asserted by Python repository tests;
- one configuration value repeated through repository, workflow, and deployment tests;
- exhaustive fake-command ordering where final state and forbidden side effects are sufficient;
- the same domain invariant repeated at model, application, view, and product-flow layers;
- pure validators hosted by `TestCase` or `TransactionTestCase` fixtures;
- several concurrency scenarios proving the same lock or fence;
- historical regressions fully subsumed by a deeper current interface.

The intended 20–30% test-line reduction is directional evidence, not an acceptance gate.

### Suite selector

One versioned machine-readable selector owns changed-path rules. Given a base and head revision or
an explicit file list, it returns:

- required suites from `core`, `operational`, `migrations`, and `visual`;
- the changed paths that activated each suite; and
- a stable success result when an optional suite is not selected.

The selector is deterministic, has no network dependency, and fails closed to the exhaustive set
for an unclassified production, workflow, build, or infrastructure path. Tests, documentation,
skills, and CI consume the selector rather than copying its path map.

Core is always selected. Operational is selected for deployment scripts, Compose, deployment
workflows, runtime and secret configuration, or operational-test changes. Migrations are selected
for Django model, migration, migration-check, or schema-sensitive deployment changes. Visual is
selected for templates, customer CSS or JavaScript, visual fixtures, screenshots, and visual
runner changes.

### Local and CI interfaces

The maintained local interfaces are:

- `make test` for the required core;
- `make test-operational` for operational contracts;
- `make test-migrations` for migration identity and rehearsal;
- `make test-all` for every default Python layer;
- `make check` for static checks, core coverage, Django checks, and migration drift;
- existing JavaScript and visual commands for their layers.

CI first evaluates the selector and then publishes stable required job names. Core runs for every
pull request. Selected operational, migration, and visual jobs run their complete layer; unselected
jobs finish successfully with an explicit selection reason. A manual workflow or explicit local
target runs the exhaustive set. Main-branch deployment behavior does not change.

Coverage uses branch coverage and includes every maintained production Python package. The 75%
repository threshold remains a regression guard rather than a per-task completeness target.

### Agent verification skill

A model-invoked project skill applies when an agent selects tests, implements or reviews a plan
task, or prepares a handoff. Its entrypoint contains only the decision workflow and evidence rules;
the selector and Make targets remain the executable source of truth.

The skill requires this evidence flow:

1. An implementer runs focused RED/GREEN checks for the changed interface.
2. Before handoff, it runs selector-required suites for which the unchanged package has no valid
   evidence.
3. Its report records exact commands, exit status, result summary, and a deterministic fingerprint
   of the tested working-tree package.
4. A reviewer inspects the diff and evidence and reruns a check only for a named gap, invalidated
   package, or reproducibility concern.
5. A review fix invalidates only suites whose owned paths or behavior changed.
6. The root controller runs `make check` once on the final core package and each selected expensive
   layer once on the final package.
7. Unchanged evidence is reused across role boundaries. CI owns the post-push repetition.

The skill is developed with process TDD. Baseline pressure scenarios must demonstrate redundant
full runs or unsafe omission without the skill. The same scenarios with the skill must select the
minimum complete suite set, reuse unchanged evidence, and retain safety coverage. Skill metadata
is validated structurally; CI does not assert its prose.

## Engineering-Knowledge Decision

Proposed ADR 0033 supersedes ADR 0004. Durable architecture, accepted decisions, active plans, and
project-specific agent workflows remain in Git beside the implementation. Their purpose is
discoverability and review, not conversion of narrative prose into runtime behavior.

CI validates stable machine-readable structure: ADR file/index/status/link consistency, supported
skill metadata schemas, and local-link integrity. It does not validate exact prose, copied shell
commands, section wording, historical counts, or semantic synchronization between duplicated
narratives. Executable operational authority lives in scripts, manifests, settings, and workflows;
documentation links to those sources.

Documentation changes accompany code only when the change alters a durable decision, current
architecture boundary, accepted operator workflow, or evidence-backed job status. Ordinary
implementation details do not require repository-wide narrative reconciliation.

## Failure Semantics and Safety

- An unknown changed path selects every expensive suite rather than silently skipping coverage.
- A selector or CI setup failure fails the required check.
- Missing or mismatched evidence triggers the suite once; it does not authorize reuse.
- A package fingerprint changes when tracked or untracked task files change.
- Coverage configuration failure, omitted maintained package, Django check failure, or migration
  drift fails `make check`.
- Refactoring tests never changes a deployed resource, feature flag, database, migration, customer
  data, or visual baseline.
- A unique invariant discovered during deletion review is retained and assigned an owner even when
  that prevents the directional reduction target.

## Acceptance Criteria

- Every retained Python test belongs to one explicit layer.
- Every maintained production Python package participates in branch coverage; repository coverage
  remains at least 75%.
- The suite selector is the only changed-path map consumed by agents and CI, is deterministic, and
  fails closed for unknown relevant paths.
- Core runs on every pull request. Operational, migration, and visual layers run when selected and
  remain manually exhaustive.
- Stable CI job names succeed explicitly when an optional layer is not selected.
- The invariant inventory records a retained owner or an approved non-product classification for
  every deleted test family.
- Unique security, privacy, data-loss, concurrency, migration, and rollback guarantees remain.
- `make test`, `make test-operational`, `make test-migrations`, `make test-all`, and `make check`
  have documented, tested behavior.
- The project agent skill passes baseline/control and post-skill pressure scenarios and causes no
  redundant complete run for unchanged evidence.
- Before/after reports record test definitions, collected cases, test lines, layer runtime,
  coverage scope and result, and retained heavy suites. A 20–30% test-line reduction and material
  default-path acceleration are reported as directional outcomes, not gates.
- `make check`, every selector-required expensive layer, JavaScript tests, and relevant visual
  verification pass once on the unchanged final package.
- Architecture, engineering jobs, ADR index, and agent guidance describe the delivered workflow
  without exact-text regression tests.
