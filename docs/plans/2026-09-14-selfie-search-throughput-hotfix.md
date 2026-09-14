# Selfie Search Throughput Hotfix Implementation Plan

- Date: 2026-09-14
- Status: Approved for implementation
- Owner: project maintainer
- Related specifications:
  - [Public selfie search](../superpowers/specs/2026-07-30-public-selfie-search-design.md)
  - [Production AdaFace for new events](../superpowers/specs/2026-08-16-production-adaface-new-events-design.md)
- Related architecture: [Search and photo processing](../architecture.md)
- Related ADRs:
  - [ADR 0017: Django-polled photo-processing jobs](../adr/0017-use-django-polled-photo-processing-jobs.md)
  - [ADR 0019: Public event-scoped selfie search](../adr/0019-use-public-event-selfie-search.md)
  - [ADR 0028: One canonical deployment](../adr/0028-operate-one-canonical-deployment.md)
- ADR impact: Conforms to ADRs 0017, 0019, and 0028. The hotfix retains one canonical Compose
  deployment, the private HTTP worker boundary, PostgreSQL authority, event-scoped exact ranking,
  transient selfie query vectors, and immutable result snapshots. No new ADR is required.

## Goal

Restore bounded exact selfie-search latency for the current 45,239-face AdaFace event cohort while
ensuring that a slow or failed selfie search cannot stop bulk metadata, preview, face, watermark, or
bib processing.

## Scope

The approved 2026-09-14 hotfix includes:

- replace the regressed generation-aware cohort query with an accepted-projection-anchored query;
- remove full configuration-JSON comparisons and wide global sorting from the hot path while
  preserving exact generation, event, publication, accepted-attempt, and deterministic-result
  semantics;
- keep cohort loading and exact ranking outside `SelfieSearch` and `SelfieSearchJob` row locks;
- split the existing worker image into one bulk service and one selfie-only service;
- give the selfie service a 900-second HTTP timeout and handle socket `TimeoutError` without exiting
  the daemon; and
- deploy and validate the hotfix on the single canonical VM without disabling selfie search or
  resetting any queue.

Excluded: `pgvector`, ANN search, query-vector persistence, result-threshold changes, face-generation
changes, existing-event replay, embedding backfill, a broker, another VM, and changes to selfie or
result authorization.

## Acceptance criteria

- A frozen fixture containing old and new face generations returns the exact same candidate IDs,
  best detection per photo, cosine distances, and final order before and after the query rewrite.
- The cohort query uses `PhotoFaceEmbeddingProjection` generation identity and scalar immutable
  attempt identity; it does not compare full `configuration` JSON on processing jobs, attempts, or
  runs and does not globally sort wide vector rows.
- A concurrent selfie claim and an unrelated photo-processing claim/complete operation are not
  blocked while a test-controlled cohort load/rank phase is paused.
- No query embedding is persisted, and a stale or duplicate callback cannot replace the accepted
  result after the final lock/recheck phase.
- Canonical Compose runs exactly one selfie-only worker and the configured number of bulk workers
  from the same immutable worker image. The selfie identity is absent from every bulk worker; every
  required bulk identity is absent from the selfie worker.
- A raw socket timeout produces bounded worker backoff instead of a process exit. The selfie worker
  waits up to 900 seconds for the known synchronous completion path; bulk HTTP timeout remains 180
  seconds.
- On a production-equivalent cohort, `load_ms + rank_ms` is below 60,000 ms and the result snapshot
  is exactly equal to the baseline. Failure to meet either condition blocks deployment.
- In the canonical post-deployment check, an active selfie search and the Krylatskoye bulk queue make
  progress concurrently, worker restart counters remain stable, PostgreSQL has no accumulating lock
  waiters, the selfie reaches `ready`, and public HTTP remains healthy.

## Worker/state/artifact release safeguards

- [x] **Live-state inventory.** Read-only production evidence on 2026-09-14 recorded deployed SHA
  `3218a16130ad25aa5dac2ac7e14ca12cea56dd35`; worker identities
  `1/capture_metadata/2,2/generate_preview/1,2/generate_watermarked_preview/1,2/face_embedding/3,3/face_embedding/5,1/selfie_query/2,1/bib_recognition/1`;
  processor priority `selfie_query,face_embedding,capture_metadata,generate_preview`; durable
  Krylatskoye queues above 11,000 metadata and 13,000 preview jobs; and one 45,239-face AdaFace
  cohort. Existing selfie attempts included expired leases and accepted successes, the observed
  search finished `ready`, and its `selfie-search/` temporary object was cleared by the existing
  cleanup gate. The current Object Storage prefixes and artifact identities are unchanged by this
  plan.
