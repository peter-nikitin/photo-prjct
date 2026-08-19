# Single Deployment and Runtime Feature Gates Design

- **Status:** Approved on 2026-08-19
- **Date:** 2026-08-18
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md)
- **Related ADRs:**
  [ADR 0003](../../adr/0003-docker-compose-yandex-cloud.md),
  [ADR 0005](../../adr/0005-promote-images-through-staging.md),
  [ADR 0006](../../adr/0006-yandex-object-storage-media.md),
  [ADR 0011](../../adr/0011-use-minimal-shared-https-rollout.md),
  [ADR 0013](../../adr/0013-use-direct-private-object-storage-ingestion.md),
  [ADR 0018](../../adr/0018-use-managed-yandex-monitoring.md),
  [ADR 0023](../../adr/0023-store-consented-selfie-search-feedback.md),
  [ADR 0026](../../adr/0026-use-lockbox-for-environment-secrets.md),
  [ADR 0028](../../adr/0028-operate-one-canonical-deployment.md)
- **ADR impact:** Supersedes ADR 0005 and ADR 0026 through accepted ADR 0028. ADR 0028 also
  replaces the environment-isolation mechanism in ADR 0011 and the environment
  metric label in ADR 0018 while retaining their HTTPS, rollback, monitoring, and public-probe
  decisions. Conforms to ADRs 0003, 0006, 0013, and 0023.

## Outcome

Operate one canonical FindMe Photo deployment on the existing VM and keep incomplete
customer-facing capabilities hidden with database-backed runtime feature gates. Remove the false
`staging` and unused `production` identities from active deployment code, secrets, monitoring,
Object Storage bucket names, and runbooks. Do not replace them with `live`, `stable`, or another
name for the sole deployment.

Use `catalog` as the only active name for the Django module that owns events, event folders,
photos, and gallery behavior. Remove the inherited `picflow` package, Django app label, migration
graph identity, PostgreSQL object names, content types, and permissions through a controlled
one-time schema-identity cutover.

A future test deployment is outside this design. If one becomes necessary, it receives an explicit
`test` suffix and isolated compute, data, credentials, Object Storage buckets, and deployment
configuration. The current implementation does not add multi-environment abstractions in advance.

## Release model

Every accepted merge to `main` remains eligible for automatic immutable-image deployment to the
canonical VM after CI. There is no staging promotion workflow and no second release SHA to track.
The deployed application may contain incomplete user-facing implementation only when every public
entry point and side effect is protected by a runtime feature gate whose initial state is `off`.

Runtime gates control exposure, not deployment safety. They do not make schema changes, startup
configuration, shared middleware, base templates, worker protocol changes, or destructive data
operations safe. Those changes retain focused tests, complete CI, deployment preflights, in-process
image rollback, and an explicit maintenance procedure when data or availability is at risk.

## Runtime feature-gate model

Create a focused Django `feature_flags` module with one database model and one evaluation
interface. A feature row has a stable key, a short operator description, one of three states, and
standard change timestamps:

- `off`: unavailable to every request and prohibited from initiating feature-specific side effects;
- `staff`: available only to an authenticated active Django user with `is_staff=True`;
- `on`: available to every otherwise-authorized caller.

The evaluation interface accepts a feature key and the current user and returns a Boolean. A
missing row evaluates to `off`. Unknown rows have no behavior because call sites use code-owned
feature keys. The interface performs no percentage rollout, cohort assignment, cookie override,
query-parameter override, or per-user allowlist.

Django Admin is the only runtime mutation interface. Ordinary staff do not automatically receive
permission to change gates; the existing Django permission model grants that capability
explicitly. Django Admin history supplies the operator audit record. Gate changes take effect on
the next request without application restart, deployment, or a delayed process cache.

Every gated feature protects all of its authoritative seams:

- navigation, controls, and explanatory copy are absent when the current user cannot access it;
- direct HTTP access fails closed with `404` before feature-specific reads or writes;
- commands, job enrollment, and asynchronous side effects check the gate at their authoritative
  server-side entry point rather than relying on hidden UI;
- `staff` mode uses Django authentication only and cannot be enabled by a browser-controlled
  header, cookie, query string, or static asset;
- storage, worker, privacy, and configuration preconditions remain independent requirements that a
  feature gate cannot bypass.

Existing environment switches that protect infrastructure or validate complete startup
configuration remain operational capability switches. A customer-facing capability may require
both its operational configuration and its runtime release gate. This delivery does not
mechanically convert every existing setting into a database flag.

