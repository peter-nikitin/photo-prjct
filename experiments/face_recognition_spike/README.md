# Local all-people face-clustering spike

This is a macOS-only, offline experiment. It uses OpenCV YuNet to find every
accepted face in each event photo and OpenCV SFace to group successful face
embeddings into anonymous `person-NNNN` clusters. It is directional evidence,
not a production feature, an identity claim, a biometric-governance approval,
or customer-facing search.

Peakshot data is used only after clustering as an algorithmic silver-label
reference. It is never an input to detection, embedding, or clustering.

## Privacy and scope

Run it only on photos the maintainer is authorized to process. Keep source
photos, ONNX models, face crops, immutable runs, and comparisons outside Git;
do not upload them as CI artifacts. Raw embeddings are intentionally retained
only in memory and are never written to the artifacts.

This spike is maintained for the current macOS environment. Production
containerization and any production recognition design are separate future
work; this README deliberately provides no Docker setup or run path.

## Local setup and verification

The repository uses Python 3.12. Create an ignored virtual environment and
install the isolated runtime:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt \
  -r experiments/face_recognition_spike/requirements.txt
```

Model-independent tests use generated images and adapters; they do not read
event photos or model files:

```sh
PYTHONPATH=experiments/face_recognition_spike:experiments/face_recognition_spike/tests:src/backend:src/worker \
.venv/bin/pytest -q experiments/face_recognition_spike/tests -m "not face_models"
```

The opt-in smoke test exercises the public `cluster` command with real local
models and one photo. It is skipped unless all three paths are provided:

```sh
DB_NAME=test DB_USER=test DB_PASSWORD=test DB_HOST=127.0.0.1 DB_PORT=5432 \
SECRET_KEY=test \
FACE_SPIKE_YUNET_MODEL=/absolute/models/yunet.onnx \
FACE_SPIKE_SFACE_MODEL=/absolute/models/sface.onnx \
FACE_SPIKE_SMOKE_PHOTOS=/absolute/smoke-photos \
PYTHONPATH=experiments/face_recognition_spike:experiments/face_recognition_spike/tests:src/backend:src/worker \
.venv/bin/pytest -q \
  experiments/face_recognition_spike/tests/test_model_smoke.py -m face_models
```

The database variables only satisfy the repository's pytest bootstrap; this
isolated test does not access Django or a database.

## Create an immutable cluster run

The output directory must not already exist. The input photo directory is a
flat inventory of immediate regular `.jpg` or `.jpeg` files: nested
directories, symlinked images, case-folded filename collisions, and an empty
inventory are rejected. A run never modifies the sources.

```sh
PYTHONPATH=experiments/face_recognition_spike:experiments/face_recognition_spike/tests:src/backend:src/worker \
.venv/bin/python -m face_spike cluster \
  --photos /absolute/photos \
  --yunet-model /absolute/models/yunet.onnx \
  --sface-model /absolute/models/sface.onnx \
  --output /absolute/runs/all-people-run-001 \
  --detection-threshold 0.75 \
  --min-face-px 32 \
  --severe-blur-threshold 25 \
  --borderline-blur-threshold 50 \
  --minimum-relative-area 0.0009 \
  --minimum-confidence 0.82 \
  --cluster-threshold 0.363 \
  --representative-threshold 0.363 \
  --distance-block-size 512 \
  --max-candidate-edges 100000