- [x] **Compatibility matrix.** New Django accepts callbacks from the old shared worker and both new
  role-specific services because the worker HTTP contract, processor contract versions, payloads,
  leases, and result schemas do not change. New workers use the same API against new Django. Existing
  queued, retryable, processing, failed, succeeded, cancelled, stale, and expired rows remain readable
  under their current versions. A prior-image rollback restores the shared worker and continues the
  same durable rows. Mixed old/new service topology is allowed only inside the deployment transaction,
  never as a steady state.
- [x] **Reviewed data-state migration or reset semantics.** Use compatible drain. The schema change is
  an index only; no durable processing or selfie rows, attempts, accepted projections, results,
  derivatives, reports, or Object Storage objects are rewritten, requeued, backfilled, purged, or
  reset. A lease active during container replacement may expire and follow its existing bounded retry
  policy.
- [x] **End-to-end contract sizing.** The maximum observed path is AdaFace 512 dimensions over 45,239
  faces and 21,619 photos. Tests cover the 512-value query callback, exact candidate/result equality,
  bounded response payload, callback validation and persistence, and absence of persisted query
  vectors. The production-equivalent benchmark records `eligible_face_count`, `eligible_photo_count`,
  `load_ms`, `rank_ms`, matched IDs/order/distances, and peak Django/PostgreSQL resource use without
  logging vectors, bearer tokens, storage keys, or selfie bytes.
- [x] **Previous-snapshot upgrade rehearsal.** Before deployment, restore or project a sanitized
  previous-version database snapshot containing successful, failed, retryable, stale, active/expired
  selfie attempts, accepted SFace/AdaFace projections, terminal bearer results, derived previews, and
  never-enrolled photos. Run migrations, cohort-equivalence checks, lease recovery, and both worker
  roles. Every prior row/artifact must retain its status and identity; only the new index and runtime
  topology may differ.
- [x] **Staged activation and rollback order.** Deploy compatible Django plus the shared worker image;
  apply the additive index; start bulk and selfie services; verify role identities and readiness;
  observe one bounded existing-cohort search while bulk jobs continue; then accept the release. Stop
  on result mismatch, latency at or above 60 seconds, new lock accumulation, restarts, cleanup failure,
  or bulk starvation. Roll back to the previous successful image/Compose package without changing
  queue rows, results, projections, prefixes, or feature-gate state.
- [x] **Supported bounded operational commands.** Inventory uses read-only Django aggregates,
  `pg_stat_activity`, `docker inspect`, `docker stats`, and sanitized structured selfie timing logs.
  Migration rehearsal uses `make test-migrations` and `scripts/check_migration_immutability.py`.
  Deployment uses only `.github/workflows/deploy.yml` with an exact `deployment_sha`; rollback uses the
  deployment workflow's prior-successful-image path. This release authorizes no manual ORM deletion,
  SQL update, `TRUNCATE`, bulk requeue, Object Storage deletion, or feature disablement.

Rationale: [2026-07-31 staging processing-state reset postmortem](../postmortems/2026-07-31-staging-processing-state-reset.md).

## Implementation

Execute this plan through `$execute-implementation-plan`.

### Task 1: Reproduce the regression and anchor the exact cohort on accepted projections

**Files:**

- Modify `src/backend/processing/models.py`.
- Create the next `src/backend/processing/migrations/` migration.
- Modify `src/backend/processing/services/face_cohort.py`.
- Modify `src/backend/processing/tests/test_face_cohort.py`.
- Modify focused cohort tests in `src/backend/selfie_search/tests/test_submission.py`.

- **Specification:** Public selfie search “Candidate cohort” and Production AdaFace “Design”.
- **Depends on:** None.
- **Produces:** A shared exact cohort loader whose projection-first query is used by uploaded-selfie,
  gallery-face, and offline-cluster consumers without changing membership.

- [ ] Add a failing PostgreSQL regression test with multiple events, SFace/AdaFace generations,
  superseded attempts, hidden/ineligible photos, rejected detections, and equal-distance faces. Assert
  exact candidate/result equality and assert that the generated hot-path SQL contains no full
  configuration-JSON comparisons or wide global vector sort.
- [ ] Record the current query's red plan and timing against the bounded production-equivalent cohort.
- [ ] Add a generation-first composite index to `PhotoFaceEmbeddingProjection` covering contract
  version, processor version, configuration hash, photo, and accepted attempt.
- [ ] Rewrite the loader to start from exact accepted projections, validate scalar immutable
  generation/event/attempt identity, and fetch only ranking fields in bounded batches. Do not
  hydrate related models or persist candidate rows.
