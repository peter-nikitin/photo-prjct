# 0030: Use anonymous server-side event carts

- Status: Accepted
- Date: 2026-08-20
- Deciders: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The first paid-event purchase increment needs customers to preserve and edit a photo selection
before checkout exists. Customers are not required to authenticate, and one person using different
browsers must receive different carts. The selected photos, current prices, and eligibility are
authoritative product state that must remain transactionally consistent as event publication,
photo availability, and prices change.

ADR 0001 keeps product rules in the Django modular monolith, and ADR 0002 selects PostgreSQL for
carts, orders, and entitlements. Accepted sibling ADR 0029 makes newly uploaded paid photos
browsable only through watermarked previews and denies their originals and downloads. It
deliberately leaves price, cart, payment, and entitlement undefined.

A durable decision is needed for anonymous cart authority, browser storage, event separation,
retention, price behavior, and the boundary between selecting a right and acquiring it.

## Decision drivers

- Keep anonymous selection scoped to one browser profile without accounts or contact details.
- Keep cart contents and current eligibility authoritative in Django and PostgreSQL.
- Avoid cookie growth, client-controlled product state, and cross-event checkout assumptions.
- Preserve ADR 0029's protected-media and no-download boundary before payment exists.
- Support the next fixed-price-package increment without modeling unapproved package rules now.
- Bound abandoned browser-linked state without making request correctness depend on cleanup timing.
- Deploy unfinished behavior safely under ADR 0028's database-backed runtime gates.

## Considered options

1. Store event-separated carts in PostgreSQL and address them with one opaque browser cookie.
2. Store selected photo identifiers and prices directly in one or more browser cookies.
3. Reuse Django login/session identity or require an account before selection.
4. Defer all persisted selection until checkout and keep selection only in page-local JavaScript.

## Decision

Select option 1.

Add a Commerce module inside the Django modular monolith. PostgreSQL stores one cart for each
opaque-browser-token digest and paid event, plus one unique item for each selected photo. The raw
token contains at least 256 bits of cryptographically secure randomness, is stored only in a
`Secure`, `HttpOnly`, `SameSite=Lax`, path-wide cookie, and is represented in PostgreSQL only by its
SHA-256 digest. One token may address independent carts for several events; carts never mix events.
No visit creates a token or row. The first successful eligible add creates them.

Treat the token as a narrow bearer capability for viewing and editing cart contents. It is not a
customer identity, authentication session, analytics identifier, selfie-result token, or media
authority. It must not appear in URLs, HTML, logs, or analytics. Clearing or losing the cookie
intentionally loses access, and there is no cross-browser recovery or merge.

Give every paid event one positive per-photo price stored as integer kopecks; free events have no
price. RUB is the only currency. Existing paid events receive 30000 kopecks through a data
migration. Carts store no price snapshots: every response calculates the total from the event's
current price and current eligible unique item count. Immutable price evidence remains a future
order-item responsibility. Do not add package models or compatibility fields before package rules
are approved.

Allow only a currently published paid-event photo whose explicit ADR 0029 policy has mutually
consistent accepted watermarked evidence. Commerce consumes the catalog's authoritative
purchasability boundary and does not copy media-selection rules. Legacy paid selfie-result members
that still authorize originals are not purchasable through this cart.

Cart membership selects a future right to download one original but grants no entitlement, media
access, download URL, storage key, or signed capability. Checkout, order, payment, packages,
entitlement, and purchased-original delivery remain separate decisions.

Expire each event cart 30 days after its last actual user add, remove, or clear mutation. Reads,
price changes, failed or duplicate commands, automatic pruning, and mutations in another event do
not extend it. Every request enforces logical expiry and current eligibility before returning
state. A bounded daily cleanup physically removes expired rows; cleanup delay cannot revive them.

Gate all cart entry points, side effects, cookies, and rendered actions with a separate
database-backed `paid-photo-cart` runtime gate conforming to ADR 0028. Missing and `off` fail
closed, `staff` permits acceptance, and `on` permits anonymous use. The gate does not replace price,
publication, watermark, event, photo, CSRF, or token checks.

This decision conforms to ADRs 0001, 0002, 0028, and 0029. It does not supersede ADRs 0019–0021 or
0029, change legacy paid-result policy, or authorize original delivery.

## Consequences

### Positive

- Anonymous customers receive durable browser-local selection without accounts or personal
  contact data.
- Cookie size stays constant while PostgreSQL preserves event and photo integrity.
- Current price and current photo eligibility are authoritative on every response.
- Cart selection cannot bypass protected-media or future purchase-entitlement checks.
- Independent event carts avoid prematurely defining cross-event seller, package, payment, and
  fulfillment behavior.
- One Commerce price-calculation boundary can evolve when fixed-price packages are approved.

### Negative

- Losing or clearing the cookie irrecoverably loses access to the selection.
- A stolen live cookie can read and edit its carts until expiry, although it cannot access
  originals.
- Price changes immediately change existing cart totals; the cart is not a quote.
- PostgreSQL and daily cleanup retain abandoned browser-linked selection for up to the bounded
  retention period.
- Open tabs do not synchronize in real time and may show stale state until another action or reload.
- The first purchase slice remains incomplete until later order, payment, entitlement, and delivery
  decisions are implemented.

### Follow-up

- Define fixed-price package composition and application rules in a separate approved design.
- Define idempotent order, payment, immutable price evidence, entitlement, refund, and purchased
  delivery boundaries before accepting money or releasing originals.
- Review and update the personal-data policy's necessary-cookie disclosure before public cart
  activation.
- Remove the temporary cart runtime gate after public rollout is stable or the feature is rejected.

## Validation and rollback

Validate browser-token isolation, event separation, raw-token non-persistence and non-disclosure,
CSRF protection, exact price arithmetic, unique positions, authoritative paid-photo eligibility,
logical expiry, bounded cleanup, concurrency, cache privacy, and denial of every original/download
path. Verify `off`, `staff`, and `on` behavior with the ADR 0029 paid-gallery gate and one real
watermarked photo before public activation.

Rollback sets `paid-photo-cart=off` first, which closes entry points and side effects without
deleting unexpired state. Application rollback must retain schema compatibility while cart rows
exist; a later separately reviewed migration may remove abandoned cart data after the retention
period. Reconsider this decision if accounts become mandatory, cross-device recovery becomes a
product requirement, browser-token abuse becomes material, or package/order semantics require a
different cart authority.

## References

- [Anonymous paid-photo cart design](../superpowers/specs/2026-08-20-anonymous-paid-photo-cart-design.md)
- [Architecture: target MVP](../architecture.md#target-mvp-architecture--proposed)
- [Architecture: purchase and download](../architecture.md#purchase-and-download)
- [Architecture: security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- [ADR 0001](0001-django-modular-monolith.md)
- [ADR 0002](0002-postgresql-system-of-record.md)
- [ADR 0028](0028-operate-one-canonical-deployment.md)
- [ADR 0029](0029-use-watermarked-previews-for-paid-photos.md)
