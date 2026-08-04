# Engineering Jobs

This registry tracks engineering and operational capabilities for FindMe Photo, rather than
individual technical tasks. Engineering jobs use these actors: Developer, Contributor, Maintainer,
and Operator.

## Job format

Each job has a stable `EJ-NNN` identifier and uses this Jobs-to-be-Done form:

> When &lt;situation&gt;, I want to &lt;motivation&gt;, so I can &lt;expected outcome&gt;.

Every job records its current status, supporting evidence, and last-updated date. Status must not
advance from a proposal alone; an advance requires evidence appropriate to the new status.

When a job's status changes, update its current-state row and detail together, append exactly one new
history row with PR or commit evidence where available, and never edit earlier history rows.

## Statuses

| Status | Definition |
| --- | --- |
| Candidate | The job is recognized as potentially valuable, but has not been committed to a delivery plan. |
| Planned | The job is committed to a decision-complete delivery plan, but implementation has not started. |
| In progress | Implementation of the planned job has started but is not yet delivered. |
| Delivered | The capability is implemented and available in the relevant workflow or environment. |
| Validated | Automated evidence or observed operation demonstrates the expected outcome. |
| Deferred | Work on the job is intentionally postponed, with the reason recorded. |

## Current state

| Job | Actor | Summary | Status | Last updated |
| --- | --- | --- | --- | --- |
| EJ-001 | Developer | Reproduce local PostgreSQL development | Validated | 2026-07-17 |
| EJ-002 | Contributor | Receive complete CI feedback | Validated | 2026-07-17 |
| EJ-003 | Maintainer | Deploy an immutable image to staging | Validated | 2026-07-17 |
| EJ-004 | Operator | Run the current staging HTTPS edge | Validated | 2026-07-17 |
| EJ-005 | Contributor | Reproduce visual regression | Validated | 2026-07-17 |
| EJ-006 | Maintainer | Promote the staging-verified image | Validated | 2026-07-17 |
| EJ-007 | Operator | Provision a production environment | Candidate | 2026-07-17 |
| EJ-008 | Operator | Activate trusted HTTPS | Delivered | 2026-07-17 |
| EJ-009 | Operator | Detect service degradation | Candidate | 2026-07-17 |
| EJ-010 | Operator | Restore service data | Candidate | 2026-07-25 |
| EJ-011 | Maintainer | Gate private gallery media activation | Validated | 2026-07-19 |
| EJ-012 | Maintainer | Gate temporary selfie storage activation | Validated | 2026-07-31 |
| EJ-013 | Contributor | Start isolated repository work reliably | Validated | 2026-08-04 |
| EJ-014 | Operator | Inspect bounded selfie-search operational evidence | Delivered | 2026-08-04 |

## Job details

### EJ-001 — Developer — Reproduce local PostgreSQL development

When I start repository work, I want Django and PostgreSQL to run from the documented environment
contract, so I can reproduce production-relevant behavior locally.

- Status: Validated
- Evidence: [`docker-compose.yml`](../docker-compose.yml), [`.env.example`](../.env.example), and [`src/backend/config/settings.py`](../src/backend/config/settings.py)
- Last updated: 2026-07-17

### EJ-002 — Contributor — Receive complete CI feedback

When I update a pull request or `main` advances, I want formatting, lint, types, PostgreSQL tests,
migrations, Django checks, and visual regression to run automatically, so I can detect regressions
before merge and validate the integrated branch.

Pull requests run through the `pull_request` trigger, while branch-push validation is limited to
`main`. Updating a feature branch therefore does not create a duplicate push run alongside its pull
request run.

- Status: Validated
- Evidence: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), [`pyproject.toml`](../pyproject.toml), and [`package.json`](../package.json)
- Last updated: 2026-07-17

### EJ-003 — Maintainer — Deploy an immutable image to staging

When main advances, I want one SHA-tagged image built and applied to staging, so I can test the exact
artifact that may later be promoted.

- Status: Validated
- Evidence: [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), [`Dockerfile`](../Dockerfile), [`docker-compose.prod.yml`](../docker-compose.prod.yml), and [`deploy/apply-deployment.sh`](../deploy/apply-deployment.sh)
- Last updated: 2026-07-17

### EJ-004 — Operator — Run the current staging HTTPS edge

