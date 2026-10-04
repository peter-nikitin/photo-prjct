# FindMe monitoring as code

The operator-approved host installation and initial cloud activation ran on 2026-09-28. Fresh
public/Linux/HTTP/Commerce samples, eleven successful rule evaluations and all fourteen desired
dashboard widgets were verified through the cloud API. Email acceptance and retirement of native
duplicates remain separate gates. Existing native alerts, routes and timers remain active. See
[activation evidence](../../../docs/operations/2026-09-28-monitoring-activation.md).

## Owned objects and inputs

- `environment.json`: reviewed folder, dashboard, workspace, channel and metric contracts.
- `rules.yml`: one owned `findme-photo.yml` file; alert thresholds/windows are editable here.
- `alertmanager.yml`: full routing configuration for a **dedicated FindMe workspace**. The CLI
  rejects any foreign rule file before replacing routing. It never deletes foreign rules.
- `dashboard.json`: one dashboard with eight collapsible system groups and 48 charts. Customer
  HTTP errors are separate from internal worker-control errors. Existing worker and imgproxy
  duration histograms remain; freshness diagnostics do not synthesize a healthy zero. Other
  dashboard fields come from a fresh Get and are preserved.
- `oidc.json`: protected `monitoring` environment identity; service account
  `aje3t70qka1dtc09k5ic` (`findme-monitoring-ci`).

The image-origin rules are enabled in the reviewed package. They require additional Prometheus
series from the existing image-origin VM before `check` or `apply` can succeed. The native chart
queries were checked against actual Monitoring data on 2026-09-30; a dashboard render does not
prove their later freshness. See the [image alert contract](../../image-origin/monitoring/alerts.md).

## Automatic reconciliation and acceptance

Pull requests run offline validation. A `main` push with changed cloud inputs automatically runs
`monitoring.yml` `apply` at `github.sha` under the existing OIDC identity. Canonical host, public
exporter/probe and image-origin monitoring packages have separate selected paths; image-origin
monitoring uses `monitoring_only=true` and does not restart application containers. A failed
preflight or apply fails its observability job and retains backup evidence. Documentation-only
changes do not contact the cloud or VMs. The [host foundation](../../observability/README.md) must
be installed once, and the existing GitHub `monitoring` reviewer requirement must be removed while
retaining main-only branch and exact-workflow OIDC restrictions. Until then, automatic jobs fail
or wait; a merged Git revision alone is not live acceptance.

When one merge changes host collection and cloud rules/dashboard together, the cloud job waits for
the selected host jobs at the same push SHA and then for fresh samples from their new routes. A
missing, skipped or failed required host job, or a source that remains stale, fails the cloud job
within a bounded interval; it does not apply against the preceding host configuration.
Host-only observability merges also rerun cloud reconciliation after the selected host jobs. This
recovers a previously blocked cloud apply automatically once its source is repaired.

After activation, read back fresh Prometheus samples for `sys_memory_MemAvailable{job="findme-image-linux"}`,
`origin_image_origin_responses_total{job="findme-image-origin"}`, and
`imgproxy_requests_total{job="findme-imgproxy"}`; check rule snapshots and all eight dashboard
groups. A controlled firing and recovery must reach both email and Telegram before retiring any
duplicate native UI alert. Existing manual workflow actions are recovery tools only.