```

`--detection-threshold` controls which YuNet detections enter the measured
quality gate. The experiment calls `photo_worker.face_quality` directly: an
unusably small face or severe blur rejects independently, while borderline blur
requires low confidence or small relative area as corroboration. Rejected detections remain in `faces.csv`,
`faces.json`, and `faces/`, with `quality_rejected`, measured signal values,
and every rejection reason. `--cluster-threshold` creates candidate
edges and `--representative-threshold` guards each merge against chaining; the
initial values are experimental parameters, not production-calibrated values.
`--distance-block-size` bounds pairwise-distance working memory. For an
opt-in short smoke run, `--image-limit 1` limits the already validated photo
inventory. `--max-image-dimension` (default `12000`) and
`--max-image-pixels` (default `100000000`) reject oversized images before
decoding.

The command writes through a hidden sibling staging directory and atomically
publishes only a completed run. Fatal inventory, model, configuration, or
publication errors return nonzero without a completed output. Per-image
decode/detection and per-face alignment/embedding failures remain in the
completed evidence when other work can continue.

Each run contains:

| Artifact | Meaning |
| --- | --- |
| `manifest.json` | Input/model basenames and hashes, parameters, versions, timings, and materialization counts. |
| `faces.csv`, `faces.json`, `faces/` | Every face instance, status, metadata, and review crop; no raw embeddings. |
| `clusters.csv`, `clusters.json` | Anonymous cluster membership, representatives, and representative distances. |
| `annotated/` | Per-image detection previews. |
| `people/person-NNNN/` | Review crops and the cluster's unique source photos. A group photo can occur in several people directories. |
| `metrics.json` | Detection, embedding, cluster-size, singleton, and failure counts. |
| `report.html` | Lightweight local index: one representative crop per cluster. |
| `people/person-NNNN/index.html` | One cluster's face crops and source photos, loaded only when opened. |

## Compare a quality configuration with a sampled review

`compare-quality` accepts only the current immutable cluster-run
`manifest.json`/`faces.json` schema. Each run freezes its sorted photo inventory hash, per-photo media
SHA-256 values, generation hash, terminal photo states, detections, production
quality evidence, and technical failures. It rejects a changed or incomplete
cohort instead of dropping items. The baseline and candidate run directories
and candidate review crops are private local inputs outside Git.

```sh
PYTHONPATH=experiments/face_recognition_spike:src/backend:src/worker \
.venv/bin/python -m face_spike compare-quality \
  --baseline-run /absolute/private/baseline-quality-run \
  --candidate-run /absolute/private/candidate-quality-run \
  --output /absolute/private/quality-comparison-001 \
  --minimum-face-px 32 \
  --severe-blur-threshold 25 \
  --borderline-blur-threshold 50 \
  --minimum-relative-area 0.0009 \
  --minimum-confidence 0.82
```

The output is immutable and atomic. `comparison.json` contains aggregate and
detection evidence but no embeddings. Build the immutable ten-percent sampled
review from that frozen comparison; the output must be a new private directory.

```sh
PYTHONPATH=experiments/face_recognition_spike:src/backend:src/worker \
.venv/bin/python -m face_spike build-quality-sample \
  --comparison /absolute/private/quality-comparison-001 \
  --output /absolute/private/quality-sample-001 \
  --sample-size 1506 \
  --page-size 250
```

The command strictly revalidates the complete source comparison before it
selects its deterministic 1,506 rejected faces. It copies only those sampled
crops and the separate 100 retained threshold controls. It neither changes the
comparison nor accesses Django, PostgreSQL, Object Storage, the downloader, or
a running application. The sampled report has fixed logical pages of at most
250 faces, so `--page-size` must remain `250`.

Open the private `report.html` directly with `file://`. One reviewer labels
each sampled rejection as exactly one of `clear`, `blurred`, `unusably_small`,
or `uncertain`; keys `1` through `4` select those labels. The browser stores a
bundle-scoped local draft only on that device. The report exports a CSV only
after all 1,506 rows are labelled. A complete exported CSV can be imported
back into the same immutable sample; malformed, incomplete, duplicate,
unknown, or cross-sample rows are rejected.

Finalization creates a separate immutable weighted-evidence report. It is not
an approval or activation command.

```sh
PYTHONPATH=experiments/face_recognition_spike:src/backend:src/worker \
.venv/bin/python -m face_spike finalize-quality-sample \
  --sample /absolute/private/quality-sample-001 \
  --labels-csv /absolute/private/quality-sample-labels.csv \
  --reviewer reviewer-id \
  --reviewed-at 2026-08-08T00:00:00Z \
  --output /absolute/private/quality-sample-analysis-001
```

It validates the exact sample, complete labels, reviewer, timestamp, and new
output path, then reports raw and population-weighted evidence, a 95% Wilson
interval for `clear`, strata, retained controls, and every sampled `clear` or
`uncertain` crop. Treat it as sampled evidence only: it does not establish
zero clear-face loss or full-population manual coverage. It only informs a
later explicit experimental decision; it does not approve or activate a
generation.