A release flag is temporary. After a feature has operated in `on` state and rollback through the
flag is no longer needed, a focused cleanup removes the conditional branch and row. Rejected
features remove their guarded implementation. Permanent product authorization and event
publication state do not become release flags.

## Staff-visible event publication state

Keep three explicit product states:

- `unavailable`: displayed as `Недоступно`, absent from the site for everyone, and used as the
  default for a newly created event;
- `draft`: displayed as `Черновик` and available on the site only to an authenticated active user
  with `is_staff=True`;
- `published`: available through the existing public catalog and event flows.

This is durable event publication state, not a runtime release flag. Introduce one catalog-owned
access interface that returns events visible on the site to the current user. Anonymous users,
ordinary authenticated users, and photographers receive only `published` events. Active staff
receive `published` plus `draft` events. `unavailable` remains absent from the site even for staff.

The schema migration introduces `unavailable` and assigns it as the default without rewriting
event rows. On deployment, existing `draft` rows immediately acquire the new staff-only semantics
and remain hidden from every non-staff user. The maintainer then reviews them in Django Admin and
manually changes those that nobody should see to `unavailable`; there is no automated data
migration or separate activation step for that decision.

The same access decision applies to every event-scoped site seam: catalog listing, event detail,
gallery media and original download, selfie upload and gallery-face submission, search polling and
ready results, and feedback submission. A non-staff request for a `draft` event receives `404` and
cannot obtain a signed media URL, create a search, poll a bearer result, or submit feedback. Worker
execution may finish an already authorized staff-preview search; public retrieval still evaluates
the requesting user before exposing the result.

Draft event cards and detail pages render the exact warning `Черновик — виден только администраторам`
so the maintainer cannot confuse preview state with publication. Staff-preview requests do not emit
public analytics. The `draft` state requires the same valid event timezone as `published` because
the normal gallery and time-filter behavior is available during preview.

## Canonical deployment identity

Active code uses unqualified names for the sole deployment:

- the GitHub workflow is `deploy.yml`, displayed as `Deploy`;
- jobs do not declare a GitHub Environment;
- the deployment concurrency group is `deploy`;
- the Lockbox manifest is `deploy/environment-secrets.json`;
- consumers are `deploy`, `remote-check`, `public-monitor`, and `local-web`;
- the remote helper is `deploy/run-remote.sh`;
- deployment issue reconciliation is deployment-scoped rather than staging-scoped;
- the Compose project becomes `photo-prjct`;
- active Compose files use responsibility names such as `docker-compose.deployment.yml` and
  `docker-compose.https.yml`;
- logs and metrics retain service, route, method, and status dimensions but remove the constant
  environment dimension;
- `DEPLOYMENT_TARGET`, deployment-target marker files, and accepted-value branches for `staging`
  and `production` are removed where they serve only environment naming.

GitHub Actions obtains short-lived Yandex Cloud access without a GitHub Environment. The reviewed
OIDC subject is restricted to this repository and `refs/heads/main`; the existing exact
`workflow_ref` allowlist and resource-level Lockbox roles remain mandatory. One Lockbox secret is
the sole persistent authority for deployed credentials. Runtime containers still do not read
Lockbox.

The sole maintainer retains full interactive access to the canonical Lockbox secret and the project
cloud resources. The supported `local-web` consumer may project the application and media
credentials needed to reproduce deployed behavior locally. Projection remains ephemeral and keeps
the existing mandatory local overrides: loopback-only exposure, `DEBUG=True`, the local Compose
database host and identity, and no remote deployment target. It must not persist secret payloads in
a repository or worktree `.env` file.

Local use of a real database snapshot or private media is permitted for the authorized maintainer.
Cloning data, starting the local application, operating storage, and changing the deployed system
remain separate explicit commands so a normal local launch cannot accidentally connect to or
mutate the deployed PostgreSQL database. Revisit narrower human roles and local projections when a
second maintainer or contributor receives infrastructure access.

The deployment continues to use immutable GHCR images, Docker Compose, Nginx/Certbot, read-only
migration preflight, private-media preflight, public HTTPS health checks, the successful-image
marker, and in-process restoration of the prior image. Removing environment names does not weaken
those contracts.

## Persistent Docker identity cutover

The current `photo-prjct-staging` Compose name identifies live PostgreSQL and certificate volumes;
it cannot be changed by search-and-replace. The target has no legacy staging identifier:

