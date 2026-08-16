# Pre-v3 cluster corpus compatibility

## Observed gap

The implicit baseline for an event without an explicit face-generation activation now contains
gallery generations v1, v2, and v3. Cluster expansion requires exact equality between that frozen
generation list and the list stored on the active corpus. A corpus built against the former v1/v2
baseline is therefore incompatible with the new implicit v1/v2/v3 baseline and correctly falls
back to direct-only results.

## Why this does not block the SCRFD release

Cluster expansion is disabled by repository default, and this release does not activate a corpus
for any environment or event. Direct event-scoped ranking remains available and unchanged, so the
critical new-upload and selfie paths do not depend on corpus compatibility.

## Trigger and required work

Before enabling this release for any environment or event where cluster expansion is active:

1. Inventory the active corpus and record its exact stored face-generation list.
2. If the corpus does not contain the exact v1/v2/v3 baseline, rebuild it against that baseline and
   review the rebuilt corpus evidence before activation.
3. Activate only the reviewed corpus version for the intended event and environment.
4. Verify the activated corpus generation list is exactly v1/v2/v3, then compare direct and
   expanded outcome counts to confirm direct ranking remains intact and expansion is actually used.
