#!/usr/bin/env bash
# Fail-closed gate for android/core gitlinks.
# One-way publish flow (documented in UPSTREAMS.md):
#   工作树 → 快照 → push fork → 更新 gitlink
# This script never stages android or core. --check only validates pointers.
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(CDPATH= cd -- "$script_dir/.." && pwd)

DEFAULT_CORE_REMOTE=https://github.com/ewo3344/smart-box-core.git
DEFAULT_ANDROID_REMOTE=https://github.com/ewo3344/smart-box-android.git
CORE_SMART_PATH=protocol/group/smart.go
ANDROID_GRADLE_PATH=app/build.gradle.kts
ANDROID_APPLICATION_ID='io.nekohasekai.sfa.smartbox'

mode=
root=$project_root
core_remote=$DEFAULT_CORE_REMOTE
android_remote=$DEFAULT_ANDROID_REMOTE
core_commit=
android_commit=

usage() {
    cat <<'EOF'
usage: scripts/publish-submodules.sh --check [options]

Validate that the superproject gitlinks for core and android:
  1. are reachable as refs on the fork remote (not a private/unpushed commit
     and not a SagerNet baseline the fork cannot see)
  2. contain smart-box identity (core/protocol/group/smart.go, or the Android
     applicationId io.nekohasekai.sfa.smartbox)

This command never runs git add android, git add core, git add ., git add -A,
or git commit -a.

options:
  --root DIR              superproject root (default: repository root)
  --core-remote URL       fork remote for core
  --android-remote URL    fork remote for android
  --core-commit SHA       override HEAD:core gitlink
  --android-commit SHA    override HEAD:android gitlink
EOF
}

die() {
    printf 'publish-submodules: %s\n' "$*" >&2
    exit 1
}

in_repo() {
    git -C "$root" "$@"
}

gitlink_sha() {
    local path=$1
    local mode sha rest
    # ls-tree HEAD <path> → "160000 commit <sha>\t<path>"
    rest=$(in_repo ls-tree HEAD -- "$path") || die "missing gitlink path $path"
    [[ "$rest" == 160000\ commit\ * ]] || die "HEAD:$path is not a gitlink"
    sha=${rest#160000 commit }
    sha=${sha%%$'\t'*}
    sha=${sha%% *}
    [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "invalid gitlink sha for $path: $sha"
    printf '%s\n' "$sha"
}

remote_has_commit() {
    local remote=$1
    local sha=$2
    local line remote_sha
    while IFS= read -r line; do
        remote_sha=${line%%[[:space:]]*}
        if [[ "$remote_sha" == "$sha" ]]; then
            return 0
        fi
    done < <(git ls-remote "$remote" 2>/dev/null || true)
    return 1
}

ensure_commit_object() {
    local remote=$1
    local sha=$2
    if in_repo cat-file -e "${sha}^{commit}" 2>/dev/null; then
        return 0
    fi
    git -C "$root" fetch --depth=1 "$remote" "$sha" >/dev/null 2>&1 || \
        die "cannot fetch $sha from $remote"
    in_repo cat-file -e "${sha}^{commit}" 2>/dev/null || \
        die "commit $sha missing after fetch from $remote"
}

tree_has_smart_core() {
    local sha=$1
    in_repo cat-file -e "${sha}:${CORE_SMART_PATH}" 2>/dev/null
}

tree_has_smart_android() {
    local sha=$1
    local blob
    in_repo cat-file -e "${sha}:${ANDROID_GRADLE_PATH}" 2>/dev/null || return 1
    blob=$(in_repo cat-file -p "${sha}:${ANDROID_GRADLE_PATH}")
    printf '%s\n' "$blob" | grep -F -q "$ANDROID_APPLICATION_ID"
}

check_one() {
    local name=$1
    local path=$2
    local remote=$3
    local sha=$4
    local kind=$5

    printf 'publish-submodules: checking %s gitlink %s\n' "$name" "$sha"

    if ! remote_has_commit "$remote" "$sha"; then
        die "$name gitlink $sha is not reachable from fork remote $remote"
    fi
    ensure_commit_object "$remote" "$sha"

    case "$kind" in
        core)
            if ! tree_has_smart_core "$sha"; then
                die "$name gitlink $sha tree lacks ${CORE_SMART_PATH}"
            fi
            ;;
        android)
            if ! tree_has_smart_android "$sha"; then
                die "$name gitlink $sha tree lacks Android smart-box identity (${ANDROID_APPLICATION_ID})"
            fi
            ;;
        *)
            die "unknown gitlink kind: $kind"
            ;;
    esac

    printf 'publish-submodules: %s OK\n' "$name"
}

run_check() {
    [[ -d "$root/.git" ]] || die "not a git repository: $root"
    if [[ -z "$core_commit" ]]; then
        core_commit=$(gitlink_sha core)
    fi
    if [[ -z "$android_commit" ]]; then
        android_commit=$(gitlink_sha android)
    fi
    check_one core core "$core_remote" "$core_commit" core
    check_one android android "$android_remote" "$android_commit" android
    printf 'publish-submodules: CHECK PASS\n'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)
            mode=check
            shift
            ;;
        --root)
            [[ $# -ge 2 ]] || die "missing value for --root"
            root=$2
            shift 2
            ;;
        --core-remote)
            [[ $# -ge 2 ]] || die "missing value for --core-remote"
            core_remote=$2
            shift 2
            ;;
        --android-remote)
            [[ $# -ge 2 ]] || die "missing value for --android-remote"
            android_remote=$2
            shift 2
            ;;
        --core-commit)
            [[ $# -ge 2 ]] || die "missing value for --core-commit"
            core_commit=$2
            shift 2
            ;;
        --android-commit)
            [[ $# -ge 2 ]] || die "missing value for --android-commit"
            android_commit=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            die "unknown argument: $1"
            ;;
    esac
done

[[ -n "$mode" ]] || {
    usage >&2
    die "required: --check"
}

case "$mode" in
    check)
        run_check
        ;;
    *)
        die "unsupported mode: $mode"
        ;;
esac
