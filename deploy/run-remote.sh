#!/bin/sh
set -eu

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

file_mode() {
    if mode=$(stat -f '%Lp' "$1" 2>/dev/null); then
        case "$mode" in
            [0-7][0-7][0-7]|[0-7][0-7][0-7][0-7])
                printf '%s\n' "$mode"
                return
                ;;
        esac
    fi
    stat -c '%a' "$1"
}

require_private_file() {
    [ -f "$1" ] || fail "$2" "$3"
    [ "$(file_mode "$1")" = 600 ] || fail "$2" "$4"
}

require_paused_observability_identifiers() {
    release_sha=${RELEASE_SHA:-}
    case "$release_sha" in
        ''|*[!0-9a-f]*) fail observability invalid_release_sha ;;
    esac
    [ "${#release_sha}" -eq 40 ] || fail observability invalid_release_sha

    source_manifest_sha256=${OBSERVABILITY_SOURCE_MANIFEST_SHA256:-}
    case "$source_manifest_sha256" in
        ''|*[!0-9a-f]*) fail observability invalid_manifest_sha256 ;;
    esac
    [ "${#source_manifest_sha256}" -eq 64 ] || fail observability invalid_manifest_sha256
}

decode_value() {
    python3 - "$FINDME_ENV_FILE" "$1" <<'PY'
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


requested = sys.argv[2]
for line in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
    name, separator, encoded = line.partition('=')
    if separator and name == requested:
        print(decode(encoded))
        raise SystemExit(0)
raise SystemExit(1)
PY
}

write_remote_environment() {
    source_environment=$1
    destination_environment=$2
    shift 2
    python3 - "$source_environment" "$destination_environment" "$@" <<'PY'
import os
import sys
from pathlib import Path


def encode(value: str) -> str:
    return '"' + (
        value.replace('\\', '\\\\')
        .replace('"', '\\"')
        .replace('\n', '\\n')
        .replace('\r', '\\r')
        .replace('\t', '\\t')
        .replace('$', '\\$')
    ) + '"'


source = Path(sys.argv[1]).read_text(encoding='utf-8').splitlines()
destination = Path(sys.argv[2])
names = sys.argv[3:]
if any(name not in os.environ for name in names):
    raise SystemExit(1)
with destination.open('w', encoding='utf-8') as stream:
    for line in source:
        if not line.startswith('VM_SSH_KEY_FILE='):
            stream.write(line + '\n')
    for name in names:
        stream.write(f'{name}={encode(os.environ[name])}\n')
os.chmod(destination, 0o600)
PY
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

run_quietly_with_stdin() {
    stage=$1
    code=$2
    input_file=$3
    shift 3
    python3 -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); signal.signal(signal.SIGQUIT, signal.SIG_DFL); os.execvp(sys.argv[1], sys.argv[1:])' "$@" <"$input_file" >"$command_output" 2>&1 &
    active_child=$!
    if wait "$active_child"; then
        active_child=''
    else
        active_child=''
        fail "$stage" "$code"
    fi
}

relay_deployment_markers() {
    awk '
        /^DEPLOY_PHASE=(validate|snapshot|candidate-pull|private-media-preflight|migration-preflight|projection-preflight|observability-preflight|observability-reconcile|certificate|compose-reconcile|local-health|worker-health|public-health|observability-verify|commit) elapsed_seconds=[0-9]+$/ {
            print
            next
        }
        /^DEPLOY_RESULT=(success|failure) phase=(validate|snapshot|candidate-pull|private-media-preflight|migration-preflight|projection-preflight|observability-preflight|observability-reconcile|certificate|compose-reconcile|local-health|worker-health|public-health|observability-verify|commit) rollback=(not-needed|succeeded|failed) elapsed_seconds=[0-9]+$/ {
            print
        }
    ' "$command_output"
}

quote_for_remote_shell() {
    printf '%s' "$1" | sed "s/'/'\\\\''/g"
}

