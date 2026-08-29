# smart-box 版本控制和发布管理

**文档版本**: 1.0  
**更新时间**: 2026-08-24

---

## 版本号规范

smart-box 采用 **语义化版本号 2.0.0** (Semver)：

```
MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]

示例：
  0.1.0          - 当前版本
  0.1.1          - 补丁版本
  0.2.0-beta.1   - Beta 预发布
  1.0.0-rc.2     - Release Candidate
  1.0.0          - 正式版本
  1.0.1+20260824 - 带构建元数据
```

### 版本号语义

- **MAJOR (主版本)**: 不兼容的 API/配置变更
  - 示例：`0.x.x` → `1.0.0` (首个稳定版)
  - 示例：`1.x.x` → `2.0.0` (订阅格式变更)

- **MINOR (次版本)**: 向后兼容的功能新增
  - 示例：`0.1.x` → `0.2.0` (新增 macOS 客户端)
  - 示例：`1.0.x` → `1.1.0` (新增流量统计)

- **PATCH (补丁版本)**: 向后兼容的问题修复
  - 示例：`0.1.0` → `0.1.1` (修复 Android 崩溃)
  - 示例：`1.0.0` → `1.0.1` (修复内存泄漏)

- **PRERELEASE (预发布)**: 
  - `alpha` - 内部测试，功能不完整
  - `beta` - 公开测试，功能冻结
  - `rc` (Release Candidate) - 发布候选，仅修复严重 bug

### 组件版本关系

smart-box 是多组件项目，各组件独立迭代但协同发布：

```
产品版本: smart-box 0.1.0
  ├─ Core:      smart-box-core 0.1.0 (基于 sing-box 1.14.0-beta.14)
  ├─ Linux:     smart-box-linux 0.1.0
  ├─ Android:   smart-box-android 0.1.0 (versionCode: 1)
  ├─ Windows:   smart-box-windows 0.1.0
  └─ Converter: smart-box-converter 0.1.0
```

**规则**：
- 所有组件使用统一的产品版本号
- Core 版本号记录上游 sing-box 基线（用于兼容性追踪）
- Android versionCode 单调递增，不重置

---

## 版本历史和路线图

### 当前版本：v0.1.0 (2026-08-24) - 首个功能完整版

**发布状态**: Beta  
**Git Tag**: `v0.1.0` (待创建)  
**核心功能**:
- ✅ Smart 自适应出站组（延迟+失败+记忆+持久化）
- ✅ Linux 客户端完整实现（197/197 测试）
- ✅ Android 客户端基本可用（NAT 耗尽已解决）
- ✅ Windows 客户端基本可用
- ✅ 树莓派 Converter（24h 刷新 + 39 规则集）
- ✅ 26 个业务策略 + AI 排除香港

**已知问题**:
- Android 完整测试矩阵未完成
- Windows 自动化测试缺失
- 树莓派无监控告警
- Git 历史缺失

**升级路径**: 首个版本，无升级

**回滚方案**:
```bash
# Linux
cd /home/e/workspace/smart-box/dist
./smart-box-0.1.0-linux-x86_64/uninstall.sh

# Android
adb uninstall io.nekohasekai.sfa.smartbox

# 树莓派
sudo systemctl stop smart-box-converter.service smart-box.service
sudo cp /usr/local/bin/smart-box-converter.bak-YYYYMMDD \
       /usr/local/bin/smart-box-converter
sudo systemctl start smart-box-converter.service smart-box.service
```

---

### 计划版本：v0.1.1 (2026-09-07) - 测试和稳定性

**发布类型**: Patch  
**Git Tag**: `v0.1.1`  
**里程碑**: M1 完成  
**工作周期**: 2 周

**目标**:
- 🎯 完成 Android 完整设备矩阵 (T001)
- 🎯 Windows 自动化测试套件 (T002)
- 🎯 发布检查清单自动化 (T003)
- 🎯 树莓派监控脚本 (T004 部分)

**功能变更**:
- 无新功能，仅测试覆盖和工具改进

**破坏性变更**: 无

