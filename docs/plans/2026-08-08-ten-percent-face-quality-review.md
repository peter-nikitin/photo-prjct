# Ten-percent Face-quality Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use the project
> `$execute-implementation-plan` skill to implement this plan task by task.

- Date: 2026-08-08
- Status: Draft
- Owner: project maintainer
- Related specification:
  [Ten-percent face-quality review design](../superpowers/specs/2026-08-08-ten-percent-face-quality-review-design.md)
- Related architecture: [Local face-recognition experiment boundary](../architecture.md)
- Related ADRs: none
- ADR impact: None — reversible, private, filesystem-only experiment tooling; no runtime,
  persistence, search-policy, or activation decision changes

**Goal:** Produce a reproducible 1,506-face sampled review, a resumable one-reviewer local UI, and
an immutable weighted analysis from the frozen 15,052-face quality comparison.

**Architecture:** Keep sampling/statistics independent from filesystem publication. A pure domain
module selects and analyzes typed comparison evidence; a separate artifact module strictly loads,
copies the bounded private crops, renders one paginated file-only review, validates its CSV, and
atomically publishes results. Thin CLI commands orchestrate those modules without Django or a
database.

**Tech stack:** Python 3.12, dataclasses, standard-library `csv`/`hashlib`/`json`/`math`, existing
`face_spike` comparison types and artifact validation, file-only HTML/CSS/JavaScript, pytest.

## Global constraints

- Treat the approved specification as authoritative for sampling, labels, weighting, confidence
  interval, privacy, and decision semantics.
- Strictly validate the frozen quality-comparison bundle before selecting or finalizing evidence.
- Select exactly 1,506 unique rejections and retain the existing 100 threshold controls separately.
- Derive selection order from SHA-256 of bundle identity, NUL, and face identifier; never use
  runtime randomness.
- Use exact rejection-reason tuples as strata, minimum one item per non-empty stratum when possible,
  then deterministic capacity-aware largest-remainder allocation.
- The review remains filesystem-only; do not access Django, PostgreSQL, Object Storage, port 55432,
  or either running application.
- Keep crops, labels, identifiers, reports, and absolute private paths outside Git.
- Publish only to non-existing output directories through hidden staging and atomic rename.
- Do not infer a pass/fail result, claim zero clear-face loss, alter production thresholds, or
  activate a processor generation.

## Scope

Implements the approved specification without scope changes. Existing full-population comparison,
indexes, 143 GiB search proposal, and failed-attempt evidence remain immutable inputs. Search
relevance continues through the already implemented benchmark annotation/finalization commands
after the sampled quality decision; no new search-ranking behavior is added here.

## Acceptance criteria

- The frozen comparison produces exactly 1,506 sampled rejections, with every exact reason stratum
  represented and recorded population/sample counts reconciling to 15,052/1,506.
- The local report displays no more than 250 rejected faces per logical page, supports keys `1`–`4`,
  resumes a bundle-scoped browser-local draft, validates imports, and exports only complete labels.
- Finalization rejects changed inputs and incomplete, duplicate, unknown, cross-sample, or invalid
  rows; valid labels produce weighted class estimates and the specified 95% Wilson interval.
- A private real-corpus smoke publishes a new immutable sampled bundle and verifies its exact
  identity without modifying its source comparison.
- No automatic acceptance, production configuration, deployment, database, or activation change is
  produced.

## Implementation

### Task 1: Deterministic sample and weighted analysis domain

**Files:**

- Create: `experiments/face_recognition_spike/face_spike/quality_sample.py`
- Create: `experiments/face_recognition_spike/tests/test_quality_sample.py`

- **Specification:** Sample; Analysis and decision.
- **Depends on:** Existing `QualityComparison`, `NewRejection`, and comparison-bundle SHA-256.
- **Produces:** Immutable `QualitySample`, `QualitySampleStratum`, `SampledRejection`,
  `QualitySampleLabel`, and `QualitySampleAnalysis` values; public functions
  `build_quality_sample(comparison, bundle_sha256, sample_size=1506)` and
  `analyze_quality_sample(sample, labels)`.

- [ ] Add failing tests for exact 1,506 selection, uniqueness, canonical reason-tuple strata,
  minimum rare-stratum allocation, capacity-aware largest remainder, stable hash selection,
  population/sample reconciliation, and repeatability across input order.
- [ ] Run
  `DB_PORT=55433 TEST_DB_NAME=findme_test_quality_sample_domain make test TESTS="experiments/face_recognition_spike/tests/test_quality_sample.py"`.
  Expected: the new imports or assertions fail before implementation.
- [ ] Implement frozen validated values and the smallest deterministic allocator/selector that
  satisfies the approved formulas. Reject invalid digest, sample size, duplicate faces, empty
  population, impossible allocation, or non-reconciling strata.
