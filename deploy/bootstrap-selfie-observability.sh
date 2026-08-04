#!/bin/sh

set -eu

# One-time operator action.  It copies reviewed, validated assets into a
# root-owned package; normal deployments never execute repository files as
# root.  Re-run this step explicitly when the host observability package
# changes.
DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/photo-prjct}"
PACKAGE_TARGET=/usr/local/lib/findme-selfie-observability-package
HELPER_TARGET=/usr/local/sbin/findme-selfie-observability
SUDOERS_TARGET=/etc/sudoers.d/findme-selfie-observability
temporary=""

cleanup() { [ -z "$temporary" ] || rm -f "$temporary"; }
trap cleanup EXIT INT TERM HUP

case "$DEPLOY_ROOT" in
    ''|*[!A-Za-z0-9_./-]*) echo "DEPLOY_ROOT contains unsupported characters" >&2; exit 2 ;;
esac

for command in cmp grep install mktemp sed sudo systemd-analyze; do
    command -v "$command" >/dev/null 2>&1 || { echo "missing bootstrap dependency: $command" >&2; exit 1; }
done

source_dir="$DEPLOY_ROOT/deploy/selfie-observability"
for name in root-helper.sh journald.conf selfie-search-summary.service \
    selfie-search-summary.timer run-daily-summary.sh summarize.py; do
    source="$source_dir/$name"
    [ -f "$source" ] && [ -r "$source" ] || { echo "invalid bootstrap source: $name" >&2; exit 1; }
done

helper_source="$source_dir/root-helper.sh"
if grep -Fq '/opt/photo-prjct' "$helper_source" || \
    grep -Fq 'deploy/selfie-observability' "$helper_source"; then
    echo "root helper must not reference the mutable deployment tree" >&2
    exit 1
fi
for setting in 'Storage=persistent' 'MaxRetentionSec=14day' 'SystemMaxUse=1G'; do
    grep -Fqx "$setting" "$source_dir/journald.conf" || {
        echo "invalid bootstrap journald policy" >&2
        exit 1
    }
done
systemd-analyze verify "$source_dir/selfie-search-summary.service" \
    "$source_dir/selfie-search-summary.timer" >/dev/null

temporary="$(mktemp)"
cat > "$temporary" <<'EOF'
deploy ALL=(root) NOPASSWD: /usr/local/sbin/findme-selfie-observability install
deploy ALL=(root) NOPASSWD: /usr/local/sbin/findme-selfie-observability rollback
deploy ALL=(root) NOPASSWD: /usr/local/sbin/findme-selfie-observability commit
deploy ALL=(root) NOPASSWD: /usr/local/sbin/findme-selfie-observability verify
deploy ALL=(root) NOPASSWD: /usr/local/sbin/findme-selfie-observability verify-probe *
EOF
sudo visudo -cf "$temporary" >/dev/null

sudo install -d -o root -g root -m 0755 "$PACKAGE_TARGET" /usr/local/lib /usr/local/sbin /etc/sudoers.d
for name in journald.conf selfie-search-summary.service selfie-search-summary.timer \
    run-daily-summary.sh summarize.py; do
    mode=0644
    case "$name" in run-daily-summary.sh|summarize.py) mode=0755 ;; esac
    sudo install -o root -g root -m "$mode" "$source_dir/$name" "$PACKAGE_TARGET/.$name.new"
    sudo mv "$PACKAGE_TARGET/.$name.new" "$PACKAGE_TARGET/$name"
done
sudo install -o root -g root -m 0755 "$helper_source" "$HELPER_TARGET.new"
sudo mv "$HELPER_TARGET.new" "$HELPER_TARGET"
sudo install -o root -g root -m 0440 "$temporary" "$SUDOERS_TARGET.new"
sudo visudo -cf "$SUDOERS_TARGET.new" >/dev/null
sudo mv "$SUDOERS_TARGET.new" "$SUDOERS_TARGET"
temporary=""
echo SELFIE_OBSERVABILITY_BOOTSTRAP_READY
