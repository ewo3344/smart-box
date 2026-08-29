# smart-box 完整可用计划

更新时间：2026-08-24

这份计划是项目当前的验收入口。它把源码测试、真实核心、桌面运行、Android
真机和树莓派服务分开记录；历史交接记录不再作为当前状态的唯一依据。

## 当前基线

- Linux 源码、`dist/smart-box-0.1.0-linux-x86_64` 发布目录与
  `/usr/local/lib/smart-box` 已同步；backend SHA-256 为
  `1b331d74f0ac83851e946be134e1b3c4c86e4388297a2ccd71a33f8428cce6b7`，GUI 为
  `dc5eba82ec0f158dcf425e6ec8d2ef594cf278ca12cb031e747030ef1a21347c`；主服务 unit 为
  `ef3a7c5c4ca0bd57a76f10b09b2c30ab8753de48558b7701497abdefb7666ad8`，cleanup unit 为
  `e7ef479bd0a1b49852f0bf35a6b0b268d532242c16b37602a94391b6a768fbe0`。项目内
  `SHA256SUMS` 全部通过，tar SHA-256 为
  `72c437a7780570d6091f3972c58ac10b5a28c9e01e3d7bf22ac1fb573209cdfe`。本轮旧版 backend/GUI
  保存在 `~/.local/state/smart-box/backups/runtime-20260824-092700-journal/`。
- 当前完整 Linux 回归为 197/197 通过；`scripts/verify-release.sh --allow-live`
  已整体通过 Python 编译、shell 语法、四个 systemd 单元、真实 profile/runtime
  core check、converter Go 测试、发布目录 15 项 checksum 和系统副本 `cmp`。
- Linux runtime 使用 gVisor TUN、SmartBox 接口、`127.0.0.1:20808` mixed 和
  `127.0.0.1:20809` Clash API。
- Smart 节点质量分数和目标到节点记忆已经持久化，并在启动时优先使用
  低成本节点及仍在有效期内的上次成功节点；失败冷却状态也会跨核心重启恢复。
- 本机于 2026-08-24 08:06 发生过一次整机重启；重启后 smart-box 主服务与
  watchdog 均为 active，当前 PID 分别为 2548、112458。这次 core PID 变化来自整机重启，
  不归因于 GUI/backend-only 部署。
  SmartBox TUN、runtime profile、Clash API 与遥测均正常，运行模式为 Rule。
  本轮部署只重启 GUI 与 watchdog，core PID 2548 全程未变；桌面启动器的单实例
  唤醒返回 0，当前 GUI PID 112449 已加载新版，watchdog `NRestarts=0`。
  FlClash 的历史 unit 当前未安装，因此不能伪造
  `app-FlClash@autostart.service=active`。
- Android 静态构建、JVM 测试和一次 live VPN/TUN/Telegram/抖音 smoke 矩阵
  已通过；当前 `adb devices` 已识别 vivo V2352A。
- Android 仪表盘订阅刷新现在等待运行服务真实 reload/restart 回执后才显示成功，
  并区分“订阅已是最新 / 已拉取并保存 / 已拉取并应用”；模式变化重启限制为 30 秒，
  ViewModel 回执限制为 35 秒。拉取失败与“已保存但应用失败”分开反馈，弹窗不再拼接
  可能含私密订阅 URL 的底层异常。当前 Android JVM 门禁为 20/20，arm64 构建通过；
  真机最终包 SHA-256 为 `dbeda8773446ef0888bf4222f11aa195c66858f17843b155076dae5a6c9bed3d`，
  证据见 `verification/android-profile-refresh-20260824/`。
- 树莓派 converter、wrapper 和 route-bypass 均 active；刷新周期为 24 小时，
  当前缓存 profile 可被核心使用。
- Linux 设置页已加入只读 pacman/paru（Arch）与 CachyOS 源测速；排序结果写入
  `~/.local/state/smart-box/mirror-rankings/`，只有显式点击“应用最快源”才请求
  root 授权替换 `/etc/pacman.d/*`，应用前保留备份。
- Linux 状态页的运行模式已改为中文用途卡片，并补充影响说明；内部仍使用原始
  core mode 值，避免 UI 文案与运行配置耦合。
- Linux 状态页提供“立即验网”，用户可主动检查国内、公网、Smart 代理、GitHub
  和 Telegram 五条关键路径，并在按钮、状态横幅和验收字段看到一致的进度与结果。
- Linux 域名名单页会按规范化结果识别未保存更改；无变化、格式错误或直连/Smart
  冲突时禁止应用，避免无意义重载核心，并通过状态文字与悬浮详情解释原因。
- Linux 分流策略页支持按策略名、当前节点和测速状态筛选，并实时展示匹配数；零
  结果会明确提示，而不是留下容易误解为空配置的空白页面。
