#!/bin/sh
set -eu

fail() {
    printf 'IMAGE_ORIGIN_DEPLOY=error code=%s\n' "$1" >&2
    exit 2
}

file_mode() {
    python3 - "$1" <<'PY'
import stat
import sys
from pathlib import Path

print(f"{stat.S_IMODE(Path(sys.argv[1]).stat().st_mode):o}")
PY
}

release=${IMAGE_ORIGIN_RELEASE:-}
case "$release" in
    ''|*[!0-9a-f]*) fail invalid_release_sha ;;
esac
[ "${#release}" -eq 40 ] || fail invalid_release_sha

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
[ "$(git -C "$repository_root" rev-parse HEAD 2>/dev/null)" = "$release" ] || fail checkout_sha_mismatch

: "${FINDME_ENV_FILE:?}"
: "${VM_HOST:?}"
: "${VM_USER:?}"
: "${VM_SSH_KNOWN_HOSTS:?}"
: "${IMAGE_ORIGIN_VM_HOST:?}"
: "${IMAGE_ORIGIN_VM_USER:?}"
: "${IMAGE_ORIGIN_SSH_KNOWN_HOSTS:?}"
: "${PRIVATE_MEDIA_S3_BUCKET:?}"
: "${IMAGE_ORIGIN_PROBE_PATH:?}"
: "${YANDEX_CLOUD_FOLDER_ID:?}"
[ -f "$FINDME_ENV_FILE" ] && [ "$(file_mode "$FINDME_ENV_FILE")" = 600 ] || \
    fail invalid_environment_file
case "$VM_HOST" in ''|*[!A-Za-z0-9.:-]*) fail invalid_bastion_host ;; esac
case "$VM_USER" in ''|*[!A-Za-z0-9_-]*) fail invalid_bastion_user ;; esac
case "$IMAGE_ORIGIN_VM_HOST" in ''|*[!A-Za-z0-9.:-]*) fail invalid_host ;; esac
case "$IMAGE_ORIGIN_VM_USER" in ''|*[!A-Za-z0-9_-]*) fail invalid_user ;; esac
case "$YANDEX_CLOUD_FOLDER_ID" in ''|*[!A-Za-z0-9-]*) fail invalid_folder ;; esac

scratch=$(mktemp -d)
archive="$scratch/findme-image-origin-$release.tar"
remote_environment="$scratch/findme-image-origin-$release.env"
known_hosts="$scratch/known_hosts"
ssh_config="$scratch/ssh_config"
output="$scratch/output"
cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    rm -f "$archive" "$remote_environment" "$known_hosts" "$ssh_config" "$output"
    rmdir "$scratch"
    exit "$status"
}
trap cleanup EXIT HUP INT TERM
umask 077

key_file=$(python3 - "$FINDME_ENV_FILE" <<'PY'
import sys
from pathlib import Path


def decode(value: str) -> str:
    if len(value) < 2 or value[0] != '"' or value[-1] != '"':
        raise ValueError
    result = []
    escaped = False
    for character in value[1:-1]:
        if escaped:
            result.append({'n': '\n', 'r': '\r', 't': '\t'}.get(character, character))
            escaped = False
        elif character == '\\':
            escaped = True
        else:
            result.append(character)
    if escaped:
        raise ValueError
    return ''.join(result)


for line in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
    name, separator, value = line.partition('=')
    if separator and name == 'VM_SSH_KEY_FILE':
        print(decode(value))
        raise SystemExit(0)
raise SystemExit(1)
PY
) || fail missing_ssh_key
[ -f "$key_file" ] && [ "$(file_mode "$key_file")" = 600 ] || fail invalid_ssh_key

python3 - "$FINDME_ENV_FILE" "$remote_environment" <<'PY'
import os
import sys
from pathlib import Path


