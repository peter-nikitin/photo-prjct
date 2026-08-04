#!/bin/sh

set -eu

# This file is copied once by deploy/bootstrap-selfie-observability.sh to a
# root-owned path.  It deliberately has no repository path or user-controlled
# source path: all candidates are read from the root-owned package installed by
# that bootstrap step.
PACKAGE_DIR=/usr/local/lib/findme-selfie-observability-package
STATE_DIR=/var/lib/findme-selfie-observability
SYSTEMD_DIR=/etc/systemd/system
JOURNALD_DIR=/etc/systemd/journald.conf.d
RUNTIME_DIR=/usr/local/lib/findme-selfie-observability
action="${1:-install}"
mutation_started=0
transaction_complete=0
temporary=""
test_mode="${SELFIE_OBSERVABILITY_TEST_MODE:-0}"

if [ "$test_mode" = 1 ]; then
    [ "$(id -u)" -ne 0 ] || {
        echo "SELFIE_OBSERVABILITY_TEST_MODE is not permitted as root" >&2
        exit 2
    }
    PACKAGE_DIR="${SELFIE_OBSERVABILITY_PACKAGE_DIR:?Set SELFIE_OBSERVABILITY_PACKAGE_DIR}"
    STATE_DIR="${SELFIE_OBSERVABILITY_STATE_DIR:?Set SELFIE_OBSERVABILITY_STATE_DIR}"
    SYSTEMD_DIR="${SELFIE_OBSERVABILITY_SYSTEMD_DIR:?Set SELFIE_OBSERVABILITY_SYSTEMD_DIR}"
    JOURNALD_DIR="${SELFIE_OBSERVABILITY_JOURNALD_DIR:?Set SELFIE_OBSERVABILITY_JOURNALD_DIR}"
    RUNTIME_DIR="${SELFIE_OBSERVABILITY_RUNTIME_DIR:?Set SELFIE_OBSERVABILITY_RUNTIME_DIR}"
elif [ "$test_mode" != 0 ]; then
    echo "invalid SELFIE_OBSERVABILITY_TEST_MODE" >&2
    exit 2
fi

cleanup_temporary() {
    [ -z "$temporary" ] || rm -f "$temporary"
}

target_for() {
    case "$1" in
        journald.conf) printf '%s/60-findme-selfie-observability.conf\n' "$JOURNALD_DIR" ;;
        selfie-search-summary.service|selfie-search-summary.timer) printf '%s/%s\n' "$SYSTEMD_DIR" "$1" ;;
        run-daily-summary.sh|summarize.py) printf '%s/%s\n' "$RUNTIME_DIR" "$1" ;;
        *) echo "invalid managed observability file: $1" >&2; return 1 ;;
    esac
}

mode_for() {
    case "$1" in
        *.sh|*.py) printf '0755\n' ;;
        *) printf '0644\n' ;;
    esac
}

validate_root_package() {
    [ -d "$PACKAGE_DIR" ] || { echo "root observability package is unavailable" >&2; return 1; }
    if [ "$test_mode" -eq 0 ]; then
        [ "$(stat -c '%U:%G:%a' "$PACKAGE_DIR")" = "root:root:755" ] || {
            echo "root observability package metadata mismatch" >&2
            return 1
        }
    fi
    for name in journald.conf selfie-search-summary.service selfie-search-summary.timer \
        run-daily-summary.sh summarize.py; do
        candidate="$PACKAGE_DIR/$name"
        [ -f "$candidate" ] && [ -r "$candidate" ] || {
            echo "invalid root observability package: $name" >&2
            return 1
        }
        if [ "$test_mode" -eq 0 ]; then
            expected_mode="$(mode_for "$name")"; expected_mode="${expected_mode#0}"
            [ "$(stat -c '%U:%G:%a' "$candidate")" = "root:root:$expected_mode" ] || {
                echo "root observability package file metadata mismatch: $name" >&2
                return 1
            }
        fi
    done
    for setting in 'Storage=persistent' 'MaxRetentionSec=14day' 'SystemMaxUse=1G'; do
        grep -Fqx "$setting" "$PACKAGE_DIR/journald.conf" || {
            echo "invalid root observability package: journald policy" >&2
            return 1
        }
    done
    systemd-analyze verify "$PACKAGE_DIR/selfie-search-summary.service" \
        "$PACKAGE_DIR/selfie-search-summary.timer" >/dev/null
}

