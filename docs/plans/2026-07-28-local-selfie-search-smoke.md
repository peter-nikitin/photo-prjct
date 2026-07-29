# Local Selfie Search Happy-Path Smoke Implementation Plan

- Date: 2026-07-28
- Status: Approved
- Owner: project maintainer
- Related specification:
  [`docs/superpowers/specs/2026-07-28-local-selfie-search-smoke-design.md`](../superpowers/specs/2026-07-28-local-selfie-search-smoke-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md), proposed Recognition and
  Search modules and event-scoped face search
- Related ADRs: none
- ADR impact: None — reversible implementation detail

## Goal

Deliver the specification's qualitative five-to-ten-query
[happy-path smoke search](../superpowers/specs/2026-07-28-local-selfie-search-smoke-design.md#goal)
without implementing the deferred 30-person metric benchmark.

## Scope

Implement the approved
[scope and non-goals](../superpowers/specs/2026-07-28-local-selfie-search-smoke-design.md#scope)
as one cohesive experimental command. The existing Tasks 7–13 in
[`docs/plans/2026-07-26-local-selfie-search.md`](2026-07-26-local-selfie-search.md) remain deferred
and are not prerequisites for this delivery.

## Acceptance criteria

Use the specification's
[acceptance criteria](../superpowers/specs/2026-07-28-local-selfie-search-smoke-design.md#acceptance-criteria).
The implementation additionally requires the existing index, benchmark, and cluster test suites to
remain unchanged and passing.

## Planned file structure and interfaces

- Create `experiments/face_recognition_spike/face_spike/smoke_search.py` for query processing,
  exact face ranking, unique-photo aggregation, result values, deterministic JSON, and bounded HTML.
- Create `experiments/face_recognition_spike/tests/test_smoke_search.py` for the focused domain and
  artifact behaviors.
- Modify `experiments/face_recognition_spike/face_spike/cli.py` to add the immutable
  `SmokeSearchConfig`, trusted-input compatibility wiring, model loading, and `smoke-search`
  dispatch.
- Modify `experiments/face_recognition_spike/tests/test_selfie_search_cli.py` for command parsing,
  successful wiring, and one failed-query publication boundary.
- Modify `experiments/face_recognition_spike/README.md` with the one-command local workflow,
  qualitative interpretation, and explicit non-goals.

The module exposes focused immutable values for a query result and photo result plus:

- `rank_unique_photos(query_embedding, index, held_out_filename, *, limit)`;
- `run_smoke_search(proposal, index, query_processor, *, query_count, limit)`; and
- `write_smoke_search_output(output, result, run_root, photos_root)`.

Exact value fields follow the specification's
[result contract](../superpowers/specs/2026-07-28-local-selfie-search-smoke-design.md#result-contract).
The CLI query processor reuses the public single-image analysis contract from the completed first
task and never reads the query vector from the index.

## Implementation

### Task 1: Implement and expose the bounded happy-path smoke search

**Files:**

- Create: `experiments/face_recognition_spike/face_spike/smoke_search.py`
- Create: `experiments/face_recognition_spike/tests/test_smoke_search.py`
- Modify: `experiments/face_recognition_spike/face_spike/cli.py`
- Modify: `experiments/face_recognition_spike/tests/test_selfie_search_cli.py`
- Modify: `experiments/face_recognition_spike/README.md`

- **Specification:** Public Interface, Data Flow, Result Contract, Happy-Path Failure Boundary,
  Explicit Non-Goals, and Acceptance Criteria.
- **Depends on:** completed reusable analysis, face index, and benchmark proposal implementation.
- **Produces:** the public `face_spike smoke-search` command, `results.json`, and `report.html`.

- [ ] Add focused failing tests that prove exact cosine ordering with stable face-ID tie breaking,
  complete source-photo holdout, best-face unique-photo aggregation with stable filename tie
  breaking, bounded query/result selection, deterministic JSON/HTML output, and absence of raw
  vectors.
- [ ] Run
  `PYTHONPATH=experiments/face_recognition_spike .venv/bin/pytest -q experiments/face_recognition_spike/tests/test_smoke_search.py`
  and confirm collection fails because `face_spike.smoke_search` does not exist.
- [ ] Implement immutable result values, normalized-vector validation, vectorized exact cosine
  distance, full-filename holdout, unique-photo aggregation, selected-query orchestration, and the
  two bounded output files. Keep this logic in the focused module; do not add calibration,
  annotations, thresholds, metric functions, or generic artifact infrastructure.
- [ ] Rerun the focused test module and confirm every smoke-search domain and output test passes.
- [ ] Add CLI tests for the exact approved arguments and defaults, compatible happy-path wiring,
  existing output, and one query-processing failure that leaves no completed output.
- [ ] Run
  `PYTHONPATH=experiments/face_recognition_spike .venv/bin/pytest -q experiments/face_recognition_spike/tests/test_selfie_search_cli.py -k smoke_search`
  and confirm the tests fail because the command is not registered.
- [ ] Implement `SmokeSearchConfig`, parser registration, trusted local input loading,
  proposal/index/model compatibility checks, real YuNet/SFace query processing through
  `analyze_decoded_event_photo`, and sanitized command dispatch. Resolve query crops from `--run`
  and report originals from `--photos`; do not persist absolute paths.
- [ ] Rerun the focused CLI selection and confirm all `smoke_search` tests pass.
- [ ] Document the command ordering (`build-index`, `build-benchmark`, `smoke-search`), trusted
  local input roots, visual-review criterion, and prohibition on interpreting the output as
  measured accuracy.
- [ ] Run the complete focused regression command:

  ```sh
  PYTHONPATH=experiments/face_recognition_spike \
  .venv/bin/pytest -q \
    experiments/face_recognition_spike/tests/test_smoke_search.py \
    experiments/face_recognition_spike/tests/test_selfie_search_cli.py \
    experiments/face_recognition_spike/tests/test_index.py \
    experiments/face_recognition_spike/tests/test_index_artifacts.py \
    experiments/face_recognition_spike/tests/test_benchmark.py \
    experiments/face_recognition_spike/tests/test_benchmark_artifacts.py \
    experiments/face_recognition_spike/tests/test_benchmark_report.py
  ```

  Expected: all selected tests pass with no warnings or collection errors.

- [ ] Run Ruff format/check and mypy over every changed Python file, then run `git diff --check`.
- [ ] Self-review against every acceptance criterion and confirm no Task 7–13 metric or
  production-hardening behavior entered the diff.

### Final task: Architecture and ADR reconciliation

- [ ] Compare the delivered behavior with the approved smoke specification,
  `docs/architecture.md`, and `docs/adr/README.md`.
- [ ] Confirm the change remains an isolated local experiment and does not change implemented
  product architecture, runtime configuration, deployment, persistence, or an accepted ADR.
- [ ] Record `None — reversible implementation detail`; do not update current architecture status.

## Verification

After independent task approval, rerun from the project `.venv`:

```sh
PYTHONPATH=experiments/face_recognition_spike \
.venv/bin/pytest -q \
  experiments/face_recognition_spike/tests/test_smoke_search.py \
  experiments/face_recognition_spike/tests/test_selfie_search_cli.py \
  experiments/face_recognition_spike/tests/test_index.py \
  experiments/face_recognition_spike/tests/test_index_artifacts.py \
  experiments/face_recognition_spike/tests/test_benchmark.py \
  experiments/face_recognition_spike/tests/test_benchmark_artifacts.py \
  experiments/face_recognition_spike/tests/test_benchmark_report.py

.venv/bin/ruff format --check \
  experiments/face_recognition_spike/face_spike/smoke_search.py \
  experiments/face_recognition_spike/face_spike/cli.py \
  experiments/face_recognition_spike/tests/test_smoke_search.py \
  experiments/face_recognition_spike/tests/test_selfie_search_cli.py

.venv/bin/ruff check \
  experiments/face_recognition_spike/face_spike/smoke_search.py \
  experiments/face_recognition_spike/face_spike/cli.py \
  experiments/face_recognition_spike/tests/test_smoke_search.py \
  experiments/face_recognition_spike/tests/test_selfie_search_cli.py

PYTHONPATH=experiments/face_recognition_spike \
.venv/bin/mypy \
  experiments/face_recognition_spike/face_spike/smoke_search.py \
  experiments/face_recognition_spike/face_spike/cli.py

git diff --check
```

Expected: all tests pass, Ruff reports no formatting or lint violations, mypy reports no issues,
and `git diff --check` emits no output.

## Operational impact and rollout

None. The command is an opt-in macOS-only local experiment. It adds no dependency, migration,
environment variable, Django route, worker, deployment step, or persistent production format.
Source photos, models, index, crops, and smoke outputs remain external local artifacts.

## Rollback

Remove the `smoke-search` command, focused module/tests, and README section. Existing immutable
cluster, index, and proposal artifacts remain unchanged and usable.

## Open questions

None.