- Compose project: `photo-prjct`;
- PostgreSQL volume: `photo-prjct_pgdata`;
- Let's Encrypt and ACME volumes use the same canonical project prefix;
- containers and networks use the canonical project identity.

Changing this identity is a maintenance operation with verified database backup and restore,
certificate backup, stopped writers, explicit source and destination volume checks, and rollback
to the old project identity before the old volumes are retired. The design does not retain an
external-volume alias to a staging-named volume after acceptance.

## Object Storage bucket identity

Yandex Object Storage bucket names are immutable. Replace the three existing buckets with newly
created canonical buckets and copy the current objects before switching application configuration:

| Role | Current name | Canonical name |
| --- | --- | --- |
| Public event covers and legacy file media | `project-storage-dev-2026` | `findme-photo-public-media-b1g2qttg` |
| Private originals, accepted derivatives, and transient processing input | `hires-staging` | `findme-photo-private-media-b1g2qttg` |
| Consented selfie feedback | `findme-selfie-feedback-staging-b1g2qttg` | `findme-photo-selfie-feedback-b1g2qttg` |

Read-only inventory on 2026-08-19 found 8 current public objects using 14 MiB, 54,778 private
objects using 76.3 GiB, and 61 feedback objects using 111 MiB. The private bucket contains 24,001
durable originals using 66.9 GiB, 24,071 durable derivatives using 7.2 GiB, and 6,706 temporary
`processing-staging` objects using 2.2 GiB. Reconfirm all figures immediately before execution.

Each target bucket receives the source role's maximum size, storage class, bucket and object ACLs,
CORS, policy, versioning mode, lifecycle, private-endpoint posture, and encryption/KMS behavior
before any application switch. New buckets necessarily receive new resource IDs. The public
target copies the current object versions and enables versioning; historical non-current versions
remain in the retained source bucket and are not required by application data. Public objects keep
their explicit anonymous-read object ACL. Private and feedback buckets remain non-public, and the
feedback target remains distinct and KMS/lifecycle governed as required by ADR 0023.

Application data continues to store object keys rather than bucket URLs, so durable object keys do
not change and no database migration is required. Copy only current durable objects. For private
media, copy `originals/` and `derivatives/`; drain active processing and selfie searches instead of
copying temporary upload, selfie, or processing objects. New preview candidates use the semantic
prefix `processing-pending/`, and the seven-day lifecycle rule moves from
`processing-staging/previews/` to `processing-pending/previews/`.

Every bucket uses its own migration window. Create and configure the target, copy while the source
is the only configured authority, compare complete source/target manifests, pause the applicable
writers, wait for issued grants to expire, copy and verify any final delta, then deploy the target
bucket variable. The maximum currently configured grant lifetime is the ten-minute photographer
upload grant. Public reads continue during background copying. The final private-media delta may
use a brief full web/worker maintenance stop because selfie bytes are written synchronously by the
web process; the design does not add a second write-control mechanism solely for this cutover.

The source bucket remains unmodified and readable but is removed from application configuration.
This preserves already issued signed URLs, cached public object URLs, and immediate rollback. It is
not a second writable authority. Retain each source for 14 days and through at least two successful
deployments plus the focused rollback check, then delete it only through a separately approved
destructive operation. The feedback lifecycle approval digest is recalculated because it binds the
target bucket name and KMS key.

Future test buckets, if approved later, use the corresponding canonical role name plus `-test`
before the uniqueness suffix.

## Catalog module identity

Rename `src/backend/picflow` to `src/backend/catalog`. The `catalog` module owns the current
`Event`, `EventFolder`, and `Photo` models, event administration, public gallery queries and forms,
capture-time projection, and their management commands. This is a name correction, not a split or
redesign of those responsibilities.

The active system contains no compatibility package, import alias, `AppConfig.label="picflow"`,
legacy migration module, or dual model registration. Fresh databases and the existing database use
the same `catalog` app label and migration graph.

Rewrite the project migration files as one coordinated identity change:

- move the `picflow` migration module to `catalog`;
- replace graph dependencies, historical model references, raw SQL identifiers, and migration
  helper imports with `catalog` throughout every project app;
- preserve migration numbers and modeled schema behavior except for the app and PostgreSQL object
  identities;
- teach migration-immutability verification to recognize this single reviewed whole-graph identity
  transition while continuing to reject later edits to the rewritten deployed migrations.

The existing PostgreSQL cutover runs with web and worker writers stopped and performs one
transaction that:

- verifies the exact expected applied project migration set and absence of conflicting `catalog`
  rows or content types;
