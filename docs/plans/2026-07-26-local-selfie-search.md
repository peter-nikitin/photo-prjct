# Local Selfie Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The repository
> `AGENTS.md` overrides generic per-task commit guidance: leave implementation and review fixes
> unstaged, then create one final task commit only after independent approval and final verification.

- Date: 2026-07-26
- Status: Draft
- Owner: project maintainer
- Related specification:
  [`docs/superpowers/specs/2026-07-26-local-selfie-search-design.md`](../superpowers/specs/2026-07-26-local-selfie-search-design.md)
- Related architecture:
  [`docs/architecture.md`](../architecture.md), proposed Recognition and Search modules,
  event-scoped search flow, security/privacy boundaries
- Related ADRs: none
- ADR impact: None — reversible implementation detail

## Goal

Deliver the approved local 30-person face-retrieval benchmark, exact event-scoped selfie-query
baseline, held-out evaluation, and review evidence without Django, workers, or production
integration.

## Architecture

Build a private immutable face index from the existing event inventory, use it with the immutable
cluster run to construct and manually finalize an independent face-instance benchmark, and embed
each query crop through the real YuNet/SFace path before exact cosine search. Rank face instances,
aggregate them to unique photos, calibrate on 15 fixed people, and publish the final metrics for the
other 15 with bounded local review pages.

## Tech stack

Python 3.12, NumPy, OpenCV YuNet/SFace, Pillow, standard-library CSV/JSON/HTML generation, pytest,
Ruff, and mypy. No new runtime dependency or ANN engine.

## Global constraints

- Keep all behavior inside `experiments/face_recognition_spike/`, its tests, and documentation.
- Preserve the macOS-only offline experiment boundary and the current `.venv`-first workflow.
- Process decoded photos one at a time and release RGB/BGR arrays before decoding the next photo.
- Keep source photos, crops, models, embeddings, indexes, annotations, benchmarks, and evaluations
  outside Git and out of CI artifacts.
- Treat embeddings as private biometric-derived experiment data.
- Never mutate a completed cluster run or another completed output.
- Write every external output through a hidden sibling staging directory and publish atomically.
- Use clusters only to propose annotation candidates; only manual face-level labels define
  relevance.
- Exclude the query's complete source photo from its gallery.
- Search one event with exact normalized cosine distance and no cluster expansion.
- Require exactly 30 valid query people with at least three confirmed relevant non-query photos
  each.
- Fix the 15-person calibration and 15-person evaluation split before threshold selection.
- Do not claim a production identity match, production readiness, or biometric-governance approval.
- Follow the root-controller-only Git boundary and create no intermediate implementation commits.

## Scope

Implements the approved specification without changing scope.

## Acceptance criteria

Use the specification's [Acceptance Criteria](../superpowers/specs/2026-07-26-local-selfie-search-design.md#acceptance-criteria).
This plan additionally requires each CLI stage to reject incompatible inputs without publishing a
completed output and requires the final repository checks to match the current CI workflow.

## Planned file structure and interfaces

### New focused modules

- `face_spike/index.py`: index domain values, source-run reconciliation, vector validation, and
  one-photo-at-a-time index construction.
- `face_spike/index_artifacts.py`: immutable `manifest.json`, `faces.json`, and `embeddings.npz`
  writer/loader with compatibility validation.
- `face_spike/benchmark.py`: deterministic query selection, annotation-pool construction,
  annotation validation, fixed split, and finalized benchmark values.
- `face_spike/benchmark_artifacts.py`: proposal/final benchmark writers and strict CSV/JSON
  annotation import.
- `face_spike/benchmark_report.py`: bounded local annotation UI and versioned local-storage
  import/export behavior.
- `face_spike/retrieval.py`: query-image processing, exact face ranking, full-photo holdout, and
  unique-photo aggregation.
- `face_spike/retrieval_metrics.py`: calibration threshold selection and ranked/thresholded
  unique-photo metrics.
- `face_spike/retrieval_artifacts.py`: immutable evaluation artifacts and compatible input loading.
- `face_spike/retrieval_report.py`: bounded evaluation index and per-query error-review pages.

### Existing modules to modify

