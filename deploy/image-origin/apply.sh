#!/bin/sh
set -eu

root=${IMAGE_ORIGIN_ROOT:-/opt/photo-prjct-image-origin}
package=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mode=${1:-apply}
if [ "$mode" = apply ]; then
    case "${IMAGE_ORIGIN_RELEASE:-}" in
        ''|*[!0-9a-f]*) echo 'Invalid release: expected a repository SHA' >&2; exit 2 ;;
    esac
    [ "${#IMAGE_ORIGIN_RELEASE}" -eq 40 ] || { echo 'Invalid release length' >&2; exit 2; }
elif [ "$mode" != rollback ]; then
    echo 'Use apply.sh [apply|rollback]' >&2; exit 2
fi
case "$root" in
    /|''|*[!A-Za-z0-9/_.-]*) echo 'Invalid image-origin install root' >&2; exit 2 ;;
    /*) ;;
    *) echo 'Image-origin install root must be absolute' >&2; exit 2 ;;
esac

umask 077
mkdir -p "$root/releases" "$root/certificates" "$root/acme"
mkdir "$root/.apply-lock" || { echo 'Another origin apply is active' >&2; exit 2; }
active=$(readlink "$root/current" 2>/dev/null || true)
candidate=
replacing=0
compose() (
    release=$1
    project=$2
    shift 2
    # Stored release configuration owns rollback, even when the caller has new secrets.
    unset PRIVATE_MEDIA_S3_BUCKET GALLERY_IMGPROXY_KEY GALLERY_IMGPROXY_SALT \
        IMAGE_ORIGIN_HEADER_SECRET IMAGE_ORIGIN_S3_ACCESS_KEY_ID IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY \
        IMAGE_ORIGIN_CERTIFICATES IMAGE_ORIGIN_ACME
    docker compose --env-file "$release/.env" -f "$release/compose.yml" -p "$project" "$@"
)
candidate_compose() {
    IMAGE_ORIGIN_BIND=127.0.0.1 IMAGE_ORIGIN_HTTP_PORT=18080 \
    IMAGE_ORIGIN_HTTPS_PORT=18443 IMAGE_ORIGIN_METRICS_PORT=18082 \
    compose "$candidate" findme-image-origin-candidate "$@"
}
cleanup() {
    status=$?
    trap - EXIT INT TERM
    if [ -n "$candidate" ]; then
        candidate_compose down --remove-orphans >/dev/null 2>&1 || true
    fi
    if [ "$status" -ne 0 ] && [ "$replacing" -eq 1 ] && [ -n "$active" ]; then
        if compose "$active" findme-image-origin up -d --force-recreate --wait --wait-timeout 45 nginx; then
            sh "$active/check.sh" "$active/.env" || true
            echo 'IMAGE_ORIGIN_ROLLBACK=restored_previous_package' >&2
        else
            echo 'IMAGE_ORIGIN_ROLLBACK=failed' >&2
        fi
    fi
    rmdir "$root/.apply-lock"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$mode" = rollback ]; then
    candidate=$(readlink "$root/previous" 2>/dev/null || true)
    [ -n "$candidate" ] && [ -f "$candidate/.env" ] || {
        echo 'No previous origin package is available' >&2; exit 2;
    }
else
    candidate="$root/releases/$IMAGE_ORIGIN_RELEASE"
    # Only these projections become persistent runtime configuration. No app/CDN token secret.
    values='PRIVATE_MEDIA_S3_BUCKET GALLERY_IMGPROXY_KEY GALLERY_IMGPROXY_SALT
IMAGE_ORIGIN_HEADER_SECRET IMAGE_ORIGIN_S3_ACCESS_KEY_ID IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY
IMAGE_ORIGIN_PROBE_PATH'
    for name in $values; do
        eval "value=\${$name:-}"
        case "$value" in
            ''|*[!A-Za-z0-9_./:=+-]*) echo "Invalid required configuration: $name" >&2; exit 2 ;;
        esac
    done
    if [ ! -d "$candidate" ]; then
        mkdir "$candidate"
        cp -R "$package/." "$candidate/"
    elif [ "$package" != "$candidate" ]; then
        diff -qr --exclude=.env "$package" "$candidate" >/dev/null || {
            echo 'Existing release package differs from the reviewed package' >&2; exit 2;
        }
    fi
    temporary_env=$(mktemp "$root/.candidate-env.XXXXXX")
    for name in $values; do
        eval "value=\${$name}"
        printf '%s=%s\n' "$name" "$value" >>"$temporary_env"
    done
    printf 'IMAGE_ORIGIN_CERTIFICATES=%s/certificates\nIMAGE_ORIGIN_ACME=%s/acme\n' \
        "$root" "$root" >>"$temporary_env"
    if [ -f "$candidate/.env" ]; then
        cmp -s "$candidate/.env" "$temporary_env" || {
            rm -f "$temporary_env"
            echo 'Existing release configuration differs; use a new reviewed release' >&2
            exit 2
        }
        rm -f "$temporary_env"
    else
        mv "$temporary_env" "$candidate/.env"
    fi
fi

for config_source in \
    imgproxy-start.sh presets.txt nginx.conf.template nginx-start.sh monitoring/metrics.js
do
    chmod go+r "$candidate/$config_source"
done

# docker config can contain secrets: validate without rendering it to logs.
candidate_compose config --quiet
# Prepare root-owned ACME directories through the restricted Certbot identity, without issuance.
candidate_compose run --rm --no-deps certbot --version
candidate_compose up -d --wait --wait-timeout 45 imgproxy
candidate_compose run --rm --no-deps nginx -t
candidate_compose up -d --wait --wait-timeout 45 nginx
IMAGE_ORIGIN_CHECK_PORT=18443 sh "$candidate/check.sh" "$candidate/.env"
candidate_compose down --remove-orphans

# The old package stays active until the candidate's real signed JPEG gate is green.
replacing=1
compose "$candidate" findme-image-origin up -d --force-recreate --wait --wait-timeout 45 nginx
sh "$candidate/check.sh" "$candidate/.env"
if [ -n "$active" ] && [ "$active" != "$candidate" ]; then
    ln -sfn "$active" "$root/previous"
fi
ln -sfn "$candidate" "$root/current"
replacing=0
echo 'IMAGE_ORIGIN_APPLY=green'
