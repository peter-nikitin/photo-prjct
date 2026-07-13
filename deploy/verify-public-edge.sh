#!/bin/sh

set -eu

: "${PUBLIC_DOMAIN:?Set PUBLIC_DOMAIN}"
: "${EXPECTED_PUBLIC_IPV4:?Set EXPECTED_PUBLIC_IPV4}"
PUBLIC_DOMAIN_ALIAS="${PUBLIC_DOMAIN_ALIAS:-}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

desired_names="$(
    {
        printf '%s\n' "$PUBLIC_DOMAIN"
        if [ -n "$PUBLIC_DOMAIN_ALIAS" ]; then
            printf '%s\n' "$PUBLIC_DOMAIN_ALIAS"
        fi
    } | tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz' | LC_ALL=C sort -u
)"

dns_records() {
    host="$1"
    record_type="$2"
    response_file="$tmp_dir/dns-${record_type}-$(printf '%s' "$host" | tr '.' '-').json"
    curl --fail --silent --show-error --max-time 15 \
        "https://dns.google/resolve?name=$host&type=$record_type" > "$response_file"
    python3 -c '
import json, sys
kind = {"A": 1, "AAAA": 28}[sys.argv[1]]
with open(sys.argv[2], encoding="utf-8") as source:
    data = json.load(source)
if data.get("Status") != 0:
    raise SystemExit(f"DNS status {data.get(chr(83)+chr(116)+chr(97)+chr(116)+chr(117)+chr(115))}")
for item in sorted(answer["data"] for answer in data.get("Answer", []) if answer.get("type") == kind):
    print(item)
' "$record_type" "$response_file"
}

verify_dns() {
    host="$1"
    a_records="$(dns_records "$host" A)"
    if [ "$a_records" != "$EXPECTED_PUBLIC_IPV4" ]; then
        echo "$host A records do not exactly match $EXPECTED_PUBLIC_IPV4" >&2
        exit 1
    fi
    aaaa_records="$(dns_records "$host" AAAA)"
    if [ -n "$aaaa_records" ]; then
        echo "$host AAAA records must be empty" >&2
        exit 1
    fi
}

verify_redirect() {
    host="$1"
    probe_path='/__edge_verify__?source=github'
    expected_location="https://$PUBLIC_DOMAIN$probe_path"
    output_file="$tmp_dir/redirect-$(printf '%s' "$host" | tr '.' '-')"
    curl --silent --show-error --max-time 15 --output /dev/null \
        --write-out '%{http_code}\n%{redirect_url}\n' \
        "http://$host$probe_path" > "$output_file"
    status="$(sed -n '1p' "$output_file")"
    location="$(sed -n '2p' "$output_file")"
    if [ "$status" != 308 ]; then
        echo "$host HTTP redirect returned $status instead of 308" >&2
        exit 1
    fi
    if [ "$location" != "$expected_location" ]; then
        echo "$host Location does not preserve the canonical path and query" >&2
        exit 1
    fi
}

certificate_names() {
    host="$1"
    timeout 15 openssl s_client -connect "$host:443" -servername "$host" </dev/null 2>/dev/null |
        openssl x509 -noout -ext subjectAltName |
        tr ',' '\n' |
        sed -n 's/^[[:space:]]*DNS:[[:space:]]*//p' |
        tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz' |
        LC_ALL=C sort -u
}

verify_certificate() {
    host="$1"
    served_names="$(certificate_names "$host")"
    if [ "$served_names" != "$desired_names" ]; then
        echo "$host certificate SAN set does not exactly match configured domains" >&2
        exit 1
    fi
}

verify_dns "$PUBLIC_DOMAIN"
verify_redirect "$PUBLIC_DOMAIN"

health_status="$(curl --silent --show-error --max-time 15 --output /dev/null \
    --write-out '%{http_code}\n' "https://$PUBLIC_DOMAIN/health/")"
if [ "$health_status" != 200 ]; then
    echo "Canonical HTTPS health returned $health_status instead of 200" >&2
    exit 1
fi

verify_certificate "$PUBLIC_DOMAIN"

if [ -n "$PUBLIC_DOMAIN_ALIAS" ]; then
    verify_dns "$PUBLIC_DOMAIN_ALIAS"
    verify_redirect "$PUBLIC_DOMAIN_ALIAS"
    verify_certificate "$PUBLIC_DOMAIN_ALIAS"
fi
