# Browser Order History and Independent Carts

## Status and references

The maintainer approved this written specification in chat on 2026-09-30. ADR 0047 and the implementation plan were accepted; the repository implementation is under verification.

- Related architecture: [Architecture](../../architecture.md), especially Purchase and download and Security, privacy, and legal boundaries.
- Related product jobs: [PJ-010 Purchase selected photos](../../product-jobs.md#pj-010--customer--purchase-selected-photos) and [PJ-016 Select paid event photos](../../product-jobs.md#pj-016--customer--select-paid-event-photos).
- Related specifications: [Paid Photo Purchase and Original Delivery](2026-08-20-paid-photo-purchase-and-original-delivery-design.md) and [Anonymous Paid Photo Cart](2026-08-20-anonymous-paid-photo-cart-design.md).
- Related ADRs: [ADR 0030](../../adr/0030-use-anonymous-server-side-event-carts.md) and [ADR 0031](../../adr/0031-use-orders-and-adapters-for-paid-original-delivery.md).
- ADR impact: **Supersedes ADR 0031's cart retention, active-attempt cart lock, cart-based retry, and paid-time cart removal decisions.** The immutable Order, distinct cart and purchase capabilities, one active attempt per Order, authenticated payment evidence, and paid-OrderItem entitlement remain. The browser order list conforms to the existing temporary purchase capability. A new ADR must record the replacement decision before implementation.

## Outcome

A customer can find the orders created in the current browser, return to an unpaid Order to complete payment, and start a new selection immediately after placing an Order. A bank initiation error does not trap photos in a locked cart. Payment uncertainty does not start a second active attempt or grant access to originals.

## Customer behavior

- The main navigation contains **Мои заказы**. It opens a private, uncached list of Orders associated with the current valid purchase-browser cookie, newest first. Each row shows the public Order number, event, creation date, total, and customer-safe status, and opens that Order. With no valid cookie or matching Orders, the page shows an empty state without revealing whether other Orders exist.
- The purchase cookie keeps the existing 30-day lifetime from the most recent Order creation. The list is unavailable after cookie expiry, deletion, or on another device. No account or email-based history recovery is introduced; existing individual Order access grants and support recovery remain separate.
- The cart remains a mutable selection until checkout creates an immutable Order. In the same committed transaction, checkout removes that event's selected cart and gives subsequent selection a new cart identity. Carts for other events in the same browser retain their items and expiry. The purchase cookie remains independent and continues to authorize earlier Orders.
- The cart bearer cookie lasts 30 days from its initial issuance or a checkout/reset rotation. Ordinary cart reads and mutations do not refresh or delete that cookie. A cart's server expiry may extend after a mutation, but the browser can no longer reach it when the cookie expires; the customer may need to start a new selection then.
- This transition happens even if the subsequent bank `Init` call fails, times out, or returns no usable payment URL. The checkout response retains access to the new Order and exposes a path to its page; it does not display the old selection as an editable cart.
- A customer may select the same photo again and place another Order. Each Order has its own immutable price and items, and payment of one Order authorizes only that Order's items. A late successful payment of an earlier Order never changes the new cart or another Order.
- For Orders made before this behavior is deployed, an existing locked cart offers an explicit **Начать новую корзину** action. It clears that selection and changes only the cart identity, without canceling or changing the existing Order or its payment attempt.

## Completing an Order

- A pending Order page has a **Повторить оплату** action only when the current purchase-browser cookie authorizes that exact Order. The action is a CSRF-protected POST and uses the Order's saved immutable email, items, amount, and currency. It does not require a cart cookie or accept customer-supplied amount, items, or email.
- The customer-facing pending state distinguishes an active payment with a usable bank link, initiation still under reconciliation, and an unsuccessful terminal attempt ready for retry. A canceled attempt alone is not displayed as if payment were still being checked. The Order's durable `pending` status is unchanged until authoritative payment evidence or a trusted staff action changes it.
- If the current pending PaymentAttempt has a validated hosted payment URL, the action redirects there. If initiation is uncertain and no URL is available, the action requests or awaits reconciliation and shows a clear **Проверяем связь с банком; повторите позже** outcome. It does not create another attempt while the first remains active.
- If all earlier attempts are terminal and unsuccessful, the action creates one new attempt for the same pending Order and redirects to its validated hosted URL. A bank failure keeps the Order pending and retryable. Paid, canceled, and superseded Orders cannot start a new payment. Existing payment notifications, status fetches, and trusted staff actions remain authoritative.
- The cart no longer claims **Продолжить оплату** when it only opens an Order page. An existing locked cart links to its Order as **Открыть заказ** and offers the explicit new-cart action. Normal new carts retain their existing checkout form.
- The Order page keeps its current payment status and support contact visible. It never treats a browser return, a cookie, or a redirect as payment confirmation. Original downloads appear only after trusted paid evidence.

## Identity and transaction boundaries

- The existing cart cookie remains an opaque selection bearer, never Order or media authority. Checkout rotates it after creating an Order. The old event cart is removed, and other event carts are reassociated with the new random cart digest atomically. The new cookie is sent on both bank success redirects and handled bank-error responses. No raw token appears in a URL, HTML, log, analytics, or stored row.
- The existing purchase cookie remains an opaque Order bearer. The order-history query is restricted to its digest and applies the same latest-Order-plus-30-days validity rule as individual Order access. It does not accept an email address, public Order number, or cart cookie as list authority.
- A retry command locks the exact Order and its attempts, checks the pending status and purchase capability, and reuses the existing idempotent gateway/reconciliation boundaries. It never creates a different Order. Payment gateway I/O stays outside the database transaction.
- Payment fulfillment no longer deletes cart positions. This prevents a late payment from deleting a customer's new selection, including a newly selected copy of the same photo.
- Existing unresolved payment attempts stay in place through deployment. Their reconciliation and staff attention continue; cart reset does not cancel them.

## Boundaries

This change does not add accounts, permanent browser identity, email-based history search, payment cancellation, refunds, duplicate-purchase prevention, a new provider API, or new payment evidence rules. It does not change fiscal receipt fields, purchased-original authorization, or email delivery.

## Acceptance criteria

1. After Order creation and a failed bank initiation, the Order appears in **Мои заказы**, the event cart is empty and editable, and the Order page offers a safe retry.
2. A second Order can be placed from the same browser and event while the first remains pending, including with the same photo; both retain independent immutable items and attempts.
3. A cart at another event survives checkout with its items and original expiry.
4. A pending attempt with a hosted URL can be resumed. A pending attempt without one cannot be duplicated; after terminal failure, exactly one new attempt can be started for the same Order.
5. Missing, expired, or foreign purchase cookies reveal no Order list or retry action. POST retry requires CSRF and cannot alter amount, email, items, or Order identity.
6. A late verified success for the earlier Order grants its own originals and leaves a newly populated cart untouched.
7. Existing locked carts can be released through the explicit new-cart action without changing their Orders or payment evidence.
8. Desktop and mobile order, cart, and navigation states clearly expose these actions and statuses; no-JavaScript navigation and forms work.
