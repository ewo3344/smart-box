#!/usr/bin/python3

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


class RaspberryPiVerifyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.script = (
            Path(__file__).resolve().parents[2] / "scripts" / "verify-raspberry-pi.sh"
        )
        self.assertTrue(self.script.is_file(), f"missing shipped script: {self.script}")

    def test_script_probes_real_state_paths_with_sudo_fallback(self) -> None:
        text = self.script.read_text(encoding="utf-8")
        self.assertIn("/var/lib/smart-box/profile.json", text)
        self.assertIn("/var/lib/smart-box/cache.db", text)
        self.assertIn("sudo -n test -f", text)
        self.assertIn("file_profile.json=present,", text)
        self.assertIn("file_cache.db=present,", text)

    def test_missing_host_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            result = subprocess.run(
                [str(self.script), "--out", temporary_dir],
                check=False,
                capture_output=True,
                text=True,
            )
            report = Path(temporary_dir) / "REPORT.md"
            body = report.read_text(encoding="utf-8") if report.is_file() else ""
        output = result.stdout + result.stderr + body
        self.assertEqual(result.returncode, 2, output)
        self.assertIn("BLOCKED", body)
        self.assertNotIn("Result: **PASS**", body)
