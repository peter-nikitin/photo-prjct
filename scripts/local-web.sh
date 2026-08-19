#!/bin/sh

set -eu
umask 077

fail() {
    printf '%s\n' "[local-web] stage=preflight status=error code=$1" >&2
    exit 1
}

script_dir=$(CDPATH= cd -- "${0%/*}" && pwd -P)
repository_root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
python_bin="$repository_root/.venv/bin/python"
temporary_root=""

cleanup() {
    if [ -z "$temporary_root" ]; then
        return 0
    fi
    if ! rm -rf "$temporary_root" >/dev/null 2>&1; then
        printf '%s\n' \
            "[local-web] stage=cleanup status=error code=cleanup_failed retained_path=$temporary_root" \
            >&2
        return 1
    fi
    temporary_root=""
    return 0
}

finish() {
    status=$1
    ready=$2
    trap - EXIT HUP INT TERM
    if ! cleanup; then
        exit 1
    fi
    if [ "$ready" -eq 1 ]; then
        printf '%s\n' '[local-web] stage=launch status=ready'
    fi
    exit "$status"
}

on_exit() {
    status=$?
    finish "$status" 0
}

on_signal() {
    status=$1
    trap - EXIT HUP INT TERM
    if ! cleanup; then
        exit 1
    fi
    exit "$status"
}

case "$#" in
    0) resolved=0 ;;
    1)
        [ "$1" = --resolved ] || fail arguments_invalid
        resolved=1
        ;;
    *) fail arguments_invalid ;;
esac

if ! git -C "$repository_root" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
    [ "$(git -C "$repository_root" rev-parse --show-toplevel 2>/dev/null || true)" != "$repository_root" ]; then
    fail repository_invalid
fi

[ -x "$python_bin" ] || fail python_missing
command -v docker >/dev/null 2>&1 || fail docker_missing
command -v yc >/dev/null 2>&1 || fail yc_missing

context_endpoint_json=$(docker context inspect --format '{{json .Endpoints.docker.Host}}' 2>/dev/null) ||
    fail docker_endpoint_invalid
context_endpoint=$("$python_bin" - "$context_endpoint_json" <<'PY'
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
) || fail docker_endpoint_invalid

if [ -n "${DOCKER_CONTEXT:-}" ]; then
    effective_endpoint=$context_endpoint
elif [ -n "${DOCKER_HOST:-}" ]; then
    effective_endpoint=$DOCKER_HOST
else
    effective_endpoint=$context_endpoint
fi

if ! "$python_bin" - "$effective_endpoint" <<'PY'
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
    fail docker_endpoint_invalid
fi

docker compose version >/dev/null 2>&1 || fail docker_compose_missing

if [ "$resolved" -eq 0 ]; then
    exec "$python_bin" "$repository_root/scripts/run-with-environment-secrets.py" \
        --consumer local-web --identity yc -- \
        "$repository_root/scripts/local-web.sh" --resolved
fi

[ -n "${FINDME_ENV_FILE:-}" ] && [ -f "$FINDME_ENV_FILE" ] || fail resolved_environment_missing

temporary_root=$(mktemp -d "${TMPDIR:-/tmp}/findme-local-web.XXXXXX") || fail temporary_file_failed
trap on_exit EXIT
trap 'on_signal 129' HUP
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
temporary_root=$(CDPATH= cd -- "$temporary_root" && pwd -P) || fail temporary_file_failed

overrides_path="$temporary_root/overrides.env"
overlay_path="$temporary_root/compose.yml"
governed_path="$temporary_root/governed-names"

if ! "$python_bin" - "$repository_root/deploy/environment-secrets.json" "$overrides_path" \
    "$overlay_path" "$governed_path" <<'PY'
import json
import re
import sys
from pathlib import Path

name = re.compile(r"[A-Z][A-Z0-9_]*\Z")


def encode(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace("$", "\\$")
    )
    return f'"{escaped}"'


try:
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    entries = manifest["entries"]
    projection = manifest["consumers"]["local-web"]
    overrides = manifest["local_overrides"]
    by_key = {entry["key"]: entry for entry in entries}
    targets = [by_key[key]["target"] for key in projection]
    governed = [entry["key"] for entry in entries] + [entry["target"] for entry in entries]
    governed.extend(overrides)
    if (
        not isinstance(overrides, dict)
        or not all(isinstance(key, str) and name.fullmatch(key) and isinstance(value, str)
                   for key, value in overrides.items())
        or not all(isinstance(key, str) and name.fullmatch(key) for key in targets + governed)
        or len(targets) != len(set(targets))
    ):
        raise ValueError
except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
    raise SystemExit(1) from None

Path(sys.argv[2]).write_text(
    "".join(f"{key}={encode(value)}\n" for key, value in overrides.items()), encoding="utf-8"
)
environment_names = targets + list(overrides)
Path(sys.argv[3]).write_text(
    "services:\n"
    "  web:\n"
    "    env_file: !reset []\n"
    "    environment:\n"
    + "".join(f"      {key}: ${{{key}}}\n" for key in environment_names)
    + "    ports:\n"
    + '      - "${WEB_BIND_ADDRESS:?WEB_BIND_ADDRESS must be set}:8000:8000"\n',
    encoding="utf-8",
)
Path(sys.argv[4]).write_text("".join(f"{key}\n" for key in set(governed)), encoding="utf-8")
PY
then
    fail manifest_invalid
fi

chmod 600 "$overrides_path" "$overlay_path" "$governed_path" || fail temporary_file_failed

while IFS= read -r governed_name; do
    unset "$governed_name"
done < "$governed_path"

printf '%s\n' '[local-web] warning=local-capable-process'
cd "$repository_root"
if ! docker compose --env-file "$FINDME_ENV_FILE" --env-file "$overrides_path" \
    -f "$repository_root/docker-compose.yml" -f "$overlay_path" up -d db web >/dev/null 2>&1; then
    printf '%s\n' '[local-web] stage=launch status=error code=compose_failed' >&2
    finish 1 0
fi
finish 0 1
