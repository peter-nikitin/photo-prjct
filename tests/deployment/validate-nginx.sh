#!/bin/sh

set -eu

root_dir="$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)"
tmp_dir="$(mktemp -d)"
runtime_container=""

cleanup() {
    if [ -n "$runtime_container" ]; then
        docker rm -f "$runtime_container" >/dev/null 2>&1 || true
    fi
    rm -rf "$tmp_dir"
}

trap cleanup EXIT INT TERM

certificate_dir="$tmp_dir/letsencrypt/live/photo-prjct"
mkdir -p "$certificate_dir" "$tmp_dir/rendered"

docker run --rm --entrypoint openssl \
    -v "$certificate_dir:/certificates" \
    certbot/certbot:v2.11.0 \
    req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout /certificates/privkey.pem \
    -out /certificates/fullchain.pem \
    -subj /CN=findme-photo.ru >/dev/null 2>&1

assert_no_referrer_header() {
    header_file="$1"

    if ! tr -d '\r' < "$header_file" | grep -Eiq '^Referrer-Policy:[[:space:]]*no-referrer$'; then
        echo "response did not retain Referrer-Policy: no-referrer" >&2
        exit 1
    fi
    if tr -d '\r' < "$header_file" | grep -Eiq '^Referrer-Policy:.*same-origin'; then
        echo "response retained a conflicting Referrer-Policy" >&2
        exit 1
    fi
}

assert_same_origin_referrer_header() {
    header_file="$1"

    if ! tr -d '\r' < "$header_file" | grep -Eiq '^Referrer-Policy:[[:space:]]*same-origin$'; then
        echo "response did not retain Referrer-Policy: same-origin" >&2
        exit 1
    fi
    if tr -d '\r' < "$header_file" | grep -Eiq '^Referrer-Policy:.*no-referrer'; then
        echo "response retained a conflicting Referrer-Policy" >&2
        exit 1
    fi
}

request_status() {
    header_file="$1"
    request_host="$2"
    request_port="$3"
    request_path="$4"

    curl --silent --insecure --max-time 5 --noproxy '*' \
        --resolve "$request_host:$request_port:127.0.0.1" \
        --dump-header "$header_file" \
        --output /dev/null \
        --write-out '%{http_code}' \
        "https://$request_host:$request_port$request_path" || true
}

exercise_bearer_error_logging() {
    name="$1"
    rendered="$2"
    alias="$3"
    runtime_log_dir="$tmp_dir/runtime-$name"
    access_log="$runtime_log_dir/access.log"
    error_log="$runtime_log_dir/error.log"
    container_log="$runtime_log_dir/container.log"
    bearer_headers="$runtime_log_dir/bearer.headers"
    non_bearer_headers="$runtime_log_dir/non-bearer.headers"
    bearer_token="bearer-log-token-$name-$$"
    bearer_path="/events/runtime/selfie-search/$bearer_token/"
    non_bearer_path="/runtime-proxy-check-$name"

    mkdir -p "$runtime_log_dir"
    : > "$access_log"
    : > "$error_log"
    runtime_container="nginx-bearer-privacy-$name-$$"

    docker run --detach \
        --name "$runtime_container" \
        --add-host web:127.0.0.1 \
        --publish 127.0.0.1::443 \
        --volume "$rendered:/etc/nginx/conf.d/default.conf:ro" \
        --volume "$tmp_dir/letsencrypt:/etc/letsencrypt:ro" \
        --volume "$runtime_log_dir:/var/log/nginx" \
        --entrypoint nginx \
        nginx:1.27-alpine \
        -g 'daemon off;' >/dev/null

    runtime_binding="$(docker port "$runtime_container" 443/tcp)"
    runtime_port="${runtime_binding##*:}"
    case "$runtime_port" in
        ''|*[!0-9]*)
            echo "$name did not publish an HTTPS port" >&2
            exit 1
            ;;
    esac

    bearer_status=""
    attempt=1
    while [ "$attempt" -le 10 ]; do
        bearer_status="$(request_status "$bearer_headers" findme-photo.ru "$runtime_port" "$bearer_path")"
        if [ "$bearer_status" = 502 ]; then
            break
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
    if [ "$bearer_status" != 502 ]; then
        echo "$name bearer proxy returned $bearer_status instead of 502" >&2
        docker logs "$runtime_container" >&2 || true
        exit 1
    fi
    assert_no_referrer_header "$bearer_headers"

    non_bearer_status="$(request_status "$non_bearer_headers" findme-photo.ru "$runtime_port" "$non_bearer_path")"
    if [ "$non_bearer_status" != 502 ]; then
        echo "$name non-bearer proxy returned $non_bearer_status instead of 502" >&2
        docker logs "$runtime_container" >&2 || true
        exit 1
    fi
    assert_same_origin_referrer_header "$non_bearer_headers"

    if [ -n "$alias" ]; then
        alias_headers="$runtime_log_dir/alias.headers"
        alias_status="$(request_status "$alias_headers" "$alias" "$runtime_port" "$bearer_path")"
        if [ "$alias_status" != 308 ]; then
            echo "$name alias bearer redirect returned $alias_status instead of 308" >&2
            exit 1
        fi
        if ! tr -d '\r' < "$alias_headers" | grep -Fxiq "Location: https://findme-photo.ru$bearer_path"; then
            echo "$name alias bearer redirect changed its canonical target" >&2
            exit 1
        fi
        assert_no_referrer_header "$alias_headers"
    fi

    docker logs "$runtime_container" > "$container_log" 2>&1 || true

    if ! grep -Fq "$non_bearer_path" "$access_log"; then
        echo "$name non-bearer request did not use the normal access log" >&2
        exit 1
    fi
    if ! grep -Fq "$non_bearer_path" "$error_log"; then
        echo "$name non-bearer upstream failure did not use the normal error log" >&2
        exit 1
    fi
    for log_file in "$access_log" "$error_log" "$container_log"; do
        if grep -Fq "$bearer_token" "$log_file"; then
            echo "$name persisted a bearer token in $(basename "$log_file")" >&2
            exit 1
        fi
    done

    docker rm -f "$runtime_container" >/dev/null
    runtime_container=""
}

