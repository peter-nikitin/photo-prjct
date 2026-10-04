# Complete the pgvector face-search cutover

- Date: 2026-10-04
- Status: Approved for implementation; production release gates remain pending
- Owner: project maintainer
- Related specification: [exact pgvector search](../superpowers/specs/2026-09-25-pgvector-exact-face-search-design.md)
- Related architecture: [Search](../architecture.md#search)
- Related ADRs: [0040](../adr/0040-use-pgvector-for-exact-face-search.md), [0041](../adr/0041-accept-pgvector-numerical-boundaries.md), [0032](../adr/0032-reconcile-code-owned-feature-flags-at-startup.md)
- ADR impact: Conforms to ADR 0040's coordinated removal of the JSON reader, parallel writes, old embedding store and temporary gate. ADR 0041's numerical policy remains binding. SFace recognition-model retirement is a later release.

## Goal and boundary

Make `processing_faceembeddingvector` (`FaceEmbeddingVector.vector`) the only persisted face-embedding store and exact pgvector the only face-search reader. Remove `processing_faceembedding` (`FaceEmbedding.vector` as JSON), all writes and runtime dependencies on it, the Python/JSON ranking path, dual-reader comparison and `pgvector-face-search-read` flag. Preserve **both** SFace and AdaFace recognition models and their 128D/512D native vector rows for now.

`SelfieSearch`, its jobs/attempts, `SelfieSearchResult` and `SelfieSearchDirectEvidence` are shared search-history tables, not legacy-reader tables. Ready result evidence points to `PhotoFaceDetection` and stores a distance, not a JSON embedding FK. Keep these tables, detections, projections, event records and saved results. No event backfill, model switch, unpublished-event deletion, SFace inference removal or SFace-vector purge belongs to this release. The 636 photos accepted as an AdaFace quality difference do not block same-generation pgvector reads.

## Starting evidence and acceptance

Read-only production audit on 2026-10-04 found web image `0028b60f320930e8f27940325195c501285a18d5`, read flag `on`, eight AdaFace events and nine SFace events, including one published SFace event. Native rows exist for both models, but the full production reconciliation was cancelled after more than six minutes. Reuse the existing local database snapshot for a full isolated rehearsal; inspect its freshness and check the live delta with bounded event-scoped queries. Counts alone do not prove current native completeness.

Acceptance:

1. Uploaded-selfie and gallery-face searches use exact native ranking and native source lookup on both SFace and AdaFace events. Missing, divergent or invalid native rows fail closed. Event scope, frozen generation, source identity, privacy, result publication, cleanup and authorization remain correct. ADR 0041 permits only its defined numerical boundary differences.
2. `FaceEmbedding` is absent from the current Django model graph and `processing_faceembedding` is absent from the deployed schema. No worker, web, background operation, offline cluster-corpus build, report, verifier or active management command queries or writes it. Historical migration files may still mention it to reconstruct old schema states.
3. `FaceEmbeddingVector` keeps both model generations and all retained accepted faces, including hidden photos that may later be shown without reprocessing. New accepted SFace and AdaFace face processing writes only native vectors; vector-only AdaFace processing remains valid. The native completeness verifier works *after* JSON-table removal and detects missing/wrong-model/wrong-dimension/invalid native rows without using JSON as an oracle.
4. The old Python/JSON reader, cache, comparison command and temporary read gate have no production call sites. Feature reconciliation removes only that gate's database row. Ready result pages still render saved results.
5. The maintainer's valueless unfinished searches already present at a recorded cutover cutoff are terminalized through a bounded, idempotent operation, regardless of frozen model. Preserve observed diagnostic state without inventing causes; do not alter ready results or searches created after the cutoff.

## Worker/state/artifact release safeguards

- [x] **Live-state inventory.** The 2026-10-04 read-only snapshot recorded web SHA/gate, event generations, vector/JSON populations and 15 queued gallery searches on the published SFace event. Immediately before each rollout stage, record current web/worker digests, search/job/attempt/lease counts, accepted and cleanup-pending results, current vector gaps, and object prefixes for temporary selfies. No photo media or published artifact is intentionally deleted.
- [x] **Compatibility matrix.** Old web/worker require the JSON table for some paths and writes. Cutover web/worker use native ranking and native-only writes while the JSON table still exists. They are compatible with existing SFace/AdaFace native rows, vector-only AdaFace rows and ready result records. Once any new native-only write exists, rollback to old code requires restoring JSON completeness; after schema drop, old images are incompatible and only forward-fix or a rehearsed database restore is supported. Drain old web and worker processes before dropping the table; never run old code against the contracted schema.
- [x] **Reviewed data-state migration or reset semantics.** No event/photo/detection/projection/result rows are reset. Verify native vectors for every retained current SFace/AdaFace cohort, plus gallery source identities and relevant offline corpus inputs. After both live readers and writers use only native vectors, remove only `FaceEmbedding` and its table through reviewed state migration plus guarded post-commit drop; leave SFace rows in the shared native table. For pre-cutover nonterminal searches, record aggregate state/failure/lease evidence, terminalize selected IDs without results and clean temporary selfies. Unknown cause remains unknown.
- [x] **End-to-end contract sizing.** Worker HTTP/callback format does not change. Exercise the accepted maximum AdaFace 32-face/512-component callback and SFace 128-component fixture through serialization, Django validation, native persistence, SQL ranking and cleanup. Confirm callback retries do not recreate JSON rows; no new payload size is introduced.
- [x] **Previous-snapshot upgrade rehearsal.** Use the existing local production snapshot, or refresh an isolated snapshot if too stale, with both model generations, parallel/native-only vectors, ready results, queued gallery searches, retryable/leased attempts, historical activations and a never-enrolled photo. Rehearse the exact staged code/schema sequence, both query sources, active processing, terminalization, saved links, native-only verifier, and rollback boundary. Record snapshot timestamp and differences from live state.
- [x] **Staged activation and rollback order.** Stage A deploy native-only reader while retaining dual writes/table; validate live search and preserve easy rollback. Stage B deploy native-only writers and all non-search consumers while the table remains, after draining old worker claims; validate new writes and establish forward-fix recovery. Stage C removes the Django model from migration state without dropping the physical table during the canonical pre-activation `migrate`; after new web/worker compatibility and deployment commit are verified, a guarded post-commit operation drops the physical table. The gate is removed with its last call site. Run post-drop smoke and native-only verifier. Stop on native gap, worker mismatch, search regression or unproven recovery. Never let the ordinary pre-activation migration drop the table while old web processes are still running. Once a native-only candidate can accept writes, any failure before deployment commit also requires forward recovery; old-image rollback is unsafe.
- [x] **Supported bounded operational commands.** Run the current `verify_pgvector_face_embeddings` on the isolated snapshot before contraction; use event-scoped live gap/count queries with timeouts. Rework that command to verify native-only state after contraction. A separate terminalization command must have dry-run, fixed cutoff, explicit selected IDs, batch limit and before/after receipt. No long unbounded production scan or table rewrite is part of this plan.

## Implementation

Execute an approved version of this plan through `$execute-implementation-plan`.

### Task 1: Prove native completeness and freeze release evidence

**Files:** `src/backend/processing/management/commands/verify_pgvector_face_embeddings.py`, current reconciliation tests and a task-local operator receipt/runbook.

- **Depends on:** None.
- **Produces:** Full isolated comparison plus bounded live event-scoped native identity/gap receipt for SFace and AdaFace; baseline search latency/error metrics and stop conditions.

- [ ] Reuse the local database copy to compare retained active cohorts and gallery-source evidence detection by detection, including dimensions/model/value. Record freshness; reconcile any live delta before data contraction.
- [ ] Test native search and gallery-source lookup on SFace, parallel AdaFace and vector-only AdaFace, including missing/divergent native data and ready links.
- [ ] Run focused native ranking/reconciliation tests with `make test TESTS="src/backend/selfie_search/tests/test_vector_ranking.py src/backend/processing/tests/test_vector_reconciliation.py"`.

### Task 2: Cut both search flows over to one native reader

**Files:** `src/backend/selfie_search/services/{jobs,submission,read_selection,direct_ranking,reader_comparison,ranking,cohort_cache}.py`, `src/backend/selfie_search/management/commands/review_pgvector_face_search.py`, relevant search tests.

- **Depends on:** Task 1.
- **Produces:** Native-only selfie and gallery searches while both embedding tables and dual writes still exist (Stage A).

- [ ] Write failing tests for both query sources, both models, fail-closed gaps, saved results and cleanup. Then route both flows directly through native ranking/source lookup; remove comparison and cache-backed reader calls and the private comparison command.
- [ ] Keep shared scalar result/frozen-generation validation; delete only code exclusive to legacy JSON ranking. Do not remove SFace query validation or thresholds.
- [ ] Deploy Stage A, verify live SFace and AdaFace selfie/gallery searches, errors, latency and temp-object cleanup. A rollback to the prior image is still supported while dual-store completeness holds.
- [ ] Run `make test TESTS="src/backend/selfie_search/tests"` and record GREEN after the last task-file change.

### Task 3: Move every remaining embedding consumer and writer to native only

**Files:** `src/backend/processing/services/{vector_embeddings,face_cohort,face_cluster_corpora,historical_adaface,vector_reconciliation,worker_pool_state,reports}.py`, `src/backend/selfie_search/services/submission.py`, pgvector backfill/inspection/verification management commands, affected processing tests and worker image tests.

- **Depends on:** Task 2.
- **Produces:** Native-only web/worker and offline corpus/reporting/activation paths while the JSON table still exists (Stage B).

- [ ] Replace JSON-based cohort identity and completeness predicates with native detection/model/generation identity checks. Ensure SFace cohorts and ordinary AdaFace cohorts are supported without requiring a JSON sibling. Preserve historical AdaFace activation and offline corpus correctness using native evidence.
- [ ] Make accepted embedding publication write only `FaceEmbeddingVector` for both current models; remove parallel-write helper. Change worker-pool and processing reports to count native evidence. Retire the JSON-to-native backfill/inspection commands when their purpose ends; provide a native-only verifier that still detects real gaps and invalid rows.
- [ ] Drain old worker claims before Stage B and verify candidate worker/web digests. Prove a new accepted SFace write and AdaFace write create native rows only. Verify existing saved results and offline corpus build remain usable. Do not roll back to code requiring new JSON siblings after the first native-only write.
- [ ] Run `make test TESTS="src/backend/processing/tests src/backend/selfie_search/tests"` and the worker image smoke; record GREEN after the last task-file change.

### Task 4: Settle pre-cutover unfinished searches

**Files:** a bounded command under `src/backend/selfie_search/management/commands/`, focused tests and the operator runbook.

- **Depends on:** Native readiness. Complete before model retirement; it need not hold up Stage A if SFace native vectors are complete.
- **Produces:** Idempotent terminalization and privacy-safe aggregate diagnosis for an explicit pre-cutover nonterminal search set.

- [ ] Inventory frozen model, search/job/attempt states, leases, existing failure evidence and temporary objects as of a fixed cutoff. Distinguish never-started gallery rows from expired/in-flight attempts; do not guess why any row stopped.
- [ ] Dry-run selected IDs, execute bounded conditional status changes and cleanup, then verify zero targeted nonterminal rows, no new result rows and unchanged ready/post-cutoff searches.

### Task 5: Remove the JSON table, temporary gate and dead schema/code

**Files:** `src/backend/processing/models.py`, a new `src/backend/processing/migrations/` migration, `src/backend/feature_flags/registry.py`, `src/backend/selfie_search/models.py` and a new `src/backend/selfie_search/migrations/` migration for unused reader-review fields, deployment checks and relevant tests.

- **Depends on:** Stages A/B live evidence and no old web process or command using JSON. Workers send callbacks through web and do not access the JSON table directly.
- **Produces:** Final schema/code with no `FaceEmbedding`, JSON reader/writer or read gate (Stage C).

- [ ] Rehearse schema contraction on the isolated snapshot and inspect the FK graph. Because `deploy/apply-deployment.sh` runs Django migrations before replacing the old web, use a state-only `DeleteModel` migration. Drop the physical table only through a guarded, idempotent post-commit command after old web processes have been replaced; verify deployment command ordering. Preserve all historical Django migrations and shared result/detection/projection tables.
- [ ] Remove the `PGVECTOR_FACE_SEARCH_READ` definition with its last call site; verify `sync_feature_flags` deletes only its stale row. Remove `reader_staff_eligible` and `reader_comparison_requested` columns if all runtime consumers are gone; their historical migration remains.
- [ ] Add a reviewed post-commit deployment step that verifies the candidate web image is active, current code has no JSON consumers, native-only verifier succeeds, old workers cannot directly access the table, and the table has no incoming FKs before it runs the guarded drop command. On failure, keep the new compatible image and report incomplete cleanup for forward recovery; never roll an old image onto a dropped table.
- [ ] Run the native-only verifier, focused tests, Django migration check, and live SFace/AdaFace search/processing smoke after deployment. Confirm `processing_faceembedding` is absent and no current code depends on it.

### Final task: Verify, document and hand off model retirement

**Files:** `docs/architecture.md`, `docs/engineering-jobs.md`, pgvector/worker/deployment runbooks and affected monitoring documentation.

- [ ] Update actual architecture facts: one pgvector table, native-only reads/writes, SFace/AdaFace coexistence, saved-result preservation and new rollback boundary. State that unpublished SFace events and their later minimal empty-recognition migration belong to the model-retirement plan.
- [ ] Run `.venv/bin/python scripts/select_test_suites.py select --base origin/main` and `.venv/bin/python scripts/select_test_suites.py fingerprint --base origin/main`; complete all selector-required expensive suites at the final package fingerprint. Run `.venv/bin/pre-commit run --files <changed Python files>`, `make static`, `make check` and `git diff --check`.
- [ ] Search active source for `FaceEmbedding`, JSON cohort reads/writes, `rank_legacy_direct`, reader comparison and `PGVECTOR_FACE_SEARCH_READ`; only historical migrations or deliberately archival docs may retain references. Reconcile final behavior with ADRs 0040/0041/0032 before push.

## Operational rollout and rollback

Stage A removes production JSON **reads** but keeps JSON writes and the table only as a short release-safety stage. Stage B removes JSON writes and all other current consumers, then validates new native-only face processing. Stage C drops the JSON table and read gate after incompatible processes are drained. Stage C is the completion criterion; do not call this migration finished after Stage A or B. Observe fresh failure, latency, worker and cleanup metrics after each stage, with one controlled selfie and gallery query for a published SFace event and an AdaFace event where access allows.

Before Stage B, reverting the previous image is possible while both stores are complete. After Stage B, prefer forward repair; old code needs a reviewed JSON reconstruction before it could read newly written faces. After Stage C, old images cannot run against the contracted schema. Recovery requires the rehearsed database restore plus compatible images or a forward fix, not a feature-flag flip. No event records, ready results, SFace model artifact or SFace native vectors are deleted in this release.

## Open execution gate

Full current-data native completeness and the canonical old-web-process drain/migration order must be recorded before Stage C. The prior production full verifier did not complete; an old web process writing during table contraction would fail. Worker pool and member build IDs are separate from the web image revision and must not be compared to it as a table-retirement guard. These are concrete release checks, not reasons to keep the JSON table indefinitely.
