#!/bin/sh

set -eu

: "${DEPLOYMENT_TARGET:?Set DEPLOYMENT_TARGET to staging or production}"
: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"
: "${COMPOSE_PROJECT_NAME:?Set COMPOSE_PROJECT_NAME}"
: "${APP_IMAGE:?Set APP_IMAGE}"
: "${SECRET_KEY:?Set SECRET_KEY}"
: "${DEBUG:?Set DEBUG}"
: "${ALLOWED_HOSTS:?Set ALLOWED_HOSTS}"
: "${DB_NAME:?Set DB_NAME}"
: "${DB_USER:?Set DB_USER}"
: "${DB_PASSWORD:?Set DB_PASSWORD}"
: "${PUBLIC_DOMAIN:?Set PUBLIC_DOMAIN}"
PUBLIC_DOMAIN_ALIAS="${PUBLIC_DOMAIN_ALIAS:-}"
requested_image="$APP_IMAGE"
requested_processing_enabled="${PHOTO_PROCESSING_ENABLED:-False}"

case "${PHOTO_UPLOAD_ENABLED:-False}" in
    True)
        command -v crontab >/dev/null 2>&1 || {
            echo "crontab is required when photographer uploads are enabled" >&2
            exit 1
        }
        command -v flock >/dev/null 2>&1 || {
            echo "flock is required when photographer uploads are enabled" >&2
            exit 1
        }
        : "${PRIVATE_MEDIA_S3_BUCKET:?Set PRIVATE_MEDIA_S3_BUCKET}"
        : "${PRIVATE_MEDIA_S3_ACCESS_KEY_ID:?Set PRIVATE_MEDIA_S3_ACCESS_KEY_ID}"
        : "${PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY:?Set PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY}"
        : "${PRIVATE_MEDIA_ALLOWED_ORIGINS:?Set PRIVATE_MEDIA_ALLOWED_ORIGINS}"
        ;;
    False)
        ;;
    *)
        echo "PHOTO_UPLOAD_ENABLED must be True or False" >&2
        exit 2
        ;;
esac

case "$DEPLOYMENT_TARGET" in
    staging|production)
        ;;
    *)
        echo "DEPLOYMENT_TARGET must be staging or production" >&2
        exit 2
        ;;
esac

case "$requested_processing_enabled" in
    True)
        if [ -z "${WORKER_IMAGE:-}" ]; then
            echo "Set WORKER_IMAGE" >&2
            exit 2
        fi
        if [ -z "${PHOTO_PROCESSING_WORKER_TOKEN:-}" ]; then
            echo "Set PHOTO_PROCESSING_WORKER_TOKEN" >&2
            exit 2
        fi
        ;;
    False)
        ;;
    *)
        echo "PHOTO_PROCESSING_ENABLED must be True or False" >&2
        exit 2
        ;;
esac

: "${LETSENCRYPT_EMAIL:?Set LETSENCRYPT_EMAIL}"
overlay_file="$DEPLOY_ROOT/docker-compose.https.yml"
health_port=443
health_url="https://$PUBLIC_DOMAIN/health/"

compose_with_env_file() {
    compose_env_file="$1"
    shift
    APP_ENV_FILE="$compose_env_file" \
    docker compose --project-name "$COMPOSE_PROJECT_NAME" \
        --env-file "$compose_env_file" \
        -f "$DEPLOY_ROOT/docker-compose.prod.yml" \
        -f "$overlay_file" "$@"
}

compose() {
    compose_with_env_file "$DEPLOY_ROOT/.env" "$@"
}

compose_with_processing_profile() {
    processing_enabled="$1"
    compose_env_file="$2"
    shift 2

    if [ "$processing_enabled" = True ]; then
        compose_with_env_file "$compose_env_file" --profile worker "$@"
    else
        compose_with_env_file "$compose_env_file" "$@"
    fi
}

compose_with_requested_processing_profile() {
    compose_with_processing_profile "$requested_processing_enabled" "$DEPLOY_ROOT/.env" "$@"
}

