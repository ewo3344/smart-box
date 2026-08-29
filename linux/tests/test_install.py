#!/usr/bin/python3

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class InstallerRuntimeUnmaskTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.release = self.root / "release"
        self.fake_bin = self.root / "fake-bin"
        self.home = self.root / "home"
        self.calls = self.root / "calls.tsv"
        self.file_calls = self.root / "file-calls.tsv"
        self.fake_bin.mkdir()
        self.home.mkdir()
        (self.release / "bin").mkdir(parents=True)
        (self.release / "lib").mkdir()
        (self.release / "systemd").mkdir()

        core = self.release / "bin" / "smart-box-core"
        core.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        core.chmod(0o755)
        (self.release / "lib" / "smart_box_linux.py").write_text(
            "# test fixture\n", encoding="utf-8"
        )
        (self.release / "systemd" / "smart-box-unmask@.service").write_text(
            "# test fixture\n", encoding="utf-8"
        )
        (self.release / "systemd" / "smart-box-cleanup@.service").write_text(
            "# test fixture\n", encoding="utf-8"
        )

        self.fake_python = self.fake_bin / "python3"
        self.fake_profile = self.fake_bin / "smart-box-profile"
        self._write_executable(self.fake_python, "#!/bin/sh\nexit 0\n")
        self._write_executable(
            self.fake_profile,
            "#!/bin/sh\nprintf '%s\\n' 'smart-box test profile'\n",
        )

        source = Path(__file__).resolve().parents[1] / "install.sh"
        script = source.read_text(encoding="utf-8")
        script = script.replace("/usr/bin/python3", str(self.fake_python))
        script = script.replace(
            "/usr/local/bin/smart-box-profile", str(self.fake_profile)
        )
        self.installer = self.release / "install.sh"
        self.installer.write_text(script, encoding="utf-8")
        self.installer.chmod(0o755)
        uninstall_source = source.with_name("uninstall.sh")
        self.uninstaller = self.release / "uninstall.sh"
        self.uninstaller.write_text(
            uninstall_source.read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.uninstaller.chmod(0o755)

        self._write_executable(
            self.fake_bin / "id",
            """#!/bin/sh
case ${1:-} in
    -u)
        shift
        [ "${1:-}" = "--" ] && shift
        if [ "$#" -gt 0 ]; then
            printf '%s\\n' "${SMART_BOX_TEST_ACCOUNT_UID:-1000}"
        else
            printf '%s\\n' "${SMART_BOX_TEST_PROCESS_UID:-0}"
        fi
        ;;
    -un)
        printf '%s\\n' "${SMART_BOX_TEST_USER:-desktop-user}"
        ;;
    *)
        exit 2
        ;;
esac
""",
        )
        for command in ("install", "rm"):
            self._write_executable(
                self.fake_bin / command,
                f"""#!/bin/sh
{{
    printf '{command}'
    for argument do
        printf '\\t%s' "$argument"
    done
    printf '\\n'
}} >> "$SMART_BOX_TEST_FILE_CALLS"
""",
            )
        for command in ("setcap", "update-desktop-database"):
            self._write_executable(self.fake_bin / command, "#!/bin/sh\nexit 0\n")
        self._write_executable(
            self.fake_bin / "pkexec",
            """#!/bin/sh
{
    printf 'pkexec'
    for argument do
        printf '\\t%s' "$argument"
    done
    printf '\\n'
} >> "$SMART_BOX_TEST_CALLS"
""",
        )
        self._write_executable(
            self.fake_bin / "systemctl",
            """#!/bin/sh
{
    printf 'systemctl'
    for argument do
        printf '\\t%s' "$argument"
    done
    printf '\\n'
} >> "$SMART_BOX_TEST_CALLS"
if [ "${SMART_BOX_TEST_FAIL_UNMASK:-0}" = 1 ] &&
   [ "${1:-}" = "--runtime" ] && [ "${2:-}" = "unmask" ]; then
    printf '%s\\n' 'synthetic unmask failure' >&2
    exit 23
fi
""",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def _write_executable(path: Path, contents: str) -> None:
        path.write_text(contents, encoding="utf-8")
        path.chmod(0o755)

    def _run(self, *arguments: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.pop("SUDO_USER", None)
        environment.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.fake_bin}:/usr/bin:/bin",
                "SMART_BOX_TEST_CALLS": str(self.calls),
                "SMART_BOX_TEST_FILE_CALLS": str(self.file_calls),
                "SMART_BOX_TEST_PROCESS_UID": "0",
                "SMART_BOX_TEST_ACCOUNT_UID": "1000",
                **overrides,
            }
        )
        return subprocess.run(
            [str(self.installer), *arguments],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def _run_uninstaller(
        self, *arguments: str, **overrides: str
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.pop("SUDO_USER", None)
        environment.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.fake_bin}:/usr/bin:/bin",
                "SMART_BOX_TEST_CALLS": str(self.calls),
                "SMART_BOX_TEST_FILE_CALLS": str(self.file_calls),
                "SMART_BOX_TEST_PROCESS_UID": "0",
                "SMART_BOX_TEST_ACCOUNT_UID": "1000",
                **overrides,
            }
        )
        return subprocess.run(
            [str(self.uninstaller), *arguments],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def _calls(self) -> list[list[str]]:
        if not self.calls.exists():
            return []
        return [line.split("\t") for line in self.calls.read_text().splitlines()]

    def _file_calls(self) -> list[list[str]]:
        if not self.file_calls.exists():
            return []
        return [line.split("\t") for line in self.file_calls.read_text().splitlines()]

    def test_desktop_stage_passes_current_user_to_privileged_stage(self) -> None:
        result = self._run(
            SMART_BOX_TEST_PROCESS_UID="1000",
            SMART_BOX_TEST_USER="desktop-user",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._calls(),
            [["pkexec", str(self.installer), "--system", "desktop-user"]],
        )

    def test_system_stage_unmasks_only_the_two_exact_user_units(self) -> None:
        result = self._run("--system", "desktop-user")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._calls(),
            [
                ["systemctl", "daemon-reload"],
                [
                    "systemctl",
                    "--runtime",
                    "unmask",
                    "--",
                    "smart-box@desktop-user.service",
                    "smart-box-watchdog@desktop-user.service",
                ],
            ],
        )

    def test_system_stage_installs_unmask_helper_template(self) -> None:
        result = self._run("--system", "desktop-user")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            [
                "install",
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                "0644",
                str(self.release / "systemd" / "smart-box-unmask@.service"),
                "/usr/local/lib/systemd/system/smart-box-unmask@.service",
            ],
            self._file_calls(),
        )

    def test_system_stage_installs_cleanup_helper_template(self) -> None:
        result = self._run("--system", "desktop-user")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            [
                "install",
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                "0644",
                str(self.release / "systemd" / "smart-box-cleanup@.service"),
                "/usr/local/lib/systemd/system/smart-box-cleanup@.service",
            ],
            self._file_calls(),
        )

    def test_system_stage_uses_sudo_user_when_argument_is_absent(self) -> None:
        result = self._run("--system", SUDO_USER="sudo-user")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._calls()[-1],
            [
                "systemctl",
                "--runtime",
                "unmask",
                "--",
                "smart-box@sudo-user.service",
                "smart-box-watchdog@sudo-user.service",
            ],
        )

    def test_unmask_failure_names_both_units_and_fails_install(self) -> None:
        result = self._run(
            "--system",
            "desktop-user",
            SMART_BOX_TEST_FAIL_UNMASK="1",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("synthetic unmask failure", result.stderr)
        self.assertIn(
            "could not clear runtime masks for smart-box@desktop-user.service "
            "and smart-box-watchdog@desktop-user.service",
            result.stderr,
        )

    def test_system_stage_rejects_missing_or_root_desktop_identity(self) -> None:
        missing = self._run("--system")
        root = self._run(
            "--system",
            "root",
            SMART_BOX_TEST_ACCOUNT_UID="0",
        )

        self.assertEqual(missing.returncode, 1)
        self.assertIn("desktop user is required", missing.stderr)
        self.assertEqual(root.returncode, 1)
        self.assertIn("desktop user must not be root", root.stderr)
        self.assertEqual(self._calls(), [])

    def test_system_stage_rejects_release_without_unmask_helper(self) -> None:
        (self.release / "systemd" / "smart-box-unmask@.service").unlink()

        result = self._run("--system", "desktop-user")

        self.assertEqual(result.returncode, 1)
        self.assertIn("missing systemd/smart-box-unmask@.service", result.stderr)
        self.assertEqual(self._calls(), [])
        self.assertEqual(self._file_calls(), [])

    def test_system_uninstall_stops_and_removes_unmask_helper(self) -> None:
        result = self._run_uninstaller("--system", "desktop-user")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            [
                "systemctl",
                "--runtime",
                "unmask",
                "--",
                "smart-box@desktop-user.service",
                "smart-box-watchdog@desktop-user.service",
            ],
            self._calls(),
        )
        self.assertIn(
            [
                "systemctl",
                "stop",
                "--",
                "smart-box@desktop-user.service",
                "smart-box-watchdog@desktop-user.service",
                "smart-box-unmask@desktop-user.service",
                "smart-box-cleanup@desktop-user.service",
            ],
            self._calls(),
        )
        removed_paths = [
            argument
            for call in self._file_calls()
            if call[0] == "rm"
            for argument in call[1:]
        ]
        self.assertIn(
            "/usr/local/lib/systemd/system/smart-box-unmask@.service",
            removed_paths,
        )
        self.assertIn(
            "/usr/local/lib/systemd/system/smart-box-cleanup@.service",
            removed_paths,
        )

    def test_system_uninstall_requires_an_explicit_desktop_identity(self) -> None:
        result = self._run_uninstaller("--system")

        self.assertEqual(result.returncode, 1)
        self.assertIn("desktop user is required", result.stderr)
        self.assertEqual(self._calls(), [])

    def test_build_script_packages_unmask_helper(self) -> None:
        linux_directory = Path(__file__).resolve().parents[1]
        build_script = (linux_directory / "build-package.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('"$source_dir/smart-box-unmask@.service"', build_script)
        self.assertIn(
            '"$staging_dir/systemd/smart-box-unmask@.service"', build_script
        )
        self.assertIn('"$source_dir/smart-box-cleanup@.service"', build_script)
        self.assertIn(
            '"$staging_dir/systemd/smart-box-cleanup@.service"', build_script
        )

    def test_build_script_uses_clean_exact_staging_manifest(self) -> None:
        project = self.root / "package-project"
        linux_directory = project / "linux"
        package = project / "dist" / "smart-box-0.1.0-linux-x86_64"
        (linux_directory / "icons").mkdir(parents=True)
        (package / "bin").mkdir(parents=True)
        (package / "lib" / "__pycache__").mkdir(parents=True)

        source_directory = Path(__file__).resolve().parents[1]
        build_script = linux_directory / "build-package.sh"
        build_script.write_text(
            (source_directory / "build-package.sh").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        build_script.chmod(0o755)

        executable_sources = {
            "smart-box",
            "smart-box-profile",
            "install.sh",
            "uninstall.sh",
        }
        source_files = {
            "smart-box",
            "smart-box-profile",
            "smart_box_backend.py",
            "smart_box_linux.py",
            "smart-box.desktop",
            "smart-box@.service",
            "smart-box-watchdog@.service",
            "smart-box-unmask@.service",
            "smart-box-cleanup@.service",
            "smart-box.rules",
            "install.sh",
            "uninstall.sh",
            "README.md",
        }
        for name in source_files:
            path = linux_directory / name
            path.write_text(f"fixture for {name}\n", encoding="utf-8")
            path.chmod(0o755 if name in executable_sources else 0o644)
        (linux_directory / "icons" / "smart-box.png").write_bytes(b"fixture icon\n")

        core = package / "bin" / "smart-box-core"
        core.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        core.chmod(0o755)
        (package / "lib" / "__pycache__" / "stale.pyc").write_bytes(b"stale")
        (package / "unexpected.txt").write_text("stale\n", encoding="utf-8")

        result = subprocess.run(
            [str(build_script)],
            cwd=project,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        expected_files = {
            "README.md",
            "SHA256SUMS",
            "bin/smart-box",
            "bin/smart-box-core",
            "bin/smart-box-profile",
            "config/smart-box.desktop",
            "config/smart-box.rules",
            "icons/smart-box.png",
            "install.sh",
            "lib/smart_box_backend.py",
            "lib/smart_box_linux.py",
            "systemd/smart-box-cleanup@.service",
            "systemd/smart-box-unmask@.service",
            "systemd/smart-box-watchdog@.service",
            "systemd/smart-box@.service",
            "uninstall.sh",
        }
        actual_files = {
            str(path.relative_to(package))
            for path in package.rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual_files, expected_files)

        checksums = (package / "SHA256SUMS").read_text(encoding="utf-8")
        self.assertEqual(len(checksums.splitlines()), 15)
        self.assertIn("  install.sh", checksums)
        self.assertIn("  uninstall.sh", checksums)
        self.assertIn("  README.md", checksums)
        self.assertNotIn("__pycache__", checksums)
        checksum_result = subprocess.run(
            ["sha256sum", "-c", "SHA256SUMS"],
            cwd=package,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(checksum_result.returncode, 0, checksum_result.stdout)

        tar_result = subprocess.run(
            [
                "tar",
                "-tzf",
                str(project / "dist" / "smart-box-0.1.0-linux-x86_64.tar.gz"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(tar_result.returncode, 0, tar_result.stderr)
        self.assertNotIn("__pycache__", tar_result.stdout)
        self.assertNotIn("unexpected.txt", tar_result.stdout)

    def test_helper_unit_and_polkit_rule_are_narrowly_scoped(self) -> None:
        linux_directory = Path(__file__).resolve().parents[1]
        helper = (linux_directory / "smart-box-unmask@.service").read_text(
            encoding="utf-8"
        )
        cleanup = (linux_directory / "smart-box-cleanup@.service").read_text(
            encoding="utf-8"
        )
        rules = (linux_directory / "smart-box.rules").read_text(encoding="utf-8")

        exec_start = [
            line for line in helper.splitlines() if line.startswith("ExecStart=")
        ]
        self.assertEqual(
            exec_start,
            [
                "ExecStart=/usr/bin/systemctl --runtime unmask -- "
                "smart-box@%i.service smart-box-watchdog@%i.service"
            ],
        )
        self.assertIn("Type=oneshot", helper)
        self.assertIn("User=root", helper)
        self.assertIn("NoNewPrivileges=true", helper)
        self.assertIn("ProtectSystem=strict", helper)
        self.assertNotIn("[Install]", helper)
        self.assertIn("org.freedesktop.systemd1.manage-units", rules)
        self.assertIn('"smart-box-unmask@" + subject.user + ".service"', rules)
        self.assertNotIn("manage-unit-files", rules)
        self.assertIn("ExecStart=/usr/local/bin/smart-box-profile cleanup", cleanup)
        self.assertIn("systemctl --runtime mask", cleanup)
        self.assertIn("CAP_NET_ADMIN", cleanup)
        self.assertIn('"smart-box-cleanup@" + subject.user + ".service"', rules)


if __name__ == "__main__":
    unittest.main()
