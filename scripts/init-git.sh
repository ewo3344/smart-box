#!/bin/bash
# Git 仓库初始化脚本
# 为 smart-box 项目设置完整的 Git 版本控制

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Serialize initialization so two interrupted runs cannot mix their indexes.
exec 9>"$PROJECT_ROOT/.git-init.lock"
if ! flock -n 9; then
    echo "[ERROR] another Git initialization is already running" >&2
    exit 1
fi

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

cd "$PROJECT_ROOT"

# If this invocation creates an unborn repository and a later gate fails,
# remove only the repository created by this invocation.  This prevents a
# failed first run from masquerading as a resumable, half-initialized repo.
CREATED_REPOSITORY=0
ROLLBACK_UNBORN_REPOSITORY=0
rollback_unborn_repository() {
    rc=$?
    if [[ $ROLLBACK_UNBORN_REPOSITORY -eq 1 && $CREATED_REPOSITORY -eq 1 ]] &&
       ! git rev-parse --verify HEAD >/dev/null 2>&1; then
        rm -rf -- "$PROJECT_ROOT/.git"
        log_warn "初始化门禁失败，已移除本次创建的未提交 .git 目录"
    fi
    exit "$rc"
}
trap rollback_unborn_repository EXIT HUP INT TERM

# 允许在一次提交中断后重新运行；已有提交时不触碰历史。
if [[ -e "$PROJECT_ROOT/.git" ]]; then
    if git rev-parse --verify HEAD >/dev/null 2>&1; then
        if git rev-parse --verify refs/tags/v0.1.0 >/dev/null 2>&1; then
            log_info "Git 初始化已完成（已有 HEAD 与 v0.1.0），无需重复提交"
            exit 0
        fi
        log_error "当前目录已经包含提交历史，请使用常规提交流程"
        exit 1
    fi
    log_warn "检测到未完成的 Git 初始化，继续执行恢复流程"
else
    log_info "开始初始化 Git 仓库..."
    # Set this before invoking git so even a partially-created .git directory
    # from a failed `git init` can be removed by the EXIT trap.
    CREATED_REPOSITORY=1
    git init
    ROLLBACK_UNBORN_REPOSITORY=1
    log_info "Git 仓库已初始化"
fi

# 设置主分支名称
git branch -M main
log_info "默认分支设置为 main"

# 创建 .gitignore（已存在则保留，不覆盖）
if [[ -f .gitignore ]]; then
    log_warn "已存在 .gitignore，保留现有内容不覆盖"
    log_warn "如需使用脚本内置模板，请先备份并删除现有文件"
    SKIP_GITIGNORE=1
else
    SKIP_GITIGNORE=0
fi

if [[ $SKIP_GITIGNORE -eq 0 ]]; then
log_info "创建 .gitignore 文件..."
cat > .gitignore << 'EOF'
# 构建产物
dist/
build/
*.apk
*.aar
*.exe
*.tar.gz
*.zip

# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
env/
venv/
.venv/
*.egg-info/
.eggs/

# Go
*.exe~
*.dll
*.so
*.dylib
*.test
*.out
vendor/

# Android
*.iml
.gradle
local.properties
.idea/
.DS_Store
/build
/captures
.externalNativeBuild
.cxx
*.apk
*.ap_
*.aab
output.json

# Windows
*.user
*.suo
*.cache
bin/
obj/
packages/

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# 日志和数据库
*.log
*.db
*.db-shm
*.db-wal

# 临时文件
*.tmp
*.bak
*.orig
.DS_Store
Thumbs.db

# 敏感信息
config.json
*-config.json
service-account-credentials.json
*.keystore
*.jks
*.pem
*.key
*.p12
*.pfx
*.idsig

# 测试和验证产物
verification/*/
crash-fix-*/
*-fix-*/
*-delivery-*/

# 备份和存档
*.zip
*-handoff-*.zip
*.bak-*

# 缓存
cache/
.cache/
EOF

log_info ".gitignore 已创建"
fi

# 创建 .gitattributes（已存在则保留）
if [[ -f .gitattributes ]]; then
    log_warn "已存在 .gitattributes，保留现有内容不覆盖"
