# Private selfie detector benchmark

This isolated harness compares the deployed YuNet detector against the exact, locally retained
feedback snapshot. It is not product code, does not contact staging, and must not be used for
training or tuning. Keep source JSON, media, snapshots, runs, crops, overlays, and HTML review
bundles in the ignored private directories or another private local path.

## Snapshot

An operator performs the separately approved read-only export, producing a local JSON array with
the safe `SnapshotRecord` fields plus `content_path`. It must have no contacts, bearer tokens,
Object Storage keys, consent records, public URLs, embeddings, or vectors. The exporter assigns
deterministic `case-NNN` IDs, verifies every source SHA-256 and byte count, and atomically publishes
only the complete 40-object snapshot.

```sh
PYTHONPATH=experiments/selfie_detector_benchmark \
.venv/bin/python -m detector_benchmark snapshot \
  --records-json /private/feedback-records.json \
  --output /private/snapshots/feedback-001

PYTHONPATH=experiments/selfie_detector_benchmark \
.venv/bin/python -m detector_benchmark verify-snapshot \
  --snapshot /private/snapshots/feedback-001
```

For the one approved staging export, copy only this harness into the running **web** container and
run the dedicated read-only exporter. It invokes the existing feedback storage's `inspect` and
`get_object` paths, reads the ORM only, verifies each object against its database size/content type,
and writes no staging state. Copy the completed private directory to the local private volume only
after the command exits successfully; do not transfer the credentials, database dump, source JSON,
or object keys.

```sh
WEB_CONTAINER="$(docker compose -f docker-compose.prod.yml ps -q web)"
docker cp "$PWD/experiments/selfie_detector_benchmark/detector_benchmark" \
  "$WEB_CONTAINER:/tmp/detector_benchmark"
docker compose -f docker-compose.prod.yml exec -T web sh -ec \
  'PYTHONPATH=/tmp:/app/src/backend python -m detector_benchmark.staging_export \
    --output /tmp/feedback-001'
docker cp "$WEB_CONTAINER:/tmp/feedback-001" /private-export/feedback-001
```

## Exact worker-image run

Set `WORKER_IMAGE` to the immutable deployed worker image **digest**, not a mutable tag, and set
`REVISION` to this harness checkout's full Git commit SHA. The
harness is mounted read-only, runs without a network, and receives only the private snapshot,
model path already inside the image, and an output directory. `REVISION` is the checked-out harness
commit recorded in the immutable run manifest.

```sh
WORKER_IMAGE='ghcr.io/peter-nikitin/photo-prjct-worker@sha256:<exact-deployed-digest>'
REVISION="$(git rev-parse HEAD)"
docker run --rm --network none --read-only --tmpfs /tmp:rw,size=256m \
  -e DETECTOR_BENCHMARK_REVISION="$REVISION" \
  -v "$PWD/experiments/selfie_detector_benchmark:/harness:ro" \
  -v /private/snapshots/feedback-001:/snapshot:ro \
  -v /private/runs:/runs:rw \
  --entrypoint sh "$WORKER_IMAGE" -ec '
    PYTHONPATH=/harness python -m detector_benchmark run \
      --snapshot /snapshot \
      --yunet-model "$PHOTO_WORKER_YUNET_MODEL_PATH" \
      --output /runs/detector-001 \
      --experiment-revision "$DETECTOR_BENCHMARK_REVISION"'
```

The command verifies exactly 40 objects then evaluates only the exact 36-case detector cohort in
the frozen order. It writes the three variants (`baseline-original`, `normalized-1600`, and
`normalized-1600-quality`) with threshold 0.75, current `normalized-laplacian-v1` quality
thresholds, JSON evidence, bounded crops/annotated images, and a local report. It creates no
completed output if the snapshot, model, decoder, quality gate, or publication fails.

## Review and finalization

The run report shows the original beside all variants. The reviewer labels each `review.csv` row
exactly once as `correct`, `incorrect`, or `uncertain`; finalization rejects missing, duplicate,
unknown, or invalid labels and atomically writes only aggregate analysis.

```sh
PYTHONPATH=experiments/selfie_detector_benchmark .venv/bin/python -m detector_benchmark build-review \
  --run /private/runs/detector-001 --output /private/review-bundles/detector-001
PYTHONPATH=experiments/selfie_detector_benchmark .venv/bin/python -m detector_benchmark finalize \
  --run /private/runs/detector-001 \
  --labels-csv /private/review-bundles/detector-001/review.csv \
  --output /private/runs/detector-001-analysis
```

## Frozen foreground derivation

This follow-up is a local derivation, not another detector run: it consumes only the verified
`normalized-1600` evidence and bounded review visuals in the approved immutable detector run. Before
starting, set `EXPECTED_REVISION` to the independently reviewed full Task 1 commit. The harness
checkout must be clean at that exact revision, the source must verify to the approved source identity,
its directory must be mounted read-only, and the output root must be private and empty. Do not run any
of these commands with network access.

```sh
EXPECTED_REVISION="${EXPECTED_REVISION:?set the reviewed full Task 1 revision}"
REVISION="$(git rev-parse HEAD)"
test -z "$(git status --porcelain)"
test "${#EXPECTED_REVISION}" -eq 40
test "$REVISION" = "$EXPECTED_REVISION"

PYTHONPATH=experiments/selfie_detector_benchmark \
.venv/bin/python -m detector_benchmark verify-run --run /private/runs/detector-001

docker run --rm --network none --read-only --tmpfs /tmp:rw,size=256m \
  -v "$PWD/experiments/selfie_detector_benchmark:/harness:ro" \
  -v /private/runs/detector-001:/source:ro \
  -v /private/runs:/runs:rw \
  --entrypoint sh "$WORKER_IMAGE" -ec '
    PYTHONPATH=/harness python -m detector_benchmark derive-foreground \
      --source-run /source --output /runs/detector-001-foreground \
      --experiment-revision "'"$EXPECTED_REVISION"'"'
```

The derivation rejects a source whose complete identity is not
`19f58e027c3aca32487d13ef3e420fca9ade15fc189c7bd7d70625b39cc101aa`, an incomplete 36-case
cohort, altered source evidence, or an existing destination. Verify the derived bundle before
building review labels. The finalizer accepts exactly one label per row, all bound to the derived
run identity, and rejects `uncertain`, duplicate, missing, or foreign labels.
It also requires the complete 108-row source detector review CSV bound to the frozen source-run
identity. The immutable foreground analysis embeds changed, helped, harmed, and neutral totals for
both normalized source variants; it never accepts a partial or foreign source label set.

```sh
PYTHONPATH=experiments/selfie_detector_benchmark .venv/bin/python -m detector_benchmark \
  verify-foreground --run /private/runs/detector-001-foreground
PYTHONPATH=experiments/selfie_detector_benchmark .venv/bin/python -m detector_benchmark \
  build-foreground-review --run /private/runs/detector-001-foreground \
  --output /private/review-bundles/detector-001-foreground
PYTHONPATH=experiments/selfie_detector_benchmark .venv/bin/python -m detector_benchmark \
  finalize-foreground --run /private/runs/detector-001-foreground \
  --labels-csv /private/review-bundles/detector-001-foreground/review.csv \
  --source-labels-csv /private/review-bundles/detector-001/review.csv \
  --output /private/runs/detector-001-foreground-analysis
```