restore_file() {
    name="$1"; target="$(target_for "$name")"
    [ -f "$STATE_DIR/$name.changed" ] || return 0
    if [ -f "$STATE_DIR/$name.existed" ]; then
        backup="$(sed -n '1p' "$STATE_DIR/$name.backup-path")"
        case "$backup" in "$target".previous.*) ;; *) return 1 ;; esac
        temporary="$(mktemp "$target.rollback.XXXXXX")"
        install -o root -g root -m "$(mode_for "$name")" "$backup" "$temporary"
        mv "$temporary" "$target"; temporary=""
    else
        rm -f "$target"
    fi
}

rollback_transaction() {
    if [ ! -f "$STATE_DIR/transaction-armed" ]; then
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
    if [ -f "$STATE_DIR/systemd.changed" ]; then systemctl daemon-reload || rollback_status=1; fi
    if [ -f "$STATE_DIR/journald.changed" ]; then systemctl restart systemd-journald || rollback_status=1; fi
    if [ -f "$STATE_DIR/timer.was-enabled" ]; then
        systemctl enable selfie-search-summary.timer || rollback_status=1
        systemctl is-enabled --quiet selfie-search-summary.timer || rollback_status=1
    else
        systemctl disable selfie-search-summary.timer || rollback_status=1
        if systemctl is-enabled --quiet selfie-search-summary.timer; then rollback_status=1; fi
    fi
    if [ -f "$STATE_DIR/timer.was-active" ]; then
        systemctl start selfie-search-summary.timer || rollback_status=1
        systemctl is-active --quiet selfie-search-summary.timer || rollback_status=1
    else
        systemctl stop selfie-search-summary.timer || rollback_status=1
        if systemctl is-active --quiet selfie-search-summary.timer; then rollback_status=1; fi
    fi
    [ -f "$STATE_DIR/runtime-dir.existed" ] || rmdir "$RUNTIME_DIR" 2>/dev/null || true
    [ -f "$STATE_DIR/systemd-dir.existed" ] || rmdir "$SYSTEMD_DIR" 2>/dev/null || true
    [ -f "$STATE_DIR/journald-dir.existed" ] || rmdir "$JOURNALD_DIR" 2>/dev/null || true
    if [ "$rollback_status" -eq 0 ]; then
        transaction_complete=1
        echo SELFIE_OBSERVABILITY_ROLLBACK_COMPLETE
        return 0
    fi
    echo SELFIE_OBSERVABILITY_ROLLBACK_FAILED >&2
    return 1
}

remove_state() {
    for name in journald.conf selfie-search-summary.service selfie-search-summary.timer \
        run-daily-summary.sh summarize.py; do
        if [ -f "$STATE_DIR/$name.backup-path" ]; then
            backup="$(sed -n '1p' "$STATE_DIR/$name.backup-path")"
            case "$backup" in "$(target_for "$name")".previous.*) rm -f "$backup" ;; *) return 1 ;; esac
        fi
        rm -f "$STATE_DIR/$name.changed" "$STATE_DIR/$name.existed" \
            "$STATE_DIR/$name.backup-path"
    done
    rm -f "$STATE_DIR/systemd.changed" "$STATE_DIR/journald.changed" \
        "$STATE_DIR/timer.was-enabled" "$STATE_DIR/timer.was-active" \
        "$STATE_DIR/transaction-armed" "$STATE_DIR/runtime-dir.existed" \
        "$STATE_DIR/systemd-dir.existed" "$STATE_DIR/journald-dir.existed"
    rmdir "$STATE_DIR" 2>/dev/null || true
}

