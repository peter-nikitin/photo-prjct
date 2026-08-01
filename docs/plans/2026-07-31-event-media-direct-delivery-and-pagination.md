# Event Media Direct Delivery and Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this
> plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

- Date: 2026-07-31
- Status: Approved — implementation and local verification in progress; staging verification pending
- Owner: project maintainer
- Related specification:
  [`Event Media Direct Delivery and Pagination Design`](../superpowers/specs/2026-07-31-event-media-direct-delivery-and-pagination-design.md)
- Related architecture: [`Current architecture`](../architecture.md#current-architecture--implemented),
  [`Accepted constraints`](../architecture.md#accepted-constraints), and
  [`Security, privacy, and legal boundaries`](../architecture.md#security-privacy-and-legal-boundaries)
- Related ADRs: [ADR 0006](../adr/0006-yandex-object-storage-media.md),
  [ADR 0013](../adr/0013-use-direct-private-object-storage-ingestion.md),
  [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md), and
  [ADR 0020](../adr/0020-use-signed-direct-object-storage-media-delivery.md)
- ADR impact: Resolved — ADR 0020 is accepted and supersedes only ADR 0019's inline-media
  transport rule.

## Goal

Deliver the approved direct Object Storage media delivery and bounded gallery/result presentation so
the current VM is not the image data plane for the next event.

## Scope

Implements the approved specification without scope changes. In particular, it retains the current
1600px `preview-small-v1` for tiles and face embedding, uses originals only in lightbox, and adds
neither a new derivative nor a CDN.

## Acceptance criteria

Implement every criterion in the [approved specification](../superpowers/specs/2026-07-31-event-media-direct-delivery-and-pagination-design.md#acceptance-criteria).
Delivery additionally requires a real private-object staging smoke for both redirect variants and a
20,000-row presentation check before enabling the release for the event.

## File structure

- `src/backend/ingestion/storage.py`: add a narrow exact-object inspect-and-sign read capability;
  it owns no presentation or authorization rules.
- `src/backend/picflow/gallery.py`: select the already authorized gallery key and expose a signed
  redirect target without reading image bytes.
- `src/backend/config/views.py`: authorize normal free-gallery media, issue redirects, and request
  one bounded gallery page.
- `src/backend/selfie_search/services/results.py`: resolve a bounded ordered page from the immutable
  result snapshot without reranking it.
- `src/backend/selfie_search/views.py`: authorize result media redirects and request one bounded
  ready-result page.
- `src/backend/templates/catalog/event_detail.html` and
  `src/backend/selfie_search/templates/selfie_search/result.html`: render current page cards and an
  accessible next-page action.
- `src/backend/static/ui/event-gallery.js` and its tests: progressively append a next-page HTML
  fragment and initialize newly appended lightbox cards; no client state becomes authoritative.
- `src/backend/entrypoint.sh`, deployment contract tests, and `.env.example`: make bounded Gunicorn
  process settings explicit and configurable only within approved limits.
- Existing Django, JS, visual, repository-foundation, and deployment test modules: preserve media,
  authorization, pagination, and shell contracts.

## Implementation

### Task 1: Add exact-object signing without body streaming

**Files:**

- Modify: `src/backend/ingestion/storage.py`, `src/backend/picflow/gallery.py`, and
  `src/backend/config/views.py`
- Modify tests: `src/backend/ingestion/tests/test_storage.py`,
  `src/backend/picflow/tests/test_gallery.py`, and `src/backend/picflow/tests/test_views.py`

- **Specification:** Selected Design; Gallery eligibility; Failure Semantics.
- **Depends on:** ADR 0020.
- **Produces:** a storage adapter operation that verifies one selected final object and returns a
  short-lived signed GET URL; normal-gallery media routes return redirects and never construct a
  `StreamingHttpResponse` or `CloseableMediaIterator`.

- [ ] Write failing storage and view tests for accepted preview-tile redirect, lightbox-original
  redirect, exact key selection, missing-object `404`, signing failure `503`, unknown variant, and
  unpublished/paid normal-gallery denial.
- [ ] Run the focused Django tests with CI-like DB and Django environment variables; confirm the
  redirect assertions fail against the inline-streaming baseline.
- [ ] Implement the smallest adapter, resolver, and normal-route changes. Keep signed URLs out of
  model fields, rendering contexts, exceptions, and logs; preserve stable application URLs.
- [ ] Re-run the focused tests and confirm redirect-only responses, sanitized errors, and no body
  read by Django.

### Task 2: Redirect authorized selfie-result media directly

**Files:**

- Modify: `src/backend/selfie_search/views.py`
- Modify tests: `src/backend/selfie_search/tests/test_views.py`

- **Specification:** Selfie-search eligibility; Failure Semantics.
- **Depends on:** Task 1 signing interface.
- **Produces:** `result_media` redirects only after a valid ready public token, immutable-result
  membership, event publication, and selected media eligibility are verified.

- [ ] Write failing result-media tests for free and paid ready-result member redirects, exact
  preview/original selection, non-member denial, wrong event/token denial, non-ready denial,
  missing-object `404`, and signing failure `503`.
- [ ] Run the focused selfie-search view tests and confirm they fail because the route still streams.
- [ ] Replace result-media streaming with the shared signing path while retaining bearer response
  headers and all ADR 0019 access checks.
- [ ] Re-run focused tests and prove the normal paid-gallery route remains denied.

### Task 3: Add a reusable signed opaque cursor and bounded normal-gallery pages

**Files:**

- Create: `src/backend/picflow/pagination.py` and `src/backend/picflow/tests/test_pagination.py`
- Modify: `src/backend/picflow/gallery.py`, `src/backend/config/views.py`,
  `src/backend/templates/catalog/event_detail.html`, and `src/backend/picflow/tests/test_views.py`

- **Specification:** Pagination Contract — Normal gallery; Browser Behavior.
- **Depends on:** None; may proceed after Task 1 without sharing mutable state.
- **Produces:** a versioned signed cursor bound to a collection identity and a gallery-page result
  containing at most 100 photos plus a nullable next cursor.

- [ ] Write failing unit and view tests for first page, next page, no duplicate photo across pages,
  ascending photo-ID order, 20,000 eligible-row bounded query/result behavior, final-page null
  cursor, and malformed/tampered/event-mismatched cursor `404`.
- [ ] Run the focused tests and confirm page behavior fails because the view materializes all
  gallery photos.
- [ ] Implement cursor signing/validation and keyset pagination. Bind normal-gallery cursors to the
  event and gallery context; never use offset pagination or a client-supplied page size.
- [ ] Render page-local count/controls without claiming the page length is the event total, and
  preserve server-rendered no-JavaScript next-page navigation.
- [ ] Re-run focused tests and inspect query count/card count for the synthetic 20,000-row case.

### Task 4: Paginate immutable ready selfie-search results without reranking

**Files:**

- Modify: `src/backend/selfie_search/services/results.py`, `src/backend/selfie_search/views.py`,
  `src/backend/selfie_search/templates/selfie_search/result.html`, and
  `src/backend/selfie_search/tests/test_views.py`
- Create: `src/backend/selfie_search/tests/test_results.py`
- Modify tests: `src/backend/selfie_search/tests/test_views.py`

- **Specification:** Pagination Contract — Ready selfie-search result; Selfie-search eligibility.
- **Depends on:** Task 3 cursor interface.
- **Produces:** a search-token-bound ready-result page of at most 100 currently displayable saved
  rows in persisted rank/photo-ID order, with a nullable next cursor and no new search computation.

- [ ] Write failing service and view tests for first/later pages, stable snapshot ordering after
  current publication filtering, cursor search-token mismatch `404`, no pagination for non-ready
  state, and no reranking or inclusion of photos outside the saved result.
- [ ] Run the focused tests and confirm they fail because the ready view materializes every result.
- [ ] Implement the bounded result query and render the page controls using the existing
  token-scoped media URL builder.
- [ ] Re-run focused tests and confirm paid result-media authorization remains tied to the complete
  immutable membership rather than only the displayed page.

### Task 5: Add progressive “Show more” behavior without a client-only data path

**Files:**

- Modify: `src/backend/static/ui/event-gallery.js`,
  `src/backend/templates/catalog/event_detail.html`, and
  `src/backend/selfie_search/templates/selfie_search/result.html`
- Modify tests: `tests/js/event-gallery.test.js`, `tests/visual/visual.spec.js`, associated visual
  fixtures/snapshots, and relevant Django template tests

- **Specification:** Browser Behavior; Pagination Contract.
- **Depends on:** Tasks 3 and 4 page URLs and fragment markup.
- **Produces:** accessible link-first next-page navigation plus optional JavaScript append behavior
  that initializes lightbox cards added after the first page.

- [ ] Write failing JS and visual tests for a visible next-page action, successful append,
  keyboard-accessible cards after append, request failure preserving the link fallback, and normal
  navigation when JavaScript is disabled.
- [ ] Run the targeted Node and Playwright cases and confirm absent controls/append behavior fail.
- [ ] Implement unobtrusive interception of the next-page link and a same-origin HTML-fragment
  request. Keep the link usable without JavaScript and avoid retaining signed URLs or an unbounded
  client-side source list.
- [ ] Re-run targeted JS and visual tests at desktop and mobile sizes.

### Task 6: Make Gunicorn bounded and deployable on the selected VM profile

**Files:**

- Modify: `src/backend/entrypoint.sh`, `.env.example`, `deploy/apply-deployment.sh`, and relevant
  deployment workflow/contract tests under `tests/deployment/`

- **Specification:** Gunicorn Bound; Failure Semantics.
- **Depends on:** None.
- **Produces:** five workers and two threads per worker, finite timeout and worker recycling, with
  deployment validation rejecting absent or unsafe process configuration.

- [ ] Write failing shell/deployment contract tests for the exact bounded Gunicorn invocation and
  environment propagation without exposing secrets.
- [ ] Run targeted deployment tests and `sh -n src/backend/entrypoint.sh`; confirm the invocation
  is currently unbounded/default.
- [ ] Implement the explicit bounded configuration and validate it before candidate deployment.
  Do not add image-serving behavior or alter worker container resource limits in this task.
- [ ] Re-run shell syntax and targeted deployment tests; inspect the generated candidate environment
  for the approved values and absence of credentials in logs.

### Task 7: Reconcile documentation, verify, and prepare staged rollout

**Files:**

- Modify: `docs/architecture.md`, `docs/product-jobs.md`, and `docs/engineering-jobs.md` only where
  verified implementation evidence changes their status; retain ADR 0020 and this plan as the
  decision/delivery records.
- Modify tests or runbooks only when rollout commands reveal a concrete contract gap.

- **Specification:** Acceptance Criteria; Rollout and Revisit Trigger.
- **Depends on:** Tasks 1–6.
- **Produces:** verified implementation evidence, an operationally safe activation sequence, and a
  documented rollback decision.

- [ ] Run the full CI-equivalent repository suite after targeted checks pass, including Python,
  Ruff, mypy, Django checks/migration drift, JavaScript, and visual tests.
- [ ] Build immutable web/worker images and deploy the normal candidate-and-rollback path. The
  candidate contains direct delivery; if staging checks fail, restore the prior immutable image.
- [ ] On staging, use one eligible preview-required photo and one lightbox original to prove
  redirect authorization and complete direct body transfer. Test normal paid-gallery denial and a
  paid ready-result member separately.
- [ ] Create or load a controlled 20,000-row event fixture without copying real media, then measure
  first-page card count, query time/count, next-page response, and Gunicorn process configuration.
- [ ] Record actual evidence before changing product/engineering job statuses. If direct transfer,
  authorization, or pagination fails, stop and use the prior image rather than adding infrastructure
  during the event window.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with ADR 0020, ADR 0019, and the approved specification.
- [ ] Update `docs/architecture.md` from “accepted but not implemented” to verified implemented
  direct delivery only after staging evidence exists.
- [ ] Keep ADR 0019's broader selfie-search decision accepted; do not alter its saved-result,
  bearer, or paid-result boundaries.
- [ ] Have the independent reviewer classify every finding as blocking or future under `AGENTS.md`.
- [ ] The root controller stages only exact task files, reruns final verification, and creates one
  final commit after reviewer approval.

## Verification

Run with CI-like Django variables and the repository virtual environment:

```sh
DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost .venv/bin/pytest -q src/backend/ingestion/tests/test_storage.py src/backend/picflow/tests/test_pagination.py src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py src/backend/selfie_search/tests/test_results.py src/backend/selfie_search/tests/test_views.py
DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost .venv/bin/python src/backend/manage.py check
DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost .venv/bin/python src/backend/manage.py makemigrations --check --dry-run
.venv/bin/ruff check src/backend tests
.venv/bin/mypy src/backend src/worker/photo_worker
npm test
npx playwright test
```

Expected outcomes: targeted redirect/pagination tests pass; Django checks and migration drift are
clean; lint/type/JS/visual suites have no new failures. Before delivery, run the exact repository
CI workflow equivalent and record category counts.

## Operational impact and rollout

The release changes public media transport and therefore requires the ADR 0020 staging gate before
the planned VM resize. First deploy the application candidate, verify its bounded Gunicorn command,
then run direct Object Storage smoke checks with private real objects and synthetic pagination data.
Do not treat a `302` alone as successful delivery: verify the final browser retrieval, redirects
after signed-link expiry, and sanitized application logs.

Only after those checks succeed may the separately approved cloud change make the VM
non-preemptible and resize it to 8 vCPU, 32 GiB RAM, and 100 GiB network SSD. The cloud mutation is
outside this code plan and needs fresh pricing/availability approval immediately before execution.

## Rollback

Revert to the prior immutable application image to restore inline media streaming and the
pre-pagination presentation. No database migration, media rewrite, or Object Storage mutation is
introduced. Signed URLs already issued remain usable only to their configured expiry; previously
delivered originals cannot be revoked. Keep the old image/deployment marker available until direct
delivery, paid-result access, and pagination are independently verified.

## Open questions

None. Future tile-size reduction, CDN adoption, and object-storage transfer-cost optimization have
the measurable revisit triggers recorded in the approved specification.
