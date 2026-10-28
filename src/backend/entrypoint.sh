#!/bin/sh
set -eu

# Compose permits a sparse DB-only environment; the web container does not.
for name in SECRET_KEY DEBUG ALLOWED_HOSTS DB_NAME DB_USER DB_PASSWORD \
    GUNICORN_WORKERS GUNICORN_THREADS GUNICORN_TIMEOUT GUNICORN_MAX_REQUESTS \
    GUNICORN_MAX_REQUESTS_JITTER PUBLIC_DOMAIN GALLERY_CDN_ORIGIN \
    GALLERY_CDN_TOKEN_SECRET GALLERY_IMGPROXY_KEY GALLERY_IMGPROXY_SALT \
    MEDIA_S3_PUBLIC_BUCKET MEDIA_S3_ACCESS_KEY_ID MEDIA_S3_SECRET_ACCESS_KEY \
    PRIVATE_MEDIA_S3_BUCKET PRIVATE_MEDIA_S3_ACCESS_KEY_ID \
    PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY; do
    eval "value=\${$name-}"
    if [ -z "$value" ]; then
        echo "$name is required before web startup" >&2
        exit 2
    fi
done
if [ "$DEBUG" != False ] || [ "${MEDIA_STORAGE_BACKEND:-}" != s3 ] ||
    [ "${PHOTO_PROCESSING_ENABLED:-}" != True ] ||
    [ "${PHOTO_PROCESSING_FACE_ENABLED:-}" != True ]; then
    echo 'Production web requires DEBUG=False, S3 media and enabled photo/face processing' >&2
    exit 2
fi

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
