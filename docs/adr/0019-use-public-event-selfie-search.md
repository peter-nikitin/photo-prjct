# 0019: Use public event-scoped selfie search

- Status: Accepted
- Date: 2026-07-30
- Deciders: project maintainers
- Supersedes: [ADR 0015](0015-allow-anonymous-free-event-original-delivery.md)
- Superseded by: none

## Context

Published event pages need a customer-facing path from one selfie to probable matching photos. The
repository already stores accepted event-photo face embeddings and runs ML inference in a
separately runnable worker governed by ADR 0017. Django and PostgreSQL remain authoritative for
product state, while workers have no database or permanent Object Storage credentials.

The feature also needs a stable shareable result URL, deletion of the submitted selfie after query
embedding, and useful results for both free and paid published events. ADR 0015 currently permits
anonymous original delivery only for free events and explicitly keeps paid-event originals
unavailable. No accepted ADR governs public biometric queries, query-vector retention, or
bearer-linked face-search results.

## Decision drivers

- Deliver the shortest working event-to-selfie-to-results customer path.
- Reuse the deployed Django/PostgreSQL and worker boundaries without adding a broker or vector
  service.
- Keep every query and match event-scoped and present matches as probable, not identified people.
- Delete the raw selfie before publishing a terminal result and never persist its query embedding.
- Give one result a stable URL that can be shared without an account.
- Support paid published events before reduced or watermarked derivatives exist.

## Considered options

1. Use the existing ML worker for query embedding, let Django perform exact event-scoped search,
   and publish an unguessable bearer-linked result snapshot.
2. Run face inference synchronously in Django and keep the result in the current browser session.
3. Introduce a separately public ML/search service and a dedicated vector engine.
4. Keep paid-event selfie search unavailable until reduced watermarked previews exist.

## Decision

Use the existing private Django-polled worker boundary for selfie query embedding. Django accepts
one bounded JPEG or PNG, stores it under a generated private temporary Object Storage key, creates
a durable search record and job, and immediately redirects the visitor to an event-scoped result
URL containing a cryptographically unguessable token.

The worker receives a short-lived read grant for exactly that selfie, requires exactly one
acceptable face, and returns one finite normalized embedding through its protected terminal
callback. It receives no gallery vectors, database access, permanent Object Storage credential, or
public result token.

Django validates the returned embedding and performs direct exact cosine comparison in memory
against the frozen compatible accepted face embeddings of that event. PostgreSQL stores the
immutable ordered photo-result snapshot, score evidence, state, and model/threshold versions.
Django never persists the query embedding. The result cannot become publicly terminal until the
temporary selfie has been deleted; application cleanup retry and a bounded temporary-prefix
lifecycle cover deletion failures and abandoned objects.

The stable result is a non-expiring public bearer resource. Anyone with its URL may view the saved
snapshot without authentication. New photos, embeddings, or model versions do not recompute it.
Current event and photo publication eligibility is rechecked when rendering, so ineligible photos
are omitted without changing the relative order of remaining results.

Search is available for every published free or paid event. A valid ready-result bearer token may
authorize anonymous inline delivery of an existing original only when that original belongs to the
saved result and its event remains published. This narrow paid-event exception does not activate
the normal paid gallery, unrelated paid photo URLs, attachment downloads, purchases, or
entitlements.

This decision deliberately does not add authentication, result expiry, consent records, link
revocation, rate limiting, abuse tooling, moderation, named identity, cross-event matching,
cluster/graph expansion, thumbnails, watermarks, a broker, or a vector database. Face-match copy
must describe probable matches rather than assert identity.

## Consequences

### Positive

- The customer receives one working path and one shareable URL on the existing product stack.
- ML dependencies stay out of Django request handlers and the worker retains its existing
  no-database/no-permanent-storage-credential boundary.
- PostgreSQL remains authoritative for state and result membership; exact search avoids premature
  vector infrastructure.
- Raw selfie retention is bounded and query embeddings are not stored.
- Free and paid published events behave consistently before derivative-media work is delivered.

### Negative

- Anyone who obtains a result URL can view it indefinitely; there is no ownership check,
  revocation, or expiry in this increment.
- Saved probable-match identities and scores remain durable even though the selfie and query vector
  are deleted.
- A bearer link can deliver full-resolution paid-event originals, weakening the future commerce
  boundary until reduced watermarked previews replace them.
- Exact comparison runs inside a protected Django callback and must be revisited if measured event
  size or latency exceeds an explicit bound.
- The deliberately narrow MVP does not provide the broader biometric governance and abuse controls
  required by future identity, cross-event, or private-account features.

### Follow-up

- Replace paid-event original presentation with reduced watermarked previews without changing
  stable search identity or result membership.
- Revisit bearer-link retention and access when accounts, deletion requests, or link abuse become
  product requirements.
- Revisit exact Django comparison only when measured cohort size or callback latency violates an
  agreed bound.
- Add consent, suppression, incident, and moderation workflows before named identity, cross-event
  search, or broader biometric reuse.

## Validation and rollback

Validate event isolation, exactly-one-face behavior, deterministic direct ranking, immutable result
snapshots, absence of persisted query embeddings, selfie deletion before terminal publication,
unguessable bearer lookup, paid-result-only media authorization, and unchanged normal paid-gallery
denial. Confirm the worker still has no database or permanent Object Storage credentials.

Roll back by disabling new selfie-search submissions and worker job creation. Existing search rows
may remain readable under their approved bearer semantics, while temporary-object cleanup
continues. Reconsider this decision if result-link abuse becomes material, paid-original exposure
is unacceptable, deletion cannot be made reliable, or measured exact-search latency exceeds the
documented callback bound.

## References

- [Public selfie search design](../superpowers/specs/2026-07-30-public-selfie-search-design.md)
- [Architecture: Search](../architecture.md#search)
- [Architecture: security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- [ADR 0001: Use a Django modular monolith](0001-django-modular-monolith.md)
- [ADR 0002: Use PostgreSQL as the system of record](0002-postgresql-system-of-record.md)
- [ADR 0006: Use Yandex Object Storage for media](0006-yandex-object-storage-media.md)
- [ADR 0015: Allow anonymous free-event original delivery](0015-allow-anonymous-free-event-original-delivery.md)
- [ADR 0017: Use Django-polled photo-processing jobs](0017-use-django-polled-photo-processing-jobs.md)
