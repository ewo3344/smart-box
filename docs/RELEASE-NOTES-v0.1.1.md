# smart-box v0.1.1 Release Notes

**发布日期**: 发布时间由维护者决定
**类型**: Patch Release
**主题**: 测试覆盖和稳定性收敛

---

## 主要变更

### 测试基础设施完善

- **Android T001 完整设备矩阵**：自动化脚本 `scripts/android-full-matrix.sh` 实现 START/STOP 生命周期验证，人工矩阵覆盖 15 项功能场景（13/15 PASS，2/15 DEFERRED 第 10、12 项，0 FAIL）
- **树莓派健康检查**：`scripts/verify-raspberry-pi.sh` 实现只读 SSH 健康检查，覆盖服务状态、route-bypass 规则、profile/cache 完整性
- **Windows 验证脚本**：`scripts/verify-windows.ps1` 完成环境门禁框架。zip 已在 Linux 上交叉编译（PE32+），本轮不随发布提供
- **Submodule 发布门禁**：`scripts/publish-submodules.sh --check` 实现 fail-closed 检查，防止 gitlink 指向不含产品代码的 commit

### 工具链收敛

- Go 工具链统一到 `go1.26.5`（根级 `TOOLCHAIN_VERSION` 文件）
- 版本管理器重写（`scripts/version-manager.sh`）：原子回滚、SemVer 完整校验、Android VERSION_CODE 自动递增

### 文档完善

- 新增 `docs/MANUAL-MATRIX-T001.md`：Android 人工验收矩阵签核记录
- 新增 `docs/DEVICE-MATRIX.md`：设备兼容性清单（vivo V2352A / Android 16 已验证）
- 新增 `RELEASE-CHECKLIST-v0.1.1.md`：标准化发布流程
- 更新 `UPSTREAMS.md`：上游来源与 GPL 归属声明

### 修复

- **树莓派健康检查权限问题**：`verify-raspberry-pi.sh` 现用 `sudo -n` 探测 `/var/lib/smart-box/` 下的 root-owned 文件，避免误报 missing
- **Android STOP 检测**：改用 `isForeground` 和 `startRequested` 实时布尔值，而非累计计数器 `startForegroundCount`

---

## 验证状态

### 已完成验证

| 平台 | 状态 | 证据 |
|------|------|------|
| **Linux** | PASS | `scripts/verify-release.sh --allow-live` |
| **Android** | PASS（自动化） / 人工 13/15 PASS、2 DEFERRED | T001 自动化 + 人工矩阵，vivo V2352A (Android 16) |
| **树莓派** | PASS | converter/core active，route-bypass 生效，profile/cache present |
| **Converter** | PASS | Go 测试（含 `-race`）全部通过 |
| **Submodule** | PASS | gitlink 在 fork 远端可达且含 smart 代码 |

### 不在本次发布范围

| 平台 | 状态 | 原因 |
|------|------|------|
| **Windows** | 不在本次发布范围 | 交叉编译产物 PE32+ 已验证，尚未在 Windows 真机上验证托盘启动、系统代理与 core 重启 |

---

## 已知问题

- **Android 组页不显示节点罚分**：core 已通过 SmartGroupCandidateStatus 暴露 AppliedFailurePenalty / QualityScore 等字段，Android 组页尚未接入，仅显示 urlTestDelay。属功能缺口，不影响选路正确性。计划 v0.1.2 接入。
- **Windows 客户端体积**：self-contained `smart-box.exe` 约 165MB。`PublishTrimmed` 在 `net10.0-windows` + `UseWindowsForms` 下触发 `NETSDK1175`（启用剪裁时不支持 Windows 窗体），不能作为漏参补上。framework-dependent 可降到数 MB，但要求用户自装 .NET 10 运行时，本版不降低安装门槛。体积优化延到 v0.1.2。

---

## 升级说明

### 从 v0.1.0 升级

**Linux**:

```bash
# 备份配置（可选）
cp -r ~/.config/smart-box ~/.config/smart-box.backup

# 安装新版本（会覆盖 /usr/local/lib/smart-box/）
tar xzf smart-box-0.1.1-linux-x86_64.tar.gz
cd smart-box-0.1.1-linux-x86_64
sudo ./install.sh

# 重启服务
sudo systemctl restart smart-box@$USER
```

**Android**:

- 直接安装 APK（覆盖安装会保留数据和设置）

---

## 平台范围

v0.1.1 发布 Linux 与 Android。Windows 客户端本轮不随发布提供：
交叉编译产物（PE32+ 已验证）尚未在 Windows 真机上验证托盘启动、
系统代理切换与 core 崩溃重启，因此不作为支持平台发布。
Windows 支持计划在具备真机验证条件后于后续版本提供。

---

## 下载

产物在 GitHub Release `v0.1.1`（发布时间由维护者决定后上传）：

- Linux (x86_64): `smart-box-0.1.1-linux-x86_64.tar.gz`
- Android (arm64): `smart-box-0.1.1-android-arm64.apk`
- `SHA256SUMS`

完整变更日志见仓库 `CHANGELOG.md`。