- Linux 运行日志页可按关键词即时筛选最近 500 行，并显示匹配数；筛选条件在
  手动或自动刷新后保持生效，清除关键词即可恢复完整缓存日志。
- Linux 设置页的私密订阅路径默认掩码；临时显示 15 秒后自动隐藏，离开设置页
  或窗口收起到托盘时立即重新掩码，避免敏感路径长期暴露在屏幕上。
- Linux 设置页会实时校验订阅主机、端口和私密路径，并区分已保存/未保存状态；
  地址无效时禁止保存及拉取，无变化时禁止重复保存，减少延迟报错和无意义写入。
- 退出客户端前会同时检查域名名单和订阅地址草稿；包括尚未填完或格式无效的
  订阅输入。取消退出不会停止轮询或丢失草稿，并自动返回对应编辑页。
- 订阅的协议、主机、端口、私密路径已建立可访问名称和标签关联；域名编辑器的
  Tab 键会移动焦点，每个策略的节点选择、测速状态与按钮也包含唯一策略名上下文。
- “登录时启动客户端”以 autostart desktop 文件为唯一真值来源；写入失败时按
  文件实际状态恢复复选框，不再受多余 settings 二次写入影响。
- 域名名单应用已标记是否触碰过实时核心；保存旧设置、恢复 runtime、重启旧核心的
  任一回滚步骤失败都会保留完整错误链，并执行经验收的 fail-open 直连恢复。
- KDE 桌面代理安装在最后状态 JSON 落盘失败时，会恢复原 `kioslaverc`
  内容、属主与权限，或删除本次新建的配置；补偿也失败时同时报告两层原因。
- 普通 CLI `cleanup` 或 `desktop-proxy restore` 在主服务 active 时拒绝执行，systemd
  启停使用隐藏的显式生命周期参数；服务状态查询失败时 fail-closed。发布测试使用临时
  HOME/XDG，并修复了两个漏 mock 的 cleanup 测试，门禁不再修改真实 KDE 代理。当前
  `kioslaverc` 为 ProxyType=1，HTTP/HTTPS/SOCKS 均指向 `127.0.0.1:20808`。
- settings 写入使用稳定的 `.settings.json.lock` 旁路文件和跨进程 `flock`；
  每次字段更新都在短锁内 fresh-load 并原子替换，当前真实锁文件为 `0600 e:e`。
  策略、主题、模式、域名、订阅与 TUN 栈失败只条件回滚自己拥有的字段，不再
  把 API 延迟前的旧整文档覆盖其他已成功操作。
- profile 拉取和 canonical runtime 生成使用每次操作唯一的候选文件；核心校验后在
  同一 settings 锁内复核 mode、TUN 栈、日志、域名和 selector 快照，profile 或相关
  settings 变化即重新生成、重新校验。三文件提交任一步异常会补偿恢复旧 bundle；
  GUI 联网验收回退使用提交凭据，只在仍拥有当前 bundle 时恢复，不能覆盖更晚的 CLI 拉取。
- profile/runtime/settings 三文件提交现在使用持久化 prepared/committed journal：替换前以
  `0600` 备份记录旧内容、权限、长度和 SHA-256，并对备份、journal、目标文件及父目录执行
  fsync；prepared 冷启动精确恢复旧 bundle，committed 冷启动校验并保留新 bundle，损坏或
  元数据不匹配时 fail-closed 而不猜测覆盖。GUI、CLI prepare/run、设置读写和新拉取都会
  先恢复遗留事务；真实子进程在 runtime 替换后遭 SIGKILL 的测试已验证下一进程能取得
  flock 并逐字节恢复。设置读取同样参与旁路锁，但用只读 fd 打开既有锁 inode，兼容
  watchdog 的 `ProtectHome=read-only` 沙箱；真实 watchdog 已跨探测周期保持零重启。
- 服务启停、profile、域名、模式、策略与 TUN 栈更改共用 GUI 核心事务代际；过期联网
  探针在任何状态写入或 fail-open 前都会二次核对代际。事务期间电源、重启、模式、策略、
  域名应用、订阅拉取和 TUN 栈统一禁用，轮询/策略刷新不能提前重新启用；拉取与域名应用
  还会冻结对应编辑器，任务未排入或回调抛错也由事务层恢复控件。
- Linux 系统源测速结果按 Arch/CachyOS 分开缓存和展示；测速或应用期间锁定仓库
  选择，切换仓库时不会沿用另一仓库的“可应用”状态，避免误导高权限操作。
- 浅色与深色主题都为主操作按钮定义了明确禁用态，确保订阅或域名校验失败时
  不仅逻辑上不可点击，视觉上也不会继续呈现为可执行的蓝色按钮。
