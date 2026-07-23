# Local Face Recognition Spike Design

- Date: 2026-07-22
- Status: Approved
- Owner: project maintainer
- Related architecture: [Product goal](../../architecture.md#product-goal),
  [Target MVP architecture — proposed](../../architecture.md#target-mvp-architecture--proposed),
  [Security, privacy, and legal boundaries](../../architecture.md#security-privacy-and-legal-boundaries),
  [Evolution stages](../../architecture.md#evolution-stages), and
  [Open decisions](../../architecture.md#open-decisions)
- Related jobs: [PJ-008 — Find photos by face](../../product-jobs.md#pj-008--customer--find-photos-by-face)
- Related roadmap: [Stage 8 — legal and technical validation of face search](../../plans/2026-07-11-mvp-product-roadmap.md#этап-8-правовая-и-техническая-валидация-поиска-по-лицу)
- Related ADRs: [0004 — Keep engineering knowledge in the repository](../../adr/0004-repository-engineering-knowledge.md)
- ADR impact: None — reversible implementation detail. The spike evaluates YuNet and SFace but does
  not select a production model, vector store, deployment topology, or biometric-data policy.

## Outcome

A developer can run one local, reproducible experiment over a small labelled folder of cycling-event
photos and inspect whether YuNet detects usable faces and whether SFace retrieves other race photos
from the same manually labelled rider series. The run produces immutable machine-readable metrics
and a local visual report that distinguishes detection failure from embedding/retrieval failure.

The spike answers whether this model pair deserves a larger benchmark. It does not prove production
fitness and does not implement customer selfie search.

## Motivation

Representative event photos contain small faces, downward head pose, helmets, glasses, rain, weak
illumination, and motion blur. A selfie-to-race-photo experiment combines detector failure with a
large capture-domain difference and would make a negative result hard to diagnose. The first
increment therefore compares race photos with other race photos and evaluates the two pipeline
stages separately.

## Experiment dataset

The dataset is local and untracked. The first directional run contains:

- 5–10 manually identified rider series;
- at least 3 photos in every labelled series;
- 30–50 additional photos containing other riders or hard negative examples; and
- approximately 50–100 JPEG images in total.

Labels use a CSV file with this exact header:

```csv
filename,participant_group,face_expected
0M0A0513.JPG,rider-01,yes
0M0A0514.JPG,rider-01,yes
0M0A0515.JPG,rider-01,uncertain
0M0A0600.JPG,,no
```

`participant_group` is an experiment-local pseudonymous series label, never a name or asserted
identity. It is required for positive retrieval examples and blank for ungrouped negatives.
`face_expected` is exactly `yes`, `no`, or `uncertain`. Metrics use `yes` as the detection-recall
denominator, `no` to count false detections, and report `uncertain` separately.

Each image is assumed to have one primary photographed rider. If YuNet returns multiple faces, the
largest valid bounding box is the primary face used for retrieval; all detections remain visible in
diagnostics. Images for which this rule selects the wrong subject must be removed from the first
dataset rather than adding manual bounding-box tooling to this increment.

## Architecture

The spike is an isolated Python package under `experiments/face_recognition_spike/`. It does not
import Django, access PostgreSQL or Object Storage, or modify application dependencies. A dedicated
Docker image pins the experiment runtime. Photos, model files, labels, and generated runs are mounted
at execution time and remain outside the image and Git repository.

```text
local JPEG folder + labels.csv + ONNX model files
                    |
                    v
          deterministic local CLI
                    |
        EXIF orientation normalization
                    |
              YuNet detection
             /               \
   annotated diagnostics    valid primary face
                                  |
                         SFace aligned embedding
                                  |
                    exact cosine search in memory
                                  |
              CSV/JSON metrics + local HTML report
```

No database or approximate vector index is needed for 50–100 images. Exact NumPy cosine comparison
keeps the experiment inspectable and prevents pgvector behavior from influencing model evaluation.

## Command contract

The container exposes one run command equivalent to:

```sh
python -m face_spike run \
  --photos /input/photos \
  --labels /input/labels.csv \
  --yunet-model /models/yunet.onnx \
  --sface-model /models/sface.onnx \
  --output /output/run-001 \
  --detection-threshold 0.75 \
  --min-face-px 32 \
  --top-k 10
```

The output directory must not already exist. Every invocation therefore creates an immutable run;
the tool never overwrites or merges prior evidence. Invalid labels, unreadable images, missing
models, model initialization failure, or an existing output path return a non-zero exit code.
Individual image decode/detection/embedding failures are recorded and processing continues; the run
returns a non-zero exit code after writing its report when any labelled image failed unexpectedly.

## Processing behavior

1. Validate CLI parameters, exact label columns and values, unique filenames, and that every label
   resolves to one regular file below the supplied photo directory.
2. Compute SHA-256 hashes of both ONNX files and record Python, OpenCV, NumPy, Pillow, platform,
   model hashes, CLI parameters, start time, and finish time in `manifest.json`.
3. Load every labelled JPEG, apply EXIF orientation, convert it deterministically to OpenCV BGR, and
   reject images whose decoded dimensions or pixel count exceed configured safety limits.
4. Run YuNet at the decoded image dimensions. Normalize and clip bounding boxes and five landmarks;
   reject non-finite geometry, boxes outside the image after clipping, confidence below the command
   threshold, and boxes whose clipped width or height is smaller than `min-face-px`.
5. Draw every accepted detection on a downscaled diagnostic copy. Choose the largest accepted face
   as the primary face and store its lossless crop. Original photos are never copied to the run.
6. Use OpenCV SFace `alignCrop` and `feature` for the primary detection. Flatten the result to one
   finite `float32` vector and L2-normalize it. A zero or non-finite norm is an embedding failure.
7. Perform leave-one-out exact cosine search. Each successfully embedded labelled image becomes one
   query and all other successful embeddings are candidates. Stable ordering is cosine distance,
   then filename. Retrieval metrics include only queries whose `participant_group` has at least one
   other successfully embedded candidate.
8. Write the complete run artifacts only beneath the newly created output directory.

## Run artifacts

```text
run-001/
├── manifest.json
├── detections.csv
├── retrieval.csv
├── metrics.json
├── annotated/
├── crops/
└── report.html
```

- `manifest.json` makes the run reproducible and records fatal and per-image failure counts.
- `detections.csv` contains one row per input image plus detection count, primary bbox, confidence,
  crop dimensions, status, and stable error code.
- `retrieval.csv` contains query filename, candidate filename, rank, cosine distance, whether their
  nonblank group labels match, and both pseudonymous group labels.
- `metrics.json` contains dataset counts, detection coverage for expected faces, false-detection
  count for `face_expected=no`, embedding success, Recall@1/5/10, evaluable-query count, latency
  summaries, and error counts.
- `annotated/` contains bounded-size JPEG previews with boxes, confidence, and primary-face marker.
- `crops/` contains only detected face crops needed for inspection; it never contains original
  frames.
- `report.html` is self-contained except for relative references to `annotated/` and `crops/`. It
  shows aggregate metrics, failures, face-size distributions as tables, and each query beside its ten
  nearest candidates.

CSV and JSON are authoritative. HTML is a deterministic review surface, not an application UI.

## Metrics and interpretation gates

The small first dataset supplies directional evidence only. Results are interpreted in this order:

1. If expected-face detection coverage is below 50%, do not interpret SFace retrieval metrics;
   evaluate another detector or an explicit tiling/upscaling experiment.
2. If detection coverage is at least 50% but Recall@5 is below 60%, reject the current YuNet/SFace
   configuration as a basis for a larger benchmark.
3. If detection coverage and Recall@5 are both at least 80%, the pair is promising enough for a
   larger, independently specified benchmark that may include selfie queries.
4. Every other result is inconclusive and requires reviewing diagnostic failure categories before
   selecting one narrowly scoped follow-up experiment.

These gates are experiment triage thresholds, not production accuracy requirements and not future
search confidence thresholds.

## Privacy and repository boundaries

- Only photos for which the maintainer is authorized to perform this local evaluation may be used.
- Photos, labels, face crops, embeddings, model weights, and run outputs are ignored locally and must
  not be committed, uploaded, logged by external services, or included in CI artifacts.
- The tool has no network behavior and does not accept selfie input.
- `participant_group` values remain pseudonymous.
- Deleting the local input and run directories removes all experiment image and embedding data.
- This experiment does not satisfy the biometric-governance gate in `docs/architecture.md` and
  cannot be exposed through a product endpoint.

## Testing

Fast tests use generated images and fake detector/recognizer adapters; model weights and event photos
are absent from CI. They verify label validation, EXIF normalization, geometry clipping, primary-face
selection, vector normalization, stable cosine ranking, Recall@K calculations, failure accounting,
path containment, immutable output behavior, and deterministic report construction.

A separately marked local smoke test uses explicitly supplied YuNet and SFace model paths and a
small maintainer-owned fixture folder. It verifies that OpenCV can initialize both models and produce
the complete artifact contract. It is never part of the default CI test suite.

## Acceptance criteria

- One documented Docker command processes the labelled local dataset without Django, a database,
  Object Storage, network access, or GPU infrastructure.
- A successful run creates every specified artifact in a previously absent output directory.
- The manifest records exact dependency versions, model hashes, parameters, and timing.
- Visual diagnostics make missing, invalid, multiple, and primary detections distinguishable.
- Leave-one-out search uses race-photo embeddings only and reports stable top-10 neighbors.
- Metrics distinguish detection coverage, embedding success, retrieval quality, and failures.
- Repeating the command with the same output path fails without changing the existing run.
- Default repository and CI tests use no personal photos, embeddings, or model weights.
- The README explains the four interpretation outcomes without claiming identity or production
  suitability.

## Explicitly out of scope

- Selfie upload or selfie-to-photo comparison.
- Automatic clustering or DBSCAN.
- Django models, management commands, Admin, API, or product UI.
- PostgreSQL, pgvector, vector indexes, task queues, workers, or deployment changes.
- Face naming, identity profiles, cross-event search, or automatic identity confirmation.
- Training, fine-tuning, alternate detectors, tiling, super-resolution, whole-person re-identification,
  bib/OCR, clothing, or bicycle features.
- Production model selection, threshold selection, biometric-policy approval, or customer release.