REMOTE_PROGRAM=$(cat <<'PY'
import os
import sys


def decode(encoded):
    if len(encoded) < 2 or encoded[0] != '"' or encoded[-1] != '"':
        raise ValueError
    result = []
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


environment = {'PATH': os.environ.get('PATH', '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')}
for line in sys.stdin:
    name, separator, encoded = line.rstrip('\n').partition('=')
    if not separator or not name or name == 'VM_SSH_KEY_FILE':
        raise SystemExit(2)
    environment[name] = decode(encoded)
mode = sys.argv[1]
benchmark_command = r'''set -eu
cd /opt/photo-prjct

worker_identity="$(sed -n 's/^PHOTO_WORKER_PROCESSOR_IDENTITIES=//p' .env | head -n 1)"
test "$worker_identity" = '3/face_embedding_benchmark/1'
worker_replicas="$(sed -n 's/^PHOTO_WORKER_REPLICAS=//p' .env | head -n 1)"
preview_enabled="$(sed -n 's/^PHOTO_PROCESSING_PREVIEW_ENABLED=//p' .env | head -n 1)"
test "$preview_enabled" = False

run_web() {
  docker compose --project-name photo-prjct \
    --env-file .env \
    -f docker-compose.deployment.yml \
    -f docker-compose.https.yml \
    exec -T -e BENCHMARK_SOURCE_RUN_UUID web python manage.py "$@"
}

require_uuid() {
  printf '%s' "$1" | grep -Eq '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
}

case "$BENCHMARK_OPERATION" in
  baseline)
    test "$worker_replicas" = 1
    test -n "$BENCHMARK_EVENT_SLUG"
    benchmark_run_id="$(run_web run_face_embedding_benchmark \
      --event "$BENCHMARK_EVENT_SLUG" \
      --limit 114 \
      --label deployment-baseline-one-replica)"
    require_uuid "$benchmark_run_id"
    printf 'BENCHMARK_RUN_ID=%s\n' "$benchmark_run_id"
    ;;
  replay)
    test "$worker_replicas" = 2
    require_uuid "$BENCHMARK_SOURCE_RUN_UUID"
    benchmark_run_id="$(run_web run_face_embedding_benchmark \
      --source-run "$BENCHMARK_SOURCE_RUN_UUID" \
      --label deployment-replay-two-replicas)"
    require_uuid "$benchmark_run_id"
    printf 'BENCHMARK_RUN_ID=%s\n' "$benchmark_run_id"
    ;;
  report)
    require_uuid "$BENCHMARK_SOURCE_RUN_UUID"
    export BENCHMARK_SOURCE_RUN_UUID
    run_web shell -c '
from collections import Counter, defaultdict
import json
import os
from django.core.management.base import CommandError
from processing.models import EventProcessingRun, ProcessingAttempt, ProcessingJob

run = EventProcessingRun.objects.filter(pk=os.environ["BENCHMARK_SOURCE_RUN_UUID"]).first()
if run is None:
    raise CommandError("benchmark run does not exist")
if not (
    run.contract_version == 3
    and run.processor_type == "face_embedding_benchmark"
    and run.processor_version == 1
    and run.status == EventProcessingRun.Status.CLOSED
):
    raise CommandError("benchmark run must be closed")

jobs = list(ProcessingJob.objects.filter(run=run).only("id", "status", "created_at", "input_fingerprint"))
attempts = list(
    ProcessingAttempt.objects.filter(run=run).only(
        "job_id", "status", "error_code", "claimed_at", "terminal_at",
        "download_duration_ms", "compute_duration_ms", "total_duration_ms", "result",
    )
)
terminal_statuses = {
    ProcessingJob.Status.SUCCEEDED,
    ProcessingJob.Status.FAILED,
    ProcessingJob.Status.CANCELLED,
}
terminal_attempts = [attempt for attempt in attempts if attempt.terminal_at is not None]
attempts_by_job = defaultdict(list)
for attempt in attempts:
    attempts_by_job[attempt.job_id].append(attempt)

def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

def timing(result, name):
    value = result.get("timings", {}).get(name)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

creation_to_claim = []
for job in jobs:
    claims = [attempt.claimed_at for attempt in attempts_by_job[job.id] if attempt.claimed_at]
    if claims:
        creation_to_claim.append((min(claims) - job.created_at).total_seconds() * 1000)
measurements = {
    "download": [attempt.download_duration_ms for attempt in terminal_attempts if attempt.download_duration_ms is not None],
    "compute": [attempt.compute_duration_ms for attempt in terminal_attempts if attempt.compute_duration_ms is not None],
    "total": [attempt.total_duration_ms for attempt in terminal_attempts if attempt.total_duration_ms is not None],
    "model_load": [value for attempt in terminal_attempts if (value := timing(attempt.result, "model_load_ms")) is not None],
    "decode": [value for attempt in terminal_attempts if (value := timing(attempt.result, "decode_ms")) is not None],
    "detect": [value for attempt in terminal_attempts if (value := timing(attempt.result, "detect_ms")) is not None],
    "embed": [value for attempt in terminal_attempts if (value := timing(attempt.result, "embed_ms")) is not None],
}
size_buckets = Counter()
for job in jobs:
    size = job.input_fingerprint.get("original_size")
    if isinstance(size, int) and size < 1_000_000:
        size_buckets["<1MB"] += 1
    elif isinstance(size, int) and size < 5_000_000:
        size_buckets["1-5MB"] += 1
    elif isinstance(size, int) and size < 10_000_000:
        size_buckets["5-10MB"] += 1
    else:
        size_buckets[">=10MB"] += 1
wall_clock_ms = (run.closed_at - run.created_at).total_seconds() * 1000
output = {
    "cohort_size": len(jobs),
    "terminal_outcomes": dict(sorted(Counter(job.status for job in jobs if job.status in terminal_statuses).items())),
    "sample_counts": {
        "jobs": len(jobs),
        "terminal_attempts": len(terminal_attempts),
        "creation_to_claim_ms": len(creation_to_claim),
        **{f"{name}_ms": len(values) for name, values in measurements.items()},
    },
    "retried_job_count": sum(len(job_attempts) > 1 for job_attempts in attempts_by_job.values()),
    "expired_attempt_count": sum(attempt.status == ProcessingAttempt.Status.EXPIRED for attempt in attempts),
    "stale_attempt_count": sum(attempt.status == ProcessingAttempt.Status.STALE for attempt in attempts),
    "lease_loss_count": sum(attempt.error_code == "lease_not_current" for attempt in attempts),
    "terminal_error_code_counts": dict(sorted(Counter(attempt.error_code for attempt in terminal_attempts if attempt.error_code).items())),
    "representative_input_size_distribution": dict(sorted(size_buckets.items())),
    "representative_dimension_distribution": "not_collected_by_benchmark_contract",
    "wall_clock_ms": wall_clock_ms,
    "photos_per_minute": len([job for job in jobs if job.status in terminal_statuses]) / (wall_clock_ms / 60_000) if wall_clock_ms else None,
    "creation_to_claim_p50_ms": percentile(creation_to_claim, 0.5),
    "creation_to_claim_p95_ms": percentile(creation_to_claim, 0.95),
    **{
        f"{name}_{percentile_name}_ms": percentile(values, fraction)
        for name, values in measurements.items()
        for percentile_name, fraction in (("p50", 0.5), ("p95", 0.95))
    },
}
print(json.dumps(output, sort_keys=True))
'
    ;;
  *)
    exit 2
    ;;
esac'''
commands = {
    'deploy': 'DEPLOY_ROOT=/opt/photo-prjct COMPOSE_PROJECT_NAME=photo-prjct exec sh /opt/photo-prjct/deploy/apply-deployment.sh',
    'cutover-compose-identity': r'''set -eu
test "$COMPOSE_IDENTITY_CUTOVER_CONFIRMATION" = confirm-canonical-compose-identity-cutover
cd /opt/photo-prjct
DEPLOY_ROOT=/opt/photo-prjct COMPOSE_PROJECT_NAME=photo-prjct-staging \
  exec sh /opt/photo-prjct/deploy/cutover-compose-identity.sh \
    --confirm-canonical-compose-identity-cutover \
    --backup-dir /opt/photo-prjct/backups/compose-identity-cutover''',
    'private-storage': "cd /opt/photo-prjct; docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml -f docker-compose.https.yml exec -T -e PHOTO_UPLOAD_ENABLED=True web sh -lc 'python manage.py verify_private_upload_storage --confirm-real-storage --origin \"$PRIVATE_MEDIA_ALLOWED_ORIGINS\"'",
    'selfie-storage': "cd /opt/photo-prjct; docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml -f docker-compose.https.yml exec -T web python manage.py verify_selfie_search_storage --confirm-real-storage",
    'selfie-feedback-storage': "cd /opt/photo-prjct; test \"$(sed -n 's/^SELFIE_FEEDBACK_ENABLED=//p' .env | head -n 1)\" = False; docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml -f docker-compose.https.yml exec -T -e SELFIE_FEEDBACK_ENABLED=True -e SELFIE_FEEDBACK_S3_BUCKET -e SELFIE_FEEDBACK_S3_ACCESS_KEY_ID -e SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY -e SELFIE_FEEDBACK_KMS_KEY_ID web python manage.py verify_selfie_feedback_storage --confirm-real-storage",
    'configure-monitoring': 'exec sudo sh /opt/photo-prjct/deploy/configure-monitoring-agent.sh --folder-id "$YANDEX_CLOUD_FOLDER_ID"',
    'verify-deployed-image': r'''set -eu
test "$(cat /opt/photo-prjct/deployed-image)" = "$APP_IMAGE"
test "$(sed -n 's/^PHOTO_WORKER_PROCESSOR_IDENTITIES=//p' /opt/photo-prjct/.env | head -n 1)" = "$PHOTO_WORKER_PROCESSOR_IDENTITIES"''',
    'verify-paused-observability-release': r'''set -eu
case "$RELEASE_SHA" in
  ''|*[!0-9a-f]*) exit 2 ;;
esac
test "${#RELEASE_SHA}" -eq 40
case "$OBSERVABILITY_SOURCE_MANIFEST_SHA256" in
  ''|*[!0-9a-f]*) exit 2 ;;
esac
test "${#OBSERVABILITY_SOURCE_MANIFEST_SHA256}" -eq 64

staged_root="/opt/photo-prjct/privileged-observability-releases/$RELEASE_SHA"
cd "$staged_root"
test "$(cat observability-release-sha)" = "$RELEASE_SHA"
printf '%s  observability-source.sha256\n' "$OBSERVABILITY_SOURCE_MANIFEST_SHA256" | sha256sum --check -
sha256sum --check observability-source.sha256
cmp -s deploy/selfie-observability/root-helper.sh "/usr/local/sbin/findme-selfie-observability"
for name in journald.conf selfie-search-summary.service selfie-search-summary.timer run-daily-summary.sh summarize.py; do
  cmp -s "deploy/selfie-observability/$name" "/usr/local/lib/findme-selfie-observability-package/$name"
done
sudo -n /usr/local/sbin/findme-selfie-observability verify''',
    'face-embedding-benchmark': benchmark_command,
}
if mode not in commands:
    raise SystemExit(2)
os.execve('/bin/sh', ['sh', '-c', commands[mode]], environment)
PY
)

