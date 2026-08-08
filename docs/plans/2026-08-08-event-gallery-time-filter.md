# Event Gallery Time Filter Implementation Plan

- Date: 2026-08-08
- Status: Ready for implementation; customer delivery remains gated by the final live-staging
  capture-time acceptance report.
- Owner: project maintainer
- Related specification:
  [`2026-08-08-event-gallery-time-filter-design.md`](../superpowers/specs/2026-08-08-event-gallery-time-filter-design.md)
  (approved at `76220c3`)
- Related architecture:
  [Current architecture — implemented: event timezone and capture metadata](../architecture.md#current-architecture--implemented),
  [Core data flows — proposed: Search](../architecture.md#search), and
  [Security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- Related ADRs: [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md),
  [ADR 0020](../adr/0020-use-signed-direct-object-storage-media-delivery.md),
  [ADR 0021](../adr/0021-allow-original-download-for-authorized-photos.md), and
  [ADR 0022](../adr/0022-use-numbered-gallery-pages.md)
- ADR impact: Conforms to ADR 0017, ADR 0019, and ADR 0022; ADRs 0020 and 0021 retain their
  existing media and download boundaries. No new or superseding ADR is required.

## Goal

Deliver the approved server-rendered, event-local gallery time filter and its permanently available
free-event discovery area, while retaining the direct current-v2 processing evidence as the only
source of capture time.

## Scope

Implement the approved specification without scope changes. In particular, this plan adds no photo
capture-time projection, migration, index, persisted manual query, asynchronous time search, or
browser-timezone behavior. Historical plans and specifications that mention the retired gate remain
immutable delivery evidence and are not rewritten.

## Acceptance criteria

The approved specification's acceptance criteria are authoritative. Completion additionally requires
these observable delivery gates:

- all unfiltered gallery, media, download, bearer-result, selfie privacy, feedback, and cluster
  regressions covered by the affected focused suites continue to pass;
- `rg -n "SELFIE_SEARCH_ENABLED"` has no active-code, configuration, workflow, deployment, test,
  or current-documentation result; historical evidence under `docs/plans/` and
  `docs/superpowers/specs/` is excluded from that check; and
- the local-clone performance report and final live-staging report both meet the specified 2x
  filtered-versus-unfiltered execution and rendered-page-latency gate before customer delivery.

## Execution

Execute this approved plan using `$execute-implementation-plan`.

## Implementation

### Task 1: Add the event-local manual-time domain contract and direct evidence queryset

**Files:** create `src/backend/picflow/forms.py`; modify `src/backend/picflow/gallery.py`,
`src/backend/config/views.py`, `src/backend/picflow/tests/test_gallery.py`, and
`src/backend/picflow/tests/test_views.py`.

- **Specification:** User experience and GET interface; Manual-time semantics; Evidence selection
  and gallery behavior; acceptance criteria 1 and 4-11.
- **Depends on:** The capture-time-v2 corpus contract named in Preconditions and evidence status.
- **Produces:** `EventGalleryTimeFilterForm(event, data)` and a filtered extension of
  `gallery_photo_queryset(event=event)` that receives validated UTC bounds; `event_detail` exposes
  one of the unfiltered, valid-filtered, or invalid-search states without a stored search object.

- [ ] Add failing form tests for scalar-only `from`/`to`, required `from` once a filter is requested,
  optional blank `to`, complete `datetime-local` parsing, event-local min/max bounds, UTC conversion
  through `Event.timezone_name`, explicit browser/server-timezone mismatch, and rejected nonexistent
  and ambiguous DST wall times. Cover multi-day ranges, midnight, omitted `to`'s exclusive next-local-
  midnight end, and `to <= from`.
- [ ] Add failing gallery-query tests with a current accepted version-2 capture-metadata state plus
  matching accepted attempt. Prove inclusive `-10`/`+10` minute boundaries, immediately-outside
  exclusion, filename-plus-ID order, 100-item offset pages, and exclusion of version 1, stale,
  unaccepted, failed, null/malformed capture time, other-event, and otherwise ineligible gallery
  rows. The tests must prove the query joins the mutable current state to its accepted successful
  attempt, rather than merely matching convenient JSON fields.
- [ ] Add failing view tests for the three state machine outcomes: no `from`/`to` preserves current
  page behavior; a valid filter applies the query before existing page validation; malformed, blank,
  repeated, out-of-range, or DST-invalid values return 200 with form errors and no cards, pager, or
  ordinary empty state. Assert no `SelfieSearch`, processing job, storage object, or manual-query
  record is created.
- [ ] Run
  `make test TESTS="src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py"`
  and confirm the new contract tests fail before implementation.
- [ ] Implement the form as the only wall-clock parser. Detect repeated `from` or `to` through the
  request `QueryDict` before accepting form data; retain safe submitted values in errors. Convert
  only unambiguous event-local wall times to aware UTC datetimes, compute the approved implicit end
  and tolerance, and pass no bounds at all for an unfiltered request.
- [ ] Extend the existing eligibility queryset, not media authorization or presentation factories,
  with the exact current accepted `capture_metadata` v2 relation and canonical capture-time window.
  Keep `original_filename, id`, `.distinct()`, and `GALLERY_PAGE_SIZE` semantics unchanged; do not
  add a model field, index, projection, fallback evidence source, or migration.
- [ ] Make `event_detail` construct the manual form for published free events, skip gallery paging
  entirely after an invalid manual request, and otherwise call the existing paginator over the
  chosen queryset. Preserve paid-event behavior and only ask gallery-face services about photos on
  the selected valid/unfiltered page.
- [ ] Rerun the same focused command and expect all tests to pass.

### Task 2: Render the permanent two-column discovery area and filter-aware navigation

**Files:** modify `src/backend/templates/catalog/event_detail.html`,
`src/backend/templates/ui/gallery_pagination.html`, `src/backend/static/ui/selfie-search.css`,
`src/backend/static/ui/catalog.css`, `src/backend/picflow/tests/test_views.py`,
`tests/visual/views.py`, `tests/visual/urls.py`, `tests/visual/visual.spec.js`, and the affected
  baselines in `tests/visual/visual.spec.js-snapshots/`.

- **Specification:** User experience and GET interface; Discovery area and selfie availability;
  acceptance criteria 2, 3, and 8-10.
- **Depends on:** Task 1's manual form, page state, and validated query context.
- **Produces:** A `#gallery`-addressable server-rendered discovery container with selfie search first
  and manual search second, a filtered-empty state, and pagination that retains only validated
  manual values.

- [ ] Add failing HTML/view assertions for the permanent free-event **«Найти свои фото»** container,
  its **«Поиск по селфи»** and **«Ручной поиск»** columns, event-local date guidance and
  `datetime-local` min/max values, field errors, filtered-empty copy, and query-free reset URL.
  Prove a new manual form has no `page`, while filtered previous/next links and numbered-page form
  retain both validated `from` and `to`; unfiltered pagination remains `page` only.
- [ ] Add no-JavaScript Playwright coverage that submits the manual GET form, reaches `#gallery`,
  sees returned errors in the full HTML response, follows a filtered pager, and resets to the
  canonical event URL. Extend visual fixtures/routes for populated, filtered-empty, and invalid
  manual-search states; add desktop (1440px) and mobile (390px) snapshot/geometry assertions that
  show two columns at desktop and selfie then manual then gallery at mobile without horizontal
  overflow.
- [ ] Run
  `make test TESTS="src/backend/picflow/tests/test_views.py"`
  and
  `npm run test:visual`, the repository-supported containerized visual command, and confirm the
  new assertions fail before implementation.
- [ ] Replace the optional standalone selfie section only on published free-event detail pages with
  the approved container above `#gallery`. Keep the existing selfie multipart form, disclosures,
  history, feedback attributes, face controls, and progressive enhancement intact; add the manual
  GET form beside it and use normal labels/error markup rather than JavaScript behavior.
- [ ] Make `gallery_pagination.html` accept an optional validated filter query context so it emits
  encoded `from`/`to` hidden inputs and prev/next links only for a valid filter. Render a dedicated
  valid-zero-match message, while invalid requests render no gallery section contents beyond the
  form's correction controls.
- [ ] Add responsive CSS at the established breakpoint: a two-column discovery layout on wide
  screens and one document-order column on mobile. Preserve existing gallery, lightbox, focus, and
  44px target rules.
- [ ] Rerun the focused Django and visual commands, update only reviewed visual baselines, and
  expect the selected tests to pass.

### Task 3: Remove the selfie availability gate while preserving prerequisite and independent-flag contracts

**Files:** modify `.env.example`, `README.md`, `src/backend/config/settings.py`,
`src/backend/config/views.py`, `src/backend/selfie_search/apps.py`,
`src/backend/selfie_search/views.py`, `src/backend/picflow/tests/test_views.py`,
`src/backend/selfie_search/tests/test_settings.py`,
`src/backend/selfie_search/tests/test_views.py`, `src/backend/selfie_search/tests/test_submission.py`,
`src/backend/selfie_search/tests/test_feedback_submission.py`, `tests/processing/test_selfie_search_e2e.py`,
`tests/deployment/test_deployment_scripts.py`, `tests/test_repository_foundation.py`,
`scripts/run-in-test-env.sh`, `docker-compose.visual.yml`, `.github/workflows/ci.yml`,
`deploy/apply-deployment.sh`, `.github/workflows/deploy.yml`, and
`.github/workflows/promote-production.yml`.

- **Specification:** Discovery area and selfie availability; Scope exclusions; acceptance criteria
  12 and 13.
- **Depends on:** Task 2 so the always-present discovery UI has its final presentation boundary.
- **Produces:** No executable `SELFIE_SEARCH_ENABLED` setting or disabled-mode branch; every
  published free event exposes the existing selfie submission and gallery-origin path whenever
  Django/deployment prerequisite checks succeed.

- [ ] Add or revise failing settings/deployment tests so the selfie contract values are always
  parsed and validated, while `PHOTO_PROCESSING_ENABLED` and
  `PHOTO_PROCESSING_FACE_ENABLED` remain mandatory fail-fast prerequisites. Replace tests that
  assert hide/404 behavior with availability tests for selfie submit and gallery-origin submit on a
  published free event.
- [ ] Add failing regression tests proving `SELFIE_FEEDBACK_ENABLED` still validates its dedicated
  storage independently, and `SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED` still governs only optional
  expansion. Remove only their former dependency on the retired availability gate; do not weaken
  feedback consent/storage or ADR 0019 bearer authorization tests.
- [ ] Run
  `make test TESTS="src/backend/selfie_search/tests/test_settings.py src/backend/selfie_search/tests/test_views.py src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_feedback_submission.py src/backend/picflow/tests/test_views.py tests/processing/test_selfie_search_e2e.py tests/deployment/test_deployment_scripts.py tests/test_repository_foundation.py"`
  and confirm failures point to the removed flag assumptions.
- [ ] Make the fixed selfie-search limits/model/prefix part of normal settings initialization, remove
  all `SELFIE_SEARCH_ENABLED` environment parsing and all view/template gating, and retain startup
  checks that reject missing processing or face-embedding configuration. Do not add an emergency
  hide switch or a compatibility alias.
- [ ] Set the existing test and CI/visual execution environments to provide the processing and face
  prerequisite booleans while retaining negative system-check coverage. This keeps `make check`,
  CI, and visual fixtures executable without reintroducing an availability mode; production remains
  guarded by the stricter deployment validation below.
- [ ] Remove the variable from sample environment, candidate environment rendering/rollback
  interpolation, deployment validation, GitHub workflow environment maps and pass-through lists,
  workflow storage probes, and repository-foundation/deployment fixtures. Deployment must require
  processing, face embeddings, and private-media prerequisites for the normal selfie capability
  instead of accepting a configuration that starts a silently unavailable page.
- [ ] Update current operational instructions in `README.md`; leave historical plan/spec records
  untouched. Rerun the focused command, then run
  `rg -n "SELFIE_SEARCH_ENABLED" --glob '!docs/plans/**' --glob '!docs/superpowers/specs/**' .`
  and expect no output.

### Task 4: Record the direct-query local-clone performance evidence and enforce its gate

**Files:** create
`src/backend/picflow/management/commands/benchmark_event_gallery_time_filter.py` and
`src/backend/picflow/tests/test_gallery_time_filter_benchmark.py`; create
`docs/performance/2026-08-08-event-gallery-time-filter-local-clone.json` only from the measured
  local clone; modify `README.md` only if the command needs a concise operator invocation reference.

- **Specification:** Preconditions and evidence status; Performance decision; acceptance criterion
  14.
- **Depends on:** Task 1's final direct queryset and Task 2's rendered page; a restored accepted
  local staging clone with the event-9 v2 acceptance report.
- **Produces:** A read-only, privacy-safe benchmark command that compares unfiltered and valid
  filtered first/later pages and reports the direct PostgreSQL plan/execution timing and fully
  rendered Django response timing without identifiers or metadata values.

- [ ] Add failing command tests for strict `--event-id 9` scope, no writes, event timezone/current-
  v2 preconditions, deterministic sanitized JSON, and representative pages `1`, midpoint, and last.
  Assert the output contains corpus count, plan shape, database execution milliseconds, rendered
  milliseconds, matching unfiltered baselines, ratio calculation, and pass/fail gate, but contains
  no filenames, photo IDs, storage keys, EXIF source values, or individual capture times.
- [ ] Run
  `make test TESTS="src/backend/picflow/tests/test_gallery_time_filter_benchmark.py"`
  and confirm it fails before the command exists.
- [ ] Implement `benchmark_event_gallery_time_filter` as a read-only command. It derives a valid
  full-event local filter from the event's first local instant and implicit end, invokes the same
  form/queryset/page/render path as the page, calls PostgreSQL `EXPLAIN (ANALYZE, FORMAT JSON)` for
  the unfiltered and filtered page query, and uses a monotonic wall-clock measurement through
  rendered response content. It must exit non-zero when either filtered measurement exceeds twice
  its matching unfiltered page baseline, a request times out/errors, or the v2 corpus precondition
  is not met; it must never introduce a projection/index or modify data.
- [ ] Rerun the command tests and expect them to pass. First run
  `docker compose --project-directory ../capture-time-filtering exec -T web python manage.py report_event_capture_times --event-id 9 --processor-version 2`
  against the existing `codex/capture-time-filtering` local database/accepted artifact and verify
  its terminal 17,043-photo v2 acceptance. Reuse that accepted source directly, or make a separate
  safe local snapshot copy for this worktree; never write to, replace, reprocess, or otherwise
  mutate the accepted source. Only if that source is absent or unfit may the documented guarded
  clone be used, and only after it has independently verified the same terminal acceptance on its
  source; a live-incomplete staging database is not an acceptable substitute. Restart only the
  target local services as documented, then run
  `docker compose exec -T web python manage.py benchmark_event_gallery_time_filter --event-id 9 --pages 1,mid,last > docs/performance/2026-08-08-event-gallery-time-filter-local-clone.json`.
  Review the report before retaining it: event 9 has 17,043 qualifying current-v2 results, every
  page has a plan/timing pair, each filtered database and rendered value is at most 2x its matching
  baseline, and no timeout or health regression occurred.
- [ ] If the local comparison fails, stop this delivery before any denormalization. Preserve the
  report as evidence and require the separately approved projection/freshness/correction design
  specified by the approved design; do not add a speculative index or projection here.

### Task 5: Reconcile architecture and evidence, then complete full verification

**Files:** modify `docs/architecture.md`, `docs/product-jobs.md`, and
`docs/engineering-jobs.md` only where their current-state/evidence claims change; modify the
approved specification and this plan only to record actually observed evidence/status; retain the
Task 4 report. No ADR file changes are expected.

- **Specification:** Architecture and ADR reconciliation; all acceptance criteria.
- **Depends on:** Tasks 1-4 and the final local-clone result.
- **Produces:** Accurate implemented/current documentation and one reviewable, fully verified task
  diff. The final public release gate remains separate from local verification.

- [ ] Run the integrated focused suite:
  `make test TESTS="src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py src/backend/picflow/tests/test_gallery_time_filter_benchmark.py src/backend/selfie_search/tests/test_settings.py src/backend/selfie_search/tests/test_views.py src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_feedback_submission.py tests/processing/test_selfie_search_e2e.py tests/deployment/test_deployment_scripts.py tests/test_repository_foundation.py"`.
  Expect all selected tests to pass.
- [ ] Run the visual suite once, after its focused checks:
  `npm run test:visual`; expect every visual/no-JavaScript test and reviewed snapshot to pass.
  Then run `make check` once (not concurrently with the visual run) and require zero exit status for
  formatting, lint, types, Django checks, migration drift, default tests, and coverage.
- [ ] Update current architecture/job facts to describe the event-local GET refinement, direct
  current-v2 evidence source, unchanged 100-item filename-plus-ID pagination/media boundaries,
  permanently available free-event selfie path, retained independent flags, performance evidence,
  and the distinction between local-clone, CI, deployed staging, and customer-live evidence. Do not
  claim the pending live report as complete and do not revise historical gate evidence.
- [ ] Compare the completed implementation with the approved specification and ADRs 0017, 0019,
  0020, 0021, and 0022. Record: `Conforms to ADR 0017, ADR 0019, and ADR 0022; ADRs 0020 and 0021
  remain unchanged; no new or superseding ADR required.` Stop for an ADR decision if this statement
  cannot truthfully be made.
- [ ] Run `git diff --check` and inspect the complete unstaged task diff, including the new report
  and visual baselines. Classify review findings as blocking or future under `AGENTS.md`, resolve
  blocking findings through the implementation/review loop, and rerun affected focused checks plus
  the final commands above before handoff to the root controller.

## Verification

Run in this order:

1. Each task's specified RED command, then its GREEN command.
2. The Task 5 integrated focused suite.
3. `npm run test:visual` after focused visual coverage, with all production and no-JavaScript
   assertions passing.
4. `make check` with exit status 0, run separately from the visual suite.
5. `git diff --check` with no output.
6. The Task 4 local-clone benchmark command with the checked-in sanitized report and every 2x gate
   passing.
7. After deployment, the same read-only benchmark against staging plus the existing capture-time
   terminal acceptance report. This final live report, not local clone evidence or CI, authorizes
   customer delivery.

## Operational impact and rollout

There is no schema migration, index, worker protocol change, object-store mutation, or new service.
The normal Django image/deployment is required because both the customer page and the no-longer-
optional selfie prerequisite checks change together.

1. Complete Tasks 1-5 and obtain the required review and CI evidence through the normal workflow.
2. Before staging deploy, ensure the environment has the existing processing, face-embedding,
   worker, and private-media prerequisites; remove the obsolete `SELFIE_SEARCH_ENABLED` repository/
   environment variable rather than setting it. Keep feedback and cluster-expansion values under
   their independent existing controls.
3. Deploy normally. Candidate checks must fail before service switch when those selfie prerequisites
   are absent; they must not deploy a hidden/404 selfie capability.
4. On staging, perform published-free-event smoke checks for unfiltered gallery, event-local valid
   filter, invalid filter, filtered paging/reset, selfie submission, gallery-origin submission,
   bearer authorization, and health. Run the read-only performance command/report.
5. Run the existing live event-9 capture-time terminal report and require exactly 17,043 terminal
   current accepted v2 non-null times, zero terminal failures/missing times/`inferred_none`, and
   reviewed bounded distributions with no JPEG/MPO three-hour split. Require the staging benchmark
   to meet both per-page 2x gates without timeout or health degradation.
6. Only after both live reports pass may the customer-facing delivery be marked accepted. The
   reports must remain aggregate-only and must not include customer identifiers or metadata values.

## Rollback

Before service switch, deployment validation failure leaves the prior image/environment active. If
the candidate fails after mutation begins, use the existing deployment entrypoint's automatic
previous-image/environment recovery and confirm health.

After a successful switch, revert the application/workflow change as one release if the gallery
filter or always-available selfie path regresses. This removes the time UI/query path and restores
the prior image; it does not delete immutable capture attempts, gallery media, bearer results,
selfie/feedback records, or the aggregate performance evidence. Do not recreate the retired
availability flag as an emergency switch. A failed performance gate or capture-time report blocks
customer delivery and requires correction or a separately approved projection design, not data
mutation or fallback to version 1.

## Open questions

None.
