# Selfie Search In-Memory Ranking Cache Implementation Plan

- Date: 2026-09-14
- Status: Approved for implementation
- Owner: project maintainer
- Related specification:
  [Selfie search in-memory ranking cache](../superpowers/specs/2026-09-14-selfie-search-in-memory-ranking-cache-design.md)
- Related architecture: [Search and photo processing](../architecture.md)
- Related ADRs:
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md),
  [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md), and
  [ADR 0028](../adr/0028-operate-one-canonical-deployment.md)
- ADR impact: Conforms to ADRs 0019, 0017, and 0028. The change is a reversible
  implementation detail inside Django exact ranking and the canonical deployment; no ADR is
  added or superseded.

## Goal

Implement the approved specification's warm-path exact-ranking optimization and canonical
Gunicorn process-lifetime change.

## Scope

Implement the approved specification without scope changes.

## Acceptance criteria

The approved specification's [acceptance criteria](../superpowers/specs/2026-09-14-selfie-search-in-memory-ranking-cache-design.md#acceptance-criteria)
are authoritative. Delivery additionally requires selector-chosen final-package suites, `make
check`, exact architecture reconciliation, and one immutable-image canonical rollout with bounded
live verification before any success claim.

## Worker/state/artifact release safeguards

- [x] **Live-state inventory.** The read-only 2026-09-14 inventory recorded one canonical 16-vCPU,
  16-GiB VM, separate bulk and selfie workers from the same image, a live 45,239-face/21,619-photo
  AdaFace cohort, `GUNICORN_MAX_REQUESTS=1000`, repeated web-worker recycling, concurrent bulk
  queue progress, and no approved queue, feature-state, storage, or database mutation in this
  release.
- [x] **Compatibility matrix.** Django/worker callback contracts, processor identities, schema,
  leases, rows, result representation, and Object Storage artifacts are unchanged. New Django
  accepts existing worker callbacks and reads all existing searches/results. A prior-image rollback
  reads rows written by the new image because no durable representation changes.
- [x] **Reviewed data-state migration or reset semantics.** Compatible drain applies. There is no
  migration, reset, purge, backfill, replay, requeue, accepted-projection change, result rewrite,
  or Object Storage mutation. Ephemeral process memory disappears on replacement or rollback.
- [x] **End-to-end contract sizing.** The maximum required path is one normalized 512-value AdaFace
  query against 45,239 faces and 21,619 photos. Task verification covers callback validation,
  authoritative identity comparison, an entry below 256 MiB, conservative shortlist, exact
  distances/order, immutable persistence, and absence of query-vector persistence.
- [x] **Previous-snapshot upgrade rehearsal.** With no schema or durable-contract change, the
  existing migration suite plus focused tests over prior successful, failed, stale, expired,
  retryable, and terminal searches are the rehearsal. No snapshot transformation is permitted.
- [x] **Staged activation and rollback order.** Build and verify one immutable image; deploy it
  through `deploy.yml`; verify exact SHA, Gunicorn settings, container health/restarts, public
  health, DB waiters, bulk progress, one cold search, and one warm search. Stop on result mismatch,
  warm ranking path at or above five seconds, cold path at or above 60 seconds, memory above the
  accepted bound, restarts, lock accumulation, cleanup failure, or bulk starvation. Roll back to
  the prior successful image without touching durable rows or artifacts.
- [x] **Supported bounded operational commands.** Use read-only Django aggregates, structured
  timing logs, `pg_stat_activity`, `docker inspect`, `docker stats`, public HTTPS checks, and the
  deployment workflow with an exact SHA. This plan authorizes no manual SQL/ORM update, queue
  operation, feature change, object deletion, or container-local patch.

Rationale: [2026-07-31 staging processing-state reset postmortem](../postmortems/2026-07-31-staging-processing-state-reset.md).

## Implementation

Execute this plan through `$execute-implementation-plan`.

### Task 1: Authoritative cohort identity and bounded process cache

**Files:** create `src/backend/selfie_search/services/cohort_cache.py`; modify
`src/backend/processing/services/face_cohort.py`,
`src/backend/processing/tests/test_face_cohort.py`, and focused cache/cohort tests under
`src/backend/selfie_search/tests/`.

- **Specification:** Authoritative cohort identity; Cache lifetime and bounds; Concurrency and
  failure semantics.
- **Depends on:** None.
- **Produces:** A shared lightweight identity projection and a one-entry thread-safe process cache
  that returns an immutable validated matrix/metadata snapshot without changing cohort membership.

- [ ] Add focused failing tests proving identity reads exclude vectors, exact identity equality
  hits, same-count membership changes miss, only one concurrent build publishes, invalid rows fail
  closed, and replacement retains only one entry.
