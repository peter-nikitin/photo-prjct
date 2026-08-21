# Product Jobs

This registry tracks customer-facing jobs for FindMe Photo. Product jobs use only these actors:
Visitor, Customer, Photographer, and Operator.

## Job format

Each job has a stable `PJ-NNN` identifier and uses this Jobs-to-be-Done form:

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
| Delivered | The job is available in the product, but its expected outcome has not yet been validated with sufficient evidence. |
| Validated | Evidence shows that the delivered job works and supports its expected outcome. |
| Deferred | Work on the job is intentionally postponed, with the reason recorded. |

## Current state

| Job | Actor | Summary | Status | Last updated |
| --- | --- | --- | --- | --- |
| PJ-001 | Operator | Publish an event | Validated | 2026-07-17 |
| PJ-002 | Visitor | Discover published events | Validated | 2026-07-17 |
| PJ-003 | Visitor | Review event details | Validated | 2026-07-17 |
| PJ-004 | Photographer | Upload an event batch | Validated | 2026-08-15 |
| PJ-005 | Visitor | Browse an event gallery | Validated | 2026-08-21 |
| PJ-006 | Operator | Review processing results | Candidate | 2026-07-17 |
| PJ-007 | Customer | Find photos by bib | Candidate | 2026-07-17 |
| PJ-008 | Customer | Find photos by face | In progress | 2026-08-20 |
| PJ-009 | Visitor | Receive a free-event original | Delivered | 2026-08-21 |
| PJ-010 | Customer | Purchase selected photos | Candidate | 2026-08-21 |
| PJ-011 | Customer | Download purchased photos | Candidate | 2026-08-20 |
| PJ-012 | Visitor | Jump to a known gallery page | Delivered | 2026-08-21 |
| PJ-013 | Customer | Report selfie-search quality | In progress | 2026-08-05 |
| PJ-014 | Customer | Return to saved selfie-search results | In progress | 2026-08-04 |
| PJ-015 | Customer | Filter an event gallery by capture time | Delivered | 2026-08-21 |
| PJ-016 | Customer | Select paid event photos | In progress | 2026-08-21 |

## Job details

References below to `staging` or `production` are dated delivery evidence from the former
two-environment model. They do not describe a current deployment target; ADR 0028 governs the
canonical deployment and runtime feature gates.

### PJ-001 — Operator — Publish an event

When an event is ready for customers, I want to create and publish its catalog record, so I can make
it discoverable without developer assistance.

- Status: Validated
- Evidence: [`src/backend/picflow/tests/test_admin.py::EventAdminTests::test_admin_creates_and_publishes_event`](../src/backend/picflow/tests/test_admin.py)
- Last updated: 2026-07-17

### PJ-002 — Visitor — Discover published events

When I arrive at FindMe Photo, I want to browse only published events in a useful order, so I can
choose the event I attended.

- Status: Validated
- Evidence: [`src/backend/picflow/tests/test_views.py::PageTests::test_catalog_only_shows_published_events`](../src/backend/picflow/tests/test_views.py) and [`src/backend/picflow/tests/test_views.py::PageTests::test_catalog_orders_upcoming_then_past`](../src/backend/picflow/tests/test_views.py)
- Last updated: 2026-07-17

### PJ-003 — Visitor — Review event details

When I find an event, I want to open its public details, so I can confirm it is the event I attended.

- Status: Validated
- Evidence: [`src/backend/picflow/tests/test_views.py::PageTests::test_event_detail_renders_published_event`](../src/backend/picflow/tests/test_views.py) and [`src/backend/picflow/tests/test_views.py::PageTests::test_event_detail_returns_404_for_draft_event`](../src/backend/picflow/tests/test_views.py)
- Last updated: 2026-07-17

### PJ-004 — Photographer — Upload an event batch

When I finish photographing an event, I want to upload a batch with event and capture context, so I
can submit it for processing.

