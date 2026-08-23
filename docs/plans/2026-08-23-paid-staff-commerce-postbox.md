# Paid Staff Commerce and Postbox Implementation Plan

- Date: 2026-08-23
- Status: Approved
- Owner: project maintainer
- Related specification:
  [paid photo purchase and original delivery](../superpowers/specs/2026-08-20-paid-photo-purchase-and-original-delivery-design.md)
  plus the maintainer-approved 2026-08-23 production-email and staff-acceptance scope
- Related architecture: [current architecture](../architecture.md)
- Related ADRs:
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md),
  [ADR 0028](../adr/0028-operate-one-canonical-deployment.md),
  [ADR 0029](../adr/0029-use-watermarked-previews-for-paid-photos.md),
  [ADR 0030](../adr/0030-use-anonymous-server-side-event-carts.md), and
  [ADR 0031](../adr/0031-use-orders-and-adapters-for-paid-original-delivery.md)
- ADR impact: conforms to the cited ADRs. Selecting Postbox behind the accepted `EmailSender`
  seam is a reversible provider implementation detail; no new ADR is required. Update
  `docs/architecture.md` because the Commerce worker and real email adapter become deployed facts.

## Goal

Make the next staff production test of a paid event work end to end: face search, RUB prices,
cart, simulated payment, return to the order, purchased-original access, and a real Postbox order
email, while keeping every paid capability unavailable to non-staff users.

## Scope

- Keep `paid-events`, `paid-watermarked-previews`, `paid-photo-cart`, `paid-photo-purchase`, and
  `paid-photo-payment-simulator` in database state `staff`; do not set any paid gate to `on`.
- Remove the unintended free-event-only condition around the selfie upload form. Event visibility,
  paid-watermarked media, selfie submission gates, and event isolation remain authoritative.
- Render all human-visible Commerce monetary values as formatted RUB values through
  `commerce.pricing.format_rub`; keep integer kopecks and literal `RUB` in models and provider
  contracts only.
- Add one Postbox-specific authenticated SMTPS adapter using TLS 1.2+ on
  `postbox.cloud.yandex.net:465`, a configured sender, and dedicated scoped credentials.
- Activate the existing Commerce worker through the canonical Deploy workflow. Keep the payment
  simulator as the deployed staff-only payment adapter for this acceptance milestone.
- Provision one Postbox sender identity for `findme-photo.ru`, publish required DKIM plus a single
  compatible SPF record and DMARC, store credentials and the order-link signing secret in the
  canonical Lockbox secret, and verify delivery to controlled external mailboxes.
- After the release, audit remaining code-owned, database, deployment, and CI flags and record the
  evidence separately; do not change unrelated flags as part of this release.

## Acceptance criteria

- An authenticated staff user can open published paid event `paid-test`, see the selfie form,
  price and cart controls, create an order, confirm it in the simulator, land on a 200 order page,
  download each purchased original, and receive the permanent order link by real email.
- An anonymous or authenticated non-staff user receives no paid catalog/event/cart/checkout/order
  or simulator capability; staff-only database flag states remain unchanged after deployment.
- No customer-facing or Commerce Admin monetary value is labeled or rendered as kopecks. Example:
  `40000` is rendered as `400 ₽`; `45075` is rendered as `450,75 ₽`.
- The deployed Commerce worker has exactly one running container, passes
  `commerce_worker_health`, emits no credential values, and processes the existing compatible
  pending delivery without changing paid Order or entitlement evidence.
- A provider acceptance is recorded as a successful `EmailDeliveryAttempt`; recipient-server
  delivery and inbox/spam placement are verified from Postbox status evidence and controlled
  mailboxes without enabling open/click tracking.
- Focused tests, pre-commit on exact changed Python files, `make static`, one final `make check`,
  relevant visual regression, CI, merge, canonical Deploy, public HTTP checks, and live ORM checks
  all pass on the exact delivered SHA.

## Worker/state/artifact release safeguards