Search relevance is independent of this sampled face-quality review. Use the
existing immutable benchmark proposal to review the 30 primary queries first,
with `relevant`, `different`, or `uncertain` annotations. Open and use a
deterministic replacement query page only when the primary query has fewer
than three relevant held-out photos or conflicts with an already selected
manual identity. After 30 valid person-disjoint queries are finalized, run the
closed set against both private indexes. This reuses the existing SFace query
path, exact cosine ranking, inclusive direct threshold `0.363`, one best face
per photo, deterministic ordering, and full-photo holdout for gallery proxies.

```sh
PYTHONPATH=experiments/face_recognition_spike:src/backend:src/worker \
.venv/bin/python -m face_spike compare-search \
  --benchmark /absolute/private/final-benchmark \
  --baseline-index /absolute/private/baseline-index \
  --candidate-index /absolute/private/candidate-index \
  --run /absolute/private/baseline-run \
  --yunet-model /absolute/models/yunet.onnx \
  --sface-model /absolute/models/sface.onnx \
  --quality-comparison /absolute/private/quality-comparison-001 \
  --output /absolute/private/search-comparison-001
```

The writer attempts hard links for source photos and copies only when the
filesystem does not permit that link. A singleton is a valid discovered
cluster and is preserved.

Open the local cluster index after completion. It is deliberately bounded: it
does not put every crop and full source photo into one browser page. Select a
cluster to open its separate detail page.

```sh
open /absolute/runs/all-people-run-001/report.html
```

## Review fragmented people locally

Build a separate, immutable review bundle from a completed run and its
completed Peakshot comparison. This command does not rerun models, alter either
input, copy embeddings, or copy source photos; its pages link to local media in
the immutable run.

```sh
PYTHONPATH=experiments/face_recognition_spike:experiments/face_recognition_spike/tests:src/backend:src/worker \
.venv/bin/python -m face_spike review \
  --run /absolute/runs/all-people-run-001 \
  --comparison /absolute/comparisons/all-people-run-001-vs-peakshot \
  --peakshot-export /absolute/peakshot-reference-export \
  --output /absolute/reviews/all-people-run-001-fragmentation-review-001
```

The output must be a new path. It contains a bounded `report.html`, one
`people/person-NNNN/index.html` page per cluster, and
`fragmentation-review.html`. The latter lists every deterministic pair of our
clusters for each Peakshot person aligned to two or more clusters. It is review
evidence: shared source photos, particularly group photos, are not proof that
two face clusters are the same person.

The review command validates the supplied Peakshot export with the same strict
rules as `compare`. It needs the validated person-to-filename sets to calculate
filtered relationship metrics by unique photo union: the same source photo in
two remaining clusters is counted once for that Peakshot person. The bundle
also contains `original-metrics.json`, a byte-for-byte copy of the comparison's
`metrics.json`. The displayed immutable metrics use embedded semantic values
validated while the bundle is built; the page links to `original-metrics.json`
as the byte-preserved source artifact for local audit.

For each cluster, choose one quality state: `usable`, `not_face` (for example a
shoe or hand), `low_quality` (a real but unusably small, blurred, or background
face), `mixed`, or leave it `unreviewed`. For each pair choose `same`,
`different`, or `uncertain`, and mark the evidence `direct` or
`group_photo_ambiguous`. A pair that touches `not_face`, `low_quality`, or
`mixed` is visibly `not_applicable` for identity review; its saved identity
decision remains available and is not erased.

The page always keeps the original immutable comparison metrics visible. Its
neighbouring **Provisional filtered metrics** exclude only explicitly unusable
clusters; `usable` and `unreviewed` remain included. Review coverage is shown
with these provisional numbers. A separate manual-fragmentation summary applies
saved `same` decisions as virtual connected components; it never rewrites
cluster IDs or relationship metrics.

Pair annotations and cluster qualities are stored only in this browser's
`localStorage`, under separate versioned keys scoped to this review bundle; they
are never sent anywhere and do not modify the run, comparison, source photos,
or Peakshot export. Use **Export combined CSV** for pairs and their repeated
cluster-quality values, and **Export cluster CSV** for a one-row-per-cluster
quality audit. Each matching import validates the exact bundle, headers, known
IDs, states, duplicates, and repeated-quality consistency before atomically
replacing its stored map. A malformed import leaves existing annotations
unchanged.

## Compare a completed run with Peakshot

Comparison is a separate, evaluation-only command. It requires an immutable
completed cluster run and a Peakshot export containing
`peakshot-person-photo-map.csv`, `peakshot-people.json`,
`peakshot-photos.json`, and `metadata.json`. The comparison output must not
exist and must not be inside either input directory.

