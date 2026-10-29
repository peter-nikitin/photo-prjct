#!/bin/sh
set -eu

# v4.0.12 silently ignores unknown names. Admit only the reviewed OSS settings.
# IMGPROXY_MALLOC is owned by the pinned upstream image entrypoint.
for name in $(env | cut -d= -f1); do
    case "$name" in
        IMGPROXY_KEY|IMGPROXY_SALT|IMGPROXY_ONLY_PRESETS|IMGPROXY_PRESETS_PATH|\
        IMGPROXY_WORKERS|IMGPROXY_REQUESTS_QUEUE_SIZE|IMGPROXY_MAX_CLIENTS|\
        IMGPROXY_TIMEOUT|IMGPROXY_READ_REQUEST_TIMEOUT|IMGPROXY_WRITE_RESPONSE_TIMEOUT|\
        IMGPROXY_DOWNLOAD_TIMEOUT|IMGPROXY_MAX_SRC_FILE_SIZE|IMGPROXY_MAX_SRC_RESOLUTION|\
        IMGPROXY_MAX_RESULT_DIMENSION|IMGPROXY_MAX_REDIRECTS|IMGPROXY_USE_S3|\
        IMGPROXY_S3_ENDPOINT|IMGPROXY_S3_REGION|IMGPROXY_S3_ENDPOINT_USE_PATH_STYLE|\
        IMGPROXY_S3_ALLOWED_BUCKETS|IMGPROXY_ALLOWED_SOURCES|IMGPROXY_JPEG_PROGRESSIVE|\
        IMGPROXY_AUTO_ROTATE|IMGPROXY_STRIP_METADATA|IMGPROXY_KEEP_COPYRIGHT|\
        IMGPROXY_STRIP_COLOR_PROFILE|IMGPROXY_LOG_LEVEL|IMGPROXY_FAIL_ON_DEPRECATION|\
        IMGPROXY_PROMETHEUS_BIND|IMGPROXY_MALLOC) ;;
        IMGPROXY_*) echo 'Unsupported imgproxy configuration' >&2; exit 2 ;;
    esac
done

# v4 errors include source paths. No raw imgproxy logs reach Docker or disk.
# Startup errors are reported by the mandatory health and signed-image gates.
exec /usr/local/bin/entrypoint.sh imgproxy >/dev/null 2>&1
