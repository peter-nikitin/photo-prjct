#!/bin/sh

set -eu
umask 077

fail() {
    echo "$1" >&2
    exit 1
}

validate_local_docker_endpoint() {
    context_endpoint_json="$(
        docker context inspect --format '{{json .Endpoints.docker.Host}}' 2>/dev/null
    )" || fail "Could not inspect the effective Docker context"
    context_endpoint="$(
        python3 - "$context_endpoint_json" <<'PY'
import json
import sys

try:
    endpoint = json.loads(sys.argv[1])
except (json.JSONDecodeError, TypeError):
    raise SystemExit(1) from None
if not isinstance(endpoint, str) or not endpoint:
    raise SystemExit(1)
print(endpoint)
PY
    )" || fail "Could not inspect the effective Docker context"

    if [ -n "${DOCKER_CONTEXT:-}" ]; then
        effective_endpoint="$context_endpoint"
    elif [ -n "${DOCKER_HOST:-}" ]; then
        effective_endpoint="$DOCKER_HOST"
    else
        effective_endpoint="$context_endpoint"
    fi

    if ! python3 - "$effective_endpoint" <<'PY'
import ipaddress
import sys
from urllib.parse import urlsplit

endpoint = sys.argv[1]
if any(ord(character) <= 0x1F or ord(character) == 0x7F for character in endpoint):
    raise SystemExit(1)

try:
    parsed = urlsplit(endpoint)
    port = parsed.port
except ValueError:
    raise SystemExit(1) from None

if parsed.scheme == "unix":
    allowed = (
        not parsed.netloc
        and parsed.path.startswith("/")
        and not parsed.query
        and not parsed.fragment
    )
elif parsed.scheme == "tcp":
    host = parsed.hostname
    if host is None or port is None or parsed.username or parsed.password or parsed.path:
        allowed = False
    elif host.lower() == "localhost":
        allowed = not parsed.query and not parsed.fragment
    else:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            allowed = False
        else:
            allowed = address.is_loopback and not parsed.query and not parsed.fragment
else:
    allowed = False

raise SystemExit(not allowed)
PY
    then
        fail "Refusing to clone: the effective Docker endpoint is not a recognized local Docker endpoint"
    fi
}

backup_dir="${BACKUP_DIR:-var/backups/staging}"
staging_dump_file="${STAGING_DUMP_FILE:-}"

if [ -n "$staging_dump_file" ]; then
    dump_source="retained"
else
    dump_source="staging"
    : "${STAGING_SSH_TARGET:?Set STAGING_SSH_TARGET to the staging SSH destination}"
fi

command -v python3 >/dev/null 2>&1 || fail "python3 is required to validate rendered Compose configuration"
command -v docker >/dev/null 2>&1 || fail "docker is required"
validate_local_docker_endpoint
docker compose version >/dev/null 2>&1 || fail "docker compose is required"
if [ "$dump_source" = staging ]; then
    command -v ssh >/dev/null 2>&1 || fail "ssh is required"
fi

