# smart-box 文档索引

本文档是 smart-box 项目所有文档的导航中心。

---

## 📚 核心文档

### 项目概览
- **[README.md](./README.md)** - 项目主页，技术概述和快速开始
- **[PROJECT-SUMMARY.md](./PROJECT-SUMMARY.md)** - 完整项目总结，技术亮点和设计决策
- 设备交接和运行时凭据不纳入公开文档，请在私有运维空间维护

### 规划和管理
- **[DEVELOPMENT-PLAN.md](./DEVELOPMENT-PLAN.md)** - 完整开发计划，里程碑和任务列表
- **[SMART-BOX-PLAN.md](./SMART-BOX-PLAN.md)** - 当前验收计划和门禁清单
- **[VERSION-CONTROL.md](./VERSION-CONTROL.md)** - 版本控制策略和发布流程
- **[UPSTREAMS.md](./UPSTREAMS.md)** - 上游归属与 submodule 单向发布流
- **[CHANGELOG.md](./CHANGELOG.md)** - 版本变更历史
- **[docs/MANUAL-MATRIX-T001.md](./docs/MANUAL-MATRIX-T001.md)** - Android T001 人工签核矩阵

### 快速参考
- **[QUICK-REFERENCE.md](./QUICK-REFERENCE.md)** - 常用命令和故障排查一页速查

---

## 🖥️ 平台文档

### Linux (CachyOS)
- **[linux/README.md](./linux/README.md)** - 安装、配置和使用指南
- 功能：PySide6 GUI、systemd 集成、Watchdog 保护、源测速

### Android
- **[android/README.md](./android/README.md)** - 构建和部署说明
- 功能：Kotlin 客户端、gVisor TUN、订阅刷新、域名覆盖

### Windows
- 功能：WPF 托盘客户端、系统代理切换、bundled core
- 文档：待完善（当前在主 README）

### Raspberry Pi Converter
- **[converter/README.md](./converter/README.md)** - 部署和配置指南
- **[converter/ROUTING.md](./converter/ROUTING.md)** - 完整路由规则和 Fallback 语义
- 功能：订阅聚合、39 规则集镜像、24 小时刷新

### Core (sing-box fork)
- **[core/README.md](./core/README.md)** - Core 开发文档（如存在）
- 功能：Smart 自适应出站组、节点评分、持久化

---

## 🛠️ 开发文档

### 构建和测试
- **[scripts/verify-release.sh](./scripts/verify-release.sh)** - Linux 发布验证脚本
- **[scripts/version-manager.sh](./scripts/version-manager.sh)** - 版本管理工具
- **[scripts/init-git.sh](./scripts/init-git.sh)** - Git 仓库初始化
- **[scripts/sign-android-device.sh](./scripts/sign-android-device.sh)** - Android APK 签名
- **[scripts/build-windows.ps1](./scripts/build-windows.ps1)** - Windows 构建脚本

### 测试证据
- 发布门禁会在本地生成脱敏报告；`verification/` 目录由 `.gitignore` 排除，
  不作为公开源码的一部分

---

## 📖 使用指南

### 新用户入门
1. 阅读 [README.md](./README.md) 了解项目概况
2. 根据平台查看对应的安装指南：
   - Linux: [linux/README.md](./linux/README.md)
   - Android: [android/README.md](./android/README.md)
   - Windows: 主 README 的 Windows 部分
3. 参考 [QUICK-REFERENCE.md](./QUICK-REFERENCE.md) 快速上手

### 开发者入门
1. 阅读 [PROJECT-SUMMARY.md](./PROJECT-SUMMARY.md) 理解架构
2. 查看 [DEVELOPMENT-PLAN.md](./DEVELOPMENT-PLAN.md) 了解当前任务
3. 学习 [VERSION-CONTROL.md](./VERSION-CONTROL.md) 掌握工作流程
4. 使用 [scripts/init-git.sh](./scripts/init-git.sh) 初始化仓库

### 维护者入门
1. 熟悉 [SMART-BOX-PLAN.md](./SMART-BOX-PLAN.md) 的验收标准
2. 掌握发布流程（VERSION-CONTROL.md）
3. 定期更新 [CHANGELOG.md](./CHANGELOG.md)
4. 维护本地（不入库）的脱敏测试报告

---

## 🎯 按任务查找文档

### 我想安装 smart-box
- Linux: [linux/README.md](./linux/README.md) → 安装章节
- Android: [android/README.md](./android/README.md) → 构建和安装
- Windows: [README.md](./README.md) → Windows 客户端

### 我想配置订阅
- Converter 端：[converter/README.md](./converter/README.md) → 配置章节
- 客户端：各平台 README → 订阅设置

### 我想理解路由规则
- [converter/ROUTING.md](./converter/ROUTING.md) - 26 个策略详解
- [README.md](./README.md) → Smart outbound 章节
- [PROJECT-SUMMARY.md](./PROJECT-SUMMARY.md) → 路由和分流

### 我想贡献代码
1. [VERSION-CONTROL.md](./VERSION-CONTROL.md) → Git 分支策略
2. [DEVELOPMENT-PLAN.md](./DEVELOPMENT-PLAN.md) → 查找待完成任务
3. [QUICK-REFERENCE.md](./QUICK-REFERENCE.md) → 开发命令速查
4. 提交 PR 前运行 `scripts/verify-release.sh`

