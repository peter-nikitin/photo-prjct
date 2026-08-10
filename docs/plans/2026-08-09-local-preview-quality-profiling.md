# Local Preview Quality Profiling Implementation Plan

- Date: 2026-08-09
- Status: Approved for execution
- Owner: project maintainer
- Related specification:
  [`2026-08-09-local-preview-quality-profile-design.md`](../superpowers/specs/2026-08-09-local-preview-quality-profile-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md), preview-first processing and
  immutable processing attempts
- Related ADRs: [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md), and
  [ADR 0024](../adr/0024-use-gallery-face-as-search-query.md)
- ADR impact: None — reversible private local experiment tooling

> Execute this plan with `$execute-implementation-plan` and its required subagent review loop.

## Goal

Implement and run the approved specification through the human profile-selection checkpoint: build
the exact 17,043-photo production-contract preview corpus, then compare detector and quality
profiles on the seven reported photos and the frozen 10% sample without database or S3 writes.

## Scope

This plan stops before the full database replay. The selected profile values are intentionally not
guessed by an implementer; a follow-up plan will freeze the operator-approved profile and replay it
as one new immutable generation on isolated PostgreSQL port 55433.

## Acceptance criteria

In addition to the specification's acceptance criteria:

- the preview materializer is independently runnable as
  `PYTHONPATH=src/worker:experiments/face_recognition_spike .venv/bin/python -m face_spike.local_preview_quality materialize ...`;
- the profiler is independently runnable through the same module's `compare` command;
- both commands accept explicit source, output, model, and sample paths and have no database or
  object-storage settings;
- immutable outputs publish only after validation and a repeated materialization reuses all 17,043
  verified previews;
- the comparison report contains image and crop links relative to its own bundle, so it opens from
  `file://` without the broken cross-directory media links seen in the earlier report.

## Implementation

### Task 1: Production-contract local preview corpus

**Files:**

- Create `experiments/face_recognition_spike/face_spike/preview_corpus.py`.
- Create `experiments/face_recognition_spike/tests/test_preview_corpus.py`.
- Create `experiments/face_recognition_spike/face_spike/local_preview_quality.py` with the
  `materialize` command only.
- Create `experiments/face_recognition_spike/tests/test_local_preview_quality.py` for command
  argument and exit-code coverage.

- **Specification:** `Preview corpus`, its failure semantics, and the corresponding acceptance
  criteria.
- **Depends on:** the verified source manifest and `src/worker/photo_worker/preview.py`.
- **Produces:** `materialize_preview_corpus(source_manifest: Path, originals: Path, output: Path,
  *, workers: int) -> PreviewCorpusManifest` plus `load_verified_preview_corpus(output: Path) ->
  PreviewCorpusManifest`. The manifest maps each photo ID to its verified preview path and frozen
  evidence.

- [ ] Write failing unit tests for deterministic photo-ID mapping, exact `OutputSlot` contract,
  EXIF-oriented output evidence, atomic staging/publication, source-manifest mismatch, symlink and
  unexpected-file rejection, incomplete failure preservation, and verified-file reuse.
- [ ] Run
  `PYTHONPATH=src/worker:experiments/face_recognition_spike .venv/bin/pytest -q experiments/face_recognition_spike/tests/test_preview_corpus.py experiments/face_recognition_spike/tests/test_local_preview_quality.py`
  and confirm failures are caused only by the missing module and command.
- [ ] Implement bounded parallel generation using `photo_worker.preview.generate_preview`, partial
  files in the destination filesystem, canonical JSON hashing, and atomic final manifest
  publication. Never accept a filename not declared by the source manifest and never follow a
  symlink.
- [ ] Run the targeted command again and require all tests to pass.

### Task 2: Filesystem-only detector and quality profile comparison

**Files:**

- Create `experiments/face_recognition_spike/face_spike/quality_profiles.py`.
- Create `experiments/face_recognition_spike/face_spike/preview_profile_comparison.py`.
- Create `experiments/face_recognition_spike/face_spike/preview_profile_report.py`.
- Create `experiments/face_recognition_spike/tests/test_quality_profiles.py`.
- Create `experiments/face_recognition_spike/tests/test_preview_profile_comparison.py`.
- Create `experiments/face_recognition_spike/tests/test_preview_profile_report.py`.
- Modify `experiments/face_recognition_spike/face_spike/local_preview_quality.py` to add `compare`.
- Extend `experiments/face_recognition_spike/tests/test_local_preview_quality.py`.

- **Specification:** `Profile comparison`, the no-global-confidence rule, human decision boundary,
  and comparison acceptance criteria.
- **Depends on:** Task 1's `load_verified_preview_corpus`, existing `YuNetDetector`, production face
  quality evidence calculation, and the frozen `quality-sample-10pct-attempt-1/sample.json`.
- **Produces:** named immutable profiles combining detector thresholds `0.75`, `0.70`, and `0.65`
  with the current gate and bounded small/background candidate rules; a comparison bundle with
  canonical manifest hash, machine-readable per-photo/per-face evidence, self-contained source
  previews and crops, and `report.html`.