diagnostics() {
    compose ps || true
    compose logs --tail=100 web nginx || true
}

requested_env_tmp=""
recovery_env_tmp=""
previous_env_tmp=""
previous_deployment_target_tmp=""
previous_compose_project_name_tmp=""
marker_tmp=""
mutation_started=0
deployment_committed=0
recovery_in_progress=0

cleanup() {
    rm -f \
        ${requested_env_tmp:+"$requested_env_tmp"} \
        ${recovery_env_tmp:+"$recovery_env_tmp"} \
        ${previous_env_tmp:+"$previous_env_tmp"} \
        ${previous_deployment_target_tmp:+"$previous_deployment_target_tmp"} \
        ${previous_compose_project_name_tmp:+"$previous_compose_project_name_tmp"} \
        ${marker_tmp:+"$marker_tmp"}
}

restore_previous_deployment_markers() {
    if [ "$previous_deployment_target_exists" -eq 1 ]; then
        mv "$previous_deployment_target_tmp" "$DEPLOY_ROOT/deployment-target" || return 1
        previous_deployment_target_tmp=""
    else
        rm -f "$DEPLOY_ROOT/deployment-target" || return 1
    fi

    if [ "$previous_compose_project_name_exists" -eq 1 ]; then
        mv "$previous_compose_project_name_tmp" "$DEPLOY_ROOT/compose-project-name" || return 1
        previous_compose_project_name_tmp=""
    else
        rm -f "$DEPLOY_ROOT/compose-project-name" || return 1
    fi
}

clear_candidate_compose_interpolation() {
    unset \
        APP_IMAGE \
        SECRET_KEY \
        DEBUG \
        ALLOWED_HOSTS \
        DB_NAME \
        DB_USER \
        DB_PASSWORD \
        DB_HOST \
        DB_PORT \
        PUBLIC_DOMAIN \
        PUBLIC_DOMAIN_ALIAS \
        LETSENCRYPT_EMAIL \
        MEDIA_STORAGE_BACKEND \
        MEDIA_S3_ENDPOINT_URL \
        MEDIA_S3_REGION \
        MEDIA_S3_PUBLIC_BUCKET \
        MEDIA_S3_ACCESS_KEY_ID \
        MEDIA_S3_SECRET_ACCESS_KEY \
        PHOTO_UPLOAD_ENABLED \
        PRIVATE_MEDIA_S3_BUCKET \
        PRIVATE_MEDIA_S3_ACCESS_KEY_ID \
        PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY \
        PRIVATE_MEDIA_ALLOWED_ORIGINS \
        WORKER_IMAGE \
        PHOTO_PROCESSING_ENABLED \
        PHOTO_PROCESSING_WORKER_TOKEN \
        PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS \
        PHOTO_PROCESSING_MAX_REQUEST_BYTES \
        PHOTO_WORKER_BUILD \
        PHOTO_WORKER_LEASE_SECONDS
}

recover_previous_deployment() {
    restore_previous_deployment_markers || return 1

    if [ "$previous_env_exists" -eq 0 ]; then
        recovery_env_tmp="$(mktemp "$DEPLOY_ROOT/.env.recovery.XXXXXX")" || return 1
        cp "$DEPLOY_ROOT/.env" "$recovery_env_tmp" || return 1
        if ! compose_with_processing_profile "$requested_processing_enabled" "$recovery_env_tmp" \
            down --remove-orphans; then
            return 1
        fi
        rm -f "$DEPLOY_ROOT/.env"
        echo "No previous deployment environment was present; restored no-env state" >&2
        return 0
    fi

    [ -n "$previous_env_tmp" ] || return 1
    mv "$previous_env_tmp" "$DEPLOY_ROOT/.env" || return 1
    previous_env_tmp=""
    clear_candidate_compose_interpolation
    compose_with_processing_profile "$previous_processing_enabled" "$DEPLOY_ROOT/.env" \
        up -d --remove-orphans || return 1
    echo "Previous application and worker profile reconciled" >&2
}

