# Gallery Face Selector Design

## Status

Approved on 2026-08-05.

- Related product job: `PJ-008 — Customer — Find photos by face`.
- Related architecture: [`docs/architecture.md`](../../architecture.md), public event-scoped face
  search, accepted face-processing evidence, and gallery presentation.
- Related ADRs:
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md) and
  [ADR 0024](../../adr/0024-use-gallery-face-as-search-query.md).
- ADR impact: **Conforms to ADR 0024**. ADR 0024 is expanded in the same unmerged delivery to cover
  explicit selection of one stored face from a multi-face gallery photo while retaining its
  event-isolation, direct-ranking, immutable-result, and no-query-vector boundaries.

## Goal

Let a customer start the existing event-scoped probable-match search from any usable recognized
face in a gallery photo. Keep the one-face path to one click, and make the person explicit when a
photo contains multiple faces.

The interaction takes visual direction from the compact detected-person controls on the
[ZebraSnap gallery](https://zebrasnap.com/en/Bernimb_fotografia/sabana-2/photos), while following
FindMe Photo's existing card footer, result, authorization, and accessibility contracts.

## Evidence and Existing Capability

Accepted face-processing results already persist one `PhotoFaceDetection` and one compatible
`FaceEmbedding` per kept face. A detection contains a stable ID, face index, bounding box, and, for
the current preview-backed generation, the exact `preview-small-v1` coordinate space and pixel
dimensions. The accepted preview is already available through the gallery's authorized small-media
route.

The restored staging database contained 10,682 photos with more than one kept embedding and 50,483
detections on those photos. Every one had a bounding box; 50,481 used the explicit
`preview-small-v1` coordinate space. This is sufficient to render face thumbnails and select a
stored embedding without new inference or a face-crop asset pipeline.

## Scope

### Included

- Replace the one-face text action under gallery cards with compact circular face thumbnails.
- Open a queued result tab immediately when a photo has exactly one usable face.
- Show an overlapping face stack when a photo has multiple usable faces.
- Show all usable faces in a compact chooser anchored to that card's footer.
- Start queued event-scoped ranking from the explicitly selected detection in that result tab.
- Use the existing preview image and persisted bounding boxes for visual crops.
- Preserve the existing result page, bearer token, ranking model and threshold, result media
  authorization, pagination, and feedback behavior.
- Cover desktop, mobile, keyboard, no-JavaScript, and realistic stale-data failure paths.

### Excluded

- New face detection or embedding inference.
- Persisted or Object Storage face-crop files, a crop endpoint, or a crop cache.
- Cross-event search, named identity, face clustering, or automatic selection of a person.
- Changes to ranking, the `0.363` threshold, model generations, or result deduplication.
- A new free-versus-paid branch or a change to gallery/result media policy.
- A global modal, backdrop, lightbox integration, or changes to the result UI.

## Selected Design

### Usable face contract

A face is presentable and selectable only when all of the following remain true:

- the source event is published and the photo remains eligible for the current gallery;
- the detection belongs to the photo's current compatible accepted successful face-processing
  attempt and has status `kept`;
- it has one finite normalized embedding of the configured model and dimensions;
- its geometry contains a valid bounding box in the explicit `preview-small-v1` coordinate space,
  with matching positive preview dimensions.

Legacy detections without an explicit preview coordinate space are not shown. Zero usable faces
produce no face control. Face order is deterministic by persisted `face_index`, then detection ID.
The gallery resolves faces for the already paginated photo set in bounded batched queries; it must
not issue one query per card or hydrate vectors for presentation.

The server derives a square crop rectangle from the bounding box with 20% padding around the
largest face dimension, then clips it to the preview bounds. It exposes only normalized crop
geometry and the opaque detection ID to the template. Embedding vectors, storage keys, processing
payloads, and signed Object Storage URLs never enter the HTML.

### Card footer controls

The face control occupies the left side of the existing `.gallery-card-actions` footer. The
original-download control remains independently available on the right. Face controls are siblings
of the image/lightbox link, so clicking or focusing them never opens GLightbox.

Each crop is rendered as a circular thumbnail from the card's existing `preview-small` application
URL. The browser may reuse that resource; no distinct face media request contract is introduced.
The circle has a visible boundary against light and dark photos and a minimum 44 by 44 pixel
interactive target even when the visible crop is smaller.

Behavior by usable face count:

- **One:** render one circular submit button. Its accessible name is
  `Найти похожие фото этого человека`. Activating it opens a new tab and submits the selected
  detection there immediately.
- **Two or three:** render all face circles as an overlapping, left-to-right stack. The stack is one
  chooser trigger; clicking any visible part opens the chooser and does not start a search.
- **More than three:** render the first two face circles and a third summary circle labelled
  `+ N`, where `N` is the number of remaining faces (`total - 2`). The entire stack opens the
  chooser.

Overlap may not make the trigger's 44-pixel target or keyboard focus indicator unreachable. The
summary circle is text-only and is never interpreted as a face selection.

### Anchored face chooser

For multiple faces, the stack is the `<summary>` of a card-local `<details>` disclosure. Its
content is a compact `role="dialog"` chooser labelled `Кого искать?`. This provides a functional
keyboard and no-JavaScript path while JavaScript adds the expected popover behavior.

The chooser is positioned relative to the face-control area in the card footer and opens upward,
over the lower edge of the preview, with a small visual pointer to its trigger. It is not centered
on the page and has no backdrop. It is constrained to the card and viewport width. At the one-column
mobile breakpoint it remains anchored to the same footer instead of becoming a bottom sheet.

The chooser contains a three-column grid of all usable faces in deterministic order. Every tile is
a separate CSRF-protected submit button with the accessible name
`Найти похожие фото человека N`. The grid displays at most three rows before becoming vertically
scrollable; a maximum of 32 faces is already enforced by the accepted processing contract. A tile
uses the same padded crop rule as its footer thumbnail.

Opening keeps focus on the stack, so disclosure does not change the page's scroll position or imply
that the first face is selected. Escape and a click outside close the chooser and keep or return
focus to the stack. Opening another chooser closes the previous one. Selecting a tile opens a new
tab and submits the form there; no intermediate selected state or confirmation button is required.
These enhancements must not remove the native `<details>` no-JavaScript path.

### Selected-face submission and queued ranking

The POST contract identifies the published event, source photo, and selected detection. The
existing gallery-photo submission route and service evolve from requiring a unique source face to
requiring the exact selected face. Presentation is never authority.

At gallery-form submission, Django:

1. Re-resolves the published event and current gallery-eligible source photo.
2. Re-resolves the selected detection through the same current compatible accepted predicate used
   by gallery presentation and candidate ranking.
3. Verifies the detection belongs to the source photo and event and that its vector remains usable.
4. Creates a queued `SelfieSearch` with no result rows, worker job, temporary media, or persisted
   query vector, then redirects the new tab to its bearer result page.

The queued result page exposes a CSRF-protected nested POST form only for a gallery-origin queued
search. Its browser controller retries rejected and non-success requests with a bounded delay and
no parallel request; the existing status polling remains responsible for terminal reload. A visible
no-JavaScript submit control starts the same server-rendered POST. Inside the locked process boundary,
Django:

1. Revalidates the current published event, gallery source, selected detection, and usable vector.
2. Uses the selected embedding transiently for exact ranking against the current event cohort.
3. Requires the selected detection's source photo to appear in the ranked result.
4. Atomically publishes the immutable ready `SelfieSearch` and ordered rows. A repeated process
   request is a no-op; a source that turns stale after queued creation publishes a terminal
   unavailable result without rows, while ranking/invariant failure publishes terminal failure.
   Database failure remains queued for browser retry.

The stored search configuration records the gallery query kind, source photo ID, and source
detection ID plus the existing model/generation/threshold evidence. It does not store the vector,
bounding box, landmarks, filename, object key, or face crop.

As before, this path creates no temporary media object, `SelfieSearchJob`, worker attempt, or
cleanup work. Ranking remains strictly limited to the source event and keeps one best matching face
per result photo.

### Failure semantics

- Non-POST submissions use the route's normal method rejection.
- A forged, cross-event, rejected, incompatible, malformed, or no-longer-current detection at
  submission returns the existing sanitized `404` and creates no search. If it becomes stale after
  queued creation, processing publishes a terminal unavailable result without rows.
- Ranking/vector/invariant failure after queued creation publishes a terminal failure without rows;
  database persistence failure returns body-free `503` and leaves the queued search retryable.
- A preview that cannot be loaded follows the existing gallery media `404`/`503` behavior; the UI
  does not fall back to originals or create a new media authorization path.
- If JavaScript fails, the one-face POST and multi-face native disclosure/forms remain usable.

## Privacy, Authorization, and Compatibility

The selector exposes crops only from a photo already visible in the current gallery and uses the
same authorized preview URL. It does not expose biometric vectors or widen media access. Any public
visitor who can access the gallery may select one of its usable faces and create a bearer result;
this is the deliberate public biometric-access expansion accepted by ADR 0024.

Existing uploaded-selfie searches and already-created gallery-origin bearer results remain
readable without a compatibility layer. New one-face and multi-face searches share one selected-
detection submission contract; the obsolete unique-face-only branch is removed rather than kept as
a fallback.

## Validation Contract

Focused behavior tests must prove:

- zero usable faces render no control;
- one usable face renders one direct POST control for its detection;
- two and three usable faces render the complete overlapping stack and chooser;
- four or more usable faces render two crops plus the exact `+ N` remainder and all faces in the
  chooser;
- faces are ordered deterministically and crop geometry is padded, square, normalized, and clipped;
- legacy/no-coordinate, stale-generation, rejected, malformed, and cross-event detections are not
  presented or accepted;
- page-level face loading stays bounded for a 100-photo gallery page and does not load vectors for
  presentation;
- selecting each face queues the exact source without a result, then uses that exact embedding in
  the locked event-scoped process operation, includes the source photo, and preserves atomic
  ready-result semantics;
- forged IDs fail closed with no persisted search, vector, job, attempt, temporary object, or crop;
- face controls do not open the lightbox and the download action remains unchanged;
- chooser focus, Escape, outside click, single-open behavior, and the no-JavaScript path work;
- desktop and mobile visual baselines cover one face, two faces, more than three faces, and an open
  anchored chooser without viewport overflow.

## Product Job Update

Keep `PJ-008` as the single face-search job. Broaden its gallery-photo evidence from exactly one
recognized person to an explicitly selected usable person when several are present. Its status may
change only when implementation, local verification, deployment, and customer evidence satisfy the
registry's existing rules.

## Acceptance Criteria

- Every rendered gallery card with at least one current compatible accepted face and valid preview
  geometry shows the compact face control; cards with none do not.
- One face opens the queued bearer result in one activation; the result tab starts processing it.
- Multiple faces open the anchored chooser; selecting any tile starts the search from that exact
  detection.
- More than three faces show exactly two crops and `+ N`, while the chooser contains every usable
  face.
- No screenshot, upload, new inference, face-crop object, crop endpoint, worker job, temporary media,
  or persisted query vector is introduced.
- Search and results remain limited to the current event and preserve existing bearer and media
  authorization behavior.
- The controls are keyboard-operable, usable without JavaScript, and visually contained on desktop
  and mobile.
- ADR 0024 explicitly accepts public selection among multiple stored gallery faces.