- Status: Validated
- Evidence: The implemented request-driven uploader creates owned batches, transfers JPEGs directly
  to the private incoming prefix, confirms and promotes verified originals, and lets a returning
  photographer explicitly reselect files for an unfinished owned batch. PostgreSQL reconstructs the
  open-page queue, skips server-confirmed items, and leaves missing or ambiguous files unresolved;
  closing the page still stops transfer. Local 2026-08-01 evidence: 174 focused ingestion/visual
  Python tests passed (54 subtests), 38 JavaScript tests passed, 60 containerized Playwright tests
  passed, and the full Python suite passed 890 tests with 3 skipped at 82.71% branch coverage.
  `manage.py check` and migration drift passed. After independently reviewed test-only corrections,
  the full-repository Ruff format check, Ruff lint, mypy over 131 source files, and
  `git diff --check` also passed.
  Local 2026-08-15 folder evidence adds event-admin management and one shared queue for named
  folders plus `Без папки`; each assignment survives registration, retry, confirmation, and resume.
  Existing upload ownership and incoming-object authorization remain unchanged. Moving already
  uploaded photos, including a photographer-facing mass editor, is deliberately deferred.
  This is local automated evidence only; CI, canonical deployment, and customer validation are not
  recorded.
- Last updated: 2026-08-15

### PJ-005 — Visitor — Browse an event gallery

When an event has published photos, I want to browse its gallery, so I can inspect photos from that
event.

- Status: Validated
- Evidence: [`src/backend/picflow/tests/test_views.py::PageTests::test_event_detail_builds_ordered_gallery_without_storage`](../src/backend/picflow/tests/test_views.py), [`src/backend/picflow/tests/test_views.py::PageTests::test_event_detail_excludes_legacy_other_event_and_paid_originals`](../src/backend/picflow/tests/test_views.py), [`src/backend/picflow/tests/test_views.py::PageTests::test_event_detail_gallery_markup_and_loading_policy`](../src/backend/picflow/tests/test_views.py), Task 6's passing keyboard, pointer, touch, focus-restoration, no-JavaScript, populated, and empty interaction/snapshot evidence in [`tests/visual/visual.spec.js`](../tests/visual/visual.spec.js), and [PR #45 CI run 29693681091](https://github.com/peter-nikitin/photo-prjct/actions/runs/29693681091), which passed all 44 visual tests for the CI-tested implementation commit `7d6a718` after the local Docker/`networkidle` failure; later docs-only evidence commits were not included in that run.
  Local 2026-08-15 evidence adds stable repeated-folder GET filtering: named folders and
  `Без папки` come only from the base eligible gallery, selections combine with capture time,
  survive numbered pagination, and invalid or foreign values cannot widen the event gallery.
  Existing gallery-media authorization is unchanged. This new evidence is local only; it does not
  claim CI, canonical deployment, or customer validation.
  On 2026-08-20, 215 focused Django tests passed for the paid-watermark repository slice: newly
  confirmed paid photos use the explicit policy, appear only after accepted watermark evidence,
  present the watermark in both gallery roles, and omit the download action. This is local
  repository evidence only. Current main `be22bdd` passed [CI run 32457775703](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775703), and its automatic [Deploy run 32457775668](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775668) succeeded. The paid-watermark gate and real artwork were not directly observed as active; purchase, entitlement, and purchased-original delivery remain unimplemented.
- Last updated: 2026-08-21

### PJ-006 — Operator — Review processing results

When automated processing fails or produces uncertain metadata, I want to inspect and correct the
result, so I can keep published search data reliable.

