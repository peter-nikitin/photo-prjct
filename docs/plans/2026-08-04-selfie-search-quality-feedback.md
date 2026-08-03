# Selfie Search Quality Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`
> task-by-task. Follow repository `AGENTS.md`: implementers and reviewers do not modify the Git
> index, commits, branches, tags, or remotes; the root controller creates the single final commit.

- Date: 2026-08-04
- Status: Approved
- Owner: project maintainer
- Related specification:
  [`2026-08-04-selfie-search-quality-feedback-design.md`](../superpowers/specs/2026-08-04-selfie-search-quality-feedback-design.md)
- Related product job:
  [`PJ-012 — Customer — Report selfie-search quality`](../product-jobs.md#pj-012--customer--report-selfie-search-quality)
- Related architecture:
  [`docs/architecture.md`](../architecture.md), accepted public selfie-search and biometric
  feedback boundaries
- Related ADRs:
  [ADR 0001](../adr/0001-django-modular-monolith.md),
  [ADR 0002](../adr/0002-postgresql-system-of-record.md),
  [ADR 0006](../adr/0006-yandex-object-storage-media.md),
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md), and
  [ADR 0023](../adr/0023-store-consented-selfie-search-feedback.md)
- ADR impact: Conforms to accepted ADR 0023 and preserves ADR 0019.

**Goal:** Implement one consented quality-feedback record per terminal selfie search, including
same-browser selfie reuse, compact failed/empty feedback, optional per-result marking, restricted
staff inspection, and lifecycle-bound private feedback media.

**Architecture:** Keep the search pipeline unchanged. Browser code retains the selected file in
IndexedDB for at most seven days and uploads it only with explicit feedback consent. Django owns
validation and immutable feedback state in PostgreSQL; a dedicated private KMS-encrypted bucket owns
feedback-selfie bytes and deletes them through a 30-day lifecycle.

**Tech stack:** Django 5.2, PostgreSQL 16, boto3/S3-compatible Yandex Object Storage, Yandex KMS
bucket encryption, browser IndexedDB/sessionStorage/localStorage, Node 22 `node:test`, and
Playwright visual tests.

## Global constraints

- Implement the approved specification without a manual feedback file picker, free-form comment,
  rating, `Не уверен`, second thumbnail grid, cross-search feedback, or ranking/model mutation.
- Preserve ADR 0019: the search pipeline selfie is deleted before terminal publication and the
  query embedding is never persisted.
- One `SelfieSearch` accepts at most one immutable feedback; every label targets a currently visible
  saved `SelfieSearchResult` belonging to that search.
- Stored feedback requires `personal_data_consent = true`, consent text version
  `2026-08-04`, and an acceptance timestamp. The checkbox is initially unchecked.
- Contact is required plaintext of at most 254 characters. It is absent from logs, indexes beyond
  ordinary relational keys, admin lists, search, sorting, and exports.
- Feedback-local browser bytes expire after seven days, are deleted after successful submission or
  browser-wide opt-out, and are never required for the ordinary search to work.
- Opt-out uses exactly `findme_selfie_feedback_prompt=disabled:2026-08-04` in `localStorage` and
  suppresses future prompts and future feedback-local selfie preservation in that profile.
- The dedicated feedback bucket is private, KMS-encrypted, unversioned, unlocked, and governed by
  an authoritative 30-day lifecycle. No application cleanup scheduler is added.
- Feedback object keys and metadata contain no contact, filename, event slug, bearer token, or
  other customer-provided identifier.
- Public media routes and the ML worker receive no feedback bucket access. Sensitive admin access
  requires the dedicated permission and an append-only audit row.
- Subagents work sequentially in the shared worktree, perform strict red-green TDD, leave changes
  unstaged, and must not spawn other agents. Root review gates each task before the next begins.

## Scope

Implements the approved specification without scope changes. Environment activation remains gated
on the published personal-data policy being confirmed to cover the accepted purpose and retention
behavior, plus real feedback-bucket lifecycle/KMS/privacy preflight.

## Acceptance criteria

The specification's 18 acceptance criteria are authoritative. Delivery additionally requires all
new migrations to apply from the current head, no migration drift, focused server/JavaScript/
deployment/visual suites, full CI-equivalent checks, and a disabled-by-default deployment that does
not expose feedback credentials to the worker.

## Implementation

