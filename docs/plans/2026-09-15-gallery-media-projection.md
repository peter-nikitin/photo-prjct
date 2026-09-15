# Gallery Media Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace request-time processing-history scans with one synchronous, rebuildable gallery
media projection used by gallery, exact media, selfie-search, and commerce reads.

**Architecture:** `picflow.GalleryMediaProjection` stores only accepted clean and watermarked
presentation keys plus immutable source-attempt provenance. The existing preview publication
transaction updates it atomically; a single gallery-media module owns set-oriented and exact-photo
read interfaces. A worker-paused deployment backfills and verifies the projection before switching
readers.

**Tech Stack:** Django 5 models and transactions, PostgreSQL constraints and set operations, pytest,
Docker Compose deployment shell, GitHub Actions.

**Spec:**
[`docs/superpowers/specs/2026-09-15-gallery-media-projection-design.md`](../superpowers/specs/2026-09-15-gallery-media-projection-design.md)

## Global Constraints

- `ProcessingAttempt`, `PhotoProcessingState`, and `PhotoDerivative` remain authoritative evidence.
- `GalleryMediaProjection` stores no event publication, visibility, folder, capture-time, price,
  search-membership, order, entitlement, or original-media facts.
- Accepted derivative and projection publication commit or roll back in one transaction.
- Customer-facing eligibility SQL must not reference the three processing tables after cutover.
- Exact media and download checks use `Photo.pk + event_id`; they never invoke the collection query.
- Missing or incomplete preview-first projection state fails closed.
- Do not add an asynchronous queue, materialized view, cache, compatibility read, or generic
  projection framework.
- Implementers and reviewers must use `$select-verification-suites`; subagents leave changes
  unstaged and never commit or push.
- The root controller creates one reviewed commit per task, then runs final selector-required suites
  on the exact final fingerprint.

---

## File Map

- `src/backend/picflow/models.py`: persistence shape and database-level pair constraints.
- `src/backend/picflow/migrations/0016_gallery_media_projection.py`: additive projection schema.
- `src/backend/picflow/gallery_media_projection.py`: projection publication, derivation, rebuild,
  and privacy-safe exact verification.
- `src/backend/processing/services/previews.py`: sole production publication call site.
- `src/backend/picflow/gallery.py`: canonical collection and exact-photo read interfaces and media
  selection without derivative queries.
- `src/backend/config/views.py`: exact media/download route adoption.
- `src/backend/picflow/management/commands/rebuild_gallery_media_projection.py`: explicit dry-run or
  set-oriented apply command.
- `src/backend/picflow/management/commands/verify_gallery_media_projection.py`: symmetric-difference
  report and clean gate.
- `src/backend/picflow/management/commands/smoke_gallery_media_projection.py`: bounded largest-event
  page and exact-photo query-shape smoke.
- `deploy/apply-deployment.sh`: worker pause, candidate migration, rebuild, verification, cutover,
  and recovery order.
- Focused test files under `src/backend/picflow/tests/`, `src/backend/processing/tests/`, and
  `tests/deployment/`: model, transaction, query-shape, command, and deployment contracts.
- `docs/adr/0037-use-gallery-media-read-projection.md`, `docs/adr/README.md`,
  `docs/architecture.md`, and `docs/engineering-jobs.md`: accepted decision and implemented-state
  evidence.

---

### Task 1: Projection schema and atomic publication

**Files:**

- Modify: `src/backend/picflow/models.py`
- Create: `src/backend/picflow/migrations/0016_gallery_media_projection.py`
- Create: `src/backend/picflow/gallery_media_projection.py`
- Modify: `src/backend/processing/services/previews.py`
- Create: `src/backend/picflow/tests/test_gallery_media_projection.py`
- Modify: `src/backend/processing/tests/test_previews.py`

**Interfaces:**

- Consumes: immutable accepted `PhotoDerivative` rows produced inside
  `processing.services.previews._publish_after_verification()`.
- Produces:

```text
class GalleryMediaProjection(models.Model):
    photo: OneToOneField[Photo]
    clean_preview_final_key: str | None
    clean_preview_source_attempt: ProcessingAttempt | None
    watermarked_preview_final_key: str | None
    watermarked_preview_source_attempt: ProcessingAttempt | None

publish_gallery_media(derivative: PhotoDerivative) -> GalleryMediaProjection
```

- [ ] **Step 1: Add failing model-shape and publication tests**

Create tests proving sparse one-to-one identity, both-null-or-both-non-null slot constraints,
accepted producer validation, clean/watermarked slot preservation, exact-repeat idempotency, and a
conflict for a different value in an occupied slot. The core publication assertion is:

