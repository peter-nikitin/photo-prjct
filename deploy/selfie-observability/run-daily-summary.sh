#!/bin/sh

set -eu

DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/photo-prjct}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
SUMMARIZER="$SCRIPT_DIR/summarize.py"
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
window_bounds=$(
    "$PYTHON_BIN" -c \
        'import sys; from datetime import date, datetime, time, timedelta, timezone; from zoneinfo import ZoneInfo; report_date=date.fromisoformat(sys.argv[1]); timezone_moscow=ZoneInfo("Europe/Moscow"); print(" ".join(datetime.combine(report_date + timedelta(days=offset), time.min, tzinfo=timezone_moscow).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") for offset in (0, 1)))' \
        "$report_date"
)
window_start=${window_bounds% *}
window_end=${window_bounds#* }

journal_input=$(mktemp)
trap 'rm -f "$journal_input"' EXIT HUP INT TERM
journalctl \
    --since "$window_start" \
    --until "$window_end" \
    --output=cat \
    "CONTAINER_TAG=findme.service=web findme.environment=$DEPLOYMENT_TARGET" + \
    "CONTAINER_TAG=findme.service=worker findme.environment=$DEPLOYMENT_TARGET" + \
    "CONTAINER_TAG=findme.service=nginx findme.environment=$DEPLOYMENT_TARGET" \
    > "$journal_input"

if [ "$recomputed" = True ]; then
    "$PYTHON_BIN" "$SUMMARIZER" \
        --date "$report_date" --recomputed < "$journal_input"
else
    "$PYTHON_BIN" "$SUMMARIZER" \
        --date "$report_date" < "$journal_input"
fi
