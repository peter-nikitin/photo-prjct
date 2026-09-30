# 0047: Separate Order payment from new cart selection

- Status: Accepted
- Date: 2026-09-30
- Deciders: project maintainers
- Supersedes: [ADR 0031](0031-use-orders-and-adapters-for-paid-original-delivery.md), only its decisions to retain and lock an originating cart until payment, retry from that cart, and remove cart positions on paid fulfillment
- Superseded by: none

## Context

ADR 0031 keeps an anonymous event cart after Order creation. An active PaymentAttempt locks that cart, and a failed bank initiation can leave the Order pending without a usable hosted URL. The customer then cannot select more photos, start another Order for the event, or resume payment from the Order page. A cart link labeled as payment continuation leads to a page with no payment action.

The customer needs to complete an existing immutable Order and independently start another selection, even when the first payment remains uncertain. The current purchase-browser bearer can identify Orders from one browser for its existing 30-day lifetime. The cart bearer must remain limited to selection.

## Decision drivers

- Preserve one active payment attempt per Order and authoritative bank evidence before fulfillment.
- Let a new Order use the same event and even the same photo without mutating the old Order.
- Keep other event carts in the browser, and keep cart and Order authority separate.
- Recover existing locked carts without canceling their unresolved payments.
- Avoid a schema migration when an atomic identity rotation meets these constraints.

## Considered options

1. On Order creation, remove the event cart and rotate the random cart bearer, moving other event carts to its new digest. Resume payment by exact Order from the purchase-browser bearer.
2. Add a durable cart generation to Cart and Order, keep one browser cart bearer, and distinguish successive carts through the new generation and a data migration.
3. Retain ADR 0031's locked cart and add only a retry action to the Order page. This leaves customers unable to start another selection during an unresolved payment.

## Decision

Choose option 1. Creating an immutable Order consumes its event selection in the same database transaction and gives the browser a new cart bearer. Other event carts move to that new bearer without changing their contents or expiry. An unsuccessful or uncertain bank initiation does not restore the old cart. The purchase-browser bearer remains separate and continues to authorize the exact Order.

Offer a private browser Order list under the existing 30-day purchase capability. A CSRF-protected Order action resumes a valid hosted URL or creates a new attempt after all prior attempts are terminal and unsuccessful. While bank initiation remains uncertain, reconciliation continues and no second active attempt is created. Paid, canceled, and superseded Orders cannot initiate another payment.

The cart no longer controls payment retry or paid fulfillment. A late verified payment fulfills only the immutable OrderItems and never deletes positions from a newer cart. A customer may place another Order for the same photo. Existing locked carts may be explicitly reset to a new cart identity without changing their Orders or payment evidence.

The cart bearer cookie has a 30-day browser lifetime from issuance or rotation. Ordinary reads and mutations neither refresh nor delete it. Server Cart expiry can extend with mutation, but a customer loses browser access to that Cart when its bearer cookie expires. Checkout and explicit legacy reset rotate the bearer and issue a new 30-day cookie.

ADR 0031's immutable Order and PaymentAttempt evidence, one-active-attempt invariant, separate purchase capability, trusted payment confirmation, paid-OrderItem entitlement, email delivery, and protected original access remain accepted. ADR 0030's cart bearer remains selection-only.

## Consequences

### Positive

- Payment uncertainty no longer freezes future selection or creates a dead end in the browser.
- Each payment retry is tied to one immutable Order; late bank evidence cannot alter a new cart.
- Existing Order and Cart schema can remain intact.

### Negative

- Checkout must rotate a cookie and preserve other event carts atomically, including handled bank-error responses.
- One browser may create and pay multiple Orders containing the same photo. A customer can lose browser history by deleting or outliving the purchase cookie; permanent recovery remains through individual grants or support.
- Already locked carts need an explicit one-time reset path.

### Follow-up

- Implement and verify [the approved specification](../superpowers/specs/2026-09-30-browser-order-history-and-independent-carts-design.md) after this ADR is accepted.
- Reconcile the Purchase and download architecture summary and the affected product-job evidence after delivery.

## Validation and rollback

Validate failed bank initiation, hosted-URL continuation, uncertain-initiation reconciliation, terminal retry, same-event second Order, preservation of another event's cart, exact browser authorization, and late success without mutation of a newer cart. Inspect desktop and mobile navigation, cart, and Order states.

Rollback first closes new checkout through the existing paid-purchase gate while retaining payment reconciliation and Order access. Existing Orders and attempts remain durable; an application rollback must not erase those records or treat a browser return as payment evidence. Revisit the decision if cart-token rotation cannot preserve other event carts safely under concurrent checkout and cart mutations.

## References

- [Browser Order History and Independent Carts design](../superpowers/specs/2026-09-30-browser-order-history-and-independent-carts-design.md)
- [Architecture: Purchase and download](../architecture.md#purchase-and-download)
- [ADR 0030: Use anonymous server-side event carts](0030-use-anonymous-server-side-event-carts.md)
- [ADR 0031: Use orders and adapters for paid original delivery](0031-use-orders-and-adapters-for-paid-original-delivery.md)
