# 0034: Stream page-scoped photo archives through Django

- Status: Proposed
- Date: 2026-08-30
- Deciders: project maintainers
- Supersedes: [ADR 0020](0020-use-signed-direct-object-storage-media-delivery.md), only for the
  transport of an authorized page-scoped aggregate archive
- Superseded by: none

## Context

ADR 0020 keeps Django and PostgreSQL authoritative for media authorization while moving each
authorized media body to a direct, short-lived Object Storage download. ADR 0021 applies that
transport to individual original attachments. ADR 0031 applies it to each original purchased in a
paid Order and deliberately leaves ZIP delivery for a later decision.

Customers now need one action that downloads the currently visible free selfie-result page or paid
Order page as one archive. Numbered pages already bound the intended batch to at most 100 photos.
Object Storage cannot assemble those private originals into one ZIP response, so aggregate delivery
must either pass through application compute, create and retain another aggregate object, or become
many independent browser downloads.

The selected product behavior starts one download immediately, stores no archive, and retries from
the beginning after interruption. This is a durable exception to ADR 0020's data-plane boundary and
affects web capacity, proxy buffering, failure semantics, and privacy, so it requires a separate
decision before implementation planning.

## Decision drivers

- Produce one browser download immediately from the exact open page.
- Keep every original private and preserve existing result and paid-Order authorization.
- Avoid a durable aggregate object, archive job, preparation state, polling, lifecycle, and cleanup.
- Bound memory and open Object Storage bodies independently of total archive size.
- Keep the initial operational model proportional to current traffic and one canonical deployment.
- Preserve a measurable trigger for moving archive work out of ordinary web request capacity.

## Considered options

1. Authorize the current page and stream one ZIP through Django while opening originals
   sequentially from private Object Storage.
2. Build a temporary ZIP in Object Storage with a background job and return a signed link after
   preparation.
3. Keep direct Object Storage delivery and make one browser action start an individual download for
   every photo on the page.

## Decision

Select option 1.

Django authorizes the exact numbered page before response bytes begin and streams one ZIP64 archive
without first materializing it locally or in Object Storage. The page contains no more than 100
entries. Archive production opens one exact private original at a time, copies it in bounded chunks
without recompressing JPEG or PNG data, closes it before opening the next, and emits only safe flat
member names derived from public photo identifiers and validated media types.

The free path retains ADR 0019's exact ready-result bearer, event isolation, immutable result
membership, current read eligibility, and paid-media denial. The paid path retains ADR 0031's exact
paid OrderItem entitlement and purchase-browser or active OrderAccessGrant capability, including
the existing per-item grant audit and missing-original attention behavior. Cart state never becomes
download authority. Individual original downloads retain ADR 0020's direct signed Object Storage
transport unchanged.

Archive responses omit a final size and disable reverse-proxy buffering only for the archive path,
so bytes can reach the customer as they are produced without proxy temporary-file spill. Each
archive occupies one ordinary Django/Gunicorn request slot for its transfer. A dedicated worker,
task broker, archive state model, aggregate Object Storage object, resume protocol, and download
history are outside this decision.

Authorization and archive-entry construction complete before streaming. A source failure after
bytes begin terminates the stream, closes the current source, opens no later source, and leaves an
incomplete archive rather than silently omitting a photo. A client disconnect closes the current
source and stops production. The customer retries the page from the beginning.

One code-owned database gate controls both archive user-interface actions and direct archive
endpoints, failing closed in `off`, permitting controlled acceptance in `staff`, and permitting
otherwise-authorized public use in `on`. The gate does not replace result, Event, Order, or customer
capability authorization.

This decision supersedes ADR 0020 only for the aggregate archive body. ADR 0020 remains
authoritative for every individual preview, presentation original, and original attachment. ADRs
0019, 0021, 0022, 0028, 0031, and 0032 otherwise remain unchanged.

## Consequences

### Positive

- The accepted one-click archive behavior works without preparation state or retained aggregate
  media.
- Memory use and simultaneous private-object reads stay bounded as archive size grows within one
  page.
- Existing result membership, paid entitlement, and private Object Storage boundaries remain the
  only sources of download authority.
- Rollback requires no archive-row migration, object cleanup, lifecycle transition, or worker drain.

### Negative

- A long or slow archive occupies an ordinary web request slot and consumes VM plus Object Storage
  egress for the duration of the transfer.
- Once response bytes begin, a later source failure cannot return a normal HTTP error; the customer
  receives an incomplete archive and must retry.
- The application becomes a data plane for this narrow aggregate path even though ADR 0020 keeps it
  out of every individual media transfer.
- The response has no final `Content-Length`, persisted progress, resume, or prepared-link reuse.

### Follow-up

- Validate representative and maximum-page streaming under the canonical Gunicorn and Nginx
  configuration before public activation.
- Measure archive duration, outcome, bytes, ordinary request latency, and request-slot pressure with
  privacy-safe low-cardinality telemetry.
- Reconsider a stored archive or dedicated archive process only after measured degradation of
  ordinary requests or material customer/support pain from retrying interrupted transfers.
- Remove the release gate after stable public rollout or remove the feature and definition together
  if the decision is rejected operationally.

## Validation and rollback

Validate exact page membership and ordering, cross-result and cross-Order denial, free-versus-paid
original policy, paid per-item audit, safe names, ZIP64 structure, one-source-at-a-time bounded
streaming, absence of local and Object Storage archive artifacts, route-specific proxy buffering,
source failure, client disconnect, and `off` / `staff` / `on` behavior. Staff acceptance must use a
large representative page and confirm that ordinary request capacity stays healthy while the
archive is active.

Roll back exposure by setting the archive gate to `off`. A code rollback removes the archive routes
and action while retaining individual direct downloads, paid entitlement, audits, and attention
records. No aggregate object or archive state requires cleanup. Reconsider this decision when
streaming measurably harms ordinary request latency or availability, or retry failures become a
material customer problem.

## References

- [Page-scoped photo archive download design](../superpowers/specs/2026-08-25-page-scoped-photo-archive-download-design.md)
- [Architecture](../architecture.md)
- [ADR 0019: Use public event-scoped selfie search](0019-use-public-event-selfie-search.md)
- [ADR 0020: Use signed direct Object Storage media delivery](0020-use-signed-direct-object-storage-media-delivery.md)
- [ADR 0021: Allow original download for authorized photos](0021-allow-original-download-for-authorized-photos.md)
- [ADR 0022: Use numbered gallery pages](0022-use-numbered-gallery-pages.md)
- [ADR 0028: Operate one canonical deployment](0028-operate-one-canonical-deployment.md)
- [ADR 0031: Use orders and adapters for paid original delivery](0031-use-orders-and-adapters-for-paid-original-delivery.md)
- [ADR 0032: Reconcile code-owned feature flags at startup](0032-reconcile-code-owned-feature-flags-at-startup.md)