on_exit() {
    status=$?
    trap - EXIT INT TERM HUP
    cleanup_temporary
    if [ "$status" -ne 0 ] && [ "$mutation_started" -eq 1 ] && [ "$transaction_complete" -eq 0 ]; then
        if rollback_transaction; then
            remove_state || echo SELFIE_OBSERVABILITY_STATE_CLEANUP_FAILED >&2
        else
            echo SELFIE_OBSERVABILITY_ROLLBACK_FAILED >&2
        fi
    fi
    exit "$status"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

case "$action" in
    rollback)
        [ "$#" -eq 1 ] || { echo "rollback accepts no arguments" >&2; exit 2; }
        mutation_started=1
        rollback_transaction
        remove_state
        exit 0
        ;;
    commit)
        [ "$#" -eq 1 ] || { echo "commit accepts no arguments" >&2; exit 2; }
        remove_state
        transaction_complete=1
        echo SELFIE_OBSERVABILITY_INSTALL_COMMITTED
        exit 0
        ;;
    verify)
        [ "$#" -eq 1 ] || { echo "verify accepts no arguments" >&2; exit 2; }
        for command in grep journalctl sed stat systemctl systemd-analyze tail; do
            command -v "$command" >/dev/null 2>&1 || { echo "missing observability dependency: $command" >&2; exit 1; }
        done
        validate_root_package
        JOURNAL_DIR=/var/log/journal
        [ "$test_mode" -eq 1 ] && JOURNAL_DIR="${SELFIE_OBSERVABILITY_JOURNAL_DIR:?Set SELFIE_OBSERVABILITY_JOURNAL_DIR}"
        [ -d "$JOURNAL_DIR" ] || { echo "persistent journal directory is unavailable" >&2; exit 1; }
        effective="$(systemd-analyze cat-config systemd/journald.conf)"
        effective_value() {
            printf '%s\n' "$effective" | sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" | tail -n 1
        }
        [ "Storage=$(effective_value Storage)" = "Storage=persistent" ] || { echo "effective journald policy mismatch" >&2; exit 1; }
        [ "MaxRetentionSec=$(effective_value MaxRetentionSec)" = "MaxRetentionSec=14day" ] || { echo "effective journald policy mismatch" >&2; exit 1; }
        [ "SystemMaxUse=$(effective_value SystemMaxUse)" = "SystemMaxUse=1G" ] || { echo "effective journald policy mismatch" >&2; exit 1; }
        systemctl is-enabled --quiet selfie-search-summary.timer || { echo "selfie summary timer is not enabled" >&2; exit 1; }
        systemctl is-active --quiet selfie-search-summary.timer || { echo "selfie summary timer is not active" >&2; exit 1; }
        disk_line="$(journalctl --disk-usage 2>/dev/null)" || { echo "journal disk usage is unavailable" >&2; exit 1; }
        disk_usage="$(printf '%s\n' "$disk_line" | sed -n 's/.*take[s]* up \([^ ]*\).*/\1/p' | tail -n 1)"
        [ -n "$disk_usage" ] || { echo "journal disk usage is unreadable" >&2; exit 1; }
        printf 'journal_disk_usage=%s\n' "$disk_usage"
        oldest_line="$(journalctl -u docker.service --since '14 days ago' -o short-unix --grep '"event":"selfie_' 2>/dev/null | sed -n '1p')"
        if [ -z "$oldest_line" ]; then
            echo oldest_selfie_event_realtime=none
        else
            oldest_timestamp="${oldest_line%% *}"
            case "$oldest_timestamp" in *[!0-9.]*|'') echo "oldest selfie event timestamp is unreadable" >&2; exit 1 ;; esac
            printf 'oldest_selfie_event_realtime=%s\n' "$oldest_timestamp"
        fi
        echo SELFIE_OBSERVABILITY_HOST_VERIFIED
        exit 0
        ;;
    verify-probe)
        [ "$#" -eq 2 ] || { echo "verify-probe requires one UUID" >&2; exit 2; }
        probe_id="$2"
        case "$probe_id" in
            ????????-????-????-????-????????????) ;;
            *) echo "invalid observability probe id" >&2; exit 2 ;;
        esac
        case "$probe_id" in *[!0-9a-f-]*) echo "invalid observability probe id" >&2; exit 2 ;; esac
        for command in grep journalctl; do
            command -v "$command" >/dev/null 2>&1 || { echo "missing observability dependency: $command" >&2; exit 1; }
        done
        journalctl --since '2 minutes ago' -o cat \
            | grep -Fq "\"probe_id\":\"$probe_id\"" || {
                echo "emitted observability probe is unreadable" >&2
                exit 1
            }
        echo SELFIE_OBSERVABILITY_PROBE_VERIFIED
        exit 0
        ;;
    install)
        [ "$#" -le 1 ] || { echo "install accepts no arguments" >&2; exit 2; }
        ;;
    *)
        echo "usage: $0 install|rollback|commit|verify|verify-probe UUID" >&2
        exit 2
        ;;
