# 0031: Use orders and adapters for paid original delivery

- Status: Accepted
- Date: 2026-08-20
- Deciders: project maintainers
- Supersedes: [ADR 0029](0029-use-watermarked-previews-for-paid-photos.md), only for original
  authorization after a qualifying Order becomes paid
- Superseded by: none

## Context

[ADR 0029](0029-use-watermarked-previews-for-paid-photos.md) permits public presentation of a
paid-policy photo only through its accepted watermarked derivative and denies access to its
original. [ADR 0030](0030-use-anonymous-server-side-event-carts.md) adds anonymous event-separated
selection while deliberately making cart possession insufficient for payment or original access.

The next product increment must accept payment for one immutable selection and permanently deliver
exactly those originals. The bank protocol and production email provider are not yet selected, so
provider SDKs and callback formats cannot become Commerce domain contracts. Payment confirmation
can also require trusted manual recovery when the operator verifies a transfer outside the
application. Customer access must survive gallery unpublication and email correction without
introducing accounts or making a cart token a media credential.

These choices affect durable commercial evidence, authorization, external I/O, background work,
privacy, operations, and the boundary established by ADR 0029, so they require an ADR before an
implementation plan.

## Decision drivers

- Grant an original only from authoritative payment evidence or a trusted administrator action.
- Preserve exact item, price, currency, and payment-attempt evidence after checkout.
- Keep the unavailable bank and email protocols replaceable without a speculative provider
  framework.
- Support permanent anonymous fulfillment and operator recovery without customer accounts.
- Keep public paid-photo presentation watermarked before purchase.
- Fail closed until real adapters, fiscal fields, legal copy, worker monitoring, and operational
  activation are approved.

## Considered options

1. Persist immutable Orders and PaymentAttempts in Commerce, isolate bank and email I/O behind
   narrow adapters, and derive permanent original entitlement from paid OrderItems.
2. Integrate the first bank directly into Django views and models, then add abstractions when a
   second provider appears.
3. Treat a successful browser return or paid cart as sufficient original authorization.
4. Require an authenticated customer account and attach purchases to that identity.

## Decision

Choose option 1.

Commerce owns an immutable, single-event, RUB Order snapshot and quantity-one OrderItems. An Order
may have multiple PaymentAttempts, with at most one nonterminal attempt. PostgreSQL is authoritative
for Order state, normalized payment evidence, customer delivery work, operator attention, access
grants, and download-grant audit.

The core uses a narrow `PaymentGateway` seam for hosted-payment creation, current-status fetch, and
authenticated notification normalization. A payment becomes authoritative only through verified
normalized provider evidence with the exact Order amount and currency, or through an explicitly
trusted Django Admin action after the operator checks the bank externally. Browser returns, public
Order references, positive cart totals, and unverified callback fields never create entitlement.

A paid OrderItem is the durable entitlement fact for its exact Photo original. This supersedes ADR
0029 only after that qualifying paid transition. ADR 0029 continues to require watermarked public
presentation and to deny the original before purchase. Cart tokens remain selection authority only.
Django rechecks the paid OrderItem and customer capability before issuing the short-lived,
exact-object signed attachment URL accepted by ADR 0020.

Anonymous fulfillment uses two capabilities distinct from cart identity: a short-lived
purchase-browser capability for the checkout browser and permanent, individually revocable signed
Order access grants for email and support delivery. The database stores digests and grant metadata,
not complete bearer secrets or permanent Object Storage URLs. Entitlement survives Event or Photo
unpublication, while paid Event and Photo rows are protected from ordinary deletion.

Customer email is asynchronous notification rather than entitlement authority. A narrow
`EmailSender` seam and durable EmailDelivery work allow bounded retries and trusted resend after an
administrator corrects the delivery address. One lightweight PostgreSQL-polling Commerce worker
handles email and payment reconciliation as separate work types; it does not reuse the image
worker or introduce a general broker.