### 我想发布新版本
1. [VERSION-CONTROL.md](./VERSION-CONTROL.md) → 发布流程
2. [DEVELOPMENT-PLAN.md](./DEVELOPMENT-PLAN.md) → 发布检查清单
3. 使用 `scripts/version-manager.sh bump X.Y.Z`
4. 更新 [CHANGELOG.md](./CHANGELOG.md)

### 我想排查问题
- [QUICK-REFERENCE.md](./QUICK-REFERENCE.md) → 故障排查章节
- [linux/README.md](./linux/README.md) → 命令行检查
- 查看日志（journalctl / adb logcat / 事件查看器）
- 查看 CHANGELOG.md 和组件 README 中的已知问题

### 我想理解设计决策
- [PROJECT-SUMMARY.md](./PROJECT-SUMMARY.md) → 关键设计决策
- 设计决策 → PROJECT-SUMMARY.md 和组件 README
- 各组件的 README 和代码注释

### 我想查看性能指标
- [PROJECT-SUMMARY.md](./PROJECT-SUMMARY.md) → 性能指标
- [DEVELOPMENT-PLAN.md](./DEVELOPMENT-PLAN.md) → 成功指标
- [QUICK-REFERENCE.md](./QUICK-REFERENCE.md) → 性能参考值

---

## 📊 文档状态

### 完整文档 ✅
- README.md
- PROJECT-SUMMARY.md
- DEVELOPMENT-PLAN.md
- VERSION-CONTROL.md
- CHANGELOG.md
- QUICK-REFERENCE.md
- linux/README.md
- converter/README.md
- converter/ROUTING.md

### 部分文档 ⚠️
- android/README.md - 基本可用，待补充详细构建步骤
- 设备交接记录不随公开源码发布

### 待完善文档 📝
- core/README.md - 待创建
- windows/README.md - 待从主 README 分离
- 用户手册 - 待创建（非技术用户导向）
- API 文档 - 待创建（Converter API 规范）
- 故障诊断指南 - 待详细化
- 贡献指南 - 待正式化

---

## 🔄 文档维护

### 更新频率
- **每次发布必更新**：CHANGELOG.md, VERSION, README.md
- **每月审查**：DEVELOPMENT-PLAN.md（任务进度）
- **每季度审查**：VERSION-CONTROL.md（兼容性矩阵）
- **按需更新**：其他文档

### 责任人
- 项目文档（README, PROJECT-SUMMARY）：项目负责人
- 开发计划（DEVELOPMENT-PLAN）：技术负责人
- 版本控制（VERSION-CONTROL, CHANGELOG）：发布经理
- 平台文档（各 README）：对应平台开发者
- 本地测试报告（不入库）：测试工程师

### 文档规范
- 使用 Markdown 格式
- 中文文档使用简体中文
- 技术术语保持英文（如 Smart、Fallback、TUN）
- 代码块指定语言（```bash, ```json 等）
- 链接使用相对路径
- 包含最后更新时间

---

## 🔍 搜索提示

### 关键词索引
- **Smart 出站组**: README.md, PROJECT-SUMMARY.md, converter/ROUTING.md
- **持久化**: PROJECT-SUMMARY.md, CHANGELOG.md, SMART-BOX-PLAN.md
- **Android NAT**: CHANGELOG.md, PROJECT-SUMMARY.md
- **FlClash 共存**: linux/README.md, DEVELOPMENT-PLAN.md
- **版本号**: VERSION-CONTROL.md, scripts/version-manager.sh
- **发布流程**: VERSION-CONTROL.md, DEVELOPMENT-PLAN.md
- **故障排查**: QUICK-REFERENCE.md, linux/README.md
- **性能指标**: PROJECT-SUMMARY.md, DEVELOPMENT-PLAN.md

### 常见问题文档位置
- 如何安装？ → 各平台 README.md
- 如何升级？ → VERSION-CONTROL.md
- 如何回滚？ → VERSION-CONTROL.md, QUICK-REFERENCE.md
- 配置文件在哪？ → 各平台 README.md, QUICK-REFERENCE.md
- 如何查看日志？ → QUICK-REFERENCE.md
- 为什么选择这个设计？ → PROJECT-SUMMARY.md
- 下一步做什么？ → DEVELOPMENT-PLAN.md
- 有哪些已知问题？ → CHANGELOG.md, SMART-BOX-PLAN.md

---

## 📞 获取帮助

### 内部团队
1. 查找相关文档（使用本索引）
2. 查看 CHANGELOG.md 和组件 README 中的已知问题
3. 询问对应平台负责人

### 外部用户（待建立）
1. 查看文档和 FAQ
2. 搜索已有 Issue
3. 提交新 Issue（附上诊断信息）
4. 社区讨论（论坛/Discord）

---

## 📝 文档贡献

发现文档错误或需要改进？

1. 小修正：直接编辑并提交 PR
2. 大改动：先创建 Issue 讨论
3. 新文档：参考现有文档的格式和风格
4. 提交前检查：拼写、链接、代码块语法

---

**最后更新**: 2026-08-24  
**维护者**: 项目团队  
**反馈**: 通过 Issue 或 PR
