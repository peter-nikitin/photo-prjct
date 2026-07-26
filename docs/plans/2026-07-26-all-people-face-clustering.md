# All-People Face Clustering Implementation Plan

- Date: 2026-07-26
- Status: Approved
- Owner: project maintainer
- Related specification:
  [`2026-07-26-all-people-face-clustering-design.md`](../superpowers/specs/2026-07-26-all-people-face-clustering-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md), proposed Recognition
  module, recognition processing flow, face-data security constraints, and evolution stage 6
- Related ADRs: none
- ADR impact: None — reversible implementation detail

## Goal

Deliver the approved
[all-people clustering outcome](../superpowers/specs/2026-07-26-all-people-face-clustering-design.md#goal)
as one isolated experiment change, then run it over the supplied event and compare the immutable
result with the supplied Peakshot export.

## Scope

Implements the approved
[scope](../superpowers/specs/2026-07-26-all-people-face-clustering-design.md#scope) without changes.

## Acceptance Criteria

The authoritative behavioral criteria are in the specification's
[Acceptance Criteria](../superpowers/specs/2026-07-26-all-people-face-clustering-design.md#acceptance-criteria).
Delivery additionally requires:

- the complete isolated test suite and repository CI-equivalent checks to pass;
- one immutable full-event clustering run;
- one immutable Peakshot comparison containing `people-comparison.csv` and
  `people-comparison.html`; and
- recorded aggregate results and limitations in the experiment README.

## Implementation

### Task 1: Extract Every Face Instance

**Specification:**
[Face-Instance Model](../superpowers/specs/2026-07-26-all-people-face-clustering-design.md#face-instance-model)
and [Inputs and Isolation Boundary](../superpowers/specs/2026-07-26-all-people-face-clustering-design.md#inputs-and-isolation-boundary).

**Files:**

- Create `experiments/face_recognition_spike/face_spike/inventory.py`.
- Create `experiments/face_recognition_spike/face_spike/analysis.py`.
- Create `experiments/face_recognition_spike/tests/test_inventory.py`.
- Create `experiments/face_recognition_spike/tests/test_analysis.py`.

**Depends on:** None.

**Produces:** An unlabelled event-photo inventory and pixel-free per-image analyses containing all
accepted face instances, their stable IDs, per-face statuses, and successful in-memory embeddings.
The new modules do not import or adapt the previous labelled domain, retrieval, artifacts, or CLI.

- [x] Add failing tests for deterministic unlabelled inventory, unsupported or ambiguous
  inventories, multiple faces in one image, stable face IDs, and one face failing without
  discarding the others.
- [x] Run the focused inventory and analysis tests and confirm they fail because the new modules
  are absent.
- [x] Add the event-photo inventory and multi-face analysis types without compatibility generics or
  labelled fields.
- [x] Extract an embedding independently for every accepted detection while retaining per-face
  recoverable failures.
- [x] Add a regression test proving decoded RGB/BGR arrays are released before the next image.
- [x] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q \
    experiments/face_recognition_spike/tests/test_inventory.py \
    experiments/face_recognition_spike/tests/test_analysis.py
  ```

  Expected: all focused new-pipeline tests pass.

### Task 2: Cluster Successful Face Instances

**Specification:**
[Clustering](../superpowers/specs/2026-07-26-all-people-face-clustering-design.md#clustering).

**Files:**

- Create `experiments/face_recognition_spike/face_spike/clustering.py`.
- Create `experiments/face_recognition_spike/tests/test_clustering.py`.

**Depends on:** Task 1 face-instance analyses.

**Produces:** Deterministic `person-NNNN` clusters, representative face IDs, member face IDs, and
member-to-representative distances.

- [x] Add failing tests for threshold boundaries, deterministic output, block-size independence,
  singleton retention, exclusion of failed embeddings, and the approved representative guard
  preventing an obvious single-link chain merge.
- [x] Run the new clustering test file and confirm it fails because the module is absent.
- [x] Implement bounded blockwise candidate-distance calculation and deterministic guarded graph
  unions exactly as specified.
- [x] Assign stable cluster IDs after final membership is known.
- [x] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q \
    experiments/face_recognition_spike/tests/test_clustering.py
  ```

  Expected: all clustering scenarios pass with identical membership across repeated runs and
  configured block sizes.

### Task 3: Publish the Immutable Cluster Run

**Specification:**
[Cluster Artifacts](../superpowers/specs/2026-07-26-all-people-face-clustering-design.md#cluster-artifacts)
and [Failure Handling](../superpowers/specs/2026-07-26-all-people-face-clustering-design.md#failure-handling).

**Files:**

- Create `experiments/face_recognition_spike/face_spike/cluster_artifacts.py`.
- Create `experiments/face_recognition_spike/face_spike/cluster_report.py`.
- Create `experiments/face_recognition_spike/face_spike/image_decoder.py`.
- Create `experiments/face_recognition_spike/face_spike/models.py`.
- Rewrite `experiments/face_recognition_spike/face_spike/cli.py` and
  `experiments/face_recognition_spike/face_spike/__main__.py` for the new commands.
- Create `experiments/face_recognition_spike/tests/test_cluster_artifacts.py`.
- Create `experiments/face_recognition_spike/tests/test_cluster_cli.py` and
  `experiments/face_recognition_spike/tests/test_cluster_report.py`.
- Delete the previous `domain.py`, `dataset.py`, `pipeline.py`, `opencv_models.py`, `retrieval.py`,
  `artifacts.py`, `report.py`, `scripts/build_labels.py`, and their dedicated legacy tests after the
  new cluster command covers the required behavior. Delete the old model-smoke test; Task 5 adds a
  smoke test for the new command.

**Depends on:** Task 1 analyses and Task 2 clusters.

**Produces:** The `face_spike cluster` command and the complete immutable artifact tree defined by
the specification.

- [x] Add failing tests for `cluster` arguments and validation, every required artifact, all-face
  crops, deterministic serialization, singleton directories, one photo per cluster, one group
  photo in multiple clusters, hard-link fallback, no serialized embeddings or absolute paths, and
  staging cleanup after failure.
- [x] Run the focused CLI and cluster-artifact tests and confirm the missing behavior.
- [x] Add cluster configuration and orchestration without labels, primary-face retrieval, or
  Peakshot inputs.
- [x] Stream diagnostic writes per image, drop pixels, cluster the retained embeddings, and publish
  machine-readable and visual artifacts atomically.
- [x] Materialize unique source photos per cluster using the approved hard-link/copy behavior.
- [x] Render a static local report with stable `person-NNNN` anchors for later comparison links.
- [x] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q \
    experiments/face_recognition_spike/tests/test_inventory.py \
    experiments/face_recognition_spike/tests/test_analysis.py \
    experiments/face_recognition_spike/tests/test_clustering.py \
    experiments/face_recognition_spike/tests/test_cluster_cli.py \
    experiments/face_recognition_spike/tests/test_cluster_artifacts.py \
    experiments/face_recognition_spike/tests/test_cluster_report.py
  ```

  Expected: the new cluster command and artifact contract pass with no legacy workflow remaining.

### Task 4: Compare a Completed Run with Peakshot

**Specification:**
[Peakshot Comparison](../superpowers/specs/2026-07-26-all-people-face-clustering-design.md#peakshot-comparison),
[Required People Comparison Table](../superpowers/specs/2026-07-26-all-people-face-clustering-design.md#required-people-comparison-table),
and [Failure Handling](../superpowers/specs/2026-07-26-all-people-face-clustering-design.md#failure-handling).

**Files:**

- Create `experiments/face_recognition_spike/face_spike/comparison.py`.
- Create `experiments/face_recognition_spike/face_spike/comparison_report.py`.
- Modify `experiments/face_recognition_spike/face_spike/cli.py`.
- Create `experiments/face_recognition_spike/tests/test_comparison.py`.
- Modify `experiments/face_recognition_spike/tests/test_cluster_cli.py` for two-command dispatch.

**Depends on:** Task 3 serialized `clusters.json` and stable report anchors. It must not import or
invoke detection, recognition, or clustering.

**Produces:** The `face_spike compare` command and an immutable comparison containing
`comparison.json`, `metrics.json`, `people-comparison.csv`, `people-comparison.html`, and
`manifest.json`.

- [x] Add failing tests for Peakshot and cluster-run validation, case-sensitive inventory
  mismatches, deterministic alignment, fragmentation, merges, singleton statistics, group-photo
  statistics, unmatched people and clusters, unique-photo counts, table headers, cluster links,
  and atomic publication.
- [x] Run the focused comparison and CLI tests and confirm the missing behavior.
- [x] Implement reference loading and evaluation-only alignment according to the approved
  comparison contract.
- [x] Calculate relationship metrics and per-person table rows without silently dropping inventory
  differences.
- [x] Publish the machine-readable comparison and static linked HTML table atomically.
- [x] Run:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q \
    experiments/face_recognition_spike/tests/test_comparison.py \
    experiments/face_recognition_spike/tests/test_cluster_cli.py
  ```

  Expected: deterministic comparison artifacts pass all split, merge, unmatched, and table
  scenarios.

### Task 5: Document and Verify the Isolated Implementation

**Specification:**
[Acceptance Criteria](../superpowers/specs/2026-07-26-all-people-face-clustering-design.md#acceptance-criteria).

**Files:**

- Modify `experiments/face_recognition_spike/README.md`.

**Depends on:** Tasks 1–4.

**Produces:** Current usage and privacy documentation plus a model-independent verified
implementation ready for the real event run.

- [x] Document only the new `cluster` and evaluation-only `compare` workflows, parameters, artifact
  meanings, privacy boundaries, macOS-only spike scope, and honest interpretation rules. Remove
  Docker setup/run instructions; production containerization is a separate future implementation.
- [x] Run:

  ```sh
  .venv/bin/ruff format --check experiments/face_recognition_spike
  .venv/bin/ruff check experiments/face_recognition_spike
  .venv/bin/mypy experiments/face_recognition_spike/face_spike
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q experiments/face_recognition_spike/tests -m "not face_models"
  ```

  Expected: every command exits `0`; record exact test counts.
- [x] Run the opt-in real-model smoke test with the supplied models and authorized photos.
- [x] Inspect the implementation diff for spec coverage, bounded memory, immutable publication,
  evaluator isolation, privacy, and absence of unrelated changes.

### Task 6: Run the Full Event and Compare It

**Specification:** The full-event evidence required by
[Acceptance Criteria](../superpowers/specs/2026-07-26-all-people-face-clustering-design.md#acceptance-criteria).

**Files:** External immutable artifacts only; update the experiment README afterward with aggregate
evidence and limitations.

**Depends on:** Task 5 verification.

**Produces:** One full clustering run and one Peakshot comparison.

- [x] Confirm these paths do not exist; if either exists, use the next unused numeric suffix:

  ```text
  /path/to/photo-refs/runs/all-people-run-001
  /path/to/photo-refs/comparisons/all-people-run-001-vs-peakshot
  ```

- [x] Run clustering:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/python -m face_spike cluster \
    --photos /path/to/photo-refs/all \
    --yunet-model /path/to/photo-refs/models/yunet.onnx \
    --sface-model /path/to/photo-refs/models/sface.onnx \
    --output /path/to/photo-refs/runs/all-people-run-001 \
    --detection-threshold 0.75 \
    --min-face-px 32 \
    --cluster-threshold 0.363 \
    --representative-threshold 0.363 \
    --distance-block-size 512
  ```

  Expected: exit `0` with a complete immutable run and no source mutation.
- [x] Run comparison:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/python -m face_spike compare \
    --run /path/to/photo-refs/runs/all-people-run-001 \
    --peakshot-export \
      /path/to/photo-refs/peakshot-reference-exports/20260725T053629Z \
    --output \
      /path/to/photo-refs/comparisons/all-people-run-001-vs-peakshot
  ```

  Expected: exit `0` with every required comparison artifact.
- [x] Reconcile manifest counts with CSV/JSON rows and confirm every successful face belongs to
  exactly one cluster, all 171 Peakshot people have comparison rows, and every referenced cluster
  ID exists.
- [x] Manually inspect representative large, singleton, fragmented, merged, group-photo, and
  unmatched cases.
- [x] Record commands, parameters, counts, metrics, observed limitations, and one narrow next
  experiment in the README without committing external artifacts or personal filenames.

### Final Task: Review, Repository Verification, and Architecture Reconciliation

**Specification:** Entire approved design.

**Depends on:** Tasks 1–6.

**Produces:** Independently reviewed changes, complete verification evidence, and an explicit
architecture/ADR outcome.

- [x] Prepare the complete unstaged task diff and dispatch one independent reviewer for spec
  compliance, clustering correctness, comparison semantics, privacy, bounded memory, and immutable
  output.
- [x] Return fixes to the implementer and re-review with the same reviewer.
- [x] Re-read `.github/workflows/ci.yml` and run every current CI-equivalent command using explicit
  `.venv/bin/` tools and the documented container fallbacks.
- [x] Record exact Python, JavaScript, visual, formatting, lint, typing, Django, and migration-check
  outcomes.
- [x] Reconcile the result with `docs/architecture.md` and accepted ADRs. Expected outcome:
  `None — reversible implementation detail`; the isolated experiment does not update implemented
  production architecture.
- [x] After reviewer approval and final green verification, stage only task files and create one
  implementation commit. Do not commit models, source photos, runs, comparisons, caches, or local
  absolute paths.

## Verification

Task-level commands appear once with the task they verify. Final verification uses the complete
current CI command set derived from `.github/workflows/ci.yml`; it must not be replaced by the
isolated experiment suite.

## Operational Impact and Rollout

None. The change remains an offline experiment and does not alter Django, databases, deployment,
cloud resources, or customer-facing behavior.

## Rollback

Revert the experiment implementation and documentation commit. External immutable evidence remains
outside Git and may be retained for comparison or deleted separately when no longer needed.

## Open Questions

None.