config_tmp=""
resolved_tmp=""
dump_tmp=""
toc_tmp=""
checksum_tmp=""
metadata_tmp=""
safety_tmp=""
published_dump=""
published_checksum=""
published_metadata=""
publication_complete=0
safety_dump_path=""
replacement_started=0
replacement_committed=0
recovery_attempted=0
lock_path=""
lock_acquired=0
cleanup() {
    status=$?
    if [ "$status" -ne 0 ] && [ "$replacement_started" -eq 1 ] && \
        [ "$replacement_committed" -ne 1 ] && [ "$recovery_attempted" -ne 1 ]; then
        attempt_safety_recovery "Clone failed after local DROP began" || true
    fi
    rm -f \
        ${config_tmp:+"$config_tmp"} \
        ${resolved_tmp:+"$resolved_tmp"} \
        ${dump_tmp:+"$dump_tmp"} \
        ${toc_tmp:+"$toc_tmp"} \
        ${checksum_tmp:+"$checksum_tmp"} \
        ${metadata_tmp:+"$metadata_tmp"} \
        ${safety_tmp:+"$safety_tmp"}
    if [ "$publication_complete" -ne 1 ]; then
        rm -f \
            ${published_dump:+"$published_dump"} \
            ${published_checksum:+"$published_checksum"} \
            ${published_metadata:+"$published_metadata"}
    fi
    if [ "$lock_acquired" -eq 1 ]; then
        rmdir "$lock_path" 2>/dev/null || true
    fi
    return "$status"
}
on_signal() {
    signal_status="$1"
    trap - EXIT HUP INT TERM
    if [ "$replacement_started" -eq 1 ] && [ "$replacement_committed" -ne 1 ]; then
        recovery_attempted=1
        printf 'Signal received during local database replacement; attempting recovery from safety dump %s.\n' \
            "$safety_dump_path" >&2
        if recreate_local_database && restore_local_dump "$safety_dump_path"; then
            printf 'Local database recovery from safety dump succeeded after signal interruption.\n' >&2
        else
            printf 'Local database recovery from safety dump failed after signal interruption.\n' >&2
        fi
    fi
    cleanup
    exit "$signal_status"
}
trap cleanup EXIT
trap 'on_signal 129' HUP
trap 'on_signal 130' INT
trap 'on_signal 143' TERM

config_tmp="$(mktemp "${TMPDIR:-/tmp}/clone-staging-db.config.XXXXXX")" || fail "Could not create temporary Compose configuration"
resolved_tmp="$(mktemp "${TMPDIR:-/tmp}/clone-staging-db.resolved.XXXXXX")" || fail "Could not create temporary Compose resolution"

if ! docker compose config --format json > "$config_tmp"; then
    fail "Could not render the local Docker Compose configuration"
fi

if ! python3 - "$config_tmp" > "$resolved_tmp" <<'PY'
import json
import sys


