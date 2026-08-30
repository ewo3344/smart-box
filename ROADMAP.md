# 后续计划

当前版本 0.1.1（2026-08-30 发布，Linux + Android）。本文档记录尚未完成的工作。
已完成项见 `CHANGELOG.md`；设计取舍与已知限制见 `docs/DESIGN-NOTES.md`。

---

## 阻塞下一次发布

### Windows 运行时验证

`scripts/build-windows.ps1` 已能在 Linux 上交叉编译出 PE32+ 产物，但从未在
Windows 上启动过。0.1.1 因此未把 Windows 列入发布范围。

需要在 Windows 真机或 VM 上验证：

- 托盘应用可启动并驻留
- 系统代理开关生效与恢复
- core 崩溃后自动重启
- profile 原子更新

入口：`scripts/verify-windows.ps1`

### release 变体真机验证

0.1.1 的 Android 产物是 debug 签名变体（`android:debuggable="true"`，未启用
proguard）。发布用 release 变体启用混淆，尚未在真机验证。

- 核对 `android/app/proguard-rules.pro` 是否覆盖 libbox JNI 入口与 Gson 序列化类型
- 在真机验证 release 变体；注意它与已装 debug 签名冲突，建议用独立测试设备而非
  覆盖主设备

---

## 从 0.1.1 结转的缺口

- **性能回归未采数**：0.1.1 未与 0.1.0 做启动时间/内存对比
- **`build-all-platforms.sh` 未单独执行**：0.1.1 的产物由各平台脚本分别产出
- **Android 组页接入罚分显示**：core 侧字段已就绪，客户端未接
- **第 10 项七天衰减未验证**：需跨 7 日的节点分数对照，见
  `docs/MANUAL-MATRIX-T001.md`

---

## 性能基准

建立可复现的基线，用于跟踪回归。

指标：冷启动时间（到首个连接）、热启动时间、连接建立延迟、吞吐量（单连接与
并发）、内存占用（空闲与负载）、CPU 占用。

工具：Linux 用脚本；Android 用 Profiler 加自定义计时；网络用 iperf3 与 wrk。

目标：至少 10 个指标有基线数据，定义回归阈值（如 +20% 触发告警）。

---

## 安全审计

**代码审计**：订阅 URL / 域名 / 端口的输入验证；权限最小化；敏感信息处理
（订阅路径、Token）；文件权限与 umask。

**依赖扫描**：Go modules 用 `govulncheck`；Python 用 `safety`；.NET 用
`dotnet list package --vulnerable`；检查 Android AAR 依赖。

**网络**：Converter API 认证强度、TLS 配置、DNS 泄漏、IP 泄漏。

**本地攻击面**：systemd 沙箱、capability 最小化、文件系统访问限制、IPC。

目标：零高危漏洞；中危有缓解措施或修复计划。

---

## 断电与崩溃韧性

当前已验证逐阶段模拟崩溃与真实 SIGKILL，但尚未做物理断电实测。

**环境**：可牺牲的 VM，配合 `dm-flakey` 做文件系统故障注入。

**断电场景**：profile / settings / cache.db 写入过程中断电，验证冷启动恢复。

**崩溃场景**：core 进程 SIGKILL、GUI 异常退出、systemd 服务 OOM。

**数据一致性**：journal 完整性、backup 正确性、fsync 顺序、损坏文件检测。

目标：100 次注入零数据损坏，恢复时间小于 10 秒。

---

## 设备矩阵扩展

已验证：vivo V2352A（Android 16）。待验证设备与优先级见
`docs/DEVICE-MATRIX.md`。

---

## 工程债

见 `docs/DESIGN-NOTES.md` 的「技术债务」一节：Android Spotless 的 CRLF 问题、
core libbox 的 linkname 问题、Windows 测试覆盖、submodule 发布流程半手工。