**升级说明**:
```bash
# Linux - 原地升级
cd dist/smart-box-0.1.1-linux-x86_64
./install.sh  # 自动覆盖 0.1.0

# Android - 直接安装
adb install -r smart-box-0.1.1-android-arm64.apk

# Windows - 覆盖安装
.\smart-box-0.1.1-windows-x64-setup.exe

# 树莓派 - 热更新
sudo systemctl stop smart-box-converter.service
sudo cp smart-box-converter-0.1.1 /usr/local/bin/smart-box-converter
sudo systemctl start smart-box-converter.service
```

**回滚方案**:
- Linux: 保留在 `~/.local/state/smart-box/backups/` 的旧版本
- Android: 从 `dist/smart-box-0.1.0-android-arm64.apk` 重装
- 树莓派: 使用 `.bak-*` 备份文件

**验证清单**:
- [ ] Linux: `scripts/verify-release.sh --allow-live`
- [ ] Android: `scripts/android-full-matrix.sh` (新增)
- [ ] Windows: `scripts/verify-windows.ps1` (新增)
- [ ] 树莓派: `scripts/verify-raspberry-pi.sh` (新增)

---

### 计划版本：v0.2.0 (2026-09-21) - 生产就绪

**发布类型**: Minor  
**Git Tag**: `v0.2.0-rc.1`, `v0.2.0-rc.2`, `v0.2.0`  
**里程碑**: M2 完成  
**工作周期**: 2 周 (在 v0.1.1 之后)

**目标**:
- 🎯 完整监控和告警系统 (T004)
- 🎯 断电/崩溃韧性测试 (T009)
- 🎯 性能基准测试 (T007)
- 🎯 安全审计 (T008)
- 🎯 FlClash 共存验证 (T005)

**新功能**:
- 🆕 树莓派监控仪表板（Grafana 或 HTML）
- 🆕 故障诊断工具 `smart-box-doctor`
- 🆕 自动回滚机制（检测到关键故障时）
- 🆕 性能分析报告生成器

**破坏性变更**: 无

**配置迁移**: 无需手动迁移

**升级说明**:
```bash
# 与 v0.1.1 相同，但增加验证步骤

# Linux - 升级后自动运行诊断
cd dist/smart-box-0.2.0-linux-x86_64
./install.sh
smart-box-doctor --full-check

# 树莓派 - 部署监控
sudo systemctl stop smart-box-converter.service
sudo cp smart-box-converter-0.2.0 /usr/local/bin/smart-box-converter
sudo cp smart-box-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now smart-box-monitor.service
sudo systemctl start smart-box-converter.service
```

**回滚触发条件**:
- 启动失败连续 3 次
- 关键路径不可用超过 2 分钟
- 内存占用 > 500MB (桌面) / 200MB (Android)
- Watchdog 检测到数据损坏

**回滚自动化**:
```bash
# 自动回滚由 systemd 或 watchdog 触发
# 手动强制回滚：
smart-box-profile rollback --to-version 0.1.1
```

**验证清单**:
- [ ] 所有 v0.1.1 检查项
- [ ] 24 小时稳定性测试（零崩溃）
- [ ] 断电注入测试（100 次，零损坏）
- [ ] 性能回归 < 10%
- [ ] 安全扫描零高危漏洞

---

### 计划版本：v0.3.0 (2026-10-05) - 用户体验优化

**发布类型**: Minor  
**Git Tag**: `v0.3.0`  
**里程碑**: M3 完成  
**工作周期**: 2 周 (在 v0.2.0 之后)

**目标**:
- 🎯 一键安装/卸载改进 (T010)
- 🎯 图形化订阅配置向导
- 🎯 详细用户文档和常见问题

**新功能**:
- 🆕 智能安装向导（自动检测环境）
- 🆕 订阅配置图形界面（不再手动编辑 JSON）
- 🆕 内置教程和提示
- 🆕 中英双语完整支持
- 🆕 一键诊断报告生成（用于提交 bug）

**破坏性变更**: 无

**配置迁移**: 
- 旧版 JSON 配置自动迁移到新格式
- 迁移日志记录到 `~/.local/state/smart-box/migrations/`

**升级说明**:
```bash
# Linux - 交互式升级向导
cd dist/smart-box-0.3.0-linux-x86_64
./install.sh --interactive

# 首次启动会有新手引导
smart-box --first-run-wizard
```

**用户可见变更**:
- 设置页重新设计（更直观的布局）
- 新增"快速设置"向导（3 步完成配置）
- 托盘菜单增加常用操作
- 日志页支持导出诊断包

