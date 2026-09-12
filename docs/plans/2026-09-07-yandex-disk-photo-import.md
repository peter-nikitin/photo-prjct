# Public Yandex Disk Photo Import Implementation Plan

- Date: 2026-09-07
- Status: Implementation present; local acceptance recorded. Deployment and activation have not started.
- Execution evidence: [Container and upgrade acceptance](2026-09-07-yandex-disk-import-acceptance.md),
  [operational runbook](../runbooks/yandex-disk-photo-import.md). The task checklist below is the
  original execution procedure; delivery distinguishes local verification from live activation.
- Owner: project maintainer
- Related specification: [Approved import design](../superpowers/specs/2026-09-07-yandex-disk-photo-import-design.md)
- Related architecture: [Photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing)
- Related ADRs: [0035](../adr/0035-use-django-polled-yandex-disk-import.md),
  [0013](../adr/0013-use-direct-private-object-storage-ingestion.md),
  [0014](../adr/0014-keep-stage-2-ingestion-request-driven.md),
  [0017](../adr/0017-use-django-polled-photo-processing-jobs.md),
  [0028](../adr/0028-operate-one-canonical-deployment.md),
  [0032](../adr/0032-reconcile-code-owned-feature-flags-at-startup.md).
- ADR impact: Conforms to accepted ADR 0035. Its narrow supersession of ADRs 0013/0014 is
  recorded in both records; no further architecture choice is delegated to the implementer.

Execute through `$execute-implementation-plan`. The current isolated workspace is
`.worktrees/yandex-disk-import-spec`, branch `codex/yandex-disk-import-spec`, based on `899e9cc`.
Tasks below are sequential because they share the original-publication and import-state contract.

## Goal

