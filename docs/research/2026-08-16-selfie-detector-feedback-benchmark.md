# Selfie detector feedback benchmark

- **Date:** 2026-08-16
- **Status:** Complete — neither candidate is recommended for production.
- **Scope:** Approved private, offline model-quality evaluation under ADR 0023. This did not train
  or tune a model, change product behavior, or make a production-readiness claim.

## What ran

One read-only export produced a frozen 40-record feedback snapshot. It contained 17 historic
`no_face` outcomes, 16 successful-result controls, three true multiple-face controls, and four
ready searches with zero visible results. The latter four were retained in the snapshot but excluded
from detector metrics.

The exact 36-case detector cohort ran locally in one immutable worker image with networking disabled.
The root filesystem and input mounts were read-only; a bounded writable `/tmp` tmpfs and the private
output directory were writable. Each input ran, in order, with YuNet threshold `0.75` through:

1. `baseline-original` — raw original bytes and raw cardinality.
2. `normalized-1600` — EXIF orientation, ICC-to-sRGB normalization, no-upscale 1600-pixel long
   edge, and raw cardinality.
3. `normalized-1600-quality` — the same normalized input followed by the frozen
   `normalized-laplacian-v1` quality gate.

All 108 case/variant rows received one manual label. The finalized review contains 70 `correct`, 38
`incorrect`, and zero `uncertain` labels.

## Immutable identities

| Item | SHA-256 / immutable identity |
| --- | --- |
| Snapshot manifest | `cb0cfef85e411b2cd4dc4128d9015c490214669b746f6e5fd10bd3371c31d373` |
| YuNet model | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` |
| Worker image | `sha256:8d27126501a6a5bd9ee6882756b7093ed906d521fcf363615dabd05c1c465797` |
| Experiment revision | `1cc32ad28392c61550c182359d6b3659dcd5f2b3` |
| Verified run identity | `19f58e027c3aca32487d13ef3e420fca9ade15fc189c7bd7d70625b39cc101aa` |
| Final analysis | `d348d5e109b11fc8aaf81882f82e9a69dd5b5635f7300fb0a8c9342a7336a404` |

## Finalized results

| Variant | Correct no-face recoveries | Successful controls preserved | Multiple-face accepted-single violations | Promising |
| --- | ---: | ---: | ---: | --- |
| `baseline-original` | 0/17 | 16/16 | 0/3 | No |
| `normalized-1600` | 8/17 | 16/16 | 1/3 | No |
| `normalized-1600-quality` | 9/17 | 15/16 | 2/3 | No |

The frozen rule requires at least 5/17 correct recoveries, all 16 successful controls preserved,
and zero multiple-face accepted-single violations. Although normalization recovered prior failures,
it violated the multiple-face guardrail. The quality-gated variant also lost one successful control
and had two guardrail violations. Neither candidate is promising.

## Reproducibility and privacy

The private snapshot verifier confirmed all 40 objects before the run. The run identity binds the
snapshot manifest, model identity, experiment revision, evidence, and displayed review visuals.
Finalization accepted exactly one complete review for that identity.

The following command trail is deliberately parameterized: set the private local directories and
review labels outside this report. It records the frozen run's image and experiment revision, while
keeping all customer data and private locations out of version control.

```sh
export SNAPSHOT_DIR='<private snapshot directory>'
export RUNS_DIR='<private runs directory>'
export RUN_NAME='run-name'
export REVIEW_DIR='<private review directory>'
export LABELS_CSV='<private completed labels CSV>'
export WORKER_IMAGE='<immutable worker image with the SHA-256 listed above>'
export EXPERIMENT_REVISION='1cc32ad28392c61550c182359d6b3659dcd5f2b3'
export HARNESS_ROOT='<clean checkout at the exact experiment revision>'
export HARNESS_DIR="$HARNESS_ROOT/experiments/selfie_detector_benchmark"

test "$(git -C "$HARNESS_ROOT" rev-parse HEAD)" = "$EXPERIMENT_REVISION"
test -z "$(git -C "$HARNESS_ROOT" status --porcelain)"

PYTHONPATH="$HARNESS_DIR" "$HARNESS_ROOT/.venv/bin/python" \
  -m detector_benchmark verify-snapshot --snapshot "$SNAPSHOT_DIR"

docker run --rm --network none --read-only --tmpfs /tmp:rw,size=256m \
  -e DETECTOR_BENCHMARK_REVISION="$EXPERIMENT_REVISION" \
  -e DETECTOR_BENCHMARK_RUN_NAME="$RUN_NAME" \
  -v "$HARNESS_DIR:/harness:ro" \
  -v "$SNAPSHOT_DIR:/snapshot:ro" -v "$RUNS_DIR:/runs:rw" \
  --entrypoint sh "$WORKER_IMAGE" -ec '
    PYTHONPATH=/harness python -m detector_benchmark run \
      --snapshot /snapshot --yunet-model "$PHOTO_WORKER_YUNET_MODEL_PATH" \
      --output "/runs/$DETECTOR_BENCHMARK_RUN_NAME" \
      --experiment-revision "$DETECTOR_BENCHMARK_REVISION"'

PYTHONPATH="$HARNESS_DIR" "$HARNESS_ROOT/.venv/bin/python" \
  -m detector_benchmark build-review \
  --run "$RUNS_DIR/$RUN_NAME" --output "$REVIEW_DIR"
PYTHONPATH="$HARNESS_DIR" "$HARNESS_ROOT/.venv/bin/python" \
  -m detector_benchmark finalize \
  --run "$RUNS_DIR/$RUN_NAME" --labels-csv "$LABELS_CSV" \
  --output "$RUNS_DIR/$RUN_NAME-analysis"
PYTHONPATH="$HARNESS_DIR" "$HARNESS_ROOT/.venv/bin/python" \
  -c 'import sys; from pathlib import Path; from detector_benchmark.offline import verify_run; print(verify_run(Path(sys.argv[1])))' \
  "$RUNS_DIR/$RUN_NAME"
```

During the frozen execution, the exporter needed an explicit canonical Django bootstrap before ORM
access. The reviewed entry point now supplies that bootstrap in post-run commit `a25f79f`; it was
not part of, and does not alter, the frozen experiment revision above.

The tracked report deliberately excludes media, customer or contact data, record mappings, object
keys, public URLs, embeddings, vectors, credentials, and private filesystem locations. Private
snapshot and review artifacts remain outside Git and should be deleted after the benchmark decision
and any explicitly approved follow-up are complete.

## Limitations and next decision

This is a small, feedback-selected cohort from one event. It is not a prevalence estimate and does
not establish general performance. Runtime measurements are additionally limited because the
immutable AMD64 worker image ran under host emulation.

Do not adopt either candidate as specified. Any further work needs a separate approval for an
alternative-detector comparison or a new study design; this evidence does not authorize tuning,
activation, deployment, or threshold changes.

## Architecture reconciliation

No architecture update required. The benchmark changed no production code, dependency, schema,
configuration, deployment, or deployed state, and remained within ADR 0023 and the approved scope.