- [x] **Live-state inventory.** At deployed SHA `d4e0311e72412bbe84cfd150f4f4c946683a3805`
  there is no `commerce-worker` container. Paid flags are all `staff`. Orders: one `paid`;
  PaymentAttempts: one `succeeded`; EmailDeliveries: one `pending`, zero attempts; open Commerce
  attention: zero. Order `FM-HKZTEY9F` is paid for `40000 RUB`. Paid event `paid-test` is published
  at `10000` kopecks/photo; legacy paid event `test` is unavailable at `30000` kopecks/photo.
- [x] **Compatibility matrix.** There is no schema or durable contract change. Old and new Django
  read every current Order, PaymentAttempt, grant, delivery, and attempt row unchanged. The new
  worker drains current pending rows through the existing `EmailSender` result contract. Rolling
  back the image stops new processing but preserves all rows and grants.
- [x] **Reviewed data-state migration or reset semantics.** Use compatible drain. Do not reset,
  requeue, backfill, purge, or edit existing commerce rows. The single pending delivery is eligible
  for the first controlled real send after its recipient and grant are checked without logging the
  address or secret link.
- [x] **End-to-end contract sizing.** The order email is one bounded plaintext message with fixed
  lines, one HTTPS grant URL, aggregate item count and formatted total; it contains no item list,
  attachment, preview, tracking pixel, or provider payload. Tests exercise UTF-8 subject/body,
  timeout propagation, SMTP response classification, and safe failure categories.
- [x] **Previous-snapshot upgrade rehearsal.** Existing worker tests cover pending, processing,
  retryable, terminal, succeeded, canceled, expired-lease, exhausted and attention states. Add a
  deployment-compatible test showing a pre-existing pending delivery succeeds through the new
  adapter contract without row conversion; run focused worker/delivery suites before activation.
- [x] **Staged activation and rollback order.** Merge dark-compatible code; provision and verify
  Postbox/DNS/Lockbox; set non-secret deploy variables; audit the pending recipient; deploy with
  Commerce worker enabled; verify worker health, provider acceptance and inbox delivery; then run a
  new staff order. Stop on authentication, DKIM, rejection, duplicate-attempt, health, or non-staff
  exposure failure. Rollback sets `COMMERCE_WORKER_ENABLED=False` and redeploys the last successful
  image while leaving paid feature flags `staff` and preserving rows.
- [x] **Supported bounded operational commands.** Read-only inspection uses the running web
  container and `commerce_worker_health`. Worker activation/deactivation uses only GitHub workflow
  **Deploy** with an exact main SHA. Queue changes are limited to existing Admin resend/retry
  actions; no ad-hoc SQL, bulk requeue, purge, or state reset is authorized.

## Implementation

Execute this plan with `$execute-implementation-plan`.

### Task 1: Complete paid staff UX and canonical RUB presentation

**Files:**

- Modify: `src/backend/config/views.py`
- Modify: `src/backend/commerce/views.py`
- Modify: `src/backend/templates/commerce/payment_simulator.html`
- Modify: `src/backend/commerce/admin.py`
- Modify: `src/backend/picflow/tests/test_views.py`
- Modify: `src/backend/commerce/tests/test_payment_simulator.py`
- Modify: `src/backend/commerce/tests/test_admin.py`
- Modify as required by the existing visual harness: paid-event visual tests/snapshots only

- **Specification:** ADR 0019 paid-event search, ADR 0028 staff gates, and paid-purchase acceptance
  criteria 24, 29, 38, 39, and 41.
- **Depends on:** None.
- **Produces:** paid staff event context with the selfie form and one human-facing monetary
  presentation contract: `format_rub(kopecks)`.

- [ ] Add a failing event-view test proving a staff-visible published paid event receives the
  selfie upload form when the paid gallery gate is `staff`, while non-staff visibility remains
  denied.
- [ ] Add failing simulator and Commerce Admin tests proving whole and fractional amounts render
  as RUB and that no human-facing value contains `коп` or raw `*_kopecks` labels.