```python
projection = publish_gallery_media(clean_derivative)
assert projection.photo_id == clean_derivative.photo_id
assert projection.clean_preview_final_key == clean_derivative.final_key
assert projection.clean_preview_source_attempt_id == clean_derivative.accepted_attempt_id
assert projection.watermarked_preview_final_key is None
```

- [ ] **Step 2: Run the focused tests and record RED evidence**

Run:

```bash
make test TESTS="src/backend/picflow/tests/test_gallery_media_projection.py"
```

Expected: collection or import failure because the model and publication interface do not exist.

- [ ] **Step 3: Add the model and migration**

Add `GalleryMediaProjection` in `picflow.models` with `photo` as the one-to-one primary key,
nullable key/source pairs, `on_delete=PROTECT` for evidence references, `related_name="+"` for
source attempts, and `updated_at=auto_now=True`. Add named check constraints equivalent to:

```python
Q(clean_preview_final_key__isnull=True, clean_preview_source_attempt__isnull=True)
| Q(clean_preview_final_key__isnull=False, clean_preview_source_attempt__isnull=False)
```

and the matching watermarked constraint. Generate migration `0016`, inspect it, and keep it purely
additive; it must not populate rows.

- [ ] **Step 4: Implement explicit projection publication**

In `picflow.gallery_media_projection`, map only `preview-small-v1` and
`preview-watermarked-v1` to their slots and required processor types. Validate photo identity,
processor identity, succeeded status, accepted flag, and current accepted state. Lock the projection
after the caller's existing photo/evidence locks. Return unchanged state for an exact repeat and
raise `GalleryMediaProjectionConflict` for a different occupied slot.

- [ ] **Step 5: Insert publication into the existing transaction**

In `_publish_after_verification()`, retain the created derivative, then call:

```python
derivative = PhotoDerivative.objects.create(
    photo_id=attempt.photo_id,
    variant=publication.result["variant"],
    final_key=publication.final_key,
    byte_size=publication.result["byte_size"],
    content_type=publication.result["content_type"],
    width=publication.result["width"],
    height=publication.result["height"],
    oriented_source_width=publication.result["oriented_source_width"],
    oriented_source_height=publication.result["oriented_source_height"],
    sha256=publication.result["sha256"],
    accepted_attempt=attempt,
)
publish_gallery_media(derivative)
```

Do this before downstream enrollment and run closure so any projection error rolls back attempt,
state, derivative, and enrollment together.

- [ ] **Step 6: Add transaction and concurrency regressions**

Extend preview tests to patch `publish_gallery_media` with an exception and assert that the attempt
remains in progress, state is not succeeded, and no derivative or projection commits. Add a
`TransactionTestCase` that publishes clean and watermarked results for one photo and proves both
slots survive without lock inversion.

- [ ] **Step 7: Run GREEN checks after the last Task 1 edit**

Run:

```bash
make test TESTS="src/backend/picflow/tests/test_gallery_media_projection.py src/backend/processing/tests/test_previews.py"
.venv/bin/pre-commit run --files src/backend/picflow/models.py src/backend/picflow/gallery_media_projection.py src/backend/processing/services/previews.py src/backend/picflow/tests/test_gallery_media_projection.py src/backend/processing/tests/test_previews.py
```

Record commands, exit statuses, summaries, selector output, fingerprint, and that GREEN followed the
last Task 1 file change. After independent approval, the root controller commits only Task 1 files.

---

### Task 2: Canonical projection-backed read paths

**Files:**

- Modify: `src/backend/picflow/gallery.py`
- Modify: `src/backend/config/views.py`
- Modify: `src/backend/selfie_search/views.py`
- Modify: `src/backend/selfie_search/services/results.py`
- Modify: `src/backend/selfie_search/services/submission.py`
- Modify: `src/backend/commerce/views.py`
- Modify: `src/backend/commerce/services.py`
- Modify: `src/backend/commerce/checkout.py`
- Create: `src/backend/picflow/tests/test_gallery_media_projection_queries.py`
- Modify: `src/backend/picflow/tests/test_gallery.py`
- Modify: `src/backend/picflow/tests/test_gallery_preview_grants.py`
- Modify: `src/backend/picflow/tests/test_photo_visibility.py`
- Modify: `src/backend/picflow/tests/test_views.py`
- Modify: `src/backend/processing/tests/test_paid_watermarked_preview_flow.py`
- Modify: `src/backend/selfie_search/tests/test_results.py`
- Modify: `src/backend/selfie_search/tests/test_submission.py`
- Modify: `src/backend/selfie_search/tests/test_views.py`
- Modify: `src/backend/commerce/tests/test_checkout.py`
- Modify: `src/backend/commerce/tests/test_checkout_views.py`
- Modify: `src/backend/commerce/tests/test_services.py`
- Modify: `src/backend/commerce/tests/test_views.py`
- Modify: `src/backend/commerce/tests/test_paid_photo_cart_flow.py`
- Modify: `src/backend/commerce/tests/test_paid_photo_purchase_flow.py`

