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
    shift 4

    curl --silent --insecure --max-time 5 --noproxy '*' \
        --resolve "$request_host:$request_port:127.0.0.1" \
        --dump-header "$header_file" \
        --output /dev/null \
        --write-out '%{http_code}' \
        "$@" \
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
    submission_headers="$runtime_log_dir/submission.headers"
    submission_post_headers="$runtime_log_dir/submission-post.headers"
    status_headers="$runtime_log_dir/status.headers"
    media_headers="$runtime_log_dir/media.headers"
    download_headers="$runtime_log_dir/download.headers"
    bearer_4xx_headers="$runtime_log_dir/bearer-4xx.headers"
    internal_headers="$runtime_log_dir/internal.headers"
    event_headers="$runtime_log_dir/event.headers"
    static_headers="$runtime_log_dir/static.headers"
    request_body="$runtime_log_dir/request-body.bin"
    runtime_rendered="$runtime_log_dir/runtime.conf"
    bearer_token="bearer-log-token-$name-$$"
    sentinel_client_ip="sentinel-client-ip-$name"
    sentinel_referrer="https://sentinel-referrer.invalid/$name"
    sentinel_user_agent="sentinel-user-agent/$name"
    sentinel_tracking="sentinel-tracking-$name"
    sentinel_request_body="sentinel-request-body-$name"
    ordinary_referrer="https://ordinary-referrer.invalid/$name"
    ordinary_user_agent="ordinary-user-agent/$name"
    submission_path="/events/runtime/selfie-search/?utm_source=$sentinel_tracking&fbclid=$sentinel_tracking"
    bearer_result_path="/events/runtime/selfie-search/$bearer_token/?utm_source=$sentinel_tracking"
    bearer_status_path="/events/runtime/selfie-search/$bearer_token/status/?$sentinel_tracking=1"
    bearer_media_path="/events/runtime/selfie-search/$bearer_token/media/photo.jpg?download=$sentinel_tracking"
    bearer_download_path="/events/runtime/selfie-search/$bearer_token/download/original?ref=$sentinel_tracking"
    ordinary_path="/runtime-proxy-check-$name?ordinary=ordinary-query-$name"
    internal_path="/internal/photo-processing/"

    mkdir -p "$runtime_log_dir"
    : > "$access_log"
    : > "$error_log"
    printf '%s' "$sentinel_request_body" > "$request_body"
    dd if=/dev/zero bs=1024 count=64 >> "$request_body" 2>/dev/null
    awk '
        index($0, "location ~ ^/events/[^/]+/selfie-search/$ {") ||
        index($0, "location ~ ^/events/[^/]+/selfie-search/[^/]+(?:/|$) {") {
            print
            print "        client_max_body_size 1k;"
            next
        }
        { print }
    ' "$rendered" > "$runtime_rendered"
    runtime_container="nginx-bearer-privacy-$name-$$"

    docker run --detach \
        --name "$runtime_container" \
        --add-host web:127.0.0.1 \
        --publish 127.0.0.1::443 \
        --volume "$runtime_rendered:/etc/nginx/conf.d/default.conf:ro" \
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
        bearer_status="$(request_status "$bearer_headers" findme-photo.ru "$runtime_port" "$bearer_result_path" \
            --header "X-Forwarded-For: $sentinel_client_ip" --header "Referer: $sentinel_referrer" \
            --user-agent "$sentinel_user_agent")"
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

    submission_status="$(request_status "$submission_headers" findme-photo.ru "$runtime_port" "$submission_path" \
        --header "X-Forwarded-For: $sentinel_client_ip" --header "Referer: $sentinel_referrer" \
        --user-agent "$sentinel_user_agent")"
    if [ "$submission_status" != 502 ]; then
        echo "$name exact submission proxy returned $submission_status instead of 502" >&2
        docker logs "$runtime_container" >&2 || true
        exit 1
    fi

    submission_post_status="$(request_status "$submission_post_headers" findme-photo.ru "$runtime_port" "$submission_path" \
        --header "X-Forwarded-For: $sentinel_client_ip" --header "Referer: $sentinel_referrer" \
        --user-agent "$sentinel_user_agent" --request POST --data-binary "@$request_body")"
    if [ "$submission_post_status" != 413 ]; then
        echo "$name buffered submission returned $submission_post_status instead of 413" >&2
        docker logs "$runtime_container" >&2 || true
        exit 1
    fi

    bearer_4xx_status="$(request_status "$bearer_4xx_headers" findme-photo.ru "$runtime_port" "$bearer_result_path" \
        --header "X-Forwarded-For: $sentinel_client_ip" --header "Referer: $sentinel_referrer" \
        --user-agent "$sentinel_user_agent" --request POST --data-binary "@$request_body")"
    if [ "$bearer_4xx_status" != 413 ]; then
        echo "$name buffered bearer request returned $bearer_4xx_status instead of 413" >&2
        docker logs "$runtime_container" >&2 || true
        exit 1
    fi

    status_status="$(request_status "$status_headers" findme-photo.ru "$runtime_port" "$bearer_status_path" \
        --header "X-Forwarded-For: $sentinel_client_ip" --header "Referer: $sentinel_referrer" \
        --user-agent "$sentinel_user_agent")"
    media_status="$(request_status "$media_headers" findme-photo.ru "$runtime_port" "$bearer_media_path" \
        --header "X-Forwarded-For: $sentinel_client_ip" --header "Referer: $sentinel_referrer" \
        --user-agent "$sentinel_user_agent")"
    download_status="$(request_status "$download_headers" findme-photo.ru "$runtime_port" "$bearer_download_path" \
        --header "X-Forwarded-For: $sentinel_client_ip" --header "Referer: $sentinel_referrer" \
        --user-agent "$sentinel_user_agent")"
    if [ "$status_status" != 502 ] || [ "$media_status" != 502 ] || [ "$download_status" != 502 ]; then
        echo "$name bearer result/status/media/download did not return 502" >&2
        docker logs "$runtime_container" >&2 || true
        exit 1
    fi

    non_bearer_status="$(request_status "$non_bearer_headers" findme-photo.ru "$runtime_port" "$ordinary_path" \
        --header "X-Forwarded-For: $sentinel_client_ip" --header "Referer: $ordinary_referrer" \
        --user-agent "$ordinary_user_agent")"
    if [ "$non_bearer_status" != 502 ]; then
        echo "$name non-bearer proxy returned $non_bearer_status instead of 502" >&2
        docker logs "$runtime_container" >&2 || true
        exit 1
    fi
    assert_same_origin_referrer_header "$non_bearer_headers"

    event_path="/events/runtime/page/$name?ordinary=event-query-$name"
    event_status="$(request_status "$event_headers" findme-photo.ru "$runtime_port" "$event_path" \
        --header "X-Forwarded-For: $sentinel_client_ip" --header "Referer: $ordinary_referrer" \
        --user-agent "$ordinary_user_agent")"
    if [ "$event_status" != 502 ]; then
        echo "$name ordinary event request returned $event_status instead of 502" >&2
        exit 1
    fi

    static_path="/static/runtime-check-$name.css?ordinary=static-query-$name"
    static_status="$(request_status "$static_headers" findme-photo.ru "$runtime_port" "$static_path" \
        --header "X-Forwarded-For: $sentinel_client_ip" --header "Referer: $ordinary_referrer" \
        --user-agent "$ordinary_user_agent")"
    if [ "$static_status" != 502 ]; then
        echo "$name ordinary static request returned $static_status instead of 502" >&2
        exit 1
    fi

    internal_status="$(request_status "$internal_headers" findme-photo.ru "$runtime_port" "$internal_path" \
        --header "X-Forwarded-For: $sentinel_client_ip" --header "Referer: $sentinel_referrer" \
        --user-agent "$sentinel_user_agent")"
    if [ "$internal_status" != 404 ]; then
        echo "$name private processing path returned $internal_status instead of 404" >&2
        exit 1
    fi

    if [ -n "$alias" ]; then
        alias_headers="$runtime_log_dir/alias.headers"
        alias_status="$(request_status "$alias_headers" "$alias" "$runtime_port" "$bearer_result_path" \
            --header "X-Forwarded-For: $sentinel_client_ip" --header "Referer: $sentinel_referrer" \
            --user-agent "$sentinel_user_agent")"
        if [ "$alias_status" != 308 ]; then
            echo "$name alias bearer redirect returned $alias_status instead of 308" >&2
            exit 1
        fi
        if ! tr -d '\r' < "$alias_headers" | grep -Fxiq "Location: https://findme-photo.ru$bearer_result_path"; then
            echo "$name alias bearer redirect changed its canonical target" >&2
            exit 1
        fi
        assert_no_referrer_header "$alias_headers"

        alias_submission_headers="$runtime_log_dir/alias-submission.headers"
        alias_submission_status="$(request_status "$alias_submission_headers" "$alias" "$runtime_port" "$submission_path" \
            --header "X-Forwarded-For: $sentinel_client_ip" --header "Referer: $sentinel_referrer" \
            --user-agent "$sentinel_user_agent")"
        if [ "$alias_submission_status" != 308 ]; then
            echo "$name alias submission redirect returned $alias_submission_status instead of 308" >&2
            exit 1
        fi
        if ! tr -d '\r' < "$alias_submission_headers" | grep -Fxiq "Location: https://findme-photo.ru$submission_path"; then
            echo "$name alias submission redirect changed its canonical target" >&2
            exit 1
        fi
        assert_no_referrer_header "$alias_submission_headers"
    fi

    docker logs "$runtime_container" > "$container_log" 2>&1 || true

    if ! grep -Fq "$ordinary_path" "$access_log"; then
        echo "$name non-bearer request did not use the normal access log" >&2
        exit 1
    fi
    if ! grep -Fq "$event_path" "$access_log" || ! grep -Fq "$static_path" "$access_log"; then
        echo "$name ordinary event/static requests did not use the normal access log" >&2
        exit 1
    fi
    if grep -F "$event_path" "$access_log" | grep -Fq '<selfie-search>' || \
       grep -F "$static_path" "$access_log" | grep -Fq '<selfie-search>'; then
        echo "$name ordinary event/static requests used the selfie placeholder" >&2
        exit 1
    fi
    if ! grep -Fq "$ordinary_referrer" "$access_log" || \
       ! grep -Fq "$ordinary_user_agent" "$access_log"; then
        echo "$name ordinary access log dropped existing referrer or user-agent fields" >&2
        exit 1
    fi
    if ! grep -Fq "$ordinary_path" "$error_log"; then
        echo "$name non-bearer upstream failure did not use the normal error log" >&2
        exit 1
    fi

    selfie_access_log="$runtime_log_dir/selfie-access.log"
    grep -F '<selfie-search>' "$access_log" > "$selfie_access_log" || true
    if [ ! -s "$selfie_access_log" ]; then
        echo "$name did not write redacted selfie access lines" >&2
        exit 1
    fi
    if ! awk '!/^-[[:space:]]+-[[:space:]]+-[[:space:]]+\[/{ exit 1 }' "$selfie_access_log"; then
        echo "$name selfie access line did not start with fixed client/user placeholders" >&2
        cat "$selfie_access_log" >&2
        exit 1
    fi
    ordinary_client_address="$(awk -v needle="$ordinary_path" 'index($0, needle) { print $1; exit }' "$access_log")"
    case "$ordinary_client_address" in
        ''|'-')
            echo "$name ordinary access line did not retain a real client address" >&2
            exit 1
            ;;
    esac
    if grep -Fq "$ordinary_client_address" "$selfie_access_log"; then
        echo "$name selfie access lines retained the ordinary client address" >&2
        exit 1
    fi
    if grep -E '"(GET|POST) <selfie-search>" [45][0-9]{2} [0-9]+ "-" "-" [0-9]+\.[0-9]{3}$' "$selfie_access_log" >/dev/null; then
        :
    else
        echo "$name selfie access line omitted the fixed fields or request duration" >&2
        cat "$selfie_access_log" >&2
        exit 1
    fi
    for selfie_log in "$selfie_access_log" "$error_log" "$container_log"; do
        if grep -Fq "$submission_path" "$selfie_log" || \
           grep -Fq "$bearer_token" "$selfie_log" || \
           grep -Fq "$sentinel_tracking" "$selfie_log" || \
           grep -Fq "$sentinel_referrer" "$selfie_log" || \
           grep -Fq "$sentinel_user_agent" "$selfie_log" || \
           grep -Fq "$sentinel_client_ip" "$selfie_log" || \
           grep -Fq "$sentinel_request_body" "$selfie_log"; then
            echo "$name persisted private metadata in $(basename "$selfie_log")" >&2
            cat "$selfie_log" >&2 || true
            exit 1
        fi
    done
    for log_file in "$access_log" "$error_log" "$container_log"; do
        if grep -Fq "$bearer_token" "$log_file" || \
           grep -Fq "$sentinel_client_ip" "$log_file" || \
           grep -Fq "$sentinel_request_body" "$log_file"; then
            echo "$name persisted a bearer token, client identity, or request body in $(basename "$log_file")" >&2
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
    grep -Fq 'map $uri $selfie_search_access_client_address {' "$rendered"
    grep -Fq 'map $uri $selfie_search_access_remote_user {' "$rendered"
    grep -Fq 'map $uri $selfie_search_access_request {' "$rendered"
    grep -Fq 'map $uri $selfie_search_access_referrer {' "$rendered"
    grep -Fq 'map $uri $selfie_search_access_user_agent {' "$rendered"
    grep -Fq '~^/events/[^/]+/selfie-search/$ "$request_method <selfie-search>";' "$rendered"
    grep -Fq '"$request_method <selfie-search>"' "$rendered"
    grep -Fq 'log_format selfie_search_safe' "$rendered"
    grep -Fq "log_format selfie_search_safe '\$selfie_search_access_client_address - \$selfie_search_access_remote_user" "$rendered"
    grep -Fq 'body_bytes_sent' "$rendered"
    grep -Fq ' $request_time' "$rendered"
    grep -Fq 'proxy_hide_header Referrer-Policy;' "$rendered"
    grep -Fq 'add_header Referrer-Policy "same-origin" always;' "$rendered"
    grep -Fq 'add_header Referrer-Policy "no-referrer" always;' "$rendered"
    grep -Fq 'location ~ ^/events/[^/]+/selfie-search/$ {' "$rendered"
    grep -Fq 'location ~ ^/events/[^/]+/selfie-search/[^/]+(?:/|$) {' "$rendered"
    grep -Fq 'error_log /dev/null emerg;' "$rendered"
    expected_access_logs=5
    expected_bearer_error_logs=2
    if [ -n "$alias" ]; then
        grep -Fq "server_name $alias;" "$rendered"
        expected_access_logs=6
        expected_bearer_error_logs=4
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
