# 0011: Use a minimal shared HTTPS rollout

- Status: Accepted
- Date: 2026-07-14
- Deciders: project maintainers
- Supersedes: [0010](0010-share-https-edge-across-environments.md)
- Superseded by: none

## Context

ADR 0010 selected one environment-neutral Nginx and Certbot overlay for every public environment.
That shared boundary remains appropriate, but ADR 0010 also required automated DNS and certificate
name reconciliation plus a multi-stage release and recovery protocol.

The current deployment has one public VM, one canonical domain, infrequent hostname changes, and no
provisioned production environment. The additional protocol would add more failure modes and
operator surface than this topology currently needs. A smaller contract can retain the security,
environment isolation, and immutable-image guarantees while making initial HTTPS activation easier
to review and operate.

## Decision drivers

- Keep one tested HTTPS topology for staging and future production.
- Obtain trusted HTTPS without weakening the current VM's staging classification.
- Preserve the last successful image and persistent data on deployment failure.
- Minimize certificate issuance attempts and deployment state for a one-domain topology.
- Keep DNS and hostname-change operations explicit when they are rare and operationally sensitive.

## Considered options

1. Retain ADR 0010's automated DNS, certificate-name, and multi-stage release gates.
2. Keep the shared overlay but use a minimal deployment contract with operator preflights.
3. Return to separate HTTP staging and HTTPS production overlays.

## Decision

Retain one environment-neutral Nginx and Certbot HTTPS overlay for every public environment. Compose
project identity and GitHub Environments continue to isolate credentials, data, certificates, and
successful-image markers. `PUBLIC_DOMAIN`, optional `PUBLIC_DOMAIN_ALIAS`, and certificate contact
email are the only HTTPS-edge deployment inputs.

Certificate bootstrap uses the pinned Certbot image to ensure that the stable certificate exists.
When it is absent, deployment makes one issuance attempt for the configured names. When it exists,
normal deployment reuses it without automatically comparing or changing its DNS names. Changing the
canonical name or alias requires an operator-controlled certificate backup, removal, and reissue.

Deployment validates exact canonical redirects and canonical trusted HTTPS health with `curl`. DNS
resolution and reachability are manual activation preflights rather than automated recurring
deployment gates.

The apply process remembers the prior `APP_IMAGE`. If certificate bootstrap, image pull, Compose
reconciliation, local health, or public HTTPS smoke validation fails, that same process restores the
prior image on the selected overlay and exits non-zero. It updates `deployed-image` atomically only
after the requested image and all applicable checks succeed. No independent workflow release-state
machine or separate recovery command is part of this decision.

Preparation, HTTPS activation, and removal of the temporary HTTP edge remain separate releases.
The current VM remains preemptible staging, and assigning it the canonical domain does not satisfy
production-readiness requirements. When production and staging split, production retains the apex
and alias while staging moves to its own hostname using the same overlay and a separate certificate
volume.

## Consequences

### Positive

- Staging and future production share one Nginx, Certbot, and renewal topology.
- The deployment contract has one success marker and one in-process recovery path.
- Normal deployments do not consume certificate issuance attempts or depend on DNS-provider APIs.
- Redirect and health behavior remain externally verifiable with standard TLS validation.
- Django, PostgreSQL, data volumes, and certificate volumes remain private or persistent as before.

### Negative

- DNS drift is not detected by a dedicated deployment gate; failed public smoke checks or operator
  observation provide the signal.
- An existing certificate can become inconsistent with changed domain configuration until an
  operator performs the documented maintenance procedure.
- Certificate reissue and first activation can briefly interrupt the edge while port 80 is released
  for HTTP-01 validation.
- Recovery is best effort within the apply process; an infrastructure-level failure may still need
  manual intervention using preserved logs and volumes.
- The preemptible VM remains an availability risk while it carries the canonical domain.

### Follow-up

- Deliver preparation, activation, and HTTP-edge cleanup as separate reviewed changes.
- Before activation, verify public DNS and ports 80/443 manually and configure the three HTTPS
  inputs in the staging GitHub Environment.
- Validate public redirects, trusted HTTPS health, browser trust, successful-image marker, and a
  Certbot renewal dry run before cleanup.
- Reconsider automated DNS or certificate-name reconciliation only when multiple endpoints,
  frequent hostname changes, IPv6, a load balancer, or a CDN makes manual operation unreliable.

## Validation and rollback

Validate this decision by rendering the shared Compose and Nginx configurations, exercising focused
deployment tests, activating the edge on the current staging Compose project, and observing exact
HTTP 308 redirects, trusted HTTPS health, browser certificate trust, and a successful renewal dry
run. Confirm that a simulated failed apply leaves `deployed-image` unchanged and restores the prior
application image without deleting database or certificate volumes.

Preparation is reversible without live effect because staging remains on HTTP. During activation,
the apply process attempts to restore the prior image and selected edge before failing. Operators
retain the temporary HTTP overlay until activation and renewal evidence are complete. After cleanup,
reverting application configuration uses the last successful immutable image on the validated HTTPS
overlay. Never remove named data or certificate volumes as part of recovery.

Reconsider this decision if the shared overlay cannot preserve environment isolation, recovery
cannot keep the last successful image available, or the public environments develop materially
different edge requirements.

## References

- [Current architecture](../architecture.md#current-architecture--implemented)
- [Deployment domain assignment](../architecture.md#deployment-domain-assignment--accepted)
- [ADR 0005](0005-promote-images-through-staging.md)
- [ADR 0007](0007-nginx-certbot-https-edge.md)
- [ADR 0010](0010-share-https-edge-across-environments.md)
- [Canonical domain HTTPS edge design](../superpowers/specs/2026-07-13-canonical-domain-https-edge-design.md)
- [Canonical domain HTTPS edge implementation plan](../plans/2026-07-13-canonical-domain-https-edge.md)
