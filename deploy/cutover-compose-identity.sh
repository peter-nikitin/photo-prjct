#!/bin/sh

set -eu
umask 077

source_project=photo-prjct-staging
destination_project=photo-prjct
source_pgdata=photo-prjct-staging_pgdata
source_letsencrypt=photo-prjct-staging_letsencrypt
source_certbot_webroot=photo-prjct-staging_certbot-webroot
destination_pgdata=photo-prjct_pgdata
destination_letsencrypt=photo-prjct_letsencrypt
destination_certbot_webroot=photo-prjct_certbot-webroot

usage() {
    echo "Usage: $0 (--dry-run|--confirm-canonical-compose-identity-cutover) --backup-dir ABSOLUTE_DIRECTORY" >&2
    exit 2
}

fail() {
    echo "cutover-compose-identity: $*" >&2
    exit 1
}

mode=""
backup_dir=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run|--confirm-canonical-compose-identity-cutover)
            [ -z "$mode" ] || usage
            mode="$1"
            ;;
        --backup-dir)
            shift
            [ "$#" -gt 0 ] || usage
            [ -z "$backup_dir" ] || usage
            backup_dir="$1"
            ;;
        *)
            usage
            ;;
    esac
    shift
done

[ -n "$mode" ] && [ -n "$backup_dir" ] || usage
case "$backup_dir" in
    /*) ;;
    *) fail "backup directory must be an absolute path" ;;
esac

: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"
: "${DB_NAME:?Set DB_NAME}"
: "${DB_USER:?Set DB_USER}"
[ -d "$DEPLOY_ROOT" ] || fail "DEPLOY_ROOT is not a directory"
[ -f "$DEPLOY_ROOT/.env" ] || fail "DEPLOY_ROOT/.env is required"
[ -f "$DEPLOY_ROOT/docker-compose.deployment.yml" ] || fail "deployment Compose file is required"
[ -f "$DEPLOY_ROOT/docker-compose.https.yml" ] || fail "HTTPS Compose file is required"
[ -f "$DEPLOY_ROOT/deploy/apply-deployment.sh" ] && [ -r "$DEPLOY_ROOT/deploy/apply-deployment.sh" ] || fail "generic deployment entrypoint is required"
[ -d "$backup_dir" ] || fail "backup directory must already exist"
[ -w "$backup_dir" ] || fail "backup directory is not writable"

compose() {
    compose_project="$1"
    shift
    docker compose --project-name "$compose_project" \
        --env-file "$DEPLOY_ROOT/.env" \
        -f "$DEPLOY_ROOT/docker-compose.deployment.yml" \
        -f "$DEPLOY_ROOT/docker-compose.https.yml" "$@"
}

volume_names() {
    volume_names="$(docker volume ls \
        --format '{{.Name}}')" || return 1
    printf '%s\n' "$volume_names" | sed '/^$/d' | sort
}

names_with_prefix() {
    prefix=$1
    volume_names | while IFS= read -r volume; do
        case "$volume" in "$prefix"*) printf '%s\n' "$volume" ;; esac
    done
}

require_exact_source_volumes() {
    expected="$(printf '%s\n' \
        "$source_certbot_webroot" \
        "$source_letsencrypt" \
        "$source_pgdata" | sort)"
    actual="$(names_with_prefix "${source_project}_" | sort)" || fail "could not inventory source volumes"
    [ "$actual" = "$expected" ] || fail "unexpected source volumes"
    for volume in "$source_pgdata" "$source_letsencrypt" "$source_certbot_webroot"; do
        docker volume inspect "$volume" >/dev/null || fail "required source volume is missing: $volume"
    done
}

require_empty_destination() {
    destination_volumes="$(names_with_prefix "${destination_project}_" | sort)" || fail "could not inventory destination volumes"
    [ -z "$destination_volumes" ] || fail "canonical destination volumes already exist"
    for volume in "$destination_pgdata" "$destination_letsencrypt" "$destination_certbot_webroot"; do
        if docker volume inspect "$volume" >/dev/null 2>&1; then
            fail "canonical destination volume already exists: $volume"
        fi
    done
    destination_containers="$(docker ps -a --format '{{.Names}}' | sed -n "/^$destination_project[-_]/p" | sed -n "/^$source_project[-_]/!p")" || fail "could not inventory destination containers"
    [ -z "$destination_containers" ] || fail "canonical destination containers already exist"
    destination_networks="$(docker network ls --format '{{.Name}}' | sed -n "/^$destination_project[_-]/p" | sed -n "/^$source_project[_-]/!p")" || fail "could not inventory destination networks"
    [ -z "$destination_networks" ] || fail "canonical destination networks already exist"
}

require_stopped_writers() {
    for service in web worker; do
        writer_containers="$(docker ps --format '{{.Names}}' | sed -n "/^$source_project[-_]$service[-_]/p")" || fail "could not inventory source writers"
        [ -z "$writer_containers" ] || fail "web or worker writers are still running"
    done
}

require_empty_backup_targets() {
    for name in \
        postgresql.dump \
        certificates.tar.gz \
        source-row-counts.txt \
        destination-row-counts.txt \
        photo-prjct-staging.tables \
        photo-prjct.tables; do
        [ ! -e "$backup_dir/$name" ] || fail "backup target already exists: $name"
    done
}

database_row_counts() {
    project="$1"
    output="$2"
    tables="$backup_dir/$project.tables"
    compose "$project" exec -T db \
        psql --username "$database_user" --dbname "$database_name" -At -c "SELECT quote_ident(table_name) FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name" \
        > "$tables" || fail "could not list database tables for $project"
    : > "$output"
    while IFS= read -r table; do
        [ -n "$table" ] || continue
        count="$(compose "$project" exec -T db psql --username "$database_user" --dbname "$database_name" -At -c "SELECT count(*) FROM $table")" || \
            fail "could not count rows for $table"
        printf '%s=%s\n' "$table" "$count" >> "$output"
    done < "$tables"
    rm -f "$tables"
}

clear_candidate_compose_interpolation() {
    unset \
        APP_IMAGE WORKER_IMAGE SECRET_KEY DEBUG ALLOWED_HOSTS DB_NAME DB_USER DB_PASSWORD DB_HOST DB_PORT \
        PUBLIC_DOMAIN PUBLIC_DOMAIN_ALIAS LETSENCRYPT_EMAIL \
        MEDIA_STORAGE_BACKEND MEDIA_S3_ENDPOINT_URL MEDIA_S3_REGION MEDIA_S3_PUBLIC_BUCKET \
        MEDIA_S3_ACCESS_KEY_ID MEDIA_S3_SECRET_ACCESS_KEY \
        PHOTO_UPLOAD_ENABLED PRIVATE_MEDIA_S3_BUCKET PRIVATE_MEDIA_S3_ACCESS_KEY_ID \
        PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY PRIVATE_MEDIA_ALLOWED_ORIGINS \
        PHOTO_PROCESSING_ENABLED PHOTO_PROCESSING_PREVIEW_ENABLED PHOTO_PROCESSING_FACE_ENABLED \
        PHOTO_PROCESSING_WORKER_TOKEN PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS \
        PHOTO_PROCESSING_MAX_REQUEST_BYTES PHOTO_WORKER_BUILD PHOTO_WORKER_LEASE_SECONDS \
        PHOTO_WORKER_PROCESSOR_IDENTITIES PHOTO_WORKER_PROCESSOR_TYPES PHOTO_WORKER_REPLICAS \
        SELFIE_SEARCH_MAX_UPLOAD_BYTES SELFIE_SEARCH_MAX_PIXELS SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS \
        SELFIE_SEARCH_EMBEDDING_MODEL SELFIE_SEARCH_EMBEDDING_DIMENSIONS \
        SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD SELFIE_SEARCH_TEMPORARY_PREFIX \
        SELFIE_SEARCH_LIFECYCLE_MAX_AGE_HOURS SELFIE_FEEDBACK_ENABLED SELFIE_FEEDBACK_S3_BUCKET \
        SELFIE_FEEDBACK_S3_ACCESS_KEY_ID SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY \
        SELFIE_FEEDBACK_S3_ENDPOINT_URL SELFIE_FEEDBACK_S3_REGION SELFIE_FEEDBACK_KMS_KEY_ID \
        SELFIE_FEEDBACK_STORAGE_PREFLIGHT_CONFIRMED
}

restart_source() {
    clear_candidate_compose_interpolation
    source_processing_enabled="$(sed -n 's/^PHOTO_PROCESSING_ENABLED=//p' "$DEPLOY_ROOT/.env" | head -n 1)"
    source_worker_replicas="$(sed -n 's/^PHOTO_WORKER_REPLICAS=//p' "$DEPLOY_ROOT/.env" | head -n 1)"
    case "$source_processing_enabled" in
        True)
            case "$source_worker_replicas" in 1|2) ;; *) return 1 ;; esac
            compose "$source_project" --profile worker up -d --scale "worker=$source_worker_replicas" --remove-orphans
            ;;
        False) compose "$source_project" up -d --remove-orphans ;;
        *) return 1 ;;
    esac
}

mutation_started=0
recover_on_failure() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ "$status" -ne 0 ] && [ "$mutation_started" -eq 1 ]; then
        recovery_failed=0
        compose "$destination_project" down --remove-orphans >/dev/null 2>&1 || recovery_failed=1
        restart_source || recovery_failed=1
        if [ "$recovery_failed" -ne 0 ]; then
            echo "cutover-compose-identity: recovery failed" >&2
            exit 1
        fi
    fi
    exit "$status"
}
trap recover_on_failure EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

require_exact_source_volumes
require_empty_destination
require_stopped_writers
require_empty_backup_targets

if [ "$mode" = --dry-run ]; then
    printf '%s\n' "DRY RUN: source volumes are preserved; canonical destination is empty; writers are stopped."
    exit 0
fi

database_user="$(compose "$source_project" exec -T db printenv POSTGRES_USER)" || fail "could not determine source database user"
database_name="$(compose "$source_project" exec -T db printenv POSTGRES_DB)" || fail "could not determine source database name"
[ "$database_user" = "$DB_USER" ] || fail "source database user does not match deployment configuration"
[ "$database_name" = "$DB_NAME" ] || fail "source database name does not match deployment configuration"
case "$database_user" in ''|*[!A-Za-z0-9_]*) fail "source database user is invalid" ;; esac
case "$database_name" in ''|*[!A-Za-z0-9_]*) fail "source database name is invalid" ;; esac

compose "$source_project" exec -T db pg_dump --format=custom --file=- --username "$database_user" --dbname "$database_name" \
    > "$backup_dir/postgresql.dump" || fail "PostgreSQL dump failed"
[ -s "$backup_dir/postgresql.dump" ] || fail "PostgreSQL dump is empty"
docker run --rm -v "$backup_dir:/backup:ro" postgres:16 \
    pg_restore -l /backup/postgresql.dump >/dev/null || fail "PostgreSQL dump verification failed"

docker run --rm \
    -v "$source_letsencrypt:/source/letsencrypt:ro" \
    -v "$source_certbot_webroot:/source/certbot-webroot:ro" \
    -v "$backup_dir:/backup" \
    alpine:3.20 tar -czf /backup/certificates.tar.gz -C /source letsencrypt certbot-webroot || \
    fail "certificate backup failed"
[ -s "$backup_dir/certificates.tar.gz" ] || fail "certificate backup is empty"
docker run --rm -v "$backup_dir:/backup:ro" alpine:3.20 \
    tar -tzf /backup/certificates.tar.gz >/dev/null || fail "certificate backup verification failed"
certificate_entries="$(docker run --rm -v "$backup_dir:/backup:ro" alpine:3.20 tar -tzf /backup/certificates.tar.gz)" || fail "certificate backup verification failed"
printf '%s\n' "$certificate_entries" | grep -Fx 'letsencrypt/live/photo-prjct/fullchain.pem' >/dev/null || fail "certificate backup is missing fullchain.pem"
printf '%s\n' "$certificate_entries" | grep -Fx 'letsencrypt/live/photo-prjct/privkey.pem' >/dev/null || fail "certificate backup is missing privkey.pem"
docker run --rm -v "$backup_dir:/backup:ro" alpine:3.20 sh -c 'mkdir /verify && tar -xzf /backup/certificates.tar.gz -C /verify && test -r /verify/letsencrypt/live/photo-prjct/fullchain.pem && test -s /verify/letsencrypt/live/photo-prjct/fullchain.pem && test -r /verify/letsencrypt/live/photo-prjct/privkey.pem && test -s /verify/letsencrypt/live/photo-prjct/privkey.pem' || fail "certificate backup contains missing or broken required files"

mutation_started=1
compose "$source_project" stop nginx certbot || fail "could not stop source certificate edge"

docker volume create --label "com.docker.compose.project=$destination_project" \
    --label "com.docker.compose.volume=pgdata" "$destination_pgdata" >/dev/null
docker volume create --label "com.docker.compose.project=$destination_project" \
    --label "com.docker.compose.volume=letsencrypt" "$destination_letsencrypt" >/dev/null
docker volume create --label "com.docker.compose.project=$destination_project" \
    --label "com.docker.compose.volume=certbot-webroot" "$destination_certbot_webroot" >/dev/null

compose "$destination_project" up -d db || fail "canonical database initialization failed"
compose "$destination_project" exec -T db \
    pg_restore --clean --if-exists --no-owner --no-privileges --username "$database_user" --dbname "$database_name" \
    < "$backup_dir/postgresql.dump" || fail "PostgreSQL restore failed"
docker run --rm \
    -v "$destination_letsencrypt:/destination/letsencrypt" \
    -v "$destination_certbot_webroot:/destination/certbot-webroot" \
    -v "$backup_dir:/backup:ro" \
    alpine:3.20 tar -xzf /backup/certificates.tar.gz -C /destination || fail "certificate restore failed"

database_row_counts "$source_project" "$backup_dir/source-row-counts.txt"
database_row_counts "$destination_project" "$backup_dir/destination-row-counts.txt"
cmp -s "$backup_dir/source-row-counts.txt" "$backup_dir/destination-row-counts.txt" || \
    fail "restored database row counts do not match source"
compose "$destination_project" run --rm --no-deps -T \
    --entrypoint python web manage.py migrate --check || fail "restored database has pending migrations"
docker run --rm -v "$destination_letsencrypt:/etc/letsencrypt:ro" \
    certbot/certbot:v2.11.0 certificates >/dev/null || fail "restored certificate verification failed"
docker run --rm -v "$destination_letsencrypt:/etc/letsencrypt:ro" alpine:3.20 \
    sh -c 'test -s /etc/letsencrypt/live/photo-prjct/fullchain.pem && test -s /etc/letsencrypt/live/photo-prjct/privkey.pem' || fail "restored certificate files are missing"

COMPOSE_PROJECT_NAME="$destination_project" sh "$DEPLOY_ROOT/deploy/apply-deployment.sh" || fail "generic deployment failed"
mutation_started=0
trap - EXIT HUP INT TERM
printf '%s\n' "Cutover complete: source volumes remain rollback authority."
