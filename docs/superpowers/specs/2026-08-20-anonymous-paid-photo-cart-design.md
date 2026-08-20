# Anonymous Paid-Photo Cart Design

## Status

Approved in conversation and repository review by the project maintainer on 2026-08-20.

- Related architecture: [`docs/architecture.md`](../../architecture.md), target MVP Commerce,
  purchase and download, and security, privacy, and legal boundaries.
- Related product jobs:
  [`PJ-005 — Visitor — Browse an event gallery`](../../product-jobs.md#pj-005--visitor--browse-an-event-gallery),
  [`PJ-008 — Customer — Find photos by face`](../../product-jobs.md#pj-008--customer--find-photos-by-face),
  [`PJ-010 — Customer — Purchase selected photos`](../../product-jobs.md#pj-010--customer--purchase-selected-photos),
  and
  [`PJ-011 — Customer — Download purchased photos`](../../product-jobs.md#pj-011--customer--download-purchased-photos).
- Related specifications:
  [`2026-08-01-one-click-original-download-design.md`](2026-08-01-one-click-original-download-design.md)
  and the accepted sibling
  [`2026-08-20-paid-watermarked-previews-design.md`](2026-08-20-paid-watermarked-previews-design.md),
  which must merge before this feature is implemented.
- Related ADRs:
  [ADR 0001](../../adr/0001-django-modular-monolith.md),
  [ADR 0002](../../adr/0002-postgresql-system-of-record.md),
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md),
  [ADR 0020](../../adr/0020-use-signed-direct-object-storage-media-delivery.md),
  [ADR 0021](../../adr/0021-allow-original-download-for-authorized-photos.md),
  [ADR 0022](../../adr/0022-use-numbered-gallery-pages.md),
  [ADR 0024](../../adr/0024-use-gallery-face-as-search-query.md), and accepted sibling
  [ADR 0029](../../adr/0029-use-watermarked-previews-for-paid-photos.md), and accepted
  [ADR 0030](../../adr/0030-use-anonymous-server-side-event-carts.md).
- ADR impact: **Conforms to accepted ADR 0030.** ADRs 0001 and 0002 already place Commerce
  product rules and transactional state in Django and PostgreSQL. ADR 0029 supplies the protected
  paid-photo presentation prerequisite and continues to deny original delivery. ADR 0030 defines
  the durable anonymous browser-token authority, server-side cart persistence and retention, event
  pricing invariant, and explicit separation between selection and purchase entitlement. This
  specification does not supersede ADR 0029 or authorize paid-original delivery.

The sibling ADR 0029 and specification are accepted in the parallel paid-watermarked-preview
worktree but are not present in this branch's current `origin/main`. Their merge is a prerequisite;
the relative links above become repository-local after that merge.

## Outcome

A customer browsing a published paid event can add any currently purchasable watermarked photo to
an anonymous event-specific cart, see the current per-photo price and total, remove individual
photos, and clear the selection. The cart survives navigation and browser restarts for 30 days
after its last actual user mutation.

The cart is identified only by an opaque cookie in one browser profile. It is not associated with
an account, contact, Django login session, selfie-search token, or cross-device identity. Different
browsers, devices, ordinary and private browser profiles therefore have different carts. Clearing
the cookie loses access to every cart associated with that browser token.

This increment ends at a correct editable selection and total. It creates no order, accepts no
payment, grants no entitlement, and provides no original download. Packages with fixed prices and
the purchase path are explicitly separate follow-up increments.

## Scope

### Included

- One positive event-level per-photo price for every paid event, denominated only in RUB.
- A data migration assigning `300.00 RUB` to every existing paid event and no price to free events.
- One opaque browser cookie backed by PostgreSQL carts separated by event.
- One unique cart position for one eligible paid photo; quantities are not supported.
- Add and remove controls in the normal paid gallery, its lightbox, and watermarked paid
  selfie-search results.
- Current cart state on every rendered paid-photo card, including cards loaded on another numbered
  gallery or result page.
- An event-specific cart link and count on paid gallery and paid selfie-result surfaces.
- A server-rendered cart page with watermarked previews, prices, count, total, removal, clear, and
  return-to-event actions.
- Progressive enhancement for mutation without page reload, with server-rendered POST forms as the
  complete no-JavaScript path.
- Thirty-day mutation-based expiry, fail-closed request-time expiry, and daily physical cleanup.
- Automatic pruning of positions that are no longer eligible for paid public presentation.
- A separate runtime release gate, absent or off by default.
- Critical-path data, authorization, cookie, UI, JavaScript, accessibility, retention, and visual
  validation.

### Excluded

- Checkout, order creation, payment provider integration, callbacks, refunds, or cancellations.
- Purchase entitlement, authenticated ownership, purchased exports, or original download.
- Email, telephone, customer account, login recovery, cross-browser merge, cart sharing, transfer,
  or restoration after cookie loss.
- Multiple copies of one photo, print products, formats, licenses, delivery options, or shipping.
- Package products, bundle selection, volume discounts, promotions, coupons, or tax calculation.
- Per-photo pricing, multiple currencies, exchange rates, or speculative currency configuration.
- Price snapshots in cart positions; immutable price evidence belongs to a future order item.
- One cart containing photos from several events.
- Real-time synchronization between open tabs, WebSockets, polling, or browser broadcast channels.
- Custom commerce analytics events.
- Backfill or reinterpretation of legacy paid photos and saved selfie results that ADR 0029 leaves
  under their existing media policy.
- Changes to watermark generation, public-media selection, face search, ranking, or result
  membership.
- A disabled or placeholder checkout button.

## Selected Design

Add a cohesive Commerce module inside the existing Django modular monolith. Catalog continues to
own `Event`, `Photo`, publication, price, and public photo eligibility. Commerce owns anonymous
cart identity, persisted selection, price calculation, mutation, expiry, and presentation. It
consumes catalog's approved purchasable-photo boundary; it does not independently select media or
authorize originals.

```text
opaque HttpOnly cookie
        |
        v
Commerce cart service ------ PostgreSQL Cart + CartItem
        |                              |
        |                              `- event-separated selection
        |
        +---- Catalog purchasable-photo eligibility
        |          `- published paid event
        |          `- accepted watermarked public presentation
        |
        +---- current Event price
        |
        `---- GalleryPhoto presentation
                   `- stable photo_id
                   `- watermarked semantic media
                   `- download_url = None
```

The selected approach keeps the cookie constant-size, makes PostgreSQL authoritative for
transactional selection, and leaves future package pricing able to replace one Commerce price
calculation boundary without adding package placeholders now.

## Domain and Data Model

### Event price

`Event.price_per_photo_kopecks` is one nullable integer amount representing the current price of
the right to download one photo, in kopecks. The user-facing admin field is labelled
`Цена фотографии, ₽` and accepts a positive RUB amount with at most two decimal places.

The database and model invariant is exact:

| Event access type | Per-photo price |
| --- | --- |
| `free` | `NULL` |
| `paid` | positive integer number of kopecks |

The currency is globally RUB and has no configurable field in this increment. Existing paid events
receive `30000` kopecks through an explicit data migration; free rows remain `NULL`. An operator may
change a paid event's price before or after publication. Existing carts immediately use the current
price on their next response; a price change does not mutate the cart or extend its expiry.

The sibling paid-watermarked-preview feature makes `Event.access_type` immutable after the first
photo exists. Price remains independently editable. Changing an event between free and paid before
its first photo must still satisfy the price invariant atomically.

### Cart

A Commerce `Cart` belongs to exactly one event and one anonymous browser token digest. Its durable
fields are:

- `browser_token_sha256`, the SHA-256 digest of the opaque browser token;
- the paid `Event`;
- `expires_at`, advanced only by an actual customer add, remove, or clear mutation; and
- `created_at`, used with expiry for deterministic retention.

The raw browser token is never stored. The `(browser_token_sha256, event)` pair is unique, so one
browser token can own several independent event carts but never two carts for the same event.
There is no separate browser-profile, customer, visitor, or session entity.

A cart is not an order, reservation, price quote, ownership record, or entitlement. It stores no
currency, subtotal, total, price snapshot, promotion, payment, contact, or download capability.

### Cart item

A Commerce `CartItem` relates one cart to one photo and records `added_at`. The `(cart, photo)` pair
is unique. A photo can appear at most once in one cart, so no quantity field exists. Items are
shown by `added_at` ascending, with photo ID as the deterministic tie-breaker.

The photo must belong to the cart event. Database relations and the Commerce service preserve that
event boundary; every mutation additionally re-evaluates current public purchasability. Deleting a
photo removes its cart positions. A cart whose last item is removed is deleted rather than retained
as an empty record.

## Anonymous Browser Identity

The first successful add operation for a browser without a valid token generates at least 256 bits
of cryptographically secure randomness and returns it in one cookie. Merely visiting a gallery,
selfie result, or empty cart page creates neither a cookie nor a database row.

The `findme_cart` cookie contract is:

- one stable project-owned name for all event carts;
- `Secure`;
- `HttpOnly`;
- `SameSite=Lax`;
- `Path=/`;
- a 30-day maximum age refreshed only after an actual add, remove, or clear mutation; and
- no JavaScript access.

An absent, expired, or malformed token is treated as no cart, without disclosing whether a digest
exists. A new token is issued only if a subsequent eligible add succeeds. Cookie deletion,
browser-profile deletion, another browser, another device, and private browsing all intentionally
produce an unrelated selection.

If deletion of the final item also removes the final unexpired event cart for that digest, the
response deletes the browser cookie. If other unexpired event carts remain, the cookie remains. No
cart token appears in a URL, HTML, form field, JSON body, analytics identifier, or application log.

## Cart Eligibility and Authority

Presentation is never authority. Every add request must prove all of the following from current
database state:

1. the runtime cart gate permits the current caller;
2. the event is published and paid;
3. the event has a valid positive current price;
4. the photo belongs to that event;
5. the photo uses ADR 0029's watermarked paid-media policy; and
6. the photo has the mutually consistent accepted state, successful accepted attempt, and
   published watermarked derivative required by the paid public-presentation query.

The Commerce module must consume the catalog's public purchasability query or service rather than
copying watermark-state joins or treating `download_url is None` as sufficient. Unknown events,
foreign photos, draft events, free photos, legacy paid policies, unaccepted or missing watermark
evidence, and disabled gates return a sanitized 404 before creating a token, cart, or item.

Cart membership never makes a photo eligible for media or download. Media remains governed by
`PublicMediaResolver` and ADR 0029. Knowing a cart token, cart identifier, event slug, or photo ID
cannot authorize an original, clean preview, storage object, or signed URL.

Legacy paid saved-result members that retain original presentation or download under their
persisted pre-ADR-0029 policy show no cart action and cannot be added. Offering a paid selection for
bytes that the same bearer result already authorizes would be contradictory. Only watermarked
paid-result cards with `download_url=None` participate.

## Pricing

The current price calculation is deliberately one rule:

```text
unit_price = event.price_per_photo_kopecks
item_count = count(current eligible unique items)
total = unit_price * item_count
```

Every cart response derives unit price and total from the current event price. It never reads a
stored cart-item price. Integer arithmetic is exact; floating point is forbidden. Display uses RUB
formatting, including `300 ₽` for the migrated price.

Future fixed-price packages will replace or extend this one Commerce calculation interface in a
separate approved design. This increment creates no package table, product abstraction, pricing
strategy registry, reserved discriminator, or compatibility field.

## User Experience

### Paid gallery cards and lightbox

Every eligible watermarked photo shows its current price and one compact icon-only cart action on:

- the normal paid event gallery card;
- the corresponding lightbox slide; and
- a watermarked paid selfie-result card and lightbox slide.

The two states are:

| Cart state | Visual action | Accessible name |
| --- | --- | --- |
| Absent | cart with plus | `Добавить в корзину` |
| Present | cart with check, visibly active | `Удалить из корзины` |

State must differ by icon shape and accessible name, not color alone. Repeating the customer action
removes the item and returns the absent state. All visible controls for the same `photo_id` on the
current page update together after the server confirms the mutation.

The current event's compact cart link appears in the paid gallery and paid selfie-result page
header. It shows the number of eligible positions in that event cart and links to
`/events/<slug>/cart/`. It is not a site-global cross-event cart and does not sum other event carts.
Free events and unrelated pages show no cart action or cart link.

### Cart page

The server-rendered event cart page contains:

- event name;
- every current eligible position in addition order;
- the watermarked thumbnail selected by the existing presentation contract;
- the current unit price on every position;
- one remove action per position;
- total item count and current total;
- `Продолжить выбор`, linking to the event page; and
- `Очистить корзину`.

It contains no filename, quantity, package, discount, promotion, checkout, payment, disabled future
button, original URL, or download action. The empty state is exactly:

> В корзине пока нет фотографий

Clearing requires confirmation with the exact prompt:

> Удалить все фотографии из корзины?

Removing one item requires no confirmation. After the final removal the customer remains on the
empty cart page. If positions became ineligible since the previous view, they are removed and the
page shows:

> Некоторые фотографии больше недоступны и удалены из корзины

There is no arbitrary maximum item count and no product-level rejection based only on cart size.
The first release does not introduce a separate cart pagination contract.

### Multiple tabs

PostgreSQL is authoritative, but open pages do not synchronize in real time. A different tab
updates its icon and count on its next mutation, navigation, or reload. WebSockets, polling,
`BroadcastChannel`, and storage events are excluded.

## Mutation Interface and Progressive Enhancement

Add, remove, and clear are CSRF-protected POST commands. Add and remove communicate an explicit
desired state rather than a blind server-side toggle, making retries and duplicate delivery
idempotent. The service serializes the event cart mutation, relies on uniqueness for duplicate-add
safety, and returns the authoritative photo state, event item count, unit price, and total.

Without JavaScript, each action is an ordinary form submission followed by a redirect to a
server-approved local return path; the event page is the fallback. A submitted return value may
never become an open redirect. With JavaScript, the same command is enhanced in place using the
existing CSRF and same-origin request conventions.

During an enhanced request the initiating button is disabled. The browser changes icons, labels,
count, and totals only after a successful server response. On failure it retains the previous state
and shows the exact message:

> Не удалось обновить корзину. Попробуйте ещё раз.

Mutation responses are authoritative snapshots for the current event. A response cannot update or
expose another event cart. Duplicate add or remove commands are harmless no-ops and do not extend
expiry because no actual cart change occurred.

## Expiry, Pruning, and Cleanup

An actual customer add, remove, or clear mutation sets the affected cart's `expires_at` to exactly
30 days after that mutation and refreshes the cookie maximum age. Reads, price changes, failed
commands, duplicate no-ops, automatic pruning, and activity in another event cart do not extend
that cart's expiry.

Every read and mutation treats `expires_at <= now` as absent before returning cart content. Request
correctness therefore does not depend on background cleanup. An expired cart cannot be revived; a
later eligible add creates a new cart under a current valid token or a new token if the cookie has
expired.

When a still-published cart is read or changed, positions that no longer satisfy paid public
purchasability are deleted before count and total are returned. Automatic pruning does not extend
expiry. If pruning removes the last position, the empty cart is deleted. An unpublished or removed
event remains inaccessible and its cart expires normally; cart state never reopens the event.

A bounded management command physically deletes expired cart rows and their items. The command is
safe to repeat and is invoked by the established daily host-cron pattern. Cleanup delay may retain
an expired database row briefly, but cannot make it readable. This feature does not introduce an
application scheduler, task broker, or Object Storage lifecycle.

## Administration

Django Admin exposes the event price in a dedicated Commerce fieldset near access and publication.
Validation attaches clear errors to access type or price when the pair violates the invariant.
Operators may edit a paid price at any time, and a successful edit affects every existing cart on
its next response.

The admin does not expose cart tokens or raw digest search. Ordinary cart rows and items do not
need an operator workflow in this increment. Support inspection, manual cart mutation, customer
lookup, and cart recovery are excluded.

## Feature Gate and Activation Boundary

The stable runtime release key is `paid-photo-cart`. A missing row and `off` fail closed. `staff`
permits staff-only end-to-end verification; `on` permits anonymous customers. Direct cart GET and
POST routes, cart context, cookies, and actions all obey the gate.

The cart gate is independent from the sibling `paid-watermarked-previews` gate, but a photo is
purchasable only while both the cart gate and catalog's current watermarked presentation boundary
permit that caller. Turning the cart gate off hides UI and returns sanitized denial from direct
cart routes without deleting unexpired carts. Re-enabling can restore a still-unexpired cart after
the same authority checks and pruning.

Implementation may merge with `paid-photo-cart` absent/off. Public activation is blocked until:

- ADR 0029 and the paid-watermarked-preview implementation have merged;
- approved watermark assets and the compatible worker are active;
- one staff-only normal-gallery and paid selfie-result cart path passes with protected media;
- the personal-data policy's description of the necessary cart cookie, purpose, and 30-day period
  has received the project's required legal review; and
- this specification's required new commerce ADR is accepted.

This specification authorizes no deployment, feature-flag mutation, cron installation, legal-copy
change, or cloud operation by itself.

## Security, Privacy, and Caching

The cart is anonymous, but the browser token and selected photos are still browser-linked private
state. The token is a narrow bearer capability for reading and editing cart contents only. It is
not reused for analytics, customer correlation, selfie-result history, authentication, or media
authorization.

All mutations require same-origin CSRF protection. Token comparison uses its server-side digest.
Responses containing cart state must be private and must not be served from a shared cache; cart
pages and mutation responses use `private, no-store`, and personalized gallery responses vary on
the cart cookie or use an equivalently fail-closed private-cache policy. Selfie bearer pages retain
their stronger existing no-store, no-referrer, and analytics-suppression boundary.

Application logs, metrics labels, error reports, and analytics must not contain the raw token,
cookie header, selected photo list, or selfie-result bearer. Sanitized denials do not distinguish a
missing cart, invalid token, foreign event, ineligible photo, disabled gate, or expired cart in a
way that reveals private state.

The existing cookie notice remains unchanged and does not gate the necessary cart cookie. Its `OK`
button remains an informational acknowledgement stored in `localStorage`; it is not consent state
for cart operation. The cart stores no contact details and has no customer identity recovery path.

## Failure and Consistency Semantics

- Concurrent adds of the same photo create one position.
- Explicit add and remove commands are idempotent; retries cannot invert the requested state.
- Mutations lock or otherwise serialize the affected event cart so count, total, expiry, and
  deletion remain mutually consistent.
- A price change racing a cart response may yield either complete old-price or complete new-price
  calculation for that response, never mixed unit prices within one event cart. The next response
  uses the current committed price.
- An event or photo becoming ineligible races fail closed: a mutation must not commit a position
  that fails its authoritative eligibility check.
- A missing or malformed cookie creates no state until an eligible add succeeds.
- Cookie write failure can lose future access but cannot expose an original or another browser's
  cart.
- A database or validation failure leaves the previous cart state authoritative and produces the
  agreed retry message for enhanced UI.
- A cleanup failure may delay physical deletion but never extends logical access after expiry.
- A missing watermarked object follows the catalog media failure contract and cannot fall back to
  a clean preview or original.
- Free gallery, free selfie-result, and legacy paid-result download behavior remain governed by
  their current explicit media policies; cart code does not reinterpret them.

## Alternatives Considered

### Store cart items in the cookie

Rejected because the cookie would grow with the selection, expose product state to the client, make
server-side eligibility and pruning harder, and leave no transactional boundary for the next order
increment.

### Use the Django login session

Rejected because the requested identity is one anonymous browser cookie, independent of accounts
and login. Reusing the authenticated session would couple unrelated photographer/admin identity to
customer selection and make session expiry the cart contract.

### Create one cookie per event

Rejected because cookie count would grow with browsed events. One opaque token plus a unique
server-side `(token digest, event)` cart keeps browser storage constant and preserves event
separation.

### Mix events in one cart

Rejected because events may later differ in pricing, sellers, packages, payment, and fulfillment.
Independent event carts avoid deciding those future aggregation rules now.

### Store a price snapshot in CartItem

Rejected because this increment is an editable selection, not a quote or order. Current prices are
recalculated on every response; immutable commercial evidence belongs to future order items.

### Model packages now

Rejected even though fixed-price packages are an expected next step. Their composition and
application rules are not approved. One Commerce calculation boundary is sufficient preparation;
placeholder models and discriminators would be speculative compatibility work.

### Treat cart membership as download authority

Rejected because adding an item is neither purchase nor payment. It would expose paid originals to
any anonymous browser and contradict ADR 0029.

### Update the interface optimistically

Rejected because a failed eligibility or database mutation would display a selection that the
server never accepted. The UI waits for the authoritative response.

## Acceptance Criteria

1. Every free event has no per-photo price, while every paid event has one positive RUB price in
   integer kopecks; existing paid rows receive exactly `30000` kopecks.
2. Admin validation prevents every invalid access-type/price pair and permits changing a valid paid
   price after publication.
3. The current cart total always equals current event price multiplied by current eligible unique
   item count, with exact integer arithmetic and no cart-item price snapshot.
4. One valid browser token can address separate carts for several events; different tokens cannot
   read or mutate each other's carts.
5. No visit creates a token or row. The first eligible add creates both; malformed or absent tokens
   reveal nothing and create nothing on rejected requests.
6. The raw token appears only in a `Secure`, `HttpOnly`, `SameSite=Lax`, path-wide cookie with a
   30-day maximum age and never appears in storage, HTML, URLs, logs, or analytics.
7. A cart contains at most one position per photo, has no quantity, and lists positions by addition
   order with deterministic photo-ID ties.
8. Add, remove, and clear are CSRF-protected, explicit-state, idempotent POST operations with a
   complete server-rendered redirect path and enhanced same-origin behavior.
9. Only a currently published, paid, correctly priced, ADR-0029-policy photo with consistent
   accepted watermark evidence can be added; all cross-event, free, draft, legacy, missing,
   unaccepted, and disabled-gate cases fail before state creation.
10. Cart membership grants no media or download access and causes no original, clean preview,
    object key, or signed URL to be selected or exposed.
11. Eligible paid normal-gallery and paid selfie-result cards and lightboxes show the current price
    and exact absent/present icon states; all same-photo controls on a page update after success.
12. Legacy paid selfie-result members that retain an original capability show no cart action and
    cannot be added.
13. The event-specific cart link shows only the current event count and appears only on eligible
    paid gallery and paid selfie-result surfaces.
14. The cart page shows the approved fields, exact empty/pruning/error/confirmation copy, current
    total, removal, clear, and return-to-event action, with no checkout, download, package, quantity,
    filename, or disabled placeholder control.
15. A failed enhanced mutation preserves the previous visual state and presents `Не удалось
    обновить корзину. Попробуйте ещё раз.`; a successful response updates state only from the
    server's authoritative result.
16. Open tabs require a later action, navigation, or reload to observe one another; no real-time
    synchronization mechanism exists.
17. Only actual user mutations advance one event cart's exact 30-day expiry. Reads, price changes,
    no-ops, pruning, failures, and other-event activity do not.
18. Expired carts are inaccessible at request time even before cleanup; daily cleanup is bounded,
    repeatable, and removes their rows without creating a new scheduler.
19. Ineligible positions are pruned before display and totals without extending expiry; the agreed
    message appears, and a last-item prune deletes the empty cart.
20. Removing the last item deletes that event cart; the cookie is deleted only when no carts remain
    for its digest.
21. The absent/off `paid-photo-cart` gate closes all direct and rendered cart behavior. Staff mode
    supports isolated verification, and public activation waits for the accepted ADR, protected
    paid gallery, approved legal-cookie disclosure, and real staff smoke.
22. Cart-bearing responses cannot leak between browsers through shared caching, and existing
    selfie-result no-store, no-referrer, and analytics suppression remain intact.
23. Free-event gallery and download behavior, paid watermark selection, public-media authorization,
    selfie ranking and membership, numbered pagination, filters, and face controls remain unchanged
    outside the added cart presentation.
24. Focused model, migration, service, concurrency, authorization, expiry, cleanup, cookie, template,
    JavaScript, accessibility, and desktop/mobile visual evidence covers the complete selection
    critical path and realistic failure paths.

## Open Questions

None. Package rules, checkout, orders, payment, entitlement, and purchased-original delivery are
deliberately separate designs, not unresolved questions in this specification.