def environment(service):
    value = service.get("environment", {})
    if isinstance(value, dict):
        return {key: str(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        result = {}
        for item in value:
            if not isinstance(item, str) or "=" not in item:
                continue
            key, item_value = item.split("=", 1)
            result[key] = item_value
        return result
    return {}


try:
    with open(sys.argv[1], encoding="utf-8") as config_file:
        config = json.load(config_file)
except json.JSONDecodeError as error:
    raise SystemExit("Rendered Compose configuration must be valid JSON") from error

if not isinstance(config, dict):
    raise SystemExit("Rendered Compose configuration must be an object")
services = config.get("services")
if not isinstance(services, dict):
    raise SystemExit("Rendered Compose configuration must define services")
db = services.get("db")
web = services.get("web")
project = config.get("name")

if not isinstance(project, str) or not project:
    raise SystemExit("Rendered Compose configuration has no local project name")
if not isinstance(db, dict):
    raise SystemExit("Rendered Compose configuration has no usable db service")
if not isinstance(web, dict):
    raise SystemExit("Rendered Compose configuration has no usable web service")

db_environment = environment(db)
web_environment = environment(web)
if web_environment.get("DB_HOST") != "db":
    raise SystemExit("Rendered application DB_HOST must resolve to the local db service")

database = db_environment.get("POSTGRES_DB")
user = db_environment.get("POSTGRES_USER")
image = db.get("image")
if not database or not user:
    raise SystemExit("Rendered db service must define POSTGRES_DB and POSTGRES_USER")
if not isinstance(image, str) or not image:
    raise SystemExit("Rendered db service must define an image")

for value in (project, database, user, image):
    if any(ord(character) <= 0x1F or ord(character) == 0x7F for character in value):
        raise SystemExit("Rendered Compose values contain unsupported control characters")

print(project, database, user, image, sep="\t")
PY
then
    fail "Unsafe rendered local Docker Compose database configuration"
fi

tab="$(printf '\t')"
IFS="$tab" read -r compose_project local_database local_user local_db_image < "$resolved_tmp"
[ -n "$compose_project" ] && [ -n "$local_database" ] && [ -n "$local_user" ] && [ -n "$local_db_image" ] || \
    fail "Could not resolve the local Compose project and database"

mkdir -p -m 700 "$backup_dir" || fail "Could not create backup directory"
chmod 700 "$backup_dir" || fail "Could not secure backup directory"
lock_root="$backup_dir/.locks"
mkdir -p -m 700 "$lock_root" || fail "Could not create clone lock directory"
chmod 700 "$lock_root" || fail "Could not secure clone lock directory"
lock_key="$(
    python3 - "$compose_project" "$local_database" <<'PY'
import hashlib
import sys

digest = hashlib.sha256()
digest.update(sys.argv[1].encode("utf-8"))
digest.update(b"\0")
digest.update(sys.argv[2].encode("utf-8"))
print(digest.hexdigest())
PY
)" || fail "Could not derive the local clone lock"
lock_path="$lock_root/$lock_key.lock"
if mkdir -m 700 "$lock_path" 2>/dev/null; then
    lock_acquired=1
elif [ -d "$lock_path" ]; then
    fail "Another clone may be active, or a stale clone lock remains at $lock_path. Verify no clone is running, then remove only that exact lock with: rmdir $lock_path"
else
    fail "Could not acquire the local database clone lock"
fi

if [ -t 0 ]; then
    printf 'This will replace local Compose project %s database %s owned by %s. Type yes to continue: ' \
        "$compose_project" "$local_database" "$local_user" >&2
    IFS= read -r confirmation || fail "Confirmation declined"
    [ "$confirmation" = yes ] || fail "Confirmation declined"
elif [ "${CONFIRM_REPLACE_LOCAL_DB:-}" != yes ]; then
    fail "Non-interactive execution requires CONFIRM_REPLACE_LOCAL_DB=yes"
fi

postgres_major() {
    printf '%s\n' "$1" | sed -n 's/.*PostgreSQL[^0-9]*\([0-9][0-9]*\).*/\1/p' | head -n 1
}

shell_quote() {
    python3 - "$1" <<'PY'
import shlex
import sys

print(shlex.quote(sys.argv[1]))
PY
}

sql_identifier() {
    python3 - "$1" <<'PY'
import sys

print('"' + sys.argv[1].replace('"', '""') + '"')
PY
}

sql_literal() {
    python3 - "$1" <<'PY'
import sys

print("'" + sys.argv[1].replace("'", "''") + "'")
PY
}

require_postgres_16() {
    label="$1"
    version="$2"
    major="$(postgres_major "$version")"
    [ "$major" = 16 ] || fail "$label PostgreSQL must be major 16 (found: $version)"
}

reject_control_characters() {
    value="$1"
    label="$2"
    if ! python3 - "$value" <<'PY'
import sys

raise SystemExit(any(ord(character) <= 0x1F or ord(character) == 0x7F for character in sys.argv[1]))
PY
    then
        fail "$label contains unsupported control characters"
    fi
}

available_kb="$(df -Pk "$backup_dir" | awk 'NR == 2 { print $4 }')"
case "$available_kb" in
    ''|*[!0-9]*) fail "Could not determine free space for backup directory" ;;
esac
[ "$available_kb" -ge 1024 ] || fail "Not enough free space in backup directory"

local_version="$(docker run --rm --network none "$local_db_image" postgres --version)" || \
    fail "Could not determine local PostgreSQL version"
require_postgres_16 "Local" "$local_version"

timestamp="$(date -u '+%Y%m%dT%H%M%SZ')" || fail "Could not create backup timestamp"
if [ "$dump_source" = retained ]; then
    [ -f "$staging_dump_file" ] && [ -r "$staging_dump_file" ] || \
        fail "STAGING_DUMP_FILE must name a readable regular dump file"
    retained_checksum_path="$staging_dump_file.sha256"
    [ -f "$retained_checksum_path" ] && [ -r "$retained_checksum_path" ] || \
        fail "STAGING_DUMP_FILE checksum is missing or unreadable"
    if ! python3 - "$staging_dump_file" "$retained_checksum_path" <<'PY'
import hashlib
import os
import re
import sys

dump_path, checksum_path = sys.argv[1:]
dump_name = os.path.basename(dump_path)
if not dump_name or dump_name in {".", ".."}:
    raise SystemExit(1)