**文档更新**:
- 🆕 用户快速入门指南
- 🆕 常见问题 FAQ (20+ 问题)
- 🆕 故障排查流程图
- 🆕 视频教程（可选）

**验证清单**:
- [ ] 所有 v0.2.0 检查项
- [ ] 全新安装流程（10 分钟内完成）
- [ ] 配置迁移测试（从 v0.1.0/v0.2.0）
- [ ] 多语言测试（中英）
- [ ] 用户接受度测试（至少 5 名非技术用户）

---

### 计划版本：v1.0.0 (2026-10-19) - 首个稳定版

**发布类型**: Major (0.x → 1.0)  
**Git Tag**: `v1.0.0-rc.1`, `v1.0.0-rc.2`, `v1.0.0`  
**里程碑**: 生产就绪里程碑  
**工作周期**: 2 周 (在 v0.3.0 之后)

**目标**:
- 🎯 API/配置格式稳定承诺
- 🎯 长期支持 (LTS) 计划
- 🎯 完整的发布文档
- 🎯 社区反馈整合

**稳定性承诺**:
- 🔒 配置格式向后兼容（至少 1 年）
- 🔒 订阅 API 稳定（Converter 协议）
- 🔒 安全更新至少 6 个月
- 🔒 重大 bug 修复优先级

**破坏性变更**: 
- 无（相对 v0.3.0）
- 未来 1.x.x 系列保证兼容性

**RC (Release Candidate) 流程**:
```
v1.0.0-rc.1 (2026-10-05)
  ↓ 测试 1 周，收集反馈
v1.0.0-rc.2 (2026-10-12)
  ↓ 最后验证，仅修复严重 bug
v1.0.0 (2026-10-19) 🎉
```

**升级说明**:
```bash
# 从 v0.3.0 升级到 v1.0.0 是平滑的
# 无需手动迁移

# Linux
cd dist/smart-box-1.0.0-linux-x86_64
./install.sh

# 升级后自动验证
smart-box-doctor --post-upgrade-check
```

**发布检查清单**:
- [ ] 所有自动化测试通过（Linux/Android/Windows/树莓派）
- [ ] 性能基准达标（无回归）
- [ ] 安全审计通过（零高危）
- [ ] 文档完整（用户+开发者）
- [ ] 至少 2 周的 RC 测试（社区参与）
- [ ] 发布说明和变更日志
- [ ] 升级路径验证（从所有 0.x 版本）
- [ ] 回滚方案测试

**发布产物**:
```
dist/v1.0.0/
  ├─ smart-box-1.0.0-linux-x86_64.tar.gz
  ├─ smart-box-1.0.0-android-arm64.apk
  ├─ smart-box-1.0.0-android-universal.apk
  ├─ smart-box-1.0.0-windows-x64.zip
  ├─ smart-box-converter-1.0.0-linux-arm64
  ├─ SHA256SUMS
  ├─ SHA256SUMS.asc (GPG 签名)
  ├─ RELEASE-NOTES.md
  └─ CHANGELOG.md
```

**长期支持计划**:
- v1.0.x: 安全更新至少 6 个月
- v1.1.0+ 发布后，v1.0.x 进入维护期（仅严重 bug）
- v2.0.0 发布前至少 3 个月通知

---

### 未来版本：v1.1.0+ - 功能增强

**v1.1.0** (预计 2026-12)
- 🆕 多订阅源支持（客户端侧合并）
- 🆕 自定义规则编辑器（GUI）
- 🆕 节点分享和导入

**v1.2.0** (预计 2027-02)
- 🆕 流量统计和分析（按域名/应用）
- 🆕 高级日志查询（时间范围、条件过滤）
- 🆕 性能分析和优化建议

**v1.3.0** (预计 2027-04)
- 🆕 插件系统（自定义规则、协议）
- 🆕 API 开放（第三方客户端）

**v2.0.0** (预计 2027-Q3)
- 🆕 macOS 客户端
- 🆕 全新 UI/UX 设计
- ⚠️ 可能的破坏性变更（提前 3 个月通知）

---

## Git 分支策略

### 主要分支

```
main (或 master)
  ├─ 生产就绪代码
  ├─ 每个 commit 可发布
  └─ 受保护，只接受 PR

develop
  ├─ 开发主线
  ├─ 集成功能分支
  └─ 定期合并到 main

release/v0.2.0
  ├─ 发布准备分支
  ├─ 仅修复 bug，不加新功能
  └─ 测试通过后合并到 main 并打 tag
```