REMOTE_DEPLOYMENT_VALUES='
APP_IMAGE
WORKER_IMAGE
DEBUG
ALLOWED_HOSTS
GUNICORN_WORKERS
GUNICORN_THREADS
GUNICORN_TIMEOUT
GUNICORN_MAX_REQUESTS
GUNICORN_MAX_REQUESTS_JITTER
DB_NAME
DB_USER
PUBLIC_DOMAIN
PUBLIC_DOMAIN_ALIAS
MEDIA_STORAGE_BACKEND
MEDIA_S3_ENDPOINT_URL
MEDIA_S3_REGION
MEDIA_S3_PUBLIC_BUCKET
PHOTO_UPLOAD_ENABLED
PRIVATE_MEDIA_S3_BUCKET
PRIVATE_MEDIA_ALLOWED_ORIGINS
PHOTO_PROCESSING_ENABLED
PHOTO_PROCESSING_PREVIEW_ENABLED
PHOTO_PROCESSING_FACE_ENABLED
PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS
PHOTO_PROCESSING_MAX_REQUEST_BYTES
PHOTO_WORKER_BUILD
PHOTO_WORKER_LEASE_SECONDS
PHOTO_WORKER_PROCESSOR_IDENTITIES
PHOTO_WORKER_PROCESSOR_TYPES
PHOTO_WORKER_REPLICAS
SELFIE_SEARCH_MAX_UPLOAD_BYTES
SELFIE_SEARCH_MAX_PIXELS
SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS
SELFIE_SEARCH_EMBEDDING_MODEL
SELFIE_SEARCH_EMBEDDING_DIMENSIONS
SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD
SELFIE_SEARCH_TEMPORARY_PREFIX
SELFIE_SEARCH_LIFECYCLE_MAX_AGE_HOURS
SELFIE_FEEDBACK_ENABLED
SELFIE_FEEDBACK_S3_BUCKET
SELFIE_FEEDBACK_S3_ENDPOINT_URL
SELFIE_FEEDBACK_S3_REGION
SELFIE_FEEDBACK_KMS_KEY_ID
SELFIE_FEEDBACK_STORAGE_PREFLIGHT_CONFIRMED
COMMERCE_WORKER_ENABLED
COMMERCE_PUBLIC_ORIGIN
COMMERCE_PAYMENT_GATEWAY_FACTORY
COMMERCE_EMAIL_SENDER_FACTORY
COMMERCE_WORKER_FACTORY
COMMERCE_EMAIL_FROM_ADDRESS
COMMERCE_SUPPORT_CONTACT
COMMERCE_WORKER_HEALTH_MAX_READY_AGE_SECONDS
GHCR_USERNAME'

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
mode=$1
case "$mode" in
    cutover-compose-identity)
        remote_deployment_values="$REMOTE_DEPLOYMENT_VALUES COMPOSE_IDENTITY_CUTOVER_CONFIRMATION"
        ;;
    *)
        remote_deployment_values="$REMOTE_DEPLOYMENT_VALUES"
        ;;
