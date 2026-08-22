# Paid Photo Purchase and Original Delivery Implementation Plan

- Date: 2026-08-20
- Last verified: 2026-08-21
- Status: Approved by maintainer instruction on 2026-08-20; prerequisite merge/deployment gate is
  satisfied and the implementation worktree may be created from current `origin/main`
- Owner: project maintainer
- Related specification:
  [`docs/superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md`](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md)
- Related architecture:
  [`docs/architecture.md`](../architecture.md#target-mvp-architecture--proposed),
  [purchase and download](../architecture.md#purchase-and-download), and
  [security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- Related ADRs:
  [ADR 0001](../adr/0001-django-modular-monolith.md),
  [ADR 0002](../adr/0002-postgresql-system-of-record.md),
  [ADR 0020](../adr/0020-use-signed-direct-object-storage-media-delivery.md),
  [ADR 0028](../adr/0028-operate-one-canonical-deployment.md),
  [ADR 0029](../adr/0029-use-watermarked-previews-for-paid-photos.md),
  [ADR 0030](../adr/0030-use-anonymous-server-side-event-carts.md), and
  [ADR 0031](../adr/0031-use-orders-and-adapters-for-paid-original-delivery.md)
- ADR impact: implements accepted ADR 0031 and its narrow supersession of ADR 0029 after a
  qualifying Order becomes paid. It preserves ADR 0030 cart-token non-authority and ADR 0020's
  Django authorization before short-lived exact-object delivery.

## Goal

Deliver the approved [paid purchase and original-delivery
outcome](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#outcome)
as a complete dark-deployed critical path using deterministic local/test payment and email
adapters, without accepting real money, sending network email, activating a runtime gate, or
deploying operational changes.

## Scope

Implement the approved specification without changing its scope.

Execution must use `$execute-implementation-plan`. The prerequisite increments have now merged in
the required order:

1. paid-watermarked previews;
2. the complete anonymous paid-photo cart rebased onto that watermark merge; and
3. this purchase increment will start from the resulting `main`, which also contains the completed
   canonical-production Compose cutover.

Create the implementation worktree from current `origin/main` at or after `be22bdd`; never return to
the old partial watermark/cart branches. Do not edit either retained prerequisite worktree. The
merged cart interface is now the implementation authority; remove stale plan assumptions rather
than adding a compatibility layer.

Fresh prerequisite evidence on 2026-08-21:

- canonical-production Compose cutover PR 153 is merged in the history before the paid increments;
- watermark PR 155 is merged as `84c53a9`;
- combined watermark/cart PR 157 is merged as current `origin/main` `be22bdd`, and both its PR CI
  and post-merge CI completed successfully;
- Deploy run `32457775668` completed successfully for `be22bdd`, and public-health run
  `32458314168` also completed successfully;
- the live deployment marker and web image are
  `ghcr.io/peter-nikitin/photo-prjct:be22bdd0118fbc6f416b96cc31683890ec930540`;
- live db, web, and nginx containers are healthy, both worker replicas are running, and public
  `/health/` returns `{"status": "ok"}`;
- live migrations include `commerce.0001_initial`, `picflow.0012_paid_watermarked_photo_policy`,
  `picflow.0013_event_photo_price`, and
  `processing.0008_watermarked_preview_derivative_producer`;
- the live worker identity set includes `2/generate_watermarked_preview/1`, and the
  `paid-watermarked-previews` gate is `staff`; and
- the `paid-photo-cart` gate is absent and the cart-cleanup cron is not installed, so cart code is
  deployed fail-closed rather than publicly activated.

The absent cart gate and cleanup cron do not block dark purchase implementation, because Task 0
consumes merged code/schema rather than live customer carts. They do block later cart/purchase
activation and remain owned by the cart rollout; do not silently install the cron or enable the cart
gate inside this implementation plan. Re-read current `origin/main` and live status immediately
before Task 0 because deployment facts can drift.

## Acceptance criteria

Use all 42 numbered [specification acceptance
criteria](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#acceptance-criteria).
In addition:

- every task starts only after its prerequisite task has an approved independent working-tree
  review and one root-created task commit;
- the final migration graph extends the merged Commerce cart migrations linearly and does not
  rewrite cart, watermark, or canonical-deployment history;
- no test adapter can be selected in a deployed configuration, even while the purchase gate is
  absent/off or staff-only;
- no browser return, cart token, public Order number, email address, or unverified provider value
  can reach original signing authority;
- test and support output contains no raw purchase-browser token, Order access signature, signed
  Object Storage URL, full customer email, provider credential, or callback body;
- both `paid-photo-purchase` and real-adapter activation remain absent/off after repository
  implementation; and
- final evidence distinguishes local implementation, PR/CI, merge, deployment, adapter readiness,
  legal/fiscal readiness, runtime activation, and live customer verification.

## Implementation

### Task 0: Create the implementation worktree from the converged baseline

**Files:** no product files; create the isolated worktree through the repository Make target and
create the SDD ledger under `.superpowers/sdd/2026-08-20-paid-photo-purchase/`.

- **Specification:** [Status](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#status)
  and [Feature Gate and Activation Boundary](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#feature-gate-and-activation-boundary).
- **Depends on:** satisfied by current `origin/main` `be22bdd`: canonical-production Compose,
  watermark, and complete cart with approved review, green PR/post-merge CI, successful deployment,
  and the migrations listed above.
- **Produces:** a clean `codex/paid-photo-purchase` worktree from the resulting current `main`, with
  the approved specification, ADR 0031, this plan, final cart interfaces, and one linear migration
  graph.

- [x] Refresh `origin`, inspect the exact watermark/cart PRs and CI, and confirm both are merged into
  `origin/main`; verified PRs 155/157 and current `be22bdd` on 2026-08-21.
- [x] Confirm every related worktree is clean or contains only changes owned by its active task;
  verified the retained watermark, cart, and purchase documentation worktrees clean on 2026-08-21.
- [x] Confirm `origin/main` contains ADRs 0029/0030, the complete cart service/HTTP/UI/cleanup code,
  the canonical Compose identity, and no pending prerequisite merge. ADR 0031 and this purchase
  documentation are intentionally supplied by the purchase branch in the later copy step.
- [ ] Run `make worktree NAME=paid-photo-purchase BASE=origin/main` from the main checkout and
  confirm the generated `.env` is local/test-safe and the shared `.venv` is linked.
- [ ] Run `make test TESTS="src/backend/commerce/tests src/backend/picflow/tests/test_gallery.py"`
  and `npm run test:js`; expect the merged prerequisite baseline to pass before any purchase test
  is written.
- [ ] Copy only the approved specification, accepted ADR, plan, and domain-language additions that
  are absent from converged `main`; do not copy prerequisite code from this documentation branch.

### Task 1: Persist immutable Orders and normalized payment evidence

**Files:**

- Modify `src/backend/commerce/models.py`.
- Create `src/backend/commerce/migrations/0002_orders_and_payments.py` after merged
  `commerce.0001_initial`.
- Create `src/backend/commerce/order_numbers.py`.
- Create `src/backend/commerce/tests/test_order_models.py`.
- Create `src/backend/commerce/tests/test_order_migrations.py`.
- Create `src/backend/commerce/tests/test_order_numbers.py`.

- **Specification:** [Order](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#order),
  [Order item and entitlement](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#order-item-and-entitlement),
  [Payment attempt and evidence](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#payment-attempt-and-evidence),
  and [Failure and Consistency Semantics](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#failure-and-consistency-semantics).
- **Depends on:** Task 0's merged Event price, Cart, CartItem, and final migration graph.
- **Produces:** Order, OrderItem, PaymentAttempt, append-only normalized PaymentEvidence, exact
  constraints/indexes, and non-sequential `FM-XXXXXXXX` public-number generation.

- [ ] Add failing model/migration tests for immutable commercial snapshots, one Event and literal
  RUB, quantity-one unique items, summed totals, protected paid Photo/Event deletion, exact states,
  one nonterminal attempt, idempotency/provider uniqueness, append-only evidence, and no raw
  callback or access secret columns.
- [ ] Add failing public-number tests for the exact format, collision retry, non-sequential
  randomness, and absence of access authority.
- [ ] Run
  `make test TESTS="src/backend/commerce/tests/test_order_models.py src/backend/commerce/tests/test_order_migrations.py src/backend/commerce/tests/test_order_numbers.py"`
  and confirm failures identify only the absent purchase schema and number generator.
- [ ] Implement the smallest schema and helpers. Keep mutable state fields explicit while preventing
  edits to checkout email, OrderItems, amounts, currency, and provider evidence after creation.
- [ ] Re-run the focused command and
  `sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py makemigrations --check --dry-run`;
  expect all tests to pass and no migration drift.

### Task 2: Persist fulfillment capabilities, delivery work, attention, and audit

**Files:**

- Modify `src/backend/commerce/models.py`.
- Create `src/backend/commerce/migrations/0003_fulfillment.py`.
- Create `src/backend/commerce/capabilities.py`.
- Create `src/backend/commerce/tests/test_fulfillment_models.py`.
- Create `src/backend/commerce/tests/test_capabilities.py`.

- **Specification:** [Order access grant](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#order-access-grant),
  [Email delivery](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#email-delivery),
  [Commerce attention](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#commerce-attention),
  [Download-grant audit](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#download-grant-audit),
  and [Security, Privacy, and Caching](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#security-privacy-and-caching).
- **Depends on:** Task 1 Orders and OrderItems.
- **Produces:** purchase-browser digest, signed/revocable OrderAccessGrant metadata,
  EmailDelivery plus immutable attempt history, deduplicated CommerceAttention, DownloadGrantAudit,
  and capability creation/verification helpers.

- [ ] Add failing tests for 32-byte opaque purchase-browser tokens, digest-only persistence,
  30-day creation-only refresh, stable-key HMAC grants, multiple active grants, individual
  revocation, sanitized invalid/foreign behavior, and no full bearer storage.
- [ ] Add failing model tests for delivery states/history, recipient snapshots, attention
  `(kind, subject)` deduplication and resolution, safe audit fields, and
  `first_customer_access_at` set-once behavior.
- [ ] Run
  `make test TESTS="src/backend/commerce/tests/test_fulfillment_models.py src/backend/commerce/tests/test_capabilities.py"`
  and confirm failures are caused by the absent fulfillment schema/helpers.
- [ ] Implement the exact schema and capability helpers using a dedicated stable signing secret;
  never reuse Django `SECRET_KEY`, the cart token, or Object Storage signing credentials.
- [ ] Re-run the focused tests and migration-drift check; expect all to pass.

### Task 3: Define adapters and create immutable checkout atomically

**Files:**

- Create `src/backend/commerce/payment_gateway.py`.
- Create `src/backend/commerce/test_payment_gateway.py`.
- Create `src/backend/commerce/checkout.py`.
- Create `src/backend/commerce/tests/test_payment_gateway.py`.
- Create `src/backend/commerce/tests/test_checkout.py`.

- **Specification:** [Checkout and Order Creation](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#checkout-and-order-creation),
  [Payment Gateway Boundary](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#payment-gateway-boundary),
  and [Seller, Product, and License](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#seller-product-and-license).
- **Depends on:** Task 2 capabilities and merged Cart/CartItem plus `CartSnapshot`, `read_cart`,
  `set_photo_selected`, `clear_cart`, and the authoritative purchasable-photo queryset. Checkout
  locks Cart/CartItem rows inside its own Commerce transaction; it must not import the cart
  service's private `_locked_*` helpers.
- **Produces:** provider-neutral payment DTOs, the three-operation `PaymentGateway` Protocol,
  deterministic local/test adapter, and checkout service returning immutable Order/attempt/hosted
  redirect plus purchase-cookie decision.

- [ ] Add failing contract tests for create, fetch, and authenticated notification normalization;
  deterministic success/cancel/pending; exact amount/currency/idempotency; sanitized errors; and
  rejection of provider SDK/raw callback values outside the adapter.
- [ ] Add failing checkout tests for one Event, locked/revalidated current cart, pruned/empty
  rejection, matching normalized emails, one exact snapshot, concurrent duplicate submissions,
  external create outside the transaction, create timeout reconciliation, active-attempt cart lock,
  unchanged-order retry, supersession after a cart mutation, and deliberately allowed repeat
  purchase of the same Photo by the same browser/email.
- [ ] Run
  `make test TESTS="src/backend/commerce/tests/test_payment_gateway.py src/backend/commerce/tests/test_checkout.py"`
  and verify the intended missing-contract/service failures.
- [ ] Implement the narrow adapter and checkout module. Persist the application idempotency key
  before external I/O and reconcile the response against the current locked attempt.
- [ ] Re-run the focused tests; expect one Order/current attempt for equivalent concurrent or retried
  checkout and no entitlement or email before paid.

### Task 4: Implement authoritative payment transitions and operator attention

**Files:**

- Create `src/backend/commerce/payments.py`.
- Create `src/backend/commerce/attention.py`.
- Create `src/backend/commerce/tests/test_payments.py`.
- Create `src/backend/commerce/tests/test_attention.py`.

- **Specification:** [Payment State and Idempotency](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#payment-state-and-idempotency),
  [Commerce attention](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#commerce-attention),
  and [Failure and Consistency Semantics](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#failure-and-consistency-semantics).
- **Depends on:** Task 3 PaymentGateway and checkout outputs.
- **Produces:** one serialized idempotent transition boundary for notifications, fetched status,
  expiry reconciliation, late success, conflict evidence, and trusted manual paid/cancel actions.

- [ ] Add failing `TransactionTestCase` coverage for equivalent concurrent notifications/fetches,
  exact success, amount/currency mismatch, superseded late success, canceled conflict, manual paid,
  manual/automatic races, idempotent cart cleanup/email creation, and current-cart mutation after a
  terminal unsuccessful attempt.
- [ ] Add failing attention tests for every initial kind, transactional open, `(kind, subject)`
  deduplication, safe structured logging, automatic repair resolution, manual commented resolution,
  and no customer/provider secrets.
- [ ] Run
  `make test TESTS="src/backend/commerce/tests/test_payments.py src/backend/commerce/tests/test_attention.py"`
  and confirm failures identify only absent transition/attention behavior.
- [ ] Implement row-locked transitions that compare exact provider identity, amount, and RUB before
  paid; manual paid deliberately bypasses provider evidence but uses the same atomic fulfillment
  transition and standard Admin actor/time history.
- [ ] Re-run the focused tests; expect one paid transition, one initial email job, exact cart cleanup,
  and one deduplicated attention per conflict.

### Task 5: Authorize and sign only purchased originals

**Files:**

- Create `src/backend/commerce/original_delivery.py`.
- Create `src/backend/commerce/tests/test_original_delivery.py`.
- Modify `src/backend/picflow/tests/test_gallery.py` only for regression coverage if the final
  watermark seam requires it.

- **Specification:** [Order item and entitlement](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#order-item-and-entitlement),
  [Customer Access and Original Delivery](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#customer-access-and-original-delivery),
  and [Security, Privacy, and Caching](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#security-privacy-and-caching).
- **Depends on:** Task 4 paid transition and Task 2 customer capabilities; final watermark
  `PublicMediaResolver`/private-storage transport contracts.
- **Produces:** a separate purchased authorization context which verifies capability + paid
  OrderItem, signs one exact original attachment, and appends DownloadGrantAudit.

- [ ] Add failing tests for purchase-browser and grant authorization, cross-order/event/photo
  denial, pending/superseded/canceled denial, hidden/unpublished paid access, permanent unlimited
  issuance, correct jpg/png filename, revocation, audit source, and no cart/public-number authority.
- [ ] Add failing missing/mismatched-object tests proving safe failure, one attention record, no
  preview fallback, no alternate Photo, and no signing before complete authorization.
- [ ] Run `make test TESTS="src/backend/commerce/tests/test_original_delivery.py"` and confirm the
  expected missing purchased-delivery failures.
- [ ] Implement a Commerce-owned signer module using the accepted exact-object storage transport;
  do not weaken the watermark resolver's public original denial or expose object keys to views.
- [ ] Re-run focused tests plus `make test TESTS="src/backend/picflow/tests/test_gallery.py"`; expect
  purchased delivery to pass and every pre-purchase watermark denial to remain green.

### Task 6: Deliver access email and reconcile payments through one Commerce worker

**Files:**

- Create `src/backend/commerce/email_sender.py`.
- Create `src/backend/commerce/test_email_sender.py`.
- Create `src/backend/commerce/delivery.py`.
- Create `src/backend/commerce/worker.py`.
- Create `src/backend/commerce/management/commands/run_commerce_worker.py`.
- Create `src/backend/commerce/management/commands/commerce_worker_health.py`.
- Create `src/backend/commerce/tests/test_email_sender.py`.
- Create `src/backend/commerce/tests/test_delivery.py`.
- Create `src/backend/commerce/tests/test_worker.py`.

- **Specification:** [Email Delivery](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#email-delivery-1),
  [Commerce Worker](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#commerce-worker),
  and [Administration and Attention](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#administration-and-attention).
- **Depends on:** Task 4 due-attempt reconciliation and attention; Task 2 grants/delivery records.
- **Produces:** narrow EmailSender Protocol, deterministic capture/failure adapter, exact retry
  schedule, customer/admin message builders, lease-safe PostgreSQL polling for two explicit work
  types, and a read-only oldest-ready-work/liveness health command for independent monitoring.

- [ ] Add failing sender/delivery tests for exact subject/body/link, no attachment/preview/marketing,
  success/retryable/terminal normalization, immediate/1m/5m/30m/2h/12h schedule, stop within 24h,
  recipient snapshot, correction cancellation, new-grant resend, exhaustion attention, and no open
  tracking. Prove attention email targets only active staff with `commerce.handle_attention` and a
  nonempty email, and reminders run at most once per 24 hours.
- [ ] Add failing worker tests for bounded claims, separate delivery/reconciliation vocabularies,
  lease expiry/recovery, idempotent restart, adapter timeout, sanitized logs, daily attention
  reminder, and health failure when ready work exceeds the configured operational threshold.
- [ ] Run
  `make test TESTS="src/backend/commerce/tests/test_email_sender.py src/backend/commerce/tests/test_delivery.py src/backend/commerce/tests/test_worker.py"`
  and confirm failures identify the missing sender/delivery/worker modules.
- [ ] Implement the smallest polling loop without a generic job table, broker, image-worker import,
  or unbounded retry. Reconstruct access URLs only at send time.
- [ ] Re-run the focused tests; expect deterministic delivery/reconciliation and privacy-safe
  worker/health output.

### Task 7: Expose gated checkout, return, Order, resend, and download routes

**Files:**

- Create `src/backend/commerce/forms.py`.
- Modify `src/backend/commerce/urls.py` from the merged cart result.
- Modify `src/backend/commerce/views.py` from the merged cart result.
- Modify `src/backend/commerce/presentation.py` without duplicating its existing cart/media
  presentation work.
- Create `src/backend/templates/commerce/order.html`.
- Modify `src/backend/templates/commerce/cart.html`.
- Modify `src/backend/config/urls.py`.
- Create `src/backend/commerce/tests/test_checkout_views.py`.
- Create `src/backend/commerce/tests/test_order_views.py`.
- Create `src/backend/commerce/tests/test_download_views.py`.

- **Specification:** [Checkout and Order Creation](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#checkout-and-order-creation),
  [Payment State and Idempotency](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#payment-state-and-idempotency),
  [Customer Access and Original Delivery](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#customer-access-and-original-delivery),
  and [Feature Gate and Activation Boundary](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#feature-gate-and-activation-boundary).
- **Depends on:** Tasks 3–6 services and the merged cart detail route.
- **Produces:** exact inline cart checkout form/copy, hosted redirect, inert return polling endpoint, private Order
  page, rate-limited resend, purchased download redirect, and `findme_purchase` cookie responses.

- [ ] Add failing route/gate tests for missing/off/staff/on, CSRF, one normalized email,
  safe redirects, provider notification authentication, browser return never mutating state,
  sanitized unknown/revoked grants, exact cache/referrer/analytics protections, and no side effects
  while closed.
- [ ] Add failing page tests for the approved Russian copy, immutable summary, masked email,
  watermarked photos, pending/paid states, individual download actions, resend/support, and absence
  of provider/storage/internal identifiers.
- [ ] Run
  `make test TESTS="src/backend/commerce/tests/test_checkout_views.py src/backend/commerce/tests/test_order_views.py src/backend/commerce/tests/test_download_views.py"`
  and verify failures identify only the absent HTTP boundary.
- [ ] Implement thin views over the domain services, strict CSRF/callback separation, private
  no-store responses, exact purchase cookie attributes, and rate limits without account/session
  identity.
- [ ] Re-run focused tests; expect closed-by-default routes and a complete staff/local test-adapter
  browser flow.

### Task 8: Add trusted Admin recovery and operator attention UX

**Files:**

- Create `src/backend/commerce/admin.py`.
- Create `src/backend/commerce/tests/test_admin.py`.
- Create `src/backend/templates/admin/commerce/order/change_form.html` only if ordinary Admin
  actions cannot provide the required refresh/copy controls accessibly.
- Modify `src/backend/templates/admin/base_site.html` only if the final repository has no existing
  extension point for the open-attention count.

- **Specification:** [Administration and Attention](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#administration-and-attention)
  and [Payment State and Idempotency](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#payment-state-and-idempotency).
- **Depends on:** Tasks 2, 4, and 6 Admin-safe commands.
- **Produces:** read-only commercial evidence, editable delivery email only, trusted manual paid,
  pending cancel, provider refresh, resend, grant create/copy/revoke, delivery retry, attention list
  and resolution, plus `commerce.handle_attention` permission visibility.

- [ ] Add failing Admin tests for full trusted visibility, immutable checkout/item/amount/evidence
  fields, allowed state actions, actor/time history, delivery correction, new-link behavior,
  provider refresh, resend/retry, deduplicated attention count, and commented resolution.
- [ ] Prove manual paid requires confirmation but no bank reference, amount, attachment, or comment;
  it must be unavailable from paid/canceled and unavailable while the purchase gate disables new
  manual side effects.
- [ ] Run `make test TESTS="src/backend/commerce/tests/test_admin.py"` and confirm the intended
  missing Admin behavior.
- [ ] Implement standard ModelAdmin actions and narrow custom POST endpoints only where an external
  adapter call or one-time displayed grant requires it. Never persist or list a full bearer link.
- [ ] Re-run focused tests; expect trusted recovery with immutable evidence and ordinary Admin
  audit.

### Task 9: Add progressive checkout/order behavior and visual evidence

**Files:**

- Create `src/backend/static/ui/commerce-purchase.js`.
- Modify `src/backend/static/ui/catalog.css` or the final merged Commerce stylesheet.
- Create `tests/js/commerce-purchase.test.js`.
- Modify `tests/visual/views.py`.
- Modify `tests/visual/urls.py`.
- Modify `tests/visual/visual.spec.js`.
- Add only purchase-related desktop and 390px mobile snapshots under
  `tests/visual/visual.spec.js-snapshots/`.
- Modify `.agents/skills/update-visual-design/references/screen-inventory.md`.

- **Specification:** [Checkout and Order Creation](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#checkout-and-order-creation),
  [Customer Access and Original Delivery](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#customer-access-and-original-delivery),
  and acceptance criteria 24, 28–30, and 42.
- **Depends on:** Task 7 production markup and the merged cart/watermark visual baselines.
- **Produces:** accessible payment-status polling, resend feedback, checkout/order responsive layout,
  and reviewed deterministic desktop/mobile evidence.

- [ ] Use `$update-visual-design` before changing templates/CSS/snapshots and preserve its render and
  focused visual-review contract.
- [ ] Add failing JavaScript tests proving bounded same-origin pending polling, no optimistic paid
  transition, terminal stop, safe network failure, and resend state based only on authoritative
  responses.
- [ ] Run `npm run test:js`; confirm failures are limited to the absent purchase behavior.
- [ ] Implement progressive enhancement over working server forms/pages with accessible live status,
  focus, keyboard, and reduced-motion behavior; add no client-side payment or access authority.
- [ ] Add deterministic pending, paid, email-failed, inline cart-checkout, and populated Order fixtures at
  desktop and 390px mobile, preserving cart/watermark/free-gallery baselines.
- [ ] Run `npm run test:js` and `npm run test:visual`; expect all JavaScript tests and visually
  inspected snapshots to pass.

### Task 10: Package fail-closed runtime, worker, and independent health monitoring

**Files:**

- Modify `src/backend/config/settings.py`.
- Create `src/backend/commerce/checks.py`.
- Create `src/backend/commerce/tests/test_checks.py`.
- Modify `.env.example`.
- Modify `docker-compose.yml`.
- Modify `docker-compose.deployment.yml`.
- Modify `deploy/apply-deployment.sh`.
- Create `deploy/run-commerce-worker-health.sh`.
- Modify `deploy/configure-monitoring-agent.sh` and
  `deploy/monitoring/unified-agent.yml.template` only to package the independent disabled-default
  probe; perform no host/cloud activation.
- Modify `deploy/monitoring/alerts.md` and `deploy/monitoring/dashboard.json` only for the safe
  oldest-ready-work/liveness signals actually implemented.
- Modify `tests/deployment/test_deployment_scripts.py`.
- Modify `tests/deployment/test_monitoring_agent.py`.
- Modify `tests/deployment/test_monitoring_contract.py`.
- Modify `tests/test_repository_foundation.py`.

- **Specification:** [Commerce Worker](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#commerce-worker),
  [Feature Gate and Activation Boundary](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#feature-gate-and-activation-boundary),
  and [Security, Privacy, and Caching](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#security-privacy-and-caching).
- **Depends on:** Task 6 worker/health commands and final canonical Compose/deployment contracts.
- **Produces:** web-image Commerce worker service, blank/off defaults, stable adapter/signing-secret
  settings, deployed test-adapter rejection, bounded health probe packaging, and no activation.

- [ ] Add failing system/deployment tests proving missing/off fail closed, blank real-adapter
  settings, local/test adapter availability, deployed rejection of either test adapter in every
  feature mode, least-credential worker environment, bounded resources/restart, and no image-worker
  protocol.
- [ ] Add failing monitoring tests proving the independent probe runs outside the Commerce worker,
  uses current canonical Compose identity, fails on stopped process or overdue ready work, and emits
  only safe numeric/status fields.
- [ ] Run
  `make test TESTS="src/backend/commerce/tests/test_checks.py tests/deployment/test_deployment_scripts.py tests/deployment/test_monitoring_agent.py tests/deployment/test_monitoring_contract.py tests/test_repository_foundation.py"`
  and confirm failures identify only missing packaging/checks.
- [ ] Implement disabled-default settings and packaging without credentials, host mutation, cloud
  mutation, worker start, monitoring install, or feature-flag write.
- [ ] Re-run focused tests and `docker compose config`; expect a valid canonical configuration with
  no real payment/email activity and no test adapter deployability.

### Task 11: Prove the assembled dark purchase critical path and reconcile documentation

**Files:**

- Create `src/backend/commerce/tests/test_paid_photo_purchase_flow.py`.
- Modify `docs/architecture.md`.
- Modify `docs/product-jobs.md`.
- Modify `docs/engineering-jobs.md`.
- Modify `CONTEXT.md` only if delivered domain language differs from the accepted definitions.

- **Specification:** all [Acceptance Criteria](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#acceptance-criteria)
  and [External Prerequisites](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md#external-prerequisites-not-open-design-questions).
- **Depends on:** Tasks 1–10.
- **Produces:** one auditable end-to-end staff/local flow and repository ledgers that claim only
  implemented, disabled-default evidence.

- [ ] Add one failing integration module covering cart → checkout → deterministic hosted payment →
  inert return → authoritative paid → immediate browser access → captured email link → individual
  original signing, including cross-order denial and no prepayment original.
- [ ] In the same module cover cancellation, pending expiry/reconciliation, superseded late success,
  mismatch attention, manual paid, corrected email/resend, revoked/parallel grants, missing original,
  email exhaustion, the paid-plus-email-sent-or-first-access fulfillment milestone, worker restart,
  and unchanged free/legacy/watermark/cart behavior.
- [ ] Run
  `make test TESTS="src/backend/commerce/tests/test_paid_photo_purchase_flow.py"`
  and confirm failures identify only missing assembled wiring before completing it.
- [ ] Complete only the missing assembly, then run every command in Verification with both Commerce
  gates absent/off by default.
- [ ] Update architecture and job ledgers to `implemented locally, disabled by default`; do not
  claim real gateway/email, fiscal/legal approval, PR, CI, merge, deployment, activation, or live
  customer evidence.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the approved specification, ADR 0031, its narrow supersession
  of ADR 0029, and still-applicable ADRs 0001/0002/0020/0028/0030.
- [ ] Confirm the cart token remains selection-only, `PaymentGateway` and `EmailSender` remain narrow,
  paid OrderItem is the only persisted entitlement fact, and public watermark denial is unchanged
  before paid.
- [ ] Confirm `docs/architecture.md`, product/engineering jobs, and `CONTEXT.md` distinguish accepted
  architecture, local implementation, external prerequisites, activation, and live evidence.
- [ ] Stop for a new decision instead of adding provider-specific assumptions, refunds, ZIP,
  accounts, packages, promotions, retention automation, or compatibility paths.
- [ ] Record the exact convergence SHAs, migration graph, task reviews, verification outcomes, and
  unperformed operational gates in the pull request.

## Verification

Run targeted commands in each task first. After the assembled implementation, run:

```sh
make test TESTS="src/backend/commerce/tests src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py src/backend/selfie_search/tests/test_views.py"
make test TESTS="tests/deployment/test_deployment_scripts.py tests/deployment/test_monitoring_agent.py tests/deployment/test_monitoring_contract.py tests/test_repository_foundation.py"
sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py makemigrations --check --dry-run
make check
npm run test:js
npm run test:visual
docker compose config
git diff --check
```

Expected outcomes:

- one linear migration graph, no drift, and all focused/full Python checks pass;
- every JavaScript and desktop/mobile visual test passes with reviewed purchase snapshots and no
  watermark/cart/free-surface regression;
- deterministic adapters prove success, cancellation, pending, retryable and terminal email
  failure without real network payment or email;
- no unauthorized path reaches Object Storage signing and all missing-object/conflict paths create
  deduplicated safe attention;
- deployed configuration rejects test adapters and keeps `paid-photo-purchase` absent/off;
- Compose is valid with the Commerce worker unactivated and no cloud/host state changed; and
- the final working tree is whitespace-clean and contains only task-owned changes.

Treat full Python, visual, Docker, or build commands as monitored long operations: start them in a
PTY/session, surface progress at least every 30 seconds, inspect the live process/output when no
new progress appears, and interrupt/retry narrowly when the process is genuinely stuck. Do not run
overlapping full Django or visual suites.

## Operational impact and rollout

The repository delivery adds Order/payment/fulfillment tables, a separate Commerce worker command,
test-only adapters, fail-closed configuration, and independent health-probe packaging. It performs
no real payment, network email, deployment, host mutation, monitoring installation, secret
creation, legal-document change, or feature-flag mutation.

The prerequisite baseline is already deployed: canonical Compose, watermark, and cart code/schema
are present at `be22bdd`; watermark is staff-only and cart remains fail-closed. The remaining rollout
after implementation is separately authorized and must occur in this order:

1. Merge purchase code based on `be22bdd` or later, then deploy schema/web with
   `paid-photo-purchase` off and no
   Commerce worker or external adapter active.
2. Select and implement the concrete bank adapter; approve authenticated notification/status,
   idempotency, sandbox, fiscal fields, secrets, and failure mapping.
3. Select and implement the concrete email adapter; approve sender identity, DNS/domain auth,
   quotas, bounce behavior, credentials, and delivery monitoring.
4. Before any cart or purchase activation, install/reconcile the already merged bounded cart
   cleanup cron and complete the cart rollout's approved staff verification; do not treat deployed
   fail-closed cart code as an active customer path.
5. Complete legal/accounting review, deploy the Commerce worker and independent alert, configure
   stable access-signing secret, and run staff-only real-adapter checkout/manual recovery/email/
   original smoke on one watermarked photo.
6. Only after explicit maintainer approval set `paid-photo-cart=on` and
   `paid-photo-purchase=on` in their approved order, then monitor payment
   conflicts, reconciliation age, email failure, missing originals, worker health, 4xx/5xx, and
   database growth.

## Rollback

Set `paid-photo-purchase=off` first and stop new payment creation/notifications through the
configured external boundary. Stop the Commerce worker only after preserving due work and ensuring
operators can inspect Orders/attention in Admin. Roll application containers back to a compatible
image while retaining migrations and all Order, payment, grant, email, attention, and audit rows.

Never reverse a paid Order, delete paid evidence, revoke all customer access, remove the stable
signing secret, or drop Commerce schema during incident rollback. Existing paid fulfillment remains
an obligation even while checkout is closed. A later separately reviewed migration may remove
never-paid data only after a legal retention decision; this plan defines no automatic cleanup.

## Open questions

None for the dark-deployed implementation. The exact bank/email protocols, fiscal attributes,
legal wording, support contact, worker/monitor deployment, secrets, public activation, refunds, and
ZIP delivery are explicit external prerequisites or later scopes and must not be guessed by an
implementer.
