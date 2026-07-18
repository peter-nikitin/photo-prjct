# 0009: Separate the staging HTTP edge from the production HTTPS edge

- Status: Superseded
- Date: 2026-07-13
- Deciders: project maintainers
- Supersedes: [0008](0008-temporary-staging-http-fallback.md)
- Superseded by: [0010](0010-share-https-edge-across-environments.md)

## Context

Staging cannot obtain a public certificate while its domain resolves to an unroutable address. The previous fallback selected an HTTP Nginx template at runtime but retained Certbot, certificate volumes, the TLS listener, and a mode-switching Nginx wrapper in every staging deployment. That creates operational paths that staging cannot validate and makes the deployment outcome dependent on more than the staging edge actually needs.

## Decision drivers

- Keep the Django application private behind Nginx in every environment.
- Make staging deployment independent of certificate state until its public DNS is repaired.
- Preserve the accepted Nginx and Certbot HTTPS pattern for a future production VM.
- Make each environment's runtime topology directly visible in versioned Compose files.

## Considered options

1. Keep one Compose file and select HTTP or HTTPS through an environment variable.
2. Publish Django directly on port 8000 while staging DNS is unavailable.
3. Use a shared application Compose file with separate HTTP staging and HTTPS production overlays.

## Decision

Use a shared Compose file for PostgreSQL and Django. Add an HTTP-only staging overlay containing only Nginx on port 80. It has no Certbot service, certificate volumes, TLS listener, or runtime mode selector.

Use a separate HTTPS production overlay that retains the Nginx and Certbot design of ADR 0007. Production uses the HTTPS overlay only after a production VM and publicly routable DNS are provisioned. The staging workflow does not accept an `ENABLE_HTTPS` setting.

## Consequences

### Positive

- Staging has a minimal, inspectable three-service topology: PostgreSQL, Django, and HTTP Nginx.
- Certificate and DNS failures cannot affect staging rollout.
- Future production HTTPS remains explicit rather than being an untested runtime branch.

### Negative

- The staging overlay deliberately does not provide public HTTPS until DNS is repaired.
- Shared and environment-specific Compose files must be supplied together for deployments.

### Follow-up

- Before provisioning production, validate the HTTPS overlay against a routable production domain, certificate issuance, redirect behavior, and renewal.

## Validation and rollback

Validate staging by checking its rendered Compose configuration has only the HTTP Nginx service in the overlay, the web container has the requested immutable image, and Nginx returns HTTP 200 for `/health/`. Revert the deployment revision to restore the previous staging topology; PostgreSQL data remains in the existing named volume.

## References

- [ADR 0003](0003-docker-compose-yandex-cloud.md)
- [ADR 0005](0005-promote-images-through-staging.md)
- [ADR 0007](0007-nginx-certbot-https-edge.md)
- [Minimal staging deployment plan](../plans/2026-07-13-minimal-staging-deployment.md)