### Task 1: Add the immutable feedback, label, and access-audit schema

**Files:**

- Modify: `src/backend/selfie_search/models.py`
- Create: `src/backend/selfie_search/migrations/0002_selfiesearchfeedback_and_more.py`
- Modify: `src/backend/selfie_search/tests/test_models.py`
- Modify: `src/backend/selfie_search/tests/test_migrations.py`

**Specification:** Data model and invariants; Contact and consent; Private media and staff access.

**Depends on:** Accepted ADR 0023.

**Produces:**

- `SelfieSearchFeedback` with UUID primary key, one-to-one protected `search`, variant choices
  `problem`/`result_labels`, required plaintext `contact`, required
  `personal_data_consent`, `consent_text_version`, `consented_at`, source status/count/configuration
  snapshot, feedback object metadata, and creation timestamp.
- `SelfieSearchFeedbackLabel` with UUID primary key, protected feedback and result references,
  `present`/`absent` value, and unique `(feedback, result)` membership.
- `SelfieSearchFeedbackAccessAudit` with UUID primary key, protected feedback/staff references,
  `contact_view`/`selfie_view` action, and timestamp.
- Database constraints for one feedback per search, consent always true, valid choices, unique
  labels, and custom `selfie_search.view_sensitive_feedback` permission.

- [ ] Add focused model and migration tests first. Prove the tests fail because the three models,
  constraints, and permission do not exist; include rejection of false consent, duplicate feedback,
  duplicate labels, cross-search label validation, and mutation after creation.
- [ ] Run `.venv/bin/pytest -q src/backend/selfie_search/tests/test_models.py src/backend/selfie_search/tests/test_migrations.py`; expected RED is missing model/migration behavior, not fixture failure.
- [ ] Implement the minimal models and generated migration. Keep contact unindexed and omit it from
  `__str__`; use model/service validation plus database constraints for invariants representable in
  SQL.
- [ ] Re-run the same command; expected GREEN is all selected tests passing.
- [ ] Run `.venv/bin/python src/backend/manage.py makemigrations --check --dry-run`; expected output
  is `No changes detected`.

### Task 2: Add the dedicated feedback-selfie storage and fail-closed settings

**Files:**

- Modify: `src/backend/config/settings.py`
- Modify: `src/backend/selfie_search/apps.py`
- Modify: `src/backend/selfie_search/storage.py`
- Modify: `src/backend/selfie_search/tests/test_settings.py`
- Modify: `src/backend/selfie_search/tests/test_storage.py`

**Specification:** Private media and staff access; Submission validation; Failure semantics.

**Depends on:** Task 1 object-metadata fields.

**Produces:**

- Disabled-by-default `SELFIE_FEEDBACK_ENABLED` and dedicated bucket/access-key settings available
  only to the web service; exact maximum upload of 20 MiB, 60-second staff grant TTL, required KMS
  key ID, and dependency on `SELFIE_SEARCH_ENABLED`.
- `FeedbackSelfieStorage.put()`, `.delete()`, `.inspect()`, and
  `.create_download_grant()` using a random 32-hex object key, the dedicated bucket, private ACL,
  canonical JPEG/PNG content types, bounded size, and sanitized `StorageUnavailable`/
  `ObjectMissing` behavior.

- [ ] Add settings and storage tests first for disabled defaults, incomplete/unsafe enabled
  configuration, search dependency, exact bucket selection, random-key validation, private PUT,
  bounded content, 60-second grant, missing object, and sanitized SDK failures.
- [ ] Run `.venv/bin/pytest -q src/backend/selfie_search/tests/test_settings.py src/backend/selfie_search/tests/test_storage.py`; expected RED is missing feedback settings/storage APIs.
- [ ] Implement the smallest separate adapter without changing `TemporarySelfieStorage` or sharing
  its bucket/key validator.
- [ ] Re-run the focused command; expected GREEN is all selected tests passing.
- [ ] Run `.venv/bin/python src/backend/manage.py check --tag selfie_search`; expected exit zero for
  disabled feedback defaults and deterministic errors for each invalid enabled configuration in
  the tests.

### Task 3: Implement transactional feedback validation and the public POST endpoint

**Files:**

