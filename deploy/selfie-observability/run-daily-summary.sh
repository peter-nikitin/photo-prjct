#!/bin/sh

set -eu

DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/photo-prjct}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
if [ -z "${DEPLOYMENT_TARGET:-}" ]; then
    DEPLOYMENT_TARGET=$(sed -n '1p' "$DEPLOY_ROOT/deployment-target")
fi
case "$DEPLOYMENT_TARGET" in
    staging|production) ;;
    *)
        echo "selfie summary requires a valid deployment target" >&2
        exit 2
        ;;
esac

if [ "$#" -gt 1 ]; then
    echo "usage: run-daily-summary.sh [YYYY-MM-DD]" >&2
    exit 2
fi
if [ "$#" -eq 1 ]; then
    report_date="$1"
    recomputed=True
else
    report_date=$(
        "$PYTHON_BIN" -c \
            'from datetime import datetime, timedelta; from zoneinfo import ZoneInfo; print((datetime.now(ZoneInfo("Europe/Moscow")).date() - timedelta(days=1)).isoformat())'
    )
    recomputed=False
fi
next_date=$(
    "$PYTHON_BIN" -c \
        'from datetime import date, timedelta; import sys; print((date.fromisoformat(sys.argv[1]) + timedelta(days=1)).isoformat())' \
        "$report_date"
)

journal_input=$(mktemp)
trap 'rm -f "$journal_input"' EXIT HUP INT TERM
journalctl \
    --since "$report_date 00:00:00 Europe/Moscow" \
    --until "$next_date 00:00:00 Europe/Moscow" \
    --output=cat \
    "CONTAINER_TAG=findme.service=web findme.environment=$DEPLOYMENT_TARGET" + \
    "CONTAINER_TAG=findme.service=worker findme.environment=$DEPLOYMENT_TARGET" + \
    "CONTAINER_TAG=findme.service=nginx findme.environment=$DEPLOYMENT_TARGET" \
    > "$journal_input"

if [ "$recomputed" = True ]; then
    "$PYTHON_BIN" "$DEPLOY_ROOT/deploy/selfie-observability/summarize.py" \
        --date "$report_date" --recomputed < "$journal_input"
else
    "$PYTHON_BIN" "$DEPLOY_ROOT/deploy/selfie-observability/summarize.py" \
        --date "$report_date" < "$journal_input"
fi