esac

case "$mode" in
    deploy|cutover-compose-identity|private-storage|selfie-storage|selfie-feedback-storage|configure-monitoring|verify-deployed-image|verify-paused-observability-release|face-embedding-benchmark|public-monitor|remote-preflight|stage-paused-observability-release) ;;
    *) fail arguments unknown_operation ;;
esac

case "$mode" in
    stage-paused-observability-release|verify-paused-observability-release)
        require_paused_observability_identifiers
        ;;
esac

[ -n "${FINDME_ENV_FILE:-}" ] || fail input missing_environment_file
require_private_file "$FINDME_ENV_FILE" input environment_not_private environment_not_private
temporary_root=$(mktemp -d "${TMPDIR:-/tmp}/findme-remote.XXXXXX") || fail temporary create_failed
command_output=$temporary_root/command-output

if [ "$mode" = public-monitor ]; then
    run_public_monitor
    exit 0
fi

[ -n "${VM_HOST:-}" ] || fail input missing_host
[ -n "${VM_USER:-}" ] || fail input missing_user
[ -n "${VM_SSH_KNOWN_HOSTS:-}" ] || fail input missing_known_hosts
key_file=$(decode_value VM_SSH_KEY_FILE 2>"$command_output") || fail input invalid_environment_file
require_private_file "$key_file" key key_missing key_not_private

