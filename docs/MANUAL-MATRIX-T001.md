# Android T001 人工验收矩阵

**设备**: vivo V2352A (10AE6J03LC001JL) / Android 16
**版本**: smart-box 0.1.0-core.1.14.0-beta.14
**自动化报告**: 本地 verification 目录（不入库）

脚本 `scripts/android-full-matrix.sh` 按设计不推断以下项目，需人工签核。
每项填写：结论(PASS/FAIL) / 执行时间 / 观察到的现象。

| # | 检查项 | 结论 | 时间 | 现象 |
|---|--------|------|------|------|
| 1 | DOMAIN_ALLOWLIST_DIRECT | | | |
| 2 | DOMAIN_BLOCKLIST_SMART | | | |
| 3 | DOMAIN_IDN_WILDCARD | | | |
| 4 | DOMAIN_CONFLICT_DETECTION | | | |
| 5 | REGION_MANUAL_SELECTION | | | |
| 6 | AI_FALLBACK_EXCLUDES_HONG_KONG | | | |
| 7 | TELEGRAM_FALLBACK_INDEPENDENT_PROBE | | | |
| 8 | NODE_SCORE_RESTORE_AFTER_RESTART | | | |
| 9 | NODE_SCORE_FAILURE_PENALTY | | | |
| 10 | NODE_SCORE_SEVEN_DAY_DECAY | | | |
| 11 | WIFI_MOBILE_NETWORK_SWITCH | | | |
| 12 | DOUYIN_COMMENT_POST | | | |
| 13 | TELEGRAM_SEND_RECEIVE | | | |
| 14 | NOTIFICATION_PERMISSION | | | |
| 15 | VPN_CONSENT | | | |

## 记录规则
- 不写订阅 URL、Token、账号、私密路径
- 未做就留空，不填推测值
- 第 10 项（7 天衰减）如无法在单次会话验证，标注 `DEFERRED` 并写明依据