When staging is deployed after HTTPS activation, I want the shared HTTPS edge to terminate trusted
traffic and proxy the application, so I can operate the current environment without presenting it as
production.

- Status: Validated
- Evidence: [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), [`docker-compose.https.yml`](../docker-compose.https.yml), [`deploy/apply-deployment.sh`](../deploy/apply-deployment.sh), and [successful GitHub Actions staging deploy run 29556330740](https://github.com/peter-nikitin/photo-prjct/actions/runs/29556330740)
- Last updated: 2026-07-17

### EJ-005 — Contributor — Reproduce visual regression

When UI rendering changes, I want Playwright to run in the same pinned container environment locally
and in CI, so I can review deterministic snapshots.

Local runs tag the dependency-only visual-test image from its Dockerfile and lock files, then mount
the current source at runtime. Source-only changes therefore reuse the installed Chromium instead of
rebuilding it.

CI computes the same dependency key and pulls the corresponding read-only GHCR image before falling
back to a local build. A separate main-only workflow publishes a new keyed image when the visual
Dockerfile or dependency lock files change; pull requests never receive package write permission.

- Status: Validated
- Evidence: [`package.json`](../package.json), [`Dockerfile.visual-tests`](../Dockerfile.visual-tests), [`docker-compose.visual.yml`](../docker-compose.visual.yml), [`.github/workflows/visual-test-image.yml`](../.github/workflows/visual-test-image.yml), [`tests/visual/run-in-container.sh`](../tests/visual/run-in-container.sh), [`tests/test_visual_test_runner.py`](../tests/test_visual_test_runner.py), and [`tests/test_repository_foundation.py::test_visual_regression_runs_in_a_pinned_container_environment`](../tests/test_repository_foundation.py)
- Last updated: 2026-07-19

### EJ-006 — Maintainer — Promote the staging-verified image

When a staging image is selected for promotion, I want the production-environment workflow to verify
and reuse that exact image, so I can avoid rebuilding a different artifact.

- Status: Validated
- Evidence: [`.github/workflows/promote-production.yml`](../.github/workflows/promote-production.yml) and [`tests/test_repository_foundation.py::test_deployment_workflows_separate_staging_and_production`](../tests/test_repository_foundation.py)
- Last updated: 2026-07-17

### EJ-007 — Operator — Provision a production environment

When readiness evidence and pricing are approved, I want a separate non-preemptible production
environment, so I can serve customers without staging lifecycle constraints.

- Status: Candidate
- Evidence: [Architecture accepted constraints](architecture.md#accepted-constraints) and [staging-production deployment design — Phase 3](superpowers/specs/2026-07-11-staging-production-deployment-design.md#phase-3-provision-production)
- Last updated: 2026-07-17

### EJ-008 — Operator — Activate trusted HTTPS

When the canonical domain prerequisites are confirmed, I want the prepared shared HTTPS edge
activated and observed, so I can serve trusted canonical traffic and renew certificates safely.

- Status: Delivered
- Evidence: [Canonical domain HTTPS edge plan — Chunk 2](plans/2026-07-13-canonical-domain-https-edge.md#chunk-2-https-activation-release), [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), and [successful GitHub Actions staging deploy run 29556330740](https://github.com/peter-nikitin/photo-prjct/actions/runs/29556330740)
- Last updated: 2026-07-17

### EJ-009 — Operator — Detect service degradation

When a product or processing component becomes unhealthy, I want monitoring and actionable alerts,
so I can respond before failures persist unnoticed.

- Status: Candidate
- Evidence: [Architecture open decisions — Observability stack](architecture.md#open-decisions)
- Last updated: 2026-07-17

### EJ-010 — Operator — Restore service data

When transactional data or media metadata is lost or corrupted, I want a tested backup and restore
procedure with agreed recovery targets, so I can recover service safely.

- Status: Candidate
- Evidence: [`scripts/clone-staging-db.sh`](../scripts/clone-staging-db.sh) and [`tests/deployment/test_clone_staging_database.py`](../tests/deployment/test_clone_staging_database.py) provide partial local restore evidence: a developer can create a validated staging logical dump, replace only the current checkout's local Compose database through a serialized local-Docker-only workflow, quiesce the normal web service, retain diagnostic and safety dumps, and validate migration readiness without running the mutating web entrypoint. Separate isolated PostgreSQL 16 integrations verify marker/owner/ACL normalization and the actual project image's `django_migrations`, `showmigrations`, and `makemigrations` readiness against a restored migrated schema without staging network contact. This remains insufficient for service-data recovery: scheduled backups, retention, RPO/RTO, media recovery, and a staging disaster-recovery drill are not established. See also [Architecture Security, privacy, and legal boundaries](architecture.md#security-privacy-and-legal-boundaries) and [Open decisions](architecture.md#open-decisions).
- Last updated: 2026-07-25

### EJ-014 — Operator — Inspect bounded selfie-search operational evidence

When I operate selfie search, I want privacy-bounded events and a reproducible daily summary, so I
can diagnose its funnel without turning logs into product state or a backup.

- Status: Delivered
- Evidence: repository tests cover strict event contracts, redacted edge logs, deterministic daily
  aggregation, idempotent managed-file installation, exact rollback, effective journal caps, timer
  state, Compose tags, and bounded probe readability. No staging deployment or replacement-
  persistence evidence is claimed yet.
- Last updated: 2026-08-04

### EJ-011 — Maintainer — Gate private gallery media activation

When I deploy a gallery-capable image, I want its candidate code and requested private-media
settings checked before environment promotion or a service switch, so I can avoid activating media
delivery that cannot read an eligible original.

The deployment entrypoint always pulls the candidate web image. With no successful
`deployed-image` marker, automated tests prove that a truly fresh deployment emits the sanitized
`no-existing-deployment` skip, constructs no ORM preflight container, and completes the normal
first-deployment flow; that skip is not `GetObject` validation. Once the marker exists, the
entrypoint uses a mode-0600 temporary environment file for the fail-closed candidate-image one-off.
Tests cover candidate pull failure; the no-eligible-row skip without storage construction; the
successful storage construction, final-object open, one-byte read, and body close; sanitized
database-query, storage-construction, and object-open failures; ordering before environment
promotion and service switching; preservation of canonical environment, deployment markers, and
services on those pre-promotion failures; and removal of the secret-bearing temporary file when
environment promotion itself fails. They do not exercise empty-read, read-exception, or
close-exception failure paths.
This is repository automation evidence only: no live staging or production activation, IAM
permission, bucket policy, or private object was validated or changed.

- Status: Validated
- Evidence: [`deploy/apply-deployment.sh`](../deploy/apply-deployment.sh), [`tests/deployment/test_deployment_scripts.py::test_fresh_first_deployment_skips_orm_gate_and_completes_normal_flow`](../tests/deployment/test_deployment_scripts.py), [`tests/deployment/test_deployment_scripts.py::test_candidate_pull_failure_leaves_canonical_env_without_service_reconciliation`](../tests/deployment/test_deployment_scripts.py), [`tests/deployment/test_deployment_scripts.py::test_candidate_private_media_preflight_skips_when_no_eligible_photo`](../tests/deployment/test_deployment_scripts.py), [`tests/deployment/test_deployment_scripts.py::test_candidate_private_media_preflight_reads_when_photo_exists`](../tests/deployment/test_deployment_scripts.py), [`tests/deployment/test_deployment_scripts.py::test_candidate_private_media_preflight_runs_before_service_switch`](../tests/deployment/test_deployment_scripts.py), [`tests/deployment/test_deployment_scripts.py::test_failed_candidate_private_media_preflight_leaves_canonical_env_untouched`](../tests/deployment/test_deployment_scripts.py), and [`tests/deployment/test_deployment_scripts.py::test_failed_env_promotion_removes_secret_bearing_requested_temp`](../tests/deployment/test_deployment_scripts.py)
- Last updated: 2026-07-19

### EJ-012 — Maintainer — Gate temporary selfie storage activation

When I prepare an environment for public selfie search, I want lifecycle mutation and scratch-object
access to require explicit, fail-closed checks, so I can avoid enabling uploads without bounded
cleanup and exact-object access.

Repository automation requires an explicit mutation flag, an approved bucket-name digest, an
unversioned bucket, collision-free preservation of existing lifecycle rules, exact readback, and
automatic restoration after a mismatch. A separate explicit real-storage preflight checks the
bounded `selfie-search/` lifecycle and performs one generated put/head/grant/delete cycle with
sanitized markers and cleanup on failure. Automated tests validate these contracts. On 2026-07-31,
staging applied the one-day `selfie-search/` lifecycle rule while preserving the existing preview
rule; the real-bucket preflight passed its put/head/grant/delete markers, and the feature was then
enabled. This is staging-only evidence; production remains unactivated.

- Status: Validated
- Evidence: [`src/backend/selfie_search/management/commands/configure_selfie_search_lifecycle.py`](../src/backend/selfie_search/management/commands/configure_selfie_search_lifecycle.py), [`src/backend/selfie_search/management/commands/verify_selfie_search_storage.py`](../src/backend/selfie_search/management/commands/verify_selfie_search_storage.py), [`src/backend/selfie_search/tests/test_configure_lifecycle_command.py`](../src/backend/selfie_search/tests/test_configure_lifecycle_command.py), and [`src/backend/selfie_search/tests/test_storage_contract_command.py`](../src/backend/selfie_search/tests/test_storage_contract_command.py)
- Last updated: 2026-07-31

### EJ-013 — Contributor — Start isolated repository work reliably

When I start work in an isolated worktree, I want the repository's Python environment, safe local
Django settings, formatting hooks, and verification commands prepared automatically, so I can run
the first test and create a consistently formatted commit without reconstructing setup knowledge.

`make worktree NAME=<name> [BASE=<ref>]` creates the isolated branch and checkout, links the shared
ignored `.venv`, creates a worktree-local `.env` without copying root secrets, installs the shared
Ruff pre-commit hook, and verifies Python, pytest, and Django settings. `make hooks` repairs the hook
for an existing checkout, while `make test` and `make check` provide stable commands with CI-like
Django variables. Behavioral tests cover validation before Git mutation, secret isolation, clean
Git state, hook installation, environment defaults, and Make command forwarding. A real disposable
worktree smoke and an actual hook-driven commit additionally validated the integrated workflow.

- Status: Validated
- Evidence: [`scripts/create-worktree.py`](../scripts/create-worktree.py),
  [`scripts/run-in-test-env.sh`](../scripts/run-in-test-env.sh), [`Makefile`](../Makefile),
  [`tests/test_create_worktree.py`](../tests/test_create_worktree.py),
  [`tests/test_worktree_commands.py`](../tests/test_worktree_commands.py), and
  [PR #94 successful Quality checks](https://github.com/peter-nikitin/photo-prjct/actions/runs/30857287973/job/91831124522)
- Last updated: 2026-08-04

## Status log

This log is append-only.

| Date | Job | Previous status | New status | Evidence or reason |
| --- | --- | --- | --- | --- |
| 2026-07-17 | EJ-001 | Not recorded | Validated | [`docker-compose.yml`](../docker-compose.yml), [`.env.example`](../.env.example), and [`src/backend/config/settings.py`](../src/backend/config/settings.py) |
| 2026-07-17 | EJ-002 | Not recorded | Validated | [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), [`pyproject.toml`](../pyproject.toml), and [`package.json`](../package.json) |
| 2026-07-17 | EJ-003 | Not recorded | Validated | [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), [`Dockerfile`](../Dockerfile), [`docker-compose.prod.yml`](../docker-compose.prod.yml), and [`deploy/apply-deployment.sh`](../deploy/apply-deployment.sh) |
| 2026-07-17 | EJ-004 | Not recorded | Validated | [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), [`docker-compose.https.yml`](../docker-compose.https.yml), [`deploy/apply-deployment.sh`](../deploy/apply-deployment.sh), and [successful GitHub Actions staging deploy run 29556330740](https://github.com/peter-nikitin/photo-prjct/actions/runs/29556330740) |
| 2026-07-17 | EJ-005 | Not recorded | Validated | [`package.json`](../package.json), [`Dockerfile.visual-tests`](../Dockerfile.visual-tests), [`docker-compose.visual.yml`](../docker-compose.visual.yml), and [`tests/test_repository_foundation.py::test_visual_regression_runs_in_a_pinned_container_environment`](../tests/test_repository_foundation.py) |
| 2026-07-17 | EJ-006 | Not recorded | Validated | [`.github/workflows/promote-production.yml`](../.github/workflows/promote-production.yml) and [`tests/test_repository_foundation.py::test_deployment_workflows_separate_staging_and_production`](../tests/test_repository_foundation.py) |
| 2026-07-17 | EJ-007 | Not recorded | Candidate | [Architecture accepted constraints](architecture.md#accepted-constraints) and [staging-production deployment design — Phase 3](superpowers/specs/2026-07-11-staging-production-deployment-design.md#phase-3-provision-production) |
| 2026-07-17 | EJ-008 | Not recorded | Delivered | [Canonical domain HTTPS edge plan — Chunk 2](plans/2026-07-13-canonical-domain-https-edge.md#chunk-2-https-activation-release), [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), and [successful GitHub Actions staging deploy run 29556330740](https://github.com/peter-nikitin/photo-prjct/actions/runs/29556330740) |
| 2026-07-17 | EJ-009 | Not recorded | Candidate | [Architecture open decisions — Observability stack](architecture.md#open-decisions) |
| 2026-07-17 | EJ-010 | Not recorded | Candidate | [Architecture Security, privacy, and legal boundaries](architecture.md#security-privacy-and-legal-boundaries) and [Open decisions](architecture.md#open-decisions) |
| 2026-07-19 | EJ-005 | Validated | Validated | Local visual runs now reuse a dependency-keyed image; [`tests/test_visual_test_runner.py`](../tests/test_visual_test_runner.py) verifies build-once behavior. |
| 2026-07-19 | EJ-002 | Validated | Validated | Pull requests retain the complete suite while branch-push CI is limited to `main`; [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) and [`tests/test_repository_foundation.py`](../tests/test_repository_foundation.py) enforce the trigger contract. |
| 2026-07-19 | EJ-005 | Validated | Validated | CI reuses a dependency-keyed GHCR image with build fallback, and [`.github/workflows/visual-test-image.yml`](../.github/workflows/visual-test-image.yml) publishes changed dependency images only from `main`. |
| 2026-07-19 | EJ-011 | Not recorded | Validated | Behavioral deployment tests verify candidate private-media preflight, ordering, temporary-environment cleanup, and the absence of an IAM mutation path; no live environment activation is claimed. |
| 2026-07-19 | EJ-011 | Validated | Validated | Clarified boundary: automated tests cover candidate pull failure, no-row skip, successful one-byte read/close, sanitized storage construction/open failures with pre-promotion state preserved, and promotion-fault temporary-file cleanup; empty-read/read-exception/close-exception paths and live activation are not claimed. |
| 2026-07-19 | EJ-011 | Validated | Validated | Fresh unprovisioned deployments now skip the unavailable ORM gate based on absence of the successful `deployed-image` marker; established deployments retain the fail-closed database/no-row/storage gate, and neither skip is live `GetObject` evidence. |
| 2026-07-25 | EJ-010 | Candidate | Candidate | Local clone automation now enforces a serialized local-Docker-only replacement, quiesces the normal web service, retains safety/diagnostic artifacts, and has isolated PostgreSQL 16 plus real-Django restored-schema evidence for migration readiness without startup mutations. This is partial local evidence only; scheduled backups, retention, RPO/RTO, media recovery, and a staging disaster-recovery drill remain unestablished. |
| 2026-07-31 | EJ-012 | Not recorded | Validated | Automated command tests verify explicit confirmation, exact lifecycle preservation/readback/recovery, unversioned-bucket enforcement, bounded-prefix preflight, generated scratch-object cleanup, and sanitized output. Staging additionally applied the one-day `selfie-search/` rule without changing the preview rule, then passed real-bucket put/head/grant/delete preflight before feature enablement. Production is not activated. |
| 2026-08-04 | EJ-013 | Not recorded | Validated | PR #94 adds and tests the supported worktree bootstrap, CI-like test wrapper, shared Ruff pre-commit installation, clean Git state, secret isolation, and stable `make test`/`make check` entry points; a disposable real-worktree smoke, hook-driven commit, and successful Quality checks validate the integrated workflow. |
| 2026-08-04 | EJ-014 | Not recorded | Delivered | Repository verification covers strict bounded events, edge redaction, journald reconciliation and exact rollback, timer/driver/tag checks, probe readability, and deterministic recomputation. Staging activation is not claimed. |
