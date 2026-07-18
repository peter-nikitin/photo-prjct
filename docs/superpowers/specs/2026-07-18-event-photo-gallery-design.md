# Event Photo Gallery Design

- Date: 2026-07-18
- Status: Approved
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

- Uploaded photos belonging to a published free event.
- A responsive gallery based on the simple card grid from the original photobank prototype.
- A separate immutable application model created from `Photo` for public gallery rendering.
- Stable, unsigned, same-origin application media URLs without exposing permanent object keys.
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
            +-- stable small- and large-preview application URLs
            |
            +-- GalleryPhoto(photo_id, preview_media_small, preview_media_large, alt)
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
- `preview_media_small`: a `GalleryMedia` for the list/card image;
- `preview_media_large`: a `GalleryMedia` for the lightbox image;
- `alt`: useful accessible text that does not expose filenames or storage keys.

`GalleryMedia` contains the stable browser URL and presentation variant (`preview-small` or
`preview-large`). It may gain responsive-source metadata when derivatives exist, but the initial
lightbox contract does not require intrinsic dimensions that the current `Photo` model cannot
provide.

For the initial implementation, both media values address the same application endpoint with an
explicit `preview-small` or `preview-large` variant. The endpoint resolver maps both variants to the
original. When derivative records are added, only the resolver changes: it selects the small
derivative for `preview-small` and the larger derivative for `preview-large`.

Neither value object exposes `original_key`. The URLs are deterministic application routes, not
signed URLs or expiring capabilities.

## Future processing seam

Using the original for both preview variants is an explicit transitional policy while derivative
generation does not exist. A later background-processing increment will start only after the
original and its `Photo` row are durably stored. It will create and verify the small and large
preview objects, persist their metadata, and only then atomically mark the photo ready for gallery
publication.

At that point the gallery eligibility query will require readiness and the media resolver will map
`preview-small` and `preview-large` to the corresponding derivatives. `GalleryPhoto`, the event
detail template, and the lightbox contract will not change. A partial or failed processing result
will not become gallery-visible and will never fall back to a paid original.

The worker, broker, task recovery, derivative persistence schema, and readiness representation are
outside this increment and require their own design and execution decision. The gallery neither
starts processing nor probes S3 to infer readiness; it consumes only database state owned by the
future processing boundary.

## Future commerce seam

`GalleryPhoto.photo_id` is the only trusted bridge available to future commerce. Both media fields
remain public presentation previews; neither is a purchased asset or a promise of original access.
A future cart boundary can use the stable photo identity without changing the gallery template or
exposing storage metadata.

Cart, payment, entitlement, purchased-file generation, and protected delivery contracts are not
designed in this increment. They will be decided when commerce is implemented rather than coupled
to a speculative schema now.

## Photo eligibility and ordering

The gallery query includes only rows that:

- belong to the requested published event;
- have a non-null `original_key`;
- belong to a free event while the original is the only available media; and
- represent a completed upload according to the current `Photo` row-shape contract.

Legacy rows with `src` are never rendered. Photos use the model's stable default ordering by ID for
this increment. Pagination and custom chronological ordering remain out of scope.

The factory performs no storage access and cannot omit an otherwise eligible row because of current
S3 state. A storage failure affects only the media request for that card; it never changes gallery
membership or turns a populated event into the empty state.

Paid-event photos remain absent until a watermarked preview exists; the gallery never exposes a paid
original as a temporary shortcut.

## Private-media delivery

The public media endpoint is a stable unsigned route addressed by event slug, photo ID, and the
requested presentation variant:

```text
GET /events/<event-slug>/photos/<photo-id>/media/<preview-small|preview-large>/
```

It reloads the published free event and eligible uploaded photo on every request, then asks the
resolver for the current variant. The browser never supplies an object key. Missing events, paid or
draft events, unknown photos, legacy `src` rows, and unknown variants return 404.

The private storage adapter gains a narrowly scoped `open_final` operation for one validated final
original key. It returns a closeable body plus validated content length and content type. Django
streams that body inline and does not redirect to S3, because a presigned S3 URL would disclose the
permanent key in its path. The operation never grants list, write, copy, or delete access.

Generated HTML and responses must not contain the permanent `original_key`, bucket credentials, S3
ETag, or internal error details. Storage errors raised before response headers map `ObjectMissing`
to 404 and other storage failures to a bodyless sanitized 503. Once streaming has begun the status
cannot change: a later body-read error is logged using only event slug and photo ID, the response is
terminated, and no private exception text is sent.

The streaming iterator closes the S3 body in `finally` after normal completion, a read exception,
or generator close caused by client disconnect. The response contract is deliberately minimal:

- `200 OK` with `Content-Type` restricted to the stored `image/jpeg` or `image/png` value;
- exact `Content-Length` from the validated S3 response;
- `Content-Disposition: inline; filename="photo-<photo-id>.<safe-extension>"`;
- `Cache-Control: private, no-store` so unpublishing takes effect on the next request;
- `X-Content-Type-Options: nosniff`;
- no `ETag` response header and no conditional-request handling; and
- no Range support in this increment; a GET always returns the complete object.

An in-flight response is not revoked retroactively, but every later request re-evaluates event and
photo eligibility. The current increment provides inline viewing only; it does not define an
attachment download or commerce entitlement.

## UI and interaction

The event header and event metadata remain unchanged. A new section below them contains:

- a heading and photo count;
- an adaptive grid of 4:3 cards inspired by the original prototype;
- a linked image and compact photo identifier;
- an empty state when no items are available.

Activating a card opens its `preview_media_large` in GLightbox 3.3.1. GLightbox is selected because
the current upload metadata does not include intrinsic pixel dimensions, which PhotoSwipe requires.
GLightbox assets are pinned and served from `src/backend/static/ui/`; no CDN or runtime package fetch
is allowed. The gallery supports
keyboard opening, next/previous navigation, Escape to close, focus restoration, touch gestures, and
reduced-motion preferences provided by the library and local styles.

Images use lazy loading outside the first immediately visible content. The page remains usable when
JavaScript fails: each card is a normal link to its stable large-preview application URL.

## Verification

- Factory tests prove only eligible uploaded rows become `GalleryPhoto` instances, no S3 operation
  occurs while building the page, and small- and large-preview URLs use the stable variant routes.
- Resolver/storage tests prove reads are restricted to validated final keys, stream without a
  public S3 redirect, sanitize failures, and never return permanent keys as presentation fields.
- View tests prove event scoping, stable ordering, empty state, omission of legacy rows, and absence
  of `original_key` in HTML.
- Media-endpoint tests prove free/public/event/variant scoping, the exact inline and no-store
  headers, no S3 redirect or ETag, pre-header 404/503 mapping, complete streaming, mid-stream
  termination, and body closure on completion, exception, and generator close.
- Markup tests prove each item has a normal fallback link and the GLightbox gallery attributes.
- Browser tests prove open, close, next/previous, Escape, focus restoration, and a no-JavaScript
  fallback link.
- Desktop and mobile event-detail snapshots cover populated and empty galleries; every intentional
  snapshot update is inspected before acceptance.
