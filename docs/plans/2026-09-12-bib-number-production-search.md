# Bib-number production search implementation plan

- Date: 2026-09-12
- Status: Approved by maintainer on 2026-09-12
- Owner: project maintainer
- Related specification: [Production bib recognition and event-scoped number search](../superpowers/specs/2026-09-12-bib-number-production-search-design.md)
- Related architecture: [Current architecture](../architecture.md#current-architecture--implemented), [Photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing), [Search](../architecture.md#search)
- Related ADRs: [0002](../adr/0002-postgresql-system-of-record.md), [0003](../adr/0003-docker-compose-yandex-cloud.md), [0017](../adr/0017-use-django-polled-photo-processing-jobs.md), [0019](../adr/0019-use-public-event-selfie-search.md), [0028](../adr/0028-operate-one-canonical-deployment.md), [0033](../adr/0033-keep-durable-knowledge-test-executable-contracts.md)
- ADR impact: Conforms to ADRs 0002, 0003, 0017, 0028, and 0033. ADR 0019 is public-search precedent only. No new or superseding ADR is required.

## Goal

Deliver the approved [outcome](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#1-outcome) as a release-ready Linux/CPU worker and event-scoped exact bib search, then prove the package with one local full-event cycle before any production event is enabled.

## Scope

Implements the approved [included and excluded scope](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#3-scope) without deltas. Deployment and the two production observation cohorts remain separately recorded operational milestones after the implementation PR is merged.

## Acceptance criteria

The implementation must satisfy all twelve [specification acceptance criteria](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#12-acceptance-criteria). Before deployment, the local Docker cycle must also produce a saved machine-readable comparison against the reviewed Istra baseline, a bounded event report, and measured container/host resource evidence for selecting the initial worker limit.

## Worker/state/artifact release safeguards

- [x] **Live-state inventory.** Read-only inventory on 2026-09-12 found canonical VM `epdr5g3p24tdns9890nr` (`dev-photo-prjct`, `standard-v3`, 4 vCPU, 16 GiB RAM, 100 GiB network SSD, no swap), deployed application SHA `0388614d9ddc4653cf058d0dcfe7dc8a9abe0a1e`, and two healthy 1 CPU/2 GiB worker replicas. Configured identities were `1/capture_metadata/2,2/generate_preview/1,2/generate_watermarked_preview/1,2/face_embedding/3,3/face_embedding/5,1/selfie_query/2`; configured types were `selfie_query,face_embedding,capture_metadata,generate_preview`. No bib identity, rows, active leases, or expired live leases existed. Durable states were capture metadata 48,447 succeeded/139 failed, face embedding 48,592 succeeded, face benchmark 114 succeeded, preview 48,583 succeeded/3 cancelled, and watermarked preview 4 succeeded. Object Storage held 48,586 `preview-small-v1` plus 4 `preview-watermarked-v1` derivatives under `derivatives/`; bib reads `originals/` and creates no storage artifact. Historical failed/expired/stale attempts remain governed by the existing processing contract.
- [x] **Compatibility matrix.** Existing events migrate with bib disabled and existing photos with policy `disabled`; their jobs, attempts, derivatives, and visibility remain readable and are neither retried nor backfilled. New Django with an old worker is deployable only while every event checkbox remains off; enabling an event is forbidden until the new worker advertises the exact bib identity. The new worker continues to support all old configured identities and receives no bib job from old Django. New Django plus new worker supports the full path. Application rollback first disables event enrollment and removes the bib identity or restores the previous image; additive columns/tables remain in place, old code ignores them, and bib attempts/projections are retained rather than purged or schema-reversed.
- [x] **Reviewed data-state migration or reset semantics.** Use additive schema migrations only. Existing events receive `bib_search_enabled=false`; existing and never-enrolled photos receive immutable `bib_processing_policy=disabled`; the new current projection starts empty. Preserve every existing processing row and derivative. Do not reset, purge, reconcile, or backfill old data. New bib attempts and projections remain durable across application rollback.
- [x] **End-to-end contract sizing.** The representative maximum is a 120 KiB serialized bib attempt result within the existing 128 KiB attempt-result bound and a 128 KiB worker completion HTTP envelope. Exercise worker serialization, HTTP client, Nginx/Django's configured 393,216-byte request limit, completion parsing, deterministic validation, and PostgreSQL persistence at the exact boundary; reject the same contract at one byte over its declared bound without partial projection updates.
- [x] **Previous-snapshot upgrade rehearsal.** A migration-executor test creates the previous schema with successful, failed, retryable, stale, terminal, active-lease, and expired-lease processing rows; published derivatives; and never-enrolled photos. Forward migration must preserve every row/status/lease/artifact reference, initialize every event/photo as disabled, and permit new bib state/projection rows. A previous-application model probe must continue reading existing data after new rows are added, without reversing the schema.
- [x] **Staged activation and rollback order.** Build and verify the Linux image; run the Istra local full cycle and quality/resource gate; deploy with all event checkboxes off; verify image digest, migrations, model hashes, and advertised identity; set `PHOTO_WORKER_REPLICAS=1` plus the measured CPU/memory limits; enable and upload only Istra; record the specified cohort; tune only through a new pinned processor generation if required; then repeat with Gagarin. Stop on any quality, publication, privacy, lease, OOM/restart, host-headroom, CPU/iowait, health, or latency gate from the specification. Rollback disables affected events, stops new bib enrollment, removes the bib identity or restores the prior application/worker image, and preserves rows and attempts.
- [x] **Supported bounded operational commands.** Add `report_event_bib_processing --event-slug <exact-slug>` as a read-only aggregate report for exactly one event. Add a local-only acceptance command/script bounded to one event/source root and one baseline manifest, writing only a named local artifact. Do not ship generic bib requeue, backfill, retry, or purge commands; automatic retries remain code-owned. Existing database inspection and deployment commands remain read-only unless their existing confirmation contract says otherwise.

Rationale: [2026-07-31 staging processing-state reset postmortem](../postmortems/2026-07-31-staging-processing-state-reset.md).

## Implementation

Execute this plan with `$execute-implementation-plan`.

### Task 1: Persist event applicability and searchable bib projections

**Files:** `src/backend/picflow/models.py`, `src/backend/picflow/admin.py`, `src/backend/picflow/migrations/0015_bib_search_policy.py`, `src/backend/picflow/tests/test_admin.py`, `src/backend/processing/models.py`, `src/backend/processing/migrations/0009_bib_reading_projection.py`, `src/backend/processing/tests/test_models.py`, `tests/processing/test_bib_upgrade.py`.

- **Specification:** [Event and photo applicability](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#4-event-and-photo-applicability), [Search projection and query](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#8-search-projection-and-query).
- **Depends on:** None.
- **Produces:** `Event.bib_search_enabled`, immutable `Photo.bib_processing_policy`, and an indexed current `BibReading` projection linked by `PROTECT` to its source attempt.

- [ ] Add failing model/admin tests for disabled defaults, event-checkbox editing, immutable per-photo policy, exact digit storage with leading zeros, per-photo/number uniqueness, and source-attempt retention.
- [ ] Add the previous-snapshot migration rehearsal described in the safeguard section and confirm it fails before the migrations exist.
- [ ] Implement the two additive migrations and minimal admin fields; add database constraints that keep numbers ASCII-only and 1–16 characters.
- [ ] Run `make test TESTS="picflow.tests.test_admin processing.tests.test_models tests.processing.test_bib_upgrade"`; expect all selected tests to pass and no migration data mutation outside the new defaults.

### Task 2: Add the Django bib processor contract, validation, enrollment, projection, and reporting

**Files:** `src/backend/processing/contracts.py`, `src/backend/processing/bib_validation.py`, `src/backend/processing/services/bibs.py`, `src/backend/processing/services/enrollment.py`, `src/backend/processing/services/previews.py`, `src/backend/processing/views.py`, `src/backend/processing/management/commands/report_event_bib_processing.py`, `src/backend/processing/tests/test_bib_processing.py`, `src/backend/processing/tests/test_enrollment.py`, `src/backend/processing/tests/test_previews.py`, `src/backend/processing/tests/test_reports.py`, `src/backend/processing/tests/test_views.py`, `tests/processing/test_bib_pipeline_e2e.py`, `tests/processing/test_worker_api_contract.py`.

- **Specification:** [Publication and enrollment flow](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#5-publication-and-enrollment-flow), [Recognition and validation contract](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#7-recognition-and-validation-contract), [Failure evidence and statistics](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#9-failure-evidence-and-statistics).
- **Depends on:** Task 1 schema.
- **Produces:** immutable bib processor/configuration generation `1`, bounded callback validation, idempotent post-publication enrollment, atomic accepted projection replacement, and a one-event aggregate report.

- [ ] Adapt the typed local experiment contract and exact validator from commit `4102ab0`; add failing tests for accepted/rejected/uncertain candidates, telephone/sign/body false-positive fixtures, ASCII/length bounds, empty success, retryable and terminal errors, stale/idempotent completion, and atomic projection replacement.
- [ ] Add failing flow tests proving confirmation snapshots event state, later checkbox changes do nothing, publication precedes bib work, a bib enqueue/completion failure never unpublishes a valid photo, and reconciliation acts only on persisted eligible policy.
- [ ] Add exact-boundary tests for a 120 KiB attempt result in a 128 KiB completion envelope through API parsing and persistence, plus one-byte-over rejection with no partial state.
- [ ] Implement the smallest contract/service/view changes and bounded aggregate report. Keep retryability code-owned and expose no manual retry/backfill/purge path.
- [ ] Run `make test TESTS="processing.tests.test_bib_processing processing.tests.test_enrollment processing.tests.test_previews processing.tests.test_reports processing.tests.test_views tests.processing.test_bib_pipeline_e2e tests.processing.test_worker_api_contract"`; expect all bib and existing preview/face publication invariants to pass.

### Task 3: Package and execute the pinned Linux/CPU bib runtime

**Files:** `Dockerfile.worker`, `src/worker/requirements.txt`, `src/worker/photo_worker/runtime_contract.py`, `src/worker/photo_worker/model_smoke.py`, `src/worker/photo_worker/contracts.py`, `src/worker/photo_worker/client.py`, `src/worker/photo_worker/runner.py`, `src/worker/photo_worker/bib_recognition.py`, `src/worker/photo_worker/bib_visual.py`, `src/worker/photo_worker/bib_execution.py`, `src/worker/tests/test_bib_contract.py`, `src/worker/tests/test_bib_recognition.py`, `src/worker/tests/test_bib_execution.py`, `tests/processing/test_worker_container_contract.py`.

- **Specification:** [Sequential worker and Linux inference runtime](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#6-sequential-worker-and-linux-inference-runtime), [Recognition and validation contract](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#7-recognition-and-validation-contract), [Capacity and activation gates](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#11-capacity-and-activation-gates).
- **Depends on:** Task 2 worker contract.
- **Produces:** one-job-at-a-time Linux executor using original-image tiling, RapidOCR 3.9.2, ONNX Runtime 1.29.0, and a loopback-only per-photo `llama-server` child/process tree for sequential crop reads.

- [ ] Adapt bounded recognition/execution code and tests from commit `4102ab0`, replacing MLX with a process-isolated llama.cpp adapter. Add failing tests for exact command/config identity, `127.0.0.1` binding, parallelism one, bounded output, deadlines, lease loss, child-tree termination, and process exit after each photo.
- [ ] Pin llama.cpp commit `5266f24da75dc449bd56cbed7addb9c8e4a6a73e` using tarball SHA256 `2de0d87eda4696e9f6bbd771d4c623267f4e95856cce6f99793f91522f993e43`, build with `GGML_NATIVE=OFF`, and copy only required runtime files. Pin Qwen artifact revision `d38d39f5972e27cd58023f9b1e9f994b0c85ca47`, Q4 model SHA256 `089d75c52f4b7ffc56ba998ffc50aae89fcafc755f9e7208aacca281dca6c2ae`, and Q8 projector SHA256 `f9a68fabba69c3b81e153367b2c7521030b0fa8bb0de400c9599c8e6725f9c82`. Runtime downloads and fallback runtimes are forbidden.
- [ ] Upgrade the shared ONNX Runtime only after the current face model smoke and face unit tests prove compatibility. Raise the packaged site-packages/models budget from 2 GiB to 4 GiB and keep its assertion message/test exact.
- [ ] Make the non-root image build run offline runtime/model smoke for preview, current SFace/AdaFace, and bib identities. Verify the runner still has total concurrency one.
- [ ] Run `make test TESTS="src/worker/tests/test_bib_contract.py src/worker/tests/test_bib_recognition.py src/worker/tests/test_bib_execution.py src/worker/tests/test_face_embedding.py tests/processing/test_worker_container_contract.py"`; expect all tests to pass.
- [ ] Run `docker build -f Dockerfile.worker -t findme-photo-worker:bib-local .`; expect checksum verification, non-root model smoke, and runtime budget checks to pass without runtime model downloads.

### Task 4: Add exact event-scoped bib search as an independent public form

**Files:** `src/backend/picflow/forms.py`, `src/backend/picflow/gallery.py`, `src/backend/config/views.py`, `src/backend/templates/catalog/event_detail.html`, `src/backend/static/ui/selfie-search.css`, `src/backend/picflow/tests/test_views.py`, `src/backend/picflow/tests/test_gallery.py`, `tests/visual/visual.spec.js`, visual baselines selected by the repository visual suite.

- **Specification:** [Search projection and query](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#8-search-projection-and-query).
- **Depends on:** Task 1 projection.
- **Produces:** `?bib=<digits>` exact filtering over the ordinary eligible event queryset and a separate GET form whose visibility follows the event checkbox directly.

- [ ] Add failing view/gallery tests for hidden form while disabled, visible separate form while enabled, trimmed ASCII validation, leading-zero distinction, exact event and eligibility isolation, invalid-query error, empty unfiltered query, and `bib` preservation across numbered pages and other gallery filters.
- [ ] Add a failing visual scenario for desktop side-by-side forms and narrow-screen stacking; ensure neither form contains the other's input.
- [ ] Implement the form/query and minimal responsive styling, reusing the existing gallery paginator and `gallery_photo_queryset` authorization boundary.
- [ ] Run `make test TESTS="picflow.tests.test_views picflow.tests.test_gallery"`; expect all search/filter/pagination cases to pass.
- [ ] Run the visual target selected by `scripts/select_test_suites.py`; expect approved desktop and mobile snapshots with separate forms.

### Task 5: Wire deployment resources and build a one-event local acceptance cycle

**Files:** `docker-compose.deployment.yml`, `.github/workflows/deploy.yml`, `deploy/apply-deployment.sh`, `deploy/run-remote.sh`, `deploy/cutover-compose-identity.sh`, `scripts/local-bib-acceptance.sh`, `experiments/bib_search/worker_acceptance/baseline_manifest.json`, `experiments/bib_search/worker_report.py`, `experiments/bib_search/README.md`, `tests/deployment/test_bib_deployment_contract.py`, `tests/deployment/test_bib_local_acceptance.py`.

- **Specification:** [Release and observation sequence](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#10-release-and-observation-sequence), [Capacity and activation gates](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#11-capacity-and-activation-gates).
- **Depends on:** Tasks 2–4.
- **Produces:** deployment variables `PHOTO_WORKER_CPUS` and `PHOTO_WORKER_MEMORY_LIMIT`, exact bib identity forwarding, and a reproducible one-event Docker acceptance artifact.

- [ ] Add failing deployment tests for variable validation/forwarding/preservation and for one worker replica during bib activation. Defaults must preserve the current non-bib 1 CPU/2 GiB deployment.
- [ ] Add the reviewed Istra baseline manifest from the predecessor branch, preserving the exclusion of `00011_Vlad.jpg · 67`, and add failing acceptance-report tests for missing confirmed numbers, returned rejected junk, publication regressions, event leakage, leading-zero loss, malformed evidence, and absent resource metrics.
- [ ] Implement a bounded local script that creates one event with bib enabled, uploads its source directory through normal APIs, waits for preview/face/bib terminal states, exercises public exact search, emits aggregate/report/comparison JSON, and exits nonzero on the local quality or capacity gates.
- [ ] Select the initial worker memory limit as `ceil(peak RSS / 0.70)` rounded up to 512 MiB, capped at 6 GiB; block activation if the calculated limit exceeds 6 GiB. Record peak RSS, CPU, lease duration, event wall time, p50/p95, restarts/OOM, and host headroom. Use one worker replica and initially 2 CPU only if the measured local gate passes.
- [ ] Run `make test TESTS="tests/deployment/test_bib_deployment_contract.py tests/deployment/test_bib_local_acceptance.py"`; expect all deployment and artifact-boundary tests to pass.
- [ ] Run `scripts/local-bib-acceptance.sh --event-slug local-istra-bib --source-root '/Users/petrnikitin/Documents/Projects/findme-photo-images/bibs/2026-04-19_Истра' --baseline experiments/bib_search/worker_acceptance/baseline_manifest.json --output var/bib-search/production-istra-001`; expect a GREEN comparison with all required state and resource evidence. Keep the generated `var/` artifact ignored and link it from the implementation report.

### Task 6: Reconcile documentation and produce the production activation runbook

**Files:** `docs/architecture.md`, `docs/product-jobs.md`, `docs/engineering-jobs.md`, `docs/runbooks/bib-number-recognition.md`.

- **Specification:** [Release and observation sequence](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#10-release-and-observation-sequence), [Capacity and activation gates](../superpowers/specs/2026-09-12-bib-number-production-search-design.md#11-capacity-and-activation-gates).
- **Depends on:** Tasks 1–5 and the actual local acceptance evidence.
- **Produces:** current architecture/job status plus exact deploy, inspect, cohort, stop, and rollback commands for the canonical VM.

- [ ] Update architecture with the shipped worker identity, event/photo applicability snapshot, projection, sequential child-process boundary, search path, and failure semantics.
- [ ] Mark PJ-007 and its engineering capability as implemented locally/release-ready while keeping production activation and live evidence explicitly incomplete.
- [ ] Write the runbook with all events disabled at deploy, exact image/migration/hash/identity checks, `PHOTO_WORKER_REPLICAS=1`, the locally selected CPU/memory values, one exact event report, host/container observations, first-event stop conditions, rollback order, and the second-event prerequisite. It must contain no automatic backfill/requeue/purge step.
- [ ] Run `make test TESTS="tests/test_repository_foundation.py"`; expect documentation links and repository contracts to pass.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the approved specification, ADRs 0002, 0003, 0017, 0019, 0028, 0033, and `docs/architecture.md`.
- [ ] Confirm the implementation remains one PostgreSQL control plane, one canonical Compose deployment, one private polling worker, event-scoped public search, and executable durable contracts.
- [ ] Update implemented architecture facts after the local full-cycle evidence exists.
- [ ] Stop for a decision instead of contradicting an accepted ADR; supersede rather than edit it.
- [ ] Record `Conforms; no new ADR` in the pull request unless implementation evidence changes the boundary.

## Verification

Focused RED/GREEN commands are listed per task. For the final package:

1. Run `.venv/bin/python scripts/select_test_suites.py select --base 507ea552 --head working-tree` and record every selected suite and reason.
2. Run `.venv/bin/python scripts/select_test_suites.py fingerprint --base 507ea552 --head working-tree` and retain the exact final-package fingerprint.
3. Run `.venv/bin/pre-commit run --files <all changed Python files>`; expect Ruff fixes/format and full-project mypy to pass after the last Python change.
4. Run `make check`; expect formatting, lint, mypy, Django checks, migration drift checks, and the complete Python test suite to pass.
5. Run every expensive target selected by step 1, including the CI-owned visual commands and worker Docker build when selected; expect GREEN evidence for the exact step-2 fingerprint.
6. Run the Istra local Docker command in Task 5; expect the quality comparison and capacity gates to pass and produce a complete saved artifact.

## Operational impact and rollout

The release adds two additive migrations, enlarges the worker image by approximately 1.55 GiB of pinned visual-model artifacts plus llama.cpp/RapidOCR runtime, and adds deploy-time worker CPU/memory variables. Deployment remains safe while every event checkbox is off. Activation must use the exact order in the safeguards and runbook: deploy disabled, verify package and identity, set one worker replica and measured limits, then enable and upload one bounded event. Production activation, first-event upload, tuning, and second-event upload are distinct recorded milestones after merge.

## Rollback

Disable bib on affected events to stop new enrollment and hide the public field, then remove the bib processor identity or deploy the previous application/worker image. Keep the additive schema, per-photo applicability snapshots, bib jobs, immutable attempts, failure evidence, and current projections. Existing published photos stay published. Do not purge or reprocess data during rollback. Restore two 1 CPU/2 GiB worker replicas only after the bib identity is absent and verify the old face/preview identities and public health.

## Open questions

None.
