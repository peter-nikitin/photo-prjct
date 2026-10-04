# Zero-downtime Django deployment implementation plan

- Date: 2026-10-04
- Status: Approved by maintainer on 2026-10-04
- Owner: project maintainer
- Related specification: [Zero-downtime Django releases](../superpowers/specs/2026-10-04-zero-downtime-django-deployment-design.md)
- Related architecture: [Current architecture — implemented](../architecture.md#current-architecture--implemented)
- Related ADRs: [0003](../adr/0003-docker-compose-yandex-cloud.md), [0007](../adr/0007-nginx-certbot-https-edge.md), [0011](../adr/0011-use-minimal-shared-https-rollout.md), [0028](../adr/0028-operate-one-canonical-deployment.md), [0051](../adr/0051-release-photo-worker-images-independently.md), [0053](../adr/0053-reconcile-observability-independently-on-main.md)
- ADR impact: Conforms to the cited accepted ADRs; no new environment, cloud resource or release authority.

## Goal, scope and acceptance

Implement the approved specification's [outcome and scope](../superpowers/specs/2026-10-04-zero-downtime-django-deployment-design.md#outcome-and-scope) and [acceptance criteria](../superpowers/specs/2026-10-04-zero-downtime-django-deployment-design.md#acceptance-criteria). Scope delta: none. The first activation must migrate the existing single `web` container while it serves traffic; future ordinary web releases alternate `web` and `web-next` without a VM, PostgreSQL or Nginx restart. No VM resize or worker-image change.

The project `$execute-implementation-plan` skill controls implementer/reviewer dispatch, Git ownership, verification fingerprints, final commit and PR handoff. The same draft PR carries this plan and the implementation after review.

## Worker/state/artifact release safeguards

Not applicable: this plan changes neither the photo-worker claim/result contract nor durable processing rows or generated customer-photo artifacts. The local import worker's endpoint moves behind the existing private Nginx edge without changing its request/response contract. Static build assets have their own overlap check in Task 2.

## Implementation

### Task 1: Make Django slots start without database mutation

**Files:** `src/backend/entrypoint.sh`, `src/backend/local-entrypoint.sh` (new), `docker-compose.yml`, `docker-compose.deployment.yml`, `deploy/apply-deployment.sh`, `tests/deployment/test_deployment_scripts.py`, `tests/deployment/test_import_deployment.py`, `tests/deployment/test_local_web.py`, `tests/processing/test_worker_container_contract.py`, `tests/processing/test_import_worker_container_contract.py`, `tests/deployment/test_commerce_deployment_compose.py`, `src/backend/ingestion/tests/test_bootstrap_group.py`, `README.md`.

- **Specification:** [Selected design](../superpowers/specs/2026-10-04-zero-downtime-django-deployment-design.md#selected-design), [Shared database and compatibility contract](../superpowers/specs/2026-10-04-zero-downtime-django-deployment-design.md#shared-database-and-compatibility-contract).
- **Depends on:** none.
- **Produces:** `web` and `web-next` are otherwise equivalent deployment Compose services; starting either runs Gunicorn without migration, feature synchronization, group bootstrap or other database mutation. The deploy entrypoint performs that setup once before candidate start. Ordinary local Compose keeps its existing database/static preparation through an explicit local-only startup path.

- [ ] Write focused failing tests: both slot services render with equivalent private application settings and distinct service names; deployment entrypoint startup does not invoke mutating Django commands; the existing deploy path performs release setup exactly once before candidate start; ordinary local Compose still prepares migrations, feature definitions, photographer group and static files before local web starts.
- [ ] Run `sh scripts/run-in-test-env.sh .venv/bin/pytest -q -m operational tests/deployment/test_deployment_scripts.py tests/deployment/test_import_deployment.py`; record the expected focused failures.
- [ ] Implement the smallest Compose/entrypoint/deploy changes. Keep the currently serving `web` container untouched while adding `web-next`; do not reconcile either slot through an all-service `compose up` during preparation.
- [ ] Re-run the same focused command and the three Compose consumer contract files GREEN. Confirm first-activation and already-two-slot cases in the fake deployment harness.

### Task 2: Route all Django traffic to one selected slot, retaining static assets

**Files:** `deploy/nginx/https.conf.template`, `deploy/nginx/private-worker.conf.template`, `deploy/nginx/reload-nginx.sh`, `docker-compose.deployment.yml`, `deploy/web-slot.py` (new), `tests/deployment/test_deployment_scripts.py`, `tests/deployment/test_import_deployment.py`, `tests/deployment/test_worker_pool_transport.py`, `tests/processing/test_import_worker_container_contract.py`, `tests/deployment/validate-nginx.sh`.

- **Specification:** [Selected design](../superpowers/specs/2026-10-04-zero-downtime-django-deployment-design.md#selected-design), including public/private/import routes and static files; [Failure and recovery semantics](../superpowers/specs/2026-10-04-zero-downtime-django-deployment-design.md#failure-and-recovery-semantics).
- **Depends on:** Task 1 provides the two slot names.
- **Produces:** a single validated active-upstream selection in the existing Nginx configuration, readable after process restart; `deploy/web-slot.py` validates `web` or `web-next`, prepares the candidate configuration, switches by graceful reload and reads back the selected upstream. It never publishes a new listener or restarts the edge. The internal import URL goes through the selected private route. A shared additive static-files volume retains immutable assets from both releases.

- [ ] Add failing Nginx/config tests for selection of each slot, private worker and import routing, public denial of import endpoints, unchanged bearer/privacy headers, invalid upstream rejection, and old/new hashed static URLs across a switch.
- [ ] Run `sh scripts/run-in-test-env.sh .venv/bin/pytest -q -m operational tests/deployment/test_deployment_scripts.py tests/deployment/test_import_deployment.py` and `sh tests/deployment/validate-nginx.sh`; record the focused failures.
- [ ] Implement a small host-owned slot selector and Nginx template change. Store only the selected upstream in the already bind-mounted Nginx directory so the first migration does not recreate Nginx to add a mount. Provide a tested static-asset seeding interface; Task 3 invokes it before the first switch, then adds candidate assets without deleting predecessor files.
- [ ] Re-run focused tests and Nginx validation GREEN. Verify that an invalid candidate config leaves the old selected upstream in place.

### Task 3: Replace stop/start deploy with warm handoff and bounded recovery

**Files:** `deploy/apply-deployment.sh`, `deploy/web-slot.py`, `tests/deployment/test_deployment_scripts.py`, `tests/deployment/test_import_deployment.py`, `tests/deployment/test_remote_only_deployment.py`, `tests/deployment/test_component_release.py`, `tests/deployment/validate-web-slot-handoff.sh` (new).

- **Specification:** [Selected design](../superpowers/specs/2026-10-04-zero-downtime-django-deployment-design.md#selected-design), [Shared database and compatibility contract](../superpowers/specs/2026-10-04-zero-downtime-django-deployment-design.md#shared-database-and-compatibility-contract), [Failure and recovery semantics](../superpowers/specs/2026-10-04-zero-downtime-django-deployment-design.md#failure-and-recovery-semantics).
- **Depends on:** Task 2's selected-slot read/switch interface and shared assets.
- **Produces:** ordinary Deploy starts only the unselected web slot, checks readiness, atomically switches and validates the edge, drains the previous slot, then commits `deployed-image`. It leaves the old slot serving on pre-switch failure and preserves it on uncertain post-switch failure. The existing deployment lock and safe forward-recovery path remain authoritative.

- [ ] Add failing phase tests for candidate pull/setup/readiness failure, Nginx validation failure, successful held-request handoff, post-switch smoke failure, interrupted retry, drain timeout and prior-image compatibility. Assert that neither Nginx nor PostgreSQL is stopped or recreated on an ordinary web release and the marker advances only after verified success.
- [ ] Run `sh scripts/run-in-test-env.sh .venv/bin/pytest -q -m operational tests/deployment/test_deployment_scripts.py tests/deployment/test_remote_only_deployment.py tests/deployment/test_component_release.py`; record focused RED outcomes.
- [ ] Replace the ordinary release's `compose stop nginx`, broad `compose up` and unconditional database reconciliation with slot-specific candidate operations. Keep initial certificate/database bootstrap and explicit exceptional database/certificate maintenance separate. Move Commerce/import worker image reconciliation after web selection without changing the private import contract or terminating accepted work.
- [ ] Re-run focused tests GREEN. Run `sh tests/deployment/validate-web-slot-handoff.sh` against local disposable Docker containers: hold an old-slot request open while switching, check new requests and private routes on the candidate, and prove the held request completes before predecessor stop. A failed/uncertain switch must show a healthy selected slot and truthful deployment failure.

### Task 4: Reconcile shipped behavior and operating instructions

**Files:** `docs/architecture.md`, `docs/runbooks/deployment.md`, `docs/engineering-jobs.md`, and any deployment test fixture changed by Tasks 1–3.

- **Specification:** [Acceptance criteria](../superpowers/specs/2026-10-04-zero-downtime-django-deployment-design.md#acceptance-criteria), [Explicit non-goals](../superpowers/specs/2026-10-04-zero-downtime-django-deployment-design.md#explicit-non-goals).
- **Depends on:** verified Tasks 1–3.
- **Produces:** current architecture/runbook distinguish the committed active slot, temporary candidate, one PostgreSQL, compatible migrations, static retention, ordinary rollback and maintenance-window operations. No ADR text is silently rewritten.

- [ ] Document ordinary CI release and first-activation behavior, plus exact read-only checks for selected upstream, both web images, public/private health and old-request drain.
- [ ] Document that incompatible migration, PostgreSQL upgrade, certificate reissue, VM reboot and resize are outside the zero-downtime promise; state how an operator identifies a stopped, switched-but-uncommitted or committed release.
- [ ] Compare final behavior with specification and ADRs 0003/0007/0011/0028/0051/0053; update implemented architecture facts and record conformance in PR. Stop for an ADR decision if implementation would contradict an accepted boundary.

## Verification and handoff

- Use `$select-verification-suites` on the final changed-path package; run each selected suite for its exact final-package fingerprint, and run `make check` once after all task/review loops. Run `.venv/bin/pre-commit run --files` for every changed Python file and `make static` after integrating Python changes.
- Focused deployment, `sh tests/deployment/validate-nginx.sh`, and `sh tests/deployment/validate-web-slot-handoff.sh` must be GREEN. The local Docker handoff must demonstrate continuous public health, correct private route selection, held-request completion, static assets from both versions and no PostgreSQL/Nginx restart. Simulated failures must leave truthful marker and active-slot state.
- PR CI repeats the same package checks. A successful merge/workflow is not itself live zero-downtime proof: live acceptance checks the selected SHA, old/new container state, no edge/database restart, public and private health and deployment-phase outcome. Do not induce customer traffic or a database restart solely to prove the handoff.

## Operational impact and rollout

The first activation is an ordinary `main` Deploy after green review: the existing Nginx and `web` stay serving, the candidate slot is added, static assets are seeded, the candidate is warmed, and Nginx reloads onto it. The database and certificate are retained. Later releases alternate slots. No direct operator command on the VM or paid Yandex resource change is required; any unexpected need for one is a stop condition requiring separate review. Web/worker/observability release classification remains independent.

## Rollback

Before switch, discard only the candidate and keep the old slot and marker. After switch, reverse the Nginx selection to the still-running compatible predecessor before it is drained. A migration that has made the predecessor incompatible forbids reverse selection and requires compatible forward recovery instead. Preserve database, volumes, certificate and immutable images; never remove a serving slot to force the deployment to green. After a committed release, a rollback is a new reviewed compatible immutable web-image deployment through the same workflow.

## Open questions

None. The specification limits zero downtime to ordinary releases whose old and new versions overlap safely on the shared database; incompatible database maintenance is a separate operation.
