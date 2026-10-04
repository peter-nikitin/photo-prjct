# Commerce queue size and PostgreSQL internals are not collected

The grouped observability dashboard can show Commerce ready-work age and monitor freshness, but no verified Commerce ready-work count series exists. It also has no verified PostgreSQL connection, lock, transaction or replication series. Adding empty charts would suggest these states are measured when they are not.

This does not block the current dashboard: actionable Commerce age and worker liveness are already measured, and existing application/VM metrics cover the current release path.

Bring this back into scope when Commerce queue depth or database saturation becomes a diagnosed incident or an accepted capacity objective. First verify the producer, labels, scrape route, freshness and cost of the new series; then add corresponding graphs and alert rules in the Git-owned package.