- [ ] Add failing analysis tests for raw counts, inclusion weights, population-weighted proportions,
  Kish effective sample size, clamped 95% Wilson bounds, four-label completeness, and deterministic
  `clear`/`uncertain` review lists.
- [ ] Implement analysis without filesystem access or automatic pass/fail state.
- [ ] Rerun the targeted command. Expected: all domain tests pass with no Django/database access.

### Task 2: Immutable sampled-review artifact and one-file reviewer

**Files:**

- Create: `experiments/face_recognition_spike/face_spike/quality_sample_artifacts.py`
- Create: `experiments/face_recognition_spike/tests/test_quality_sample_artifacts.py`
- Modify: `experiments/face_recognition_spike/face_spike/quality_comparison_artifacts.py`
  only to reuse or expose the existing strict comparison-bundle loader without duplicating its
  validation

- **Specification:** Frozen inputs; Review workflow; Privacy, isolation, and immutability.
- **Depends on:** Task 1 domain values and the existing strict quality-comparison loader.
- **Produces:** `write_quality_sample_bundle(output, source_bundle, sample)`,
  `load_quality_sample_bundle(path)`, `load_quality_sample_labels(path, sample)`, and
  `write_quality_sample_analysis(output, sample, labels, reviewer, reviewed_at)`.

- [ ] Add failing writer/loader tests for exact source binding, 1,506 sampled crops, 100 separate
  retained controls, no vectors/embeddings, canonical manifest/file hashes, no absolute private
  paths, no symlinks, no overwrite, tamper rejection, hidden-staging cleanup, and atomic publish.
- [ ] Run
  `DB_PORT=55433 TEST_DB_NAME=findme_test_quality_sample_artifacts make test TESTS="experiments/face_recognition_spike/tests/test_quality_sample_artifacts.py"`.
  Expected: artifact interfaces are absent or contract assertions fail.
- [ ] Implement the strict artifact loader/writer. Copy only sampled rejection crops and the frozen
  retained controls into bounded private subdirectories; never copy the full 15,052-crop population.
- [ ] Add failing report-contract tests for one `report.html`, logical pages capped at 250,
  lazy-loaded visible images, visible label definitions, keys `1`–`4`, previous/next navigation,
  bundle-scoped `localStorage`, progress, validated atomic import state, and export refusal until all
  1,506 rows are labelled.
- [ ] Implement the single-file paginated reviewer so all logical pages share one browser-local
  storage key under `file://`; keep controls in a separate non-estimation view.
- [ ] Add failing label/finalizer tests for exact headers and sample identity, incomplete/duplicate/
  unknown/invalid rows, reviewer/timestamp validation, weighted report fields, every `clear` and
  `uncertain` gallery item, bounded aggregate output, and output no-overwrite semantics.
- [ ] Implement CSV loading and immutable analysis publication using Task 1 only; do not call the
  full-coverage `finalize-quality-review` approval path.
- [ ] Rerun the targeted command. Expected: all artifact, UI-contract, and finalizer tests pass.

### Task 3: CLI orchestration and real frozen-corpus sample

**Files:**

- Modify: `experiments/face_recognition_spike/face_spike/cli.py`
- Modify: `experiments/face_recognition_spike/tests/test_selfie_search_cli.py`
- Modify: `experiments/face_recognition_spike/README.md`
- Private output outside Git: a new child of
  `/Users/petrnikitin/Documents/Projects/photo-prjct-private/face-quality-benchmark/cyclingrace-vechernee-sadovoe/run-20260808T015921Z`

- **Specification:** Entire sampled-review workflow; Search-level review.
- **Depends on:** Tasks 1–2 and frozen comparison bundle
  `f1028cf1e581645dd0cf108e356394dc5ada838b92c9f662c1356cd52e657b48`.
- **Produces:** CLI commands `build-quality-sample` and `finalize-quality-sample`; documented exact
  operator flow; one immutable real sampled-review bundle ready for the sole reviewer.

- [ ] Add failing CLI tests for required arguments, defaults `sample_size=1506` and `page_size=250`,
  sanitized nonzero failures, existing-output rejection, and exact delegation to the domain/artifact
  seams. Finalization arguments are `--sample`, `--labels-csv`, `--reviewer`, `--reviewed-at`, and
  `--output`.
- [ ] Run
  `DB_PORT=55433 TEST_DB_NAME=findme_test_quality_sample_cli make test TESTS="experiments/face_recognition_spike/tests/test_selfie_search_cli.py"`.
  Expected: parser/dispatch assertions fail before the commands exist.
- [ ] Add the two thin CLI configurations, parser entries, orchestration, and sanitized error
  boundary. Do not add Django settings or a database path.