known_hosts=$temporary_root/known_hosts
printf '%s\n' "$VM_SSH_KNOWN_HOSTS" >"$known_hosts"
require_private_file "$known_hosts" known-hosts write_failed known_hosts_not_private
remote_target=$VM_USER@$VM_HOST
cd "$REPOSITORY_ROOT"

if [ "$mode" = remote-preflight ]; then
    run_quietly remote remote_failed ssh -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts" -i "$key_file" "$remote_target" "test -d /opt/photo-prjct && test -r /opt/photo-prjct/deployed-image"
    printf '[remote] stage=%s status=ok\n' "$mode"
    exit 0
fi

if [ "$mode" = stage-paused-observability-release ]; then
    release_root=/opt/photo-prjct/privileged-observability-releases/$RELEASE_SHA
    run_quietly remote remote_failed ssh -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts" -i "$key_file" "$remote_target" "mkdir -p -- $release_root $release_root/deploy"
    run_quietly copy copy_failed scp -r -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts" -i "$key_file" observability-release-sha observability-source.sha256 "$remote_target:$release_root/"
    run_quietly copy copy_failed scp -r -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts" -i "$key_file" deploy/bootstrap-selfie-observability.sh deploy/selfie-observability "$remote_target:$release_root/deploy/"
    printf '[remote] stage=%s status=ok\n' "$mode"
    exit 0
