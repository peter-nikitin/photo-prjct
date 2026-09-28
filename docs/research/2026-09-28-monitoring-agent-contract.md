# Monitoring migration: observed agent contract

Read-only preparation on 2026-09-28 for the monitoring-as-code package.
No cloud metric writes, service restarts, IAM changes or alert changes were performed.

## Dashboard and agent

- Official SDK `yandexcloud==0.408.0` DashboardService.Get over gRPC returned dashboard
  `fbeketud0mdaupj43of6`, title `FindMe Photo — мониторинг`, 14 widgets, folder
  `b1g2qttgfhb4gdunvlge`. The JSON snapshot remains outside Git for activation backup.
- The HTTP `/monitoring/v3/dashboards/<id>` path returned 404; use the documented gRPC API.
- Canonical `/usr/bin/unified_agent --svnrevision` reports `26.09.10/21106904`.
- `unified_agent --config FILE check-config` is supported.

## Isolated output inspection

An additional temporary unprivileged agent process collected Linux/private HTTP observations
and sent them only to a temporary loopback HTTP receiver. It used no cloud IAM or cloud URL,
no disk buffer, a 64 MiB memory bound and a bounded capture followed by process termination.
The existing agent, its configuration and timers were untouched; temporary files were removed.

The documented `metrics` output emits Yandex SPACK in this agent build. Inspection used the
primary open-source [SPACK decoder](https://github.com/ydb-platform/ydb/blob/main/library/cpp/monlib/encode/spack/spack_v1_decoder.cpp)
and [type enum](https://github.com/ydb-platform/ydb/blob/main/library/cpp/monlib/metrics/metric_type.h).
The research decoder is not shipped as a production transport.

| Metric | Observed type and labels | Migration consequence |
| --- | --- | --- |
| `sys_system_UsefulTime`, `sys_system_IdleTime` | COUNTER, `instance=dev-photo-prjct`, job; no `cpu` label | Use `rate()` and no `cpu="-"` selector |
| `sys_memory_MemAvailable`, `sys_memory_MemTotal` | GAUGE in bytes | Preserve ratio |
| `sys_filesystem_FreeB`, `sys_filesystem_SizeB` | GAUGE in bytes, `mountpoint=/` | Preserve percent and 5 GiB checks |
| `sys_system_UpTimeRaw` | GAUGE in milliseconds | Divide by 1000 for the seconds graph; checked against `/proc/uptime` |
| `sys_proc_LoadAverage1min` | GAUGE | Preserve load graph |
| `findme_http_requests_total` | Serialized as GAUGE, cumulative application counter semantics | `rate`/`increase` rely on the application contract, not a SPACK type assertion |
| `findme_http_request_duration_seconds_bucket`, `_count`, `_sum` | Names and `le` buckets preserved | Use standard histogram PromQL |

The private HTTP capture contains an automatic `up` gauge and `instance=dev-photo-prjct`.
It contained 397 HTTP metric samples at this observation; earlier 180 and 774 snapshots are
not current volume evidence. Linux capture contained 213 samples. Cardinality and billing
must be measured again at activation, including generated alert series and other workspaces.

## Limits

These checks prove agent-side serialization, not ingestion or rule calculation in a workspace.
Workspace queries, freshness and notification delivery remain mandatory activation checks.
Existing native worker-pool publishing must survive this migration because autoscaling depends
on its successful write/observation contract.

The exact channel name supplied by the maintainer is `findme-photo-operator-email`, ID
`fbefs2ubu6sq0k0jvlch`. Workspace ID and protected CI identity foundation remain pending.

Sources: [agent configuration](https://yandex.cloud/en/docs/monitoring/operations/prometheus/ingestion/prometheus-agent),
[dashboard gRPC](https://yandex.cloud/en/docs/monitoring/operations/dashboard/api-examples),
[supported query endpoints](https://yandex.cloud/en/docs/monitoring/operations/prometheus/querying/grafana).
