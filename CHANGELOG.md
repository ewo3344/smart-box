# smart-box 变更日志

本文档记录 smart-box 项目的所有重要变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)。

---

## [0.1.1] - 2026-09-12

### 验收
- **P0 门禁 (2026-08-29)**: Linux 单元测试与 converter 测试通过；发布包 checksum 通过。Android 设备矩阵观察到 START/STOP 成功、无失败与 BLOCKED，脚本按设计保留人工项为 MANUAL_REQUIRED。树莓派 converter 与 core 服务为 active，route-bypass 优先级 8998/8999 存在。
- **T001 人工矩阵 (2026-08-29)**: `docs/MANUAL-MATRIX-T001.md` 在设备 `10AE6J03LC001JL` 上签核 1–8、11、13–15 为 PASS；第 9 项失败罚分为 FAIL（待确认）；第 10、12 项 DEFERRED。
- **树莓派健康检查 (2026-08-30)**: `scripts/verify-raspberry-pi.sh --host smart-box-pi` 报告 Result PASS、非 BLOCKED；`smart-box-converter.service` 与 `smart-box.service` 为 active，route-bypass 规则 8998/8999 存在；`file_profile.json=present` 与 `file_cache.db=present`（完整性通过）。
- **Submodule 发布门禁 (2026-08-29)**: `scripts/publish-submodules.sh --check` 拒绝 fork 远端不可达的 gitlink，以及缺少 `smart.go` / Android smart-box 包名的指针。`--setup-remotes` 在 submodule 工作树加 `publish` remote 指向 fork，不更新 gitlink。

### 修复
- **Windows 交叉编译包 (2026-08-30)**: `build-windows.ps1` 在 Linux 上设置 `GOOS=windows` 与 `EnableWindowsTargeting`，并把 `README.md`、`config/` 模板打进 zip。
- **树莓派健康检查误报 missing (2026-08-30)**: `/var/lib/smart-box/profile.json` 与 `cache.db` 实际存在但目录为 root `0700`，无特权 SSH 用户看不到。`verify-raspberry-pi.sh` 对这两条路径增加 `sudo -n` 探测，并把 `file_*=present` 纳入 PASS 条件。
- **TUN 无法启动（部署配置，2026-08-25）**: `smart-box@e.service` 被运行时 mask 且「🎯 基准 Smart」缓存选中已失效的「🇬🇧 英国 Smart」，导致 baseline-dns 与 GitHub 链路 DNS 全部超时、联网验收循环失败。解除 runtime mask，将基准/GitHub 组固定到健康的「🇸🇬 新加坡 Smart」（settings.json 与 runtime.json 同步），备份并移除残留选择的 cache.db。详见 `tun-startup-fix-2026-08-25/`。

### 新增
- **树莓派健康检查首次运行 (2026-08-30)**: converter/core 服务 active，route-bypass 规则生效，profile/cache 完整性通过。
- 版本控制文档和工具 (2026-08-24)
- 完整的开发计划和项目总结 (2026-08-24)
- Git 分支策略和 Commit 规范 (2026-08-24)
- **UPSTREAMS.md** (2026-08-27): 上游来源与 GPL 归属声明。记录 core
  (`db1053f8`) 和 Android 客户端 (`8f634380`) 的导入基线 commit、许可证保留
  说明，以及排除签名密钥/私密订阅/验证快照的公开源码策略。
- **README 上游声明** (2026-08-27): 明确 smart-box 是独立维护的修改版发行，
  不是 SagerNet 官方发布。
- **.gitignore** (2026-08-27): 排除设备签名密钥、私密订阅配置、运行时
  profile/settings、验证快照和构建产物。
- **发布/设备门禁脚本 (2026-08-29)**: 新增 `android-full-matrix.sh`、
  `android-collect-logs.sh`、`verify-windows.ps1`、`verify-raspberry-pi.sh` 和
  `build-all-platforms.sh`。脚本会保存脱敏证据，并把缺少真机、runner 或远端
  连接标为 `BLOCKED/MANUAL_REQUIRED`，不把静态结果冒充端到端通过。
- **发布门禁收敛 (2026-08-29)**: Linux 包名、Windows/Android 产物和 Core
  查找从版本真值派生；发布门禁使用无凭据 fixture，避免误用旧的已安装 Core。
  Android 日志收集对多设备、采集失败和无关系统属性 fail-closed。
- **scripts/publish-submodules.sh (2026-08-29)**: 单向发布流的 fail-closed `--check`（工作树 → 快照 → push fork → 更新 gitlink）。

### 已解决
- **Go 工具链声明收敛 (2026-08-29)**: 新增根级 `TOOLCHAIN_VERSION`，将精确
  发布构建工具链固定为 `go1.26.5`，并同步根文档、Converter 命令和发布门禁。
  `core/go.mod` 保留 `go 1.25.5` 作为最低语言版本；历史验证产物中的
  `go1.25.5` 记录继续保留为历史证据。

