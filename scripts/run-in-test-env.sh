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
: "${PRIVATE_MEDIA_S3_BUCKET:=test-private-media}"
: "${PRIVATE_MEDIA_S3_ACCESS_KEY_ID:=test-private-access}"
: "${PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY:=test-private-secret}"
: "${PRIVATE_MEDIA_ALLOWED_ORIGINS:=https://photos.example.test}"
: "${SELFIE_FEEDBACK_S3_BUCKET:=test-feedback-media}"
: "${SELFIE_FEEDBACK_S3_ACCESS_KEY_ID:=test-feedback-access}"
: "${SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY:=test-feedback-secret}"
: "${SELFIE_FEEDBACK_KMS_KEY_ID:=test-feedback-kms}"

export SECRET_KEY DEBUG ALLOWED_HOSTS DB_NAME DB_USER DB_PASSWORD DB_HOST DB_PORT TEST_DB_NAME
export PRIVATE_MEDIA_S3_BUCKET PRIVATE_MEDIA_S3_ACCESS_KEY_ID PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY PRIVATE_MEDIA_ALLOWED_ORIGINS
export SELFIE_FEEDBACK_S3_BUCKET SELFIE_FEEDBACK_S3_ACCESS_KEY_ID SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY SELFIE_FEEDBACK_KMS_KEY_ID
if [ "${1##*/}" = pytest ]; then
    .venv/bin/python scripts/cleanup_stale_test_databases.py
fi
exec "$@"
