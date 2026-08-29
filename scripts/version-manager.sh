#!/bin/bash
# smart-box 版本管理工具
# 用于统一更新所有组件的版本号

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

usage() {
    cat << EOF
用法: $0 <命令> [参数]

命令:
    bump <版本号>        更新所有组件到指定版本号
    current              显示当前版本号
    check                检查版本号一致性
    validate <版本号>    验证版本号格式

示例:
    $0 bump 0.2.0
    $0 current
    $0 check
    $0 validate 1.0.0-rc.1

版本号格式: MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]
EOF
    exit 1
}

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

# 验证版本号格式（Semver 2.0.0）
validate_version() {
    local version=$1
    local semver_regex='^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$'

    if [[ ! $version =~ $semver_regex ]]; then
        log_error "无效的版本号格式: $version\n应符合 Semver 2.0.0: MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]"
    fi

    log_info "版本号格式有效: $version"
}

# 从 VERSION 文件读取当前版本
get_current_version() {
    if [[ -f "$PROJECT_ROOT/VERSION" ]]; then
        cat "$PROJECT_ROOT/VERSION"
    else
        echo "unknown"
    fi
}

# 更新 VERSION 文件
update_version_file() {
    local version=$1
    echo "$version" > "$PROJECT_ROOT/VERSION"
    log_info "已更新 VERSION 文件: $version"
}

# 核心版本来源（经 2026-08-28 核对真实路径）
#   core:      构建期通过 -ldflags 注入 constant.Version，仓库内无静态版本号
#   linux:     linux/smart_box_backend.py 的 APP_VERSION
#   android:   android/version.properties 的 SMART_VERSION / VERSION_NAME / VERSION_CODE
#   converter: 无独立版本常量，跟随产品版本
#   windows:   windows/SingBoxSmart.Windows.csproj

# Core 版本 (Go, 构建期注入，无需改文件)
update_core_version() {
    local version=$1
    log_info "Core 版本由构建期 -ldflags 注入 constant.Version，无静态文件需更新"
}

# 更新 Linux 版本 (Python)
update_linux_version() {
    local version=$1
    local backend_file="$PROJECT_ROOT/linux/smart_box_backend.py"

    if [[ ! -f "$backend_file" ]]; then
        log_warn "Linux backend 不存在，跳过: $backend_file"
        return
    fi

    sed -i "s/^APP_VERSION = \"[^\"]*\"/APP_VERSION = \"$version\"/" "$backend_file"
    log_info "已更新 Linux 版本: $backend_file (APP_VERSION)"
}

# 更新 Android 版本 (version.properties)
update_android_version() {
    local version=$1
    local props_file="$PROJECT_ROOT/android/version.properties"

    if [[ ! -f "$props_file" ]]; then
        log_warn "Android version.properties 不存在，跳过: $props_file"
        return
    fi

    local upstream
    upstream=$(grep '^UPSTREAM_VERSION=' "$props_file" | cut -d= -f2)

    sed -i "s/^SMART_VERSION=.*/SMART_VERSION=$version/" "$props_file"
    if [[ -n "$upstream" ]]; then
        sed -i "s/^VERSION_NAME=.*/VERSION_NAME=$version-core.$upstream/" "$props_file"
    fi

    # 递增 versionCode（Android 要求单调递增，不可重置）
    local current_code
    current_code=$(grep '^VERSION_CODE=' "$props_file" | cut -d= -f2)
    if [[ -n "$current_code" ]]; then
        local new_code=$((current_code + 1))
        sed -i "s/^VERSION_CODE=.*/VERSION_CODE=$new_code/" "$props_file"
        log_info "已更新 Android 版本: SMART_VERSION=$version, VERSION_CODE=$new_code"
    else
        log_info "已更新 Android 版本: SMART_VERSION=$version"
    fi
}

# 更新 Windows 版本 (csproj)
update_windows_version() {
    local version=$1
    local csproj_file="$PROJECT_ROOT/windows/SingBoxSmart.Windows.csproj"

    if [[ ! -f "$csproj_file" ]]; then
        log_warn "Windows csproj 不存在，跳过: $csproj_file"
        return
    fi

    # 提取纯数字版本（AssemblyVersion 不接受 prerelease 后缀）
    local numeric_version
    numeric_version=$(echo "$version" | grep -oE '^[0-9]+\.[0-9]+\.[0-9]+')

    if grep -q "<Version>" "$csproj_file"; then
        sed -i "s|<Version>[^<]*</Version>|<Version>$version</Version>|" "$csproj_file"
        sed -i "s|<AssemblyVersion>[^<]*</AssemblyVersion>|<AssemblyVersion>$numeric_version</AssemblyVersion>|" "$csproj_file"
        sed -i "s|<FileVersion>[^<]*</FileVersion>|<FileVersion>$numeric_version</FileVersion>|" "$csproj_file"
        log_info "已更新 Windows 版本: $csproj_file"
    else
        log_warn "Windows csproj 中没有 <Version> 元素，跳过（需手动确认版本注入方式）"
    fi
}

# Converter 版本（无独立常量，跟随产品版本）
update_converter_version() {
    local version=$1
    log_info "Converter 无独立版本常量，跟随 VERSION 文件（$version）"
}