- Modify: `src/backend/selfie_search/forms.py`
- Create: `src/backend/selfie_search/services/feedback.py`
- Modify: `src/backend/selfie_search/services/results.py`
- Modify: `src/backend/selfie_search/views.py`
- Modify: `src/backend/selfie_search/urls.py`
- Create: `src/backend/selfie_search/tests/test_feedback.py`
- Modify: `src/backend/selfie_search/tests/test_forms.py`
- Modify: `src/backend/selfie_search/tests/test_results.py`
- Modify: `src/backend/selfie_search/tests/test_views.py`

**Specification:** Feedback eligibility and variants; Compact problem report; Result-marking
feedback; Contact and consent; Submission flow; Failure semantics.

**Depends on:** Tasks 1 and 2.

**Produces:**

- Shared image validation reused by search and feedback uploads without changing the accepted
  search input contract.
- `FeedbackSubmissionForm` accepting multipart `selfie`, `contact`,
  `personal_data_consent`, and a bounded JSON `labels` mapping saved-result UUIDs to
  `present`/`absent`; consent version comes from the server constant, not customer input.
- `feedback_presentation(search)` returning authoritative `problem` or `result_labels`, current
  visible result count, and current eligible saved-result IDs.
- `submit_search_feedback(...)` that checks existing feedback before upload, revalidates terminal
  state/variant/membership inside one transaction, uploads one object, creates immutable rows, and
  exact-deletes a just-uploaded object after database failure.
- CSRF-protected POST route
  `/events/<event-slug>/selfie-search/<public-token>/feedback/` returning `201 submitted`,
  idempotent `200 already_submitted`, `422 invalid`, `409 result_changed/non_terminal`, `404` for an
  invalid bearer result, and `503` for storage unavailability; responses contain no sensitive
  detail.

- [ ] Add failing form/service/result/view tests first for every response class, failed/empty/ready
  variant selection, zero/some/all labels, cross-search/ineligible/duplicate labels, false consent,
  contact bounds, corrupt/oversize image, CSRF, one-to-one retry, concurrent uniqueness recovery,
  storage failure, compensating delete, and absence of sensitive logs.
- [ ] Run `.venv/bin/pytest -q src/backend/selfie_search/tests/test_forms.py src/backend/selfie_search/tests/test_feedback.py src/backend/selfie_search/tests/test_results.py src/backend/selfie_search/tests/test_views.py`; expected RED is missing feedback form/service/route.
- [ ] Implement the minimal form, service, result helper, URL, and view. Lock or recover from the
  one-to-one race without introducing a second feedback state machine; reject a changed result as a
  whole instead of silently dropping labels.
- [ ] Re-run the focused command; expected GREEN is all selected tests passing.
- [ ] Run `.venv/bin/pytest -q src/backend/selfie_search/tests`; expected GREEN is the complete app
  suite passing with the original search cleanup, ranking, pagination, and media authorization
  tests unchanged.

### Task 4: Preserve the selected file locally and implement browser-wide opt-out

**Files:**

- Modify: `src/backend/templates/catalog/event_detail.html`
- Modify: `src/backend/selfie_search/templates/selfie_search/result.html`
- Modify: `src/backend/static/ui/selfie-search.js`
- Modify: `tests/js/selfie-search.test.js`
- Modify: `src/backend/selfie_search/tests/test_views.py`

**Specification:** Preserve the selected selfie locally; Explicit browser opt-out; Feedback
eligibility and variants.

**Depends on:** Task 3 result context and feedback endpoint.

**Produces:**

- A browser-storage adapter with injectable IndexedDB/sessionStorage/localStorage/time seams for
  Node tests.
- Search submission behavior that, when opt-out is absent, stores the exact selected file under a
  random local handle before allowing the ordinary POST, records only bounded canonical metadata,
  and falls back to the unchanged search when storage fails.
- Result-page association of the tab's pending handle to the current result digest; seven-day
  expiry and opportunistic cleanup.
- Exact browser-wide opt-out behavior and current-page error handling from the specification.
- Server markup that loads `selfie-search.js` on both polling and terminal pages while keeping the
  existing poller single-instanced.

- [ ] Add Node and Django markup tests first for successful preservation/association, simultaneous
  tab isolation, seven-day cleanup, successful-feedback cleanup hook, active opt-out, read failure,
  write failure, absence of future preservation, and search submission continuing after every
  storage error.
