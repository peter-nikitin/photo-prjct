# PostgreSQL observability

- Date: 2026-10-04
- Status: Proposed design for maintainer review; no collection or alerts activated
- Related architecture: [Current deployment and database](../../architecture.md#current-architecture--implemented), [Search](../../architecture.md#search), [Open decisions](../../architecture.md#open-decisions)
- Related ADRs: [0002](../../adr/0002-postgresql-system-of-record.md), [0018](../../adr/0018-use-managed-yandex-monitoring.md), [0052](../../adr/0052-notify-only-on-actionable-service-degradation.md), [0053](../../adr/0053-reconcile-observability-independently-on-main.md)
- ADR impact: Conforms to ADRs 0002, 0018, 0052 and 0053. ADR 0018 excluded PostgreSQL internals from its **first increment**, not from later extensions. Collection stays private and unprivileged, uses the existing managed store and Git reconciliation, and never controls PostgreSQL.

## Outcome and boundary

Make the canonical PostgreSQL instance observable as its own dashboard group. Operators should distinguish a database outage, connection exhaustion, blocked work, storage pressure, and ordinary workload growth without treating missing telemetry as a database failure. Email and Telegram should carry only sustained, actionable database degradation. No database query text, customer identity, object key or credential may leave the VM in metric names, labels, annotations or logs.

This design covers the single PostgreSQL 16/pgvector container on the canonical VM and the `app` database. It does not move or resize PostgreSQL, change application query plans, introduce a second monitoring database, enable replication, implement backups, or create automatic remediation. The current root-filesystem capacity rule remains the sole paging disk alert; the PostgreSQL group links that capacity to database growth without duplicating the notification.

## Verified current inventory

Read-only evidence on 2026-10-04: `origin/main` at `1801c097` and canonical VM `epdr5g3p24tdns9890nr` in folder `b1g2qttgfhb4gdunvlge`. The live `db` container was running and Docker-healthy. PostgreSQL reported version 16.15, database `app`, 100 maximum connections (3 reserved for superusers), approximately 4.92 GB database size, and zero replication clients. A point-in-time `pg_stat_activity` read found five sessions, no blocked sessions, and no active or idle-in-transaction sessions older than five minutes. Those values are a snapshot, not a representative baseline.

The Monium read was against workspace `mon0c97qv2s5uju1ark8`. The exact canonical root-free-space, disk-read and available-memory samples were fresh at 2026-10-04 16:52 UTC; the PostgreSQL-name matcher returned no active series at that time. This proves a current ingestion gap, not that PostgreSQL lacks the underlying statistics.

| Layer | Already available | Missing or insufficient |
| --- | --- | --- |
| PostgreSQL itself | `pg_stat_activity`, `pg_stat_database`, `pg_stat_bgwriter`, `pg_stat_wal`, table vacuum statistics, relation/XID age and database size are readable by SQL. | They are not continuously exported. `track_io_timing` is off; `pg_stat_statements` is neither preloaded nor installed. There is no active replica or WAL archive. |
| Container/application | Compose `pg_isready` healthcheck tests server readiness. Django has request count and duration histograms. | `/health/` only returns a JSON response and does not execute SQL; it cannot confirm that Django can use PostgreSQL. There is no database error, pool-wait, query latency or transaction-duration application metric. |
| VM/Monium | Fresh `sys_filesystem_FreeB`, `sys_filesystem_SizeB`, `sys_io_Disks_*`, memory and CPU series for `dev-photo-prjct`; root-disk, CPU, memory and customer HTTP alerts already exist. | These are shared VM signals; they cannot attribute disk, I/O or latency to PostgreSQL. No PostgreSQL charts or alerts exist in the Git-owned dashboard/rules. A live workspace query for names matching `pg_*`, `postgres_*`, `postmaster_*`, `db_*`, `django_db_*` and `findme_db_*` returned no series. |
| Recovery | A manual deployed-database clone/dump path exists. | Scheduled, retained, restore-tested production backups and an agreed RPO/RTO are not established; no truthful backup-freshness metric or alert can be added yet. |

The SQL counters for transactions, temporary bytes, cache hits and WAL bytes are cumulative. Their absolute values cannot establish current rates or degradation; graphs use `rate`/`increase` and handle counter resets. The current `temp_bytes` total is particularly large, but without a rate history it does not establish present pressure.

## Selected observation design

Use the maintained `prometheus-community/postgres_exporter` as a private, fixed-version collector connected over the existing private Compose network. Give it a dedicated PostgreSQL monitoring identity with only the read permissions needed for selected built-in statistics; do not reuse the application superuser or expose the exporter port publicly. The canonical Unified Agent scrapes its loopback-only metrics route every minute and sends bounded series to the existing Managed Prometheus workspace. If the chosen exporter cannot satisfy the required private/unprivileged boundary, revisit the collector choice before activation; do not install a privileged Docker-socket collector.

Limit collection to instance readiness, connection and activity state, database transactions and blocks, locks/waiters, table vacuum/freeze state, database size, and WAL/checkpoint counters. Database and state labels are bounded. Exclude query text, user/application names, per-query IDs and per-table labels from the first dashboard and alert package. A later query-level investigation can explicitly consider `pg_stat_statements` after reviewing its memory/restart cost and privacy exposure. `track_io_timing` also remains off until its overhead and need are measured. The exporter must report connection failure as an observed failure (`pg_up = 0`); absence of the series is an unknown telemetry state.

Separately, a private Django-side functional observation should run a bounded `SELECT 1` with the application database identity and export only success/failure and observation freshness. It must not make public `/health/` or customer requests depend on monitoring. This confirms whether the application can use its database and distinguishes a failed monitoring role/collector from a real service impact. A failed check yields explicit zero; inability to execute or publish the check yields missing data, never a cached healthy value. Until this observation exists, `pg_up = 0` is diagnostic rather than a paging database-outage signal.

Every new metric must be verified at source, agent output and fresh Monium sample before a dashboard panel or rule is considered live. Metric names below are **source contracts**, not an assertion that the exporter currently publishes a specific spelling. Implementation records the exact exporter names, types and labels after choosing its pinned version. Keep the estimated new series count and monthly ingestion cost visible before cloud activation.

## Dashboard: one PostgreSQL group

Keep PostgreSQL separate from Canonical VM, Django/API and worker groups. Arrange panels in diagnosis order; use common time ranges and clear units rather than another row of visually identical generic graphs.

| Panel | Source and presentation | Question answered |
| --- | --- | --- |
| Database usable | Application SQL check and exporter `pg_up`, two state series with distinct labels; show missing observations separately. | Can Django use its database, and can the collector observe it? |
| Connections | Active, idle, idle-in-transaction and waiting sessions, with usable connection ceiling (`max_connections` minus reserved slots). | Is capacity running out, or are sessions accumulating? |
| Long transactions and blocked work | Oldest active/idle-in-transaction age, blocked-session count and lock-wait age; seconds and sessions, separate axes/panels if necessary. | Is a stalled transaction holding up useful work? |
| Transaction outcome | Commit and rollback rates, plus rollback share only above a minimum transaction volume; deadlock increase as a separate marker. | Are operations failing more often than the normal workload explains? |
| Database I/O and cache | Block reads and hits per second, cache-hit share with traffic denominator, temporary bytes per second, and VM disk read/write rates as a separately identified shared-host context. | Is workload becoming more I/O or temporary-file heavy? |
| WAL and checkpoints | WAL bytes per second; timed versus requested checkpoint increases, and checkpoint write/sync time where the PostgreSQL 16 collector provides it. | Is write pressure forcing checkpoints or generating unusual WAL? |
| Storage | `pg_database_size(app)` and growth rate beside root filesystem free bytes and percent; label the filesystem series as shared VM capacity. | How fast is the database growing, and how much host space remains? |
| Vacuum/freeze | Dead/live tuple estimate, last vacuum/autovacuum age, and maximum relation XID age as a share of the configured freeze limit. | Is maintenance falling behind or approaching wraparound risk? |

Query latency percentiles are **not available** from current PostgreSQL settings. Django HTTP latency already shows customer symptoms but does not isolate SQL. Do not draw an empty “PostgreSQL latency” histogram or treat block timing as query latency. If HTTP/worker symptoms and the above DB signals cannot locate a recurring bottleneck, review `pg_stat_statements` or bounded application SQL timing as a separate design extension.

## Alert policy

Only `notification=actionable` rules route to email and Telegram under ADR 0052. All rules require fresh constituent observations, evaluate continuously for the stated period, and recover only after the measured condition clears. Missing exporter/SQL-check data and stale counters appear as an `unknown` diagnostic rule or panel state, not as zero, health, or a database outage. Correlate related signals under one `incident=database` notification group so one failure does not generate separate routine pages.

| Rule | Initial condition | Delivery |
| --- | --- | --- |
| Database unavailable to Django | Fresh application SQL checks report failure for at least 3 minutes; an exporter failure strengthens diagnosis but is not required. Guard against a single scrape gap. | Actionable; email + Telegram. A real inability to use the system of record needs intervention even at low request volume. |
| Connection exhaustion | At least 90% of usable connections occupied for 10 minutes **and** fresh application SQL failures, customer 5xx, or sustained blocked application work. | Actionable only with corroborated service impact; otherwise dashboard/diagnostic. Avoid a second page when `Database unavailable to Django` already groups the incident. |
| Lock-induced degradation | Blocked application sessions or an idle transaction persist for 5 minutes **and** customer 5xx/latency degradation or overdue worker/Commerce work is measured. | Actionable only with corroborated impact; otherwise diagnostic. Do not page for short normal locks. |
| Transaction failures | Sustained rollback/deadlock rise with meaningful transaction volume and measured customer or worker errors. | Initially diagnostic while a normal-rate baseline is gathered; promote to actionable only after a reviewed threshold and replay against history. |
| Vacuum/freeze danger | Oldest relation XID age approaches 80% of the configured `autovacuum_freeze_max_age` for 30 minutes, with valid collection. | Actionable capacity/safety alarm after verifying the chosen threshold against the live cluster. No table names in notifications. |
| Telemetry missing | Exporter or application SQL-check samples absent beyond two expected scrape intervals plus ingestion delay. | Diagnostic in Monium/dashboard only; not a service-failure notification. |
| Database storage | Reuse existing `DiskSpaceCritical` on the shared root filesystem. Database growth and free space remain visible together. | No duplicate PostgreSQL disk page. |
| Backup/replication | No scheduled backup or replica currently exists. | No synthetic “healthy” panel or alert. Add only when those capabilities have verifiable sources and an accepted recovery contract. |

The numeric windows are initial policy values, not claims that they fit observed production variance. Before routing new alerts, replay each candidate on retained history after metric collection has produced a representative baseline. Keep alerts diagnostic if the evidence cannot separate ordinary peaks from intervention-worthy degradation. Existing service alerts still cover customer impact while DB collection is introduced.

## Acceptance and failure semantics

- The private exporter and SQL check produce fresh, bounded samples in the VM, Unified Agent and Monium without public endpoints or secret/query-bearing labels. A collector failure does not affect DB startup, Django serving or deployment.
- The PostgreSQL dashboard group answers the eight questions above with correct units, no duplicate VM alert, and explicit unknown state. Panels using counters remain meaningful across restarts/resets and no-traffic periods.
- A controlled app-credential SQL failure distinguishes real app-to-DB failure from exporter-credential failure. A lost exporter sample produces only diagnostic unknown; neither produces a false healthy state.
- Candidate rules are replayed against an observed baseline, then one controlled firing and recovery reaches both operator channels before any obsolete UI-created database alert is retired. Git-owned dashboard, rules and host collector reconcile from `main` under ADR 0053; no ordinary manual activation step remains.
- Backup freshness, replication lag and PostgreSQL query-latency claims remain explicitly absent until those producers/capabilities exist.

## Sources

- [PostgreSQL 16 cumulative statistics](https://www.postgresql.org/docs/16/monitoring-stats.html)
- [PostgreSQL 16 statistics configuration](https://www.postgresql.org/docs/16/runtime-config-statistics.html)
- [PostgreSQL 16 `pg_stat_statements`](https://www.postgresql.org/docs/16/pgstatstatements.html)
- [Prometheus community PostgreSQL exporter](https://github.com/prometheus-community/postgres_exporter)
