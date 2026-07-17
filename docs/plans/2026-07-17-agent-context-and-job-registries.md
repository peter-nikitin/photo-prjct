# Agent Context and Job Registries Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents
> available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`)
> syntax for tracking.

**Goal:** Make `AGENTS.md` a stable project map, add separate product and engineering capability
registries, and remove tests that enforce Markdown prose or structure.

**Architecture:** Keep changing state in two append-only job registries and keep implementation
facts in their existing authoritative sources. Preserve executable repository contracts while
removing Markdown reads and assertions from the test suite.

**Tech Stack:** Markdown, pytest, YAML, GitHub Actions.

- Date: 2026-07-17
- Status: Ready for review
- Owner: project maintainer
- Related architecture: [`docs/architecture.md`](../architecture.md)
- Related ADRs: [0011](../adr/0011-use-minimal-shared-https-rollout.md) governs the factual
  activation-validation and cleanup status synchronized here; this change makes no architectural
  decision
- Related design:
  [`docs/superpowers/specs/2026-07-17-agent-context-and-job-registries-design.md`](../superpowers/specs/2026-07-17-agent-context-and-job-registries-design.md)

---

## Scope

### In scope

- Reduce `AGENTS.md` to a concise project description and links to information owners.
- Add product-only JTBD status tracking in `docs/product-jobs.md`.
- Add capability-level engineering JTBD status tracking in `docs/engineering-jobs.md`.
- Remove every repository test read or assertion over Markdown content or existence.
- Preserve tests for executable YAML, runtime configuration, deployment scripts, Compose, Django,
  and visual-test isolation.
- Remove the duplicate targeted pytest invocation from CI.
- Synchronize the factual HTTPS status in `docs/architecture.md` and
  `docs/plans/2026-07-13-canonical-domain-https-edge.md` with the live switch and observed public
  behavior while preserving the remaining ADR 0011 validation and temporary-fallback cleanup.

### Out of scope

- Changing application behavior, architecture decisions, ADRs, deployment topology, or domain
  decisions. Factual status synchronization in existing architecture and plan documents is in
  scope.
- Adding a Markdown linter or replacement documentation-contract tests.
- Tracking implementation tasks in either job registry.
- Listing individual skills in `AGENTS.md`.

## Acceptance criteria

- `AGENTS.md` contains only the project description and paths to authoritative information.
- `docs/product-jobs.md` contains only visitor, customer, photographer, and operator outcomes.
- `docs/engineering-jobs.md` contains only engineering capabilities, not implementation steps.
- Both registries use stable IDs, the shared status vocabulary, evidence links, last-updated dates,
  and an initial append-only history entry for every seeded job.
- No remaining test reads or asserts against a `.md` file or Markdown file existence.
- The remaining repository foundation tests pass.
- CI runs the full pytest suite once rather than repeating `tests/test_repository_foundation.py`.
- `docs/architecture.md` and the canonical HTTPS plan describe shared HTTPS as the normal staging
  edge, retain the HTTP overlay only as a temporary manual recovery fallback pending ADR 0011
  cleanup, and distinguish completed public verification from remaining browser, internal-state,
  and renewal follow-ups.

## Chunk 1: Stable context and job registries

### Task 0: Commit the reviewed implementation plan

**Files:**

- Create: `docs/plans/2026-07-17-agent-context-and-job-registries.md`

- [x] **Step 1: Commit the reviewed plan before implementation**

  Run:

  ```bash
  git add docs/plans/2026-07-17-agent-context-and-job-registries.md
  git commit -m "docs: plan agent context and job registries"
  ```

  Expected: the decision-complete plan is tracked before implementation starts.

### Task 1: Replace the root agent instructions with a source map

**Files:**

- Modify: `AGENTS.md`

- [x] **Step 1: Replace dynamic guidance with stable orientation**

  Keep a short `Project` section describing FindMe Photo as an event photo marketplace. Add a
  `Where to find information` section pointing to the two job registries, architecture, ADRs,
  plans, and the project skills directory. Do not name individual skills or retain workflow,
  deployment, validation, current-stage, or roadmap content.

- [x] **Step 2: Inspect the result**

  Run: `sed -n '1,120p' AGENTS.md`

  Expected: only project orientation and six source paths are present.

### Task 2: Create the product job registry

**Files:**

- Create: `docs/product-jobs.md`
- Reference: `docs/architecture.md`
- Reference: `src/backend/config/views.py`
- Reference: `src/backend/picflow/tests/test_admin.py`
- Reference: `src/backend/picflow/tests/test_views.py`

- [x] **Step 1: Add the shared registry contract**

  Document the JTBD form, stable `PJ-XXX` IDs, allowed product actors, shared statuses, evidence
  requirement, and append-only status-history rule.

- [x] **Step 2: Seed current product outcomes**

  Add exactly these rows, each last updated on 2026-07-17:

  | ID | Actor | Title | JTBD statement | Status | Evidence |
  | --- | --- | --- | --- | --- | --- |
  | `PJ-001` | Operator | Publish an event | When an event is ready for customers, I want to create and publish its catalog record, so I can make it discoverable without developer assistance. | `Validated` | `src/backend/picflow/tests/test_admin.py::EventAdminTests::test_admin_creates_and_publishes_event` |
  | `PJ-002` | Visitor | Discover published events | When I arrive at FindMe Photo, I want to browse only published events in a useful order, so I can choose the event I attended. | `Validated` | `src/backend/picflow/tests/test_views.py::PageTests::test_catalog_only_shows_published_events` and `test_catalog_orders_upcoming_then_past` |
  | `PJ-003` | Visitor | Review event details | When I find an event, I want to open its public details, so I can confirm it is the event I attended. | `Validated` | `src/backend/picflow/tests/test_views.py::PageTests::test_event_detail_renders_published_event` and `test_event_detail_returns_404_for_draft_event` |
  | `PJ-004` | Photographer | Upload an event batch | When I finish photographing an event, I want to upload a batch with event and capture context, so I can submit it for processing. | `Candidate` | `docs/architecture.md#target-mvp-architecture--proposed` (`Ingestion`) |
  | `PJ-005` | Visitor | Browse an event gallery | When an event has published photos, I want to browse its gallery, so I can inspect photos from that event. | `Candidate` | `docs/architecture.md#evolution-stages` (`Photo-bank core`) |
  | `PJ-006` | Operator | Review processing results | When automated processing fails or produces uncertain metadata, I want to inspect and correct the result, so I can keep published search data reliable. | `Candidate` | `docs/architecture.md#target-mvp-architecture--proposed` (`Moderation`) |
  | `PJ-007` | Customer | Find photos by bib | When I know a participant bib number, I want to search within one event, so I can find likely photos quickly. | `Candidate` | `docs/architecture.md#search` |
  | `PJ-008` | Customer | Find photos by face | When I have an appropriate reference image and the required consent applies, I want to search within one event, so I can review probable matches. | `Candidate` | `docs/architecture.md#search` and `#security-privacy-and-legal-boundaries` |
  | `PJ-009` | Visitor | Receive a free-event original | When an event offers free photos, I want a controlled anonymous download, so I can receive the original without exposing its permanent storage key. | `Candidate` | `docs/architecture.md#security-privacy-and-legal-boundaries` |
  | `PJ-010` | Customer | Purchase selected photos | When I select paid photos, I want to complete an order and payment, so I can obtain download entitlement. | `Candidate` | `docs/architecture.md#purchase-and-download` |
  | `PJ-011` | Customer | Download purchased photos | When my order is paid, I want to download only its entitled photos, so I can receive the files I purchased securely. | `Candidate` | `docs/architecture.md#purchase-and-download` |

  Do not infer delivery from test-only design reference screens.