- `face_spike/analysis.py`: expose a focused single-image analysis entrypoint reused by index and
  query processing without changing cluster-run behavior.
- `face_spike/cli.py`: add `build-index`, `build-benchmark`, `finalize-benchmark`, and
  `evaluate-benchmark` commands and sanitized exit semantics.
- `face_spike/__main__.py`: retain the existing CLI entrypoint; change only if command dispatch
  requires it.
- `README.md`: document commands, privacy boundaries, artifact schemas, honest metrics, and the
  completed external evidence.
- `tests/fixtures.py`: add generated source-run, index, benchmark, annotation, and ranking helpers.
- `tests/test_model_smoke.py`: extend the opt-in real-model smoke through public index and evaluate
  commands.

### New test modules

- `tests/test_index.py`
- `tests/test_index_artifacts.py`
- `tests/test_benchmark.py`
- `tests/test_benchmark_artifacts.py`
- `tests/test_benchmark_report.py`
- `tests/test_retrieval.py`
- `tests/test_retrieval_metrics.py`
- `tests/test_retrieval_artifacts.py`
- `tests/test_retrieval_report.py`
- `tests/test_selfie_search_cli.py`

## Implementation

### Task 1: Reusable single-image analysis contract

**Files:**

- Modify: `experiments/face_recognition_spike/face_spike/analysis.py`
- Modify: `experiments/face_recognition_spike/tests/test_analysis.py`

**Specification:** Models and parameters, Index Construction, Query Processing, and
model-independent bounded-memory tests.

**Depends on:** None.

**Produces:** `analyze_decoded_event_photo(photo, decoded, detector, recognizer, *,
quality_thresholds) -> EventPhotoAnalysis`, the shared deterministic face-ordering and quality
contract used by clustering, index construction, and query validation.

- [ ] Add tests proving the public single-photo function returns the same ordered face IDs,
  qualities, statuses, and embeddings as inventory analysis for an identical decoded photo.
- [ ] Add a regression test proving `analyze_event_photo_inventory` still releases each
  `DecodedImage` before requesting the next one.
- [ ] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q experiments/face_recognition_spike/tests/test_analysis.py
  ```

  Expected: the new public-contract test fails because the function is absent; existing tests pass.
- [ ] Extract the current decoded-image behavior behind the named public function. Keep inventory
  iteration, error conversion, diagnostic writing, face sorting, IDs, quality decisions, and
  exception semantics unchanged.
- [ ] Rerun the targeted command. Expected: all analysis tests pass.
- [ ] Self-review the diff for unchanged cluster behavior and no decoded-pixel retention.

### Task 2: Build and load the private immutable face index

**Files:**

- Create: `experiments/face_recognition_spike/face_spike/index.py`
- Create: `experiments/face_recognition_spike/face_spike/index_artifacts.py`
- Create: `experiments/face_recognition_spike/tests/test_index.py`
- Create: `experiments/face_recognition_spike/tests/test_index_artifacts.py`
- Modify: `experiments/face_recognition_spike/tests/fixtures.py`

**Specification:** Inputs and Artifact Boundaries, Private embedding index, Index Construction, and
Failure and Publication Semantics.

**Depends on:** Task 1 single-image contract.

**Produces:**

- `FaceIndexEntry(face_id, filename, face_index, bounding_box, crop_path, quality)`;
- `FaceIndex(entries, embeddings, manifest)`, with row `N` binding entry `N` to vector `N`;
- `build_face_index(...) -> FaceIndex`;
- `FaceIndexArtifactWriter.finish(index)`; and
- `load_face_index(path) -> FaceIndex`.

- [ ] Write failing tests for stable reconciliation against source `faces.json`, including exact
  filename/face-index/geometry binding, missing faces, unexpected faces, changed geometry,
  duplicate IDs, and recoverable failures that do not touch required source faces.
- [ ] Write failing tests for finite nonempty equal-dimensional normalized vectors, deterministic
  row order by `face_id`, and one-photo-at-a-time decoded-image release.
- [ ] Write failing artifact tests for `manifest.json`, `faces.json`, `embeddings.npz`, relative
  references, model and parameter hashes, source-manifest hash, schema rejection, row/count
  reconciliation, immutable destination rejection, staging cleanup, and atomic publication.
- [ ] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q \
    experiments/face_recognition_spike/tests/test_index.py \
    experiments/face_recognition_spike/tests/test_index_artifacts.py
  ```

  Expected: collection fails because the new modules are absent.
