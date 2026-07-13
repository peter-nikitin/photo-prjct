#!/bin/sh

set -eu

: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"
: "${COMPOSE_PROJECT_NAME:?Set COMPOSE_PROJECT_NAME}"
: "${PUBLIC_DOMAIN:?Set PUBLIC_DOMAIN}"

[ "$#" -eq 2 ] || {
    echo "Usage: $0 EXPECTED_CANDIDATE http|https" >&2
    exit 2
}

expected_candidate="$1"
edge_mode="$2"

case "$edge_mode" in
    http)
        overlay_file="$DEPLOY_ROOT/docker-compose.staging.yml"
        health_port=80
        health_url="http://$PUBLIC_DOMAIN/health/"
        ;;
    https)
        overlay_file="$DEPLOY_ROOT/docker-compose.https.yml"
        health_port=443
        health_url="https://$PUBLIC_DOMAIN/health/"
        ;;
    *)
        echo "Rollback edge mode must be http or https" >&2
        exit 2
        ;;
esac

candidate_file="$DEPLOY_ROOT/candidate-image"
previous_file="$DEPLOY_ROOT/previous-image"
if [ ! -f "$candidate_file" ] || [ "$(cat "$candidate_file")" != "$expected_candidate" ]; then
    echo "Rollback candidate does not match the expected image" >&2
    exit 1
fi
if [ ! -f "$previous_file" ]; then
    echo "Rollback requires a previous successful image" >&2
    exit 1
fi
previous_image="$(cat "$previous_file")"

compose() {
    docker compose --project-name "$COMPOSE_PROJECT_NAME" \
        --env-file "$DEPLOY_ROOT/.env" \
        -f "$DEPLOY_ROOT/docker-compose.prod.yml" \
        -f "$overlay_file" "$@"
}

diagnostics() {
    compose ps || true
    compose logs --tail=200 web nginx || true
}

env_tmp="$(mktemp "$DEPLOY_ROOT/.env.rollback.XXXXXX")"
marker_tmp=""
trap 'rm -f "$env_tmp" ${marker_tmp:+"$marker_tmp"}' EXIT
awk -v image="$previous_image" '
    BEGIN { replaced = 0 }
    /^APP_IMAGE=/ && !replaced { print "APP_IMAGE=" image; replaced = 1; next }
    { print }
    END { if (!replaced) print "APP_IMAGE=" image }
' "$DEPLOY_ROOT/.env" > "$env_tmp"
chmod 600 "$env_tmp"
mv "$env_tmp" "$DEPLOY_ROOT/.env"
unset APP_IMAGE

if ! compose up -d --remove-orphans; then
    echo "Rollback Compose reconciliation failed" >&2
    diagnostics
    exit 1
fi

attempt=1
max_attempts=12
while [ "$attempt" -le "$max_attempts" ]; do
    web_container="$(compose ps -q web)"
    running_image=""
    if [ -n "$web_container" ]; then
        running_image="$(docker inspect --format '{{.Config.Image}}' "$web_container" 2>/dev/null || true)"
    fi
    if [ "$running_image" = "$previous_image" ] && \
        curl --fail-with-body --silent --show-error --max-time 15 \
            --resolve "$PUBLIC_DOMAIN:$health_port:127.0.0.1" "$health_url"; then
        marker_tmp="$(mktemp "$DEPLOY_ROOT/.deployed-image.XXXXXX")"
        printf '%s\n' "$previous_image" > "$marker_tmp"
        mv "$marker_tmp" "$DEPLOY_ROOT/deployed-image"
        marker_tmp=""
        rm -f "$DEPLOY_ROOT/candidate-image" "$DEPLOY_ROOT/previous-image"
        exit 0
    fi
    if [ "$attempt" -lt "$max_attempts" ]; then
        attempt=$((attempt + 1))
        sleep 5
        continue
    fi
    echo "Rollback health check failed after $max_attempts attempts" >&2
    diagnostics
    exit 1
done
