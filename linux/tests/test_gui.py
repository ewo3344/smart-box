#!/usr/bin/python3

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

import smart_box_backend as backend
import smart_box_linux as gui


class GuiInteractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication(["smart-box-gui-test"])

    def setUp(self) -> None:
        self.settings = copy.deepcopy(backend.DEFAULT_SETTINGS)
        self.settings_lock = threading.Lock()

        def load_settings() -> dict:
            with self.settings_lock:
                return copy.deepcopy(self.settings)

        def save_settings(value: dict) -> None:
            with self.settings_lock:
                self.settings = copy.deepcopy(value)

        def mutate_settings(mutator: object) -> dict:
            with self.settings_lock:
                candidate = copy.deepcopy(self.settings)
                mutator(candidate)  # type: ignore[operator]
                self.settings = candidate
                return copy.deepcopy(candidate)

        self.patchers = [
            mock.patch.object(backend, "load_settings", side_effect=load_settings),
            mock.patch.object(backend, "save_settings", side_effect=save_settings),
            mock.patch.object(backend, "mutate_settings", side_effect=mutate_settings),
            mock.patch.object(backend, "gui_autostart_enabled", return_value=True),
            mock.patch.object(
                backend, "read_service_log", return_value="first line\nsecond line"
            ),
            mock.patch.object(gui.MainWindow, "poll", autospec=True),
            mock.patch.object(gui.MainWindow, "refresh_policies", autospec=True),
        ]
        for patcher in self.patchers:
            patcher.start()
        gui.apply_application_theme(self.application, "light")
        self.window = gui.MainWindow()
        self.window.poll_timer.stop()
        self.window.resize(920, 660)
        self.window.show()
        self.application.processEvents()

    def tearDown(self) -> None:
        self.window.allow_exit = True
        self.window.close()
        self.window.thread_pool.waitForDone(2000)
        self.application.processEvents()
        gui.apply_application_theme(self.application, "light")
        for patcher in reversed(self.patchers):
            patcher.stop()

    def wait_for_task(self, key: str) -> None:
        for _ in range(200):
            self.application.processEvents()
            if key not in self.window.tasks:
                return
            QTest.qWait(5)
        self.fail(f"GUI task did not finish: {key}")

    def test_log_refresh_and_toggle_have_visible_feedback(self) -> None:
        self.window.select_page(3)
        self.wait_for_task("logs")
        self.assertEqual(self.window.log_view.toPlainText(), "first line\nsecond line")
        self.assertIn("2 行", self.window.log_refresh_status.text())
        self.assertTrue(self.window.log_refresh_button.isEnabled())
        self.assertLess(
            self.window.live_logs.geometry().right(),
            self.window.log_refresh_status.geometry().left(),
        )

        self.window.live_logs.click()
        self.assertFalse(self.window.live_logs.isChecked())
        self.assertFalse(self.settings["log_auto_refresh"])
        self.assertIn("自动刷新暂停", self.window.log_refresh_status.text())

        self.window.live_logs.click()
        self.wait_for_task("logs")
        self.assertTrue(self.window.live_logs.isChecked())
        self.assertTrue(self.settings["log_auto_refresh"])
        self.assertIn("自动刷新开启", self.window.log_refresh_status.text())

        self.window.clear_log_view()
        self.assertEqual(self.window.log_view.toPlainText(), "")
        self.assertIn("视图已清空", self.window.log_refresh_status.text())

    def test_manual_log_refresh_shows_busy_state(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_log_read(lines: int) -> str:
            self.assertEqual(lines, 500)
            started.set()
            release.wait(1)
            return "completed"

        with mock.patch.object(backend, "read_service_log", side_effect=slow_log_read):
            self.window.select_page(3)
            for _ in range(100):
                self.application.processEvents()
                if started.is_set():
                    break
                QTest.qWait(5)
            self.assertTrue(started.is_set())
            self.assertFalse(self.window.log_refresh_button.isEnabled())
            self.assertEqual(self.window.log_refresh_button.text(), "刷新中…")
            self.assertIn("正在读取", self.window.log_refresh_status.text())
            release.set()
            self.wait_for_task("logs")
        self.assertTrue(self.window.log_refresh_button.isEnabled())
        self.assertEqual(self.window.log_refresh_button.text(), "刷新")

    def test_log_filter_survives_refresh_and_reports_matches(self) -> None:
        first = "INFO core started\nERROR DNS timeout\nINFO TUN ready"
        second = "INFO refreshed\nERROR DNS timeout\nERROR TUN closed\nDEBUG done"
        with mock.patch.object(backend, "read_service_log", return_value=first):
            self.window.select_page(3)
            self.wait_for_task("logs")

        self.window.log_search.setText("error")
        self.application.processEvents()
        self.assertEqual(self.window.log_view.toPlainText(), "ERROR DNS timeout")
        self.assertEqual(self.window.log_filter_status.text(), "显示 1 / 共 3 行")
        self.assertTrue(self.window.log_copy_button.isEnabled())
        self.window.log_copy_button.click()
        self.assertEqual(QApplication.clipboard().text(), "ERROR DNS timeout")
        self.assertIn("已复制当前可见日志：1 行", self.window.operation_message)

        with mock.patch.object(backend, "read_service_log", return_value=second):
            self.window.refresh_logs()
            self.wait_for_task("logs")

        self.assertEqual(
            self.window.log_view.toPlainText(),
            "ERROR DNS timeout\nERROR TUN closed",
        )
        self.assertEqual(self.window.log_filter_status.text(), "显示 2 / 共 4 行")
        self.assertEqual(self.window.log_search.text(), "error")

        self.window.log_search.setText("missing")
        self.application.processEvents()
        self.assertEqual(self.window.log_view.toPlainText(), "")
        self.assertEqual(self.window.log_filter_status.text(), "未找到 · 共 4 行")
        self.assertFalse(self.window.log_copy_button.isEnabled())

        self.window.log_search.clear()
        self.application.processEvents()
        self.assertEqual(self.window.log_view.toPlainText(), second)
        self.assertEqual(self.window.log_filter_status.text(), "共 4 行")
        self.assertTrue(self.window.log_copy_button.isEnabled())

    def test_log_refresh_failure_preserves_last_successful_view(self) -> None:
        content = "INFO core started\nERROR DNS timeout\nINFO TUN ready"
        with mock.patch.object(backend, "read_service_log", return_value=content):
            self.window.select_page(3)
            self.wait_for_task("logs")

        self.window.log_search.setText("error")
        self.application.processEvents()
        with mock.patch.object(
            backend,
            "read_service_log",
            side_effect=backend.SmartBoxError("journal temporarily unavailable"),
        ):
            self.window.refresh_logs()
            self.wait_for_task("logs")

        self.assertEqual(self.window.log_content, content)
        self.assertEqual(self.window.log_view.toPlainText(), "ERROR DNS timeout")
        self.assertEqual(self.window.log_filter_status.text(), "显示 1 / 共 3 行")
        self.assertTrue(self.window.log_copy_button.isEnabled())
        self.assertIn("刷新失败 · 已保留 3 行", self.window.log_refresh_status.text())
        self.assertIn("journal temporarily unavailable", self.window.log_refresh_status.toolTip())
        self.assertEqual(
            self.window.log_refresh_status.property("semanticState"),
            "error",
        )

    def test_night_mode_applies_immediately_and_persists(self) -> None:
        self.window.select_page(4)
        self.window.night_mode_check.click()
        self.application.processEvents()
        self.assertTrue(self.window.night_mode_check.isChecked())
        self.assertEqual(self.settings["theme"], "dark")
        self.assertEqual(self.window.theme, "dark")
        self.assertEqual(self.window.traffic_chart.theme, "dark")
        self.assertIn("smart-box-theme: dark", self.application.styleSheet())
        self.window.endpoint_validation_label.setText("测试错误")
        self.window.set_label_state(self.window.endpoint_validation_label, "error")
        self.window.update_theme_accents()
        self.assertIn("#f1bdc3", self.window.endpoint_validation_label.styleSheet())
        self.assertIn("#69a9ff", self.window.download_legend.styleSheet())
        self.assertIn("#62c4a6", self.window.upload_legend.styleSheet())

        self.window.night_mode_check.click()
        self.application.processEvents()
        self.assertFalse(self.window.night_mode_check.isChecked())
        self.assertEqual(self.settings["theme"], "light")
        self.assertNotIn("smart-box-theme: dark", self.application.styleSheet())
        self.assertIn("#a43e46", self.window.endpoint_validation_label.styleSheet())
        self.assertIn("#1769d2", self.window.download_legend.styleSheet())

    def test_telemetry_gap_preserves_traffic_baseline_without_false_spike(self) -> None:
        snapshot = {
            "active": True,
            "api": True,
            "telemetry": True,
            "tun": True,
            "flclash": False,
            "mode": "Rule",
            "connections": 2,
            "upload": 1000,
            "download": 2000,
            "memory": 1024,
        }
        self.window.last_connectivity_check = 1000
        with tempfile.TemporaryDirectory() as temporary_dir:
            missing_profile = Path(temporary_dir) / "profile.json"
            with mock.patch.object(
                gui.time, "monotonic", side_effect=[100.0, 102.0, 104.0]
            ), mock.patch.object(backend, "PROFILE_PATH", missing_profile):
                self.window.status_updated(snapshot, None)
                sample_count = len(self.window.traffic_chart.upload_samples)

                self.window.status_updated(
                    {
                        **snapshot,
                        "telemetry": False,
                        "connections": 0,
                        "upload": 0,
                        "download": 0,
                    },
                    None,
                )
                self.assertEqual(self.window.last_traffic_time, 100.0)
                self.assertEqual(self.window.last_upload_total, 1000)
                self.assertEqual(self.window.last_download_total, 2000)
                self.assertEqual(len(self.window.traffic_chart.upload_samples), sample_count)
                self.assertEqual(self.window.upload_card.value_label.text(), "--/s")
                self.assertIn(
                    "遥测暂不可用",
                    self.window.connection_card.secondary_label.text(),
                )

                self.window.status_updated(
                    {
                        **snapshot,
                        "connections": 3,
                        "upload": 1400,
                        "download": 2600,
                    },
                    None,
                )

        self.assertEqual(self.window.traffic_chart.upload_samples[-1], 100.0)
        self.assertEqual(self.window.traffic_chart.download_samples[-1], 150.0)
        self.assertEqual(len(self.window.traffic_chart.upload_samples), sample_count + 1)
        self.assertEqual(self.window.upload_card.value_label.text(), "100 B/s")
        self.assertEqual(self.window.download_card.value_label.text(), "150 B/s")
        self.assertEqual(self.window.connection_card.value_label.text(), "3")

    def test_mode_buttons_explain_effect_without_changing_core_value(self) -> None:
        expected = {
            "Rule": ("智能分流", "按业务规则选择线路"),
            "Global": ("全局代理", "所有流量使用代理"),
            "Direct": ("全部直连", "临时绕过代理"),
            "节能": ("节能模式", "减少测速与切换"),
        }
        for mode, (title, summary) in expected.items():
            button = self.window.mode_buttons[mode]
            self.assertEqual(button.property("modeValue"), mode)
            self.assertIn(title, button.text())
            self.assertIn(summary, button.text())
            self.assertTrue(button.toolTip())
            self.assertIn(title, button.accessibleName())

    def test_subscription_fields_and_domain_editors_are_accessible(self) -> None:
        subscription_fields = {
            "协议": self.window.protocol_box,
            "域名或 IP": self.window.host_edit,
            "端口": self.window.port_spin,
            "私密订阅路径": self.window.path_edit,
        }
        labels = self.window.findChildren(gui.QLabel)
        for name, field in subscription_fields.items():
            self.assertEqual(field.accessibleName(), name)
            matching_labels = [label for label in labels if label.text() == name]
            self.assertTrue(matching_labels, name)
            self.assertTrue(
                any(label.buddy() is field for label in matching_labels),
                f"{name} label is not associated with its field",
            )

        self.assertTrue(self.window.allow_editor.tabChangesFocus())
        self.assertTrue(self.window.proxy_editor.tabChangesFocus())
        self.assertIn("直连", self.window.allow_editor.accessibleName())
        self.assertIn("Smart", self.window.proxy_editor.accessibleName())

    def test_domain_editor_only_applies_valid_unsaved_changes(self) -> None:
        self.window.select_page(2)
        self.assertFalse(self.window.domain_save_button.isEnabled())
        self.assertFalse(self.window.domain_reset_button.isEnabled())
        self.assertIn("无待应用更改", self.window.domain_validation_label.text())

        self.window.allow_editor.setPlainText("example.cn")
        self.application.processEvents()
        self.assertEqual(self.window.allow_count.text(), "1 条生效规则")
        self.assertIn("自动合并", self.window.allow_count.toolTip())
        self.assertTrue(self.window.domain_save_button.isEnabled())
        self.assertTrue(self.window.domain_reset_button.isEnabled())
        self.assertIn("未保存更改", self.window.domain_validation_label.text())

        self.window.proxy_editor.setPlainText("bad_domain")
        self.application.processEvents()
        self.assertFalse(self.window.domain_save_button.isEnabled())
        self.assertTrue(self.window.domain_reset_button.isEnabled())
        self.assertIn("格式无效", self.window.domain_validation_label.text())
        self.assertIn("bad_domain", self.window.domain_validation_label.toolTip())

        self.window.proxy_editor.setPlainText("sub.example.cn")
        self.application.processEvents()
        self.assertFalse(self.window.domain_save_button.isEnabled())
        self.assertIn("冲突", self.window.domain_validation_label.text())

        self.window.domain_reset_button.click()
        self.application.processEvents()
        self.assertFalse(self.window.domain_save_button.isEnabled())
        self.assertFalse(self.window.domain_reset_button.isEnabled())
        self.assertEqual(self.window.allow_editor.toPlainText(), "")
        self.assertEqual(self.window.proxy_editor.toPlainText(), "")

    def test_domain_save_falls_back_to_direct_when_old_core_restart_fails(self) -> None:
        self.window.allow_editor.setPlainText("example.cn")
        previous_settings = copy.deepcopy(self.settings)
        new_core_restart = mock.Mock(returncode=0, stdout="")
        old_core_restart = mock.Mock(returncode=1, stdout="旧核心重启失败")

        with mock.patch.object(
            backend, "PROFILE_PATH", Path("/etc/hosts")
        ), mock.patch.object(
            backend, "unit_active", return_value=True
        ), mock.patch.object(
            backend, "prepare_runtime"
        ) as prepare_runtime, mock.patch.object(
            self.window, "ensure_flclash_stopped"
        ) as stop_flclash, mock.patch.object(
            backend,
            "systemctl_service",
            side_effect=[new_core_restart, old_core_restart],
        ) as restart, mock.patch.object(
            self.window,
            "verify_service_ready_then_probe",
            side_effect=backend.SmartBoxError("公网探测失败"),
        ) as probe, mock.patch.object(
            self.window, "verify_service_ready"
        ) as restored_ready, mock.patch.object(
            backend,
            "recover_failed_switch",
            return_value="smart-box 已停止",
        ) as recover, mock.patch.object(
            self.window, "run_task"
        ) as run_task:
            self.window.save_domain_rules()
            apply_rules = run_task.call_args.args[1]
            done = run_task.call_args.args[2]

            ordered = mock.Mock()
            ordered.attach_mock(restart, "restart")
            ordered.attach_mock(probe, "probe")
            ordered.attach_mock(recover, "recover")

            with self.assertRaises(backend.SmartBoxError) as raised:
                apply_rules()

            expected = (
                "公网探测失败；恢复旧域名名单或核心失败："
                "恢复旧核心失败：旧核心重启失败；"
                "smart-box 已停止，系统已回到直连"
            )
            self.assertEqual(str(raised.exception), expected)
            self.assertEqual(
                ordered.mock_calls,
                [
                    mock.call.restart(
                        "restart",
                        backend.SERVICE_UNIT,
                        timeout=gui.SERVICE_START_TIMEOUT,
                    ),
                    mock.call.probe(),
                    mock.call.restart(
                        "restart",
                        backend.SERVICE_UNIT,
                        timeout=gui.SERVICE_START_TIMEOUT,
                    ),
                    mock.call.recover(False),
                ],
            )
            self.assertEqual(prepare_runtime.call_count, 2)
            stop_flclash.assert_called_once_with()
            restored_ready.assert_not_called()
            self.assertEqual(self.settings, previous_settings)

            done(None, raised.exception)

        self.assertEqual(self.window.operation_state, "error")
        self.assertIn(expected, self.window.operation_message)

    def test_background_start_retries_tray_registration(self) -> None:
        self.window.background_requested = True
        self.window.tray_icon = None
        tray = mock.Mock()

        def delayed_tray(icon: object) -> bool:
            self.assertIs(icon, self.window.app_icon)
            if self.window.tray_retry_attempts < 2:
                return False
            self.window.tray_icon = tray
            return True

        with mock.patch.object(self.window, "build_tray", side_effect=delayed_tray):
            self.window.start_tray_retry()
            self.assertTrue(self.window.tray_retry_timer.isActive())
            self.window.retry_tray()
            self.assertTrue(self.window.tray_retry_timer.isActive())
            self.window.retry_tray()

        self.assertIs(self.window.tray_icon, tray)
        self.assertFalse(self.window.tray_retry_timer.isActive())

    def test_startup_policy_preload_is_silent(self) -> None:
        with mock.patch.object(self.window, "refresh_policies") as refresh:
            QTest.qWait(350)
            self.application.processEvents()
        refresh.assert_called_once_with(silent=True)
        self.assertEqual(self.window.operation_state, "idle")
        self.assertEqual(self.window.operation_message, "等待操作")

    def test_flclash_stop_is_delegated_to_backend_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            switch_state = Path(temporary_dir) / "switch-state.json"
            switch_state.write_text("{}\n", encoding="utf-8")
            with mock.patch.object(
                backend, "SWITCH_STATE_PATH", switch_state
            ), mock.patch.object(backend, "stop_flclash") as stop_flclash:
                self.window.ensure_flclash_stopped()
            stop_flclash.assert_called_once_with(timeout=30)
            self.assertFalse(switch_state.exists())

    def test_start_keeps_ready_core_when_connectivity_is_temporarily_degraded(self) -> None:
        started = mock.Mock(returncode=0, stdout="")
        degraded = {"stable": False, "passed": 4, "total": 5, "latency_ms": 6000}
        with mock.patch.object(backend, "PROFILE_PATH", Path("/etc/hosts")), mock.patch.object(
            self.window, "run_task"
        ) as run_task:
            self.window.start_service()

        sequence = run_task.call_args.args[1]
        with mock.patch.object(
            backend, "flclash_conflict", return_value=False
        ), mock.patch.object(self.window, "ensure_flclash_stopped"), mock.patch.object(
            backend, "systemctl_service", return_value=started
        ) as systemctl_service, mock.patch.object(
            self.window, "verify_service_ready_then_probe", return_value=degraded
        ), mock.patch.object(self.window, "failed_switch_error") as recovery:
            result = sequence()

        self.assertTrue(result["degraded"])
        self.assertIn("已接管网络", result["message"])
        self.assertEqual(systemctl_service.call_args_list[0].args[:2], ("start", backend.SERVICE_UNIT))
        self.assertEqual(len(systemctl_service.call_args_list), 1)
        recovery.assert_not_called()

    def test_start_runs_runtime_mask_preflight_before_systemd_start(self) -> None:
        started = mock.Mock(returncode=0, stdout="")
        probe = {"stable": True, "passed": 5, "total": 5, "latency_ms": 120}
        with mock.patch.object(backend, "PROFILE_PATH", Path("/etc/hosts")), mock.patch.object(
            self.window, "run_task"
        ) as run_task:
            self.window.start_service()

        sequence = run_task.call_args.args[1]
        with mock.patch.object(
            backend, "flclash_conflict", return_value=False
        ), mock.patch.object(self.window, "ensure_flclash_stopped"), mock.patch.object(
            backend, "ensure_runtime_service_units_unmasked"
        ) as ensure_unmasked, mock.patch.object(
            backend, "run_command", return_value=started
        ) as run_command, mock.patch.object(
            self.window, "verify_service_ready_then_probe", return_value=probe
        ), mock.patch.object(
            self.window, "require_usable_connectivity"
        ), mock.patch.object(self.window, "failed_switch_error") as recovery:
            result = sequence()

        self.assertNotIn("degraded", result)
        ensure_unmasked.assert_called_once_with(timeout=15.0)
        run_command.assert_called_once_with(
            ["systemctl", "start", backend.SERVICE_UNIT],
            timeout=gui.SERVICE_START_TIMEOUT,
        )
        recovery.assert_not_called()

    def test_start_still_recovers_when_core_never_becomes_ready(self) -> None:
        started = mock.Mock(returncode=0, stdout="")
        with mock.patch.object(backend, "PROFILE_PATH", Path("/etc/hosts")), mock.patch.object(
            self.window, "run_task"
        ) as run_task:
            self.window.start_service()

        sequence = run_task.call_args.args[1]
        with mock.patch.object(
            backend, "flclash_conflict", return_value=False
        ), mock.patch.object(self.window, "ensure_flclash_stopped"), mock.patch.object(
            backend, "systemctl_service", return_value=started
        ), mock.patch.object(
            self.window,
            "verify_service_ready_then_probe",
            side_effect=backend.SmartBoxError("TUN unavailable"),
        ), mock.patch.object(self.window, "failed_switch_error") as recovery:
            recovery.return_value = backend.SmartBoxError("recovered")
            with self.assertRaisesRegex(backend.SmartBoxError, "recovered"):
                sequence()

        recovery.assert_called_once()

    def test_start_recovers_when_stable_probe_is_not_usable(self) -> None:
        """A stable-looking probe must still pass the route usability gate."""
        started = mock.Mock(returncode=0, stdout="")
        probe = {"stable": True, "passed": 5, "total": 5, "latency_ms": 120}
        with mock.patch.object(backend, "PROFILE_PATH", Path("/etc/hosts")), mock.patch.object(
            self.window, "run_task"
        ) as run_task:
            self.window.start_service()

        sequence = run_task.call_args.args[1]
        with mock.patch.object(
            backend, "flclash_conflict", return_value=False
        ), mock.patch.object(self.window, "ensure_flclash_stopped"), mock.patch.object(
            backend, "systemctl_service", return_value=started
        ), mock.patch.object(
            self.window, "verify_service_ready_then_probe", return_value=probe
        ), mock.patch.object(
            backend, "connectivity_is_usable_for_mode", return_value=False
        ), mock.patch.object(self.window, "failed_switch_error") as recovery:
            recovery.return_value = backend.SmartBoxError("recovered")
            with self.assertRaisesRegex(backend.SmartBoxError, "recovered"):
                sequence()

        recovery.assert_called_once()
        self.assertFalse(recovery.call_args.args[1])

    def test_background_guard_keeps_tun_for_partial_but_usable_connectivity(self) -> None:
        result = {
            "online": False,
            "passed": 3,
            "total": 5,
            "checks": [
                {"key": "domestic", "online": True},
                {"key": "basic", "online": True},
                {"key": "proxy", "online": True},
                {"key": "github", "online": False},
                {"key": "telegram", "online": False},
            ],
            "latency_ms": 40,
            "error": "GitHub（timeout）；Telegram（timeout）",
            "guard_confirmed": False,
        }
        scheduled: list[tuple[str, object, object]] = []

        def run_task(key: str, function: object, callback: object, _activity: str = "") -> bool:
            scheduled.append((key, function, callback))
            return True

        self.window.run_task = run_task  # type: ignore[method-assign]
        with mock.patch.object(self.window, "disable_unusable_tun") as disable:
            self.window.check_connectivity_background()
            self.assertEqual(scheduled[0][0], "connectivity-status")
            callback = scheduled[0][2]
            callback(result, None)  # type: ignore[operator]

        disable.assert_not_called()
        self.assertEqual(self.window.detail_values["联网验收"].text(), "3/5 路 · 最慢 40 ms")
        self.assertEqual(self.window.operation_state, "working")

    def test_manual_connectivity_check_has_busy_and_success_feedback(self) -> None:
        result = {
            "online": True,
            "passed": 5,
            "total": 5,
            "latency_ms": 86,
            "checks": [
                {"key": "domestic", "online": True},
                {"key": "basic", "online": True},
                {"key": "proxy", "online": True},
                {"key": "github", "online": True},
                {"key": "telegram", "online": True},
            ],
        }
        scheduled: list[tuple[str, object, object]] = []

        def run_task(key: str, function: object, callback: object, _activity: str = "") -> bool:
            scheduled.append((key, function, callback))
            return True

        self.window.run_task = run_task  # type: ignore[method-assign]
        self.window.connectivity_button.click()

        self.assertFalse(self.window.connectivity_button.isEnabled())
        self.assertEqual(self.window.connectivity_button.text(), "验网中…")
        self.assertEqual(self.window.operation_state, "working")
        self.assertEqual(scheduled[0][0], "connectivity-status")

        callback = scheduled[0][2]
        callback(result, None)  # type: ignore[operator]

        self.assertTrue(self.window.connectivity_button.isEnabled())
        self.assertEqual(self.window.connectivity_button.text(), "立即验网")
        self.assertEqual(self.window.detail_values["联网验收"].text(), "5/5 路 · 最慢 86 ms")
        self.assertEqual(self.window.operation_state, "success")
        self.assertIn("网络已验证", self.window.operation_message)

    def test_manual_connectivity_uses_direct_probe_when_core_is_stopped(self) -> None:
        scheduled: list[tuple[str, object, object]] = []

        def run_task(key: str, function: object, callback: object, _activity: str = "") -> bool:
            scheduled.append((key, function, callback))
            return True

        self.window.run_task = run_task  # type: ignore[method-assign]
        direct = {
            "online": True,
            "latency_ms": 42,
            "http_status": 204,
            "url": "https://direct.example/",
            "error": "",
        }
        with mock.patch.object(backend, "unit_active", return_value=False), mock.patch.object(
            backend, "probe_direct_connectivity", return_value=direct
        ) as direct_probe, mock.patch.object(
            backend, "probe_connectivity_guard"
        ) as matrix_probe:
            self.window.connectivity_button.click()
            probe = scheduled[0][1]
            result = probe()  # type: ignore[operator]
            callback = scheduled[0][2]
            callback(result, None)  # type: ignore[operator]

        direct_probe.assert_called_once_with()
        matrix_probe.assert_not_called()
        self.assertEqual(self.window.detail_values["联网验收"].text(), "直连可用 · 42 ms")
        self.assertEqual(self.window.operation_state, "success")
        self.assertIn("直连网络已验证", self.window.operation_message)

    def test_background_guard_disables_tun_after_confirmed_outage(self) -> None:
        result = {
            "online": False,
            "passed": 2,
            "total": 5,
            "checks": [
                {"key": "domestic", "online": True},
                {"key": "basic", "online": True},
                {"key": "proxy", "online": False},
                {"key": "github", "online": False},
                {"key": "telegram", "online": False},
            ],
            "latency_ms": 120,
            "error": "代理链路（timeout）",
            "guard_confirmed": True,
        }
        scheduled: list[tuple[str, object, object]] = []

        def run_task(key: str, function: object, callback: object, _activity: str = "") -> bool:
            scheduled.append((key, function, callback))
            return True

        self.window.run_task = run_task  # type: ignore[method-assign]
        with mock.patch.object(self.window, "disable_unusable_tun") as disable:
            self.window.check_connectivity_background()
            callback = scheduled[0][2]
            callback(result, None)  # type: ignore[operator]

        disable.assert_called_once_with(result, 0)
        self.assertIn("保护触发", self.window.detail_values["联网验收"].text())

    def test_stale_probe_after_core_restart_cannot_disable_or_overwrite_status(self) -> None:
        outage = {
            "online": False,
            "passed": 1,
            "total": 5,
            "latency_ms": 120,
            "error": "旧核心代理超时",
            "guard_confirmed": True,
        }
        scheduled: list[tuple[str, object, object]] = []

        def run_task(key: str, function: object, callback: object, _activity: str = "") -> bool:
            scheduled.append((key, function, callback))
            return True

        self.window.run_task = run_task  # type: ignore[method-assign]
        self.window.check_connectivity_background()
        stale_callback = scheduled[0][2]

        def restarted_done(_result: object, _error: object) -> None:
            self.window.detail_values["联网验收"].setText("新核心已验证")
            self.window.set_label_state(self.window.detail_values["联网验收"], "success")
            self.window.set_operation_state("新核心已验证", "success")
            self.window.last_connectivity_check = 321.0

        self.assertTrue(
            self.window.run_core_task(
                "service-switch", lambda: None, restarted_done, "正在重启…"
            )
        )
        restarted_callback = scheduled[1][2]
        restarted_callback({}, None)  # type: ignore[operator]
        self.assertIsNone(self.window.core_transaction)

        with mock.patch.object(self.window, "disable_unusable_tun") as disable:
            stale_callback(outage, None)  # type: ignore[operator]

        disable.assert_not_called()
        self.assertEqual(self.window.detail_values["联网验收"].text(), "新核心已验证")
        self.assertEqual(
            self.window.detail_values["联网验收"].property("semanticState"),
            "success",
        )
        self.assertEqual(self.window.operation_message, "新核心已验证")
        self.assertEqual(self.window.last_connectivity_check, 321.0)

    def test_core_transactions_are_exclusive_and_block_new_probes(self) -> None:
        scheduled: list[tuple[str, object, object]] = []
        callback_saw_transaction: list[bool] = []

        def run_task(key: str, function: object, callback: object, _activity: str = "") -> bool:
            scheduled.append((key, function, callback))
            return True

        self.window.run_task = run_task  # type: ignore[method-assign]
        self.assertTrue(
            self.window.run_core_task(
                "profile-pull",
                lambda: None,
                lambda _result, _error: callback_saw_transaction.append(
                    self.window.core_transaction is not None
                ),
            )
        )
        generation = self.window.core_generation
        self.assertFalse(
            self.window.run_core_task(
                "domain-rules", lambda: None, lambda _result, _error: None
            )
        )

        self.window.check_connectivity_background(manual=True)
        self.assertEqual(len(scheduled), 1)
        self.assertTrue(self.window.connectivity_button.isEnabled())
        self.assertIn("核心操作进行中", self.window.operation_message)

        callback = scheduled[0][2]
        callback(None, backend.SmartBoxError("测试失败"))  # type: ignore[operator]
        self.assertEqual(callback_saw_transaction, [True])
        self.assertIsNone(self.window.core_transaction)
        self.assertEqual(self.window.core_generation, generation)

        with mock.patch.object(self.window, "run_task", return_value=False):
            self.assertFalse(
                self.window.run_core_task(
                    "service-switch", lambda: None, lambda _result, _error: None
                )
            )
        self.assertIsNone(self.window.core_transaction)
        self.assertGreater(self.window.core_generation, generation)

    def test_core_transaction_keeps_all_mutating_controls_disabled_across_refreshes(self) -> None:
        scheduled: list[tuple[str, object, object]] = []

        def run_task(
            key: str, function: object, callback: object, _activity: str = ""
        ) -> bool:
            scheduled.append((key, function, callback))
            return True

        self.window.host_edit.setText("converter.example.com")
        self.window.path_edit.setText("/private/token")
        self.window.allow_editor.setPlainText("direct.example")
        self.window.core_active_hint = True
        self.window.policies_updated(
            ([{"name": "策略组", "all": ["节点 A"], "now": "节点 A"}], True),
            None,
        )
        self.window.refresh_core_action_availability()
        self.assertTrue(self.window.domain_save_button.isEnabled())
        self.assertTrue(self.window.pull_button.isEnabled())
        self.assertTrue(self.window.restart_button.isEnabled())

        self.window.run_task = run_task  # type: ignore[method-assign]
        self.assertTrue(
            self.window.run_core_task(
                "profile-pull", lambda: None, lambda _result, _error: None
            )
        )

        combo = self.window.policy_rows[0][2]
        probe_button = self.window.policy_rows[0][4]
        disabled = [
            self.window.power_button,
            self.window.restart_button,
            *self.window.mode_buttons.values(),
            combo,
            probe_button,
            self.window.domain_save_button,
            self.window.pull_button,
            self.window.pull_quick_button,
            self.window.stack_box,
        ]
        self.assertTrue(all(not control.isEnabled() for control in disabled))
        self.assertTrue(self.window.connectivity_button.isEnabled())
        self.assertTrue(self.window.night_mode_check.isEnabled())

        with mock.patch.object(backend, "PROFILE_PATH", Path("/missing/profile")):
            self.window.status_updated(
                {
                    "active": True,
                    "api": True,
                    "telemetry": True,
                    "tun": True,
                    "flclash": False,
                    "mode": "Rule",
                },
                None,
            )
        self.window.policies_updated(
            ([{"name": "策略组", "all": ["节点 A"], "now": "节点 A"}], True),
            None,
        )
        self.assertFalse(self.window.restart_button.isEnabled())
        self.assertFalse(self.window.policy_rows[0][2].isEnabled())
        self.assertFalse(self.window.policy_rows[0][4].isEnabled())

        callback = scheduled[0][2]
        callback(None, None)  # type: ignore[operator]
        self.assertIsNone(self.window.core_transaction)
        self.assertTrue(self.window.power_button.isEnabled())
        self.assertTrue(self.window.restart_button.isEnabled())
        self.assertTrue(self.window.domain_save_button.isEnabled())
        self.assertTrue(self.window.pull_button.isEnabled())
        self.assertTrue(self.window.pull_quick_button.isEnabled())
        self.assertTrue(self.window.stack_box.isEnabled())
        self.assertTrue(self.window.policy_rows[0][2].isEnabled())
        self.assertTrue(self.window.policy_rows[0][4].isEnabled())

    def test_core_transaction_callback_exception_still_restores_controls(self) -> None:
        scheduled: list[object] = []

        def run_task(
            _key: str, _function: object, callback: object, _activity: str = ""
        ) -> bool:
            scheduled.append(callback)
            return True

        def fail_callback(_result: object, _error: object) -> None:
            raise RuntimeError("injected callback failure")

        self.window.run_task = run_task  # type: ignore[method-assign]
        self.assertTrue(
            self.window.run_core_task(
                "profile-pull", lambda: None, fail_callback
            )
        )
        self.assertFalse(self.window.stack_box.isEnabled())
        with self.assertRaisesRegex(RuntimeError, "injected callback failure"):
            scheduled[0](None, None)  # type: ignore[operator]
        self.assertIsNone(self.window.core_transaction)
        self.assertTrue(self.window.stack_box.isEnabled())

    def test_profile_and_domain_tasks_freeze_editors_and_recover_if_unscheduled(self) -> None:
        self.window.host_edit.setText("converter.example.com")
        self.window.path_edit.setText("/private/token")
        self.window.allow_editor.setPlainText("direct.example")

        with tempfile.TemporaryDirectory() as temporary_dir, mock.patch.object(
            backend, "PROFILE_PATH", Path(temporary_dir) / "profile.json"
        ), mock.patch.object(
            backend, "RUNTIME_PATH", Path(temporary_dir) / "runtime.json"
        ), mock.patch.object(
            backend, "unit_active", return_value=False
        ), mock.patch.object(
            self.window, "run_task", return_value=False
        ):
            self.window.pull_profile()
            self.assertTrue(self.window.protocol_box.isEnabled())
            self.assertTrue(self.window.host_edit.isEnabled())
            self.assertTrue(self.window.port_spin.isEnabled())
            self.assertTrue(self.window.path_edit.isEnabled())
            self.assertIsNone(self.window.core_transaction)

            self.window.save_domain_rules()
            self.assertTrue(self.window.allow_editor.isEnabled())
            self.assertTrue(self.window.proxy_editor.isEnabled())
            self.assertIsNone(self.window.core_transaction)

    def test_confirmed_outage_recovery_stops_tun_and_reports_direct_fallback(self) -> None:
        probe = {"passed": 2, "total": 5, "latency_ms": 120}
        scheduled: list[tuple[str, object, object]] = []

        def run_task(key: str, function: object, callback: object, _activity: str = "") -> bool:
            scheduled.append((key, function, callback))
            return True

        self.window.run_task = run_task  # type: ignore[method-assign]
        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            backend, "recover_failed_switch", return_value="smart-box 已停止"
        ) as recovery:
            self.window.disable_unusable_tun(probe)
            self.assertEqual(scheduled[0][0], "service-switch")
            sequence = scheduled[0][1]
            result = sequence()  # type: ignore[operator]
            recovery.assert_called_once_with(False)
            self.assertIn("系统直连和 DNS 已恢复", result["message"])
            callback = scheduled[0][2]
            callback(result, None)  # type: ignore[operator]

        self.assertIn("已自动关闭", self.window.detail_values["联网验收"].text())
        self.assertEqual(self.window.operation_state, "error")

    def test_restart_failure_only_restores_an_existing_flclash(self) -> None:
        failed = mock.Mock(returncode=1, stdout="restart failed")
        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            self.window, "run_task"
        ) as run_task:
            self.window.restart_service()

        sequence = run_task.call_args.args[1]
        with mock.patch.object(
            backend, "flclash_conflict", return_value=False
        ), mock.patch.object(self.window, "ensure_flclash_stopped"), mock.patch.object(
            backend, "systemctl_service", return_value=failed
        ), mock.patch.object(self.window, "failed_switch_error") as recovery:
            recovery.return_value = backend.SmartBoxError("recovered")
            with self.assertRaisesRegex(backend.SmartBoxError, "recovered"):
                sequence()

        recovery.assert_called_once()
        self.assertFalse(recovery.call_args.args[1])

    def test_restart_failure_restores_flclash_when_it_was_running(self) -> None:
        failed = mock.Mock(returncode=1, stdout="restart failed")
        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            self.window, "run_task"
        ) as run_task:
            self.window.restart_service()

        sequence = run_task.call_args.args[1]
        with mock.patch.object(
            backend, "flclash_conflict", return_value=True
        ), mock.patch.object(self.window, "ensure_flclash_stopped"), mock.patch.object(
            backend, "systemctl_service", return_value=failed
        ), mock.patch.object(self.window, "failed_switch_error") as recovery:
            recovery.return_value = backend.SmartBoxError("recovered")
            with self.assertRaisesRegex(backend.SmartBoxError, "recovered"):
                sequence()

        recovery.assert_called_once()
        self.assertTrue(recovery.call_args.args[1])

    def test_restart_keeps_ready_core_when_connectivity_is_temporarily_degraded(self) -> None:
        restarted = mock.Mock(returncode=0, stdout="")
        degraded = {"stable": False, "passed": 4, "total": 5, "latency_ms": 6000}
        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            self.window, "run_task"
        ) as run_task:
            self.window.restart_service()

        sequence = run_task.call_args.args[1]
        with mock.patch.object(
            backend, "flclash_conflict", return_value=False
        ), mock.patch.object(self.window, "ensure_flclash_stopped"), mock.patch.object(
            backend, "systemctl_service", return_value=restarted
        ) as systemctl_service, mock.patch.object(
            self.window, "verify_service_ready_then_probe", return_value=degraded
        ), mock.patch.object(self.window, "failed_switch_error") as recovery:
            result = sequence()

        self.assertTrue(result["degraded"])
        self.assertIn("保持接管", result["message"])
        self.assertEqual(systemctl_service.call_args_list[0].args[:2], ("restart", backend.SERVICE_UNIT))
        self.assertEqual(len(systemctl_service.call_args_list), 1)
        recovery.assert_not_called()

    def test_restart_still_recovers_when_core_never_becomes_ready(self) -> None:
        restarted = mock.Mock(returncode=0, stdout="")
        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            self.window, "run_task"
        ) as run_task:
            self.window.restart_service()

        sequence = run_task.call_args.args[1]
        with mock.patch.object(
            backend, "flclash_conflict", return_value=False
        ), mock.patch.object(self.window, "ensure_flclash_stopped"), mock.patch.object(
            backend, "systemctl_service", return_value=restarted
        ), mock.patch.object(
            self.window,
            "verify_service_ready_then_probe",
            side_effect=backend.SmartBoxError("TUN unavailable"),
        ), mock.patch.object(self.window, "failed_switch_error") as recovery:
            recovery.return_value = backend.SmartBoxError("recovered")
            with self.assertRaisesRegex(backend.SmartBoxError, "recovered"):
                sequence()

        recovery.assert_called_once()

    def test_restart_recovers_when_stable_probe_is_not_usable(self) -> None:
        restarted = mock.Mock(returncode=0, stdout="")
        probe = {"stable": True, "passed": 5, "total": 5, "latency_ms": 120}
        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            self.window, "run_task"
        ) as run_task:
            self.window.restart_service()

        sequence = run_task.call_args.args[1]
        with mock.patch.object(
            backend, "flclash_conflict", return_value=False
        ), mock.patch.object(self.window, "ensure_flclash_stopped"), mock.patch.object(
            backend, "systemctl_service", return_value=restarted
        ), mock.patch.object(
            self.window, "verify_service_ready_then_probe", return_value=probe
        ), mock.patch.object(
            backend, "connectivity_is_usable_for_mode", return_value=False
        ), mock.patch.object(self.window, "failed_switch_error") as recovery:
            recovery.return_value = backend.SmartBoxError("recovered")
            with self.assertRaisesRegex(backend.SmartBoxError, "recovered"):
                sequence()

        recovery.assert_called_once()
        self.assertFalse(recovery.call_args.args[1])

    def test_pull_profile_restores_files_and_active_service_after_probe_failure(self) -> None:
        old_profile = b'{"profile":"old"}\n'
        old_runtime = b'{"runtime":"old"}\n'
        old_settings = copy.deepcopy(backend.DEFAULT_SETTINGS)
        old_settings.update(
            {
                "subscription_url": "https://old.example/sub",
                "last_pull_utc": "2026-08-19T12:00:00+00:00",
            }
        )
        new_settings = copy.deepcopy(old_settings)
        new_settings.update(
            {
                "subscription_url": "https://new.example/sub",
                "last_pull_utc": "2026-08-20T12:00:00+00:00",
            }
        )
        probe = {"stable": True, "passed": 5, "total": 5, "latency_ms": 120}

        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            profile_path = directory / "profile.json"
            runtime_path = directory / "runtime.json"
            settings_path = directory / "settings.json"
            profile_path.write_bytes(old_profile)
            runtime_path.write_bytes(old_runtime)
            settings_path.write_text(
                json.dumps(old_settings, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            def load_settings() -> dict:
                return json.loads(settings_path.read_text(encoding="utf-8"))

            def save_settings(value: dict) -> None:
                settings_path.write_text(
                    json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

            def mutate_settings(mutator: object) -> dict:
                value = load_settings()
                mutator(value)  # type: ignore[operator]
                save_settings(value)
                return copy.deepcopy(value)

            def fetch_profile(_url: str) -> dict:
                profile_path.write_bytes(b'{"profile":"new"}\n')
                runtime_path.write_bytes(b'{"runtime":"new"}\n')
                save_settings(new_settings)
                return {"nodes": 2, "selectors": 1}

            restarted = mock.Mock(returncode=0, stdout="")
            with mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path), mock.patch.object(
                backend, "load_settings", side_effect=load_settings
            ), mock.patch.object(backend, "save_settings", side_effect=save_settings), mock.patch.object(
                backend, "mutate_settings", side_effect=mutate_settings
            ), mock.patch.object(
                backend, "unit_active", return_value=True
            ), mock.patch.object(
                self.window, "build_subscription_url", return_value="https://new.example/sub"
            ), mock.patch.object(
                self.window, "run_task"
            ) as run_task:
                self.window.pull_profile()
                sequence = run_task.call_args.args[1]
                with mock.patch.object(
                    backend, "fetch_profile", side_effect=fetch_profile
                ), mock.patch.object(
                    backend, "flclash_conflict", return_value=False
                ), mock.patch.object(self.window, "ensure_flclash_stopped"), mock.patch.object(
                    backend, "systemctl_service", return_value=restarted
                ) as systemctl_service, mock.patch.object(
                    self.window, "verify_service_ready_then_probe", return_value=probe
                ), mock.patch.object(
                    backend, "connectivity_is_usable_for_mode", return_value=False
                ), mock.patch.object(
                    backend, "wait_for", return_value=True
                ), mock.patch.object(self.window, "verify_service_ready"):
                    with self.assertRaisesRegex(backend.SmartBoxError, "关键网络路径不可用"):
                        sequence()

                self.assertEqual(profile_path.read_bytes(), old_profile)
                self.assertEqual(runtime_path.read_bytes(), old_runtime)
                self.assertEqual(
                    json.loads(settings_path.read_text(encoding="utf-8")), old_settings
                )
                self.assertEqual(
                    [call.args[:2] for call in systemctl_service.call_args_list],
                    [
                        ("restart", backend.SERVICE_UNIT),
                        ("stop", backend.SERVICE_UNIT),
                        ("start", backend.SERVICE_UNIT),
                    ],
                )

    def test_pull_profile_fetch_failure_keeps_active_service_running(self) -> None:
        old_profile = b'{"profile":"old"}\n'
        old_runtime = b'{"runtime":"old"}\n'
        old_settings = copy.deepcopy(backend.DEFAULT_SETTINGS)
        old_settings["subscription_url"] = "https://old.example/sub"

        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            profile_path = directory / "profile.json"
            runtime_path = directory / "runtime.json"
            settings_path = directory / "settings.json"
            profile_path.write_bytes(old_profile)
            runtime_path.write_bytes(old_runtime)
            settings_path.write_text(
                json.dumps(old_settings, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            def load_settings() -> dict:
                return json.loads(settings_path.read_text(encoding="utf-8"))

            with mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path), mock.patch.object(
                backend, "load_settings", side_effect=load_settings
            ), mock.patch.object(
                backend, "unit_active", return_value=True
            ), mock.patch.object(
                self.window, "build_subscription_url", return_value="https://new.example/sub"
            ), mock.patch.object(self.window, "run_task") as run_task:
                self.window.pull_profile()
                sequence = run_task.call_args.args[1]

                with mock.patch.object(
                    backend,
                    "fetch_profile",
                    side_effect=backend.SmartBoxError("订阅服务器暂时不可用"),
                ), mock.patch.object(
                    backend, "flclash_conflict", return_value=False
                ), mock.patch.object(
                    self.window, "ensure_flclash_stopped"
                ) as stop_flclash, mock.patch.object(
                    backend, "systemctl_service"
                ) as systemctl_service:
                    with self.assertRaisesRegex(
                        backend.SmartBoxError, "当前核心未重启"
                    ):
                        sequence()

                stop_flclash.assert_not_called()
                systemctl_service.assert_not_called()
                self.assertEqual(profile_path.read_bytes(), old_profile)
                self.assertEqual(runtime_path.read_bytes(), old_runtime)
                self.assertEqual(
                    json.loads(settings_path.read_text(encoding="utf-8")),
                    old_settings,
                )

    def test_pull_failure_never_overwrites_a_newer_profile_bundle(self) -> None:
        self.window.host_edit.setText("new.example")
        self.window.path_edit.setText("/sub")
        probe = {"stable": True, "passed": 5, "total": 5, "latency_ms": 120}

        with tempfile.TemporaryDirectory() as temporary_dir:
            directory = Path(temporary_dir)
            profile_path = directory / "profile.json"
            runtime_path = directory / "runtime.json"
            profile_path.write_bytes(b'{"profile":"old"}\n')
            runtime_path.write_bytes(b'{"runtime":"old"}\n')
            receipt = backend.ProfileUpdateReceipt(
                previous_profile=(b'{"profile":"old"}\n', 0o600),
                previous_runtime=(b'{"runtime":"old"}\n', 0o600),
                previous_subscription_url="https://old.example/sub",
                previous_last_pull_utc=None,
                profile_sha256="owned-profile",
                runtime_sha256="owned-runtime",
                runtime_settings=backend.runtime_settings_snapshot(self.settings),
                subscription_url="https://new.example/sub",
                last_pull_utc="2026-08-24T00:00:00+00:00",
            )

            def fetch_profile(_url: str) -> backend.ProfileUpdateResult:
                profile_path.write_bytes(b'{"profile":"newer"}\n')
                runtime_path.write_bytes(b'{"runtime":"newer"}\n')
                with self.settings_lock:
                    self.settings["subscription_url"] = "https://newer.example/sub"
                    self.settings["last_pull_utc"] = "2026-08-24T01:00:00+00:00"
                return backend.ProfileUpdateResult(
                    {"nodes": 2, "selectors": 1}, receipt
                )

            restarted = mock.Mock(returncode=0, stdout="")
            with mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(
                backend, "unit_active", return_value=True
            ), mock.patch.object(
                self.window,
                "build_subscription_url",
                return_value="https://new.example/sub",
            ), mock.patch.object(
                self.window, "run_task"
            ) as run_task:
                self.window.pull_profile()
                sequence = run_task.call_args.args[1]
                with mock.patch.object(
                    backend, "fetch_profile", side_effect=fetch_profile
                ), mock.patch.object(
                    backend, "flclash_conflict", return_value=False
                ), mock.patch.object(
                    self.window, "ensure_flclash_stopped"
                ), mock.patch.object(
                    backend, "systemctl_service", return_value=restarted
                ), mock.patch.object(
                    self.window,
                    "verify_service_ready_then_probe",
                    return_value=probe,
                ), mock.patch.object(
                    backend, "connectivity_is_usable_for_mode", return_value=False
                ), mock.patch.object(
                    backend, "wait_for", return_value=True
                ), mock.patch.object(
                    self.window, "verify_service_ready"
                ), mock.patch.object(
                    backend, "rollback_profile_update", return_value=False
                ) as rollback, mock.patch.object(
                    backend, "atomic_write_bytes"
                ) as legacy_restore:
                    with self.assertRaisesRegex(
                        backend.SmartBoxError,
                        "更晚的配置更新，未覆盖.*当前配置恢复",
                    ):
                        sequence()

            rollback.assert_called_once_with(receipt)
            legacy_restore.assert_not_called()
            self.assertEqual(profile_path.read_bytes(), b'{"profile":"newer"}\n')
            self.assertEqual(runtime_path.read_bytes(), b'{"runtime":"newer"}\n')
            self.assertEqual(
                self.settings["subscription_url"], "https://newer.example/sub"
            )

    def test_smart_status_parser_sorts_candidates_and_formats_cost_breakdown(self) -> None:
        raw_status = {
            "selected": "节点 A",
            "selection_cost_semantics": "lower_is_better",
            "quality_score_semantics": "0_to_100_higher_is_better",
            "candidates": [
                {
                    "name": "节点 A",
                    "selected": True,
                    "selection_cost": 542,
                    "quality_score": 65,
                    "base_cost": 42,
                    "last_probe_delay_ms": 42,
                    "last_successful_probe_delay_ms": 42,
                    "last_probe_at": "2026-08-21T12:34:56Z",
                    "last_probe_succeeded": True,
                    "failure_count": 1,
                    "failure_penalty_per_failure": 500,
                    "applied_failure_penalty": 500,
                    "applied_stale_probe_penalty": 0,
                },
                {
                    "name": "节点 B",
                    "selected": False,
                    "selection_cost": 120,
                    "quality_score": 89,
                    "base_cost": 120,
                    "last_probe_delay_ms": 120,
                    "last_successful_probe_delay_ms": 120,
                    "last_probe_at": "2026-08-21T12:35:10Z",
                    "last_probe_succeeded": True,
                    "failure_count": 0,
                    "failure_penalty_per_failure": 500,
                    "applied_failure_penalty": 0,
                    "applied_stale_probe_penalty": 0,
                },
            ],
        }

        status = gui.normalize_smart_status(raw_status)

        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(
            [candidate["name"] for candidate in status["candidates"]],
            ["节点 B", "节点 A"],
        )
        self.assertEqual(
            gui.format_smart_status_summary(status),
            "实际 节点 A\n质量 65/100 · 42 ms",
        )
        tooltip = gui.format_smart_status_tooltip(status)
        self.assertIn("质量分：65/100（越高越优）", tooltip)
        self.assertLess(tooltip.index("1. 节点 B"), tooltip.index("2. 节点 A"))
        self.assertIn("选择成本 542", tooltip)
        self.assertIn("失败惩罚 500（1 次 × 500）", tooltip)
        self.assertIn("时间 2026-08-21T12:34:56Z", tooltip)

    def test_smart_probe_summary_marks_stale_and_recent_failure(self) -> None:
        stale = {
            "last_probe_at": "2026-08-15T00:00:00Z",
            "last_probe_succeeded": True,
            "last_probe_delay_ms": 35,
            "last_successful_probe_delay_ms": 35,
            "applied_stale_probe_penalty": 400,
        }
        failed = {
            "last_probe_at": "2026-08-21T12:00:00Z",
            "last_probe_succeeded": False,
            "last_successful_probe_delay_ms": 48,
        }

        self.assertEqual(gui.smart_probe_summary(stale), "35 ms（已陈旧）")
        self.assertEqual(
            gui.smart_probe_summary(failed),
            "最近失败 · 上次成功 48 ms",
        )

    def test_online_policy_merge_consumes_smart_status_and_old_core_falls_back(self) -> None:
        offline = [
            {"name": "AI", "all": ["AI Smart", "DIRECT"], "now": "DIRECT"},
            {"name": "普通", "all": ["节点"], "now": "节点"},
        ]
        smart_status = {
            "selected": "物理节点",
            "candidates": [
                {
                    "name": "物理节点",
                    "selection_cost": 40,
                    "quality_score": 96,
                    "last_probe_delay_ms": 40,
                    "last_successful_probe_delay_ms": 40,
                    "last_probe_at": "2026-08-21T12:00:00Z",
                    "last_probe_succeeded": True,
                }
            ],
        }
        proxies = {
            "AI": {"all": ["AI Smart", "DIRECT"], "now": "AI Smart"},
            "AI Smart": {"smart_status": smart_status},
            "普通": {"all": ["节点"], "now": "节点"},
            "节点": {"type": "VLESS"},
        }

        merged = gui.merge_online_policy_selectors(offline, proxies)

        self.assertEqual(merged[0]["now"], "AI Smart")
        self.assertEqual(merged[0]["smart_status"]["selected"], "物理节点")
        self.assertNotIn("smart_status", merged[1])
        old_core = gui.merge_online_policy_selectors(
            offline,
            {"AI": {"all": ["AI Smart", "DIRECT"], "now": "AI Smart"}},
        )
        self.assertNotIn("smart_status", old_core[0])
        malformed = copy.deepcopy(proxies)
        malformed["AI Smart"]["smart_status"]["candidates"][0]["quality_score"] = 999
        self.assertNotIn(
            "smart_status",
            gui.merge_online_policy_selectors(offline, malformed)[0],
        )

    def test_policy_row_displays_selected_physical_node_score_and_stale_probe(self) -> None:
        status = {
            "selected": "新加坡 01",
            "candidates": [
                {
                    "name": "新加坡 01",
                    "selection_cost": 350,
                    "quality_score": 74,
                    "last_probe_delay_ms": 55,
                    "last_successful_probe_delay_ms": 55,
                    "last_probe_at": "2026-08-15T00:00:00Z",
                    "last_probe_succeeded": True,
                    "applied_stale_probe_penalty": 295,
                }
            ],
        }
        self.window.policies_updated(
            (
                [
                    {
                        "name": "AI",
                        "all": ["新加坡 Smart", "日本 Smart"],
                        "now": "新加坡 Smart",
                        "smart_status": status,
                    }
                ],
                True,
            ),
            None,
        )

        _row, _label, _combo, status_label, _probe = self.window.policy_rows[0]
        self.assertIn("实际 新加坡 01", status_label.text())
        self.assertIn("质量 74/100", status_label.text())
        self.assertIn("55 ms（已陈旧）", status_label.text())
        self.assertIn("选择成本 350", status_label.toolTip())
        self.window.filter_policies("新加坡 01")
        self.assertFalse(self.window.policy_rows[0][0].isHidden())

    def test_policy_filter_reports_matches_and_empty_results(self) -> None:
        selectors = [
            {"name": "AI", "all": ["新加坡 01", "日本 01"], "now": "新加坡 01"},
            {"name": "Telegram", "all": ["香港 01", "美国 01"], "now": "美国 01"},
            {"name": "流媒体", "all": ["日本 02"], "now": "日本 02"},
        ]
        self.window.policies_updated((selectors, True), None)
        self.assertEqual(self.window.policy_filter_status.text(), "共 3 项策略")

        self.window.policy_search.setText("日本")
        self.application.processEvents()
        self.assertEqual(self.window.policy_filter_status.text(), "显示 1 / 共 3 项")
        self.assertFalse(self.window.policy_rows[2][0].isHidden())
        self.assertTrue(self.window.policy_rows[0][0].isHidden())

        self.window.policy_search.setText("不存在的节点")
        self.application.processEvents()
        self.assertEqual(
            self.window.policy_filter_status.text(), "未找到匹配策略 · 共 3 项"
        )
        self.assertTrue(all(row[0].isHidden() for row in self.window.policy_rows))
        self.assertTrue(self.window.policy_search.toolTip())

        self.window.policy_search.clear()
        self.application.processEvents()
        self.assertEqual(self.window.policy_filter_status.text(), "共 3 项策略")

    def test_policy_controls_include_selector_name_for_accessibility(self) -> None:
        selectors = [
            {"name": "AI 服务", "all": ["Global Smart"], "now": "Global Smart"},
            {"name": "Telegram", "all": ["Global Smart"], "now": "Global Smart"},
        ]
        self.window.policies_updated((selectors, True), None)

        accessible_names: list[tuple[str, str, str]] = []
        for _row, label, combo, status, probe in self.window.policy_rows:
            selector_name = label.text()
            self.assertIn(selector_name, combo.accessibleName())
            self.assertIn(selector_name, status.accessibleName())
            self.assertIn(selector_name, probe.accessibleName())
            accessible_names.append(
                (
                    combo.accessibleName(),
                    status.accessibleName(),
                    probe.accessibleName(),
                )
            )

        self.assertEqual(len(accessible_names), 2)
        self.assertNotEqual(accessible_names[0][0], accessible_names[1][0])
        self.assertNotEqual(accessible_names[0][1], accessible_names[1][1])
        self.assertNotEqual(accessible_names[0][2], accessible_names[1][2])

    def test_policy_refresh_hides_empty_placeholder_immediately(self) -> None:
        self.window.policies_updated(([], False), None)
        empty = next(
            label
            for label in self.window.policy_container.findChildren(gui.QLabel)
            if "尚无可用策略" in label.text()
        )
        self.assertFalse(empty.isHidden())

        self.window.policies_updated(
            ([{"name": "AI", "all": ["新加坡 Smart"], "now": "新加坡 Smart"}], True),
            None,
        )
        self.assertTrue(empty.isHidden())
        self.assertEqual(len(self.window.policy_rows), 1)

    def test_minimum_window_width_has_no_horizontal_page_overflow(self) -> None:
        self.window.resize(920, 660)
        self.application.processEvents()
        selectors = [
            {"name": "AI 服务", "all": ["新加坡 Smart", "日本 Smart"], "now": "新加坡 Smart"},
            {"name": "Telegram", "all": ["香港 Fallback"], "now": "香港 Fallback"},
        ]
        self.window.policies_updated((selectors, True), None)
        self.application.processEvents()

        for index in (0, 4):
            scroll = self.window.pages.widget(index)
            self.assertIsInstance(scroll, gui.QScrollArea)
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(self.window.policy_scroll.horizontalScrollBar().maximum(), 0)
        self.assertGreaterEqual(self.window.policy_search.width(), 280)

        navigation = sorted(
            self.window.nav_group.buttons(), key=lambda button: button.geometry().left()
        )
        for previous, current in zip(navigation, navigation[1:]):
            self.assertLess(previous.geometry().right(), current.geometry().left())
        self.assertLess(
            navigation[-1].geometry().right(),
            self.window.status_pill.geometry().left(),
        )

    def test_navigation_and_find_keyboard_shortcuts(self) -> None:
        for index, button in enumerate(self.window.nav_group.buttons(), 1):
            self.assertEqual(button.shortcut().toString(), f"Ctrl+{index}")
            self.assertIn(f"Ctrl+{index}", button.toolTip())

        navigation_button = self.window.nav_group.button(1)
        navigation_button.click()
        self.application.processEvents()
        self.assertEqual(self.window.pages.currentIndex(), 1)
        self.window.policy_search.setText("AI")
        self.window.focus_current_filter()
        self.assertTrue(self.window.policy_search.hasFocus())
        self.assertEqual(self.window.policy_search.selectedText(), "AI")

        self.window.select_page(3)
        self.window.log_search.setText("error")
        QTest.keyClick(self.window, gui.Qt.Key.Key_F, gui.Qt.KeyboardModifier.ControlModifier)
        self.application.processEvents()
        self.assertTrue(self.window.log_search.hasFocus())
        self.assertEqual(self.window.log_search.selectedText(), "error")

        self.window.select_page(0)
        self.window.focus_current_filter()
        self.assertIn("没有搜索框", self.window.statusBar().currentMessage())

    def test_policy_speed_test_probes_physical_candidates_and_refreshes_smart_status(self) -> None:
        name = "AI"
        initial_status = {
            "selected": "物理节点 A",
            "candidates": [
                {
                    "name": "物理节点 A",
                    "selection_cost": 600,
                    "quality_score": 62,
                    "last_probe_delay_ms": 100,
                    "last_successful_probe_delay_ms": 100,
                    "last_probe_at": "2026-08-21T12:00:00Z",
                    "last_probe_succeeded": True,
                },
                {
                    "name": "物理节点 B",
                    "selection_cost": 700,
                    "quality_score": 58,
                    "last_probe_delay_ms": 120,
                    "last_successful_probe_delay_ms": 120,
                    "last_probe_at": "2026-08-21T12:00:00Z",
                    "last_probe_succeeded": True,
                },
                {
                    "name": "物理节点 C",
                    "selection_cost": 750,
                    "quality_score": 57,
                    "last_probe_delay_ms": 130,
                    "last_successful_probe_delay_ms": 130,
                    "last_probe_at": "2026-08-21T12:00:00Z",
                    "last_probe_succeeded": True,
                }
            ],
        }
        refreshed_status = {
            "selected": "物理节点 B",
            "candidates": [
                {
                    "name": "物理节点 B",
                    "selection_cost": 30,
                    "quality_score": 97,
                    "last_probe_delay_ms": 30,
                    "last_successful_probe_delay_ms": 30,
                    "last_probe_at": "2026-08-21T12:05:00Z",
                    "last_probe_succeeded": True,
                    "failure_count": 0,
                    "failure_penalty_per_failure": 500,
                    "applied_failure_penalty": 0,
                    "applied_stale_probe_penalty": 0,
                },
                {
                    "name": "物理节点 C",
                    "selection_cost": 1500,
                    "quality_score": 40,
                    "last_successful_probe_delay_ms": 130,
                    "last_probe_at": "2026-08-21T12:05:00Z",
                    "last_probe_succeeded": False,
                    "failure_count": 1,
                    "failure_penalty_per_failure": 500,
                    "applied_failure_penalty": 500,
                    "applied_stale_probe_penalty": 0,
                }
            ],
        }
        self.window.policies_updated(
            (
                [
                    {
                        "name": name,
                        "all": ["新加坡 Smart", "日本 Smart"],
                        "now": "新加坡 Smart",
                        "smart_status": initial_status,
                    }
                ],
                True,
            ),
            None,
        )
        _row, _label, combo, status_label, probe_button = self.window.policy_rows[0]
        group_result = {
            "name": "新加坡 Smart",
            "expected": ["物理节点 A", "物理节点 B", "物理节点 C"],
            "delays": {"物理节点 A": 110, "物理节点 B": 30},
            "failed": ["物理节点 C"],
            "tested": 2,
            "total": 3,
        }
        proxy_response = {
            "proxies": {
                name: {"now": "新加坡 Smart"},
                "新加坡 Smart": {"smart_status": refreshed_status},
            }
        }
        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            backend, "probe_group_delays", return_value=group_result
        ) as probe, mock.patch.object(
            backend, "api_request", return_value=proxy_response
        ) as api_request:
            probe_button.click()
            self.wait_for_task(f"policy-delay:{name}")

        probe.assert_called_once_with(
            "新加坡 Smart",
            ["物理节点 A", "物理节点 B", "物理节点 C"],
        )
        api_request.assert_called_once_with("/proxies", timeout=3)
        self.assertEqual(combo.itemText(0), "新加坡 Smart")
        self.assertEqual(combo.itemText(1), "日本 Smart")
        self.assertIn("实际 物理节点 B", status_label.text())
        self.assertIn("质量 97/100", status_label.text())
        self.assertIn("30 ms", status_label.text())
        self.assertIn("本轮 新加坡 Smart 物理节点测速", status_label.toolTip())
        self.assertIn("物理节点 B: 30 ms", status_label.toolTip())
        self.assertIn("物理节点 C: 失败", status_label.toolTip())
        self.assertEqual(combo.currentData(gui.Qt.ItemDataRole.UserRole), "新加坡 Smart")

    def test_policy_speed_test_shows_summary_and_failed_nodes(self) -> None:
        name = "🎯 / 基准 Smart"
        self.window.policies_updated(
            ([{"name": name, "all": ["节点 A", "节点 B"], "now": "节点 A"}], True),
            None,
        )
        row, _label, combo, delay_label, probe_button = self.window.policy_rows[0]
        self.assertIsNotNone(row)
        self.assertEqual(combo.currentText(), "节点 A")
        result = {
            "name": name,
            "expected": ["节点 A", "节点 B"],
            "delays": {"节点 B": 80},
            "failed": ["节点 A"],
            "tested": 1,
            "total": 2,
        }
        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            backend, "probe_group_delays", return_value=result
        ) as probe:
            probe_button.click()
            self.wait_for_task(f"policy-delay:{name}")

        probe.assert_called_once_with(name, ["节点 A", "节点 B"])
        self.assertEqual(delay_label.text(), "最快 80 ms · 1/2")
        self.assertIn("节点 A: 失败", delay_label.toolTip())
        self.assertIn("节点 B: 80 ms", delay_label.toolTip())
        self.assertTrue(probe_button.isEnabled())
        self.assertEqual(combo.itemText(0), "节点 A · 失败")
        self.assertEqual(combo.itemText(1), "节点 B · 80 ms")
        self.assertEqual(combo.currentData(gui.Qt.ItemDataRole.UserRole), "节点 A")
        self.assertNotIn(f"policy:{name}", self.window.tasks)

        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            backend, "api_request"
        ) as api_request:
            combo.setCurrentIndex(1)
            self.wait_for_task(f"policy:{name}")
        api_request.assert_called_once_with(
            f"/proxies/{gui.urllib.parse.quote(name, safe='')}",
            method="PUT",
            payload={"name": "节点 B"},
            timeout=4,
        )

    def test_policy_speed_test_disables_button_until_result(self) -> None:
        name = "测试 Smart"
        self.window.policies_updated(
            ([{"name": name, "all": ["节点"], "now": "节点"}], True), None
        )
        _row, _label, _combo, delay_label, probe_button = self.window.policy_rows[0]
        started = threading.Event()
        release = threading.Event()

        def slow_probe(group: str, expected: list[str]) -> dict:
            self.assertEqual(group, name)
            self.assertEqual(expected, ["节点"])
            started.set()
            release.wait(1)
            return {
                "name": group,
                "expected": expected,
                "delays": {"节点": 10},
                "failed": [],
                "tested": 1,
                "total": 1,
            }

        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            backend, "probe_group_delays", side_effect=slow_probe
        ):
            probe_button.click()
            for _ in range(100):
                self.application.processEvents()
                if started.is_set():
                    break
                QTest.qWait(5)
            self.assertTrue(started.is_set())
            self.assertFalse(probe_button.isEnabled())
            self.assertEqual(probe_button.text(), "测速中…")
            self.assertEqual(delay_label.text(), "测试中…")
            release.set()
            self.wait_for_task(f"policy-delay:{name}")

        self.assertTrue(probe_button.isEnabled())
        self.assertEqual(probe_button.text(), "测速")
        self.assertEqual(delay_label.text(), "最快 10 ms · 1/1")

    def test_mode_change_ignores_second_selection_until_first_finishes(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_api(path: str, **_kwargs: object) -> dict:
            self.assertEqual(path, "/configs")
            started.set()
            release.wait(1)
            return {}

        probe_result = {"online": True, "passed": 5, "total": 5, "latency_ms": 60}
        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            backend, "api_request", side_effect=slow_api
        ) as api_request, mock.patch.object(
            backend, "probe_connectivity_guard", return_value=probe_result
        ) as probe_connectivity:
            self.window.mode_buttons["Global"].click()
            for _ in range(100):
                self.application.processEvents()
                if started.is_set():
                    break
                QTest.qWait(5)
            self.assertTrue(started.is_set())
            self.assertTrue(self.window.mode_buttons["Global"].isChecked())
            self.assertFalse(any(button.isEnabled() for button in self.window.mode_buttons.values()))

            self.window.change_mode("Direct")
            self.assertEqual(self.window.settings["mode"], "Global")
            self.assertTrue(self.window.mode_buttons["Global"].isChecked())
            self.assertEqual(api_request.call_count, 1)

            release.set()
            self.wait_for_task("mode-change")

        probe_connectivity.assert_called_once_with(mode="Global")
        self.assertTrue(all(button.isEnabled() for button in self.window.mode_buttons.values()))
        self.assertEqual(self.window.settings["mode"], "Global")

    def test_live_mode_change_rolls_back_when_new_mode_is_unusable(self) -> None:
        unusable = {
            "online": False,
            "passed": 1,
            "total": 5,
            "latency_ms": 5000,
            "error": "Smart 代理路径超时",
        }
        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            backend, "api_request", return_value={}
        ) as api_request, mock.patch.object(
            backend, "probe_connectivity_guard", return_value=unusable
        ), mock.patch.object(
            backend, "connectivity_is_usable_for_mode", return_value=False
        ):
            self.window.mode_buttons["Global"].click()
            self.wait_for_task("mode-change")

        self.assertEqual(
            api_request.call_args_list,
            [
                mock.call("/configs", method="PATCH", payload={"mode": "Global"}),
                mock.call("/configs", method="PATCH", payload={"mode": "Rule"}),
            ],
        )
        self.assertEqual(self.window.settings["mode"], "Rule")
        self.assertTrue(self.window.mode_buttons["Rule"].isChecked())
        self.assertEqual(self.window.operation_state, "error")
        self.assertIn("已恢复智能分流", self.window.operation_message)

    def test_live_mode_change_rolls_back_when_connectivity_probe_raises(self) -> None:
        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            backend, "api_request", return_value={}
        ) as api_request, mock.patch.object(
            backend,
            "probe_connectivity_guard",
            side_effect=backend.SmartBoxError("验网接口暂时不可用"),
        ):
            self.window.mode_buttons["Global"].click()
            self.wait_for_task("mode-change")

        self.assertEqual(
            api_request.call_args_list,
            [
                mock.call("/configs", method="PATCH", payload={"mode": "Global"}),
                mock.call("/configs", method="PATCH", payload={"mode": "Rule"}),
            ],
        )
        self.assertEqual(self.window.settings["mode"], "Rule")
        self.assertTrue(self.window.mode_buttons["Rule"].isChecked())
        self.assertEqual(self.window.operation_state, "error")
        self.assertIn("验网接口暂时不可用", self.window.operation_message)
        self.assertIn("已恢复智能分流", self.window.operation_message)

    def test_live_mode_change_fails_open_when_core_rollback_fails(self) -> None:
        unusable = {
            "online": False,
            "passed": 1,
            "total": 5,
            "error": "Smart 代理不可用",
        }
        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            backend,
            "api_request",
            side_effect=[{}, backend.SmartBoxError("回滚 PATCH 超时")],
        ) as api_request, mock.patch.object(
            backend, "probe_connectivity_guard", return_value=unusable
        ), mock.patch.object(
            backend, "connectivity_is_usable_for_mode", return_value=False
        ), mock.patch.object(
            backend, "recover_failed_switch", return_value="smart-box 已停止"
        ) as recover:
            self.window.mode_buttons["Global"].click()
            self.wait_for_task("mode-change")

        self.assertEqual(
            api_request.call_args_list,
            [
                mock.call("/configs", method="PATCH", payload={"mode": "Global"}),
                mock.call("/configs", method="PATCH", payload={"mode": "Rule"}),
            ],
        )
        recover.assert_called_once_with(False)
        self.assertEqual(self.window.settings["mode"], "Rule")
        self.assertTrue(self.window.mode_buttons["Rule"].isChecked())
        self.assertEqual(self.window.operation_state, "error")
        self.assertIn("恢复核心模式失败", self.window.operation_message)
        self.assertIn("系统已回到直连", self.window.operation_message)
        self.assertNotIn("已恢复智能分流", self.window.operation_message)

    def test_live_mode_change_keeps_usable_partial_connectivity(self) -> None:
        partial = {
            "online": False,
            "passed": 3,
            "total": 5,
            "latency_ms": 320,
            "error": "GitHub 超时；Telegram 超时",
        }
        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            backend, "api_request", return_value={}
        ) as api_request, mock.patch.object(
            backend, "probe_connectivity_guard", return_value=partial
        ), mock.patch.object(
            backend, "connectivity_is_usable_for_mode", return_value=True
        ):
            self.window.mode_buttons["Global"].click()
            self.wait_for_task("mode-change")

        api_request.assert_called_once_with(
            "/configs", method="PATCH", payload={"mode": "Global"}
        )
        self.assertEqual(self.window.settings["mode"], "Global")
        self.assertTrue(self.window.mode_buttons["Global"].isChecked())
        self.assertEqual(self.window.operation_state, "working")
        self.assertIn("部分站点降级", self.window.operation_message)
        self.assertIn("已切换为全局代理", self.window.operation_message)
        self.assertEqual(
            self.window.detail_values["联网验收"].property("semanticState"),
            "warning",
        )

    def test_policy_selection_reverts_duplicate_while_request_is_pending(self) -> None:
        name = "测试策略"
        self.window.policies_updated(
            ([{"name": name, "all": ["节点 A", "节点 B", "节点 C"], "now": "节点 A"}], True),
            None,
        )
        _row, _label, combo, _delay, _probe = self.window.policy_rows[0]
        started = threading.Event()
        release = threading.Event()

        def slow_api(path: str, **_kwargs: object) -> dict:
            self.assertIn("/proxies/", path)
            started.set()
            release.wait(1)
            return {}

        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            backend, "api_request", side_effect=slow_api
        ) as api_request:
            combo.setCurrentIndex(1)
            for _ in range(100):
                self.application.processEvents()
                if started.is_set():
                    break
                QTest.qWait(5)
            self.assertTrue(started.is_set())
            self.assertFalse(combo.isEnabled())
            self.assertEqual(combo.currentData(gui.Qt.ItemDataRole.UserRole), "节点 B")

            combo.setCurrentIndex(2)
            self.application.processEvents()
            self.assertEqual(combo.currentData(gui.Qt.ItemDataRole.UserRole), "节点 B")
            self.assertEqual(api_request.call_count, 1)

            release.set()
            self.wait_for_task(f"policy:{name}")

        self.assertTrue(combo.isEnabled())
        self.assertEqual(combo.currentData(gui.Qt.ItemDataRole.UserRole), "节点 B")

    def test_slow_policy_selection_preserves_theme_changed_during_api_call(self) -> None:
        name = "慢策略"
        self.window.policies_updated(
            ([{"name": name, "all": ["节点 A", "节点 B"], "now": "节点 A"}], True),
            None,
        )
        _row, _label, combo, _delay, _probe = self.window.policy_rows[0]
        started = threading.Event()
        release = threading.Event()

        def slow_api(path: str, **_kwargs: object) -> dict:
            self.assertIn("/proxies/", path)
            started.set()
            release.wait(2)
            return {}

        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            backend, "api_request", side_effect=slow_api
        ):
            combo.setCurrentIndex(1)
            for _ in range(100):
                self.application.processEvents()
                if started.is_set():
                    break
                QTest.qWait(5)
            self.assertTrue(started.is_set())
            self.assertNotIn(name, self.settings.get("selector_overrides", {}))

            self.window.night_mode_check.click()
            self.assertEqual(self.settings["theme"], "dark")

            release.set()
            self.wait_for_task(f"policy:{name}")

        self.assertEqual(self.settings["theme"], "dark")
        self.assertEqual(self.settings["selector_overrides"][name], "节点 B")

    def test_live_policy_selection_restores_core_when_settings_save_fails(self) -> None:
        name = "测试策略"
        self.window.policies_updated(
            ([{"name": name, "all": ["节点 A", "节点 B"], "now": "节点 A"}], True),
            None,
        )
        _row, _label, combo, _delay, _probe = self.window.policy_rows[0]

        def mutate_settings(mutator: object) -> dict:
            candidate = copy.deepcopy(self.settings)
            mutator(candidate)  # type: ignore[operator]
            overrides = candidate.get("selector_overrides", {})
            if isinstance(overrides, dict) and overrides.get(name) == "节点 B":
                raise OSError("settings are read-only")
            self.settings = candidate
            return copy.deepcopy(candidate)

        with mock.patch.object(backend, "unit_active", return_value=True), mock.patch.object(
            backend, "api_request", return_value={}
        ) as api_request, mock.patch.object(
            backend, "mutate_settings", side_effect=mutate_settings
        ):
            combo.setCurrentIndex(1)
            self.wait_for_task(f"policy:{name}")

        self.assertEqual(api_request.call_count, 2)
        self.assertEqual(
            [call.kwargs["payload"] for call in api_request.call_args_list],
            [{"name": "节点 B"}, {"name": "节点 A"}],
        )
        self.assertEqual(combo.currentData(gui.Qt.ItemDataRole.UserRole), "节点 A")
        self.assertNotIn(name, self.settings.get("selector_overrides", {}))
        self.assertEqual(self.window.operation_state, "error")
        self.assertIn("核心与设置已恢复到“节点 A”", self.window.operation_message)

    def test_stack_change_runs_off_gui_thread_and_rolls_back(self) -> None:
        started = threading.Event()
        release = threading.Event()
        with tempfile.TemporaryDirectory() as temporary_dir:
            profile_path = Path(temporary_dir) / "profile.json"
            profile_path.write_text("{}\n", encoding="utf-8")

            def slow_prepare(**_kwargs: object) -> Path:
                started.set()
                release.wait(1)
                return profile_path

            with mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "unit_active", return_value=False
            ), mock.patch.object(backend, "prepare_runtime", side_effect=slow_prepare):
                self.window.stack_box.setCurrentIndex(self.window.stack_box.findData("system"))
                for _ in range(100):
                    self.application.processEvents()
                    if started.is_set():
                        break
                    QTest.qWait(5)
                self.assertTrue(started.is_set())
                self.assertFalse(self.window.stack_box.isEnabled())
                release.set()
                self.wait_for_task("stack-change")

            self.assertTrue(self.window.stack_box.isEnabled())
            self.assertEqual(self.window.stack_box.currentData(), "system")
            self.assertEqual(self.window.settings["tun_stack"], "system")

            with mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "unit_active", return_value=False
            ), mock.patch.object(
                backend,
                "prepare_runtime",
                side_effect=backend.SmartBoxError("invalid stack"),
            ):
                self.window.stack_box.setCurrentIndex(self.window.stack_box.findData("mixed"))
                self.wait_for_task("stack-change")

            self.assertTrue(self.window.stack_box.isEnabled())
            self.assertEqual(self.window.stack_box.currentData(), "system")
            self.assertEqual(self.window.settings["tun_stack"], "system")

    def test_autostart_success_does_not_write_settings(self) -> None:
        self.assertTrue(self.window.autostart_check.isChecked())
        with mock.patch.object(backend, "set_gui_autostart") as set_autostart, mock.patch.object(
            backend, "save_settings"
        ) as save_settings:
            self.window.autostart_check.click()

        set_autostart.assert_called_once_with(False)
        save_settings.assert_not_called()
        self.assertFalse(self.window.autostart_check.isChecked())
        self.assertIn("登录启动已关闭", self.window.operation_message)

    def test_autostart_failure_restores_actual_desktop_entry_state(self) -> None:
        self.window.autostart_check.blockSignals(True)
        self.window.autostart_check.setChecked(False)
        self.window.autostart_check.blockSignals(False)

        with mock.patch.object(
            backend, "set_gui_autostart", side_effect=OSError("autostart is read-only")
        ) as set_autostart, mock.patch.object(
            backend, "gui_autostart_enabled", return_value=True
        ) as enabled:
            self.window.autostart_check.click()

        set_autostart.assert_called_once_with(True)
        enabled.assert_called_once_with()
        self.assertTrue(self.window.autostart_check.isChecked())
        self.assertEqual(self.window.operation_state, "error")
        self.assertIn("更新登录启动失败", self.window.operation_message)

    def test_mirror_benchmark_can_apply_ranked_arch_source(self) -> None:
        benchmark = {
            "repo": "arch",
            "summaries": {
                "arch": {
                    "label": "pacman / paru 官方源",
                    "tested": 2,
                    "successful": 2,
                    "failed": 0,
                    "best": {"server": "https://fast.example/", "speed_kib_s": 500.0, "latency_ms": 20},
                    "results": [
                        {"server": "https://fast.example/", "ok": True, "speed_kib_s": 500.0, "latency_ms": 20},
                        {"server": "https://slow.example/", "ok": True, "speed_kib_s": 50.0, "latency_ms": 200},
                    ],
                }
            },
        }
        with mock.patch.object(backend, "benchmark_mirror_sources", return_value=benchmark), mock.patch.object(
            backend,
            "apply_mirror_ranking",
            return_value={"target": "/etc/pacman.d/mirrorlist"},
        ) as apply:
            self.window.select_page(4)
            self.window.benchmark_mirrors()
            self.wait_for_task("mirror-benchmark")
            self.assertTrue(self.window.mirror_apply_button.isEnabled())
            self.assertIn("最快 https://fast.example/", self.window.mirror_status_label.text())
            self.window.apply_mirror_ranking()
            self.wait_for_task("mirror-apply")

        apply.assert_called_once()
        self.assertIn("最快源已应用", self.window.operation_message)

    def test_mirror_results_are_scoped_to_selected_repository(self) -> None:
        arch_result = {
            "repo": "arch",
            "summaries": {
                "arch": {
                    "label": "pacman / paru 官方源",
                    "tested": 1,
                    "successful": 1,
                    "failed": 0,
                    "best": {
                        "server": "https://arch-fast.example/",
                        "speed_kib_s": 400.0,
                        "latency_ms": 25,
                    },
                    "results": [],
                }
            },
        }
        self.window.select_page(4)
        with mock.patch.object(
            backend, "benchmark_mirror_sources", return_value=arch_result
        ):
            self.window.benchmark_mirrors()
            self.assertFalse(self.window.mirror_repo_box.isEnabled())
            self.wait_for_task("mirror-benchmark")

        self.assertTrue(self.window.mirror_apply_button.isEnabled())
        self.assertIn("arch-fast.example", self.window.mirror_status_label.text())

        self.window.mirror_repo_box.setCurrentIndex(
            self.window.mirror_repo_box.findData("cachyos")
        )
        self.application.processEvents()
        self.assertFalse(self.window.mirror_apply_button.isEnabled())
        self.assertIn("尚未测速", self.window.mirror_status_label.text())

        self.window.mirror_repo_box.setCurrentIndex(
            self.window.mirror_repo_box.findData("arch")
        )
        self.application.processEvents()
        self.assertTrue(self.window.mirror_apply_button.isEnabled())
        self.assertIn("arch-fast.example", self.window.mirror_status_label.text())

    def test_exit_waits_for_background_tasks_before_quitting(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def blocking_task() -> str:
            started.set()
            release.wait(1)
            return "done"

        self.assertTrue(self.window.run_task("blocking", blocking_task, lambda *_args: None))
        for _ in range(100):
            self.application.processEvents()
            if started.is_set():
                break
            QTest.qWait(5)
        self.assertTrue(started.is_set())

        self.window.exit_application()
        self.assertTrue(self.window.exiting)
        self.assertFalse(self.window.allow_exit)
        self.assertFalse(self.window.poll_timer.isActive())
        self.assertFalse(self.window.run_task("late", lambda: None, lambda *_args: None))

        release.set()
        self.wait_for_task("blocking")
        for _ in range(50):
            self.application.processEvents()
            if self.window.allow_exit:
                break
            QTest.qWait(5)
        self.assertTrue(self.window.allow_exit)

    def test_exit_confirmation_preserves_or_discards_all_unsaved_edits(self) -> None:
        self.window.allow_editor.setPlainText("unsaved.example.cn")
        self.window.host_edit.setText("bad host/name")
        self.application.processEvents()
        self.assertTrue(self.window.domain_dirty)
        self.assertTrue(self.window.endpoint_dirty)

        with mock.patch.object(
            gui.QMessageBox,
            "question",
            side_effect=[
                gui.QMessageBox.StandardButton.No,
                gui.QMessageBox.StandardButton.Yes,
            ],
        ) as question, mock.patch.object(QApplication, "quit") as quit_application:
            self.window.select_page(4)
            self.window.exit_application()
            self.application.processEvents()

            self.assertFalse(self.window.closing)
            self.assertFalse(self.window.exiting)
            self.assertFalse(self.window.allow_exit)
            self.assertTrue(self.window.isVisible())
            self.assertEqual(self.window.pages.currentIndex(), 2)
            self.assertTrue(self.window.allow_editor.hasFocus())
            quit_application.assert_not_called()
            prompt = question.call_args.args[2]
            self.assertIn("域名名单", prompt)
            self.assertIn("订阅地址", prompt)

            self.window.exit_application()

            self.assertTrue(self.window.closing)
            self.assertTrue(self.window.exiting)
            self.assertTrue(self.window.allow_exit)
            quit_application.assert_called_once_with()

        self.assertEqual(question.call_count, 2)

    def test_clean_exit_does_not_ask_for_discard_confirmation(self) -> None:
        self.assertFalse(self.window.domain_dirty)
        self.assertFalse(self.window.endpoint_dirty)
        with mock.patch.object(gui.QMessageBox, "question") as question, mock.patch.object(
            QApplication, "quit"
        ) as quit_application:
            self.window.exit_application()

        question.assert_not_called()
        quit_application.assert_called_once_with()
        self.assertTrue(self.window.exiting)
        self.assertTrue(self.window.allow_exit)

    def test_instance_lock_allows_only_one_process_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            with mock.patch.object(backend, "STATE_DIR", Path(temporary_dir)), mock.patch.object(
                backend, "ensure_directories"
            ):
                first = gui.acquire_instance_lock()
                self.assertIsNotNone(first)
                second = gui.acquire_instance_lock()
                self.assertIsNone(second)
                gui.release_instance_lock(first)
                third = gui.acquire_instance_lock()
                self.assertIsNotNone(third)
                gui.release_instance_lock(third)

    def test_instance_command_argument_mapping(self) -> None:
        self.assertEqual(
            gui.instance_command_for_arguments(gui.parse_arguments([])),
            gui.INSTANCE_SHOW_COMMAND,
        )
        self.assertIsNone(
            gui.instance_command_for_arguments(gui.parse_arguments(["--background"]))
        )
        self.assertIsNone(
            gui.instance_command_for_arguments(
                gui.parse_arguments(["--screenshot", "/tmp/smart-box.png"])
            )
        )

    def test_primary_instance_show_command_restores_hidden_window(self) -> None:
        self.window.hide()
        self.application.processEvents()
        self.assertTrue(self.window.isHidden())

        self.assertTrue(self.window.handle_instance_command(gui.INSTANCE_SHOW_COMMAND))
        self.application.processEvents()

        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())
        self.assertFalse(self.window.handle_instance_command("unsupported"))

    def test_local_instance_command_round_trip_shows_primary_window(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            state_dir = Path(temporary_dir) / "smart-box"
            state_dir.mkdir()
            state_home = str(Path(temporary_dir))
            environment = dict(os.environ)
            environment["XDG_STATE_HOME"] = state_home
            environment["PYTHONPATH"] = str(Path(gui.__file__).resolve().parent)
            child_code = (
                "import smart_box_linux as gui; "
                "raise SystemExit(0 if gui.send_instance_command("
                "gui.INSTANCE_SHOW_COMMAND, timeout_ms=2000) else 1)"
            )
            patcher = mock.patch.object(backend, "STATE_DIR", state_dir)
            patcher.start()
            server = gui.InstanceCommandServer(self.window.handle_instance_command)
            try:
                self.assertTrue(server.listen())
                self.window.hide()
                self.application.processEvents()
                sender = subprocess.Popen(
                    [sys.executable, "-c", child_code],
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                for _ in range(400):
                    self.application.processEvents()
                    if sender.poll() is not None:
                        break
                    QTest.qWait(5)
                output, _ = sender.communicate(timeout=2)
            finally:
                server.close()
                patcher.stop()

        self.assertEqual(
            sender.returncode,
            0,
            output,
        )
        self.assertTrue(self.window.isVisible())

    def test_second_foreground_instance_requests_primary_window(self) -> None:
        with mock.patch.object(gui, "acquire_instance_lock", return_value=None), mock.patch.object(
            gui, "send_instance_command", return_value=True
        ) as send_command:
            result = gui.main([])

        self.assertEqual(result, 0)
        send_command.assert_called_once_with(gui.INSTANCE_SHOW_COMMAND)

    def test_second_background_instance_does_not_disturb_primary_window(self) -> None:
        with mock.patch.object(gui, "acquire_instance_lock", return_value=None), mock.patch.object(
            gui, "send_instance_command"
        ) as send_command:
            result = gui.main(["--background"])

        self.assertEqual(result, 0)
        send_command.assert_not_called()

    def test_second_instance_reports_failed_wakeup(self) -> None:
        with mock.patch.object(gui, "acquire_instance_lock", return_value=None), mock.patch.object(
            gui, "send_instance_command", return_value=False
        ):
            self.assertEqual(gui.main([]), 1)

    def test_primary_instance_releases_lock_when_transaction_recovery_fails(self) -> None:
        instance_lock = object()
        error = backend.SmartBoxError("journal damaged")
        with mock.patch.object(
            gui, "acquire_instance_lock", return_value=instance_lock
        ), mock.patch.object(
            backend, "recover_profile_transaction", side_effect=error
        ), mock.patch.object(
            gui.QMessageBox, "critical"
        ) as critical, mock.patch.object(
            gui, "release_instance_lock"
        ) as release:
            result = gui.main([])

        self.assertEqual(result, 1)
        critical.assert_called_once()
        self.assertEqual(critical.call_args.args[1], "配置恢复失败")
        self.assertIn("journal damaged", critical.call_args.args[2])
        release.assert_called_once_with(instance_lock)

    def test_save_endpoint_reports_oserror(self) -> None:
        with mock.patch.object(
            self.window, "build_subscription_url", return_value="https://new.example/sub"
        ), mock.patch.object(
            backend, "mutate_settings", side_effect=OSError("read-only")
        ):
            self.window.save_endpoint()
        self.assertEqual(self.window.operation_state, "error")
        self.assertIn("保存订阅地址失败", self.window.operation_message)

    def test_private_subscription_path_is_only_temporarily_visible(self) -> None:
        self.window.select_page(4)
        self.assertEqual(self.window.path_edit.echoMode(), gui.QLineEdit.EchoMode.Password)
        self.assertFalse(self.window.path_visibility_timer.isActive())

        self.window.reveal_action.trigger()
        self.assertEqual(self.window.path_edit.echoMode(), gui.QLineEdit.EchoMode.Normal)
        self.assertTrue(self.window.path_visibility_timer.isActive())
        self.assertIn("15 秒", self.window.reveal_action.toolTip())

        self.window.select_page(0)
        self.assertEqual(self.window.path_edit.echoMode(), gui.QLineEdit.EchoMode.Password)
        self.assertFalse(self.window.path_visibility_timer.isActive())
        self.assertIn("已隐藏", self.window.path_edit.accessibleDescription())

        self.window.select_page(4)
        self.window.reveal_action.trigger()
        self.window.hide()
        self.application.processEvents()
        self.assertEqual(self.window.path_edit.echoMode(), gui.QLineEdit.EchoMode.Password)

    def test_subscription_endpoint_validation_controls_save_and_pull(self) -> None:
        self.window.select_page(4)
        self.assertFalse(self.window.save_endpoint_button.isEnabled())
        self.assertFalse(self.window.pull_button.isEnabled())
        self.assertFalse(self.window.pull_quick_button.isEnabled())
        self.assertIn(
            "QPushButton#primaryButton:disabled",
            self.application.styleSheet(),
        )

        self.window.protocol_box.setCurrentText("HTTPS")
        self.window.host_edit.setText("converter.example.com")
        self.window.port_spin.setValue(443)
        self.window.path_edit.setText("/private/token")
        self.application.processEvents()

        self.assertTrue(self.window.save_endpoint_button.isEnabled())
        self.assertTrue(self.window.pull_button.isEnabled())
        self.assertTrue(self.window.pull_quick_button.isEnabled())
        self.assertIn("未保存更改", self.window.endpoint_validation_label.text())

        self.window.save_endpoint_button.click()
        self.application.processEvents()
        self.assertFalse(self.window.save_endpoint_button.isEnabled())
        self.assertTrue(self.window.pull_button.isEnabled())
        self.assertIn("已保存", self.window.endpoint_validation_label.text())

        self.window.host_edit.setText("bad host/name")
        self.application.processEvents()
        self.assertFalse(self.window.save_endpoint_button.isEnabled())
        self.assertFalse(self.window.pull_button.isEnabled())
        self.assertFalse(self.window.pull_quick_button.isEnabled())
        self.assertIn("有效的域名", self.window.endpoint_validation_label.text())


if __name__ == "__main__":
    unittest.main()
