#!/bin/sh
set -eu

agent_version=26.09.01
agent_sha256=08a79e7ce2a06d5b51e368025fd1efb2ccd3e7e91de550730063256b162ddc00
agent_url="https://storage.yandexcloud.net/yc-unified-agent/releases/$agent_version/deb/ubuntu-24.04-noble/yandex-unified-agent_${agent_version}_amd64.deb"
ready_marker=${IMAGE_ORIGIN_BOOTSTRAP_READY_PATH:-/var/lib/findme-image-origin/bootstrap-ready}
os_release=${IMAGE_ORIGIN_OS_RELEASE_PATH:-/etc/os-release}
apt_sources_list=${IMAGE_ORIGIN_APT_SOURCES_LIST_PATH:-/etc/apt/sources.list}
apt_sources_dir=${IMAGE_ORIGIN_APT_SOURCES_DIR:-/etc/apt/sources.list.d}
package=$(mktemp)
ready=0

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    rm -f "$package"
    if [ "$ready" -ne 1 ] || [ "$status" -ne 0 ]; then
        rm -f "$ready_marker"
    fi
    exit "$status"
}
trap cleanup EXIT HUP INT TERM
rm -f "$ready_marker"

[ -r "$os_release" ]
. "$os_release"
[ "${ID:-}" = ubuntu ]
[ "${VERSION_ID:-}" = 24.04 ]

install -d -m 0755 "$(dirname "$apt_sources_list")" "$apt_sources_dir"
rm -f "$apt_sources_dir"/*.list "$apt_sources_dir"/*.sources
install -m 0644 /dev/null "$apt_sources_list"
cat >"$apt_sources_dir/ubuntu.sources" <<'SOURCES'
Types: deb
URIs: https://archive.ubuntu.com/ubuntu
Suites: noble noble-updates noble-backports
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: https://security.ubuntu.com/ubuntu
Suites: noble-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
SOURCES

apt-get update --error-on=any
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl docker.io docker-compose-v2
systemctl enable --now docker

curl --fail --silent --show-error --connect-timeout 10 --max-time 120 \
    --output "$package" "$agent_url"
printf '%s  %s\n' "$agent_sha256" "$package" | sha256sum -c -
dpkg -i "$package"

compose_version=$(docker compose version --short)
compose_version=${compose_version#v}
dpkg --compare-versions "$compose_version" ge 2.24.4
command -v unified_agent >/dev/null 2>&1
systemctl cat unified-agent >/dev/null 2>&1
systemctl enable --now unified-agent
systemctl is-active --quiet docker
systemctl is-active --quiet unified-agent

install -d -m 0755 "$(dirname "$ready_marker")"
install -m 0600 /dev/null "$ready_marker"
ready=1
