# Selfie Search Face-Cluster Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`
> task-by-task. Steps use checkbox syntax for tracking. Follow repository `AGENTS.md`: implementers
> and reviewers do not modify the Git index, commits, branches, tags, or remotes; the root controller
> creates one final commit per reviewed task.

- Date: 2026-08-05
- Status: Implemented
- Owner: project maintainer
- Related specification:
  [`2026-08-05-selfie-search-face-cluster-expansion-design.md`](../superpowers/specs/2026-08-05-selfie-search-face-cluster-expansion-design.md)
- Related product jobs:
  [`PJ-008 — Customer — Find photos by face`](../product-jobs.md#pj-008--customer--find-photos-by-face)
  and
  [`PJ-013 — Customer — Report selfie-search quality`](../product-jobs.md#pj-013--customer--report-selfie-search-quality)
- Related architecture:
  [`docs/architecture.md`](../architecture.md), accepted public selfie search, derived recognition
  data, PostgreSQL authority, and bounded observability
- Related ADRs:
  [ADR 0001](../adr/0001-django-modular-monolith.md),
  [ADR 0002](../adr/0002-postgresql-system-of-record.md),
  [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md),
  [ADR 0023](../adr/0023-store-consented-selfie-search-feedback.md), and
  [ADR 0025](../adr/0025-expand-selfie-search-with-face-clusters.md)
- ADR impact: Conforms to accepted ADR 0025 while preserving ADRs 0019 and 0023.

**Goal:** Implement disabled-by-default event-scoped face-cluster expansion that appends additional
unique photographs after unchanged direct selfie-search results, preserves immutable source
provenance, and measures its incremental value without activating uncalibrated thresholds.

**Architecture:** Django and PostgreSQL own immutable offline cluster corpora, explicit activation,
direct-first result snapshots, and source evidence. A NumPy-backed exact bounded-block kernel ports
the already measured guarded graph algorithm into production code; the existing worker remains
unchanged and the query vector remains transient inside Django completion. Structured events cover
the retained operational window while durable search/evidence rows support later aggregate reports.

**Tech stack:** Python 3.12, Django 6.0.6, PostgreSQL 16, NumPy 2.2.0, existing YuNet/SFace
embeddings, host journald, and the repository's private face-recognition experiment tooling.

## Global constraints

- Implement the approved specification without named identity, cross-event clustering, participant
  counting, contextual evidence, ANN/vector services, a broker, or customer-visible source badges.
- Preserve ADR 0019: direct results remain unchanged and first; the selfie is deleted before
  terminal publication; the query embedding is never persisted.
- Use exact bounded-block candidate-edge calculation, deterministic edge ordering, recomputed
  medoids, the representative-radius guard, singleton preservation, and an explicit candidate-edge
  limit. Do not use experimental threshold `0.363` as a production default.
- Cluster edge, representative, and strong-anchor thresholds are required versioned inputs selected
  by a private labelled benchmark; environment activation remains unavailable without an approved
  report hash and matching configuration hash.
- PostgreSQL stores no duplicate gallery vector in the cluster corpus. Published corpus membership
  and bearer result provenance are immutable.
- A result has primary source `direct` or `face_cluster_expansion`. A direct result also reached by
  a cluster remains direct-primary and retains both typed evidence sources.
- Historical source metrics remain null/`not_available`; do not fabricate zero expansion or
  structured events for searches created before this contract.
- Feedback stays customer-provided evaluation evidence. It never mutates clusters, thresholds,
  models, activation, or saved membership automatically.
- Logs and reports contain no photo, face, detection, cluster, selfie, vector, contact, filename,
  object, bearer, client, or per-person identity.
- Remove the obsolete `SelfieSearchCandidate` model and callback branch instead of implementing
  cluster expansion twice. Preserve existing bearer results through truthful direct-evidence data
  conversion, not a runtime compatibility layer.
- Subagents work sequentially in the shared worktree, use strict red-green TDD, do not spawn agents,
  and leave all changes unstaged. Root review gates and commits each task before the next begins.

## Scope

Implements the approved specification without scope changes. It includes corpus building,
publication, evaluation evidence, guarded activation, direct-first expansion, provenance,
source-separated feedback reporting, structured observability, and documentation. It does not
activate staging or production because numeric recall/precision gates have not yet been approved.

## Acceptance criteria

The specification's 20 acceptance criteria are authoritative. Delivery additionally requires
migrations from the current head, removal of the legacy candidate model/path, deterministic
fixtures for every new state and count identity, no migration drift, focused suites after every
task, and the complete `make check` release gate before branch review.

## Implementation

### Task 1: Port the deterministic guarded clustering kernel into production

**Files:**

- Modify: `src/backend/requirements.txt`
- Create: `src/backend/processing/services/face_clustering.py`
- Create: `src/backend/processing/tests/test_face_clustering.py`
- Reference without importing: `experiments/face_recognition_spike/face_spike/clustering.py`

**Specification:** Conservative anonymous face clusters; Evaluation and activation gate.

**Depends on:** Accepted ADR 0025.

**Produces:**

- Pinned backend dependency `numpy==2.2.0`, matching the existing worker dependency.
- Immutable `ClusterFace`, `ClusterMember`, and `BuiltFaceCluster` value types using UUID face
  identities and finite normalized vectors.
- `build_face_clusters(faces, *, edge_threshold, representative_threshold,
  distance_block_size, max_candidate_edges)` returning deterministic clusters with stable keys,
  medoids, member ordering, representative distances, and singleton preservation.
- Bounded `CandidateEdgeLimitExceeded` plus validation for duplicate identities, dimensions,
  normalization, thresholds, block size, and edge limit.

- [ ] Add focused tests first for stable repeated output, medoid ties, bridge rejection, singleton
  preservation, invalid vectors/configuration, bounded distance blocks, and candidate-edge overflow.
- [ ] Run `make test TESTS="src/backend/processing/tests/test_face_clustering.py"`; expected RED is
  the missing production module and interfaces.
- [ ] Implement the smallest production kernel independently of the experiment package; do not add
  artifact writers, image decoding, Peakshot, or an approximate neighbour implementation.
- [ ] Re-run the focused command; expected GREEN is all selected tests passing.
- [ ] Run `.venv/bin/mypy src/backend/processing/services/face_clustering.py`; expected exit zero.

### Task 2: Add immutable corpus persistence, publication, and frozen cohort loading

**Files:**

- Modify: `src/backend/processing/models.py`
- Create: `src/backend/processing/migrations/0006_face_cluster_corpus.py`
- Create: `src/backend/processing/services/face_cohort.py`
- Create: `src/backend/processing/services/face_cluster_corpora.py`
- Create: `src/backend/processing/management/commands/build_face_cluster_corpus.py`
- Create: `src/backend/processing/tests/test_face_cluster_models.py`
- Create: `src/backend/processing/tests/test_face_cluster_corpora.py`
- Create: `src/backend/processing/tests/test_face_cluster_commands.py`
- Modify: `src/backend/selfie_search/services/submission.py`
- Modify: `src/backend/selfie_search/tests/test_submission.py`

**Specification:** Versioned offline corpus lifecycle; Conservative anonymous face clusters;
Privacy and authorization boundaries; Failure semantics.

**Depends on:** Task 1 value types and builder.

**Produces:**

- `FaceClusterCorpus` with event, bounded version, `building|failed|published` status, immutable
  algorithm/input configuration and hash, processor/model/dimension identity, required thresholds
  and limits, input/cluster/member/singleton/resource counts, and publication timestamps.
- `FaceCluster`, `FaceClusterMember`, and `EventFaceClusterActivation` with protected event/corpus/
  detection relationships, unique membership, representative distance, activation configuration,
  approved evaluation-report hash, and one active selection per event.
- `CompatibleFaceEmbedding` plus
  `load_compatible_face_embeddings(event, generations, dimensions)` as the one shared eligibility
  implementation consumed by both direct search and corpus building.
- `build_face_cluster_corpus(...)` that freezes eligible inputs, writes a complete unpublished run,
  validates cross-table/count identities, and atomically publishes it; any failure leaves it
  non-selectable.
- `build_face_cluster_corpus` command with explicit event/version/edge threshold/representative
  threshold/block/edge-limit arguments and UUID-only success output. No threshold defaults.

- [ ] Add model, migration, shared-cohort, service, and command tests first for event/generation
  isolation, exact frozen membership including singletons, uniqueness, immutable published rows,
  atomic publication, count/hash reproducibility, edge-limit failure, and sanitized output.
- [ ] Run `make test TESTS="src/backend/processing/tests/test_face_cluster_models.py src/backend/processing/tests/test_face_cluster_corpora.py src/backend/processing/tests/test_face_cluster_commands.py src/backend/selfie_search/tests/test_submission.py"`; expected RED is missing schema/services and the duplicated current cohort loader.
- [ ] Implement the schema and services. Map the shared processing values to existing
  `CandidateEmbedding` values at the selfie-search boundary; processing must not import
  `selfie_search`.
- [ ] Re-run the focused command; expected GREEN is all selected tests passing.
- [ ] Run `make test TESTS="src/backend/processing/tests src/backend/selfie_search/tests/test_submission.py"`; expected GREEN is the processing suite plus direct-cohort regression passing.
- [ ] Run `.venv/bin/python src/backend/manage.py makemigrations --check --dry-run`; expected
  `No changes detected`.

### Task 3: Introduce immutable result provenance and retire direct-only result columns

**Files:**

- Modify: `src/backend/selfie_search/models.py`
- Create: `src/backend/selfie_search/migrations/0003_result_provenance_and_clusters.py`
- Modify: `src/backend/selfie_search/tests/test_models.py`
- Modify: `src/backend/selfie_search/tests/test_migrations.py`
- Modify: `src/backend/selfie_search/admin.py`

**Specification:** Immutable result provenance; Feedback and quality interpretation; Durable
retrospective report.

**Depends on:** Task 2 corpus models.

**Produces:**

- Nullable historical snapshot fields on `SelfieSearch`: selected corpus/configuration, direct,
  expanded, final, strong-anchor, and selected-cluster counts plus bounded expansion outcome.
- `SelfieSearchResult.primary_source` with only `direct` and `face_cluster_expansion`.
- `SelfieSearchDirectEvidence` owning the matched detection and finite query cosine distance.
- `SelfieSearchClusterEvidence` owning corpus/cluster, strong-anchor result/detection, expanded
  member detection, representative distance, and deterministic source ordering.
- A schema/data transition that converts every existing result's truthful detection/distance into
  direct evidence, then removes obsolete `SelfieSearchResult.detection` and `cosine_distance`
  columns. Historical search-level expansion fields remain null.
- Database constraints for result uniqueness, source enums, evidence uniqueness, finite/bounded
  distances where representable, and protected relationships; explicit model/service validation for
  same-search, event, corpus, anchor, and member invariants.

- [ ] Add model and migration tests first for historical conversion, null historical metrics,
  direct/expanded/dual evidence, immutable terminal rows, cross-search/event/corpus rejection,
  duplicate evidence, and absence of sentinel detections/distances.
- [ ] Run `make test TESTS="src/backend/selfie_search/tests/test_models.py src/backend/selfie_search/tests/test_migrations.py"`; expected RED is the missing provenance schema.
- [ ] Implement the migration and minimal model/admin changes. Admin exposure remains read-only and
  does not display sensitive customer or biometric identifiers in list/search/export surfaces.
- [ ] Re-run the focused command; expected GREEN is all selected tests passing.
- [ ] Run `.venv/bin/python src/backend/manage.py makemigrations --check --dry-run`; expected
  `No changes detected`.

### Task 4: Integrate strong-anchor expansion into accepted search completion

**Files:**

- Modify: `src/backend/config/settings.py`
- Modify: `src/backend/selfie_search/apps.py`
- Modify: `src/backend/selfie_search/models.py`
- Modify: `src/backend/selfie_search/services/ranking.py`
- Create: `src/backend/selfie_search/services/cluster_expansion.py`
- Modify: `src/backend/selfie_search/services/jobs.py`
- Modify: `src/backend/selfie_search/services/results.py`
- Modify: `src/backend/selfie_search/tests/test_settings.py`
- Modify: `src/backend/selfie_search/tests/test_ranking.py`
- Create: `src/backend/selfie_search/tests/test_cluster_expansion.py`
- Modify: `src/backend/selfie_search/tests/test_jobs.py`
- Modify: `src/backend/selfie_search/tests/test_results.py`
- Modify: `src/backend/selfie_search/tests/test_views.py`
- Create: `src/backend/selfie_search/migrations/0004_remove_selfie_search_candidate.py`

**Specification:** Strong direct anchors; Expanded membership and deterministic ordering; Data
flow; Failure semantics; Privacy and authorization boundaries.

**Depends on:** Tasks 2 and 3.

**Produces:**

- Disabled default `SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED=False` with fail-closed static checks
  requiring ordinary selfie search. Missing or invalid per-event activation remains a runtime
  direct-only outcome and is never queried by startup system checks.
- `expand_ranked_photos(search, direct, query, activation)` returning immutable direct-first unique
  result/evidence values, count snapshot, corpus/configuration identity, duration input boundary,
  and one exact bounded outcome.
- Strict anchors only at or below the activation's required anchor threshold; singleton, repeated
  anchor, group-photo, multi-cluster duplicate, and dual-evidence behavior from the specification.
- Transactional preparation of result membership, both evidence types, source counts, corpus
  identity, and intended terminal state before cleanup; terminal publication verifies rather than
  derives the source-count identity.
- Direct-only fallback for disabled, unavailable, incompatible, or integrity-failed optional corpus
  state without publishing partial expanded evidence.
- Removal of `SelfieSearchCandidate`, its migration state, `rank_search()`, the dormant persisted-
  candidate branch in `complete_search_attempt()`, and candidate-only tests/imports.

- [ ] Add strict red tests first for unchanged direct output, strong versus ordinary anchors,
  deterministic appended ordering, every deduplication/dual-source case, exact count identity,
  optional failure outcomes, corpus incompatibility, and no partial persistence.
- [ ] Run `make test TESTS="src/backend/selfie_search/tests/test_ranking.py src/backend/selfie_search/tests/test_cluster_expansion.py src/backend/selfie_search/tests/test_jobs.py src/backend/selfie_search/tests/test_results.py src/backend/selfie_search/tests/test_views.py"`; expected RED is missing expansion integration and obsolete candidate behavior still present.
- [ ] Implement the minimal expansion service and accepted-callback integration. Keep the transient
  query inside the existing completion operation and never pass cluster data to the worker.
- [ ] Remove the candidate model/path and update migration/tests in the same task so no supported
  runtime has two ranking implementations.
- [ ] Re-run the focused command; expected GREEN is all selected tests passing.
- [ ] Run `make test TESTS="src/backend/selfie_search/tests"`; expected GREEN is the complete app
  suite with cleanup, pagination, feedback, bearer authorization, and media behavior unchanged.

### Task 5: Advance structured events and daily expansion summaries

**Files:**

- Modify: `src/backend/selfie_search/observability.py`
- Modify: `src/backend/selfie_search/services/jobs.py`
- Modify: `src/backend/selfie_search/tests/test_observability.py`
- Modify: `src/backend/selfie_search/tests/test_jobs.py`
- Modify: `deploy/selfie-observability/summarize.py`
- Modify: `tests/deployment/test_selfie_observability_summary.py`
- Modify: `tests/deployment/test_selfie_observability_units.py`
- Modify: `docs/runbooks/selfie-search-log-analysis.md`
- Modify: `README.md`

**Specification:** Per-search structured event; Terminal event; Daily operational summary;
Observability failure semantics.

**Depends on:** Task 4 durable counts and outcomes.

**Produces:**

- Per-event schema versions: submission/probe and worker events remain version 1; ranking and
  terminal events advance to version 2. Parsers validate `(event, schema_version)` explicitly.
- Strict ranking-v2 and terminal-v2 constructors containing only the approved bounded expansion
  fields and enforcing `final = direct + expanded`, outcome/count relationships, and non-ready
  zero-source counts.
- Daily expansion aggregates for eligible/helped searches, direct/expanded/final totals, p50/p95
  added photos and expansion time, anchor/cluster totals, outcomes, version/hash counts, and exact
  numerator/denominator rate fields.
- Ranking-to-terminal reconciliation; mismatches, invalid identities, unknown versions, duplicates,
  or missing counterparts make `complete=false`. Historical v1 ranking/terminal metrics remain
  `not_available`, never zero.
- Updated privacy-safe runbook allowlist and interpretation. Journald retention, timer, Compose
  tags, probe, and cloud topology remain unchanged.

- [ ] Add failing producer and deterministic JSONL parser fixtures first, including expanded,
  no-anchor, no-new, disabled, incompatible, v1 historical, mismatch, duplicate, malformed, and
  prohibited-sentinel cases.
- [ ] Run `make test TESTS="src/backend/selfie_search/tests/test_observability.py src/backend/selfie_search/tests/test_jobs.py tests/deployment/test_selfie_observability_summary.py tests/deployment/test_selfie_observability_units.py"`; expected RED is missing v2 event fields and summary aggregates.
- [ ] Implement producer validation, on-commit terminal emission, parser aggregation, integrity
  reconciliation, and bounded documentation changes without changing deployment infrastructure.
- [ ] Re-run the focused command; expected GREEN is all selected tests passing.
- [ ] Run `sh -n deploy/selfie-observability/run-daily-summary.sh deploy/verify-selfie-observability.sh deploy/selfie-observability/root-helper.sh`; expected exit zero.

### Task 6: Add source-separated feedback and durable retrospective reporting

**Files:**

- Modify: `src/backend/selfie_search/services/feedback.py`
- Create: `src/backend/selfie_search/services/cluster_reporting.py`
- Create: `src/backend/selfie_search/management/commands/report_face_cluster_expansion.py`
- Create: `src/backend/selfie_search/tests/test_cluster_reporting.py`
- Create: `src/backend/selfie_search/tests/test_report_face_cluster_expansion_command.py`
- Modify: `src/backend/selfie_search/tests/test_feedback_submission.py`

**Specification:** Feedback and quality interpretation; Durable retrospective report; Privacy and
authorization boundaries.

**Depends on:** Tasks 3 through 5 provenance and counts.

**Produces:**

- `build_cluster_expansion_report(*, start, end, event=None)` returning aggregate-only direct,
  expanded, and dual-evidence result volume; explicit-label counts and coverage; source-separated
  `Я есть`/`Меня нет`; and `labelled_sample_precision` with integer numerators/denominators. Date
  bounds use closed-open Europe/Moscow calendar windows.
- `report_face_cluster_expansion --start YYYY-MM-DD --end YYYY-MM-DD [--event <event-id>]` producing
  one bounded JSON object without individual identifiers or sensitive fields.
- Historical rows reported as `not_available`; unmarked photos remain unknown; event filtering does
  not expose event identity in output.
- Regression evidence that feedback submission accepts only server-derived immutable result
  membership and never accepts or mutates provenance from customer input.

- [ ] Add failing aggregate/service/command tests first for complete and partial labels, direct,
  expanded, dual evidence, zero labels, historical rows, date/event bounds, invalid arguments, and
  sentinel privacy denial.
- [ ] Run `make test TESTS="src/backend/selfie_search/tests/test_cluster_reporting.py src/backend/selfie_search/tests/test_report_face_cluster_expansion_command.py src/backend/selfie_search/tests/test_feedback_submission.py"`; expected RED is the missing report service/command.
- [ ] Implement bounded ORM aggregates and command serialization; do not read feedback contact,
  feedback selfie metadata, raw result identities, or logs.
- [ ] Re-run the focused command; expected GREEN is all selected tests passing.

### Task 7: Extend the closed benchmark and guard corpus activation

**Files:**

- Create: `experiments/face_recognition_spike/face_spike/cluster_expansion.py`
- Modify: `experiments/face_recognition_spike/face_spike/cli.py`
- Create: `experiments/face_recognition_spike/tests/test_cluster_expansion.py`
- Modify: `experiments/face_recognition_spike/tests/test_selfie_search_cli.py`
- Create: `src/backend/processing/management/commands/activate_face_cluster_corpus.py`
- Modify: `src/backend/processing/services/face_cluster_corpora.py`
- Modify: `src/backend/processing/tests/test_face_cluster_corpora.py`
- Modify: `src/backend/processing/tests/test_face_cluster_commands.py`
- Modify: `experiments/face_recognition_spike/README.md`

**Specification:** Evaluation and activation gate; Versioned offline corpus lifecycle; Contextual
evidence extension boundary.

**Depends on:** Tasks 1, 2, and 4 exact production semantics.

**Produces:**

- An immutable `evaluate-cluster-expansion` experiment command that applies the same direct-first,
  strict-anchor, cluster-expansion, unique-photo, and full-photo-holdout rules to the existing final
  person-split benchmark.
- Aggregate calibration/evaluation JSON containing configuration hash, direct/final recall,
  source-separated precision, incremental correct/incorrect photos, searches helped/harmed, false
  merges, fragmentation/singletons, and measured phase/resource fields; no production query data is
  committed.
- `activate_face_cluster_corpus` requiring a published compatible corpus, matching configuration
  hash, explicit calibrated anchor threshold, lowercase SHA-256 evaluation-report identity, and
  explicit operator confirmation that numeric gates were reviewed. It atomically replaces only the
  event's activation pointer.
- No numeric threshold defaults, no activation as a side effect of build/evaluation, and no
  contextual-factor implementation.

- [ ] Add failing experiment and Django command tests first for calibration/evaluation separation,
  holdout, exact direct-first results, metric identities, deterministic report hash, mismatched
  configuration/report denial, unpublished corpus denial, and explicit activation confirmation.
- [ ] Run `make test TESTS="experiments/face_recognition_spike/tests/test_cluster_expansion.py experiments/face_recognition_spike/tests/test_selfie_search_cli.py src/backend/processing/tests/test_face_cluster_corpora.py src/backend/processing/tests/test_face_cluster_commands.py"`; expected RED is the missing evaluator and guarded activation command.
- [ ] Implement the evaluator by reusing existing immutable benchmark/index/cluster artifact readers
  and the production ranking rules without copying private artifacts into Git.
- [ ] Implement the activation service/command as the only mutable corpus-selection path.
- [ ] Re-run the focused command; expected GREEN is all selected tests passing.
- [ ] Run `make test TESTS="experiments/face_recognition_spike/tests src/backend/processing/tests"`; expected GREEN is both complete component suites passing.

### Task 8: Reconcile delivered architecture and run the release gate

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/product-jobs.md`
- Modify: `docs/engineering-jobs.md`
- Modify: `docs/runbooks/selfie-search-log-analysis.md` if final command names changed
- Modify: `docs/plans/2026-08-05-selfie-search-face-cluster-expansion.md`
- Modify only if required by delivered configuration: `docker-compose.prod.yml`
- Modify only if required by delivered verification: deployment contract tests under `tests/deployment/`

**Specification:** Entire approved design, especially Scope, Evaluation and activation gate,
Acceptance criteria, and Rejected alternatives.

**Depends on:** Tasks 1 through 7 reviewed and committed.

**Produces:**

- Architecture and job evidence that distinguish implemented disabled-by-default capability from
  unapproved environment activation and measured customer outcomes.
- Final operator commands for build, private benchmark, report, guarded activation, direct-only
  rollback, and observability verification without secrets or raw biometric artifacts.
- No worker credential/configuration expansion and no environment activation in this branch.
- One recorded reconciliation outcome: implementation conforms to accepted ADR 0025 and preserves
  ADRs 0019/0023; any discovered contradiction blocks delivery instead of editing accepted ADRs.

- [x] Compare every delivered interface and behavior with all 20 specification acceptance criteria;
  the evidence matrix and reconciliation outcome are recorded in
  [`task-8-report.md`](../../.superpowers/sdd/2026-08-05-selfie-search-face-cluster-expansion/task-8-report.md).
  Documentation facts were changed only for interfaces present in the Task 1–7 implementation.
- [x] Run `PYTHONPATH=experiments/face_recognition_spike:experiments/face_recognition_spike/tests:src/backend make test TESTS="src/backend/processing/tests src/backend/selfie_search/tests tests/deployment experiments/face_recognition_spike/tests"`; final GREEN: 1043 passed, 3 skipped.
- [x] Run `npm run test:js`; final post-merge GREEN: 85 passed.
- [x] Run `make check`; final GREEN: Ruff format/check, mypy over 175 files, 1330 Python tests passed with 3 skipped and 82.37% coverage, Django checks, and migration drift.
- [x] Run `git diff --check` and verify no private benchmark media, vectors, crops, labels, absolute
  external paths, `.env`, or generated reports are tracked.
- [x] Update implemented architecture/jobs and mark this plan `Implemented` only when the complete
  release gate and independent whole-branch review pass. Keep environment activation explicitly
  blocked pending an approved real benchmark configuration and report.

### Task 8 documentation evidence — 2026-08-05

- The repository exposes `build_face_cluster_corpus`, `evaluate-cluster-expansion`,
  `report_face_cluster_expansion`, and `activate_face_cluster_corpus`; command-help verification
  was run in the task worktree. The operator workflow is recorded in the
  [selfie-search log-analysis runbook](../runbooks/selfie-search-log-analysis.md) with placeholders
  only, no secrets or private benchmark paths.
- `docs/architecture.md`, `docs/product-jobs.md`, and `docs/engineering-jobs.md` now distinguish
  the implemented disabled-default capability from environment activation and customer outcomes.
  No `docker-compose.prod.yml`, worker, deployment, credential, cloud, or accepted ADR change was
  required.
- The root release gate passed after merging current `origin/main`: 1043 affected/deployment/
  experiment tests passed with 3 skipped; JavaScript passed 85 tests; `make check` passed 1330 tests
  with 3 skipped at 82.37% coverage plus Ruff, mypy, Django checks, and migration drift; the final
  production Docker image built; diff/artifact scanning found no private biometric artifacts or
  secrets; and independent whole-branch review approved the final fixes with no findings.

## Verification

Run task-focused commands exactly as listed after each task. Before final review run:

```bash
PYTHONPATH=experiments/face_recognition_spike:experiments/face_recognition_spike/tests:src/backend make test TESTS="src/backend/processing/tests src/backend/selfie_search/tests tests/deployment experiments/face_recognition_spike/tests"
npm run test:js
make check
docker build -f Dockerfile --tag photo-prjct-web:face-cluster-test .
git diff --check
```

Expected outcomes: all selected tests pass; the full quality/coverage/migration gate exits zero;
the production Django image builds with the pinned NumPy dependency; there is no migration drift;
direct-only search remains the repository default; and Git contains no private or generated
biometric artifacts.

## Operational impact and rollout

The branch adds PostgreSQL schema migrations, NumPy to the Django image, offline corpus/benchmark/
report commands, optional search expansion, and expanded local observability. Normal deployment
applies migrations while the new environment gate remains false. The worker image, Object Storage,
IAM, networking, VM resources, journald retention, and timer do not change.

After merge, a separate reviewed rollout may build one event corpus and run the private held-out
benchmark. Activation requires explicit approval of numeric recall/precision gates, the exact corpus
configuration hash and evaluation-report hash, resource measurements, and the normal staging
deployment/health/observability checks. This implementation plan does not authorize that activation
or any pricing, access, availability, or cloud mutation.

## Rollback

Disable `SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED` in the reviewed environment configuration; all
new searches then use direct-only ranking even when an event pointer exists. The delivered guarded
activation command can replace an event pointer only with a newly reviewed compatible corpus; this
branch does not run activation. Do not delete published corpus, result provenance, feedback, or
historical expanded results: they are immutable evidence and existing bearer snapshots remain
readable. Reverting application code requires a database-compatible rollback review because the plan
removes obsolete result columns and the candidate model; do not reverse migrations destructively
against real result data.

## Architecture and ADR reconciliation

- Accepted ADR 0025 is the authoritative cluster-expansion decision.
- ADR 0019 remains authoritative for query processing, cleanup, event isolation, bearer access, and
  probable-match semantics.
- ADR 0023 remains authoritative for consented feedback and prohibits automatic feedback tuning.
- Task 8 evidence confirms the repository implementation conforms to ADR 0025 and preserves ADRs
  0019/0023: direct-first immutable snapshots, transient query processing and cleanup-before-
  publication remain unchanged; feedback is evaluation evidence only. The capability is implemented
  but disabled by default, and no environment activation or customer outcome is claimed.
- Task 8 release evidence is complete and independently reviewed, so the repository capability is
  `Implemented`. Environment activation and customer outcome validation remain separate blocked
  rollout work.
- A contradiction requires a new proposed/superseding ADR and explicit maintainer acceptance before
  delivery.

## Open questions

None. Numeric quality thresholds and activation gates are deliberately evidence outputs, not
implementation choices; environment activation remains blocked until they receive explicit later
approval.