- [ ] Document build, local `file://` review, CSV export/import, finalization, sampled-evidence
  interpretation, and the separate existing 30-query relevance flow. State that replacement query
  pages are opened only when a primary query is invalid or duplicates an identity.
- [ ] Rerun Task 1–3 tests together with
  `DB_PORT=55433 TEST_DB_NAME=findme_test_quality_sample_integration make test TESTS="experiments/face_recognition_spike/tests/test_quality_sample.py experiments/face_recognition_spike/tests/test_quality_sample_artifacts.py experiments/face_recognition_spike/tests/test_selfie_search_cli.py"`.
  Expected: all pass.
- [ ] Run `build-quality-sample` against the frozen private comparison into a new output path.
  Expected: exit 0; exactly 1,506 unique sampled rejections, 100 retained controls, complete stratum
  reconciliation, matching source hash, no source mutation, and no database process or connection.
- [ ] Strict-load the result, hash every published file, inspect the first/middle/last logical page,
  round-trip a separate fixture label CSV through `finalize-quality-sample`, and preserve that smoke
  analysis as non-human fixture evidence. Do not represent fixture labels as the operator decision.
- [ ] Present the real `report.html` to the reviewer and stop for the exported human label CSV.
  After export, run `finalize-quality-sample` into a new immutable output and present its weighted
  report for the operator's explicit experimental decision.

### Task 4: Regression and architecture/ADR reconciliation

**Files:**

- Modify: `docs/engineering-jobs.md` to record the completed real sampled bundle as local evidence
- Inspect without expected modification: `docs/architecture.md`

- **Specification:** Privacy, isolation, and immutability; Verification.
- **Depends on:** Tasks 1–3 implementation and independent task reviews.
- **Produces:** CI-ready experiment tooling plus an explicit record that runtime architecture,
  production activation, and accepted ADR boundaries remain unchanged.

- [ ] Run
  `DB_PORT=55433 TEST_DB_NAME=findme_test_quality_sample_full make test TESTS="experiments/face_recognition_spike/tests"`.
  Expected: the complete experiment suite passes; the opt-in real-model smoke remains skipped unless
  its explicit local environment is supplied.
- [ ] Run `DB_PORT=55433 TEST_DB_NAME=findme_test_quality_sample_check make check` only after focused
  suites pass and no other full suite is using this worktree/database. Expected: repository Python
  tests, lint, format check, types, and migration checks pass.
- [ ] Verify `git diff --check`, inspect the complete diff for private paths/media/labels/identifiers/
  vectors/secrets, and confirm only intended source, test, README, plan/spec, and bounded evidence
  documentation is tracked.
- [ ] Reconcile delivered behavior with the approved specification, `docs/architecture.md`, and ADR
  index. Expected outcome: no ADR impact and no architecture update; record only verified local
  experiment evidence in `docs/engineering-jobs.md` when available.
- [ ] Use `$execute-implementation-plan` for task-isolated implementation and independent review.

## Verification

Run in order, never overlapping full suites:

1. `DB_PORT=55433 TEST_DB_NAME=findme_test_quality_sample_domain make test TESTS="experiments/face_recognition_spike/tests/test_quality_sample.py"` — domain selection and statistics pass.
2. `DB_PORT=55433 TEST_DB_NAME=findme_test_quality_sample_artifacts make test TESTS="experiments/face_recognition_spike/tests/test_quality_sample_artifacts.py"` — immutable artifact/UI/finalizer contracts pass.
3. `DB_PORT=55433 TEST_DB_NAME=findme_test_quality_sample_integration make test TESTS="experiments/face_recognition_spike/tests/test_quality_sample.py experiments/face_recognition_spike/tests/test_quality_sample_artifacts.py experiments/face_recognition_spike/tests/test_selfie_search_cli.py"` — CLI integration passes.
4. `DB_PORT=55433 TEST_DB_NAME=findme_test_quality_sample_full make test TESTS="experiments/face_recognition_spike/tests"` — complete experiment regression passes.
5. `DB_PORT=55433 TEST_DB_NAME=findme_test_quality_sample_check make check` — repository Python quality suite passes in the isolated implementation database.
6. Real filesystem smoke — 1,506/15,052 sampled rejections, 100 retained controls, exact source and
   stratum hashes, fixture-label round trip, zero source changes, and no DB/application access.

## Operational impact and rollout

None. Commands are opt-in local experiment tools. They create private filesystem artifacts only and
do not change dependencies, migrations, settings, Docker images, deployment workflows, event state,
processor defaults, or active generations. Human quality and relevance decisions remain explicit
later inputs.

## Rollback

Revert the source/documentation commits and stop using the sampled commands. Preserve already
published private sampled-review and analysis bundles as immutable attempt evidence; they are not
runtime state and require no database rollback. Continue using the frozen full comparison and
baseline generation.

## Open questions

None.
