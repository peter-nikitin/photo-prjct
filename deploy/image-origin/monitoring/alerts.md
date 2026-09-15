# Gallery image delivery alert contract

These are activation requirements for the separate image origin. The application can deploy with
`gallery-cdn-images=off` before the origin, CDN, credentials, or these alerts exist. Task 6 records
resource IDs, exact live metric selectors and notification-channel IDs before staff activation.
No alert in this package is claimed to be live. Missing telemetry is unknown, never healthy.

| Alert | Source and aggregation | Threshold | Evaluation window |
| --- | --- | --- | --- |
| Image 5xx | CDN `edge.requests_status`, 5xx / all responses for the selected resource | >2% | 5m |
| Origin CPU | `sys.system.UsefulTime`, VM cores combined | >90% | 10m |
| Origin memory | `1 - sys.memory.MemAvailable / sys.memory.MemTotal` | >85% | 10m |
| Origin 429 | derivative of `origin.image_origin_limited_total` / all `origin.image_origin_responses_total` | >1% | 5m |
| Warm-cohort CDN hit ratio | CDN `edge.requests_cache_status`, HIT / total for a confirmed warm observation window | <70% | 30m |
| Object Storage 403 | **Activation blocker: a documented 403-specific source must be established in Task 6** | any 403 in each of five consecutive 1m windows | 5 consecutive 1m windows |
| Origin authentication rejection | derivative of `origin.image_origin_auth_rejected_total`, converted to requests/minute | >10/min | 5m |

The origin response panel separately displays `origin.image_origin_responses_total{status_class="5xx"}`.
The source-error panel uses native `imgproxy.errors_total{type="..."}` with its observed bounded
error types. Any positive source-error rate for 5m warrants investigation; it is **not** an S3 403
measurement and cannot satisfy that activation requirement. The image transform duration panel
uses `imgproxy.request_duration_seconds_*` and the concurrency panel uses `imgproxy.workers`,
`imgproxy.images_in_progress` and `imgproxy.requests_in_progress`.

## S3 status observability boundary

The current [Object Storage metric reference](https://yandex.cloud/en/docs/monitoring/metrics-ref/storage-ref)
documents bucket request rates by method, but no HTTP-status dimension. imgproxy v4.0.12 Prometheus
labels errors by type, without upstream status. Its logs include source URI even at error level,
so this package discards them. Do not invent a `status=403` selector or enable raw request logging.
Task 6 must validate a supported status signal, such as an appropriately minimized managed audit
source, and prove the five consecutive windows. Until then, keep `gallery-cdn-images=off`.

## Metric scope and delivery

Bind custom metric queries to this VM's `host` and the configured folder. Bind CDN queries to the
single recorded CDN resource, and managed storage observations to the private bucket. Custom
endpoints expose only fixed status classes, counters, durations, worker counts and process/VM
measurements; they contain no URLs, object keys, event/photo IDs or credentials. The njs dictionary
is 32 KiB and stores a fixed set of eight keys. A metrics failure does not fail an image response.

Use the existing operator notification channel for firing and recovery. Zero traffic suppresses
ratio alerts; missing telemetry produces a separate unknown-state operational check. CDN native
metrics normally arrive about every three minutes, so allow for collection delay while preserving
the required evaluation windows. Confirm label names from live series before importing the
[dashboard](dashboard.json); provider status label names are intentionally represented by
placeholders until that discovery is recorded.

References: [CDN metrics](https://yandex.cloud/en/docs/monitoring/metrics-ref/cdn-ref),
[imgproxy v4 Prometheus implementation](https://github.com/imgproxy/imgproxy/blob/v4.0.12/monitoring/prometheus/prometheus.go).
