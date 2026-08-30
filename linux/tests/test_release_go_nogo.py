#!/usr/bin/python3

from __future__ import annotations

import unittest
from pathlib import Path


class ReleaseGoNoGoLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[2]
        self.checklist = (self.root / "docs" / "releases" / "RELEASE-CHECKLIST-v0.1.1.md").read_text(
            encoding="utf-8"
        )
        self.changelog = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.matrix = (self.root / "docs" / "MANUAL-MATRIX-T001.md").read_text(
            encoding="utf-8"
        )
        self.notes = (self.root / "docs" / "RELEASE-NOTES-v0.1.1.md").read_text(
            encoding="utf-8"
        )

    def test_item_9_pass_with_core_coverage_and_ui_gap(self) -> None:
        self.assertRegex(
            self.matrix,
            r"\|\s*9\s*\|\s*NODE_SCORE_FAILURE_PENALTY\s*\|\s*PASS\s*\|",
        )
        self.assertIn("smart_score_test.go", self.matrix)
        self.assertIn("AppliedFailurePenalty", self.matrix)
        self.assertIn("urlTestDelay", self.matrix)
        self.assertIn("13/15 PASS", self.matrix)
        self.assertNotIn("1/15 FAIL", self.matrix)
        self.assertIn("组页不显示节点罚分", self.notes)
        self.assertNotIn("触发条件", self.notes)
        self.assertNotIn("影响范围", self.notes)

    def test_item_12_stays_deferred(self) -> None:
        self.assertRegex(
            self.matrix,
            r"\|\s*12\s*\|\s*DOUYIN_COMMENT_POST\s*\|\s*DEFERRED\s*\|",
        )

    def test_linux_android_gates_closed_windows_out_of_scope(self) -> None:
        self.assertIn(
            "- [x] `scripts/android-full-matrix.sh --serial <DEVICE_SERIAL>`",
            self.checklist,
        )
        self.assertIn(
            "- [x] Android: APK 安装（覆盖安装保留数据）",
            self.checklist,
        )
        self.assertIn(
            "- [x] Linux: tar.gz 解压 + `install.sh`",
            self.checklist,
        )
        self.assertIn("不适用（本次发布不含 Windows）", self.checklist)
        self.assertIn("不在本次发布范围", self.notes)
        self.assertNotIn("阻塞可发布", self.checklist)
        self.assertNotIn(
            "- [ ] 运行时验证（需 Windows 机器：托盘启动、系统代理、core 崩溃重启）",
            self.checklist,
        )
        self.assertIn("可发布（Linux + Android）", self.checklist)
        self.assertNotIn("不可发布", self.checklist)
        self.assertNotIn("2026-09-12", self.checklist)
        self.assertNotIn("2026-09-12", self.notes)
        self.assertNotIn("smart-box-0.1.1-windows-x64.zip", self.notes)


if __name__ == "__main__":
    unittest.main()
