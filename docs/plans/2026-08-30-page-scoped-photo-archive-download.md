# Page-Scoped Photo Archive Download Implementation Plan

- Date: 2026-08-30
- Status: Approved by maintainer instruction on 2026-08-30
- Owner: project maintainer
- Related specification:
  [`docs/superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md`](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md)
- Related architecture:
  [`docs/architecture.md`](../architecture.md#target-mvp-architecture--proposed),
  [search](../architecture.md#search), and
  [purchase and download](../architecture.md#purchase-and-download)
- Related ADRs:
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md),
  [ADR 0020](../adr/0020-use-signed-direct-object-storage-media-delivery.md),
  [ADR 0021](../adr/0021-allow-original-download-for-authorized-photos.md),
  [ADR 0022](../adr/0022-use-numbered-gallery-pages.md),
  [ADR 0028](../adr/0028-operate-one-canonical-deployment.md),
  [ADR 0031](../adr/0031-use-orders-and-adapters-for-paid-original-delivery.md),
  [ADR 0032](../adr/0032-reconcile-code-owned-feature-flags-at-startup.md), and
  [ADR 0034](../adr/0034-stream-page-scoped-photo-archives-through-django.md)
- ADR impact: implements accepted ADR 0034's narrow page-scoped Django streaming exception while
  preserving direct Object Storage delivery for individual media and all existing authorization.

## Goal

Deliver the approved [page-scoped archive outcome](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md#outcome): one immediate ZIP download for the open free selfie-result page or paid Order page, without storing an aggregate archive.

## Scope

Implement the approved specification without changing its scope. Execution must use
`$execute-implementation-plan` in the existing isolated worktree based on current `origin/main`.

## Acceptance criteria

Use all numbered [specification acceptance criteria](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md#acceptance-criteria). In addition:

- the archive gate remains `off` after repository implementation;
- no schema migration, archive row, background job, temporary archive file, or Object Storage
  archive object is introduced;
- individual original download routes and paid ready-result cart behavior remain unchanged; and
- final evidence distinguishes implementation, review, commit, deployment, gate activation, and
  live acceptance.

## Worker/state/artifact release safeguards

- [x] **Live-state inventory.** Current code has no archive processor, job, attempt, row, lease,
  accepted archive result, or archive Object Storage prefix. Originals remain existing private
  objects, and archive output is request-local. Before staff activation, inspect the code-owned
  feature registry plus the database gate row and representative free-result and paid-Order page
  counts with read-only commands; no customer tokens, object keys, or photo identifiers are logged.
- [x] **Compatibility matrix.** The canonical deployment uses one atomic Django image and no
  archive worker. Old image + old rows has no archive route. New image + old rows reconciles the
  new gate `off`, leaves searches/orders readable, and leaves individual downloads unchanged. New
  image + `staff`/`on` exposes only authorized page archives. Rolling back to the old image removes
  the route and reconciled definition per ADR 0032; no generated archive state requires draining.
- [x] **Reviewed data-state migration or reset semantics.** No schema or data migration, backfill,
  requeue, purge, or reset is required. Existing search, result, Order, OrderItem, grant, audit, and
  attention rows are read through their current contracts; completed bytes are never persisted.
- [x] **End-to-end contract sizing.** The existing page size and 50 MiB upload limit bound one input
  set to 100 originals and approximately 5 GiB. Tests verify ZIP64, sequential one-object opening,
  bounded chunks, response/proxy headers, callback behavior, and absence of persistence. Staff
  acceptance uses a representative maximum-page transfer through Django/Gunicorn/Nginx and checks
  ordinary request latency while the transfer is active. No archive payload crosses a worker,
  callback, model, or database boundary because those layers do not exist for this artifact.
- [x] **Previous-snapshot upgrade rehearsal.** On a previous-version database containing ready
  searches, free and paid events, pending/paid Orders, active/revoked grants, audits, attentions,
  and existing feature rows, startup reconciliation adds only `bulk-photo-download=off`. Every old
  row remains readable and no archive work is enrolled, retried, superseded, or reset; pages render
  their existing state while the gate is off.
- [x] **Staged activation and rollback order.** Deploy the compatible image with the gate off,
  verify reconciliation and unchanged individual downloads, move only the archive gate to `staff`,
  exercise free and paid pages including a large page, failure, disconnect, and concurrent ordinary
  requests, then move to `on` only with healthy latency and request-slot pressure. Stop on elevated
  ordinary latency, incomplete-archive support pain, or storage errors. Roll back exposure to `off`
  first and then roll back the image; there is no artifact cleanup.
- [x] **Supported bounded operational commands.** Startup's existing `sync_feature_flags` command
  is the only state-changing operational command. Feature-state inspection and representative page
  counts are read-only and event/Order bounded. Admin changes `off` to `staff` or `on`. There are no
  supported archive requeue, backfill, purge, or maintenance commands because no durable archive
  state exists.

Rationale: [2026-07-31 staging processing-state reset postmortem](../postmortems/2026-07-31-staging-processing-state-reset.md).

## Implementation

### Task 1: Build the authorized streaming ZIP capability and shared action presentation

**Files:**

- Create `src/backend/picflow/archive.py`.
- Create `src/backend/picflow/archive_presentation.py`.
- Create `src/backend/picflow/tests/test_archive.py`.
- Create `src/backend/picflow/tests/test_archive_presentation.py`.

- **Specification:** [Customer interface](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md#customer-interface), [archive identity and contents](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md#archive-identity-and-contents), [streaming boundary](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md#streaming-boundary), and [failure semantics](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md#failure-semantics).
- **Depends on:** existing `FinalObjectStorage.open_final` and `OpenedObject` contracts.
- **Produces:** an authorization-agnostic `ArchiveEntry`, a one-shot prepared ZIP64 stream, typed
  missing/unavailable failures, and one shared page-action presenter consumed by Tasks 2 and 3.

- [ ] Add failing behavior tests for valid ZIP64-capable stored JPEG/PNG members, exact order and
  safe flat names, first-source preflight, one open source at a time, bounded output, close and
  disconnect handling, missing/mismatched/unavailable sources, incomplete later failure, and no
  local or storage artifact.
- [ ] Add failing presentation tests for hidden action below two items, exact single-page and
  multi-page labels, helper copy, page counts, and Russian photo pluralization.
- [ ] Run `make test TESTS="src/backend/picflow/tests/test_archive.py src/backend/picflow/tests/test_archive_presentation.py"` and confirm failures identify the absent capability.
- [ ] Implement the smallest standard-library `zipfile` stream over a non-seekable bounded sink.
  Open and close one exact private original at a time, use `ZIP_STORED` with ZIP64, validate size
  and content type, expose no keys, and emit only privacy-safe aggregate telemetry.
- [ ] Re-run the focused command; expect all archive and presentation contracts to pass.

### Task 2: Gate and integrate free ready-result page archives

**Files:**

- Modify `src/backend/feature_flags/registry.py`.
- Modify `src/backend/feature_flags/bootstrap_local_purchase_review.py`.
- Modify focused tests under `src/backend/feature_flags/tests/`.
- Modify `src/backend/selfie_search/services/results.py` if a public page-entry projection is needed.
- Modify `src/backend/selfie_search/views.py`.
- Modify `src/backend/selfie_search/urls.py`.
- Modify `src/backend/selfie_search/templates/selfie_search/result.html`.
- Modify `src/backend/static/ui/selfie-search.css`.
- Modify `src/backend/selfie_search/tests/test_results.py` and `src/backend/selfie_search/tests/test_views.py`.

- **Specification:** [Free ready-result archive](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md#free-ready-result-archive), [common protections](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md#common-protections), and [streaming boundary](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md#streaming-boundary).
- **Depends on:** Task 1 archive and action-presentation interfaces.
- **Produces:** code-owned `bulk-photo-download` gate, free-only ready-result action, and an exact
  bearer-authorized `?page=N` archive route.

- [ ] Add failing tests for `off`/`staff`/`on`, free-versus-paid denial, exact current page/order,
  page validation, two-item threshold, single/multi-page copy and filename, unchanged paid-result
  cart UI, 404/503 pre-stream mapping, no `Content-Length`, private caching/referrer headers, and
  `X-Accel-Buffering: no` only on archive responses.
- [ ] Run `make test TESTS="src/backend/feature_flags/tests src/backend/selfie_search/tests/test_results.py src/backend/selfie_search/tests/test_views.py"` and confirm the missing gate/route/UI failures.
- [ ] Implement the smallest integration by re-resolving the existing bearer and saved ready page,
  constructing entries only after authorization, and passing them to Task 1. Do not add any action
  to a paid ready result.
- [ ] Re-run the focused command; expect authorized free archives and every denial/regression path
  to pass.

### Task 3: Paginate paid Orders and integrate purchased page archives

**Files:**

- Create `src/backend/commerce/order_items.py` and its focused test module.
- Modify `src/backend/commerce/presentation.py`.
- Modify `src/backend/commerce/original_delivery.py`.
- Modify `src/backend/commerce/views.py`.
- Modify `src/backend/commerce/urls.py`.
- Modify `src/backend/templates/commerce/order.html`.
- Modify `src/backend/static/ui/catalog.css`.
- Modify focused tests under `src/backend/commerce/tests/`, including presentation, order views,
  original delivery, and archive delivery.

- **Specification:** [Paid Order archive](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md#paid-order-archive), [the open page is the batch](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md#the-open-page-is-the-batch), [observability and operations](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md#observability-and-operations), and [streaming boundary](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md#streaming-boundary).
- **Depends on:** Tasks 1 and 2, existing purchase-browser/grant authorization, `DownloadGrantAudit`,
  and missing-original Commerce attention.
- **Produces:** fixed 100-item Order pages, total-order summary plus current-page photos, browser and
  grant archive routes, per-item audits, and the primary archive/secondary resend hierarchy.

- [ ] Add failing tests for stable `photo_id` pages, invalid pages, total-versus-current counts,
  pending/paid presentation, purchase-browser and grant authorization, cross-Order denial,
  revoked/invalid capability, exact current-page entitlement/order, archive names and response
  headers, per-item audit, first access, missing-original attention, and gate failure closed.
- [ ] Run `make test TESTS="src/backend/commerce/tests/test_order_items.py src/backend/commerce/tests/test_presentation.py src/backend/commerce/tests/test_order_views.py src/backend/commerce/tests/test_original_delivery.py src/backend/commerce/tests/test_archive_delivery.py"` and confirm failures identify absent pagination/archive behavior.
- [ ] Implement one shared page query for rendering and authorization. Keep paid-state and customer
  capability checks inside Commerce, append existing per-item audit authority before streaming,
  and retain missing-original attention callbacks for both first and later source failures.
- [ ] Re-run the focused command; expect all paid pagination, archive, audit, attention, and existing
  individual-download paths to pass.

### Task 4: Lock production visual contracts and reconcile documentation

**Files:**

- Modify `tests/visual/views.py`.
- Modify `tests/visual/urls.py` only if new explicit fixture routes are required.
- Modify `tests/visual/visual.spec.js`.
- Update affected files under `tests/visual/visual.spec.js-snapshots/`.
- Modify `.agents/skills/update-visual-design/references/screen-inventory.md`.
- Modify `docs/architecture.md`, `docs/adr/README.md`, ADR 0034, and the approved specification only
  where final implementation facts require reconciliation.

- **Specification:** [Customer interface](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md#customer-interface) and [acceptance criteria](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md#acceptance-criteria).
- **Depends on:** final templates, CSS, pagination, and action presentation from Tasks 2 and 3.
- **Produces:** deterministic desktop/390px coverage for free single/multi-page, paid ready-result
  denial, paid Order single/multi-page, and final implemented architecture facts.

- [ ] Add or adapt deterministic production visual fixtures and behavior/geometry assertions; do
  not replace production templates with design-reference markup.
- [ ] Run `npm run test:js` and confirm new assertions fail before their fixture/UI contract exists.
- [ ] Update baselines with `npm run test:visual:update`, inspect every changed image, and record the
  approved action hierarchy, helper copy, page controls, and mobile full-width behavior.
- [ ] Run `npm run test:js` and `npm run test:visual`; expect all JavaScript and visual contracts to
  pass.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the approved specification, accepted ADRs 0019, 0020, 0021,
  0022, 0028, 0031, 0032, and 0034, and `docs/architecture.md`.
- [ ] Record page-scoped streaming ZIP and the new default-off gate as implemented repository facts;
  keep activation and live capacity validation explicitly incomplete.
- [ ] Stop for a decision instead of contradicting an accepted ADR; supersede rather than edit it.
- [ ] Record the reconciliation outcome in the final implementation report.

## Verification

Run focused commands in each task and normalize exact changed Python files with
`.venv/bin/pre-commit run --files <changed Python files>` after the last task-file change.

On the final reviewed branch run:

1. `.venv/bin/python scripts/select_test_suites.py select --base origin/main --head HEAD --format json` and expect selector reasons for all changed paths.
2. `.venv/bin/python scripts/select_test_suites.py fingerprint --base origin/main --head HEAD --format json` and record the exact final-package fingerprint.
3. `make check` and expect static checks, Django checks, migration checks, and the complete Python suite to pass.
4. `npm run test:js` and expect all JavaScript tests to pass.
5. `npm run test:visual` when selected (expected because production templates, CSS, fixtures, and snapshots change), and expect all desktop/mobile baselines to pass.
6. Run any additional expensive suite selected by the executable selector exactly once for the same final-package fingerprint.
7. `git diff --check` and expect no whitespace errors.

## Operational impact and rollout

The Django image gains a streaming response that occupies one ordinary request slot and pulls one
private original at a time. Startup reconciliation creates the code-owned gate in `off`; this plan
does not activate it or deploy. No dependency, schema, worker, Compose service, storage prefix, or
lifecycle changes. A later approved rollout follows the staged activation safeguard above and
records representative transfer duration, bytes, outcome, storage failures, disconnects, and
ordinary request latency with low-cardinality privacy-safe telemetry.

## Rollback

Set `bulk-photo-download` to `off` first. If necessary, deploy the prior Django image; individual
signed downloads and all existing Order/search state remain valid. No archive rows, files, jobs,
objects, migrations, or leases require cleanup or reversal.

## Open questions

None.
