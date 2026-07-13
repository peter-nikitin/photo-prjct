# 0010: Share the HTTPS edge across public environments

- Status: Accepted
- Date: 2026-07-13
- Deciders: project maintainers
- Supersedes: [0009](0009-separate-staging-http-edge.md)
- Superseded by: none

## Context

The current preemptible staging VM is the project's only active environment and is assigned the
canonical public domain during the transition to separate staging and production infrastructure.
Public DNS now resolves `findme-photo.ru` and `www.findme-photo.ru` to that VM, removing the DNS
failure that required the HTTP-only staging edge in ADR 0009.

The repository already has an accepted Nginx and Certbot HTTPS pattern, but its Compose overlay is
named and selected only for production. Keeping staging HTTP-only would leave the canonical public
URL without trusted HTTPS. Copying the HTTPS topology into a staging-specific overlay would create
two implementations of the same certificate and proxy boundary.

## Decision drivers

- Provide trusted HTTPS for the canonical public domain on the current single active environment.
- Keep Django and PostgreSQL private behind Nginx in every public environment.
- Preserve separate credentials, data, certificates, and release eligibility for staging and
  production.
- Avoid duplicated Nginx, Certbot, and certificate-renewal topology.
- Keep the current preemptible VM classified as staging rather than weakening production-readiness
  gates.

## Considered options

1. Use the production-named HTTPS overlay from staging without changing its boundary.
2. Add a separate staging HTTPS overlay.
3. Use one environment-neutral HTTPS overlay for every public environment.

## Decision

Use one environment-neutral Nginx and Certbot HTTPS overlay for the current staging environment and
the later separate staging and production environments. Environment configuration supplies the
canonical domain, an optional redirect alias, the expected public endpoint, and certificate contact
information. Compose project identity and GitHub Environments keep certificate volumes, data,
credentials, and deployment markers isolated.

The current single active staging environment serves `findme-photo.ru`; `www.findme-photo.ru`
redirects to that canonical host. When production and staging are split, production retains the
canonical and `www` names while staging moves to `staging.findme-photo.ru` using the same overlay and
its own certificate volume.

This decision supersedes the HTTP-only staging topology in ADR 0009. It retains ADR 0007's Nginx and
Certbot pattern. It does not provision production infrastructure or make the current preemptible VM
production-ready.

## Consequences

### Positive

- The canonical public URL can use a trusted, automatically renewed certificate immediately.
- Staging and production validate the same public-edge topology without sharing runtime state.
- Environment-specific domains remain configuration rather than duplicated Compose implementations.
- Django and PostgreSQL stay private to the Compose network.

### Negative

- Certificate bootstrap and domain changes can cause a short edge interruption while port 80 is
  released for HTTP-01 validation.
- Deployment must reconcile certificate names, validate public DNS and HTTPS, and preserve a safe
  activation rollback path.
- The preemptible VM remains an availability risk even though it serves the canonical domain.

### Follow-up

- Deliver the shared edge through separate preparation, activation, and cleanup phases.
- Prove canonical redirects, public HTTPS health, certificate renewal, failure rollback, and release
  marker correctness before removing the HTTP fallback.
- Move staging to `staging.findme-photo.ru` only when a separate production environment exists.

## Validation and rollback

Validate the decision by deploying the shared edge to the current staging Compose project, obtaining
a trusted certificate for the configured names, verifying public HTTP 308 redirects and HTTPS
health, completing a renewal dry run, and proving failed public verification restores the previous
successful release.

Retain the HTTP edge only through initial activation. Before cleanup, rollback restores the previous
image and HTTP edge. After cleanup, rollback retains the validated HTTPS edge and restores the
previous application image. Reconsider the decision if the shared overlay cannot preserve
environment isolation or creates materially different edge requirements between staging and
production.

## References

- [Current architecture](../architecture.md#current-architecture--implemented)
- [Deployment domain assignment](../architecture.md#deployment-domain-assignment--accepted)
- [ADR 0007](0007-nginx-certbot-https-edge.md)
- [Canonical domain HTTPS edge design](../superpowers/specs/2026-07-13-canonical-domain-https-edge-design.md)