```sh
PYTHONPATH=experiments/face_recognition_spike:experiments/face_recognition_spike/tests:src/backend:src/worker \
.venv/bin/python -m face_spike compare \
  --run /absolute/runs/all-people-run-001 \
  --peakshot-export /absolute/peakshot-reference-export \
  --output /absolute/comparisons/all-people-run-001-vs-peakshot
```

The evaluator aligns a result cluster to the Peakshot person with the greatest
photo intersection, then Jaccard similarity, then smallest `person_id`; it
does not alter the run and does not feed any reference data back into
clustering. It retains unmatched people and clusters, one-to-many splits, and
many-to-one merge evidence rather than silently discarding them.

The immutable comparison includes `comparison.json`, `metrics.json`,
`manifest.json`, `people-comparison.csv`, and `people-comparison.html`.
The required table makes visual reconciliation possible:

| Column | Meaning |
| --- | --- |
| `peakshot_person_id` | Person identifier from the Peakshot export. |
| `peakshot_photo_count` | Unique photos assigned by Peakshot. |
| `matched_cluster_ids` | All anonymous cluster IDs primarily aligned to that person. |
| `our_photo_count` | Unique photos across those clusters, without double-counting. |

It also reports intersection, missing, extra, precision, and recall counts;
the HTML links each cluster ID back to its cluster report section. Unmatched
clusters are listed separately with their review links.

## Local selfie-search smoke check

After a completed run, build its private face index and a trusted local benchmark proposal, then
run the short smoke check in this order: `build-index`, `build-benchmark`, `smoke-search`. The
five query crops come from the proposal; the command recomputes each crop through YuNet and SFace
instead of reusing its gallery vector.

```sh
PYTHONPATH=experiments/face_recognition_spike:experiments/face_recognition_spike/tests:src/backend:src/worker \
.venv/bin/python -m face_spike smoke-search \
  --proposal /absolute/benchmarks/proposal \
  --index /absolute/indexes/event-index \
  --run /absolute/runs/all-people-run-001 \
  --photos /absolute/photos \
  --yunet-model /absolute/models/yunet.onnx \
  --sface-model /absolute/models/sface.onnx \
  --output /absolute/smoke-searches/run-001
```

`--run` resolves the proposal's crop paths and `--photos` resolves the original photo links in
the report. All inputs are trusted, local artifacts and must remain outside Git. The new output
contains `results.json` and `report.html`; the latter is for a reviewer to visually decide whether
the same person appears among useful top results. The held-out source photo never appears for its
query, and each result photo is represented by its best matching face.

This is a bounded qualitative smoke test, not an accuracy measurement. Do not interpret it as
precision, recall, F1, calibration, a production identity claim, or approval for public-selfie
processing.

## Closed cluster-expansion evaluation

After a reviewer has finalized the person-split benchmark, compare direct search with strict-anchor
cluster expansion on that same immutable benchmark. The command consumes the final benchmark, its
reconciled private index, and the immutable cluster run; it writes one aggregate JSON report only.
The held-out source photo is excluded for every query, direct photos remain first, and expanded
photos are unique additions. Calibration and evaluation metrics remain separate.

```sh
PYTHONPATH=experiments/face_recognition_spike:experiments/face_recognition_spike/tests:src/backend:src/worker \
.venv/bin/python -m face_spike evaluate-cluster-expansion \
  --benchmark /absolute/benchmarks/final \
  --index /absolute/indexes/event-index \
  --cluster-run /absolute/runs/all-people-run-001 \
  --output /absolute/evaluations/cluster-expansion-001.json \
  --direct-threshold "$APPROVED_DIRECT_THRESHOLD" \
  --anchor-threshold "$APPROVED_STRONG_ANCHOR_THRESHOLD" \
  --configuration-hash "$CORPUS_CONFIGURATION_HASH" \
  --generations-json "$FACE_CLUSTER_GENERATIONS_JSON"
```