- [ ] Run `npm run test:js` and
  `.venv/bin/pytest -q src/backend/selfie_search/tests/test_views.py`; expected RED is missing
  storage/opt-out interfaces and terminal script markup.
- [ ] Implement browser storage behind small exported functions/classes; never place selfie bytes
  in localStorage/sessionStorage and never send an analytics event for opt-out.
- [ ] Re-run both commands; expected GREEN is all JavaScript and selected Django tests passing.

### Task 5: Build the compact form and in-gallery marking mode

**Files:**

- Modify: `src/backend/selfie_search/views.py`
- Modify: `src/backend/selfie_search/templates/selfie_search/result.html`
- Modify: `src/backend/static/ui/selfie-search.js`
- Modify: `src/backend/static/ui/selfie-search.css`
- Modify: `src/backend/static/ui/catalog.css`
- Modify: `tests/js/selfie-search.test.js`
- Modify: `tests/js/event-gallery.test.js`
- Modify: `src/backend/selfie_search/tests/test_views.py`
- Modify: `tests/visual/views.py`
- Modify: `tests/visual/urls.py`
- Modify: `tests/visual/visual.spec.js`
- Create/update: feedback desktop/mobile snapshots under
  `tests/visual/visual.spec.js-snapshots/`

**Specification:** Compact problem report; Result-marking feedback; Per-photo controls; Progress
and optional completion; Contact and consent.

**Depends on:** Tasks 3 and 4.

**Produces:**

- Terminal invitation hidden when feedback is disabled, already submitted, opted out, or missing
  its local selfie.
- Compact problem form for failed/zero-visible-result searches and marking form for non-empty ready
  results, with exact disclosure, required contact, required policy-linked consent, opt-out action,
  progress text, and no manual file input.
- Existing result cards carry authorized saved-result IDs and gain initially unselected lower-left
  `Я есть`/`Меня нет` pressed buttons only in marking mode, opposite the lower-right download.
- Session-scoped marks keyed by result digest across numbered page navigation; authoritative total
  count from Django; multipart `fetch` submission using the associated IndexedDB file and CSRF.

- [ ] Add failing JS, Django markup, interaction, accessibility, and visual tests first. Cover
  failed/empty/ready variants, `0 из M`, toggling/clearing, cross-page restoration, no second grid,
  button events not opening GLightbox/download, exact consent link/copy, field-error preservation,
  changed-result reload, successful confirmation, mobile geometry, and ordinary browsing outside
  marking mode.
- [ ] Run `npm run test:js` and
  `.venv/bin/pytest -q src/backend/selfie_search/tests/test_views.py`; expected RED is missing form,
  card controls, state, and submission behavior.
- [ ] Implement the smallest server-rendered form/card markup and progressive enhancement. Reuse
  existing gallery cards and button visual language; do not add a modal framework or duplicate
  gallery component.
- [ ] Re-run the focused JavaScript/Django commands; expected GREEN is all selected tests passing.
- [ ] Run `npm run test:visual:update`, inspect every changed desktop/mobile snapshot, then run
  `npm run test:visual`; expected GREEN is all Playwright checks passing with only intentional
  feedback snapshots changed.

### Task 6: Add restricted, audited staff inspection

**Files:**

- Create: `src/backend/selfie_search/admin.py`
- Create: `src/backend/selfie_search/tests/test_admin.py`
- Modify: `src/backend/selfie_search/tests/test_models.py`
- Modify: `src/backend/selfie_search/storage.py`

**Specification:** Private media and staff access; Data model and invariants; Failure semantics.

**Depends on:** Tasks 1 and 2.

**Produces:**

- Read-only feedback admin list/detail excluding contact and object key from list/search/filter/
  export surfaces.
- CSRF-protected explicit contact-view and selfie-view POST actions requiring both staff status,
  ordinary model view permission, and `selfie_search.view_sensitive_feedback`.
- Append-only audit row created only for a successful sensitive action, containing staff identity,
  feedback UUID, action, and timestamp.
- Selfie action issuing at most a 60-second exact-object redirect; expired/missing lifecycle object
  renders `Селфи удалено`, while storage unavailability renders a retryable sanitized error.

- [ ] Add failing admin tests first for anonymous/non-staff/staff-without-permission denial,
  authorized contact reveal, authorized selfie grant, CSRF, audit content, list/search redaction,
  missing expired object, storage error, and immutable admin behavior.
