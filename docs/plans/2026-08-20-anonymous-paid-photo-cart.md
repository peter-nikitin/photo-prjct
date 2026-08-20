# Anonymous Paid-Photo Cart Implementation Plan

- Date: 2026-08-20
- Status: Approved by maintainer instruction on 2026-08-20
- Owner: project maintainer
- Related specification:
  [`docs/superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md`](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md)
- Related architecture:
  [`docs/architecture.md`](../architecture.md#target-mvp-architecture--proposed),
  [purchase and download](../architecture.md#purchase-and-download), and
  [security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- Related ADRs:
  [ADR 0001](../adr/0001-django-modular-monolith.md),
  [ADR 0002](../adr/0002-postgresql-system-of-record.md),
  [ADR 0028](../adr/0028-operate-one-canonical-deployment.md),
  [ADR 0029](../adr/0029-use-watermarked-previews-for-paid-photos.md), and
  [ADR 0030](../adr/0030-use-anonymous-server-side-event-carts.md)
- ADR impact: implements accepted ADR 0030 and consumes, without weakening, ADR 0029's paid-media
  presentation and original-denial boundary.

## Goal

Deliver the approved [anonymous paid-photo cart
outcome](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#outcome) from current
paid-photo selection through an editable event cart and exact total, ending before checkout,
payment, entitlement, or original delivery.

## Scope

Implement the approved specification without changing its scope.

The paid-watermarked-preview branch must complete independent review and verification before work
on shared gallery, processing, view, template, or media paths. The watermark-independent foundation
in Task 0 may start from reviewed watermark SHA `acac975`. Rebase this branch again onto the final
reviewed watermark SHA before Task 1, retain its `GalleryPhoto.photo_id`, nullable
`download_url`, stable `data-photo-id`/action-container markup, and `PublicMediaResolver` authority,
and use the next available picflow migration after its `0012`. If the final seam differs from the
accepted watermark plan, reconcile this plan's exact names before dispatching code; do not restore
unconditional downloads or copy watermark-evidence joins into Commerce.

Execution must use `$execute-implementation-plan`. No task activates either runtime gate, installs
a cron on a host, changes legal copy, deploys code, or mutates cloud resources.

## Acceptance criteria

Use all 24 numbered [specification acceptance
criteria](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#acceptance-criteria).
In addition:

- each task starts only after its dependencies have one approved working-tree review and task
  commit;
- migrations preserve the final watermark generation/policy constraint and contain no package,
  order, payment, entitlement, or price-snapshot fields;
- every cart-bearing response is private and the raw token is absent from database rows, rendered
  bodies, redirects, structured logs, and test failure output;
- the assembled implementation passes focused Python, JavaScript, visual, migration, deployment
  contract, and repository quality checks while both feature gates remain absent/off by default.

## Implementation

### Task 0: Build the watermark-independent pricing and cart persistence foundation

**Files:**

- Modify `src/backend/picflow/models.py`.
- Modify `src/backend/picflow/admin.py`.
- Create `src/backend/picflow/migrations/0013_event_photo_price.py`.
- Modify `src/backend/picflow/tests/test_models.py`.
- Modify `src/backend/picflow/tests/test_admin.py`.
- Modify `src/backend/picflow/tests/test_photo_migrations.py`.
- Create `src/backend/commerce/__init__.py`.
- Create `src/backend/commerce/apps.py`.
- Create `src/backend/commerce/models.py`.
- Create `src/backend/commerce/identity.py`.
- Create `src/backend/commerce/pricing.py`.
- Create `src/backend/commerce/migrations/__init__.py`.
- Create `src/backend/commerce/migrations/0001_initial.py`.
- Create `src/backend/commerce/tests/__init__.py`.
- Create `src/backend/commerce/tests/test_models.py`.
- Create `src/backend/commerce/tests/test_identity.py`.
- Create `src/backend/commerce/tests/test_pricing.py`.
- Modify `src/backend/config/settings.py`.

- **Specification:** [Event price](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#event-price),
  [Cart](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#cart),
  [Cart item](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#cart-item),
  [Anonymous Browser Identity](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#anonymous-browser-identity),
  [Pricing](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#pricing), and
  [Administration](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#administration).
- **Depends on:** reviewed watermark SHA `acac975`; no unfinished watermark processing/gallery path.
- **Produces:** nullable `Event.price_per_photo_kopecks`, exact free/paid database invariant,
  `Cart`, `CartItem`, strict 32-byte URL-safe token parsing/digest helpers, and pure current-price
  calculation/RUB formatting. It produces no cart service, route, cookie response, or UI.

- [ ] Add failing model and migration tests for free/`NULL`, paid/positive, invalid pairs, the exact
  `30000` data migration, cart/item uniqueness and ordering, no quantity/stored price, cascades, and
  digest-only persistence.
- [ ] Add failing admin tests for `Цена фотографии, ₽`, exact decimal conversion, invalid pairs,
  post-publication price edits, and the already reviewed access-type lock after the first photo.
- [ ] Add failing identity/pricing tests for cryptographic 32-byte tokens, strict malformed-token
  rejection, SHA-256 stability/non-disclosure, current integer totals, `300 ₽`, and immediate event
  price changes without any item price snapshot.
- [ ] Run
  `make test TESTS="src/backend/picflow/tests/test_models.py src/backend/picflow/tests/test_admin.py src/backend/picflow/tests/test_photo_migrations.py src/backend/commerce/tests/test_models.py src/backend/commerce/tests/test_identity.py src/backend/commerce/tests/test_pricing.py"`
  and confirm failures identify only the missing price/schema/identity/pricing foundation.
- [ ] Implement the price field and migration in the documented add/backfill/constraint order;
  preserve watermark migration `0012` and add no currency, package, order, entitlement, service,
  route, cookie, or media behavior.
- [ ] Implement the Commerce app schema, identity helpers, and pure calculation/formatting boundary.
  The Commerce initial migration depends on the new picflow price migration.
- [ ] Run the focused command and
  `sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py makemigrations --check --dry-run`;
  expect all checks to pass with no migration drift.

### Task 1: Add the authoritative purchasable-photo query after watermark completion

**Files:**

- Modify `src/backend/picflow/gallery.py`.
- Modify `src/backend/picflow/tests/test_gallery.py`.

- **Specification:** [Cart Eligibility and Authority](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#cart-eligibility-and-authority).
- **Depends on:** the final reviewed watermark branch and its schema/public-gallery interfaces.
- **Produces:** `purchasable_paid_photo_queryset(*, event: Event,
  watermarked_previews_enabled: bool) ->
  QuerySet[Photo]` as Commerce's only photo-eligibility dependency.

- [ ] Add failing queryset tests proving only a published, correctly priced, new-policy paid photo
  with mutually consistent accepted watermark evidence is returned when the watermark gate permits
  the caller. Cover draft, free, foreign, legacy, pending, failed, missing derivative, and gate-off
  cases while preserving the watermark branch's free-gallery and saved-result compatibility tests.
- [ ] Run
  `make test TESTS="src/backend/picflow/tests/test_gallery.py"` and confirm failures identify only
  the missing purchasable query.
- [ ] Implement the named queryset by composing the watermark branch's authoritative event-surface
  eligibility boundary. It must not select public bytes, sign media, or accept
  `download_url is None` as evidence.
- [ ] Run the focused command above; expect the new query and all rebased watermark gallery tests
  to pass.

### Task 2: Implement cart mutation, expiry, pruning, and cleanup services

**Files:**

- Create `src/backend/commerce/services.py`.
- Create `src/backend/commerce/management/__init__.py`.
- Create `src/backend/commerce/management/commands/__init__.py`.
- Create `src/backend/commerce/management/commands/cleanup_expired_carts.py`.
- Create `src/backend/commerce/tests/test_services.py`.
- Create `src/backend/commerce/tests/test_cleanup.py`.

- **Specification:** [Cart](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#cart),
  [Cart item](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#cart-item),
  [Anonymous Browser Identity](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#anonymous-browser-identity),
  [Pricing](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#pricing),
  [Expiry, Pruning, and Cleanup](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#expiry-pruning-and-cleanup),
  and [Failure and Consistency Semantics](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#failure-and-consistency-semantics).
- **Depends on:** Task 0's schema/identity/pricing and Task 1's purchasable queryset.
- **Produces:** `CartSnapshot`, `CartMutationResult`, `read_cart`, `set_photo_selected`,
  `clear_cart`, and the bounded `cleanup_expired_carts --limit` command.

- [ ] Add failing service tests for no state on reads/rejections, first eligible add, event and token
  isolation, explicit idempotent add/remove, clear, deterministic order, pruning, last-item cart
  deletion, cookie-retention decision across multiple event carts, exact mutation-only expiry, and
  logical expiry before physical cleanup.
- [ ] Add `TransactionTestCase` coverage with separate database connections for duplicate adds,
  opposite desired-state mutations, eligibility loss, and a concurrent price edit. Assert one
  unique item and one internally consistent authoritative snapshot rather than timing details.
- [ ] Run `make test TESTS="src/backend/commerce/tests"` and confirm failures identify the absent
  app, schema, identity, calculation, mutation, expiry, and cleanup boundaries.
- [ ] Implement the smallest service layer that accepts the raw token only at its boundary, stores
  only its digest, uses `transaction.atomic()` plus row locking/uniqueness for event-cart mutation,
  and calls Task 1's queryset for every add/read/prune. A no-op must not advance expiry.
- [ ] Implement bounded, repeatable cleanup of rows with `expires_at <= now`; request services must
  remain correct when the command has not run.
- [ ] Run the focused suite and
  `sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py makemigrations --check --dry-run`;
  expect all tests and schema checks to pass.

### Task 3: Expose gated, CSRF-protected cart HTTP and presentation boundaries

**Files:**

- Create `src/backend/commerce/urls.py`.
- Create `src/backend/commerce/views.py`.
- Create `src/backend/commerce/presentation.py`.
- Create `src/backend/commerce/tests/test_views.py`.
- Create `src/backend/commerce/tests/test_presentation.py`.
- Create `src/backend/templates/commerce/cart.html`.
- Modify `src/backend/config/urls.py`.
- Modify `src/backend/config/views.py`.
- Modify `src/backend/selfie_search/views.py`.
- Modify `src/backend/picflow/tests/test_views.py`.
- Modify `src/backend/selfie_search/tests/test_views.py`.

- **Specification:** [Paid gallery cards and lightbox](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#paid-gallery-cards-and-lightbox),
  [Cart page](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#cart-page),
  [Mutation Interface and Progressive Enhancement](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#mutation-interface-and-progressive-enhancement),
  [Feature Gate and Activation Boundary](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#feature-gate-and-activation-boundary),
  and [Security, Privacy, and Caching](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#security-privacy-and-caching).
- **Depends on:** Task 2 service DTOs; final watermark `GalleryPhoto.photo_id`, nullable download,
  stable photo identity markup, saved-result eligibility, and media route authorization.
- **Produces:** named cart detail/set-state/clear routes under `/events/<slug>/cart/`, ordinary-form
  redirects, authoritative JSON snapshots for `Accept: application/json`, and
  `cart_presentation_for_photos` for gallery/result/cart rendering.

- [ ] Add failing route and gate tests for missing/off, staff, and on states. Assert sanitized 404
  and zero cookie/database side effects for direct GET/POST denial and ineligible photos.
- [ ] Add failing cookie tests proving no cookie on visits/no-op failures, and exact `findme_cart`
  `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, 30-day attributes only after actual mutation;
  prove final-cart deletion clears it only when no other unexpired event cart remains.
- [ ] Add failing HTTP tests for CSRF, explicit `selected=1|0`, idempotent retries, same-event JSON
  count/unit/total/photo state, safe local return paths, rejected open redirects, and the exact
  private/no-store/Vary behavior without raw-token reflection.
- [ ] Add failing cart-page tests for current eligible items/order/media/price/total, exact empty and
  pruning copy, return/remove/clear actions, and absence of filename, quantity, package, checkout,
  payment, download, original, or storage identifiers.
- [ ] Add failing normal-gallery and saved-result presentation tests for current selection/price and
  event-only count. Prove free pages and legacy paid results remain byte-for-byte free of cart UI,
  and bearer-result cache/referrer/analytics protections remain stronger than cart defaults.
- [ ] Run
  `make test TESTS="src/backend/commerce/tests/test_views.py src/backend/commerce/tests/test_presentation.py src/backend/picflow/tests/test_views.py src/backend/selfie_search/tests/test_views.py"`
  and confirm failures cover only the missing HTTP and presentation contracts.
- [ ] Implement three POST/GET boundaries using `feature_flags.services.is_enabled` with stable key
  `paid-photo-cart`, `require_POST`/CSRF middleware, strict cookie parsing, private responses, and
  service-returned cookie decisions. Never pass the token beyond the service boundary or into a
  template/context value.
- [ ] Build presentation from current eligible photo IDs and existing `GalleryPhoto` media; do not
  ask Commerce to resolve storage or authorize downloads. Prune before every count/total/render.
- [ ] Run the focused command; expect all route, cookie, authorization, presentation, and regression
  tests to pass.

### Task 4: Add accessible cart controls and progressive enhancement to both paid surfaces

**Files:**

- Modify `src/backend/templates/ui/event_detail_header.html`.
- Modify `src/backend/templates/catalog/event_detail.html`.
- Modify `src/backend/selfie_search/templates/selfie_search/result.html`.
- Modify `src/backend/templates/commerce/cart.html`.
- Create `src/backend/static/ui/commerce-cart.js`.
- Modify `src/backend/static/ui/icons.svg`.
- Modify `src/backend/static/ui/catalog.css`.
- Modify `src/backend/static/ui/selfie-search.css` for the paid-result cart action layout.
- Create `tests/js/commerce-cart.test.js`.
- Modify `tests/js/event-gallery.test.js`.
- Modify `tests/visual/views.py`.
- Modify `tests/visual/urls.py`.
- Modify `tests/visual/visual.spec.js` and add only cart-related desktop/mobile snapshots.

- **Specification:** [User Experience](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#user-experience),
  [Mutation Interface and Progressive Enhancement](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#mutation-interface-and-progressive-enhancement),
  and acceptance criteria 11–16 and 23–24.
- **Depends on:** Task 3 presentation and HTTP contracts and the watermark branch's shared card
  action/lightbox behavior.
- **Produces:** exact absent/present cart icons and names, event-specific count link, no-JavaScript
  forms, synchronized same-photo controls, and server-confirmed enhanced mutation behavior.

- [ ] Add failing rendered-markup tests for price, cart-plus/`Добавить в корзину`, active
  cart-check/`Удалить из корзины`, one form per visible card and lightbox action, event count link,
  CSRF, safe return path, clear confirmation, and all exact Russian empty/pruning/error copy.
- [ ] Add failing JavaScript tests proving the initiator disables during fetch, uses same-origin JSON
  plus CSRF, updates all current-page controls with the same `data-photo-id` and all event counters
  only after a successful response, handles cart-page removal/empty/total, leaves state unchanged on
  failure, and emits the exact retry message. Assert no optimistic update or cross-tab mechanism.
- [ ] Add a GLightbox regression proving newly injected slide actions bind to the same photo state
  while nullable download remains absent and no media URL is rewritten.
- [ ] Run `npm run test:js`; confirm failures are limited to the missing cart DOM/behavior.
- [ ] Add two distinct SVG symbols and the shared server-rendered forms inside the existing action
  container. Use icon shape, accessible name, and active state together; do not rely on color alone.
- [ ] Implement `commerce-cart.js` as progressive enhancement over the forms. Consume only the
  Task 3 authoritative response, preserve ordinary submission when fetch is unavailable, and do not
  read the HttpOnly cookie or introduce local/session storage, polling, WebSockets, or broadcast.
- [ ] Add focused visual fixtures for paid gallery, paid saved result, populated cart, and empty cart
  at desktop and 390px mobile widths. Preserve the watermark snapshots and existing free surfaces.
- [ ] Run `npm run test:js` and `npm run test:visual`; expect the full JavaScript and visual suites
  to pass with reviewed cart snapshots.

### Task 5: Package daily cleanup and prove the complete selection critical path

**Files:**

- Create `deploy/run-cart-cleanup.sh`.
- Create `deploy/install-cart-cleanup-cron.sh`.
- Modify `deploy/apply-deployment.sh`.
- Modify `tests/deployment/test_deployment_scripts.py`.
- Create `src/backend/commerce/tests/test_paid_photo_cart_flow.py`.
- Modify `tests/test_repository_foundation.py`.
- Modify `docs/architecture.md`.
- Modify `docs/product-jobs.md`.
- Modify `docs/engineering-jobs.md`.

- **Specification:** [Expiry, Pruning, and Cleanup](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#expiry-pruning-and-cleanup),
  [Feature Gate and Activation Boundary](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#feature-gate-and-activation-boundary),
  and all [Acceptance Criteria](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md#acceptance-criteria).
- **Depends on:** Tasks 0 through 4.
- **Produces:** an idempotent daily host-cron entry invoking the bounded command, one auditable
  paid-gallery/selfie/cart integration proof, and documentation that distinguishes implemented
  gated code from activation, deployment, payment, and entitlement.

- [ ] Add failing deployment tests for an idempotent daily cron line guarded by `flock`, bounded
  `docker compose exec -T web python manage.py cleanup_expired_carts`, safe install/remove behavior,
  and deployment-script reconciliation without enabling `paid-photo-cart`.
- [ ] Add one failing application integration test that enables both gates for staff, creates an
  accepted new-policy watermarked photo, adds it from the normal gallery, observes it selected in a
  paid saved result, reloads a different page, renders the cart, removes/clears it, and proves every
  original/download route remains denied without a storage signer call.
- [ ] In the same module prove browser-token and event isolation, current-price change, ineligible
  pruning with exact notice, request-time expiry before cleanup, and unchanged free/legacy paid
  behavior.
- [ ] Run
  `make test TESTS="src/backend/commerce/tests/test_paid_photo_cart_flow.py tests/deployment/test_deployment_scripts.py tests/test_repository_foundation.py"`
  and confirm failures identify only the missing assembled flow and cleanup packaging.
- [ ] Add the cron scripts and deployment reconciliation using the established upload-cleanup
  pattern. The code rollout may install cleanup safely, but this task performs no host operation.
- [ ] Run the focused command, then the complete commands in Verification; expect all results to
  pass with both runtime gates still absent/off by default.
- [ ] Update architecture and job ledgers only with locally evidenced implementation. Keep public
  activation blocked on approved real watermark assets/worker, staff smoke, legal review, explicit
  gate mutation, deployment, and live verification; keep packages, checkout, payment, order,
  entitlement, and original delivery unimplemented.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the approved cart specification, ADR 0030, ADR 0029, and the
  still-applicable boundaries of ADRs 0001/0002/0019/0020/0021/0022/0024/0028.
- [ ] Confirm `PublicMediaResolver` still exclusively selects bytes; neither cart membership nor its
  token reaches any media/download authorization or storage-signing input.
- [ ] Confirm `docs/architecture.md`, `docs/product-jobs.md`, and `docs/engineering-jobs.md` describe
  the locally implemented but disabled-default state and do not claim PR, CI, merge, deployment,
  activation, legal review, payment, entitlement, or live evidence.
- [ ] Confirm ADR 0030 remains accurate. Stop for a decision instead of contradicting it; supersede
  rather than editing the accepted decision.
- [ ] Record the rebase watermark SHA, migration graph, focused/full verification, visual snapshots,
  review outcomes, and unperformed operational gates in the delivery report or pull request.

## Verification

Run after the complete implementation:

```sh
make test TESTS="src/backend/picflow/tests src/backend/commerce/tests src/backend/selfie_search/tests"
make test TESTS="tests/deployment/test_deployment_scripts.py tests/test_repository_foundation.py"
sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py makemigrations --check --dry-run
make check
npm run test:js
npm run test:visual
git diff --check
```

Expected outcomes:

- all focused and complete Python checks pass with one linear migration graph and no drift;
- all JavaScript and desktop/mobile visual tests pass for paid gallery, saved result, cart, and
  unchanged free surfaces;
- no rejected/no-op/read request creates or refreshes a token, cart, item, or expiry;
- no raw token or original/download/storage capability appears in rendered or persisted cart state;
- deployment tests prove cleanup packaging without performing deployment or enabling a gate;
- working-tree diff is whitespace-clean and the final documentation states only verified local
  implementation facts.

## Operational impact and rollout

The schema rollout adds `Event.price_per_photo_kopecks`, backfills existing paid events to `30000`,
adds the free/paid price check, then creates Commerce cart/item tables. It does not touch photo
policy, derivatives, orders, payments, entitlements, originals, or existing browser sessions.

Code and schema may ship with `paid-photo-cart` absent/off. The cleanup cron is safe before
activation because it only deletes logically expired cart rows in bounded batches. This plan does
not authorize deployment, cron installation, cloud changes, legal-copy changes, or feature-flag
mutation.

Public rollout is a later operational action in this exact order:

1. Complete the watermark branch's approved-artwork/worker deployment and verify its staff-only
   normal-gallery and saved-result paths.
2. Obtain the required legal review for the necessary `findme_cart` cookie purpose and 30-day
   retention disclosure; change customer-facing legal documents only through that reviewed task.
3. Deploy cart code/schema with `paid-photo-cart` absent/off; verify migrations, health, cleanup
   command, and that no cart cookie/action/route is public.
4. Install/reconcile the daily cleanup cron, then set `paid-photo-cart=staff` and verify one real
   watermarked photo through gallery add, saved-result state, cart page, removal, expiry inspection,
   and continued original/download denial.
5. After maintainer approval, set `paid-photo-cart=on`; verify two isolated anonymous browsers and
   monitor cart 404/4xx/5xx rates, mutation latency, expired-row cleanup count, and database growth.

## Rollback

Set `paid-photo-cart=off` first. This closes direct routes, hides actions, and prevents cookie/cart
side effects without deleting unexpired selection. Remove the cleanup cron only if its code is
being withdrawn; leaving it active is safe and bounds retained state.

Application rollback must retain code/schema capable of reading the new Event field and Commerce
tables while the migration is applied. Do not reverse the price data migration or delete live cart
rows during an incident. A later separately reviewed migration may remove abandoned cart data and
schema after the 30-day retention window and confirmation that no compatible application remains.

## Open questions

None. Final watermark completion, legal review, deployment, staff smoke, and gate activation are
explicit execution/rollout prerequisites rather than unresolved implementation choices.
