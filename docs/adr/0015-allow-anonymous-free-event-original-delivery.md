# 0015: Allow anonymous free-event original delivery

- Status: Proposed
- Date: 2026-07-19
- Deciders: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The event gallery needs usable media before reduced derivatives exist. Originals remain private
under ADRs 0006 and 0013, and the architecture leaves anonymous free-event original delivery as an
open decision. A narrow policy is needed before the gallery may rely on complete-original delivery.

## Decision drivers

- Make completed uploads viewable for currently published free events without waiting for derivative
  processing.
- Preserve private storage keys and credentials and allow unpublishing to affect the next request.
- Avoid defining paid-media, commerce, or general download policy in the gallery increment.
- Keep product eligibility in Django and PostgreSQL under ADRs 0001 and 0002.
- Depend only on existing least-privilege final-prefix read access rather than changing cloud access.

## Considered options

1. Wait until reduced derivatives and their readiness contract exist.
2. Redirect anonymous clients to short-lived presigned Object Storage URLs.
3. Stream eligible originals through a controlled Django response.

## Decision

Allow an anonymous client to receive the complete stored private original inline only for an
eligible completed upload belonging to a currently published `FREE` event. Delivery uses the
deterministic event/photo/variant Django route. Every request reloads and rechecks event and photo
eligibility in PostgreSQL before opening the object.

Django streams the object without exposing the permanent object key, bucket credentials, or an S3
redirect. The response is `private, no-store` and initially supports neither Range requests nor a
response ETag. Paid-event originals remain unavailable.

Existing least-privilege `GetObject` access limited to the validated final-object prefix is an
external prerequisite for activation. Acceptance of this ADR authorizes no IAM or service-account
role change, bucket-policy change, ACL, CORS, lifecycle change, credential broadening, bucket
change, or other cloud mutation. If the prerequisite is absent, the route remains disabled until a
separately authorized operational change establishes it.

This is a transitional policy until derivatives and persisted readiness exist. It does not define
attachment or general download behavior, paid delivery, purchases or entitlements, derivative
generation or readiness, watermarks, cart or checkout, public-bucket or CDN delivery, retention or
deletion, or broader anonymous-original access.

The decision conforms to ADRs 0001, 0002, 0006, 0013, and 0014, supersedes none, and resolves only
the anonymous inline-delivery part of the open media policy.

## Consequences

### Positive

- Free-event galleries can show completed uploads through stable application URLs.
- Database eligibility remains authoritative and private storage details remain undisclosed.
- A later resolver can select derivatives without changing the public gallery route.

### Negative

- Complete originals consume Django and VM transfer capacity and reveal their full-resolution bytes
  to anonymous users of eligible free events.
- The bytes are the unsanitized stored originals and may contain embedded EXIF, GPS coordinates,
  camera identifiers, timestamps, or other metadata; this policy performs no stripping or
  redaction.
- `inline` and `private, no-store` affect browser presentation and caching but cannot prevent a
  recipient from saving, copying, or redistributing the bytes. Broader product download UX remains
  a separate decision, but that separation cannot disguise the actual complete-byte access granted
  here.
- No Range support or public caching is available during the transitional policy.
- Paid media and broader download policy remain unresolved.

### Follow-up

- Define derivative persistence, readiness, and execution before replacing originals with previews.
- Decide paid-event previews, entitlements, and broader download policy separately.

## Validation and rollback

Validate database eligibility on every request; absence of object-key or credential disclosure;
inline, private/no-store headers; complete streaming and close behavior; 404 responses after an
event or photo becomes ineligible; and existing least-privilege `GetObject` access only for the
validated final prefix, without a cloud mutation under this ADR. Reconsider when persisted
derivatives can replace originals, privacy or legal policy disallows unsanitized anonymous original
access, recipient-saving semantics are unacceptable, or measured Django/VM transfer cannot meet
service targets. Roll back by disabling the gallery media route or redeploying the prior application
image; this does not revoke bytes already received, and no object or database migration is required.

## References

- [Event photo gallery design](../superpowers/specs/2026-07-18-event-photo-gallery-design.md)
- [Event photo gallery implementation plan](../plans/2026-07-18-event-photo-gallery.md)
- [Architecture: purchase and download](../architecture.md#purchase-and-download)
- [Architecture: security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- [Architecture: open decisions](../architecture.md#open-decisions)
- [ADR 0001](0001-django-modular-monolith.md)
- [ADR 0002](0002-postgresql-system-of-record.md)
- [ADR 0006](0006-yandex-object-storage-media.md)
- [ADR 0013](0013-use-direct-private-object-storage-ingestion.md)
- [ADR 0014](0014-keep-stage-2-ingestion-request-driven.md)