### 待修正
- **Android/Windows/树莓派环境门禁**: 自动化脚本已加入，但需要对应真机、
  Windows runner 和树莓派连接后才能完成端到端验收。

---

## [0.1.0] - 2026-08-24

### 新增

#### Core 功能
- **Smart 自适应出站组**: 基于延迟、失败惩罚和目标记忆的智能节点选择
- **节点质量持久化**: 分数和近期成功/失败状态存储在 `cache.db` 的 `smart_score` bucket
- **目标记忆持久化**: 目标到节点映射存储在 `smart_memory` bucket，1 小时 TTL
- **探索探测**: 每轮后台探测保留 4 个当前最优 + 4 个轮换探索候选
- **失败惩罚**: 短期失败降权，7 天历史衰减到中性
- **节点指纹**: 使用 one-way hash 识别节点，配置变更不丢失历史
- **有界重试**: 单次连接最多尝试 8 个候选，最多 2 个并发竞速

#### Linux 客户端 (PySide6)
- **完整桌面 GUI**: 状态、策略、日志、域名、设置五个页面
- **实时监控**: 流量曲线、连接数、内存占用、运行模式
- **26 个策略选择器**: 支持手动地区选择和节点测速
- **域名黑白名单**: 本地覆盖订阅，支持 IDN 和通配符，自动归一化
- **四种运行模式**: Rule（智能分流）/ Global（全局代理）/ Direct（全部直连）/ 节能（AI+Telegram 代理，其他直连）
- **TUN 接口**: `SmartBox` gVisor 栈，避免 CachyOS mixed/system 栈停滞问题
- **Systemd 集成**: 用户级服务，受限 capability，Polkit 精确授权
- **独立 Watchdog**: Root 运行，持续验证关键路径，fail-open 恢复直连
- **DNS 管理**: 自动注册/撤销 systemd-resolved 链路 DNS
- **FlClash 共存**: 启动前停止 FlClash，失败时自动恢复
- **系统源测速**: Arch (pacman/paru) 和 CachyOS 镜像测速和应用
- **持久化 Journal**: Profile/Runtime/Settings 三文件原子提交，带 fsync 和冷启动恢复
- **浅色/深色主题**: 持久化主题选择
- **快捷键支持**: `Ctrl+1-5` 页面切换，`Ctrl+F` 搜索
- **197 测试通过**: 包含 Python 编译、单元测试、真实环境验证

#### Android 客户端 (Kotlin)
- **NAT 耗尽解决方案**: 运行时强制 `gvisor` TUN 栈，避免部分 Android 厂商的
  mixed 栈在长时间运行后耗尽连接资源
- **双重网络绑定**: `protect() + bindSocket()` 确保 Android 出站 socket 绑定到底层 Network
- **TUN FD 保护**: 复制到高描述符 (≥1024) 避免 vendor 异步关闭
- **订阅刷新优化**: 等待真实 reload/restart 回执，区分"已是最新/已保存/已应用"三态
- **有界重启**: 模式变化重启限制 30 秒，ViewModel 回执限制 35 秒
- **域名黑白名单**: 与 Linux 相同的本地覆盖功能
- **Rebranding**: 包名 `io.nekohasekai.sfa.smartbox`，与上游 sing-box 应用共存
- **单订阅源**: 移除多配置列表，改为单一 Converter 端点（协议/主机/端口/私密路径）
- **移除自更新**: 去除 APK 安装、更新源、更新轨道功能，保留 30 分钟自动 profile 刷新
- **Android 冒烟验证**: VPN 启停、gVisor TUN、DNS EPERM=0、Telegram 加载、抖音评论响应

#### Windows 客户端 (WPF)
- **托盘客户端**: 通知区域运行，控制 bundled core
- **系统代理切换**: 可选开启 `127.0.0.1:20808` 系统代理
- **设置迁移**: 从旧品牌 `%LOCALAPPDATA%\sing-box-smart` 一次性复制配置
- **独立标识**: 进程名和数据目录与上游分离

#### 树莓派 Converter (Go)
- **订阅聚合**: 并行拉取多个 Clash 订阅，去重和格式转换
- **节点可达性检查**: TCP 端口探测（非 QUIC），有界并发
- **Emoji 分组**: 按旗帜自动生成区域 Smart 组（如 🇸🇬 新加坡 Smart）
- **基准 Smart 选择器**: 全局自动或手动指定区域
- **26 个业务策略**: AI、Telegram、抖音、流媒体（8 个平台）、社交、游戏、开发服务、厂商生态（Apple/Microsoft/Google）、测速、下载、国内、广告
- **专用 Fallback 池**: AI (排除香港)、Telegram (独立探测 URL)、媒体、游戏
- **39 规则集镜像**: 私有端点，ETag/Last-Modified 支持，4 MiB 限制，原子替换
- **Per-Policy DoH**: 每个策略独立 DoH 传输，DNS 出口跟随策略选择
- **24 小时刷新周期**: 缓存可用时直接启动，不等待首次刷新
- **Route-bypass**: UID 995 强制走物理路由，避免探测流量走 TUN
- **缓存持久化**: Profile 和 Rule cache 存储在 `/var/lib/smart-box`

