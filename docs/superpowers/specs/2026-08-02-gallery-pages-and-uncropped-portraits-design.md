# Gallery Pages and Uncropped Portraits Design

## Status

Approved on 2026-08-02.

- Related architecture: [`docs/architecture.md`](../../architecture.md), current gallery and public
  selfie-search presentation boundaries.
- Related product jobs: `PJ-005 — Visitor — Browse an event gallery` and `PJ-008 — Customer — Find
  photos by face`.
- Related specifications:
  [`2026-07-31-event-media-direct-delivery-and-pagination-design.md`](2026-07-31-event-media-direct-delivery-and-pagination-design.md)
  and [`2026-07-30-public-selfie-search-design.md`](2026-07-30-public-selfie-search-design.md).
- Related ADRs: [ADR 0019](../../adr/0019-use-public-event-selfie-search.md),
  [ADR 0020](../../adr/0020-use-signed-direct-object-storage-media-delivery.md), and
  [ADR 0021](../../adr/0021-allow-original-download-for-authorized-photos.md).
- ADR impact: **Supersedes ADR 0020 for the pagination choice only.** A new ADR must replace its
  cursor-pagination follow-up with numbered offset pages while preserving every media-delivery,
  authorization, privacy, and selfie-result boundary in ADRs 0019, 0020, and 0021.

## Goal

Make portrait photos fully visible in gallery cards, place normal-gallery photos in filename order
as a low-cost approximation of photographer grouping, and make a visitor's location durable in the
URL through ordinary numbered pages.

## Scope

### Included

- The normal event gallery and ready selfie-search result card presentation.
- Numbered server-rendered pages for both collections.
- Original-filename ordering for the normal event gallery only.
- Existing persisted relevance ordering for ready selfie-search results.
- Removal of automatic infinite-scroll loading.

### Excluded

- An explicit photographer model, photographer heading, or photographer filter.
- Changes to selfie-search ranking, saved-result membership, or media authorization.
- Changes to preview generation, image files, Object Storage, or download behavior.
- Browser persistence through local storage or a client-only navigation state.

## Selected Design

### Image presentation

Every event-gallery and ready-result tile retains the current fixed card geometry. The image is
scaled down with `object-fit: contain` inside that geometry, so the entire portrait or landscape
frame remains visible without cropping. Unused space uses the existing neutral card background.
Opening a tile in GLightbox and downloading its original retain their current behavior.

### Normal-gallery ordering

Eligible normal-gallery rows are ordered ascending by `original_filename`, then ascending by photo
ID as a deterministic tie-breaker. The filename is stored with the confirmed photo and is not
shown as a new public label. This is an intentionally inexpensive approximation: it does not claim
or persist explicit author identity.

### Selfie-result ordering

Ready selfie-search members retain their persisted ascending `rank`, then photo ID tie-breaker.
Pagination must neither rerank them nor replace relevance order with filename order.

### Pagination

Both collections use Django server-side numbered pagination with a fixed page size of 100. The
first page uses the canonical URL without a required query parameter; later pages use `?page=N`.
The rendered navigation contains:

- a previous-page link when one exists;
- the text `Страница N из M`; and
- a next-page link when one exists.

Reloading, bookmarking, or sharing the URL therefore returns the visitor to the same numbered page.
A missing page parameter selects page 1. A non-integer, zero, negative, or out-of-range page returns
a sanitized `404` through Django's paginator semantics.

The page replaces the current cursor and infinite-scroll contracts. The browser never appends a
subsequent page into the current document. The gallery JavaScript remains responsible only for
GLightbox and the existing original-download action.

The event-detail page has no other supported query parameters. The ready-result page likewise has
no query state that pagination must preserve. Navigation links therefore emit only their target
`page` parameter.

## Failure and Compatibility Semantics

- Pagination does not change collection eligibility or any media authorization check.
- If photos are added or removed while a visitor browses, offset page boundaries may shift on the
  next request. This is accepted for the current event workflow and does not justify cursor state or
  browser persistence.
- The existing no-JavaScript anchor behavior becomes ordinary full-page navigation.
- Existing stable application media URLs, bearer-token isolation, signed redirects, and original
  downloads remain unchanged.
- Empty collections keep their existing empty states and render no pagination controls.

## Alternatives Rejected

### Cursor links with previous-page history

Cursor pagination is stable under concurrent inserts but does not naturally expose a numbered page
or direct backward navigation. Adding cursor history would retain opaque URLs and more state than
the requested workflow needs.

### Browser-stored scroll or page state

Local storage or history-state restoration depends on JavaScript and is weaker for reloads, shared
links, and multiple tabs than a direct numbered URL.

### Filename ordering for selfie results

This would discard the primary meaning of the search result: descending match relevance. The ready
result therefore retains its persisted rank order.

## Acceptance Criteria

1. Portrait and landscape photos are fully visible without cropping in both normal-gallery and
   ready-result cards at desktop and mobile widths.
2. The normal gallery orders eligible photos by original filename and then photo ID.
3. Ready selfie-search results retain persisted rank and photo-ID ordering.
4. Each response renders no more than 100 cards and exposes numbered full-page navigation when more
   rows exist.
5. Reloading a URL with `?page=N` returns the same numbered page while the underlying collection is
   unchanged.
6. Invalid and out-of-range page values return sanitized `404` responses.
7. Infinite-scroll observation, fetch, append, and lightbox-refresh behavior is absent.
8. Empty states, GLightbox access, authorized original download, stable media URLs, and gallery or
   bearer authorization continue to work.
9. Focused Django, JavaScript, contract, and desktop/mobile visual regression checks pass.
