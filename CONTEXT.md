# FindMe Photo Domain

FindMe Photo is an event-photo marketplace where customers discover photos and acquire access to
private originals while operators and photographers manage publication.

## Commerce language

**Cart**:
An anonymous, browser-local selection of currently purchasable photos from one Event. It is neither
a quote nor proof of purchase.
_Avoid_: Order, purchase, entitlement

**Order**:
An immutable, event-specific commercial snapshot submitted for payment at one exact total.
_Avoid_: Cart, payment, transaction

**Order Item**:
One quantity-one photo and its immutable price inside an Order. In a paid Order, it is the durable
fact from which access to that photo's original is derived.
_Avoid_: Cart item, entitlement row

**Payment Attempt**:
One idempotent effort to pay an Order through a payment gateway. An Order may outlive a failed or
expired Payment Attempt.
_Avoid_: Order, payment confirmation

**Payment Confirmation**:
Authoritative evidence that moves an Order to paid, either verified through the payment gateway or
asserted by a trusted administrator after an external check.
_Avoid_: Browser return, redirect success

**Entitlement**:
The right to obtain one purchased original, derived only from an Order Item in a paid Order.
_Avoid_: Cart membership, gallery visibility, signed URL

**Order Access Grant**:
A permanent, revocable bearer capability that opens exactly one Order and its entitled originals.
_Avoid_: Cart token, customer account, Object Storage URL

**Purchase Browser Capability**:
An anonymous browser-local bearer capability that restores Orders created in that browser. It is
separate from both cart identity and emailed Order Access Grants.
_Avoid_: Customer identity, cart token, login session

**Delivery Email**:
The current address used to send Order access. It may differ from the immutable email entered at
checkout after a trusted administrator corrects it.
_Avoid_: Customer account, verified identity

**Commerce Attention**:
A durable, operator-facing commercial exception that requires confirmed repair or an explicit
administrative resolution.
_Avoid_: Application log, customer notification