Durable CommerceAttention records surface payment conflicts, missing paid originals, exhausted
email delivery, reconciliation failure, and stale ready work in Django Admin, safe staff email, and
structured logs. An independent deployed monitor must detect a stopped Commerce worker because the
worker cannot reliably notify about its own death.

The capability is controlled by an independent database runtime gate which is absent or off by
default. The initial implementation may use deterministic local/test adapters, but deployed
configuration rejects them. Real payment, customer email, fiscalization, public activation,
refund workflows, ZIP delivery, accounts, and cross-event checkout are outside this decision.

## Consequences

### Positive

- Payment, entitlement, and fulfillment remain reconstructable from immutable PostgreSQL evidence.
- A future bank or email provider can be added at a narrow seam without leaking its SDK into domain
  services or inventing a universal payment framework.
- Late verified payment and trusted manual recovery can fulfill the original immutable obligation.
- Permanent customer access remains independent of cart retention, gallery publication, and email
  delivery success.
- The public system can receive the schema and dark code without accepting money or exposing paid
  originals.

### Negative

- Commerce gains several durable records and a separate background process with operational
  monitoring requirements.
- Permanent bearer links can be forwarded and remain valid until individually revoked.
- Trusted manual confirmation can create entitlement without machine-verifiable bank evidence; the
  ordinary Django Admin actor/time audit is the accepted control for this increment.
- Paid Photo and Event rows cannot follow ordinary destructive cleanup.
- Real activation remains blocked on external bank, fiscal, email, legal, and operational facts.

### Follow-up

- Write an implementation plan only after this ADR is explicitly accepted.
- Base implementation on current `main` only after the paid-watermark and anonymous-cart increments
  have merged in that order; reconcile rather than copy their parallel branch changes.
- Implement and review a concrete bank adapter after its protocol is available.
- Complete fiscal, legal, email-provider, worker-monitoring, staff-smoke, and explicit activation
  gates before enabling public payment.
- Design refunds and ZIP delivery separately if those product requirements are accepted.

## Validation and rollback

Validate the decision with focused tests proving immutable order snapshots, one active attempt,
authenticated and idempotent payment transitions, manual recovery, strict paid-OrderItem
authorization, permanent/revocable customer access, email-outbox retries, operator attention, and
fail-closed runtime/test-adapter gates. A local staff flow must exercise success, cancellation,
pending reconciliation, email failure, corrected delivery, late payment, missing original, and
cross-order denial without real money or network email.

Before public activation, validate a concrete provider sandbox, fiscal payload, real email
delivery, independent worker alerting, legal copy, and one end-to-end paid original on the deployed
candidate. Disable the runtime gate and stop new external payment creation to roll back customer
entry while preserving Orders, paid entitlement, access grants, and commercial evidence. Revisit
the decision if a selected provider cannot support authenticated idempotent status evidence, or if
legal/accounting requirements invalidate permanent anonymous fulfillment.

## References

- [Paid Photo Purchase and Original Delivery Design](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md)
- [Architecture: Target MVP module responsibilities](../architecture.md#target-mvp-architecture--proposed)
- [Architecture: Purchase and download](../architecture.md#purchase-and-download)
- [Architecture: Security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- [ADR 0001: Use a Django modular monolith](0001-django-modular-monolith.md)
- [ADR 0002: Use PostgreSQL as the system of record](0002-postgresql-system-of-record.md)
- [ADR 0020: Use signed direct Object Storage media delivery](0020-use-signed-direct-object-storage-media-delivery.md)
- [ADR 0028: Operate one canonical deployment](0028-operate-one-canonical-deployment.md)
- [ADR 0029: Use watermarked previews for paid photo presentation](0029-use-watermarked-previews-for-paid-photos.md)
- [ADR 0030: Use anonymous server-side event carts](0030-use-anonymous-server-side-event-carts.md)
