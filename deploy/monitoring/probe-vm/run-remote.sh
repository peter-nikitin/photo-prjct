#!/bin/sh
set -eu

fail() {
    printf 'PUBLIC_PROBE_DEPLOY=error code=%s\n' "$1" >&2
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

action=${PUBLIC_PROBE_ACTION:-install}
case "$action" in install|disable|rollback) ;; *) fail invalid_action ;; esac

release=${PUBLIC_PROBE_RELEASE:-}
case "$release" in
    ''|*[!0-9a-f]*) fail invalid_release_sha ;;
esac
[ "${#release}" -eq 40 ] || fail invalid_release_sha

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
[ "$(git -C "$repository_root" rev-parse HEAD 2>/dev/null)" = "$release" ] || fail checkout_sha_mismatch

: "${FINDME_ENV_FILE:?}"
: "${VM_HOST:?}"
: "${VM_USER:?}"
: "${VM_SSH_KNOWN_HOSTS:?}"
: "${IMAGE_ORIGIN_VM_HOST:?}"
: "${IMAGE_ORIGIN_VM_USER:?}"
: "${IMAGE_ORIGIN_SSH_KNOWN_HOSTS:?}"
[ -f "$FINDME_ENV_FILE" ] && [ "$(file_mode "$FINDME_ENV_FILE")" = 600 ] || \
    fail invalid_environment_file
case "$VM_HOST" in ''|*[!A-Za-z0-9.:-]*) fail invalid_bastion_host ;; esac
case "$VM_USER" in ''|*[!A-Za-z0-9_-]*) fail invalid_bastion_user ;; esac
case "$IMAGE_ORIGIN_VM_HOST" in ''|*[!A-Za-z0-9.:-]*) fail invalid_host ;; esac
case "$IMAGE_ORIGIN_VM_USER" in ''|*[!A-Za-z0-9_-]*) fail invalid_user ;; esac

scratch=$(mktemp -d)
archive="$scratch/findme-public-probe-$release.tar"
known_hosts="$scratch/known_hosts"
ssh_config="$scratch/ssh_config"
output="$scratch/output"
cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    rm -f "$archive" "$known_hosts" "$ssh_config" "$output"
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


printf '%s\n%s\n' "$VM_SSH_KNOWN_HOSTS" "$IMAGE_ORIGIN_SSH_KNOWN_HOSTS" >"$known_hosts"
# Reject whitespace in the key path before embedding it in ssh_config.
case "$key_file" in *[!A-Za-z0-9_./-]*) fail invalid_ssh_key_path ;; esac
cat >"$ssh_config" <<EOF
Host probe-bastion
    HostName $VM_HOST
    User $VM_USER
    IdentityFile $key_file
    BatchMode yes
    IdentitiesOnly yes
    StrictHostKeyChecking yes
    UserKnownHostsFile $known_hosts
    ConnectTimeout 15

Host probe-target
    HostName $IMAGE_ORIGIN_VM_HOST
    User $IMAGE_ORIGIN_VM_USER
    IdentityFile $key_file
    BatchMode yes
    IdentitiesOnly yes
    StrictHostKeyChecking yes
    UserKnownHostsFile $known_hosts
    ConnectTimeout 15
    ProxyJump probe-bastion
EOF
python3 "$repository_root/deploy/monitoring/probe-vm/package.py" "$release" "$archive" >"$output" 2>&1 || fail package_failed
# Stream to an unpredictable private remote directory; no credentials leave the runner.
ssh -F "$ssh_config" probe-target "action=$action"'
set -eu
stage=$(mktemp -d /tmp/findme-public-probe.XXXXXX)
cleanup_remote() { rm -rf "$stage"; }
trap cleanup_remote EXIT HUP INT TERM
cat > "$stage/package.tar"
tar -xf "$stage/package.tar" -C "$stage"
sudo -n python3 "$stage/install.py" "$action" --package "$stage"' <"$archive" >"$output" 2>&1 || fail action_failed
LC_ALL=C grep -E '^(PUBLIC_PROBE_RELEASE=[0-9a-f]{40}|PUBLIC_PROBE_TIMER=(active|disabled))$' "$output"
