#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
version_file="$project_root/VERSION"
linux_file="$project_root/linux/smart_box_backend.py"
android_file="$project_root/android/version.properties"
windows_file="$project_root/windows/SingBoxSmart.Windows.csproj"
rollback_dir=
rollback_active=0

rollback_version_metadata() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ "${rollback_active:-0}" -eq 1 ] && [ -n "${rollback_dir:-}" ]; then
        cp -- "$rollback_dir/$(basename "$version_file")" "$version_file"
        cp -- "$rollback_dir/$(basename "$linux_file")" "$linux_file"
        cp -- "$rollback_dir/$(basename "$android_file")" "$android_file"
        cp -- "$rollback_dir/$(basename "$windows_file")" "$windows_file"
    fi
    if [ -n "${rollback_dir:-}" ]; then
        rm -rf -- "$rollback_dir"
    fi
    exit "$status"
}

usage() {
    cat <<'EOF'
usage: scripts/version-manager.sh <command> [arguments]

commands:
  current                    print the smart-box product version
  check                      verify Linux, Android, and Windows metadata
  validate <version>         validate a SemVer 2.0.0 product version
  bump <version> [--yes]     update product metadata and increment Android code

The upstream core version stays independent. Update it deliberately in
android/version.properties when the core submodule is rebased.
EOF
}

die() {
    printf 'version-manager: %s\n' "$*" >&2
    exit 1
}

require_file() {
    [ -f "$1" ] || die "missing required file: $1"
}

read_trimmed() {
    tr -d '\r\n' < "$1"
}

validate_version() {
    local version=${1:-} prerelease identifier
    local -a prerelease_identifiers=()
    [[ "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$ ]] || \
        die "invalid SemVer 2.0.0 version: $version"
    if [[ "$version" == *-* ]]; then
        prerelease=${version#*-}
        prerelease=${prerelease%%+*}
        IFS=. read -r -a prerelease_identifiers <<< "$prerelease"
        for identifier in "${prerelease_identifiers[@]}"; do
            [[ ! "$identifier" =~ ^[0-9]+$ ]] || [[ "$identifier" = "0" ]] || [[ "$identifier" != 0* ]] || \
                die "numeric prerelease identifiers cannot contain leading zeroes: $version"
        done
    fi
}

property_value() {
    local key=$1
    awk -F= -v key="$key" '$1 == key { value = substr($0, length(key) + 2) } END { if (value == "") exit 1; print value }' "$android_file" || \
        die "missing Android property: $key"
}

xml_value() {
    local tag=$1
    sed -n "s#.*<$tag>\\([^<]*\\)</$tag>.*#\\1#p" "$windows_file" | head -n 1
}

replace_linux_version() {
    local version=$1 temporary
    temporary=$(mktemp "${linux_file}.XXXXXX")
    awk -v version="$version" '
        /^APP_VERSION = "/ {
            print "APP_VERSION = \"" version "\""
            found = 1
            next
        }
        { print }
        END { if (!found) exit 42 }
    ' "$linux_file" > "$temporary" || {
        rm -f -- "$temporary"
        die "could not update APP_VERSION"
    }
    mv -- "$temporary" "$linux_file"
}

replace_android_property() {
    local key=$1 value=$2 temporary
    temporary=$(mktemp "${android_file}.XXXXXX")
    awk -v key="$key" -v value="$value" '
        $0 ~ "^" key "=" {
            print key "=" value
            found = 1
            next
        }
        { print }
        END { if (!found) exit 42 }
    ' "$android_file" > "$temporary" || {
        rm -f -- "$temporary"
        die "could not update Android property: $key"
    }
    mv -- "$temporary" "$android_file"
}

replace_windows_metadata() {
    local version=$1 upstream=$2 temporary
    temporary=$(mktemp "${windows_file}.XXXXXX")
    awk -v version="$version" -v upstream="$upstream" '
        /<Version>[^<]*<\/Version>/ {
            sub(/<Version>[^<]*<\/Version>/, "<Version>" version "</Version>")
            version_found = 1
        }
        /<InformationalVersion>[^<]*<\/InformationalVersion>/ {
            sub(/<InformationalVersion>[^<]*<\/InformationalVersion>/, "<InformationalVersion>smart-box " version " (core " upstream ")</InformationalVersion>")
            info_found = 1
        }
        { print }
        END { if (!version_found || !info_found) exit 42 }
    ' "$windows_file" > "$temporary" || {
        rm -f -- "$temporary"
        die "could not update Windows metadata"
    }
    mv -- "$temporary" "$windows_file"
}

