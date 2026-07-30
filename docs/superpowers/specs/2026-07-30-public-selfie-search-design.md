# Public Selfie Search Design

## Status

Approved in conversation and repository review on 2026-07-30.

- Related architecture: [`docs/architecture.md`](../../architecture.md), current event gallery and
  photo-processing boundaries, proposed Recognition and Search modules, event-scoped face search,
  and face-data security constraints
- Related product job: [`PJ-008 — Customer — Find photos by face`](../../product-jobs.md#pj-008--customer--find-photos-by-face)
- Related specifications:
  [`2026-07-26-local-selfie-search-design.md`](2026-07-26-local-selfie-search-design.md) and
  [`2026-07-29-event-photo-processing-worker-design.md`](2026-07-29-event-photo-processing-worker-design.md)
- Related ADRs:
  [ADR 0001](../../adr/0001-django-modular-monolith.md),
  [ADR 0002](../../adr/0002-postgresql-system-of-record.md),
  [ADR 0006](../../adr/0006-yandex-object-storage-media.md),
  [ADR 0013](../../adr/0013-use-direct-private-object-storage-ingestion.md),
  [ADR 0015](../../adr/0015-allow-anonymous-free-event-original-delivery.md), and
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md)
- ADR impact:
  - Requires new ADR — public event-scoped biometric query processing, bearer-link result access,
    query/result retention, and the Django/worker search boundary are durable choices not yet
    governed by an accepted ADR.
  - Supersedes ADR 0015 — a selfie-result bearer link may deliver existing originals from a
    published paid event, while ADR 0015 currently prohibits anonymous paid-event original
    delivery.

Approval of this specification selects the product design. It does not by itself accept the new
ADR or supersede ADR 0015. Those decisions must be recorded before implementation relies on them.

## Outcome

A visitor to any published event can upload one selfie and immediately reach a stable result URL.
The page first shows search progress and then, without another form submission, shows probable
matching event photos in a deterministic order.

The result URL is an unguessable public bearer link. Anyone who has it can view the saved result
without signing in. The result is an immutable snapshot: later photo uploads or newly completed
face processing do not change it.

The raw selfie is temporary. The ML worker converts it to one query embedding, Django computes the
event-scoped result, and the system deletes the selfie before publishing the result as ready. The
query embedding is never persisted.

The objective is the shortest working customer path from event page to probable matches. It is not
a complete biometric-governance, abuse-prevention, derivative-media, or commerce increment.

## Success Criteria

The feature succeeds when:

- every published event page offers one clear selfie-selection action;
- a valid submission redirects immediately to one stable public result URL;
- the same URL represents queued, processing, cleanup, ready, and terminal failure states;
- one acceptable face produces a deterministic snapshot of probable photos from only that event;
- the ready result remains shareable without authentication;
- the raw selfie has been deleted and the query embedding has not been stored before the result
  becomes ready; and
- the existing event gallery and photographer ingestion paths continue to work unchanged.

Face matches are candidates, not identity assertions. The UI must use language such as “Похожие
фотографии” or “Возможные совпадения” and must not state that a depicted person has been
identified.

## Scope

### Included

- A selfie-search block on every published event detail page, including free and paid events.
- One JPEG or PNG upload with explicit byte and pixel-dimension limits.
- A durable `SelfieSearch` product record with an unguessable public token and explicit state.
- A private temporary Object Storage object for the submitted selfie.
- A new selfie-query job executed through the existing Django-polled worker boundary.
- Exactly-one-face validation and query embedding in the existing ML runtime.
- Exact cosine search in Django over persisted, eligible event photo embeddings.
- Stable unique-photo ranking and an immutable stored result snapshot.
- A public progress/result page and a small polling endpoint at the same stable result identity.
- Existing gallery-card and media-opening behavior for result photos.
- Temporary anonymous inline original delivery from a published paid event when reached through a
  valid selfie-result bearer link.
- Terminal cleanup of the selfie and an Object Storage lifecycle backstop for abandoned temporary
  objects.
- Critical-path product, contract, state, privacy, ranking, and regression tests.

### Excluded

