#!/bin/sh
set -eu

: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"
: "${SELFIE_OBSERVABILITY_STATE_DIR:?Set SELFIE_OBSERVABILITY_STATE_DIR}"
SYSTEMD_DIR="${SELFIE_OBSERVABILITY_SYSTEMD_DIR:-/etc/systemd/system}"
JOURNALD_DIR="${SELFIE_OBSERVABILITY_JOURNALD_DIR:-/etc/systemd/journald.conf.d}"
RUNTIME_DIR="${SELFIE_OBSERVABILITY_RUNTIME_DIR:-/usr/local/lib/findme-selfie-observability}"
action="${1:-install}"
mutation_started=0
transaction_complete=0
temporary=""

cleanup_temporary() { [ -z "$temporary" ] || rm -f "$temporary"; }

target_for() {
    case "$1" in
        journald.conf) printf '%s/60-findme-selfie-observability.conf\n' "$JOURNALD_DIR" ;;
        selfie-search-summary.service|selfie-search-summary.timer) printf '%s/%s\n' "$SYSTEMD_DIR" "$1" ;;
        run-daily-summary.sh|summarize.py) printf '%s/%s\n' "$RUNTIME_DIR" "$1" ;;
    esac
}

mode_for() { case "$1" in *.sh|*.py) printf '0755\n' ;; *) printf '0644\n' ;; esac; }

restore_file() {
    name="$1"; target="$(target_for "$name")"
    [ -f "$SELFIE_OBSERVABILITY_STATE_DIR/$name.changed" ] || return 0
    if [ -f "$SELFIE_OBSERVABILITY_STATE_DIR/$name.existed" ]; then
        backup="$(sed -n '1p' "$SELFIE_OBSERVABILITY_STATE_DIR/$name.backup-path")"
        case "$backup" in "$target".previous.*) ;; *) return 1 ;; esac
        temporary="$(mktemp "$target.rollback.XXXXXX")"
        install -o root -g root -m "$(mode_for "$name")" "$backup" "$temporary"
        mv "$temporary" "$target"; temporary=""
    else
        rm -f "$target"
    fi
}

rollback() {
    if [ ! -f "$SELFIE_OBSERVABILITY_STATE_DIR/transaction-armed" ]; then
        transaction_complete=1
        echo SELFIE_OBSERVABILITY_ROLLBACK_COMPLETE
        return 0
    fi
    rollback_status=0
    restore_file summarize.py || rollback_status=1
    restore_file run-daily-summary.sh || rollback_status=1
    restore_file selfie-search-summary.timer || rollback_status=1
    restore_file selfie-search-summary.service || rollback_status=1
    restore_file journald.conf || rollback_status=1
    if [ -f "$SELFIE_OBSERVABILITY_STATE_DIR/systemd.changed" ]; then systemctl daemon-reload || rollback_status=1; fi
    if [ -f "$SELFIE_OBSERVABILITY_STATE_DIR/journald.changed" ]; then systemctl restart systemd-journald || rollback_status=1; fi
    if [ -f "$SELFIE_OBSERVABILITY_STATE_DIR/timer.was-enabled" ]; then
        systemctl enable selfie-search-summary.timer || rollback_status=1
        systemctl is-enabled --quiet selfie-search-summary.timer || rollback_status=1
    else
        systemctl disable selfie-search-summary.timer || rollback_status=1
        if systemctl is-enabled --quiet selfie-search-summary.timer; then rollback_status=1; fi
    fi
    if [ -f "$SELFIE_OBSERVABILITY_STATE_DIR/timer.was-active" ]; then
        systemctl start selfie-search-summary.timer || rollback_status=1
        systemctl is-active --quiet selfie-search-summary.timer || rollback_status=1
    else
        systemctl stop selfie-search-summary.timer || rollback_status=1
        if systemctl is-active --quiet selfie-search-summary.timer; then rollback_status=1; fi
    fi
    [ -f "$SELFIE_OBSERVABILITY_STATE_DIR/runtime-dir.existed" ] || rmdir "$RUNTIME_DIR" 2>/dev/null || true
    [ -f "$SELFIE_OBSERVABILITY_STATE_DIR/systemd-dir.existed" ] || rmdir "$SYSTEMD_DIR" 2>/dev/null || true
    [ -f "$SELFIE_OBSERVABILITY_STATE_DIR/journald-dir.existed" ] || rmdir "$JOURNALD_DIR" 2>/dev/null || true
    if [ "$rollback_status" -eq 0 ]; then
        transaction_complete=1
        echo SELFIE_OBSERVABILITY_ROLLBACK_COMPLETE
        return 0
    fi
    echo SELFIE_OBSERVABILITY_ROLLBACK_FAILED >&2
    return 1
}

