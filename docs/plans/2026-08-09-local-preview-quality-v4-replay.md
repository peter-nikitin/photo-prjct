# Local Preview Quality V4 Replay Implementation Plan

- Date: 2026-08-09
- Status: Approved for execution
- Owner: project maintainer
- Related specification:
  [`2026-08-09-local-preview-quality-profile-design.md`](../superpowers/specs/2026-08-09-local-preview-quality-profile-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md), immutable processing
  attempts and preview-first processing
- Related ADRs: [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md)
- ADR impact: Conforms to ADR 0017; no new architecture decision

> Execute this plan with `$execute-implementation-plan` and its required subagent review loop.

## Goal

Replay the approved `0.75 + current-v3` quality decision as a new preview-backed processor version
4 over exactly 17,043 event-9 previews, then activate it only in the isolated local application.

## Scope

No production, staging, S3, ports 5432/55432, downloader application, or old-row mutation. Version
3 remains readable and selectable as immutable rollback history. All operational commands require
explicit `DB_PORT=55433` and the verified local preview corpus.

## Implementation

### Task 1: Versioned preview-backed quality generation

**Files:**

- Modify `src/backend/processing/services/enrollment.py`.
- Modify `src/backend/processing/services/face_quality.py`.
- Modify `src/backend/processing/services/jobs.py`.
- Modify the quality-specific branches in `src/backend/processing/views.py`.
- Modify `src/worker/photo_worker/contracts.py` and `src/worker/photo_worker/runner.py`.
- Modify focused backend and worker contract/runner/activation/enrollment tests.

- **Produces:** processor version 4 with the exact version-3 face configuration and strict
  preview-only input; version 3 remains a historical valid generation, while the current candidate
  and new enqueue path use version 4.

- [ ] Add failing tests for v4 preview claims/results/projections/enrollment, rejection of v4
  original input, distinct v3/v4 projection keys, current candidate identity, and historical v3
  activation resolution.
- [ ] Implement the smallest version-set changes without duplicating quality logic or adding a
  compatibility fallback.
- [ ] Run focused backend/worker suites and require all tests to pass.

### Task 2: Isolated local preview replay helper

**Files:** ignored `var/local_cache_face_v4_preview_replay.py` and its ignored operational report.

- **Produces:** explicit `status`, idempotent `enroll`, independent `work`, guarded `activate`, and
  local-server commands bound to event 9, port 55433, preview manifest hash, report hash, and model
  hashes.

- [ ] Copy only the useful lifecycle structure from the v3 helper; remove original-backed paths and
  geometry adaptation.
- [ ] Verify every local preview against manifest filename, size, dimensions, and SHA before
  enrollment; use the accepted database derivative fingerprint and the matching local preview
  bytes for work.
- [ ] Refuse activation unless version 4 has exactly 17,043 terminal successful accepted attempts
  and projections, zero active/retry/failed/stale/technical failures, and unchanged evidence/model
  hashes.

### Task 3: Full replay and local activation

- [ ] Inspect version-3 and version-4 counts before mutation and record them.
- [ ] Enroll exactly 17,043 version-4 jobs on `DB_PORT=55433`.
- [ ] Run four independent workers; restart only after confirming no active duplicate workers.
- [ ] Require exact terminal counts and preserve every failed attempt as evidence.
- [ ] Append a local-only version-4 activation, relaunch the ordinary local server, and verify the
  event page, gallery-face selection, and search use version 4.

## Verification

- Focused backend/worker tests from Task 1 pass.
- Complete experiment suite remains `402 passed, 1 skipped` or better.
- Version 4 reaches `jobs=attempts=accepted_attempts=projections=17043`; all non-success states are
  zero.
- Version-3 counts and projection attempt IDs remain unchanged.
- Local event URL responds successfully and resolves active generation version 4.

## Operational impact and rollback

Local database and local process only. Rollback appends a new activation selecting the preserved
version-3 generation; no job, attempt, projection, or activation is updated or deleted.

## Open questions

None.
