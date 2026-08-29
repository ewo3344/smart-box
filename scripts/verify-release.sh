#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
require_fail_open=1
run_android=0
config_dir_arg=

usage() {
    printf '%s\n' "usage: $0 [--allow-live] [--android] [--config-dir DIR]"
}

while [ "$#" -gt 0 ]; do
    argument=$1
    case "$argument" in
        --allow-live) require_fail_open=0 ;;
        --android) run_android=1 ;;
        --config-dir)
            [ "$#" -ge 2 ] || { usage >&2; exit 64; }
            config_dir_arg=$2
            shift
            ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 64 ;;
    esac
    shift
done

run() {
    printf '\n>>> %s\n' "$*"
    "$@"
}

cd "$root"

toolchain_file="$root/TOOLCHAIN_VERSION"
if [ ! -f "$toolchain_file" ]; then
    printf '%s\n' "release gate: missing $toolchain_file" >&2
    exit 1
fi
toolchain_version=$(awk '
    NF != 1 { exit 1 }
    { value = $1; count++ }
    END { if (count != 1) exit 1; print value }
' "$toolchain_file") || {
    printf '%s\n' "release gate: invalid $toolchain_file (expected one goX.Y.Z line)" >&2
    exit 1
}
if ! printf '%s\n' "$toolchain_version" | grep -Eq '^go[0-9]+\.[0-9]+\.[0-9]+$'; then
    printf '%s\n' "release gate: invalid Go toolchain pin: $toolchain_version" >&2
    exit 1
fi

version_file="$root/VERSION"
if [ ! -r "$version_file" ]; then
    printf '%s\n' "release gate: missing $version_file" >&2
    exit 1
fi
product_version=$(sed -n '1p' "$version_file" | tr -d '[:space:]')
if ! printf '%s\n' "$product_version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$'; then
    printf '%s\n' "release gate: invalid product version: $product_version" >&2
    exit 1
fi
package_name="smart-box-${product_version}-linux-x86_64"
package_dir="$root/dist/$package_name"

run python3 -m compileall -q linux
test_home_dir=$(mktemp -d "${TMPDIR:-/tmp}/smart-box-tests.XXXXXX")
trap 'rm -rf -- "$test_home_dir"' EXIT HUP INT TERM
mkdir -p "$test_home_dir/.config/smart-box" "$test_home_dir/.local/state/smart-box"
source_home=${HOME:-}
source_config_dir=${config_dir_arg:-${SMART_BOX_CONFIG_DIR:-}}
if [ -z "$source_config_dir" ]; then
    source_config_dir="$root/test-fixtures/release-gate"
fi
source_gopath=${GOPATH:-}
if [ -z "$source_gopath" ] && command -v go >/dev/null 2>&1; then
    source_gopath=$(go env GOPATH)
fi
profile_source="$source_config_dir/profile.json"
runtime_source="$source_config_dir/runtime.json"
if [ -f "$source_config_dir/profile.fixture.json" ] &&
   [ -f "$source_config_dir/runtime.fixture.json" ] &&
   [ ! -f "$profile_source" ] && [ ! -f "$runtime_source" ]; then
    profile_source="$source_config_dir/profile.fixture.json"
    runtime_source="$source_config_dir/runtime.fixture.json"
fi
if [ ! -f "$profile_source" ] || [ ! -f "$runtime_source" ]; then
    # A caller-provided directory takes precedence.  On a developer machine,
    # fall back to the private profile only when no checked-in fixture exists.
    private_config_dir="$source_home/.config/smart-box"
    if [ -z "$config_dir_arg" ] && [ -z "${SMART_BOX_CONFIG_DIR:-}" ] &&
       [ -f "$private_config_dir/profile.json" ] &&
       [ -f "$private_config_dir/runtime.json" ]; then
        source_config_dir="$private_config_dir"
        profile_source="$source_config_dir/profile.json"
        runtime_source="$source_config_dir/runtime.json"
    else
        printf '%s\n' "release gate: missing source configs under $source_config_dir" >&2
        exit 1
    fi
fi
export HOME="$test_home_dir"
export XDG_CONFIG_HOME="$test_home_dir/.config"
export XDG_STATE_HOME="$test_home_dir/.local/state"
if [ -n "$source_gopath" ]; then
    # Keep the downloaded, version-pinned toolchain cache available while all
    # application state remains isolated in the temporary HOME.
    export GOPATH="$source_gopath"
fi
run env \
    PYTHONPATH=linux \
    QT_QPA_PLATFORM=offscreen \
    python3 -m unittest discover -s linux/tests -p 'test*.py' -q
install -m 0600 "$profile_source" "$test_home_dir/.config/smart-box/profile.json"
install -m 0600 "$runtime_source" "$test_home_dir/.config/smart-box/runtime.json"
run sh -n linux/build-package.sh linux/install.sh linux/uninstall.sh linux/smart-box linux/smart-box-profile
run systemd-analyze verify linux/smart-box@.service linux/smart-box-watchdog@.service linux/smart-box-cleanup@.service linux/smart-box-unmask@.service

if [ -x "$package_dir/bin/smart-box-core" ]; then
    core="$package_dir/bin/smart-box-core"
elif [ "${SMART_BOX_ALLOW_INSTALLED_CORE:-0}" = 1 ] &&
      [ -x /usr/local/lib/smart-box/smart-box-core ]; then
    core=/usr/local/lib/smart-box/smart-box-core
else
    printf '%s\n' "release gate: no Core for $package_name (build the matching dist package first)" >&2
    exit 1
fi

run "$core" check --disable-color -D "$HOME/.local/state/smart-box" -c "$HOME/.config/smart-box/profile.json"
run "$core" check --disable-color -D "$HOME/.local/state/smart-box" -c "$HOME/.config/smart-box/runtime.json"

if [ -x "$root/converter/smart-box-converter-linux-arm64" ] || command -v go >/dev/null 2>&1; then
    (cd "$root/converter" && run env SMART_BOX_CORE="$core" GOTOOLCHAIN="$toolchain_version" go test ./...)
fi

run "$root/linux/build-package.sh"
(cd "$package_dir" && run sha256sum -c SHA256SUMS)

for source in linux/smart_box_backend.py linux/smart_box_linux.py; do
    installed=/usr/local/lib/smart-box/$(basename "$source")
    package="$package_dir/lib/$(basename "$source")"
    if [ "${SMART_BOX_VERIFY_INSTALLED:-0}" = 1 ] && [ -e "$installed" ]; then
        run cmp "$source" "$installed"
    fi
    run cmp "$source" "$package"
done

if [ "$run_android" -eq 1 ]; then
    run sh -c "cd '$root/android' && ./gradlew testOtherDebugUnitTest"
fi

if [ "$require_fail_open" -eq 1 ]; then
    if systemctl is-active --quiet smart-box@"$(id -un)".service; then
        printf '%s\n' 'release gate: smart-box service is still active' >&2
        exit 1
    fi
    if systemctl is-active --quiet smart-box-watchdog@"$(id -un)".service; then
        printf '%s\n' 'release gate: smart-box watchdog is still active' >&2
        exit 1
    fi
    if ip link show SmartBox >/dev/null 2>&1; then
        printf '%s\n' 'release gate: SmartBox interface is still present' >&2
        exit 1
    fi
    run curl --noproxy '*' -4 -L --connect-timeout 5 --max-time 10 -sS -o /dev/null \
        -w 'direct_baidu=%{http_code} %{time_total}\n' https://www.baidu.com/
fi

printf '%s\n' 'release gate: PASS'
