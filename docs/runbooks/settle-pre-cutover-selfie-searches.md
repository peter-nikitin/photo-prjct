# Settle unfinished pre-cutover selfie searches

Use `settle_pre_cutover_searches` only after recording a fixed UTC cutover timestamp and an explicit, reviewed list of search UUIDs. The command accepts at most 100 IDs per invocation. It never scans for IDs on its own. Keep the ID list in a restricted operator workspace; command receipts contain aggregate counts only.

1. Pause or drain search workers before the operation. Capture the selected IDs and cutoff from a read-only database snapshot. Record the command's JSON dry-run receipt. Check `found`, `eligible`, frozen model, search/job/attempt statuses, in-progress leases, existing failure-evidence field counts, temporary-object counts, and result-row counts. Missing IDs, active leases, ready rows, saved results, and post-cutoff rows are excluded.
2. Run the same command with `--execute`. For example, inside the canonical Django web container:

   ```sh
   python manage.py settle_pre_cutover_searches --cutoff 2026-10-04T00:00:00+00:00 --limit 2 --search-id UUID_A --search-id UUID_B
   python manage.py settle_pre_cutover_searches --cutoff 2026-10-04T00:00:00+00:00 --limit 2 --search-id UUID_A --search-id UUID_B --execute
   ```

3. Keep both privacy-safe JSON receipts. Verify `after.eligible` is zero for the selected batch. A `cleanup_pending` count means temporary-object deletion failed; rerun the same explicit invocation after storage recovers. Repeat execution is idempotent. Recheck ready and post-cutoff search and result counts separately against the pre-operation snapshot.

The command marks eligible searches and jobs failed, expires only unfinished attempts, and deletes temporary selfies before finalizing searches. Existing attempt error codes/details and search failure codes remain as observed; an empty field means the cause was not recorded. No result rows are created or deleted. A search with any result row is skipped. Do not infer a worker or model failure from the administrative `failed` status.
