# 0040: Use pgvector for exact event-scoped face search

- Status: Accepted
- Date: 2026-09-27
- Deciders: project maintainer; explicitly accepted in conversation on 2026-09-27
- Supersedes: [ADR 0019](0019-use-public-event-selfie-search.md), in-memory direct comparison and initial exclusion of vector infrastructure only
- Superseded by: [ADR 0041](0041-accept-pgvector-numerical-boundaries.md), strict numerical parity requirement only

## Context

Direct selfie and gallery-face searches validate scalar cohort identity, reuse a process-local
NumPy matrix when possible, and supply final evidence through exact Python arithmetic. Large
event cohorts make cache misses, identity reads, and matrix hydration expensive. The approved
specification selects an exact PostgreSQL path while preserving result membership and privacy.
Existing SFace 128D and AdaFace 512D generations must coexist during this storage transition.

## Decision drivers

- Preserve full threshold-based result membership and deterministic provenance.
- Reduce online gallery-vector transfer without another service or biometric store outside PostgreSQL.
- Compare the two readers on the same data before changing public exposure.
- Keep an immediate legacy-reader rollback until later new-only model reprocessing.

## Considered options

1. Add a parallel pgvector table and introduce exact database-side ranking through a temporary read gate.
2. Continue JSON/Python ranking and maintain or extend an in-process cohort cache.
3. Use an approximate pgvector index or a dedicated vector engine.

## Decision

Select option 1, conditional on measured parity and shared-host performance acceptance.

Keep PostgreSQL authoritative. Bind new vector rows directly to face detections and model
identities, independently of legacy embedding rows. Populate historical data and keep accepted
new writes complete in both stores during the reversible read transition. Use exact scans with
no ANN index or top-K truncation. Preserve event/generation eligibility, best face per photo,
direct ordering, cluster anchors, immutable results, query-vector ephemerality, and selfie cleanup.

Use the existing code-owned `off` / `staff` / `on` gate lifecycle to select the reader. Only
bounded explicit reviews compare both readers; only one publishes results. Missing new data or
new-reader failure never silently publishes a legacy or partial result.

Worker separation and SFace-to-AdaFace reprocessing are later work. New-only AdaFace evidence
ends legacy-reader rollback for affected events and requires an explicit recovery boundary.
Remove legacy embedding storage, reader, parallel writes, and gate together after their
dependents have migrated; retain detections, projections, saved results, and provenance.

This supersedes only ADR 0019's in-memory ranking and initial vector-infrastructure exclusion.
It conforms to ADRs 0002, 0024, 0025, 0028, and 0032. It does not authorize new recognition
models, threshold changes, ANN, cross-event matching, or persisted query embeddings.

## Consequences

### Positive

- Exact ranking avoids recall loss from approximate candidate selection.
- Controlled staff review and a temporary old reader permit reversible exposure.
- One vector representation can later support new-only AdaFace processing.

### Negative

- Parallel storage temporarily duplicates biometric-derived data and increases write/reconciliation work.
- Exact scans and joins add load to the shared PostgreSQL host; speedup is not guaranteed.
- Float32 representation and distance arithmetic require explicit boundary and ordering parity proof.

### Follow-up

- Deliver and measure the gated pgvector read transition before model reprocessing.
- Decide later worker capacity, model quality, new-only recovery, and legacy cleanup in separate plans.

## Validation and rollback

Require complete cohort reconciliation, exact result identity/order parity, distance tolerance
at most `1e-6`, unchanged anchors and privacy, upgrade/restore rehearsal, and improved measured
search performance without material web/processing regression. During parallel completeness,
return the gate to `off`. Never roll a vector-bearing database onto an image lacking the extension.
Reconsider the decision if parity cannot be established or PostgreSQL contention outweighs gains.

## References

- [Approved specification](https://github.com/peter-nikitin/photo-prjct/blob/c20ea18e8f9647ad6b29f3163279646220927198/docs/superpowers/specs/2026-09-25-pgvector-exact-face-search-design.md)
- [Architecture: Search](../architecture.md#search)
- [ADR 0002](0002-postgresql-system-of-record.md)
- [ADR 0028](0028-operate-one-canonical-deployment.md)
- [ADR 0032](0032-reconcile-code-owned-feature-flags-at-startup.md)