if any(ord(character) <= 0x1F or ord(character) == 0x7F for character in dump_name):
    raise SystemExit(1)

try:
    dump_name_bytes = dump_name.encode("utf-8")
    checksum_entry = open(checksum_path, "rb").read()
except (OSError, UnicodeEncodeError):
    raise SystemExit(1) from None

entry = re.fullmatch(rb"([0-9a-f]{64})  " + re.escape(dump_name_bytes) + rb"\n?", checksum_entry)
if entry is None:
    raise SystemExit(1)

digest = hashlib.sha256()
try:
    with open(dump_path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
except OSError:
    raise SystemExit(1) from None

raise SystemExit(digest.hexdigest().encode("ascii") != entry.group(1))
PY
    then
        fail "STAGING_DUMP_FILE checksum validation failed"
    fi

    dump_path="$staging_dump_file"
    toc_tmp="$(mktemp "$backup_dir/$timestamp.retained.toc.XXXXXX")" || \
        fail "Could not create retained dump table-of-contents file"
    if ! docker run --rm --network none --volume "$dump_path:/dump:ro" "$local_db_image" \
        pg_restore --list /dump > "$toc_tmp"; then
        fail "Could not validate staging dump"
    fi
    rm -f "$toc_tmp"
    toc_tmp=""
else
    remote_compose='docker compose --project-name photo-prjct-staging -f /opt/photo-prjct/docker-compose.prod.yml'
    remote_version="$(ssh "$STAGING_SSH_TARGET" "$remote_compose exec -T db postgres --version")" || \
        fail "Could not determine staging PostgreSQL version"
    require_postgres_16 "Staging" "$remote_version"

    source_database="$(ssh "$STAGING_SSH_TARGET" "$remote_compose exec -T db printenv POSTGRES_DB")" || \
        fail "Could not determine staging database name"
    [ -n "$source_database" ] || fail "Staging database name is empty"
    source_user="$(ssh "$STAGING_SSH_TARGET" "$remote_compose exec -T db printenv POSTGRES_USER")" || \
        fail "Could not determine staging database user"
    [ -n "$source_user" ] || fail "Staging database user is empty"
    reject_control_characters "$source_database" "Staging database name"
    reject_control_characters "$source_user" "Staging database user"
    source_database_quoted="$(shell_quote "$source_database")" || fail "Could not safely quote staging database name"
    source_user_quoted="$(shell_quote "$source_user")" || fail "Could not safely quote staging database user"

    dump_name="$timestamp.dump"
    dump_path="$backup_dir/$dump_name"
    checksum_path="$dump_path.sha256"
    metadata_path="$backup_dir/$timestamp.metadata"
    [ ! -e "$dump_path" ] && [ ! -e "$checksum_path" ] && [ ! -e "$metadata_path" ] || \
        fail "Backup output already exists for timestamp $timestamp"

    dump_tmp="$(mktemp "$backup_dir/$timestamp.dump.XXXXXX")" || fail "Could not create temporary dump file"
    if ! ssh "$STAGING_SSH_TARGET" \
        "$remote_compose exec -T db pg_dump --format=custom --no-owner --no-acl --username=$source_user_quoted --dbname=$source_database_quoted" \
        > "$dump_tmp"; then
        fail "Staging dump stream failed"
    fi
    [ -s "$dump_tmp" ] || fail "Staging dump was empty"

    toc_tmp="$(mktemp "$backup_dir/$timestamp.toc.XXXXXX")" || fail "Could not create temporary dump table-of-contents file"
    if ! docker run --rm --network none --volume "$dump_tmp:/dump:ro" "$local_db_image" pg_restore --list /dump > "$toc_tmp"; then
        fail "Could not validate staging dump"
    fi

    toc_entries="$(awk 'END { print NR + 0 }' "$toc_tmp")"
    toc_sha256="$(python3 - "$toc_tmp" <<'PY'
import hashlib
import sys

digest = hashlib.sha256()
with open(sys.argv[1], "rb") as source:
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
print(digest.hexdigest())
PY
)" || fail "Could not summarize staging dump table of contents"

    checksum_tmp="$(mktemp "$backup_dir/$timestamp.dump.sha256.XXXXXX")" || fail "Could not create temporary dump checksum"
    if ! python3 - "$dump_tmp" "$checksum_tmp" "$dump_name" <<'PY'
