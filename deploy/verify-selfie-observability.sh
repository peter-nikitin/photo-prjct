#!/bin/sh
set -eu

: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"
: "${COMPOSE_PROJECT_NAME:?Set COMPOSE_PROJECT_NAME}"
OBSERVABILITY_HELPER=/usr/local/sbin/findme-selfie-observability

for command in docker python3 sed sudo; do
    command -v "$command" >/dev/null 2>&1 || { echo "missing observability dependency: $command" >&2; exit 1; }
done

compose() { APP_ENV_FILE="$DEPLOY_ROOT/.env" docker compose --project-name "$COMPOSE_PROJECT_NAME" --env-file "$DEPLOY_ROOT/.env" -f "$DEPLOY_ROOT/docker-compose.deployment.yml" -f "$DEPLOY_ROOT/docker-compose.https.yml" "$@"; }
processing_enabled="$(sed -n 's/^PHOTO_PROCESSING_ENABLED=//p' "$DEPLOY_ROOT/.env" | head -n 1)"
for service in web nginx worker; do
    containers="$(compose ps -q "$service")"
    if [ -z "$containers" ]; then
        [ "$service" = worker ] && [ "$processing_enabled" != True ] && continue
        echo "observability container is unavailable: $service" >&2
        exit 1
    fi
    for container in $containers; do
        actual="$(docker inspect --format '{{.HostConfig.LogConfig.Type}}|{{index .HostConfig.LogConfig.Config "tag"}}' "$container")"
        [ "$actual" = "journald|findme.service=$service" ] || { echo "container logging contract mismatch: $service" >&2; exit 1; }
    done
done
probe_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
probe_code="import logging; from selfie_search.observability import SelfieEventName, emit_selfie_event; emit_selfie_event(logging.getLogger('selfie_search'), event=SelfieEventName.OBSERVABILITY_PROBE, probe_id='$probe_id')"
compose exec -T web sh -c 'python manage.py shell --no-imports -c "$1" 2>/proc/1/fd/2' sh "$probe_code"
sudo -n "$OBSERVABILITY_HELPER" verify-probe "$probe_id" || { echo "emitted observability probe is unreadable" >&2; exit 1; }
echo SELFIE_OBSERVABILITY_VERIFIED
