---
name: deliver-operational-change
description: Use when delivering a small DNS, public-domain, HTTPS, Nginx, Certbot, Docker Compose, GitHub Actions, or single-Yandex-Cloud-VM change in FindMe Photo.
---

# Deliver an Operational Change

Use the fastest safe path for a small, reversible operational change. Do not turn a one-domain or
one-VM task into a reusable deployment platform.

## Establish the boundary

1. Read `AGENTS.md`, `deploy/apply-deployment.sh`, `docs/architecture.md`, the ADR index, applicable
   accepted ADRs, and the current workflow and Compose files before proposing changes.
2. Confirm one observable outcome and the smallest rollback. Reuse the current apply entrypoint and
   existing services.
3. Use the fast lane only when the change is limited to one active VM/domain, is reversible without
   data migration, and does not alter an accepted decision.
4. Take a scope checkpoint before introducing multi-environment orchestration, a new service,
   candidate or other persistent state, a DNS/SAN management platform, data migration, a
   pricing-affecting cloud change, or an ADR conflict. Ask whether the expansion is really required.

Keep a new durable decision `Proposed` until its minimal implementation is agreed.
Do not create a separate documentation PR; use one worktree, one branch, and one pull request.

## Diagnose before changing

- Verify public records with authoritative DNS or DNS-over-HTTPS. Results in `198.18.0.0/15` may be
  local fake-IP interception and are not evidence of public DNS state.
- Inspect live configuration and logs read-only before editing. Never expose secrets in output.
- For Yandex Cloud, also use `$manage-yandex-cloud`. Approval of a plan is not approval to execute.
  Obtain its required confirmation before any mutation or pricing-affecting action.

## Implement and verify

1. Add a small behavioral test for the failure and success path, then make the minimal change.
2. Preserve the existing rollback path. Never destroy Docker volumes or replace durable data as a
   rollback shortcut.
3. Run bounded checks for the changed surface. Use a temporary `.env` populated from `.env.example`
   and the repository's known virtual environment when the worktree lacks them; do not copy secrets.
4. Use one combined review for requirements and quality, fix findings in the same branch, then run
   one final proportional verification including `git diff --check`.
5. Re-read applicable ADRs and `docs/architecture.md` after verification. Record one result: no ADR
   impact, conformance, architecture update, new ADR, or superseding ADR. Update implemented facts
   in the same change; stop for explicit authority rather than contradicting an accepted ADR.
6. Push one draft pull request with the outcome, rollback, and evidence. Do not merge or mutate the
   live environment unless the user explicitly requested and authorized that action.