### 辅助分支

```
feature/android-full-matrix     # 功能分支
  ↓ PR 合并到 develop

hotfix/v0.1.1-android-crash    # 紧急修复
  ↓ 直接从 main 分出，修复后合并回 main 和 develop

bugfix/linux-memory-leak       # Bug 修复
  ↓ 从 develop 分出，修复后合并回 develop
```

### 命名规范

- `feature/<简短描述>` - 新功能
- `bugfix/<issue号>-<简短描述>` - Bug 修复
- `hotfix/v<版本号>-<简短描述>` - 紧急修复
- `release/v<版本号>` - 发布准备
- `refactor/<简短描述>` - 重构
- `docs/<简短描述>` - 文档更新

### 工作流程

#### 开发新功能

```bash
# 1. 从 develop 创建功能分支
git checkout develop
git pull origin develop
git checkout -b feature/multi-subscription

# 2. 开发和提交
git add .
git commit -m "feat(converter): support multiple subscription sources"

# 3. 推送并创建 PR
git push origin feature/multi-subscription
# 在 GitHub 创建 PR: feature/multi-subscription → develop

# 4. Code Review 通过后合并
# 5. 删除功能分支
git branch -d feature/multi-subscription
```

#### 发布新版本

```bash
# 1. 从 develop 创建 release 分支
git checkout develop
git checkout -b release/v0.2.0

# 2. 更新版本号
# - core/version.go
# - linux/setup.py
# - android/app/build.gradle
# - windows/AssemblyInfo.cs
git commit -m "chore: bump version to 0.2.0"

# 3. 仅修复发布阻塞的 bug
git commit -m "fix(android): resolve crash on startup"

# 4. 完成发布测试后合并
git checkout main
git merge --no-ff release/v0.2.0
git tag -a v0.2.0 -m "Release version 0.2.0"
git push origin main --tags

# 5. 合并回 develop
git checkout develop
git merge --no-ff release/v0.2.0
git push origin develop

# 6. 删除 release 分支
git branch -d release/v0.2.0
```

#### 紧急修复

```bash
# 1. 从 main 创建 hotfix 分支
git checkout main
git checkout -b hotfix/v0.1.1-android-crash

# 2. 修复 bug
git commit -m "fix(android): resolve critical VPN crash"

# 3. 更新版本号到 0.1.1
git commit -m "chore: bump version to 0.1.1"

# 4. 合并到 main 和 develop
git checkout main
git merge --no-ff hotfix/v0.1.1-android-crash
git tag -a v0.1.1 -m "Hotfix release 0.1.1"
git push origin main --tags

git checkout develop
git merge --no-ff hotfix/v0.1.1-android-crash
git push origin develop

# 5. 删除 hotfix 分支
git branch -d hotfix/v0.1.1-android-crash
```

---

## Commit 消息规范

采用 **Conventional Commits** 格式：

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Type 类型

- `feat`: 新功能
- `fix`: Bug 修复
- `docs`: 文档更新
- `style`: 代码格式（不影响功能）
- `refactor`: 重构（既不是新功能也不是修复）
- `perf`: 性能优化
- `test`: 添加测试
- `chore`: 构建过程或辅助工具变更
- `revert`: 回滚之前的 commit

### Scope 范围

- `core`: smart-box-core (Go)
- `linux`: Linux 客户端
- `android`: Android 客户端
- `windows`: Windows 客户端
- `converter`: 树莓派转换器
- `docs`: 文档
- `ci`: CI/CD
- `deps`: 依赖更新

### 示例

```bash
# 新功能
git commit -m "feat(android): add multi-subscription support"

# Bug 修复
git commit -m "fix(linux): resolve memory leak in traffic monitor"

# 破坏性变更（注意感叹号）
git commit -m "feat(converter)!: change subscription API format

BREAKING CHANGE: subscription endpoint now requires API version header"

# 关闭 issue
git commit -m "fix(android): resolve VPN crash on Android 14

Fixes #123"

# 多行消息
git commit -m "refactor(core): improve Smart node selection algorithm

- Use weighted scoring instead of simple latency
- Add exponential backoff for failed nodes
- Optimize memory usage for large node pools

Performance improved by 15% in benchmarks."
```

