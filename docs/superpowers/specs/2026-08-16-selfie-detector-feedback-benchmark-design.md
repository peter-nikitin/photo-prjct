# Selfie Detector Feedback Benchmark Design

- **Status:** Approved in conversation on 2026-08-16
- **Date:** 2026-08-16
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md)
- **Related ADR:** [ADR 0023](../../adr/0023-store-consented-selfie-search-feedback.md)
- **ADR impact:** **Conforms to ADR 0023.** This is a separately approved, private model-quality
  evaluation. It neither trains a model nor changes ranking, production code, staging data, or
  deployment configuration.

## Outcome

Determine whether current selfie detection failures are materially reduced by applying the
gallery input normalization and quality gate before considering a different detector.

The benchmark uses all 40 currently retained staging feedback records in one private local
snapshot. The detector cohort contains 17 `no_face` cases, 16 result-labelled successful controls,
and 3 `multiple_faces` controls. Four `ready` searches with zero visible results remain in the
snapshot for a later recognition experiment but are excluded from detector success metrics.

## Privacy and isolation

- Download the snapshot once from the existing private staging feedback store to
  `photo-prjct-private`; subsequent runs are local and offline.
- Export selfie bytes, pseudonymous case IDs, source outcome, safe search diagnostics, customer
  result labels, and immutable checksums only.
- Do not export contacts, bearer tokens, Object Storage keys, consent records, or public URLs.
- Keep images, crops, overlays, manifests containing record mappings, and HTML review artifacts
  outside Git, CI, and external services.
- Use the feedback only for this approved evaluation. Do not train or fine-tune a model.
- Preserve the bucket's existing 30-day lifecycle; the local snapshot does not authorize extending
  customer-data retention indefinitely. Delete it after the benchmark decision and any explicitly
  approved follow-up are complete.

## Frozen inputs

Each feedback record receives a deterministic local ID `case-NNN`. The private manifest records the
case cohort, source status, source counts, safe configuration fields, result labels and direct
cosine distances, media type, byte size, and SHA-256. Snapshot publication is atomic and fails if
any of the 40 objects is missing or differs from database metadata.

The benchmark also records SHA-256 for the deployed YuNet model and the exact experiment revision.
No gallery originals or embeddings are required.

## Detector variants

Every detector-cohort image is evaluated in the same order with YuNet threshold `0.75`:

1. `baseline-original`: OpenCV decode of the original bytes and the current raw-detection
   cardinality rule.
2. `normalized-1600`: EXIF transpose, sRGB conversion, and downscale of the long edge to 1600 px
   without upscaling, followed by the raw-detection cardinality rule.
3. `normalized-1600-quality`: the same normalized input followed by the current gallery quality
   gate before cardinality is decided.

The frozen quality configuration is `normalized-laplacian-v1`, crop size 112, minimum face side
32 px, severe blur threshold 25, borderline blur threshold 50, minimum relative area `0.0009`, and
minimum confidence `0.82`.

For the quality variant, only accepted detections count. Zero accepted detections reject the
selfie, one is a usable single face, and two or more remain `multiple_faces`. One sharp foreground
face plus a weak blurred face on a banner or in the background is therefore accepted; two accepted
real faces are not.

## Artifacts and review

Each immutable run contains a manifest, per-case JSON/CSV evidence, aggregate metrics, bounded
annotated images, face crops, and a private local HTML report. Evidence includes original and input
geometry, resize scale, raw and accepted detection counts, bounding boxes, landmarks, confidence,
quality values and rejection reasons, and per-variant runtime. It contains no embeddings.

The report displays the original beside all three annotated variants. The reviewer assigns exactly
one label to every case/variant: `correct`, `incorrect`, or `uncertain`. A correct single-face result
must select the intended foreground person. Background print, another person, or an arbitrary crop
is incorrect. Two real usable faces must not be reported as a usable single face.

Finalization requires complete labels and creates a separate immutable analysis. Automated metrics
are diagnostic; the manual review is authoritative for correctness.

## Acceptance criteria

The experiment is complete when:

- the local snapshot contains and verifies all 40 retained feedback objects;
- all three variants finish for the exact 36-case detector cohort;
- every case/variant has a manual outcome or is explicitly `uncertain`;
- the report separates recovery, successful-control regressions, and multiple-face guardrails;
- a candidate variant is promising only if it correctly recovers at least 5 of 17 `no_face`
  cases, preserves the intended single face in all 16 successful controls, and does not convert any
  of the 3 true multiple-face controls into an accepted single face; and
- the final Markdown report states what was run, immutable input/model/run identities, observed
  results, limitations, privacy handling, and the next decision without claiming production
  readiness.

## Failure handling

Snapshot and run outputs are published only after complete validation. Partial staging directories
are removed on failure. A missing/expired source object, checksum mismatch, unsupported image, model
failure, incomplete review, or changed manifest prevents final analysis. No failure mutates staging
or production state.

## Excluded

- Alternative face detectors or recognition models in this first experiment.
- SFace embedding, gallery search, threshold calibration, cluster expansion, training, or tuning.
- Product code, migrations, dependencies, deployment, activation, or automatic threshold changes.
- Generalization claims beyond the retained feedback from this one event.
