# Local face-recognition spike

This isolated experiment evaluates whether OpenCV YuNet can detect usable faces in event photos and
whether OpenCV SFace can retrieve other event photos from the same pseudonymous participant series.
It is directional technical evidence, not a production model selection, identity claim, biometric
governance approval, or customer-facing search implementation.

## Privacy and authorization

Run the experiment only on photos the maintainer is authorized to process. Source photos, labels,
model files, crops, embeddings, and generated runs stay outside Git and must not be uploaded as CI
artifacts. Confirm the licenses of separately obtained model files before use.

## Local setup

The repository targets Python 3.12. Create an ignored virtual environment and install the isolated
runtime:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt \
  -r experiments/face_recognition_spike/requirements.txt
```

Fast tests use generated images and fake adapters; they read no model or event-photo file:

```sh
PYTHONPATH=experiments/face_recognition_spike \
python -m pytest -q experiments/face_recognition_spike/tests -m "not face_models"
```

The opt-in compatibility smoke test requires maintainer-owned model and photo paths:

```sh
FACE_SPIKE_YUNET_MODEL=/absolute/models/yunet.onnx \
FACE_SPIKE_SFACE_MODEL=/absolute/models/sface.onnx \
FACE_SPIKE_SMOKE_PHOTOS=/absolute/smoke-photos \
PYTHONPATH=experiments/face_recognition_spike \
python -m pytest -q \
  experiments/face_recognition_spike/tests/test_model_smoke.py \
  -m face_models
```

## Labels

The CSV schema is exact:

```csv
filename,participant_group,face_expected
event-0001.jpg,participant-001,yes
event-0002.jpg,participant-001,yes
event-0003.jpg,,uncertain
event-0004.jpg,,no
```

`participant_group` is an experiment-local pseudonym, not a name or confirmed identity.
`face_expected` is one of `yes`, `no`, or `uncertain`. Every filename is relative to the supplied
photo root. A `yes` row requires a nonblank group.

For the current folder convention, the one-off label builder maps sorted `Person ...` directories
to `person-001`, `person-002`, and so on, marks unmatched event photos `uncertain`, and omits files
that occur in more than one person directory:

```sh
python experiments/face_recognition_spike/scripts/build_labels.py \
  /absolute/dataset-root \
  --output /absolute/labels.csv
```

For a broader directional benchmark, prepare 5–10 independently reviewed participant groups with
at least three unambiguous photos per group and 30–50 explicit negative examples. Group photos whose
label cannot be bound to the selected primary face remain `uncertain`.

## Run

The output directory must not exist. Every invocation creates one immutable evidence directory:

```sh
shasum -a 256 /absolute/models/yunet.onnx /absolute/models/sface.onnx

PYTHONPATH=experiments/face_recognition_spike \
python -m face_spike run \
  --photos /absolute/photos \
  --labels /absolute/labels.csv \
  --yunet-model /absolute/models/yunet.onnx \
  --sface-model /absolute/models/sface.onnx \
  --output /absolute/runs/run-001 \
  --detection-threshold 0.75 \
  --min-face-px 32 \
  --top-k 10
```

The equivalent network-disabled container run is:

```sh
docker build -t findme-photo-face-spike:local experiments/face_recognition_spike
docker run --rm --network none \
  --mount type=bind,src=/absolute/photos,dst=/input/photos,readonly \
  --mount type=bind,src=/absolute/labels.csv,dst=/input/labels.csv,readonly \
  --mount type=bind,src=/absolute/models,dst=/models,readonly \
  --mount type=bind,src=/absolute/runs,dst=/output \
  findme-photo-face-spike:local run \
  --photos /input/photos \
  --labels /input/labels.csv \
  --yunet-model /models/yunet.onnx \
  --sface-model /models/sface.onnx \
  --output /output/run-001 \
  --detection-threshold 0.75 \
  --min-face-px 32 \
  --top-k 10
