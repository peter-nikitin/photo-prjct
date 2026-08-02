# 0022: Use numbered gallery pages

- Status: Accepted
- Date: 2026-08-02
- Deciders: project maintainers
- Supersedes: ADR 0020 only for its cursor-pagination follow-up
- Superseded by: none

## Context

ADR 0020 introduced bounded cursor pagination for normal event galleries and ready selfie-search
results. The current browser then progressively appends cursor pages through infinite scrolling.
That keeps each server response bounded but makes a visitor's position difficult to reload,
bookmark, share, or revisit.

The immediate product need is an ordinary page URL that restores the same location after a reload.
Both collections are already database-backed and bounded to 100 cards per response. Normal-gallery
photo changes during a browsing session are infrequent enough that stable insertion behavior is less
valuable than direct numbered navigation. Ready selfie-search membership and rank remain immutable;
only current presentation eligibility can remove rows.

## Decision drivers

- Make the current browsing location explicit in a reloadable and shareable URL.
- Provide direct backward and forward navigation without browser-only state.
- Keep HTML, database, and browser work bounded to 100 cards per response.
- Preserve selfie-result relevance order and every existing media-authorization boundary.
- Prefer the smallest server-rendered implementation for the current event workflow.

## Considered options

1. Replace gallery cursors with numbered offset pages for both collections.
2. Retain opaque cursors and add previous-cursor history.
3. Persist scroll or page position in browser storage while retaining infinite scrolling.

## Decision

Use server-rendered numbered offset pagination for normal event galleries and ready selfie-search
results. Each response contains at most 100 cards. The canonical first page needs no query
parameter; later pages use `?page=N` and expose previous and next page links plus the current and
total page numbers.

The normal gallery orders eligible photos by original filename, then photo ID. The ready
selfie-search result retains persisted rank, then photo ID. Pagination does not rerank a saved
result, change collection eligibility, or change any normal-gallery, bearer-result, media,
download, signing, privacy, or Object Storage boundary.

Infinite-scroll fetch and append behavior is removed. JavaScript remains progressive enhancement
for the existing lightbox and download actions, not pagination state.

This decision supersedes only ADR 0020's cursor-pagination follow-up. ADR 0020's direct media
delivery decision and all other consequences remain accepted. ADRs 0019 and 0021 remain unchanged.

## Consequences

### Positive

- Reloaded, bookmarked, and shared URLs return to an explicit numbered page.
- Visitors receive familiar previous and next navigation with visible position.
- Pagination no longer depends on JavaScript, cursor history, or browser storage.
- Server and browser collection work remains bounded.

### Negative

- Adding or removing an eligible photo can shift offset page boundaries between requests.
- A high page number requires the database to traverse an offset instead of seeking from a stable
  cursor key.
- The normal-gallery ordering contract changes from photo ID to original filename and photo ID.

### Follow-up

- Validate representative high page numbers against the expected approximately 20,000-photo event.
- Reconsider keyset navigation only if measured database latency becomes unacceptable or the event
  workflow begins changing gallery membership frequently during public browsing.

## Validation and rollback

Validate bounded first, middle, and final pages for both collections; invalid and out-of-range page
handling; normal-gallery filename ordering; retained selfie-result rank ordering; full-page previous
and next navigation; and absence of infinite-scroll fetches. Measure a representative final page of
an approximately 20,000-row gallery on staging.

Rollback restores ADR 0020's cursor links and progressive loading. Numbered URLs created while this
decision is active are presentation locators only and create no persistent state or data migration.

## References

- [Architecture](../architecture.md)
- [ADR 0019: Use public event-scoped selfie search](0019-use-public-event-selfie-search.md)
- [ADR 0020: Use signed direct Object Storage media delivery](0020-use-signed-direct-object-storage-media-delivery.md)
- [ADR 0021: Allow original download for authorized photos](0021-allow-original-download-for-authorized-photos.md)
- [Gallery pages and uncropped portraits design](../superpowers/specs/2026-08-02-gallery-pages-and-uncropped-portraits-design.md)