- [x] **Step 3: Seed history**

  Add one `Not recorded` to initial-status entry dated 2026-07-17 for every `PJ-XXX` row. Link each
  history reason to the same evidence used by the registry.

### Task 3: Create the engineering job registry

**Files:**

- Create: `docs/engineering-jobs.md`
- Reference: `docs/architecture.md`
- Reference: `.github/workflows/ci.yml`
- Reference: `.github/workflows/deploy.yml`
- Reference: `.github/workflows/promote-production.yml`
- Reference: `docker-compose.yml`
- Reference: `docker-compose.prod.yml`
- Reference: `docker-compose.staging.yml`
- Reference: `docker-compose.https.yml`
- Reference: `docker-compose.visual.yml`
- Reference: `Dockerfile`
- Reference: `Dockerfile.visual-tests`
- Reference: `package.json`
- Reference: `deploy/apply-deployment.sh`
- Reference: `tests/test_repository_foundation.py`

- [x] **Step 1: Add the shared registry contract**

  Document the JTBD form, stable `EJ-XXX` IDs, engineering actors, shared statuses, evidence
  requirement, capability-level boundary, and append-only status-history rule.

- [x] **Step 2: Seed current engineering capabilities**

  Add exactly these rows, each last updated on 2026-07-17:

  | ID | Actor | Title | JTBD statement | Status | Evidence |
  | --- | --- | --- | --- | --- | --- |
  | `EJ-001` | Developer | Reproduce local PostgreSQL development | When I start repository work, I want Django and PostgreSQL to run from the documented environment contract, so I can reproduce production-relevant behavior locally. | `Validated` | `docker-compose.yml`, `.env.example`, and `src/backend/config/settings.py` |
  | `EJ-002` | Contributor | Receive complete CI feedback | When I push a change or open a pull request, I want formatting, lint, types, PostgreSQL tests, migrations, Django checks, and visual regression to run automatically, so I can detect regressions before merge. | `Validated` | `.github/workflows/ci.yml`, `pyproject.toml`, and `package.json` |
  | `EJ-003` | Maintainer | Deploy an immutable image to staging | When `main` advances, I want one SHA-tagged image built and applied to staging, so I can test the exact artifact that may later be promoted. | `Validated` | `.github/workflows/deploy.yml`, `Dockerfile`, `docker-compose.prod.yml`, and `deploy/apply-deployment.sh` |
  | `EJ-004` | Operator | Run the current staging HTTPS edge | When staging is deployed after HTTPS activation, I want the shared HTTPS edge to terminate trusted traffic and proxy the application, so I can operate the current environment without presenting it as production. | `Validated` | `.github/workflows/deploy.yml`, `docker-compose.https.yml`, `deploy/apply-deployment.sh`, and [successful GitHub Actions staging deploy run 29556330740](https://github.com/peter-nikitin/photo-prjct/actions/runs/29556330740) |
  | `EJ-005` | Contributor | Reproduce visual regression | When UI rendering changes, I want Playwright to run in the same pinned container environment locally and in CI, so I can review deterministic snapshots. | `Validated` | `package.json`, `Dockerfile.visual-tests`, `docker-compose.visual.yml`, and `tests/test_repository_foundation.py::test_visual_regression_runs_in_a_pinned_container_environment` |
  | `EJ-006` | Maintainer | Promote the staging-verified image | When a staging image is selected for promotion, I want the production-environment workflow to verify and reuse that exact image, so I can avoid rebuilding a different artifact. | `Validated` | `.github/workflows/promote-production.yml` and `tests/test_repository_foundation.py::test_deployment_workflows_separate_staging_and_production` |
  | `EJ-007` | Operator | Provision a production environment | When readiness evidence and pricing are approved, I want a separate non-preemptible production environment, so I can serve customers without staging lifecycle constraints. | `Candidate` | `docs/architecture.md#accepted-constraints` and `docs/superpowers/specs/2026-07-11-staging-production-deployment-design.md#phase-3-provision-production` |
  | `EJ-008` | Operator | Activate trusted HTTPS | When the canonical domain prerequisites are confirmed, I want the prepared shared HTTPS edge activated and observed, so I can serve trusted canonical traffic and renew certificates safely. | `Delivered` | `docs/plans/2026-07-13-canonical-domain-https-edge.md#chunk-2-https-activation-release`, `.github/workflows/deploy.yml`, and [successful GitHub Actions staging deploy run 29556330740](https://github.com/peter-nikitin/photo-prjct/actions/runs/29556330740) |
  | `EJ-009` | Operator | Detect service degradation | When a product or processing component becomes unhealthy, I want monitoring and actionable alerts, so I can respond before failures persist unnoticed. | `Candidate` | `docs/architecture.md#open-decisions` (`Observability stack`) |
  | `EJ-010` | Operator | Restore service data | When transactional data or media metadata is lost or corrupted, I want a tested backup and restore procedure with agreed recovery targets, so I can recover service safely. | `Candidate` | `docs/architecture.md#security-privacy-and-legal-boundaries` and `#open-decisions` |

- [x] **Step 3: Seed history**

  Add one `Not recorded` to initial-status entry dated 2026-07-17 for every `EJ-XXX` row. Keep
  commands and implementation sequences out of the registry and link to plans where available.

- [x] **Step 4: Commit the context and registries**

  Run:

  ```bash
  git add AGENTS.md docs/product-jobs.md docs/engineering-jobs.md
  git commit -m "docs: separate agent context from job status"
  ```

  Expected: one documentation commit with no application or architecture changes.

### Task 3A: Synchronize authoritative HTTPS status

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/plans/2026-07-13-canonical-domain-https-edge.md`

- [x] **Step 1: Record live HTTPS and pending validation without changing decisions or runtime**

  Replace the stale current-HTTP descriptions with the implemented shared HTTPS staging topology.
  Record merged PR #32, successful GitHub Actions staging deploy run
  [29556330740](https://github.com/peter-nikitin/photo-prjct/actions/runs/29556330740), and the
  observed canonical redirects and trusted apex response. Keep browser observation, remote
  marker/container inspection, and the Certbot renewal dry-run explicitly incomplete. Retain
  `docker-compose.staging.yml` and `deploy/nginx/staging.conf` as temporary manual recovery assets
  excluded from normal deployment until validation permits the cleanup required by ADR 0011.

  Expected: authoritative sources describe the deployed state and remaining evidence accurately;
  no architecture decision, runtime configuration, or deployment behavior changes.

## Chunk 2: Remove Markdown contracts while preserving executable tests

### Task 4: Delete Markdown reads and assertions from repository tests

**Files:**

- Modify: `tests/test_repository_foundation.py`

- [x] **Step 1: Remove Markdown-only tests**

  Delete these tests completely:

  - `test_documentation_foundation_exists`;
  - `test_adr_index_lists_all_accepted_decisions`;
  - `test_skills_reference_repository_templates`;
  - `test_yandex_cloud_skill_requires_pricing_confirmation`;
  - `test_operational_fast_lane_is_project_context`;
  - `test_operational_change_skill_prevents_platform_scope_drift`;
  - `test_write_plan_has_an_operational_fast_lane`;
  - `test_visual_design_skill_has_required_files`.

- [x] **Step 2: Split the mixed skill metadata test**

  Rename `test_project_skills_have_valid_metadata_and_ui_configuration` to
  `test_project_skill_ui_configuration_is_valid`. Remove `SKILL.md` reads and frontmatter
  assertions. Retain parsing and validation of each `agents/openai.yaml`, including the expected
  interface keys.

- [x] **Step 3: Prove that Markdown contracts are gone**

  Run:

  ```bash
  rg -n 'AGENTS\.md|SKILL\.md|\.md\b' tests --glob '*.py'
  ```

  Expected: no matches.

- [x] **Step 4: Run the focused tests**

  Run:

  ```bash
  set -a
  source .env.example
  set +a
  ../../.venv/bin/pytest tests/test_repository_foundation.py -q
  ```

  Expected: 14 tests pass.

### Task 5: Remove the redundant CI invocation

**Files:**

- Modify: `.github/workflows/ci.yml`

- [x] **Step 1: Remove the duplicate step**

  Delete `Validate project skills`, which reruns
  `pytest tests/test_repository_foundation.py -q` after the full `pytest --cov` step has already
  collected the same tests.

- [x] **Step 2: Confirm one pytest invocation remains**

  Run: `rg -n 'pytest' .github/workflows/ci.yml`

  Expected: only `pytest --cov --cov-report=term-missing` remains.

- [x] **Step 3: Commit the test and CI cleanup**

  Run:

  ```bash
  git add tests/test_repository_foundation.py .github/workflows/ci.yml
  git commit -m "test: remove markdown repository contracts"
  ```

  Expected: one commit that removes prose enforcement while retaining executable contracts.

## Chunk 3: Final verification

### Task 6: Verify the completed change

**Files:**

- Verify: `AGENTS.md`
- Verify: `docs/product-jobs.md`
- Verify: `docs/engineering-jobs.md`
- Verify: `docs/architecture.md`
- Verify: `docs/plans/2026-07-13-canonical-domain-https-edge.md`
- Verify: `tests/test_repository_foundation.py`
- Verify: `.github/workflows/ci.yml`

- [x] **Step 1: Check whitespace and patch integrity**

  Run: `git diff --check origin/main...HEAD`

  Expected: exit code 0 with no output.

- [x] **Step 2: Run the remaining repository contracts**

  Run:

  ```bash
  set -a
  source .env.example
  set +a
  ../../.venv/bin/pytest tests/test_repository_foundation.py -q
  ```

  Expected: 14 tests pass.

- [x] **Step 3: Confirm documentation and test boundaries**

  Run:

  ```bash
  rg -n 'AGENTS\.md|SKILL\.md|\.md\b' tests --glob '*.py'
  git diff --name-only origin/main...HEAD
  ```

  Expected: the first command has no matches. The changed-file list contains only the approved
  specification, implementation plan, `AGENTS.md`, two job registries, authoritative architecture
  and canonical HTTPS plan status updates, repository test file, and CI workflow.

- [x] **Step 4: Review job status evidence**

  Check every `Validated` row against an executable test or implemented configuration, every
  `Delivered` row against implemented behavior, and every unimplemented target against architecture
  status. Downgrade any status that lacks evidence.

- [x] **Step 5: Confirm the worktree is clean after commits**

  Run: `git status --short --branch`

  Expected: branch `agent-instruction-sources` has no uncommitted changes.

### Task 7: Publish the review branch

**Files:** none.

- [ ] **Step 1: Push the dedicated branch**

  Run: `git push -u origin agent-instruction-sources`

  Expected: the remote branch is created or updated without touching `main`.

- [ ] **Step 2: Open a draft pull request**

  Run:

  ```bash
  env -u GITHUB_TOKEN gh pr create \
    --draft \
    --base main \
    --head agent-instruction-sources \
    --title "docs: separate agent context from job status" \
    --body-file - <<'EOF'
  ## Outcome

  - reduce `AGENTS.md` to stable project orientation and source routing;
  - add separate product and engineering JTBD registries with current status and append-only history;
  - synchronize authoritative documentation with live staging HTTPS, public verification, and the
    remaining ADR 0011 validation and cleanup;
  - remove repository tests that enforce Markdown content or existence.

  ## Preserved checks

  Executable YAML, Django, deployment, Compose, npm, and visual-container contracts remain covered.
  CI continues to run the full pytest suite once.

  ## Verification

  - `git diff --check origin/main...HEAD`
  - `pytest tests/test_repository_foundation.py -q` — 14 passed
  - no Markdown content/existence references remain in Python tests
  EOF
  ```

  Expected: GitHub returns the draft PR URL.

- [ ] **Step 3: Verify the pull request**

  Run: `env -u GITHUB_TOKEN gh pr view agent-instruction-sources --json url,state,isDraft`

  Expected: state `OPEN`, `isDraft: true`, and a reviewable URL. Do not merge.

## Operational impact and rollout

None. The change does not modify runtime configuration, database state, deployment behavior,
architecture decisions, or application code. It records a live HTTPS switch and public verification
that merged separately while this branch was in progress, without claiming the remaining activation
validation or ADR 0011 cleanup is complete. CI becomes slightly faster by removing one duplicate
pytest invocation.

## Rollback

Revert the implementation commits in reverse order: first the factual source-synchronization commit,
then `test: remove markdown repository contracts`, then `docs: separate agent context from job
status`. Keep the reviewed design and plan history. Reverting the registries or factual status
updates loses only documentation history; it does not alter runtime or persistent application data.

## Open questions

None.
