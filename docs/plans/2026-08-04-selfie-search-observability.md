# Selfie Search Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Repository `AGENTS.md` requires each implementation task
> to remain unstaged until independent review approves its complete working-tree diff; only the root
> controller stages and creates the task's single final commit.

- Date: 2026-08-04
- Status: Draft
- Owner: project maintainer
- Related specification:
  [`2026-08-04-selfie-search-observability-design.md`](../superpowers/specs/2026-08-04-selfie-search-observability-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md), implemented public selfie
  search and proposed Operations capability
- Related ADRs: [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md) and
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md)
- ADR impact: None — reversible implementation detail. Final reconciliation must confirm that no
  product-state, biometric-retention, ranking, authorization, or Django/worker authority decision
  changed.

## Goal

Implement the approved [selfie-search observability outcome](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#outcome):
bounded structured events, privacy-reduced edge logs, 14-day host-local retention, and one
deterministic daily aggregate without adding product data or a cloud logging service.

## Architecture

Django and the worker emit strict single-line JSON through small service-local constructors;
product state remains authoritative in PostgreSQL. Docker sends web, worker, and Nginx stdout to a
persistent, size-bounded system journal that survives container replacement. A pure Python
aggregator consumes selected JSONL journal messages, and a host systemd timer emits one aggregate
summary for the previous Moscow calendar day.

## Tech Stack

Python 3.12, Django 6, the existing worker runtime, Python standard-library `json`/`datetime`, Nginx,
Docker Compose, Docker's `journald` log driver, systemd-journald, systemd timers, shell deployment
scripts, pytest, and the existing repository deployment-test harness. Add no Python, system, or
cloud dependency.

## Global Constraints

- The approved specification is authoritative for event names, allowed fields, reason codes,
  privacy denylist, retention, aggregation, failure semantics, and acceptance criteria.
- Emit no selfie bytes, pixels, crop, vector, EXIF, filename, storage identity, URL, token, client
  identity, result-member identity, or raw exception payload.
- Product requests, terminal state transitions, and cleanup must continue when event emission fails.
- PostgreSQL schema and product state do not change; no migration is allowed.
- Retention is `MaxRetentionSec=14day` with `SystemMaxUse=1G`; never increase the cap automatically.
- Use Europe/Moscow calendar windows and UTC event timestamps.
- Do not change accepted upload formats, face detection, cosine threshold `0.363`, result
  membership, result authorization, or customer copy.
- Do not create or configure Yandex Cloud Logging, Unified Agent, IAM, networking, or any paid
  resource.

## Scope

Implements the specification without scope changes.

## Acceptance Criteria

The implementation must satisfy all ten criteria in the approved
[specification](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#acceptance-criteria).
Delivery additionally requires a clean staging deployment, a container-replacement persistence
probe, one fixture-window aggregate, and confirmation that existing selfie-search and worker
contracts remain green.

## Implementation

### Task 1: Add strict service-local JSON event constructors

**Files:**

- Create: `src/backend/selfie_search/observability.py`
- Create: `src/backend/selfie_search/tests/test_observability.py`
- Create: `src/worker/photo_worker/observability.py`
- Create: `src/worker/tests/test_observability.py`

- **Specification:** [Structured event envelope](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#structured-event-envelope),
  [Event contracts](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#event-contracts),
  [Severity](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#severity), and
  [Privacy](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#privacy-and-edge-logging).
- **Depends on:** None.
- **Produces:**
  - backend `emit_selfie_event(logger: logging.Logger, *, event: SelfieEventName,
    level: int = logging.INFO, **fields: object) -> None`;
  - backend classifiers `declared_type_label(value: object) -> str` and
    `source_size_bucket(value: object) -> str`;
  - worker `emit_selfie_worker_event(logger: logging.Logger, *, event: str,
    level: int = logging.INFO, **fields: object) -> None`;
  - deterministic compact JSON with common envelope fields and event-specific allowlists.

- [ ] Add failing backend parameterized tests for all backend-owned event schemas, exact common
  envelope keys, UTC millisecond timestamps, enum validation, non-negative bounded integer fields,
  UUID string conversion, compact one-line serialization, and rejection of unknown/nested fields.
- [ ] Add failing backend tests that pass sentinel bearer tokens, signed URLs, object keys,
  filenames, IPs, user-agents, vectors, photo/face IDs, and exception text through every supported
  field and assert no sentinel can enter the emitted line.
- [ ] Add failing classifier tests covering every declared-type label and exact size-bucket boundary
  at 0, 1 MiB, 5 MiB, 10 MiB, and 20 MiB.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost .venv/bin/pytest -q src/backend/selfie_search/tests/test_observability.py`
  and confirm failure because the backend module does not exist.
- [ ] Implement immutable event definitions and the smallest logging helper. Construct the complete
  dict before serialization; on invalid producer input raise a local contract exception before
  calling `logger.log`. Do not add a general structured-logging framework.
- [ ] Add equivalent failing worker tests for `selfie_worker_attempt_finished`, including error
  codes, retryable boolean, nullable durations, denylisted sentinels, and serialization/logger
  failure containment.
- [ ] Run `.venv/bin/pytest -q src/worker/tests/test_observability.py` and confirm failure before the
  worker helper exists, then implement the worker-local constructor without importing Django code.
- [ ] Verify both targeted test files pass, then run `.venv/bin/ruff format --check .`,
  `.venv/bin/ruff check .`, and `.venv/bin/mypy`; expect three zero exits.
- [ ] Self-review the unstaged diff for schema drift and privacy leakage; prepare the task review
  package, obtain independent approval, then let the root controller create the task's one commit.

### Task 2: Instrument the submission boundary without changing customer behavior

**Files:**

- Modify: `src/backend/selfie_search/forms.py`
- Modify: `src/backend/selfie_search/views.py`
- Modify: `src/backend/selfie_search/observability.py`
- Modify: `src/backend/selfie_search/tests/test_forms.py`
- Modify: `src/backend/selfie_search/tests/test_views.py`
- Modify: `src/backend/selfie_search/tests/test_observability.py`

- **Specification:** [`selfie_submission_finished`](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#selfie_submission_finished)
  and [Failure semantics](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#failure-semantics).
- **Depends on:** Task 1 backend event constructor and classifiers.
- **Produces:**
  - stable Django form error codes matching the specification reason-code enumeration;
  - `SelfieSearchUploadForm.observation() -> SelfieUploadObservation`, where the frozen value carries
    only `actual_format`, `declared_type`, and `source_size_bucket` labels;
  - exactly one `selfie_submission_finished` event for every owned accepted, rejected, or
    storage-unavailable outcome.

- [ ] Add failing form tests asserting each existing rejection branch preserves its current message
  and HTTP rendering behavior while exposing the exact bounded code: `missing_or_empty`,
  `unsupported_format`, `corrupt_image`, `source_too_large`, or `pixel_limit_exceeded`. Reserve
  `normalized_too_large` without introducing a currently unreachable behavior branch.
- [ ] Add failing tests proving `observation()` derives only allowlisted format/type/size labels,
  never reads the upload again after validation, and yields `unknown` when Pillow cannot establish
  the actual format.
- [ ] Add failing view tests for one event per invalid form, storage failure, and accepted redirect;
  assert accepted logging occurs only after object/search/job success and includes the created
  `search_id`, while compensating-delete/database failures do not claim acceptance.
- [ ] Add logger-failure tests proving submission response, temporary-object cleanup, and database
  behavior are identical when event serialization or output fails. Capture only a fixed sanitized
  observability error marker.
- [ ] Run the focused form/view/observability cases and confirm they fail for absent error codes and
  events.
- [ ] Add error `code=` values to existing `ValidationError` branches and retain the exact approved
  user messages and upload acceptance rules. Populate only the frozen bounded observation fields.
- [ ] Instrument the view with a monotonic endpoint timer and one terminal emission point per owned
  outcome. Do not log request metadata, exception objects, or the plaintext result token.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost .venv/bin/pytest -q src/backend/selfie_search/tests/test_forms.py src/backend/selfie_search/tests/test_views.py src/backend/selfie_search/tests/test_observability.py`
  and expect all selected tests to pass.
- [ ] Self-review, independently review the complete unstaged task diff, and create one root-owned
  task commit only after approval.

### Task 3: Instrument worker, ranking, and cleanup-confirmed terminal outcomes

**Files:**

- Modify: `src/worker/photo_worker/runner.py`
- Modify: `src/worker/photo_worker/observability.py`
- Modify: `src/worker/tests/test_runner.py`
- Modify: `src/worker/tests/test_observability.py`
- Modify: `src/backend/selfie_search/services/jobs.py`
- Modify: `src/backend/selfie_search/observability.py`
- Modify: `src/backend/selfie_search/tests/test_jobs.py`
- Modify: `src/backend/selfie_search/tests/test_submission.py`
- Modify: `src/backend/selfie_search/tests/test_observability.py`

- **Specification:** [`selfie_worker_attempt_finished`](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#selfie_worker_attempt_finished),
  [`selfie_ranking_finished`](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#selfie_ranking_finished),
  and [`selfie_search_terminal`](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#selfie_search_terminal).
- **Depends on:** Task 1 constructors; Task 2 provides accepted-submission correlation.
- **Produces:** one bounded attempt event per worker terminal callback path, one ranking event per
  accepted ranking execution, and one logical terminal event after first confirmed cleanup/public
  terminal publication.

- [ ] Add failing runner tests for selfie-query success, each bounded failure family, retryable
  failure, callback transport retry, and duplicate callback. Assert the event uses `search_id`,
  `job_id`, and `attempt_id`, never `photo_id`, and does not duplicate a logical attempt outcome.
- [ ] Add failing Django job tests for positive ranking, zero-match ranking, incompatible ranking,
  cleanup retry, cleanup recovery, duplicate completion, stale/expired attempts, and the legacy
  frozen-candidate path. Assert exact eligible/matched counts and timing nullability.
- [ ] Add failing tests proving terminal logging occurs only after `cleanup_confirmed_at` is durable,
  `cleanup_confirmed` is always true, and the recovery path—not an earlier failed cleanup—owns the
  first terminal event.
- [ ] Add failure-injection tests proving a logging error cannot roll back an accepted callback,
  prevent cleanup, alter retry disposition, or change immutable result rows.
- [ ] Run the selected worker and backend cases and confirm expected failures for missing JSON
  events.
- [ ] Replace only selfie-query terminal uses of free-form worker lifecycle logging with the typed
  helper. Preserve non-selfie processor logging and existing callback payloads.
- [ ] Replace the free-form `selfie cohort ranked` message with `selfie_ranking_finished`, retaining
  existing measured phases and adding eligible-photo count from the already loaded cohort without
  another database query.
- [ ] Centralize terminal emission immediately after the first successful cleanup/publication
  transition. Query the already locked/durable search context for attempt count without exposing
  result-member identities.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost .venv/bin/pytest -q src/backend/selfie_search/tests/test_jobs.py src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_observability.py src/worker/tests/test_runner.py src/worker/tests/test_observability.py`
  and expect all selected tests to pass.
- [ ] Run the public selfie end-to-end contract without real cloud writes:
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost .venv/bin/pytest -q tests/processing/test_selfie_search_e2e.py -m 'not face_models'`.
- [ ] Self-review, independently review the complete unstaged task diff, and create one root-owned
  task commit only after approval.

### Task 4: Redact the submission route and make edge duration observable

**Files:**

- Modify: `deploy/nginx/https.conf.template`
- Modify: `deploy/nginx/staging.conf`
- Modify: `deploy/nginx/reload-nginx.sh`
- Modify: `tests/deployment/validate-nginx.sh`
- Modify: `tests/deployment/test_deployment_scripts.py`
- Modify: Nginx fixture/expected files under `tests/deployment/` only if the current harness requires
  them.

- **Specification:** [Privacy and edge logging](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#privacy-and-edge-logging).
- **Depends on:** Task 2 supplies application-level rejection evidence, allowing edge identity
  removal without losing the required diagnosis.
- **Produces:** one safe log format for exact submission and bearer-result route families, retaining
  method, fixed route label, status, response bytes, and `$request_time` only.

- [ ] Extend the Nginx validation harness with failing assertions for exact submission, bearer
  result/status/media/download, query-string, 4xx/5xx, upstream failure, and request-body buffering
  cases. Use sentinel client IP, referrer, user-agent, token, and tracking parameters and assert none
  appear in rendered logs or error output.
- [ ] Add a failing assertion that ordinary event and static routes retain the existing access-log
  fields and are not accidentally mapped to the selfie placeholder.
- [ ] Run `bash tests/deployment/validate-nginx.sh` and the focused Nginx deployment tests; confirm
  failure because the exact submission route still logs raw client metadata and request URI.
- [ ] Add explicit route-family maps for client address, request, referrer, and user-agent; add
  `$request_time` to the safe format. Give the exact submission location the same bounded error-log
  suppression needed to prevent buffering warnings from reintroducing raw request data.
- [ ] Apply the identical contract to the generated HTTPS template, checked-in staging fallback,
  and reload validation so recovery cannot regress privacy.
- [ ] Run `bash tests/deployment/validate-nginx.sh` and
  `.venv/bin/pytest -q tests/deployment/test_deployment_scripts.py -k nginx`; expect all selected
  checks to pass.
- [ ] Run `nginx -t` through the repository's existing containerized validation path and inspect
  representative rendered log lines for both redacted and ordinary routes.
- [ ] Self-review, independently review the complete unstaged task diff, and create one root-owned
  task commit only after approval.

### Task 5: Persist bounded logs and generate deterministic daily summaries

**Files:**

- Create: `deploy/selfie-observability/summarize.py`
- Create: `deploy/selfie-observability/run-daily-summary.sh`
- Create: `deploy/selfie-observability/journald.conf`
- Create: `deploy/selfie-observability/selfie-search-summary.service`
- Create: `deploy/selfie-observability/selfie-search-summary.timer`
- Create: `tests/deployment/test_selfie_observability_summary.py`
- Create: `tests/deployment/test_selfie_observability_units.py`
- Modify: `docker-compose.prod.yml`
- Modify: `docker-compose.https.yml`
- Modify: `.github/workflows/deploy.yml`
- Modify: `.github/workflows/promote-production.yml`

- **Specification:** [Durable local log transport](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#durable-local-log-transport),
  [Daily aggregate report](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#daily-aggregate-report),
  and [Failure semantics](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#failure-semantics).
- **Depends on:** Tasks 1–4 define every input event and safe Nginx output.
- **Produces:**
  - pure `summarize_jsonl(lines: Iterable[str], *, report_date: date,
    timezone_name: str = "Europe/Moscow") -> DailySummary`;
  - CLI accepting `--date YYYY-MM-DD` and JSONL stdin, emitting one compact summary JSON line;
  - root-owned systemd oneshot/timer invoking the host runner at 00:10 Moscow;
  - Compose `journald` driver with stable `findme.service`/`findme.environment` tags for `web`,
    worker replicas, and Nginx.

- [ ] Create a failing table-driven aggregator fixture covering accepted, every rejection reason,
  actual/declared formats, size buckets, positive/zero terminal results, worker retry, ranking
  timings, duplicate logical events, malformed JSON, unknown schema/event, late event, missing
  counterpart, empty day, and recomputation. Assert exact counts, p50/p95 nearest-rank behavior,
  min/max cohort values, and `complete`.
- [ ] Run `.venv/bin/pytest -q tests/deployment/test_selfie_observability_summary.py` and confirm
  failure because the aggregator does not exist.
- [ ] Implement the aggregator with standard-library types only. Parse one line at a time, ignore
  unrelated non-JSON service output, count malformed objects that claim the selfie schema, dedupe by
  event plus logical internal IDs, and never copy individual IDs into `DailySummary`.
- [ ] Add failing CLI and unit-file tests for UTC/Moscow boundaries, `--date`, compact JSON output,
  zero-traffic output, nonzero exit on unreadable input, fixed 00:10 schedule, root ownership
  expectations, and no secret environment dependency.
- [ ] Implement `run-daily-summary.sh` to calculate the previous Moscow date, use explicit
  `journalctl --since/--until --output=cat` filters for stable service tags, pipe only the selected
  messages into the CLI, and let stdout become the systemd journal summary event. Add explicit
  recomputation by date for operator recovery.
- [ ] Add failing Compose tests proving `web`, `worker`, and Nginx use `journald` with stable tags;
  assert database logging remains unchanged and no secret is interpolated into log options.
- [ ] Add the minimal Compose logging blocks and pass `DEPLOYMENT_TARGET` into the stable environment
  tag through existing deployment workflows without changing application secrets or feature flags.
- [ ] Add unit tests that parse `journald.conf` and require exactly `Storage=persistent`,
  `MaxRetentionSec=14day`, and `SystemMaxUse=1G`; reject relaxed/missing values.
- [ ] Run
  `.venv/bin/pytest -q tests/deployment/test_selfie_observability_summary.py tests/deployment/test_selfie_observability_units.py tests/deployment/test_deployment_scripts.py`
  and expect all selected tests to pass.
- [ ] Run the pure aggregator twice on the same fixture date and byte-compare all semantic fields
  except generated/recomputed metadata; confirm the latest output declares `recomputed=true`.
- [ ] Self-review, independently review the complete unstaged task diff, and create one root-owned
  task commit only after approval.

### Task 6: Reconcile host configuration through the deployment entrypoint

**Files:**

- Create: `deploy/install-selfie-observability.sh`
- Create: `deploy/verify-selfie-observability.sh`
- Modify: `deploy/apply-deployment.sh`
- Modify: `tests/deployment/test_deployment_scripts.py`
- Modify: `tests/test_repository_foundation.py`
- Modify: `docs/architecture.md`
- Modify: `docs/engineering-jobs.md`
- Modify: `README.md`

- **Specification:** [Deployment and operation](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#deployment-and-operation)
  and all [Acceptance criteria](../superpowers/specs/2026-08-04-selfie-search-observability-design.md#acceptance-criteria).
- **Depends on:** Tasks 1–5 complete the event, edge, journal, and summary artifacts.
- **Produces:** idempotent installation/verification through the one supported deployment entrypoint,
  bounded rollback for managed host files, an operator query/recompute runbook, and reconciled
  implemented architecture.

- [ ] Add failing shell-harness tests for first install, no-op reconciliation, invalid candidate
  config, missing persistent journal directory, journald restart failure, Compose deployment
  failure after install, prior-file restoration, unit daemon reload/enable failure, timer inactive,
  wrong Docker driver/tags, unreadable probe, cap/retention mismatch, and sanitized diagnostics.
- [ ] Add a failing test proving rollback restores only repository-managed drop-in/unit/script files
  and never replaces the complete host journald configuration, journal directory, unrelated units,
  Docker daemon configuration, or existing logs.
- [ ] Run the focused deployment tests and confirm failure because installation and verification
  entrypoints are absent.
- [ ] Implement `install-selfie-observability.sh` with strict mode, dependency checks, validated
  source paths, mode/owner checks, same-filesystem temporary backups, atomic replacement, conditional
  `systemctl daemon-reload`, conditional journald restart, timer enablement, traps, and exact rollback
  status markers. Do not run journal vacuum or delete logs during installation or rollback.
- [ ] Implement `verify-selfie-observability.sh` to inspect effective journald values, persistence,
  disk use, oldest selected selfie event when present, systemd unit/timer state, Compose logging
  driver/tags, and bounded probe readability. A fresh no-event environment is valid; an unreadable
  emitted probe is not.
- [ ] Integrate install before candidate container replacement and verification after container
  health in `apply-deployment.sh`. Preserve the previous managed logging files until all deployment
  checks succeed; invoke their rollback from the existing deployment rollback path.
- [ ] Add a non-mutating probe interface using the Task 1 constructor through the existing web
  container. The probe event is `selfie_observability_probe`, contains only the common envelope plus
  a random non-secret probe ID, and is excluded from customer funnel aggregation.
- [ ] Run
  `.venv/bin/pytest -q tests/deployment/test_deployment_scripts.py tests/test_repository_foundation.py`
  and expect all selected deployment/repository tests to pass.
- [ ] Run the complete focused regression set from Tasks 1–5, `git diff --check`,
  `.venv/bin/ruff format --check .`, `.venv/bin/ruff check .`, `.venv/bin/mypy`, shell syntax checks
  for every created/modified script, and the containerized Nginx validation. Expect zero failures
  and no secret sentinel in captured output.
- [ ] Update `docs/architecture.md` only after behavior verification: move the bounded selfie-search
  structured-event/journald/daily-summary slice into implemented Operations facts while leaving
  dashboards, alert delivery, central/cloud logging, and biometric-quality benchmarking proposed or
  excluded.
- [ ] Update `docs/engineering-jobs.md` with dated repository/staging evidence only after the
  deployment and replacement-persistence probes succeed. Document query/recompute commands and the
  14-day/1 GiB limits in `README.md` without presenting the journal as a backup.
- [ ] Compare the delivered diff with ADRs 0017 and 0019. Record `No ADR impact — reversible
  implementation detail` if the worker authority, query retention, cleanup, result immutability,
  and bearer boundaries remain unchanged; stop and invoke `write-adr` if any changed.
- [ ] Self-review, independently review the complete unstaged task diff, and create one root-owned
  task commit only after approval.

## Verification

Run from the implementation worktree with the repository virtual environment and CI-like Django
variables:

```bash
export DB_NAME=app
export DB_USER=app
export DB_PASSWORD=app
export DB_HOST=localhost
export DB_PORT=5432
export SECRET_KEY=test
export DEBUG=False
export ALLOWED_HOSTS=localhost

.venv/bin/pytest -q \
  src/backend/selfie_search/tests/test_observability.py \
  src/backend/selfie_search/tests/test_forms.py \
  src/backend/selfie_search/tests/test_views.py \
  src/backend/selfie_search/tests/test_jobs.py \
  src/backend/selfie_search/tests/test_submission.py \
  src/worker/tests/test_observability.py \
  src/worker/tests/test_runner.py \
  tests/deployment/test_selfie_observability_summary.py \
  tests/deployment/test_selfie_observability_units.py \
  tests/deployment/test_deployment_scripts.py \
  tests/test_repository_foundation.py

bash tests/deployment/validate-nginx.sh
sh -n deploy/install-selfie-observability.sh
sh -n deploy/verify-selfie-observability.sh
sh -n deploy/selfie-observability/run-daily-summary.sh
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy
git diff --check
```

Expected: all selected pytest cases pass, Nginx configuration/rendered-log checks pass, all shell
scripts parse, Ruff reports no formatting or lint findings, configured `mypy` reports no issues, and
the diff contains no whitespace errors. Invoke `mypy` without a path so `pyproject.toml` remains the
source of its configured targets; do not run `mypy .`.

Before merge, run the repository's required full CI workflow once. Do not overlap multiple full
pytest suites locally. Required staging evidence is:

- candidate deployment healthy with the expected immutable image;
- effective `Storage=persistent`, `MaxRetentionSec=14day`, and `SystemMaxUse=1G`;
- `web`, both worker replicas, and Nginx using `journald` with correct stable tags;
- a probe emitted before one controlled Compose replacement still readable afterward;
- one fixture-date `selfie_search_daily_summary` readable and marked complete;
- no raw submission URI, bearer token, IP, referrer, or user-agent in sampled selfie route logs;
- normal event access logging unchanged;
- public health HTTP 200 and no unexpected container restarts or OOM;
- no application/database/search-state mutation from probes or aggregation.

## Operational Impact and Rollout

This changes host logging configuration and restarts journald when its managed drop-in changes. It
does not intentionally interrupt containers, but deployment must treat a journald restart failure as
an activation failure and restore the prior managed file. The subsequent normal Compose deployment
recreates services with the journald driver.

Roll out to the current staging VM through the normal GitHub deployment after CI. Verify the logging
driver, tags, journal limits, timer, probe persistence, aggregate fixture, health, and privacy before
promotion. Production remains unprovisioned; later promotion applies the same files through the
existing approved workflow. No pricing approval is needed because this plan creates no cloud
resource and caps existing VM disk use.

The first automatic report runs at the next 00:10 Moscow boundary. Deployment acceptance does not
wait for that boundary; it uses an explicit fixture-date recomputation that is clearly labelled and
contains no customer identifiers.

## Rollback

Application rollback returns to the previous immutable web/worker image and Compose files through
the existing deployment rollback path. The installer restores the exact prior repository-managed
journald drop-in, systemd units, and runner artifacts when deployment has not committed success,
then conditionally reloads/restarts only the affected host services.

Rollback must not vacuum, truncate, export, or delete the system journal. Existing structured events
age out under the effective post-rollback host policy. PostgreSQL and Object Storage require no
rollback because this increment adds no schema, row, object, or lifecycle state. If application
rollback leaves the approved journald configuration in place after a fully successful deployment,
that is safe only when the operator explicitly chooses to retain it; otherwise use the documented
installer rollback to the captured prior managed state.

## Architecture and ADR Reconciliation

After behavior and staging verification, update implemented architecture facts and record exactly
one final outcome in the PR: `No ADR impact — reversible implementation detail`. Do not mark central
logging, dashboards, alerts, or quality benchmarking as implemented. If execution discovers that
14-day persistence requires a new cloud service, product-data table, durable biometric field, or
change to the worker/search authority boundary, stop the plan and return to specification/ADR review
instead of expanding scope.

## Open Questions

None.