- Authentication, customer accounts, saved search history, or ownership recovery.
- Expiry or user-initiated deletion of ready result pages.
- CAPTCHA, rate limiting, abuse dashboards, link revocation, and audit UI.
- Thumbnails, reduced copies, watermarks, CDN delivery, or paid download entitlements.
- Changes to the normal paid-event gallery or general paid-original delivery policy.
- Recomputing an existing result when photos, embeddings, thresholds, or models change.
- Persisting query embeddings or adding a vector database, `pgvector`, or ANN search.
- Cross-event search, cluster expansion, graph expansion, bib/time/location expansion, or person
  naming.
- Operator correction, face suppression, complaint, incident-response, and legal-consent
  workflows.
- A claim of complete biometric-governance or production-scale readiness.

These exclusions do not override the explicit deletion, event-isolation, and bearer-token
boundaries required by this specification.

## User Experience

### Event page

Every published event detail page contains a selfie-search block. It explains that:

- the system looks for probable matches within this event;
- the selfie is deleted after the search is prepared; and
- anyone with the result link can view the result.

The block contains one file input and one primary action, “Найти мои фото”. The browser prevents a
second submission while the first is in progress. The server remains authoritative for format,
size, and event eligibility.

An invalid format, empty file, or exceeded upload limit is rejected on the event page with a
specific correction message. No durable search or temporary object is created for a request that
fails this initial validation.

### Stable result page

After Django accepts the upload, the browser receives an HTTP redirect to:

`/events/<event-slug>/selfie-search/<public-token>/`

`public-token` is generated from cryptographically secure randomness and contains no sequential
database identifier or user data. Knowledge of the full URL is the only access requirement.

The page renders the event name and exactly one of these views:

- `queued` or `processing`: “Ищем ваши фотографии…” and automatic bounded polling;
- `cleanup_pending`: the same customer-facing progress state while selfie deletion is retried;
- `ready`: the immutable probable-match gallery;
- `no_face`: no acceptable face was found;
- `multiple_faces`: more than one acceptable face was found;
- `quality_rejected`: the face cannot produce a reliable query embedding;
- `search_unavailable`: no eligible gallery embeddings existed for the event snapshot; or
- `failed`: a stable, sanitized processing failure.

Every terminal page offers “Искать по другому селфи”, which returns to the event and creates a new
search and token on submission. A failed search is not mutated into a retry with different bytes.

The status endpoint exposes only the state and bounded presentation information required to update
the page. It does not return a query vector, gallery vectors, storage key, signed URL, internal
exception, or worker credential.

### Ready result

The ready page shows:

- the event name;
- “Возможные совпадения”;
- the number of matched photos;
- the number of published photos with eligible embeddings searched in the frozen candidate cohort;
- result cards in stored rank order; and
- the new-search action.

Cards reuse the current gallery presentation and lightbox behavior. A result from a free or paid
published event can open the existing original inline. For a paid event this access is authorized
only by a valid ready-search bearer token whose saved result contains that photo. It does not make
the normal paid-event gallery or a general photo URL publicly eligible.

An empty ready result is a successful search with “Совпадений не найдено”. It is distinct from
`search_unavailable`, which means there were no eligible embeddings to search.

## Product State

### Selfie search

One durable search record owns:

- immutable event identity;
- a unique public-token digest and token lookup identity;
- state;
- creation and state-transition timestamps;
- the processor contract, face model, embedding model, and threshold versions;
- bounded failure code when terminal;
- frozen eligible-photo and eligible-face counts;
- matched-photo count; and
- cleanup confirmation time.

The plaintext bearer token must not be stored in ordinary structured logs. Storing a lookup-safe
digest instead of the plaintext token is preferred where it does not complicate normal route
resolution.

The state machine is:

```text
queued -> processing -> cleanup_pending -> ready
                              |---------> no_face
                              |---------> multiple_faces
                              |---------> quality_rejected
                              |---------> search_unavailable
                              `---------> failed