esac

for command in cmp cp grep install mktemp mv rm rmdir sed stat systemctl systemd-analyze; do
    command -v "$command" >/dev/null 2>&1 || { echo "missing dependency: $command" >&2; exit 1; }
done
validate_root_package
[ ! -e "$STATE_DIR" ] || [ -d "$STATE_DIR" ] || { echo "invalid observability state path" >&2; exit 1; }
[ ! -f "$STATE_DIR/transaction-armed" ] || { echo "observability transaction is already armed" >&2; exit 1; }
timer_was_enabled=0
timer_was_active=0
systemctl is-enabled --quiet selfie-search-summary.timer && timer_was_enabled=1 || true
systemctl is-active --quiet selfie-search-summary.timer && timer_was_active=1 || true
systemd_dir_existed=0; [ -d "$SYSTEMD_DIR" ] && systemd_dir_existed=1
journald_dir_existed=0; [ -d "$JOURNALD_DIR" ] && journald_dir_existed=1
runtime_dir_existed=0; [ -d "$RUNTIME_DIR" ] && runtime_dir_existed=1

mutation_started=1
install -d -o root -g root -m 0700 "$STATE_DIR"
[ "$timer_was_enabled" -eq 0 ] || : > "$STATE_DIR/timer.was-enabled"
[ "$timer_was_active" -eq 0 ] || : > "$STATE_DIR/timer.was-active"
[ "$systemd_dir_existed" -eq 0 ] || : > "$STATE_DIR/systemd-dir.existed"
[ "$journald_dir_existed" -eq 0 ] || : > "$STATE_DIR/journald-dir.existed"
[ "$runtime_dir_existed" -eq 0 ] || : > "$STATE_DIR/runtime-dir.existed"
: > "$STATE_DIR/transaction-armed"
install -d -o root -g root -m 0755 "$SYSTEMD_DIR" "$JOURNALD_DIR" "$RUNTIME_DIR"

install_one() {
    name="$1"; candidate="$PACKAGE_DIR/$name"; target="$(target_for "$name")"
    if [ -f "$target" ] && cmp -s "$candidate" "$target"; then return 0; fi
    if [ -f "$target" ]; then
        backup="$(mktemp "$target.previous.XXXXXX")"
        cp -p "$target" "$backup"
        printf '%s\n' "$backup" > "$STATE_DIR/$name.backup-path"
        : > "$STATE_DIR/$name.existed"
    fi
    : > "$STATE_DIR/$name.changed"
    temporary="$(mktemp "$target.candidate.XXXXXX")"
    install -o root -g root -m "$(mode_for "$name")" "$candidate" "$temporary"
    mv "$temporary" "$target"; temporary=""
    expected_mode="$(mode_for "$name")"; expected_mode="${expected_mode#0}"
    if [ "$test_mode" -eq 0 ]; then
        [ "$(stat -c '%U:%G:%a' "$target")" = "root:root:$expected_mode" ] || {
            echo "managed observability file metadata mismatch: $name" >&2
            return 1
        }
    fi
}

install_one journald.conf
install_one selfie-search-summary.service
install_one selfie-search-summary.timer
install_one run-daily-summary.sh
install_one summarize.py

if [ -f "$STATE_DIR/selfie-search-summary.service.changed" ] || [ -f "$STATE_DIR/selfie-search-summary.timer.changed" ]; then
    : > "$STATE_DIR/systemd.changed"
    systemctl daemon-reload
fi
if [ -f "$STATE_DIR/journald.conf.changed" ]; then
    : > "$STATE_DIR/journald.changed"
    systemctl restart systemd-journald
fi
systemctl enable --now selfie-search-summary.timer
transaction_complete=1
echo SELFIE_OBSERVABILITY_INSTALL_READY
