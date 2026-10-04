# Support settling old searches while workers remain active

The `settle_pre_cutover_searches` command locks a search before its job. The normal job claim and callback paths lock the job before the search. Running the command concurrently with those paths could deadlock in PostgreSQL.

This does not block the current one-time operation: its runbook requires pausing and draining workers before `--execute`, and it skips active leases. Revisit when operators need to run the command while claims, callbacks or recovery remain live. Align the job/search/attempt lock order with the normal lifecycle and test the interleaving before enabling that mode.
