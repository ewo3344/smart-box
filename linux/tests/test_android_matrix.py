#!/usr/bin/python3

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


class AndroidMatrixWaitTest(unittest.TestCase):
    def test_wait_stopped_does_not_treat_adb_loss_as_stop(self) -> None:
        script = (
            Path(__file__).resolve().parents[2] / "scripts" / "android-full-matrix.sh"
        )
        self.assertTrue(script.is_file(), f"missing shipped matrix script: {script}")
        with tempfile.TemporaryDirectory() as temporary_dir:
            result = subprocess.run(
                [
                    str(script),
                    "--self-test-wait-stopped",
                    "--out",
                    temporary_dir,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        output = result.stdout + result.stderr
        self.assertEqual(
            result.returncode,
            0,
            output,
        )
        self.assertIn("SELFTEST PASS: adb loss is not STOP; post-stop count=1 is STOP", output)
        self.assertNotIn("SELFTEST FAIL", output)
