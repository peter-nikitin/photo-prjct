# Operator cancellation of an unusable T-Bank attempt

- Date: 2026-09-30
- Status: Approved for implementation by the operator request
- Owner: project maintainer
- Related specification: none; this bounded recovery action extends the accepted payment flow
- Related architecture: [Purchase and download](../architecture.md#purchase-and-download)
- Related ADRs: [0031](../adr/0031-use-orders-and-adapters-for-paid-original-delivery.md), [0047](../adr/0047-separate-order-payment-from-new-cart-selection.md)
- ADR impact: conforms to ADR 0047's requirement that a new attempt starts only after the previous attempt is terminal; no refund or post-payment reversal workflow is added

## Goal and scope

Let an authorized Commerce operator cancel one T-Bank attempt that has a bank PaymentId but no hosted URL, leaving its Order pending so the customer can retry after authenticated bank evidence confirms cancellation. Put the action on the Order detail page. Do not permit arbitrary status editing, cancel an Order, refund a charged payment, or initiate a new payment in the operator request.

## Acceptance criteria

- The Order detail page shows the bank-cancellation action only for an authorized operator and an eligible pending T-Bank attempt without a usable URL; the page explains that Order cancellation is a separate operation.
- A separate confirmation POST acts on exactly one attempt. The server rechecks eligibility and signed bank `GetState`; it calls `Cancel` only while the bank says `NEW`. The bank's subsequent `GetState` must establish `CANCELED` before the existing payment transition records evidence and makes Order retry available.
- A different bank state, an uncertain bank response, a mismatched identity or amount, a stale Order, repeated POST, and missing permissions fail without a new `Init` or a locally fabricated terminal status.
- The operator action is audited. No bank cancellation is invoked by page views, deployment, tests against production, or the background worker.

## Worker/state/artifact release safeguards

- [x] **Live-state inventory.** On 2026-09-30, Order FM-MY66U9JC is pending; attempt 5 is pending with a PaymentId, no hosted URL, and a read-only `GetState` of `NEW`. Existing Commerce reconciliation continues every five minutes. This change affects one selected attempt per operator POST; no generated artifact or Object Storage object is involved.
- [x] **Compatibility matrix.** Old and new Commerce workers both understand existing `pending` and `canceled` evidence. Old and new web versions can read the same Order/Attempt rows. Only the new admin view can call bank `Cancel`; rolling deployment does not require a worker contract change.
- [x] **Reviewed data-state migration or reset semantics.** No schema migration, reset, backfill, or row rewrite is needed. The existing authenticated observation transition records a bank-confirmed `CANCELED` result.
- [x] **End-to-end contract sizing.** One PaymentId and one bounded bank response per `GetState`/`Cancel` call use the existing 64 KiB bank response limit. The admin POST includes only the selected local Order/Attempt identity and CSRF token; it does not contain card data, bank credentials, or a receipt.
- [x] **Previous-snapshot upgrade rehearsal.** Tests load pre-change pending Orders/Attempts with and without a PaymentId or URL, plus terminal and paid rows, and verify eligibility and unchanged existing data.
- [x] **Staged activation and rollback order.** Merge and deploy code with the existing staff purchase gate; verify the admin control and permissions with a test fixture before any live cancellation. Stop on bank errors or identity mismatch. Code rollback removes the control without changing previously authenticated evidence; a completed bank cancellation itself cannot be undone.
- [x] **Supported bounded operational commands.** The only mutation path is a CSRF-protected admin POST for one selected Order/Attempt after a confirmation page. Existing read-only admin refresh and worker reconciliation remain available; no bulk or generic CLI cancellation is added.

## Implementation

### Task 1: Bank and domain cancellation seam

**Files:** `src/backend/commerce/tbank_gateway.py`, `src/backend/commerce/payments.py`, focused tests in `src/backend/commerce/tests/`.

- [x] Add failing tests for `NEW` cancellation, changed bank status, uncertain response, identity mismatch, and exact terminal evidence.
- [x] Add a T-Bank-specific `Cancel` operation behind a domain service that rejects an Order/Attempt with a saved URL or any nonpending state. Reuse the existing observation transition after bank confirmation.
- [x] Run focused gateway and payment tests GREEN.

### Task 2: Discoverable operator action

**Files:** `src/backend/commerce/admin.py`, `src/backend/templates/admin/commerce/order/`, `src/backend/commerce/tests/test_admin.py`.

- [x] Add failing tests for Order-detail visibility, confirmation, permission/CSRF, exact-attempt POST, success audit, and refusal paths.
- [x] Add a direct Order-detail control and confirmation page with an exact one-attempt POST. Keep immutable Order status read-only and explain the distinction from Order cancellation.
- [x] Run focused admin tests GREEN.

### Final task: Architecture and ADR reconciliation

- [x] Compare the delivered action with ADR 0031, ADR 0047, and the Purchase and download architecture text.
- [x] Update architecture and operator documentation for the new action; record the final ADR impact in the PR.

## Verification

- `make test TESTS='src/backend/commerce/tests/test_tbank_gateway.py src/backend/commerce/tests/test_payments.py src/backend/commerce/tests/test_admin.py'`: focused bank, transition, and admin tests pass.
- `.venv/bin/pre-commit run --files <changed Python files>` and `make static`: Ruff and mypy pass.
- Select suites with `scripts/select_test_suites.py`; run `make check` and every selector-required expensive suite on the final package fingerprint. CI repeats those checks after push.

## Operational impact and rollback

No settings, secret, IAM, schema, worker deployment contract, or feature-gate change. The action appears only for staff with Order change permission while the paid purchase gate is active. Do not call it on the live attempt as part of implementation verification. To stop future cancellations, roll back the web code or close the purchase gate; retain all bank evidence and already completed cancellations.

## Open questions

None. The user approved an operator-only recovery action after checking the current `NEW` state.
