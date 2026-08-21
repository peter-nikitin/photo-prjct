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
| EJ-003 | Maintainer | Deploy an immutable image to the canonical deployment | Delivered | 2026-08-20 |
| EJ-004 | Operator | Run the canonical HTTPS edge | Validated | 2026-08-20 |
| EJ-005 | Contributor | Reproduce visual regression | Validated | 2026-07-17 |
| EJ-006 | Maintainer | Retire the image-promotion path | Superseded | 2026-08-20 |
| EJ-007 | Operator | Retire the separate-environment proposal | Superseded | 2026-08-20 |
| EJ-008 | Operator | Activate trusted HTTPS | Delivered | 2026-07-17 |
| EJ-009 | Operator | Detect service degradation | Planned | 2026-07-30 |
| EJ-010 | Operator | Restore service data | Candidate | 2026-07-25 |
| EJ-011 | Maintainer | Gate private gallery media activation | Validated | 2026-07-19 |
| EJ-012 | Maintainer | Gate temporary selfie storage activation | Validated | 2026-07-31 |
| EJ-013 | Contributor | Start isolated repository work reliably | Validated | 2026-08-04 |
| EJ-014 | Maintainer | Gate consented feedback storage activation | Validated | 2026-08-04 |
| EJ-015 | Operator | Inspect bounded selfie-search operational evidence | Delivered | 2026-08-04 |
| EJ-016 | Maintainer | Build and guard event face-cluster expansion | Delivered | 2026-08-05 |
| EJ-017 | Developer | Read canonical secret projections consistently | Delivered | 2026-08-20 |
| EJ-018 | Maintainer | Minimize and recover runtime credentials | Candidate | 2026-08-07 |
| EJ-019 | Maintainer | Reconcile the capture-time projection before gallery cutover | Validated | 2026-08-08 |
| EJ-020 | Operator | Cache a frozen private event-original corpus | Candidate | 2026-08-07 |
| EJ-021 | Operator | Prepare private sampled face-quality review evidence | Validated | 2026-08-08 |
| EJ-022 | Maintainer | Gate preview-backed version-4 face generation activation | Delivered | 2026-08-10 |
| EJ-023 | Maintainer | Keep event-photo folders durable and safely filterable | Validated | 2026-08-15 |
| EJ-024 | Maintainer | Prepare paid watermarked-preview activation | In progress | 2026-08-20 |
| EJ-025 | Operator | Retain anonymous cart data within its bounded lifecycle | In progress | 2026-08-20 |

## Job details

Unless a job was updated on or after 2026-08-20, references below to `staging`, `production`, or
promotion record dated evidence from the former two-environment model. They are historical evidence,
not current deployment instructions; ADR 0028 and EJ-003/EJ-004 control the canonical deployment.

### EJ-001 — Developer — Reproduce local PostgreSQL development

When I start repository work, I want Django and PostgreSQL to run from the documented environment
contract, so I can reproduce production-relevant behavior locally.

- Status: Validated
- Evidence: [`docker-compose.yml`](../docker-compose.yml), [`.env.example`](../.env.example), and [`src/backend/config/settings.py`](../src/backend/config/settings.py)
- Last updated: 2026-07-17

### EJ-002 — Contributor — Receive complete CI feedback

When I update a pull request or `main` advances, I want formatting, lint, types, PostgreSQL tests,
migrations, Django checks, and visual regression to run automatically, so I can detect regressions
before merge and validate the integrated branch. Pull requests also protect the identities of base
migrations, so an environment that already applied them can upgrade safely.

Pull requests run through the `pull_request` trigger, while branch-push validation is limited to
`main`. Updating a feature branch therefore does not create a duplicate push run alongside its pull
request run.

- Status: Validated
- Evidence: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), [`scripts/check_migration_immutability.py`](../scripts/check_migration_immutability.py), [`tests/test_migration_immutability.py`](../tests/test_migration_immutability.py), [migration conflict runbook](runbooks/django-migration-conflicts.md), [`pyproject.toml`](../pyproject.toml), and [`package.json`](../package.json)
- Last updated: 2026-07-17

### EJ-003 — Maintainer — Deploy an immutable image to the canonical deployment

When main advances, I want one SHA-tagged image built and applied to the canonical deployment, so I can
serve the exact reviewed artifact. Before any application mutation, the candidate migration ledger
and plan are checked read-only. Pull requests protect numbered migration identities, privileged
observability-package changes pause the automatic path until an operator bootstrap and manual
dispatch, and named deployment phases feed one bounded non-blocking failure issue. The existing
GHCR image, Docker Compose, root-owned package, and rollback path remain authoritative.

