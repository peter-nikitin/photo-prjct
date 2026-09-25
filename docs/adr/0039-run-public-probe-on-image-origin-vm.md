# 0039: Run the public health probe on the image-origin VM

- Status: Accepted
- Date: 2026-09-25
- Deciders: project maintainer
- Supersedes: ADR 0018, public-probe placement only
- Superseded by: none

## Context

ADR 0018 selected a checker outside Yandex Cloud. The implemented GitHub Actions schedule
does not meet its 10–15 minute alert target: the 99 intervals between the latest 100 scheduled
runs were 100–403 minutes. The maintainer requires detection in minutes and chose the existing
image-origin VM as the probe host, accepting that a shared Yandex Cloud failure is not covered.

## Decision drivers

- Detect a failure of the canonical public HTTPS path within minutes.
- Keep the probe off the VM it observes and avoid a new VM or stored API key.
- Do not interrupt image delivery when installing or running the probe.

## Considered options

1. Keep GitHub Actions scheduling and accept multi-hour gaps.
2. Run a scheduled probe on the existing image-origin VM.
3. Add a checker outside Yandex Cloud with its own hosting and credential lifecycle.

## Decision

Run the public HTTPS and certificate probe on the existing `findme-gallery-image-origin` VM.
Schedule it every five minutes as a small host service, separate from image-origin containers.
Write its bounded observations to Yandex Monitoring with a short-lived token obtained from the
VM's attached service account. The probe must not require a new API key, public listener, or
container restart. Yandex Monitoring remains the metric store, dashboard, alert evaluator, and
email delivery path selected by ADR 0018.

The probe checks `https://findme-photo.ru/health/` through the public DNS and TLS path. It is an
independent observation of the application VM, not of the availability of Yandex Cloud or its
`ru-central1-b` zone. Both VMs currently share that zone.

## Consequences

### Positive

- A host timer can maintain the required five-minute cadence without GitHub's observed schedule gaps.
- Existing VM identity and monitoring permissions avoid a new long-lived secret.
- The application VM and image-origin containers do not run the probe.

### Negative

- A shared zone or cloud failure can stop the probe and metric delivery together with the service.
- Image-origin VM maintenance can create a probe no-data condition even when the public site is up.
- The probe adds a small recurring workload and custom-metric writes to the existing VM and folder.

### Follow-up

- Reconsider external hosting if shared-zone detection becomes a requirement.
- Keep missing observation distinct from an observed failed HTTPS response in alerts and the runbook.

## Validation and rollback

Verify timer cadence, fresh success/TLS metric points, a controlled failing check with a separate
validation label, and firing/recovery email. Confirm image delivery and its existing metrics remain
healthy. Roll back by disabling the probe timer and restoring the previous probe package; do not
restart the image-origin containers.

## References

- [ADR 0018](0018-use-managed-yandex-monitoring.md)
- [Minimal monitoring design](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md)
- [Image-origin deployment](../runbooks/gallery-image-delivery.md)
