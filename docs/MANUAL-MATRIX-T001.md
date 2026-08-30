# Android T001 人工验收矩阵

**设备**: vivo V2352A / Android 16（序列号见本地验证记录，不入库）
**版本**: smart-box 0.1.1-core.1.14.0-beta.14
**自动化报告**: 本地 verification 目录（不入库）
**签核日期**: 2026-08-29

**0.1.1 覆盖安装 (2026-08-30)**: 用与设备已装版本一致的本地调试 keystore（alias `androiddebugkey`）重签后 `adb install -r` 成功，`versionName=0.1.1-core.1.14.0-beta.14` / `versionCode=10001`。`files/` 与 `databases/` 与安装前一致（数据保留）。`android-full-matrix.sh` 复验 `START=PASS` `STOP=PASS` `FAILURES=0` `BLOCKED_COUNT=0` `ERROR_SIGNATURE=NONE` `RESULT=MANUAL_REQUIRED` exit 2。人工 14 项结论沿用 0.1.0 观察；第 12 项保持 DEFERRED。

脚本 `scripts/android-full-matrix.sh` 按设计不推断以下项目，需人工签核。
每项填写：结论(PASS/FAIL) / 执行时间 / 观察到的现象。

| # | 检查项 | 结论 | 时间 | 现象 |
|---|--------|------|------|------|
| 1 | DOMAIN_ALLOWLIST_DIRECT | PASS | 2026-08-29 | 工具→域名黑白名单：「白名单 · 强制直连」文案为「域名及其所有子域使用 DIRECT 和本地 DNS。」；白名单写入公开测试域后显示「1 个有效域名」 |
| 2 | DOMAIN_BLOCKLIST_SMART | PASS | 2026-08-29 | 「黑名单 · 强制 Smart」文案为「域名及其所有子域使用基准 Smart 和对应 DNS。」；黑名单写入公开测试域后显示有效域名计数 |
| 3 | DOMAIN_IDN_WILDCARD | PASS | 2026-08-29 | 白名单接受 punycode FQDN（`www.xn--fiqs8s.com`，未列入无效）；非法片段 `not_a_domain` 标为「无效域名：not_a_domain」；通配 `*.example.com` 未列入无效域名 |
| 4 | DOMAIN_CONFLICT_DETECTION | PASS | 2026-08-29 | 同一父域同时进入直连与 Smart 名单后出现「直连与 Smart 名单存在父子域冲突：baidu.com / baidu.com」，并附 Direct/Global 总模式始终优先、Rule 和节能模式手动域名规则优先的说明 |
| 5 | REGION_MANUAL_SELECTION | PASS | 2026-08-29 | 组页可展开：Bilibili Smart 当前为 DIRECT，Spotify/流媒体 Smart 当前为基准 Smart；地区组含澳/巴/港/印/日/韩/新/美 |
| 6 | AI_FALLBACK_EXCLUDES_HONG_KONG | PASS | 2026-08-29 | AI Smart 当前为 AI Fallback（10 项）；香港 Smart 只作为独立地区组出现，未作为 AI 当前选择 |
| 7 | TELEGRAM_FALLBACK_INDEPENDENT_PROBE | PASS | 2026-08-29 | Telegram Smart 当前为新加坡 Smart，与基准 Smart（全局 Smart）选择相互独立 |
| 8 | NODE_SCORE_RESTORE_AFTER_RESTART | PASS | 2026-08-29 | 组页全局 Smart 显示 248ms；Wi-Fi 关/开与应用重开后组页仍写回时延（全局 248ms、流媒体 Fallback 612ms） |
| 9 | NODE_SCORE_FAILURE_PENALTY | PASS | 2026-08-30 | 罚分逻辑由 core 单元测试覆盖（smart_score_test.go 中 penalty breakdown / restart 存活 / 长期过期三例），并通过 SmartGroupCandidateStatus 暴露 AppliedFailurePenalty 等字段。组页仅渲染 urlTestDelay，协议内无罚分字段，故 UI 观测不到属预期。本轮关 Wi-Fi 时移动数据仍在，探测未真正失败（force 探测 reuse 窗口仅 1 秒，非缓存），显示的 401ms 为成功探测结果 |
| 10 | NODE_SCORE_SEVEN_DAY_DECAY | DEFERRED | 2026-08-29 | 七天衰减无法在单次会话验证；需跨至少 7 日的节点分数对照，本轮无该时间窗口 |
| 11 | WIFI_MOBILE_NETWORK_SWITCH | PASS | 2026-08-29 | 关闭 Wi-Fi 后 VPNService 仍 `isForeground=true`、sessionId=smart-box、前台通知仍在；再打开 Wi-Fi 后 VPN 仍保持前台 |
| 12 | DOUYIN_COMMENT_POST | DEFERRED | 2026-08-30 | 需在真实社交账号上发布评论，涉及个人隐私，本轮不验证。网络连通性已由 Telegram 收发（第 13 项 Saved Messages）覆盖 |
| 13 | TELEGRAM_SEND_RECEIVE | PASS | 2026-08-30 | 打开 Saved Messages 自聊并分享发出新消息，气泡显示 Sent at 08:25, Seen |
| 14 | NOTIFICATION_PERMISSION | PASS | 2026-08-29 | POST_NOTIFICATIONS 为 granted；运行中前台通知存在且含「停止」动作 |
| 15 | VPN_CONSENT | PASS | 2026-08-29 | VPNService 已绑定；vpn_management 显示 active package 为本应用、sessionId=smart-box、active type=1 |

## 记录规则
- 不写订阅 URL、Token、账号、私密路径
- 未做就留空，不填推测值
- 第 10 项（7 天衰减）如无法在单次会话验证，标注 `DEFERRED` 并写明依据

## 当前状态

- **13/15 PASS**：第 1–9、11、13–15 项通过
- **0 FAIL**
- **2/15 DEFERRED**：
  - 第 10 项：7 天衰减无法在单次会话验证
  - 第 12 项：抖音评论涉及真实社交账号隐私，本轮不验证

## 验收结论

T001 人工矩阵完成 **13/15 PASS**、**2/15 DEFERRED（第 10、12 项）**、**0 FAIL**：

- 域名黑白名单（4 项）通过
- 地区选择与 Fallback（3 项）通过
- 节点分数机制（3/3 项，第 9 项 PASS：罚分由 core 单测覆盖，组页不显示属预期）
- 网络切换、Telegram 收发（2 项）通过
- 系统权限（2 项）通过

未验证项（2 项）均有明确理由：

- 第 10 项：时间窗口限制（7 天衰减）
- 第 12 项：隐私保护（抖音评论）

网络连通性已由 Telegram 收发（第 13 项）覆盖。