on_exit() {
    status=$?
    set +e
    trap - EXIT INT TERM HUP

    if [ "$mutation_started" -eq 1 ] && [ "$deployment_committed" -eq 0 ]; then
        [ "$status" -ne 0 ] || status=1
        if [ "$recovery_in_progress" -eq 0 ]; then
            recovery_in_progress=1
            if ! recover_previous_deployment; then
                echo "Previous deployment recovery failed" >&2
                diagnostics
            elif [ "${previous_upload_enabled:-False}" = True ]; then
                sh "$DEPLOY_ROOT/deploy/install-upload-cleanup-cron.sh" install || true
            else
                sh "$DEPLOY_ROOT/deploy/install-upload-cleanup-cron.sh" remove || true
            fi
        fi
    fi

    cleanup
    exit "$status"
}

trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

fail() {
    echo "$1" >&2
    exit 1
}

install -d -m 0755 "$DEPLOY_ROOT"
previous_upload_enabled="False"
previous_processing_enabled="False"
previous_env_exists=0
previous_deployment_target_exists=0
previous_compose_project_name_exists=0
has_successful_deployment=0
if [ -f "$DEPLOY_ROOT/.env" ]; then
    previous_env_exists=1
    previous_env_tmp="$(mktemp "$DEPLOY_ROOT/.env.previous.XXXXXX")" || fail "Could not snapshot previous deployment environment"
    cp -p "$DEPLOY_ROOT/.env" "$previous_env_tmp" || fail "Could not snapshot previous deployment environment"
    previous_upload_enabled="$(
        sed -n 's/^PHOTO_UPLOAD_ENABLED=//p' "$DEPLOY_ROOT/.env" | head -n 1
    )"
    previous_processing_enabled="$(
        sed -n 's/^PHOTO_PROCESSING_ENABLED=//p' "$DEPLOY_ROOT/.env" | head -n 1
    )"
    case "$previous_processing_enabled" in
        True|False)
            ;;
        *)
            previous_processing_enabled="False"
            ;;
    esac
fi
if [ -f "$DEPLOY_ROOT/deployment-target" ]; then
    previous_deployment_target_exists=1
    previous_deployment_target_tmp="$(mktemp "$DEPLOY_ROOT/.deployment-target.previous.XXXXXX")" || fail "Could not snapshot deployment target marker"
    cp -p "$DEPLOY_ROOT/deployment-target" "$previous_deployment_target_tmp" || fail "Could not snapshot deployment target marker"
fi
if [ -f "$DEPLOY_ROOT/compose-project-name" ]; then
    previous_compose_project_name_exists=1
    previous_compose_project_name_tmp="$(mktemp "$DEPLOY_ROOT/.compose-project-name.previous.XXXXXX")" || fail "Could not snapshot compose project marker"
    cp -p "$DEPLOY_ROOT/compose-project-name" "$previous_compose_project_name_tmp" || fail "Could not snapshot compose project marker"
fi
if [ -f "$DEPLOY_ROOT/deployed-image" ]; then
    has_successful_deployment=1
fi

ALLOWED_HOSTS="${ALLOWED_HOSTS:+$ALLOWED_HOSTS,}web,$PUBLIC_DOMAIN"
if [ -n "$PUBLIC_DOMAIN_ALIAS" ]; then
    ALLOWED_HOSTS="$ALLOWED_HOSTS,$PUBLIC_DOMAIN_ALIAS"
fi

