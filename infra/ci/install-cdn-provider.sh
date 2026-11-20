#!/usr/bin/env bash
set -euo pipefail

install_dir="$1"
provider_dir="$(cd "$(dirname "$0")/../providers/findmecdn" && pwd)"
source="terraform.findme-photo.ru/findme/findmecdn"
version="0.1.0"
platform="$(go env GOOS)_$(go env GOARCH)"
mkdir -p "$install_dir"
install_dir="$(cd "$install_dir" && pwd)"
mirror="$install_dir/mirror"
destination="$mirror/$source/$version/$platform"
mkdir -p "$destination"

(
  cd "$provider_dir"
  CGO_ENABLED=0 go build -mod=readonly -trimpath -buildvcs=false \
    -o "$destination/terraform-provider-findmecdn_v$version" .
)
cat > "$install_dir/terraform.rc" <<CONFIG
provider_installation {
  filesystem_mirror {
    path    = "$mirror"
    include = ["$source"]
  }
  direct {
    exclude = ["$source"]
  }
}
CONFIG
