#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$script_dir/.." && pwd)
go_command=${GO:-go}
version=$(tr -d '\r\n' < "$root/VERSION")
upstream_version=$(sed -n 's/^UPSTREAM_VERSION=//p' "$root/android/version.properties" | head -n 1)
package_dir="$root/dist/smart-box-${version}-linux-x86_64"
core="$package_dir/bin/smart-box-core"

[ -e "$root/core/.git" ] || {
    printf '%s\n' 'core submodule is not initialized; run git submodule update --init --recursive' >&2
    exit 1
}
[ -n "$upstream_version" ] || {
    printf '%s\n' 'missing UPSTREAM_VERSION in android/version.properties' >&2
    exit 1
}
command -v "$go_command" >/dev/null 2>&1 || {
    printf 'Go command not found: %s\n' "$go_command" >&2
    exit 1
}

mkdir -p "$(dirname -- "$core")"
(
    cd "$root/core"
    "$go_command" build \
        -tags "with_gvisor,with_quic,with_wireguard,with_utls,with_clash_api" \
        -trimpath \
        -ldflags "-X github.com/sagernet/sing-box/constant.Version=smart-box-${version}-core-${upstream_version} -s -w -buildid=" \
        -o "$core" \
        ./cmd/sing-box
)

exec "$root/linux/build-package.sh"