- [ ] Implement the index domain and builder using the Task 1 contract, the existing inventory and
  image limits, existing YuNet/SFace adapters, and the source run's recorded quality parameters.
  Retain only compact normalized vectors and metadata after each photo.
- [ ] Implement the artifact writer/loader. Store embeddings as a non-pickled float32 NumPy array,
  reject object arrays, and validate every declared count/hash before returning a loaded index.
- [ ] Rerun the targeted command. Expected: all index and artifact tests pass.
- [ ] Run the existing cluster tests:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q \
    experiments/face_recognition_spike/tests/test_analysis.py \
    experiments/face_recognition_spike/tests/test_cluster_artifacts.py \
    experiments/face_recognition_spike/tests/test_clustering.py
  ```

  Expected: all pass with unchanged cluster artifacts.
- [ ] Self-review the diff for deterministic binding, private-vector handling, bounded decoded
  pixels, and failure cleanup that cannot remove pre-existing outputs.

### Task 3: Expose the `build-index` CLI

**Files:**

- Modify: `experiments/face_recognition_spike/face_spike/cli.py`
- Create: `experiments/face_recognition_spike/tests/test_selfie_search_cli.py`

**Specification:** Models and parameters, Index Construction, and Failure and Publication
Semantics.

**Depends on:** Task 2 public builder and writer.

**Produces:** Public command:

```text
face_spike build-index
  --run RUN --photos PHOTOS --yunet-model MODEL --sface-model MODEL --output OUTPUT
```

The command derives detection, image-limit, and quality parameters from `RUN/manifest.json`; it
does not permit silent parameter overrides.

- [ ] Write CLI tests for required arguments, missing/incompatible inputs, existing output,
  successful builder wiring, exit `0` on success, and sanitized exit `2` on configuration,
  compatibility, model, inventory, or publication failure.
- [ ] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q \
    experiments/face_recognition_spike/tests/test_selfie_search_cli.py -k build_index
  ```

  Expected: tests fail because `build-index` is not registered.
- [ ] Add a focused immutable `BuildIndexConfig`, validation, lazy model loading, command
  registration, and dispatch. Preserve the existing `cluster`, `compare`, and `review` behavior.
- [ ] Rerun the targeted command. Expected: every `build-index` CLI test passes.
- [ ] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q experiments/face_recognition_spike/tests/test_cluster_cli.py
  ```

  Expected: all existing CLI tests pass.

### Task 4: Construct the deterministic annotation proposal

**Files:**

- Create: `experiments/face_recognition_spike/face_spike/benchmark.py`
- Create: `experiments/face_recognition_spike/face_spike/benchmark_artifacts.py`
- Create: `experiments/face_recognition_spike/tests/test_benchmark.py`
- Create: `experiments/face_recognition_spike/tests/test_benchmark_artifacts.py`
- Modify: `experiments/face_recognition_spike/tests/fixtures.py`

**Specification:** Benchmark Construction, Candidate selection, Annotation pool, Benchmark
validity, Annotation persistence, and Calibration and Evaluation Split.

**Depends on:** Task 2 compatible loaded index.

**Produces:**

- `BenchmarkQuery(query_id, query_face_id, query_filename, query_crop_path, proposed_cluster_id,
candidate_face_ids, split)`;
- `Annotation(query_id, candidate_face_id, label, note)`;
- `BenchmarkProposal(queries, source identities)`;
- `FinalBenchmark(queries, annotations, source identities)`;
- `build_benchmark_proposal(run, index, query_count=30)`;
- `finalize_benchmark(proposal, annotations)`;
- `load_annotations_csv(...)`; and
- immutable proposal/final benchmark writers and loaders.

- [ ] Write failing selection tests for eligible clusters with four distinct source photos,
  deterministic query choice, stable replacement of invalid candidates, exactly 30 distinct
  proposed people, quality/cluster-size coverage ordering, and a fatal insufficient-candidate
  result.
- [ ] Write failing pool tests proving it contains all non-query faces from the proposed cluster,
  nearest cross-cluster faces, a fixed-size deterministic distant sample, no held-out filename, no
  duplicate face, and stable ordering.
- [ ] Write failing finalization tests for exactly 30 valid queries, at least three relevant
  distinct non-query photos each, `relevant/different/uncertain` vocabulary, unreviewed exclusion,
  unknown IDs, duplicate rows, wrong query ownership, held-out-photo leakage, and deterministic
  replacement before finalization.
- [ ] Write failing split tests for a stable stored 15/15 assignment, with no person in both halves.
  Stratify deterministically by cluster-size and available query-quality bands, then use `query_id`
  as the final tie-breaker.
- [ ] Write failing artifact/import tests for schema/source hashes, strict CSV headers, optional
  single-line note, JSON parity, immutable publication, and atomic rejection that preserves the
  prior annotation map.
- [ ] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q \
    experiments/face_recognition_spike/tests/test_benchmark.py \
    experiments/face_recognition_spike/tests/test_benchmark_artifacts.py
  ```

  Expected: collection fails because the benchmark modules are absent.
