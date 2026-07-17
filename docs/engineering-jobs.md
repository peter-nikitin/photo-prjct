# Engineering Jobs

This registry tracks engineering and operational capabilities for FindMe Photo, rather than
individual technical tasks. Engineering jobs use these actors: Developer, Contributor, Maintainer,
and Operator.

## Job format

Each job has a stable `EJ-NNN` identifier and uses this Jobs-to-be-Done form:

> When &lt;situation&gt;, I want to &lt;motivation&gt;, so I can &lt;expected outcome&gt;.

Every job records its current status, supporting evidence, and last-updated date. Status must not
advance from a proposal alone; an advance requires evidence appropriate to the new status.

## Statuses

| Status | Definition |
| --- | --- |
| Candidate | The job is recognized as potentially valuable, but has not been committed to a delivery plan. |
| Planned | The job is committed to a decision-complete delivery plan, but implementation has not started. |
| In progress | Implementation of the planned job has started but is not yet delivered. |
| Delivered | The job is available in the product, but its expected outcome has not yet been validated with sufficient evidence. |
| Validated | Evidence shows that the delivered job works and supports its expected outcome. |
| Deferred | Work on the job is intentionally postponed, with the reason recorded. |

## Current state

| Job | Actor | Summary | Status | Last updated |
| --- | --- | --- | --- | --- |
| EJ-001 | Developer | Reproduce local PostgreSQL development | Validated | 2026-07-17 |
| EJ-002 | Contributor | Receive complete CI feedback | Validated | 2026-07-17 |
| EJ-003 | Maintainer | Deploy an immutable image to staging | Validated | 2026-07-17 |
| EJ-004 | Operator | Run the current staging HTTP edge | Validated | 2026-07-17 |
| EJ-005 | Contributor | Reproduce visual regression | Validated | 2026-07-17 |
| EJ-006 | Maintainer | Promote the staging-verified image | Validated | 2026-07-17 |
| EJ-007 | Operator | Provision a production environment | Candidate | 2026-07-17 |
| EJ-008 | Operator | Activate trusted HTTPS | Planned | 2026-07-17 |
| EJ-009 | Operator | Detect service degradation | Candidate | 2026-07-17 |
| EJ-010 | Operator | Restore service data | Candidate | 2026-07-17 |

## Job details

### EJ-001 — Developer — Reproduce local PostgreSQL development

When I start repository work, I want Django and PostgreSQL to run from the documented environment
contract, so I can reproduce production-relevant behavior locally.

- Status: Validated
- Evidence: [`docker-compose.yml`](../docker-compose.yml), [`.env.example`](../.env.example), and [`src/backend/config/settings.py`](../src/backend/config/settings.py)
- Last updated: 2026-07-17

### EJ-002 — Contributor — Receive complete CI feedback

When I push a change or open a pull request, I want formatting, lint, types, PostgreSQL tests,
migrations, Django checks, and visual regression to run automatically, so I can detect regressions
before merge.

- Status: Validated
- Evidence: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), [`pyproject.toml`](../pyproject.toml), and [`package.json`](../package.json)
- Last updated: 2026-07-17

### EJ-003 — Maintainer — Deploy an immutable image to staging

When main advances, I want one SHA-tagged image built and applied to staging, so I can test the exact
artifact that may later be promoted.

- Status: Validated
- Evidence: [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), [`Dockerfile`](../Dockerfile), [`docker-compose.prod.yml`](../docker-compose.prod.yml), and [`deploy/apply-deployment.sh`](../deploy/apply-deployment.sh)
- Last updated: 2026-07-17

### EJ-004 — Operator — Run the current staging HTTP edge

When staging is deployed before HTTPS activation, I want its dedicated HTTP edge to proxy health and
application traffic, so I can operate the current environment without presenting it as production.

- Status: Validated
- Evidence: [`docker-compose.staging.yml`](../docker-compose.staging.yml), [`deploy/nginx/staging.conf`](../deploy/nginx/staging.conf), and [`test_public_environments_share_one_https_edge_overlay`](../tests/test_repository_foundation.py)
- Last updated: 2026-07-17

### EJ-005 — Contributor — Reproduce visual regression

When UI rendering changes, I want Playwright to run in the same pinned container environment locally
and in CI, so I can review deterministic snapshots.

