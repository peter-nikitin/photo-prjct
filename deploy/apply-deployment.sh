#!/bin/sh
set -eu

: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"
case "$DEPLOY_ROOT" in /*) ;; *) echo 'DEPLOY_ROOT must be absolute' >&2; exit 2 ;; esac
for value in "${DEPLOY_WEB:-false}" "${DEPLOY_PHOTO:-false}" "${DEPLOY_IMPORT:-false}" "${DEPLOY_COMMERCE:-false}"; do
    case "$value" in true|false) ;; *) echo 'Deployment selection must be true or false' >&2; exit 2 ;; esac
done
[ -f "$DEPLOY_ROOT/.env" ] && [ ! -L "$DEPLOY_ROOT/.env" ] || {
    echo 'Deployment environment is missing or symlinked' >&2; exit 2;
}
compose() {
    docker compose --env-file "$DEPLOY_ROOT/.env" -f "$DEPLOY_ROOT/docker-compose.deployment.yml" "$@"
}
compose config --quiet
if [ "${DEPLOY_WEB:-false}" = true ] || [ "${DEPLOY_COMMERCE:-false}" = true ]; then
    # A regular app release must never turn a fresh PostgreSQL instance into an empty site.
    schema=$(compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "SELECT to_regclass('\''public.django_migrations'\'') IS NOT NULL"' 2>/dev/null) || schema=
    if [ "$schema" != t ]; then
        echo 'Restore the database before starting an application deployment' >&2
        exit 1
    fi
fi

# Image refs are independent. Keep all other root-private dotenv lines byte-for-byte.
saved_env=$(mktemp "$DEPLOY_ROOT/.env.previous.XXXXXX")
cp "$DEPLOY_ROOT/.env" "$saved_env"
cleanup() {
    code=$?
    trap - 0
    if [ "$code" -eq 0 ]; then
        rm -f "$saved_env"
    else
        mv -f "$saved_env" "$DEPLOY_ROOT/.env"
    fi
    exit "$code"
}
trap cleanup 0
trap 'exit 130' INT
trap 'exit 143' TERM
python3 - "$DEPLOY_ROOT/.env" <<'PY'
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
if stat.S_IMODE(path.stat().st_mode) != 0o600:
    raise SystemExit('Deployment environment must be mode 0600')
selected = {
    'APP_IMAGE': os.getenv('DEPLOY_WEB') == 'true' or os.getenv('DEPLOY_COMMERCE') == 'true',
    'PHOTO_WORKER_IMAGE': os.getenv('DEPLOY_PHOTO') == 'true',
    'IMPORT_WORKER_IMAGE': os.getenv('DEPLOY_IMPORT') == 'true',
}
updates = {}
for name, enabled in selected.items():
    if not enabled:
        continue
    image = os.getenv(name, '')
    if not re.fullmatch(r'ghcr\.io/[a-z0-9._/-]+(?::[0-9a-f]{40}|@sha256:[0-9a-f]{64})', image):
        raise SystemExit(f'{name} must be an immutable GHCR image ref')
    updates[name] = image
if selected['PHOTO_WORKER_IMAGE']:
    build = os.getenv('PHOTO_WORKER_BUILD', '')
    if not re.fullmatch(r'[0-9a-f]{40}', build):
        raise SystemExit('PHOTO_WORKER_BUILD must be an immutable Git SHA')
    updates['PHOTO_WORKER_BUILD'] = build
lines = path.read_text().splitlines(keepends=True)
values = {}
for line in lines:
    name, separator, value = line.partition('=')
    if separator and name.isupper():
        values[name] = value.strip().strip('"')
if values.get('PHOTO_WORKER_ENABLED') == 'True':
    if not values.get('PHOTO_PROCESSING_WORKER_TOKEN'):
        print('PHOTO_PROCESSING_WORKER_TOKEN is required for active photo worker', file=sys.stderr)
        raise SystemExit(2)
    if values.get('PHOTO_PROCESSING_ENABLED') != 'True':
        print('PHOTO_PROCESSING_ENABLED must be True for active photo worker', file=sys.stderr)
        raise SystemExit(2)
if values.get('PHOTO_IMPORT_WORKER_ENABLED') == 'True':
    if not values.get('PHOTO_IMPORT_WORKER_TOKEN'):
        print('PHOTO_IMPORT_WORKER_TOKEN is required for active import worker', file=sys.stderr)
        raise SystemExit(2)
    if values.get('PHOTO_IMPORT_ENABLED') != 'True':
        print('PHOTO_IMPORT_ENABLED must be True for active import worker', file=sys.stderr)
        raise SystemExit(2)
if values.get('COMMERCE_PAYMENT_GATEWAY_FACTORY') == 'commerce.tbank_gateway.tbank_gateway_factory':
    required = ('COMMERCE_PUBLIC_ORIGIN', 'TBANK_TERMINAL_KEY', 'TBANK_TERMINAL_PASSWORD',
                'TBANK_API_ORIGIN', 'TBANK_RUB_ONLY', 'TBANK_PAY_TYPE', 'TBANK_RECEIPT_FFD',
                'TBANK_RECEIPT_TAXATION', 'TBANK_RECEIPT_TAX', 'TBANK_RECEIPT_PAYMENT_METHOD',
                'TBANK_RECEIPT_PAYMENT_OBJECT', 'TBANK_RECEIPT_MEASUREMENT_UNIT',
                'TBANK_RECEIPT_CLOSING_REQUIRED')
    for name in required:
        if not values.get(name):
            print(f'{name} is required for the selected bank', file=sys.stderr)
            raise SystemExit(2)
for name, value in updates.items():
    replacement = f'{name}={value}\n'
    matches = [index for index, line in enumerate(lines) if line.startswith(f'{name}=')]
    if len(matches) > 1:
        raise SystemExit(f'duplicate {name} in deployment environment')
    if matches:
        lines[matches[0]] = replacement
    else:
        lines.append(replacement)
descriptor, temporary_name = tempfile.mkstemp(prefix='.env.pending.', dir=path.parent)
temporary = Path(temporary_name)
try:
    with os.fdopen(descriptor, 'w') as stream:
        stream.writelines(lines)
    temporary.replace(path)
finally:
    temporary.unlink(missing_ok=True)
PY

compose config --quiet
if [ "${DEPLOY_WEB:-false}" = true ] || [ "${DEPLOY_COMMERCE:-false}" = true ]; then
    compose pull web
    compose run --rm --no-deps --entrypoint python web manage.py migrate --noinput
    compose up -d --wait --no-deps web imgproxy edge
fi
if [ "${DEPLOY_PHOTO:-false}" = true ]; then
    compose --profile processing pull photo-worker
    if grep -qx 'PHOTO_WORKER_ENABLED=True' "$DEPLOY_ROOT/.env"; then
        compose --profile processing up -d --wait --no-deps photo-worker
    fi
fi
if [ "${DEPLOY_IMPORT:-false}" = true ]; then
    compose --profile import pull import-worker
    if grep -qx 'PHOTO_IMPORT_WORKER_ENABLED=True' "$DEPLOY_ROOT/.env"; then
        compose --profile import up -d --wait --no-deps import-worker
    fi
fi
if [ "${DEPLOY_COMMERCE:-false}" = true ] && grep -qx 'COMMERCE_WORKER_ENABLED=True' "$DEPLOY_ROOT/.env"; then
    compose --profile commerce up -d --wait --no-deps commerce-worker
fi
printf 'DEPLOY_RESULT=success\n'
