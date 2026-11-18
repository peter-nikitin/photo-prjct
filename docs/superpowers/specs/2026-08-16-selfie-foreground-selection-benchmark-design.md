# Selfie Foreground Selection Benchmark Design

- **Status:** Proposed; conversation design approved on 2026-08-16
- **Date:** 2026-08-16
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md)
- **Related ADRs:** [ADR 0023](../../adr/0023-store-consented-selfie-search-feedback.md)
- **ADR impact:** **Conforms to ADR 0023.** This is a reversible, private model-quality evaluation
  over already exported consented feedback. It does not train a model, change product behavior, or
  authorize activation.

## Outcome

Determine whether a conservative foreground-selection rule can retain one intended selfie face
when YuNet also detects weak blurred or very small background faces, without accepting genuine
multiple-person selfies as single-face inputs.

The evaluation reuses the exact immutable 36-case detector cohort and normalized YuNet evidence
from the completed selfie detector feedback benchmark. It does not download customer data again or
rerun face detection.

## Selected design

Add one derived experimental variant, `normalized-1600-foreground`. It consumes the raw detections
and their quality measurements from the normalized 1600 px input in the immutable source run.

For each case:

1. Zero raw detections produce `no_face`.
2. One raw detection produces `single_face`; the rule does not apply a quality rejection to an
   otherwise unambiguous single face.
3. For two or more raw detections, rank detections by bounding-box area and treat the largest as the
   foreground candidate. Convert the case to `single_face` selecting that candidate only when all
   of these conditions hold:
   - the largest area is strictly greater than the next-largest area;
   - the foreground candidate is accepted by the frozen quality gate;
   - every secondary detection has area no greater than 25% of the foreground area; and
   - every secondary detection is rejected by the frozen quality gate with `severe_blur` or
     `too_small` among its reasons.
4. Every other multi-detection case remains `multiple_faces`.

The area ratio is frozen at 4:1 before examining the derived outcomes. The rule adds no centrality
score, weighted ranking, new confidence threshold, or adjustable coefficient. Thresholds must not
be changed after manual review of this cohort.

The frozen quality configuration remains `normalized-laplacian-v1`: crop size 112, minimum face
side 32 px, severe blur threshold 25, borderline blur threshold 50, minimum relative area `0.0009`,
and minimum confidence `0.82`.

## Alternatives rejected

- **Largest-area face alone:** cheaper, but it can silently discard a real second person and does
  not distinguish a weak banner detection from a valid smaller face.
- **Weighted size, centrality, confidence, and sharpness score:** potentially more flexible, but it
  introduces multiple coefficients that this small feedback-selected cohort cannot calibrate
  without substantial overfitting risk.
- **New detector in the same run:** answers a different hypothesis and adds model, dependency,
  licensing, and runtime variables. It remains the next separately approved experiment.

## Evidence and review

The derived run must bind to the verified source-run identity
`19f58e027c3aca32487d13ef3e420fca9ade15fc189c7bd7d70625b39cc101aa`
and fail if the source evidence no longer verifies. It records the frozen rule, source detection
index, area ratios, quality decisions and reasons, derived outcome, and an immutable identity.

Private review visuals show the normalized image, all raw detections, the selected foreground face
when present, and every retained or ignored secondary detection. Images, crops, labels, mappings,
and generated HTML remain outside Git and external services.

All 36 derived outcomes receive one manual label: `correct`, `incorrect`, or `uncertain`. A correct
`single_face` must select the intended foreground person. A genuine second usable person may not be
ignored. Manual review is authoritative; calculated outcomes alone do not establish correctness.

## Acceptance criteria

The foreground variant is promising only if it satisfies all frozen gates:

- at least 5 of the 17 historical `no_face` cases are correctly recovered;
- the intended single face is preserved in all 16 successful controls;
- none of the 3 genuine multiple-face controls is converted to an accepted single face; and
- all 36 rows are reviewed with zero `uncertain` labels before a decision.

The report must also state how many cases changed relative to `normalized-1600` and
`normalized-1600-quality`, separate beneficial and harmful changes, and avoid generalizing beyond
this one feedback-selected event cohort.

Passing these gates marks the rule only as promising for a later production design. It does not
authorize product integration, threshold tuning, deployment, or activation.

## Privacy, isolation, and failure semantics

- Reuse only the existing approved private local snapshot and immutable run. Do not contact
  staging, Object Storage, or external services.
- Do not export or track media, contacts, bearer tokens, record mappings, object keys, URLs,
  embeddings, vectors, credentials, or private filesystem paths.
- Publish derived artifacts atomically to the private benchmark root and never overwrite the source
  run or a prior derived run.
- Reject missing, altered, incomplete, mismatched, or ambiguous source evidence. Reject incomplete
  or identity-mismatched review labels.
- Keep the local customer-data copy only through this evaluation and any explicitly approved
  immediate follow-up, then delete it explicitly; do not silently extend retention.

## Architecture reconciliation

No production component, dependency, schema, configuration, deployment, or runtime path changes.
The experiment remains under `experiments/selfie_detector_benchmark/` and the sanitized decision
report under `docs/research/`.

This conforms to ADR 0023 because feedback remains labelled evaluation evidence used only through a
separately approved private workflow. It does not automatically change ranking, thresholds,
embeddings, model weights, or product behavior. No new or superseding ADR is required.

## Next hypothesis

If the foreground rule fails a frozen gate, test whether the remaining recall and background
false-detection errors are YuNet-specific: one separately selected and license-reviewed alternative
detector, run on the exact normalized 1600 px inputs, can recover at least 5 of 17 historical
`no_face` cases while preserving 16 of 16 successful controls and causing zero violations in the 3
genuine multiple-face controls, without foreground post-processing.

That alternative-detector comparison is excluded from this specification and requires its own
frozen model identity, threshold, execution design, and approval.

## Excluded

- Production selfie-search code, migrations, dependencies, flags, deployment, or activation.
- Re-running YuNet, downloading staging data again, changing the source snapshot, or editing prior
  review labels.
- Tuning the 4:1 rule or quality thresholds from observed outcomes.
- SFace embeddings, similarity thresholds, result ranking, face clusters, training, or fine-tuning.
- Comparing alternative detectors in this increment.
