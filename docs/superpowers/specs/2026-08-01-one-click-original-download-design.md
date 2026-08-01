# One-click Original Download Design

## Status

Proposed for review on 2026-08-01.

- Related architecture: [`docs/architecture.md`](../../architecture.md), current gallery-media
  delivery, public event-scoped selfie-search results, and proposed commerce boundary.
- Related product jobs: `PJ-005 — Visitor — Browse an event gallery` and `PJ-008 — Customer — Find
  photos by face`.
- Related specifications:
  [`2026-07-18-event-photo-gallery-design.md`](2026-07-18-event-photo-gallery-design.md),
  [`2026-07-30-public-selfie-search-design.md`](2026-07-30-public-selfie-search-design.md), and
  [`2026-07-31-event-media-direct-delivery-and-pagination-design.md`](2026-07-31-event-media-direct-delivery-and-pagination-design.md).
- Related ADRs: [ADR 0019](../../adr/0019-use-public-event-selfie-search.md) and
  [ADR 0020](../../adr/0020-use-signed-direct-object-storage-media-delivery.md).
- ADR impact: **Conforms to ADR 0021.** ADR 0021 authorizes attachment delivery for an original
  that an existing gallery or ready-result route already authorizes while preserving ADR 0019's
  result-membership boundary and ADR 0020's Django-authorized, short-lived direct Object Storage
  transport.

## Goal

Let a visitor download the original of any photograph already presented in an event gallery or a
ready selfie-search result with one click. Keep the change small: reuse existing media eligibility
and direct-delivery boundaries, add no commerce state, and add no new free-versus-paid branch.

## Scope

### Included

- A subdued icon-only original-download action below and right-aligned with every rendered gallery
  or search-result card.
- Removal of the current `Фото <photo-id>` caption below cards.
- A text action, `Скачать оригинал`, in GLightbox's built-in bottom description area.
- A stable application download URL which reuses the authorization context of the containing
  normal gallery or ready selfie-search result.
- Direct attachment delivery of the original through a short-lived signed Object Storage URL.
- The minimum critical-path server, template, and JavaScript checks needed to protect the new
  behavior.
- A future-work note for replacing direct download with a cart action when commerce becomes real.

### Excluded

- Cart, selection state, pricing, orders, payments, purchased-download entitlements, or watermarks.
- New free-versus-paid branching in presentation or download code.
- Opening a normal paid-event gallery or changing existing gallery/result eligibility.
- Download analytics, rate limits, batch or ZIP downloads, alternate sizes, and download history.
- A custom GLightbox toolbar, fork, or replacement.
- Broader gallery-card redesign or unrelated media hardening.

## Selected Design

### Presentation contract

`GalleryPhoto` gains one stable application download URL alongside its existing small- and
large-preview URLs. The same presentation value is used by the normal gallery and ready
selfie-search result builders, so their card markup stays aligned.

Each rendered card has two separate actions:

1. The image remains the link which opens GLightbox.
2. A download link appears in a row immediately below the image, aligned to the right.

The existing photo-ID caption is removed. The download link shows only the packaged download icon.
It uses a transparent background and muted foreground color so it remains secondary to the photo.
Its interactive area is at least 44 by 44 CSS pixels. Its accessible name and native hover hint are
`Скачать оригинал`. Activating it follows the download URL and must not open GLightbox.

The card supplies the same download URL to GLightbox through the library's supported description
content. GLightbox renders `Скачать оригинал` in its built-in bottom description area. The
application does not create a custom overlay or control bar. Keyboard activation follows the same
link as pointer activation.

Cards added by progressive pagination contain the complete server-rendered actions. The existing
GLightbox reload after an append makes the newly added description action available without a
second pagination-specific implementation.

### Authorization and data flow

The normal gallery download URL is addressed by event slug and photo ID. The ready-result download
URL additionally carries the existing public result token. These deterministic application URLs,
not signed URLs or permanent object keys, are the only download values rendered into HTML.

On a download request Django:

1. Applies the same normal-gallery or ready-result lookup already used for the corresponding
   `preview-large` media request.
2. Selects the photo's original object.
3. Creates a short-lived signed exact-object GET URL whose response disposition is attachment.
4. Redirects the browser to Object Storage; Django does not proxy the image body.

No new `access_type` condition is introduced. Consequently, the action exists for every card the
current presentation rules render. The normal gallery remains limited by its existing eligibility
rules. A valid ready selfie-search result retains its existing free-or-paid result membership
semantics. This increment does not attempt to establish a future purchase entitlement boundary.

The attachment filename is deterministic and contains no submitted storage filename or object key:

```text
findme-photo-<photo-id>.<jpg|png>
```

The extension comes from the already validated original media type. The signing interface accepts
the response disposition as an explicit download concern; ordinary `preview-small` and
`preview-large` signing remains inline and unchanged.

### Failure semantics

- An invalid route variant, unavailable event or result, ineligible or non-member photo, or missing
  original returns the existing sanitized not-found response.
- A temporary Object Storage failure returns the existing sanitized `503` response.
- The rendered page never contains a signed URL, permanent key, or credential.
- A failed download does not change gallery, result, photo, order, or processing state.

## Minimal Validation Contract

Tests protect only the changed critical path and existing authorization boundary:

- the shared gallery presentation value builds the stable download URL for normal-gallery and
  ready-result contexts;
- gallery and ready-result templates remove the photo-ID caption and render the accessible
  icon-only card action plus the GLightbox description action;
- an authorized download signs the selected original as an attachment and redirects without an
  image body;
- existing ineligible-gallery and non-member-result lookups still fail before signing;
- the focused gallery JavaScript test confirms GLightbox continues to initialize and progressive
  pagination reloads appended cards.

No exhaustive free/paid matrix, commerce test double, new visual snapshot matrix, or speculative
download-state coverage is required.

## Future Commerce Trigger

Implementation creates `docs/future-work/2026-08-01-paid-photo-cart-action.md` with:

- **Observed gap:** every currently rendered card offers direct original download; there is no
  paid-photo cart or purchase entitlement.
- **Why it is non-blocking:** this increment's accepted requirement is one-click download, and the
  product has no implemented commerce path to substitute.
- **Revisit trigger:** implementation begins for commerce or the first normal paid event is prepared
  for publication, whichever happens first.
- **Likely scope:** replace the direct action for paid media with add-to-cart behavior, define
  entitlement-backed purchased downloads, decide ready-result paid behavior, and add the realistic
  free/paid validation matrix at that time.

## Acceptance Criteria

- Every photograph rendered in a normal gallery or ready selfie-search result has a one-click
  original-download action below the image at the right.
- The card has no visible `Фото <photo-id>` caption.
- The card action is icon-only, visually subdued, keyboard reachable, and accessibly named.
- Opening the image still opens GLightbox, whose built-in bottom description contains one
  `Скачать оригинал` action.
- Activating either download action produces an attachment transfer of that photo's original with
  the deterministic safe filename.
- Download reuses current gallery or ready-result authorization and adds no explicit free/paid
  decision.
- Django does not stream the original or expose a signed URL, permanent object key, or credential
  in rendered content.
- The future commerce boundary is recorded with a concrete revisit trigger and is not implemented
  in this increment.
