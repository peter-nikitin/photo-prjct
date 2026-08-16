# Selfie foreground selection benchmark

- **Date:** 2026-08-16
- **Status:** Complete — not promising; not recommended for production.
- **Scope:** Approved private, offline derivation from the immutable detector evidence. This did
  not rerun face detection, train or tune a model, change product behavior, or authorize activation.

## Decision

The `normalized-1600-foreground` variant is **not promising**. It passed the historical recovery
and successful-control gates, but failed the genuine multiple-face guardrail: 2 of 3 controls were
accepted as single-face inputs even though the frozen rule requires zero such violations. The
failed guardrail ends this experiment. No ratio, quality constant, manual judgment, or replacement
run was changed or attempted.

## Verified evidence

The derivation reused the exact normalized 1600 px evidence from the source run; the detector was
not invoked again. The reviewed identities are:

| Item | SHA-256 / immutable identity |
| --- | --- |
| Reviewed harness revision | `bec612a7f2b79cb368a0cc7f1ff7a81db430556e` |
| Verified source evidence identity | `19f58e027c3aca32487d13ef3e420fca9ade15fc189c7bd7d70625b39cc101aa` |
| Verified derived foreground identity | `aa6a3af2849bd37e2181fa6c862294fcf4b3c82239f2330a2f9e81f28c0d196e` |
| Final analysis SHA-256 | `b59caeb5697dd4bd9932c57c0324e6de30ac8c424d4617c0baa80333dbb730c4` |

The source cohort contains exactly 36 cases: 17 historical `no_face` cases, 16 successful-result
controls, and 3 genuine multiple-face controls. All 36 derived outcomes received a complete
identity-bound review: 26 `correct`, 10 `incorrect`, and 0 `uncertain`. The derived evidence
contains exactly 36 rows and 36 bounded review visuals.

## Frozen foreground rule

The variant consumes the source run's raw detections and quality measurements for the normalized
1600 px input. It applies this rule without a new detector pass:

1. Zero raw detections remains `no_face`.
2. One raw detection becomes `single_face`; an otherwise unambiguous single face receives no
   quality rejection from this rule.
3. For two or more detections, rank by bounding-box area and consider the largest detection as the
   foreground candidate. Convert the case to `single_face` only when all of these conditions hold:
   - the largest area is strictly greater than the next-largest area;
   - the foreground candidate passes the frozen quality gate;
   - every secondary area is no greater than 25% of the foreground area; and
   - every secondary is rejected by the frozen quality gate with `severe_blur` or `too_small`
     among its reasons.
4. Every other multi-detection case remains `multiple_faces`.

The 25% secondary-area limit is the frozen 4:1 rule. The unchanged quality configuration is
`normalized-laplacian-v1`: crop size 112, minimum face side 32 px, severe-blur threshold 25,
borderline-blur threshold 50, minimum relative area `0.0009`, and minimum confidence `0.82`.
There is no centrality score, weighted ranking, new confidence threshold, or adjustable
coefficient.

## Frozen acceptance gates

| Gate | Result | Decision |
| --- | ---: | --- |
| Historical `no_face` recovery | 9/17 | Pass; minimum 5 |
| Successful controls preserved | 16/16 | Pass; all required |
| Genuine multiple-face accepted-single violations | 2/3 | **Fail; zero required** |
| Uncertain review rows | 0/36 | Pass; zero required |
| Overall | — | **Not promising** |

The independent recomputation from the immutable derived rows and complete review exactly matches
the final analysis. Manual review is authoritative; calculated outcomes alone do not establish
correctness.

## Changes versus the normalized source variants

| Compared with | Changed | Helped | Harmed | Neutral changed |
| --- | ---: | ---: | ---: | ---: |
| `normalized-1600` | 2 | 1 | 1 | 0 |
| `normalized-1600-quality` | 1 | 1 | 0 | 0 |

Here, `changed` means that the derived outcome differs from the named frozen source outcome;
`helped` means a changed case moved from incorrect to correct review judgment; and `harmed` means
a changed case moved from correct to incorrect review judgment. The two comparisons were
independently recomputed from the exact source and derived evidence.

## Limitations and boundary

This is a small, feedback-selected cohort from one event. It is not a prevalence estimate and does
not establish general performance. The run was a deterministic offline derivation from already
frozen evidence, so it does not test a new detector, recognition model, embedding, ranking
threshold, or production runtime. Runtime observations are additionally limited because the
immutable AMD64 worker image ran under host emulation.

The result does not authorize tuning, product integration, deployment, activation, or
generalization beyond this cohort. A failed multi-face guardrail is not converted into an overall
positive result by the two passed gates.

## Privacy and architecture reconciliation

The private source evidence, derived media, inspection visuals, completed case-level review, and
analysis artifacts remain outside Git. This report publishes only bounded aggregate counts and
immutable verification hashes. It contains no media, customer or contact details, case mappings,
record identifiers, object keys, bearer tokens, URLs, embeddings, vectors, credentials, or private
filesystem paths.

The derivation used no staging host, database, bucket, external service, or network endpoint. No
production component, dependency, schema, configuration, deployment, or runtime path changed.

**Conforms to ADR 0023; no architecture update required.** Feedback remains labelled evaluation
evidence in a separately approved private workflow; it does not automatically change ranking,
thresholds, embeddings, model weights, or product behavior.

## Separately scoped next hypothesis

If the foreground rule fails a frozen gate, test whether the remaining recall and background
false-detection errors are YuNet-specific: one separately selected and license-reviewed alternative
detector, run on the exact normalized 1600 px inputs, can recover at least 5 of 17 historical
`no_face` cases while preserving 16 of 16 successful controls and causing zero violations in the 3
genuine multiple-face controls, without foreground post-processing.

That alternative-detector comparison is excluded from this report and requires its own frozen model
identity, threshold, execution design, and approval. This report selects no model, dependency,
license, or threshold and does not authorize that execution.

## Verification record

- The source identity, derived identity, exact 36-case cardinality, complete review, gate totals,
  comparison totals, and final-analysis digest were independently reconciled.
- The focused experiment harness suite passed after this documentation change.
- `git diff --check` passed, and the changed-file scan found no tracked media, generated review
  artifact, customer identifier, secret, or absolute private path.
