# Android T001 人工验收矩阵

**设备**: vivo V2352A (10AE6J03LC001JL) / Android 16
**版本**: smart-box 0.1.0-core.1.14.0-beta.14
**自动化报告**: 本地 verification 目录（不入库）
**签核日期**: 2026-08-29

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
| 9 | NODE_SCORE_FAILURE_PENALTY | | 2026-08-29 | 关 Wi-Fi/数据后对全局 Smart 节点测速，logcat 出现 unavailable: context deadline exceeded，组页 Smart 时延数字消失（DIRECT 仍 227ms）；组页 Score 为 urlTestDelay，未见 +500 失败罚分 |
| 10 | NODE_SCORE_SEVEN_DAY_DECAY | DEFERRED | 2026-08-29 | 七天衰减无法在单次会话验证；需跨至少 7 日的节点分数对照，本轮无该时间窗口 |
| 11 | WIFI_MOBILE_NETWORK_SWITCH | PASS | 2026-08-29 | 关闭 Wi-Fi 后 VPNService 仍 `isForeground=true`、sessionId=smart-box、前台通知仍在；再打开 Wi-Fi 后 VPN 仍保持前台 |
| 12 | DOUYIN_COMMENT_POST | | | |
| 13 | TELEGRAM_SEND_RECEIVE | PASS | 2026-08-30 | 打开 Saved Messages 自聊并分享发出新消息，气泡显示 Sent at 08:25, Seen |
| 14 | NOTIFICATION_PERMISSION | PASS | 2026-08-29 | POST_NOTIFICATIONS 为 granted；运行中前台通知存在且含「停止」动作 |
| 15 | VPN_CONSENT | PASS | 2026-08-29 | VPNService 已绑定；vpn_management 显示 active package 为本应用、sessionId=smart-box、active type=1 |

## 记录规则
- 不写订阅 URL、Token、账号、私密路径
- 未做就留空，不填推测值
- 第 10 项（7 天衰减）如无法在单次会话验证，标注 `DEFERRED` 并写明依据

第 9 项本轮打挂后未见 +500 失败罚分数字，结论留空。
第 12 项未在本轮观察到评论条数变化，结论留空。