validate_variant() {
    name="$1"
    alias="$2"
    rendered="$tmp_dir/rendered/$name.conf"

    docker run --rm \
        -e PUBLIC_DOMAIN=findme-photo.ru \
        -e PUBLIC_DOMAIN_ALIAS="$alias" \
        -v "$root_dir/deploy/nginx:/opt/nginx:ro" \
        -v "$tmp_dir/rendered:/rendered" \
        nginx:1.27-alpine \
        /bin/sh /opt/nginx/reload-nginx.sh --render "/rendered/$name.conf"

    if grep -Eq '^[[:space:]]*server_name[[:space:]]*;' "$rendered"; then
        echo "$name rendered an empty server_name directive" >&2
        exit 1
    fi
    grep -Fq 'return 308 https://findme-photo.ru$request_uri;' "$rendered"
    grep -Fq 'location ^~ /internal/photo-processing/ {' "$rendered"
    grep -Fq 'return 404;' "$rendered"
    grep -Fq '    listen 8080;' "$rendered"
    metric_locations="$(grep -c '^    location = /metrics/ {' "$rendered")"
    if [ "$metric_locations" -ne 2 ]; then
        echo "$name must define public and private exact metrics locations" >&2
        exit 1
    fi
    public_metric_denials="$(awk '
        /location = \/metrics\// {
            getline
            if ($0 ~ /^[[:space:]]*return 404;/) count++
        }
        END { print count + 0 }
    ' "$rendered")"
    if [ "$public_metric_denials" -ne 1 ]; then
        echo "$name must deny metrics exactly once on the public HTTPS edge" >&2
        exit 1
    fi
    canonical_tls_server="$(awk '
        /^[[:space:]]*server[[:space:]]*\{/ {
            block = $0 ORS
            depth = 1
            next
        }
        depth {
            block = block $0 ORS
            opens = gsub(/\{/, "{", $0)
            closes = gsub(/\}/, "}", $0)
            depth += opens - closes
            if (depth == 0) {
                if (index(block, "listen 443 ssl;") && \
                    index(block, "server_name findme-photo.ru;")) {
                    print block
                }
                depth = 0
            }
        }
    ' "$rendered")"
    if [ -z "$canonical_tls_server" ]; then
        echo "$name did not render a canonical TLS server" >&2
        exit 1
    fi
    canonical_metrics_line="$(printf '%s\n' "$canonical_tls_server" | \
        awk '/^[[:space:]]*location = \/metrics\/ \{/ { location = NR; in_metrics = 1; next } \
             in_metrics && /^[[:space:]]*return 404;/ { print location; exit } \
             in_metrics && /^[[:space:]]*}/ { exit }')"
    canonical_catchall_line="$(printf '%s\n' "$canonical_tls_server" | \
        awk '/^[[:space:]]*location \/ \{/ { print NR; exit }')"
    if [ -z "$canonical_metrics_line" ] || [ -z "$canonical_catchall_line" ] || \
        [ "$canonical_metrics_line" -ge "$canonical_catchall_line" ]; then
        echo "$name canonical TLS server must deny exact metrics before its proxy catch-all" >&2
        exit 1
    fi
    private_server="$(awk '/^[[:space:]]*listen 8080;/ { private = 1 } private { print }' "$rendered")"
    private_locations="$(printf '%s\n' "$private_server" | grep -c '^[[:space:]]*location ')"
    if [ "$private_locations" -ne 3 ]; then
        echo "$name private listener must expose only health, metrics, and its deny fallback" >&2
        exit 1
    fi
    printf '%s\n' "$private_server" | grep -Fq 'location = /health/ {'
    printf '%s\n' "$private_server" | grep -Fq 'location = /metrics/ {'
    printf '%s\n' "$private_server" | grep -Fq 'return 444;'
    private_proxies="$(printf '%s\n' "$private_server" | grep -c 'proxy_pass http://django_upstream;')"
    if [ "$private_proxies" -ne 2 ]; then
        echo "$name private listener must proxy only health and metrics" >&2
        exit 1
    fi
    grep -Fq 'map $uri $selfie_search_access_request {' "$rendered"
    grep -Fq 'map $uri $selfie_search_access_referrer {' "$rendered"
    grep -Fq 'map $uri $selfie_search_access_user_agent {' "$rendered"
    grep -Fq '"$request_method <selfie-search>"' "$rendered"
    grep -Fq 'log_format selfie_search_safe' "$rendered"
    grep -Fq 'proxy_hide_header Referrer-Policy;' "$rendered"
    grep -Fq 'add_header Referrer-Policy "same-origin" always;' "$rendered"
    grep -Fq 'add_header Referrer-Policy "no-referrer" always;' "$rendered"
    grep -Fq 'location ~ ^/events/[^/]+/selfie-search/[^/]+(?:/|$) {' "$rendered"
    grep -Fq 'error_log /dev/null emerg;' "$rendered"
    expected_access_logs=5
    expected_bearer_error_logs=1
    if [ -n "$alias" ]; then
        grep -Fq "server_name $alias;" "$rendered"
        expected_access_logs=6
        expected_bearer_error_logs=2
    elif grep -Fq 'server_name www.findme-photo.ru;' "$rendered"; then
        echo "$name retained the optional alias server" >&2
        exit 1
    fi
    actual_access_logs="$(grep -Fc 'access_log /var/log/nginx/access.log selfie_search_safe;' "$rendered")"
    if [ "$actual_access_logs" -ne "$expected_access_logs" ]; then
        echo "$name does not sanitize every public server access log" >&2
        exit 1
    fi
    actual_bearer_error_logs="$(grep -Fc 'error_log /dev/null emerg;' "$rendered")"
    if [ "$actual_bearer_error_logs" -ne "$expected_bearer_error_logs" ]; then
        echo "$name does not isolate bearer upstream errors exactly once per bearer server" >&2
        exit 1
    fi

    docker run --rm \
        --add-host web:127.0.0.1 \
        -v "$rendered:/etc/nginx/conf.d/default.conf:ro" \
        -v "$tmp_dir/letsencrypt:/etc/letsencrypt:ro" \
        nginx:1.27-alpine nginx -t

    exercise_bearer_error_logging "$name" "$rendered" "$alias"
}

