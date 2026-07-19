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
| EJ-010 | Operator | Restore service data | Candidate | 2026-07-17 |
| EJ-011 | Maintainer | Gate private gallery media activation | Validated | 2026-07-19 |

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
- Evidence: [Architecture Security, privacy, and legal boundaries](architecture.md#security-privacy-and-legal-boundaries) and [Open decisions](architecture.md#open-decisions)
- Last updated: 2026-07-17

### EJ-011 — Maintainer — Gate private gallery media activation

When I deploy a gallery-capable image, I want its candidate code and requested private-media
settings checked before environment promotion or a service switch, so I can avoid activating media
delivery that cannot read an eligible original.

The deployment entrypoint uses a mode-0600 temporary environment file for a candidate-image
one-off. Automated tests cover candidate pull failure; the no-eligible-row skip without storage
construction; the successful storage construction, final-object open, one-byte read, and body
close; sanitized storage-construction and object-open failures; ordering before environment
promotion and service switching; preservation of canonical environment, deployment markers, and
services on those pre-promotion failures; and removal of the secret-bearing temporary file when
environment promotion itself fails. They do not exercise empty-read, read-exception, or
close-exception failure paths. This is repository automation evidence only: no live staging or
production activation, IAM permission, bucket policy, or private object was validated or changed.

- Status: Validated
- Evidence: [`deploy/apply-deployment.sh`](../deploy/apply-deployment.sh), [`tests/deployment/test_deployment_scripts.py::test_candidate_pull_failure_leaves_canonical_env_without_service_reconciliation`](../tests/deployment/test_deployment_scripts.py), [`tests/deployment/test_deployment_scripts.py::test_candidate_private_media_preflight_skips_when_no_eligible_photo`](../tests/deployment/test_deployment_scripts.py), [`tests/deployment/test_deployment_scripts.py::test_candidate_private_media_preflight_reads_when_photo_exists`](../tests/deployment/test_deployment_scripts.py), [`tests/deployment/test_deployment_scripts.py::test_candidate_private_media_preflight_runs_before_service_switch`](../tests/deployment/test_deployment_scripts.py), [`tests/deployment/test_deployment_scripts.py::test_failed_candidate_private_media_preflight_leaves_canonical_env_untouched`](../tests/deployment/test_deployment_scripts.py), and [`tests/deployment/test_deployment_scripts.py::test_failed_env_promotion_removes_secret_bearing_requested_temp`](../tests/deployment/test_deployment_scripts.py)
- Last updated: 2026-07-19

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
| 2026-07-19 | EJ-011 | Not recorded | Validated | Automated tests cover candidate pull failure, no-row skip, successful one-byte read/close, sanitized storage construction/open failures with pre-promotion state preserved, and promotion-fault temporary-file cleanup; empty-read/read-exception/close-exception paths and live activation are not claimed. |
