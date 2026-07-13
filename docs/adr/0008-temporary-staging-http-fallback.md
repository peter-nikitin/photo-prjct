# 0008: Temporarily allow HTTP-only staging when public DNS is unroutable

- Status: Superseded
- Date: 2026-07-13
- Deciders: project maintainers
- Supersedes: none
- Superseded by: [0009](0009-separate-staging-http-edge.md)

## Context

ADR 0007 requires a public DNS record and ports 80/443 for Let's Encrypt HTTP-01 validation. The
current staging domain resolves to `198.18.0.117`, a non-public benchmarking address, so the CA
receives NXDOMAIN for the configured domain and cannot issue a certificate. The staging pipeline
must continue to deploy the catalog while DNS ownership is corrected.

## Decision drivers

- Unblock staging deployment without weakening the accepted production HTTPS edge.
- Make the exception explicit, reversible, and impossible to enable accidentally in production.
- Avoid deleting certificate volumes or changing cloud networking.

## Considered options

1. Keep failing deployment until DNS is corrected.
2. Publish Django directly on port 8000 again.
3. Keep Nginx as the only public edge and make its HTTPS mode environment-controlled.

## Decision

Keep Nginx in front of Django. The staging workflow defaults `ENABLE_HTTPS=false`, which selects
an HTTP proxy configuration, skips Certbot issuance, and verifies the local HTTP edge through a
host-resolved probe. Production defaults `ENABLE_HTTPS=true` and retains the ADR 0007 HTTPS path.

This is a staging-only exception. It does not make the unreachable staging hostname usable from the
public Internet; it only restores repeatable deployment until DNS is repaired.

## Consequences

### Positive

- Staging deployments no longer fail solely because the public DNS record is invalid.
- Django remains private behind Nginx and no direct port 8000 exposure returns.
- HTTPS return requires a configuration change, not another code change.

### Negative

- Staging has no trusted public HTTPS endpoint while `ENABLE_HTTPS=false`.
- The deploy health probe proves the VM edge locally, not public DNS reachability.

### Follow-up

Before enabling HTTPS, replace the domain's public A record with the staging VM public IP and wait
for public resolvers to return that address. Then set staging Environment variable
`ENABLE_HTTPS=true` and rerun Deploy staging. Verify HTTP redirect, HTTPS health, certificate SAN,
and `certbot renew --dry-run` before considering the fallback removed.

## Validation and rollback

Validate fallback deployment with an HTTP health probe through Nginx. Roll back the fallback by
setting `ENABLE_HTTPS=true` after DNS repair; the workflow then performs the standard ADR 0007
certificate issuance and HTTPS probe. Do not delete the `letsencrypt` volume.

## References

- [ADR 0007](0007-nginx-certbot-https-edge.md)
- [Architecture deployment topology](../architecture.md#current-architecture--implemented)
