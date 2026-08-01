# Compact lightbox download action

- Status: Approved on 2026-08-01
- Related architecture: `docs/architecture.md` current gallery and original-download behavior
- Related ADRs: ADR 0021
- ADR impact: None — reversible implementation detail

## Outcome

The original-download action in an open GLightbox uses the same subdued icon-only visual language
as the action below a gallery card. Opening a photo no longer creates a large white description
area or a blue underlined text link.

## Design

Keep GLightbox's built-in description slot and the existing stable application download URL. Render
the shared download SVG inside an icon-only link with `aria-label` and `title` set to
`Скачать оригинал`. Scope CSS to the GLightbox container: collapse the description wrapper to a
compact transparent action row aligned right, and reuse the gallery action dimensions, muted
color, hover surface, and visible keyboard focus treatment.

Do not add an overlay, a custom GLightbox toolbar, new JavaScript lifecycle behavior, download
authorization changes, or free-versus-paid branching.

## Acceptance criteria

- Gallery and ready selfie-result lightboxes show a subdued download icon without visible link text.
- The description area has no large white block and keeps the action at least 44 by 44 CSS pixels.
- The action remains a keyboard-focusable link with an accessible name and the existing download URL.
- Card download actions and other GLightbox controls retain their existing behavior.
- Desktop and mobile visual baselines cover the intentional lightbox appearance.