- “立即验网”会按运行状态选择探测范围：核心运行时检查五条 TUN/代理关键路径，
  核心停止时禁用代理并只验收物理直连，避免把预期不存在的 Smart 路径误报为降级。
- Linux 五个页面已在声明的 920×660 最小窗口下检查，并增加无横向溢出与顶部
  导航不重叠门禁；策略刷新会立即隐藏旧空状态，避免真实策略已出现时短暂叠字。
- Linux 成功、警告、错误等状态文字改用主题语义色，夜间模式切换会刷新现有状态；
  实时流量图和图例也使用深色专用高对比蓝/绿色，避免浅色行内样式覆盖夜间主题。
- 启动后的策略预加载改为静默后台任务，不再用“读取策略已完成”抢占状态页反馈；
  用户主动进入策略页或点击刷新时仍保留可见的读取进度。
- Linux 日志页可一键复制当前筛选后可见的日志行，并反馈复制行数；没有匹配结果
  或视图已清空时按钮自动禁用，避免误复制完整日志或空内容。
- 日志读取暂时失败时保留最后一次成功的 500 行缓存、筛选词和可复制结果；状态栏
  标出保留行数，并把 journal 错误放在悬浮详情，避免诊断证据被错误文本覆盖。
- 核心运行中切换模式后会立即执行该模式的联网验收；关键路径不可用时自动把
  core 和持久化设置恢复到原模式，部分站点降级但关键路径可用时保留模式并明确提示。
- 运行模式 PATCH 后即使联网探针直接抛异常也会回滚核心；若回滚 PATCH 本身失败，
  则触发 fail-open 停止异常 TUN 并恢复直连，避免 UI、设置和实时 core 模式分叉。
- 在线策略切换后若设置落盘失败，会同时 PUT 回原节点并恢复旧设置；不再出现界面
  显示旧节点、核心实际仍使用新节点的状态不一致。
- `/connections` 遥测短暂失败时保留最后有效累计量和图表基线，卡片明确显示“暂不可用”；
  恢复后只按跨缺口的真实增量计算，避免零占位造成巨幅假流量尖峰。
- 订阅下载或校验尚未提交时不写回下载前快照，也不停止正在正常工作的 TUN；仅在已经
  尝试重启新配置后才 stop/start。若验收窗口出现更晚的 profile bundle，则保留新 bundle
  并按当前配置恢复核心，避免失败拉取覆盖并发成功更新。
- 模式切换、联网降级和自动回滚反馈统一使用“智能分流/全局代理/全部直连/节能模式”
  中文名称，不再向用户暴露 `Rule`、`Global` 等 core 内部值。
- Linux 顶部五页支持 `Ctrl+1…5` 快速切换；策略和日志页支持标准 `Ctrl+F`
  聚焦并全选当前搜索词，快捷键同时写入导航悬浮提示，提升键盘可达性。
- 2026-08-22 已完成一次直连 IPv4 吞吐重测并应用到普通 pacman、paru 的用户
  pacman 配置和 CachyOS v3 列表：Arch 首选 `mirrors.wsyu.edu.cn`，CachyOS
  首选 `mirror.nju.edu.cn`；每份列表仍保留多个后备源，证据在
  `verification/mirror-speed-20260822/`。

## 阶段与门禁

### P0：网络接管不破坏日常使用

- [x] 启动前停止 FlClash 并检查旧 TUN。
- [x] 启动后验证 SmartBox、mixed/API、resolved DNS 和五路联网探针。
- [x] watchdog 连续失败时停止主服务、清理 TUN、撤销链路 DNS。
- [x] 停止服务后确认 SmartBox 消失且直连恢复。
- [x] 多播地址在 runtime 路由中直连，避免被 Smart selector 接管。
- [ ] 在一台存在 FlClash unit 的机器上验证失败切换后自动恢复原 owner。

### P1：分流和 Smart 选择

- [x] Rule、Global、Direct、节能四种模式。
- [x] 26 个业务 selector、手动地区选择、AI 排除香港、Telegram 专用 Fallback。
- [x] 域名白名单强制 DIRECT，黑名单强制基准 Smart；父域和 IDN 归一化。
- [x] 节点质量分数、失败惩罚、探索探测和 selector 选择持久化。
- [x] 目标到节点记忆跨核心重启持久化，并用持久化失败冷却测试验证旧节点暂时避让。
- [ ] 对每个高影响策略运行真实 core route assertion，确认具体规则优先级。

### P1：订阅和树莓派