- [ ] Run the exact tests and confirm the failures are caused by the free-only form condition and
  raw monetary fields.
- [ ] Remove only the free-only form condition, pass `format_rub(order.total_kopecks)` to the
  simulator template, and expose formatted read-only Admin displays while retaining immutable raw
  model fields and filters internally.
- [ ] Run focused picflow, commerce view, simulator, pricing, presentation, and Admin tests; expect
  zero failures.

### Task 2: Add the production Postbox email adapter and fail-closed runtime checks

**Files:**

- Create: `src/backend/commerce/postbox_email_sender.py`
- Create: `src/backend/commerce/tests/test_postbox_email_sender.py`
- Modify: `src/backend/config/settings.py`
- Modify: `src/backend/commerce/checks.py`
- Modify: `src/backend/commerce/tests/test_checks.py`
- Modify: `.env.example`

- **Specification:** paid-purchase Email Delivery, Commerce Worker, failure semantics, security,
  and acceptance criteria 28-31, 34, 37, and 40.
- **Depends on:** existing `EmailMessage`, `EmailSender`, `EmailSendOutcome`, and
  `EmailSendResult` interfaces remain unchanged.
- **Produces:** `commerce.postbox_email_sender.postbox_email_sender_factory`, configured by a
  sender address, API-key ID and secret, using SMTPS/465 with default certificate verification.

- [ ] Add failing unit tests for UTF-8 sender/message submission, login, timeout, successful
  acceptance, SMTP 4xx retryable mapping, recipient/data 5xx terminal mapping, authentication
  failure, connection failure, invalid configuration, and secret-safe exceptions/repr.
- [ ] Run the adapter tests and confirm failure because the adapter is absent.
- [ ] Implement the smallest provider-specific adapter. Never log or return recipient, body,
  access URL, username, password, or raw SMTP response text.
- [ ] Add failing deployed-runtime checks requiring the production factory and complete Postbox
  configuration whenever the Commerce worker is enabled; continue rejecting deterministic test
  adapters and `noreply@localhost`.
- [ ] Implement settings and checks, then run adapter, checks, delivery and worker tests; expect
  zero failures.

### Task 3: Wire the Commerce worker and secrets through the canonical deployment

**Files:**

- Modify: `docker-compose.yml`
- Modify: `docker-compose.deployment.yml`
- Modify: `docker-compose.local-purchase.yml` only if required to preserve the local Mailpit path
- Modify: `deploy/apply-deployment.sh`
- Modify: `deploy/run-remote.sh`
- Modify: `deploy/environment-secrets.json`
- Modify: `.github/workflows/deploy.yml`
- Modify: deployment tests under `tests/deployment/`
- Modify: `docs/runbooks/deployment.md`
- Modify: `docs/architecture.md`

- **Specification:** ADR 0028 canonical Deploy/Lockbox boundary, ADR 0031 worker monitoring and
  rollback, and this plan's staged activation order.
- **Depends on:** Task 2 factory path and setting names.
- **Produces:** worker-only projection of Postbox credentials; non-secret factory/origin/contact
  settings; deployment validation, Compose `commerce` profile activation, health verification,
  and rollback that preserves the prior profile state.

- [ ] Add failing deployment tests proving all required Commerce/Postbox settings reach only the
  intended containers, missing credentials fail before cutover, local/test factories are rejected,
  an enabled worker has exactly one healthy container, and disabled rollback removes it.
- [ ] Run the focused deployment tests and confirm the expected missing-wiring failures.
- [ ] Add the smallest manifest/workflow/apply/Compose wiring. Credentials must come from the
  canonical Lockbox consumer; factory paths, public origin, sender and support contact may be
  repository variables with exact defaults where safe.
- [ ] Preserve the existing local Mailpit adapter and do not expose Postbox credentials to web,
  photo-worker, Nginx, DB, Certbot, or monitoring containers.