check_versions() {
    local version upstream expected_android linux_version android_smart android_name windows_version windows_info android_code failed=0
    require_file "$version_file"
    require_file "$linux_file"
    require_file "$android_file"
    require_file "$windows_file"

    version=$(read_trimmed "$version_file")
    validate_version "$version"
    upstream=$(property_value UPSTREAM_VERSION)
    expected_android="${version}-core.${upstream}"
    linux_version=$(sed -n 's/^APP_VERSION = "\([^"]*\)"$/\1/p' "$linux_file" | head -n 1)
    android_smart=$(property_value SMART_VERSION)
    android_name=$(property_value VERSION_NAME)
    android_code=$(property_value VERSION_CODE)
    windows_version=$(xml_value Version)
    windows_info=$(xml_value InformationalVersion)

    printf 'VERSION=%s\n' "$version"
    printf 'UPSTREAM_VERSION=%s\n' "$upstream"

    for item in \
        "Linux APP_VERSION|$linux_version|$version" \
        "Android SMART_VERSION|$android_smart|$version" \
        "Android VERSION_NAME|$android_name|$expected_android" \
        "Windows Version|$windows_version|$version" \
        "Windows InformationalVersion|$windows_info|smart-box $version (core $upstream)"; do
        IFS='|' read -r label actual expected <<EOF
$item
EOF
        if [ "$actual" = "$expected" ]; then
            printf 'OK %s=%s\n' "$label" "$actual"
        else
            printf 'MISMATCH %s=%s (expected %s)\n' "$label" "$actual" "$expected" >&2
            failed=1
        fi
    done

    if [[ ! "$android_code" =~ ^[1-9][0-9]*$ ]]; then
        printf 'MISMATCH Android VERSION_CODE=%s (expected a positive integer)\n' "$android_code" >&2
        failed=1
    else
        printf 'OK Android VERSION_CODE=%s\n' "$android_code"
    fi

    [ "$failed" -eq 0 ] || return 1
}

bump_version() {
    local version=${1:-} confirmation=${2:-} current upstream code next_code
    [ -n "$version" ] || die "missing version"
    [ -z "$confirmation" ] || [ "$confirmation" = "--yes" ] || die "unknown bump option: $confirmation"
    validate_version "$version"
    check_versions >/dev/null
    current=$(read_trimmed "$version_file")
    [ "$current" != "$version" ] || die "VERSION already is $version"

    if [ "$confirmation" != "--yes" ]; then
        printf 'Update smart-box product version from %s to %s? [y/N] ' "$current" "$version"
        read -r answer
        case "$answer" in
            y|Y|yes|YES) ;;
            *) printf 'version-manager: cancelled\n'; return 0 ;;
        esac
    fi

    upstream=$(property_value UPSTREAM_VERSION)
    code=$(property_value VERSION_CODE)
    next_code=$((10#$code + 1))
    rollback_dir=$(mktemp -d "${TMPDIR:-/tmp}/smart-box-version.XXXXXX")
    rollback_active=1
    cp -- "$version_file" "$linux_file" "$android_file" "$windows_file" "$rollback_dir/"
    trap rollback_version_metadata EXIT HUP INT TERM

    printf '%s\n' "$version" > "$version_file"
    replace_linux_version "$version"
    replace_android_property SMART_VERSION "$version"
    replace_android_property VERSION_NAME "${version}-core.${upstream}"
    replace_android_property VERSION_CODE "$next_code"
    replace_windows_metadata "$version" "$upstream"
    check_versions
    rollback_active=0
    trap - EXIT HUP INT TERM
    rm -rf -- "$rollback_dir"
    rollback_dir=
    printf 'Updated smart-box product version to %s; Android VERSION_CODE=%s\n' "$version" "$next_code"
}

case "${1:-}" in
    current)
        [ "$#" -eq 1 ] || { usage >&2; exit 64; }
        require_file "$version_file"
        printf '%s\n' "$(read_trimmed "$version_file")"
        ;;
    check)
        [ "$#" -eq 1 ] || { usage >&2; exit 64; }
        check_versions
        ;;
    validate)
        [ "$#" -eq 2 ] || { usage >&2; exit 64; }
        validate_version "$2"
        printf 'valid SemVer: %s\n' "$2"
        ;;
    bump)
        [ "$#" -eq 2 ] || [ "$#" -eq 3 ] || { usage >&2; exit 64; }
        bump_version "$2" "${3:-}"
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        usage >&2
        exit 64
        ;;
esac