**Interfaces:**

- Consumes: Task 1 projection fields and publication invariant.
- Produces:

```text
class GalleryMediaPurpose(StrEnum):
    PRESENTATION = "presentation"
    ORIGINAL_DOWNLOAD = "original_download"
    PURCHASE = "purchase"

gallery_photo_queryset(
    *, event: Event, capture_time_start=None, capture_time_end=None,
    folder_ids: Collection[int] | None = None, include_unfiled: bool = False,
    paid_watermarked_previews_enabled: bool = False,
) -> QuerySet[Photo]

public_gallery_photo(
    *, event_id: int, photo_id: str, purpose: GalleryMediaPurpose,
    paid_watermarked_previews_enabled: bool = False,
) -> Photo
```

The existing collection name remains to minimize caller knowledge; its implementation and exact
interface share one private projection-backed eligibility builder.

- [ ] **Step 1: Add failing semantic-parity and SQL-shape tests**

Create fixtures for legacy original, free clean preview, paid watermarked preview, missing slot,
hidden photo, unpublished event, and a photo from another event. Capture SQL for collection, exact
media, exact download, saved selfie results, selfie submission, cart validation, and checkout.
Assert case-insensitively that captured SQL contains none of:

```python
FORBIDDEN_RELATIONS = (
    "processing_photoprocessingstate",
    "processing_processingattempt",
    "processing_photoderivative",
)
```

For exact-photo lookup, assert one query contains predicates for both the photo primary key and
event foreign key and that media-key selection causes no subsequent query.

- [ ] **Step 2: Run representative tests and record RED evidence**

Run:

```bash
make test TESTS="src/backend/picflow/tests/test_gallery_media_projection_queries.py src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py"
```

Expected: failures because current eligibility and resolver SQL still joins processing relations and
exact routes call the collection queryset.

- [ ] **Step 3: Replace processing eligibility with projection eligibility**

Change the private eligibility builder so legacy policy reads existing `Photo` fields, clean policy
requires `gallery_media_projection__clean_preview_final_key__isnull=False`, and watermarked policy
requires the matching watermarked field plus the existing feature/event rules. Remove the
processing `F` expression, processing model imports, and `distinct()` that existed only for
one-to-many processing joins.

- [ ] **Step 4: Add the exact-photo interface**

Implement `public_gallery_photo()` as a direct `Photo.objects.select_related(
"gallery_media_projection")` query constrained by `pk` and `event_id`. Apply the shared eligibility
semantics and purpose-specific original/purchase restrictions. Return no storage key for an
unauthorized purpose and raise `Photo.DoesNotExist` for every fail-closed case.

- [ ] **Step 5: Remove resolver-side derivative reads**

Change `PublicMediaResolver` to select a clean or watermarked key from the already-loaded
projection. Preserve legacy original selection and all current `ObjectMissing`, `ObjectMismatch`,
and storage-unavailable behavior. A resolver call must issue zero database queries.

- [ ] **Step 6: Cut exact routes and set-oriented consumers over**

Use `public_gallery_photo()` in `config.views.photo_media` and `photo_download`. Keep
`gallery_photo_queryset()` as the only set-oriented entry for gallery, selfie, and commerce. Remove
any local eligibility reconstruction found in those consumers; preserve bearer-result membership,
published-event checks, feature gates, paid-photo original denial, and entitlement checks.

- [ ] **Step 7: Update direct-derivative test factories**

For tests that intentionally construct accepted derivatives without exercising preview completion,
call `publish_gallery_media()` in the shared/local factory. Tests for incomplete publication must
explicitly omit that call. Do not create signals or automatic model-save behavior for test
convenience.

- [ ] **Step 8: Run GREEN checks after the last Task 2 edit**

Run:

```bash
make test TESTS="src/backend/picflow/tests/test_gallery_media_projection_queries.py src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_gallery_preview_grants.py src/backend/picflow/tests/test_photo_visibility.py src/backend/picflow/tests/test_views.py src/backend/processing/tests/test_paid_watermarked_preview_flow.py src/backend/selfie_search/tests/test_results.py src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_views.py src/backend/commerce/tests/test_checkout.py src/backend/commerce/tests/test_checkout_views.py src/backend/commerce/tests/test_services.py src/backend/commerce/tests/test_views.py src/backend/commerce/tests/test_paid_photo_cart_flow.py src/backend/commerce/tests/test_paid_photo_purchase_flow.py"
```