- [ ] Write failing tests proving every profile receives identical preview bytes, profile names and
  parameters are frozen in output, no profile applies a global confidence floor, changed decisions
  and detector misses/recoveries are classified, technical failures remain separate, and all HTML
  media links resolve inside the published bundle.
- [ ] Run
  `PYTHONPATH=src/worker:experiments/face_recognition_spike .venv/bin/pytest -q experiments/face_recognition_spike/tests/test_quality_profiles.py experiments/face_recognition_spike/tests/test_preview_profile_comparison.py experiments/face_recognition_spike/tests/test_preview_profile_report.py experiments/face_recognition_spike/tests/test_local_preview_quality.py`
  and confirm the new behavior fails before implementation.
- [ ] Implement one detector pass per threshold, production-compatible quality evidence, pure
  decision functions for the current and candidate profiles, immutable bundle staging/publication,
  and a self-contained report covering the seven explicit photo IDs plus every sampled changed
  decision.
- [ ] Run the targeted command again and require all tests to pass.

### Task 3: Materialize and verify the complete preview corpus

**Files:** private output outside Git only under
`<private-artifact-root>/event-corpora/cyclingrace-vechernee-sadovoe/previews/preview-small-v1/`.

- **Specification:** complete-corpus count, determinism, reuse, and no-S3 requirements.
- **Depends on:** approved Task 1 and the complete source cache at
  `<private-artifact-root>/event-corpora/cyclingrace-vechernee-sadovoe/`.
- **Produces:** a complete manifest with exactly 17,043 verified previews and zero unresolved items.

- [ ] Run the `materialize` command with an explicit bounded worker count against the source
  manifest and `originals/`; record elapsed time, preview count, byte count, manifest SHA-256, and
  zero unresolved failures.
- [ ] Run the identical command again and require `generated=0`, `reused=17043`, the same canonical
  manifest hash, and no changed preview hashes.
- [ ] Inspect the seven reported previews and verify that the long edge is at most 1600 and each
  manifest digest matches its file.

### Task 4: Run and publish profile evidence for human selection

**Files:** a new immutable private attempt directory outside Git under
`<private-artifact-root>/face-quality-benchmark/cyclingrace-vechernee-sadovoe/run-20260808T015921Z/`.

- **Specification:** seven-photo and deterministic 10% comparison, immutable evidence, and human
  selection boundary.
- **Depends on:** approved Tasks 1–3; fixed YuNet and SFace model paths already used by the existing
  benchmark; frozen sample
  `quality-sample-10pct-attempt-1/sample.json`.
- **Produces:** one verified comparison bundle and an operator-facing summary. It does not choose,
  replay, or activate a profile.

- [ ] Run `compare` with explicit preview-manifest, sample, seven photo IDs, model paths, and a new
  attempt directory; do not export database environment variables and do not access ports 5432,
  55432, or 55433.
- [ ] Validate the bundle and record its manifest hash, profile names, processed photo count,
  detector misses/recoveries, keep/reject totals, changed-decision totals, and technical failures.
- [ ] Open or directly inspect `report.html` and prove every referenced preview and crop exists.
- [ ] Present the report and concise per-profile evidence to the operator, then stop for the exact
  profile selection before writing a full-replay plan.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the specification, cited ADRs, and `docs/architecture.md`.
- [ ] Confirm `None — reversible implementation detail`: no deployed storage boundary, runtime
  topology, processing contract, or search authorization changed.
- [ ] Record that the original-backed local v3 attempt and activation were preserved and excluded
  from evidence.

## Verification

- `PYTHONPATH=src/worker:experiments/face_recognition_spike .venv/bin/pytest -q experiments/face_recognition_spike/tests/test_preview_corpus.py experiments/face_recognition_spike/tests/test_quality_profiles.py experiments/face_recognition_spike/tests/test_preview_profile_comparison.py experiments/face_recognition_spike/tests/test_preview_profile_report.py experiments/face_recognition_spike/tests/test_local_preview_quality.py` — all focused tests pass.
- `PYTHONPATH=src/worker:experiments/face_recognition_spike .venv/bin/pytest -q experiments/face_recognition_spike/tests` — the complete experiment suite passes.
- `.venv/bin/ruff check experiments/face_recognition_spike/face_spike experiments/face_recognition_spike/tests` — no lint errors.
- `git diff --check` — no whitespace errors.
- Private manifest verifier reports `complete=true`, `photos=17043`, `unresolved=0`; a second
  materialization reports full reuse with unchanged hashes.
- Private comparison verifier reports no missing media and zero technical failures before human
  review.

## Operational impact and rollout

No runtime, migration, deployment, or object-storage effect. Tasks 3 and 4 write only private local
artifacts. The isolated database is not used until a separately approved full-replay plan.

## Rollback

Tracked tooling is reverted normally. Private incomplete staging directories may be retained as
failure evidence; a completed corpus or report is deleted only after it is no longer needed. No
historical attempt, projection, activation, or benchmark artifact is mutated or removed.

## Open questions

None before Tasks 1–4. The exact winning profile is an intentional human decision after Task 4 and
is the entry condition for the follow-up replay plan.
