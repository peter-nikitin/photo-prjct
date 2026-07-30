# Refresh worker sizing rollout status

## Observed gap

The introduction of `docs/photo-processing-vm-sizing.md` still describes the staging worker profile
and live VM details as unverified, although the ML worker has now been activated and smoke-tested on
the staging VM.

## Why this is non-blocking

The stale introductory wording does not affect the deployed Compose limits, worker execution, or
the recorded ML acceptance evidence.

## Bring back into scope when

Refresh the document's rollout-status framing when the next operational documentation pass records
the staging deployment as a durable supported environment.