- [ ] Make final ranking tie-breaking independent of database row order so the SQL-wide
  `ORDER BY detection_id` can be removed without changing deterministic output.
- [ ] Run `make test TESTS="src/backend/processing/tests/test_face_cohort.py src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_ranking.py"`; expect the cohort and ranking package to pass.
- [ ] Re-run the bounded benchmark; require exact result equality and total cohort load plus rank
  below 60 seconds before Task 2 proceeds.

### Task 2: Remove cohort work from the locked callback transaction

**Files:**

- Modify `src/backend/selfie_search/services/jobs.py`.
- Modify `src/backend/selfie_search/services/submission.py` only where the loader currently mutates
  search counters.
- Modify `src/backend/selfie_search/tests/test_jobs.py`.
- Modify `src/backend/selfie_search/tests/test_submission.py`.

- **Specification:** Public selfie search “Product state”, “Django”, and “Failure handling”.
- **Depends on:** Task 1 loader and deterministic result contract.
- **Produces:** A two-phase callback: pure snapshot load/rank first, short lock/recheck/persist second.

- [ ] Add a failing `TransactionTestCase` that pauses cohort ranking and proves another selfie claim
  plus an unrelated photo claim/complete can finish without waiting for the paused callback.
- [ ] Add stale, duplicate, lease-expired, cleanup-failure, and concurrent-completion cases that prove
  final locked revalidation preserves immutable attempt/result semantics.
- [ ] Refactor candidate loading to return candidates and counts without saving `SelfieSearch`.
- [ ] Load and rank from an unlocked immutable search/configuration snapshot, then atomically lock the
  search/job/attempt, revalidate current lease and payload identity, save counters/results/evidence,
  and retain the existing cleanup-before-terminal-publication gate.
- [ ] Run `make test TESTS="src/backend/selfie_search/tests/test_jobs.py src/backend/selfie_search/tests/test_submission.py tests/processing/test_selfie_search_e2e.py"`; expect all concurrency, privacy, cleanup, and result tests to pass.

### Task 3: Make worker HTTP timeout bounded and non-fatal

**Files:**

- Modify `src/worker/photo_worker/client.py`.
- Modify `src/worker/photo_worker/runner.py`.
- Modify `src/worker/tests/test_client.py`.
- Modify `src/worker/tests/test_runner.py`.
- Modify `.env.example`.

- **Specification:** ADR 0017 retry/termination semantics; no processor payload change.
- **Depends on:** None.
- **Produces:** `PHOTO_WORKER_HTTP_TIMEOUT_SECONDS`, defaulting to 180 seconds and set to 900 only for
  the selfie service; raw socket timeout becomes sanitized recoverable `network_interruption`.

- [ ] Add failing configuration tests for positive finite timeout parsing and client construction.
- [ ] Add a failing raw `TimeoutError` client/runner test proving bounded backoff and no daemon exit or
  secret leakage.
- [ ] Implement the environment setting and map raw socket timeout through the existing sanitized API
  interruption path.
- [ ] Run `make test TESTS="src/worker/tests/test_client.py src/worker/tests/test_runner.py"`; expect all worker transport and daemon-lifecycle tests to pass.

### Task 4: Run independent bulk and selfie services from one worker image

**Files:**

- Modify `docker-compose.yml`.
- Modify `docker-compose.deployment.yml`.
- Modify applicable local Compose overlays.
- Modify `.github/workflows/deploy.yml`.
- Modify `deploy/apply-deployment.sh` and `deploy/cutover-compose-identity.sh`.
- Modify deployment helpers only where they enumerate worker services or environment keys.
- Modify `tests/processing/test_worker_container_contract.py`.
- Modify focused tests under `tests/deployment/`.

- **Specification:** Approved 2026-09-14 scope delta; ADR 0017 worker boundary; ADR 0028 canonical
  deployment.
- **Depends on:** Task 3 timeout interface.
- **Produces:** One fixed selfie service and one configurable-replica bulk service with disjoint
  identities, readiness checks, logs, resource limits, deployment state, and rollback.

- [ ] Add failing Compose and deployment tests proving disjoint identity sets, one selfie replica,
  the configured bulk replica count, same immutable image, no database/storage credentials, selfie
  timeout 900, bulk timeout 180, and exact health/restart verification for both services.
- [ ] Keep `PHOTO_WORKER_REPLICAS` as the bulk replica count because it remains an active supported
  capacity control; introduce explicit bulk/selfie identity and priority variables and remove the
  obsolete shared identity variables from generated deployment state.
- [ ] Update canonical reconciliation so processing enabled starts both roles and processing disabled
  removes both. A failed deploy restores the previous shared-worker package and its environment
  atomically rather than leaving a mixed steady state.