Then run `.venv/bin/pre-commit run --files` with every Python file changed by Task 2. Record the
exact selector, formatting, type-check, and test commands in the task report. After independent
approval, the root controller commits only Task 2 files.

---

### Task 3: Set-oriented rebuild and exact clean gate

**Files:**

- Modify: `src/backend/picflow/gallery_media_projection.py`
- Create: `src/backend/picflow/management/commands/rebuild_gallery_media_projection.py`
- Create: `src/backend/picflow/management/commands/verify_gallery_media_projection.py`
- Create: `src/backend/picflow/tests/test_gallery_media_projection_commands.py`

**Interfaces:**

- Consumes: Task 1 model and exact evidence rules.
- Produces:

```text
rebuild_gallery_media_projection(*, apply: bool) -> ProjectionRebuildReport
verify_gallery_media_projection() -> ProjectionVerificationReport
```

Both reports contain aggregate counts only. Commands accept `--all-events`; rebuild requires
`--apply` to mutate and verify accepts `--require-clean` to fail on drift.

- [ ] **Step 1: Add failing command contract tests**

Cover dry-run no-write behavior, empty database, clean and watermarked derivation, ignored failed or
unaccepted evidence, wrong-source drift, missing and extra rows, idempotent repeat, explicit apply,
and privacy-safe JSON. Patch query capture so verification fails if it loads per-photo model rows or
emits a key, filename, photo ID, or attempt ID.

- [ ] **Step 2: Run command tests and record RED evidence**

Run:

```bash
make test TESTS="src/backend/picflow/tests/test_gallery_media_projection_commands.py"
```

Expected: command imports fail because rebuild and verifier do not exist.

- [ ] **Step 3: Implement one canonical expected projection relation**

Build a PostgreSQL relation that selects accepted `preview-small-v1` and
`preview-watermarked-v1` derivatives only when their source attempt and current processing state
match the Task 1 invariant. Pivot the two supported variants into one row per photo. Reuse this
relation for rebuild and verification so they cannot drift semantically.

- [ ] **Step 4: Implement set-oriented dry-run and apply**

Dry-run returns aggregate inserted, changed, and removed counts. Apply executes bounded SQL inside
one transaction: upsert expected rows, then delete projection rows absent from expected. It never
changes evidence or object storage. A repeated apply reports zero changes.

- [ ] **Step 5: Implement the symmetric-difference clean gate**

Count:

```sql
SELECT count(*)
FROM (
  (expected_projection EXCEPT actual_projection)
  UNION ALL
  (actual_projection EXCEPT expected_projection)
) AS projection_difference
```

Return only `clean`, total projected/expected counts, and aggregate mismatch count. The command exits
non-zero under `--require-clean` when mismatch count is not zero.

- [ ] **Step 6: Run GREEN checks after the last Task 3 edit**

Run:

```bash
make test TESTS="src/backend/picflow/tests/test_gallery_media_projection.py src/backend/picflow/tests/test_gallery_media_projection_commands.py"
.venv/bin/pre-commit run --files src/backend/picflow/gallery_media_projection.py src/backend/picflow/management/commands/rebuild_gallery_media_projection.py src/backend/picflow/management/commands/verify_gallery_media_projection.py src/backend/picflow/tests/test_gallery_media_projection_commands.py
```

Record exact evidence. After independent approval, the root controller commits only Task 3 files.

---

### Task 4: Safe one-release cutover, operational smoke, and durable documentation

**Files:**

- Modify: `deploy/apply-deployment.sh`
- Modify: `deploy/run-remote.sh` only if a validated phase allow-list changes.
- Modify: `.github/workflows/deploy.yml` only if its validated phase allow-list changes.
- Modify: `tests/deployment/test_deployment_scripts.py`
- Create: `src/backend/picflow/management/commands/smoke_gallery_media_projection.py`
- Create: `src/backend/picflow/tests/test_gallery_media_projection_smoke.py`
- Create: `docs/adr/0037-use-gallery-media-read-projection.md`
- Modify: `docs/adr/README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/engineering-jobs.md`

**Interfaces:**

- Consumes: Task 3 rebuild and clean-gate commands and existing Docker Compose recovery machinery.
- Produces: a deployment sequence that leaves old web serving while processing publication is
  paused, then starts only a fully populated projection-aware candidate.

- [ ] **Step 1: Add failing deployment-order and rollback tests**

Extend the fake Compose harness to assert this exact order for an established deployment:

