# Gated pgvector exact face search implementation plan

- Date: 2026-09-27
- Status: Implementation prepared; production rollout pending; numerical policy accepted under ADR 0041

Repository progress: Tasks 1–5 are implemented and independently reviewed, including the
native reader, controlled routing and explicit comparison. Task 6 supplies local snapshot/restore,
HTTP acceptance and rollout instructions; final review and exact-package verification are recorded
in the implementation handoff and pull request. Production schema/image deployment,
historical population, capacity acceptance and public `on` have not been performed.

Local acceptance reuses the existing 25 September database dump; it never downloads another copy.
The snapshot contains both supported models. Native ranking uses one scored/window stream to
avoid repeated best-face scans, and base/deployment PostgreSQL has a `256m` shared-memory ceiling
after two concurrent ordinary searches reproduced the default `64m` failure. This ceiling does not
reserve memory or resize the VM. Production concurrency targets still require separate acceptance.
- Owner: project maintainer
- Related specification: [Approved design](../superpowers/specs/2026-09-25-pgvector-exact-face-search-design.md)
- Related architecture: [Search](../architecture.md#search), [Deployment](../architecture.md#current-architecture--implemented)
- Related ADRs: [0040](../adr/0040-use-pgvector-for-exact-face-search.md), [0002](../adr/0002-postgresql-system-of-record.md), [0019](../adr/0019-use-public-event-selfie-search.md), [0024](../adr/0024-use-gallery-face-as-search-query.md), [0025](../adr/0025-expand-selfie-search-with-face-clusters.md), [0028](../adr/0028-operate-one-canonical-deployment.md), [0032](../adr/0032-reconcile-code-owned-feature-flags-at-startup.md)
- ADR impact: Conforms to accepted ADR 0040; its partial supersession of ADR 0019 is cross-linked
- Repository baseline inspected: `origin/main` at `7bfb0598dbfe81e715aeb608150d61b936a26e79`

For delegated implementation, use `$execute-implementation-plan` once for this plan. This document
does not authorize implementation, provisioning, public activation, or data deletion by itself.

## Implementation finding and accepted correction

A [synthetic numerical proof](../research/2026-09-27-pgvector-exact-arithmetic.md) shows that
native cosine ranking changes very borderline results. The maintainer accepts those changes in
[ADR 0041](../adr/0041-accept-pgvector-numerical-boundaries.md). Continue with native SQL-only
ranking, no Python refinement and no extra float64 representation. Comparison reports classify
changes within the existing `1e-6` numerical band; unexplained differences block activation.

## Goal

Deliver the [specified outcome](../superpowers/specs/2026-09-25-pgvector-exact-face-search-design.md#outcome)
through public pgvector read activation, retaining the complete legacy reader for rollback.

## Scope

Implement the specification's pgvector storage/read transition. Worker separation, recognition
model backfill, cluster-builder migration, and removal of old storage/reader/gate are later work.
Do not enroll new ML generations or change worker payloads, model thresholds, or stored results.

Start implementation from refreshed `origin/main`, using `make worktree NAME=pgvector-exact-search`.
Bring the approved specification, this plan, and ADR 0040 into that package. The planning checkout
is behind current main; preserve its existing NumPy/cache optimization and prepare new migrations
after the actual branch leaves, not after the older planning checkout's migrations.

## Acceptance criteria

Use the [specification criteria](../superpowers/specs/2026-09-25-pgvector-exact-face-search-design.md#acceptance-criteria).
Delivery additionally requires an empty missing/divergent eligible-row report, correct staff and
ordinary-user routing in the deployed application, a witnessed `off` rollback, and a retained
aggregate performance/parity report before the operator selects `on`.

## Worker/state/artifact release safeguards

- **Live-state inventory.** Task 2 supplies `inspect_pgvector_face_search`. Before the first
  database-image change, capture PostgreSQL version/image/volume identity, extension availability,
  event model counts, processing contract identities, job/attempt statuses and retry eligibility,
  active/expired leases, accepted projections and embeddings, queued searches and their frozen
  generations, cluster activations, and existing flag states. Retain counts and bounded metadata
  privately; do not print payloads, tokens, vectors, object keys, or credentials. Object Storage
  prefixes and artifact identities remain untouched.
- **Compatibility matrix.** Old Django/old worker/old rows continue normally before migration.
  Old Django on the expanded schema with a vector-capable database reads JSON normally. New Django
  with the current worker writes both stores and reads JSON in `off`; old rows default to
  non-staff/non-comparison search context. New reader plus unfilled historical rows refuses the
  incomplete cohort. Worker contracts and leases stay unchanged. A plain PostgreSQL image without
  pgvector is unsupported once vector schema exists, including application rollback.
- **Reviewed data-state migration or reset semantics.** Add schema only in migrations; backfill
  existing immutable embeddings with bounded explicit commands. Use detection uniqueness and
  idempotent insert/verify, never overwrite divergent rows. New publication writes JSON and vector
  atomically. Keep failed/stale attempts, old generations, projections, clusters, and results.
  No reset, purge, requeue, deletion, or SFace-to-AdaFace conversion occurs in this release.
- **End-to-end contract sizing.** Worker wire contracts are unchanged. Reuse current contract
  tests and add a maximum configured face-count/512D callback persistence check: serialization,
  HTTP request, Django validation, and atomic dual persistence must remain inside the existing
  `384 KiB` gallery callback and `16 KiB` selfie callback limits. Search review context belongs to
  `SelfieSearch`, not the worker configuration/result/hash.
- **Previous-snapshot upgrade rehearsal.** Task 6 restores a retained previous-version dump into
  an isolated local pgvector-capable PostgreSQL 16 database. Exercise successful/failed/retryable
  attempts, stale states, active/expired leases, queued/terminal searches, accepted previews,
  existing results/clusters, never-enrolled photos, and both recognition generations. Expected
  result: unchanged identities/states and old-reader behavior; only the new table/context fields
  are added and then explicitly filled. Restore the resulting vector-bearing dump a second time.
- **Staged activation and rollback order.** Backup and restore proof → compatible DB image →
  expanded schema and atomic parallel writes with `off` → bounded historical fill and reconciliation
  → staff review and parity/load evidence → operator `on`. Incomplete data, unexplained numerical differences,
  capacity regression, callback failures, or cleanup/publication regressions stop advancement.
  `off` restores old reading while parallel writes remain. Retain the vector-capable DB image on
  application rollback; schema expansion is not reversed during the reversible read transition.
- **Supported bounded operational commands.** Task 2 delivers read-only inventory/verification
  commands and `backfill_pgvector_face_embeddings --event-slug EVENT --batch-size 500 --max-rows 5000`.
  Default mode is dry-run; `--apply` explicitly writes. Hard bounds are batch size 1–1000 and
  maximum rows 1–50000 per invocation. Task 5 delivers an authorized bounded review command with
  at most 100 gallery source detections and 30 repetitions per source. No destructive command is
  introduced. Operational examples below run through the existing protected web-container context.

## Implementation

Backend path abbreviations below are relative to `src/backend/`: `processing/`, `selfie_search/`,
and `feature_flags/`. Other paths are relative to the repository root.

### Task 1: Vector-capable schema and atomic parallel publication

**Files:** modify `src/backend/requirements.txt`, `src/backend/processing/models.py`,
`src/backend/processing/services/jobs.py`, `docker-compose.yml`, `docker-compose.deployment.yml`,
`docker-compose.visual.yml`, `.github/workflows/ci.yml`, `deploy/apply-deployment.sh`,
`scripts/clone-deployed-db.sh`; inspect inherited local-purchase/AdaFace overlays and
`deploy/cutover-compose-identity.sh`. Create `processing/migrations/0011_pgvector_face_embeddings.py`,
`processing/services/vector_embeddings.py`, `processing/tests/test_vector_embeddings.py`, and
`tests/deployment/test_pgvector_database.py`. Migration numbering must follow the refreshed branch.

- **Specification:** Parallel vector representation; Availability and migration.
- **Depends on:** Accepted ADR and refreshed implementation worktree.
- **Produces:** `FaceEmbeddingVector`, keyed independently and unique by detection, with protected
  detection FK, model identity, variable-dimension `VectorField`, immutable vector, and retained
  non-vector metadata needed when legacy storage is later removed. `persist_parallel_embedding`
  is the single Django callback helper for validated legacy/new writes.

- [ ] Write RED tests for both dimensions, model/dimension mismatch, non-finite/zero/non-normalized
  input, rejected faces without vectors, uniqueness, immutable accepted evidence, and rollback of
  both writes when either fails. Cover both legacy and quality callback producers.
- [ ] Pin Python `pgvector==0.5.0` and `pgvector/pgvector:0.8.6-pg16-bookworm` throughout database
  server/restore targets. Preserve PostgreSQL major version, volume name, credentials, ports, and
  Compose identity. Verify/pin the published image digest in the implementation package.
- [ ] Add `VectorExtension` before the new table; add model/dimension and nonzero normalized-value
  constraints. Convert driver vectors explicitly at the application boundary; do not depend on
  NumPy scalar/list return behavior. Add no HNSW/IVFFlat index.
- [ ] Make both callback persistence producers call the helper inside their existing transaction.
  Extension/image preflight must precede candidate migrations: current deployment runs migrations
  before ordinary Compose reconciliation, so a Compose image edit alone is insufficient.
- [ ] Verify fresh database migration and an existing PG16 data volume start on the compatible
  image. Old app rollback must not restore a plain database image against vector-bearing data.
- [ ] GREEN: `make test TESTS="src/backend/processing/tests/test_vector_embeddings.py src/backend/processing/tests/test_jobs.py src/backend/processing/tests/test_models.py src/worker/tests/test_contracts.py"`.
  Run `make test-operational` for the image/order/restore contracts. Expected: complete parallel
  publication, unchanged worker protocol, and pre-migration extension availability.

### Task 2: Bounded population, inventory, and completeness verification

**Files:** create `processing/management/commands/inspect_pgvector_face_search.py`,
`backfill_pgvector_face_embeddings.py`, `verify_pgvector_face_embeddings.py`,
`processing/services/vector_reconciliation.py`, `processing/tests/test_vector_reconciliation.py`;
extend `processing/services/vector_embeddings.py` and `processing/services/face_cohort.py`.

- **Specification:** Parallel vector representation; Activation and rollback contract.
- **Depends on:** Task 1.
- **Produces:** Commands named in the safeguards; one reusable scalar eligible-cohort definition
  rooted in accepted projections, plus a same-snapshot missing/divergent-identity check for Task 3.

- [ ] RED tests: dry-run performs no writes; rerun is idempotent; conflict is verified rather than
  overwritten; bounds stop work; invalid eligible JSON blocks acceptance; new callback rows racing
  with a batch remain complete through detection uniqueness. Include hidden/current/stale/foreign
  projections and both model generations.
- [ ] Populate existing immutable face embeddings without inference. Commit batches independently;
  retain an explicit resume cursor in a private operator report, not application logs. Never rewrite
  old processing states or a vector that disagrees with its immutable source.
- [ ] Verification compares eligible identities by detection/model/generation and validates converted
  values. Emit aggregate missing/divergent/invalid counts by event/model; nonzero eligible gaps
  produce a nonzero exit. Inactive historical rows are accounted for separately.
- [ ] The inventory command must also run from the candidate image before schema expansion: it
  detects absent extension/table/fields and reports the old state without querying nonexistent
  columns. This supplies the pre-change inventory without requiring an earlier schema deployment.
- [ ] Keep existing cache fingerprints and projection eligibility intact. New SQL eligibility must
  not require a legacy embedding FK once the old store is removed in the later project.
- [ ] GREEN: `make test TESTS="src/backend/processing/tests/test_vector_reconciliation.py src/backend/processing/tests/test_vector_embeddings.py src/backend/processing/tests/test_face_cohort.py"`.
  Expected: empty eligible gaps after fill, safe reruns and preserved historical state.

### Task 3: Exact SQL reader with current result contracts

**Files:** create `selfie_search/services/vector_ranking.py`,
`selfie_search/services/direct_ranking.py`, `selfie_search/tests/test_vector_ranking.py`;
modify `processing/services/face_cohort.py`, `selfie_search/services/ranking.py` only as needed
to share frozen-configuration validation. Preserve `cohort_cache.py` and `rank_cached_embeddings`.

- **Specification:** Direct exact ranking; Shared cohort; Numerical parity.
- **Depends on:** Tasks 1–2.
- **Produces:** One `DirectRankingOutcome` containing ordered `RankedPhoto` rows, eligible face/photo
  counts, bounded timings, and scalar snapshot evidence. A legacy adapter uses the current cache;
  the vector adapter checks cohort completeness and calculates distance in PostgreSQL.

- [ ] RED tests for inclusive thresholds, self-match, multi-face best choice and exact ties,
  deterministic photo order, empty cohort, malformed query, wrong model/dimension, hidden photos,
  stale generation, missing vector, and foreign event. Assert no gallery-vector payload is selected
  into Django on a new-reader scan. No top-K or approximate candidate selection is permitted.
- [ ] Calculate pgvector cosine distances over the entire eligible cohort, choose a best detection
  per photo, and return all threshold matches in contract order. Keep the completeness check and
  scan in one consistent database snapshot outside search/job publication locks.
- [ ] Compare with exact legacy evidence for adversarial boundaries and representative float32-origin
  vectors. Classify very borderline threshold/tie/anchor changes within `1e-6` as accepted under ADR 0041.
  Unexpected eligibility or differences beyond that band block activation. Do not widen final
  thresholds, introduce ANN, recheck candidates in Python or add a legacy failure fallback.
- [ ] GREEN: `make test TESTS="src/backend/selfie_search/tests/test_vector_ranking.py src/backend/selfie_search/tests/test_ranking.py src/backend/selfie_search/tests/test_cohort_cache.py src/backend/processing/tests/test_face_cohort.py"`.
  Expected: identical non-boundary results, classified boundary differences and distance error at most `1e-6`.

### Task 4: Feature-selected reading for selfie and gallery sources

**Files:** modify `feature_flags/registry.py`, `selfie_search/models.py`,
`selfie_search/services/submission.py`, `selfie_search/services/jobs.py`, `selfie_search/views.py`,
`selfie_search/observability.py`; create `selfie_search/services/read_selection.py`,
`selfie_search/migrations/0006_reader_review_context.py`, `selfie_search/tests/test_read_selection.py`;
extend flag, submission, jobs, views, and observability tests.

- **Specification:** Read selection; Failure/publication semantics.
- **Depends on:** Task 3.
- **Produces:** Registered `PGVECTOR_FACE_SEARCH_READ` definition with key
  `pgvector-face-search-read`; a server-only staff-eligibility boolean on `SelfieSearch`, default
  false for old rows; a common reader selector and common outcome consumed by both completion paths.

- [ ] RED tests for missing/off/staff/on state, anonymous/ordinary/inactive-staff callers, old queued
  rows, staff submission followed by worker callback, and gate-off before ranking. Neither worker
  configuration hashes nor payloads carry staff/review context.
- [ ] Resolve the current flag before preparing ranking, using validated submission eligibility
  for asynchronous work. Preserve current snapshot/lease revalidation after ranking and before
  atomic publication; never move scans back inside long row-lock transactions.
- [ ] Read a gallery query vector through the selected representation and preserve crop/source
  eligibility. Ordinary gallery presentation and old results must remain functional in `off`.
- [ ] Preserve source-photo membership, direct-first cluster expansion, cleanup-before-terminal,
  no partial rows, idempotent callbacks, and ambiguous database failure semantics. Add bounded
  reader identity/timing telemetry without vector/face/token data; keep old cache metrics valid.
- [ ] GREEN: `make test TESTS="src/backend/selfie_search/tests/test_read_selection.py src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_jobs.py src/backend/selfie_search/tests/test_views.py src/backend/selfie_search/tests/test_cluster_expansion.py src/backend/selfie_search/tests/test_observability.py src/backend/feature_flags/tests"`.
  Expected: correct routing and unchanged publication/privacy behavior for both sources.

### Task 5: Explicit bounded side-by-side review and performance report

**Files:** create `selfie_search/services/reader_comparison.py`,
`selfie_search/management/commands/review_pgvector_face_search.py`,
`selfie_search/tests/test_reader_comparison.py`; extend submission/views/jobs/observability tests.

- **Specification:** Side-by-side proof; Acceptance criteria.
- **Depends on:** Tasks 3–4.
- **Produces:** Staff-only, CSRF-protected opt-in `compare_readers=1` on search submission, recorded
  separately from worker configuration; a private management review command using existing gallery
  sources and aggregate-only reports. Review never creates an extra bearer result.

- [ ] RED tests for forged non-staff opt-in, shared transient input and database snapshot, incomplete
  new cohort, comparison error isolation from selected-result publication, differing membership,
  selected detection, order and anchors, and absence of vectors/identifiers in retained diagnostics.
- [ ] Compare both reader outcomes before transient query release; publish only the selected result.
  Record mismatch counts, maximum distance delta and separate timings. Gallery review also checks
  the two source representations before supplying the same query to both readers.
- [ ] Benchmark warm cache, cold cache, alternating events, both dimensions and bounded concurrent
  search plus normal gallery/processing traffic. Include completeness-check cost, not only the
  distance operator. Keep full-photo-holdout quality artifacts outside Git if real selfies are used.
- [ ] GREEN: `make test TESTS="src/backend/selfie_search/tests/test_reader_comparison.py src/backend/selfie_search/tests/test_read_selection.py src/backend/selfie_search/tests/test_jobs.py src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_observability.py"`.
  Expected: zero unexplained differences, classified numerical boundary effects and one immutable result per accepted search.

### Task 6: Snapshot, restore, rollout and live acceptance

**Files:** create `processing/tests/test_vector_migrations.py`,
`tests/deployment/pgvector_acceptance/run.py`, `tests/deployment/test_pgvector_acceptance.py`,
`docs/runbooks/pgvector-face-search.md`; extend deployment and clone tests from Task 1.

- **Specification:** Availability/migration/rollback; Acceptance criteria.
- **Depends on:** Tasks 1–5.
- [ ] RED/GREEN the previous-snapshot rehearsal and compatible database/app rollback described in
  the seven safeguards. Prove vector-bearing dump restore and no mutation of old evidence/results.
- [ ] Run `make test-migrations` and `make test-operational`; run the maximum callback fixture
  through the isolated HTTP acceptance stack. Record exact commands, exits and package fingerprint.
- [ ] Write the runbook with the agent/operator split and the commands below. Take a fresh read-only
  inventory and verified backup before changing the deployed DB image. Do not reinitialize or
  replace the persistent volume. Apply expanded schema and verify the new flag arrived in `off`.
- [ ] Populate one bounded reviewed event, verify, then fill the reviewed remaining events in bounded
  invocations. Confirm ongoing accepted dual writes and that ordinary requests still use legacy.
- [ ] Operator selects `staff`; run both uploaded-selfie and gallery-source reviews. Establish and
  obtain maintainer acceptance of numeric latency/capacity targets from the fresh baseline before
  public activation. Report warm/cold/alternating p50/p95, CPU/RSS, DB wait/connection pressure,
  transferred bytes, gallery latency, processing queue age and errors. Do not promise a speedup.
- [ ] Witness `off` rollback, unchanged saved results and temporary-selfie cleanup. Return to `staff`
  for final acceptance, then operator selects `on` only with complete reconciliation, zero unexplained result
  differences, and accepted performance evidence. Monitor the same metrics after `on`; failures
  return the gate to `off` and leave both stores intact.

### Final task: Architecture and ADR reconciliation

**Files:** `docs/architecture.md`, `docs/engineering-jobs.md`, `docs/product-jobs.md`,
`docs/adr/README.md`, this plan, and the runbook.

- [ ] Compare delivered behavior with the approved specification and ADR 0040 after verification,
  before push. Record conformance; report implementation, deployment and public activation evidence
  separately. Keep pending/live claims accurate.
- [ ] Rerun final selector/fingerprint and missing exact-package suites. Update implemented facts
  only for delivered behavior; retain legacy cleanup and worker/model migration as future work.
- [ ] Use one final implementation commit after task/review loops under the project execution skill;
  CI repeats the locally verified package. Any parity-driven design change returns to the maintainer.

## Verification

Focused commands are listed under their tasks. Final package commands, from the implementation
worktree after all task and review fixes:

```sh
.venv/bin/python scripts/select_test_suites.py select --base origin/main --head HEAD
.venv/bin/python scripts/select_test_suites.py fingerprint --base origin/main
make check
make test-operational
make test-migrations
.venv/bin/python scripts/check_migration_immutability.py --base origin/main --head HEAD
git diff --check
```

Apply `.venv/bin/pre-commit run --files` to exact changed Python files before handoff. The selector
requires both revisions for committed packages. Before the final commit, pass every changed and
new task path using repeated `--changed-file` arguments instead; `--base` alone selects all suites
as a fail-closed default.
The selector
and CI job own any additional expensive/visual command; do not infer a visual exemption from the
absence of UI changes. See [Testing](../testing.md) and `$select-verification-suites` for evidence
reuse. Run immutability against the final commit/actual PR base and head, not an unstaged package.
GREEN means all selected layers cover the same final fingerprint; this plan itself is not test evidence.

## Operational impact and rollout

The agent prepares code, tests, restore rehearsal, deployment/runbook, bounded data fill and review
evidence. The operator chooses reviewed event scopes, accepts baseline-derived numerical targets,
and changes the gate through Admin. Deployment of the PG image/schema is scheduled explicitly;
it is a shared-database restart risk and is not hidden behind the read gate. The worker API and
Object Storage configuration remain unchanged.

Inside the protected existing web-container context, with `REVIEW_EVENT` selected from inventory:

```sh
python manage.py inspect_pgvector_face_search
python manage.py backfill_pgvector_face_embeddings --event-slug "$REVIEW_EVENT" --batch-size 500 --max-rows 5000
python manage.py backfill_pgvector_face_embeddings --event-slug "$REVIEW_EVENT" --batch-size 500 --max-rows 5000 --apply
python manage.py verify_pgvector_face_embeddings --event-slug "$REVIEW_EVENT"
python manage.py review_pgvector_face_search --event-slug "$REVIEW_EVENT" --max-sources 100 --repeats 10
```

These are interfaces to be delivered, not currently available commands. The fill ends when the
verified eligible missing count is zero, not merely when the command stops creating rows.
Extra disk/WAL and parallel-write/SQL CPU costs must be measured before broad fill and activation.

## Rollback

Set the read gate to `off`. Keep dual writes and the complete new table for inspection/retry;
saved results are not recomputed. If application rollback is needed, preserve the compatible
pgvector database image and expanded schema. Restoring a pre-change backup is a separately
reviewed data recovery operation because it can discard writes after the backup. Do not drop the
extension/table or replace the volume as routine rollback.

The later new-only AdaFace backfill must first close the legacy rollback boundary for affected
events and supply its own recovery plan. This release performs no legacy data/code deletion.

## Open questions

None for implementation. Public activation is gated by fresh inventory, complete data,
side-by-side parity, accepted baseline-derived numerical targets, and witnessed recovery. Worker
separation, model conversion, and legacy removal require their later specifications/plans.

## Dependency sources

- [pgvector supported PostgreSQL 16 image tags](https://github.com/pgvector/pgvector#docker)
- [Python package 0.5.0 and Django integration](https://pypi.org/project/pgvector/0.5.0/)
