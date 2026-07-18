# Event Photo Gallery Design

- Date: 2026-07-18
- Status: Approved design
- Owner: project maintainer
- Related architecture: [System architecture](../../architecture.md)
- Visual reference: [Photobank prototype PR](https://github.com/peter-nikitin/photo-prjct/pull/1/files)

## Outcome

The public event-detail page lists photos that completed the photographer upload flow and opens
them in a keyboard- and touch-accessible lightbox. The Django template receives presentation
objects that already know which media to use for the card and enlarged view. Adding generated
thumbnails later does not require changing the event-detail view or template contract.

## Scope

### In scope

- Uploaded photos belonging to a published event.
- A responsive gallery based on the simple card grid from the original photobank prototype.
- A separate immutable application model created from `Photo` for public gallery rendering.
- Stable application media URLs without exposing permanent object keys.
- The original as both card media and enlarged media until derivatives exist.
- A locally served GLightbox 3.3.1 lightbox with keyboard navigation, touch gestures, zoom, and
  close.
- An accessible empty state when the event has no displayable uploaded photos.
- Focused model/service, view/template, JavaScript, accessibility, and visual regression tests.

### Out of scope

- Search, filtering, recognition, selection, pricing, checkout, and downloads.
- Derivative generation or new derivative database fields.
- Displaying legacy `Photo.src` rows.
- Making the private bucket or permanent object keys public.
- A public media API independent of the event-detail page.
- Restoring the removed `src/proto` tree or loading UI assets from a CDN.

## Considered approaches

### Immutable gallery presentation model — selected

An application service converts an uploaded `Photo` into a `GalleryPhoto`. The presentation object
contains stable photo identity and separate card and enlarged media values. The template only
renders this contract and does not inspect storage fields or choose between originals and future
derivatives.

This keeps storage and media-policy decisions out of the ORM model and template while leaving a
clear seam for derivative selection and future cart integration.

### Computed properties on `Photo` — rejected

Properties would reduce the initial amount of code, but they would couple the persistence model to
private-storage signing, public media policy, and presentation-specific variants. Those concerns
will change independently once derivatives and commerce exist.

### Template tag media selection — rejected

A template tag could hide URL selection from the page, but it would move application logic into the
rendering layer, make storage failures less explicit, and provide a weaker typed boundary for tests
and future cart integration.

## Architecture

```text
Event detail view
    |
    +-- query uploaded Photo rows for the published event
    |
    +-- GalleryPhotoFactory
            |
            +-- stable preview and large application URLs
            |
            +-- GalleryPhoto(photo_id, preview_media, large_media, alt)
    |
    +-- event_detail.html -> GLightbox markup

Browser -> public photo-media endpoint -> PublicMediaResolver -> private storage stream
```

The view owns event visibility and query composition. The factory owns conversion from persistent
photo data into public presentation data. A dedicated media endpoint owns delivery, while its
resolver chooses the actual storage variant. The template owns markup only.

## Presentation contract

`GalleryPhoto` is an immutable application value rather than a Django model. Its public contract is:

- `photo_id`: stable `Photo` identity;
- `preview_media`: a `GalleryMedia` for the list/card image;
- `large_media`: a `GalleryMedia` for the lightbox image;
- `alt`: useful accessible text that does not expose filenames or storage keys.

`GalleryMedia` contains the stable browser URL. It may gain responsive-source metadata when
derivatives exist, but the initial lightbox contract does not require intrinsic dimensions that the
current `Photo` model cannot provide.

For the initial implementation, both media values address the same application endpoint with an
explicit `preview` or `large` variant. The endpoint resolver maps both variants to the original.
When a thumbnail field or derivative record is added, only the resolver changes: it selects the
thumbnail for `preview` and retains the appropriate larger variant for `large`.

Neither value object exposes `original_key`. URLs are opaque, short-lived capabilities.

## Future cart boundary

`GalleryPhoto.photo_id` is the only trusted bridge to commerce. A later cart adapter may convert a
gallery item into a cart reference containing that identity, but cart creation must reload the
authoritative `Photo` and re-evaluate publication, availability, access type, price, and permission
server-side. Media URLs, alt text, and any browser-supplied price are never trusted cart data.

No cart class or conversion method is added in this increment because the cart domain does not yet
exist. The stable identity and separate presentation model keep that conversion possible without
coupling the gallery to a speculative cart schema.

## Photo eligibility and ordering

The gallery query includes only rows that:

- belong to the requested published event;
- have a non-null `original_key`;
- belong to a free event while the original is the only available media; and
- represent a completed upload according to the current `Photo` row-shape contract.

Legacy rows with `src` are never rendered. Photos use the model's stable default ordering by ID for
this increment. Pagination and custom chronological ordering remain out of scope.

If one photo cannot be resolved into safe public media, the factory omits that item and records the
failure through normal application logging without exposing storage details to the visitor. A total
resolver failure still renders the event and its empty gallery state rather than returning a broken
page.

Paid-event photos remain absent until a watermarked preview exists; the gallery never exposes a paid
original as a temporary shortcut.

## Private-media delivery

The public media endpoint is addressed by event slug, photo ID, and the requested presentation
variant. It reloads the published event and eligible photo on every request, then asks the resolver
for the current variant. The browser never supplies an object key.

The private storage adapter gains a narrowly scoped read operation for one validated final-original
key. Django returns the object as an inline streaming response with the correct content type and
safe cache headers. It does not redirect to S3, because a presigned S3 URL would disclose the
permanent key in its path. The operation never grants list, write, copy, or delete access.

Generated HTML and responses must not contain the permanent `original_key`, bucket credentials, or
internal error details. Missing or ineligible media returns 404; a sanitized storage failure returns
503. The current increment provides inline viewing only; it does not define an attachment download
or commerce entitlement.

## UI and interaction

The event header and event metadata remain unchanged. A new section below them contains:

- a heading and photo count;
- an adaptive grid of 4:3 cards inspired by the original prototype;
- a linked image and compact photo identifier;
- an empty state when no items are available.

Activating a card opens its `large_media` in GLightbox 3.3.1. GLightbox is selected because the
current upload metadata does not include intrinsic pixel dimensions, which PhotoSwipe requires.
GLightbox assets are pinned and served from `src/backend/static/ui/`; no CDN or runtime package fetch
is allowed. The gallery supports
keyboard opening, next/previous navigation, Escape to close, focus restoration, touch gestures, and
reduced-motion preferences provided by the library and local styles.

Images use lazy loading outside the first immediately visible content. The page remains usable when
JavaScript fails: each card is a normal link to its signed large-media URL.

## Verification

- Factory tests prove only uploaded rows become `GalleryPhoto` instances and that the current
  preview and large variants resolve to the original.
- Resolver/storage tests prove reads are restricted to validated final keys, stream without a
  public S3 redirect, sanitize failures, and never return permanent keys as presentation fields.
- View tests prove event scoping, stable ordering, empty state, omission of legacy rows, and absence
  of `original_key` in HTML.
- Media-endpoint tests prove free/public/event scoping, inline content headers, 404 behavior, and
  sanitized 503 behavior.
- Markup tests prove each item has a normal fallback link and the GLightbox gallery attributes.
- Browser tests prove open, close, next/previous, Escape, focus restoration, and a no-JavaScript
  fallback link.
- Desktop and mobile event-detail snapshots cover populated and empty galleries; every intentional
  snapshot update is inspected before acceptance.