```

Exit codes are `0` for a completed run without unexpected labelled-image failures, `2` for fatal
input/configuration/model setup failure, and `3` for a completed evidence run in which at least one
`face_expected=yes` image failed unexpectedly. An exit code of `3` still produces review artifacts.

Open `report.html` from the new run directory. CSV and JSON files are authoritative; the report is
the visual review surface:

```sh
open /absolute/runs/run-001/report.html
```

Delete the external input, model, and run directories when the local evaluation no longer needs
them. Never modify an existing run; correct labels and create a new run.

## Directional evidence — 2026-07-24

The first host-native full-event experiment used separately supplied YuNet and SFace ONNX files,
Python 3.12.13, OpenCV 4.12.0, NumPy 2.2.6, and Pillow 12.0.0.

Model SHA-256:

- YuNet: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
- SFace: `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`

Dataset and processing aggregates:

| Metric | Result |
| --- | ---: |
| Event photos | 1,411 |
| Accepted detections | 3,231 |
| Photos with a primary detection and embedding | 1,370 |
| Photos without a detected face | 41 |
| Unexpected processing failures | 0 |
| Decode time | 66.20 s |
| Detection time | 79.70 s |
| Embedding time | 10.08 s |
| Total run duration | 242.29 s |

The initial image-level evaluation contained 20 labelled queries. It reported 100% expected-face
detection coverage, Recall@1 of 80%, and Recall@5/10 of 85%. Manual review found that the four
queries responsible for the retrieval misses were group photos with multiple clearly visible
faces. The current single-primary-face rule could not establish that the embedded primary face was
the face associated with the image-level label.

Those four ambiguous images were reclassified as `uncertain`, retained in the candidate gallery,
and evaluated in a new immutable run:

| Metric | Reviewed result |
| --- | ---: |
| Unambiguous evaluable queries | 16 |
| Expected-face detection coverage | 100% (16/16) |
| Recall@1 | 100% |
| Recall@5 | 100% |
| Recall@10 | 100% |

This passes the spike's directional continuation gate, but the reviewed result is post-review,
contains only one known participant series, and is selected from photos already recognized by
another service. It does not estimate production recall, precision, an open-set verification
threshold, or performance on every photo of that participant. No `face_expected=no` rows were
included, so this run provides no false-detection estimate.

The opt-in real-model smoke test passed locally (`1 passed`). The fast model-independent suite
passed with `89 passed, 1 deselected`. The isolated Docker image built successfully on Apple
silicon from its Python 3.12 base and pinned dependencies.

## Required change for group photos

The current spike retains all YuNet detections for diagnostics but embeds only the largest accepted
face (`PRIMARY`). Proper group-photo support requires changing the evaluation and retrieval unit
from an image to a face instance:

1. Assign every accepted detection a stable per-image face-instance identifier and persist its
   bounding box, landmarks, crop, and SFace embedding.
2. Allow zero or more labelled face instances per image. Ground truth must bind a pseudonymous
   participant group to a specific bounding box or reviewed face-instance identifier rather than
   to the whole image.
3. Rank face instances by cosine distance, then aggregate results back to unique photos using the
   best matching face so one photo is returned once.
4. Report instance-level detection/retrieval metrics and photo-level retrieval metrics separately.
   A known face found anywhere in a group photo counts as a hit; another face selected as primary
   is no longer treated as a recognition error.
5. Show every face crop and its matches in the local report, preserving the immutable artifact,
   bounded-memory streaming, and no-raw-embedding-export boundaries.

The next experiment should implement this offline multi-face representation and repeat the
benchmark with several independently reviewed participant series and explicit negative examples.
It must remain isolated from Django and production infrastructure until biometric-governance and
production-model decisions are made separately.

## Interpretation

- Detection coverage below 50%: evaluate a different detector before interpreting retrieval.
- Detection coverage at least 50% with Recall@5 below 60%: reject this model configuration.
- Detection coverage and Recall@5 both at least 80%: proceed only to a larger offline benchmark.
- Other outcomes: inspect diagnostic categories before choosing one narrow follow-up experiment.

No result from this spike authorizes production use.