---

## GitHub 标签和里程碑

### 标签 (Labels)

#### 类型标签
- `bug` - 缺陷
- `enhancement` - 功能增强
- `feature` - 新功能
- `documentation` - 文档
- `performance` - 性能
- `security` - 安全

#### 优先级标签
- `priority: critical` - P0，阻塞发布
- `priority: high` - P1，重要
- `priority: medium` - P2，普通
- `priority: low` - P3，次要

#### 状态标签
- `status: in-progress` - 进行中
- `status: blocked` - 被阻塞
- `status: needs-review` - 需要审查
- `status: needs-testing` - 需要测试

#### 平台标签
- `platform: linux` - Linux 相关
- `platform: android` - Android 相关
- `platform: windows` - Windows 相关
- `platform: converter` - 转换器相关

#### 特殊标签
- `good first issue` - 新手友好
- `help wanted` - 需要帮助
- `wontfix` - 不会修复
- `duplicate` - 重复 issue

### 里程碑 (Milestones)

```
Milestone: v0.1.1 - Testing & Stability
Due: 2026-09-07
Issues: 15 open, 5 closed
Progress: 25%

Milestone: v0.2.0 - Production Ready
Due: 2026-09-21
Issues: 20 open, 0 closed
Progress: 0%

Milestone: v1.0.0 - First Stable Release
Due: 2026-10-19
Issues: 35 open, 0 closed
Progress: 0%
```

---

## GitHub Release 格式

### Release v0.1.0 示例

```markdown
## smart-box v0.1.0 - First Feature-Complete Beta

**Release Date**: 2026-08-24  
**Type**: Beta  
**Based on**: sing-box 1.14.0-beta.14

### 🎉 Highlights

- **Smart Adaptive Outbound**: Automatic node selection based on latency, failure, and history
- **Multi-Platform**: Full support for Linux, Android, and Windows
- **26 Routing Policies**: Fine-grained control for AI, Telegram, streaming, and more
- **Persistent State**: Node scores and target memory survive restarts

### ✨ New Features

#### Core
- Smart outbound group with scoring and memory persistence
- Failure penalty and 7-day history decay
- 8-candidate retry with 2-concurrent hedging

#### Linux Client
- PySide6 GUI with real-time traffic monitoring
- 4 modes: Rule / Global / Direct / Energy-saving
- Domain whitelist/blacklist with IDN support
- Arch/CachyOS mirror speed test and ranking
- Systemd watchdog with fail-open recovery

#### Android Client
- Solved NAT exhaustion issue (gVisor TUN)
- Bounded restart with 30-second timeout
- Profile refresh with service reload receipt
- Zero DNS EPERM and protect failures

#### Raspberry Pi Converter
- 24-hour refresh cycle with cache-aware startup
- 39 rule-set mirror with ETag support
- Route-bypass for probe isolation
- AI Fallback excludes Hong Kong

### 🐛 Bug Fixes

- Fixed vivo TUN descriptor close race
- Fixed Douyin routing collision with TikTok
- Fixed ad DNS causing EPERM
- Fixed multicast traffic hijacking

### 📦 Assets

Download the appropriate package for your platform:

- **Linux (CachyOS/x86_64)**: `smart-box-0.1.0-linux-x86_64.tar.gz`
- **Android (arm64-v8a)**: `smart-box-0.1.0-android-arm64.apk`
- **Android (universal)**: `smart-box-0.1.0-android-universal.apk`
- **Android (legacy 5/6)**: `smart-box-0.1.0-legacy-android-5-arm64.apk`
- **Windows (x64)**: `smart-box-0.1.0-windows-x64.zip`
- **Converter (ARM64)**: `smart-box-converter-0.1.0-linux-arm64`

### 🔐 Checksums

See `SHA256SUMS` file. Verify with:
```bash
sha256sum -c SHA256SUMS
```

### 📚 Documentation

- [README](../README.md)
- [Linux Setup Guide](../linux/README.md)
- [Converter Documentation](../converter/README.md)
- [Routing Rules](../converter/ROUTING.md)

### ⚠️ Known Issues

- Android full device matrix testing incomplete (#15)
- Windows automated testing missing (#16)
- Raspberry Pi lacks monitoring/alerting (#17)
- Git history not initialized (#18)

### 🔄 Upgrade Instructions

**First installation** - Follow platform-specific README

**Future upgrades** - Will support in-place upgrade from this version

### 🙏 Acknowledgments

- Based on [sing-box](https://github.com/SagerNet/sing-box) 1.14.0-beta.14
- Rule sets from [SagerNet/sing-geoip](https://github.com/SagerNet/sing-geoip) and [sing-geosite](https://github.com/SagerNet/sing-geosite)
- Inspired by [GUI.for.SingBox](https://github.com/GUI-for-Cores/GUI.for.SingBox)

---

**Full Changelog**: (first release)
```

