# smart-box v0.1.1 Release Notes

**发布日期**: 2026-09-12
**类型**: Patch Release
**主题**: 测试覆盖和稳定性收敛

---

## 主要变更

### 测试基础设施完善

- **Android T001 完整设备矩阵**：自动化脚本 `scripts/android-full-matrix.sh` 实现 START/STOP 生命周期验证，人工矩阵覆盖 15 项功能场景（12/15 实测通过，3/15 有明确 DEFERRED 理由）
- **树莓派健康检查**：`scripts/verify-raspberry-pi.sh` 实现只读 SSH 健康检查，覆盖服务状态、route-bypass 规则、profile/cache 完整性
- **Windows 验证脚本**：`scripts/verify-windows.ps1` 完成环境门禁框架（等待 Windows runner 端到端验证）
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
| **Android** | PASS | T001 自动化 + 12/15 人工矩阵，vivo V2352A (Android 16) |
| **树莓派** | PASS | converter/core active，route-bypass 生效，profile/cache present |
| **Converter** | PASS | Go 测试（含 `-race`）全部通过 |
| **Submodule** | PASS | gitlink 在 fork 远端可达且含 smart 代码 |

### 待完成验证

| 平台 | 状态 | 原因 |
|------|------|------|
| **Windows** | 待验证 | 等待 Windows runner 环境配置 |

---

## 已知问题

- **Android 第 9 项（NODE_SCORE_FAILURE_PENALTY）**：关网后手动测速时，UI 组页未显示 +500 失败罚分。待确认是否为预期行为（罚分仅在实际连接失败时触发 vs 测速失败也触发）。不影响正常使用。

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

**Windows**:

- 解压新版本 zip，覆盖旧版本目录

---

## 下载

产物在 GitHub Release `v0.1.1`（发布日上传）：

- Linux (x86_64): `smart-box-0.1.1-linux-x86_64.tar.gz`
- Android (arm64): `smart-box-0.1.1-android-arm64.apk`
- Windows (x64): `smart-box-0.1.1-windows-x64.zip`
- `SHA256SUMS`

完整变更日志见仓库 `CHANGELOG.md`。
