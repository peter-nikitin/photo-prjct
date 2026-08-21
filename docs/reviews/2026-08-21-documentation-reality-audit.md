# Documentation reality audit — 2026-08-21

**Audited commit:** `be22bdd0118fbc6f416b96cc31683890ec930540` (`origin/main`)

## Scope and method

This audit checks the current repository only: tracked source, tests, workflows, deployment
configuration, Git history, the documentation named in the task, and the current first-party GitHub
Actions state for the audited SHA. It did not query a VM, cloud account, database, bucket, DNS, or
public endpoint directly. Therefore a repository path is implementation evidence and a successful
Actions deployment is release evidence, but neither independently proves current VM state or a
runtime feature-gate value.

The audit covered 16 product-job rows/details, 25 engineering-job rows/details, 30 numbered ADRs,
13 future-work findings, `docs/architecture.md`, and both postmortems. The ADR index agrees with
all 30 numbered records: 23 Accepted, six Superseded, and one Proposed. The separate `0000` template
is Proposed and is intentionally not indexed.

Priority means:

- **blocking** — a registry cannot reliably identify a current job or contradicts its own status.
- **next** — current documentation, command, or source-of-truth evidence is inaccurate and should
  be reconciled in the next documentation change.
- **future** — a bounded gap remains correctly deferred by its trigger.
- **retire** — a finding has been implemented or superseded and must not remain an open work item.

## Reconciliation with the 2026-08-20 audit

