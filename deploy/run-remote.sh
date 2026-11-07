#!/bin/sh
set -eu

if [ "${1:-}" = deploy ]; then
    exec python3 "$(dirname "$0")/run-single-vm.py"
fi

# This helper is executed only by run-with-environment-secrets.py. Secret values
# remain in FINDME_ENV_FILE and are never expanded into arguments or logs.

umask 077
REPOSITORY_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
temporary_root=''
command_output=''
active_child=''

stop_active_child() {
    signal_name=$1
    [ -n "$active_child" ] || return 0
    child=$active_child
    (
        sleep 0.5
        kill -TERM "$child" 2>/dev/null || exit 0
        sleep 0.5
        kill -KILL "$child" 2>/dev/null || true
    ) &
    shutdown_guard=$!
    kill -"$signal_name" "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
    active_child=''
    kill -TERM "$shutdown_guard" 2>/dev/null || true
    wait "$shutdown_guard" 2>/dev/null || true
}

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    stop_active_child TERM
    if [ -n "$temporary_root" ]; then
        rm -rf -- "$temporary_root"
    fi
    exit "$status"
}

relay_signal() {
    signal_name=$1
    status=$2
    trap - HUP INT TERM
    stop_active_child "$signal_name"
    exit "$status"
}

trap cleanup EXIT
trap 'relay_signal HUP 129' HUP
trap 'relay_signal INT 130' INT
trap 'relay_signal TERM 143' TERM

fail() {
    printf '[remote] stage=%s status=error code=%s\n' "$1" "$2" >&2
    exit 2
}

run_quietly() {
    stage=$1
    code=$2
    shift 2
    python3 -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); signal.signal(signal.SIGQUIT, signal.SIG_DFL); os.execvp(sys.argv[1], sys.argv[1:])' "$@" >"$command_output" 2>&1 &
    active_child=$!
    if wait "$active_child"; then
        active_child=''
    else
        active_child=''
        fail "$stage" "$code"
    fi
}

run_public_monitor() {
    for name in MONITOR_TARGET MONITOR_CHECK YANDEX_CLOUD_FOLDER_ID; do
        printenv "$name" >/dev/null 2>&1 || fail monitor missing_configuration
    done
    monitor_runner=$temporary_root/public-monitor.py
    cat >"$monitor_runner" <<'PY'
import os
import importlib.util
import sys
from pathlib import Path


def decode(encoded: str) -> str:
    if len(encoded) < 2 or encoded[0] != '"' or encoded[-1] != '"':
        raise ValueError
    result: list[str] = []
    index = 1
    while index < len(encoded) - 1:
        character = encoded[index]
        if character != '\\':
            result.append(character)
            index += 1
            continue
        index += 1
        if index >= len(encoded) - 1:
            raise ValueError
        escaped = encoded[index]
        result.append({'n': '\n', 'r': '\r', 't': '\t'}.get(escaped, escaped))
        index += 1
    return ''.join(result)


environment_path = Path(sys.argv[1])
repository_root = Path(sys.argv[2])
api_key = None
for line in environment_path.read_text(encoding='utf-8').splitlines():
    name, separator, encoded = line.partition('=')
    if separator and name == 'YANDEX_MONITORING_API_KEY':
        api_key = decode(encoded)
        break
if not api_key:
    raise SystemExit(2)
module_path = repository_root / 'scripts/monitor_public_health.py'
spec = importlib.util.spec_from_file_location('findme_monitor_public_health', module_path)
if spec is None or spec.loader is None:
    raise SystemExit(2)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
config = module.ProbeConfig(
    target=os.environ['MONITOR_TARGET'],
    folder_id=os.environ['YANDEX_CLOUD_FOLDER_ID'],    check_name=os.environ['MONITOR_CHECK'],
    api_key=api_key,
)
raise SystemExit(module.run_probe(config))
PY
    chmod 600 "$monitor_runner"
    run_quietly monitor monitor_failed python3 "$monitor_runner" "$FINDME_ENV_FILE" "$REPOSITORY_ROOT"
    printf '[remote] stage=public-monitor status=ok\n'
}

[ "$#" = 1 ] || fail arguments invalid_arguments
[ "$1" = public-monitor ] || fail arguments unknown_operation
[ -n "${FINDME_ENV_FILE:-}" ] || fail input missing_environment_file
temporary_root=$(mktemp -d "${TMPDIR:-/tmp}/findme-remote.XXXXXX") || fail temporary create_failed
command_output=$temporary_root/command-output
run_public_monitor
