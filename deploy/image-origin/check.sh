#!/bin/sh
set -eu
package=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$#" -gt 0 ]; then
    # apply.sh writes this root-private, validated projection; never source an arbitrary file.
    set -a
    . "$1"
    set +a
fi
: "${IMAGE_ORIGIN_HEADER_SECRET:?Set IMAGE_ORIGIN_HEADER_SECRET}"
: "${IMAGE_ORIGIN_PROBE_PATH:?Set IMAGE_ORIGIN_PROBE_PATH}"
port=${IMAGE_ORIGIN_CHECK_PORT:-443}
case "$port" in 443|18443) ;; *) echo 'Invalid check port' >&2; exit 2 ;; esac
case "$IMAGE_ORIGIN_HEADER_SECRET" in
    *[!A-Za-z0-9_-]*|'') echo 'Invalid origin header configuration' >&2; exit 2 ;;
esac
printf '%s\n' "$IMAGE_ORIGIN_PROBE_PATH" | \
    grep -Eq '^/[A-Za-z0-9_-]{43}/gallery-v1/[A-Za-z0-9_-]+\.jpg$' && \
    [ "${#IMAGE_ORIGIN_PROBE_PATH}" -le 828 ] || {
        echo 'Invalid signed image probe path' >&2; exit 2;
    }
scratch=$(mktemp -d)
trap 'rm -f "$scratch/headers" "$scratch/image.jpg"; rmdir "$scratch"' EXIT
umask 077
# Secret and signed URI travel via stdin, never process arguments or diagnostic output.
if ! printf 'url = "https://img-origin.findme-photo.ru:%s%s"\nheader = "X-FindMe-Origin-Auth: %s"\nresolve = "img-origin.findme-photo.ru:%s:127.0.0.1"\n' \
    "$port" "$IMAGE_ORIGIN_PROBE_PATH" "$IMAGE_ORIGIN_HEADER_SECRET" "$port" | \
    curl --config - --fail --silent --connect-timeout 1 --max-time 4 \
        --output "$scratch/image.jpg" --dump-header "$scratch/headers"; then
    echo 'IMAGE_ORIGIN_CHECK=failed_http_or_tls' >&2
    exit 1
fi
python3 "$package/check-image.py" "$scratch/image.jpg" "$scratch/headers"
