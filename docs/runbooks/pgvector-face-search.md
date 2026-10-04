# Exact pgvector face search: release and recovery

Related: [cutover plan](../plans/2026-10-04-complete-pgvector-face-read-cutover.md),
[ADR 0040](../adr/0040-use-pgvector-for-exact-face-search.md),
[ADR 0041](../adr/0041-accept-pgvector-numerical-boundaries.md).

The application now ranks both uploaded selfies and gallery faces against
`processing_faceembeddingvector`. SFace and AdaFace remain supported. The JSON reader,
parallel writes, comparison command and `pgvector-face-search-read` switch have been removed
from the candidate code. Saved search results and their detection evidence stay in the shared
search tables. The physical `processing_faceembedding` table and two retired search-context
columns remain until a guarded post-commit operation drops them.

Repository tests and the isolated local database rehearsal do not prove current production
completeness, deployment or public behavior. The existing local snapshot dated 2026-09-25 was
restored separately; its 202,899 old embeddings were copied into pgvector and all 17 events
passed the bounded native identity check. Reconcile current production state before release.

## Before the release

1. Record the exact deployed and candidate image revisions, web and worker-pool builds, current
   event generations, accepted detection/native-vector counts, nonterminal search/job/attempt and
   lease counts, temporary selfie cleanup backlog, errors and search latency. Keep private
   receipts free of vectors, photo IDs, object keys and bearer links.
2. Take and verify a database backup on an isolated compatible PostgreSQL 16/pgvector instance.
   Confirm the current production native cohort event by event, including SFace and AdaFace,
   published, unpublished and currently hidden photos that may later be shown. Any missing,
   divergent or invalid native evidence stops the
   release. A search result count or the September snapshot alone is not enough.
3. Drain old worker claims and confirm the candidate worker/web builds. Keep the old JSON table
   available until candidate web replacement succeeds. Review the post-commit contraction flags
   only after the live cohort, old-process drain and recovery boundary are established.

The canonical deployment runs a bounded all-event release gate after its state-only Django
migrations and before it replaces old web. Each event query has a 15-second timeout and the
command rejects more than 1,000 retained events. In the protected Compose context, the same
read-only gate is:

```sh
cd /opt/photo-prjct
compose() {
  sudo docker compose --project-name photo-prjct --env-file .env \
    -f docker-compose.deployment.yml -f docker-compose.https.yml "$@"
}
compose exec -T web python manage.py retire_json_face_embeddings
```

For a single event, `verify_pgvector_face_embeddings --scalar --event-slug EVENT` returns
aggregate eligible, missing, divergent and invalid counts. Its per-event timeout defaults to
15 seconds and may be set from 1 to 60 seconds. Do not run the full value verifier across the
whole production corpus as a release gate.

## Physical contraction

The `processing.0017` and `selfie_search.0007` migrations remove the old model/fields from
Django state only. Old web remains able to use the physical table and columns during the
pre-activation migration. The canonical deployment first verifies the native cohort, activates
and checks candidate web, commits the candidate image, then optionally runs the physical drop.
Once the native-only candidate can accept a write, recovery stays forward-only even if a later
health check fails before commit. Old-image rollback is safe only before candidate activation.

The ordinary CI deployment does not transport the destructive opt-in and reports
`DEPLOY_JSON_RETIREMENT_RESULT=retained`. After that deployment commits, a reviewed operator can
run the physical command on the VM using the actual active image revision:

```sh
web_container="$(compose ps -q web)"
active_image="$(sudo docker inspect --format '{{.Config.Image}}' "$web_container")"
test "$active_image" = "$(cat deployed-image)"
active_revision="$(sudo docker image inspect --format \
  '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$active_image")"
compose exec -T web python manage.py retire_json_face_embeddings \
  --execute --reviewed-release --old-processes-drained --active-build "$active_revision"
```

The `--reviewed-release` and `--old-processes-drained` arguments assert the evidence in the
preceding section; the command cannot establish those operational facts by itself. The canonical
apply script also supports a direct VM invocation with `RETIRE_JSON_FACE_EMBEDDINGS=True`,
`JSON_FACE_RETIREMENT_REVIEWED=True` and `JSON_FACE_OLD_PROCESSES_DRAINED=True` after the same
review. Those flags are deliberately absent from the routine remote workflow transport. The
post-commit command checks the active build, applied migrations,
worker-pool builds and recent sessions, native evidence for all retained events and incoming
foreign keys. It drops exactly `processing_faceembedding` and the two retired search columns in
one transaction, without `CASCADE`. Its SQL uses bounded statement and lock timeouts. It is
idempotent. A refused or interrupted drop leaves the compatible candidate running and requires
forward recovery; the deployment must never restore an old image after the schema is contracted.

Do not run contraction before current-data and process-drain review. With `retained`, the new
reader can run with the unused table temporarily present. If direct apply reports `incomplete`, inspect
its exact guard failure and rerun a reviewed forward operation. A success receipt is not complete
until the table and columns are absent in the deployed database.

## After the release

Verify the deployed revision and worker builds, absence of the old table and columns, native
cohort counts, a selfie and gallery-face search on accessible SFace and AdaFace events, saved
result links, temporary selfie deletion, failure rate and latency. Retain aggregate before/after
receipts. Stop and repair forward on native gaps, worker mismatch, broken links, privacy failures
or capacity regression. Very borderline numerical result changes permitted by ADR 0041 are not a
reason to restore the old reader.

Use [settle pre-cutover searches](settle-pre-cutover-selfie-searches.md) separately to finish
selected valueless unfinished searches with a fixed cutoff and workers paused. It does not delete
unpublished events. Later SFace-to-AdaFace reprocessing and SFace model retirement are a separate
release; SFace native vectors remain until then.