```text
candidate pull
processing worker stop
candidate migrate
rebuild_gallery_media_projection --all-events --apply
verify_gallery_media_projection --all-events --require-clean
candidate compose reconciliation
smoke_gallery_media_projection
worker health
```

Add failures for worker stop, migration, rebuild, verification, and smoke. Assert previous web stays
available before reconciliation, previous workers recover after a failed pre-switch mutation, no
candidate starts after a dirty verification, and no secret or row-level media fact reaches output.

- [ ] **Step 2: Run deployment tests and record RED evidence**

Run:

```bash
sh scripts/run-in-test-env.sh .venv/bin/pytest -q -m operational tests/deployment/test_deployment_scripts.py
```

Expected: ordering assertions fail because migration and gallery projection preparation currently
occur after or during candidate container reconciliation.

- [ ] **Step 3: Implement worker-paused projection preparation**

Add a narrowly named helper that stops existing `worker` service containers without disabling
processing configuration or touching import/commerce workers. Mark the deployment mutated before
the stop so existing recovery restarts the prior worker topology on any failure. With the requested
candidate image and environment, run migrate, rebuild apply, and clean verification before candidate
Compose reconciliation. Keep old web and edge containers running throughout preparation.

- [ ] **Step 4: Add two bounded candidate read smokes**

Implement `smoke_gallery_media_projection`. It selects the published, site-visible event with the
largest photo count; when none exists it returns an explicit skipped result. Otherwise it renders
page one through the real gallery path and resolves one eligible photo through
`public_gallery_photo()`. Capture aggregate elapsed time and sanitized plan node names only, fail on
a non-200 render or any processing relation name, and emit no photo ID, filename, key, attempt ID,
or customer field. Run it after candidate local health with:

```bash
python manage.py smoke_gallery_media_projection
```

Do not add absolute timing gates.

- [ ] **Step 5: Preserve rollback and deployment marker contracts**

Ensure every new failure uses the existing `fail` path, restores the prior package/environment, and
reconciles the previous processing worker topology. If `projection-preflight` moves earlier, update
the three validated phase allow-lists and their tests consistently; do not add an unvalidated raw
status channel.

- [ ] **Step 6: Record the accepted architecture and implemented state**

Create ADR 0037 from `docs/adr/0000-template.md` with status Accepted, the selected synchronous
separate projection, rejected `Photo` fields and materialized view, atomicity, rebuild, and
worker-paused cutover consequences. Add it to the ADR index. Update architecture and engineering
jobs with repository evidence only; mark CI, merge, deployment, and live outcome separately until
each exists.

- [ ] **Step 7: Run GREEN operational and documentation checks**

Run:

```bash
sh scripts/run-in-test-env.sh .venv/bin/pytest -q -m operational tests/deployment/test_deployment_scripts.py
make test TESTS="src/backend/picflow/tests/test_gallery_media_projection_smoke.py"
.venv/bin/pre-commit run --files tests/deployment/test_deployment_scripts.py src/backend/picflow/management/commands/smoke_gallery_media_projection.py src/backend/picflow/tests/test_gallery_media_projection_smoke.py
git diff --check
```

Run the suite selector for the complete Task 4 package and record its exact output and fingerprint.
After independent approval, the root controller commits only Task 4 files.

---

## Final branch verification and delivery

- [ ] Rebase the branch on current `origin/main` and resolve only task-owned conflicts.
- [ ] Rerun `scripts/select_test_suites.py` against the final base/head package and capture the final
  whole-package fingerprint.
- [ ] Run `make check` once on that exact fingerprint.
- [ ] Run every selector-required expensive target, expected to include `make test-operational` and
  `make test-migrations`; run the migration immutability check using the actual PR base/head.
- [ ] Run `git diff --check`, inspect the full branch diff, and obtain a final whole-branch review.
- [ ] Consolidate any review fixes through the same task implementer/reviewer loop; never amend
  accepted ADR content after it is committed.
- [ ] Push `codex/gallery-media-projection-design`, open a PR with the problem evidence, design,
  tests, migration/cutover risk, and rollback contract, then enable GitHub auto-merge.
- [ ] Watch every required CI job to green and investigate failures from evidence before changing
  code.
- [ ] Confirm the PR merged and the production deployment workflow ran for the merge SHA.
- [ ] Verify on the canonical VM: deployed image SHA, container and worker health, clean projection,
  public gallery response, exact media response, fresh errors, query fingerprints, and processing
  table counters during natural traffic.
- [ ] Compare post-deploy latency and processing-table reads with the 2026-09-15 incident snapshot;
  report implementation, CI, merge, deployment, and live verification as separate states.
