# 0011: Use direct private Object Storage ingestion

- Status: Accepted
- Date: 2026-07-13
- Deciders: project maintainer
- Supersedes: none
- Superseded by: none

## Context

Photographers may upload batches of large JPEG files. Proxying those bytes through the application
VM would make Django, Nginx, and the VM network path an ingestion bottleneck. Direct browser upload
must not allow the browser to choose authoritative object keys or gain read access to originals.

## Decision drivers

- Keep media transfer off the application VM.
- Preserve a private boundary for untrusted uploads and confirmed originals.
- Prevent a changed incoming object from being promoted after verification.
- Bound abandoned-object retention and browser storage privileges.

## Considered options

1. Direct per-file browser upload to a private incoming prefix followed by server-side promotion.
2. Proxy every file through Django.
3. Upload one archive and unpack it on the server.

## Decision

Use the separate private Yandex Object Storage bucket selected by ADR 0006. Django issues a
presigned POST valid for 10 minutes and constrained to one generated incoming key, the private
bucket, JPEG content type, and the allowed content-length range. The browser never receives storage
credentials or authorization to write a final key.

Django captures the incoming object's ETag during verification and binds subsequent byte-range
reads and the server-side copy to that ETag. A valid object is promoted to a separately generated,
immutable final key and checked before authoritative photo metadata is committed. Confirmed
originals have no automatic deletion in this stage. Incoming objects and unlinked final objects are
stale after 24 hours without activity and are eligible for idempotent cleanup.

Private-bucket credentials follow least privilege for the ingestion prefixes and required signing,
inspection, copy, and deletion operations. CORS permits `POST` only from configured application
origins with only the headers required by the policy; browser read, list, copy, and delete access
remain denied.

## Consequences

### Positive

- Large uploads bypass application request bodies and VM bandwidth.
- Incoming writes cannot replace confirmed originals or select final keys.
- ETag conditions prevent promotion of bytes different from those verified.
- Abandoned objects have a defined cleanup boundary.

### Negative

- Bucket IAM, CORS, signing, promotion, and cleanup require operational ownership.
- Confirmed originals accumulate until a later retention decision changes the policy.
- Object Storage compatibility must be tested for conditional reads and server-side copy.

### Follow-up

- Provision the private bucket and least-privilege credentials before enabling ingestion.
- Validate the policy, CORS, ETag conditions, promotion, and cleanup against the S3-compatible API.
- Decide lifecycle rules for confirmed originals before their retention policy changes.

## Validation and rollback

Validate that an authorized upload can write only its constrained incoming key, cannot read or list
objects, cannot write a final key, and cannot promote an object changed after verification. Disable
new grant issuance if these boundaries fail; retained private objects and PostgreSQL metadata remain
the recovery source while the adapter is corrected.

## References

- [ADR 0006: Use Yandex Object Storage for media](0006-yandex-object-storage-media.md)
- [Stage 2 photographer upload design](../superpowers/specs/2026-07-13-stage-2-photographer-upload-design.md)
- [Architecture: photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing)