import hashlib
import sys

source_path, output_path, archive_name = sys.argv[1:]
digest = hashlib.sha256()
with open(source_path, "rb") as source:
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
with open(output_path, "w", encoding="utf-8") as output:
    output.write(f"{digest.hexdigest()}  {archive_name}\n")
PY
    then
        fail "Could not checksum staging dump"
    fi

    metadata_tmp="$(mktemp "$backup_dir/$timestamp.metadata.XXXXXX")" || fail "Could not create temporary dump metadata"
    staging_host_label="${STAGING_SSH_TARGET#*@}"
    if ! {
        printf 'timestamp=%s\n' "$timestamp"
        printf 'staging_host=%s\n' "$staging_host_label"
        printf 'postgresql_major=16\n'
        printf 'database=%s\n' "$source_database"
        printf 'dump_toc_entries=%s\n' "$toc_entries"
        printf 'dump_toc_sha256=%s\n' "$toc_sha256"
    } > "$metadata_tmp"; then
        fail "Could not write staging dump metadata"
    fi
    chmod 600 "$dump_tmp" "$checksum_tmp" "$metadata_tmp" || fail "Could not secure staging dump artifacts"

    published_checksum="$checksum_path"
    mv "$checksum_tmp" "$checksum_path" || fail "Could not publish staging dump checksum"
    checksum_tmp=""
    published_metadata="$metadata_path"
    mv "$metadata_tmp" "$metadata_path" || fail "Could not publish staging dump metadata"
    metadata_tmp=""
    rm -f "$toc_tmp"
    toc_tmp=""
    published_dump="$dump_path"
    mv "$dump_tmp" "$dump_path" || fail "Could not publish staging dump"
    dump_tmp=""
    publication_complete=1
fi

start_and_wait_for_local_db() {
    if ! docker compose up -d db; then
        fail "Could not start the local db service"
    fi

    attempts=0
    while ! docker compose exec -T db pg_isready --username="$local_user" --dbname=postgres >/dev/null; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 30 ]; then
            fail "Local db service did not become ready"
        fi
        sleep 1
    done
}

recreate_local_database() {
    target_identifier="$(sql_identifier "$local_database")" || return 1
    owner_identifier="$(sql_identifier "$local_user")" || return 1
    target_literal="$(sql_literal "$local_database")" || return 1

    if ! docker compose exec -T db psql --username="$local_user" --dbname=postgres -v ON_ERROR_STOP=1 \
        -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $target_literal AND pid <> pg_backend_pid();"; then
        return 1
    fi
    replacement_started=1
    if ! docker compose exec -T db psql --username="$local_user" --dbname=postgres -v ON_ERROR_STOP=1 \
        -c "DROP DATABASE IF EXISTS $target_identifier"; then
        return 1
    fi
    docker compose exec -T db psql --username="$local_user" --dbname=postgres -v ON_ERROR_STOP=1 \
        -c "CREATE DATABASE $target_identifier OWNER $owner_identifier"
}

restore_local_dump() {
    archive_path="$1"
    docker compose exec -T db pg_restore --exit-on-error --clean --if-exists --no-owner --no-acl \
        --username="$local_user" --dbname="$local_database" < "$archive_path"
}

attempt_safety_recovery() {
    failure_reason="$1"
    recovery_attempted=1
    printf '%s; attempting local recovery from safety dump %s.\n' \
        "$failure_reason" "$safety_dump_path" >&2
    if recreate_local_database && restore_local_dump "$safety_dump_path"; then
        printf 'Local database recovery from safety dump succeeded; clone remains failed.\n' >&2
    else
        printf 'Local database recovery from safety dump failed; both dumps were retained for manual recovery.\n' >&2
    fi
    return 1
}

