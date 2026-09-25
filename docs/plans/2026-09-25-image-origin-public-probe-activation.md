# Image-origin public probe activation

- Date: 2026-09-25
- Status: In progress
- Owner: project maintainer
- Specification: [Image-origin hosted public health probe](../superpowers/specs/2026-09-25-image-origin-public-probe-design.md)
- ADR impact: [ADR 0039](../adr/0039-run-public-probe-on-image-origin-vm.md) supersedes only the external-probe placement in ADR 0018.
- Execution: use `$execute-implementation-plan` for repository tasks and review.

## Scope and order

1. **Alert manifest correction.** In `deploy/monitoring/alerts.md` and its contract test, remove `folderId` from metric label selectors while preserving folder resource/query context. Prove with focused tests against the observed Monitoring label model.
2. **Probe runtime.** Extend `scripts/monitor_public_health.py` to obtain a VM metadata IAM token at each invocation while retaining the existing GitHub API-key path until cutover. Add tests for metadata response, Bearer authorization, failed public checks, failed metric writes, and secret-free diagnostics. The probe must be executable with Python's standard library on Ubuntu 24.04.
3. **Independent VM installer.** Add a self-contained image-origin probe package with a host service/timer, exact folder/target configuration, idempotent installation, a retained previous package, and rollback. It must not call `deploy/image-origin/apply.sh`, restart Compose, or expose a port. Add focused shell/systemd contract tests and an offline install rehearsal.
4. **Deployment transport.** Add one manual GitHub workflow using the existing image-origin SSH host-key/bastion and OIDC/Lockbox identity. Restrict its secret projection to the transport key. It stages only the probe package, runs the installer, and records a safe result. Add workflow/secret-manifest tests. Do not remove the old GitHub schedule yet.
5. **Monitoring resources and cutover.** Read the live folder's dashboards, channels, and alerts; identify the maintainer account for email. Prepare an exact resource diff and monthly cost estimate. After fresh approval, install the VM timer, confirm two canonical points, remove the GitHub schedule, then create or update one dashboard, one email channel, and seven baseline alerts. Keep Commerce placeholders inactive. Record IDs and URLs.
6. **Live validation and reconciliation.** Verify the five-minute cadence and alert conditions, controlled failure/recovery and email, image-origin health, public health, and rollback readiness. Update the runbook, architecture and EJ-009 only to evidence-supported statuses. Run final selector, `make check`, and every selected expensive suite before the single task commit/PR. Wait for green CI, merge, deployment and live proof.

## Operational gates

- Target VM: `epdf6696opq3ock91pih`, image-origin at private `10.129.0.21`, folder `b1g2qttgfhb4gdunvlge`; existing attached service account `ajef0cammpfg75gn7ntd` already has `monitoring.editor`.
- Sole notification recipient: existing cloud owner account `ajeo8gv7nlo4tgb9u9r4`; its inherited role covers Monitoring viewing, so no IAM binding change is planned.
- The VM has 2 vCPU and 4 GiB, hosts the current image-origin containers, and has recent available memory around 3.5 GiB. Before installation, recheck health, utilization, exact release, host key, service identity, and available disk.
- No new VM, API key, service account, public port, or image-origin container restart is planned. Stop if the read-only audit contradicts that assumption.
- `manage-yandex-cloud` requires fresh explicit approval for any pricing, access, or availability-affecting live command. Present exact commands, target IDs, expected cost, validation, and rollback after preparing the reviewable package.
- If a live check fails, disable only the new timer, restore its prior package, verify public health and image-origin health, and leave the prior GitHub schedule running until the replacement is proven.

## Reconciliation

ADR 0039 records the accepted same-zone blind spot. The existing minimal-monitoring spec and plan remain historical for their delivered VM/application metrics; this plan governs the public-probe replacement and final activation. No data migration or durable worker-state change is involved.
