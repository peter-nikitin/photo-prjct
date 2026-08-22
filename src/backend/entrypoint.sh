#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py sync_feature_flags
python manage.py bootstrap_photographer_group
python manage.py collectstatic --noinput

metrics_dir=/tmp/prometheus_multiproc
if rm -rf "$metrics_dir" && mkdir -p "$metrics_dir"; then
    export PROMETHEUS_MULTIPROC_DIR="$metrics_dir"
else
    echo "Prometheus multiprocess metrics are unavailable" >&2
    unset PROMETHEUS_MULTIPROC_DIR
fi

exec gunicorn config.wsgi:application --config python:config.gunicorn --bind 0.0.0.0:8000 \
    --workers "$GUNICORN_WORKERS" \
    --threads "$GUNICORN_THREADS" \
    --timeout "$GUNICORN_TIMEOUT" \
    --max-requests "$GUNICORN_MAX_REQUESTS" \
    --max-requests-jitter "$GUNICORN_MAX_REQUESTS_JITTER"
