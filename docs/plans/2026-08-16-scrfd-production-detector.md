# SCRFD Production Detector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace YuNet with SCRFD-10G_KPS for new preview-backed gallery embeddings and new selfie
queries, preserving SFace, old stored embeddings, and selfie normalization at 1600 px.

**Architecture:** A worker-owned SCRFD module hides ONNX preprocessing, anchor decode, NMS, and
coordinate mapping behind one detector interface. New immutable processor identities route only new
gallery and selfie jobs through that detector; old stored gallery generations remain readable but
are never executed by the new worker. The worker image packages one checksum-pinned SCRFD model and
the unchanged SFace model.

**Tech Stack:** Python 3.12, NumPy 2.2, OpenCV 4.12, Pillow 12, ONNX Runtime 1.23.2, Django 6,
Docker multi-stage build.

## Global Constraints

- SCRFD detector SHA-256:
  `5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91` from official
  `buffalo_l` v0.7 archive SHA-256
  `80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f`.
- Fixed detector behavior: `640×640`, confidence `0.5`, NMS `0.4`, CPU provider, five landmarks.
- SFace model, normalized 128-dimensional vectors, cosine threshold `0.363`, ranking, clustering,
  and result provenance remain unchanged.
- New identities only: contract 2 / `face_embedding` v3 and contract 1 / `selfie_query` v2.
- Selfie normalization: EXIF transpose, ICC to sRGB, long edge 1600, no upscale, no JPEG re-encode.
- No old-photo backfill, quality-v4 change, YuNet veto/fallback, migration, deployment, or external
  inference.
- Preserve stable `decode_failed`, `no_face_detected`, `multiple_faces_detected`,
  `quality_rejected`, and `model_inference_error` outcomes.
- Validate only the changed critical paths; deferred work is already recorded in
  `docs/future-work/2026-08-16-scrfd-production-follow-ups.md`.

---

### Task 1: Shared SCRFD detector and normalized selfie inference

**Files:**

- Create: `src/worker/photo_worker/scrfd.py`
- Create: `src/worker/tests/test_scrfd.py`
- Modify: `src/worker/photo_worker/face_embedding.py`
- Modify: `src/worker/tests/test_face_embedding.py`

**Interfaces:**

- Produces:
  `SCRFDDetector(model_path: Path, *, session: object | None = None)` and
  `SCRFDDetector.detect(image: numpy.ndarray, *, threshold: float) -> tuple[DetectedFace, ...]`.
- `DetectedFace` is frozen and contains `bbox: tuple[float, float, float, float]`,
  `confidence: float`, and
  `landmarks: tuple[tuple[float, float], tuple[float, float], tuple[float, float],
  tuple[float, float], tuple[float, float]]`.
- `face_embedding.py` consumes `SCRFDDetector` and converts each result to its existing internal
  detection mapping before quality and SFace code runs.

- [ ] **Step 1: Add focused failing SCRFD adapter tests**

  Use an injected fake ONNX session with the real nine-output SCRFD shape contract. Test zero
  detections, one decoded face with five landmarks, two overlapping faces reduced by deterministic
  NMS, aspect-ratio padding mapped back to source coordinates, coordinate clipping, and rejection of
  a graph without nine outputs. Assert fixed input shape `1×3×640×640`, mean/scale preprocessing,
  score ordering, confidence `0.5`, and NMS `0.4`.

  Run:

  ```bash
  make test TESTS="src/worker/tests/test_scrfd.py"
  ```

  Expected: collection or import failure because `photo_worker.scrfd` does not exist.

- [ ] **Step 2: Implement the minimal detector module**

  Define the frozen types and constants in `scrfd.py`. Lazily import `onnxruntime` only when no
  session is injected. Validate one input and nine outputs, run only `CPUExecutionProvider`, decode
  strides `(8, 16, 32)` with two anchors, and build the NCHW float32 blob from letterboxed BGR
  pixels with mean `127.5`, scale `1 / 128`, and channel swap to RGB. Apply stable
  descending-score NMS and return clipped source-space detections. Convert unexpected session,
  output-shape, or numeric failures to `SCRFDError` without including the model path.

