# Browser Order History and Independent Carts Implementation Plan

- Date: 2026-09-30
- Status: Draft for maintainer review
- Owner: project maintainer
- Related specification: [Browser Order History and Independent Carts](../superpowers/specs/2026-09-30-browser-order-history-and-independent-carts-design.md)
- Related architecture: [Purchase and download](../architecture.md#purchase-and-download), [Security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- Related ADRs: [ADR 0030](../adr/0030-use-anonymous-server-side-event-carts.md), [ADR 0031](../adr/0031-use-orders-and-adapters-for-paid-original-delivery.md), [ADR 0047](../adr/0047-separate-order-payment-from-new-cart-selection.md)
- ADR impact: ADR 0047 was explicitly accepted on 2026-09-30 and partially supersedes ADR 0031; the implementation must conform to it.

## Goal

Deliver the [approved outcome](../superpowers/specs/2026-09-30-browser-order-history-and-independent-carts-design.md#outcome) with one reviewable code package and a staged production verification.

## Scope

The [specification's customer behavior, payment retry, identity, and boundaries](../superpowers/specs/2026-09-30-browser-order-history-and-independent-carts-design.md#customer-behavior) are authoritative. No scope delta.

## Acceptance criteria

Use the [eight specification criteria](../superpowers/specs/2026-09-30-browser-order-history-and-independent-carts-design.md#acceptance-criteria). Delivery also requires the old pending Order and Cart rows to remain readable and bank-reconcilable after deployment.

## Worker/state/artifact release safeguards

- [x] **Live-state inventory.** Read-only canonical DB snapshot at 2026-09-30 06:11 UTC: four Orders (two paid, two pending), five PaymentAttempts (two succeeded, two pending, one canceled), two unexpired Carts, both tied to pending Orders, two pending attempts without a hosted URL, zero due reconciliation attempts or active reconciliation leases, and two succeeded email deliveries. No worker protocol, serialized job payload, Object Storage object, or derived media artifact changes in this plan. Re-read these counts and active leases immediately before rollout.
- [x] **Compatibility matrix.** Existing paid Orders and deliveries remain untouched. Existing pending Orders and attempts remain readable and reconcile under both old and new Commerce workers because the schema and adapter contract do not change. Their locked Carts remain until a customer explicitly starts a new cart. New checkout consumes only its event Cart and rotates the browser Cart cookie; a rollback of web code loses the new list/retry UI but retains Order and payment evidence. New browser cookies still use the accepted Cart token format and old code can read their new selections. Keep the Commerce worker running during a web rollback.
- [x] **Reviewed data-state migration or reset semantics.** No schema migration, bulk backfill, or destructive reset. Existing Cart, Order, PaymentAttempt, EmailDelivery, and evidence rows stay in place. A CSRF-protected customer action releases one legacy locked Cart without changing its Order or attempts. New Order creation consumes its event Cart transactionally and reassociates other event Carts with a new digest. Paid transition ceases Cart mutation for new and old Orders.
- [x] **End-to-end contract sizing.** The bank request, callback, and worker reconciliation DTOs are unchanged, so their existing size limits and provider validation remain authoritative. Bound the new browser history response to 50 Orders per page; test a 51st Order and stable newest-first pagination. The retry command emits one existing `PaymentRequest` for the exact immutable Order, with no new provider fields or HTTP payload shape. Existing Order pages remain capped by their current pagination.
- [x] **Previous-snapshot upgrade rehearsal.** Before deployment, run tests seeded with a pre-change pending Order plus no-URL active attempt and locked Cart, a canceled attempt, a paid Order, and a second event Cart. Expected outcomes: all Orders remain accessible; active attempts reconcile without duplication; explicit Cart reset preserves the other event; no data is purged; late success leaves the new Cart intact. Execute the same read-only state inventory on the deployed candidate before exposing the new action.
- [x] **Staged activation and rollback order.** Land code behind the existing paid-cart and paid-purchase gates, pass selected suites and CI, then deploy the exact reviewed main commit through the canonical workflow. Inspect live Order/attempt counts and feature flags; smoke the existing pending Order page, browser history, and legacy Cart reset with staff access without triggering a new bank charge. Stop if any Order is misidentified, a second attempt appears for an active payment, or another event Cart disappears. Keep public gate state unchanged. Rollback closes paid purchase before reverting web code, leaves reconciliation running, and preserves all commercial rows.
- [x] **Supported bounded operational commands.** Use read-only `docker exec ... python manage.py shell` aggregate queries for Order/attempt/Cart counts and a one-Order lookup by public number; no ad hoc SQL updates, broad requeue, or artifact purge. Customer Cart reset and exact-Order payment retry use the reviewed HTTP routes with CSRF and capability checks. Operator payment status changes remain existing audited Django Admin actions.

## Implementation

### Task 1: Browser-scoped Order history

**Files:** `src/backend/commerce/capabilities.py`, `src/backend/commerce/views.py`, `src/backend/commerce/urls.py`, new `src/backend/commerce/context_processors.py`, `src/backend/config/settings.py`, `src/backend/templates/ui/base.html`, new `src/backend/templates/commerce/order_list.html`, `src/backend/static/ui/catalog.css`, `src/backend/commerce/tests/test_capabilities.py`, `src/backend/commerce/tests/test_order_views.py`, `tests/visual/views.py`, `tests/visual/urls.py`, `tests/visual/visual.spec.js`, and the selected visual snapshots.

- **Specification:** Customer behavior and Identity and transaction boundaries.
- **Depends on:** Accepted ADR 0047.
- **Produces:** One private `commerce:order_list` GET route, a purchase-cookie-scoped query with existing 30-day validity, and a gate-aware navigation link.

- [ ] Add failing tests for current/foreign/expired/missing purchase cookies, 50-plus-one pagination, private/no-store responses, masked customer data, and purchase-gate visibility.
- [ ] Run `make test TESTS="src/backend/commerce/tests/test_capabilities.py src/backend/commerce/tests/test_order_views.py"` and confirm the named new tests fail for missing behavior.
- [ ] Implement the route and nav using the existing purchase-cookie digest and latest-Order validity rule; never use Cart identity or email lookup as list authority.
- [ ] Rerun the same targeted command and `npm run test:js`; confirm the new tests pass.

### Task 2: Consume an event Cart when an Order is created

**Files:** `src/backend/commerce/checkout.py`, `src/backend/commerce/services.py`, `src/backend/commerce/views.py`, `src/backend/commerce/payments.py`, `src/backend/commerce/urls.py`, `src/backend/templates/commerce/cart.html`, `src/backend/commerce/tests/test_checkout.py`, `src/backend/commerce/tests/test_checkout_views.py`, `src/backend/commerce/tests/test_services.py`, `src/backend/commerce/tests/test_payments.py`, and `src/backend/commerce/tests/test_views.py`.

- **Specification:** Customer behavior; Identity and transaction boundaries; acceptance criteria 1–3, 6–7.
- **Depends on:** Task 1's browser Order access.
- **Produces:** Checkout response data carrying the rotated Cart token on redirect and handled failure; one explicit legacy Cart reset action; paid fulfillment independent of Cart contents.

- [ ] Add failing service and HTTP tests for first Order creation, handled bank initiation error, same-event second Order (including the same photo), preservation of another event Cart and its expiry, stale-tab replay, and explicit release of an old locked Cart.
- [ ] Add a payment-transition regression that a verified late success for an old Order does not delete a newly selected copy of its photo.
- [ ] Run `make test TESTS="src/backend/commerce/tests/test_checkout.py src/backend/commerce/tests/test_checkout_views.py src/backend/commerce/tests/test_services.py src/backend/commerce/tests/test_payments.py src/backend/commerce/tests/test_views.py"` and confirm the new tests fail on current behavior.
- [ ] Implement atomic Cart-token rotation and reassociation under the existing digest lock, remove paid-time Cart removal, and deliver both browser cookies on successful and handled-failure checkout responses. The checkout failure page must link to its newly created Order instead of rendering stale Cart contents. Keep existing unresolved Orders and attempts unchanged.
- [ ] Rerun the targeted command; confirm the accepted cart/payment invariants pass.

### Task 3: Exact-Order payment continuation

**Files:** `src/backend/commerce/checkout.py` or a focused new `src/backend/commerce/order_payment.py`, `src/backend/commerce/views.py`, `src/backend/commerce/urls.py`, `src/backend/commerce/presentation.py`, `src/backend/templates/commerce/order.html`, `src/backend/templates/commerce/cart.html`, `src/backend/static/ui/catalog.css`, `src/backend/commerce/tests/test_checkout.py`, `src/backend/commerce/tests/test_checkout_views.py`, and `src/backend/commerce/tests/test_order_views.py`.

- **Specification:** Completing an Order; Identity and transaction boundaries; acceptance criteria 4–5.
- **Depends on:** Task 2's cart-independent checkout.
- **Produces:** One CSRF-protected `commerce:order_retry_payment` POST route, exact-Order initiation through the existing gateway, and truthful Order-page feedback.

- [ ] Add failing tests for an active attempt with valid URL, an active no-URL attempt, terminal unsuccessful attempt, gateway failure, paid/canceled/superseded Orders, wrong/expired purchase cookie, CSRF, and immutable amount/email/items.
- [ ] Run `make test TESTS="src/backend/commerce/tests/test_checkout.py src/backend/commerce/tests/test_order_views.py src/backend/commerce/tests/test_checkout_views.py"` and confirm the named new tests fail.
- [ ] Extract the current gateway-initiation step for reuse, keep database locks and provider I/O separated, and reject any branch that could create another Order or a second active attempt. Validate persisted hosted URLs before redirecting. Show waiting feedback while reconciliation remains uncertain.
- [ ] Rerun the targeted command and confirm the new tests pass. The Cart's legacy link says **Открыть заказ**; the Order page owns **Повторить оплату**.

### Task 4: Product flow and visual verification

**Files:** `src/backend/commerce/tests/test_paid_photo_purchase_flow.py`, `src/backend/commerce/tests/test_paid_photo_cart_flow.py`, `tests/visual/views.py`, `tests/visual/urls.py`, `tests/visual/visual.spec.js`, `tests/visual/visual.spec.js-snapshots/`, `src/backend/static/ui/catalog.css`, `.agents/skills/update-visual-design/references/screen-inventory.md`, and `docs/product-jobs.md`.

- **Specification:** All acceptance criteria and customer-facing navigation/copy.
- **Depends on:** Tasks 1–3.
- **Produces:** End-to-end test evidence for the two-order browser flow and reviewed desktop/mobile visual states.

- [ ] Extend the purchase-flow fixture from Cart → failed bank initiation → Order list → exact-Order retry and an independent second Cart/Order; assert no original access before trusted payment and no mutation of the second Cart after late success.
- [ ] Run `make test TESTS="src/backend/commerce/tests/test_paid_photo_purchase_flow.py src/backend/commerce/tests/test_paid_photo_cart_flow.py"` and confirm the new scenario fails, then passes after integration.
- [ ] Add deterministic visual fixtures for browser Order history and retryable/waiting Order states. Run `npm run test:visual:update` for intentional screenshots, inspect every changed image, update the screen inventory, then run `npm run test:visual` and `npm run test:js`.
- [ ] Reconcile product-job status with code/test evidence, distinguishing local behavior from deployed customer proof.

### Final task: Architecture and ADR reconciliation

- [ ] Compare the final behavior with the approved specification, ADRs 0030/0031/0047, and `docs/architecture.md`; update the architecture's implementation status only after verification.
- [ ] Run the selector and fingerprint against the final package, normalize/type-check exact changed Python files with `.venv/bin/pre-commit run --files <changed Python paths>`, run `make static` after integration, and run `make check` once on the final branch.
- [ ] Run every selector-required expensive suite with GREEN evidence for that exact fingerprint; do not reuse evidence after a package change. Record exact commands, exits, and results.
- [ ] Review the full diff and report ADR conformance before one consolidated implementation commit and PR. Execute the approved plan through `$execute-implementation-plan` and its implementer/reviewer gates.

## Verification

- Focused RED/GREEN commands are listed with each task. GREEN means all named tests pass after the last affected edit.
- Final selector: `.venv/bin/python scripts/select_test_suites.py select --base origin/main`; fingerprint: `.venv/bin/python scripts/select_test_suites.py fingerprint --base origin/main`. Re-run after the final change.
- Final Python gate: `make check`; selected expensive gates use `make test-operational`, `make test-migrations`, and `npm run test:visual` only as required by the selector. The visual job also requires `npm run test:js`.
- Deploy/live gate: exact merged SHA on the canonical VM, read-only Order/attempt/Cart inventory, staff-only browser flow and old-Order page; a browser redirect is not bank payment evidence.

## Operational impact and rollout

No new secret, environment variable, worker protocol, provider field, storage resource, or schema migration. The existing purchase/cart gates control entry. Deploy only after PR review, final-package local checks, and CI. Keep real payment initiation tied to an explicit customer action. Production smoke must not automatically retry the existing pending bank attempts. The user performs any card payment; bank-confirmed state is checked separately.

## Rollback

Set the paid-purchase gate off briefly to stop new checkouts and exact-Order retries; this also temporarily hides customer Order pages, so restore the gate as soon as the previous web image is healthy. Keep the Commerce worker and already-created Orders/attempts; inspect bank and reconciliation evidence before reverting web code. Reverting the web release removes Order-history/retry UI but must not delete Orders or Carts. Restore the new flow only after reproducing the problem with a fixed test and a reviewed new commit.

## Open questions

None. A test or live observation that contradicts the accepted Cart rotation or payment-safety contract returns to the maintainer as a scope/ADR decision rather than becoming an unreviewed workaround.
