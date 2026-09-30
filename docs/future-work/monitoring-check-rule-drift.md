# Monitoring check should report rule drift before checking snapshots

Observed on 2026-09-30: after image-origin telemetry passed the fresh-sample preflight,
`monitoring.yml` with `action=check` failed with `rule evaluation snapshot missing, stale or
failed`. The reviewed Git package had a new rule group, while the live workspace still
held the previous package. `check` asked for snapshots of the new group before reporting
that the live rules differed.

This does not block the image-origin rollout: `apply` separately preflights the data,
saves the previous rules and dashboard, writes the new rules, and verifies their
post-write snapshots. Live read-back confirmed the new rule content and snapshots.

Return to this when using `action=check` as a pre-apply drift report for a changed rule
package. Make it report `rules_match=false` without requiring snapshots for rules that
have not been applied; still verify snapshots when live and desired rule content match.