- [ ] **Step 3: Add failing selfie normalization and shared-consumer tests**

  Extend `test_face_embedding.py` with four critical cases: a 2400×1200 selfie becomes 1600×800;
  an 800×400 selfie is not upscaled; EXIF orientation changes the normalized geometry; and an
  embedded non-sRGB profile is converted to RGB before OpenCV BGR conversion. Keep the existing
  one-face, zero-face, multiple-face, minimum-size, gallery quality, and SFace feature assertions,
  but replace YuNet-specific fakes with one injected SCRFD detector.

  Run:

  ```bash
  make test TESTS="src/worker/tests/test_face_embedding.py -k 'selfie or model or quality'"
  ```

  Expected: failures because the selfie decoder still passes original pixels and the runtime still
  constructs `FaceDetectorYN`.

- [ ] **Step 4: Replace the face_embedding detector seam**

  Rename the model argument and environment lookup from `yunet_model_path` /
  `PHOTO_WORKER_YUNET_MODEL_PATH` to `scrfd_model_path` / `PHOTO_WORKER_SCRFD_MODEL_PATH`. Cache a
  `_ModelRuntime(detector=SCRFDDetector(...), recognizer=FaceRecognizerSF(...))` by resolved SCRFD
  and SFace paths. Replace `_detect_faces` with the adapter call while preserving the current
  internal mapping, confidence ordering, quality gate, SFace alignment, timings, warnings, and
  result payloads.

  Add `_decode_selfie_image` using Pillow `ImageOps.exif_transpose` and `ImageCms.profileToProfile`,
  enforce existing caps before allocating the normalized array, resize only above 1600 with
  Lanczos, and return BGR pixels. Gallery preview decoding remains the existing bounded OpenCV path.

- [ ] **Step 5: Run the Task 1 critical suite**

  ```bash
  make test TESTS="src/worker/tests/test_scrfd.py src/worker/tests/test_face_embedding.py src/worker/tests/test_face_quality.py"
  ```

  Expected: all selected tests pass.

### Task 2: New processor identities and mixed historical/new gallery generations

**Files:**

- Modify: `src/worker/photo_worker/contracts.py`
- Modify: `src/worker/photo_worker/runner.py`
- Modify: `src/worker/tests/test_contracts.py`
- Modify: `src/worker/tests/test_runner.py`
- Modify: `src/backend/processing/contracts.py`
- Modify: `src/backend/processing/services/enrollment.py`
- Modify: `src/backend/processing/services/face_quality.py`
- Modify: `src/backend/processing/tests/test_enrollment.py`
- Modify: `src/backend/processing/tests/test_face_quality_activation.py`
- Modify: `src/backend/selfie_search/services/jobs.py`
- Modify: `src/backend/selfie_search/tests/test_jobs.py`
- Modify: `src/backend/processing/tests/test_views.py`

**Interfaces:**

- Produces preview contract `(2, "face_embedding", 3)` and selfie contract
  `(1, "selfie_query", 2)` on both Django and worker sides.
- Produces `SCRFD_FACE_EMBEDDING_CONFIGURATION`, which is the existing non-quality preview
  configuration plus `"detection_threshold": 0.5`.
- Produces `historical_baseline_face_embedding_generations()` for read-only validation of already
  stored v1/v2 activation rows. `baseline_face_embedding_generations()` returns v1, v2, and v3 for
  event-scoped reads; enqueue selects only v3 for new previews.

- [ ] **Step 1: Add failing version and enqueue tests**

  Assert that an accepted preview completion queues contract 2 / face-embedding v3 with threshold
  `0.5`; a selfie job and claim use contract 1 / selfie-query v2 with threshold `0.5`; worker parsing
  accepts both; and default worker routing no longer claims selfie v1 or gallery v1/v2. Run:

  ```bash
  make test TESTS="src/worker/tests/test_contracts.py src/worker/tests/test_runner.py src/backend/processing/tests/test_enrollment.py src/backend/selfie_search/tests/test_jobs.py src/backend/processing/tests/test_views.py"
  ```

  Expected: focused assertions fail on current versions 2 and 1 and threshold `0.75`.

- [ ] **Step 2: Add failing generation-set tests**

  Assert that the default readable set contains historical v1/v2 plus v3, new enqueue creates no
  v1/v2 job, an existing activation containing exactly the former v1/v2 set still validates, and a
  new activation cannot select the historical-only set. Assert quality-v4 candidate and historical
  projections are unchanged.

- [ ] **Step 3: Implement exact Django and worker version plumbing**

  Update processor constants and strict claim unions together. Create the v3 configuration and use
  it only in preview-backed new enqueue. Update selfie configuration/error wording to v2 and
  threshold `0.5`. Extend generation validation with the historical baseline read path while keeping
  new activation choices limited to the combined baseline, quality-v4 candidate, and the existing
  historical quality set. Do not migrate or enqueue existing photos.

