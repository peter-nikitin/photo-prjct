# T-Bank eacq payment gateway implementation plan

- Date: 2026-09-23
- Status: Approved for implementation by the maintainer's request on 2026-09-23
- Owner: project maintainer
- Related specification: [T-Bank eacq payment gateway](../superpowers/specs/2026-09-23-tbank-eacq-payment-gateway-design.md)
- Related architecture: [Purchase and download](../architecture.md#purchase-and-download),
  [Checkout, payment, entitlement, and original-delivery seam](../architecture.md#checkout-payment-entitlement-and-original-delivery-seam)
- Related ADRs: [0031](../adr/0031-use-orders-and-adapters-for-paid-original-delivery.md),
  [0028](../adr/0028-operate-one-canonical-deployment.md),
  [0032](../adr/0032-reconcile-code-owned-feature-flags-at-startup.md)
- ADR impact: Conforms to accepted ADR 0031; no new or superseding ADR.

## Goal

Implement the [approved outcome](../superpowers/specs/2026-09-23-tbank-eacq-payment-gateway-design.md#outcome)
behind existing Commerce and feature-gate boundaries without initiating a live payment.

## Scope

The [specification's scope](../superpowers/specs/2026-09-23-tbank-eacq-payment-gateway-design.md#scope-and-boundaries)
is unchanged. Payment acceptance uses the existing Order and entitlement state; this plan adds no
customer account, second provider, or refund system.

## Acceptance criteria

All nine [specification acceptance criteria](../superpowers/specs/2026-09-23-tbank-eacq-payment-gateway-design.md#acceptance-criteria)
apply. Bank sandbox acceptance and real fiscal profile are separate activation gates because no
test/live terminal credentials or merchant-approved tax values are provided to this worktree.

## Worker/state/artifact release safeguards

- [x] **Live-state inventory.** Read-only canonical VM snapshot on 2026-09-23: Commerce worker
  running; `paid-photo-purchase=staff`, `paid-photo-payment-simulator=staff`; two paid Orders,
  two succeeded simulator PaymentAttempts, two notification evidence rows, zero pending attempts,
  zero active/expired leases and zero overdue reconciliation rows. No T-Bank identity exists.
  This plan changes no generated photo/media artifact or Object Storage prefix.
- [x] **Compatibility matrix.** New web/worker read existing succeeded simulator rows unchanged;
  old web/worker can read an additively migrated attempt schema while the real adapter remains
  unselected. New T-Bank attempts are created only after new web and worker are configured
  together, so an old worker never needs to interpret them. Existing paid entitlement and emails
  remain valid across both images. Rollback after a live T-Bank charge requires keeping a capable
  reconciler running and is not a blind image revert.
- [x] **Reviewed data-state migration or reset semantics.** Add only nullable/optional T-Bank
  attempt identity and uncertainty fields if the implementation needs them; leave all current
  simulator rows and PaymentEvidence untouched. No backfill, purge, or reset. Uncertain T-Bank
  attempts remain durable and operator-visible until bank lookup resolves them.
- [x] **End-to-end contract sizing.** The maximum current purchase page is 100 OrderItems; validate
  the bank's 100-line receipt cap, receipt email/name limits, HTTP payload bound, response parsing,
  callback body bound, Django fields, and PostgreSQL persistence as one contract. Reject an
  oversized/incompatible receipt before `Init`, without losing its Order or cart.
- [x] **Previous-snapshot upgrade rehearsal.** Migration tests start from the immediately previous
  Commerce migration with successful, pending, failed, and leased simulator attempts, then verify
  row preservation and T-Bank defaults. Existing canonical snapshot currently has only two
  succeeded simulator attempts and no leases; synthetic old-state rows exercise the absent cases.
- [x] **Staged activation and rollback order.** Merge/deploy schema and code with real adapter
  unselected; confirm deployed SHA, flags, simulator rows, worker health, and public HTTPS.
  Configure test credentials/fiscal profile only for explicit staff acceptance; public `on`
  requires separate merchant/legal/operational approval. On failure stop new `Init`, inspect
  pending T-Bank attempts and reconcile accepted charges before replacing the adapter or image.
- [x] **Supported bounded operational commands.** Read-only Django Admin Commerce attempts,
  attention, and worker-health command inspect state; existing Admin manual confirmation is the
  bounded recovery path after an independent bank check. No bulk requeue, backfill, purge, or
  media reset command is needed or authorized.

## Implementation

Execute the approved tasks with `$execute-implementation-plan`.

### Task 1: T-Bank protocol adapter and receipt

**Files:** `src/backend/commerce/tbank_gateway.py`, `src/backend/commerce/tests/test_tbank_gateway.py`,
`src/backend/commerce/payment_gateway.py` only if a narrow provider-neutral input is necessary.

- **Specification:** [Selected design](../superpowers/specs/2026-09-23-tbank-eacq-payment-gateway-design.md#selected-design-and-alternatives),
  [initiation](../superpowers/specs/2026-09-23-tbank-eacq-payment-gateway-design.md#payment-initiation-and-attempt-identity),
  [receipt](../superpowers/specs/2026-09-23-tbank-eacq-payment-gateway-design.md#receipt-and-merchant-configuration).
- **Depends on:** None.
- **Produces:** one `PaymentGateway` implementation, a factory with fail-closed configuration,
  deterministic attempt `OrderId`, signed `Init`/`GetState`/`CheckOrder`, strict response/status
  handling, and receipt conversion. It must expose the small order-lookup/recovery hook consumed
  by Task 2 without widening the generic gateway for the simulator.

- [ ] Write focused failing tests for signing (including booleans/nested fields), exact receipt,
  unique per-attempt order ID, `Init` success/error/timeout, malformed response, `GetState`,
  `CheckOrder`, and status mapping; run `make test TESTS="src/backend/commerce/tests/test_tbank_gateway.py"`
  and confirm RED.
- [ ] Implement the minimal adapter with a bounded network timeout, safe error categories,
  configuration validation, and no secret/raw-payload logging.
- [ ] Run the same focused command GREEN and `.venv/bin/pre-commit run --files
  src/backend/commerce/tbank_gateway.py src/backend/commerce/payment_gateway.py` for changed
  Python files only.

### Task 2: Durable initiation recovery, callback, and reconciliation

**Files:** `src/backend/commerce/models.py`, a new Commerce migration if needed,
`src/backend/commerce/checkout.py`, `src/backend/commerce/payments.py`,
`src/backend/commerce/views.py`, `src/backend/commerce/urls.py`,
`src/backend/commerce/worker.py`, and focused tests in `src/backend/commerce/tests/`.

- **Specification:** [initiation](../superpowers/specs/2026-09-23-tbank-eacq-payment-gateway-design.md#payment-initiation-and-attempt-identity),
  [notification and status](../superpowers/specs/2026-09-23-tbank-eacq-payment-gateway-design.md#notification-status-and-fulfillment),
  [exposure](../superpowers/specs/2026-09-23-tbank-eacq-payment-gateway-design.md#exposure-and-failure-semantics).
- **Depends on:** Task 1 adapter and recovery hook.
- **Produces:** persisted uncertainty that prevents duplicate `Init`, signed and matched callback
  routing with exact `OK` acknowledgment, return-triggered bank lookup, at-most-15-minute worker
  reconciliation for pending T-Bank attempts, and unaltered simulator/paid entitlement behavior.

- [ ] Add failing checkout, callback, worker, and previous-migration-state tests for successful,
  duplicate, forged, mismatched, delayed, timeout, and already-paid observations. Run each focused
  `make test TESTS="..."` command and record the expected RED.
- [ ] Implement the smallest durable recovery and routing changes. Keep external requests outside
  DB locks; retain the existing atomic payment transition and fail-closed gate behavior.
- [ ] Run focused tests GREEN and pre-commit on exact changed Python files after the last edit.

### Task 3: Runtime/deployment contract and architecture reconciliation

**Files:** `src/backend/config/settings.py`, `src/backend/commerce/runtime.py`,
`src/backend/commerce/checks.py`, `.env.example`, `docker-compose.yml`,
`docker-compose.deployment.yml`, `.github/workflows/deploy.yml`,
`deploy/apply-deployment.sh`, `deploy/run-remote.sh`, relevant deployment/Commerce tests,
`docs/architecture.md`, `docs/product-jobs.md`, `docs/engineering-jobs.md`.

- **Specification:** [merchant configuration](../superpowers/specs/2026-09-23-tbank-eacq-payment-gateway-design.md#receipt-and-merchant-configuration),
  [exposure](../superpowers/specs/2026-09-23-tbank-eacq-payment-gateway-design.md#exposure-and-failure-semantics),
  [acceptance](../superpowers/specs/2026-09-23-tbank-eacq-payment-gateway-design.md#acceptance-criteria).
- **Depends on:** Tasks 1 and 2 interfaces.
- **Produces:** real-adapter selection for web and worker, required secret/fiscal/URL validation,
  dark deployment defaults, and documentation that distinguishes code readiness from bank sandbox,
  fiscal, legal, deployment, and live-payment evidence.

- [ ] Add failing runtime/deployment tests for adapter selection, missing/invalid settings,
  simulator protection, secret projection, disabled defaults, and rollback compatibility.
- [ ] Make the configuration and deployment changes; keep actual external setup and flag changes
  outside this task.
- [ ] Run focused runtime/deployment tests GREEN, applicable operational tests, and exact-file
  pre-commit for changed Python files.

### Final task: Architecture and ADR reconciliation

- [ ] Compare the final diff with the approved specification and ADRs 0031, 0028, and 0032.
- [ ] Confirm architecture/jobs accurately label local code, canonical deployment, credentials,
  bank sandbox, fiscal review, and public activation separately.
- [ ] Record one outcome in the PR: conformance without new ADR, or stop if a new durable decision
  emerged. Do not edit an accepted ADR to fit the implementation.

## Verification

- For each changed behavior, focused RED then GREEN through `make test TESTS="<selectors>"`.
- `.venv/bin/pre-commit run --files <exact changed Python files>` after task implementation.
- `scripts/select_test_suites.py select --base origin/main` and
  `scripts/select_test_suites.py fingerprint --base origin/main` on the final package;
  `make check` and every selector-required expensive target GREEN for that exact fingerprint.
- Actual bank sandbox acceptance, including fiscal validation, is an activation gate and cannot
  be claimed from local mocks or simulator checks.

## Operational impact and rollout

The repository can ship the adapter and any additive schema while the current staff simulator
remains configured. The current live staff flags must not be interpreted as authorization to
switch to a real terminal. Configuration, external test, worker/HTTPS verification, fiscal/legal
approval, and public flag activation are separately controlled operations. This implementation
does not perform them.

## Rollback

Before any real T-Bank attempt, revert the candidate image; existing simulator attempts and paid
entitlements remain unchanged. After a real attempt exists, stop new initiation, retain compatible
schema and a functioning T-Bank reconciler until every accepted charge is resolved, then revert
only the presentation/creation path. Never purge attempts or revoke paid access as rollback.

## Open questions

None for dark repository implementation. Merchant credentials, fiscal values, eacq Bearer-label
confirmation, bank test responses, and legal/public activation remain explicit external gates.
