#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py bootstrap_photographer_group
python manage.py collectstatic --noinput

exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 \
    --workers "$GUNICORN_WORKERS" \
    --threads "$GUNICORN_THREADS" \
    --timeout "$GUNICORN_TIMEOUT" \
    --max-requests "$GUNICORN_MAX_REQUESTS" \
    --max-requests-jitter "$GUNICORN_MAX_REQUESTS_JITTER"