---

## 版本支持矩阵

| 版本 | 发布日期 | 支持状态 | 安全更新截止 | 说明 |
|------|----------|----------|--------------|------|
| v0.1.0 | 2026-08-24 | Beta | - | 首个功能完整版，不保证稳定性 |
| v0.1.1 | 2026-09-07 | 计划中 | - | 测试覆盖和稳定性 |
| v0.2.0 | 2026-09-21 | 计划中 | - | 生产就绪候选 |
| v0.3.0 | 2026-10-05 | 计划中 | - | UX 优化 |
| v1.0.0 | 2026-10-19 | 计划中 | 2027-04-19 (6个月) | 首个稳定版，LTS |
| v1.1.0+ | TBD | 未来版本 | TBD | 功能增强 |

### 支持状态说明

- **Active**: 积极维护，新功能和 bug 修复
- **Maintenance**: 仅严重 bug 和安全问题
- **EOL (End of Life)**: 不再支持

---

## 依赖版本管理

### Core 依赖

```go
// go.mod
module github.com/your-org/smart-box-core

go 1.25.5

require (
    github.com/sagernet/sing-box v1.14.0-beta.14
    // ... 其他依赖
)
```

The `go 1.25.5` directive in `core/go.mod` is the minimum language version
accepted by the core module. The exact release compiler is pinned separately
in the root [`TOOLCHAIN_VERSION`](TOOLCHAIN_VERSION) file (`go1.26.5`); build
scripts and release checks must use that pin.

**锁定策略**:
- 上游 sing-box 版本锁定在 v1.14.0-beta.14
- 定期评估升级（每季度）
- 重大变更需要独立测试周期

### Python 依赖

```txt
# linux/requirements.txt
PySide6>=6.6.0,<7.0.0
requests>=2.31.0,<3.0.0
```

**锁定策略**:
- 允许 patch 版本自动升级
- Minor 版本需测试验证
- Major 版本需要代码适配

### Android 依赖

```gradle
// android/app/build.gradle
dependencies {
    implementation "org.jetbrains.kotlin:kotlin-stdlib:1.9.20"
    implementation "androidx.core:core-ktx:1.12.0"
    // ...
}
```

**锁定策略**:
- Kotlin 和 AndroidX 库跟随 stable channel
- 重大版本变更需要回归测试

---

## 回滚和恢复策略

### 自动回滚触发条件

1. **启动失败** (连续 3 次)
   ```bash
   systemctl status smart-box@e.service
   # failed (3 consecutive starts)
   # → 自动回滚到上一个已知良好版本
   ```

2. **关键路径不可用** (超过 2 分钟)
   ```bash
   # Watchdog 检测到：
   # - 国内直连失败
   # - 公网不可达
   # - Smart 代理全部超时
   # → fail-open 恢复直连，记录事件
   ```

3. **数据损坏检测**
   ```bash
   # Journal 校验失败
   # Profile/Runtime SHA-256 不匹配
   # → 从 backup 恢复，记录损坏详情
   ```

4. **资源耗尽**
   ```bash
   # 内存 > 500MB (桌面) / 200MB (Android)
   # 文件描述符耗尽
   # → 重启服务，持续则回滚版本
   ```

### 手动回滚命令

```bash
# Linux - 回滚到指定版本
smart-box-profile rollback --to-version 0.1.0
# 或
cd ~/.local/state/smart-box/backups/
./restore-bundle-YYYYMMDD-HHMMSS.sh

# Android - 重装旧版本
adb install -r smart-box-0.1.0-android-arm64.apk

# Windows - 使用安装程序
.\smart-box-0.1.0-windows-x64-setup.exe

# 树莓派 - 从备份恢复
sudo systemctl stop smart-box-converter.service
sudo cp /usr/local/bin/smart-box-converter.bak-YYYYMMDD \
       /usr/local/bin/smart-box-converter
sudo systemctl start smart-box-converter.service
```