- [ ] Run deployment tests, Compose config validation, shell checks, runtime system checks and
  `git diff --check`; expect zero failures and no secret values in output.

### Task 4: Reconcile architecture, verify, deliver, and activate

**Files:** all approved task files above plus the PR evidence; no schema migration.

- **Specification:** all acceptance criteria and release safeguards in this plan.
- **Depends on:** Tasks 1-3 approved and committed.
- **Produces:** merged main SHA, canonical deployment, working staff-only production flow, real
  Postbox delivery evidence, rollback evidence, and a separate feature-flag audit report.

- [ ] Run independent task reviews and one final whole-branch review. Resolve every blocking
  finding on the same task package.
- [ ] Run exact-file pre-commit, `make static`, one final `make check`, relevant JavaScript/visual
  regression, `git diff --check`, migration drift, Compose rendering, and shell syntax checks.
- [ ] Push one PR, wait for all required CI, merge, and verify the merge SHA.
- [ ] Immediately before Yandex Cloud or Lockbox mutations, present the exact profile, folder,
  resource IDs, commands, current/target state, cost delta, availability/data impact, validation,
  and rollback required by `$manage-yandex-cloud`; obtain the fresh manual confirmation gate.
- [ ] Provision Postbox sender/DNS and scoped credentials, add the Commerce signing secret, set
  non-secret repository variables, audit the pending recipient without exposing it, and launch
  canonical **Deploy** for the exact main SHA.
- [ ] Verify public health, worker health, unchanged `staff` flags, non-staff denial, existing
  pending delivery, new staff checkout/simulator/order/download/email, provider status and inbox
  placement. Disable the worker and roll back the image on any stop condition.
- [ ] Audit all remaining database, code, environment, Compose, workflow-input, repository-variable
  and CI-derived feature flags/constraints; write an evidence-linked report without changing their
  states.

## Verification

- `make test TESTS="<focused changed-surface test files>"` — every RED is observed before its
  implementation and the final focused run has zero failures.
- `.venv/bin/pre-commit run --files <exact changed Python files>` — zero hook failures after the
  last Python change.
- `make static` — Ruff format/lint and full mypy pass.
- `make check` — complete Python suite, Django checks and migration drift pass once on the final
  branch state.
- Existing package scripts for the paid-event desktop/mobile visual scenarios — zero unexpected
  diffs; update snapshots only for the intended paid-event discovery controls.
- `docker compose --env-file <test-env> -f docker-compose.deployment.yml -f docker-compose.https.yml config`
  — renders worker configuration without printing secrets into retained evidence.
- `sh -n deploy/apply-deployment.sh deploy/run-remote.sh` and focused deployment pytest files — pass.
- PR required checks and canonical Deploy — success on the exact merge SHA.
- Live: HTTPS 200/expected redirects, one healthy Commerce worker, health command success, all paid
  flags `staff`, non-staff 404/denial, paid-test staff end-to-end completion and real email delivery.

## Operational impact and rollout

Postbox and the Commerce worker become new active external/runtime components on the existing VM.
No database migration or additional VM is introduced. The worker adds one lightweight polling
container. Postbox charges only accepted messages after its current free monthly allowance; the
first 2,000 accepted messages per month are currently free. Its default quota must be checked and
raised before the staff-only flow can exceed it. DNS authentication may take time to propagate.

## Rollback

Set `COMMERCE_WORKER_ENABLED=False` through the canonical deployment configuration and run Deploy
for the last successful image. Keep all paid feature flags at `staff` or set the purchase/simulator
gates `off` if containment is required. Do not delete Orders, PaymentAttempts, grants,
EmailDeliveries, attempts, Postbox identity, DKIM records, or Lockbox entries during incident
rollback. After recovery, retry only through the supported Admin action.

## Open questions

None. The fresh Yandex Cloud pricing/access confirmation immediately before resource mutation is a
mandatory execution gate, not an unresolved design choice.