- [ ] Run `.venv/bin/pytest -q src/backend/selfie_search/tests/test_admin.py src/backend/selfie_search/tests/test_models.py`; expected RED is missing admin registration/actions.
- [ ] Implement the minimum admin/views/storage calls and append-only audit behavior; never place
  contact, key, signed URL, or exception detail in messages/logs/audit.
- [ ] Re-run the focused command; expected GREEN is all selected tests passing.

### Task 7: Provision and verify the feedback bucket contract in deployment code

**Files:**

- Create: `src/backend/selfie_search/feedback_lifecycle.py`
- Create: `src/backend/selfie_search/management/commands/configure_selfie_feedback_lifecycle.py`
- Create: `src/backend/selfie_search/management/commands/verify_selfie_feedback_storage.py`
- Create: `src/backend/selfie_search/tests/test_feedback_lifecycle_configuration.py`
- Create: `src/backend/selfie_search/tests/test_configure_feedback_lifecycle_command.py`
- Create: `src/backend/selfie_search/tests/test_feedback_storage_contract_command.py`
- Modify: `deploy/apply-deployment.sh`
- Modify: `.github/workflows/deploy.yml`
- Modify: `tests/deployment/test_deployment_scripts.py`
- Modify: `docker-compose.prod.yml` only if a test proves feedback credentials would otherwise enter
  the worker environment

**Specification:** Private media and staff access; Failure semantics; Delivery boundary.

**Depends on:** Task 2 settings/storage adapter.

**Produces:**

- Deterministic exact 30-day whole-bucket lifecycle document with collision-safe readback/recovery
  and explicit mutation confirmation.
- Real-storage preflight that verifies the exact bucket, no versioning, no Object Lock, default
  `aws:kms` encryption with the configured KMS key ID, no anonymous read/list, lifecycle, and one
  generated private put/head/60-second-grant/delete cycle with sanitized markers.
- Deployment propagation for `SELFIE_FEEDBACK_ENABLED`, dedicated web-only bucket credentials,
  bucket/KMS identifiers, and an explicit workflow-dispatch preflight. The feature remains disabled
  unless all required values and the confirmed preflight are present.

- [ ] Add failing lifecycle/command/deployment tests first for exact rule shape, wrong bucket/KMS,
  versioning/Object Lock/public access, mutation confirmation/digest, readback recovery, scratch
  cleanup, sanitized output, disabled defaults, deploy ordering, and worker credential absence.
- [ ] Run `.venv/bin/pytest -q src/backend/selfie_search/tests/test_feedback_lifecycle_configuration.py src/backend/selfie_search/tests/test_configure_feedback_lifecycle_command.py src/backend/selfie_search/tests/test_feedback_storage_contract_command.py tests/deployment/test_deployment_scripts.py`; expected RED is missing commands and deploy variables.
- [ ] Implement the lifecycle builder, guarded commands, and deployment wiring by following the
  existing selfie-search lifecycle patterns without modifying the existing private-media bucket
  rules.
- [ ] Re-run the focused command; expected GREEN is all selected tests passing.
- [ ] Run `.venv/bin/python src/backend/manage.py check`; expected exit zero with feedback disabled.