def encode(value: str) -> str:
    return '"' + (value.replace('\\', '\\\\').replace('"', '\\"')
        .replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t').replace('$', '\\$')) + '"'


allowed = {
    'GALLERY_IMGPROXY_KEY',
    'GALLERY_IMGPROXY_SALT',
    'IMAGE_ORIGIN_HEADER_SECRET',
    'IMAGE_ORIGIN_S3_ACCESS_KEY_ID',
    'IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY',
}
source = []
seen = set()
for line in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
    name, separator, _value = line.partition('=')
    if not separator:
        raise SystemExit(1)
    if name in allowed:
        source.append(line)
        seen.add(name)
if seen != allowed:
    raise SystemExit(1)
for name in ('PRIVATE_MEDIA_S3_BUCKET', 'IMAGE_ORIGIN_PROBE_PATH'):
    source.append(f'{name}={encode(os.environ[name])}')
Path(sys.argv[2]).write_text('\n'.join(source) + '\n', encoding='utf-8')
os.chmod(sys.argv[2], 0o600)
PY

printf '%s\n%s\n' "$VM_SSH_KNOWN_HOSTS" "$IMAGE_ORIGIN_SSH_KNOWN_HOSTS" >"$known_hosts"
cat >"$ssh_config" <<EOF
Host findme-image-origin-bastion
    HostName $VM_HOST
    User $VM_USER
    IdentityFile $key_file
    BatchMode yes
    IdentitiesOnly yes
    StrictHostKeyChecking yes
    UserKnownHostsFile $known_hosts

Host findme-image-origin-target
    HostName $IMAGE_ORIGIN_VM_HOST
    User $IMAGE_ORIGIN_VM_USER
    IdentityFile $key_file
    BatchMode yes
    IdentitiesOnly yes
    StrictHostKeyChecking yes
    UserKnownHostsFile $known_hosts
    ProxyJump findme-image-origin-bastion
EOF
COPYFILE_DISABLE=1
export COPYFILE_DISABLE
tar -C "$repository_root/deploy" -cf "$archive" image-origin

target=findme-image-origin-target
set +e
ssh -F "$ssh_config" "$target" sudo sh -s >"$output" 2>&1 <<'PREFLIGHT'
set -eu
cloud-init status --wait >/dev/null 2>&1 || exit 30
[ -f /var/lib/findme-image-origin/bootstrap-ready ] || exit 31
compose_version=$(docker compose version --short 2>/dev/null) || exit 32
compose_version=${compose_version#v}
dpkg --compare-versions "$compose_version" ge 2.24.4 || exit 32
command -v unified_agent >/dev/null 2>&1 || exit 33
systemctl cat unified-agent >/dev/null 2>&1 || exit 33
systemctl is-active --quiet unified-agent || exit 33
PREFLIGHT
preflight_status=$?
set -e
case "$preflight_status" in
    0) ;;
    30) fail cloud_init_failed ;;
    31) fail bootstrap_marker_missing ;;
    32) fail docker_compose_unsupported ;;
    33) fail unified_agent_unsupported ;;
    *) fail bootstrap_preflight_transport_failed ;;
esac

scp -F "$ssh_config" "$archive" "$target:/tmp/findme-image-origin-$release.tar" >"$output" 2>&1 || \
    fail archive_transport_failed
scp -F "$ssh_config" "$remote_environment" "$target:/tmp/findme-image-origin-$release.env" >"$output" 2>&1 || \
    fail environment_transport_failed

# The remote program receives only the non-secret SHA and folder ID as arguments. Runtime secrets
# stay in the mode-0600 environment file and are removed after apply.sh persists its own projection.
ssh -F "$ssh_config" "$target" sudo sh -s -- "$release" "$YANDEX_CLOUD_FOLDER_ID" >"$output" 2>&1 <<'REMOTE'
set -eu
release=$1
folder=$2
archive="/tmp/findme-image-origin-$release.tar"
environment="/tmp/findme-image-origin-$release.env"
stage=$(mktemp -d /tmp/findme-image-origin-stage.XXXXXX)
cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    rm -rf "$stage"
    rm -f "$archive" "$environment"
    exit "$status"
}
trap cleanup EXIT HUP INT TERM
[ -f "$archive" ] && [ -f "$environment" ] || exit 20
[ "$(stat -c '%a' "$environment")" = 600 ] || exit 21
tar -xf "$archive" -C "$stage"
package="$stage/image-origin"
[ -f "$package/apply.sh" ] && [ -f "$package/check.sh" ] || exit 22
set -a
. "$environment"
set +a
IMAGE_ORIGIN_RELEASE=$release
export IMAGE_ORIGIN_RELEASE
sh "$package/apply.sh"
sh /opt/photo-prjct-image-origin/current/check.sh \
    /opt/photo-prjct-image-origin/current/.env

template=/opt/photo-prjct-image-origin/current/monitoring/unified-agent.yml.template
[ -f "$template" ] || exit 23
agent=$(command -v unified_agent || true)
[ -n "$agent" ] || exit 24
if systemctl cat unified-agent >/dev/null 2>&1; then
    service=unified-agent
    config_dir=/etc/yandex/unified_agent
    config_path=$config_dir/config.yml
elif systemctl cat unified_agent >/dev/null 2>&1; then
    service=unified_agent
    config_dir=/etc/yc/unified_agent
    config_path=$config_dir/config.yml
else
    exit 25
fi
candidate=$(mktemp)
trap 'rm -f "$candidate"; cleanup' EXIT HUP INT TERM
sed "s|__YANDEX_CLOUD_FOLDER_ID__|$folder|g" "$template" >"$candidate"
"$agent" --config "$candidate" check-config
install -d -m 0755 "$config_dir"
install -m 0644 "$candidate" "$config_path"
rm -f "$candidate"
trap cleanup EXIT HUP INT TERM
systemctl enable "$service"
systemctl restart "$service"
systemctl is-active --quiet "$service"

printf 'IMAGE_ORIGIN_DEPLOYED_SHA=%s\n' "$release"
printf 'IMAGE_ORIGIN_HEALTH=green\n'
printf 'IMAGE_ORIGIN_MONITORING=green\n'
REMOTE

LC_ALL=C grep -E '^(IMAGE_ORIGIN_DEPLOYED_SHA=[0-9a-f]{40}|IMAGE_ORIGIN_HEALTH=green|IMAGE_ORIGIN_MONITORING=green)$' "$output"
