# Selfie observability expanded regression matrix

## Observed gap

The critical producer paths now cover accepted worker callbacks, idempotent callback suppression,
transport retry, bounded failure disposition, positive ranking, cleanup retry/recovery, terminal
deduplication, and call-site logging/query failure containment. The remaining specification matrix
does not independently assert every combination of zero-match ranking, incompatible ranking,
legacy frozen-candidate exact counts and timing nullability, stale versus expired callbacks, and
every backend terminal status.

## Why this is non-blocking now

Those combinations reuse the same strict event constructors and the same producer branches covered
by the focused tests. No current production incident, accepted requirement gap, privacy leak, data
loss path, or changed lifecycle branch depends on duplicating all combinations in this delivery.
The blocking review findings were the uncontained terminal attempt-count query and duplicate worker
emission after an idempotent callback; both have direct regression coverage.

## Trigger to bring it back into scope

Implement the expanded matrix when a ranking algorithm/cohort-loading change alters eligible or
matched counts, when terminal status or retry/stale semantics change, or when an observability
schema revision adds/removes timing or disposition fields. At that point add literal assertions for
each affected combination before changing its producer.
