# Paid Search Result Package Action

## Observed gap

Paid selfie-search results have per-photo cart actions but no bulk action. A label such as “add
all” is ambiguous because comparable marketplace actions may add a specially priced package or
discounted selection rather than the same collection of individually priced cart items.

## Why it is non-blocking

The current accepted archive increment serves free ready-result downloads and already-purchased
Order fulfillment. Paid customers can continue selecting individual photos through the existing
cart flow. Choosing package composition or discount semantics is not required to make those paths
work and would expand the current task into pricing, checkout, and entitlement design.

## Revisit trigger

Revisit when the maintainer selects a concrete paid-result bulk offer, including whether it is a
discounted package, a fixed-price event product, or ordinary individual items selected together.

## Likely scope

Define the customer-facing action and copy, package membership and behavior across result pages,
price snapshot and discount rules, cart representation, checkout presentation, OrderItem or
package entitlement, later result changes, partial prior cart selection, and desktop/mobile visual
coverage. Reassess the Commerce domain model and ADRs before planning implementation.