- [x] converter 原始 profile 与客户端 runtime 隔离。
- [x] 39 个 rule-set 镜像、ETag/Last-Modified、原子替换和最后有效缓存。
- [x] provider refresh 设为每日一次；缓存可在重启后直接启动。
- [x] converter probe route-bypass 与 smart-box TUN 隔离。
- [ ] 下一次计划刷新后核对 profile hash、规则完整性和 core 子进程仍在线。

### P1：本机软件源

- [x] 设置页和 CLI 提供 Arch（pacman/paru 共用）与 CachyOS 源只读测速。
- [x] 仅显式应用才写源列表；应用前备份，普通 pacman 与 paru 用户配置已同步。
- [ ] 网络环境变化后重新测速，避免把一次性吞吐结果当作永久排序。

### P1：Android 真机

- [x] Android runtime 强制 gVisor，解决 vivo 混合栈 NAT 耗尽路径。
- [x] device-signed arm64 APK 可覆盖安装并保留数据。
- [x] 已在 vivo V2352A 上验证 VPN 启停、gVisor TUN、DNS EPERM=0、Telegram
  加载和抖音首页/评论入口响应；独立 live 证据见
  `verification/android-live-20260822/`。
- [x] 仪表盘运行中 profile refresh 已有完成回执、三态反馈和有界重启；vivo 上以
  强制 1 字节差异验证远端内容恢复、同应用 PID 及成功提示。reload 分支保持同一
  VPN NetworkAgent，模式重建分支会替换 NetworkAgent，因此不笼统声称所有刷新都不掉 VPN。
- [ ] 仍待设备矩阵：黑白名单、地区/Fallback、节点分数恢复，以及手动停止按钮
  热区；本轮没有声称完整评论发布或媒体流测试。
- [ ] 记录启动/停止、网络切换、进程重启和崩溃恢复的 logcat 结果。

### P2：Windows 和发布

- [x] Windows WPF 客户端和 bundled core 可构建。
- [x] Linux profile/runtime/settings 使用持久化 journal 和幂等冷启动恢复；逐提交点、
  并发读取、损坏 journal/备份、已提交目标篡改及真实 SIGKILL 均有自动化门禁。
- [x] Linux 发布目录采用干净临时 staging 和精确 manifest 重建，残留 `__pycache__`
  不会进入 tar；README、安装/卸载脚本在内的 15 项 checksum 全部通过。GUI 系统
  副本完成无核心重启替换及 GUI/watchdog 重载，真实桌面启动器单实例唤醒和运行态复核通过。
- [ ] 在 Windows runner 上自动验证代理切换/恢复、core 崩溃重启、profile
  原子更新和托盘启动。
- [ ] 发布门禁脚本默认运行 Linux、converter、真实 core check、发布 checksum；
  Android 真机和 Windows runner 作为显式环境门禁，不允许静默跳过。

## 每次发布的固定命令

```text
python3 -m compileall -q linux
PYTHONPATH=linux QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s linux/tests -p 'test*.py' -v
sh -n linux/build-package.sh linux/install.sh linux/uninstall.sh linux/smart-box linux/smart-box-profile
systemd-analyze verify linux/smart-box@.service linux/smart-box-watchdog@.service linux/smart-box-cleanup@.service linux/smart-box-unmask@.service
env SMART_BOX_CORE=/usr/local/lib/smart-box/smart-box-core GOTOOLCHAIN=go1.26.5 go test ./...   # converter; pin from TOOLCHAIN_VERSION
/usr/local/lib/smart-box/smart-box-core check -D ~/.local/state/smart-box -c ~/.config/smart-box/profile.json
/usr/local/lib/smart-box/smart-box-core check -D ~/.local/state/smart-box -c ~/.config/smart-box/runtime.json
(cd dist/smart-box-0.1.0-linux-x86_64 && sha256sum -c SHA256SUMS)
```

涉及 TUN 的命令必须使用自动清理；验收结束后逐项确认主服务、watchdog、SmartBox
接口、20808/20809 和直连状态。Android 设备未连接时只能报告静态门禁，不能报告真机
通过。应优先运行 `scripts/verify-release.sh`；脚本会为 Python 测试创建临时
HOME/XDG，防止测试默认路径触碰真实桌面配置。

## 当前下一步

1. 在可牺牲 VM/文件系统故障注入环境补真实断电测试，验证 journal 的 fsync 顺序；当前已
   验证逐阶段模拟崩溃与真实 SIGKILL，不把它们夸大成物理断电实测。
2. 继续完成 Android 黑白名单、地区/Fallback、分数恢复和停止按钮热区矩阵；
   仪表盘运行中订阅刷新已完成，通用 profile 编辑器/后台自动更新回执另行统一。
3. 下一次计划刷新后核对树莓派 profile hash、规则完整性和 core 子进程仍在线。
4. 在 Windows runner 上补系统代理、core 崩溃恢复、原子刷新和托盘自动化测试。
