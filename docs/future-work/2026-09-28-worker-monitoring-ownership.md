# Reconcile worker diagnostic dashboard ownership before activation

## Observed gap

The Managed Prometheus package owns the dashboard's complete baseline, now 19 widgets in Git
after PR #222 (the earlier activation snapshot below had 14).
The worker-pool template in `deploy/monitoring/dashboard.json` now declares three additional native
diagnostic widgets. Applying that template and then the Prometheus dashboard would remove those
extra views. Full Alertmanager routing also assumes a dedicated workspace without foreign rule files.

## Why this does not block repository delivery

The fresh live dashboard snapshot contained 14 widgets. There is no evidence the three worker views
were applied, and the worker deployment/provisioning tools do not update dashboards. ADR-0043 is
accepted; phase-one repository delivery does not apply dashboards or rules, and activating worker
diagnostics is a separate operational step. Native worker-pool control
publication remains untouched by this migration.

## Trigger and required work

Before importing or activating worker diagnostic views on dashboard `fbeketud0mdaupj43of6`, reconcile
their ownership with `deploy/monitoring/prometheus/dashboard.json` and prove both apply orders retain
all intended views. Before adding diagnostic rules to workspace `mon0c97qv2s5uju1ark8`, reconcile
routing ownership; the current dedicated-workspace guard must continue rejecting foreign rule files.
