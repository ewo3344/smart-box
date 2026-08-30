# Android 设备兼容性矩阵

本文档记录 smart-box Android 客户端的设备验证。

---

## 已验证设备

### vivo V2352A

**验证日期**: 2026-08-29 ~ 2026-08-30

**设备信息**:

- 型号: vivo V2352A (`10AE6J03LC001JL`)
- Android 版本: 16
- 测试版本: smart-box 0.1.0-core.1.14.0-beta.14

**TUN 栈**: gVisor（强制，解决厂商 mixed 栈问题）

**自动化测试结果**:

- START=PASS（VPN 成功启动，`isForeground=true`）
- STOP=PASS（VPN 成功停止）
- FAILURES=0, BLOCKED_COUNT=0
- RESULT=MANUAL_REQUIRED（15 项固定人工计数，符合脚本设计）

**人工验收结果**: 见 `docs/MANUAL-MATRIX-T001.md`

- 域名黑白名单（1–4）: PASS
- 地区选择与 Fallback（5–7）: PASS
- 节点分数机制（8–9）: 第 8 项 PASS；第 9 项 FAIL（手动 urlTest 写 `failures`，组页只显示 `urlTestDelay`，未见 +500）
- 七天衰减（10）: DEFERRED（单次会话无法验证）
- 网络切换（11）: PASS
- 抖音评论（12）: DEFERRED（隐私保护）
- Telegram 收发（13）: PASS
- 通知权限、VPN 授权（14–15）: PASS

汇总：12/15 实测 PASS，1/15 FAIL（第 9 项），2/15 DEFERRED（第 10、12 项）。

**已知问题**:

- 第 9 项失败罚分：手动 urlTest 经 `applySmartProbeState` 写入 `failures` 并更新 score，组页只显示 `urlTestDelay`，未见 +500。计划 v0.1.2。

**0.1.1 覆盖安装 (2026-08-30)**: BLOCKED（签名不匹配）。设备仍为 `0.1.0-core.1.14.0-beta.14` / `versionCode=10000`。`~/.android/debug.keystore` SHA-256 `2e8d0212…` ≠ 已装 0.1.0 的 `8de57370…`。未卸载、数据保留；未跑 0.1.1 `android-full-matrix.sh`。VPN 当时为关（`Active vpn type: -1`，`sessionId=null`）。

**证据位置**: 本地 `verification/` 目录（不入库）

---

## 待验证设备

### 高优先级

- [ ] Google Pixel 7 (Android 14) — AOSP 基线
- [ ] 小米 14 (HyperOS/MIUI) — 厂商定制与后台限制
- [ ] OPPO Find X7 / OnePlus 12 (ColorOS) — 电池优化策略

### 中优先级

- [ ] Samsung Galaxy S24 (One UI)
- [ ] 华为设备 (HarmonyOS)，如可获得

### 低优先级

- [ ] Android 5/6 设备 — Legacy APK 向后兼容

---

## 设备测试方法

### 自动化部分

使用统一脚本（需设备通过 ADB 连接并授权）:

```bash
cd /path/to/smart-box
./scripts/android-full-matrix.sh --serial <DEVICE_SERIAL>
```

验收标准（参考 vivo V2352A 基线）:

- START=PASS 且 STOP=PASS
- FAILURES=0 且 BLOCKED_COUNT=0
- RESULT=MANUAL_REQUIRED + exit 2 为预期（15 项人工计数）

### 人工验收部分

参照 `docs/MANUAL-MATRIX-T001.md` 模板，逐项填写：

1. 域名黑白名单（4 项）
2. 地区选择与 Fallback（3 项）
3. 节点分数机制（3 项，第 10 项可标 DEFERRED）
4. 网络与应用场景（3 项）
5. 系统权限（2 项）

注意事项:

- 不要在文档中记录订阅 URL、Token、账号、私密路径
- 单次会话无法验证的项目（如七天衰减）标注 DEFERRED 并写明依据
- 涉及个人隐私的项目（如真实社交账号操作）标注 DEFERRED 并写明依据
- 每个设备必须同时提供自动化报告与人工签核，缺一不记「已验证」

---

## 报告新设备

如果你在其他设备上完成了验证，欢迎提交 Pull Request：

1. 按照上述方法完成自动化 + 人工测试
2. 复制 vivo V2352A 的结构，在「已验证设备」段新增条目
3. 提交时附带自动化报告的关键字段（脱敏处理）
4. 人工矩阵签核结果可以内嵌或链接到单独文件

---

**维护者**: @ewo3344
**首次创建**: 2026-08-30
**最后更新**: 每次新设备验证后更新