The additional Remote Write volume is a potential charge. At the 2026-09-30 native inventory,
the host exposed about 340 series including agent-health and interface series; the three new
Prometheus routes may write roughly 330 series every minute, about 14 million values per 30 days.
This is an upper-order estimate, not a price quote: actual Remote Write cardinality and pricing
must be checked before the approved VM step. [Monium pricing](https://yandex.cloud/en/docs/monium/pricing)
lists Remote Write and alert calculation as billable. The existing 5-minute public probe route
remains in place.

The operator supplied channel names are `findme-photo-operator-email` (ID
`cloud__b1gmcsmr51o5kvp86l55_findme-photo-operator-email`) and
`findme-photo-operator-telegram` (ID
`folder__b1g2qttgfhb4gdunvlge_findme-photo-operator-telegram`). Workspace ID is
`mon0c97qv2s5uju1ark8`, supplied by the operator.
The workflow never retargets configuration from CI variables. The approved foundation was created
on 2026-09-28: GitHub environment `monitoring`, reviewer `peter-nikitin`, main-only deployment policy,
and credential `ajeprb31m2nhu6pbj2gn` in existing federation `ajeula3gd46omgf9jiko`, bound to subject
`repo:peter-nikitin/photo-prjct:environment:monitoring`. Its only folder role is `monitoring.editor`.
Ingestion and initial rule/dashboard activation have live evidence. The first GitHub OIDC check
reached cloud query preflight; final exact-main CI reconciliation and email acceptance are recorded
separately in the activation evidence.
The tools reuse validated short-lived OIDC exchange from `run-with-environment-secrets.py` without
loading any Lockbox consumer or adding a long-lived key.

## Offline commands

Keep the SDK out of Django/worker dependencies:

```sh
.venv/bin/python -m venv /tmp/findme-monitoring-tools
/tmp/findme-monitoring-tools/bin/pip install -r deploy/monitoring/prometheus/requirements.txt
/tmp/findme-monitoring-tools/bin/python deploy/monitoring/prometheus/control.py render \
  --output /tmp/findme-monitoring-render
```

Use Prometheus **3.5.0**. For Docker validation, mount the repository at its original absolute path
so promtool can read the generated rules and test paths:

```sh
cat > /tmp/findme-promtool <<'SH'
#!/bin/sh
exec docker run --rm --entrypoint promtool -v "$PWD:$PWD" -w "$PWD" prom/prometheus:v3.5.0 "$@"
SH
chmod +x /tmp/findme-promtool
/tmp/findme-monitoring-tools/bin/python deploy/monitoring/prometheus/control.py validate \
  --output "$PWD/.monitoring-render" --promtool /tmp/findme-promtool
rm -r "$PWD/.monitoring-render"
make test TESTS='tests/monitoring/test_prometheus_control.py tests/monitoring/test_prometheus_host.py -m operational'
```

Validation uses the official generated SDK schema for Prometheus and native Monitoring sources,
positive Prometheus grid steps, matching source/target kinds and references, resolved queries,
promtool syntax and behavior scenarios. It wraps the actual rendered Prometheus dashboard queries
as temporary recording rules, then runs five dashboard fixtures for independent queue/cloud
freshness, idle zero versus no data, combined runtime source age, disjoint histogram intervals and
accepted-preview zero versus a missing source. It validates both the committed enabled worker profile
and an explicitly disabled profile against the same base rules plus worker saturation, reset, missing-source,
sender-outage and idle-zero fixtures. No credential or network call to Yandex is
needed; Docker may pull the pinned tool image. Yandex's receiver extension is checked structurally
by the renderer and accepted by the service on explicit PUT; upstream Alertmanager does not
understand `yandex_monitoring_configs`. Email delivery needs a separate live drill.
Only rules labelled `notification=actionable` use the project receiver, which sends to both
operator channels with `send_resolved: true`. Diagnostic rules have no notification route.
The operator confirmed receipt of firing and recovery email. Telegram delivery needs a separate
live drill after routing is applied. No receiver default is assumed.

If public probe samples are missing, the normal `apply` preflight blocks all owned objects.
After reviewing the exact Git revision, change only the Alertmanager route without rewriting rules
or the dashboard:

```sh
/tmp/findme-monitoring-tools/bin/python deploy/monitoring/prometheus/control.py apply-routing --identity yc
```

The command requires a dedicated FindMe workspace and both named Monium channels. The provider
does not expose a supported routing GET/read-back contract; keep the previous Git revision's
rendered `alertmanager.yml` for rollback. A successful PUT does not prove Telegram delivery.

## Observed metric contract

The [preparation evidence](../../../docs/research/2026-09-28-monitoring-agent-contract.md) records
actual Unified Agent `26.09.10/21106904` loopback output. CPU useful/idle are cumulative COUNTER
series with `instance=dev-photo-prjct` and **no** `cpu` label: `rate(...[5m])` is required. Resource
values are gauges in bytes; `sys_system_UpTimeRaw` is milliseconds; load is
`sys_proc_LoadAverage1min`. Private HTTP names/buckets survive ingestion unchanged. HTTP totals
are semantically cumulative application counters even though the observed SPACK serialization
uses GAUGE; do not infer semantics from the wire type. This proof describes the producer, not
successful workspace ingestion. `type_contract_evidence` binds this reviewed contract in config.

Dashboard diagnostics use the existing observations: swap free/total bytes are shown separately
in GiB (both zero means swap is disabled), and root filesystem inode capacity is free/total in
percent. Disk read/write and network Rx/Tx are cumulative byte counters, so charts use
`rate(...[5m])` in bytes/second. Selectors restrict disk I/O to `disk=vda` and network I/O to
`intf=eth0`, both on `instance=dev-photo-prjct`; Docker/veth traffic is not summed.
Raw diagnostic observations retain 120s freshness, and their derived expressions must produce
finite nonempty vectors before check/apply.

The Canonical VM group queries native Monitoring `ua.backlog` for `host=dev-photo-prjct`,
`service=custom`, `scope=health` in the explicit configured folder. The dashboard query puts
`folderId` in its selector; the native data-read API takes that folder in the request URI instead.
It is managed by the same
Git dashboard package, but does not use the Prometheus workspace. Preserve the native Unified
Agent health observation route even if duplicate native alerts are retired later; removing or
migrating that route needs separate approval and proof. Its fresh native query is verified
separately from the CLI's Prometheus preflight. Missing native points do not mean zero backlog.

Check/apply query each raw selector as a range vector over its configured `max_age` seconds.
Selectors without labels omit `{}` because the backend returns vectors for empty-brace range
selectors; nonempty labels and the HTTP 5xx filter remain explicit.
They require a matrix with actual points, and check the latest point timestamp and finite value
of every observed series. The backend's `timestamp(selector)` reports query evaluation time and
cannot prove observation age. Public probe/TLS freshness is 600s, matching the accepted 10-minute
no-data window and two 300s probe intervals; all other raw metrics retain 120s freshness.
An empty instant vector between public scrapes does not replace this actual-point check. Counter
range calculations must have sufficient real points. An absent 5xx subset becomes zero only alongside
observed HTTP totals; zero traffic passes the preflight and fails the >=5-request alert gate.
Missing totals never become zero. Raw Linux names/labels and histogram `_count` must appear in
this workspace. A missing mapping, NaN, stale sample, query error or unsupported CPU contract
blocks apply; `/metadata` is not supported and is never called.

`findme_accepted_previews_total` is a label-free application counter initialized at zero in every
deployment and aggregated through the existing Gunicorn multiprocess `/metrics/` route. It advances
after commit only when a unique accepted `preview-small-v1` derivative is published. It does not
reconstruct history and does not count callback delivery, watermarked previews, all assigned stages
or unconditional public availability. The dashboard uses the fresh counter's five-minute rate;
counter reset handling is Prometheus-native, observed idle is zero, and a stale or missing source is
no data.

Public/Commerce availability preserve maximum-over-window semantics (10m/5m), with separate
`absent_over_time` rules. Disk and memory use maximum free capacity over 10m/15m; CPU uses minimum
utilization over 15m. TLS uses minimum remaining days over 10m, preserving observations between 300s polls plus
delivery lag; the preflight uses the same TLS window. Ready work uses maximum age over 5m.
Missing resource series do not fire pressure rules. Native worker-pool control observations are
outside this migration and stay untouched.

## Additive host preparation and installation

The canonical `deploy/configure-monitoring-agent.sh` refreshes only native-owned `status`,
`metrics_buffer`, `cloud_monitoring` and routes referencing `cloud_monitoring`. It parses the current
configuration with PyYAML, preserves unrelated storages, channels and routes (including the optional
worker Prometheus scrape), validates the merged candidate with `check-config`, and only then promotes
it. Missing Python/PyYAML or an invalid candidate fails before replacing the working configuration;
existing service-state rollback remains in force. The canonical host prerequisite was checked
read-only (`python3` 3.12.3, PyYAML 6.0.1); this task installs no host package.

After separate approval of cloud cost, IAM and service restarts, snapshot the **actual** agent
config from each host. Do not substitute the repository's old native template for the live file.
Render from the snapshot using the role `canonical` on the main VM or `public` on image origin:

```sh
/tmp/findme-monitoring-tools/bin/python deploy/monitoring/prometheus/render_agent.py \
  --current /tmp/current-agent.yml --role canonical --workspace-id mon0c97qv2s5uju1ark8 \
  --output /tmp/reviewed-agent.yml
.venv/bin/python deploy/monitoring/prometheus/install.py source-hash --source-root "$PWD"
```

Review the output diff. The renderer owns only `findme_prometheus_buffer`,
`findme_prometheus_remote_write` and routes referencing that channel. Re-rendering replaces only
those objects and preserves every native/unrelated route, including autoscaling control metrics.
A separate disk buffer uses the documented `metrics` output and VM metadata IAM. The public scrape
is 300s with 30s timeout; Commerce is 60s with 40s timeout; private HTTP/Linux are 60s. The exporter
uses only Python standard library plus copies of the existing collectors, always binds
`127.0.0.1:19091`, and serves only `/metrics`. HTTPS attempts produce observed zero on failure;
Commerce collection failure serves 503 without Commerce samples. Each scrape collects anew.

Copy the exact reviewed source revision and rendered YAML to the chosen host. Verify source,
current config and rendered hashes against the values recorded **before** transfer. Install with
explicit values (do not derive expected hashes from a potentially changed destination artifact):

```sh
sudo /usr/bin/python3 REVIEWED_SOURCE/deploy/monitoring/prometheus/install.py install \
  --source-root REVIEWED_SOURCE --revision EXACT_40_CHARACTER_SHA \
  --role canonical --instance-id EXPECTED_VM_INSTANCE_ID \
  --rendered-agent /tmp/reviewed-agent.yml \
  --current-sha256 REVIEWED_CURRENT_CONFIG_SHA256 \
  --rendered-sha256 REVIEWED_RENDERED_CONFIG_SHA256 --source-sha256 REVIEWED_SOURCE_SHA256
```

The installer checks VM metadata identity, all three hashes, minimum supported agent version and
`unified_agent --config FILE check-config` before changes. It locks installation, backs up files and
unit state, atomically installs the library/unit/config, then starts the new exporter and restarts
the agent unit for the selected role. Canonical uses `/etc/yc/unified_agent/config.yml` and
`unified_agent.service`; public uses `/etc/yandex/unified_agent/config.yml` and
`unified-agent.service`. Rollback validates the backup's role and exact managed paths/units,
then restores the recorded files and unit states. It never restarts application containers or
native timers. Public uses DynamicUser with an empty capability bounding set. Canonical retains
the existing root host-Docker observation boundary with only `CAP_DAC_READ_SEARCH`, allowing
Docker Compose to read the existing deploy-owned `0600` environment file without changing its
permissions. Output
is the retained backup directory. Any activation failure restores files and prior enabled/active
unit state; a restoration failure retains the backup and reports it for operator action.

Validate fresh workspace names, values, timestamps, counter semantics and measured sample volume
before alert apply. Cardinality and pricing approval require actual current measurements; old
sample-volume estimates are insufficient.

## Optional isolated worker diagnostics

The canonical renderer accepts `--worker-telemetry` only with `--role canonical`. It adds
`http://127.0.0.1:8080/worker-diagnostics/metrics/` with a 30-second poll and 10-second timeout
to the existing `findme_prometheus_remote_write` channel and its bounded 100 MB buffer.
Both roles retain their existing rendering when the flag is absent. Re-render with the flag
to retain diagnostics; omitting it removes that owned route. Review the complete diff against
the actual current agent snapshot before any separately approved installation.

Use the existing reviewed `environment.json` workspace input as `--workspace-id`; the renderer
accepts a workspace ID, never an arbitrary output URL. Authorization remains the canonical VM's
metadata IAM. Missing/unauthorized IAM cannot be proven offline: an agent buffer or a successful
local scrape is not a successful write. No worker receives Monitoring credentials or a new sender.
The existing installer still requires agent >=25.03.80 and checks the exact rendered config.

Only the diagnostic route uses `channel.pipe.filter`, plugin `transform_metric_labels`, with
`config.labels` equal to `[{job: '-'}, {instance: '-'}, {host: '-'}]`. This is the documented
[filter grammar](https://yandex.cloud/en/docs/monitoring/concepts/data-collection/unified-agent/filters).
The [Prometheus agent contract](https://yandex.cloud/en/docs/monitoring/operations/prometheus/ingestion/prometheus-agent)
describes generated job/instance labels and metadata IAM. The backend already constrains source
labels to pool/instance_id/zone_id and runtime kind/outcome/le. Shared-channel and native/Linux,
HTTP, Commerce and public routes keep their existing labels. The committed Git profile includes
worker alert rules for preparation while remote worker launch remains unaccepted.

`environment.json` sets `worker_alerts_enabled` true for the next launch preparation.
No worker alert profile was applied live during the failed 2026-10-01 attempt, and this Git change
does not apply the profile live. The enabled profile adds the reviewed
`findme-workers` group to the same owned rule file and existing email+Telegram receiver; it does
not add a workspace, channel or route. `check` and `apply` then require fresh 90-second queue,
complete cloud-membership and successful native-publication source clocks for both pools, finite
cap-one running/expected capacity, and current-member node diagnostic samples when an identified
member exists. An idle bulk pool with zero expected members needs no node sample. Source values,
not Prometheus ingestion timestamps, fence retained series; the current cloud-observation clock
also prevents a replaced node's retained samples from satisfying the new member.

The worker [runbook](../../../docs/runbooks/worker-pools.md#optional-worker-diagnostics)
documents status, local TLS rehearsal, reset/freshness guards and cost inputs. Before live
acceptance, query actual ingested point timestamps along with source age/freshness: a retained
`fresh=1` point during a sender outage is stale evidence. Ingestion, rules and notification
firing/no-data/recovery each require their own separately approved evidence.

The same protected, serialized Monitoring workflow has a separately approved
`drill-run` action for a finite synthetic worker-alert evaluator rehearsal. Its
offline-rendered temporary rule file clones the current worker predicates and original
`for` durations, substitutes only reviewed worker inputs with inline synthetic
vectors, and stops matching at an absolute expiry. Pinned `promtool` validation
checks both-pool saturation, missing sources, node diagnostics with fresh membership,
retained-stale sources and recovery. The production-owned rule file is unchanged.
Use the [worker runbook](../../../docs/runbooks/worker-pools.md#bounded-synthetic-worker-alert-evaluator-rehearsal)
for the exact workflow and cleanup sequence. A green synthetic rehearsal is not real
production saturation or notification receipt.

## Read-only check and explicit apply

With reviewed foundation/config in Git and separately approved live operations:

```sh
/tmp/findme-monitoring-tools/bin/python deploy/monitoring/prometheus/control.py check --identity yc
/tmp/findme-monitoring-tools/bin/python deploy/monitoring/prometheus/control.py apply --identity yc \
  --backup /tmp/findme-monitoring-backup-UNIQUE_REVISION
```

`check` compares owned dashboard fields/rules and validates existing calculation snapshots. Routing
drift is always reported **unverified**: there is no confirmed GET Alertmanager API. `apply`
preflights raw and computed samples and every alert expression, saves target-bound dashboard/rules
snapshots, applies routing before the owned rule file, verifies the file, waits up to 120s for fresh
successful evaluation snapshots, updates with the fresh etag and validates the returned synchronous gRPC operation,
then reads back owned fields. Failed or stale snapshots are failures, never health. API/SDK errors
are sanitized; credentials and response bodies are not printed. Apply is not atomic across services;
a failure after rules PUT leaves the recorded backup for explicit restore.

The GitHub workflow validates PRs. A selected `main` push applies the exact push SHA automatically;
manual `workflow_dispatch` check/apply remains a reviewed recovery entrypoint. Both paths serialize
under the `monitoring` environment and retain backups even on failed apply. The environment must
retain its main-only branch policy and exact-workflow OIDC subject, without a routine reviewer gate.

## Rollback and cutover

Host rollback uses the exact printed backup and VM identity; it does not require cloud data:

```sh
sudo /usr/bin/python3 REVIEWED_SOURCE/deploy/monitoring/prometheus/install.py rollback \
  --backup /var/lib/findme-prometheus/backups/EXACT_BACKUP --instance-id EXPECTED_VM_INSTANCE_ID
```

Control rollback verifies the saved folder/workspace/dashboard IDs, uses a fresh dashboard etag,
and restores only owned fields/rules. A first-activation absence removes only `findme-photo.yml`:

```sh
/tmp/findme-monitoring-tools/bin/python deploy/monitoring/prometheus/control.py restore \
  --identity yc --backup /tmp/findme-monitoring-backup-UNIQUE_REVISION
```

Restore does not require healthy metric queries. Routing has no server-side backup: render a known
previous Git revision's routing and add `--routing-file /tmp/previous-alertmanager.yml` to restore it.
Never invent GET/DELETE Alertmanager or delete a workspace. Full routing replacement still requires
a dedicated workspace. Retire duplicate native UI alerts only after controlled actionable firing
and recovery reach both email and Telegram, rule/dashboard parity is read back, and measured costs
are acceptable. Diagnostic missing-observation rules remain visible without operator paging. The
approved migration excludes native worker-pool control metrics.
