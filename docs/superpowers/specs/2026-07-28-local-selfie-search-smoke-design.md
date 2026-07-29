# Local Selfie Search Happy-Path Smoke Design

## Status

Approved in conversation and confirmed for implementation on 2026-07-28.

- Related architecture: [`docs/architecture.md`](../../architecture.md), proposed Recognition and
  Search modules and event-scoped face search flow
- Related experiment:
  [`experiments/face_recognition_spike/README.md`](../../../experiments/face_recognition_spike/README.md)
- Related design:
  [`2026-07-26-local-selfie-search-design.md`](2026-07-26-local-selfie-search-design.md)
- Related ADRs: none
- ADR impact: None — reversible implementation detail

## Goal

Answer one narrow experimental question before investing in the full 30-person benchmark:

> Can the existing YuNet/SFace pipeline use a face crop as a proxy selfie and return visually
> useful photos of the same person near the top of an exact event-local search?

The result is a qualitative ML smoke test. It is not a measurement of accuracy, a production
identity claim, or evidence that the system is ready for Django, workers, public selfies, or
biometric-data operations.

## Scope

The smoke test remains inside `experiments/face_recognition_spike/` and uses trusted local inputs:

- one existing completed face index;
- one compatible benchmark proposal containing proxy-selfie query crops;
- the same local YuNet and SFace model files used to create the index;
- five to ten queries selected in stable proposal order; and
- the original local event photos referenced by the index and proposal.

One command processes the selected queries, performs exact face search, and writes a compact JSON
result plus a local HTML report for visual inspection.

The existing full benchmark design remains a possible later experiment. For the current delivery,
this smoke test replaces its unimplemented retrieval, metric, evaluation-artifact, and full
30-person execution scope.

## Public Interface

```text
face_spike smoke-search
  --proposal PROPOSAL
  --index INDEX
  --run RUN
  --photos PHOTOS
  --yunet-model YUNET
  --sface-model SFACE
  --output OUTPUT
  --query-count 5
  --limit 10
```

`--run` resolves the proposal's relative query-crop references. `--photos` resolves source-photo
references in the local report. These roots remain runtime inputs because external artifacts must
not persist absolute local paths. `--query-count` selects the first five to ten proposal queries in
their stable declared order and defaults to `5`. `--limit` controls the number of unique photos
rendered per query and defaults to `10`.

The command accepts a fresh output path and produces:

```text
OUTPUT/
  results.json
  report.html
```

## Data Flow

For each selected query:

1. Read the proxy-selfie crop referenced by the proposal.
2. Run it through the real YuNet detection, configured quality checks, SFace alignment, and SFace
   embedding path. Do not reuse the query face's vector from the gallery index.
3. Require exactly one acceptable query face.
4. Remove every indexed face whose source filename equals the query's source filename.
5. Compute exact cosine distance from the normalized query vector to all remaining index vectors.
6. Sort face matches by ascending distance and stable face ID.
7. Group matches by source filename and keep the lowest-distance face for each photo.
8. Sort the unique photos by ascending distance and stable filename, then retain the requested
   top results.

Cluster membership influences only the existing proposal from which proxy queries are selected. It
does not add, remove, or rank search results.

## Result Contract

`results.json` records:

- the input identities needed to reproduce the smoke run;
- model and index compatibility identifiers already available from the inputs;
- query count and result limit;
- for each query, its query face ID, crop reference, and source filename;
- for each result, the source filename, matched face ID, bounding box, rank, and cosine distance;
  and
- query embedding and search durations as observational diagnostics, not benchmark claims.

`report.html` shows, for every query:

- the query crop and source filename;
- the ranked unique source photos;
- the matched face bounding box;
- cosine distance; and
- a local relative link to the original photo.

The report is bounded to the selected queries and top results. It contains no annotation UI,
threshold control, metric dashboard, or raw embedding vectors.

## Happy-Path Failure Boundary

The command rejects only failures that would make the qualitative result misleading or impossible:

- a required proposal, index, model, crop, or photo input is unavailable;
- proposal, index, or model compatibility needed for valid vector comparison does not match;
- a query crop cannot be decoded;
- a query produces anything other than exactly one acceptable normalized embedding;
- the query embedding dimension differs from the index;
- excluding the query source photo leaves no gallery result; or
- the output path already exists.

Any selected-query failure prevents a completed report. The command does not publish a partial
success that could be mistaken for a complete smoke run.

## Explicit Non-Goals

This short-lived trusted-local experiment does not add:

- adversarial schema, path, symlink, or report-tampering protection;
- recovery guarantees for rare filesystem or cleanup failures;
- exhaustive validation of already trusted completed artifacts;
- manual relevance annotations;
- calibration or evaluation splits;
- thresholds, precision, recall, F1, MRR, mAP, coverage, or quality slices;
- immutable evaluation evidence suitable for comparing algorithm revisions;
- approximate-nearest-neighbour search;
- cluster expansion;
- Django, database, worker, API, upload, retention, or public-selfie integration; or
- production security, privacy, legal, or biometric-governance claims.

## Acceptance Criteria

The smoke experiment is complete when:

1. One command processes five to ten proposal queries with the real YuNet/SFace query path.
2. The query's complete source photo is absent from its result list.
3. Every source photo appears at most once and is scored by its best matching face.
4. `results.json` preserves the deterministic machine-readable top results and matched face
   geometry.
5. `report.html` lets a reviewer visually inspect the query and top ten local photos without
   another tool.
6. A small synthetic happy path proves exact ordering, full-photo holdout, unique-photo
   aggregation, and JSON/HTML creation.
7. One failed query prevents a completed partial report.
8. A real local run is judged only qualitatively: whether the same person visibly appears among
   useful top results for most selected queries.

The smoke result must be described as promising, inconclusive, or weak based on visual review. It
must not be reported as a measured recognition accuracy.

## Architecture Reconciliation

The design stays within the repository's isolated experimental boundary and the proposed
event-scoped Recognition and Search flow. It makes no production module, deployment, storage, or
data-lifecycle decision. No accepted ADR governs or is changed by this reversible local
implementation detail.