- Status: Delivered
- Evidence: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml),
  [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml),
  [`scripts/check_migration_immutability.py`](../scripts/check_migration_immutability.py),
  [`tests/test_migration_immutability.py`](../tests/test_migration_immutability.py),
  [migration-conflict runbook](runbooks/django-migration-conflicts.md),
  [`src/backend/picflow/management/commands/verify_migration_history.py`](../src/backend/picflow/management/commands/verify_migration_history.py),
  [`src/backend/picflow/tests/test_verify_migration_history_command.py`](../src/backend/picflow/tests/test_verify_migration_history_command.py),
  [`deploy/apply-deployment.sh`](../deploy/apply-deployment.sh),
  [`tests/deployment/test_deployment_scripts.py`](../tests/deployment/test_deployment_scripts.py),
  [`tests/test_repository_foundation.py`](../tests/test_repository_foundation.py),
  and [canonical deployment runbook](runbooks/deployment.md).
- Deployment evidence: no PR, CI, canonical-deployment rollout, deployed-image, public-health, or notification-drill
  result is recorded in this checkout; keep `Delivered` until those acceptance checks establish
  `Validated`.
- Last updated: 2026-08-07

### EJ-004 — Operator — Run the canonical HTTPS edge

When the canonical deployment is applied, I want the shared HTTPS edge to terminate trusted traffic
and proxy the application, so I can operate the customer-serving VM.

- Status: Validated
- Evidence: [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), [`docker-compose.https.yml`](../docker-compose.https.yml), [`deploy/apply-deployment.sh`](../deploy/apply-deployment.sh), and historical GitHub Actions deploy run 29556330740.
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

### EJ-006 — Maintainer — Retire the image-promotion path

ADR 0028 supersedes this former two-environment job. **Deploy** is the only application delivery
workflow; no promotion workflow or GitHub Environment approval is part of the current architecture.

- Status: Superseded
- Evidence: [ADR 0028](adr/0028-operate-one-canonical-deployment.md)
- Last updated: 2026-08-20

### EJ-007 — Operator — Retire the separate-environment proposal

ADR 0028 supersedes the proposal. A future isolated test environment requires an accepted
availability-isolation need and a separate decision; it is not a default promotion target.

- Status: Superseded
- Evidence: [ADR 0028](adr/0028-operate-one-canonical-deployment.md)
- Last updated: 2026-08-20

### EJ-008 — Operator — Activate trusted HTTPS

When the canonical domain prerequisites are confirmed, I want the prepared shared HTTPS edge
activated and observed, so I can serve trusted canonical traffic and renew certificates safely.

