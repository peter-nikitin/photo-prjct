# Revisit temporary-selfie lifecycle timing guarantee

## Observed gap

The configured `Expiration: Days=1` lifecycle rule is a retention backstop. Object Storage may
apply lifecycle expiration asynchronously, so it is not evidence of a hard 24-hour deletion SLA.

## Why this is non-blocking now

Django still deletes the exact temporary selfie before terminal publication; lifecycle exists only
for abandoned objects. The approved MVP requires bounded retention, not a measured hard-delete
deadline, and live bucket behavior has not been activated or measured.

## Revisit trigger

Revisit before any accepted requirement, privacy commitment, regulator, or customer notice requires
a measured hard maximum deletion time rather than the current application cleanup plus lifecycle
backstop.
