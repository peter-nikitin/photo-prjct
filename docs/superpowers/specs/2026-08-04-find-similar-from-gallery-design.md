# Find Similar Photos from a Gallery Photo Design

## Status

Approved on 2026-08-04.

- Related product job: `PJ-008 — Customer — Find photos by face`.
- Related architecture: [`docs/architecture.md`](../../architecture.md), public event-scoped selfie
  search and gallery presentation.
- Related ADR: [ADR 0019](../../adr/0019-use-public-event-selfie-search.md).
- ADR impact: conforms to accepted
  [ADR 0024](../../adr/0024-use-gallery-face-as-search-query.md), which accepts an existing gallery
  face embedding as a second public query source under ADR 0019's event-scoped exact-ranking and
  immutable-bearer-result boundary. It does not change result media authorization, event
  publication rules, or existing gallery availability.

## Goal

When a customer finds themself in an existing event gallery, let them find probable matches of the
same person within that event without taking a screenshot and uploading it as a selfie. Deliver the
smallest end-to-end path needed to test the hypothesis: support only gallery photos with exactly
one current compatible accepted face.

## Scope

### Included

- A `Найти похожие фото` action below an existing gallery card when that photo has exactly one
  current compatible `kept` face embedding.
- Server-side revalidation of the published event, gallery-photo eligibility, event membership,
  and unique compatible face at submission time.
- Exact event-scoped ranking using the selected face's existing embedding as the query vector.
- An immutable ready `SelfieSearch` snapshot and the existing public bearer result page.
- The existing result pagination, feedback, media eligibility, and media authorization behavior.
- Critical-path tests and an update to `PJ-008`.

### Excluded

- Choosing a face when a photo contains multiple faces.
- Actions on photos with zero or multiple current compatible accepted faces.
- Uploading, cropping, or temporarily storing a query image.
- A selfie-query worker job or attempt, new ML inference, or Object Storage access.
- Cross-event or named-identity search.
- Opening galleries on event types where the current product does not render a gallery.
- New free-versus-paid branching, commerce behavior, entitlements, watermarks, or media policy.
- New result UI, ranking thresholds, model versions, ANN infrastructure, or search deduplication.

## Selected Design

### Gallery presentation

`GalleryPhoto` gains an optional application submission URL for similarity search. The gallery
query annotates or prefetches only enough current accepted face state to set this URL when exactly
one compatible `kept` embedding exists. Templates do not derive eligibility from counts or IDs.

An eligible card renders a secondary text action, `Найти похожие фото`, in the existing action row
below the image. The action is an HTML form using `POST` and CSRF protection. It remains separate
from the image link which opens GLightbox and from the original-download link. No JavaScript is
required for submission.

The presentation rule has no explicit `Event.access_type` branch. It applies to any card rendered
by an existing gallery surface. This increment does not make a currently hidden gallery visible.

### Submission and ranking

The submission URL identifies the current event and source photo. On `POST`, Django:

1. Resolves a published event and a source photo which belongs to that event and remains eligible
   for the existing gallery.
2. Resolves exactly one current compatible accepted `kept` detection and embedding for the source
   photo. Zero, multiple, stale, incompatible, malformed, or cross-event embeddings are rejected.
3. Uses that embedding as the query vector for the existing exact cosine-distance ranking against
   compatible accepted embeddings from the same event.
4. Creates one `SelfieSearch` with a generated public token, a configuration which records the
   gallery-photo query source and the same model, generation, dimensions, and threshold evidence
   used by ranking.
5. Saves the ordered, one-best-detection-per-photo `SelfieSearchResult` rows and marks the search
   `ready` in the same database transaction.
6. Redirects to the existing event-scoped bearer result URL.

The direct path creates no `SelfieSearchJob` or `SelfieSearchAttempt`, stores no query vector, uses
an empty temporary-object key, and records cleanup as already complete. The ready result remains
immutable and is not recomputed when photos or embeddings change.

The source embedding is a member of the ranked event cohort and has self-distance zero within
floating-point tolerance. The source photo must therefore be present in the saved result. Failure
to produce it aborts the transaction as an invariant violation rather than publishing a surprising
result.

### Result and authorization boundaries

The existing result view, pagination, feedback, inline media, and download routes are reused.
Result copy continues to describe probable matches rather than identity. The new query source does
not add an `access_type` condition and does not broaden current result membership or media rules.

Search remains strictly scoped to the source photo's current event. It never searches another
event, even if compatible face embeddings exist there. The public result remains a non-expiring
bearer resource under the existing ADR 0019 boundary.

### Failure semantics

- Non-POST requests use the route's normal method rejection.
- An unpublished or mismatched event, ineligible photo, or zero/multiple/incompatible source face
  returns `404` and creates no search.
- Invalid vector or ranking data, failure to include the source photo, or a database persistence
  failure returns a sanitized `503` and leaves no partial search or result rows.
- A retry is independent and may create a new immutable bearer result. Idempotency and result reuse
  are outside this hypothesis test.

## Minimal Validation Contract

Tests protect the changed critical path and realistic regressions:

- gallery presentation exposes the action for exactly one current compatible accepted `kept` face
  and omits it for zero, multiple, stale, rejected, incompatible, or malformed faces;
- the rendered action uses `POST`, CSRF protection, accessible text, and does not replace the
  lightbox or download actions;
- submission rejects forged cross-event, unpublished, ineligible, or stale source photos before
  creating state;
- ranking is limited to the current event, keeps one best detection per photo, preserves the
  existing distance ordering, and necessarily includes the source photo;
- the committed search is immediately `ready`, has immutable result rows and a usable existing
  bearer URL, and has no worker job, attempt, temporary object, or stored query vector;
- an injected ranking or persistence failure rolls the transaction back and returns sanitized
  failure behavior;
- focused existing result-view and media-authorization tests remain green.

Broad visual redesign, exhaustive event-type matrices, worker tests, Object Storage tests, and
full-suite coverage are not required because those paths do not change.

## Product Job Update

Keep `PJ-008` as the single face-search job and broaden its wording and evidence. The job covers two
reference sources within one event: an appropriate uploaded selfie and an existing gallery photo
with exactly one recognized person. The status changes only when implementation and verification
provide the evidence required by `docs/product-jobs.md`.

## Acceptance Criteria

- Every currently rendered gallery card with exactly one current compatible accepted face offers
  `Найти похожие фото`; other cards do not.
- Activating the action requires no screenshot, upload, temporary image, or new ML inference.
- A successful submission redirects directly to the existing ready result experience containing
  probable matches only from the current event.
- The source photo is present in the immutable saved result.
- The direct path persists no query vector and creates no worker job or temporary storage object.
- Current gallery availability and result media authorization remain unchanged and contain no new
  free-versus-paid decision.
- `PJ-008` records the delivered gallery-photo reference path with focused test evidence.
