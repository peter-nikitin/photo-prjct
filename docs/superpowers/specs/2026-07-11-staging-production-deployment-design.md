# Staging and Production Deployment Design

- Date: 2026-07-11
- Status: Proposed
- Owner: project maintainer
- Related architecture: [Current architecture](../../architecture.md#current-architecture--implemented), [Evolution stages](../../architecture.md#evolution-stages)
- Related ADRs: [ADR 0003](../../adr/0003-docker-compose-yandex-cloud.md)

## Outcome

Evolve the existing single-VM deployment into two isolated environments while preserving the
accepted GitHub Actions -> GHCR -> Yandex Cloud VM -> Docker Compose architecture:

- every merge to `main` automatically deploys the immutable commit image to staging;
- production deploys only after an explicit manual approval and promotes the exact image already
  verified on staging;
- the existing preemptible VM becomes staging;
- production infrastructure is created later, after evidence supports its configuration and the
  maintainer explicitly approves the resulting billable change.

## Boundaries

This design does not select a production VM platform, size, disk class, availability target, HTTPS
edge, backup product, or monitoring product. Those choices require measured evidence and, where
durable, an ADR. It does not introduce Kubernetes, a managed database, or multiple application
services.

The first delivery increment hardens the current VM as staging and makes deployment promotion
repeatable. It must not create or resize billable Yandex Cloud resources.

## Environment model

### Staging

The current preemptible VM is designated `staging`. It may stop unexpectedly and therefore cannot
be an availability boundary or the only holder of valuable data. It runs its own Django and
PostgreSQL containers, volumes, configuration, credentials, hostname, and GitHub Environment.

The staging database contains synthetic, anonymized, or disposable data. A merge to `master`
builds one image tagged by commit SHA, deploys that exact digest to staging, runs health checks,
and records the deployed digest. Concurrent staging deployments are serialized; a newer commit may
cancel an older deployment that has not started applying migrations.

### Production

Production is a separate, non-preemptible VM with separate disk, network controls, secrets,
credentials, hostname, GitHub Environment, Compose project, database volume, and backup lifecycle.
It is not a second Compose project on the staging VM.

Production deployment is a manually dispatched promotion that accepts a commit SHA or image digest
already deployed successfully to staging. GitHub's `production` Environment provides the required
review gate. The workflow does not rebuild the image. It verifies the selected artifact, takes the
pre-deploy safety action defined by the database/backup runbook, applies the Compose deployment,
runs health checks, and records the release.

## Deployment evolution

### Phase 0: Inventory and safety baseline

Treat the current cloud as unknown until a read-only inventory confirms the active `yc` profile,
cloud, folder, VM, disks, network, subnet, security groups, public addresses, service accounts, and
labels. Record stable resource IDs without tokens, private keys, secret values, or generated IAM
tokens. Diagnose the current CLI connection timeout before depending on live inventory.

No mutation is required in this phase.

### Phase 1: Make the current VM a reliable staging target

Rename deployment concepts and GitHub secrets from generic `VM_*` values to a `staging`
environment. Keep environment configuration in GitHub Environment secrets, pin the application
image by SHA/digest, add Compose and HTTP health checks, serialize deployment, capture useful logs
on failure, and document recovery after a preemption.

At this phase, merge to `main` becomes the automatic staging trigger. Manual dispatch remains
available for recovery and controlled redeployment.

### Phase 2: Collect production-sizing evidence

Staging and load-test runs collect the data listed below. Measurements must represent the expected
MVP workload, including image upload/processing only after those capabilities exist. Until then,
the project records that production sizing is provisional and avoids premature infrastructure.

### Phase 3: Provision production

Create production only after the readiness evidence is reviewed. The production proposal states
the selected VM platform, vCPU, RAM, disk type and size, public/static IP need, backup retention,
expected monthly cost, quotas, and headroom. Creating or changing any of these resources requires a
separate explicit confirmation immediately before the `yc` mutation.

### Phase 4: Enable controlled promotion

Configure the protected GitHub `production` Environment and its separate secrets. Prove staging
deployment, production promotion of the same image digest, health checks, rollback to the previous
digest, and database restore before production handles valuable data.

### Phase 5: Reassess separation

Docker Compose on one production VM remains the default. Revisit database or worker separation only
when measurements show resource contention, recovery objectives cannot be met, or independent
scaling reduces operational risk enough to justify added cost. Revisit the VM deployment platform
only through an ADR that supersedes ADR 0003.

## Production-sizing evidence

The production configuration decision must retain the following context in a dated sizing report:

| Category | Required data | Decision influenced |
| --- | --- | --- |
| Traffic | expected and measured requests/second, peak concurrency, daily active users, peak duration | web vCPU/RAM and headroom |
| Latency | p50/p95/p99 response time by important endpoint and error rate under peak load | acceptable VM size and scaling trigger |
| Django/Gunicorn | worker count, request timeout, CPU and RSS per worker, restart/OOM history | vCPU/RAM and process limits |
| PostgreSQL | database size and growth/day, active connections, CPU/RAM, cache hit rate, slow queries, write IOPS, migration duration | colocated DB feasibility, RAM, disk type |
| Storage | container image footprint, PostgreSQL volume size, log growth, temporary processing space, projected 3/6/12-month growth | boot/data disk size and retention |
| Disk behavior | peak and sustained read/write IOPS, throughput, latency, snapshot/restore duration | network SSD/HDD choice and provisioned size |
| Deployments | image pull time, migration time, health-check stabilization, total deploy and rollback duration | release timeout and rollback procedure |
| Reliability | tolerated downtime, target RTO/RPO, preemption incidents on staging, restore-test results | non-preemptible requirement, backups, later HA review |
| Network | ingress/egress volume, peak bandwidth, static IP/DNS/TLS requirements | network and address configuration |
| Workload roadmap | ingestion batch size, photos/event, concurrent uploads, derivative generation and recognition CPU/RAM/time | timing of worker separation and capacity |
| Cost | current staging cost, forecast by SKU, backup/traffic/storage cost, monthly budget ceiling, desired headroom | affordable production option |

Each load-test record also captures application commit, image digest, VM platform/configuration,
dataset size, test duration, workload profile, and monitoring interval so results remain comparable.

## Production readiness gate

Provisioning production is ready for a decision when:

1. the intended near-term workload and growth horizon are written down;
2. a repeatable representative test has run on staging and exposes CPU, memory, disk, database, and
   latency measurements;
3. the proposal includes at least two viable configurations with monthly cost estimates and states
   the chosen headroom;
4. RTO, RPO, backup retention, and a successful restore test are documented;
5. required network exposure and SSH access are minimized and explicit;
6. the maintainer has approved the architecture decision, operating cost, and exact billable
   `yc` commands.

Early in foundation work, missing future photo-processing measurements do not block staging. They
do block treating an initial production size as final; the sizing report must state assumptions and
the metric thresholds that trigger resizing or service separation.

## Project Yandex Cloud skill

Create `.agents/skills/manage-yandex-cloud/` as a project-specific operational skill. It is scoped
to this repository's folders, Compute Cloud VM deployment, disks, VPC resources, IAM identities,
and later resources explicitly accepted in architecture documents. It uses installed `yc` help and
official Yandex Cloud documentation as authoritative sources; the referenced community skill is a
command-discovery input, not a safety authority.

The skill follows this operation classification:

- **Read-only:** may run without additional confirmation after showing the active profile, cloud,
  and folder; must redact tokens, private key material, secret payloads, and credentials.
- **Operational mutation with no pricing effect:** present target, current state, exact command,
  impact, validation, and rollback; request confirmation for destructive or availability-affecting
  operations such as stop, restart, metadata replacement, access changes, and deletion.
- **Potential pricing mutation:** always stop and obtain explicit manual confirmation immediately
  before execution. This includes create, resize, disk expansion/type changes, snapshots/backups,
  static public IPs, managed services, load balancers, registries/storage with retention, paid
  support or billing changes, committed consumption, quotas intended to enable spend, and enabling
  chargeable features. Confirmation must name the resource, folder, old/new configuration,
  estimated price delta or state that it is unknown, and exact command(s). Approval of a plan is
  not approval to execute these commands.

Unknown commands default to the stricter class. The skill never runs `yc config list` in logs where
credentials may be exposed; it reads only non-secret profile selectors individually. It prefers
explicit `--folder-id`, stable resource IDs in automation, JSON output, labels (`project`, `env`,
`managed-by`), asynchronous operation tracking, deletion protection where supported, and a
post-change read-only verification.

The skill includes a concise reference describing the known resource inventory and environment
mapping. Live discovery updates that reference only after the maintainer reviews the diff; secret
or ephemeral credential values are never persisted.

## Failure handling and rollback

A failed staging health check prevents production eligibility and retains the prior image identity.
A failed production promotion stops before subsequent steps, captures Compose/application status,
and redeploys the previously recorded digest when application rollback is safe. Database migrations
must be backwards-compatible or have an explicit restore/roll-forward procedure; image rollback
alone is not assumed to reverse schema changes.

Loss of the preemptible staging VM is recovered from repository configuration and environment
secrets. Valuable staging data must not be required for recovery. Production recovery depends on a
tested disk/database backup and restore runbook before valuable data is admitted.

## Verification strategy

- Validate workflow syntax and configuration without contacting production.
- Test automatic staging deployment from a disposable change or explicit test commit.
- Verify the deployed image digest equals the workflow artifact.
- Exercise staging preemption recovery.
- Exercise failed health check behavior and confirm production remains unavailable for promotion.
- Exercise production approval, same-digest promotion, rollback, and restore before launch.
- Validate the project skill structurally and pressure-test its response to a read-only inventory,
  VM restart, VM resize, disk deletion, static IP creation, and ambiguous `yc` command request.

## Decisions requiring an ADR

A proposed ADR should record the two-environment promotion model, reuse of the preemptible VM for
staging, separate non-preemptible production infrastructure, and same-artifact manual promotion.
It extends ADR 0003 rather than superseding its Docker Compose/Yandex Cloud decision. The exact VM
size and reversible workflow syntax belong in sizing records and implementation plans, not the ADR.

## Sources

- [Yandex Cloud CLI command reference](https://yandex.cloud/en/docs/cli/cli-ref/)
- [Authenticate the CLI as a service account](https://yandex.cloud/en/docs/cli/operations/authentication/service-account)
- [Update a VM](https://yandex.cloud/en/docs/compute/operations/vm-control/vm-update)
- [Stop, start, or restart a VM](https://yandex.cloud/en/docs/compute/operations/vm-control/vm-stop-and-start)
- [Yandex Cloud budgets](https://yandex.cloud/en/docs/billing/concepts/budget)
- [Community Yandex Cloud CLI skill](https://github.com/elsvv/yandex-cloud-cli-skill/blob/main/SKILL.md)
