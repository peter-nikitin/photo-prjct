#!/bin/sh
set -eu

: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"
: "${COMPOSE_PROJECT_NAME:?Set COMPOSE_PROJECT_NAME}"
: "${DEPLOYMENT_TARGET:?Set DEPLOYMENT_TARGET}"
JOURNAL_DIR="${SELFIE_OBSERVABILITY_JOURNAL_DIR:-/var/log/journal}"
EXPECTED_STORAGE="Storage=persistent"
EXPECTED_RETENTION="MaxRetentionSec=14day"
EXPECTED_CAP="SystemMaxUse=1G"

for command in docker grep journalctl python3 sed systemctl systemd-analyze tail; do
    command -v "$command" >/dev/null 2>&1 || { echo "missing observability dependency: $command" >&2; exit 1; }
done

[ -d "$JOURNAL_DIR" ] || { echo "persistent journal directory is unavailable" >&2; exit 1; }
effective="$(systemd-analyze cat-config systemd/journald.conf)"
effective_value() {
    printf '%s\n' "$effective" | sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" | tail -n 1
}
[ "Storage=$(effective_value Storage)" = "$EXPECTED_STORAGE" ] || { echo "effective journald policy mismatch" >&2; exit 1; }
[ "MaxRetentionSec=$(effective_value MaxRetentionSec)" = "$EXPECTED_RETENTION" ] || { echo "effective journald policy mismatch" >&2; exit 1; }
[ "SystemMaxUse=$(effective_value SystemMaxUse)" = "$EXPECTED_CAP" ] || { echo "effective journald policy mismatch" >&2; exit 1; }
systemctl is-enabled --quiet selfie-search-summary.timer || {
    echo "selfie summary timer is not enabled" >&2
    exit 1
}
systemctl is-active --quiet selfie-search-summary.timer || {
    echo "selfie summary timer is not active" >&2
    exit 1
}
disk_line="$(journalctl --disk-usage 2>/dev/null)" || { echo "journal disk usage is unavailable" >&2; exit 1; }
disk_usage="$(printf '%s\n' "$disk_line" | sed -n 's/.*take[s]* up \([^ ]*\).*/\1/p' | tail -n 1)"
[ -n "$disk_usage" ] || { echo "journal disk usage is unreadable" >&2; exit 1; }
printf 'journal_disk_usage=%s\n' "$disk_usage"
oldest_line="$(journalctl -u docker.service --since '14 days ago' -o short-unix --grep '"event":"selfie_' 2>/dev/null | sed -n '1p')"
if [ -z "$oldest_line" ]; then
    echo oldest_selfie_event_realtime=none
else
    oldest_timestamp="${oldest_line%% *}"
    case "$oldest_timestamp" in *[!0-9.]*|'') echo "oldest selfie event timestamp is unreadable" >&2; exit 1 ;; esac
    printf 'oldest_selfie_event_realtime=%s\n' "$oldest_timestamp"
fi

compose() { APP_ENV_FILE="$DEPLOY_ROOT/.env" docker compose --project-name "$COMPOSE_PROJECT_NAME" --env-file "$DEPLOY_ROOT/.env" -f "$DEPLOY_ROOT/docker-compose.prod.yml" -f "$DEPLOY_ROOT/docker-compose.https.yml" "$@"; }
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
        [ "$actual" = "journald|findme.service=$service findme.environment=$DEPLOYMENT_TARGET" ] || { echo "container logging contract mismatch: $service" >&2; exit 1; }
    done
done
probe_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
compose exec -T web python manage.py shell --no-imports -c "import logging; from selfie_search.observability import SelfieEventName, emit_selfie_event; emit_selfie_event(logging.getLogger('selfie_search'), event=SelfieEventName.OBSERVABILITY_PROBE, probe_id='$probe_id')"
journalctl --since '2 minutes ago' CONTAINER_TAG="findme.service=web findme.environment=$DEPLOYMENT_TARGET" -o cat | grep -Fq "\"probe_id\":\"$probe_id\"" || { echo "emitted observability probe is unreadable" >&2; exit 1; }
echo SELFIE_OBSERVABILITY_VERIFIED