- [ ] **Step 4: Run the Task 2 critical suite**

  ```bash
  make test TESTS="src/worker/tests/test_contracts.py src/worker/tests/test_runner.py src/backend/processing/tests/test_enrollment.py src/backend/processing/tests/test_face_quality_activation.py src/backend/selfie_search/tests/test_jobs.py src/backend/processing/tests/test_views.py"
  ```

  Expected: all selected tests pass.

### Task 3: Immutable worker image, local defaults, and PR readiness

**Files:**

- Modify: `src/worker/requirements.txt`
- Modify: `Dockerfile.worker`
- Modify: `src/worker/photo_worker/model_smoke.py`
- Modify: `tests/processing/test_worker_container_contract.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.prod.yml`
- Modify: `deploy/apply-deployment.sh`
- Modify: `tests/deployment/test_deployment_scripts.py`
- Modify: `docs/local-photo-processing-check.md`

**Interfaces:**

- Produces `PHOTO_WORKER_SCRFD_MODEL_PATH=/worker/models/det_10g.onnx` and retains
  `PHOTO_WORKER_SFACE_MODEL_PATH`.
- Default runnable identities become
  `1/capture_metadata/2,2/generate_preview/1,2/face_embedding/3`; selfie v2 remains selected through
  the configured `selfie_query` processor type.
- Produces a build-time smoke that executes both `extract_face_embeddings` and
  `extract_selfie_embedding` with SCRFD and exercises a 128-dimensional SFace feature.

- [ ] **Step 1: Add failing image and deployment-contract assertions**

  Replace YuNet expectations with the exact SCRFD archive/model checksums, ONNX Runtime pin,
  SCRFD/SFace environment paths, absence of YuNet, new identity defaults, and deploy-script
  acceptance of only the new face/selfie execution identities. Keep the existing minimal-image,
  non-root, resource-bound, and secret-isolation assertions.

  Run:

  ```bash
  make test TESTS="tests/processing/test_worker_container_contract.py tests/deployment/test_deployment_scripts.py"
  ```

  Expected: failures because Docker and deployment defaults still package and allow YuNet-era
  identities.

- [ ] **Step 2: Package the pinned runtime and models**

  Pin `onnxruntime==1.23.2` in worker requirements. Add a model-only Docker stage that downloads the
  official `buffalo_l.zip` with archive checksum, extracts `det_10g.onnx`, verifies its detector
  checksum, and copies only that detector into the final worker image. Remove the YuNet ADD and env;
  keep the unchanged checksum-pinned SFace ADD. Update `model_smoke.py` to load real SCRFD/SFace and
  require the existing gallery no-face result, selfie `no_face_detected`, and SFace dimension.

- [ ] **Step 3: Update local and future deployment defaults**

  Change `.env.example`, both Compose files, and deploy validation/defaults to claim preview v3 and
  selfie v2 while rejecting obsolete execution identities. Update the local processing check with
  the exact v3 identity and a small gallery/selfie acceptance sequence. Do not run a deployment or
  alter GitHub/cloud variables.

- [ ] **Step 4: Run critical repository checks**

  ```bash
  make test TESTS="src/worker/tests/test_scrfd.py src/worker/tests/test_face_embedding.py src/worker/tests/test_contracts.py src/worker/tests/test_runner.py src/backend/processing/tests/test_enrollment.py src/backend/processing/tests/test_face_quality_activation.py src/backend/selfie_search/tests/test_jobs.py src/backend/processing/tests/test_views.py tests/processing/test_worker_container_contract.py tests/deployment/test_deployment_scripts.py"
  git diff --check
  ```

  Expected: all selected tests pass and diff check is clean.

- [ ] **Step 5: Build the exact worker image and run its smoke**

  ```bash
  docker build -f Dockerfile.worker -t findme-worker:scrfd-local .
  docker run --rm --network none --entrypoint python findme-worker:scrfd-local \
    -m photo_worker.model_smoke
  ```

  Expected: Docker build exits zero with `face-model-smoke-ok`; the network-disabled explicit smoke
  also exits zero.

- [ ] **Step 6: Final scope verification and PR**

  Confirm no migration, media, credential, private benchmark artifact, deployment mutation, backfill,
  SFace change, or cosine-threshold change. Run the critical suite once more on the final tree, push
  `codex/scrfd-production-detector`, and open a draft PR against `main` describing the 36-case
  benchmark evidence, known multi-face trade-off, local manual test still required, and deployment
  explicitly deferred.
