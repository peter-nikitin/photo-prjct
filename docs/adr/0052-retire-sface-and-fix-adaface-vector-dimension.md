# 0052: Retire SFace and fix the AdaFace vector dimension

- Status: Accepted
- Date: 2026-10-04
- Deciders: project maintainer; approved the AdaFace-only retirement direction in conversation on 2026-10-04
- Supersedes: none
- Superseded by: none

## Context

The historical SFace-to-AdaFace backfill and exact pgvector reader cutover are complete. The
shared pgvector column was deliberately left without a fixed dimension so 128D SFace and 512D
AdaFace evidence could coexist. Production code still selects either model and ships both worker
artifacts. That transitional contract increases maintenance and retains obsolete biometric data.
ADR 0040 deferred recognition-model retirement until after the storage transition.

## Decision drivers

- Use one current recognition contract for new processing and both query sources.
- Preserve historical processing records and immutable ready search results.
- Remove old biometric vectors and make incompatible writes structurally impossible.
- Keep exact event-scoped pgvector search and existing AdaFace threshold behavior.

## Considered options

1. Retain SFace inference and variable-dimension storage for historical events.
2. Retire SFace code and vectors, fix the shared vector column at 512D, and retain non-vector history.
3. Move AdaFace vectors to a separate table and delete the existing shared table.

## Decision

Select option 2. SCRFD-10G_KPS and AdaFace IR18 WebFace4M are the only production face-recognition
contract. New processing, selfie queries, gallery-face queries, direct ranking and optional cluster
construction use AdaFace 512D evidence. Remove SFace and YuNet executable paths, model artifacts,
selectors and compatibility adapters. The shared `processing_faceembeddingvector` table remains;
delete its SFace rows, fix its `vector` column at 512D, and permit only the current model identity.
Remove residual raw SFace vectors from historical JSON payloads while preserving non-vector attempt
history, detections, projections, hashes and saved result membership. Historical migrations and
records may still name the old model but cannot reactivate it.

Terminal AdaFace no-face and quality-rejected outcomes do not require synthetic replacement
vectors. Unavailable test events remain nonpublic with empty current recognition when source media
is unavailable. Existing ready search results remain stored snapshots subject to current media
authorization. Exact pgvector ranking, threshold, query-vector ephemerality and worker placement
do not change.

## Consequences

### Positive

- One model and fixed 512D storage remove the transitional dual-contract surface.
- SFace biometric vectors no longer remain in live application data.
- Saved searches and processing audit records remain available.

### Negative

- Some former SFace matches have no AdaFace counterpart and disappear from new searches.
- Once old vectors are deleted, an image requiring them is not a valid rollback target.
- Constraining a populated vector column requires a rehearsed, bounded data transition.

### Follow-up

- Implement and rehearse the data and contract migration against the existing local snapshot.
- Update the implemented architecture description after verification.

## Validation and rollback

Require zero SFace rows and raw JSON vector copies, a fixed 512D column, rejection of old-model
writes, healthy AdaFace processing and search, and unchanged ready-result membership. Preserve a
verified pre-transition backup. After vector deletion, recover forward with compatible web and
worker images and the backup; do not restore an old image against the contracted database.

## References

- [Retirement specification](../superpowers/specs/2026-10-04-retire-legacy-face-models-design.md)
- [ADR 0040](0040-use-pgvector-for-exact-face-search.md)
- [ADR 0041](0041-accept-pgvector-numerical-boundaries.md)
- [Architecture: Search](../architecture.md#search)
