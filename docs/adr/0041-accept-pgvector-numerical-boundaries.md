# 0041: Accept numerical boundary differences in exact pgvector search

- Status: Accepted
- Date: 2026-09-27
- Deciders: project maintainer; explicitly selected SQL-only pgvector and accepted very borderline result changes in conversation
- Supersedes: [ADR 0040](0040-use-pgvector-for-exact-face-search.md), strict identity/order/anchor parity requirement only
- Superseded by: none

## Context

The [numerical experiment](../research/2026-09-27-pgvector-exact-arithmetic.md) proves that pgvector
cosine distance and the existing Python dot-product evidence can make different inclusive
threshold decisions even on normalized float32-origin vectors. Exact scans prevent ANN recall
loss but do not provide bitwise arithmetic equivalence. The maintainer accepts very borderline
changes and selects database-side ranking without candidate rechecking in Python.

## Decision drivers

- Keep one understandable database ranking path.
- Preserve eligibility, full exact scans, fixed thresholds and deterministic result publication.
- Make accepted numerical differences visible during controlled comparison.

## Considered options

1. Use exact native pgvector cosine ranking and accept numerical boundary differences.
2. Preserve original float64 components and recheck conservative candidates with Python arithmetic.

## Decision

Select option 1. PostgreSQL calculates final distances, best detections, inclusive threshold
membership and order over the whole eligible cohort. Do not add Python candidate refinement,
extra float64 vector storage, ANN, top-K, threshold widening or a silent old-reader fallback.

Retain absolute distance comparison tolerance `1e-6`. Differences in membership near the
threshold, selected faces or ordering near ties, and strong anchors near their existing thresholds
are accepted only within this numerical band. Comparison reports classify boundary differences
separately from unexplained differences; the latter block activation. Report any resulting
expansion differences for operator review before public activation. Eligibility, query privacy,
source identity/authorization and saved-result invariants are strict; a gallery source retained by
existing source-membership rules must remain present. No recognition model or threshold changes.

All other decisions in ADR 0040, including the temporary read gate, parallel completeness,
reviewed capacity evidence, reversible rollout and eventual coordinated cleanup, remain binding.

## Consequences

### Positive

- The new reader performs native database ranking without a second arithmetic path.
- Large gallery vector payloads stay inside PostgreSQL.

### Negative

- Very borderline photos, near-tie order and anchor decisions may differ from saved old-reader results.
- Review must distinguish accepted numerical changes from eligibility or ranking defects.

### Follow-up

- Complete the reader, feature routing, bounded comparison and rollout proof.
- Review classified numerical differences and measured capacity before selecting public `on`.

## Validation and rollback

Adversarial tests must reproduce accepted threshold/tie behavior without Python refinement.
Require complete cohort identity, no unexplained differences and distance deltas within `1e-6`.
Keep old reading available through `off` during parallel completeness. Public rollout retains
operator review of numerical effects and performance; schema and biometric privacy gates remain.

## References

- [ADR 0040](0040-use-pgvector-for-exact-face-search.md)
- [Specification](https://github.com/peter-nikitin/photo-prjct/blob/c20ea18e8f9647ad6b29f3163279646220927198/docs/superpowers/specs/2026-09-25-pgvector-exact-face-search-design.md)
- [Implementation plan](https://github.com/peter-nikitin/photo-prjct/blob/c20ea18e8f9647ad6b29f3163279646220927198/docs/plans/2026-09-27-pgvector-exact-face-search.md)
