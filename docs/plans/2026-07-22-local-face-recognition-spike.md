# Local Face Recognition Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible local CLI experiment that measures YuNet face detection and SFace
race-photo retrieval on a small, manually labelled cycling-event dataset.

**Architecture:** Keep the spike in an isolated `experiments/face_recognition_spike` Python package
with ports for detection and embedding, an OpenCV adapter, exact in-memory cosine search, immutable
filesystem run artifacts, and a static HTML review report. A dedicated Docker image pins dependencies;
personal photos, labels, weights, crops, embeddings, and results stay outside Git and CI.

**Tech Stack:** Python 3.12, OpenCV contrib 4.12.0.88, NumPy 2.2.6, Pillow 12.0.0, pytest, Docker.

## Global Constraints

- Implement the approved [local face recognition spike specification](../superpowers/specs/2026-07-22-local-face-recognition-spike-design.md) exactly.
- Do not import Django or access PostgreSQL, Object Storage, the network, or a GPU.
- Do not add experiment dependencies to `src/backend/requirements.txt` or the production image.
- Do not commit photos, labels, model files, crops, embeddings, or run outputs.
- Treat YuNet and SFace as evaluated candidates, not accepted production choices.
- Use exact brute-force cosine comparison; do not add DBSCAN, pgvector, or another vector index.
- Keep original JPEGs outside run output; only bounded annotated previews and primary face crops may
  be generated.
- Make output directories immutable by refusing to run when the requested output path exists.

---

