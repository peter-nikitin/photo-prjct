# FindMe monitoring as code

Repository preparation is complete only after the recorded local checks. **Live activation is
pending.** Existing native alerts, routes and timers remain active. No command below has been
run against production as part of implementation.

## Owned objects and inputs

- `environment.json`: reviewed folder, dashboard, workspace, channel and metric contracts.
- `rules.yml`: one owned `findme-photo.yml` file; alert thresholds/windows are editable here.
- `alertmanager.yml`: full routing configuration for a **dedicated FindMe workspace**. The CLI
  rejects any foreign rule file before replacing routing. It never deletes foreign rules.
- `dashboard.json`: title and all 14 graph widgets, including duration, TLS, uptime, load, HTTP
  rates/latency and Commerce. Other dashboard fields come from a fresh Get and are preserved.
- `oidc.json`: protected `monitoring` environment identity; service account
  `aje3t70qka1dtc09k5ic` (`findme-monitoring-ci`).

The operator supplied channel name is `findme-photo-operator-email` (ID
`fbefs2ubu6sq0k0jvlch`). Workspace ID is `mon0c97qv2s5uju1ark8`, supplied by the operator.
The workflow never retargets configuration from CI variables. The approved foundation was created
on 2026-09-28: GitHub environment `monitoring`, reviewer `peter-nikitin`, main-only deployment policy,
and credential `ajeprb31m2nhu6pbj2gn` in existing federation `ajeula3gd46omgf9jiko`, bound to subject
`repo:peter-nikitin/photo-prjct:environment:monitoring`. Its only folder role is `monitoring.editor`.
Ingestion, rule activation and OIDC runtime acceptance remain separate live checks.
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

Validation uses the official generated SDK schema, positive Prometheus grid steps and target
references, promtool syntax and behavior scenarios. No credential or network call to Yandex is
needed; Docker may pull the pinned tool image. Yandex's receiver extension is checked structurally
by the renderer and accepted by the service on explicit PUT; upstream Alertmanager does not
understand `yandex_monitoring_configs`. Firing, missing-data and recovery email delivery still
require a separate live drill; no undocumented receiver options are assumed.

## Observed metric contract

The [preparation evidence](../../../docs/research/2026-09-28-monitoring-agent-contract.md) records
actual Unified Agent `26.09.10/21106904` loopback output. CPU useful/idle are cumulative COUNTER
series with `instance=dev-photo-prjct` and **no** `cpu` label: `rate(...[5m])` is required. Resource
values are gauges in bytes; `sys_system_UpTimeRaw` is milliseconds; load is
`sys_proc_LoadAverage1min`. Private HTTP names/buckets survive ingestion unchanged. HTTP totals
are semantically cumulative application counters even though the observed SPACK serialization
uses GAUGE; do not infer semantics from the wire type. This proof describes the producer, not
successful workspace ingestion. `type_contract_evidence` binds this reviewed contract in config.

Check/apply require actual selectors to return finite, fresh values through the supported query
API. Timestamp values prove observation age rather than query execution time. Counter range
calculations must have sufficient real points. An absent 5xx subset becomes zero only alongside
observed HTTP totals; zero traffic passes the preflight and fails the >=5-request alert gate.
Missing totals never become zero. Raw Linux names/labels and histogram `_count` must appear in
this workspace. A missing mapping, NaN, stale sample, query error or unsupported CPU contract
blocks apply; `/metadata` is not supported and is never called.

Public/Commerce availability preserve maximum-over-window semantics (10m/5m), with separate
`absent_over_time` rules. Disk and memory use maximum free capacity over 10m/15m; CPU uses minimum
utilization over 15m. TLS uses minimum remaining days over 5m; ready work uses maximum age over 5m.
Missing resource series do not fire pressure rules. Native worker-pool control observations are
outside this migration and stay untouched.

## Additive host preparation and installation

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
HTTP, Commerce and public routes keep their existing labels. No worker alert or routing is applied.

The worker [runbook](../../../docs/runbooks/worker-pools.md#optional-worker-diagnostics)
documents status, local TLS rehearsal, reset/freshness guards and cost inputs. Before live
acceptance, query actual ingested point timestamps along with source age/freshness: a retained
`fresh=1` point during a sender outage is stale evidence. Ingestion, rules and notification
firing/no-data/recovery each require their own separately approved evidence.

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
successful evaluation snapshots, updates with the fresh etag and waits for the gRPC operation,
then reads back owned fields. Failed or stale snapshots are failures, never health. API/SDK errors
are sanitized; credentials and response bodies are not printed. Apply is not atomic across services;
a failure after rules PUT leaves the recorded backup for explicit restore.

The GitHub workflow validates PRs. Live check/apply is `workflow_dispatch` only, main-only,
serialized under a protected environment and bound to the exact dispatch SHA. No push activates
monitoring. Retained backups are uploaded even on failed apply.

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
a dedicated workspace. Retire old native observation alerts/routes/timers only after a separate
validation alert has proved firing, missing observations and recovery email, plus dashboard parity
and acceptable measured costs. The approved migration excludes native worker-pool control metrics.
