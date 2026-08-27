#!/bin/sh
set -eu

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$source_dir/.." && pwd)
dist_dir="$project_dir/dist"
version_file="$project_dir/VERSION"
if [ -f "$version_file" ]; then
    version=$(tr -d '\r\n' < "$version_file")
else
    version=$(sed -n 's/^APP_VERSION = "\([^"]*\)"$/\1/p' "$source_dir/smart_box_backend.py" | head -n 1)
    if [ -z "$version" ]; then
        existing_package=$(find "$dist_dir" -mindepth 1 -maxdepth 1 -type d \
            -name 'smart-box-*-linux-x86_64' -print 2>/dev/null | LC_ALL=C sort | head -n 1)
        case "$existing_package" in
            "$dist_dir"/smart-box-*-linux-x86_64)
                version=${existing_package#"$dist_dir"/smart-box-}
                version=${version%-linux-x86_64}
                ;;
        esac
    fi
fi
case "$version" in
    *[!0-9A-Za-z.+-]*|'')
        printf '%s\n' "invalid package version: $version" >&2
        exit 1
        ;;
esac
package_name="smart-box-${version}-linux-x86_64"
package_dir="$dist_dir/$package_name"
core="$package_dir/bin/smart-box-core"

mkdir -p "$dist_dir"

[ -x "$core" ] || {
    printf '%s\n' "missing prebuilt core: $core" >&2
    exit 1
}

staging_root=$(mktemp -d "$dist_dir/.smart-box-package.XXXXXX")
staging_dir="$staging_root/$package_name"
backup_root=""

cleanup() {
    if [ -n "$backup_root" ] && [ -d "$backup_root/$package_name" ] && \
       [ ! -e "$package_dir" ]; then
        mv "$backup_root/$package_name" "$package_dir" || true
    fi
    if [ -n "$backup_root" ] && [ -d "$backup_root" ]; then
        rm -rf -- "$backup_root"
    fi
    if [ -n "$staging_root" ] && [ -d "$staging_root" ]; then
        rm -rf -- "$staging_root"
    fi
}
trap cleanup 0
trap 'exit 1' 1 2 15

install -d "$staging_dir/bin" "$staging_dir/lib" "$staging_dir/config" \
    "$staging_dir/systemd" "$staging_dir/icons"
install -m 0755 "$core" "$staging_dir/bin/smart-box-core"
install -m 0755 "$source_dir/smart-box" "$staging_dir/bin/smart-box"
install -m 0755 "$source_dir/smart-box-profile" "$staging_dir/bin/smart-box-profile"
install -m 0644 "$source_dir/smart_box_backend.py" "$staging_dir/lib/smart_box_backend.py"
install -m 0644 "$source_dir/smart_box_linux.py" "$staging_dir/lib/smart_box_linux.py"
install -m 0644 "$source_dir/smart-box.desktop" "$staging_dir/config/smart-box.desktop"
install -m 0644 "$source_dir/smart-box@.service" "$staging_dir/systemd/smart-box@.service"
install -m 0644 "$source_dir/smart-box-watchdog@.service" "$staging_dir/systemd/smart-box-watchdog@.service"
install -m 0644 "$source_dir/smart-box-unmask@.service" "$staging_dir/systemd/smart-box-unmask@.service"
install -m 0644 "$source_dir/smart-box-cleanup@.service" "$staging_dir/systemd/smart-box-cleanup@.service"
install -m 0644 "$source_dir/smart-box.rules" "$staging_dir/config/smart-box.rules"
install -m 0644 "$source_dir/icons/smart-box.png" "$staging_dir/icons/smart-box.png"
install -m 0755 "$source_dir/install.sh" "$staging_dir/install.sh"
install -m 0755 "$source_dir/uninstall.sh" "$staging_dir/uninstall.sh"
install -m 0644 "$source_dir/README.md" "$staging_dir/README.md"

(
    cd "$staging_dir"
    find README.md install.sh uninstall.sh bin config icons lib systemd -type f -print0 \
        | sort -z \
        | xargs -0 sha256sum > SHA256SUMS

    expected_files=$(printf '%s\n' \
        './README.md' \
        './SHA256SUMS' \
        './bin/smart-box' \
        './bin/smart-box-core' \
        './bin/smart-box-profile' \
        './config/smart-box.desktop' \
        './config/smart-box.rules' \
        './icons/smart-box.png' \
        './install.sh' \
        './lib/smart_box_backend.py' \
        './lib/smart_box_linux.py' \
        './systemd/smart-box-cleanup@.service' \
        './systemd/smart-box-unmask@.service' \
        './systemd/smart-box-watchdog@.service' \
        './systemd/smart-box@.service' \
        './uninstall.sh')
    actual_files=$(find . -type f -print | LC_ALL=C sort)
    if [ "$actual_files" != "$expected_files" ]; then
        printf '%s\n' 'smart-box package manifest contains missing or unexpected files:' >&2
        printf '%s\n' "$actual_files" >&2
        exit 1
    fi
)

if [ -d "$package_dir" ]; then
    backup_root=$(mktemp -d "$dist_dir/.smart-box-backup.XXXXXX")
    mv "$package_dir" "$backup_root/$package_name"
fi
if ! mv "$staging_dir" "$package_dir"; then
    if [ -n "$backup_root" ]; then
        mv "$backup_root/$package_name" "$package_dir" || true
    fi
    printf '%s\n' 'could not replace smart-box package directory' >&2
    exit 1
fi
if [ -n "$backup_root" ]; then
    rm -rf -- "$backup_root"
    backup_root=""
fi
rmdir "$staging_root"
staging_root=""
trap - 0 1 2 15

tarball="$dist_dir/$package_name.tar.gz"
source_date_epoch=${SOURCE_DATE_EPOCH:-0}
case "$source_date_epoch" in
    *[!0-9]*|'')
        printf '%s\n' "invalid SOURCE_DATE_EPOCH: $source_date_epoch" >&2
        exit 1
        ;;
esac
tar --sort=name \
    --mtime="@$source_date_epoch" \
    --owner=0 \
    --group=0 \
    --numeric-owner \
    --pax-option=delete=atime,delete=ctime \
    -C "$dist_dir" \
    -czf "$tarball" \
    "$package_name"
printf '%s\n' "$package_dir" "$tarball"