else
log_info "创建 .gitattributes 文件..."
cat > .gitattributes << 'EOF'
# 自动检测文本文件并标准化行尾
* text=auto

# 明确指定文本文件使用 LF
*.sh text eol=lf
*.py text eol=lf
*.go text eol=lf
*.java text eol=lf
*.kt text eol=lf
*.cs text eol=lf
*.md text eol=lf
*.json text eol=lf
*.xml text eol=lf
*.yaml text eol=lf
*.yml text eol=lf
*.toml text eol=lf
*.gradle text eol=lf
*.properties text eol=lf
Makefile text eol=lf

# Windows 脚本使用 CRLF
*.bat text eol=crlf
*.cmd text eol=crlf
*.ps1 text eol=crlf

# 二进制文件
*.png binary
*.jpg binary
*.jpeg binary
*.gif binary
*.ico binary
*.pdf binary
*.apk binary
*.aar binary
*.jar binary
*.so binary
*.dll binary
*.exe binary
*.zip binary
*.tar.gz binary
*.db binary
EOF

log_info ".gitattributes 已创建"
fi

# 提交前的安全检查：确认敏感文件确实被忽略
log_info "检查敏感文件是否被正确忽略..."
SENSITIVE_LEAK=0
for pattern in \
    "config.json" "runtime.json" "profile.json" "settings.json" \
    "*.keystore" "*.jks" "*.pem" "*.key" "*.p12" "*.pfx" "*.idsig" \
    "*.asc" "*.gpg" "id_rsa" "id_ed25519" "id_ecdsa" \
    ".env" ".env.*" "service-account-credentials.json"; do
    # 查找工作区内匹配且未被忽略的文件
    while IFS= read -r -d '' found; do
        [[ -z "$found" ]] && continue
        # --no-index is required for paths that live inside nested submodules;
        # this check validates the ignore rules before the root index exists.
        if ! git check-ignore -q --no-index "$found" 2>/dev/null; then
            log_error "敏感文件未被忽略: $found"
            SENSITIVE_LEAK=1
        fi
    done < <(find . -name "$pattern" -not -path "./.git/*" -not -path "*/.git/*" -not -path "*/third_party/*" -print0 2>/dev/null)
done

if [[ $SENSITIVE_LEAK -eq 1 ]]; then
    log_error "检测到敏感文件未被 .gitignore 覆盖，已中止初始提交"
    echo "请修正 .gitignore 后重新运行，或手动确认这些文件可以公开"
    exit 1
fi
log_info "敏感文件检查通过"

# 嵌套 Git 仓库必须作为显式子模块记录，避免把工作树误当作普通目录。
if [[ ! -f .gitmodules ]]; then
    log_error "缺少 .gitmodules，无法记录嵌套仓库"
    exit 1
fi
for module in core android; do
    declared_path=$(git config -f .gitmodules --get "submodule.$module.path" 2>/dev/null || true)
    declared_url=$(git config -f .gitmodules --get "submodule.$module.url" 2>/dev/null || true)
    if [[ "$declared_path" != "$module" || -z "$declared_url" ]]; then
        log_error "嵌套仓库 $module 缺少 .gitmodules 声明"
        exit 1
    fi
    if [[ -e "$module/.git" ]] && ! git -C "$module" rev-parse --verify HEAD >/dev/null 2>&1; then
        log_error "嵌套仓库 $module 没有可记录的 HEAD"
        exit 1
    fi
    if [[ -e "$module/.git" && -n "$(git -C "$module" status --porcelain=v1 --untracked-files=all)" ]] &&
       [[ "${SMART_BOX_ALLOW_DIRTY_SUBMODULES:-0}" != 1 ]]; then
        log_error "嵌套仓库 $module 工作树有未提交改动；请先提交并发布该子模块（或显式设置 SMART_BOX_ALLOW_DIRTY_SUBMODULES=1）"
        exit 1
    fi
done

if [[ -z "$(git config --local user.name 2>/dev/null || true)" ||
      -z "$(git config --local user.email 2>/dev/null || true)" ]]; then
    log_error "未配置本地 Git 身份，请先设置 user.name 和 user.email"
    exit 1