- [ ] Run the focused tests and record RED failures caused by the absent identity/cache interfaces.
- [ ] Implement the smallest shared identity projection and cache builder satisfying the approved
  key, fingerprint, validation, concurrency, and memory boundaries.
- [ ] Run `make test TESTS="src/backend/processing/tests/test_face_cohort.py src/backend/selfie_search/tests/test_cohort_cache.py"` and record GREEN after the last task-file change.

### Task 2: Conservative vectorized shortlist and exact ranking integration

**Files:** modify `src/backend/selfie_search/services/ranking.py`,
`src/backend/selfie_search/services/submission.py`, `src/backend/selfie_search/services/jobs.py`,
`src/backend/selfie_search/observability.py`, focused tests in
`src/backend/selfie_search/tests/test_ranking.py`, `test_submission.py`, `test_jobs.py`, and
`test_observability.py`; modify the bounded daily summary only if its typed event contract requires
the new fields.

- **Specification:** Exact ranking contract; Data flow; Observability; Privacy boundary and all
  ranking-related acceptance criteria.
- **Depends on:** Task 1 cached cohort interface.
- **Produces:** Both query sources use the cache, NumPy selects with the `1e-10` margin, exact
  `math.fsum` alone determines persisted output, and bounded logs expose hit/miss and phase timing.

- [ ] Add focused failing tests for SFace/AdaFace baseline equality, equal scores, threshold-edge
  rows, invalid vectors, warm no-vector-read behavior, uploaded-selfie and gallery-photo integration,
  and redacted bounded observability.
- [ ] Run the focused tests and record RED failures caused by the absent vectorized/cache path.
- [ ] Implement the minimal integration, retaining the current lock, lease, cleanup, immutable
  result, cluster-expansion, and privacy behavior.
- [ ] Run `make test TESTS="src/backend/selfie_search/tests/test_ranking.py src/backend/selfie_search/tests/test_cohort_cache.py src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_jobs.py src/backend/selfie_search/tests/test_observability.py tests/processing/test_selfie_search_e2e.py"` and record GREEN after the last task-file change.

### Task 3: Preserve warmed processes and reconcile deployment contracts

**Files:** modify `.env.example`, `.github/workflows/deploy.yml`, affected exact-value assertions
under `tests/deployment/`, `docs/architecture.md`, and `docs/engineering-jobs.md` if its implemented
operational evidence changes.

- **Specification:** Gunicorn process lifetime; Operational acceptance criteria; Rejected dedicated
  service boundary.
- **Depends on:** Tasks 1 and 2.
- **Produces:** Canonical deployment projects zero request-count recycling, tests enforce that
  value, and current architecture records the implemented ephemeral cache without claiming rollout.

- [ ] Add or update deployment contract assertions first and record the expected RED against the
  current `1000`/`100` values.
- [ ] Set `GUNICORN_MAX_REQUESTS=0` and `GUNICORN_MAX_REQUESTS_JITTER=0` in supported local and
  canonical projections without adding a new configuration path.
- [ ] Update current architecture facts only for implemented repository behavior; retain rollout
  evidence as pending until live verification exists.
- [ ] Run `make test TESTS="tests/deployment/test_deployment_workflow_secrets.py tests/deployment/test_deployment_scripts.py"` and `git diff --check`; record GREEN after the last task-file change.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the approved specification, ADRs 0017/0019/0028, and
  `docs/architecture.md`.
- [ ] Confirm no new durable service, persistence, schema, worker contract, or biometric authority
  was introduced.
- [ ] Record conformance and the absence of new ADR impact in the pull request.

## Verification

- Run `.venv/bin/python scripts/select_test_suites.py select --base origin/main` and record every
  required suite and reason for the final package.
- Run `.venv/bin/python scripts/select_test_suites.py fingerprint --base origin/main` and retain
  evidence only for that exact fingerprint.
- Run `.venv/bin/pre-commit run --files <all changed Python files>`; expect Ruff and full-project
  mypy success.
- Run `make check`; expect all formatting, lint, type, Django, migration-drift, and Python tests to
  succeed.
- Run every selector-required expensive target once for the exact final fingerprint; CI repeats
  these gates after push.

## Operational impact and rollout

The release increases warmed web-process RSS by a bounded event matrix and stops request-count
recycling. It changes no schema, durable row, queue, worker contract, storage artifact, or feature
state. Deploy the exact reviewed SHA through the canonical workflow, then perform only the bounded
read-only and one-user-flow checks named in the release safeguards. A deployment or production
mutation beyond the workflow requires separate authority.

## Rollback

Deploy the prior successful immutable image and configuration through the existing workflow.
Process-local caches vanish with the replaced containers. Do not mutate searches, jobs, attempts,
results, projections, queues, or Object Storage during rollback.

## Open questions

None.
