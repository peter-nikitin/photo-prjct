# Retire legacy face-recognition models

- Date: 2026-10-04
- Status: Approved for implementation; production release gates remain pending
- Owner: project maintainer
- Related specification: [legacy model retirement](../superpowers/specs/2026-10-04-retire-legacy-face-models-design.md)
- Related architecture: [Search](../architecture.md#search), [Photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing)
- Related ADRs: [0054](../adr/0054-retire-sface-and-fix-adaface-vector-dimension.md), [0040](../adr/0040-use-pgvector-for-exact-face-search.md), [0041](../adr/0041-accept-pgvector-numerical-boundaries.md)
- ADR impact: Implements accepted ADR 0054; preserves exact ranking and numerical policy from ADRs 0040/0041.

## Goal

Implement the approved AdaFace-only contract and remove live SFace code and vectors while retaining historical non-vector processing and ready search results.

## Scope

The linked specification is authoritative. This plan changes no product scope. It does not authorize production deployment or database deletion before the release safeguards below are satisfied.

## Acceptance criteria

Use the specification's acceptance criteria. In addition, a restored existing local snapshot must prove that old vector data can be removed while ready links and non-vector history remain readable and the schema ends with fixed `vector(512)`.

## Worker/state/artifact release safeguards

- [x] **Live-state inventory.** The 2026-10-04 read-only audit records one published and eight unavailable SFace events, one published event with 17,043 photos, 16,162 accepted AdaFace outcomes, 225 no-face, 20 quality-rejected, 636 accepted recognition differences, and no pending AdaFace jobs or active leases. Before release, refresh web/worker build IDs; event, job, attempt, search, lease and vector counts; ready results; active corpus; and temporary selfie-object inventory with bounded queries. An existing local PostgreSQL snapshot volume `pgvector-local-snapshot_snapshot_data` and prior dumps are available for rehearsal; do not download a duplicate before inspecting these.
- [x] **Additional accepted coverage gap.** The 2026-10-04 follow-up read-only audit found 73 published `cyclingrace-klin` photos with 174 old kept detections and no AdaFace v5 job/projection. The event has 6,621 current AdaFace projections, all with matching current state. The user accepted losing those 73 old matches without reprocessing or a special runtime exception. Report the gap at release; a wholly unreconciled published event still blocks contraction.
- [x] **Compatibility matrix.** Old web/worker can create and consume SFace 128D rows and cannot run after contraction. Candidate web/worker accept only current AdaFace 512D and retain old rows as inert history until the guarded data step. Old nonterminal SFace search/processing work is terminalized, not retried. Ready results remain readable under candidate code and after contraction. A candidate failure before vector deletion can recover with compatible candidate images; after deletion only a verified backup plus compatible images or forward repair is supported.
- [x] **Reviewed data-state migration or reset semantics.** Reconcile published AdaFace evidence first; accept terminal no-face and quality rejection. Retain unavailable events with empty current recognition where sources are absent. Preserve attempt, detection, projection, activation audit and ready result records. Redact raw SFace arrays in historical attempt/late-receipt JSON; preserve original receipt hashes as submitted-payload fingerprints. Remove only SFace pgvector rows, old active corpora/selection and obsolete executable code. Protect AdaFace vectors and all result membership.
- [x] **End-to-end contract sizing.** Keep the existing maximum current AdaFace face callback and selfie-query payload contract. Exercise maximum 32-face/512-component face completion through worker serialization, private callback, validation and native persistence; verify the existing request-size limits. No larger payload or new request field is introduced.
- [x] **Previous-snapshot upgrade rehearsal.** Restore an existing local production snapshot into an isolated PostgreSQL instance. Record its timestamp, model counts and differences from the current read-only inventory. Rehearse accepted/failed/retryable/stale attempts, active or expired leases, no-face outcomes, old ready results, old corpora and never-enrolled unavailable photos. Verify data redaction, row deletion, fixed vector dimension, write rejection and both query sources before any live contraction.
- [x] **Staged activation and rollback order.** Prepare and test candidate web and remote worker images, then drain old claims and terminate old searches. Reconcile/activate published AdaFace cohorts while old data still exists. Deploy compatible code before the guarded contraction; the canonical deploy migrates before replacing web, so irreversible schema/data contraction must not run in ordinary pre-activation `migrate`. After candidate web/worker read-back and a verified backup, execute bounded post-commit contraction. Stop on missing AdaFace evidence, old writers, new search failure or unverified backup. Recover forward after vector deletion.
- [x] **Supported bounded operational commands.** Provide dry-run and explicit execution modes for event reconciliation, old-work terminalization, JSON redaction, vector purge and schema contraction. Require a confirmation token, batch bounds, transaction-safe receipts and before/after aggregate counts; no biometric data or bearer URLs in output. Use existing job/lease commands where sufficient and avoid new adapters or permanent model switches.

## Implementation

Execute this plan through `$execute-implementation-plan`.

### Task 1: Database contraction and historical data preservation

**Files:** `src/backend/processing/models.py`, new `src/backend/processing/migrations/` migration, bounded management command under `src/backend/processing/management/commands/`, migration/data tests under `src/backend/processing/tests/`.

- **Specification:** Existing events and data; acceptance 1, 3, 4.
- **Depends on:** None for code; production execution depends on Tasks 2-3 and release safeguards.
- **Produces:** AdaFace-only model/512D schema and guarded, resumable SFace purge/redaction operation.

- [ ] Add failing tests for SFace rejection, AdaFace preservation, immutable-trigger handling, redacted historical payload with original hash, and unchanged saved-result references.
- [ ] Implement state/code schema and an operational contraction that never runs destructively during pre-activation migrations.
- [ ] Run focused migration/data tests via `make test TESTS="src/backend/processing/tests"` and inspect the resulting SQL/schema.

### Task 2: Processing and event cohorts use one current contract

**Files:** `src/backend/picflow/models.py`, `src/backend/processing/services/`, `src/backend/processing/management/commands/`, processing views/status and tests, and a picflow migration.

- **Specification:** One active recognition contract; Existing events and data.
- **Depends on:** Task 1's fixed model identity and data command interface.
- **Produces:** Fixed current face-processing generation and accepted cohort for search and offline corpus consumers; no SFace event selector, enrollment or activation path.

- [ ] Add failing tests for current photo processing, current cohort, missing current vector and unavailable empty event.
- [ ] Remove obsolete SFace and one-time backfill processing paths, model switches and commands without adding adapters or special filters.
- [ ] Run focused processing/picflow tests with `make test TESTS="src/backend/processing/tests src/backend/picflow/tests"`.

### Task 3: Selfie and gallery search use one current contract

**Files:** `src/backend/config/settings.py`, `src/backend/selfie_search/services/`, `src/backend/selfie_search/apps.py`, search views/admin/tests and obsolete search commands.

- **Specification:** One active recognition contract; Search, privacy and failure behavior.
- **Depends on:** Task 2's current processing generation and cohort interface.
- **Produces:** AdaFace-only query submission, worker configuration, exact ranking and optional cluster expansion; stored ready-result presentation remains independent of inference.

- [ ] Add failing tests for current selfie/gallery search, missing current vector, old ready result, worker callback rejection and unchanged media authorization.
- [ ] Remove SFace search defaults, dimensions, thresholds and runtime branches. Keep historical ready records inert and remove completed migration-only commands.
- [ ] Run `make test TESTS="src/backend/selfie_search/tests"`.

### Task 4: Worker and build become AdaFace-only

**Files:** `src/worker/photo_worker/`, `src/worker/tests/`, `Dockerfile.worker-base`, `pyproject.toml`, `experiments/face_recognition_spike/` executable code/tests and affected build checks.

- **Specification:** One active recognition contract; acceptance 2.
- **Depends on:** Task 3's pinned current callback and job contract.
- **Produces:** One SCRFD/AdaFace inference path and image with no SFace artifact or executable experiment code.

- [ ] Add failing worker contract/smoke tests for the AdaFace-only path and rejection of obsolete model payloads.
- [ ] Remove SFace/YuNet executable branches, old artifact fetch and old-model dependency extras; retain dependencies used by current paths.
- [ ] Run focused worker tests and build smoke, then `.venv/bin/pre-commit run --files <changed Python files>`.

### Task 5: Snapshot rehearsal, architecture reconciliation and final verification

**Files:** `docs/architecture.md`, relevant runbook, task-local rehearsal receipt and focused integration tests.

- **Specification:** Release and recovery contract; all acceptance criteria.
- **Depends on:** Tasks 1-4.
- **Produces:** Rehearsed upgrade, complete package evidence and updated implemented architecture.

- [ ] Rehearse the exact data transition against the existing local snapshot, including old ready links and fixed-vector schema checks. Do not touch production data.
- [ ] Reconcile delivered behavior with the specification and ADR 0054; update architecture only for implemented facts.
- [ ] Run selector/fingerprint, `make check`, all selector-required expensive suites, `make static`, `git diff --check` and active-code inventory. Record exact results and remaining live rollout gates.

## Verification

Use `scripts/select_test_suites.py` for final required suites and package fingerprint. Focused test commands appear in each task; `make check` and every selected expensive suite must be GREEN for the final package. Review the SQL type as `vector(512)`, current model constraint, immutable trigger, zero SFace data in the rehearsed database, and ready-result membership before declaring implementation complete.

## Operational impact and rollout

This code change is a release candidate, not a production mutation. Web and remote worker images must be compatible before any live data contraction. A guarded post-commit operation performs the irreversible SFace cleanup after current event activation and old-work drain. Read back web/worker build IDs, search latency/failures, processing completions, leases, vector counts, schema type and ready links. No new service, model threshold or feature flag is introduced.

## Rollback

Before vector deletion, keep the old data and recover with a compatible build. After deletion and 512D contraction, use the verified pre-transition backup with compatible images or repair forward; an old SFace-capable image is incompatible with the contracted schema. Never restore only code while leaving the data at the newer shape.

## Open questions

- None for code implementation. The fresh production inventory, backup verification and deployment authorization remain required release gates.