- Status: Delivered
- Evidence: [Canonical domain HTTPS edge plan — Chunk 2](plans/2026-07-13-canonical-domain-https-edge.md#chunk-2-https-activation-release), [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), and [successful then-designated staging deploy run 29556330740](https://github.com/peter-nikitin/photo-prjct/actions/runs/29556330740)
- Last updated: 2026-07-17

### EJ-009 — Operator — Detect service degradation

When a product or processing component becomes unhealthy, I want monitoring and actionable alerts,
so I can respond before failures persist unnoticed.

The approved minimum is limited to the current public critical path. An external managed checker
observes canonical HTTPS availability, TLS validity, and latency independently from Yandex Cloud.
Yandex Monitoring receives simple Linux VM metrics from Unified Agent and private low-cardinality
Django HTTP request, 5xx, and latency metrics. One dashboard and one operator email channel cover
sustained public failure, missing VM telemetry, imminent disk or memory exhaustion, sustained CPU
pressure, application 5xx degradation, and recovery. Logs, tracing, business metrics, database
internals, privileged container collection, and the disabled worker remain outside this increment.

- Status: Planned
- Evidence: [Minimal service monitoring design](superpowers/specs/2026-07-30-minimal-service-monitoring-design.md)
  and [approved implementation plan](plans/2026-07-30-minimal-service-monitoring.md)
- Last updated: 2026-07-30

### EJ-010 — Operator — Restore service data

When transactional data or media metadata is lost or corrupted, I want a tested backup and restore
procedure with agreed recovery targets, so I can recover service safely.

- Status: Candidate
- Evidence: [`scripts/clone-deployed-db.sh`](../scripts/clone-deployed-db.sh) and [`tests/deployment/test_clone_deployed_database.py`](../tests/deployment/test_clone_deployed_database.py) provide partial local restore evidence: a developer can create a validated deployed-VM logical dump, replace only the current checkout's local Compose database through a serialized local-Docker-only workflow, quiesce the normal web service, retain diagnostic and safety dumps, and validate migration readiness without running the mutating web entrypoint. Separate isolated PostgreSQL 16 integrations verify marker/owner/ACL normalization and the actual project image's `django_migrations`, `showmigrations`, and `makemigrations` readiness against a restored migrated schema without deployed-VM network contact. This remains insufficient for service-data recovery: scheduled backups, retention, RPO/RTO, media recovery, and a disaster-recovery drill are not established. See also [Architecture Security, privacy, and legal boundaries](architecture.md#security-privacy-and-legal-boundaries) and [Open decisions](architecture.md#open-decisions).
- Last updated: 2026-07-25

### EJ-014 — Operator — Inspect bounded selfie-search operational evidence

When I operate selfie search, I want privacy-bounded events and a reproducible daily summary, so I
can diagnose its funnel without turning logs into product state or a backup.

- Status: Delivered
- Evidence: repository tests cover strict event contracts, redacted edge logs, deterministic daily
  aggregation, idempotent managed-file installation, exact rollback, effective journal caps, timer
  state, Compose tags, and bounded probe readability. No canonical deployment or replacement-
  persistence evidence is claimed yet.
- Last updated: 2026-08-04

### EJ-011 — Maintainer — Gate private gallery media activation

When I deploy a gallery-capable image, I want its candidate code and requested private-media
settings checked before a canonical deployment service switch, so I can avoid activating media
delivery that cannot read an eligible original.

The deployment entrypoint always pulls the candidate web image. With no successful
`deployed-image` marker, automated tests prove that a truly fresh deployment emits the sanitized
`no-existing-deployment` skip, constructs no ORM preflight container, and completes the normal
first-deployment flow; that skip is not `GetObject` validation. Once the marker exists, the
entrypoint uses a mode-0600 temporary environment file for the fail-closed candidate-image one-off.
Tests cover candidate pull failure; the no-eligible-row skip without storage construction; the
successful storage construction, final-object open, one-byte read, and body close; sanitized
database-query, storage-construction, and object-open failures; ordering before the service switch;
preservation of canonical configuration, deployment markers, and services on those failures; and
removal of the secret-bearing temporary file when the service switch fails. They do not exercise empty-read, read-exception, or
close-exception failure paths.
This is repository automation evidence only: no canonical-deployment activation, IAM
permission, bucket policy, or private object was validated or changed.

- Status: Validated
- Evidence: [`deploy/apply-deployment.sh`](../deploy/apply-deployment.sh), [`tests/deployment/test_deployment_scripts.py::test_fresh_first_deployment_skips_orm_gate_and_completes_normal_flow`](../tests/deployment/test_deployment_scripts.py), [`tests/deployment/test_deployment_scripts.py::test_candidate_pull_failure_leaves_canonical_env_without_service_reconciliation`](../tests/deployment/test_deployment_scripts.py), [`tests/deployment/test_deployment_scripts.py::test_candidate_private_media_preflight_skips_when_no_eligible_photo`](../tests/deployment/test_deployment_scripts.py), [`tests/deployment/test_deployment_scripts.py::test_candidate_private_media_preflight_reads_when_photo_exists`](../tests/deployment/test_deployment_scripts.py), [`tests/deployment/test_deployment_scripts.py::test_candidate_private_media_preflight_runs_before_service_switch`](../tests/deployment/test_deployment_scripts.py), [`tests/deployment/test_deployment_scripts.py::test_failed_candidate_private_media_preflight_leaves_canonical_env_untouched`](../tests/deployment/test_deployment_scripts.py), and [`tests/deployment/test_deployment_scripts.py::test_failed_env_promotion_removes_secret_bearing_requested_temp`](../tests/deployment/test_deployment_scripts.py)
- Last updated: 2026-07-19

### EJ-012 — Maintainer — Gate temporary selfie storage activation

When I prepare the canonical deployment for public selfie search, I want lifecycle mutation and scratch-object
access to require explicit, fail-closed checks, so I can avoid enabling uploads without bounded
cleanup and exact-object access.

Repository automation requires an explicit mutation flag, an approved bucket-name digest, an
unversioned bucket, collision-free preservation of existing lifecycle rules, exact readback, and
automatic restoration after a mismatch. A separate explicit real-storage preflight checks the
bounded `selfie-search/` lifecycle and performs one generated put/head/grant/delete cycle with
sanitized markers and cleanup on failure. Automated tests validate these contracts. On 2026-07-31,
the then-designated staging deployment applied the one-day `selfie-search/` lifecycle rule while preserving the existing preview
rule; the real-bucket preflight passed its put/head/grant/delete markers, and the feature was then
enabled. This dated evidence does not define a current deployment target.

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

### EJ-014 — Maintainer — Gate consented feedback storage activation

When I prepare the canonical deployment for consented selfie-search feedback, I want the dedicated bucket,
KMS, lifecycle, anonymous-denial, and web-only credential contract checked fail-closed, so I can
keep feedback disabled until its private storage boundary is ready.

The repository provides a guarded 30-day lifecycle mutation with exact bucket/KMS digest,
unversioned and unlocked checks, readback, and recovery. Its explicit real-storage preflight checks
default KMS encryption, private ACLs, anonymous object and list denial, lifecycle, and one opaque
put/head/range/grant/delete scratch cycle. Deployment tests verify disabled-by-default wiring,
preflight confirmation before enablement, and that feedback credentials reach only the web service;
no Yandex bucket or canonical-deployment activation is claimed.

- Status: Validated
- Evidence: [`src/backend/selfie_search/feedback_lifecycle.py`](../src/backend/selfie_search/feedback_lifecycle.py), [`src/backend/selfie_search/management/commands/configure_selfie_feedback_lifecycle.py`](../src/backend/selfie_search/management/commands/configure_selfie_feedback_lifecycle.py), [`src/backend/selfie_search/management/commands/verify_selfie_feedback_storage.py`](../src/backend/selfie_search/management/commands/verify_selfie_feedback_storage.py), [`src/backend/selfie_search/tests/test_feedback_lifecycle_configuration.py`](../src/backend/selfie_search/tests/test_feedback_lifecycle_configuration.py), [`src/backend/selfie_search/tests/test_configure_feedback_lifecycle_command.py`](../src/backend/selfie_search/tests/test_configure_feedback_lifecycle_command.py), [`src/backend/selfie_search/tests/test_feedback_storage_contract_command.py`](../src/backend/selfie_search/tests/test_feedback_storage_contract_command.py), and [`tests/deployment/test_deployment_scripts.py`](../tests/deployment/test_deployment_scripts.py)
- Last updated: 2026-08-04

### EJ-016 — Maintainer — Build and guard event face-cluster expansion

When I need to measure a possible recall improvement without changing the ordinary selfie-search
path, I want to build immutable event-scoped cluster evidence, evaluate it privately, and require
an explicit guarded activation, so I can keep direct-only search available until quality and resource
gates are approved.

- Status: Delivered for the repository capability; release-gate completion, canonical-deployment activation,
  and customer-outcome validation remain pending.
- Evidence: [`src/backend/processing/services/face_clustering.py`](../src/backend/processing/services/face_clustering.py), [`src/backend/processing/services/face_cluster_corpora.py`](../src/backend/processing/services/face_cluster_corpora.py), [`src/backend/processing/management/commands/build_face_cluster_corpus.py`](../src/backend/processing/management/commands/build_face_cluster_corpus.py), [`src/backend/processing/management/commands/activate_face_cluster_corpus.py`](../src/backend/processing/management/commands/activate_face_cluster_corpus.py), [`src/backend/selfie_search/services/cluster_expansion.py`](../src/backend/selfie_search/services/cluster_expansion.py), [`src/backend/selfie_search/services/cluster_reporting.py`](../src/backend/selfie_search/services/cluster_reporting.py), and [`experiments/face_recognition_spike/face_spike/cli.py`](../experiments/face_recognition_spike/face_spike/cli.py). Focused tests cover deterministic clustering, immutable publication and activation guards, direct-first provenance, source-separated reports, privacy-bounded v2 events, and the closed held-out evaluator. `SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED=False` remains the default; no worker credential/configuration, Compose, cloud, or canonical-deployment activation change is included.
- Last updated: 2026-08-05

### EJ-020 — Operator — Cache a frozen private event-original corpus

When I evaluate a new gallery-face generation, I want one complete, verified private local copy of
the selected published event's originals, so repeated baseline and candidate runs use identical
bytes without re-reading intact objects from private Object Storage.

The repository provides an explicit event or deterministic latest-published selection, a frozen
private manifest, conditional streamed reads, atomic local publication, and strict reuse checks.
It performs database reads plus Object Storage `HeadObject` and conditional `GetObject` only;
neither credentials nor object keys are emitted in normal command output. Automated tests cover
the local cache contract. An authorized deployed-data clone and private-media run is still required
before this becomes operationally validated.

- Status: Candidate pending an authorized local run.
- Evidence: [`src/backend/processing/services/event_original_cache.py`](../src/backend/processing/services/event_original_cache.py), [`src/backend/processing/management/commands/cache_event_originals.py`](../src/backend/processing/management/commands/cache_event_originals.py), and [`src/backend/processing/tests/test_event_original_cache.py`](../src/backend/processing/tests/test_event_original_cache.py)
- Last updated: 2026-08-07

### EJ-021 — Operator — Prepare private sampled face-quality review evidence

When I assess a local experimental face-quality configuration, I want a reproducible private
sampled-review bundle with bounded integrity evidence, so I can later make an explicit operator
decision without changing runtime behavior.

- Status: Validated for the private local sampled-review workflow and its immutable sample bundle;
  human labels and an explicit operator decision remain pending.
- Evidence: The frozen comparison source bundle SHA-256 is
  `f1028cf1e581645dd0cf108e356394dc5ada838b92c9f662c1356cd52e657b48`. A private local bundle
  published under `run-20260808T015921Z` as `quality-sample-10pct-attempt-1` has SHA-256
  `1b2aad77523b59418a552ce2fdd92a9a290c79b8b39d3ac2ae35fa32599eb6cc`: 15,052 population
  rejections, 1,506 unique sampled rejections, six strata, seven logical pages, and 100 retained
  controls. First, middle, and last logical probes resolved. The source-comparison aggregate
  filesystem SHA-256 was
  `4271a0b017b5812d38174a5bca3308c4c8f664f9ec231830ac93ea6981aaa360` before and after the
  sample publication. A non-human, all-`uncertain` fixture CSV (SHA-256
  `e39a0930ddb22911c1872016354f33e0511b26dc49584abf2a57f09b8a44247d`) completed the finalizer
  round trip; its analysis manifest SHA-256 is
  `316fb731aec48b2ded99d7672a9ff388d6e3b49a541766457f7a609ab36160bb` and records six strata,
  100 controls, and 1,506 uncertain gallery entries. This fixture proves only the finalization
  round trip, not a quality decision. The tooling and evidence are local and filesystem-only; no
  canonical-deployment generation is activated, and the separate search-relevance review remains pending.
  See the [approved sampled-review plan](plans/2026-08-08-ten-percent-face-quality-review.md) and
  [`face_spike` experiment](../experiments/face_recognition_spike/README.md).
- Last updated: 2026-08-08

### EJ-022 — Maintainer — Gate preview-backed version-4 face generation activation

When I roll out the reviewed preview-backed face-quality generation for one event, I want a
dark-deployable worker identity, exact approval evidence, bounded replay/status reporting, and an
explicit event activation gate, so I can promote only a complete compatible cohort without changing
other events, ranking, or historical biometric evidence.

- Status: Delivered for the repository capability, focused local-contract evidence, and the
  maintainer-accepted local full-corpus quality selection. Current-merge-candidate full `make
  check`/reconciliation, PR creation, GitHub CI, canonical deployment, event replay/activation,
  and customer-facing verification are separate pending evidence states; none is claimed here.
- Evidence: Commit `e29e65a` implements the exact `3/face_embedding/4` approval, accepted
  `preview-small-v1` cohort validation, dry-run-by-default
  [`reprocess_event_face_embeddings`](../src/backend/processing/management/commands/reprocess_event_face_embeddings.py)
  command, idempotent replay, privacy-safe status aggregates, and append-only activation gate in
  [`enrollment.py`](../src/backend/processing/services/enrollment.py) and
  [`face_quality.py`](../src/backend/processing/services/face_quality.py). Its focused activation,
  replay, enrollment, and adjacent corpus tests passed (60 tests). Commit `333f5b8` accepts the
  exact identity only when configured in [`apply-deployment.sh`](../deploy/apply-deployment.sh),
  forwards it through [**Deploy**](../.github/workflows/deploy.yml), and proves with focused
  deployment/workflow and worker-contract tests that deployment performs neither replay nor
  activation. The candidate keeps the `0.363` ranking threshold and preserves baseline,
  version-3/version-4, failed-attempt, projection, activation, and bearer-result evidence. The
  maintainer accepted the complete local quality selection of 17,043
  photos/jobs/attempts/projections with zero technical failures, 37,573 kept faces, and 18,610
  quality-rejected faces; its exact configuration, preview-manifest, comparison-manifest, and
  YuNet/SFace SHA-256 values are recorded in the
  [approved rollout design](superpowers/specs/2026-08-10-preview-face-quality-v4-rollout-design.md#approval-evidence).
  That evidence keeps local canonical projection
  `a98b5d13152683419c722a115045037fdf883a1f5cdcc3e47a2bddf5291b7d63` separate from accepted
  runtime `PhotoDerivative` cohort
  `6701b7436e1b00b64e701791983a0c9c1d26bcddd56f93a36dd0923aa6bc1034`. Their immutable reviewed
  crosswalk is `055d7c72614deb3b87b607f467c16365ee6e125be005e9e8f5cf2e910ec56d51`
  with `entries=17043` and `sha_mismatch=17043`: every local/accepted SHA-256 differs, so the
  crosswalk binds reviewed identities rather than proving byte equivalence. Commit `4f10a1a`
  makes enrollment and activation recompute the full accepted cohort hash from photo ID, accepted
  SHA-256, byte size, and geometry; any field change blocks before a job write or activation append.
- Last updated: 2026-08-10

### EJ-023 — Maintainer — Keep event-photo folders durable and safely filterable

When I add event-specific photo folders, I want administration, upload state, and public filtering
to enforce the same event boundary, so folders organize photos without changing storage or media
authority.

- Status: Validated by local automated evidence only.
- Evidence: [`src/backend/picflow/tests/test_admin.py`](../src/backend/picflow/tests/test_admin.py)
  covers inline event-folder management and protected deletion;
  [`src/backend/ingestion/tests/test_batch_services.py`](../src/backend/ingestion/tests/test_batch_services.py),
  [`src/backend/ingestion/tests/test_confirmation.py`](../src/backend/ingestion/tests/test_confirmation.py),
  and [`src/backend/ingestion/tests/test_resume.py`](../src/backend/ingestion/tests/test_resume.py)
  cover durable named-folder and `Без папки` assignments through mixed-batch registration,
  confirmation, retries, and resume; [`src/backend/picflow/tests/test_views.py`](../src/backend/picflow/tests/test_views.py)
  covers stable event-local GET filtering, capture-time composition, and pagination. Folder values
  do not alter existing upload ownership, gallery eligibility, or media authorization. The mass
  editor for already uploaded photos, its photographer/time filters, and its permission model are
  explicitly deferred. No CI, canonical deployment, or customer evidence is claimed.
- Last updated: 2026-08-15

### EJ-024 — Maintainer — Prepare paid watermarked-preview activation

When I prepare newly confirmed paid photos for public presentation, I want the protected preview
path to remain disabled until its worker, artwork, and media authorization are ready, so I can
avoid exposing a clean preview or original.

- Status: In progress
- Evidence: The repository implements ADR 0029's explicit paid-photo policy, immutable
  watermarked derivative publication, gated normal-gallery and ready-result selection, and
  pre-signing original/download denial. On 2026-08-20, 215 focused Django tests and 85 direct
  worker renderer/runner tests passed. Final artwork is still placeholder-only; the runtime gate is
  off, the complete suite, visual regression, worker image, deployment, and real activation are
  not evidenced. This job adds no price, cart, purchase, entitlement, or purchased-original
  delivery.
- Last updated: 2026-08-20

### EJ-025 — Operator — Retain anonymous cart data within its bounded lifecycle

When anonymous paid-photo carts expire, I want the application to reject them immediately and the
deployment package to remove them in bounded daily batches, so browser-linked selection does not
remain usable or accumulate indefinitely.

- Status: In progress
- Evidence: Local command and deployment-contract tests cover fail-closed request-time expiry, a
  bounded `cleanup_expired_carts --limit 1000` command that reports Cart rows rather than cascaded
  objects, an idempotent `flock`-guarded host-cron package, successful-deployment installation, and
  failed-candidate removal. No host cron has been installed, no deployment has run, and no runtime
  gate has been enabled.
- Last updated: 2026-08-20

### EJ-017 — Developer — Read canonical secret projections consistently

When I run the application locally or use an approved workflow, I want one managed secret authority
with only the projection I need, so I can work without copying credentials into GitHub or local files.

The implemented canonical boundary is `deploy/environment-secrets.json`: one Lockbox secret,
main-branch and exact-workflow-restricted OIDC for GitHub, interactive `yc` for authorized humans,
and only `local-web`, `deploy`, `remote-check`, or `public-monitor` projections. The supported local
launcher materializes a mode-0600 temporary file, applies safe local overrides, removes it after use,
and emits no secret values. Exact-secret reader roles, rotation, revocation, rollback, and
non-disclosure are documented and covered by repository-foundation contracts.

- Status: Delivered
- Evidence: [`deploy/environment-secrets.json`](../deploy/environment-secrets.json),
  [`scripts/run-with-environment-secrets.py`](../scripts/run-with-environment-secrets.py),
  [environment secrets runbook](runbooks/environment-secrets.md),
  [inventory](runbooks/environment-secrets-inventory.md), [ADR 0028](adr/0028-operate-one-canonical-deployment.md),
  and [`tests/deployment/test_environment_secrets.py`](../tests/deployment/test_environment_secrets.py).
- Last updated: 2026-08-20

### EJ-018 — Maintainer — Minimize and recover runtime credentials

When I operate or recover an application environment, I want each runtime component to retain only
the credentials it needs through an explicit, recoverable lifecycle, so I can limit credential
exposure without making restart, rollback, backup, or disaster recovery unreliable.

The candidate capability covers the complete host and container boundary rather than one `.env`
file: persistent deployment environments, Docker container metadata, Docker group access, registry
authentication, shell history, TLS private keys, VM metadata and attached service accounts, disk
snapshots/backups, per-service credential projection, rotation, revocation, recovery, and audit.
It must begin with a separate specification and architecture reconciliation; this registry entry
does not select Docker secrets, file-based settings, runtime Lockbox retrieval, an agent, or another
delivery mechanism.

- Status: Candidate
- Evidence:
  [Sanitized staging runtime credential inventory](future-work/2026-08-07-runtime-credential-hygiene.md)
  records the observed exposure surfaces and the trigger for a separate design. No VM cleanup,
  credential rotation, runtime redesign, ADR, or implementation plan is claimed.
- Last updated: 2026-08-07

### EJ-019 — Maintainer — Reconcile the capture-time projection before gallery cutover

When I maintain a capture-time projection for gallery filtering, I want its writers, rebuild, and
aggregate reconciliation to agree with immutable current version-2 evidence, so I can keep direct
gallery reads safe until a separately accepted projection-reader release.

- Status: Delivered for the accepted Release A operation and deployed Release B; future deployment
  candidates use live projection evidence rather than an exact Release A image precondition.
- Evidence: [`src/backend/picflow/capture_time_projection.py`](../src/backend/picflow/capture_time_projection.py),
  [`src/backend/picflow/management/commands/rebuild_photo_capture_time_projection.py`](../src/backend/picflow/management/commands/rebuild_photo_capture_time_projection.py),
  [`src/backend/picflow/management/commands/report_photo_capture_time_projection.py`](../src/backend/picflow/management/commands/report_photo_capture_time_projection.py),
  the accepted Release A deployment (`41e3068`) has a clean 17,043/17,043 global event-9
  reconciliation and a rollback-only lifecycle smoke that clears then republishes the projection.
  The immutable accepted local clone contains 9 events and 17,310 photos. Its final Release B
  candidate report is clean before and after benchmarking, with 17,043 exact source/value pairs
  and every first/midpoint/last database and rendered ratio at or below 2x; the retained
  [benchmark JSON](performance/2026-08-08-event-gallery-time-filter-local-clone.json) is aggregate
  only. Release B commit `d5b21e4` is deployed. The integrated
  gallery/processing/projection/deployment suite, visual suite (92 tests),
  and separate `make check` exit clean locally; Ruff, MyPy, Django, and migration-drift checks are
  included in the quality gate.
- Boundary: Release B removes the direct JSON/cast filtered-reader path and is deployed. Every
  future candidate still requires live all-events projection reconciliation and the event-9
  benchmark before service switch; immutable attempt/state/run/job/result evidence remains
  authoritative and is never rewritten by rebuild or reconciliation.
- Last updated: 2026-08-10

## Status log

This log is append-only.

| Date | Job | Previous status | New status | Evidence or reason |
| --- | --- | --- | --- | --- |
| 2026-07-17 | EJ-001 | Not recorded | Validated | [`docker-compose.yml`](../docker-compose.yml), [`.env.example`](../.env.example), and [`src/backend/config/settings.py`](../src/backend/config/settings.py) |
| 2026-07-17 | EJ-002 | Not recorded | Validated | [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), [`pyproject.toml`](../pyproject.toml), and [`package.json`](../package.json) |
| 2026-07-17 | EJ-003 | Not recorded | Validated | [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), [`Dockerfile`](../Dockerfile), [`docker-compose.deployment.yml`](../docker-compose.deployment.yml), and [`deploy/apply-deployment.sh`](../deploy/apply-deployment.sh) |
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
| 2026-07-30 | EJ-009 | Candidate | Candidate | The approved minimal monitoring design now defines independent public HTTPS checks, simple VM and Django HTTP graphs, actionable email alerts, safe collection boundaries, and a new-ADR prerequisite; implementation is not yet planned or delivered. |
| 2026-07-30 | EJ-009 | Candidate | Planned | The approved implementation plan sequences bounded Django metrics, a private scrape path, a scheduled GitHub probe, Unified Agent, Yandex Monitoring resources, controlled activation, rollback, and validation. |
| 2026-07-31 | EJ-012 | Not recorded | Validated | Automated command tests verify explicit confirmation, exact lifecycle preservation/readback/recovery, unversioned-bucket enforcement, bounded-prefix preflight, generated scratch-object cleanup, and sanitized output. Staging additionally applied the one-day `selfie-search/` rule without changing the preview rule, then passed real-bucket put/head/grant/delete preflight before feature enablement. Production is not activated. |
| 2026-08-04 | EJ-013 | Not recorded | Validated | PR #94 adds and tests the supported worktree bootstrap, CI-like test wrapper, shared Ruff pre-commit installation, clean Git state, secret isolation, and stable `make test`/`make check` entry points; a disposable real-worktree smoke, hook-driven commit, and successful Quality checks validate the integrated workflow. |
| 2026-08-04 | EJ-014 | Not recorded | Validated | Automated lifecycle, storage-contract, and deployment tests verify the guarded 30-day feedback bucket contract, anonymous denial probes, scratch cleanup, disabled-by-default wiring, and web-only credential propagation. No live bucket/KMS preflight or environment activation is claimed. |
| 2026-08-04 | EJ-015 | Not recorded | Delivered | Repository verification covers strict bounded events, edge redaction, journald reconciliation and exact rollback, timer/driver/tag checks, probe readability, and deterministic recomputation. Staging activation is not claimed. |
| 2026-08-05 | EJ-016 | Not recorded | Delivered | Task 1–7 implementation commits and focused contract tests provide the repository capability for immutable event-scoped corpora, direct-first expansion, provenance, source-separated reporting, private evaluation, and guarded activation. The feature gate remains false and the release gate, environment activation, and customer outcomes are not yet evidenced. |
| 2026-08-07 | EJ-020 | Not recorded | Candidate | The repository now has a focused, read-only event-original cache command and automated local-contract coverage. No authorized staging-clone or private Object Storage invocation is claimed. |
| 2026-08-07 | EJ-003 | Validated | Delivered | Repository workflow, migration-identity, read-only preflight, deployment-phase, controlled-pause, and bounded issue-reconciliation contracts are implemented and covered by focused tests. No PR/CI/live staging rollout or notification-drill evidence is recorded yet, so the job is not advanced to Validated. |
| 2026-08-07 | EJ-017 | Not recorded | Candidate | The maintainer requested one managed, environment-scoped source of secrets that authorized local development, CI, and deployed workflows can read without copying payloads into GitHub Secrets or persistent local files. |
| 2026-08-07 | EJ-018 | Not recorded | Candidate | A sanitized staging audit confirmed persistent host and Docker credential surfaces; the maintainer deferred a comprehensive runtime credential lifecycle design to a separate task rather than expanding EJ-017. |
| 2026-08-07 | EJ-017 | Candidate | Planned | The maintainer accepted ADR 0026 and approved the decision-complete environment-scoped Lockbox implementation plan for execution. |
| 2026-08-08 | EJ-019 | Not recorded | Validated | Release A writer/schema/rebuild/report and local accepted-clone reconciliation are evidenced; direct gallery reads remain active and CI/deployment/live operational-gate evidence is pending. |
| 2026-08-08 | EJ-019 | Validated | Delivered | Accepted Release A staging writer/direct-reader operation, local Release B projection-reader evidence, clean global reconciliation, and aggregate 2x benchmark are recorded. Release B review, PR/CI, deployment, live candidate gate, and cutover remain pending. |
| 2026-08-10 | EJ-019 | Delivered | Delivered | Release B commit `d5b21e4` is deployed. The obsolete exact Release A image precondition is retired; future deployments retain live all-events reconciliation and event-9 benchmark gates. |
| 2026-08-08 | EJ-021 | Not recorded | Validated | A frozen 15,052-rejection comparison produced an immutable private 1,506-face, six-stratum sampled bundle with 100 separate retained controls; its bounded hashes, unchanged-source check, logical-page probes, and non-human fixture-finalizer round trip are recorded in EJ-021. Human labels, an operator decision, runtime activation, and search-relevance review remain separate. |
| 2026-08-10 | EJ-022 | Not recorded | Delivered | Commits `e29e65a` and `333f5b8` provide the exact preview-backed version-4 approval/replay/activation and dark-deployment capability with focused local-contract tests. The maintainer-accepted 17,043-photo full-corpus quality selection, its counts, and its exact hashes are recorded in the [approved rollout design](superpowers/specs/2026-08-10-preview-face-quality-v4-rollout-design.md#approval-evidence). Current-merge-candidate `make check`/reconciliation, PR/CI, all staging and production rollout stages, and live verification remain unrecorded. |
| 2026-08-10 | EJ-022 | Delivered | Delivered | Commit `4f10a1a` enforces the clarified accepted cohort contract: the local projection and accepted runtime cohort have distinct canonical hashes and 17,043/17,043 SHA mismatches bound by one immutable reviewed crosswalk. Runtime enrollment and activation recompute the accepted cohort identity over photo ID, accepted SHA-256, byte size, and geometry; no byte-equivalence or environment-rollout evidence is claimed. |
| 2026-08-15 | EJ-023 | Not recorded | Validated | Local automated coverage validates inline folder administration, durable mixed-folder ingestion, and stable public GET filtering restricted to the existing eligible event gallery. Folder identifiers remain non-authoritative for media; the mass editor is deferred. No CI, deployment, staging, or customer evidence is claimed. |
| 2026-08-20 | EJ-024 | Not recorded | In progress | ADR 0029's repository path is implemented with an off-by-default gate and focused Django/worker test evidence. Final artwork, complete-suite, visual, worker-image, deployment, and real-activation evidence remain pending. |
| 2026-08-20 | EJ-025 | Not recorded | In progress | Local command and deployment-contract coverage packages bounded daily cart cleanup; installation and operational verification remain pending. |
