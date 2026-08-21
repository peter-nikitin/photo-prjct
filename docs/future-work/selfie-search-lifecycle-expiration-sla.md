# Revisit temporary-selfie lifecycle timing guarantee

## Observed gap

The repository contract configures an `Expiration: Days=1` lifecycle rule as a retention backstop.
There is no canonical-deployment lifecycle observation that establishes its timing, and Object
Storage may apply expiration asynchronously; it is therefore not evidence of a hard 24-hour deletion
SLA.

## Why this is non-blocking now

Django deletes the exact temporary selfie before terminal publication; lifecycle exists only for
abandoned objects. The approved MVP requires bounded retention, not a measured hard-delete deadline.

## Revisit trigger

Revisit before an accepted requirement, privacy commitment, regulator, or customer notice requires
a measured hard maximum deletion time, or before making such a deletion-time claim. Establish the
canonical-deployment lifecycle evidence required for that commitment rather than treating the
configured rule as proof.