- Status: Validated
- Evidence: [`package.json`](../package.json), [`Dockerfile.visual-tests`](../Dockerfile.visual-tests), [`docker-compose.visual.yml`](../docker-compose.visual.yml), and [`test_visual_regression_runs_in_a_pinned_container_environment`](../tests/test_repository_foundation.py)
- Last updated: 2026-07-17

### EJ-006 — Maintainer — Promote the staging-verified image

When a staging image is approved, I want an approval-gated workflow to verify and select that exact
image for production, so I can avoid rebuilding a different artifact.

- Status: Validated
- Evidence: [`.github/workflows/promote-production.yml`](../.github/workflows/promote-production.yml) and [`test_deployment_workflows_separate_staging_and_production`](../tests/test_repository_foundation.py)
- Last updated: 2026-07-17

### EJ-007 — Operator — Provision a production environment

When readiness evidence and pricing are approved, I want a separate non-preemptible production
environment, so I can serve customers without staging lifecycle constraints.

- Status: Candidate
- Evidence: [Architecture accepted constraints](architecture.md#accepted-constraints) and [staging-production deployment design — Phase 3](superpowers/specs/2026-07-11-staging-production-deployment-design.md)
- Last updated: 2026-07-17

### EJ-008 — Operator — Activate trusted HTTPS

When the canonical domain prerequisites are confirmed, I want the prepared shared HTTPS edge
activated and observed, so I can serve trusted canonical traffic and renew certificates safely.

- Status: Planned
- Evidence: [Canonical domain HTTPS edge plan — Chunk 2](plans/2026-07-13-canonical-domain-https-edge.md)
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

## Status log

This log is append-only.

| Date | Job | Previous status | New status | Evidence or reason |
| --- | --- | --- | --- | --- |
| 2026-07-17 | EJ-001 | Not recorded | Validated | [`docker-compose.yml`](../docker-compose.yml), [`.env.example`](../.env.example), and [`src/backend/config/settings.py`](../src/backend/config/settings.py) |
| 2026-07-17 | EJ-002 | Not recorded | Validated | [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), [`pyproject.toml`](../pyproject.toml), and [`package.json`](../package.json) |
| 2026-07-17 | EJ-003 | Not recorded | Validated | [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), [`Dockerfile`](../Dockerfile), [`docker-compose.prod.yml`](../docker-compose.prod.yml), and [`deploy/apply-deployment.sh`](../deploy/apply-deployment.sh) |
| 2026-07-17 | EJ-004 | Not recorded | Validated | [`docker-compose.staging.yml`](../docker-compose.staging.yml), [`deploy/nginx/staging.conf`](../deploy/nginx/staging.conf), and [`test_public_environments_share_one_https_edge_overlay`](../tests/test_repository_foundation.py) |
| 2026-07-17 | EJ-005 | Not recorded | Validated | [`package.json`](../package.json), [`Dockerfile.visual-tests`](../Dockerfile.visual-tests), [`docker-compose.visual.yml`](../docker-compose.visual.yml), and [`test_visual_regression_runs_in_a_pinned_container_environment`](../tests/test_repository_foundation.py) |
| 2026-07-17 | EJ-006 | Not recorded | Validated | [`.github/workflows/promote-production.yml`](../.github/workflows/promote-production.yml) and [`test_deployment_workflows_separate_staging_and_production`](../tests/test_repository_foundation.py) |
| 2026-07-17 | EJ-007 | Not recorded | Candidate | [Architecture accepted constraints](architecture.md#accepted-constraints) and [staging-production deployment design — Phase 3](superpowers/specs/2026-07-11-staging-production-deployment-design.md) |
| 2026-07-17 | EJ-008 | Not recorded | Planned | [Canonical domain HTTPS edge plan — Chunk 2](plans/2026-07-13-canonical-domain-https-edge.md) |
| 2026-07-17 | EJ-009 | Not recorded | Candidate | [Architecture open decisions — Observability stack](architecture.md#open-decisions) |
| 2026-07-17 | EJ-010 | Not recorded | Candidate | [Architecture Security, privacy, and legal boundaries](architecture.md#security-privacy-and-legal-boundaries) and [Open decisions](architecture.md#open-decisions) |