umask 077
requested_env_tmp="$(mktemp "$DEPLOY_ROOT/.env.requested.XXXXXX")"
{
    printf 'APP_IMAGE=%s\n' "$requested_image"
    printf 'SECRET_KEY=%s\n' "$SECRET_KEY"
    printf 'DEBUG=%s\n' "$DEBUG"
    printf 'ALLOWED_HOSTS=%s\n' "$ALLOWED_HOSTS"
    printf 'DB_NAME=%s\n' "$DB_NAME"
    printf 'DB_USER=%s\n' "$DB_USER"
    printf 'DB_PASSWORD=%s\n' "$DB_PASSWORD"
    printf 'DB_HOST=db\n'
    printf 'DB_PORT=5432\n'
    printf 'PUBLIC_DOMAIN=%s\n' "$PUBLIC_DOMAIN"
    printf 'PUBLIC_DOMAIN_ALIAS=%s\n' "$PUBLIC_DOMAIN_ALIAS"
    printf 'MEDIA_STORAGE_BACKEND=%s\n' "${MEDIA_STORAGE_BACKEND:-filesystem}"
    printf 'MEDIA_S3_ENDPOINT_URL=%s\n' "${MEDIA_S3_ENDPOINT_URL:-https://storage.yandexcloud.net}"
    printf 'MEDIA_S3_REGION=%s\n' "${MEDIA_S3_REGION:-ru-central1}"
    printf 'MEDIA_S3_PUBLIC_BUCKET=%s\n' "${MEDIA_S3_PUBLIC_BUCKET:-}"
    printf 'MEDIA_S3_ACCESS_KEY_ID=%s\n' "${MEDIA_S3_ACCESS_KEY_ID:-}"
    printf 'MEDIA_S3_SECRET_ACCESS_KEY=%s\n' "${MEDIA_S3_SECRET_ACCESS_KEY:-}"
    printf 'PHOTO_UPLOAD_ENABLED=%s\n' "${PHOTO_UPLOAD_ENABLED:-False}"
    printf 'PRIVATE_MEDIA_S3_BUCKET=%s\n' "${PRIVATE_MEDIA_S3_BUCKET:-}"
    printf 'PRIVATE_MEDIA_S3_ACCESS_KEY_ID=%s\n' "${PRIVATE_MEDIA_S3_ACCESS_KEY_ID:-}"
    printf 'PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY=%s\n' "${PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY:-}"
    printf 'PRIVATE_MEDIA_ALLOWED_ORIGINS=%s\n' "${PRIVATE_MEDIA_ALLOWED_ORIGINS:-}"
    printf 'WORKER_IMAGE=%s\n' "${WORKER_IMAGE:-}"
    printf 'PHOTO_PROCESSING_ENABLED=%s\n' "$requested_processing_enabled"
    printf 'PHOTO_PROCESSING_WORKER_TOKEN=%s\n' "${PHOTO_PROCESSING_WORKER_TOKEN:-}"
    printf 'PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS=%s\n' "${PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS:-120}"
    printf 'PHOTO_PROCESSING_MAX_REQUEST_BYTES=%s\n' "${PHOTO_PROCESSING_MAX_REQUEST_BYTES:-16384}"
    printf 'PHOTO_WORKER_BUILD=%s\n' "${PHOTO_WORKER_BUILD:-capture-metadata-v1}"
    printf 'PHOTO_WORKER_LEASE_SECONDS=%s\n' "${PHOTO_WORKER_LEASE_SECONDS:-120}"
} > "$requested_env_tmp"
chmod 600 "$requested_env_tmp"

if [ -n "${GHCR_READ_TOKEN:-}" ]; then
    if ! printf '%s\n' "$GHCR_READ_TOKEN" | \
        docker login ghcr.io -u "${GHCR_USERNAME:?Set GHCR_USERNAME}" --password-stdin; then
        fail "Container registry login failed"
    fi
fi

if [ "$requested_processing_enabled" = True ]; then
    if ! compose_with_env_file "$requested_env_tmp" --profile worker pull web worker; then
        fail "Candidate application image pull failed"
    fi
elif ! compose_with_env_file "$requested_env_tmp" pull web; then
    fail "Candidate application image pull failed"
fi

gallery_media_preflight='
from contextlib import closing
from ingestion.storage import PrivateUploadStorage
from picflow.models import Event, Photo
try:
    photo = Photo.objects.filter(
        event__publication_status=Event.PublicationStatus.PUBLISHED,
        event__access_type=Event.AccessType.FREE,
        src="",
        original_key__isnull=False,
    ).order_by("id").first()
