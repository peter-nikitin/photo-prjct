# SCRFD Production Detector Design

- **Status:** Proposed; conversation design approved on 2026-08-16
- **Date:** 2026-08-16
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md)
- **Related ADRs:**
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md),
  [ADR 0023](../../adr/0023-store-consented-selfie-search-feedback.md)
- **Evidence:** local 36-case SCRFD-10G_KPS detector benchmark, adapter revision
  `adfb9428a1bffa46e336eb6720ef8266ebe8f263`
- **ADR impact:** Conforms to existing worker, selfie privacy, and immutable processor-generation
  boundaries. No new durable architecture decision is introduced.

## Outcome

Replace the production YuNet face detector with SCRFD-10G_KPS for two new processing identities:

- preview-backed face embedding for newly uploaded gallery photos; and
- new selfie-query jobs.

Keep SFace recognition, normalized 128-dimensional embeddings, cosine thresholds, ranking,
clustering, gallery results, and existing stored embeddings unchanged. Do not reprocess existing
photos.

The delivery ends at a reviewable pull request. After the PR is ready, the maintainer performs a
small local upload-and-selfie acceptance pass. Deployment requires a later explicit approval.

## Selected production behavior

Use the official InsightFace `det_10g.onnx` detector from the `buffalo_l` v0.7 model pack:

- detector SHA-256:
  `5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91`;
- input size `640×640`;
- detection threshold `0.5`;
- NMS threshold `0.4`;
- CPU ONNX Runtime `1.23.2`; and
- five landmarks in the layout required by OpenCV SFace alignment.

SCRFD is the only production detector after this change. Remove the YuNet asset, model-path
environment variable, loader, and detector-specific code rather than retain a compatibility path
or runtime fallback.

The detector accepts an already decoded BGR image, applies the official SCRFD aspect-preserving
resize and zero padding, decodes three output strides, applies deterministic NMS, maps boxes and
five landmarks back to source coordinates, clips them to the source image, and returns detections
sorted by descending confidence and then coordinates.

## Gallery critical path

New uploads already produce `preview-small-v1`: EXIF-oriented, ICC-normalized sRGB JPEG, no-upscale
1600 px long edge. SCRFD runs on that accepted preview, then the existing SFace alignment and
embedding path runs on the same preview pixels.

Introduce preview-backed `face_embedding` processor version **3** with a new immutable
configuration that identifies detection threshold `0.5`. New preview completions enqueue v3.
Existing preview-backed v2 and original-backed v1 stored results remain readable search
generations, but the worker no longer claims or executes those obsolete identities.

The default generation set used by events without an explicit activation contains the existing
v1/v2 generations plus the new v3 generation. This preserves old gallery membership while allowing
new v3 photos into the same event-scoped search. It does not enqueue v3 work for old photos.

Name the former v1/v2-only default set `historical baseline` and continue accepting it only when
reading an already stored activation. New activation and enqueue paths cannot select it. This
prevents existing activation rows from becoming invalid while avoiding an obsolete execution path.

The separately activated quality-v4 cohort and its thresholds, approval evidence, projections, and
historical event remain unchanged. Moving that cohort to SCRFD is outside this release.

## Selfie critical path

Introduce `selfie_query` processor version **2**. Django creates only v2 jobs and reports detection
threshold `0.5` in the immutable worker configuration.

Before SCRFD and SFace, the worker normalizes every accepted JPEG or PNG selfie as follows:

1. enforce the existing byte and decoded-pixel caps;
2. apply EXIF orientation;
3. convert an embedded ICC profile to sRGB, or convert unprofiled input to RGB;
4. resize with Lanczos to 1600 px long edge only when the source is larger; and
5. convert the normalized pixels to OpenCV BGR without a JPEG re-encode.

SCRFD then classifies raw cardinality. Zero faces still returns `no_face_detected`; more than one
still returns `multiple_faces_detected`; exactly one proceeds through the unchanged 32 px minimum,
SFace alignment, vector normalization, and transient query contract.

The accepted trade-off from the benchmark is explicit: SCRFD may return only the intended
foreground face when YuNet would have counted a usable background face. No YuNet multi-face veto,
fallback, foreground-selection rule, blur gate, threshold sweep, or second detector is included.

## Module and image boundaries