### 灾难恢复

**场景：完全删除或损坏**

```bash
# 1. 停止所有服务
systemctl stop "smart-box@$(id -un).service"
systemctl stop "smart-box-watchdog@$(id -un).service"

# 2. 清理残留
sudo /usr/local/lib/smart-box/uninstall.sh --purge

# 3. 从最后已知良好版本全新安装
cd /path/to/backup/smart-box-0.1.0-linux-x86_64
./install.sh

# 4. 恢复配置（如果备份存在）
cp ~/.local/state/smart-box/backups/latest/* \
   ~/.config/smart-box/
```

---

## 版本兼容性矩阵

### 客户端 ↔ Converter 兼容性

| 客户端版本 | Converter 0.1.x | Converter 0.2.x | Converter 1.0.x |
|-----------|----------------|----------------|----------------|
| 0.1.x     | ✅ 完全兼容     | ✅ 完全兼容     | ✅ 完全兼容     |
| 0.2.x     | ✅ 完全兼容     | ✅ 完全兼容     | ✅ 完全兼容     |
| 1.0.x     | ✅ 完全兼容     | ✅ 完全兼容     | ✅ 完全兼容     |
| 2.0.x     | ⚠️ 部分兼容     | ⚠️ 部分兼容     | ✅ 完全兼容     |

**说明**:
- v1.x 承诺与所有 v1.x converter 兼容
- v2.0 可能引入新订阅格式，但会保留兼容层

### 配置文件兼容性

| 配置版本 | 0.1.x | 0.2.x | 1.0.x | 说明 |
|---------|-------|-------|-------|------|
| profile.json v1 | ✅ | ✅ | ✅ | 当前格式，长期支持 |
| runtime.json v1 | ✅ | ✅ | ✅ | 本地生成，自动迁移 |
| settings.json v1 | ✅ | ✅ | ✅ | 字段兼容，新字段可选 |

### 跨平台版本建议

**强烈建议**: 所有平台使用相同的 MINOR 版本

```
推荐：
  Linux 0.2.0 + Android 0.2.1 + Windows 0.2.0 ✅

不推荐：
  Linux 1.0.0 + Android 0.1.0 + Windows 0.3.0 ⚠️
  (功能不一致，用户体验差)
```

---

## 发布清单模板

```markdown
# Release Checklist for v0.X.Y

## 准备阶段 (Release - 7 days)
- [ ] 创建 release 分支: `release/v0.X.Y`
- [ ] 更新所有版本号 (core/linux/android/windows/converter)
- [ ] 更新 CHANGELOG.md
- [ ] 冻结新功能，仅修复 bug
- [ ] 通知社区即将发布

## 测试阶段 (Release - 5 days)
- [ ] Linux: `scripts/verify-release.sh --allow-live`
- [ ] Android: 完整设备矩阵测试
- [ ] Windows: 自动化测试套件
- [ ] 树莓派: 健康检查和监控验证
- [ ] 性能回归测试 (< 10%)
- [ ] 安全扫描 (govulncheck, safety, etc.)

## 构建阶段 (Release - 3 days)
- [ ] 构建所有平台发布包
- [ ] 生成 SHA256SUMS
- [ ] GPG 签名 (可选)
- [ ] 验证所有包可安装
- [ ] 测试升级路径 (从上一版本)

## 文档阶段 (Release - 2 days)
- [ ] 更新 README.md
- [ ] 更新用户文档
- [ ] 编写 RELEASE-NOTES.md
- [ ] 准备发布公告
- [ ] 更新版本兼容性矩阵

## 发布阶段 (Release Day)
- [ ] 合并 release 分支到 main
- [ ] 创建 Git tag: `v0.X.Y`
- [ ] 推送到远程: `git push origin main --tags`
- [ ] 创建 GitHub Release
- [ ] 上传发布产物
- [ ] 发布公告 (社区/论坛/邮件列表)

## 发布后 (Release + 1 day)
- [ ] 合并 release 分支到 develop
- [ ] 验证发布产物可下载
- [ ] 监控社区反馈
- [ ] 准备 hotfix 响应流程
- [ ] 更新项目看板和里程碑

## 持续监控 (Release + 7 days)
- [ ] 监控树莓派服务健康
- [ ] 收集用户反馈和 bug 报告
- [ ] 性能指标验证
- [ ] 计划下一版本的改进
```