```

All paths that received selfie bytes must pass through successful selfie deletion before a
customer-visible terminal state is published. Cleanup retry may retain the internal intended
terminal outcome while the public page continues to show progress.

Terminal search state and result membership are immutable. A new attempt with different selfie
bytes is a new search.

### Candidate cohort

At search creation Django freezes the identity of eligible gallery data:

- the event is currently published;
- the photo belongs to that event;
- the photo has a confirmed private original and remains eligible for product presentation;
- the face embedding belongs to the accepted successful result of the configured current
  `face_embedding` processor generation; and
- the embedding model and dimensions are compatible with the query contract.

Photos or embeddings that become eligible after cohort creation are not added. Failed, queued,
processing, stale, superseded, quality-rejected, or incompatible face data is excluded.

The snapshot must be sufficient to reproduce membership and result evidence without copying raw
gallery embeddings into the search record. PostgreSQL remains authoritative for photo identity,
processing truth, and the saved result.

### Result rows

Each stored result row contains only:

- the search identity;
- the matched photo identity;
- stable rank;
- the best accepted cosine distance for that photo; and
- the matched gallery face identity needed for bounded diagnostic evidence.

A search contains at most one row per photo. Result rows and their order become immutable with the
terminal `ready` transition.

## Components and Responsibilities

### Django

Django owns:

- published-event and upload eligibility;
- initial upload validation;
- temporary-object identity and retention state;
- durable search state and the frozen candidate cohort;
- worker job, lease, retry, and accepted completion semantics;
- validation of the returned query embedding;
- exact event-scoped cosine comparison;
- thresholding, photo deduplication, stable ranking, and result persistence;
- selfie deletion and the `ready` publication gate;
- bearer-link authorization; and
- result-photo presentation and media eligibility.

The web request that accepts the selfie performs no face detection or model inference. The worker
completion request may perform the bounded exact comparison inside Django because the event-scale
gallery contains only thousands of faces and the accepted baseline requires no specialized vector
engine. This choice must be revisited only after measured request time or cohort size violates a
documented bound.

### ML worker

The existing separately runnable worker gains one compatible selfie-query processor. It:

- claims work through the private versioned worker API and existing lease semantics;
- receives short-lived read authorization for exactly one temporary selfie object;
- decodes one bounded image;
- detects and quality-filters faces with the configured model contract;
- succeeds only when exactly one acceptable face remains;
- aligns that face and creates one finite normalized embedding;
- returns the embedding and bounded model/quality metadata in its protected completion callback;
  and
- releases decoded pixels and the query vector after callback completion.

The worker receives no PostgreSQL access, Django secret, permanent Object Storage credential,
gallery embedding set, or public result token. It does not persist the query vector or result.

### Object Storage

The selfie uses a separate private temporary prefix. Its object key is generated by Django and is
not a user filename. It is never promoted to the immutable photo-original prefix.

Django deletes the exact temporary object after accepting the worker outcome and completing or
rejecting search calculation. A lifecycle rule on the temporary prefix removes abandoned objects
after a short bounded interval if application cleanup never completes. The exact interval is an
operational configuration value recorded in the implementation plan; it may not be unbounded.

## Search Contract

### Query validation

The upload and decoded image must satisfy versioned limits for:

- accepted content type and actual decoded format;
- encoded byte size;
- pixel dimensions and decoded-memory estimate;
- exactly one acceptable face;
- minimum face size and configured quality gate; and
- finite normalized embedding of the configured dimension.

The implementation must reuse the same compatible detection, alignment, SFace embedding, and
normalization behavior used for gallery embeddings. It must not silently choose the largest face
when multiple acceptable faces exist.

### Exact ranking

For query vector `q` and compatible normalized gallery vector `g`, Django computes:

`cosine_distance(q, g) = 1 - dot(q, g)`

Only faces whose distance is less than or equal to the configured versioned acceptance threshold
are candidates. The threshold is an application/model contract, not a customer control.

For every photo, retain the candidate face with the smallest distance. Sort unique photos by:

1. ascending best distance; then
2. stable photo ID.

Persist that complete ordered list as the result snapshot. Direct face matches are the only source
of membership and ranking. Cluster membership, Peakshot identity, and indirect evidence do not add
or reorder photos.

The search calculation fails closed if query or gallery vectors contain non-finite values, have
incompatible dimensions, or do not match the declared model contract. One corrupt gallery vector
may be excluded with bounded evidence only if exclusion cannot cross event boundaries or make an
incompatible model appear compatible.

## Data Flow

1. A visitor submits one validated selfie from a published event page.
2. Django creates a private temporary object and the durable `queued` search, freezes the eligible
   event cohort, enqueues the selfie-query job, and redirects to the bearer URL.
3. The result page polls while the worker claims the job through the existing private API.
4. Django gives the current leased attempt a short-lived download grant for the exact selfie.
5. The worker downloads, decodes, validates exactly one face, computes one normalized embedding,
   and sends the bounded terminal callback.
6. Django validates the callback. For a valid query embedding, it loads only the frozen compatible
   event cohort, performs exact ranking in memory, and prepares the immutable result rows.
7. Django deletes the temporary selfie. It never stores the query vector.
8. Only after deletion is confirmed, Django atomically publishes the intended terminal state. For
   success it publishes the prepared rows and `ready`; for a domain or permanent processing error
   it publishes the corresponding terminal error.
9. The polling page observes the terminal state. A ready page resolves saved photo IDs through
   current event/photo publication rules and renders the remaining eligible photos in saved order.

Storage deletion and a PostgreSQL transaction cannot be atomic. Therefore an accepted callback and
prepared result must be idempotently recoverable while cleanup is pending. Duplicate worker
callbacks or cleanup retries must neither create a second result nor change ranks.

## Stable URL and Visibility Semantics

- The result URL has no expiry in this MVP.
- The URL remains resolvable after browser restart and can be shared directly.
- No session, cookie, login, or referrer ownership check is required.
- A token from one search cannot select another search, event, or photo.
- Search state, rank, score, and cohort never change after `ready`.
- A later unpublished event makes the result URL unavailable.
- A later unpublished, deleted, or otherwise ineligible photo is omitted at read time; remaining
  cards keep their saved relative order.
- A later republished photo does not silently re-enter an already rendered snapshot unless it is
  still an immutable member and current media eligibility permits it.
- Public pages and status responses use cache policy appropriate for bearer data and must not be
  shared through a public intermediary cache.

This is privacy by unguessable link, not private authenticated access. The UI must state that
clearly.

## Failure Semantics

- Initial file validation failure: stay on the event page; create no search and no object.
- Temporary object write failure: create no usable search; return a retryable upload error.
- No eligible embeddings in the frozen cohort: delete the selfie and publish
  `search_unavailable`.
- No acceptable face: delete the selfie and publish `no_face`.
- Multiple acceptable faces: delete the selfie and publish `multiple_faces`.
- Quality or embedding rejection: delete the selfie and publish `quality_rejected`.
- Temporary worker, network, or download failure: use ADR 0017 lease recovery and bounded retries.
- Permanent decode/model/contract failure: delete the selfie and publish sanitized `failed`.
- Temporary selfie deletion failure: retain the intended terminal outcome internally, expose
  progress, and retry cleanup; never expose `ready` or a terminal domain outcome first.
- Stale or conflicting worker completion: preserve the current attempt/result and existing
  immutable receipt/audit semantics.
- Missing or ineligible matched photo at read time: omit that card without reranking the rest.

Public failures must be actionable but must not include storage keys, signed query strings,
vectors, thresholds that aid abuse, worker identity, stack traces, or raw exception text.

## Privacy and Security Boundary

This MVP deliberately chooses a narrow, simple privacy model:

- the selfie is temporary and private;
- the query embedding is transient memory only;
- gallery embeddings remain private server-side processing data;
- the durable result is an unguessable, non-expiring public bearer resource;
- the durable result retains probable match identities and scores but no selfie bytes or query
  vector; and
- anyone with the link can view the result and, for this increment, receive matched originals from
  a published free or paid event.

CSRF protection applies to selfie submission. Public GET and polling routes are read-only. Secrets,
signed URLs, vectors, image bytes, and raw callbacks are excluded from structured logs and error
responses.

The feature provides the approved in-product notice but does not implement a separate consent
ledger, revocation flow, rate limiter, moderation workflow, or incident console. Those gaps do not
block this explicitly approved critical path. They must be revisited before authenticated private
results, named identity, cross-event search, broader biometric reuse, or material abuse evidence is
introduced.

## Compatibility and Evolution

- Existing event catalog, event detail, ingestion, gallery ordering, and free-event media URLs
  remain compatible.
- The paid-original exception is limited to a valid ready selfie result and does not activate the
  normal paid gallery.
- Existing photo embeddings remain immutable processing evidence. Search reads the accepted
  compatible generation and does not rewrite it.
- Processor, detector, embedding model, dimension, quality gate, and threshold versions are
  recorded with each search.
- Changing those versions affects only new searches.
- A later vector index may replace Django exact comparison while PostgreSQL remains authoritative
  for search state and saved result membership.
- Later thumbnails, watermarks, or entitlements may replace result-card media resolution without
  changing stable search URLs or result membership.
- A future retention or authenticated-access policy requires an explicit migration and user-facing
  compatibility decision for existing bearer links.

## Acceptance Criteria

1. Every published free or paid event page renders the selfie-search disclosure, one-file control,
   and primary action without changing the existing gallery behavior.
2. A valid JPEG or PNG submission creates one search and private temporary object, queues one
   compatible job, and redirects immediately to an unguessable stable event-scoped bearer URL.
3. Invalid type, empty content, exceeded byte limit, or exceeded decoded-image limit creates
   neither a search job nor a retained object and returns a specific correction message.
4. The stable page and polling endpoint expose queued/progress, cleanup, ready, and sanitized
   terminal error behavior without authentication or a second submission.
5. The worker has no database or permanent Object Storage credentials, reads only the exact
   temporary object under its current lease, and returns exactly one finite normalized query
   embedding or a stable domain failure.
6. Zero acceptable faces, multiple acceptable faces, quality rejection, no eligible event
   embeddings, and permanent processing failure each produce the specified distinct state.
7. Django compares the query only with the frozen compatible accepted embeddings of photos from
   the selected event; a face or photo from another event can never enter the result.
8. Exact cosine thresholding, best-face photo deduplication, distance ordering, and photo-ID
   tie-breaking produce a deterministic unique-photo snapshot.
9. The query embedding is never persisted in PostgreSQL, Object Storage, cache, log, report, or
   search row.
10. The raw selfie is deleted before `ready` or another terminal state becomes visible. A deletion
    failure keeps the page in progress and is idempotently recoverable; the temporary-prefix
    lifecycle bounds abandoned-object retention.
11. Duplicate callbacks, lease expiry, stale completion, and cleanup retry cannot duplicate,
    reorder, or replace an accepted result.
12. Reopening or sharing the same URL returns the same state and saved result order without
    recomputation, including after later photos or embeddings are added.
13. Unpublishing the event makes its result unavailable. Removing a result photo from eligibility
    hides only that card and retains the relative order of remaining cards.
14. A ready result for a published paid event can render and open only originals that belong to
    that saved result; the normal paid gallery and unrelated paid photo URLs remain unavailable.
15. The form and result page state that matching is probabilistic, the selfie is deleted, and
    anyone with the result link can view it.
16. Public responses and application logs expose no query/gallery vector, temporary storage key,
    signed URL, worker credential, raw callback, or unsanitized exception.
17. Critical Django, worker-contract, PostgreSQL, JavaScript polling, and responsive visual tests
    pass, and the pre-existing event gallery and media tests remain green.

## Architecture Reconciliation

The design conforms to:

- ADR 0001 by keeping product state, eligibility, ranking, and result publication in Django while
  using the existing specialized ML worker only for inference;
- ADR 0002 by keeping PostgreSQL authoritative for search state and immutable result membership
  while treating embeddings as derived processing data;
- ADR 0006 by keeping temporary selfie bytes in private Object Storage rather than a container
  filesystem or PostgreSQL;
- ADR 0013 by preserving confirmed photo-original ingestion and immutable final keys; the selfie
  uses a separate temporary prefix and is never promoted as a photo; and
- ADR 0017 by reusing Django-polled jobs, leases, short-lived exact-object read grants, bounded
  retries, and a worker with no database or permanent Object Storage credentials.

The design does not conform to ADR 0015's explicit rule that paid-event originals remain
unavailable to anonymous clients. The selected critical path introduces a narrower bearer-link
exception for matched photos in a published paid event. ADR 0015 must therefore be superseded
before implementation, while preserving its existing free-gallery rules and explicitly containing
the paid exception to saved selfie results.

The architecture also leaves face governance, temporary query embeddings, public result access,
retention, and the production search boundary open. A new accepted ADR must govern those durable
choices. It may acknowledge the deliberately deferred consent ledger, revocation, rate limiting,
moderation, and derivative-media work, but it must not present them as implemented safeguards.
