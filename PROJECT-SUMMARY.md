# smart-box 项目总结

**生成时间**: 2026-08-24  
**项目版本**: 0.1.0

---

## 项目概述

smart-box 是一个增强型代理工具，基于 sing-box 1.14.0-beta.14 开发，核心创新是 **Smart 自适应出站组**，能够根据延迟、失败率和使用历史自动选择最优节点。项目提供完整的多平台客户端和订阅管理方案。

### 核心价值

1. **智能选路**: Smart 组根据实时测速、失败惩罚和目标记忆自动选择节点，无需手动干预
2. **平台全覆盖**: Linux (CachyOS)、Android、Windows 三端完整实现
3. **生产就绪**: 持久化状态、故障恢复、安全沙箱等企业级特性
4. **订阅聚合**: 树莓派转换器统一管理多个订阅源和 39 个规则集
5. **精细分流**: 26 个业务策略，支持 AI、Telegram、流媒体等专用路由

---

## 技术亮点

### 1. Smart 自适应出站组

**核心算法**:
- URL 探测获取基准延迟
- 连接成功/失败影响节点评分
- 短期失败惩罚 + 长期历史衰减（7天）
- 目标到节点记忆（1 小时有效期）
- 每轮探测：4 个当前最优 + 4 个探索候选

**持久化**:
- 节点质量分数存储在 `cache.db` 的 `smart_score` bucket
- 目标记忆存储在 `smart_memory` bucket
- 使用 one-way 指纹识别节点，订阅更新不丢失历史
- 重启后优先使用低成本节点，避免冷启动慢速节点

**容错设计**:
- 单次连接最多尝试 8 个候选
- 最多 2 个并发竞速
- 失败节点降权，后续连接优先尝试未失败节点
- 避免大型 Fallback 池的遍历开销

### 2. Linux 客户端架构

**技术栈**: Python 3 + PySide6 + systemd

**安全模型**:
- Core 以用户身份运行，仅在进程内授予必要的 5 个 capability
- Polkit 规则精确限制只能管理自己的服务
- DNS 注册/撤销 helper 以 root 执行，core 不持有 root 权限
- NoNewPrivileges + 文件系统/网络/内核限制

**可靠性**:
- 独立 watchdog 服务持续验证关键路径
- 连续失败自动 fail-open 恢复直连
- Profile/Runtime/Settings 三文件原子提交，带持久化 journal
- 支持断电/崩溃后的幂等恢复

**FlClash 共存**:
- 启动前自动停止 FlClash
- 失败时自动恢复 FlClash
- 正常停止保持 FlClash 关闭（用户预期）

**用户体验**:
- 实时流量图和累计统计
- 26 个策略选择器，手动测速
- 域名黑白名单（本地覆盖订阅）
- Arch/CachyOS 源测速和应用
- 浅色/深色主题，最小 920×660 支持

### 3. Android 客户端

**NAT 耗尽解决方案**:
- 问题：vivo V2352A 的 `mixed` 栈 5 分钟耗尽 55K 端口
- 方案：运行时强制 `gvisor` 栈，不修改订阅源文件
- 效果：评论面板连续打开无崩溃，DNS EPERM 归零

**订阅刷新优化**:
- 等待真实 reload/restart 回执后反馈
- 三态反馈：已是最新 / 已保存 / 已应用
- 重启超时限制（30 秒），ViewModel 回执限制（35 秒）
- 失败与"已保存未应用"分离，避免泄露私密 URL

**VPN 可靠性**:
- Protect + BindNetwork 双重网络绑定
- TUN FD 复制到高描述符（≥1024）避免 vendor 关闭
- 保留 NetworkAgent 实现热刷新（reload 分支）

### 4. 树莓派 Converter

**订阅聚合**:
- 并行拉取多个 Clash 订阅源
- 去重、过滤不可达节点
- 按 emoji 旗帜自动分组
- 生成区域 Smart 组 + 基准 Smart

**39 规则集镜像**:
- 私有端点，ETag + Last-Modified 支持
- 4 MiB 限制，SRS 版本 1-5 验证
- 原子替换，保留最后有效缓存
- 完整性检查（验证每个配置的 tag）

**高可用设计**:
- 24 小时刷新周期
- 缓存可用时直接启动，不等待首次刷新
- Profile 和 Rule cache 持久化到 `/var/lib/smart-box`
- Route-bypass 确保探测不走 TUN

**专用 Fallback**:
- AI Fallback: 排除香港，优先 SG/JP/US/TW
- Telegram Fallback: 独立 `https://telegram.org` 探测
- 媒体/游戏 Fallback: 专用候选池
- 避免每个服务都有独立探测开销