validate_variant alias www.findme-photo.ru
validate_variant no-alias ""

expect_render_rejected() {
    name="$1"
    domain="$2"
    alias="$3"

    if docker run --rm --entrypoint /bin/sh \
        -e PUBLIC_DOMAIN="$domain" \
        -e PUBLIC_DOMAIN_ALIAS="$alias" \
        -v "$root_dir/deploy/nginx:/opt/nginx:ro" \
        -v "$tmp_dir/rendered:/rendered" \
        nginx:1.27-alpine \
        /opt/nginx/reload-nginx.sh --render "/rendered/$name.conf"; then
        echo "$name unexpectedly accepted invalid hostname input" >&2
        exit 1
    fi
}

mkdir -p "$tmp_dir/invalid-template" "$tmp_dir/working-conf"
printf '%s\n' 'this is not valid nginx syntax;' > "$tmp_dir/invalid-template/https.conf.template"
printf '%s\n' 'server { listen 8081; }' > "$tmp_dir/working-conf/expected.conf"
cp "$tmp_dir/working-conf/expected.conf" "$tmp_dir/working-conf/default.conf"

if docker run --rm --entrypoint /usr/bin/timeout \
    --add-host web:127.0.0.1 \
    -e PUBLIC_DOMAIN=findme-photo.ru \
    -v "$root_dir/deploy/nginx:/source:ro" \
    -v "$tmp_dir/invalid-template:/opt/nginx:ro" \
    -v "$tmp_dir/working-conf:/etc/nginx/conf.d" \
    nginx:1.27-alpine 3 /bin/sh /source/reload-nginx.sh; then
    echo "invalid candidate unexpectedly passed nginx validation" >&2
    exit 1
fi

if ! cmp -s "$tmp_dir/working-conf/expected.conf" "$tmp_dir/working-conf/default.conf"; then
    echo "invalid candidate replaced the working Nginx configuration" >&2
    exit 1
fi

expect_render_rejected newline-domain "$(printf 'findme-photo.ru\ninjected.example.com')" ""
expect_render_rejected newline-alias findme-photo.ru \
    "$(printf 'www.findme-photo.ru\ninjected.example.com')"
expect_render_rejected case-insensitive-duplicate findme-photo.ru FINDME-PHOTO.RU