- [ ] Implement the smallest domain, selection, pool, validation, split, import, and immutable
  artifact behavior that satisfies the tests. Do not copy vectors into either benchmark artifact.
- [ ] Rerun the targeted command. Expected: all benchmark tests pass.
- [ ] Self-review that cluster IDs only propose candidates and no cluster membership becomes an
  implicit annotation.

### Task 5: Build the bounded browser annotation workflow

**Files:**

- Create: `experiments/face_recognition_spike/face_spike/benchmark_report.py`
- Create: `experiments/face_recognition_spike/tests/test_benchmark_report.py`

**Specification:** Annotation pool, Annotation persistence, Review Artifacts, and privacy
boundaries.

**Depends on:** Task 4 proposal values and annotation schema.

**Produces:** `write_benchmark_report(staging_root, proposal, run, photos_root)`, generating
`report.html` plus one bounded `queries/<query-id>/index.html` page per query.

- [ ] Write structural report tests for exactly 30 query links, query crop and held-out filename,
  candidate crop/source links, labels `relevant/different/uncertain`, candidate provenance, and no
  embedding serialization.
- [ ] Write JavaScript contract tests for versioned bundle-scoped local-storage keys, strict CSV
  export/import headers, source/query validation, duplicate rejection, atomic replacement, note
  normalization, and unchanged state after a malformed import.
- [ ] Write bounded-render tests proving the index contains only one representative per query and a
  query page paginates candidate cards instead of embedding all full source photos.
- [ ] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q experiments/face_recognition_spike/tests/test_benchmark_report.py
  ```

  Expected: collection fails because the report module is absent.
- [ ] Implement escaped static HTML/JavaScript using relative local links and the exact Task 4
  schema. Reuse the review module's safe patterns where applicable without coupling the two
  artifact formats.
- [ ] Rerun the targeted command. Expected: all benchmark-report tests pass.
- [ ] Manually open a generated fixture report and verify navigation, pagination, label changes,
  export, valid re-import, invalid re-import, and refresh persistence.

### Task 6: Expose proposal and finalization commands

**Files:**

- Modify: `experiments/face_recognition_spike/face_spike/cli.py`
- Modify: `experiments/face_recognition_spike/tests/test_selfie_search_cli.py`

**Specification:** Entire Benchmark Construction section and Failure and Publication Semantics.

**Depends on:** Tasks 4–5.

**Produces:**

```text
face_spike build-benchmark
  --run RUN --index INDEX --photos PHOTOS --output PROPOSAL --query-count 30

face_spike finalize-benchmark
  --proposal PROPOSAL --annotations-csv CSV --output BENCHMARK
```

`--query-count` exists for generated tests and smoke runs; a final evidence benchmark is accepted
only with the specification value `30`.

- [ ] Add CLI tests for argument validation, compatible run/index/source hashes, `query-count`,
  insufficient candidates, malformed annotations, invalid 30-query finalization, existing output,
  successful wiring, and sanitized exit codes.
- [ ] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q \
    experiments/face_recognition_spike/tests/test_selfie_search_cli.py \
    -k "build_benchmark or finalize_benchmark"
  ```

  Expected: tests fail because both commands are absent.
