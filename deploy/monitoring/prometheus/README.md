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

Public/Commerce availability preserve maximum-over-window semantics (10m/5m), with separate
`absent_over_time` rules. Disk and memory use maximum free capacity over 10m/15m; CPU uses minimum
utilization over 15m. TLS uses minimum remaining days over 10m, preserving observations between 300s polls plus
delivery lag; the preflight uses the same TLS window. Ready work uses maximum age over 5m.
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
