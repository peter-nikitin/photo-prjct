# Exact arithmetic finding for the pgvector reader

- Date: 2026-09-27
- Status: resolved by [accepted ADR 0041](../adr/0041-accept-pgvector-numerical-boundaries.md)
- Related plan: [pgvector exact search](../plans/2026-09-27-pgvector-exact-face-search.md)

## Observed contract

The current `selfie_search/services/ranking.py` accepts gallery/query norms within absolute
`1e-6` of one. Final distance is `1 - math.fsum(query[i] * gallery[i])`, clamped to `[0, 2]`;
it does not normalize the operands again. pgvector's `<=>` cosine operator normalizes them,
and its `vector` components have float32 precision. Exact scans therefore do not by themselves
preserve the current threshold membership and ordering.

## Reproduced membership difference

A synthetic 128D SFace input uses query `[1, 0, ...]` and gallery
`[0.6370000243186951, 0.7708638310432434, 0, ...]`. Both nonzero gallery components are
exactly representable in float32. Its norm is `1.0000000384963414`, well inside the current
accepted tolerance. Tested on PostgreSQL 16.15 with pgvector 0.8.6.

| Reader | Distance | Inclusive threshold 0.363 |
| --- | --- | --- |
| Current Python evidence | 0.36299997568130493 | Included |
| pgvector cosine `<=>` | 0.36300001364946166 | Excluded |

The distance delta is below `1e-6`, but the mandatory identical membership condition fails.
A stricter unit-norm check alone does not resolve float32 arithmetic or exact tie/order risks.
No production query, selfie, or vector was used or retained for this proof.

## Accepted resolution

The maintainer rejects Python candidate refinement and accepts very borderline output changes.
Use native exact pgvector cosine ranking in PostgreSQL. ADR 0041 supersedes the strict identity
parity requirement with explicit numerical-boundary classification; no ANN, threshold widening,
extra float64 storage, or silent old-reader fallback is introduced. The experiment remains proof
of the accepted difference, rather than an unresolved implementation blocker.
