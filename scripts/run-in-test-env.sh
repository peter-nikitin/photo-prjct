#!/bin/sh
set -eu

: "${SECRET_KEY:=test-not-a-secret}"
: "${DEBUG:=False}"
: "${ALLOWED_HOSTS:=localhost,127.0.0.1}"
: "${DB_NAME:=app}"
: "${DB_USER:=app}"
: "${DB_PASSWORD:=app}"
: "${DB_HOST:=localhost}"
: "${DB_PORT:=5432}"
: "${TEST_DB_NAME:=findme_test_$$}"

export SECRET_KEY DEBUG ALLOWED_HOSTS DB_NAME DB_USER DB_PASSWORD DB_HOST DB_PORT TEST_DB_NAME
if [ "${1##*/}" = pytest ]; then
    .venv/bin/python scripts/cleanup_stale_test_databases.py
fi
exec "$@"
