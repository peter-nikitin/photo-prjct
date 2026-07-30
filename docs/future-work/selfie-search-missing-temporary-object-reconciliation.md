# Reconcile a missing temporary selfie before claim

## Observed gap

If the exact temporary selfie object is already missing when Django prepares a claimed
`selfie_query`, storage inspection raises `ObjectMissing`. The protected claim endpoint returns a
retryable `503`, its transaction rolls back, and the search remains queued. Later claims repeat the
same cycle instead of publishing a sanitized terminal failure.

## Why this does not block the current task

Public selfie search remains disabled until the deployment stage. Its 24-hour temporary-prefix
lifecycle rule is also introduced only during that activation work, so the accepted critical path
does not currently delete a normally queued selfie before claim. Task 4 correctly handles the
normal claim, worker failure, callback, cleanup, replay, and recovery paths.

## Revisit trigger

Bring this into scope when either:

- observed queue age or a worker outage approaches the 24-hour lifecycle bound; or
- monitoring records the first `ObjectMissing` while preparing a selfie-search claim.

At that point, reconcile the search to a sanitized terminal `failed` state, clear the temporary
object reference, and add a regression proving repeated claims do not leave the stable result URL
queued forever.