- Status: Candidate
- Evidence: [Target MVP architecture — Moderation](architecture.md#target-mvp-architecture--proposed)
- Last updated: 2026-07-17

### PJ-007 — Customer — Find photos by bib

When I know a participant bib number, I want to search within one event, so I can find likely photos
quickly.

- Status: Candidate
- Evidence: [Target MVP architecture — Search](architecture.md#search)
- Last updated: 2026-07-17

### PJ-008 — Customer — Find photos by face

When I have an appropriate selfie or find an eligible gallery face in a selected event, I want to
search within that event, so I can review probable matches.

- Status: In progress
- Evidence: [ADR 0019](adr/0019-use-public-event-selfie-search.md), the
  [public selfie-search implementation plan](plans/2026-07-30-public-selfie-search.md), and
  [`tests/processing/test_selfie_search_e2e.py`](../tests/processing/test_selfie_search_e2e.py)
  provide repository evidence plus local real YuNet/SFace inference for the selfie query; accepted
  deterministic gallery fixtures cover both face generations, including a verified preview
  publication and current enrollment into `2/face_embedding/2`. The existing selfie-upload path
  covers event isolation, probable matches, selfie cleanup, stable results, and the narrow
  paid-result media exception. Public selfie search is now always available when its existing
  processing prerequisites are healthy; the retired availability switch is no longer active.
  On 2026-07-31, the then-designated staging deployment deployed branch `c62508a` with that path enabled: the `selfie-search/` one-day lifecycle rule and scratch-object
  put/head/grant/delete preflight passed; six legacy face jobs produced four accepted event
  embeddings; and a live Unicode event upload reached a stable ready bearer URL with the expected
  original. The temporary selfie was deleted before publication, including from the bucket prefix.
  A temporary paid-event check kept normal media denied while bearer-result media succeeded. On
  2026-08-02, the then-designated staging deployment deployed immediate queued-result navigation and then direct event-cohort
  ranking in PRs [#80](https://github.com/peter-nikitin/photo-prjct/pull/80) and
  [#82](https://github.com/peter-nikitin/photo-prjct/pull/82). New searches persist only eligible
  counts and matched results instead of one candidate row per eligible face; legacy frozen-candidate
  searches remain readable. Task 8 adds the accepted ADR 0025 path: immutable event-scoped face
  cluster corpora, strict-anchor direct-first expansion, immutable source provenance, and
  source-separated aggregate reporting. `SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED=False` remains
  the repository default; no corpus, canonical-deployment, or customer activation and no measured recall or
  precision outcome is claimed. The complete release gate is still pending independent whole-branch
  review.
  This is dated former-topology evidence for the existing selfie-upload path only; the current
  activation boundary is the canonical deployment and its feature gate.

  The gallery-photo query path is defined by [ADR 0024](adr/0024-use-gallery-face-as-search-query.md)
  and the approved [gallery-photo search design](superpowers/specs/2026-08-05-gallery-face-selector-design.md).
  Current local evidence for the combined gallery-photo implementation is 145 focused Python tests,
  70 JavaScript tests for the production markup and chooser behavior, and 83 visual tests covering
  the zero-, one-, two-, and four-face event-gallery fixture at desktop and 390px mobile widths.
  The root `make check` also passes with 1,256 tests passed and 3 skipped, 83.28% coverage, and
  clean system/migration checks. These are local repository checks only; no canonical deployment or
  customer validation is claimed for the gallery-photo path.
  On 2026-08-20, the same focused Django run covered ready paid-result presentation: saved
  membership remains unchanged, a new paid-policy member is shown only after accepted watermark
  evidence, both semantic media routes select that watermark, and original/download requests deny
  before signing. This is local repository evidence only; the gate remains off and no real
  activation or customer outcome is claimed.
- Last updated: 2026-08-20

### PJ-009 — Visitor — Receive a free-event original

When an event offers free photos, I want a controlled anonymous download, so I can receive the
original without exposing its permanent storage key.

- Status: Delivered
- Evidence: The merged anonymous download route, signed original resolver, and its regression in
  [`src/backend/picflow/tests/test_views.py`](../src/backend/picflow/tests/test_views.py) deliver
  controlled free-event original access without exposing a permanent storage key. Current-main CI
  run `32457775703` and automatic Deploy run `32457775668` succeeded for the deployed commit.
  This delivery evidence does not establish customer-outcome validation.
- Last updated: 2026-08-21

### PJ-010 — Customer — Purchase selected photos

When I select paid photos, I want to complete an order and payment, so I can obtain download
entitlement.

- Status: Candidate
- Evidence: [Purchase and download](architecture.md#purchase-and-download). The merged
  anonymous-cart slice is selection only; current main `be22bdd` passed [CI run 32457775703](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775703), and its automatic [Deploy run 32457775668](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775668) succeeded. Checkout, payment, orders, entitlement, and purchased-original delivery are not implemented.
- Last updated: 2026-08-21

### PJ-011 — Customer — Download purchased photos

When my order is paid, I want to download only its entitled photos, so I can receive the files I
purchased securely.

- Status: Candidate
- Evidence: [Purchase and download](architecture.md#purchase-and-download). Watermarked paid
  previews deny original download; purchased-original delivery and its entitlement policy are not
  implemented.
- Last updated: 2026-08-20

### PJ-016 — Customer — Select paid event photos

When I browse a paid event with watermarked previews, I want to add or remove eligible photos in an
anonymous event-specific cart, so I can preserve a selection before checkout is available.

- Status: In progress
- Evidence: Merged repository tests cover staff-gated selection from the paid gallery and saved
  selfie result, browser/event isolation, reload persistence, current-price totals, pruning,
  request-time expiry, and original/download denial. Current main `be22bdd` passed [CI run 32457775703](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775703), and its automatic [Deploy run 32457775668](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775668) succeeded. The `paid-photo-cart` gate is absent/off by default and was not directly observed as active; real paid assets, public activation, legal-cookie review, customer outcome, checkout, payment, order, entitlement, and original delivery remain unverified or unimplemented.
- Last updated: 2026-08-21

### PJ-012 — Visitor — Jump to a known gallery page

When a paginated photo gallery has many pages and I know the page number I need, I want to go
directly to it, so I can reach that part of the photo list quickly.

- Status: Delivered
- Evidence: Merge commit `02a8036` delivered the shared
  [`src/backend/templates/ui/gallery_pagination.html`](../src/backend/templates/ui/gallery_pagination.html),
  focused event pagination coverage in [`src/backend/picflow/tests/test_views.py::GalleryPageTests::test_event_detail_uses_numbered_pages_in_filename_order`](../src/backend/picflow/tests/test_views.py), focused selfie pagination coverage in [`src/backend/selfie_search/tests/test_views.py::PublicSelfieResultViewTests::test_ready_page_uses_numbered_pages_without_reranking_or_expanding_membership`](../src/backend/selfie_search/tests/test_views.py), and four production-screen visual baselines covered by [`tests/visual/visual.spec.js`](../tests/visual/visual.spec.js): [`desktop-event-gallery-populated.png`](../tests/visual/visual.spec.js-snapshots/desktop-event-gallery-populated.png), [`mobile-event-gallery-populated.png`](../tests/visual/visual.spec.js-snapshots/mobile-event-gallery-populated.png), [`desktop-selfie-search-ready.png`](../tests/visual/visual.spec.js-snapshots/desktop-selfie-search-ready.png), and [`mobile-selfie-search-ready.png`](../tests/visual/visual.spec.js-snapshots/mobile-selfie-search-ready.png). Current-main CI run `32457775703` and automatic Deploy run `32457775668` succeeded. This delivery evidence does not establish customer-outcome validation.
- Last updated: 2026-08-21

### PJ-013 — Customer — Report selfie-search quality

When a selfie search finishes, I want to report whether its result was useful and optionally mark
which returned photos contain me, so I can help FindMe Photo investigate failures and improve
search quality without selecting the same selfie again, while retaining the ability to disable
future feedback prompts in my browser.

- Status: In progress
- Evidence: The repository implementation covers the approved browser-local selfie reuse,
  compact failed/empty report, in-gallery result marking, consent/contact validation, immutable
  feedback schema, restricted audited admin inspection, and guarded feedback-bucket lifecycle.
  Focused Django/deployment tests, JavaScript tests, containerized visual tests, and the complete
  CI-equivalent release gate pass on this branch. The
  [selfie-search quality feedback specification](superpowers/specs/2026-08-04-selfie-search-quality-feedback-design.md),
  [implementation plan](plans/2026-08-04-selfie-search-quality-feedback.md), and
  [ADR 0023](adr/0023-store-consented-selfie-search-feedback.md) define the accepted boundary.
  `SELFIE_FEEDBACK_ENABLED=False` remains the default; no canonical-deployment activation or
  real customer-outcome evidence is claimed. The face-cluster increment also provides the
  aggregate-only `report_face_cluster_expansion` command, which derives direct-primary,
  cluster-primary, dual-evidence, and labelled-sample feedback metrics from immutable server-side
  provenance without accepting a customer-supplied source field. This is implementation evidence,
  not evidence that customers have used the expanded path.
- Last updated: 2026-08-05

### PJ-014 — Customer — Return to saved selfie-search results

When I have previously searched for photos by selfie on this device, I want to see my saved
results on the event page, so I can reopen the result I need without selecting and uploading the
selfie again.

- Status: In progress
- Evidence: Repository-only implementation adds a versioned browser `localStorage` list of
  canonical event-scoped result paths and timestamps, with Django rendering only a non-secret
  event slug and an initially hidden container. [`tests/js/selfie-search-history.test.js`](../tests/js/selfie-search-history.test.js)
  passed 16/16 cases; focused
  [`src/backend/selfie_search/tests/test_views.py`](../src/backend/selfie_search/tests/test_views.py)
  and [`src/backend/picflow/tests/test_views.py`](../src/backend/picflow/tests/test_views.py)
  passed 81 cases; `npm run test:js` passed 84/84 cases; `make check` passed 1,239 tests with
  3 skipped at 83.20% coverage, plus Ruff, configured mypy, Django system, and migration-drift
  checks; and `npm run test:visual` passed 81/81 cases. Existing non-expiring bearer-result URLs
  remain governed by [ADR 0019](adr/0019-use-public-event-selfie-search.md). No deployment,
  customer, or validated delivery evidence is recorded.
- Last updated: 2026-08-04

### PJ-015 — Customer — Filter an event gallery by capture time

When I know approximately when my photos were taken at an event, I want to narrow that event's
gallery by event-local capture time, so I can reach likely photos without uploading a selfie or
storing a manual query.

- Status: Delivered
- Evidence: The approved [event gallery time-filter design](superpowers/specs/2026-08-08-event-gallery-time-filter-design.md)
  and [capture-time projection design](superpowers/specs/2026-08-08-photo-capture-time-projection-design.md)
  define the event-local form, exact inclusive bounds, privacy boundary, and two-release gate.
  Release B commit `d5b21e4` delivered the projection-only filtered reader after clean immutable-
  local-clone reconciliation and a [sanitized 2x benchmark](performance/2026-08-08-event-gallery-time-filter-local-clone.json)
  for its first, midpoint, and last pages. The integrated gallery/processing/projection/deployment
  suite, visual suite (92 tests), and separate `make check` exit clean locally. This delivery
  evidence does not establish customer-outcome validation.
- Last updated: 2026-08-21

Visual design-reference screens are not delivery evidence.

## Status log

This log is append-only.

| Date | Job | Previous status | New status | Evidence or reason |
| --- | --- | --- | --- | --- |
| 2026-07-17 | PJ-001 | Not recorded | Validated | [`src/backend/picflow/tests/test_admin.py::EventAdminTests::test_admin_creates_and_publishes_event`](../src/backend/picflow/tests/test_admin.py) |
| 2026-07-17 | PJ-002 | Not recorded | Validated | [`src/backend/picflow/tests/test_views.py::PageTests::test_catalog_only_shows_published_events`](../src/backend/picflow/tests/test_views.py) and [`src/backend/picflow/tests/test_views.py::PageTests::test_catalog_orders_upcoming_then_past`](../src/backend/picflow/tests/test_views.py) |
| 2026-07-17 | PJ-003 | Not recorded | Validated | [`src/backend/picflow/tests/test_views.py::PageTests::test_event_detail_renders_published_event`](../src/backend/picflow/tests/test_views.py) and [`src/backend/picflow/tests/test_views.py::PageTests::test_event_detail_returns_404_for_draft_event`](../src/backend/picflow/tests/test_views.py) |
| 2026-07-17 | PJ-004 | Not recorded | Candidate | [Target MVP architecture — Ingestion](architecture.md#target-mvp-architecture--proposed) |
| 2026-08-01 | PJ-004 | Candidate | Validated | Implemented resumable owned-batch upload is locally evidenced by focused, JavaScript, containerized visual, full Python with 82.71% branch coverage, Django-check, migration-drift, Ruff format/lint, mypy, and diff-check runs. |
| 2026-07-17 | PJ-005 | Not recorded | Candidate | [Architecture evolution stages — Photo-bank core](architecture.md#evolution-stages) |
| 2026-07-17 | PJ-006 | Not recorded | Candidate | [Target MVP architecture — Moderation](architecture.md#target-mvp-architecture--proposed) |
| 2026-07-17 | PJ-007 | Not recorded | Candidate | [Target MVP architecture — Search](architecture.md#search) |
| 2026-07-17 | PJ-008 | Not recorded | Candidate | [Target MVP architecture — Search](architecture.md#search) and [Security, privacy, and legal boundaries](architecture.md#security-privacy-and-legal-boundaries) |
| 2026-07-17 | PJ-009 | Not recorded | Candidate | [Security, privacy, and legal boundaries](architecture.md#security-privacy-and-legal-boundaries) |
| 2026-07-17 | PJ-010 | Not recorded | Candidate | [Target MVP architecture — Purchase and download](architecture.md#purchase-and-download) |
| 2026-07-17 | PJ-011 | Not recorded | Candidate | [Target MVP architecture — Purchase and download](architecture.md#purchase-and-download) |
| 2026-07-19 | PJ-005 | Candidate | Validated | Automated page, eligibility, markup, accessibility, interaction, and visual coverage verifies browsing eligible uploaded photos for a published free event. |
| 2026-07-19 | PJ-005 | Validated | Validated | Clarified evidence: automated page, eligibility, and markup tests plus Task 6's passing interaction and inspected snapshot evidence support validation; complete current-HEAD visual evidence awaits PR CI after a local Docker/`networkidle` timeout. |
| 2026-07-19 | PJ-005 | Validated | Validated | PR #45 CI run 29693681091 supplied the pending current-HEAD evidence: all 44 visual tests passed for `7d6a718`; the earlier local Docker/`networkidle` timeout remains an infrastructure-only boundary, not passing evidence. |
| 2026-07-19 | PJ-005 | Validated | Validated | Provenance correction: PR #45 CI run 29693681091 passed all 44 visual tests for the CI-tested implementation commit `7d6a718`; later docs-only evidence commits were not included in that run. |
| 2026-07-31 | PJ-008 | Candidate | In progress | The accepted ADR and plan have a locally verified public event-scoped implementation and staging activation on `c62508a`: lifecycle/preflight passed, representative face embeddings were accepted, and a live Unicode event selfie reached a stable ready bearer result with cleanup and original-size media verified. Paid-result media remained available only through its bearer result. Production is not activated. |
| 2026-08-04 | PJ-012 | Not recorded | In progress | [PR #93](https://github.com/peter-nikitin/photo-prjct/pull/93) and locally verified Django pagination and visual contracts: the focused event/selfie view tests plus four production-screen baselines covered by [`tests/visual/visual.spec.js`](../tests/visual/visual.spec.js). |
| 2026-08-04 | PJ-013 | Not recorded | Candidate | Approved product design recorded in the selfie-search quality feedback specification; implementation planning awaits explicit acceptance of the required privacy/retention ADR. |
| 2026-08-04 | PJ-013 | Candidate | Planned | ADR 0023 was explicitly accepted and the decision-complete selfie-search quality feedback implementation plan was approved for execution. |
| 2026-08-04 | PJ-013 | Planned | In progress | Repository implementation and automated release-gate evidence are complete; the feature remains disabled by default and has no staging/production activation or real customer-outcome evidence. |
| 2026-08-04 | PJ-014 | Not recorded | Candidate | Approved product design proposes a device-local list of saved bearer results on each event page; implementation planning has not started. |
| 2026-08-04 | PJ-014 | Candidate | In progress | Repository-only implementation and reconciled release-gate evidence: 16/16 device-local history JS cases, 81 focused Django selfie-search/event-view cases, 84/84 full JavaScript cases, `make check` with 1,239 passed and 3 skipped at 83.20% coverage, and 81/81 visual cases passed. No deployment or customer-outcome evidence is claimed. |
| 2026-08-08 | PJ-015 | Not recorded | In progress | Release A writer and local accepted-clone projection reconciliation are complete while the gallery remains a direct reader; Release B cutover and customer acceptance remain gated on deployment, live reconciliation, and performance evidence. |
| 2026-08-08 | PJ-015 | In progress | In progress | Release A staging operation is accepted; the Release B projection reader has clean local reconciliation and aggregate first/midpoint/last 2x evidence. Review, PR/CI, deployment, live acceptance, and customer outcome remain pending. |
| 2026-08-15 | PJ-004 | Validated | Validated | Local automated folder coverage verifies event-admin management plus durable mixed-folder assignment through registration, retry, confirmation, and resume in one owned upload batch. No CI, deployment, staging, or customer evidence is claimed; mass reassignment remains deferred. |
| 2026-08-15 | PJ-005 | Validated | Validated | Local automated gallery coverage verifies stable repeated-folder GET filtering over the existing eligible event gallery, including `Без папки`, capture-time composition, pagination, and invalid/foreign-value rejection. Existing media authorization is unchanged; no CI, deployment, staging, or customer evidence is claimed. |
| 2026-08-20 | PJ-016 | Not recorded | In progress | Local gated cart-selection implementation is covered by repository tests; activation and purchase remain pending. |
| 2026-08-21 | PJ-009 | Candidate | Delivered | Merge commit `9cca069` delivers the anonymous download route, signed original resolver, and regression; current-main CI/deployment evidence supports delivery, while customer outcome is not validated. |
| 2026-08-21 | PJ-012 | In progress | Delivered | Merge commit `02a8036` plus current-main CI/deployment evidence deliver numbered gallery and selfie-result pagination; customer outcome is not validated. |
| 2026-08-21 | PJ-015 | In progress | Delivered | Release B commit `d5b21e4` is delivery evidence for projection-backed capture-time filtering; customer outcome is not validated. |
