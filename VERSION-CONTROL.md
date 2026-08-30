# 版本与分支规范

---

## 版本号

采用语义化版本 2.0.0：`MAJOR.MINOR.PATCH[-PRERELEASE]`

- **MAJOR**：不兼容的配置或订阅格式变更
- **MINOR**：向后兼容的功能新增
- **PATCH**：向后兼容的问题修复
- **PRERELEASE**：`alpha`（功能不完整）、`beta`（功能冻结）、`rc`（仅修严重 bug）

### 组件版本关系

多组件统一版本号协同发布：

```
smart-box 0.1.1
  ├─ Core      基于 sing-box 1.14.0-beta.14
  ├─ Linux
  ├─ Android   versionCode 10001
  ├─ Windows
  └─ Converter
```

规则：
- 所有组件使用同一产品版本号
- Core 版本记录上游 sing-box 基线，用于兼容性追踪
- Android `versionCode` 单调递增，不因版本号回退而重置

### 版本号真值

`VERSION` 是唯一真值，其余位点由 `scripts/version-manager.sh` 同步：

```
VERSION                          # 产品版本号
TOOLCHAIN_VERSION                # 精确 Go 发布工具链（当前 go1.26.5）
core/go.mod                      # Core 最低 Go 语言版本（非发布工具链）
linux/smart_box_backend.py       # Linux APP_VERSION
android/version.properties       # Android 版本与 versionCode
windows/SingBoxSmart.Windows.csproj
converter/go.mod                 # Converter 模块最低 Go 版本
```

改版本号：

```bash
./scripts/version-manager.sh bump 0.1.2 --yes
./scripts/version-manager.sh check      # 7 项须全部 OK
```

`check` 失败即表示某个位点漏改，不要手工编辑单个文件绕过。

---

## 分支

```
main                生产就绪，每个 commit 可发布
develop             开发主线
release/v<版本>     发布准备，仅修 bug 不加功能
```

辅助分支命名：

- `feature/<描述>` — 新功能，合并回 develop
- `bugfix/<描述>` — 缺陷修复，从 develop 分出
- `hotfix/v<版本>-<描述>` — 紧急修复，从 main 分出，合并回 main 与 develop
- `refactor/<描述>`、`docs/<描述>`

`local-archive/*` 保留清理前的历史快照，不推送到公开远端。

---

## Commit 规范

Conventional Commits：

```
<type>: <简短描述>

<可选正文，说明原因而非过程>
```

type 取 `feat` / `fix` / `docs` / `test` / `chore` / `refactor` / `perf`。

---

## 发布流程

1. 从 develop 切 `release/v<版本>`
2. `version-manager.sh bump` 并确认 `check` 全绿
3. 更新 `CHANGELOG.md`，写 `docs/RELEASE-NOTES-v<版本>.md`
4. 跑各平台门禁（见 `README.md` 的验证入口）
5. `scripts/publish-submodules.sh --check` 必须 PASS
6. 合并到 main（`--no-ff`）、打 annotated tag、推送
7. develop 快进到 main
8. 创建 GitHub Release，只挂经过验证的平台产物
9. 从 Release 下载产物重新校验 checksum

发布后从干净目录跑一次 `git clone --recursive` 冒烟，确认 submodule 指针可达
且含产品代码。

---

## Submodule 注意

`android` 与 `core` 是 submodule，工作树常态处于「脏 gitlink」状态
（`M android` / `M core`）。**不要用 `git add .`、`git add -A` 或 `git commit -a`**
——那会把指针倒回不含 smart 代码的上游基线。原因与正确流程见 `UPSTREAMS.md`。