on_exit() {
    status=$?
    trap - EXIT INT TERM HUP
    cleanup_temporary
    if [ "$status" -ne 0 ] && [ "$mutation_started" -eq 1 ] && [ "$transaction_complete" -eq 0 ]; then
        rollback || echo SELFIE_OBSERVABILITY_ROLLBACK_FAILED >&2
    fi
    exit "$status"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

remove_state() {
    for name in journald.conf selfie-search-summary.service selfie-search-summary.timer run-daily-summary.sh summarize.py; do
        if [ -f "$SELFIE_OBSERVABILITY_STATE_DIR/$name.backup-path" ]; then
            backup="$(sed -n '1p' "$SELFIE_OBSERVABILITY_STATE_DIR/$name.backup-path")"
            case "$backup" in "$(target_for "$name")".previous.*) rm -f "$backup" ;; *) return 1 ;; esac
        fi
        rm -f "$SELFIE_OBSERVABILITY_STATE_DIR/$name.changed" \
            "$SELFIE_OBSERVABILITY_STATE_DIR/$name.existed" \
            "$SELFIE_OBSERVABILITY_STATE_DIR/$name.backup-path"
    done
    rm -f "$SELFIE_OBSERVABILITY_STATE_DIR/systemd.changed" \
        "$SELFIE_OBSERVABILITY_STATE_DIR/journald.changed" \
        "$SELFIE_OBSERVABILITY_STATE_DIR/timer.was-enabled" \
        "$SELFIE_OBSERVABILITY_STATE_DIR/timer.was-active" \
        "$SELFIE_OBSERVABILITY_STATE_DIR/transaction-armed" \
        "$SELFIE_OBSERVABILITY_STATE_DIR/runtime-dir.existed" \
        "$SELFIE_OBSERVABILITY_STATE_DIR/systemd-dir.existed" \
        "$SELFIE_OBSERVABILITY_STATE_DIR/journald-dir.existed"
    rmdir "$SELFIE_OBSERVABILITY_STATE_DIR" 2>/dev/null || true
}

case "$action" in
    rollback) mutation_started=1; rollback; remove_state; exit 0 ;;
    commit) remove_state; transaction_complete=1; echo SELFIE_OBSERVABILITY_INSTALL_COMMITTED; exit 0 ;;
    install) ;;
    *) echo "usage: $0 install|rollback|commit" >&2; exit 2 ;;
esac

# Everything below this line is read-only validation until mutation_started is armed.
for command in cmp cp grep install mktemp mv rm rmdir sed stat systemctl systemd-analyze; do
    command -v "$command" >/dev/null 2>&1 || { echo "missing dependency: $command" >&2; exit 1; }
done
for name in journald.conf selfie-search-summary.service selfie-search-summary.timer run-daily-summary.sh summarize.py; do
    candidate="$DEPLOY_ROOT/deploy/selfie-observability/$name"
    [ -f "$candidate" ] && [ -r "$candidate" ] || { echo "invalid observability candidate: $name" >&2; exit 1; }
done
for setting in 'Storage=persistent' 'MaxRetentionSec=14day' 'SystemMaxUse=1G'; do
    grep -Fqx "$setting" "$DEPLOY_ROOT/deploy/selfie-observability/journald.conf" || { echo "invalid observability candidate: journald policy" >&2; exit 1; }