### 5. 路由和分流

**26 个策略**:
- 基准 Smart（全局或手动区域）
- AI（专用 Fallback，排除香港）
- Telegram（专用 Fallback）
- 抖音（优先级高于海外 TikTok 和广告）
- 流媒体：Netflix、Disney+、Max、Prime、Apple TV+、YouTube、TikTok、Bilibili、Spotify、其他
- 社交、游戏、GitHub、开发服务
- Apple、Microsoft、Google
- 测速、下载、国内域名/IP、广告

**节能模式**:
- AI 和 Telegram 保持代理（支持域名 + 进程/包名回退）
- 其他流量直连 + 本地 DNS
- 适用于移动数据和电池节省

**DNS 分离**:
- 每个策略独立 DoH 传输
- 外服用 Cloudflare，国内用 AliDNS
- DNS 出口跟随策略选择（手动区域也影响 DNS）

---

## 当前状态（2026-08-24）

### 已完成

✅ **Linux 客户端**
- 197/197 测试通过
- Profile/Runtime/Settings 持久化 journal
- GUI 热更新（无核心重启）
- 真实桌面启动器单实例唤醒
- 源测速和应用（Arch + CachyOS）
- 完整 fail-open 验收

✅ **Android 客户端**
- NAT 耗尽问题解决（gVisor）
- 订阅刷新有界重启和回执
- vivo V2352A 冒烟测试通过（VPN/TUN/Telegram/抖音）
- 零 DNS EPERM/protect failure

✅ **树莓派 Converter**
- 24 小时刷新周期
- 39/39 规则集镜像健康
- Route-bypass 隔离探测流量
- 缓存可用时立即启动

✅ **Core 功能**
- Smart 节点质量分数持久化
- Smart 目标记忆持久化
- 失败惩罚和探索探测
- 7 天历史衰减

✅ **发布流程**
- Linux 完整构建和验证脚本
- SHA256SUMS 清单
- 可回滚的安装/卸载

### 进行中

🔄 **Android 完整矩阵** (T001)
- 黑白名单端到端
- Fallback 和区域选择
- 分数恢复验证
- 停止按钮热区
- 完整 logcat 归档

🔄 **Windows 测试** (T002)
- 单元测试覆盖
- 集成测试
- 24 小时稳定性
- CI 集成

### 待完成

⏳ **树莓派监控** (T004)
- 健康检查脚本
- 告警机制（邮件/Bot）
- 自动恢复验证

⏳ **FlClash 共存** (T005)
- 真实 FlClash 环境测试
- 故障恢复自动化验证

⏳ **发布自动化** (T003)
- 统一检查脚本
- 多平台门禁
- 测试报告生成

⏳ **性能基准** (T007)
- 冷/热启动时间
- 吞吐量和延迟
- 内存/CPU 占用

⏳ **安全审计** (T008)
- 代码审计
- 依赖漏洞扫描
- 网络安全测试

---

## 架构图

### 系统拓扑

```
┌─────────────────────────────────────────────────────────────┐
│                        用户设备                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Linux Client │  │Android Client│  │Windows Client│      │
│  │  (PySide6)   │  │  (Kotlin)    │  │    (WPF)     │      │
│  └───────┬──────┘  └───────┬──────┘  └───────┬──────┘      │
│          │                  │                  │              │
│          └──────────────────┼──────────────────┘              │
│                             │                                 │
│                    HTTP GET /subscription/<token>            │
└─────────────────────────────┼──────────────────────────────┘
                              │
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                      树莓派 (192.168.2.102:38473)            │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  smart-box-converter                                 │    │
│  │  ├─ 聚合 Provider 订阅                               │    │
│  │  ├─ 节点可达性检查                                   │    │
│  │  ├─ 按 emoji 分组生成 Smart                          │    │
│  │  ├─ 镜像 39 规则集 (*.srs)                           │    │
│  │  └─ 输出完整 sing-box profile                        │    │
│  └─────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  smart-box-core                                      │    │
│  │  └─ 本地消费 converter profile                       │    │
│  └─────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  route-bypass (UID 995 → main table)                │    │
│  │  └─ 确保 converter 探测不走 TUN                      │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### Linux 客户端详细架构

```
┌────────────────────────────────────────────────────────────┐
│ smart-box (PySide6 GUI)                                     │
│  ├─ 状态页：实时流量、连接数、运行模式                      │
│  ├─ 策略页：26 个选择器、手动测速                           │
│  ├─ 日志页：筛选、复制、自动刷新                             │
│  ├─ 域名页：黑白名单、冲突检测                               │
│  ├─ 设置页：订阅、TUN 栈、主题、源测速                       │
│  └─ 托盘：快速启停、模式切换                                 │
└────────────────┬───────────────────────────────────────────┘
                 │ HTTP API (127.0.0.1:20809)
                 ↓
