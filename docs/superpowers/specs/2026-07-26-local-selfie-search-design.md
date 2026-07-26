# Local Selfie Search Design

## Status

Approved in conversation on 2026-07-26.

- Related architecture: [`docs/architecture.md`](../../architecture.md), proposed Recognition and
  Search modules, event-scoped face search flow, and face-data security constraints
- Related experiment:
  [`experiments/face_recognition_spike/README.md`](../../../experiments/face_recognition_spike/README.md)
- Related clustering design:
  [`2026-07-26-all-people-face-clustering-design.md`](2026-07-26-all-people-face-clustering-design.md)
- Related ADRs: none
- ADR impact: None — reversible implementation detail
- Implementation plan: pending maintainer review of this specification

## Goal

Extend the isolated local face-recognition experiment with a reproducible benchmark and exact
face-to-face retrieval path that approximates selfie search. Use face crops from the existing
clustering report as query images, build a manually reviewed face-instance relevance set, search
the remaining event photos, and measure ranked retrieval quality before any Django, worker, or
production integration.

The outcome is an evidence-backed ML and search baseline, not a production identity claim or a
customer-facing feature.

## Scope

The change remains inside `experiments/face_recognition_spike/`, its tests, and its documentation.
It uses the current macOS-only YuNet/SFace runtime and external local artifacts.

The experiment will:

- build a 30-person benchmark from quality-accepted face instances in one immutable clustering run;
- use one reviewed query crop per person;
- require at least three manually confirmed relevant photos per query after holdout;
- exclude the query's entire source photo from that query's gallery;
- create a private immutable local index of normalized SFace embeddings;
- embed every query through the real detection, alignment, and recognition path;
- perform exact cosine-distance search over all eligible face instances in the event;
- aggregate face matches to uniquely ranked source photos;
- calibrate one acceptance threshold on 15 people and report final quality on 15 held-out people;
- publish machine-readable metrics and bounded local HTML review artifacts; and
- preserve model, parameter, input, and annotation provenance.

It will not:

- integrate with Django, PostgreSQL, Object Storage, task queues, workers, APIs, or public pages;
- accept arbitrary production selfies or define their storage lifecycle;
- add an approximate-nearest-neighbour engine;
- search across events;
- use cluster membership as relevance ground truth;
- automatically identify people or attach names to them;
- use Peakshot assignments as training input;
- expand direct matches through clusters in the first retrieval baseline; or
- claim production readiness or biometric-governance approval.

## Design Principles

### Direct retrieval is the baseline

Search compares the normalized query embedding with every eligible normalized gallery face
embedding. Clusters help select annotation candidates and explain errors, but they do not determine
search relevance or result membership.

This separates retrieval quality from clustering quality. Fragmentation does not hide a good direct
match, and a false cluster merge does not automatically add unrelated photos to the result.

### The benchmark uses independent manual relevance

The existing clustering result is algorithm output, not ground truth. A reviewer labels face
instances as `relevant`, `different`, or `uncertain` relative to each query. Only manual labels
determine metrics.

### Photo holdout prevents trivial leakage

For each query, every face instance from the query's source filename is removed before ranking.
Excluding only the selected face would permit near-duplicate context or another occurrence in the
same source image to inflate the result.

### Exact search precedes search-engine selection

The current event contains only thousands of accepted faces. Exact NumPy cosine search is small
enough to provide a deterministic quality and latency reference without introducing index
approximation, additional dependencies, or ANN tuning.

## Inputs and Artifact Boundaries

### Source clustering run

Benchmark construction consumes one completed immutable clustering run plus its original event
photo directory. The run provides stable `face_id`, filename, crop path, geometry, quality
measurements, status, and cluster membership.

The builder accepts only faces whose run status is `ok`. It records and validates the source run
manifest hash. It never changes the source run.

### Models and parameters

Index construction and query processing use the same YuNet and SFace model files, detection
threshold, image limits, minimum face size, and measured quality thresholds. Every derived
artifact records:

- model basenames, sizes, and SHA-256 hashes;
- relevant Python, OpenCV, NumPy, and Pillow versions;
- detection, quality, and preprocessing parameters;
- source inventory and run manifest hashes; and
- creation timestamps and materialization counts.

Artifact consumers reject model hashes, parameters, schemas, or source identities that do not
match their declared dependencies.

### Private embedding index

The existing clustering artifacts intentionally omit raw embeddings. Retrieval therefore creates a
separate external local index containing one normalized SFace vector per successfully embedded,
quality-accepted gallery face.

Each row binds the vector to:

- `face_id`;
- source filename;
- face index and geometry;
- crop/reference path relative to the declared source artifact; and
- quality measurements.

The index, source photos, query crops, annotations, and reports remain outside Git and must not be
uploaded as CI artifacts. The index is private biometric-derived data and is not a production data
format.

## Benchmark Construction

### Candidate selection

The benchmark builder deterministically proposes 30 distinct people from the completed clustering
run. A candidate must:

- belong to a quality-accepted cluster;
- have at least four distinct source photos before selection, so at least three can remain after
  photo holdout; and
- provide one query crop that passes the same basic query validation used by search.

Selection should cover a useful spread of cluster sizes, face sizes, sharpness, detection
confidence, pose, and lighting when the source evidence permits it. The selection manifest records
the deterministic ordering and all chosen face IDs; rerunning against identical inputs produces
the same proposal.

Cluster membership is only a candidate generator. It is not an automatic relevance label.

### Annotation pool

For each query the review UI includes:

- all other quality-accepted faces from the query's proposed cluster;
- nearest faces from other clusters, computed independently by cosine distance;
- a small deterministic sample of distant faces; and
- links to the source photos needed for face-level review.

This pool exposes potential false negatives outside the source cluster and obvious negatives
without rendering the entire event on one page.

The reviewer assigns exactly one state to every reviewed candidate:

- `relevant`: the same person as the query;
- `different`: a different person; or
- `uncertain`: identity cannot be resolved from the available face-level evidence.

Group-photo co-occurrence is not identity evidence. An ambiguous face remains `uncertain` until the
target face can be reviewed directly.

### Benchmark validity

A query becomes valid only when:

- it refers to one stable query face and source filename;
- its annotation set contains at least three `relevant` faces in distinct non-query photos;
- no relevant candidate comes from the held-out source photo; and
- all referenced face IDs and filenames exist in the declared source run.

`Uncertain` candidates are excluded from both positive and negative metric denominators. Unreviewed
candidates are not silently treated as `different`.

The initial completed benchmark contains exactly 30 valid people. If a proposed person cannot meet
the validity rule, the builder advances to the next deterministic eligible candidate rather than
weakening the rule.

### Annotation persistence

The browser stores draft annotations under versioned, bundle-scoped local-storage keys. Exported
CSV and JSON include the benchmark schema version, source manifest hash, query face ID, candidate
face ID, source filename, label, and optional concise review note.

Import validates headers, schemas, source hashes, known IDs, duplicate rows, label vocabulary, and
query ownership before atomically replacing the local draft. Malformed imports leave prior
annotations unchanged.

Finalization writes a new immutable benchmark artifact and never modifies the proposal or source
run.

## Index Construction

`build-index` repeats detection, quality evaluation, alignment, and embedding over the declared
event photo inventory. Reprocessing is necessary because raw embeddings are not present in the
clustering run.

The builder reconciles detected face instances with the source run by stable filename, ordered face
index, and geometry. Any mismatch that could bind a vector to the wrong face is fatal. Per-image
decode/detection failures and per-face alignment/embedding failures may be recorded only when they
do not invalidate a benchmark query or its required relevant set.

The index writer:

- processes one decoded photo at a time;
- releases decoded RGB/BGR arrays before the next photo;
- stores vectors in a compact matrix after individual image processing;
- verifies all vectors are finite, nonempty, equal-dimensional, and normalized;
- writes through a hidden sibling staging directory; and
- atomically publishes only a complete index.

## Query Processing

The search command consumes each benchmark query crop as an image input. It does not copy the
query's already-computed gallery embedding.

The query passes through YuNet detection, the configured quality measurements, SFace alignment, and
SFace embedding. It is valid only when exactly one acceptable face is found.

Query outcomes are explicit:

- `ok`;
- `no_face`;
- `multiple_faces`;
- `quality_rejected`;
- `alignment_failed`;
- `embedding_failed`; or
- `invalid_embedding`.

The command never chooses the largest face when multiple acceptable faces are detected. A failed
benchmark query makes the evaluation run incomplete and prevents publication of final evaluation
metrics.

## Retrieval and Photo Ranking

For one query:

1. Load the compatible event-scoped index.
2. Remove every indexed face whose source filename equals the query source filename.
3. Compute exact cosine distance from the normalized query vector to every remaining vector.
4. Sort face results by ascending distance, then stable `face_id`.
5. Group results by source filename and keep that photo's lowest-distance face.
6. Sort unique photos by ascending best-face distance, then source filename.
7. Retain the full machine-readable ranking and render only bounded top-result pages.

The result preserves the matched gallery `face_id` and geometry so every photo score is
explainable. A source photo appears at most once.

No cluster expansion occurs in this baseline.

## Calibration and Evaluation

### Split

The 30 benchmark people are assigned deterministically and permanently to:

- 15 calibration queries; and
- 15 evaluation queries.

The split is stratified where practical across the recorded query-quality and cluster-size ranges.
Its membership is stored in the immutable benchmark manifest.

### Threshold calibration

The calibration half selects one cosine-distance acceptance threshold from explicit candidate
values derived from calibration rankings. The objective and tie-breakers are fixed before looking
at evaluation results:

1. maximize calibration F1 across labelled candidate photo decisions;
2. prefer higher recall when F1 ties;
3. prefer the lower distance threshold when recall also ties.

