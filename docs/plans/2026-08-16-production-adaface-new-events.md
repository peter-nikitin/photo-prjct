# Production AdaFace for New Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$execute-implementation-plan` to implement this plan task-by-task.

**Goal:** Make SCRFD plus AdaFace the immutable face-search generation for newly created events while preserving existing events on SFace without a backfill.

**Architecture:** Persist one generation selector on `Event`. Resolve gallery enrollment, selfie configuration and ranking compatibility from that selector rather than global model settings. Keep both recognizers available in the production worker because old and new events coexist.

**Tech Stack:** Django 6, PostgreSQL, Django migrations, Docker Compose, Python worker, pytest.

## Global constraints

- Approved specification: [`../superpowers/specs/2026-08-16-production-adaface-new-events-design.md`](../superpowers/specs/2026-08-16-production-adaface-new-events-design.md).
- Existing events remain SFace v3; new events default to AdaFace v5.
- AdaFace distance threshold is `0.42`; SFace remains `0.363`.
- No existing-event replay or production backfill.
- No SFace/AdaFace fallback or mixed vector comparison.
- Ordinary selfie media and query-vector privacy semantics remain unchanged.
- ADR impact: conforms to ADR 0017 and ADR 0019; no new ADR.

---

### Task 1: Persist and resolve the event generation

**Deliverable:** Existing rows migrate to SFace and newly constructed events default to AdaFace.

**Files:**
- Modify: `src/backend/picflow/models.py`
- Create: `src/backend/picflow/migrations/0010_event_face_search_generation.py`
- Modify: `src/backend/picflow/tests/test_models.py`
- Modify: `src/backend/processing/services/face_quality.py`
- Modify: `src/backend/processing/tests/test_face_quality_activation.py`

**Interface:** `Event.face_search_generation` with choices `sface_v3` and `adaface_v5`; `active_face_embedding_generations(event)` returns the persisted generation when no explicit historical activation exists.

- [ ] Add failing model/migration tests proving existing rows become `sface_v3` and new rows default to `adaface_v5`.
- [ ] Run the focused tests and observe the missing-field/default failures.
- [ ] Add the field and a single migration whose database default preserves existing rows before the model default becomes AdaFace.
- [ ] Add resolver tests for an old SFace event and new AdaFace event, then implement the minimal event-aware resolver.
- [ ] Run `make test TESTS="src/backend/picflow/tests/test_models.py src/backend/processing/tests/test_face_quality_activation.py"`; expect all selected tests to pass.

### Task 2: Route enrollment and selfie search by event generation

**Deliverable:** New-event previews enqueue v5 and their selfie queries use AdaFace 512D/0.42; old events continue v3 and SFace 128D/0.363.

**Files:**
- Modify: `src/backend/processing/services/enrollment.py`
- Modify: `src/backend/processing/services/previews.py`
- Modify: `src/backend/processing/services/jobs.py`
- Modify: `src/backend/processing/views.py`
- Modify: `src/backend/selfie_search/services/submission.py`
- Modify: `src/backend/selfie_search/services/jobs.py`
- Modify: `src/backend/config/settings.py`
- Modify focused tests under `src/backend/processing/tests/` and `src/backend/selfie_search/tests/`.

**Interface:** `request_face_embedding_enqueue(photo)` selects v3 or v5 from `photo.event.face_search_generation`; selfie configuration derives model, dimensions and threshold from the frozen gallery generation, not global model settings.

- [ ] Add failing enrollment tests for v3 old-event and v5 new-event jobs.
- [ ] Implement event-aware preview enrollment without changing original-backed legacy behavior.
- [ ] Add failing selfie submission/completion tests for both models, exact dimensions and thresholds, including rejection of mixed projections.
- [ ] Implement generation-derived selfie configuration and retain the current ephemeral-query cleanup boundary.
- [ ] Run `make test TESTS="src/backend/processing/tests/test_enrollment.py src/backend/processing/tests/test_previews.py src/backend/processing/tests/test_jobs.py src/backend/processing/tests/test_views.py src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_jobs.py"`; expect all selected tests to pass.

### Task 3: Ship both production worker paths and reconcile documentation

**Deliverable:** Production worker claims v3, v5 and selfie v2; deployment defaults support both event generations and architecture states the new-event default accurately.

**Files:**
- Modify: `docker-compose.prod.yml`
- Modify: `.env.example`
- Modify: `deploy/apply-deployment.sh` if its identity contract is explicit.
- Modify: `tests/deployment/test_deployment_scripts.py`
- Modify: `tests/processing/test_worker_container_contract.py`
- Modify: `tests/processing/test_pipeline_e2e.py`
- Modify: `tests/processing/test_selfie_search_e2e.py`
- Modify: `docs/architecture.md`
- Modify: `README.md` and `docs/local-photo-processing-check.md` only where their production defaults are stated.

- [ ] Add failing Compose/deployment tests for exact production identities `2/face_embedding/3,3/face_embedding/5,1/selfie_query/2` alongside existing non-face processors and absence of local-only gates.
- [ ] Update the production runtime wiring and ensure the worker can load both SFace and AdaFace artifacts through its existing configuration-dispatch contract.
- [ ] Add/adjust end-to-end tests proving old-event SFace and new-event AdaFace paths coexist without cross-model ranking.
- [ ] Update architecture facts and rollout/rollback wording; state explicitly that no backfill occurs.
- [ ] Run the focused deployment and E2E tests; expect all selected tests to pass.
- [ ] Run `make check`, `git diff --check`, `python manage.py makemigrations --check --dry-run` through project entrypoints; expect zero failures and no migration drift.
- [ ] Reconcile the final behavior with ADR 0017 and ADR 0019 and record conformance in PR #137.

## Rollout

1. Merge and deploy web plus worker images together.
2. Apply migration `0010`; existing events remain `sface_v3`.
3. Confirm the worker advertises both gallery generations and selfie v2.
4. Create the new event; verify it is pinned to `adaface_v5` before upload.
5. Upload photos and verify v5 jobs/projections and one privacy-safe selfie search.

## Rollback

Revert the new-event default for subsequently created events and keep both worker identities while any AdaFace event exists. Do not reinterpret existing AdaFace events or fall back their searches to SFace.