Deliver the [approved result](../superpowers/specs/2026-09-07-yandex-disk-photo-import-design.md#результат-и-границы)
as a complete, default-off feature, including its real worker and standard processing enrollment.

## Scope

Implement the approved specification without scope changes. Source selection, direct children,
limits, exact customer copy, deduplication scope, failure behavior, and exclusions remain in that
document. This plan supplies ownership, dependency order, interfaces, verification, and release
controls. Writing this plan does not execute migrations or authorize deployed changes.

## Acceptance criteria

All eleven [specification criteria](../superpowers/specs/2026-09-07-yandex-disk-photo-import-design.md#критерии-приёмки)
must map to evidence from Tasks 1–6. Delivery additionally requires final-package selected-suite
evidence, previous-version preservation evidence, and separate reporting of local, CI, deployment,
and live-activation results. A mocked source proves orchestration only; real Disk acceptance remains
an explicit later check.

## Worker/state/artifact release safeguards

The outcomes below are selected, not claims that future implementation checks have already run.
Any observed contradiction blocks the affected task or release until resolved; missing release
evidence cannot be converted into an assumed GREEN result.

- **Live-state inventory.** [Read-only baseline](2026-09-07-yandex-disk-import-inventory.md):
  deployed `899e9cc`, two photo workers, no import models/worker, no active processing leases,
  and preserved older successful/failed/expired processing state and derivatives. Task 6 supplies
  `inspect_photo_imports --limit 100`; refresh it and existing processing aggregates before release.
  Existing `incoming/` and `originals/` key shapes remain in use. No storage relocation is planned.
- **Compatibility matrix.** Old web + old photo worker: unchanged. New web + old photo worker:
  supported, with the existing processing protocol and identities. New web + new import worker:
  supported only for import API v1 after schema migration and explicit runtime enablement.
  Old web + new import worker: unsupported; deployment must stop import first, and an unexpected
  404/401/unsupported contract must never fall back to another API or publish anything.
  Old web with retained new import tables: ignores them and continues browser/processing work;
  import is stopped. There are no old import rows to translate and no compatibility shim.
- **Reviewed data-state migration or reset semantics.** Add import-owned tables/indexes only.
  Keep all existing UploadBatch, UploadItem, Photo, processing rows, and derivative identities.
  No data migration, backfill, re-enrollment, source-key rewrite, or purge. Import attempts persist
  recovery checkpoints; cleanup respects leases and Photo references. Schema reversal is not
  the rollback mechanism. Task 2 verifies the migration against this preservation contract.
- **End-to-end contract sizing.** Task 3 fixes import API v1 at at most 100 manifest items per
  callback and 1 MiB serialized JSON per request/response; a 10,000-entry manifest is never one
  callback. Reject oversize input before state changes. Task 6 exercises worst-case escaped Unicode
  names, maximum valid metadata and one 52,428,800-byte JPEG through the real client, proxy,
  Django parser, validation, and PostgreSQL. The JPEG body goes directly to S3. Verify the complete
  envelope, including error/progress responses, and transfer deadlines/lease renewal together.
- **Previous-snapshot upgrade rehearsal.** Task 6 starts from a disposable `899e9cc` schema with
  successful, failed, retryable, stale, active/expired-lease, terminal and never-enrolled fixtures
  plus derivative references. Apply only new migrations; compare identifiers, states, and keys;
  run browser confirmation and photo-processing contracts against the upgraded database. Then
  create interrupted import work, close its gate, stop its worker, and run old application paths
  against retained tables. Expected outcome: preserved existing data and recoverable import rows.
  Synthetic fixtures avoid requiring a production DB copy for the rehearsal.
- **Staged activation and rollback order.** Task 6 validates one canonical deployment: optional
  credential/config delivery → schema/new web with gate off and import worker stopped → bounded
  local contract and deployment checks → explicitly enabled worker with gate off → staff-only
  small cohort → verified transfer/processing/retry → explicit on. Abort on publication duplicates,
  privacy failure, worker protocol mismatch, preservation failure, or resource exhaustion. Rollback
  closes the gate, stops import, waits out its leases, and preserves rows/objects before reverting
  application images. Existing photo workers are not reset or reconfigured for import.
- **Supported bounded operational commands.** Task 6 provides `inspect_photo_imports --limit 100`
  and optional `--batch <uuid>` for read-only sanitized state, plus
  `cleanup_stale_imports --limit 100` (dry-run) and `--apply` for reviewed deletion of unreferenced
  expired attempt objects only. The owner UI provides bounded failed-item retry; no broad requeue,
  backfill, purge, or reset command is introduced. The cleanup command caps limits at 1,000,
  excludes live leases and referenced originals, and reports counts without source URLs.

Rationale: [processing-state reset postmortem](../postmortems/2026-07-31-staging-processing-state-reset.md).

## Implementation

### Task 1: Share verified-original publication without changing browser uploads

**Files:** modify `src/backend/ingestion/services/confirmation.py`; create
`src/backend/ingestion/services/publication.py` and `src/backend/ingestion/tests/test_publication.py`;
extend `src/backend/ingestion/tests/test_confirmation.py`.

- **Specification:** “Компоненты и поток данных”, standard publication and processing enrollment.
- **Depends on:** accepted specification and ADR 0035.
- **Produces:** a typed `VerifiedOriginal` value for a verified immutable key, object identity,
  filename/size and optional oriented geometry; a shared `publish_photo` operation consuming
  that value, explicit photo ID, uploader, event and folder, within the caller's transaction.
  Caller owns item locks/checkpoints; shared publication owns Photo policy and enrollment.

- [ ] Add regression tests for existing confirmation checkpoints, folder assignment and free/paid
  policy; add a failing contract test for the extracted shared operation.
- [ ] Run `make test TESTS="src/backend/ingestion/tests/test_confirmation.py src/backend/ingestion/tests/test_publication.py"`.
  Confirm the new contract fails for the intended missing seam, not database setup.
- [ ] Extract verified-object checks and publication/enrollment without copying them into a second
  pipeline. Retain conditional storage operations outside long database transactions and retain
  browser item/ownership handling in its existing service. Do not change processor identities.
- [ ] Repeat the focused command; expected GREEN for existing browser recovery and shared rules.

### Task 2: Persist import lifecycle and idempotent publication

**Files:** extend `src/backend/ingestion/models.py`; create
`src/backend/ingestion/migrations/0004_photo_import.py`,
`src/backend/ingestion/services/imports.py`,
`src/backend/ingestion/services/import_publication.py`,
`src/backend/ingestion/tests/test_import_models.py`,
`src/backend/ingestion/tests/test_import_services.py`,
`src/backend/ingestion/tests/test_import_publication.py`,
`src/backend/ingestion/tests/test_import_migrations.py`; extend
`src/backend/feature_flags/registry.py` with the default-off import definition.

- **Specification:** “Файлы и состав импорта”, “Повторная загрузка и идентичность”,
  “Прогресс, ошибки и восстановление”, “Доступ, хранение и включение”.
- **Depends on:** Task 1.
- **Produces:** import-only state and services `create_import`, `record_manifest_page`,
  `finish_manifest`, `claim_import_work`, `renew_import_lease`, `prepare_import_upload`,
  `complete_import_item`, `record_import_failure`, and `retry_import_errors`.
  Inputs identify batch/item/attempt and current actor; results are typed state, never raw URLs
  for browser presentation. No service calls the source network inside a database transaction.

- [ ] Write failing tests for one claimed import, manifest pagination/replay, empty/all-skipped
  batches, content duplicates, retries, lost completion responses and lease expiry.
- [ ] Run `make test TESTS="src/backend/ingestion/tests/test_import_models.py src/backend/ingestion/tests/test_import_services.py src/backend/ingestion/tests/test_import_publication.py"`.
- [ ] Add protected event/folder/owner relationships; a canonical import scope identifies the
  approved owner/source/event/folder tuple. Use PostgreSQL uniqueness including `NULL` folder
  semantics and scope locking to serialize competing publication. Persist batch/item/attempt
  state, explicit operation retry counts, received manifest pages and object checkpoints.
  A canonical scope is resolved before file claims; submission itself needs no Disk API call.
- [ ] Introduce the import gate definition with the services that own its side effects. Tests
  explicitly activate it for authorized actors and prove pause/resume independently of HTTP.
- [ ] Reuse Task 1 and `PrivateUploadStorage`; import owns its incoming/final IDs and checkpoints.
  Do not fabricate browser `UploadItem` rows or make its expected-item count represent duplicates.
  Retain one content winner per scope; revalidate current lease, gate and owner before committing
  Photo plus import result. A stale attempt's uploaded bytes cannot become authoritative.
- [ ] Bound enumeration restarts; unsuccessful enumeration never transfers partial manifests.
  Separate retries for source operations from transport retries of idempotent Django callbacks.
  Enforce the specification's four-attempt limit without multiplying nested retry loops.
- [ ] Repeat focused tests and `make test-migrations`; expect preserved old schema data, no
  duplicate Photo or enrollment under concurrent submissions, and no hidden global deduplication.

### Task 3: Expose bounded private worker and owned browser APIs

**Files:** create `src/backend/ingestion/import_contracts.py`, `import_auth.py`,
`import_worker_views.py`, `import_worker_urls.py`, `import_views.py`,
`tests/test_import_api.py`, `tests/test_import_permissions.py` under the ingestion package;
modify its `urls.py`, `src/backend/config/urls.py`, `src/backend/config/settings.py`,
`.env.example` and relevant feature-flag tests.

- **Specification:** “Сценарий на странице загрузки”, access, gate and worker boundaries.
- **Depends on:** Task 2.
- **Produces:** private `/internal/photo-import/v1/` JSON endpoints for claim, manifest page/finalize,
  lease renewal, prepare-upload, complete and failure; owned `/photographer/uploads/imports/`
  create/list and per-batch detail/items/retry routes, using the existing photographer URL prefix.
  Freeze endpoint names and response dataclasses in `import_contracts.py` before Task 4.

- [ ] Add failing permission/contract tests for photographer vs staff, cross-owner/cross-event
  denial, duplicate submission keys, unknown versions, stale lease and redacted errors.
- [ ] Run `make test TESTS="src/backend/ingestion/tests/test_import_api.py src/backend/ingestion/tests/test_import_permissions.py"`.
- [ ] Implement strict bounded parsing and pagination, session/CSRF browser mutations, dedicated
  constant-time worker-token validation and read-only progress. Worker authentication must not
  use the photo-worker token or impersonate browser sessions.
- [ ] Wire Task 2's `yandex-disk-import` definition into authoritative endpoints. Keep the
  separate deploy capability `PHOTO_IMPORT_ENABLED=False` and optional
  `PHOTO_IMPORT_WORKER_TOKEN` fail-closed. Check actor eligibility at each authoritative mutation;
  an inactive gate pauses work rather than exhausting file retries. Preserve status read access
  under the specification's current-permission rule.
- [ ] Reject oversized JSON before processing or logging it. The whole response, including
  credentials in private grants, must fit the Task 3 envelope. Source keys/download URLs remain
  excluded from browser responses and ordinary diagnostics.
- [ ] Repeat focused tests and `make test TESTS="src/backend/feature_flags src/backend/ingestion/tests/test_permissions.py"`.
  Expect normal browser paths unaffected and all import side effects gated independently.

### Task 4: Implement the standalone import runtime and one-file vertical slice

**Files:** create `src/import_worker/import_worker/{__init__,__main__,contracts,client,source,transport,runner}.py`,
`src/import_worker/tests/{test_contracts,test_client,test_source,test_transport,test_runner}.py`,
`src/import_worker/requirements.txt`, `Dockerfile.import-worker`;
create `src/backend/ingestion/tests/test_import_flow.py` for the one-file vertical slice;
extend `pyproject.toml`, development dependency inputs as needed and local Compose in Task 6.

- **Specification:** “Контракт Яндекс Диска”, file validation, worker flow, retry/recovery.
- **Depends on:** Task 3's frozen API and Task 1's shared publication.
- **Produces:** independently runnable `python -m import_worker`, with private API client,
  public Disk source adapter and isolated outbound transport; no imports from Django/ML runtimes.
  Add its paths to pytest, mypy and coverage discovery so root checks actually include it.

- [ ] Build source fixtures for multiple pages, Unicode names, direct folders, duplicate content,
  missing SHA-256, changed bytes, invalid JPEG and throttling. Use real JPEG bytes in transfer tests.
- [ ] Run `make test TESTS="src/import_worker/tests"`; confirm focused RED failures.
- [ ] Implement enumeration → persisted manifest → current file claim → fresh source URL →
  bounded temp file/hash/JPEG validation → prepare-upload → direct S3 POST → authoritative
  completion. Use the existing pinned Pillow version and standard HTTP/TLS facilities; do not
  include ONNX/face models or DB/storage credentials in this runtime.
- [ ] Keep API authentication exclusive to the configured internal API. Public-source and S3
  requests never inherit its token, cookies, ambient proxy configuration or automatic redirects.
  Enforce allowed source/download hosts and public destination addresses at connection time,
  preserving TLS hostname verification; a DNS check followed by an unchecked re-resolution fails
  acceptance. Validate every redirect and test rebinding/private-address denial explicitly.
- [ ] Verify actual supported downloader host/redirects through the official API before freezing
  the allowlist; do not ship guessed hosts or a wildcard public-URL fallback. This source evidence
  is required before treating the adapter as complete, and a missing real JPEG blocks live smoke.
- [ ] Choose bounded transport timeouts and lease heartbeat intervals together; serialize them
  in the v1 contract/config tests. Losing renewal stops side effects. Enforce whole-response/time
  limits, `Retry-After`, byte caps and exactly one temp JPEG, including process-restart cleanup.
- [ ] Repeat worker tests, then run
  `make test TESTS="src/backend/ingestion/tests/test_import_api.py src/backend/ingestion/tests/test_import_publication.py src/backend/ingestion/tests/test_import_flow.py"`.
  Expected: a fresh file reaches the shared publication path after browser disconnection.

### Task 5: Connect the existing upload page to server-owned progress

**Files:** modify `src/backend/templates/ingestion/upload.html`,
`src/backend/static/ui/upload.css`, `src/backend/ingestion/views.py`;
create `src/backend/static/ui/import-coordinator.js`, `tests/js/import-coordinator.test.js`,
`src/backend/ingestion/tests/test_import_templates.py`;
extend `tests/visual/views.py`, `tests/visual/visual.spec.js` and relevant snapshots;
update `.agents/skills/update-visual-design/references/screen-inventory.md` when entries change.

- **Specification:** “Сценарий на странице загрузки”, progress and exact alerts/messages.
- **Depends on:** real browser API from Task 3 and working vertical slice from Task 4.
- **Produces:** an event-folder source-link control and paginated durable import cards on the
  production upload page. Use `$update-visual-design`; no prototype routes or fake mutations.

- [ ] Add failing JS/template tests for event/folder selection, one-link submission, lost-response
  retry, exact subfolder warning, changed event, gate-off state, progress and errors after reload.
- [ ] Run `npm run test:js` and
  `make test TESTS="src/backend/ingestion/tests/test_import_templates.py src/backend/ingestion/tests/test_templates.py"`.
- [ ] Connect the form and bounded polling; stop polling on page exit and terminal state, without
  stopping server work. Show enumeration warnings after discovery and static direct-child guidance
  before submission. Preserve the local file coordinator and its independently owned queue.
- [ ] Link processing status to existing state rather than copy its business rules. Cover empty,
  duplicates-only, active, partial, paused and completed imports at mobile/desktop widths.
- [ ] Run `npm run test:visual:update` for intentional snapshots; inspect every changed image,
  then `npm run test:visual` and the focused JS/template checks. Expected: keyboard-accessible
  controls, bounded rendering at 10,000 items and no regression in local-upload folder snapshots.

### Task 6: Package deployment, recovery and upgrade acceptance

**Files:** modify `docker-compose.yml`, `docker-compose.deployment.yml`,
`docker-compose.https.yml` only where needed, `.github/workflows/deploy.yml`,
`deploy/apply-deployment.sh`, `deploy/environment-secrets.json`, `deploy/run-remote.sh`,
`deploy/nginx/https.conf.template`, environment projection scripts/tests;
create `src/backend/ingestion/management/commands/{inspect_photo_imports,cleanup_stale_imports}.py`,
`src/backend/ingestion/tests/{test_import_cleanup,test_import_upgrade}.py`;
extend `src/backend/ingestion/tests/test_import_flow.py`; create
`tests/deployment/test_import_deployment.py`, `tests/processing/test_import_worker_container_contract.py`,
`tests/suite-selection.toml` entries for new operational/migration/product-flow tests,
`docs/runbooks/yandex-disk-photo-import.md` and contract-sizing/rehearsal evidence.

- **Specification:** recovery, cleanup, current permission/gate checks, private execution.
- **Depends on:** Tasks 1–5.
- **Produces:** a matching release-tagged import image, an opt-in `import` Compose profile,
  supported bounded commands, preserved previous-version data and executable acceptance reports.

- [ ] Add failing container/projection/deploy tests for disabled import, missing token while
  enabled, startup without reconciliation, protocol readiness, worker stop and image rollback.
  New manifest patterns must actually classify the new tests; do not merely add marker names.
- [ ] Run `make test-operational`, `make test-migrations` and
  `make test TESTS="src/backend/ingestion/tests/test_import_cleanup.py src/backend/ingestion/tests/test_import_upgrade.py src/backend/ingestion/tests/test_import_flow.py"`.
- [ ] Package a non-root worker with no host ports, no shared app/credential mounts, bounded
  memory/temp storage and explicit API/token/build settings. Only web and import worker receive
  the new token; disabled deployments do not require it. Keep local-purchase/Commerce/photo-worker
  startup and feature reconciliation unchanged. Deny the new internal API at the public Nginx edge.
- [ ] Extend Deploy's build, secret projection, preflight, profile reconciliation and previous-image
  recovery together. The deployment must not leave an import worker running against old web.
  No ad-hoc deployment entrypoint or second environment is introduced.
- [ ] Implement the inspection and dry-run/apply cleanup commands from the safeguards section.
  Cover cleanup racing with a live lease/publication, paused work older than 24 hours, expiry after
  final-copy interruption, and restart without reusing a deleted checkpoint. Retain dedup winners
  and confirmed Photo links; cleanup may expire an attempt, never its source manifest/history.
- [ ] Rehearse the full old-schema matrix using disposable local DB fixtures; run the maximum
  manifest envelope through actual container HTTP parsing and persistence. Exercise one file at
  the accepted byte limit with bounded resources. Record measured limits before deployment;
  if safe resource limits cannot be demonstrated, block activation rather than resize implicitly.
- [ ] Run an end-to-end flow: browser submit/disconnect → worker source → S3 publication →
  standard free/paid policy and processing → browser revisit → duplicate repeat → failed-item retry.
  Keep automated source/storage fixtures distinct from an explicitly authorized real-source smoke.
- [ ] Repeat selected checks after final changes. Expected: all seven release-safeguard outcomes
  verified locally; canonical activation remains a separately authorized operation.

### Final task: Architecture and ADR reconciliation

**Files:** `docs/architecture.md`, `docs/product-jobs.md` (PJ-004), applicable
`docs/engineering-jobs.md` entries, the specification/ADR links and implementation evidence.

- [ ] After behavior verification, compare every delivered boundary with accepted ADR 0035 and
  the specification. Keep ADR bodies immutable; a discovered contradiction requires a decision.
- [ ] Update architecture from accepted/unimplemented to implemented only where evidence supports
  it; distinguish local verification from deployed behavior and runtime activation.
- [ ] Rerun final suite selection/fingerprint, fill missing evidence, then record the explicit
  conformance and architecture-update outcome before push. Do not declare successful real imports
  from mocks, image packaging, or small source-listing probes.

## Verification

Use `$select-verification-suites`; the selector and [Testing](../testing.md) remain authoritative.
Before TDD, provide the worktree's local PostgreSQL and test-safe environment. At specification
time `make check` could not reach local PostgreSQL on 5432; restore the local test prerequisite
before interpreting RED failures. Do not substitute deployed PostgreSQL for local testing.

Run per-task focused commands above; normalize exact changed Python files with the shared
pre-commit hook before review. For the final branch:

```sh
.venv/bin/python scripts/select_test_suites.py select --base origin/main --format json
.venv/bin/python scripts/select_test_suites.py fingerprint --base origin/main
make check
make test-migrations
make test-operational
npm ci
npm run test:js
npm run test:visual
git diff --check
```

Expected selection includes all three expensive layers for the anticipated models, deployment and
UI paths; actual final selector output controls execution. Expected outcomes are GREEN core,
schema-preservation/immutability checks, container/deployment contracts and visually reviewed UI.
Reuse only exact final-package fingerprint evidence. CI after push repeats these checks.
Planning-only Markdown edits require link/status/whitespace review, not a new full application run.

## Operational impact and rollout

Schema adds import-owned state; source/transfer uses the existing private media storage and a new
worker on the canonical deployment. Provision its API credential through the current Lockbox
projection only after approval; no new cloud resource or storage grant is implied. Measure resources
locally, then present exact deployment configuration and approval targets before activation.

Use the safeguard ordering and the existing Deploy workflow. Staff acceptance uses one explicitly
chosen event/folder and a small public JPEG source containing direct files, one subfolder and a
non-JPEG. Verify the warning, original/processing outcome, repeat without duplicates, one recoverable
failure and renewed access after gate pause. Reconfirm storage/processing counters and public
health before moving to on. A real test source and the operational approval are release inputs;
their absence does not justify guessing a user's folder or creating chargeable resources.

## Rollback

Set `yandex-disk-import` off, disable/stop the import worker through the supported deployment path,
and wait for its lease window. Inspect incomplete attempts. Preserve import rows, hashes, final
objects and all existing processing data. Roll back web/images only after worker stop. Keep added
tables; old web ignores them. On re-upgrade, reconcile the gate to off and resume durable state
through the new code only. Neither rollback nor cleanup deletes confirmed originals. A snapshot
restore, schema reversal, or data purge is not part of ordinary rollback.

## Open questions

None for the selected product and architecture. Implementation supplies measured transport/resource
limits and the listed verification evidence before declaring its tasks complete. Live activation
requires an authorized sample source, refreshed inventory and explicit operational approval.
