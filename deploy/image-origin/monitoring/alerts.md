# Gallery image delivery alert contract

The Git-managed Prometheus rules in
[`deploy/monitoring/prometheus/rules.yml`](../../monitoring/prometheus/rules.yml) now define the
image-origin host, Nginx, and imgproxy alerts. They are not live merely because this file is in
Git: the image-origin Unified Agent must first send the additional series to the existing workspace,
and the monitoring control must then pass its fresh-sample preflight and apply the rules. The
ordered activation and rollback are in the [monitoring README](../../monitoring/prometheus/README.md).

| Signal | Rule | Evaluation |
| --- | --- | --- |
| Nginx, imgproxy, or VM telemetry missing | `ImageOriginTelemetryMissing` | No sample for 5m; one incident even if multiple sources disappear |
| Origin 5xx | `ImageOrigin5xxDegradation` | >2% of at least 20 responses over 5m, sustained 2m |
| Origin 429 | `ImageOriginRateLimited` | >1% of at least 20 responses over 5m, sustained 2m |
| VM CPU | `ImageOriginCPUPressure` | >90% using 5m rates, sustained 10m |
| VM available memory | `ImageOriginMemoryPressure` | <15%, sustained 10m |
| Root filesystem free space | `ImageOriginDiskSpaceCritical` | <10% or <5 GiB, sustained 10m |
| Rejected origin authorization | `ImageOriginAuthRejections` | >50 requests over 5m, sustained 2m |
| imgproxy errors | `ImgproxyErrors` | Any increase in 5m, sustained 5m |
| imgproxy p95 | `ImgproxyLatencyHigh` | >1.5s with at least 20 requests over 5m, sustained 5m |

The [Git-managed dashboard](../../monitoring/prometheus/dashboard.json) includes this VM, CDN
traffic, Nginx responses, imgproxy concurrency, p50/p95, and non-overlapping duration intervals.
The queries use this VM's exact Monitoring `host` ID and the CDN resource name. Dashboard charts use
existing native Monitoring series and require no new collection.

## Signals not yet available as reliable alerts

- CDN edge 5xx is visible as a native `yccdn` metric and chart. The supported Git-managed alert
  API in this package evaluates only the Prometheus workspace; the existing CDN series is not in
  that workspace. Origin 5xx covers requests reaching Nginx but cannot detect an edge-only failure.
- A continuous image-delivery canary needs a stable, safe test image and its signed URL lifecycle.
  The current public probe checks the main site's health, not a delivered image.
- `imgproxy.errors_total` distinguishes queue and timeout errors, not the HTTP status returned by
  Object Storage. It cannot prove a storage-specific 403. No bucket key, signed URL, or raw source
  URI should be added as a metric label or logged for this purpose.
- Docker restart and OOM state are not exported by the current unprivileged public probe. Process
  and host gauges help diagnose pressure but do not identify a container exit reason.
- CDN HIT ratio needs a defined warm cohort and a measured baseline before paging; cold traffic
  alone must not trigger it.

Missing telemetry is unknown, never healthy. All ratio alerts require observed traffic. The
existing operator Alertmanager route sends both firing and recovery to the approved email and
Telegram channels; delivery still needs a live drill after activation.
