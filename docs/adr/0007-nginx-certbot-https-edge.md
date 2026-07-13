# 0007: Use Nginx and Certbot for the HTTPS edge

- Status: Accepted
- Date: 2026-07-13
- Deciders: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The staging Django container is healthy on the VM, but it is exposed only on port 8000. The public
domain has no listener on ports 80 or 443, so it cannot provide HTTPS or an HTTP-to-HTTPS redirect.
The product needs a durable HTTPS edge that preserves the Docker Compose deployment model and can
accommodate multiple application replicas later.

## Decision drivers

- Provide a trusted certificate and automatic renewal without a paid certificate service.
- Keep application containers private to the Compose network.
- Use a familiar, independently configurable reverse proxy boundary.
- Preserve a simple VM and Docker Compose operating model.

## Considered options

1. Publish Django directly on port 80 and add TLS support to the application server.
2. Use Caddy for reverse proxying and automatic certificates.
3. Use Nginx as the HTTPS edge and Certbot for Let's Encrypt issuance and renewal.

## Decision

Use an Nginx container as the only public Compose service. It publishes ports 80 and 443, serves
ACME HTTP-01 challenges, redirects ordinary HTTP requests to HTTPS, and proxies HTTPS requests to
the internal Django service. Django is not published on a host port.

Use Certbot with Let's Encrypt. Certificate material and the ACME account are persistent Compose
volumes. The deployment bootstraps a missing certificate with Certbot standalone before Nginx starts;
subsequent renewals use the Nginx-served webroot. The public domain and certificate-notification
email are environment configuration, not repository values.

This ADR establishes the edge pattern for staging and a later production VM. It does not select a
CDN, WAF, load balancer, multi-region topology, or application autoscaling mechanism.

## Consequences

### Positive

- Users receive HTTPS with a trusted, automatically renewed certificate.
- Django and PostgreSQL are not directly reachable from the Internet.
- Nginx is a stable boundary for later static delivery, rate limits, caching, and upstream replicas.

### Negative

- The deployment has two additional containers and certificate volumes.
- The public domain must resolve to the VM and ports 80 and 443 must remain reachable for HTTP-01.
- Initial certificate issuance can fail because of DNS, port conflicts, or Let's Encrypt rate limits.

### Follow-up

- Validate certificate issuance, renewal, HTTPS health, redirect behavior, and Django admin CSRF.
- Consider a CDN/WAF or managed load balancer only after traffic and availability requirements justify
  their cost and operational complexity.

## Validation and rollback

Validate `https://<public-domain>/health/` with a certificate whose subject matches the domain, an
HTTP 308 redirect from port 80, and a successful `certbot renew --dry-run`. Revert to the preceding
Compose/workflow revision if the edge rollout fails; certificate volumes remain intact for recovery.

## References

- [Architecture deployment topology](../architecture.md#current-architecture--implemented)
- [ADR 0003](0003-docker-compose-yandex-cloud.md)
- [ADR 0005](0005-promote-images-through-staging.md)
- [HTTPS edge implementation plan](../plans/2026-07-13-https-edge.md)