---

## 常见问题 (FAQ)

### Q1: 如何确定当前版本？

```bash
# Linux
smart-box-core version
# 输出: smart-box-0.1.0-core-1.14.0-beta.14

# Android
adb shell dumpsys package io.nekohasekai.sfa.smartbox | grep version
# 或在应用"关于"页面查看

# Windows
.\smart-box.exe --version
```

### Q2: 可以跨版本升级吗（如 0.1.0 → 1.0.0）？

可以，但建议：
1. 先升级到最后的 0.x 版本（如 0.3.0）
2. 验证功能正常
3. 再升级到 1.0.0

大跨度升级可能遇到配置迁移问题。

### Q3: 发生回滚后，数据会丢失吗？

不会。回滚机制：
1. 保留用户配置和缓存
2. 恢复上一个已知良好的二进制
3. 自动迁移不兼容的配置字段

### Q4: Beta/RC 版本可以用于生产吗？

- **Beta (0.x)**: 不建议生产，仅测试
- **RC (x.y.z-rc.N)**: 可以谨慎用于生产，但要准备回滚
- **Stable (x.y.z)**: 生产就绪

### Q5: 如何获取安全更新通知？

（待建立）订阅：
- GitHub Watch (Releases only)
- 邮件列表
- RSS feed

### Q6: Converter 和客户端版本必须一致吗？

不必须，但建议 MINOR 版本一致：
- ✅ Converter 0.2.1 + Client 0.2.0 (推荐)
- ✅ Converter 0.2.0 + Client 0.1.5 (兼容)
- ⚠️ Converter 1.0.0 + Client 0.1.0 (可能部分功能不可用)

---

## 附录：快速参考

### 关键文件路径

```
项目根目录/
  ├─ VERSION                    # 当前版本号
  ├─ CHANGELOG.md               # 变更日志
  ├─ VERSION-CONTROL.md         # 本文档
  ├─ core/version.go            # Core 版本定义
  ├─ linux/setup.py             # Linux 版本定义
  ├─ android/app/build.gradle   # Android 版本定义
  ├─ windows/AssemblyInfo.cs    # Windows 版本定义
  └─ converter/version.go       # Converter 版本定义
```

### 版本更新脚本

```bash
#!/bin/bash
# scripts/bump-version.sh

VERSION=$1
if [ -z "$VERSION" ]; then
  echo "Usage: $0 <version>"
  echo "Example: $0 0.2.0"
  exit 1
fi

echo "Bumping version to $VERSION"

# 更新所有版本文件
echo "$VERSION" > VERSION

sed -i "s/Version = \".*\"/Version = \"$VERSION\"/" core/version.go
sed -i "s/version='.*'/version='$VERSION'/" linux/setup.py
sed -i "s/versionName \".*\"/versionName \"$VERSION\"/" android/app/build.gradle
sed -i "s/AssemblyVersion(\".*\")/AssemblyVersion(\"$VERSION\")/" windows/AssemblyInfo.cs
sed -i "s/Version = \".*\"/Version = \"$VERSION\"/" converter/version.go

echo "Version updated to $VERSION"
echo "Don't forget to:"
echo "  1. Update CHANGELOG.md"
echo "  2. Commit: git commit -m 'chore: bump version to $VERSION'"
echo "  3. Create tag after merge: git tag -a v$VERSION"
```

### 发布命令速查

```bash
# 创建 release 分支
git checkout -b release/v0.2.0 develop

# 更新版本号
./scripts/bump-version.sh 0.2.0

# 构建所有平台
./scripts/build-all-platforms.sh

# 验证发布
./scripts/verify-release.sh --all-platforms

# 合并并打 tag
git checkout main
git merge --no-ff release/v0.2.0
git tag -a v0.2.0 -m "Release version 0.2.0"
git push origin main --tags

# 合并回 develop
git checkout develop
git merge --no-ff release/v0.2.0
git push origin develop
```

---

**文档维护**: 每次发布后更新版本历史和兼容性矩阵  
**责任人**: Release Manager  
**审核周期**: 每季度复审一次
