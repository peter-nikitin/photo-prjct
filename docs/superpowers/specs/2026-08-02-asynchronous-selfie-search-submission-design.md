# Asynchronous Selfie Search and Direct Ranking Hotfix

- **Status:** Implemented on staging
- **Date:** 2026-08-02
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md), public event-scoped
  selfie search and Django-polled worker boundaries
- **Related ADRs:** [ADR 0019](../../adr/0019-use-public-event-selfie-search.md)
- **ADR impact:** Conforms to ADR 0019

## Delivery Evidence

- Implemented and reviewed in [PR #80](https://github.com/peter-nikitin/photo-prjct/pull/80),
  merged as `a2da2a66c723a38263ae278035bfa17dca7ff229`.
- Direct in-memory cohort ranking was implemented in
  [PR #82](https://github.com/peter-nikitin/photo-prjct/pull/82), merged as
  `f356ea12fc97e36b2b750cbfaf654235bd459b41`.
- CI passed the complete Python, migration, JavaScript, and containerized visual-regression gates.
- Staging deployed the matching immutable web and worker images. The public health endpoint returned
  HTTP 200, both worker replicas and the web container had zero restarts, and the deployed Gunicorn
  timeout was 180 seconds.

## Outcome

A valid selfie submission must return the stable bearer result page without scanning the event's
gallery embeddings. The page immediately shows the existing queued/progress state while the
existing worker and Django complete the search asynchronously. New searches must rank the
compatible event cohort directly and persist only eligible counts and matched results, not one
intermediate row per eligible face.

The first hotfix addressed the observed large-event failure where synchronous creation of 56,183
candidate rows kept the submission request open beyond the reverse-proxy timeout and returned 504
instead of the result URL. Live follow-up showed that asynchronously persisting and rereading those
same rows still kept the result in `processing` for about 140 seconds. Direct ranking removes that
write and second cohort read.

## Scope

The hotfix changes submission timing and the representation of a new search's eligible cohort:

- submission validates and stores the selfie, creates the search and job, and returns the existing
  redirect;
- submission does not query `FaceEmbedding` or create `SelfieSearchCandidate` rows;
- after the worker returns one valid query embedding, Django loads the then-current compatible event
  cohort once, records eligible counts, performs the existing exact ranking in memory, and persists
  only the immutable result rows;
- new searches do not create `SelfieSearchCandidate` rows; searches that already have such rows use
  the legacy frozen-candidate ranking path;
- the existing cleanup gate deletes the selfie before publishing `ready` or another terminal
  state; and
- the worker callback timeout must cover the bounded asynchronous cohort/ranking operation without
  making the public submission wait for it.

No broker, new job type, schema migration, vector index, UI redesign, or ranking-algorithm change is
included.

## Data Flow

1. The visitor submits a valid selfie.
2. Django stores the temporary object and atomically creates a queued `SelfieSearch` and
   `SelfieSearchJob` with no candidate rows.
3. Django immediately redirects to the stable bearer result page, which renders the existing
   progress state and polls the existing status endpoint.
4. The current worker claims the queued job, reads the selfie, and returns a transient normalized
   query embedding.
5. Inside the accepted completion path, Django loads the compatible accepted face embeddings for
   the search's event once, records eligible photo/face counts, performs the existing deterministic
   exact ranking over that in-memory cohort, and prepares only the matched result rows.
6. Django deletes the temporary selfie and only then publishes the terminal state. The query
   embedding is never persisted.

The compatible cohort is a transaction-local input to one accepted completion attempt. Photos or
embeddings that become eligible after its cohort query do not enter that attempt. Before an outcome
is accepted, a retried attempt may observe a newer compatible cohort. Once cleanup succeeds and a
terminal result is published, saved result rows remain immutable. This intentionally replaces both
earlier timing rules that persisted the full cohort during submission or callback.

## Failure and Compatibility Semantics

- Upload validation and Object Storage failures retain their existing form errors and create no
  usable search.
- A queued search with no compatible embeddings remains valid; after worker completion it becomes
  `search_unavailable` through the existing cleanup gate.
- Ranking incompatibility, duplicate callbacks, stale leases, retries, and cleanup recovery retain
  their existing fail-closed and idempotent behavior.
- A callback transport timeout must not replace an already accepted durable result. The worker must
  have enough callback time for the supported large-event cohort.
- Existing searches that already contain frozen candidates remain processable and readable.
- Bearer authorization, event isolation, paid-result media authorization, saved result ordering,
  and terminal immutability do not change.

## Privacy Boundary

The worker still receives no database access, gallery vectors, permanent Object Storage
credentials, or public bearer token. Django remains authoritative. The selfie remains private and
temporary; its query embedding remains transient; no object key, vector, or signed grant enters a
public response or structured log.

## Acceptance Criteria

1. A valid submission creates the temporary selfie, queued search, and job and returns the existing
   bearer redirect without evaluating the compatible gallery-embedding queryset or creating
   candidate rows.
2. The redirected page renders the existing progress state and status polling immediately.
3. The accepted successful worker callback loads the compatible event-only cohort once, records the
   correct eligible counts, and runs the unchanged exact ranking without persisting candidate rows.
4. Empty cohorts, incompatible candidates, duplicate callbacks, lease loss, and cleanup retry keep
   their existing terminal and idempotency semantics.
5. The selfie is deleted before a terminal public state and the query vector is never persisted.
6. Existing searches with already-frozen candidates remain compatible.
7. Regression tests prove that submission and the accepted callback create no candidate rows for a
   new search while still producing the correct saved results.
8. Worker callback configuration no longer times out during the supported asynchronous large-event
   completion path.

## Rejected Alternatives

- Increasing only the public reverse-proxy timeout keeps the expensive operation in the customer
  request and does not provide fast feedback.
- Persisting one `SelfieSearchCandidate` per eligible face during callback was removed after live
  evidence showed 56,183 writes plus a second cohort read kept results processing for about 140
  seconds. The model remains only for compatibility with searches created before direct ranking.
- Adding a broker or a separate cohort-preparation job expands infrastructure and delivery time
  without improving the required critical path.