- [ ] Preserve all required identities: bulk owns metadata, clean/watermarked previews, SFace/AdaFace
  face embedding, and bib recognition; selfie owns only `1/selfie_query/2`.
- [ ] Run `make test TESTS="tests/processing/test_worker_container_contract.py tests/deployment/test_deployment_scripts.py tests/deployment/test_compose_identity_cutover.py tests/deployment/test_bib_deployment_contract.py tests/deployment/test_deployment_workflow_secrets.py"`; expect all topology, configuration, rollback, and secret-boundary tests to pass.

### Task 5: Rehearse upgrade, document reality, and prepare the rollout package

**Files:**

- Modify `docs/architecture.md`.
- Modify `docs/photo-processing-vm-sizing.md` with final benchmark/topology evidence.
- Modify operational documentation selected by changed-path references.
- Create task evidence reports under the `$execute-implementation-plan` working package.

- **Specification:** All linked specifications and this approved scope delta.
- **Depends on:** Tasks 1-4.
- **Produces:** Exact-fingerprint verification, previous-snapshot evidence, rollout commands, stop
  conditions, and architecture reconciliation.

- [ ] Rehearse the additive migration and topology transition on the available previous-version
  snapshot; inventory every safeguard state before and after and confirm no durable row/artifact
  mutation beyond the index.
- [ ] Run the production-equivalent 45,239-face benchmark without logging biometric vectors or bearer
  data. Require exact result equality and less than 60 seconds for load plus rank.
- [ ] Update architecture to state that bulk and selfie use independent services and that cohort work
  occurs outside the final locked publication transaction.
- [ ] Use `$select-verification-suites` with base `origin/main`; run every selected suite and record the
  exact package fingerprint and outputs.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the linked specifications, ADRs 0017/0019/0028, and
  `docs/architecture.md`.
- [ ] Confirm no `pgvector`, persistent query vector, changed threshold, mixed generation, new public
  endpoint, extra deployment, or relaxed media authorization entered the package.
- [ ] Confirm architecture documentation matches the implemented query and worker topology.
- [ ] Record “Conforms to ADRs 0017, 0019, and 0028; no new or superseding ADR” in the pull request.

## Verification

Focused RED/GREEN commands are specified per task. On the final unchanged package, the root
controller must run:

```sh
.venv/bin/pre-commit run --files <all changed Python files>
.venv/bin/python scripts/select_test_suites.py select --base origin/main --head HEAD --format json
.venv/bin/python scripts/select_test_suites.py fingerprint --base origin/main
make check
make test-operational
make test-migrations
.venv/bin/python scripts/check_migration_immutability.py --base origin/main --head HEAD
git diff --check
```

Run any additional expensive suite selected by `scripts/select_test_suites.py`. Expected outcome:
all commands exit zero; no migration drift; the final fingerprint matches all recorded evidence;
the concurrency regression goes green; and the cohort benchmark returns the exact baseline result
under 60 seconds.

## Operational impact and rollout

1. Capture a fresh read-only inventory of worker identities, job/search/attempt states, active leases,
   queue counts, public HTTP, VM/container resources, PostgreSQL lock waiters, and sanitized timing.
2. Push one reviewed commit and require green CI. Merge to `main`; use the canonical deploy workflow
   with the exact merged SHA.
3. The deployment applies the additive projection index, starts the new bulk and selfie services from
   the same image, verifies both, and removes the prior shared service within the deployment
   transaction. No feature flag changes.
4. Verify deployed SHA, service identity sets, container CPU/RAM limits, restart counts, and HTTP
   health. Confirm the static IP, disk, VM 8-vCPU/16-GiB size, and existing queues are unchanged.
5. Run one bounded real selfie search for event 15 only when no other active search would make the
   result ambiguous. Record sanitized load/rank timing and result-state/count equality; do not retain
   selfie bytes or query vectors.
6. During the same interval, sample Krylatskoye metadata/preview success and queue counts. Require
   concurrent progress, no accumulating locks, stable restarts, and healthy public HTTP.
7. Stop and roll back on any acceptance failure. Do not disable selfie search as a mitigation.

## Rollback

Deploy the previous successful SHA through the canonical workflow. Its Compose package restores the
single shared worker and removes the two new role services. The additive index may remain because it
does not reinterpret data; remove it only in a later reviewed migration. Preserve every processing
job/state/attempt/run, selfie search/job/attempt/result/evidence row, projection, derivative, and
Object Storage object. After rollback, verify lease recovery, queue movement, cleanup, public HTTP,
and the previous image identity. Do not reset or bulk-requeue state.

## Open questions

None.