# 检查版本一致性
check_version_consistency() {
    log_info "检查版本号一致性..."

    local expected
    expected=$(get_current_version)
    log_info "VERSION 文件: $expected"

    if [[ "$expected" == "unknown" ]]; then
        log_error "VERSION 文件缺失，无法比对"
        return 1
    fi

    local inconsistent=0
    local checked=0

    # 检查 Linux (linux/smart_box_backend.py APP_VERSION)
    local backend_file="$PROJECT_ROOT/linux/smart_box_backend.py"
    if [[ -f "$backend_file" ]]; then
        checked=$((checked + 1))
        local linux_version
        linux_version=$(grep '^APP_VERSION = ' "$backend_file" | sed 's/.*"\(.*\)".*/\1/')
        if [[ "$linux_version" != "$expected" ]]; then
            log_warn "Linux 版本不一致: '$linux_version' (预期: $expected)"
            inconsistent=1
        else
            log_info "✓ Linux 版本一致: $linux_version"
        fi
    else
        log_warn "未找到 Linux 版本文件: $backend_file"
        inconsistent=1
    fi

    # 检查 Android (android/version.properties SMART_VERSION)
    local props_file="$PROJECT_ROOT/android/version.properties"
    if [[ -f "$props_file" ]]; then
        checked=$((checked + 1))
        local android_version
        android_version=$(grep '^SMART_VERSION=' "$props_file" | cut -d= -f2)
        if [[ "$android_version" != "$expected" ]]; then
            log_warn "Android 版本不一致: '$android_version' (预期: $expected)"
            inconsistent=1
        else
            log_info "✓ Android 版本一致: $android_version"
        fi

        # 交叉检查 VERSION_NAME 是否包含 SMART_VERSION
        local version_name upstream
        version_name=$(grep '^VERSION_NAME=' "$props_file" | cut -d= -f2)
        upstream=$(grep '^UPSTREAM_VERSION=' "$props_file" | cut -d= -f2)
        local want_name="$expected-core.$upstream"
        if [[ "$version_name" != "$want_name" ]]; then
            log_warn "Android VERSION_NAME 不匹配: '$version_name' (预期: $want_name)"
            inconsistent=1
        else
            log_info "✓ Android VERSION_NAME 一致: $version_name"
        fi
    else
        log_warn "未找到 Android 版本文件: $props_file"
        inconsistent=1
    fi

    # Check the Windows project when it is present.  This keeps the product
    # version gate honest even though Windows builds run on another host.
    local windows_file="$PROJECT_ROOT/windows/SingBoxSmart.Windows.csproj"
    if [[ -f "$windows_file" ]]; then
        checked=$((checked + 1))
        local windows_version
        windows_version=$(sed -n 's:.*<Version>\([^<]*\)</Version>.*:\1:p' "$windows_file" | head -n 1)
        if [[ "$windows_version" != "$expected" ]]; then
            log_warn "Windows 版本不一致: '$windows_version' (预期: $expected)"
            inconsistent=1
        else
            log_info "✓ Windows 版本一致: $windows_version"
        fi
    else
        log_warn "未找到 Windows 版本文件: $windows_file"
        inconsistent=1
    fi

    # Core 为构建期注入，只做提示
    log_info "· Core 版本构建期注入（constant.Version），不做静态比对"
    log_info "· Converter 无独立版本常量，跟随 VERSION"

    if [[ $checked -eq 0 ]]; then
        log_error "没有找到任何可比对的版本文件，检查未生效（不是通过）"
        return 1
    fi

    if [[ $inconsistent -eq 0 ]]; then
        log_info "已比对 $checked 个组件，版本号一致 ✓"
        return 0
    else
        log_error "发现版本号不一致，请运行 'bump' 命令统一更新"
        return 1
    fi
}

# 更新所有组件版本
bump_version() {
    local new_version=$1

    validate_version "$new_version"

    local current_version=$(get_current_version)
    log_info "当前版本: $current_version"
    log_info "目标版本: $new_version"

    read -p "确认更新所有组件版本到 $new_version? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_warn "已取消版本更新"
        exit 0
    fi

    log_info "开始更新版本号..."

    update_version_file "$new_version"
    update_core_version "$new_version"
    update_linux_version "$new_version"
    update_android_version "$new_version"
    update_windows_version "$new_version"
    update_converter_version "$new_version"

    log_info "版本更新完成！"
    echo
    log_info "下一步操作:"
    echo "  1. 检查变更: git diff"
    echo "  2. 更新 CHANGELOG.md"
    echo "  3. 提交变更: git commit -am 'chore: bump version to $new_version'"
    echo "  4. 创建发布分支或标签（如适用）"
}

# 主命令处理
case "${1:-}" in
    bump)
        if [[ -z "${2:-}" ]]; then
            log_error "缺少版本号参数"
            usage
        fi
        bump_version "$2"
        ;;
    current)
        version=$(get_current_version)
        echo "$version"
        ;;
    check)
        check_version_consistency
        ;;
    validate)
        if [[ -z "${2:-}" ]]; then
            log_error "缺少版本号参数"
            usage
        fi
        validate_version "$2"
        ;;
    *)
        usage
        ;;
esac
