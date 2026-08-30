# 设计决策与已知限制

本文档记录 smart-box 的关键设计取舍及其理由。这些结论多来自实际踩坑，代码注释
和 CHANGELOG 只保留了结果，不保留原因——改动相关代码前请先读这里。

---

## 关键设计决策

### 1. 为什么 Linux 用 gVisor，但订阅保持 mixed？

**问题**：CachyOS 上 `mixed` 和 `system` 栈都能创建 TUN，但 TCP 会话在出站前停滞。

**方案**：Linux runtime 强制 `gvisor`，而 `profile.json` 保持 converter 原始的 `mixed`。

**理由**：
- Converter 输出面向多平台，树莓派与 Windows 可能需要 `mixed`
- 平台特定覆盖在各客户端本地实现，不污染上游 profile
- Android 也独立实现了 runtime 强制 `gvisor`（原因见下）

### 2. 为什么 Android 要 BindNetwork，不只 Protect？

**问题**：部分 Android 网络栈上 `protect()` 返回 true，但 socket 仍然超时。

**方案**：`protect(socket)` 之后再 `defaultNetwork.bindSocket(socket)`。

**理由**：
- 某些厂商实现接受 `protect()` 但不可靠地绑定到底层 Network
- 纯 Kotlin 测试证实 bind 是唯一稳定成功的方案
- Go 侧 FD callback 使用安全复制的 descriptor，避免 PFD 关闭 core 的 socket

另：Android 强制 gVisor 栈是为了绕开部分厂商 mixed 栈的 NAT 表耗尽路径。

### 3. 为什么 Smart 要持久化分数和记忆？

**问题**：每次重启都冷启动，第一个连接可能选到慢速或已失败的节点。

**方案**：
- 节点质量分数（延迟 + 成功/失败历史）持久化到 `cache.db`
- 目标到节点的映射记忆持久化，1 小时 TTL
- 启动时恢复，优先使用低成本节点及仍在有效期内的上次成功节点

**理由**：
- 消除用户可感知的「刚开机很慢」
- 利用历史数据加速收敛
- 7 天衰减避免永久偏好已经过时的节点

失败罚分默认每次 +500，只计入内部 score，不写入 `urlTestDelay`。因此客户端
组页看不到罚分数值——组页只渲染延迟。这是预期行为，不是缺陷。

### 4. 为什么 Converter 要镜像规则集，不直接用 GitHub？

**问题**：客户端直连 GitHub 拉规则集，等于单点故障加慢速。

**方案**：Converter 私有镜像 39 个 SRS 文件，客户端只从 Converter 拉取。

**理由**：
- GitHub 在部分网络环境不可达
- 减少客户端依赖的外部服务数量
- Converter 缓存可离线启动
- 4 MiB 上限加版本验证，防止恶意或损坏的规则进入

### 5. 为什么 AI 策略排除香港？

**问题**：主流 AI 服务从香港出口访问体验差。

**方案**：AI Fallback 自动排除香港；AI 区域选择器不暴露香港选项。

**理由**：
- 优化默认体验，多数用户不会手动调策略
- 高级用户仍可通过基准 Smart 或全局模式间接使用香港节点
- 避免「我选了自动但 AI 很慢」这类难以自查的问题

### 6. 为什么 Linux 要独立的 watchdog 服务？

**问题**：GUI 崩溃或被关闭后，TUN 可能继续拦截流量而 core 已失效——用户断网
但不知道原因。

**方案**：独立的 systemd watchdog，以 root 运行，持续探测关键路径。

**理由**：
- GUI 生命周期与网络接管解耦
- 连续失败自动 fail-open，停止服务并恢复直连
- root 权限确保它有能力停止服务和清理 TUN 接口

---

## 技术债务

1. **Android Spotless**：274 个预存在的 CRLF 文件阻止全局 `spotlessApply`
2. **Core libbox 测试**：`oomprofile` linkname 指向不可用的
   `runtime/pprof.parseProcSelfMaps`，阻塞主机上的 `go test ./experimental/libbox`
3. **Windows 测试覆盖低**：缺少自动化测试套件，且尚未在 Windows 真机验证运行时
4. **Submodule 发布为半手工**：`scripts/publish-submodules.sh` 只提供 `--check`
   与 `--setup-remotes`；快照与推送 fork 目前需手工执行，详见 `UPSTREAMS.md`

## 已知限制

1. **单订阅源**：客户端只支持一个 Converter URL，树莓派是单点
2. **无 GUI 规则编辑**：高级路由规则需手动编辑 JSON
3. **流量统计粒度**：只有总量，没有按域名或应用的细分
4. **macOS 未支持**：需额外开发
5. **IPv6**：基本可用，但测试覆盖不如 IPv4
6. **Android 组页不显示节点罚分**：core 已通过 `SmartGroupCandidateStatus` 暴露
   `AppliedFailurePenalty` 等字段，客户端尚未接入

## 不会修复的设计选择

这些不是待办，是有意为之：

1. **不支持客户端原地编辑订阅**：Converter 统一管理是核心架构
2. **Android 不自动拉取订阅**：避免消耗移动数据，由用户主动触发
3. **Linux 默认不开机自启**：TUN 接管影响全局网络，必须用户显式启用
4. **Smart 不做负载均衡**：自适应选路的目标是优选单节点，不是轮询分摊