validate_restored_database() {
    migration_validation_code='
from django.db import connection
from django.db.migrations.loader import MigrationLoader

try:
    with connection.cursor() as cursor:
        cursor.execute("SELECT app, name FROM django_migrations")
        applied_migrations = set(cursor.fetchall())
except Exception:
    raise SystemExit(12)

if not applied_migrations:
    raise SystemExit(10)
if any(not isinstance(app, str) or not app or not isinstance(name, str) or not name for app, name in applied_migrations):
    raise SystemExit(12)

try:
    loader = MigrationLoader(connection, ignore_no_migrations=True)
except Exception:
    raise SystemExit(12)

if applied_migrations - set(loader.disk_migrations):
    raise SystemExit(11)
'

    if docker compose run --rm --no-deps --entrypoint python web \
        manage.py shell -c "$migration_validation_code" >/dev/null 2>&1; then
        :
    else
        validation_status=$?
        case "$validation_status" in
            10)
                fail "Restored database has no applied Django migrations; confirm the staging dump before developing migrations."
                ;;
            11)
                fail "Restored database contains applied Django migration names absent from this checkout; update the branch before developing migrations."
                ;;
            *)
                fail "Could not validate django_migrations in the restored local database; confirm the database is reachable and restore again if needed."
                ;;
        esac
    fi

    if migration_plan="$(docker compose run --rm --no-deps --entrypoint python web \
        manage.py showmigrations --plan 2>/dev/null)"; then
        :
    else
        fail "Could not inspect the restored database migration plan; retained dumps are available for diagnosis."
    fi
    case "$migration_plan" in
        *'[ ]'*)
            fail "Restored database and checkout are not a clean migration baseline; reconcile unapplied checkout migrations before continuing."
            ;;
    esac

    if ! docker compose run --rm --no-deps --entrypoint python web \
        manage.py makemigrations --check --dry-run >/dev/null 2>&1; then
        fail "Migration model validation failed; inspect the one-off container output or run the command manually."
    fi
}

start_and_wait_for_local_db

if web_running_services="$(docker compose ps --status running --services web 2>/dev/null)"; then
    :
else
    fail "Could not determine whether the local web service is running"
fi
if [ "$web_running_services" = web ]; then
    if ! docker compose stop web; then
        fail "Could not stop the running local web service; local database replacement was not started"
    fi
elif [ -n "$web_running_services" ]; then
    fail "Could not safely determine whether the local web service is running"
fi

safety_name="$timestamp.local-safety.dump"
safety_dump_path="$backup_dir/$safety_name"
[ ! -e "$safety_dump_path" ] || fail "Local safety dump output already exists for timestamp $timestamp"
safety_tmp="$(mktemp "$backup_dir/$safety_name.XXXXXX")" || fail "Could not create temporary local safety dump file"
if ! docker compose exec -T db pg_dump --format=custom --no-owner --no-acl \
    --username="$local_user" --dbname="$local_database" > "$safety_tmp"; then
    fail "Could not create local safety dump"
fi
[ -s "$safety_tmp" ] || fail "Local safety dump was empty"
chmod 600 "$safety_tmp" || fail "Could not secure local safety dump"
mv "$safety_tmp" "$safety_dump_path" || fail "Could not publish local safety dump"
safety_tmp=""

toc_tmp="$(mktemp "$backup_dir/$timestamp.local-safety.toc.XXXXXX")" || \
    fail "Could not create local safety dump table-of-contents file"
if ! docker run --rm --network none --volume "$safety_dump_path:/dump:ro" "$local_db_image" \
    pg_restore --list /dump > "$toc_tmp"; then
    fail "Could not validate local safety dump"
fi
rm -f "$toc_tmp"
toc_tmp=""

if ! recreate_local_database; then
    if [ "$replacement_started" -eq 1 ]; then
        attempt_safety_recovery "Local database recreation failed after DROP began" || true
        exit 1
    fi
    fail "Could not terminate local database connections before DROP"
fi

if ! restore_local_dump "$dump_path"; then
    attempt_safety_recovery "Staging restore failed" || true
    exit 1
fi

replacement_committed=1
validate_restored_database

printf 'Local database replacement completed using validated staging dump %s.\n' "$dump_path" >&2
printf 'Local web service remains stopped; restart it explicitly after validation with: docker compose up -d web\n' >&2
