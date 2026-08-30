#!/usr/bin/python3

from __future__ import annotations

import json
import unittest
from pathlib import Path


class WindowsPackageLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[2]
        self.script = self.root / "scripts" / "build-windows.ps1"
        self.readme = self.root / "windows" / "README.md"
        self.config_dir = self.root / "windows" / "config"
        self.settings_example = self.config_dir / "settings.example.json"

    def test_shipped_packager_copies_readme_and_config(self) -> None:
        self.assertTrue(self.script.is_file(), f"missing {self.script}")
        text = self.script.read_text(encoding="utf-8")
        self.assertIn("Copy-Item -LiteralPath $windowsReadme", text)
        self.assertIn("Copy-Item -LiteralPath $configDir", text)
        self.assertIn("smart-box-$SmartVersion-windows-x64.zip", text)
        self.assertIn("EnableWindowsTargeting", text)

    def test_windows_readme_and_example_settings_have_no_subscription(self) -> None:
        self.assertTrue(self.readme.is_file(), f"missing {self.readme}")
        readme = self.readme.read_text(encoding="utf-8")
        self.assertIn(r"%LOCALAPPDATA%\smart-box", readme)
        self.assertIn("smart-box-core.exe", readme)
        lowered = readme.lower()
        self.assertNotIn("token=", lowered)
        self.assertNotIn("password=", lowered)

        self.assertTrue(self.settings_example.is_file(), f"missing {self.settings_example}")
        settings = json.loads(self.settings_example.read_text(encoding="utf-8"))
        self.assertEqual(settings.get("SubscriptionUrl"), "")


if __name__ == "__main__":
    unittest.main()