#### 路由和分流
- **AI 排除香港**: AI Fallback 和区域选择不包含香港，避免访问体验差
- **Telegram 优先级**: 独立规则集 + 专用 Fallback，优先于 TikTok 和广告
- **抖音路由修复**: 专用规则集，优先级高于海外 TikTok 和广告，避免 API 被拦截
- **节能模式**: AI/Telegram 保持代理（域名 + 进程/包名回退），其他流量直连 + 本地 DNS
- **多播直连**: Linux runtime 强制本地多播 CIDR 直连，不经过 Smart
- **DNS EPERM 修复**: 广告域名不再通过 `REJECT` 出站解析，改用 baseline-dns

### 修复
- **Android TUN FD 竞态**: 使用 `F_DUPFD_CLOEXEC` 复制到高描述符，避免异步关闭
- **Android 出站超时**: `protect() + bindSocket()` 确保 socket 绑定到底层 Network
- **Android NAT 耗尽**: 运行时强制 gVisor TUN，避免 system TCP NAT 耗尽
- **Douyin 路由冲突**: 专用规则集优先级高于 TikTok 和广告
- **DNS EPERM 错误**: 广告域名不再通过 REJECT 出站解析
- **CachyOS TUN 停滞**: Linux 使用 gVisor，mixed/system 栈数据路径失效
- **FlClash DNS 污染**: 启动时注册链路 DNS，停止时撤销并刷新缓存
- **Provider 状态伪节点**: Converter 过滤"剩余流量""更新时间"等伪节点
- **Linux runtime 泄漏**: 额外过滤伪节点，确保 Smart 初始候选是真实节点

### 变更
- **版本号体系**: 产品版本 `smart-box 0.1.0`，Core 基于 `sing-box 1.14.0-beta.14`
- **应用标识**: Android `io.nekohasekai.sfa.smartbox`，显示名 `smart-box`
- **工作区迁移**: 从 `C:\sing-box-smart` 移至 `C:\workspace\smart-box` (Windows)
- **配置隔离**: 各平台使用独立配置目录，不与上游 sing-box 冲突
- **Converter 缓存路径**: 从 `/var/lib/sing-box-smart-converter` 改为 `/var/lib/smart-box-converter`

### 已知问题
- Android 完整设备测试矩阵未完成（黑白名单、Fallback、分数恢复、停止按钮热区）
- Windows 自动化测试套件缺失
- 树莓派无监控和告警系统
- Git 初始历史已在 2026-08-29 建立（`main`、`develop` 和 `v0.1.0`）。
- Android 274 个 CRLF 文件阻止全局 Spotless 格式化
- Core `experimental/libbox` 主机测试因 linkname 问题被阻塞
- FlClash 共存场景未在真实环境验证

### 安全
- Linux core 以非 root 用户运行，仅 5 个必要 capability
- Polkit 规则精确限制只能管理自己的服务
- 配置文件权限 0600，配置目录 0700
- 订阅私密路径默认掩码，UI 临时显示 15 秒后自动隐藏
- Provider URL 和私密路径不出现在日志和发布文档

### 性能
- 冷启动时间: < 3 秒 (Linux，有缓存)
- 内存占用: ~150MB (Linux 桌面)，~80MB (Android)
- Smart 节点选择: < 10ms (有缓存)
- 持久化 journal 写入: < 50ms (SSD)

### 文档
- 项目 README 完整更新
- Linux 客户端安装和使用说明
- Converter 部署文档
- 完整路由规则文档 (ROUTING.md)
- 发布和验收说明（不包含设备交接或运行时凭据）
- 当前验收计划 (SMART-BOX-PLAN.md)

### 基础设施
- 发布验证脚本 `scripts/verify-release.sh`
- Linux 构建脚本 `linux/build-package.sh`
- Android 设备签名脚本 `scripts/sign-android-device.sh`
- Windows 构建脚本 `scripts\build-windows.ps1`
- 完整的 `SHA256SUMS` 清单

---

## 变更类型说明

- **新增**: 新功能
- **变更**: 现有功能的变更
- **弃用**: 即将移除的功能
- **移除**: 已移除的功能
- **修复**: Bug 修复
- **安全**: 安全相关的修复或改进

---

## 链接

- [未发布]: https://github.com/ewo3344/smart-box/compare/v0.1.0...HEAD
- [0.1.0]: https://github.com/ewo3344/smart-box/releases/tag/v0.1.0
