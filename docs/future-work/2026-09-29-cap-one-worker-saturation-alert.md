# Live acceptance of cap-one worker alerts

## Observed gap

[ADR 0048](../adr/0048-reuse-managed-prometheus-for-worker-alerts.md) selects the existing
Managed Prometheus stack. The [integration plan](../plans/2026-09-30-worker-prometheus-alerts.md)
provides a disabled-by-default cap-one rule profile, read-only pool observations and executable
fixtures. It replaces the obsolete ceiling-two native predicate. Repository delivery does not
prove live worker ingestion, evaluation or notifications.

## Why this is non-blocking now

No remote fleet is activated by this package; current local processing remains authoritative.
This is a live acceptance gate for customer cutover, not a reason to delay repository integration
or investigate a second alert API.

## Revisit trigger and evidence

Before **any ceiling-one customer cutover**, enable the worker profile through the existing
Git-owned monitoring workflow after approved provisioning. Record for bulk and selfie:

- Fresh queue, cloud and successful native-publisher source timestamps, finite actual running
  capacity and expected-node diagnostic observations. Never substitute target size for capacity.
- Sustained cap-one firing and progress/reset recovery, with source evidence current throughout.
- Absent selectors, no new samples, retained stale sources, cloud observation loss and total
  publisher outage. Missing evidence must replace current-saturation conclusions with unknown
  state; do not interpret a resolved saturation alert alone as restored service.
- Healthy bulk idle zero and warm selfie behavior.
- Fresh successful rule evaluations and delivered firing/recovery notifications on the existing
  operator channel; record the exact Git revision and known-revision rollback.

No rule grants scaling or processing authority. Keep bounded pool/node labels and exclude
customer identifiers, vectors, URLs and object keys. Native autoscaling publication remains
unchanged and requires its own live acceptance. Paid resources, access changes and live alert
activation require separately approved operational targets.