- Date: 2026-07-22
- Status: Draft
- Owner: project maintainer
- Related specification: [Local face recognition spike](../superpowers/specs/2026-07-22-local-face-recognition-spike-design.md)
- Related architecture: [Security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries),
  [Evolution stages](../architecture.md#evolution-stages), and
  [Open decisions](../architecture.md#open-decisions)
- Related ADRs: [0004 — Keep engineering knowledge in the repository](../adr/0004-repository-engineering-knowledge.md)
- ADR impact: None — reversible implementation detail. Stop and create a separate ADR before using
  the experiment to select a production face model, vector store, or deployment architecture.

## Scope

### In scope

- Isolated package, pinned Docker runtime, adapters, CLI, CSV/JSON artifacts, annotated previews,
  crops, static report, tests, and operator documentation defined by the specification.
- One developer-prepared local dataset of approximately 50–100 photos with 5–10 rider series.
- Directional detection, embedding, retrieval, latency, and failure metrics.

### Out of scope

- Django/product integration, selfie input, clustering, persistence, production deployment, alternate
  models, and legal approval for public biometric processing.

## File map

- `experiments/face_recognition_spike/Dockerfile`: pinned, CPU-only experiment runtime.
- `experiments/face_recognition_spike/requirements.txt`: isolated exact Python dependencies.
- `experiments/face_recognition_spike/README.md`: dataset preparation, commands, privacy cleanup,
  artifact interpretation, and decision gates.
- `experiments/face_recognition_spike/.gitignore`: blocks local inputs, weights, and outputs.
- `experiments/face_recognition_spike/face_spike/__main__.py`: module entry point and exit-code mapping.
- `experiments/face_recognition_spike/face_spike/cli.py`: arguments and top-level orchestration.
- `experiments/face_recognition_spike/face_spike/domain.py`: frozen domain values, enums, and ports.
- `experiments/face_recognition_spike/face_spike/dataset.py`: label validation, safe path resolution,
  JPEG loading, and EXIF orientation.
- `experiments/face_recognition_spike/face_spike/opencv_models.py`: YuNet and SFace adapters.
- `experiments/face_recognition_spike/face_spike/pipeline.py`: per-image detection and embedding flow.
- `experiments/face_recognition_spike/face_spike/retrieval.py`: exact ranking and Recall@K.
- `experiments/face_recognition_spike/face_spike/artifacts.py`: immutable run lifecycle and CSV/JSON.
- `experiments/face_recognition_spike/face_spike/report.py`: deterministic HTML and diagnostic images.
- `experiments/face_recognition_spike/tests/`: fast unit/contract tests with generated data and fakes.
- `experiments/face_recognition_spike/tests/test_model_smoke.py`: opt-in real-model smoke test.
- `pyproject.toml`: register the `face_models` marker and include experiment sources in linting without
  adding them to Django coverage.

## Acceptance criteria

- `docker build` creates the isolated runtime and the documented run command produces all seven
  artifact classes from a valid local dataset.
- Invalid labels, unsafe paths, existing output directories, missing models, and fatal model errors
  fail with stable non-zero exit codes and never overwrite prior evidence.
- Per-image failures remain visible and do not prevent the remaining images from being evaluated.
- Exact, stable leave-one-out retrieval reports Recall@1/5/10 only for evaluable grouped queries.
- Fast tests require neither event photos nor model files and pass without network access.
- The model smoke test is excluded by default and runs only with explicit local model paths.
- Repository documentation states the measured result and decision gate without claiming production
  identity recognition.

## Implementation

### Task 1: Establish the isolated experiment contract

**Files:**
- Create: `experiments/face_recognition_spike/requirements.txt`
- Create: `experiments/face_recognition_spike/Dockerfile`
- Create: `experiments/face_recognition_spike/.gitignore`
- Create: `experiments/face_recognition_spike/face_spike/__init__.py`
- Create: `experiments/face_recognition_spike/face_spike/__main__.py`
- Create: `experiments/face_recognition_spike/face_spike/cli.py`
- Create: `experiments/face_recognition_spike/tests/test_cli.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: command-line arguments from `python -m face_spike run`.
- Produces: `face_spike.cli.main(argv: Sequence[str] | None = None) -> int`; exit `0` for a complete
  run, `2` for invalid invocation/input, and `3` for a completed report containing unexpected
  labelled-image failures.

- [ ] **Step 1: Write failing CLI contract tests**

Test that `main([])` returns `2`, `main(["unknown"])` returns `2`, an existing `--output` path is
rejected before model initialization, and the run parser requires `--photos`, `--labels`,
`--yunet-model`, `--sface-model`, and `--output`. Patch the future orchestration function so this test
does not need models.

- [ ] **Step 2: Verify the tests fail**

Run:

```sh
pytest -q experiments/face_recognition_spike/tests/test_cli.py
```

Expected: collection fails because `face_spike.cli` does not exist.

- [ ] **Step 3: Add the minimal CLI and runtime boundary**

Pin exactly:

```text
numpy==2.2.6
opencv-contrib-python-headless==4.12.0.88
Pillow==12.0.0
```

Use `python:3.12-slim`, install only this requirements file, copy only `face_spike/`, and set
`ENTRYPOINT ["python", "-m", "face_spike"]`. Add arguments with defaults `0.75`, `32`, `10`,
`12000` maximum width/height, and `100000000` maximum pixels. Validate that output does not exist
before delegating to `run_experiment(config)`.

Register the `face_models` pytest marker. Ignore exactly `/input/`, `/models/`, `/runs/`,
`/labels.csv`, `*.onnx`, `__pycache__/`, and `.pytest_cache/` inside the experiment directory.

- [ ] **Step 4: Verify the CLI contract passes**

Run:

```sh
PYTHONPATH=experiments/face_recognition_spike pytest -q experiments/face_recognition_spike/tests/test_cli.py
```

Expected: all CLI tests pass without OpenCV models or event photos.

- [ ] **Step 5: Commit the contract**

```sh
git add pyproject.toml experiments/face_recognition_spike
git commit -m "test: define local face spike contract"
```

### Task 2: Validate labels and decode bounded oriented images

**Files:**
- Create: `experiments/face_recognition_spike/face_spike/domain.py`
- Create: `experiments/face_recognition_spike/face_spike/dataset.py`
- Create: `experiments/face_recognition_spike/tests/test_dataset.py`
- Create: `experiments/face_recognition_spike/tests/fixtures.py`

**Interfaces:**
- Consumes: `labels.csv`, photo root, maximum dimension, and maximum pixel count.
- Produces: `DatasetItem(filename: str, participant_group: str | None, face_expected: FaceExpected)`;
  `LoadedImage(item: DatasetItem, rgb: NDArray, bgr: NDArray, width: int, height: int)`;
  `load_labels(path: Path, photo_root: Path) -> tuple[DatasetItem, ...]`; and
  `load_image(item: DatasetItem, photo_root: Path, limits: ImageLimits) -> LoadedImage`.

- [ ] **Step 1: Write failing dataset tests**

Cover the exact header and enum, duplicate filenames, missing files, absolute paths, `..` traversal,
symlink escape, directories posing as files, unsupported extensions, EXIF orientation 6, corrupt
JPEG, maximum width/height, and maximum decoded pixel count. Generate all fixture images in pytest's
temporary directory; do not add event photos.

- [ ] **Step 2: Verify the tests fail**

Run:

```sh
PYTHONPATH=experiments/face_recognition_spike pytest -q experiments/face_recognition_spike/tests/test_dataset.py
```

Expected: collection fails because `face_spike.dataset` does not exist.

- [ ] **Step 3: Implement strict label and image loading**

Use `csv.DictReader`, `Path.resolve()`, and `candidate.is_relative_to(photo_root.resolve())`. Accept
only `.jpg` and `.jpeg`, require unique filenames and at least one row, normalize blank group values
to `None`, and sort by filename. Use Pillow `ImageOps.exif_transpose`, call `Image.verify()` before a
fresh decode, convert to RGB, enforce limits before NumPy allocation where metadata permits, then
produce contiguous RGB and BGR arrays. Raise stable typed errors `invalid_labels`, `unsafe_path`,
`image_decode_failed`, `unsupported_image`, and `image_too_large` without raw path leakage in
user-facing messages.

- [ ] **Step 4: Verify dataset behavior**

Run:

```sh
PYTHONPATH=experiments/face_recognition_spike pytest -q experiments/face_recognition_spike/tests/test_dataset.py
```

Expected: all dataset tests pass.

- [ ] **Step 5: Commit dataset handling**

```sh
git add experiments/face_recognition_spike/face_spike experiments/face_recognition_spike/tests
git commit -m "feat: load labelled face spike images safely"
```

### Task 3: Normalize YuNet detections and SFace embeddings

**Files:**
- Create: `experiments/face_recognition_spike/face_spike/opencv_models.py`
- Create: `experiments/face_recognition_spike/face_spike/pipeline.py`
- Create: `experiments/face_recognition_spike/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `LoadedImage`, `FaceDetector` and `FaceRecognizer` protocols, threshold, and minimum face
  size.
- Produces: frozen `BoundingBox`, `FaceLandmarks`, `FaceDetection`, `FaceEmbedding`,
  `ImageAnalysis`; `YuNetDetector.detect(bgr: NDArray) -> tuple[FaceDetection, ...]`;
  `SFaceRecognizer.extract(bgr: NDArray, detection: FaceDetection) -> FaceEmbedding`; and
  `analyze_image(image: LoadedImage, detector: FaceDetector, recognizer: FaceRecognizer,
  min_face_px: int) -> ImageAnalysis`.

- [ ] **Step 1: Write failing geometry and pipeline tests**

Use fake OpenCV rows and fake adapters to verify no detections, multiple detections, clipping on all
four edges, rejection of non-finite values, invalid landmarks, confidence below threshold, boxes
smaller than `min_face_px`, deterministic largest-area primary selection with confidence then
coordinates as tie-breakers, SFace row reconstruction, feature flattening, `float32` conversion,
L2 normalization, zero norm, and non-finite embeddings.

- [ ] **Step 2: Verify the tests fail**

Run:

```sh
PYTHONPATH=experiments/face_recognition_spike pytest -q experiments/face_recognition_spike/tests/test_pipeline.py
```

Expected: collection fails because the model adapters and pipeline do not exist.

- [ ] **Step 3: Implement adapters behind protocols**

Initialize each OpenCV model once per process. Set the YuNet input size for every decoded image and
normalize its 15-value rows into bbox plus the documented five landmark pairs. Clip to image bounds
before minimum-size filtering. Preserve all accepted detections and mark exactly one primary.

Pass the normalized detection back to SFace as a one-row `float32` YuNet layout, call `alignCrop`
then `feature`, require a one-dimensional nonempty finite result, normalize it by its L2 norm, and
record `detection_failed`, `alignment_failed`, `embedding_failed`, or `invalid_embedding` without
serializing third-party exception text.

- [ ] **Step 4: Verify model-independent pipeline behavior**

Run:

```sh
PYTHONPATH=experiments/face_recognition_spike pytest -q experiments/face_recognition_spike/tests/test_pipeline.py
```

Expected: all pipeline tests pass with fake adapters.

- [ ] **Step 5: Commit the pipeline**

```sh
git add experiments/face_recognition_spike/face_spike experiments/face_recognition_spike/tests
git commit -m "feat: analyze faces with YuNet and SFace adapters"
```

### Task 4: Add stable leave-one-out retrieval and metrics

**Files:**
- Create: `experiments/face_recognition_spike/face_spike/retrieval.py`
- Create: `experiments/face_recognition_spike/tests/test_retrieval.py`

**Interfaces:**
- Consumes: successful `ImageAnalysis` records and `top_k`.
- Produces: `RetrievalMatch`, `QueryRetrieval`, `RetrievalMetrics`;
  `rank_candidates(query: ImageAnalysis, candidates: Sequence[ImageAnalysis], top_k: int) -> QueryRetrieval`;
  and `evaluate_retrieval(analyses: Sequence[ImageAnalysis], top_k: int) -> RetrievalMetrics`.

- [ ] **Step 1: Write failing retrieval tests**

Use small explicit normalized vectors to verify self-exclusion, exact cosine distance, ascending
distance, filename tie-break, top-k truncation, exclusion of blank-group and singleton-group queries
from recall denominators, inclusion of ungrouped candidates as negatives, Recall@1/5/10, zero
evaluable queries, and stable repeated results.

- [ ] **Step 2: Verify the tests fail**

Run:

```sh
PYTHONPATH=experiments/face_recognition_spike pytest -q experiments/face_recognition_spike/tests/test_retrieval.py
```

Expected: collection fails because `face_spike.retrieval` does not exist.

- [ ] **Step 3: Implement exact NumPy retrieval**

Compute `distance = 1.0 - dot(query, candidate)` over already normalized vectors, clamp only floating
roundoff to `[0.0, 2.0]`, exclude the query filename, and sort by `(distance, filename)`. Define
Recall@K as the fraction of evaluable queries with at least one same-group candidate in their first K
results. Report `null` recall rather than zero when there are no evaluable queries.

- [ ] **Step 4: Verify retrieval calculations**

Run:

```sh
PYTHONPATH=experiments/face_recognition_spike pytest -q experiments/face_recognition_spike/tests/test_retrieval.py
```

Expected: all retrieval tests pass.

- [ ] **Step 5: Commit retrieval**

```sh
git add experiments/face_recognition_spike/face_spike/retrieval.py experiments/face_recognition_spike/tests/test_retrieval.py
git commit -m "feat: evaluate exact face retrieval"
```

### Task 5: Persist immutable evidence and visual diagnostics

**Files:**
- Create: `experiments/face_recognition_spike/face_spike/artifacts.py`
- Create: `experiments/face_recognition_spike/face_spike/report.py`
- Create: `experiments/face_recognition_spike/tests/test_artifacts.py`
- Create: `experiments/face_recognition_spike/tests/test_report.py`

**Interfaces:**
- Consumes: run configuration, model hashes, analyses, retrieval results, dependency versions, and
  timestamps.
- Produces: `write_run(output: Path, result: ExperimentResult) -> None` and the exact artifact tree
  from the specification.

- [ ] **Step 1: Write failing artifact tests**

Verify exclusive output-directory creation, no modification after an existing-path error, atomic
JSON/CSV file publication through same-directory temporary files, exact CSV headers, sorted records,
JSON `null` for unavailable metrics, SHA-256 model hashes, no embedding vectors in manifest or HTML,
bounded annotated previews, lossless primary crops, HTML escaping, relative image paths, top-10
neighbor display, and stable output after normalizing timestamps in two equivalent test results.

- [ ] **Step 2: Verify the tests fail**

Run:

```sh
PYTHONPATH=experiments/face_recognition_spike pytest -q \
  experiments/face_recognition_spike/tests/test_artifacts.py \
  experiments/face_recognition_spike/tests/test_report.py
```

Expected: collection fails because artifact and report writers do not exist.

- [ ] **Step 3: Implement the artifact contract**

Create output with `mkdir(exist_ok=False)`, create `annotated/` and `crops/`, and write UTF-8 JSON
with sorted keys plus RFC 4180 CSV through `csv.DictWriter`. Record SHA-256, exact package versions,
platform, parameters, UTC timestamps, durations, counts, and stable error codes. Do not write raw
exception messages or embedding values.

Scale annotated previews to fit within `1920x1920` without enlargement, draw all accepted boxes and
confidence values, and distinguish the primary face. Save annotated previews at JPEG quality 90 and
primary crops as PNG. Build HTML using escaped text and relative URLs; include summary metrics,
failure rows, face-size buckets `<32`, `32–63`, `64–127`, `128–255`, and `>=256`, and each evaluable
query with at most ten ranked candidates.

- [ ] **Step 4: Verify artifacts and report**

Run:

```sh
PYTHONPATH=experiments/face_recognition_spike pytest -q \
  experiments/face_recognition_spike/tests/test_artifacts.py \
  experiments/face_recognition_spike/tests/test_report.py
```

Expected: all artifact and report tests pass.

- [ ] **Step 5: Commit evidence generation**

```sh
git add experiments/face_recognition_spike/face_spike experiments/face_recognition_spike/tests
git commit -m "feat: export immutable face spike evidence"
```

### Task 6: Wire orchestration, partial failures, and real-model smoke coverage

**Files:**
- Modify: `experiments/face_recognition_spike/face_spike/cli.py`
- Modify: `experiments/face_recognition_spike/face_spike/__main__.py`
- Create: `experiments/face_recognition_spike/tests/test_experiment.py`
- Create: `experiments/face_recognition_spike/tests/test_model_smoke.py`

**Interfaces:**
- Consumes: all interfaces from Tasks 2–5.
- Produces: `run_experiment(config: RunConfig) -> ExperimentResult` and the final documented CLI
  behavior.

- [ ] **Step 1: Write failing end-to-end orchestration tests**

With generated JPEGs and fake adapters, verify deterministic filename processing, continuation after
one corrupt image, no-face and embedding-failure records, complete artifacts after partial failure,
exit `3` only for unexpected failures on labelled images, exit `0` for valid no-face expectations,
fatal model initialization before output creation, and no source JPEG copied into the run.

- [ ] **Step 2: Verify orchestration tests fail**

Run:

```sh
PYTHONPATH=experiments/face_recognition_spike pytest -q experiments/face_recognition_spike/tests/test_experiment.py
```

Expected: tests fail because CLI orchestration is not connected.

- [ ] **Step 3: Implement orchestration and exit codes**

Validate all fatal inputs and initialize models before creating output. Process dataset items in
filename order while timing decode, detection, and embedding separately. Convert every recoverable
per-image failure into an `ImageAnalysis`, evaluate successful embeddings, write one complete run,
and return exit `3` if a `face_expected=yes` row ends in an unexpected error other than a valid
`no_face` result; otherwise return `0`. Keep exit `2` for fatal validation/configuration failures.

- [ ] **Step 4: Add the opt-in real-model test**

Mark the test `face_models`. Read `FACE_SPIKE_YUNET_MODEL`, `FACE_SPIKE_SFACE_MODEL`, and
`FACE_SPIKE_SMOKE_PHOTOS`; skip when any is absent. With them present, run a temporary two-photo
dataset and assert model initialization, a complete manifest, both CSV files, `metrics.json`, and
`report.html`. Do not assert recognition quality in this compatibility smoke test.

- [ ] **Step 5: Verify fast orchestration and default smoke exclusion**

Run:

```sh
PYTHONPATH=experiments/face_recognition_spike pytest -q experiments/face_recognition_spike/tests -m "not face_models"
```

Expected: all fast tests pass; no model file or personal event photo is read.

- [ ] **Step 6: Commit orchestration**

```sh
git add experiments/face_recognition_spike/face_spike experiments/face_recognition_spike/tests
git commit -m "feat: run local face recognition experiment"
```

### Task 7: Document and execute the first local directional run

**Files:**
- Create: `experiments/face_recognition_spike/README.md`
- Modify after the run: `experiments/face_recognition_spike/README.md`

**Interfaces:**
- Consumes: maintainer-authorized local photos, pseudonymous labels, and separately obtained ONNX
  model files.
- Produces: documented build/run/smoke/cleanup commands and a committed aggregate result summary
  containing no personal image, filename, label, crop, embedding, or local absolute path.

- [ ] **Step 1: Write the operator guide**

Document the exact CSV schema, one sample with synthetic filenames, dataset-size requirements,
model-file checksum inspection, Docker build, read-only input/model mounts, writable run mount,
report opening, four decision outcomes, and cleanup. State that model licensing and authorized use
must be confirmed by the maintainer before the run and that no result authorizes production use.

Use this container contract, substituting only absolute local paths outside the repository:

```sh
docker build -t findme-photo-face-spike:local experiments/face_recognition_spike
docker run --rm --network none \
  --mount type=bind,src=/absolute/photos,dst=/input/photos,readonly \
  --mount type=bind,src=/absolute/labels.csv,dst=/input/labels.csv,readonly \
  --mount type=bind,src=/absolute/models,dst=/models,readonly \
  --mount type=bind,src=/absolute/runs,dst=/output \
  findme-photo-face-spike:local run \
  --photos /input/photos --labels /input/labels.csv \
  --yunet-model /models/yunet.onnx --sface-model /models/sface.onnx \
  --output /output/run-001 --detection-threshold 0.75 --min-face-px 32 --top-k 10
```

- [ ] **Step 2: Run repository and container verification**

Run:

```sh
ruff format --check experiments/face_recognition_spike pyproject.toml
ruff check experiments/face_recognition_spike pyproject.toml
PYTHONPATH=experiments/face_recognition_spike pytest -q experiments/face_recognition_spike/tests -m "not face_models"
docker build -t findme-photo-face-spike:local experiments/face_recognition_spike
```

Expected: all commands exit `0`; fast tests report no failures; the image builds without adding any
dependency to the production image.

- [ ] **Step 3: Run the real-model smoke test explicitly**

Run with maintainer-owned paths:

```sh
FACE_SPIKE_YUNET_MODEL=/absolute/models/yunet.onnx \
FACE_SPIKE_SFACE_MODEL=/absolute/models/sface.onnx \
FACE_SPIKE_SMOKE_PHOTOS=/absolute/smoke-photos \
PYTHONPATH=experiments/face_recognition_spike \
pytest -q experiments/face_recognition_spike/tests/test_model_smoke.py -m face_models
```

Expected: the smoke test passes and its temporary artifacts are deleted by pytest.

- [ ] **Step 4: Execute and review the directional dataset run**

Prepare 5–10 groups with at least three images each and 30–50 negatives, execute the documented
network-disabled container command, open `report.html`, and verify that primary-face selection is
correct for every evaluable query. If it is not, exclude the ambiguous image and create a new run
directory; never alter the prior run.

- [ ] **Step 5: Record only aggregate evidence**

Add a dated README section containing model hashes, dependency versions, dataset counts, detection
coverage, embedding success, Recall@1/5/10, latency summaries, failure counts, the applicable
interpretation gate, and the next decision. Do not include source filenames, participant groups,
images, crops, embeddings, local paths, or the generated report.

- [ ] **Step 6: Commit documentation and aggregate evidence**

```sh
git add experiments/face_recognition_spike/README.md
git commit -m "docs: record local face spike evidence"
```

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the approved specification, `docs/architecture.md`, roadmap
  Stage 8, and ADR 0004.
- [ ] Confirm that application behavior, deployment topology, production dependencies, and current
  implemented architecture have not changed; do not mark Recognition or Search implemented.
- [ ] If the evidence selects a production model, persistence layer, public workflow, or new runtime
  boundary, stop and invoke `$write-adr` instead of encoding that choice in this spike.
- [ ] Record `None — reversible implementation detail` in the pull request when the boundary remains
  intact.

## Verification

Run:

```sh
ruff format --check experiments/face_recognition_spike pyproject.toml
ruff check experiments/face_recognition_spike pyproject.toml
PYTHONPATH=experiments/face_recognition_spike pytest -q experiments/face_recognition_spike/tests -m "not face_models"
pytest -q
docker build -t findme-photo-face-spike:local experiments/face_recognition_spike
git status --short
```

Expected:

- formatting and lint commands exit `0`;
- all fast experiment tests pass without local models or photos;
- the complete repository test suite passes;
- the isolated Docker image builds;
- `git status --short` lists no photo, label, ONNX, crop, embedding, run, or report artifact.

The real-model smoke and directional dataset run are additional explicit local checks because model
weights and personal event photos are intentionally absent from CI.

## Operational impact and rollout

None. The spike is local-only, has no migrations, settings, services, public routes, deployment
configuration, production dependency changes, network calls, or runtime effect on Django.

## Rollback

Revert the experiment commits. Delete the local Docker image and the maintainer-selected local input,
model, and run directories if their retention is no longer authorized. No database, Object Storage,
or deployed state requires rollback.

## Open questions

None. Model provenance and commercial licensing are mandatory operator preconditions for executing
the real-model run; the spike does not decide or waive them.
