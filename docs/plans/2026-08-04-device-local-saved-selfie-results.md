# Device-local Saved Selfie-search Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

- Date: 2026-08-04
- Status: Draft
- Owner: project maintainer
- Related specification:
  [`2026-08-04-device-local-saved-selfie-results-design.md`](../superpowers/specs/2026-08-04-device-local-saved-selfie-results-design.md)
- Related architecture:
  [`docs/architecture.md`](../architecture.md), current public event-scoped selfie search and
  security, privacy, and legal boundaries
- Related ADRs:
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md)
- ADR impact: Conforms to ADR 0019. The plan adds browser-local discovery of existing bearer
  results without changing their identity, authorization, retention, cleanup, or media access.

## Goal

Implement [PJ-014](../product-jobs.md#pj-014--customer--return-to-saved-selfie-search-results) so
one browser can save every selfie-search result it opens and list the matching event's results on
that event page.

## Architecture

A focused browser module owns validation, versioned `localStorage` persistence, event isolation,
and DOM presentation. Django renders only non-secret event identity plus an initially hidden empty
container; bearer paths stay in JavaScript memory and are never written into event-page HTML or
DOM attributes. Existing search, feedback, analytics, and media code remains unchanged.

## Tech stack

Django 6 templates and tests, browser JavaScript with Node's built-in test runner, existing CSS,
and containerized Playwright visual tests.

## Global constraints

- The approved specification is authoritative; this plan changes no scope or behavior.
- Use `localStorage` key `findme_selfie_search_history:v1` and the exact entry fields
  `eventSlug`, `resultPath`, and `openedAt`.
- Persist every valid entry: no fixed limit, expiry, or automatic old-entry deletion.
- Store only canonical same-origin result paths without query strings or fragments.
- Never place a saved bearer path in event-page HTML, text, `href`, form values, or DOM attributes.
- Browser-storage failures must never block search submission or result viewing.
- Add no dependency, migration, model, endpoint, worker, Object Storage, configuration, or ADR.
- Keep the existing server-side bearer result, analytics suppression, feedback, and media contracts
  unchanged.

## Scope

Implement the approved specification without scope changes.

## Acceptance criteria

Use the specification's [success criteria](../superpowers/specs/2026-08-04-device-local-saved-selfie-results-design.md#success-criteria)
and [acceptance evidence](../superpowers/specs/2026-08-04-device-local-saved-selfie-results-design.md#acceptance-evidence).
Delivery additionally requires the focused JavaScript, Django, and visual checks plus the
repository release gate to pass from the final task diff.

## File map

- Create `src/backend/static/ui/selfie-search-history.js`: pure entry validation and persistence,
  event-page rendering, result-page saving, and browser bootstrap for this feature only.
- Create `tests/js/selfie-search-history.test.js`: deterministic unit and fake-DOM coverage for the
  history module.
- Modify `src/backend/templates/catalog/event_detail.html`: render non-secret event identity and the
  initially hidden empty history container; load the new script.
- Modify `src/backend/selfie_search/templates/selfie_search/result.html`: expose the non-secret event
  slug to the module and load the new script; do not expose the bearer token in a new attribute.
- Modify `src/backend/static/ui/selfie-search.css`: style the event-local list and responsive
  actions using existing tokens and button patterns.
- Modify `src/backend/selfie_search/tests/test_views.py`: assert the production markup and bearer
  privacy contract on event and result pages.
- Modify `tests/visual/views.py`: provide a deterministic event fixture whose browser storage is
  preloaded by Playwright, without adding a production endpoint.
- Modify `tests/visual/visual.spec.js`: verify persistence/navigation/removal and capture desktop and
  mobile list states.
- Create `tests/visual/visual.spec.js-snapshots/desktop-event-selfie-search-history.png` and
  `tests/visual/visual.spec.js-snapshots/mobile-event-selfie-search-history.png`: reviewed visual
  baselines for multiple results.
- Modify `docs/architecture.md`: record the delivered browser-local presentation fact after all
  behavior checks pass.
- Modify `docs/product-jobs.md`: advance PJ-014 only with evidence appropriate to the actual
  implementation/delivery state and append one status-log row.

## Implementation

### Task 1: History storage and path validation

**Deliverable:** A standalone tested history store that accepts only canonical same-event result
paths, keeps all valid entries newest first, deduplicates reopened results, and preserves unrelated
events during writes and removal.

**Files:**

- Create: `src/backend/static/ui/selfie-search-history.js`
- Create: `tests/js/selfie-search-history.test.js`

- **Specification:** [Browser storage contract](../superpowers/specs/2026-08-04-device-local-saved-selfie-results-design.md#browser-storage-contract),
  [Bearer-link privacy](../superpowers/specs/2026-08-04-device-local-saved-selfie-results-design.md#bearer-link-privacy),
  and [Failure behavior](../superpowers/specs/2026-08-04-device-local-saved-selfie-results-design.md#failure-behavior).
- **Depends on:** None.
- **Produces:** `SelfieSearchHistoryStore` with `list(eventSlug)`,
  `save({eventSlug, resultPath})`, and `remove({eventSlug, resultPath})`; exported
  `HISTORY_STORAGE_KEY`; exported canonical-path validation used by the UI controller in Task 2.

- [ ] Write Node tests for an empty store, first save, multiple entries, deterministic newest-first
  order, duplicate reopening with an updated injected clock, and persistence through a second store
  instance over the same fake `localStorage`.
- [ ] Add tests proving strict event filtering, removal of one entry while preserving the other
  event, and preservation of all valid entries without a history cap or expiry.
- [ ] Add table-driven rejection tests for malformed JSON entries, wrong types, invalid timestamps,
  absolute URLs, query strings, fragments, extra path segments, missing tokens, slug mismatch,
  encoded path traversal, and non-selfie-search paths. Include a Unicode event slug case using the
  canonical encoded pathname.
- [ ] Add throwing-storage tests for `getItem` and `setItem`; reads must return no entries and failed
  writes/removals must report failure without throwing into the caller.
- [ ] Run `node --test tests/js/selfie-search-history.test.js` and confirm the new tests fail because
  the module and exports do not exist.
- [ ] Implement the minimal UMD/CommonJS-compatible module, following the export/bootstrap pattern
  in `src/backend/static/ui/selfie-search.js`. Inject `localStorage` and `now` into the store for
  deterministic tests; validate and normalize every entry on both read and write.
- [ ] Run `node --test tests/js/selfie-search-history.test.js` and confirm all history-store tests
  pass with no uncaught storage error.
- [ ] Run `npm run test:js` and confirm the complete JavaScript suite passes without regressing the
  existing gallery, upload, or selfie-feedback modules.

### Task 2: Event-page list and result-page saving

**Deliverable:** Valid result pages save themselves locally; matching event pages render and operate
the accessible saved-results list without placing bearer paths in the DOM.

**Files:**

- Modify: `src/backend/static/ui/selfie-search-history.js`
- Modify: `tests/js/selfie-search-history.test.js`
- Modify: `src/backend/templates/catalog/event_detail.html`
- Modify: `src/backend/selfie_search/templates/selfie_search/result.html`
- Modify: `src/backend/static/ui/selfie-search.css`
- Modify: `src/backend/selfie_search/tests/test_views.py`

- **Specification:** [Customer experience](../superpowers/specs/2026-08-04-device-local-saved-selfie-results-design.md#customer-experience),
  [Success criteria](../superpowers/specs/2026-08-04-device-local-saved-selfie-results-design.md#success-criteria),
  and [Bearer-link privacy](../superpowers/specs/2026-08-04-device-local-saved-selfie-results-design.md#bearer-link-privacy).
- **Depends on:** Task 1's store and canonical-path validator.
- **Produces:** `startSelfieSearchHistory(document, window, options)` bootstrap; event markup hooks
  `data-selfie-search-history`, `data-selfie-search-history-list`, and non-secret
  `data-event-slug`; result markup supplies only `data-event-slug` and the current pathname remains
  the sole browser source of the bearer path.

- [ ] Add failing Node fake-DOM tests showing that result-page bootstrap saves the canonical current
  pathname, strips query and fragment through `location.pathname`, and quietly ignores invalid or
  unavailable storage.
- [ ] Add failing Node fake-DOM tests for an event page with zero, one, and several matching entries:
  hidden empty state, local date/time labels, newest-first rows, exact Russian disclosure and button
  copy, and no bearer token in element text or attributes.
- [ ] Add failing interaction tests proving an `Открыть результат` button revalidates its in-memory
  path and calls same-origin navigation, while `Удалить с устройства` removes only that entry,
  moves focus to the next sensible control, and hides the section after the final row.
- [ ] Add Django markup tests for free and paid event pages and every public result state. Assert the
  non-secret event slug, hidden empty container, script inclusion, and absence of a result path,
  public token, saved-result `href`, or server-rendered history row on the event page.
- [ ] Run `node --test tests/js/selfie-search-history.test.js` and
  `make test TESTS='src/backend/selfie_search/tests/test_views.py'`; confirm failures point to the
  missing bootstrap, markup, and controls.
- [ ] Add the minimal template hooks and script tags. Load `selfie-search-history.js` after the DOM
  it initializes on both templates, independently of whether feedback is enabled.
- [ ] Implement result saving and event-list rendering with DOM APIs only (`createElement`,
  `textContent`, and event listeners). Keep canonical paths in closures; do not write them into
  nodes, datasets, attributes, form controls, or anchors.
- [ ] Add focused responsive CSS under `.selfie-search-history` using current spacing, color,
  border, focus, and 44-pixel control conventions. Do not restyle the existing upload form.
- [ ] Run the two targeted commands again and confirm all new JavaScript and Django markup tests
  pass.
- [ ] Run `npm run test:js` and
  `make test TESTS='src/backend/selfie_search/tests/test_views.py src/backend/picflow/tests/test_views.py'`;
  confirm all related browser and event/result regressions pass.

### Task 3: Browser integration and visual acceptance

**Deliverable:** Real-browser evidence covers persistence, event isolation, button navigation,
deletion/focus behavior, privacy, and reviewed desktop/mobile presentation with multiple saved
results.

**Files:**

- Modify: `tests/visual/views.py`
- Modify: `tests/visual/visual.spec.js`
- Create: `tests/visual/visual.spec.js-snapshots/desktop-event-selfie-search-history.png`
- Create: `tests/visual/visual.spec.js-snapshots/mobile-event-selfie-search-history.png`

- **Specification:** [Acceptance evidence](../superpowers/specs/2026-08-04-device-local-saved-selfie-results-design.md#acceptance-evidence)
  and [Customer experience](../superpowers/specs/2026-08-04-device-local-saved-selfie-results-design.md#customer-experience).
- **Depends on:** Task 2's production markup, controller, and styles.
- **Produces:** Containerized Playwright interaction coverage and reviewed production-screen
  snapshots.

- [ ] Add a failing Playwright scenario that seeds `findme_selfie_search_history:v1` before opening
  the existing selfie-search event fixture, including two current-event entries and one other-event
  entry. Assert two visible newest-first rows and no rendered token, `href`, or token-bearing
  attribute.
- [ ] Extend the scenario to activate `Открыть результат` and observe navigation to the exact saved
  same-origin path without adding a bearer anchor. Intercept the document request so the fixture
  does not need a new production-like result endpoint.
- [ ] Add deletion assertions: the selected entry disappears from the DOM and `localStorage`, the
  other event remains unchanged, focus moves to a remaining actionable control, and deleting the
  final current-event entry hides the section.
- [ ] Add the saved-history event state to desktop and mobile screenshot coverage. Seed fixed ISO
  timestamps and assert the formatted labels explicitly so snapshots remain deterministic in the
  visual container timezone.
- [ ] Run `npm run test:visual` and confirm the new interaction assertions pass while the two new
  snapshots are reported missing.
- [ ] Run `npm run test:visual:update`, inspect both new PNGs for hierarchy, wrapping, 44-pixel
  controls, focus visibility, and horizontal overflow, and keep only the approved baselines.
- [ ] Run `npm run test:visual` again and confirm every Playwright interaction and snapshot passes
  with no console, request, response, font, image, or overflow failure.

### Task 4: Delivery evidence and architecture reconciliation

**Deliverable:** Repository documentation describes only verified implemented behavior, PJ-014 has
an evidence-backed state, and the complete release gate passes before review or publication.

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/product-jobs.md`

- **Specification:** [Outcome](../superpowers/specs/2026-08-04-device-local-saved-selfie-results-design.md#outcome)
  and [Acceptance evidence](../superpowers/specs/2026-08-04-device-local-saved-selfie-results-design.md#acceptance-evidence).
- **Depends on:** Tasks 1–3 and their recorded passing evidence.
- **Produces:** Current architecture fact, append-only PJ-014 evidence, and final verified task diff.

- [ ] Run the focused checks from Tasks 1–3 from the complete working tree and record exact pass
  counts in PJ-014 evidence. Advance PJ-014 from `Candidate` to `In progress` while implementation
  is repository-only; use `Delivered` only after the feature is actually available in the deployed
  product. Append exactly one status-log row and do not edit prior history.
- [ ] Update `docs/architecture.md` current implemented selfie-search description with the narrow
  browser-local history fact and its no-account/no-server-state/bearer-DOM boundary. Do not change
  target architecture or claim deployment.
- [ ] Run `git diff --check` and confirm no whitespace errors.
- [ ] Run `npm run test:js` and confirm the full JavaScript suite passes.
- [ ] Run `make check` and confirm Ruff format, Ruff lint, configured mypy, the complete Python
  suite with branch coverage, Django system checks, and migration drift checks pass. Do not overlap
  its full pytest process with another full Python suite.
- [ ] Run `npm run test:visual` and confirm the complete containerized visual suite passes.
- [ ] Compare the final diff line by line with the approved specification, ADR 0019, and
  `docs/architecture.md`. Record `Conforms to ADR 0019; no new or superseding ADR` in the review
  report. Stop and return to design if the implementation adds server-side history, changes bearer
  authorization/retention, or exposes a token to analytics or event-page DOM.
- [ ] Prepare one complete unstaged task diff for independent review. After approval, rerun the
  release gate, stage only the files listed by this plan, and let the root controller create the
  single final implementation commit required by `AGENTS.md`.

## Verification

Run from the isolated worktree:

```bash
node --test tests/js/selfie-search-history.test.js
npm run test:js
make test TESTS='src/backend/selfie_search/tests/test_views.py src/backend/picflow/tests/test_views.py'
npm run test:visual
git diff --check
make check
```

Expected outcomes:

- all history unit and fake-DOM cases pass;
- all existing JavaScript tests pass;
- focused selfie-search and event-view tests pass;
- all visual interaction checks and snapshots pass without browser/resource failures or overflow;
- Ruff format, Ruff lint, configured mypy, Django system, migration drift, and diff checks report no
  errors; and
- the complete Python suite inside `make check` passes at or above the repository branch-coverage
  guard.

Do not run two full Python suites concurrently. Snapshot updates are evidence only after both new
images are manually inspected and the non-update visual command passes.

## Operational impact and rollout

There is no migration, configuration, backend API, worker, storage-bucket, deployment-order, or
monitoring change. The static JavaScript, CSS, and templates ship through the existing immutable
application image and deployment workflow.

After deployment, verify one real published event in a fresh browser profile: start two searches,
return to the event page, reopen both results, remove one, refresh, and confirm the remaining item
persists. Inspect page source and the event-page network/analytics requests for absence of both
bearer tokens. This smoke uses disposable test links only and does not upload or retain additional
diagnostic biometric data beyond the ordinary approved search flow.

## Rollback

Revert the implementation commit and redeploy the prior immutable image. Existing browser
`localStorage` entries may remain inert because the prior application neither reads nor sends the
feature key. No server data needs rollback. A later re-release may read those entries again; if
that is undesirable after a security incident, ship an explicit versioned browser-key cleanup as
a separately reviewed privacy change rather than broad site-data deletion.

## Open questions

None.