- [ ] Add focused config values and lazy dispatch for both commands. Validate all inputs before
  creating staging output and preserve prior CLI commands.
- [ ] Rerun the targeted command. Expected: all proposal/finalization CLI tests pass.
- [ ] Run all benchmark tests. Expected: all pass.

### Task 7: Process queries and rank unique photos

**Files:**

- Create: `experiments/face_recognition_spike/face_spike/retrieval.py`
- Create: `experiments/face_recognition_spike/tests/test_retrieval.py`

**Specification:** Query Processing and Retrieval and Photo Ranking.

**Depends on:** Task 1 analysis contract, Task 2 index, and Task 4 finalized benchmark.

**Produces:**

- `QueryStatus` with the exact seven specification states;
- `QueryEmbeddingResult(query_id, status, embedding, quality, durations)`;
- `FaceSearchResult(face_id, filename, distance, geometry)`;
- `PhotoSearchResult(filename, best_face_id, distance)`;
- `embed_query_crop(...) -> QueryEmbeddingResult`;
- `rank_gallery_faces(query, index, held_out_filename)`;
- `aggregate_unique_photos(face_results)`; and
- `evaluate_queries(benchmark, index, query_processor)`.

- [ ] Write failing query tests for exactly one acceptable detection and explicit `no_face`,
  `multiple_faces`, `quality_rejected`, `alignment_failed`, `embedding_failed`, and
  `invalid_embedding` outcomes. Prove it never selects the largest of multiple faces and never
  reuses the index vector for the query.
- [ ] Write failing search tests for full-filename holdout across all faces, exact cosine distance,
  ascending distance, `face_id` tie-break, unique-photo aggregation by best face, filename
  tie-break, matched geometry preservation, and no cluster expansion.
- [ ] Write failure tests proving any non-`ok` benchmark query prevents a completed evaluation.
- [ ] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q experiments/face_recognition_spike/tests/test_retrieval.py
  ```

  Expected: collection fails because `retrieval.py` is absent.
- [ ] Implement the query adapter and exact vectorized NumPy search. Record query embedding and
  search durations separately and retain full machine-readable rankings.
- [ ] Rerun the targeted command. Expected: all retrieval tests pass.
- [ ] Self-review every query path for full-photo holdout and absence of implicit primary-face
  selection.

### Task 8: Calibrate and compute held-out metrics

**Files:**

- Create: `experiments/face_recognition_spike/face_spike/retrieval_metrics.py`
- Create: `experiments/face_recognition_spike/tests/test_retrieval_metrics.py`

**Specification:** Calibration and Evaluation, Threshold calibration, and Metrics.

**Depends on:** Task 7 deterministic per-query photo rankings.

**Produces:**

- `CalibrationPoint(threshold, precision, recall, f1, coverage)`;
- `CalibrationResult(selected_threshold, curve)`;
- `QueryMetrics` and `AggregateMetrics`;
- `calibrate_threshold(calibration_rankings, annotations)`;
- `measure_ranked_queries(rankings, annotations, ks=(1, 5, 10))`; and
- `measure_thresholded_queries(rankings, annotations, threshold)`.

- [ ] Write table-driven failing tests for `Recall@1/5/10`, `Precision@5/10`, reciprocal rank,
  average precision, mean aggregation, thresholded precision/recall/F1, and coverage.
- [ ] Include queries with fewer than ten returned photos, no accepted results, multiple relevant
  photos, repeated face matches aggregated to one photo, `uncertain`, and unreviewed candidates.
- [ ] Write failing calibration tests that maximize F1, then higher recall, then the lower distance
  threshold; prove only calibration query IDs participate and evaluation IDs cannot affect the
  selected threshold.
- [ ] Write failing slice tests for stable size, sharpness, confidence, and cluster-size bands with
  explicit sample counts.
- [ ] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q experiments/face_recognition_spike/tests/test_retrieval_metrics.py
  ```

  Expected: collection fails because the metrics module is absent.
