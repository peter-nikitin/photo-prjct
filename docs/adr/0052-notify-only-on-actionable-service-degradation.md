# 0052: Notify only on actionable service degradation

- Status: Accepted
- Date: 2026-10-04
- Deciders: project maintainer; explicitly approved the five-day alert-audit response in conversation
- Supersedes: none
- Superseded by: none

## Context

The dedicated Managed Prometheus workspace evaluates Git-owned public, VM, Commerce, worker and
image-delivery alerts. Its current Alertmanager route sends every `project="findme-photo"` rule to
email and Telegram. In the 2026-09-29 to 2026-10-04 audit, brief delays in queue, cloud and
publisher observations fired several critical alerts together; host and runtime diagnostic rules
had identical firing intervals. The application-wide HTTP 5xx rule fired on worker-control routes,
without observed customer-route 5xx. Real backlog, disk pressure and image-origin error bursts
also occurred. Evaluator episodes are not a count of delivered messages.

## Decision drivers

- Notify the operator about real customer-facing or processing degradation in minutes.
- Preserve visibility into missing or stale telemetry without treating it as service failure.
- Avoid duplicate notifications for one cause and retain independently useful diagnostics.
- Keep the public availability probe independent of the canonical VM and its failed-check signal
  distinct from a missing probe observation.

## Considered options

1. Send every rule to both channels and tune individual thresholds only.
2. Route actionable degradation to the operator and keep telemetry-only signals as non-notifying
   diagnostic rules and dashboard state.
3. Remove diagnostic rules and rely only on service-health alerts.

## Decision

Select option 2. Email and Telegram receive firing and recovery notifications only for sustained,
measured service degradation or an imminent, actionable loss of service capacity. This includes
observed public HTTPS failure, customer-request or image-delivery errors, overdue ready work with
evidence of sustained inability to process it, and critically low root-disk capacity. A transient
worker registration rejection or a busy pool without overdue work does not by itself establish
customer or processing degradation.

Missing/stale probe, VM, Commerce, worker or image-origin observations remain visible as unknown
diagnostic state in Monium and on the dashboard. They do not impersonate a healthy zero or an
observed outage, and do not notify the operator solely because telemetry is absent. Remove exact
duplicate diagnostic alerts; require a sustained condition before any remaining diagnostic rule
enters firing state. CPU, memory, certificate-expiry and other early-warning signals remain
diagnostic unless a reviewed rule establishes imminent actionable service loss.

Scope the HTTP 5xx service alert to customer-serving routes. Worker-control 5xx remain observable
through their metrics and worker-specific diagnostics; worker impact is judged through fresh queue,
capacity and age evidence. Group correlated service symptoms so one incident does not generate
separate routine pages. Notification routing is an explicit rule label/policy, not a blanket match
on the project label. This changes notification policy, not worker autoscaling or remediation.

## Consequences

### Positive

- Short metric-delivery delays stop producing multiple operator notifications.
- Notifications have a clearer relationship to customer or processing impact.
- Diagnostic graphs and rule states still explain gaps during investigation.

### Negative

- A monitoring outage can leave the operator blind without sending an independent notification.
- Excluding internal HTTP errors from the service alert requires separate worker-impact signals
  and route-label contract checks.
- Correlation and persistence windows add detection delay to some non-public signals.

### Follow-up

- Change the Git-owned rules and Alertmanager route, including their behavior fixtures.
- Replay candidate rules against the audited five-day history before activation, then verify
  fresh rule evaluation and actual firing/recovery delivery to both operator channels.
- Inventory native UI alerts and retire duplicates only after the replacement signal and delivery
  have been proved. UI-created alerts are outside the Git-owned routing policy until migrated.

## Validation and rollback

Compare candidate service notifications with the recorded backlog, disk and image-origin events
and reject notifications caused only by short observation gaps. Test sustained public failures,
missing observations, internal-versus-customer HTTP routes, queue saturation and recovery. Live
acceptance requires fresh metric samples, matching owned rules/dashboard and received email and
Telegram firing/recovery messages. Restore the prior reviewed Git rules/routing if the new policy
misses a controlled service failure; routing has no confirmed server-side GET backup.

## References

- [ADR 0018: Managed Yandex Monitoring](0018-use-managed-yandex-monitoring.md)
- [ADR 0039: Public probe placement](0039-run-public-probe-on-image-origin-vm.md)
- [ADR 0048: Worker alerts](0048-reuse-managed-prometheus-for-worker-alerts.md)
- [Monitoring package](../../deploy/monitoring/prometheus/README.md)
