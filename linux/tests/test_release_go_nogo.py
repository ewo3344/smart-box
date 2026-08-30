#!/usr/bin/python3

from __future__ import annotations

import unittest
from pathlib import Path


class ReleaseGoNoGoLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[2]
        self.checklist = (self.root / "RELEASE-CHECKLIST-v0.1.1.md").read_text(
            encoding="utf-8"
        )
        self.changelog = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.matrix = (self.root / "docs" / "MANUAL-MATRIX-T001.md").read_text(
            encoding="utf-8"
        )

    def test_windows_runtime_stays_unchecked(self) -> None:
        self.assertIn(
            "- [ ] 运行时验证（需 Windows 机器：托盘启动、系统代理、core 崩溃重启）",
            self.checklist,
        )
        self.assertNotIn(
            "- [x] 运行时验证（需 Windows 机器：托盘启动、系统代理、core 崩溃重启）",
            self.checklist,
        )

    def test_item_12_stays_deferred(self) -> None:
        self.assertRegex(
            self.matrix,
            r"\|\s*12\s*\|\s*DOUYIN_COMMENT_POST\s*\|\s*DEFERRED\s*\|",
        )

    def test_android_overlay_and_matrix_stay_open_when_blocked(self) -> None:
        self.assertIn(
            "- [ ] `scripts/android-full-matrix.sh --serial 10AE6J03LC001JL`",
            self.checklist,
        )
        self.assertIn(
            "- [ ] Android: APK 安装（覆盖安装保留数据）",
            self.checklist,
        )
        self.assertIn("BLOCKED", self.checklist)
        self.assertIn("不可发布", self.checklist)
        self.assertIn("BLOCKED", self.changelog)
        self.assertIn("vivo", self.changelog)


if __name__ == "__main__":
    unittest.main()