- [ ] Implement metric functions over unique-photo results. Exclude `uncertain` and unreviewed
  evidence from denominators and always report annotation coverage counts beside precision-like
  measures.
- [ ] Rerun the targeted command. Expected: all metrics tests pass.
- [ ] Self-review formulas against hand-calculated fixtures and confirm evaluation never retunes
  the calibration threshold.

### Task 9: Publish immutable evaluation evidence and HTML error review

**Files:**

- Create: `experiments/face_recognition_spike/face_spike/retrieval_artifacts.py`
- Create: `experiments/face_recognition_spike/face_spike/retrieval_report.py`
- Create: `experiments/face_recognition_spike/tests/test_retrieval_artifacts.py`
- Create: `experiments/face_recognition_spike/tests/test_retrieval_report.py`

**Specification:** Review Artifacts, Failure and Publication Semantics, and Acceptance Criteria.

**Depends on:** Tasks 7–8.

**Produces:** `EvaluationArtifactWriter.finish(...)`, writing `manifest.json`,
`calibration.json`, `metrics.json`, `rankings/<query-id>.json`, `report.html`, and one bounded
`queries/<query-id>/index.html` per query.

- [ ] Write artifact tests for benchmark/index/model/parameter hashes, fixed split, complete
  calibration curve, selected threshold, held-out metrics, duration fields, annotation counts,
  full per-query rankings, stable JSON, immutable destination rejection, staging cleanup, and
  atomic publication.
- [ ] Write report tests for calibration versus evaluation labelling, threshold decision, query
  quality, first relevant rank, false-positive/false-negative highlighting, uncertain evidence,
  matched face geometry/crop, source-photo links, and escaped content.
- [ ] Write boundedness tests proving the root page summarizes 30 queries while each detail page
  paginates results and does not embed full vectors or every full-resolution photo.
- [ ] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q \
    experiments/face_recognition_spike/tests/test_retrieval_artifacts.py \
    experiments/face_recognition_spike/tests/test_retrieval_report.py
  ```

  Expected: collection fails because both modules are absent.
- [ ] Implement compatible input validation, deterministic JSON, report generation, and atomic
  writer behavior.
- [ ] Rerun the targeted command. Expected: all evaluation artifact/report tests pass.
- [ ] Manually inspect a generated fixture report for navigation, result ordering, labels, error
  highlights, and bounded rendering.

### Task 10: Expose the complete benchmark evaluation command

**Files:**

- Modify: `experiments/face_recognition_spike/face_spike/cli.py`
- Modify: `experiments/face_recognition_spike/tests/test_selfie_search_cli.py`

**Specification:** Query Processing through Review Artifacts and all publication semantics.

**Depends on:** Tasks 7–9.

**Produces:**

```text
face_spike evaluate-benchmark
  --benchmark BENCHMARK --index INDEX
  --yunet-model MODEL --sface-model MODEL --output EVALUATION
```

- [ ] Add CLI tests for exact 30-query/fixed-split enforcement, benchmark/index/model compatibility,
  existing output, query failure, calibration isolation, successful evaluation wiring, and
  sanitized exit `2` without completed output for every fatal failure class.
- [ ] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q \
    experiments/face_recognition_spike/tests/test_selfie_search_cli.py \
    -k evaluate_benchmark
  ```

  Expected: tests fail because `evaluate-benchmark` is absent.
- [ ] Add the focused evaluation config and lazy command dispatch. Load and validate every
  dependency before creating the output staging directory.
- [ ] Rerun the targeted command. Expected: every evaluation CLI test passes.
- [ ] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q experiments/face_recognition_spike/tests/test_selfie_search_cli.py
  ```

  Expected: all selfie-search CLI tests pass.

### Task 11: Document and smoke-test the complete local workflow

**Files:**

- Modify: `experiments/face_recognition_spike/README.md`
- Modify: `experiments/face_recognition_spike/tests/test_model_smoke.py`

**Specification:** Scope, Real-model smoke, Full benchmark, Operational/Privacy impact, and all
command-facing contracts.

**Depends on:** Tasks 1–10.

**Produces:** Reproducible operator instructions and an opt-in real-model public-command smoke.

- [ ] Extend the smoke fixture to create a small compatible cluster run, build its external index,
  create/finalize a reduced query-count benchmark, and evaluate one query through real
  YuNet/SFace. Assert full-photo holdout and one unique-photo ranking without asserting production
  accuracy.
- [ ] Run the smoke test without environment variables:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q \
    experiments/face_recognition_spike/tests/test_model_smoke.py -m face_models
  ```

  Expected: skipped with the documented missing-path reason.
