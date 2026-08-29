#!/bin/sh
set -eu

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$source_dir/.." && pwd)
dist_dir="$project_dir/dist"
version_file="$project_dir/VERSION"
[ -r "$version_file" ] || {
    printf '%s\n' "missing product version: $version_file" >&2
    exit 1
}
product_version=$(sed -n '1p' "$version_file" | tr -d '[:space:]')
printf '%s\n' "$product_version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$' || {
    printf '%s\n' "invalid product version: $product_version" >&2
    exit 1
}
package_name="smart-box-${product_version}-linux-x86_64"
package_dir="$dist_dir/$package_name"
core="$package_dir/bin/smart-box-core"

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

backup_root=$(mktemp -d "$dist_dir/.smart-box-backup.XXXXXX")
mv "$package_dir" "$backup_root/$package_name"
if ! mv "$staging_dir" "$package_dir"; then
    mv "$backup_root/$package_name" "$package_dir" || true
    printf '%s\n' 'could not replace smart-box package directory' >&2
    exit 1
fi
rm -rf -- "$backup_root"
backup_root=""
rmdir "$staging_root"
staging_root=""
trap - 0 1 2 15

tarball="$dist_dir/$package_name.tar.gz"
tar -C "$dist_dir" -czf "$tarball" "$package_name"
printf '%s\n' "$package_dir" "$tarball"