┌────────────────────────────────────────────────────────────┐
│ smart-box@e.service (systemd)                              │
│  ├─ ExecStartPre: 停止 FlClash                             │
│  ├─ ExecStart: smart-box-core run -c runtime.json         │
│  │    ├─ User=e (非 root)                                  │
│  │    ├─ Capabilities: CAP_NET_ADMIN + 4 others           │
│  │    └─ NoNewPrivileges, 文件系统/网络限制                │
│  ├─ ExecStartPost: 注册 DNS、验证 TUN/API/网络              │
│  └─ ExecStopPost: 撤销 DNS、清理 TUN                        │
└────────────────┬───────────────────────────────────────────┘
                 │ 被监控
                 ↓
┌────────────────────────────────────────────────────────────┐
│ smart-box-watchdog@e.service                               │
│  ├─ User=root（但只操作固定单元）                           │
│  ├─ 每 10 秒检查关键路径                                    │
│  │    ├─ 国内直连（百度）                                   │
│  │    ├─ 公网可达（gstatic/GitHub）                         │
│  │    └─ Smart 代理（Telegram）                            │
│  └─ 连续失败 → 停止主服务 → fail-open 恢复直连             │
└────────────────────────────────────────────────────────────┘

配置文件 (~/.config/smart-box/):
  ├─ profile.json       # Converter 原始配置
  ├─ runtime.json       # Linux 运行副本（gVisor, 域名覆盖）
  └─ settings.json      # 本地设置（带 .lock 跨进程同步）

状态文件 (~/.local/state/smart-box/):
  ├─ cache.db           # Smart 分数、记忆、selector 选择
  └─ backups/           # Profile/Runtime/Settings 回滚 journal
