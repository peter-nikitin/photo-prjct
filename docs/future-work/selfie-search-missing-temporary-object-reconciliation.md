# Reconcile a missing temporary selfie before claim

## Observed gap

If the exact temporary selfie object is already missing when Django prepares a claimed
`selfie_query`, storage inspection raises `ObjectMissing`. The protected claim endpoint returns a
retryable `503`, its transaction rolls back, and the search remains queued. Later claims repeat the
same cycle instead of publishing a sanitized terminal failure.

## Why this does not block the current task

The accepted critical path handles the normal claim, worker failure, callback, cleanup, replay, and
recovery paths. It does not establish a normal path that deletes a queued selfie before claim; the
missing-object case remains a separate failure behavior to decide if its trigger occurs.

## Revisit trigger

Bring this into scope when either:

- observed queue age or a worker outage approaches the 24-hour lifecycle bound; or
- monitoring records the first `ObjectMissing` while preparing a selfie-search claim.

At that point, reconcile the search to a sanitized terminal `failed` state, clear the temporary
object reference, and add a regression proving repeated claims do not leave the stable result URL
queued forever.