fi

# 添加初始提交
log_info "准备初始提交..."
git add .

# 清理可能由早先中断留下、但现在已被忽略的构建二进制。
for generated in converter/smart-box-converter-linux-arm64; do
    if git diff --cached --name-only -- "$generated" | grep -q .; then
        # `git restore --staged` needs HEAD on an unborn branch; reset only the
        # index entry so the worktree artifact remains untouched.
        git reset --quiet -- "$generated"
        log_warn "从暂存区移除构建产物: $generated"
    fi
done

for module in core android; do
    staged_mode=$(git ls-files --stage -- "$module" | awk 'NR == 1 {print $1}')
    if [[ "$staged_mode" != 160000 ]]; then
        log_error "嵌套仓库 $module 未作为 gitlink 暂存（mode=${staged_mode:-missing}）"
        exit 1
    fi
done
if ! git diff --cached --name-only -- .gitmodules | grep -Fxq .gitmodules; then
    log_error ".gitmodules 未进入初始提交"
    exit 1
fi

# 对最终 index 再做一次敏感路径门禁，避免忽略规则变化造成误提交。
while IFS= read -r staged; do
    case "$staged" in
        config.json|*/config.json|runtime.json|profile.json|settings.json|*/runtime.json|*/profile.json|*/settings.json|*.keystore|*.jks|*.pem|*.key|*.p12|*.pfx|*.idsig|*.sqlite|*.sqlite3|*.sqlite-shm|*.sqlite-wal|service-account-credentials.json|.env|.env.*|*/.env|*/.env.*)
            log_error "敏感文件已进入暂存区: $staged"
            exit 1
            ;;
    esac
done < <(git diff --cached --name-only)

# Filename rules do not catch extensionless keys or a private key embedded in
# an otherwise innocuous text file.  Scan text entries already in the index
# for standard private-key markers before committing.
while IFS= read -r staged; do
    [[ -n "$staged" ]] || continue
    if file -b --mime "$staged" 2>/dev/null | grep -q '^text/'; then
        if grep -Eiq -- \
            '-----BEGIN (OPENSSH|RSA|EC|DSA|PGP) PRIVATE KEY-----|-----BEGIN PRIVATE KEY-----|openssh-key-v1' \
            "$staged"; then
            log_error "私钥内容已进入暂存区: $staged"
            exit 1
        fi
    fi
done < <(git diff --cached --name-only)

git commit -m "chore: initial commit - smart-box v0.1.0

- Core: Smart adaptive outbound group
- Linux: PySide6 desktop client
- Android: Kotlin mobile client
- Windows: WPF desktop client
- Converter: Raspberry Pi subscription aggregator
- Documentation: Complete README and guides"

# A commit now exists, so the failure trap must preserve this repository if a
# later branch/tag operation reports an error.
ROLLBACK_UNBORN_REPOSITORY=0

log_info "初始提交已完成"

# 创建 develop 分支
git checkout -b develop
git checkout main
log_info "develop 分支已创建"

# 创建初始标签
git tag -a v0.1.0 -m "Release version 0.1.0 - First feature-complete beta"
log_info "标签 v0.1.0 已创建"

# 显示仓库状态
echo
log_info "Git 仓库初始化完成！"
echo
echo "当前状态:"
git branch -a
echo
git tag
echo
echo "下一步操作:"
echo "  1. 添加远程仓库: git remote add origin <URL>"
echo "  2. 推送已审查的主分支: git push -u origin main"
echo "  3. 推送 develop 分支（确认其基于 main 后）: git push -u origin develop"
echo "  4. 标签仅在确认不含敏感历史后逐个推送: git push origin <tag>"
echo
log_info "建议的远程仓库设置:"
echo "  - GitHub: git remote add origin git@github.com:ewo3344/smart-box.git"
echo "  - Git remote: configure the repository URL for your hosting account"
echo "  - 自托管: git remote add origin git@your-server:smart-box.git"
echo
log_warn "提醒: 确保 .gitignore 正确排除了敏感文件（config.json, *.keystore 等）"