### Task 8: Reconcile delivered evidence and run the complete release gate

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/product-jobs.md`
- Modify: `docs/engineering-jobs.md` only when the operational contract has automated or live
  evidence appropriate to an engineering job
- Modify: `README.md` only for exact operator commands that were implemented and verified
- Modify: the approved specification, ADR 0023, or plan only for factual link/status corrections;
  do not rewrite accepted decisions

**Specification:** All sections and acceptance criteria.

**Depends on:** Tasks 1–7 and clean task reviews.

**Produces:** Verified branch evidence, reconciled architecture/product status, and one root-owned
final commit after independent whole-branch review.

- [ ] Run focused server tests:
  `.venv/bin/pytest -q src/backend/selfie_search/tests tests/deployment/test_deployment_scripts.py`;
  expected GREEN is all selected tests passing.
- [ ] Run `npm run test:js`; expected GREEN is all Node tests passing.
- [ ] Run `npm run test:visual`; expected GREEN is all Playwright tests passing with inspected
  snapshots.
- [ ] Run CI-equivalent checks without overlapping pytest processes:
  `ruff format --check .`, `ruff check .`, `mypy`,
  `pytest --cov --cov-report=term-missing`,
  `python src/backend/manage.py check`, and
  `python src/backend/manage.py makemigrations --check --dry-run`; expected GREEN is zero failures,
  branch coverage at or above the repository threshold, and no migration drift.
- [ ] Compare the complete diff with the approved specification, ADRs 0019/0023, and architecture.
  Update `PJ-012` to `In progress` only after implementation has begun, to `Delivered` only after
  the customer path is enabled in an environment, and never to `Validated` without real customer-
  outcome evidence.
- [ ] Create one working-tree review package containing every task file, including untracked files;
  obtain independent whole-branch approval, apply at most one consolidated fix wave, and rerun the
  affected focused checks plus the full release gate.
- [ ] Root controller stages exactly the approved task files and creates one final implementation
  commit. Subagents must not stage or commit.

## Verification

Run in this order from the worktree with the project `.venv` and Node 22:

1. `.venv/bin/pytest -q src/backend/selfie_search/tests tests/deployment/test_deployment_scripts.py`
   — feedback/search/deployment focused suite passes.
2. `npm run test:js` — browser unit suite passes.
3. `npm run test:visual` — containerized Playwright suite and approved snapshots pass.
4. `ruff format --check .` — no formatting changes required.
5. `ruff check .` — no lint findings.
6. `mypy` — configured 131+ source files type-check without errors.
7. `pytest --cov --cov-report=term-missing` — complete Python suite passes at or above 75% branch
   coverage. Do not run this concurrently with another full pytest suite.
8. `python src/backend/manage.py check` — Django system checks pass.
9. `python src/backend/manage.py makemigrations --check --dry-run` — no migration drift.
10. `git diff --check` before staging and `git diff --cached --check` after root staging — no
    whitespace errors.

## Operational impact and rollout

1. Merge and deploy database/application code with `SELFIE_FEEDBACK_ENABLED=False`; apply the new
   migration and verify health, ordinary selfie search, worker processing, media authorization, and
   logs remain unchanged.
2. Confirm the published personal-data policy covers feedback selfie processing, durable plaintext
   contact/labels, explicit consent evidence, staff access, and lifecycle deletion. Keep feedback
   disabled if this content gate is not satisfied.
3. With `manage-yandex-cloud`, create a dedicated private unversioned/unlocked bucket, dedicated KMS
   key, and least-privilege web-only service account/credentials. Do not change the current private
   originals/search bucket or worker identity.
4. Apply the reviewed 30-day lifecycle with the guarded management command and exact approved
   bucket digest. On mismatch or recovery failure, stop and leave feedback disabled.
5. Configure staging GitHub environment vars/secrets, run the explicit real-storage preflight, and
   require all versioning/Object Lock/KMS/public-denial/lifecycle/scratch markers.
6. Enable feedback only in staging, deploy the same immutable image, and run one failed/empty report
   plus one non-empty partial marking. Confirm one row/search, consent columns, local cleanup,
   staff audit, private selfie grant, worker credential absence, and unchanged search cleanup.
7. Review health/logs and the bucket object metadata. Production activation is a separate explicit
   decision after staging evidence; this plan does not silently enable production.

## Rollback

- Set `SELFIE_FEEDBACK_ENABLED=False` and redeploy; ordinary selfie search remains active.
- Keep the feedback bucket lifecycle enabled so existing selfies expire. Do not delete the KMS key
  while any feedback selfie may remain.
- Existing feedback/contact/consent/label rows remain restricted and readable to authorized staff;
  rollback does not destroy accepted customer feedback.
- Remove staging feedback credentials only after the feature is disabled and no rollback inspection
  requires them. The worker has no feedback credentials to revoke.
- Revert application code only after confirming the database migration can remain harmlessly
  applied; no reverse migration is required for rollback.

## Architecture and ADR reconciliation

- Final implemented behavior must conform to ADR 0023 and preserve ADR 0019.
- `docs/architecture.md` moves the feedback paragraph from accepted design/not implemented to
  implemented only after code and environment evidence support that statement.
- Any change to selfie lifecycle, contact expiry/encryption, staff authorization, cross-search
  reuse, automated training, or worker access stops execution for a new/superseding ADR decision.

## Open questions

None. Environment activation has explicit policy and storage-preflight gates rather than unresolved
implementation choices.