- renames all `picflow_*` tables, sequences, indexes, and constraints discovered by PostgreSQL
  introspection to their exact `catalog_*` targets;
- changes the project migration records from app `picflow` to app `catalog` without changing their
  migration names or applied timestamps;
- updates the existing Django content-type rows from `picflow` to `catalog`, preserving content-type
  primary keys so permissions and admin log references remain attached;
- verifies the resulting schema, migration graph, content types, permissions, row counts, and
  foreign-key integrity before commit.

No data row is recreated or copied. The cutover procedure fails before mutation on any unexpected
schema object, migration row, duplicate target, or count mismatch. Rollback before commit is the
database transaction; rollback after service activation restores the captured database and the
previous image together.

Historical prose documents may continue to mention the former names when accurately describing a
past deployment or incident. Active code, configuration, current architecture, commands, tests,
and current runbooks use only the canonical names.

## Delivery isolation

The outcome is delivered through independently reviewable milestones. Runtime feature gates are
available before incomplete features rely on them. Naming-only deployment changes precede any
persistent identity cutover. Object Storage buckets are migrated one at a time. Docker persistent
identity and the catalog schema identity each receive their own maintenance window and are never
combined with one another or with a bucket migration.

An incomplete later milestone does not invalidate an already accepted earlier milestone. No
milestone leaves two configured or writable authorities after its acceptance. Old bucket and
volume identities may exist only as bounded, unconfigured recovery artifacts until their fixed
retirement gate passes.

## Failure and recovery boundaries

- A failed ordinary application deployment restores the previously successful image through the
  existing deployment entrypoint.
- A feature judged unready returns to `off`; this does not undo schema or background side effects.
- A failed Lockbox/OIDC identity cutover restores the previous workflow before obsolete identity
  bindings are removed.
- A failed bucket migration restores the source bucket variable before writers resume; the source
  bucket remains intact throughout copying, cutover, and the fixed rollback-retention period.
- A failed Compose identity cutover retains the source volumes until the canonical deployment has
  passed database, HTTPS, renewal, and rollback checks.
- A failed catalog cutover rolls back its transaction before writers restart or restores the
  verified database backup and previous image as one recovery action.

No old bucket, volume, secret authority, OIDC subject, or schema identity is deleted until the
canonical path has passed its focused live checks.

## Acceptance criteria

1. A feature can be changed through Django Admin from `off` to `staff` to `on` without restart or
   deployment; normal users cannot discover or invoke it in `off` or `staff` state.
2. Direct endpoint calls and background enrollment fail closed whenever the caller cannot access
   the feature.
3. An event in `draft` publication state is fully previewable through the normal site by active
   staff, carries the warning `Черновик — виден только администраторам`, emits no public analytics, and remains
   `404` across catalog, detail, media, download, selfie-search, result, and feedback seams for
   every non-staff caller. An `unavailable` event remains absent from the site for staff. Existing
   `draft` rows adopt staff-only visibility on deployment and can then be manually reclassified.
4. `main` deploys one immutable image through the generic `Deploy` workflow, with no promotion
   workflow or active `staging`, `production`, `live`, or `stable` deployment identity.
5. GitHub OIDC remains repository-, branch-, and workflow-restricted, and consumer projections
   expose no additional secret values.
6. PostgreSQL data, certificate renewal, public HTTPS health, monitoring, private-media access, and
   application rollback pass after the canonical Compose identity cutover.
7. Each canonical Object Storage bucket contains the expected durable objects and reproduces its
   required public, private, lifecycle, CORS, versioning, encryption, and access behavior after
   configuration cutover; no database object key changes.
8. Active source, migration graph, Django app registry, PostgreSQL schema objects, content types,
   permissions, tests, and current operational documentation contain no `picflow` identity.
9. A fresh database migrates from zero with the rewritten `catalog` graph, and the transformed
   existing database reports no pending or conflicting migrations.
10. Full repository verification passes after every code milestone, and focused live verification
   passes before each old persistent identity is retired.

## Excluded

- Provisioning a staging, test, preview, canary, or second production deployment.
- High availability, a non-preemptible replacement VM, managed PostgreSQL, Kubernetes, load
  balancing, or zero-downtime schema cutover.
- Percentage rollout, experimentation cohorts, per-customer targeting, or a third-party feature
  flag platform.
- Treating feature gates as authorization, publication state, schema-safety, or worker-protocol
  compatibility.
- Copying production data into a future test deployment.
- Renaming historical ADR, plan, specification, or postmortem prose that accurately records the
  former system.