Set the two threshold variables to explicitly reviewed numeric values and
`CORPUS_CONFIGURATION_HASH` to the lowercase SHA-256 from the corpus proposed for activation. The
`FACE_CLUSTER_GENERATIONS_JSON` variable must point to the matching private normalized generation
document outside Git. The
command independently derives the same canonical corpus configuration identity from the validated
algorithm, actual normalized generation dictionaries, model/dimension, thresholds, and build limits;
a supplied hash mismatch rejects publication. Frozen input and cluster-membership identities remain
separate evidence. The report records direct/final recall, source-separated labelled precision, incremental correct and
incorrect photos, helped/harmed searches, false merge evidence, fragmentation/singletons, and
bounded resource measurements. It contains no query crop, photo, face, cluster, embedding, or
customer identity.

Keep every input and report outside Git. An evaluation report does not activate anything: activation
requires a separately reviewed operator action with the exact report hash, corpus hash, derived
policy hash (corpus plus reviewed direct/anchor thresholds), and
numeric gates confirmation.

## Honest interpretation

Peakshot is a useful silver-label reference, not ground truth. Differences in
the comparison are review evidence, not automatically recognition errors.
Group photos can be assigned to several Peakshot people and can contain
several detected faces, so photo-level disagreement alone cannot establish an
identity mistake. Review clean clusters, singletons, fragmented people,
merged clusters, group photos, and unmatched clusters before choosing at most
one narrowly scoped follow-up experiment.

No count, metric, alignment, or visually convincing cluster produced here
authorizes production use or claims a 100% identity match.

## Full-event evidence: 2026-07-26

One immutable full-event run (`all-people-run-001`) and its separate immutable
Peakshot comparison (`all-people-run-001-vs-peakshot`) were produced outside
Git using the documented commands and these parameters:

The scalable local review bundle for those immutable inputs is
`/Users/petrnikitin/Documents/Projects/photo-refs/reviews/all-people-run-001-fragmentation-review-003`.
Its `report.html` contains 1,092 representative crops and links to 1,092
separate detail pages; `fragmentation-review.html` contains 3,422 deterministic
fragmentation pairs and renders at most 25 pair cards at once. The earlier
`...-001` and `...-002` bundles are retained as immutable evidence of the
intermediate layouts; use `...-003` for review.

- detection threshold `0.75`, minimum face size `32` px;
- cluster and representative thresholds `0.363`;
- distance block size `512`; and
- no image limit.

The 1,420-photo run found 3,301 face instances with successful embeddings and
no recorded recoverable image or face-processing failures. It produced 1,092
clusters, including 615 singletons, in 297.33 seconds (297.06 seconds for
decode/detection/embedding and 0.27 seconds for clustering). Source-photo
review materialization used 3,296 hard links and no copies. The model hashes
recorded in the immutable manifest are YuNet
`8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` and
SFace `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`.

The comparison reconciled every successful face to exactly one cluster and
published rows for all 171 Peakshot people; every linked cluster ID exists.
The evaluation-only relationship metrics were precision `0.6932`, recall
`0.8997`, and F1 `0.7830`; cluster purity was `0.7057`. It aligned 165 people
and missed 6, aligned 902 clusters and left 190 unmatched, reported 142
fragmented reference people and 566 clusters with multi-person photo overlap.
There were 41 Peakshot-only inventory filenames and no run-only filenames.

Manual review covered a large cluster, a singleton, one component of a heavily
fragmented reference person, a cluster with many reference-photo overlaps, a
group photo, and an unmatched cluster. The reviewed large and unmatched
clusters were internally visually coherent; the singleton was retained as
intended. The group photo contained several detected faces and was represented
in several anonymous clusters. A visually coherent cluster can still have many
reference-photo overlaps when people co-occur in group photos, so the merge
metric is evidence for review rather than proof that identities were merged.
Likewise, the high fragmentation count is a discrepancy to investigate, not a
ground-truth error claim.

On this macOS/NumPy environment, blockwise matrix multiplication reproducibly
emitted divide-by-zero, overflow, and invalid-operation `RuntimeWarning`s.
A full read-only replay found zero non-finite matrix-product values and
reproduced all 1,092 cluster IDs, members, representatives, and distances
exactly from the immutable artifact. The warnings are therefore a documented
environment caveat, not evidence that this completed run was numerically
corrupt; they should be rechecked on any future environment or dependency
change.

The next narrow experiment is one repeat on the same event with only both
clustering thresholds changed from `0.363` to `0.390`, followed by the same
comparison and targeted review of the existing fragmented and multi-overlap
examples. It will measure the precision/recall, fragmentation, singleton, and
purity trade-off without changing detection, models, or reference handling.
