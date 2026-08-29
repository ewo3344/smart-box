#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$script_dir/.." && pwd)
go_command=${GO:-go}
version=$(tr -d '\r\n' < "$root/VERSION")
upstream_version=$(sed -n 's/^UPSTREAM_VERSION=//p' "$root/android/version.properties" | head -n 1)
package_dir="$root/dist/smart-box-${version}-linux-x86_64"
core="$package_dir/bin/smart-box-core"

toolchain_file="$root/TOOLCHAIN_VERSION"
[ -f "$toolchain_file" ] || {
    printf '%s\n' "missing Go toolchain pin: $toolchain_file" >&2
    exit 1
}
toolchain_version=$(awk '
    NF != 1 { exit 1 }
    { value = $1; count++ }
    END { if (count != 1) exit 1; print value }
' "$toolchain_file") || {
    printf '%s\n' "invalid $toolchain_file (expected one goX.Y.Z line)" >&2
    exit 1
}
printf '%s\n' "$toolchain_version" | grep -Eq '^go[0-9]+\.[0-9]+\.[0-9]+$' || {
    printf '%s\n' "invalid Go toolchain pin: $toolchain_version" >&2
    exit 1
}

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
    env GOTOOLCHAIN="$toolchain_version" "$go_command" build \
        -tags "with_gvisor,with_quic,with_wireguard,with_utls,with_clash_api" \
        -trimpath \
        -ldflags "-X github.com/sagernet/sing-box/constant.Version=smart-box-${version}-core-${upstream_version} -s -w -buildid=" \
        -o "$core" \
        ./cmd/sing-box
)

exec "$root/linux/build-package.sh"
