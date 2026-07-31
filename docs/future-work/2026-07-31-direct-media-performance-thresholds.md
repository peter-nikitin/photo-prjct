# Revisit direct-media performance thresholds after the event-scale staging check

## Observed gap

The direct Object Storage delivery release has no agreed numeric operating thresholds yet for bucket
egress, client tile-load time, signed-delivery error rate, or authorization latency. The rollout
requires recording those measurements against a controlled 20,000-photo event fixture, but it does
not define a CDN or a smaller tile derivative as part of the current release.

## Why it is non-blocking

The current critical path removes image bytes from Django/Gunicorn and bounds gallery and
selfie-result presentation to 100 cards per page. Its staging gate verifies full direct body
delivery, authorization, pagination, and the resulting process configuration before event use.
No current evidence shows that the existing 1600px `preview-small-v1` derivative or direct bucket
delivery violates an event requirement.

## Revisit trigger

Immediately after a successful controlled 20,000-photo staging check, record Object Storage egress,
client tile-load time, direct-delivery error rate, and authorization latency, then agree numeric
operating thresholds before the next event. Reopen this finding only if a later staging check or
event exceeds one of those thresholds.

## Likely scope

Define the measurements and thresholds, capture representative browser and Object Storage evidence,
then prepare a separate specification and ADR if a new derivative, CDN, or media topology change is
needed. Preserve the preview-first face-embedding contract unless that decision is explicitly
revisited.