```

---

## 关键设计决策

### 1. 为什么 Linux 用 gVisor，但订阅保持 mixed？

**问题**: CachyOS 上 `mixed` 和 `system` 栈都能创建 TUN，但 TCP 会话在出站前停滞。

**方案**: Linux runtime 强制 `gvisor`，但 `profile.json` 保持 converter 原始的 `mixed`。

**理由**:
- Converter 输出给多平台（树莓派/Windows 可能需要 `mixed`）
- 平台特定覆盖在各客户端本地实现
- Android 也独立实现了 runtime 强制 `gvisor`

### 2. 为什么 Android 要 BindNetwork，不只 Protect？

**问题**: vivo V2352A 上 `protect()` 返回 true，但 socket 仍超时。

**方案**: `protect(socket) + defaultNetwork.bindSocket(socket)`

**理由**:
- Vendor 接受 `protect()` 但不可靠绑定到底层 Network
- 纯 Kotlin 测试证实 bind 是唯一成功方案
- Go FD callback 使用安全复制的 descriptor（避免 PFD 关闭 core socket）

### 3. 为什么 Smart 要持久化分数和记忆？

**问题**: 每次重启都冷启动，第一个连接可能选到慢速/失败节点。

**方案**: 
- 节点质量分数（延迟 + 成功/失败）持久化
- 目标到节点记忆持久化（1 小时 TTL）
- 启动时恢复并优先使用低成本节点

**理由**:
- 避免用户感知的"刚开机很慢"
- 利用历史数据加速收敛
- 7 天衰减避免永久偏好过时节点

### 4. 为什么 Converter 要镜像规则集，不直接用 GitHub？

**问题**: 客户端直接从 GitHub 拉取规则集，单点故障 + 速度慢。

**方案**: Converter 私有镜像 39 个 SRS 文件，客户端从 Converter 拉取。

**理由**:
- GitHub 在某些网络不可达
- 减少客户端依赖的外部服务
- Converter 缓存可离线启动
- 4 MiB 限制 + 版本验证防止恶意规则

### 5. 为什么 AI 要排除香港？

**问题**: 用户反馈 AI 服务（ChatGPT/Claude 等）从香港访问体验差。

**方案**: 
- AI Fallback 自动排除香港
- AI 区域选择不暴露香港
- 仍可通过基准 Smart 或全局模式间接使用

**理由**:
- 优化默认体验
- 高级用户仍有手动选择权
- 避免"为什么我选了自动但 AI 很慢"

### 6. 为什么 Linux 要独立 watchdog 服务？

**问题**: GUI 崩溃或关闭后，TUN 可能继续拦截但 core 已失效。

**方案**: 独立 systemd watchdog，root 运行，持续探测关键路径。

**理由**:
- GUI 生命周期与网络接管解耦
- 连续失败自动 fail-open，避免"断网但不知道"
- root 权限确保能停止服务和清理 TUN

---

## 技术债务和已知限制

### 技术债务

1. **Git 历史缺失**: 项目目录不在 Git 根或未初始化，难以追踪变更
2. **Android Spotless**: 274 个 CRLF 文件阻止全局格式化
3. **Core libbox 测试**: linkname 问题阻止主机 `go test ./experimental/libbox`
4. **Windows 测试覆盖低**: 缺少自动化测试套件

### 已知限制

1. **单订阅源**: 客户端只支持一个 Converter URL（树莓派单点）
2. **无 GUI 规则编辑**: 高级路由需要手动编辑 JSON
3. **流量统计粒度**: 只有总量，无按域名/应用统计
4. **macOS 未支持**: 三平台已覆盖，macOS 需额外开发
5. **IPv6 支持**: 基本可用，但测试覆盖不如 IPv4

### 不会修复的设计选择

1. **不支持原地订阅编辑**: Converter 统一管理是核心架构
2. **Android 不自动拉取**: 避免移动数据消耗，用户主动触发
3. **Linux 默认不自启**: TUN 接管影响全局，用户显式启用
4. **Smart 不支持负载均衡**: 自适应选路不是轮询，优选单节点

---

## 快速开始（开发者）

### 环境要求

- **CachyOS/Linux**: Python 3.10+, PySide6, systemd, Go 1.26.5
- **Android**: Android Studio, SDK 37.1, NDK 28.0.13004108
- **Windows**: Visual Studio 2022, .NET 6.0+, PowerShell 7+
- **树莓派**: Debian/Raspbian, Go 1.26.5, systemd

The exact Go release-build pin is recorded in the root `TOOLCHAIN_VERSION`
file (`go1.26.5`). `core/go.mod` intentionally remains at `go 1.25.5` as the
minimum language version; it is not the exact compiler pin.

### 克隆和构建

```bash
# 注意：当前不在 Git 仓库，需要手动复制或初始化
cd /home/e/workspace/smart-box

# Linux 客户端
cd linux
./build-package.sh
cd ../dist/smart-box-0.1.0-linux-x86_64
./install.sh
smart-box

# Android 客户端
cd android
./gradlew assembleOtherDebug
adb install -r app/build/outputs/apk/other/debug/*.apk

# Converter
cd converter
env GOTOOLCHAIN=go1.26.5 go build -o smart-box-converter .
./smart-box-converter -config config.json
```

### 运行测试

```bash
# Linux 完整验证
scripts/verify-release.sh --allow-live

# Android JVM 测试
cd android && ./gradlew testOtherDebugUnitTest

# Converter 测试
cd converter
env GOTOOLCHAIN=go1.26.5 go test ./...
env GOTOOLCHAIN=go1.26.5 go test -race ./...
```

---

## 贡献指南

### 报告 Bug

1. 检查是否已有类似 issue
2. 提供完整的复现步骤
3. 附上日志（已脱敏）和系统信息
4. 使用 `smart-box-doctor`（待开发）收集诊断包

### 提交代码

1. Fork 仓库（待建立 Git）
2. 创建功能分支 (`feature/your-feature`)
3. 编写测试覆盖变更
4. 确保所有测试通过
5. 提交 Pull Request

### 代码规范

- **Go**: `go fmt`, `go vet`, `golangci-lint`
- **Python**: Black, Flake8, isort
- **Kotlin**: Spotless (待修复 CRLF)
- **C#**: Visual Studio 默认格式

---

## 许可证

GNU General Public License v3.0 (GPL-3.0)

继承自上游 sing-box 的 GPL 许可。

---

## 致谢

- **sing-box**: 上游项目，提供核心代理功能
- **CachyOS**: Linux 测试平台
- **SagerNet**: 规则集来源
- **社区贡献者**: 测试、反馈、文档

---

## 联系方式

- **项目主页**: [待补充]
- **文档**: 见各子目录 README.md
- **Bug 追踪**: [待补充]
- **讨论**: [待补充]

---

**最后更新**: 2026-08-24  
**文档版本**: 1.0  
**维护者**: [待补充]
