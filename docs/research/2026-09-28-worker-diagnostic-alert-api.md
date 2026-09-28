# Worker diagnostic alert API feasibility

- Researched: 2026-09-28.
- Scope: ADR 0043 and the approved worker telemetry specification; public official documentation
  and API definitions only. No cloud requests, credentials, provisioning or configuration changes.
- Result: **No fully compliant automated lifecycle is established by the published contract.**
  Rule application/read-back and ingestion are documented. Workspace/channel lifecycle and
  Alertmanager configuration read-back remain hard prerequisites, not implementation details.
- This finding does not change native queue autoscaling or existing alert controls.

## Support classification

“Supported” below means explicitly documented, not live-validated. “Unconfirmed” means no
published contract was found; it does not prove that an internal API or forthcoming feature
cannot exist. “Unsupported” is reserved for an explicit vendor limitation.

| Required capability | Classification | Published evidence and consequence |
| --- | --- | --- |
| Workspace create/list/get/update/delete | Unconfirmed through public API | The getting-started procedure provides only UI creation. No workspace service appears in the public Monitoring definitions. Do not invent lifecycle URLs. [Workspace guide](https://yandex.cloud/en/docs/monium/operations/prometheus/#access), [API source tree](https://github.com/yandex-cloud/cloudapi/tree/1c473fc6d1b9ee78e147e1fa9e735e8f6692152e/yandex/cloud/monitoring) |
| Notification channel create/list/get/update/delete | Unconfirmed through public API | Creation instructions provide only UI steps. IAM channel roles describe permissions, but supply no HTTP/RPC contract. [Channel creation](https://yandex.cloud/en/docs/monium/operations/alert/create-channel), [Channel editor role](https://yandex.cloud/en/docs/monium/security/#monium-channels-editor) |
| Rule file create/replace/list/get/delete and evaluation snapshots | Supported | Explicit operations share the rule resource; the alerting guide refers to recording-rule file management. [Rule operations](https://yandex.cloud/en/docs/monium/operations/prometheus/recording-rules), [Alerting](https://yandex.cloud/en/docs/monium/operations/prometheus/alerting-rules) |
| Alertmanager complete configuration replace | Supported | PUT accepts encoded YAML and returns 204 on success. This is whole-file replacement. [Alertmanager configuration](https://yandex.cloud/en/docs/monium/operations/prometheus/alerting-rules#alert-manager-create) |
| Alertmanager configuration get/download through API, version or conditional update | Unconfirmed | That guide describes API PUT, and UI Download/Replace only. It publishes no API GET, ETag, revision or compare-and-swap contract. Successful PUT cannot establish configuration read-back or drift detection. [Configuration operations](https://yandex.cloud/en/docs/monium/operations/prometheus/alerting-rules#alert-manager-create) |
| Rule drift comparison and reviewed-version rollback | Supported as a client procedure | GET supplies file content; PUT can restore a reviewed file. There is no documented server version history. [File content](https://yandex.cloud/en/docs/monium/operations/prometheus/recording-rules#get-content), [Replace](https://yandex.cloud/en/docs/monium/operations/prometheus/recording-rules#create) |
| Routing drift comparison and verified rollback | Unconfirmed end to end | Restoring known YAML through PUT is documented, but deployed content verification is not. It cannot safely adopt or preserve unknown shared routing. [Alertmanager operations](https://yandex.cloud/en/docs/monium/operations/prometheus/alerting-rules) |
| Canonical-boundary Remote Write and metric queries | Supported | The workspace supports Remote Write, Remote Read and a bounded subset of Prometheus HTTP queries. [Write](https://yandex.cloud/en/docs/monium/operations/prometheus/ingestion/remote-write), [Read](https://yandex.cloud/en/docs/monium/operations/prometheus/querying/) |
| Staleness markers, exemplars, native histograms | Unsupported | Explicit vendor limitations. Use classic histogram series and explicit source freshness. [Limitations](https://yandex.cloud/en/docs/monium/operations/prometheus/#restrictions) |
| Arbitrary upstream Alertmanager receivers | Unsupported as a delivery assumption | The managed guide says other channel types are ignored without errors; upstream webhook/SMTP configuration is not proof of managed delivery. [Managed channels](https://yandex.cloud/en/docs/monium/operations/prometheus/alerting-rules#rule-processing) |

## Exact documented application and verification operations

For the following table, `P` denotes
`https://monitoring.api.cloud.yandex.net/prometheus/workspaces/<workspace_ID>`.
Rule and Alertmanager examples use `Authorization: Bearer <IAM_token>`; writes use
`Content-Type: application/json`. YAML bytes are RFC 4648 Base64, not a JSON object parsed
as the rule definition. [Authentication](https://yandex.cloud/en/docs/monium/api-ref/authentication),
[Rule operations](https://yandex.cloud/en/docs/monium/operations/prometheus/recording-rules),
[Alertmanager operations](https://yandex.cloud/en/docs/monium/operations/prometheus/alerting-rules).

| Operation | Method and path | Body / documented result |
| --- | --- | --- |
| Create or replace one named rule file | `PUT P/extensions/v1/rules` | `{"name":"worker-diagnostics.yml","content":"<base64-YAML>"}`; success 204 |
| List rule files | `GET P/extensions/v1/rules` | `{"files":["..."]}` |
| Read one rule file | `GET P/extensions/v1/rules/<file_name>` | `{"name":"...","content":"<base64-YAML>"}` |
| Read evaluations | `GET P/extensions/v1/rules/<file_name>/snapshots` | `snapshotByGroup`; evaluation state/error/time. The documented example is a recording rule, so alert-specific snapshot fields still need validation. |
| Delete one rule file | `DELETE P/extensions/v1/rules/<file_name>` | Operation documented; do not infer response details beyond the guide. |
| Create or replace all Alertmanager configuration | `PUT P/extensions/v1/alertmanager` | `{"content":"<base64-YAML>"}`; success 204; configuration must match at least one current folder channel |

These are file replacement semantics, not a patch API. Removing groups/rules in a replacement
stops their evaluation. File names allow Latin letters, digits, `.`, `-`, `_`, up to 256
characters; group names also have a 256-character limit. [Rule file requirements and replacement](https://yandex.cloud/en/docs/monium/operations/prometheus/recording-rules).

For owned rule files, the eventual client can compare decoded bytes or a documented local
normalization with reviewed Git YAML, reject unexpected drift before mutation, PUT, GET again
and compare. It can reapply the previous reviewed file and repeat verification. This procedure
is an inference from the documented operations, not a vendor transactional guarantee. Never
delete or overwrite foreign filenames. No published multi-file/routing transaction, immutable
revision, server rollback endpoint or conditional-write guarantee was found in those guides.

Alertmanager uses managed folder channels, selected by
`receivers[].yandex_monitoring_configs[].channel_names`; labels are matched in `route`/`routes`.
Do not put bot tokens, SMTP passwords or cloud keys in routing YAML. The overview restricts
delivery to email/Telegram and says dynamic routing is unavailable, whereas the detailed guide
also lists SMS/push and shows label routing. **These official pages conflict.** Email/Telegram
are the safe documented intersection; exact routing semantics and SMS/push require vendor
clarification and controlled live proof. [Overview](https://yandex.cloud/en/docs/monium/operations/prometheus/#restrictions),
[Detailed guide](https://yandex.cloud/en/docs/monium/operations/prometheus/alerting-rules#rule-processing).

Notification recipients have their own account prerequisites: folder `monium.viewer`, enabled
monitoring notifications and method-specific contact configuration. Telegram enrollment
includes interacting with the bot and binding its code in account settings. This is a separate
recipient prerequisite; it is not evidence of automated channel lifecycle. Existing enrolled
recipients may be reused once the actual channel contract is verified. [Notification requirements source](https://github.com/yandex-cloud/docs/blob/77d87b0af59af7ff0f3a46fa0d77194fd0d4ef6f/en/_includes/monium/notifications-requirements.md),
[Channel setup](https://yandex.cloud/en/docs/monium/operations/alert/create-channel).

## Ingestion, query and credential boundaries

The documented Remote Write destination is `P/api/v1/write`; Remote Read is `P/api/v1/read`.
Write setup specifies folder `monitoring.editor`, read setup `monitoring.viewer`. Their examples
send a service-account API key with **Bearer**, including `bearer_token_file` for writes.
Remote Write examples disable metadata and bound batches; do not assume a successful request
or healthy local scrape proves fresh storage. [Remote Write](https://yandex.cloud/en/docs/monium/operations/prometheus/ingestion/remote-write),
[Remote Read](https://yandex.cloud/en/docs/monium/operations/prometheus/querying/remote-read).

Remote Write is binary POST: protobuf, Snappy block compression, milliseconds since epoch,
float values, and the protocol headers `Content-Encoding: snappy`,
`Content-Type: application/x-protobuf`, `User-Agent`,
`X-Prometheus-Remote-Write-Version: 0.1.0`. Text exposition and native Monitoring JSON cannot be
posted to that endpoint as substitutes. Select a maintained sender/library at the canonical
boundary; no monitoring writer belongs on worker VMs. Do not infer Remote Write 2.0 support.
[Remote Write 1.0 specification](https://prometheus.io/docs/specs/prw/remote_write_spec/).

The generic Monium API authentication guide supports IAM Bearer and API-key `Api-Key` headers,
while the Prometheus-specific guides use API-key Bearer. Follow the specific endpoint contract.
Published key scopes are `yc.monitoring.manage` for reading/writing and `yc.monitoring.read`
for reading. New `yc.monium.metrics.write` and `yc.monium.telemetry.write` are also listed,
but no source establishes them as replacements for Prometheus extension/control APIs.
No minimal role/scope matrix for workspace/channel/routing control was established. IAM token
is documented for extension requests; API-key scopes do not grant missing IAM permissions.
[API authentication](https://yandex.cloud/en/docs/monium/api-ref/authentication),
[API-key scopes](https://yandex.cloud/en/docs/iam/concepts/authorization/api-key#scoped-api-keys).

The supported HTTP suffixes are `/api/v1/query`, `/api/v1/query_range`, `/api/v1/labels`,
`/api/v1/<label_name>/values`, `/api/v1/series`, appended to the workspace query endpoint.
The guide obtains the query base from workspace information, not a public workspace GET.
It spells the label-values path differently from upstream `/api/v1/label/<label_name>/values`;
do not silently correct or rely on either without confirmation. Instant/range queries are enough
for fresh-point verification. `timeout` is ignored; metadata `start`/`end` are ignored;
at most eight `match[]` selectors; lookback is five minutes. Upstream `/rules`, `/alerts`,
`/status/config`, `/openapi.yaml` and Alertmanager `/api/v2/status` are **not** on this managed
supported list. [Managed query contract](https://yandex.cloud/en/docs/monium/operations/prometheus/querying/grafana#restrictions),
[Upstream query API](https://prometheus.io/docs/prometheus/latest/querying/api/).

Use URL-encoded `query` for instant evaluation and `query`, `start`, `end`, `step` for a range;
inspect the JSON success/error envelope, sample timestamps and expected identities, not HTTP
status alone. Those formats come from the supported upstream HTTP query contract. Do not assume
all newer upstream query parameters work in the managed service. [Query formats](https://prometheus.io/docs/prometheus/latest/querying/api/#expression-queries).

## Naming and missing-data caveats

Native Monitoring export is a different API:
`GET https://monitoring.api.cloud.yandex.net/monitoring/v2/prometheusMetrics`, required
`folderId` and `service`, API-key Bearer. It exports text metrics; it does not make existing native
series appear in a workspace. A separate scrape/write route would be required, and migrating
queue metrics is outside this decision. [Native export method](https://yandex.cloud/en/docs/monium/api-ref/MetricsData/prometheusMetrics).

The agent examples query Prometheus `__name__` and show `sys_cpu_CpuCores` plus an application
`http_requests_total`; this is not a general guarantee mapping native dotted names to workspace
names. Verify final names and allowed labels from real workspace samples. Agent-generated `job`
and `instance` labels must not expand the telemetry specification's allowlist accidentally.
[Agent examples](https://yandex.cloud/en/docs/monium/operations/prometheus/ingestion/prometheus-agent#view-metrics).

Five-minute lookback and unsupported stale markers can retain old-looking values longer than
the specification's 90-second freshness. Resource rules must test explicit freshness and trusted
expected membership. Empty query results cannot be equated to idle zero or healthy collection.
Classic histogram `le="+Inf"` is a label; it does not authorize non-finite sample values.
Rule evaluation has a documented two-minute global delay in the shared file-management guide;
verify its alert-specific effect before promising an end-to-end 90-second alarm. [Query lookback](https://yandex.cloud/en/docs/monium/operations/prometheus/querying/grafana#restrictions),
[Limitations](https://yandex.cloud/en/docs/monium/operations/prometheus/#restrictions),
[Evaluation delay](https://yandex.cloud/en/docs/monium/operations/prometheus/recording-rules#rule-specific).

## Smallest path that could meet the approved contract

There is **no proven complete path today** under the requirement to automate lifecycle,
configuration verification, drift detection and reviewed rollback without manual setup.
An existing workspace/channel would reduce creation work but would not establish ownership,
routing read-back or reproducibility. A local “last applied” hash records a client's intent and
cannot detect an out-of-band routing change.

The smallest compliant candidate is one explicitly owned worker diagnostic workspace, existing
enrolled recipients, owned rule filenames, one whole routing file, canonical-boundary ingestion,
and one automated apply/verify/rollback client. It becomes executable only when Yandex provides
an official supported contract for:

1. Workspace create/list/get/update/delete, ownership and endpoint discovery.
2. Channel create/list/get/update/delete, recipient fields and read-back.
3. Alertmanager configuration read-back and replacement concurrency/version semantics, plus
   exact IAM roles/scopes and the conflicting routing/channel limitations.

Obtain those contracts before an implementation plan claims alert lifecycle acceptance. If the
provider confirms these operations are unavailable, return the architectural requirement to the
maintainer for a concrete decision; neither UI automation, browser network reverse engineering,
guessed GETs, an imperative Terraform wrapper nor self-hosted monitoring satisfies the approved
contract. Repository preparation can continue separately without live alert activation.

## Source audit and verification limits

The official docs repository inspected is
[`77d87b0af59af7ff0f3a46fa0d77194fd0d4ef6f`](https://github.com/yandex-cloud/docs/commit/77d87b0af59af7ff0f3a46fa0d77194fd0d4ef6f)
(2026-09-25). The exact `en/monium/api-ref` directory has logging/metric resources and dashboard
gRPC reference, with no workspace/channel service. The complete, untruncated cloud API tree at
[`1c473fc6d1b9ee78e147e1fa9e735e8f6692152e`](https://github.com/yandex-cloud/cloudapi/tree/1c473fc6d1b9ee78e147e1fa9e735e8f6692152e)
contains only dashboard service definitions under Monitoring. These are evidence of the public
surface inspected, not proof that all private service methods are absent.

Read both linked repository contracts before research; inspected English/Russian official guides,
raw published source, exact API-reference directory and complete API definitions. No request was
made to an authenticated cloud endpoint, and no firing, notification, configuration round-trip
or actual workspace naming was live-proven. This artifact is documentation feasibility evidence.
