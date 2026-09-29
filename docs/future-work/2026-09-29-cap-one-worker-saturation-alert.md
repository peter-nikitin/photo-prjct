# At-ceiling worker saturation alert for ceiling one

## Observed gap

The [prepared worker saturation rule](../../deploy/monitoring/alerts.md#worker-pool-at-ceiling-saturation)
uses `ramp(sign(replace_nan(R, -1) - 1.5))`, where `R` is fresh actual running VM membership.
It requires two running VMs. It cannot detect sustained at-ceiling backlog with the approved
`pool_max_size=1` policy, bulk 0..1 and selfie 1..1. The
[runbook](../runbooks/worker-pools.md#saturation-stalled-work-and-missing-telemetry-diagnosis)
records that limitation, but manual queue inspection does not satisfy the inherited
[at-ceiling alert acceptance requirement](../superpowers/specs/2026-09-23-autoscaled-photo-worker-pools-design.md).
No accepted cap-one-specific predicate or native delivery proof exists in this package.

## Why this is non-blocking now

This delivery prepares repository support only. The dated accepted inventory has no provisioned
remote worker path, and the package does not activate alerts, provision paid resources or claim
live acceptance. Current local processing is unaffected. The gap is therefore future work for
repository delivery, but remains an unresolved prerequisite for customer cutover; this
classification does not waive the inherited acceptance requirement.

## Revisit trigger

Before **any ceiling-one customer cutover**, promote this finding into the separately reviewed
activation package. That package must supply a cap-one-specific at-ceiling predicate and pass
native `Alarm`, `NoData` and recovery acceptance with notification delivery for the actual
pool/zone and existing approved operator channel. Do not postpone it until optional worker
diagnostics are enabled. Manual inspection, fixture GREEN and deployment alone do not clear
this gate.

## Likely scope and acceptance evidence

- Review the smallest policy-specific saturation rule using fresh actual running membership,
  claimable age and sustained age growth at the approved ceiling one. Preserve the current
  distinction between requested target size and observed running capacity, bulk-zero/wakeup,
  and the selfie claim cap; the alert grants no scaling or processing authority.
- Validate native sustained `Alarm`, cleared-predicate recovery and both missing-selector and
  missing-points `NoData` cases for the exact pool/zone. Prove notifications reach the approved
  channel and record fresh ingestion/evaluation timestamps.
- Preserve the 90-second observation freshness boundary, absent/stale capacity as unknown
  (`F=0`, omitted `R`), missing queue observations as unknown, and no interpolation, last-value
  fill or fabricated zero. Exercise cloud-observation loss and a total publisher outage; the
  observation `Alarm`/`NoData` takes precedence over historical saturation points. Require
  fresh current capacity, queue and coordinator evidence before declaring current saturation
  or recovery, and document actual native gap/window behavior.
- Keep worker runtime diagnostics and this native demand/capacity alert distinct. No customer
  identifiers, vectors, object keys or unbounded labels belong in the rule or evidence. Resource
  changes and any consequential notification activation require their separate approved
  operational targets and read-back.

The present artifact records the gap and activation trigger only. It changes no alert query,
resource, channel or cloud state.