| Previous finding | Classification now | Rechecked result |
| --- | --- | --- |
| A1 — ADR index/lifecycle | **fixed** | ADR 0028–0030 are indexed; ADRs 0005 and 0026 contain their `Superseded by` backlinks ([ADR index](../adr/README.md:27), [ADR 0028](../adr/0028-operate-one-canonical-deployment.md:3), [ADR 0005](../adr/0005-promote-images-through-staging.md:3)). |
| A2 — architecture had two-environment topology | **fixed** | The active deployment section describes one unqualified Compose project and automatic `main` delivery ([architecture](../architecture.md:160), [architecture](../architecture.md:170)); old names are explicitly historical ([architecture](../architecture.md:36)). |
| A3 — ADR 0028 lacked automatic deployment and gate consumers | **fixed** | Deploy now triggers on `push` to `main` ([workflow](../../.github/workflows/deploy.yml:3)); database-backed gate evaluation fails closed ([service](../../src/backend/feature_flags/services.py:13)) and paid watermark/cart paths use it ([cart views](../../src/backend/commerce/views.py:93)). |
| A4 — activation wording used staging/production as current | **changed/transition** | Most current documents now distinguish repository defaults from historical deployment evidence. Current-main Deploy and scheduled public-monitor runs succeeded, but the architecture does not cite that dated evidence for its asserted DNS/TLS state; see N2. |
| A5 — ADR 0016 staging seed proposal | **still open** | It remains Proposed in the index ([ADR index](../adr/README.md:38)) and no seed implementation was found. It should be explicitly rejected/retired rather than silently treated as current. |
| E1 — EJ-015 identifier missing from detail | **still open** | The current-state row is EJ-015 ([engineering jobs](../engineering-jobs.md:48)), but the operational-evidence detail is still headed EJ-014 ([engineering jobs](../engineering-jobs.md:196)); this also duplicates the real EJ-014 feedback detail ([engineering jobs](../engineering-jobs.md:273)). |
| E2 — EJ-019 status disagreement | **still open** | The table says `Validated` ([engineering jobs](../engineering-jobs.md:52)), while the detail says `Delivered` ([engineering jobs](../engineering-jobs.md:482)) and the last status-log transition is `Delivered → Delivered` ([engineering jobs](../engineering-jobs.md:540)). |
| E3 — obsolete promotion/separate-environment jobs | **fixed** | EJ-006/EJ-007 are now Superseded with ADR 0028 as the authority ([engineering jobs](../engineering-jobs.md:142), [engineering jobs](../engineering-jobs.md:151)). |
| E4 — renamed/deleted deployment evidence | **changed/transition** | Current EJ-003/EJ-010 evidence uses canonical paths ([engineering jobs](../engineering-jobs.md:100), [engineering jobs](../engineering-jobs.md:193)); one historical status-log link remains broken, recorded as L1. |
| E5 — Lockbox implementation absent from EJ-017 | **fixed** | EJ-017 is Delivered and cites the canonical manifest/projection implementation ([engineering jobs](../engineering-jobs.md:435), [manifest](../../deploy/environment-secrets.json:1)). |
| E6 — `make worktree` false test-ready contract | **still open** | The bootstrap check does not set either processing flag ([create-worktree](../../scripts/create-worktree.py:16)), although its copied template defaults both false ([`.env.example`](../../.env.example:35)) and Django requires both true ([system check](../../src/backend/selfie_search/apps.py:114)). The test wrapper has the missing defaults, but bootstrap does not ([test wrapper](../../scripts/run-in-test-env.sh:13)). |
| E7 — service backup/restore capability absent | **still open** | EJ-010 still correctly records that local clone/restore is not scheduled backup, media recovery, RPO/RTO, or a recovery drill ([engineering jobs](../engineering-jobs.md:187)); architecture says the same ([architecture](../architecture.md:141)). |
| P1 — PJ-009/PJ-012 do not match code/history | **still open** | PJ-009 is still Candidate ([product jobs](../product-jobs.md:191)), despite the download route, signed resolver, and regression ([URLs](../../src/backend/config/urls.py:21), [view](../../src/backend/config/views.py:225), [test](../../src/backend/picflow/tests/test_views.py:1922)). PJ-012 still says PR #93 is open ([product jobs](../product-jobs.md:240)); `02a8036` is its merge commit on this `main`. |
| P2 — capture-time Release B status conflict | **still open** | PJ-015 still describes canonical deployment as pending ([product jobs](../product-jobs.md:294)), while EJ-019 says `d5b21e4` is deployed ([engineering jobs](../engineering-jobs.md:493)). This audit cannot decide live truth, so the two documents must name the same evidence boundary instead of asserting different ones. |
| P3 — face-search wording used the former topology as current | **fixed** | PJ-008 labels prior staging proof as dated former-topology evidence and identifies the canonical boundary ([product jobs](../product-jobs.md:156), [product jobs](../product-jobs.md:173)). |
| P4 — PJ-006 state inconsistency | **still open** | Current row says Candidate ([product jobs](../product-jobs.md:38)); detail says Planned ([product jobs](../product-jobs.md:125)). |
| F1 — selfie lifecycle/reconciliation activation wording | **still open** | The expiration finding says live bucket behavior was not activated ([future work](../future-work/selfie-search-lifecycle-expiration-sla.md:8)), while dated live activation is recorded in PJ-008 ([product jobs](../product-jobs.md:158)). The missing-object finding still says public search is disabled until deployment ([future work](../future-work/selfie-search-missing-temporary-object-reconciliation.md:10)), conflicting with PJ-008's current code boundary ([product jobs](../product-jobs.md:154)). |
| F2 — retained bounded future work | **changed/transition** | Most triggers remain concrete; the paid-cart finding is superseded by ADR 0030 and is now **retire** (N1). The direct-media wording still needs canonical terminology (N4). |
| F3 — worker test selector | **still open** | The documented command still ends in two `ModuleNotFoundError: photo_worker` collection errors; the finding accurately describes the defect ([future work](https://github.com/peter-nikitin/photo-prjct/blob/be22bdd0118fbc6f416b96cc31683890ec930540/docs/future-work/2026-08-10-worker-selector-import-path.md#L5)). |
| F4 — runtime credential hygiene | **still open** | EJ-017 is delivered and ADR 0028 establishes the canonical VM ([engineering jobs](../engineering-jobs.md:435), [ADR 0028](../adr/0028-operate-one-canonical-deployment.md:12)), firing the finding's stated trigger; its former staging paths and trigger language are stale (N3). |
| M1 — migration/observability postmortem navigation | **still open** | The postmortem still has two bad relative links and an old test filename; see L1. |
| M2 — worker-reset lesson only in postmortem | **still open** | The postmortem requires seven safeguards for worker/state/artifact changes ([postmortem](../postmortems/2026-07-31-staging-processing-state-reset.md:116)), but the active plan template has only generic rollout/compatibility prompts ([plan template](../plans/0000-template.md:53)). No checklist or planning-skill rule names the seven safeguards. |

## Prioritized findings

| ID | Priority | Finding | Exact evidence | Required documentation outcome |
| --- | --- | --- | --- | --- |
| R1 | **blocking** | Engineering registry detail IDs are not unique: the operational job is incorrectly titled EJ-014, leaving EJ-015 without a detail. | Current-state EJ-015 ([engineering jobs](../engineering-jobs.md:48)); first EJ-014 heading ([engineering jobs](../engineering-jobs.md:196)); feedback EJ-014 heading ([engineering jobs](../engineering-jobs.md:273)). | Rename only the operational detail to EJ-015 and retain history. Add a structural regression for one detail per current-state job. |
| R2 | **blocking** | EJ-019's current row, detail, and append-only history disagree on the status. | `Validated` row ([engineering jobs](../engineering-jobs.md:52)); `Delivered` detail ([engineering jobs](../engineering-jobs.md:482)); latest history ([engineering jobs](../engineering-jobs.md:540)). | Choose one status justified by repository versus live evidence, update row/detail, and append one correction row; do not edit history. |
| R3 | **next** | The documented `make worktree` contract remains false on a fresh worktree. | Bootstrap environment lacks processing values ([create-worktree](../../scripts/create-worktree.py:16)); copied defaults are false ([`.env.example`](../../.env.example:35)); the Django check rejects false values ([system check](../../src/backend/selfie_search/apps.py:114)). | Put the same safe processing defaults into `TEST_ENVIRONMENT` (or run the shared wrapper) and add a smoke regression for the exact bootstrap Django check. Do not call EJ-013 Validated until it passes. |
| R4 | **next** | Product registry status/evidence is stale in four separate places. | PJ-006 Candidate/Planned mismatch ([product jobs](../product-jobs.md:38), [product jobs](../product-jobs.md:130)); PJ-009 Candidate despite route/test ([product jobs](../product-jobs.md:196), [test](../../src/backend/picflow/tests/test_views.py:1922)); PJ-012 says a merged PR is open ([product jobs](../product-jobs.md:240)); PJ-015 conflicts with EJ-019 ([product jobs](../product-jobs.md:304), [engineering jobs](../engineering-jobs.md:493)). | Reconcile each current row/detail with an append-only history correction. Keep actual live deployment acceptance explicitly unverified where no repository evidence proves it. |
| N1 | **retire** | The 2026-08-01 paid-cart future-work file and several current claims predate the now-merged ADR 0030 cart implementation. | Existing finding says no cart ([future work](https://github.com/peter-nikitin/photo-prjct/blob/be22bdd0118fbc6f416b96cc31683890ec930540/docs/future-work/2026-08-01-paid-photo-cart-action.md#L3)); ADR 0030 accepts the server-side cart ([ADR 0030](../adr/0030-use-anonymous-server-side-event-carts.md:48)); cart routes and dual-gate enforcement exist ([URLs](../../src/backend/commerce/urls.py:7), [views](../../src/backend/commerce/views.py:93)); PJ-005 and EJ-024 still say no cart ([product jobs](../product-jobs.md:118), [engineering jobs](../engineering-jobs.md:411)). | Retire or replace the future-work file with a short supersession note to ADR 0030/PJ-016/EJ-025; remove only the now-false “no cart” clauses. Do not imply checkout, payment, entitlement, or original delivery exists. |
| N5 | **next** | Paid-watermark/cart current-state text was written before merge and is already stale about release evidence. | Architecture says the cart has no PR, CI, or deployment ([architecture](../architecture.md:376)); PJ-016 says the same ([product jobs](../product-jobs.md:230)); EJ-024 says worker image/deployment are unevidenced ([engineering jobs](../engineering-jobs.md:415)); EJ-025 says no deployment or cron installation occurred ([engineering jobs](../engineering-jobs.md:431)). Current `be22bdd` passed [CI run 32457775703](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775703) and automatic [Deploy run 32457775668](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775668), whose deployment job and final `DEPLOY_RESULT=success` completed. The committed success path installs the cart-cleanup schedule ([deployment script](../../deploy/apply-deployment.sh:907)), but this audit did not read the live crontab. | Record merged/CI/deployed-but-dark evidence. Keep PJ-016/EJ-024 `In progress` while their gates and real assets remain inactive. Replace EJ-025's absolute cron claim with “installation expected from the successful deployment; live schedule/run unverified” until a read-only host check is recorded. |
| N2 | **next** | Architecture states current DNS routing and trusted TLS without citing current observational evidence. | Asserted current state ([architecture](../architecture.md:356)); the runbook defines post-deploy checks ([deployment runbook](../runbooks/deployment.md:79)); the audited SHA has a successful automatic [Deploy run](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775668) and subsequent successful [public monitor run](https://github.com/peter-nikitin/photo-prjct/actions/runs/32461320506). | Cite the dated current-main release/monitor evidence or phrase the topology as the accepted assignment. Do not turn an Actions conclusion into an uncaveated claim about all present VM/DNS state. |
| N3 | **next** | Credential-hygiene future work is now a current design trigger but still describes the retired topology and deleted Compose path. | It names staging and `docker-compose.prod.yml` ([future work](../future-work/2026-08-07-runtime-credential-hygiene.md:5), [future work](../future-work/2026-08-07-runtime-credential-hygiene.md:32)); canonical transition/Lockbox delivery are current ([ADR 0028](../adr/0028-operate-one-canonical-deployment.md:49), [engineering jobs](../engineering-jobs.md:447)). | Reframe the historical inspection as dated evidence, replace the deleted path, and promote the fired trigger into EJ-018 planning without performing host cleanup or rotation. |
| N4 | **next** | Direct-media and lifecycle findings still use former staging/current-disabled wording, which obscures their real trigger. | Event-scale staging trigger ([future work](../future-work/2026-07-31-direct-media-performance-thresholds.md:18)); lifecycle activation claim ([future work](../future-work/selfie-search-lifecycle-expiration-sla.md:8)); disabled-search claim ([future work](../future-work/selfie-search-missing-temporary-object-reconciliation.md:10)). | Preserve the technical risks, but say “canonical-deployment measured evidence is unrecorded” and retain the concrete queue-age/ObjectMissing/performance triggers. |
| L1 | **next** | Static local-link scan finds three nonexistent paths in the audited documents. | Historical EJ-006 link ([engineering jobs](../engineering-jobs.md:514)); postmortem runbook link ([postmortem](../postmortems/2026-08-07-staging-deployment-after-parallel-migrations.md:132)); renamed reconciliation test link ([postmortem](../postmortems/2026-08-07-staging-deployment-after-parallel-migrations.md:180)). | Repair links without rewriting historical incident facts: use the archived/present promotion reference where appropriate, `../runbooks/django-migration-conflicts.md`, and current `scripts/reconcile_deploy_issue.py`/`tests/test_reconcile_deploy_issue.py`. |
| R5 | **next** | The literal worker-selector command is still broken; this is a developer-interface defect, not production behavior. | Command and stated workaround ([future work](https://github.com/peter-nikitin/photo-prjct/blob/be22bdd0118fbc6f416b96cc31683890ec930540/docs/future-work/2026-08-10-worker-selector-import-path.md#L5)); reproduced audit result: two collection failures for missing `photo_worker`. The root quality contract expects `src/worker` on `pythonpath` ([repository contract](../../tests/test_repository_foundation.py:265)). | Its revisit trigger has fired because this audit and the remediation plan require the supported focused command. Adjust only test invocation/import path and add its regression; do not change worker packaging speculatively. |
| R6 | **next** | The worker-reset postmortem's mandatory safeguards are still absent from the active planning workflow. | Seven worker safeguards ([postmortem](../postmortems/2026-07-31-staging-processing-state-reset.md:116)); generic template ([plan template](../plans/0000-template.md:53)); no matching rule in `.agents/skills` or the repository contract. | Add a worker/state/artifact-change checklist to the plan template and `$write-plan` guidance, with a repository structural regression, before the next worker-contract plan. |
| R7 | **next, separate architecture work** | Service backup/recovery remains the highest-value operational debt and cannot be solved by this documentation repair. | EJ-010 boundary ([engineering jobs](../engineering-jobs.md:187)); open RPO/RTO/recovery decisions ([architecture](../architecture.md:622)); one canonical customer-serving deployment under ADR 0028. | Prepare and approve a separate recovery specification/ADR covering RPO/RTO, database and media-metadata scope, off-host retention, restore authority, and a non-destructive drill before any cloud mutation. |

## Healthy current boundaries

- The one-canonical-deployment rollout is not listed as outstanding debt: the old promotion model is
  superseded, the workflow automatically handles `main`, and real runtime-gate consumers are present
  ([ADR 0028](../adr/0028-operate-one-canonical-deployment.md:45), [workflow](../../.github/workflows/deploy.yml:3), [gate service](../../src/backend/feature_flags/services.py:29)). The audited SHA also has successful CI and automatic Deploy runs; direct VM and runtime-gate state remain outside this audit.
- ADR lifecycle/index integrity is now sound: all numbered records agree with the index, including
  the new accepted paid-watermark and anonymous-cart decisions ([ADR index](../adr/README.md:49)).
- The cart implementation is safely dark by default: it requires both cart and watermark gates
  ([cart views](../../src/backend/commerce/views.py:247)), and deployment tests assert that deployment
  neither creates nor enables the cart gate ([repository contract](../../tests/test_repository_foundation.py:1195)).

## Verification performed

- Compared this commit with the prior-audit commit `0eb99bd` and independently re-read all required
  docs plus the owning code, tests, workflow, Make targets, and deployment configuration.
- Parsed 30 numbered ADR record statuses against the index: no missing numbered record and no status
  mismatch.
- Checked current product and engineering table/detail identifier sets: product has 16 matching
  rows/details; engineering has 25 rows/details but duplicate `EJ-014` and missing `EJ-015`.
- Checked local Markdown targets in the requested corpus: 51 files, three missing local paths (L1).
- Ran `make test TESTS="src/worker/tests/test_runner.py src/worker/tests/test_contracts.py"`; it
  failed during collection with two `ModuleNotFoundError: No module named 'photo_worker'` errors,
  matching R5. No production or documentation behavior was changed.
- Queried GitHub Actions for exact SHA `be22bdd`: CI run `32457775703`, automatic Deploy run
  `32457775668`, and later public monitor run `32461320506` all completed successfully. The Deploy
  job reached `DEPLOY_RESULT=success`; no VM shell or feature-flag query was performed.
- Ran `git diff --check` before writing this report: clean.

## Limits

No VM, cloud, DNS, storage, or database query was made. GitHub Actions confirms a successful release
workflow for the audited SHA, but this audit does not independently confirm the VM marker/current
containers, DNS/TLS, runtime feature-flag rows, cron installation, backup schedule, credential
exposure, bucket lifecycle, or customer outcomes. It identifies where the documentation properly
keeps that boundary explicit or incorrectly presents it as repository- or Actions-proven.