- [ ] Document the four public commands, their ordering, exact input compatibility, artifact
  meanings, manual export/finalization loop, fixed split, metric interpretation, privacy,
  immutability, failure behavior, and prohibition on committing vectors or face data.
- [ ] Run the opt-in real-model smoke with authorized local paths for models, photos, cluster run,
  and a fresh temporary external artifact root. Expected: exit `0`, one complete evaluation, no
  source mutation, and no Django/database access.
- [ ] Run the entire model-independent spike suite:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q experiments/face_recognition_spike/tests -m "not face_models"
  ```

  Expected: all tests pass; record exact passed/skipped counts.

### Task 12: Build and manually finalize the 30-person benchmark

**Files:** External immutable artifacts only. Update the README aggregate evidence after Task 13.

**Specification:** Benchmark Construction and Full benchmark.

**Depends on:** Task 11 model-independent suite and real-model smoke.

**Produces:** One compatible full-event index, one immutable 30-person annotation proposal, one
exported annotation CSV, and one finalized 30-person benchmark.

- [ ] Resolve the current authorized full-event cluster run, source photos, and model paths.
  Verify their recorded hashes and confirm the proposed index/proposal/final benchmark output paths
  do not exist; use the next unused numeric suffix rather than overwriting evidence.
- [ ] Run `build-index` with the current quality-gated full-event run. Expected: exit `0`; index
  counts reconcile with every `ok` source face; no source artifact changes.
- [ ] Run `build-benchmark --query-count 30`. Expected: exit `0`; proposal contains 30 distinct
  people, 15 calibration and 15 evaluation assignments, and no candidate from its query photo.
- [ ] Open `report.html` and manually label every candidate needed to establish relevant,
  different, or uncertain evidence. Inspect target faces directly in group photos; do not infer
  identity from co-occurrence.
- [ ] Export the combined annotation CSV, import it back into the same page, and verify an
  intentionally malformed copy is rejected without changing browser state.
- [ ] Run `finalize-benchmark`. When a person lacks three confirmed relevant non-query photos,
  regenerate a new immutable proposal using the next eligible deterministic replacement and
  preserve the earlier proposal as evidence.
- [ ] Reconcile the finalized artifact: exactly 30 queries, 15/15 fixed split, at least three
  distinct relevant photos each, no held-out-photo annotations, known face IDs only, and matching
  run/index hashes.
- [ ] Record artifact SHA-256 values and aggregate label/coverage counts without recording personal
  filenames in Git.

### Task 13: Run the held-out retrieval evaluation and record evidence

**Files:**

- External immutable evaluation artifact
- Modify: `experiments/face_recognition_spike/README.md`

**Specification:** Calibration and Evaluation, Review Artifacts, Acceptance Criteria, and honest
interpretation.

**Depends on:** Task 12 finalized benchmark.

**Produces:** One final immutable full-event evaluation and repository documentation of aggregate
results.

- [ ] Confirm a fresh evaluation output path and run `evaluate-benchmark` once. Expected: exit `0`,
  all 30 query crops embed successfully, calibration uses only its fixed 15 people, and evaluation
  uses the selected threshold without retuning.
- [ ] Reconcile `manifest.json`, `calibration.json`, `metrics.json`, and all 30 ranking files.
  Confirm every ranking excludes its query filename and contains unique photos ordered by the best
  matched face distance.
- [ ] Manually inspect representative evaluation top hits, false positives, false negatives,
  quality extremes, and group-photo ambiguities. Confirm every held-out error links to reviewable
  face-level evidence.
- [ ] Record in the README the commands, non-sensitive parameters, counts, calibration threshold,
  held-out `Recall@1/5/10`, `Precision@5/10`, MRR, mAP, thresholded precision/recall/F1/coverage,
  annotation coverage, latency, quality slices, limitations, and one narrow next experiment.
- [ ] Explicitly state that the query crops are proxies rather than real selfies, the sample is
  small, negative annotations are coverage-bounded, and the measured result does not establish
  production identity accuracy.
- [ ] Do not rerun or retune the held-out evaluation in response to its metric values. Any
  subsequent algorithm change requires a new immutable experiment and a new evaluation decision.

### Final task: Independent review, full verification, and architecture reconciliation

**Files:** All task files above plus the approved specification and this plan.

**Specification:** Entire approved design.

**Depends on:** Tasks 1–13.

**Produces:** Independently approved implementation, complete CI-equivalent evidence, architecture
reconciliation, and one final task commit.

- [ ] Prepare the complete unstaged task diff, including new untracked task files, and dispatch one
  independent reviewer for specification compliance, retrieval correctness, leakage prevention,
  metric math, calibration isolation, private data, bounded memory, immutable artifacts, and CLI
  failure semantics.
- [ ] Return all fixes to the same implementer and re-review with the same reviewer until approved.
  Neither subagent may stage, commit, push, or delegate further.
- [ ] Run focused checks:

  ```sh
  .venv/bin/ruff format --check experiments/face_recognition_spike
  .venv/bin/ruff check experiments/face_recognition_spike
  .venv/bin/mypy experiments/face_recognition_spike/face_spike
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q experiments/face_recognition_spike/tests -m "not face_models"
  ```

  Expected: every command exits `0`; record exact test counts.
- [ ] Run the opt-in real-model smoke with the exact authorized external paths used in Task 11.
  Expected: exit `0`; record the exact test count.
- [ ] Re-read `.github/workflows/ci.yml` and run the current repository-wide equivalents:

  ```sh
  .venv/bin/ruff format --check .
  .venv/bin/ruff check .
  .venv/bin/mypy
  DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=127.0.0.1 DB_PORT=5432 \
  SECRET_KEY=ci-not-a-secret DEBUG=False ALLOWED_HOSTS=localhost,127.0.0.1 \
  .venv/bin/pytest --cov --cov-report=term-missing
  DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=127.0.0.1 DB_PORT=5432 \
  SECRET_KEY=ci-not-a-secret DEBUG=False ALLOWED_HOSTS=localhost,127.0.0.1 \
  .venv/bin/python src/backend/manage.py check
  DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=127.0.0.1 DB_PORT=5432 \
  SECRET_KEY=ci-not-a-secret DEBUG=False ALLOWED_HOSTS=localhost,127.0.0.1 \
  .venv/bin/python src/backend/manage.py makemigrations --check --dry-run
  npm ci
  npm run test:js
  npm run test:visual
  ```

  Expected: formatting, lint, typing, Python coverage, Django system checks, migration drift,
  JavaScript tests, and Playwright visual regression all exit `0`; record exact category counts.
- [ ] Compare the delivered behavior with `docs/architecture.md` and all accepted ADRs. Expected
  outcome: `None — reversible implementation detail`; the experiment conforms to proposed
  event-scoped Recognition/Search but changes no implemented production fact.
- [ ] Verify `git status --short` contains no source photos, models, embeddings, index, benchmark,
  evaluation, cache, browser download, or absolute-path evidence.
- [ ] After reviewer approval and the final green checks, stage only the approved task code, tests,
  README, specification link update, and this plan. Create one task commit; do not create
  intermediate implementation or review-fix commits.

## Verification

The final task contains the exact focused, real-model, and repository-wide commands. Completion
requires every command to exit `0`, exact test counts to be recorded, the 30-query artifacts to
reconcile, and all final evaluation errors to be reviewable.

## Operational impact and rollout

None. The result is a local macOS-only offline experiment with external private artifacts. It adds
no runtime configuration, migration, deployed container, production dependency, API, background
process, or data flow.

## Rollback

Revert the eventual single implementation commit. External immutable index, proposal, benchmark,
and evaluation artifacts are not changed by a code rollback; remove them only through a separate
explicitly authorized local-data action. Existing cluster runs remain untouched.

## Open questions

None.