fi

case "$mode" in
    deploy|cutover-compose-identity)
        run_quietly copy copy_failed scp -r -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts" -i "$key_file" docker-compose.deployment.yml docker-compose.https.yml deploy "$remote_target:/opt/photo-prjct/"
        remote_environment=$temporary_root/remote.env
        # shellcheck disable=SC2086
        if ! write_remote_environment "$FINDME_ENV_FILE" "$remote_environment" $remote_deployment_values >"$command_output" 2>&1; then
            fail environment materialization_failed
        fi
        ;;
    private-storage)
        remote_environment=$temporary_root/remote.env
        if ! write_remote_environment "$FINDME_ENV_FILE" "$remote_environment" PRIVATE_MEDIA_ALLOWED_ORIGINS >"$command_output" 2>&1; then
            fail environment materialization_failed
        fi
        ;;
    selfie-storage)
        remote_environment=$temporary_root/remote.env
        : >"$remote_environment"
        chmod 600 "$remote_environment"
        ;;
    selfie-feedback-storage)
        remote_environment=$temporary_root/remote.env
        if ! write_remote_environment "$FINDME_ENV_FILE" "$remote_environment" SELFIE_FEEDBACK_ENABLED SELFIE_FEEDBACK_S3_BUCKET SELFIE_FEEDBACK_KMS_KEY_ID >"$command_output" 2>&1; then
            fail environment materialization_failed
        fi
        ;;
    configure-monitoring)
        run_quietly copy copy_failed scp -r -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts" -i "$key_file" deploy "$remote_target:/opt/photo-prjct/"
        remote_environment=$temporary_root/remote.env
        if ! write_remote_environment "$FINDME_ENV_FILE" "$remote_environment" YANDEX_CLOUD_FOLDER_ID >"$command_output" 2>&1; then
            fail environment materialization_failed
        fi
        ;;
    verify-deployed-image)
        remote_environment=$temporary_root/remote.env
        if ! write_remote_environment "$FINDME_ENV_FILE" "$remote_environment" APP_IMAGE PHOTO_WORKER_PROCESSOR_IDENTITIES >"$command_output" 2>&1; then
            fail environment materialization_failed
        fi
        ;;
    verify-paused-observability-release)
        remote_environment=$temporary_root/remote.env
        if ! write_remote_environment "$FINDME_ENV_FILE" "$remote_environment" RELEASE_SHA OBSERVABILITY_SOURCE_MANIFEST_SHA256 >"$command_output" 2>&1; then
            fail environment materialization_failed
        fi
        ;;
    face-embedding-benchmark)
        remote_environment=$temporary_root/remote.env
        if ! write_remote_environment "$FINDME_ENV_FILE" "$remote_environment" BENCHMARK_OPERATION BENCHMARK_EVENT_SLUG BENCHMARK_SOURCE_RUN_UUID >"$command_output" 2>&1; then
            fail environment materialization_failed
        fi
        ;;
esac

quoted_program=$(quote_for_remote_shell "$REMOTE_PROGRAM")
run_quietly_with_stdin remote remote_failed "$remote_environment" ssh -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts" -i "$key_file" "$remote_target" "exec python3 -c '$quoted_program' '$mode'"
if [ "$mode" = face-embedding-benchmark ]; then
    cat "$command_output"
fi
if [ "$mode" = deploy ]; then
    relay_deployment_markers
fi
printf '[remote] stage=%s status=ok\n' "$mode"