The chosen threshold and the complete calibration curve are stored. The evaluation half uses that
threshold once without retuning.

Ranked metrics are also reported without threshold truncation, because they measure ordering rather
than the accept/reject operating point.

### Metrics

Calibration and held-out evaluation reports contain:

- `Recall@1`, `Recall@5`, and `Recall@10`;
- `Precision@5` and `Precision@10`;
- mean reciprocal rank;
- mean average precision over manually labelled relevant photos;
- thresholded precision, recall, F1, and query coverage;
- query embedding latency;
- exact-search latency; and
- counts for valid, failed, relevant, different, uncertain, and unreviewed evidence.

Metrics are calculated at unique-photo level. `Uncertain` and unreviewed candidates are excluded
from denominators. The report states annotation coverage so incomplete negative review cannot be
mistaken for exhaustive precision evidence.

The report also breaks down retrieval outcomes by available query face size, sharpness, confidence,
and cluster-size bands. These slices are diagnostic on a small sample and are not standalone
accuracy claims.

## Review Artifacts

One immutable evaluation contains:

- `manifest.json`;
- `calibration.json`;
- `metrics.json`;
- per-query machine-readable rankings;
- a bounded `report.html`; and
- one detail page per query.

Each query page shows the query crop, held-out source filename, ranked unique photos, matched face
boxes/crops, distances, manual labels, threshold decision, and error category. It highlights false
positives, false negatives, the first relevant rank, and uncertain evidence without loading every
full-resolution source photo into a single page.

## Failure and Publication Semantics

Fatal configuration, inventory, model, schema, compatibility, reconciliation, or publication
errors return nonzero and leave no completed output.

Recoverable per-image and per-face failures remain structured evidence. They become fatal when
they remove:

- a selected query;
- any required manually relevant face; or
- enough evidence to violate the three-relevant-photo benchmark rule.

All proposal, benchmark, index, and evaluation outputs are immutable. Writers clean incomplete
staging directories after handled failures without deleting pre-existing completed artifacts.

## Testing Strategy

### Model-independent tests

Generated fixtures and adapters cover:

- deterministic candidate selection and replacement;
- annotation pool composition;
- strict CSV/JSON import and atomic draft replacement;
- benchmark finalization and the three-relevant-photo invariant;
- source-manifest and model compatibility checks;
- stable face-to-vector reconciliation;
- vector validation and normalization;
- full-photo holdout;
- exact distance ordering and deterministic ties;
- face-to-photo aggregation;
- calibration/evaluation split stability;
- threshold objective and tie-breakers;
- ranked and thresholded metric calculations;
- uncertain/unreviewed exclusion and coverage reporting;
- query status handling;
- immutable atomic publication and failure cleanup; and
- bounded-memory release of decoded images.

### Real-model smoke

An opt-in smoke test uses the real YuNet/SFace models, a small authorized photo inventory, one
query crop, and a temporary external index. It proves the public index and search commands execute
the real preprocessing path without Django or a database.

### Full benchmark

One external immutable evidence set includes:

- the 30-person finalized benchmark;
- the compatible full-event index;
- threshold calibration on the fixed 15-person calibration split;
- one final run on the fixed 15-person evaluation split; and
- manual inspection of representative top hits, false positives, false negatives, query failures,
  group-photo ambiguity, and quality extremes.

The README records aggregate counts, parameters, metric values, limitations, and the next narrow
experiment. It does not commit personal filenames, crops, vectors, photos, models, or absolute local
paths.

## Acceptance Criteria

The ML and search baseline is complete when:

- exactly 30 manually valid query people exist;
- every query has at least three confirmed relevant photos after full-photo holdout;
- the fixed split contains 15 calibration and 15 evaluation people;
- query embeddings are recomputed through the real query path;
- no result for a query uses any face from its held-out source photo;
- exact face retrieval produces deterministic unique-photo rankings;
- the acceptance threshold is selected only on calibration people;
- all specified held-out metrics and annotation-coverage counts are published;
- every held-out false positive and false negative is inspectable in the local review;
- manifests make the benchmark, model, index, parameters, split, and evaluation reproducible;
- model-independent tests, the real-model smoke, and current repository-wide CI-equivalent checks
  pass; and
- the result is documented as directional evidence with no production or identity guarantee.

No minimum accuracy target is asserted before the first honest held-out baseline. The measured
result informs a separate decision among direct-retrieval acceptance, query/quality improvements,
or a controlled cluster-expansion experiment.

## Operational, Privacy, and Architecture Impact

There is no runtime deployment, migration, Django, storage, worker, or public API impact.

All face images and biometric-derived vectors stay in authorized local external directories.
Query crops are experiment fixtures, not retained customer selfies. Production consent, retention,
deletion, access control, encryption, audit, and incident-response policy remain unresolved and
must be designed before product integration.

The work conforms to the proposed event-scoped Recognition and Search direction in
`docs/architecture.md` but does not change implemented architecture. No accepted ADR governs this
reversible experimental implementation, so no ADR is required.
