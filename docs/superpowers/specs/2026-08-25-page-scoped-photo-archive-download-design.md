# Page-Scoped Photo Archive Download Design

## Status

Approved by the maintainer on 2026-08-30. ADR 0034 was accepted on 2026-08-30, so implementation
planning may proceed.

- Related architecture: [`docs/architecture.md`](../../architecture.md), public selfie search,
  private media delivery, paid-order fulfillment, runtime feature gates, privacy, and operational
  boundaries.
- Related product jobs:
  [`PJ-008 — Customer — Find photos by face`](../../product-jobs.md#pj-008--customer--find-photos-by-face),
  [`PJ-009 — Visitor — Receive a free-event original`](../../product-jobs.md#pj-009--visitor--receive-a-free-event-original),
  and
  [`PJ-011 — Customer — Download purchased photos`](../../product-jobs.md#pj-011--customer--download-purchased-photos).
- Related specifications:
  [`2026-08-01-one-click-original-download-design.md`](2026-08-01-one-click-original-download-design.md),
  [`2026-08-02-gallery-pages-and-uncropped-portraits-design.md`](2026-08-02-gallery-pages-and-uncropped-portraits-design.md),
  [`2026-08-20-paid-watermarked-previews-design.md`](2026-08-20-paid-watermarked-previews-design.md),
  and
  [`2026-08-20-paid-photo-purchase-and-original-delivery-design.md`](2026-08-20-paid-photo-purchase-and-original-delivery-design.md).
- Related ADRs:
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md),
  [ADR 0020](../../adr/0020-use-signed-direct-object-storage-media-delivery.md),
  [ADR 0021](../../adr/0021-allow-original-download-for-authorized-photos.md),
  [ADR 0022](../../adr/0022-use-numbered-gallery-pages.md),
  [ADR 0028](../../adr/0028-operate-one-canonical-deployment.md),
  [ADR 0029](../../adr/0029-use-watermarked-previews-for-paid-photos.md),
  [ADR 0031](../../adr/0031-use-orders-and-adapters-for-paid-original-delivery.md), and
  [ADR 0032](../../adr/0032-reconcile-code-owned-feature-flags-at-startup.md), with accepted
  [ADR 0034](../../adr/0034-stream-page-scoped-photo-archives-through-django.md) for this increment.
- ADR impact: **Resolved by accepted ADR 0034.** Streaming an aggregate archive body through Django is a
  deliberate exception to ADR 0020's rule that application requests authorize media while Object
  Storage serves media bodies directly. The ADR must accept the page-sized streaming exception,
  preserve all existing authorization and exact-object boundaries, and record why a temporary
  stored archive and background archive job are not selected. ADR 0034 records that choice.

## Outcome

A customer can download the photos currently visible on one authorized page as one ZIP archive.
For a free event this works on a ready selfie-search result. For a paid event it works only after
purchase, on the exact paid Order page. Numbered pages make the batch boundary visible and keep one
request bounded to at most 100 originals.

The archive starts downloading immediately and is assembled while it is sent. FindMe Photo does
not persist the aggregate archive, create a background job, or require the customer to wait for a
separate preparation screen. If the transfer is interrupted, the customer starts it again.

## Scope

### Included

- A page-scoped ZIP download on ready selfie-search results for free events.
- Numbered server-rendered pagination of paid Order items, fixed at 100 items per page.
- A page-scoped ZIP download on a paid Order page through either of its existing purchase-browser
  or permanent access-grant capabilities.
- Clear one-page and multi-page button copy, item counts, and page boundaries.
- Sequential streaming of exact private originals through Django without retaining an aggregate
  archive.
- Existing per-photo download actions alongside the archive action.
- One fail-closed database runtime gate covering both archive surfaces and their direct endpoints.
- Authorization, privacy, audit, failure, observability, accessibility, responsive-layout, and
  visual-regression contracts for the two archive surfaces.

### Excluded

- A bulk add-to-cart action on paid selfie-search results. A future action may represent a
  discounted package rather than a set of ordinary individual cart items, so its product and
  commercial semantics must be designed separately.
- A ZIP action on the normal event gallery, cart, checkout, photographer workflow, or Admin.
- Downloading every page in one request, automatically advancing through pages, or starting
  several page downloads from one click.
- Persisted archive files, Object Storage archive objects, archive lifecycle rules, preparation
  jobs, polling, progress screens, resume support, or an archive-history model.
- A generic task broker, a dedicated archive worker, or changes to the photo-processing worker.
- Recompression, image conversion, alternate original formats, or fallback to previews and
  watermarked derivatives.
- A separate archive byte limit. The fixed 100-item page and existing 50 MiB per-photo upload limit
  already bound the theoretical archive input to approximately 5 GiB.
- Changes to selfie matching, result membership, result ranking, paid price calculation, cart
  representation, checkout, payment, Order entitlement, or email content.

The deferred paid-search package action is recorded in
[`docs/future-work/2026-08-25-paid-search-result-package-action.md`](../../future-work/2026-08-25-paid-search-result-package-action.md).

## Selected Design

### The open page is the batch

The batch is exactly the collection page represented by the current server-rendered URL. The
customer chooses another batch by following normal page navigation and downloading that page.
There is no hidden total-search cap and no special chunk selector.

Ready selfie-search results retain their existing fixed page size of 100 and their persisted
ascending rank followed by photo ID. The archive contains exactly the eligible photos rendered on
the requested ready-result page in that order. It neither repeats the search nor expands the saved
result.

Paid Order pages gain the same numbered-page contract: the first page has no required query
parameter, subsequent pages use `?page=N`, and each page contains at most 100 OrderItems ordered by
ascending photo ID. The archive contains exactly those current-page OrderItems in the same order.
The Order summary continues to show the whole Order's photo count and total price, not the current
page's count.

A missing page parameter selects page 1. A non-integer, zero, negative, or out-of-range page
returns a sanitized `404`. If underlying data changes between page requests, ordinary numbered
offset-page behavior is accepted; paid OrderItems themselves remain immutable.

### Customer interface

The ready-result archive action appears above the photo grid near the result count. On mobile it
occupies the available width below the count. The paid Order archive action is the bright primary
button in the right-hand summary panel above the resend action. The existing
`Отправить письмо ещё раз` text does not change, but that action becomes a white secondary button.

The archive action appears only when the current page contains at least two photos:

| Collection shape | Button | Helper text |
| --- | --- | --- |
| One page | `Скачать все` | None |
| Several pages | `Скачать эту страницу` | `В архив попадут 100 фотографий со страницы 2 из 5. Остальные страницы можно скачать отдельно.` |
| Zero or one photo on the current page | No archive action | None |

The multi-page helper uses the actual current-page count, the grammatically correct Russian noun
form, and actual page numbers, so the final page may say, for example, `В архив попадут 17
фотографий со страницы 5 из 5. Остальные страницы можно скачать отдельно.` Individual
`Скачать оригинал` actions remain available on both surfaces.

Paid selfie-search results retain only their existing per-photo cart actions. They gain no
archive, `add all`, or other bulk commercial action in this increment.

### Archive identity and contents

One-page ready-result archives use
`findme-photo-<event-slug>-search-results.zip`. Multi-page archives use
`findme-photo-<event-slug>-search-page-<page>.zip`.

One-page paid Order archives use `findme-photo-order-<public-number>.zip`. Multi-page archives use
`findme-photo-order-<public-number>-page-<page>.zip`.

Archive members are flat files named `findme-photo-<photo-id>.<ext>`, where the extension is the
validated original media extension. Member names never contain a photographer-supplied filename,
Object Storage key, directory, absolute path, or traversal component. Each authorized photo occurs
once and members retain page presentation order.

The ZIP uses ZIP64 and stores already-compressed JPEG and PNG originals without recompressing them.
The response has no `Content-Length`; the browser begins receiving the attachment before the final
size is known.

### Streaming boundary

Archive assembly is one deep media-delivery capability. Its caller supplies an ordered collection
of already-authorized archive entries. The capability owns safe member naming, opening each exact
private original, writing ZIP structure, yielding bounded response chunks, and closing resources
on success, failure, or disconnect.

The archive capability does not interpret selfie bearer tokens, purchase-browser cookies,
OrderAccessGrants, Event prices, or paid-watermark policy. Surface-specific application services
remain responsible for those rules and cannot pass an entry until its current request is
authorized.

The archive is produced with Python 3.12's standard ZIP support over a non-seekable streaming
output. Originals are opened from private S3-compatible Object Storage one at a time, copied in
bounded chunks, and closed before the next original is opened. Memory use is therefore bounded by
streaming buffers rather than total page size. The request creates no temporary archive file and
no aggregate Object Storage object.

The response is a Django streaming attachment. Reverse-proxy buffering, including spill to proxy
temporary files, is disabled specifically for archive responses so bytes reach the customer as
they are produced. Ordinary media and application responses keep their existing proxy behavior.
Each active archive occupies one ordinary web request worker/thread for the lifetime of the
transfer; this accepted first version adds no separate execution pool.

## Authorization and Privacy

### Free ready-result archive

The request must present the existing valid bearer capability for the exact saved result. The
result must be ready, belong to the requested Event, and be site-visible under the existing public
selfie-result rules. At read time, every included photo must still be eligible for that result and
for original download. The Event must use the free original-delivery policy; a paid event that
shows watermarked public results cannot obtain originals through this endpoint.

The requested `?page=N` is part of the authorized collection boundary. The endpoint cannot accept
arbitrary photo identifiers, combine results, cross an Event boundary, or include a photo absent
from the rendered page.

### Paid Order archive

The Order must be paid, and the request must present either the existing purchase-browser
capability for that exact Order or an active signed OrderAccessGrant. Only OrderItems from the
requested Order page are included. Current Event publication, gallery eligibility, price,
watermark state, and cart membership are deliberately irrelevant after purchase, matching the
existing paid entitlement contract.

Archive authorization appends the existing per-OrderItem download-grant audit for every item
authorized for inclusion, with the existing browser-or-grant source. The audit means that delivery
authority was issued; it does not claim that the entire archive reached the customer. No new
archive-history or transfer-completion record is created.

### Common protections

The `bulk-photo-download` feature uses the code-owned database gate with `off`, `staff`, and `on`
states. A newly reconciled definition is `off`. The gate controls both user-interface visibility
and direct archive endpoints; it is an operational release control, never authorization. In
`staff`, real-data acceptance is limited to staff. In `on`, each archive is available only where
its underlying result or paid Order surface is already available. The paid Order path also remains
behind every existing paid-purchase gate and capability check.

Archive pages and responses use `private, no-store` caching semantics and the existing safe
referrer policy. Secret Order URLs do not acquire analytics. Logs, audit rows, and metrics never
contain raw bearer tokens, OrderAccessGrant signatures, purchase-browser capabilities, signed
storage URLs, Object Storage keys, IP addresses, User-Agent values, or request headers.

Invalid, expired, gated, cross-result, cross-Event, cross-Order, and cross-page access fails closed
with a sanitized `404`, consistent with the existing private media surfaces.

## Failure Semantics

All collection resolution, page validation, database authorization, and safe archive-entry
construction complete before streaming begins. A failure detected before response bytes begin may
return a sanitized `404` for absence or denial, or `503` for a temporary storage/setup failure.

After any archive bytes have been sent, HTTP status cannot be replaced. If an original is missing,
changed, or unavailable, the stream stops, the current storage body closes, no later original is
opened, and the ZIP is left incomplete rather than silently succeeding with fewer files. The
customer retries the page download from the beginning.

A missing or identity-mismatched purchased original also creates or deduplicates the existing
`original_missing` Commerce attention for that OrderItem. The Order and its entitlement do not
change. No path falls back to a preview, watermarked derivative, another photo, or a similarly
named storage object.

If the customer disconnects, the current Object Storage body closes promptly and no later
original is opened. A completed stream closes the ZIP central directory and all storage and
response resources. Failure logging remains sanitized and distinguishes setup failure,
mid-stream source failure, and customer interruption.

## Observability and Operations

Safe structured events distinguish `free_result` and `paid_order` archive contexts and record page
number, file count, declared input bytes when known, streamed bytes when known, duration, and a
bounded outcome such as completed, interrupted, setup failure, or source failure. They use
low-cardinality context names and never record capabilities or storage identities.

Existing HTTP latency/error metrics and structured logs are sufficient for the first version.
There is no archive dashboard, persisted progress, retry counter, or customer download history.
Operators can disable the one archive gate without redeployment if archive traffic harms ordinary
requests.

Stored Object Storage archives or a dedicated archive process should be reconsidered only after
measurement shows material degradation of ordinary request capacity from long streaming requests,
or meaningful customer/support pain from retries. The theoretical worst page is 100 originals at
the existing 50 MiB upload maximum; staff acceptance must exercise large enough inputs to validate
streaming memory, proxy, timeout, and disconnect behavior before public activation.

## Alternatives Rejected

### Build an archive in Object Storage and return a link

Transient storage cost alone is small, but this option requires a durable archive-job model,
worker ownership, multipart creation, preparation/polling UX, expiry and lifecycle rules, cleanup,
retry semantics, and another sensitive aggregate object. None is needed for the accepted
start-immediately, retry-from-scratch workflow.

### Start one browser download per photo

Browsers may block or prompt for many downloads, failure state becomes fragmented, and one click
does not reliably produce one customer artifact.

### Download the complete result or Order in one archive

This hides an unbounded batch behind `all`, conflicts with visible numbered pages, and holds a web
request for longer than necessary. The open page is a precise, explainable boundary.

### Compress image data

JPEG and PNG originals are already compressed. Deflating them consumes CPU and delays streaming
without a dependable size reduction.

### Add all paid search results to the cart

The future action may sell a discounted bundle with composition and price semantics distinct from
ordinary per-photo cart items. Treating it as a mechanical cart shortcut now would prematurely
choose the product model and customer promise.

## Acceptance Criteria

1. A ready free-event result with at least two current-page photos offers the specified one-page or
   multi-page archive action; a paid ready result offers no new bulk cart or archive action.
2. A paid Order renders numbered pages of at most 100 OrderItems in ascending photo-ID order while
   its summary count and total continue to describe the whole Order.
3. A paid Order page with at least two current-page items renders the archive action as the primary
   summary action above the unchanged `Отправить письмо ещё раз` text, which is styled as a white
   secondary action.
4. The button is `Скачать все` with no helper for a one-page collection, and
   `Скачать эту страницу` with the exact dynamic current-count/page helper for a multi-page
   collection. It is absent when the current page has fewer than two photos.
5. Each archive contains exactly the authorized visible items from the requested page, once each,
   in presentation order, with the specified archive and safe flat member names.
6. Ready results retain persisted rank/photo-ID ordering and 100-item pages. Orders use stable
   photo-ID ordering and 100-item pages. Invalid and out-of-range page values return sanitized
   `404` responses.
7. A valid archive is ZIP64-capable, stores JPEG/PNG members without recompression, omits
   `Content-Length`, and can be opened by ordinary ZIP clients after successful completion.
8. Archive production opens at most one original body at a time, uses bounded memory, creates no
   temporary or Object Storage archive, and closes source bodies on completion, source failure,
   and client disconnect.
9. Reverse-proxy buffering and temporary-file spill are disabled for archive responses only, and
   the first bytes can reach the client before the full archive is assembled.
10. Free-result authorization enforces the exact bearer result, Event, page, current photo
    eligibility, site visibility, and free original-delivery policy. Paid authorization enforces
    the exact paid Order, page, OrderItems, and purchase-browser or active grant capability.
11. The feature gate's `off`, `staff`, and `on` states fail closed for both UI and direct endpoint
    access without replacing surface-specific authorization.
12. Paid archive authorization creates the existing per-item grant audits. A missing purchased
    original creates or deduplicates the existing Commerce attention without changing entitlement.
13. A source failure after streaming starts never produces a silently successful partial archive;
    later sources are not opened, resources close, and the safe outcome is observable.
14. Archive responses remain private and non-cacheable, secret Order pages remain free of
    analytics, and logs/audits/metrics contain none of the prohibited capabilities, storage
    identities, or request fingerprints.
15. Focused domain, view, streaming, storage-adapter, authorization, privacy, failure, disconnect,
    accessibility, and desktop/mobile visual-regression checks protect the stated contracts,
    including free single-page/multi-page and paid Order single-page/multi-page states.
