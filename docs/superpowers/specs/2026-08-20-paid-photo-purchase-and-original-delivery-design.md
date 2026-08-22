# Paid Photo Purchase and Original Delivery Design

## Status

Approved by the maintainer on 2026-08-20. ADR 0031 is accepted and implementation planning may
proceed.

- Related architecture: [`docs/architecture.md`](../../architecture.md), target MVP Commerce,
  purchase and download, security, privacy and legal boundaries, evolution stages, and open
  decisions.
- Related product jobs:
  [`PJ-005 — Visitor — Browse an event gallery`](../../product-jobs.md#pj-005--visitor--browse-an-event-gallery),
  [`PJ-008 — Customer — Find photos by face`](../../product-jobs.md#pj-008--customer--find-photos-by-face),
  [`PJ-010 — Customer — Purchase selected photos`](../../product-jobs.md#pj-010--customer--purchase-selected-photos),
  and
  [`PJ-011 — Customer — Download purchased photos`](../../product-jobs.md#pj-011--customer--download-purchased-photos).
- Related specifications:
  [`2026-08-01-one-click-original-download-design.md`](2026-08-01-one-click-original-download-design.md),
  [`2026-08-20-paid-watermarked-previews-design.md`](2026-08-20-paid-watermarked-previews-design.md),
  and
  [`2026-08-20-anonymous-paid-photo-cart-design.md`](2026-08-20-anonymous-paid-photo-cart-design.md).
- Related ADRs:
  [ADR 0001](../../adr/0001-django-modular-monolith.md),
  [ADR 0002](../../adr/0002-postgresql-system-of-record.md),
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md),
  [ADR 0020](../../adr/0020-use-signed-direct-object-storage-media-delivery.md),
  [ADR 0021](../../adr/0021-allow-original-download-for-authorized-photos.md),
  [ADR 0028](../../adr/0028-operate-one-canonical-deployment.md),
  [ADR 0029](../../adr/0029-use-watermarked-previews-for-paid-photos.md), and
  [ADR 0030](../../adr/0030-use-anonymous-server-side-event-carts.md), with
  accepted [ADR 0031](../../adr/0031-use-orders-and-adapters-for-paid-original-delivery.md) for this
  increment.
- ADR impact: **Conforms to accepted ADR 0031.** ADR 0031 authorizes
  anonymous email-addressed orders, a narrow replaceable payment-gateway boundary, trusted manual
  payment confirmation, paid-OrderItem entitlement, permanent revocable order links, asynchronous
  customer email, and purchased-original delivery. It must supersede ADR 0029 only for original
  attachment authorization after a qualifying order becomes `paid`; ADR 0029's watermarked public
  presentation and pre-purchase original denial remain accepted. The design conforms to ADRs 0001
  and 0002 by keeping Commerce rules and transactional evidence in Django/PostgreSQL, ADR 0020 by
  retaining Django authorization and short-lived exact-object signed delivery, ADR 0028 by using a
  fail-closed database runtime gate, and ADR 0030 by keeping cart selection distinct from payment
  and entitlement. It does not reuse ADR 0030's cart token as media authority.

ADR 0031 was explicitly accepted before `$write-plan` began.

The paid-watermarked-preview change must merge first, followed by the anonymous-cart change. This
purchase increment must be planned and implemented from the resulting current `main`; it must not
merge partial copies of either parallel branch.

## Outcome

A customer with an anonymous event-specific cart can review an exact checkout, provide an email
address, pay one immutable RUB order through a hosted bank-gateway flow, and receive permanent
access to the unmodified originals of exactly the purchased photos. Access works immediately in
the checkout browser after authoritative payment confirmation and from permanent secret links
sent by email or supplied by a trusted administrator.

The first implementation is deliberately dark deployable. It delivers the complete order,
payment-state, entitlement, delivery, email-outbox, administrative, and test-gateway behavior, but
it accepts no real money and sends no real customer email. A real bank adapter, real email adapter,
fiscal attributes, legally reviewed customer documents, and explicit operational activation are
separate prerequisites for public use.

## Scope

### Included

- FindMe Photo as the single seller and payment recipient for one event-specific order.
- An inline checkout form in the accepted anonymous paid-photo cart.
- One required email input without registration or pre-payment email verification.
- One immutable RUB Order and one immutable OrderItem price snapshot for every selected photo.
- One Order containing photos from exactly one event, with no quantity and no duplicate position.
- A narrow `PaymentGateway` protocol and a local/staff-only deterministic test adapter.
- Hosted payment confirmation, authenticated normalized notifications, explicit status fetch, and
  one active PaymentAttempt per Order.
- Idempotent automatic payment transitions and a trusted Django Admin action for manual payment
  confirmation after the operator checks the bank independently.
- Permanent purchased-original authorization derived from a paid OrderItem.
- A customer order page with one independent original-download action per purchased photo.
- Permanent revocable secret order links, immediate same-browser access, administrative link
  generation, and link delivery through email or another support channel.
- A mutable delivery email alongside the immutable checkout email and audited administrative
  correction.
- A narrow `EmailSender` protocol, durable email jobs, bounded retries, manual resend, and a
  lightweight Commerce worker.
- Durable Commerce attention records, Django Admin visibility, responsible-staff email, structured
  events, bounded reminders, and explicit resolution.
- Protection of paid Event and Photo rows from deletion while allowing normal unpublication and
  hiding from public galleries.
- Download-grant audit without storing access secrets, signed URLs, IP addresses, User-Agent, or
  request headers.
- A separate database runtime gate, absent or off by default.
- Critical-path domain, concurrency, idempotency, authorization, privacy, admin, email, worker,
  JavaScript, accessibility, and desktop/mobile visual validation.

### Excluded

- A real bank-gateway adapter or assumptions about an unavailable bank protocol.
- A real SMTP or email-API adapter, sender-domain setup, DNS records, or credentials.
- Public payment activation, cloud mutation, deployed worker activation, or feature-flag mutation.
- Provider-independent abstractions for refunds, capture, saved cards, payouts, recurring payment,
  installments, split settlement, or multiple currencies.
- Automated, partial, or customer-initiated refunds and a refund state machine. Mandatory legal
  claims remain an external operational process until separately designed.
- Fixed-price packages, bundles, promotions, coupons, discounts, taxes calculated by FindMe Photo,
  per-photo price overrides, or cross-event checkout.
- Accounts, passwords, email OTP, magic-link authentication, customer profiles, order-history
  lookup by email, or global purchase identity.
- Duplicate-purchase prevention or warnings. A photo may be purchased again, including by the same
  email address or browser.
- ZIP generation, batch download, alternate original formats, editing, shipping, prints, or
  licenses beyond personal non-commercial use.
- Customer-visible payment-method selection; the hosted bank page owns cards, SBP, and any other
  enabled methods.
- Storing or exposing raw Object Storage keys, permanent signed storage URLs, gateway secrets, or
  full order access secrets.
- Reinterpreting legacy paid photos or saved selfie results that do not use ADR 0029's
  watermarked paid-media policy.
- Automatic deletion or anonymization of checkout or delivery email. The approved product intent
  is indefinite retention for permanent fulfillment, subject to separately reviewed mandatory
  legal exceptions.
- A general task broker, general notification framework, or reuse of the photo-processing state
  machine for Commerce.

## Selected Design

Add purchase and fulfillment capabilities to the existing Commerce module inside the Django
modular monolith. PostgreSQL owns every order, price snapshot, payment attempt, transition,
access grant, delivery job, attention record, and download-grant audit. The bank and email systems
are replaceable I/O adapters; neither owns product state or receives authority to select photos.

```text
anonymous event Cart
        |
        | checkout locks and revalidates selection
        v
immutable Order + OrderItems -----> PaymentGateway adapter -----> hosted bank page
        |                                  |                             |
        |                                  `---- verified status <-------'
        |
        +---- paid OrderItem entitlement ---- Django authorization
        |                                         |
        |                                         `-> short-lived signed original GET
        |
        +---- OrderAccessGrant -----> customer order page
        |
        +---- EmailDelivery --------> Commerce worker ----> EmailSender adapter
        |
        `---- CommerceAttention ----> Admin + responsible-staff email + safe log
```

The adapters are narrow deep-module seams rather than a generic provider framework. Commerce
speaks only in exact RUB amounts, immutable order references, normalized payment states, and
provider-neutral email messages. Concrete adapters translate those values to one provider's API
and normalize its failures without leaking SDK types into product services.

## Seller, Product, and License

FindMe Photo is the sole seller and recipient for this increment. Photographer onboarding,
commissions, split payments, and payouts do not exist in the product flow.

Each OrderItem purchases one unmodified original photo for personal non-commercial use. The item
has quantity one. The customer purchases access to the bytes already retained as the Photo's
private original; the system does not generate a separate purchased export.

The product-intended fulfillment milestone occurs only after the Order is `paid` and either an
access email is successfully sent or the customer first opens an authorized Order context. The
system records those underlying timestamps separately; this milestone neither creates nor revokes
entitlement. Customer-facing claims that the service is rendered and the digital product handed
over at that milestone require the same legal review as the offer and consumer-return wording.

The order belongs to exactly one Event. Currency is the single literal `RUB`. Prices are stored and
calculated only as integer kopecks. The checkout and receipt-facing product description must not
claim transfer of copyright or a commercial-use license.

The exact public offer, consumer-return wording, personal-data basis, fiscal classification, tax
rate, and receipt attributes require legal/accounting review before real activation. The absence
of a refund workflow must not be rendered as an unreviewed promise that statutory claims are
impossible.

## Domain Model

### Order

An Order is the immutable commercial snapshot of one checkout. Its durable contract includes:

- an internal immutable identifier;
- a unique non-sequential public number formatted as `FM-XXXXXXXX`;
- the protected Event;
- the originating anonymous purchase-browser token digest;
- `checkout_email`, exactly normalized from the approved checkout submission and never changed;
- `delivery_email`, initially equal to `checkout_email` and later editable only by a trusted
  administrator;
- the exact integer `total_kopecks` and literal `RUB` currency;
- status, creation time, optional paid time, optional first successful customer-access time, and
  administrative audit history.

The public number uses unambiguous uppercase letters and digits and contains enough randomness to
make guessing impractical. It is a support reference, not an access capability.

Order states are:

| State | Meaning | Customer payment | Original entitlement |
| --- | --- | --- | --- |
| `pending` | Current immutable snapshot; attempts may be created or retried | Allowed | No |
| `superseded` | The originating cart changed after a terminal attempt | Denied | No |
| `paid` | Authoritative automatic or trusted manual confirmation completed | Denied; terminal | Yes |
| `canceled` | A trusted administrator deliberately closed the Order | Denied; terminal | No |

Automatic or manual `paid` is allowed from `pending` or `superseded`. `canceled` and `paid` never
transition back. Expiry or failure of one PaymentAttempt does not cancel the Order. A
`superseded` Order remains eligible for a late verified or trusted manual `paid` because money may
already have arrived for that immutable snapshot.

The originating browser receives a separate opaque purchase-access cookie when the first Order is
created. Only its digest is stored on Orders. The cookie may address multiple Orders created by
that browser, grants no cart authority, and is never sent in a URL or log. It exists solely so a
successful Order remains accessible in the checkout browser when the email is delayed or fails.
The cookie is `Secure`, `HttpOnly`, `SameSite=Lax`, path-wide, and expires 30 days after its most
recent Order creation; reads and downloads do not extend it. Losing or expiring the cookie does not
change the entitlement because a valid permanent OrderAccessGrant or trusted support can restore
access.

### Order item and entitlement

Each OrderItem references one protected Photo in the Order Event and snapshots:

- the exact unit price in kopecks;
- quantity one and an equal line total;
- the stable public photo identifier needed for presentation and support.

The pair `(order, photo)` is unique. Order total equals the sum of its immutable line totals. It is
never recalculated from the current Event price after Order creation.

A separate entitlement table is intentionally absent. The entitlement predicate is exact:

```text
Order.status == paid
AND OrderItem belongs to Order
AND requested Photo == OrderItem.photo
AND presented purchase capability authorizes Order
```

Public event status, current gallery eligibility, current Event price, current cart membership,
and watermark-processing state are not rechecked for purchased delivery. They govern selection
before checkout, not fulfillment after money is accepted.

Paid OrderItems protect their Photo and Event from deletion. Normal hiding and unpublication do
not change purchased access. The Photo's immutable original identity must not be rewritten while a
paid OrderItem exists. An exceptional legal deletion or replacement requires a separate reviewed
procedure and is outside this design.

### Payment attempt and evidence

One Order may have multiple PaymentAttempts, but at most one may be nonterminal. Every attempt
records the Order's exact amount and currency, adapter key, unique application idempotency key,
provider identifier when one exists, hosted confirmation URL when applicable, provider or
application expiry, normalized state, and timestamps.

Normalized attempt outcomes are:

- `pending`, the only nonterminal outcome;
- `succeeded`;
- `canceled`;
- `expired`;
- `failed`; and
- `conflict`, when authenticated provider evidence cannot safely drive the Order transition.

Provider notifications and explicit status fetches produce append-only normalized transition
evidence. Raw authorization headers, secrets, card details, full callback bodies, and customer
access links are not retained. Repeated equivalent notifications and status fetches are
idempotent.

### Order access grant

An OrderAccessGrant belongs to one paid or potentially payable Order and has a random public
identifier, creation source, creation actor where applicable, creation time, and optional
revocation time. Its permanent customer URL contains the grant identifier plus a signature made
with a dedicated stable server secret. PostgreSQL stores no full bearer URL or raw signing secret.

Any active valid grant authorizes only its one Order. Creating a new grant does not revoke older
grants. A trusted administrator may create, copy, and revoke grants. Revocation blocks future
application authorization but cannot revoke already downloaded bytes or an already issued
short-lived Object Storage URL.

The first successful authorized Order-page or purchased-download request sets the Order's
`first_customer_access_at` once, whether authorization came from the purchase-browser capability
or an OrderAccessGrant. It records application access, not proof that an email was opened or that
Object Storage transferred the complete file.

The signing secret is a deployed secret authority. Replacing it invalidates existing signatures
and therefore requires explicit operational handling and link reissue; silent rotation is not
compatible with permanent links.

### Email delivery

An EmailDelivery is a durable, order-scoped command with message kind, current recipient snapshot,
referenced OrderAccessGrant, state, attempt count, next-attempt time, last safe failure category,
and immutable attempt history. It does not store a rendered message body, signed Object Storage
URL, bank secret, or raw access-signing secret. The worker reconstructs the customer URL from the
grant and server signing secret immediately before sending.

Delivery states are `pending`, `processing`, `retry_wait`, `succeeded`, `failed`, and `canceled`.
Changing `delivery_email` cancels unsent deliveries addressed to the old value without rewriting
successful delivery history. Resend creates a new grant and a new delivery addressed to the
current `delivery_email`.

### Commerce attention

A CommerceAttention is one durable operator problem identified by `(kind, subject)`. It records
the safe order or attempt reference, first/last observation, reminder time, resolution time,
resolution source, and an optional administrative resolution comment. Duplicate observations
update the same open record and never spam recipients with new rows.

Initial kinds are:

- confirmed payment amount or currency differs from the immutable Order;
- a late bank state contradicts trusted manual `paid`;
- a paid OrderItem's original is absent;
- customer email exhausts automatic attempts;
- a due PaymentAttempt cannot be reconciled; and
- ready Commerce work remains unprocessed beyond its operational threshold.

Domain failures create attention records transactionally or immediately after the failed external
operation. A completely stopped Commerce worker cannot reliably notify through itself; its queue
age and liveness therefore require an independent deployed monitoring check before activation.
That check alerts through the existing monitoring channel and links operators to the Commerce
Admin view. The application does not pretend a stopped worker can email about its own failure.

### Download-grant audit

Every successful application authorization that creates a purchased-original signed URL appends
one audit record containing OrderItem, timestamp, and whether authorization came through the
originating purchase-browser capability or a named OrderAccessGrant. It records issuance, not
Object Storage transfer completion.

The audit stores no complete access URL, signature, signed Object Storage URL, raw token, IP
address, User-Agent, or request headers.

## Checkout and Order Creation

The paid cart permanently shows its checkout form beside the current photo selection on desktop and
below it in normal document flow on mobile. The form shows one email input, relevant legal links,
the exact total in its submit action, and no payment-method selector. Checkout GET redirects to the
cart; checkout POST processes the form without a separate checkout page or client-side dependency.

The customer-facing checkout copy is:

- label: `Электронная почта`;
- help: `На этот адрес мы отправим ссылку для скачивания оригиналов.`;
- submit: `Оплатить <сумма>`;
- legal lead-in: `Нажимая «Оплатить», вы принимаете условия оферты и лицензии и подтверждаете,
  что ознакомились с политикой обработки персональных данных.`

The document names and final legal wording remain subject to the required legal review. The UI
uses links and the submit action rather than an extra checkbox unless that review requires a
different contract.

Checkout GET is a read. Checkout POST is a CSRF-protected command which, in one authoritative
transaction:

1. evaluates the purchase gate and loads the browser's unexpired Event cart;
2. locks the cart and re-evaluates every photo through the catalog purchasability boundary;
3. prunes ineligible items and rejects an empty selection;
4. validates and normalizes the email;
5. snapshots one OrderItem per current unique position and the exact current Event price;
6. creates or reuses the browser's opaque purchase capability;
7. creates the first OrderAccessGrant for eventual fulfillment; and
8. creates one PaymentAttempt request with its own idempotency key.

The external adapter call runs outside the database transaction. Its result is reconciled into
the still-current attempt using an idempotent service transition. A failure before the bank
returns a usable attempt leaves the immutable Order retryable and does not create entitlement.
The error page says:

> Не удалось перейти к оплате. Попробуйте ещё раз.

An active attempt locks mutation of that Event cart. The cart page links to the pending Order and
`Продолжить оплату`. A terminal unsuccessful attempt unlocks the cart. If the cart remains
unchanged, retry creates a new attempt for the same Order. The first later cart mutation marks the
old Order `superseded`; a subsequent checkout creates a new Order. The old Order remains available
to Admin and may still become `paid` through late verified evidence or trusted manual action.

Cart positions are removed only after the Order becomes `paid`. Removal affects exactly the
purchased photos still present in the originating cart; concurrently added positions remain. An
unsuccessful or abandoned payment never clears the selection.

## Payment Gateway Boundary

The provider-neutral `PaymentGateway` contract has exactly three capabilities:

1. create one hosted payment for an immutable PaymentRequest;
2. fetch the current status of one provider payment; and
3. authenticate and normalize one incoming provider notification.

The PaymentRequest contains public Order reference, exact amount and `RUB`, short product/receipt
lines, current checkout email as required for fiscalization, an application idempotency key, and a
return URL. The return URL contains only the public Order reference and relies on the originating
purchase-browser capability; it is not an OrderAccessGrant and cannot authorize an original.

The adapter returns only normalized immutable values. It owns provider authentication,
signatures, HTTP format, hosted URL, external identifier, expiry, response validation, and
sanitized provider errors. Commerce never branches on a provider SDK type or raw callback field.

The bank page owns payment-method presentation and selection. FindMe Photo does not render card,
SBP, saved-payment, or other provider-specific controls.

The first implementation includes one deterministic test adapter with `Успех`, `Отмена`, and
`Оставить в ожидании` outcomes. It is available only to authenticated active staff in local/test
execution. A deployed startup/system check rejects selecting the test adapter even when the
runtime purchase gate is off or in staff mode.

A real adapter is a separately reviewed implementation after the bank publishes its protocol. It
must define authenticated notification verification, status mapping, expiry, idempotency,
timeout/retry behavior, fiscal receipt fields, test credentials, secret delivery, and production
readiness before activation.

## Payment State and Idempotency

The browser return never changes payment state. It renders the current Order and briefly polls a
same-origin status route. The pending copy is:

> Проверяем оплату

When a verified notification or status fetch reports a matching successful payment, one atomic
transition:

1. locks Order and PaymentAttempt;
2. verifies the provider identity, exact Order amount, and `RUB` currency;
3. makes the attempt `succeeded` and the Order `paid` idempotently;
4. records `paid_at`;
5. creates the initial customer EmailDelivery;
6. removes only matching positions from the originating cart; and
7. closes any payment-conflict attention resolved by the evidence.

The resulting copy is `Заказ оплачен`. Purchased download actions appear only after the committed
transition.

An authenticated success for a `superseded` Order also makes it `paid`; real money creates the
same fulfillment obligation even if the browser has since built another cart. Paying both the old
and new Order produces two valid purchases because duplicate purchases are intentionally allowed.

If amount or currency differs, the attempt becomes `conflict`, the Order remains `pending` or
`superseded`, no entitlement or email is created, and one CommerceAttention opens. No positive
amount, browser claim, provider redirect, cart membership, or raw callback field can substitute
for exact successful evidence.

A trusted administrator may use `Подтвердить оплату вручную` from `pending` or `superseded`.
The operator is responsible for checking the bank outside FindMe Photo. The application asks for
confirmation but does not require an external reference, amount re-entry, comment, attachment, or
programmatic bank check. Standard Django Admin history records actor and time. The same atomic
paid transition, entitlement, cart cleanup, and customer email run as for automatic success.

An administrator may move `pending` to `canceled`. `canceled` cannot later become paid. A later
provider state incompatible with trusted manual `paid` never revokes entitlement automatically;
it records immutable conflicting evidence, opens CommerceAttention, and requires operator review.
Equivalent later success is an idempotent no-op linked to the existing paid Order.

One PaymentAttempt uses the provider's expiry when available and otherwise expires after 24 hours.
At expiry the Commerce worker fetches current status before applying a terminal result. Failure to
obtain a safe terminal result opens attention and does not invent success. A terminal unsuccessful
attempt unlocks the cart while the Order remains available for retry or late manual confirmation.

## Customer Access and Original Delivery

The permanent secret Order page contains:

- public Order number and date;
- Event name;
- paid status and amount;
- masked delivery email;
- watermarked presentation for every purchased photo;
- one `Скачать оригинал` action per OrderItem;
- `Отправить письмо ещё раз`; and
- support contact information.

It contains no provider identifier, payment-attempt history, Object Storage key, signed URL,
internal UUID, administrative notes, or other Order.

The page and all access-grant responses use private no-store caching, suppress referrer leakage and
analytics, and never embed a signed Object Storage URL. An invalid, revoked, malformed, foreign,
or unknown capability produces one sanitized not-found response without revealing whether an
Order exists.

On an original-download request Django:

1. authenticates the purchase-browser capability or permanent OrderAccessGrant;
2. loads one `paid` Order and its exact OrderItem;
3. verifies the requested Photo is that OrderItem's protected Photo;
4. selects the immutable original independently of current gallery publication;
5. verifies the private object through the existing storage control plane;
6. appends download-grant audit; and
7. redirects to a short-lived exact-object signed attachment URL.

The filename remains the existing safe contract:

```text
findme-photo-<public-photo-id>.<jpg|png>
```

The raw original bytes are unchanged. There is no watermark, clean-preview substitution, ZIP,
generated export, source filename, quantity limit, download count limit, or entitlement expiry.

If the object is missing, Order and entitlement remain unchanged. The customer receives a safe
temporary failure, one storage attention opens, and the operator restores the exact object. The
system never substitutes a preview or silently changes the Order to unpaid.

Free-gallery and legacy ready-result downloads retain their existing explicit policies and routes.
The purchased route is a separate authorization context and does not make a normal paid original
public.

## Email Delivery

The customer email contains:

- subject `Ваши фотографии с мероприятия «<название>»`;
- public Order number and date;
- confirmation of payment;
- photo count and total;
- one permanent `Открыть оригиналы` link;
- a warning not to forward the secret link; and
- support contacts.

It contains no attachment, photo preview, provider identifier, temporary signed URL, Object
Storage key, access token in logs, or marketing content.

Email delivery is notification, not entitlement authority. The Order becomes `paid` and is
available in the originating browser even when email is delayed or fails. Opening or clicking an
email through provider tracking is never observed. A later successful authorized Order request
records application access without changing Order state or entitlement.

The `EmailSender` contract accepts one provider-neutral message and returns one normalized delivery
result. A concrete adapter owns SMTP/API authentication, request format, provider identifiers, and
error normalization. The core distinguishes retryable from terminal safe failures without storing
provider secrets or full responses.

The first implementation includes a deterministic local/test email adapter which captures the
provider-neutral message and can return success, retryable failure, or terminal failure for tests.
It performs no network delivery, is unavailable to anonymous users, and is rejected by the same
deployed startup/system check as the test payment adapter.

Automatic delivery attempts run immediately, then after approximately 1 minute, 5 minutes,
30 minutes, 2 hours, and 12 hours. They stop within 24 hours. Exhaustion marks the delivery failed
and opens one CommerceAttention. A valid Order page exposes a rate-limited resend command; a
trusted administrator may correct `delivery_email`, create or copy a new OrderAccessGrant, resend
to the current email, or supply the link through another channel.

The approved product intent stores checkout and delivery email indefinitely because entitlement
and fulfillment are permanent. The fields are used only for checkout, receipt, delivery, support,
and recovery, never marketing, analytics, cross-event identity, or duplicate-purchase prevention.
Public activation requires legal review of the processing basis, published purpose, localization,
retention, access, correction, and mandatory deletion or anonymization exceptions. The application
must follow that controlling legal result even if it narrows the product-intended retention.

## Commerce Worker

One lightweight Commerce worker polls PostgreSQL without sharing the CPU-heavy photo worker. It
claims and processes two explicit work types through separate services and state vocabularies:

- due EmailDelivery work; and
- due PaymentAttempt status reconciliation.

The process may share a runtime loop and container but not a generic job table or handler registry.
PostgreSQL owns claims, bounded leases, idempotency, next-attempt times, and recovery after worker
restart. The worker holds only the concrete gateway and email credentials required by its active
adapters; it receives no photo-worker protocol and performs no image work.

The worker cannot alert through its own queue when it is stopped. Public activation therefore
requires an independent monitoring probe for process liveness and oldest-ready-work age. That
probe must notify operators through the deployed monitoring channel without including email,
access grants, cart tokens, or provider secrets.

## Administration and Attention

Trusted Commerce administrators have a dedicated permission to inspect and act on customer
orders. Django Admin shows full checkout and delivery emails, immutable OrderItems and amounts,
PaymentAttempts and normalized transition history, EmailDeliveries, active/revoked access grants,
download-grant audit, and open/resolved attention records.

The allowed actions are:

- `Подтвердить оплату вручную` for `pending` or `superseded`;
- cancel a `pending` Order;
- `Проверить статус в банке` through the active adapter;
- edit only `delivery_email` with ordinary Django Admin history;
- create, copy, or revoke an OrderAccessGrant;
- send access again to the current delivery email;
- retry a failed EmailDelivery; and
- resolve CommerceAttention with a comment.

Administrators cannot edit checkout email, Event, OrderItems, unit prices, total, currency, paid
time, provider evidence, or download audit. Full administrator trust permits visibility and manual
payment confirmation; it does not make commercial evidence mutable or expose infrastructure
secrets.

CommerceAttention appears as a dedicated Admin list and visible open-count indicator. When a new
record opens, active users with `commerce.handle_attention` and a nonempty email receive an
administrative message through the email adapter. One reminder is attempted no more than once per
24 hours while the record stays open. Notifications contain kind, public Order number, and a direct
Admin link, but no customer access URL, full email, gateway secret, or callback payload.

Attention resolves automatically after confirmed repair where safe: a successful resend resolves
email failure, restored exact-object availability resolves storage failure, and compatible verified
payment evidence resolves a transient reconciliation problem. A trusted administrator may resolve
any remaining record manually with a comment. Records never disappear merely because time passes.

## Feature Gate and Activation Boundary

The stable database runtime key is `paid-photo-purchase`. Missing and `off` fail closed. `staff`
permits authenticated active staff acceptance; `on` permits anonymous checkout and purchase
access. The gate covers checkout GET/POST, payment creation, return/status routes, notification
side effects, purchase-browser cookies, customer access grants, email jobs, purchased downloads,
and customer resend. Admin inspection remains available for already persisted evidence when the
gate is off, while new manual paid transitions and external side effects remain disabled unless a
separately documented incident procedure requires them.

The purchase gate is independent from `paid-watermarked-previews` and `paid-photo-cart`. A new
checkout additionally requires both accepted prerequisite boundaries to permit the exact caller
and photos. Turning purchase off never deletes Orders, entitlements, grants, delivery history, or
audit evidence. Rollback must not silently remove already purchased access; an incident response
may separately close external entry points while preserving fulfillment evidence.

The test gateway and test email adapter are local/test-only dependencies, not a deployed staff
mode. Deployed `staff` acceptance requires real adapters and credentials. Repository code and
schema may merge with every purchase gate absent/off and no Commerce worker activated.

Public `on` is blocked until all of the following are true:

1. the watermark and cart increments are merged and their accepted behavior is verified;
2. the required Commerce ADR is accepted;
3. a real bank adapter passes provider sandbox and authenticated callback/status tests;
4. receipt ownership, tax rate, payment subject/method, seller identity, and fiscal payload are
   approved with the bank/accounting contract;
5. a real email adapter, sender identity, domain authentication, secrets, and delivery monitoring
   are approved;
6. the public offer, personal-use license, consumer-return wording, personal-data policy, and
   checkout copy receive legal review;
7. the Commerce worker and independent queue-liveness alert are deployed with bounded resources;
8. staff-only real-adapter checkout, callback, email, manual recovery, and original download pass
   on one real watermarked photo without exposing another original; and
9. the maintainer explicitly approves `on` after inspecting that evidence.

This specification authorizes no deployment, secret creation, DNS/email change, cron or worker
activation, external account setup, legal-document edit, feature-flag mutation, or real payment.

## Security, Privacy, and Caching

- Cart selection, payment evidence, purchase-browser access, permanent order links, and signed
  Object Storage URLs are distinct capabilities. None substitutes for another.
- Checkout and mutation endpoints use same-origin CSRF protection. Provider notifications use the
  concrete adapter's authenticated server-to-server contract rather than CSRF.
- Browser returns, public Order numbers, cart membership, positive amounts, and unverified callback
  fields never grant entitlement.
- Purchase-browser and order-access capabilities are secret bearers. They do not appear in
  analytics, referrers, application logs, metrics labels, attention email, support search, or
  rendered links outside their authorized context.
- Customer order, status, resend, and download responses are private and `no-store`. Shared caches
  never vary purchase state between browsers.
- Signed Object Storage URLs are generated only after current database authorization and are never
  stored in HTML, JSON, PostgreSQL, logs, email, or audit rows.
- Gateway and email credentials are delivered through the accepted deployed secret boundary. They
  never enter database rows, Admin, logs, exceptions, or customer responses.
- Payment evidence excludes card data and full raw provider payloads unless the later concrete
  adapter demonstrates a narrowly required, legally reviewed field.
- Email is plaintext personal data visible to trusted Commerce administrators. It is not used for
  marketing, search-result identity, analytics, or cross-event correlation.
- Admin manual payment confirmation is a privileged commercial action recorded in standard Django
  Admin history. It is intentionally trusted and not programmatically checked against the bank.
- Download-grant audit proves authorization issuance only. It deliberately excludes network
  identifiers and cannot claim the customer completed the transfer.
- Permanent secret links can be forwarded and remain usable until individually revoked. The
  customer email warns about this consequence.

## Failure and Consistency Semantics

- Checkout locks and revalidates the exact cart before snapshotting; an ineligible or empty cart
  cannot create a payable Order.
- Concurrent checkout submissions for the same unchanged cart create at most one current Order and
  one active PaymentAttempt. Request retries reuse application idempotency rather than creating a
  second bank payment.
- One Order response contains either one complete old price snapshot or one complete new snapshot;
  it never mixes Event prices among OrderItems.
- A provider create timeout is reconciled by idempotency/status before another external payment is
  initiated.
- Notifications, explicit fetches, return-page polling, worker reconciliation, and manual actions
  serialize on the same Order and PaymentAttempt transition boundary.
- Equivalent success is a no-op after `paid`; it never creates duplicate email, cart cleanup, or
  entitlement evidence.
- Verified success for a superseded Order remains valid. Verified success for a deliberately
  canceled Order records attention and cannot silently reopen it.
- Amount/currency mismatch never mutates immutable Order values and never grants automatic access.
- Manual `paid` trusts the administrator and runs the same atomic fulfillment transition without
  requiring bank evidence fields.
- Cart changes after a terminal failed attempt supersede the old Order; a new checkout never edits
  the old snapshot.
- Email failure cannot roll back payment, entitlement, or browser access. Repeated sends use the
  current delivery email and a newly created permanent access grant.
- Correcting delivery email never rewrites checkout email or successful delivery history.
- Revoking one access grant does not revoke the paid Order, another grant, purchase-browser access,
  or already received bytes.
- Missing original bytes never fall back to a clean preview, watermarked preview, source filename,
  or another Photo.
- Unpublishing an Event or Photo after checkout does not invalidate a created payment obligation or
  paid entitlement.
- Physical cleanup never deletes paid Orders, OrderItems, access grants, email history, attention,
  or download audit. Unpaid retention and eventual cleanup require a later explicit policy rather
  than an unapproved default.
- A database failure leaves the last committed state authoritative. External retries always begin
  by reading current committed Order and attempt state.

## Alternatives Considered

### Integrate the first bank directly into views and models

Rejected because the bank protocol is unavailable and provider-specific status, signature, and
error types would leak into the core before they can be verified. One narrow adapter is enough;
there is no generic multi-provider framework.

### Wait for the bank specification before modeling orders

Rejected because immutable order evidence, payment state, entitlement, email delivery, and
original authorization can be completed and tested independently behind an off-by-default gate.
Real acceptance remains impossible until the adapter contract is known.

### Trust the browser return as payment success

Rejected because a return URL is customer-controlled navigation and cannot prove receipt of funds.
Only authenticated normalized server evidence or a trusted manual Admin action may create `paid`.

### Require customer accounts or email verification

Rejected because the approved flow is anonymous and uses email only for receipt, delivery, and
recovery. Permanent bearer links provide cross-device access without a password or OTP system.

### Reuse the cart token as download authority

Rejected because ADR 0030 intentionally makes cart selection non-authoritative for media. A
separate purchase-browser capability and OrderAccessGrant preserve that boundary.

### Create a separate entitlement table

Rejected because the product has one permanent right per immutable paid OrderItem, no transfer,
partial refund, independent expiry, or independent revoke state. A second row would duplicate the
same fact.

### Store one raw permanent order secret

Rejected because Admin and email need reproducible links without making the database alone
sufficient to open every order. Signed revocable grants provide multiple support links and avoid
raw persistent bearer storage.

### Send customer email synchronously from the payment transition

Rejected because provider latency or email failure could delay callbacks, trigger retries, or
incorrectly couple delivery notification to payment and entitlement. A durable outbox-style job
keeps fulfillment atomic and delivery retryable.

### Use the photo-processing worker or a general broker

Rejected because Commerce I/O has different credentials, states, and failure semantics. A small
Commerce worker is sufficient and does not justify a new broker.

### Prevent duplicate purchases by email

Rejected because the email is not verified identity and such a lookup could expose another
person's purchase history. Duplicate purchases are explicitly allowed.

### Require structured evidence for manual payment confirmation

Rejected by the approved trusted-operator workflow. The administrator checks the bank outside the
application and directly chooses the transition; ordinary Admin actor/time history is sufficient
for this increment.

### Implement ZIP delivery now

Rejected because individual original delivery completes the purchase job through the existing
exact-object transport. ZIP generation, storage, expiry, and partial failure are a separate future
design.

## Acceptance Criteria

1. Checkout is available only for one current, unexpired, nonempty, correctly priced Event cart
   whose photos satisfy the accepted watermarked purchasability boundary and all three runtime
   gates for the caller.
2. Checkout requires one normalized email value, creates no account, sends no
   pre-payment verification, and displays the approved summary and legal links before `Оплатить`.
3. One Order snapshots exactly one Event, one immutable checkout email, one mutable delivery email,
   literal `RUB`, exact integer total, and one unique quantity-one OrderItem per selected Photo.
4. Every OrderItem snapshots its unit and line price; later Event price or cart changes cannot
   change an Order.
5. Public Order numbers are unique, non-sequential, support-friendly `FM-XXXXXXXX` references and
   grant no access by themselves.
6. The purchase-browser capability is separate from cart identity, stored only as a digest, and
   permits the checkout browser to reach its Order after successful payment even when email fails.
   Its secret exists only in a `Secure`, `HttpOnly`, `SameSite=Lax`, path-wide cookie that expires
   30 days after the browser's most recent Order creation; reads and downloads do not refresh it.
7. Order states and transitions are exactly `pending`, `superseded`, `paid`, and `canceled` with the
   terminal and late-payment behavior defined by this specification.
8. At most one PaymentAttempt per Order is nonterminal; every attempt uses immutable Order amount,
   `RUB`, and one application idempotency key.
9. `PaymentGateway` exposes only hosted-payment creation, current-status fetch, and authenticated
   notification normalization; provider types never enter Commerce services or models.
10. The deterministic test gateway supports success, cancellation, and pending outcomes for local
    staff acceptance and cannot be selected in the deployed application.
11. Browser return and public status polling never grant entitlement; verified normalized success
    or trusted manual Admin action is required.
12. Equivalent notifications, fetches, retries, and callbacks are idempotent and cannot duplicate
    payment creation, Order transition, cart cleanup, grants, email jobs, or entitlement.
13. Exact successful provider amount and currency make a pending or superseded Order paid; a
    mismatch opens attention and grants no automatic access.
14. A trusted administrator can make pending or superseded Order paid without entering a bank
    reference, amount, or comment, and standard Admin history records actor and time.
15. A trusted administrator can cancel only pending Order; paid and canceled states never reverse.
16. An active attempt locks that Event cart; unsuccessful terminal attempt unlocks it; paid removes
    only matching purchased positions and preserves concurrently added items.
17. An unchanged cart may retry the same Order; the first later cart mutation supersedes it and a
    new checkout creates a new immutable Order.
18. Verified late success for a superseded Order grants its originals even if another Order was
    later created or paid.
19. Payment expiry follows the adapter value or 24-hour fallback and is reconciled before a
    terminal result is inferred.
20. A paid OrderItem is the sole persisted entitlement fact; neither cart membership nor gallery
    visibility authorizes a purchased original.
21. Paid Photo and Event deletion is protected while ordinary hiding and unpublication preserve
    permanent purchased access.
22. A valid purchase-browser capability or active OrderAccessGrant can open exactly one authorized
    Order context; invalid and foreign capabilities reveal nothing.
23. OrderAccessGrants use a stable server signature, are permanent until revoked, may coexist, and
    do not store a complete bearer URL in PostgreSQL.
24. The customer Order page contains the approved order details, masked email, watermarked photos,
    individual original actions, resend, and support contact without provider or storage secrets.
25. Every purchased download rechecks paid OrderItem authorization, signs only its exact original
    as an attachment, and uses `findme-photo-<public-photo-id>.<jpg|png>`.
26. Downloads have no count or time limit; every successful signed-grant issuance is audited
    without URL, token, IP, User-Agent, or transfer-completion claim.
27. Missing purchased original preserves Order and entitlement, substitutes no other bytes, returns
    a safe failure, and opens one deduplicated storage attention.
28. Payment success creates email work without waiting for the email provider; email failure cannot
    change paid state or browser access. The fulfillment milestone is derived only after `paid`
    plus either successful access-email delivery or first authorized customer access, with the two
    evidence timestamps recorded separately and no additional entitlement transition.
29. Customer email uses the approved subject/content, contains one permanent Order link and no
    attachment, preview, provider identifier, temporary storage URL, or marketing content.
30. A deterministic local/test email adapter captures messages and exercises success, retryable
    failure, and terminal failure without sending network email; deployed configuration rejects it.
31. Email retry timing is bounded to the approved attempts within 24 hours; exhaustion becomes
    failed, opens attention, and remains manually retryable.
32. Admin email correction preserves checkout email and successful delivery history, cancels
    obsolete unsent delivery, and sends subsequent access only to current delivery email.
33. Customer and Admin resend create new grants without silently revoking earlier active links;
    grants may be individually revoked.
34. One Commerce worker processes separate email and payment-reconciliation queues without sharing
    the photo worker or introducing a generic broker.
35. Open Commerce attention deduplicates by kind/subject, appears with an Admin count, sends one
    immediate and at most daily responsible-staff reminder, emits a safe structured event, and
    supports automatic or commented manual resolution.
36. Active staff with `commerce.handle_attention` and email receive attention messages containing
    only safe references and an Admin link.
37. Independent monitoring detects a stopped Commerce worker and overdue ready work; the worker is
    never treated as capable of notifying about its own death.
38. Django Admin exposes full customer email and commercial evidence to trusted Commerce staff but
    does not permit editing checkout email, items, prices, total, currency, paid time, transition
    evidence, or download audit.
39. `paid-photo-purchase` missing/off closes every public purchase entry and side effect; staff and
    on states behave exactly as defined without deleting persisted evidence.
40. The deployed application rejects test adapters, and public activation remains blocked by the
    exact bank, fiscal, email, legal, worker, monitoring, staff-smoke, and maintainer gates.
41. Cart, free-gallery download, legacy saved-result behavior, watermarked paid presentation,
    selfie ranking/membership, processing, pagination, filters, and face controls remain unchanged
    outside the new checkout and purchased-order context.
42. Focused model, migration, service, concurrency, adapter-contract, callback, Admin, authorization,
    email-worker, attention, JavaScript, accessibility, and desktop/mobile visual evidence covers
    the complete dark-deployed purchase critical path and realistic failure paths.

## External Prerequisites, Not Open Design Questions

The internal design has no unresolved product choices. The following external facts are required
before real-adapter planning or public activation and must not be guessed:

- the bank gateway's API, authentication, notification, status, idempotency, expiry, sandbox, and
  hosted-confirmation contracts;
- fiscal receipt ownership, seller identifiers, tax rate, payment subject, payment method, and
  required customer contact fields;
- the production email provider, sender identity, domain authentication, credentials, quotas,
  bounce behavior, and data-location contract;
- legally approved public offer, personal-use license, consumer-return language, personal-data
  processing basis, retention and deletion rules, and checkout/email copy; and
- the exact support contact rendered to customers.

These prerequisites intentionally block real money and real customer email. They do not block an
approved ADR, an implementation plan for the dark-deployed core, or local test-adapter evidence.
