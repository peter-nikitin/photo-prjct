---
name: manage-feature-flags
description: "Use when adding, changing, activating, removing, deploying, or troubleshooting a FindMe Photo runtime feature gate."
---

# Manage Feature Flags

Keep gates as temporary exposure controls. Preserve independent authorization, publication,
migration, worker-protocol, and side-effect protections.

## Change the complete structure

1. Read `docs/adr/0028-operate-one-canonical-deployment.md`,
   `docs/adr/0032-reconcile-code-owned-feature-flags-at-startup.md`, and
   `docs/superpowers/specs/2026-08-22-feature-flag-definition-reconciliation-design.md`. Treat
   `src/backend/feature_flags/registry.py` as the sole definition source.
2. Add a named immutable `FeatureDefinition` to `FEATURE_DEFINITIONS`. Import it at every
   authoritative web entry point and side effect; call the typed evaluation service and preserve
   underlying eligibility and authorization checks.
3. Let `sync_feature_flags` create a new definition in `off`. Change active state only in Django
   Admin: use `staff` for controlled acceptance and `on` only after it passes. Reserve row creation
   for reconciliation, activation for Admin, and request evaluation for reads.
4. Prove ownership at every boundary. Canonical and regular local web inherit `entrypoint.sh` and
   reconcile after migrations; local-purchase web reconciles before its DEBUG-only bootstrap enables
   only named local-review definitions. Verify base-local, local-purchase, and deployed Commerce
   workers plus the photo worker bypass reconciliation.
5. Remove a completed or rejected gate by deleting all guarded call sites and its registry definition
   in one pull request. Let deployed reconciliation remove the stale row; retain Admin history.

## Roll out, verify, and recover

Before the first destructive reconciliation rollout, take a read-only canonical web-container
inventory of feature keys and states. Compare it with the candidate image's `FEATURE_DEFINITIONS`
registry (the registry to be deployed) and classify every absent-registry row as reviewed stale
before merge; save it as the first-rollout recovery record. After deployment, repeat the read-only
comparison against the deployed registry, confirm preserved states and new `off` rows, and inspect
sanitized reconciliation/startup results.

Treat invalid registry validation as a pre-write startup failure; treat a database/reconciliation
failure as a transaction rollback that keeps the candidate unhealthy. For an accidental activation,
set the row to `off` in Admin. For a rollback target with the reconciler/registry, record that it
recreates restored definitions it owns in `off`, removes definitions it does not own, never restores
prior `staff` or `on`, and preserves the database volume. If the target predates reconciliation,
record that it preserves the volume, neither recreates candidate-deleted rows/states nor removes
candidate-created rows; use the saved inventory as the recovery record. Deliberately restore
required exposure in Admin.

## Completion checklist

- Record an explicit outcome for canonical web, local web/local-purchase bootstrap, base local
  Commerce worker, local-purchase Commerce worker, deployed Commerce worker, and photo worker,
  including unchanged boundaries; prove only intended web paths reconcile.
- Cover fail-closed `off`, active-staff `staff`, public `on`, missing rows, reconciliation state
  preservation, and call-site-plus-definition removal with focused tests.
- Run the proportional repository checks, `git diff --check`, and the deployment/read-only evidence
  required by the accepted specification.

Use `registry.py`, `services.py`, `management/commands/sync_feature_flags.py`, `admin.py`,
`entrypoint.sh`, and feature-flag/deployment tests as implementation sources of truth; use the ADRs
and the cited specification for lifecycle and rollback decisions.