done
systemd-analyze verify "$DEPLOY_ROOT/deploy/selfie-observability/selfie-search-summary.service" "$DEPLOY_ROOT/deploy/selfie-observability/selfie-search-summary.timer" >/dev/null
[ ! -e "$SELFIE_OBSERVABILITY_STATE_DIR" ] || [ -d "$SELFIE_OBSERVABILITY_STATE_DIR" ] || { echo "invalid observability state path" >&2; exit 1; }
timer_was_enabled=0
timer_was_active=0
systemctl is-enabled --quiet selfie-search-summary.timer && timer_was_enabled=1 || true
systemctl is-active --quiet selfie-search-summary.timer && timer_was_active=1 || true
systemd_dir_existed=0; [ -d "$SYSTEMD_DIR" ] && systemd_dir_existed=1
journald_dir_existed=0; [ -d "$JOURNALD_DIR" ] && journald_dir_existed=1
runtime_dir_existed=0; [ -d "$RUNTIME_DIR" ] && runtime_dir_existed=1

mutation_started=1
install -d -o root -g root -m 0700 "$SELFIE_OBSERVABILITY_STATE_DIR"
[ "$timer_was_enabled" -eq 0 ] || : > "$SELFIE_OBSERVABILITY_STATE_DIR/timer.was-enabled"
[ "$timer_was_active" -eq 0 ] || : > "$SELFIE_OBSERVABILITY_STATE_DIR/timer.was-active"
[ "$systemd_dir_existed" -eq 0 ] || : > "$SELFIE_OBSERVABILITY_STATE_DIR/systemd-dir.existed"
[ "$journald_dir_existed" -eq 0 ] || : > "$SELFIE_OBSERVABILITY_STATE_DIR/journald-dir.existed"
[ "$runtime_dir_existed" -eq 0 ] || : > "$SELFIE_OBSERVABILITY_STATE_DIR/runtime-dir.existed"
: > "$SELFIE_OBSERVABILITY_STATE_DIR/transaction-armed"
install -d -o root -g root -m 0755 "$SYSTEMD_DIR" "$JOURNALD_DIR" "$RUNTIME_DIR"

install_one() {
    name="$1"; candidate="$DEPLOY_ROOT/deploy/selfie-observability/$name"; target="$(target_for "$name")"
    if [ -f "$target" ] && cmp -s "$candidate" "$target"; then return 0; fi
    if [ -f "$target" ]; then
        backup="$(mktemp "$target.previous.XXXXXX")"
        cp -p "$target" "$backup"
        printf '%s\n' "$backup" > "$SELFIE_OBSERVABILITY_STATE_DIR/$name.backup-path"
        : > "$SELFIE_OBSERVABILITY_STATE_DIR/$name.existed"
    fi
    : > "$SELFIE_OBSERVABILITY_STATE_DIR/$name.changed"
    temporary="$(mktemp "$target.candidate.XXXXXX")"
    install -o root -g root -m "$(mode_for "$name")" "$candidate" "$temporary"
    mv "$temporary" "$target"; temporary=""
    expected_mode="$(mode_for "$name")"; expected_mode="${expected_mode#0}"
    [ "$(stat -c '%U:%G:%a' "$target")" = "root:root:$expected_mode" ] || { echo "managed observability file metadata mismatch: $name" >&2; return 1; }
}

install_one journald.conf
install_one selfie-search-summary.service
install_one selfie-search-summary.timer
install_one run-daily-summary.sh
install_one summarize.py

if [ -f "$SELFIE_OBSERVABILITY_STATE_DIR/selfie-search-summary.service.changed" ] || [ -f "$SELFIE_OBSERVABILITY_STATE_DIR/selfie-search-summary.timer.changed" ]; then : > "$SELFIE_OBSERVABILITY_STATE_DIR/systemd.changed"; systemctl daemon-reload; fi
if [ -f "$SELFIE_OBSERVABILITY_STATE_DIR/journald.conf.changed" ]; then : > "$SELFIE_OBSERVABILITY_STATE_DIR/journald.changed"; systemctl restart systemd-journald; fi
systemctl enable --now selfie-search-summary.timer
transaction_complete=1
echo SELFIE_OBSERVABILITY_INSTALL_READY