except Exception:
    raise SystemExit("Gallery private-media read prerequisite failed") from None
if photo is None:
    print("gallery-private-media-preflight-skipped:no-eligible-photo")
else:
    try:
        opened = PrivateUploadStorage().open_final(key=photo.original_key)
        with closing(opened.body) as body:
            if not body.read(1):
                raise RuntimeError
    except Exception:
        raise SystemExit("Gallery private-media read prerequisite failed") from None
    print("gallery-private-media-preflight-ok")
'
if [ "$has_successful_deployment" -eq 0 ]; then
    echo "gallery-private-media-preflight-skipped:no-existing-deployment"
else
    if ! compose_with_env_file "$requested_env_tmp" run --rm --no-deps -T \
        --entrypoint python web manage.py shell --no-imports -c "$gallery_media_preflight"; then
        fail "Candidate image failed private-media read prerequisite"
    fi
fi

mutation_started=1
mv "$requested_env_tmp" "$DEPLOY_ROOT/.env"
requested_env_tmp=""

compose stop nginx || true
if ! sh "$DEPLOY_ROOT/deploy/certbot/reconcile-certificate.sh"; then
    fail "Certificate bootstrap failed"
fi

if ! compose_with_requested_processing_profile pull; then
    fail "Deployment image pull failed"
fi

compose_up_status=0
compose_with_requested_processing_profile up -d --remove-orphans || compose_up_status=$?
echo "docker compose up exit status: $compose_up_status" >&2
if [ "$compose_up_status" -ne 0 ]; then
    fail "Deployment Compose reconciliation failed"
fi

attempt=1
max_attempts=12
while [ "$attempt" -le "$max_attempts" ]; do
    web_container="$(compose ps -q web)"
    running_image=""
    if [ -n "$web_container" ]; then
        running_image="$(
            docker inspect --format '{{.Config.Image}}' "$web_container" 2>/dev/null || true
        )"
    fi
    if [ "$running_image" = "$requested_image" ] && \
        curl --fail-with-body --silent --show-error --max-time 15 \
            --resolve "$PUBLIC_DOMAIN:$health_port:127.0.0.1" "$health_url"; then
        break
    fi
    if [ "$attempt" -eq "$max_attempts" ]; then
        fail "Requested deployment failed local health verification"
    fi
    echo "Deployment health check attempt $attempt failed; retrying" >&2
    attempt=$((attempt + 1))
    sleep 5
done

if ! sh "$DEPLOY_ROOT/deploy/verify-public-edge.sh"; then
    fail "Requested deployment failed public HTTPS smoke verification"
fi

marker_tmp="$(mktemp "$DEPLOY_ROOT/.deployment-target.XXXXXX")"
printf '%s\n' "$DEPLOYMENT_TARGET" > "$marker_tmp"
mv "$marker_tmp" "$DEPLOY_ROOT/deployment-target"
marker_tmp=""

marker_tmp="$(mktemp "$DEPLOY_ROOT/.compose-project-name.XXXXXX")"
printf '%s\n' "$COMPOSE_PROJECT_NAME" > "$marker_tmp"
mv "$marker_tmp" "$DEPLOY_ROOT/compose-project-name"
marker_tmp=""

if [ "${PHOTO_UPLOAD_ENABLED:-False}" = True ]; then
    sh "$DEPLOY_ROOT/deploy/install-upload-cleanup-cron.sh" install
else
    sh "$DEPLOY_ROOT/deploy/install-upload-cleanup-cron.sh" remove
fi

marker_tmp="$(mktemp "$DEPLOY_ROOT/.deployed-image.XXXXXX")"
printf '%s\n' "$requested_image" > "$marker_tmp"
mv "$marker_tmp" "$DEPLOY_ROOT/deployed-image"
marker_tmp=""
deployment_committed=1
