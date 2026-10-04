# Retire legacy face vectors after candidate activation

This is a release procedure for the AdaFace-only candidate in [ADR 0054](../adr/0054-retire-sface-and-fix-adaface-vector-dimension.md). It is not part of ordinary `migrate` or the automatic deploy. The earlier [historical backfill runbook](historical-adaface-backfill.md) records a previous rollout and is not an executable recovery path after contraction.

## Release gates

1. Read back the exact candidate web image revision and remote worker builds. Confirm current `(3,5)` AdaFace callbacks, ordinary local/public health, selfie and gallery-face search, and worker admission. In `cyclingrace-vechernee-sadovoe`, select a visible photo with a kept current AdaFace face and confirm that the gallery presents its face crop and can start a gallery-face search. Its archived SFace activation must not hide that current evidence. The deploy still checks candidate pulls, active build identity during forward recovery, local/public health and worker health; the obsolete model-label guard has been removed because it queried the retired event selector.
2. Repeat bounded, read-only event, accepted attempt/projection, native 512D vector, ready-result, old job/search, active lease and temporary-selfie counts. A published event with photos and no accepted current projection blocks execution. The previously accepted 636 recognition differences and 73 Klin photos with 174 old detections and no AdaFace job are loss of old matches, not evidence to fabricate or reprocess.
3. Pause and drain old claims and writers. Verify a restorable pre-transition database backup and record its location privately. Keep bearer URLs, photo IDs, object keys, raw vectors and credentials out of receipts.
4. Dry-run `retire_legacy_face_vectors` in the active candidate web. Review aggregate old-vector and raw-payload counts and the published-cohort diagnostics before execution. The old September snapshot demonstrates command mechanics and refusal on incomplete current cohorts; it does not establish present production readiness or representative DDL timing.

## Bounded post-commit execution

After the exact active build, backup, cohort and drain gates above are independently verified, use the active candidate web container and the command's reviewed bounds. The `--execute` mode requires all attestations and the confirmation token. Run bounded batches repeatedly, reading each aggregate receipt and stopping on a guard failure; do not run the final DDL until old vector and payload counts are zero.

```sh
cd /opt/photo-prjct
compose() {
  sudo docker compose --project-name photo-prjct --env-file /opt/photo-prjct/.env \
    -f docker-compose.deployment.yml -f docker-compose.https.yml "$@"
}
web_container="$(compose ps -q web)"
active_image="$(sudo docker inspect --format '{{.Config.Image}}' "$web_container")"
test "$active_image" = "$(sudo cat deployed-image)"
ACTIVE_WEB_REVISION="$(sudo docker image inspect --format \
  '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$active_image")"
test "${#ACTIVE_WEB_REVISION}" -eq 40
compose exec -T web python manage.py retire_legacy_face_vectors
compose exec -T web python manage.py retire_legacy_face_vectors \
  --execute --confirm RETIRE-LEGACY-FACE-VECTORS \
  --active-build "$ACTIVE_WEB_REVISION" --backup-verified \
  --old-processes-drained --current-cohorts-verified \
  --batch-size 500 --max-batches 1 --timeout-seconds 2
compose exec -T web python manage.py retire_legacy_face_vectors \
  --execute --confirm RETIRE-LEGACY-FACE-VECTORS \
  --active-build "$ACTIVE_WEB_REVISION" --backup-verified \
  --old-processes-drained --current-cohorts-verified \
  --batch-size 500 --max-batches 1 --timeout-seconds REVIEWED_LIMIT --finalize
```

`ACTIVE_WEB_REVISION` is the exact revision read back from the active image, and `REVIEWED_LIMIT` is a measured 1–60 second DDL budget. The command refuses finalization while old material remains. Do not copy this example as a claim that those attestations have been made.

## Read-back and recovery

Read back zero SFace pgvector rows and raw historical embedding arrays; `vector(512)` column type, the current-model-only constraint and enabled immutable trigger; unchanged ready-result membership and non-vector processing records; fresh selfie and gallery queries and private worker callbacks. After vector deletion, an old SFace-capable image cannot run against the contracted schema. Recover forward with compatible images or restore the verified pre-transition backup with compatible images.
