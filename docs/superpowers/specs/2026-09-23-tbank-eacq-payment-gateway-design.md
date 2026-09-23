# Приём оплаты через интернет-эквайринг Т-Банка

- Date: 2026-09-23
- Status: Approved by maintainer on 2026-09-23 for implementation. Public payment activation remains separate.
- Related architecture: [Purchase and download](../../architecture.md#purchase-and-download),
  [Checkout, payment, entitlement, and original-delivery seam](../../architecture.md#checkout-payment-entitlement-and-original-delivery-seam).
- Related ADRs: [0002](../../adr/0002-postgresql-system-of-record.md),
  [0028](../../adr/0028-operate-one-canonical-deployment.md),
  [0029](../../adr/0029-use-watermarked-previews-for-paid-photos.md),
  [0030](../../adr/0030-use-anonymous-server-side-event-carts.md),
  [0031](../../adr/0031-use-orders-and-adapters-for-paid-original-delivery.md),
  [0032](../../adr/0032-reconcile-code-owned-feature-flags-at-startup.md).
- ADR impact: **Conforms to accepted ADR 0031.** It selects the concrete bank protocol for
  ADR 0031's existing `PaymentGateway` seam and preserves its Order, entitlement, manual recovery,
  worker, and access boundaries. It conforms to ADRs 0002, 0028, 0029, 0030, and 0032 without
  changing their durable decisions. No new or superseding ADR is required.
- Related specifications: [Paid-photo purchase and original delivery](2026-08-20-paid-photo-purchase-and-original-delivery-design.md),
  [Anonymous paid-photo cart](2026-08-20-anonymous-paid-photo-cart-design.md).
- Bank sources: [Init](https://developer.tbank.ru/eacq/api/init),
  [request token](https://developer.tbank.ru/eacq/intro/developer/token),
  [notifications](https://developer.tbank.ru/eacq/intro/developer/notification),
  [GetState](https://developer.tbank.ru/eacq/api/get-state),
  [CheckOrder](https://developer.tbank.ru/eacq/api/check-order),
  [payment statuses](https://developer.tbank.ru/eacq/intro/developer/operation-statuses),
  [test environment](https://developer.tbank.ru/eacq/intro/errors/test).

## Outcome

A customer confirms the existing immutable, single-event RUB Order, moves to T-Bank's hosted
payment form, and receives access to exactly the purchased originals only after authenticated
server-side evidence establishes a one-stage completed charge. A failed or abandoned attempt
preserves the cart and permits a safe retry. Notifications, status reconciliation, and operator
attention recover delayed or uncertain outcomes without granting access from the browser return.

This increment replaces the payment simulator on the real-payment path. It uses the existing
Commerce Order, PaymentAttempt, PaymentEvidence, paid-OrderItem entitlement, access grant, email
delivery, attention, and worker behavior. FindMe Photo remains the single seller; the bank owns
the hosted payment form and payment-method choice. The bank never selects photos or grants media
access directly.

## Scope and boundaries

Included:

- One-stage hosted payment through the T-Bank eacq `/v2/Init` protocol.
- Signed `/v2/GetState` and order lookup for uncertain initiation.
- Authenticated HTTP(S) payment notifications at a public HTTPS endpoint.
- Exact mapping of bank attempt identity, amount, and statuses to existing normalized payment
  evidence and atomic Order transitions.
- Receipt payload for the configured online-cash-register arrangement, with merchant-approved
  fiscal values before any real-money activation.
- Test and deployed configuration, secret handling, operational visibility, and disabled-default
  delivery of the real adapter.

Excluded:

- Two-stage capture, saved cards, recurrent payments, embedded card entry, customer-selected
  payment providers, split settlements, photographer payouts, and a multi-provider framework.
- Refund and post-payment reversal workflows. A bank refund after fulfillment is an operator
  incident under the existing attention boundary, not automatic entitlement revocation.
- Changes to Order pricing, cart selection, purchased-original authorization, customer email
  delivery, or the legal texts themselves.
- New external bank or cash-register accounts, credential creation, production activation, or
  real card charges as part of specification approval.

## Selected design and alternatives

Select a T-Bank-specific adapter behind the existing three-operation `PaymentGateway` contract:
create a hosted payment, fetch its current state, and authenticate a notification. Commerce keeps
provider-neutral `PaymentRequest`, `CreatedPayment`, and `PaymentObservation` values. A thin Django
notification endpoint passes the unmodified body to that adapter and applies normalized evidence
through the existing transactional payment service. The Commerce worker uses the same adapter for
reconciliation. No new payment state machine is introduced.

Direct bank calls from checkout views would mix provider credentials and response formats with
Order authorization, so they are rejected. Treating `SuccessURL` or `FailURL` as payment evidence
is rejected because browser navigation is not authenticated bank confirmation. Two-stage payment
is rejected for this increment because its `AUTHORIZED` state only holds funds and would require
capture and its own failure/recovery contract. [Init](https://developer.tbank.ru/eacq/api/init),
[status reference](https://developer.tbank.ru/eacq/intro/developer/operation-statuses).

## Payment initiation and attempt identity

For each immutable PaymentAttempt the adapter sends `POST /v2/Init` server-to-server with its
configured `TerminalKey`, integer `Amount` in kopecks, a bank `OrderId` unique to **that attempt**,
`PayType=O`, a short non-sensitive `Description`, `SuccessURL`, `FailURL`, `NotificationURL`, and
`Token`. The amount is the stored Order total and equals the sum of its stored OrderItem amounts.
The bank `OrderId` is stable across retries of the **same** attempt but differs across attempts of
one Order; the Order's public number alone is insufficient because the bank requires a unique
`OrderId` for each operation. Its representation is at most 50 characters and is durably
recoverable from the attempt. `SuccessURL` and `FailURL` lead to the existing Order return
context and contain no bearer OrderAccessGrant or cart/purchase token. The bank's returned
`PaymentId` and `PaymentURL` are validated and bound to the attempt before redirecting the
customer. `Init` success means only that a payment form exists. [Init](https://developer.tbank.ru/eacq/api/init).

The application signs each eacq request using the documented terminal-password `Token` rule:
alphabetically sort the top-level request fields with `Password`, concatenate their values as
UTF-8 text, and SHA-256 the result. `Token` and nested objects such as `Receipt` and `DATA` do
not participate. Secrets and signatures remain server-side. The eacq method pages also display a
generic “Bearer API Token” label; the terminal-password `Token` is the eacq-specific contract.
The exact need for any additional authorization header must be settled against a bank test
request or bank support before production configuration. [Token](https://developer.tbank.ru/eacq/intro/developer/token),
[Init](https://developer.tbank.ru/eacq/api/init).

The adapter accepts a valid bank response only when its reported success, payment identity,
amount, and hosted URL are consistent with the request. A transport timeout or malformed response
leaves the attempt pending and its outcome uncertain; it cannot become an ordinary failed attempt
that immediately starts another bank operation. When `PaymentId` is known, `GetState` resolves the
uncertainty. When it is unknown, bank `OrderId` lookup via `CheckOrder` resolves whether the
operation was created. Until then, the customer sees a non-success state and Commerce attention
surfaces the unresolved attempt. Reusing the same attempt identity does not assume that repeating
`Init` is bank-idempotent. A new attempt is permitted only after the previous operation has been
shown absent or terminal unsuccessful. [GetState](https://developer.tbank.ru/eacq/api/get-state),
[CheckOrder](https://developer.tbank.ru/eacq/api/check-order).

## Notification, status, and fulfillment

The public HTTPS `NotificationURL` accepts bank POST JSON without a customer session or CSRF
cookie. It enforces a bounded body and valid JSON, verifies the notification `Token` using the
bank's top-level-field rule and terminal password, then matches `TerminalKey`, `PaymentId`, bank
`OrderId`, and exact amount to one persisted attempt. It does not trust a claimed status until
those checks succeed. It rejects unrecognized payment identities and invalid signatures without
writing Order state. A valid, safely processed notification returns HTTP 200 with body exactly
`OK`, including an idempotent duplicate; other outcomes must not acknowledge unprocessed
evidence as fulfilled. The bank retries notifications that do not receive `OK`. [Notifications](https://developer.tbank.ru/eacq/intro/developer/notification).

`GetState` uses signed `TerminalKey`, `PaymentId`, and `Token`. The adapter normalizes statuses
without exposing raw bank payloads to Commerce. Under one-stage payment, authenticated
`CONFIRMED` is `succeeded`. `NEW`, `FORM_SHOWED`, `AUTHORIZING`, `AUTHORIZED`, and other states
short of confirmed charge remain `pending`; `AUTHORIZED` must never grant an original. Rejected,
failed, canceled, and deadline-expired outcomes map to the corresponding terminal normalized
failure. Unknown statuses and refund/reversal statuses do not silently become success or erase an
already paid entitlement; they open operator attention for an explicit decision. [GetState](https://developer.tbank.ru/eacq/api/get-state),
[statuses](https://developer.tbank.ru/eacq/intro/developer/operation-statuses).

The currently documented eacq request and notification fields used here do not establish a
separately authenticated currency. The adapter may report `RUB` to the existing Commerce contract only after
matching the configured RUB-only terminal, `PaymentId`, attempt `OrderId`, and signed exact amount.
It must not present a locally assumed currency as bank-observed evidence without that terminal
invariant. A terminal that can settle this flow in another currency is incompatible with this
specification.

All authenticated observations use the existing atomic transition: exact matching success makes
the attempt succeeded and Order paid, creates the existing access-email work, and removes only
the purchased positions from the originating cart. Duplicate or late observations cannot create
duplicate fulfillment. Amount or identity conflict cannot pay an unpaid Order; an already paid
Order remains paid and the conflict opens operator attention. The browser return continues to
show the Order state; it never sets paid by itself. A bounded server-side `GetState` lookup on
return and worker reconciliation of pending attempts at least every 15 minutes while the bank
is reachable reduce waiting when a notification is lost, including when the
browser is closed. Reconciliation continues until a safe terminal result or operator resolution;
expiry is checked against the current bank state before it closes an attempt.

## Receipt and merchant configuration

The receipt represents each immutable OrderItem once, with quantity one, name, unit price, line
amount, and tax value; receipt totals equal the Order amount. It uses the checkout email for the
customer receipt, subject to the bank's field limits. If an online cash register is connected,
`Receipt` is required in `Init`. Its `Taxation`, per-item `Tax`, FFD-specific required fields,
`PaymentMethod`, and `PaymentObject` are explicit merchant-approved values; bank defaults do not
decide the legal classification of a photo purchase. The selected fiscal contract must also state
whether a later closing receipt is required. The application must refuse real-payment initiation
when the required receipt profile is incomplete or incompatible with an Order (including the
bank's item-count limit). [Init receipt fields](https://developer.tbank.ru/eacq/api/init),
[receipt methods](https://developer.tbank.ru/eacq/api/metodi-raboti-s-chekami).

The deployed configuration holds the terminal key, terminal password, fixed RUB-only and
one-stage terminal identity, public HTTPS callback/return origin, and approved fiscal profile.
The canonical Lockbox secret remains the persistent credential authority under ADR 0028; no
credential, customer receipt email, raw callback body, card data, or full bank response enters
logs, metrics, attention messages, or exception text. A separate test terminal/credential pair
and the bank's documented test arrangement provide integration evidence without a real charge.
[Test environment](https://developer.tbank.ru/eacq/intro/errors/test).

## Exposure and failure semantics

The existing `paid-photo-purchase` release gate remains off by default and continues to govern
customer checkout and paid-media entry points. The simulator gate cannot authorize a public
payment. Deployed configuration must reject the simulator as the real-money adapter and accept
the T-Bank adapter only with complete, consistent credentials, HTTPS URLs, and fiscal settings.
Bank failure, unavailable status, malformed response, or invalid notification does not create
entitlement. Existing manual paid confirmation remains a trusted, audited operator recovery path,
not a fallback automatically invoked by adapter errors.

If a feature gate is closed after payment initiation, the system retains every Order, attempt,
bank identity, and payment evidence. Incident handling must still be able to reconcile and fulfill
money already accepted before disabling new initiation; closing the gate is not permission to
discard paid obligations. Public activation requires the existing purchase-spec prerequisites:
bank test evidence, approved fiscal and legal terms, working email and worker, staff acceptance on
the canonical deployment, and explicit maintainer approval. This specification authorizes none
of those external changes by itself.

## Acceptance criteria

1. A valid cart creates one immutable Order and one T-Bank operation per attempt, with exact RUB
   kopecks and a distinct bank `OrderId` on a later attempt; the customer reaches a bank-hosted
   payment form and sees no card fields in FindMe Photo.
2. Request and notification token calculations match the bank's documented examples, including
   boolean values and exclusion of nested fields. Wrong token, terminal, payment identity, bank
   `OrderId`, or amount cannot mark an Order paid.
3. `CONFIRMED` from authenticated notification or `GetState` grants exactly that Order's original
   entitlements once. `AUTHORIZED`, redirect to either return URL, forged callback, and cart
   possession grant none.
4. Duplicate, reordered, delayed, and conflicting notifications preserve a single coherent Order
   state, append appropriate evidence, and surface contradictions for operator review.
5. A timed-out `Init` with or without `PaymentId` is reconciled before another bank operation can
   be created; a pending charge cannot be duplicated by repeated checkout submissions.
6. A lost callback is recoverable by `GetState` through the Commerce worker within 15 minutes
   while the bank remains reachable; a confirmed customer can reach the paid Order without
   depending on the original browser session remaining open.
7. The approved fiscal payload validates at the bank test terminal, matches the stored Order
   totals, and fails closed on missing fiscal settings or an unsupported item count.
8. The bank test arrangement covers successful and rejected payments, 3DS, callback failure,
   delayed confirmation, duplicate notification, and `Init`/`GetState` uncertainty without
   exposing secrets or real card data.
9. With the purchase gate off, no new customer payment can start. Deployed adapter selection,
   credentials, worker, and callback URL can be inspected without activating public payment;
   previously accepted obligations remain recoverable.

## Inputs required before real-money activation

The maintainer supplies test and live terminal credentials through the accepted secret path,
confirms one-stage RUB-only settings and enabled payment methods with T-Bank, and obtains an
accounting-approved receipt profile: online-cash-register provider, FFD version, taxation, tax
rate, payment subject and method, and any closing-receipt obligation. Bank support or a test
request resolves the eacq Bearer-label ambiguity and confirms actual response fields. The legal
review and explicit public gate approval required by the purchase specification remain separate
decisions. None of these values is guessed or hard-coded by this document.
