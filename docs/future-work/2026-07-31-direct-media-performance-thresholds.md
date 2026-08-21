# Revisit direct-media performance thresholds after a canonical event-scale check

## Observed gap

Direct Object Storage delivery has no recorded canonical-deployment, event-scale measurements or
agreed numeric operating thresholds for bucket egress, client tile-load time, signed-delivery error
rate, or authorization latency. A controlled 20,000-photo fixture would provide the required
evidence, but the current delivery does not select a CDN or smaller tile derivative.

## Why it is non-blocking

The current critical path keeps image bytes outside Django/Gunicorn and bounds gallery and
selfie-result presentation to 100 cards per page. No current evidence shows that the existing
1600px `preview-small-v1` derivative or direct bucket delivery violates an event requirement.

## Revisit trigger

Before accepting direct delivery for a published event expected to have about 20,000 photos, run a
controlled canonical-deployment check and record Object Storage egress, client tile-load time,
direct-delivery error rate, and authorization latency. Agree numeric operating thresholds before
that event; reopen this finding if the check or a later event exceeds one of them.

## Likely scope

Define the measurements and thresholds, capture representative browser and Object Storage evidence,
then prepare a separate specification and ADR if a new derivative, CDN, or media topology change is
needed. Preserve the preview-first face-embedding contract unless that decision is explicitly
revisited.