Create one worker-owned SCRFD module responsible only for model loading and face detection. It
exposes a small detector result type containing `bbox`, `confidence`, and five landmarks.
`face_embedding.py` owns image decoding/normalization, quality decisions, SFace alignment, and
embedding output; it does not implement SCRFD anchor decoding.

Cache one ONNX Runtime session per resolved SCRFD model path and one SFace recognizer per resolved
SFace path. Model-load failures, invalid model output shapes, decode failures, and inference errors
map to the existing stable `model_inference_error` or `decode_failed` boundaries without leaking
paths or input data.

Add `onnxruntime==1.23.2` to worker-only dependencies. Build the worker image with a model-only
stage that downloads the checksum-pinned official pack, extracts only `det_10g.onnx`, and copies
only that 16.9 MB detector into the final image. Keep the checksum-pinned SFace asset. The
build-time model smoke must load both models, run SCRFD on a synthetic no-face JPEG through gallery
and selfie paths, and exercise one synthetic SFace feature extraction.

## Version and cutover contract

Update Django and worker constants together:

- preview-backed `face_embedding`: contract 2, processor version 3;
- `selfie_query`: contract 1, processor version 2.

Do not process a v1 selfie job or v1/v2 gallery job with SCRFD under its old identity. Before later
deployment, an operational preflight must confirm there are no leased or pending obsolete jobs that
the replacement worker would need to finish. Existing terminal results and projections remain
readable data and require no worker compatibility implementation.

No database migration is required: processor versions and configurations are stored values inside
the existing generic job/result schema.

## Critical verification only

Implementation tests cover only realistic paths changed by this release:

- SCRFD output decoding for zero, one, and multiple faces, deterministic NMS, coordinate mapping,
  and five-landmark shape using an injected fake ONNX session;
- selfie EXIF orientation, sRGB conversion, 1600 px downscale, and no-upscale behavior;
- one-face selfie continues to SFace, zero/multiple results preserve stable domain errors;
- a preview completion enqueues face-embedding v3 with threshold `0.5`;
- a selfie submission/claim uses processor v2 with threshold `0.5`;
- old stored v1/v2 gallery generations remain selectable without enqueueing backfill;
- an already stored historical-baseline activation remains valid, but cannot be newly selected;
- worker contract parsing accepts the two new identities and rejects the removed claim identities;
- worker image contract contains SCRFD and SFace, excludes YuNet, and the build-time model smoke
  succeeds.

Do not add an exhaustive detector accuracy suite, stress test, all-format image matrix, deployment
automation, or event backfill. Record credible deferred needs in the linked future-work artifact.

## Local maintainer acceptance after PR

After CI is green, build and start the PR's local worker/application stack. The maintainer uploads a
small set of event photos and submits several selfies, including at least one source over 1600 px.
Acceptance requires:

- new photo attempts identify contract 2 / `face_embedding` v3 and produce searchable SFace
  embeddings;
- the selfie attempt identifies contract 1 / `selfie_query` v2;
- the selfie detector input is bounded to 1600 px without upscaling a smaller source;
- one intended foreground face completes search; and
- no model-load, result-contract, or worker-claim error appears.

This manual pass is evidence for a later deployment decision, not part of automated CI and not
authorization to deploy.

## Licensing and privacy

InsightFace code is MIT and its official pretrained weights are restricted to non-commercial
research. The maintainer has confirmed that the current project is non-commercial and accepts that
restriction for this release. Commercial operation is a concrete trigger to obtain a commercial
licence or replace the weights before continued use.

Selfies remain temporary private inputs. Do not persist query images, SCRFD detections, landmarks,
or embeddings beyond the existing transient job and immutable result contracts. Do not add
external inference, telemetry containing biometric data, or customer-media fixtures to Git.

## Excluded

- Reprocessing or backfilling existing photos.
- Changing SFace, vector dimensions, cosine thresholds, ranking, clustering, or result provenance.
- Changing the quality-v4 cohort, its activation, or its YuNet-calibrated quality thresholds.
- A YuNet veto, fallback detector, model/threshold sweep, GPU provider, or multi-worker tuning.
- Database migrations, deployment, staging/production mutations, or commercial licence purchase.
- General accuracy claims beyond the completed 36-case feedback benchmark.
