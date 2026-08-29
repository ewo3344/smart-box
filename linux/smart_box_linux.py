#!/usr/bin/python3
"""PySide6 desktop client for smart-box on Linux."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QPointF, QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QStyleOptionButton,
    QSystemTrayIcon,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import smart_box_backend as backend


APP_TITLE = "smart-box"
POLL_INTERVAL_MS = 2500
SERVICE_START_TIMEOUT = 110
TRAY_RETRY_INTERVAL_MS = 1000
TRAY_RETRY_LIMIT = 15
INSTANCE_COMMAND_TIMEOUT_MS = 3000
INSTANCE_SHOW_COMMAND = "show"

MODE_PRESENTATION = {
    "Rule": ("智能分流", "按业务规则选择线路", "推荐日常使用：国内直连，其他流量按业务策略选择节点。"),
    "Global": ("全局代理", "所有流量使用代理", "所有可代理流量都交给代理节点，可能影响国内服务速度。"),
    "Direct": ("全部直连", "临时绕过代理", "不使用代理节点，适合排查代理或节点问题。"),
    "节能": ("节能模式", "减少测速与切换", "优先降低后台探测和节点切换频率，适合省电或弱网环境。"),
}


def mode_presentation(mode: str) -> tuple[str, str, str]:
    """Return stable user-facing copy without changing the core mode value."""
    return MODE_PRESENTATION.get(mode, (mode, "自定义运行模式", f"核心运行模式：{mode}"))


def acquire_instance_lock() -> Any | None:
    """Keep one GUI process per user session.

    ``flock`` is released by the kernel when the process exits, so a crashed
    client does not leave a stale lock that needs manual cleanup.  The open
    handle must stay alive for the lifetime of the application.
    """
    lock_path = backend.STATE_DIR / "gui.lock"
    try:
        backend.ensure_directories()
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            handle.close()
            return None
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        return handle
    except OSError:
        return None


def release_instance_lock(handle: Any | None) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        handle.close()
    except OSError:
        pass


def instance_server_name() -> str:
    return str(backend.STATE_DIR / "gui.sock")


def instance_command_for_arguments(arguments: argparse.Namespace) -> str | None:
    if arguments.background or arguments.screenshot:
        return None
    return INSTANCE_SHOW_COMMAND


def send_instance_command(command: str, timeout_ms: int = INSTANCE_COMMAND_TIMEOUT_MS) -> bool:
    """Send a command to the lock-owning GUI and wait for its acknowledgement."""
    if not command or "\n" in command:
        return False
    deadline = time.monotonic() + max(0, timeout_ms) / 1000
    while True:
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        socket = QLocalSocket()
        socket.connectToServer(instance_server_name())
        if socket.waitForConnected(min(250, remaining_ms)):
            socket.write(f"{command}\n".encode("utf-8"))
            if not socket.waitForBytesWritten(min(500, remaining_ms)):
                socket.abort()
                return False
            if socket.waitForReadyRead(remaining_ms):
                response = bytes(socket.readAll()).decode("utf-8", errors="replace").strip()
                socket.disconnectFromServer()
                return response == "ok"
            socket.abort()
            return False
        socket.abort()
        if remaining_ms <= 0:
            return False
        time.sleep(min(0.05, remaining_ms / 1000))


class InstanceCommandServer(QObject):
    def __init__(self, handler: Callable[[str], bool], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.handler = handler
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self.accept_connections)

    def listen(self) -> bool:
        name = instance_server_name()
        if self.server.listen(name):
            return True
        QLocalServer.removeServer(name)
        return self.server.listen(name)

    def close(self) -> None:
        name = self.server.serverName()
        self.server.close()
        if name:
            QLocalServer.removeServer(name)

    def accept_connections(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue
            socket.setProperty("smart_box_command_buffer", b"")
            socket.readyRead.connect(lambda connection=socket: self.read_command(connection))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self.read_command(socket)

    def read_command(self, socket: QLocalSocket) -> None:
        buffered = bytes(socket.property("smart_box_command_buffer") or b"")
        payload = buffered + bytes(socket.readAll())
        if b"\n" not in payload and len(payload) <= 128:
            socket.setProperty("smart_box_command_buffer", payload)
            return
        command = payload.partition(b"\n")[0].decode("utf-8", errors="replace")
        accepted = False
        try:
            accepted = self.handler(command)
        except Exception:  # noqa: BLE001 - keep the command server alive
            traceback.print_exc()
        socket.write(b"ok\n" if accepted else b"error\n")
        socket.flush()
        socket.disconnectFromServer()


def _smart_status_integer(value: Any, minimum: int = 0) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value


def normalize_smart_status(value: Any) -> dict[str, Any] | None:
    """Return a detached, display-safe Smart status snapshot."""
    if not isinstance(value, dict):
        return None
    selected = value.get("selected")
    candidates_value = value.get("candidates")
    if not isinstance(selected, str) or not selected or not isinstance(candidates_value, list):
        return None

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    integer_fields = (
        "selection_cost",
        "base_cost",
        "failure_count",
        "failure_penalty_per_failure",
        "applied_failure_penalty",
        "applied_stale_probe_penalty",
        "last_probe_delay_ms",
        "last_successful_probe_delay_ms",
    )
    timestamp_fields = ("last_probe_at", "last_success_at", "last_failure_at")
    for raw_candidate in candidates_value:
        if not isinstance(raw_candidate, dict):
            continue
        name = raw_candidate.get("name")
        quality_score = _smart_status_integer(raw_candidate.get("quality_score"))
        selection_cost = _smart_status_integer(raw_candidate.get("selection_cost"))
        if (
            not isinstance(name, str)
            or not name
            or name in seen
            or quality_score is None
            or quality_score > 100
            or selection_cost is None
        ):
            continue
        candidate: dict[str, Any] = {
            "name": name,
            "selected": name == selected,
            "quality_score": quality_score,
            "selection_cost": selection_cost,
            "last_probe_succeeded": raw_candidate.get("last_probe_succeeded") is True,
        }
        for field in integer_fields:
            number = _smart_status_integer(raw_candidate.get(field))
            if number is not None:
                candidate[field] = number
        for field in timestamp_fields:
            timestamp = raw_candidate.get(field)
            if isinstance(timestamp, str) and timestamp:
                candidate[field] = timestamp
        seen.add(name)
        candidates.append(candidate)

    if selected not in seen:
        return None
    candidates.sort(
        key=lambda candidate: (
            -candidate["quality_score"],
            candidate["selection_cost"],
            candidate["name"].casefold(),
        )
    )
    return {
        "selected": selected,
        "selection_cost_semantics": "lower_is_better",
        "quality_score_semantics": "0_to_100_higher_is_better",
        "candidates": candidates,
    }


def smart_status_for_selector(
    proxies: Any,
    selector_name: str,
    fallback_selected: str = "",
) -> dict[str, Any] | None:
    if not isinstance(proxies, dict):
        return None
    selector_proxy = proxies.get(selector_name)
    selected = fallback_selected
    if isinstance(selector_proxy, dict) and isinstance(selector_proxy.get("now"), str):
        selected = selector_proxy["now"]
    selected_proxy = proxies.get(selected)
    if not isinstance(selected_proxy, dict):
        return None
    return normalize_smart_status(selected_proxy.get("smart_status"))


def merge_online_policy_selectors(
    offline: list[dict[str, Any]], proxies: Any
) -> list[dict[str, Any]]:
    proxy_map = proxies if isinstance(proxies, dict) else {}
    online: list[dict[str, Any]] = []
    for selector in offline:
        current = proxy_map.get(selector.get("name"), {})
        if not isinstance(current, dict):
            current = {}
        choices = current.get("all", selector.get("all", []))
        selected = current.get("now", selector.get("now", ""))
        merged = {
            "name": selector.get("name", ""),
            "all": choices if isinstance(choices, list) else selector.get("all", []),
            "now": selected,
        }
        smart_status = smart_status_for_selector(
            proxy_map,
            str(selector.get("name", "")),
            selected if isinstance(selected, str) else "",
        )
        if smart_status is not None:
            merged["smart_status"] = smart_status
        online.append(merged)
    return online


def _selected_smart_candidate(status: dict[str, Any]) -> dict[str, Any] | None:
    selected = status.get("selected")
    candidates = status.get("candidates", [])
    if not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("name") == selected:
            return candidate
    return None


def smart_probe_summary(candidate: dict[str, Any]) -> str:
    last_probe = candidate.get("last_probe_at")
    last_probe_succeeded = candidate.get("last_probe_succeeded") is True
    last_successful_delay = _smart_status_integer(
        candidate.get("last_successful_probe_delay_ms"), 1
    )
    if isinstance(last_probe, str) and last_probe:
        if not last_probe_succeeded:
            if last_successful_delay is not None:
                return f"最近失败 · 上次成功 {last_successful_delay} ms"
            return "最近探测失败"
        delay = _smart_status_integer(candidate.get("last_probe_delay_ms"), 1)
        if delay is None:
            delay = last_successful_delay
        result = f"{delay} ms" if delay is not None else "最近探测成功"
        if _smart_status_integer(candidate.get("applied_stale_probe_penalty"), 1):
            result += "（已陈旧）"
        return result
    return "尚未探测"


def format_smart_status_summary(status: dict[str, Any]) -> str:
    candidate = _selected_smart_candidate(status)
    if candidate is None:
        return "Smart 状态不可用"
    return (
        f"实际 {candidate['name']}\n"
        f"质量 {candidate['quality_score']}/100 · {smart_probe_summary(candidate)}"
    )


def format_smart_status_tooltip(status: dict[str, Any]) -> str:
    candidate = _selected_smart_candidate(status)
    if candidate is None:
        return "Smart 状态不可用"
    lines = [
        f"实际物理节点：{candidate['name']}",
        f"质量分：{candidate['quality_score']}/100（越高越优）",
        "候选节点（按质量分从高到低；选择成本越低越优）：",
    ]
    candidates = status.get("candidates", [])
    for index, item in enumerate(candidates, 1):
        if not isinstance(item, dict):
            continue
        marker = "当前" if item.get("name") == status.get("selected") else "候选"
        lines.append(
            f"{index}. {item.get('name', '')} · {marker} · "
            f"质量 {item.get('quality_score', 0)}/100 · "
            f"选择成本 {item.get('selection_cost', 0)}"
        )
        failure_count = item.get("failure_count", 0)
        per_failure = item.get("failure_penalty_per_failure", 0)
        applied_failure = item.get("applied_failure_penalty", 0)
        stale_penalty = item.get("applied_stale_probe_penalty", 0)
        lines.append(
            f"   失败惩罚 {applied_failure}（{failure_count} 次 × {per_failure}）；"
            f"陈旧惩罚 {stale_penalty}"
        )
        probe_time = item.get("last_probe_at")
        probe_text = smart_probe_summary(item)
        lines.append(
            f"   探测 {probe_text} · 时间 {probe_time}"
            if isinstance(probe_time, str) and probe_time
            else f"   探测 {probe_text}"
        )
    return "\n".join(lines)


STYLE_SHEET = """
QWidget {
    color: #202825;
    font-family: "Noto Sans CJK SC", "Noto Sans SC", sans-serif;
    font-size: 13px;
}
QMainWindow, QWidget#windowRoot, QStackedWidget#pages {
    background: #f2f5f8;
}
QFrame#appHeader {
    background: #ffffff;
    border-bottom: 1px solid #d8e0e7;
}
QLabel#brand {
    color: #1769d2;
    font-size: 20px;
    font-weight: 700;
}
QLabel#brandVersion {
    color: #687681;
    font-size: 11px;
}
QPushButton#navButton {
    background: transparent;
    border: none;
    border-radius: 5px;
    color: #53616c;
    min-height: 34px;
    padding: 0 12px;
}
QPushButton#navButton:hover {
    background: #edf3fa;
    color: #1769d2;
}
QPushButton#navButton:checked {
    background: #e7f0fb;
    color: #1769d2;
    font-weight: 600;
}
QLabel#pageHeading {
    font-size: 15px;
    font-weight: 650;
    color: #34424d;
}
QLabel#sectionHeading {
    font-size: 15px;
    font-weight: 650;
    color: #202825;
}
QLabel#muted, QLabel.muted {
    color: #687570;
    font-size: 12px;
}
QLabel#statusPill {
    border-radius: 4px;
    color: white;
    font-size: 12px;
    font-weight: 650;
    padding: 7px 12px;
}
QFrame#toolPanel, QFrame#statCard, QFrame#policyRow, QFrame#editorPanel {
    background: #ffffff;
    border: 1px solid #dce2df;
    border-radius: 6px;
}
QFrame#controllerBar {
    background: #ffffff;
    border: 1px solid #d8e0e7;
    border-radius: 7px;
}
QFrame#trafficPanel, QFrame#modeCard {
    background: #ffffff;
    border: 1px solid #d8e0e7;
    border-radius: 7px;
}
QLabel#operationBanner {
    background: #e8f1fb;
    border: 1px solid #bad2ee;
    border-radius: 5px;
    color: #245b91;
    min-height: 34px;
    padding: 0 12px;
}
QFrame#toolPanel {
    background: #fbfcfb;
}
QLabel#statValue {
    color: #17201e;
    font-size: 20px;
    font-weight: 700;
}
QLabel#statLabel {
    color: #71807a;
    font-size: 11px;
}
QPushButton, QToolButton {
    background: #ffffff;
    border: 1px solid #cbd4d0;
    border-radius: 5px;
    min-height: 36px;
    padding: 0 13px;
}
QPushButton:hover, QToolButton:hover {
    background: #f0f4f2;
    border-color: #9eafa8;
}
QPushButton:pressed, QToolButton:pressed {
    background: #e6ece9;
}
QPushButton:checked, QToolButton:checked {
    background: #e8f1fb;
    border-color: #4b8bd6;
    color: #155ba9;
    font-weight: 650;
}
QPushButton:disabled, QToolButton:disabled {
    background: #eef1ef;
    color: #9aa49f;
    border-color: #e0e5e2;
}
QPushButton#primaryButton {
    background: #1769d2;
    border-color: #1769d2;
    color: #ffffff;
    font-weight: 650;
}
QPushButton#primaryButton:hover {
    background: #125bb8;
    border-color: #125bb8;
}
QPushButton#primaryButton:disabled {
    background: #eef1ef;
    border-color: #e0e5e2;
    color: #9aa49f;
}
QPushButton#dangerButton {
    background: #a43e46;
    border-color: #a43e46;
    color: #ffffff;
    font-weight: 650;
}
QPushButton#warningButton {
    background: #b66f20;
    border-color: #b66f20;
    color: #ffffff;
    font-weight: 650;
}
QPushButton#modeButton {
    background: #ffffff;
    border: 1px solid #d8e0e7;
    border-radius: 6px;
    min-height: 48px;
    padding: 7px 12px;
    text-align: left;
}
QPushButton#modeButton:checked {
    background: #e8f1fb;
    border-color: #4b8bd6;
    color: #155ba9;
    font-weight: 650;
}
QPushButton#tunSwitch {
    background: #eef1f3;
    border-color: #cbd4da;
    color: #4f5d67;
    font-weight: 650;
}
QPushButton#tunSwitch:checked {
    background: #1769d2;
    border-color: #1769d2;
    color: white;
}
QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {
    background: #ffffff;
    border: 1px solid #cbd4d0;
    border-radius: 5px;
    selection-background-color: #1769d2;
}
QLineEdit, QComboBox, QSpinBox {
    min-height: 38px;
    padding: 0 9px;
}
QComboBox::drop-down {
    border: none;
    width: 28px;
}
QTextEdit, QPlainTextEdit {
    padding: 9px;
}
QPlainTextEdit#logView {
    background: #111817;
    border-color: #273330;
    color: #c8d4d0;
    font-family: "JetBrains Mono", "Noto Sans Mono", monospace;
    font-size: 12px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border: 1px solid #1769d2;
}
QScrollArea {
    border: none;
    background: transparent;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #bdc8c3;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QCheckBox {
    spacing: 9px;
    min-height: 30px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    background: #ffffff;
    border: 1px solid #9eaaa5;
    border-radius: 4px;
}
QCheckBox::indicator:hover {
    border-color: #4b8bd6;
}
QCheckBox::indicator:checked {
    background: #1769d2;
    border: 4px solid #1769d2;
}
QCheckBox:checked {
    color: #155ba9;
    font-weight: 650;
}
QTabWidget::pane {
    border: none;
}
QTabBar::tab {
    background: transparent;
    border: none;
    color: #687570;
    min-width: 88px;
    padding: 9px 14px;
}
QTabBar::tab:selected {
    color: #147d64;
    border-bottom: 2px solid #147d64;
    font-weight: 650;
}
QToolTip {
    background: #1d2825;
    color: white;
    border: 1px solid #40504b;
    padding: 5px;
}
"""


DARK_STYLE_SHEET = STYLE_SHEET + """
/* smart-box-theme: dark */
QWidget {
    color: #e3e9e6;
}
QMainWindow, QWidget#windowRoot, QStackedWidget#pages {
    background: #151918;
}
QFrame#appHeader {
    background: #1c211f;
    border-bottom-color: #343d39;
}
QLabel#brand {
    color: #69a9ff;
}
QLabel#brandVersion, QLabel#muted, QLabel.muted, QLabel#statLabel {
    color: #9ba8a3;
}
QPushButton#navButton {
    color: #b8c2be;
}
QPushButton#navButton:hover {
    background: #252d2a;
    color: #8fc0ff;
}
QPushButton#navButton:checked {
    background: #243448;
    color: #8fc0ff;
}
QLabel#pageHeading, QLabel#sectionHeading, QLabel#statValue {
    color: #eef3f1;
}
QFrame#toolPanel, QFrame#statCard, QFrame#policyRow, QFrame#editorPanel,
QFrame#controllerBar, QFrame#trafficPanel, QFrame#modeCard {
    background: #202624;
    border-color: #39423f;
}
QFrame#toolPanel {
    background: #1c211f;
}
QLabel#operationBanner {
    background: #243448;
    border-color: #3e5d7f;
    color: #b7d7ff;
}
QPushButton, QToolButton {
    background: #252c29;
    border-color: #46514d;
    color: #e3e9e6;
}
QPushButton:hover, QToolButton:hover {
    background: #303936;
    border-color: #65736d;
}
QPushButton:pressed, QToolButton:pressed {
    background: #18201d;
}
QPushButton:checked, QToolButton:checked {
    background: #29466a;
    border-color: #69a9ff;
    color: #d5e8ff;
}
QPushButton:disabled, QToolButton:disabled {
    background: #1c211f;
    border-color: #303835;
    color: #66716d;
}
QPushButton#primaryButton, QPushButton#tunSwitch:checked {
    background: #367fcf;
    border-color: #4b94e5;
    color: #ffffff;
}
QPushButton#primaryButton:hover {
    background: #438ddd;
    border-color: #69a9ff;
}
QPushButton#primaryButton:disabled {
    background: #202623;
    border-color: #303835;
    color: #66716d;
}
QPushButton#modeButton {
    background: #252c29;
    border-color: #414b47;
}
QPushButton#modeButton:checked {
    background: #29466a;
    border-color: #69a9ff;
    color: #d5e8ff;
}
QPushButton#tunSwitch {
    background: #252c29;
    border-color: #46514d;
    color: #c0cac6;
}
QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {
    background: #171c1a;
    border-color: #46514d;
    color: #e3e9e6;
    selection-background-color: #367fcf;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus,
QPlainTextEdit:focus {
    border-color: #69a9ff;
}
QComboBox QAbstractItemView {
    background: #202624;
    border: 1px solid #46514d;
    color: #e3e9e6;
    selection-background-color: #29466a;
}
QPlainTextEdit#logView {
    background: #0c100f;
    border-color: #303a36;
    color: #cbd7d2;
}
QScrollBar::handle:vertical {
    background: #56635e;
}
QCheckBox::indicator {
    background: #171c1a;
    border-color: #65736d;
}
QCheckBox::indicator:hover {
    border-color: #8fc0ff;
}
QCheckBox::indicator:checked {
    background: #4b94e5;
    border-color: #4b94e5;
}
QCheckBox:checked {
    color: #b7d7ff;
}
QTabBar::tab {
    color: #9ba8a3;
}
QTabBar::tab:selected {
    color: #62c4a6;
    border-bottom-color: #62c4a6;
}
QMenu {
    background: #202624;
    border: 1px solid #46514d;
    color: #e3e9e6;
}
QMenu::item:selected {
    background: #29466a;
}
QStatusBar {
    background: #1c211f;
    color: #d5ddda;
}
QToolTip {
    background: #252c29;
    border-color: #65736d;
    color: #ffffff;
}
"""


def style_sheet_for_theme(theme: str) -> str:
    return DARK_STYLE_SHEET if theme == "dark" else STYLE_SHEET


def palette_for_theme(theme: str) -> QPalette:
    if theme != "dark":
        return QApplication.style().standardPalette()
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#151918",
        QPalette.ColorRole.WindowText: "#e3e9e6",
        QPalette.ColorRole.Base: "#171c1a",
        QPalette.ColorRole.AlternateBase: "#202624",
        QPalette.ColorRole.ToolTipBase: "#252c29",
        QPalette.ColorRole.ToolTipText: "#ffffff",
        QPalette.ColorRole.Text: "#e3e9e6",
        QPalette.ColorRole.Button: "#252c29",
        QPalette.ColorRole.ButtonText: "#e3e9e6",
        QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Highlight: "#367fcf",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.PlaceholderText: "#7f8c87",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    return palette


def apply_application_theme(application: QApplication, theme: str) -> None:
    application.setPalette(palette_for_theme(theme))
    application.setStyleSheet(style_sheet_for_theme(theme))


class TaskSignals(QObject):
    completed = Signal(object, object)


class Task(QRunnable):
    def __init__(self, function: Callable[[], Any]) -> None:
        super().__init__()
        self.function = function
        self.signals = TaskSignals()

    def run(self) -> None:
        try:
            self.signals.completed.emit(self.function(), None)
        except Exception as error:  # noqa: BLE001 - errors are surfaced in the GUI
            error.traceback_text = traceback.format_exc()  # type: ignore[attr-defined]
            self.signals.completed.emit(None, error)


class StateCheckBox(QCheckBox):
    """Checkbox with an explicit mark independent of the desktop theme."""

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        if not self.isChecked():
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        indicator = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, option, self
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ffffff"), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(
            QPointF(indicator.left() + indicator.width() * 0.25, indicator.center().y()),
            QPointF(indicator.left() + indicator.width() * 0.43, indicator.bottom() - 4),
        )
        painter.drawLine(
            QPointF(indicator.left() + indicator.width() * 0.43, indicator.bottom() - 4),
            QPointF(indicator.right() - 3, indicator.top() + 4),
        )
        painter.end()


class StatusPill(QLabel):
    def __init__(self) -> None:
        super().__init__("未运行")
        self.setObjectName("statusPill")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(92)
        self.setFixedHeight(34)
        self.set_state("stopped")

    def set_state(self, state: str) -> None:
        values = {
            "running": ("运行中", "#147d64"),
            "starting": ("正在切换", "#b66f20"),
            "error": ("启动异常", "#a43e46"),
            "stopped": ("未运行", "#4d5b56"),
        }
        text, color = values.get(state, values["stopped"])
        self.setText(text)
        self.setStyleSheet(f"background: {color};")


class StatCard(QFrame):
    def __init__(self, title: str, value: str = "--") -> None:
        super().__init__()
        self.setObjectName("statCard")
        self.setMinimumHeight(92)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 13, 15, 13)
        layout.setSpacing(5)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("statValue")
        title_label = QLabel(title)
        title_label.setObjectName("statLabel")
        layout.addWidget(self.value_label)
        layout.addWidget(title_label)
        self.secondary_label = QLabel("")
        self.secondary_label.setObjectName("muted")
        self.secondary_label.setMinimumHeight(16)
        layout.addWidget(self.secondary_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)

    def set_secondary(self, value: str) -> None:
        self.secondary_label.setText(value)


class TrafficChart(QWidget):
    """Compact rolling upload/download chart for the status page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.upload_samples: list[float] = [0.0]
        self.download_samples: list[float] = [0.0]
        self.theme = "light"
        self.setMinimumHeight(135)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("实时流量曲线")

    def add_sample(self, upload: float, download: float) -> None:
        self.upload_samples.append(max(0.0, upload))
        self.download_samples.append(max(0.0, download))
        self.upload_samples = self.upload_samples[-60:]
        self.download_samples = self.download_samples[-60:]
        self.update()

    def reset(self) -> None:
        self.upload_samples = [0.0]
        self.download_samples = [0.0]
        self.update()

    def set_theme(self, theme: str) -> None:
        self.theme = "dark" if theme == "dark" else "light"
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = self.rect().adjusted(8, 8, -8, -8)
        background = "#111614" if self.theme == "dark" else "#f8fafc"
        grid = "#2c3532" if self.theme == "dark" else "#e1e7ec"
        painter.fillRect(bounds, QColor(background))
        painter.setPen(QColor(grid))
        for index in range(5):
            y = bounds.top() + (bounds.height() * index / 4)
            painter.drawLine(QPointF(bounds.left(), y), QPointF(bounds.right(), y))

        peak = max(1024.0, *self.upload_samples, *self.download_samples)

        def draw_series(samples: list[float], color: str) -> None:
            if len(samples) < 2:
                return
            painter.setPen(QColor(color))
            denominator = max(1, len(samples) - 1)
            points = [
                QPointF(
                    bounds.left() + bounds.width() * index / denominator,
                    bounds.bottom() - bounds.height() * value / peak,
                )
                for index, value in enumerate(samples)
            ]
            for start, end in zip(points, points[1:]):
                painter.drawLine(start, end)

        download_color = "#69a9ff" if self.theme == "dark" else "#1769d2"
        upload_color = "#62c4a6" if self.theme == "dark" else "#16856b"
        draw_series(self.download_samples, download_color)
        draw_series(self.upload_samples, upload_color)
        painter.end()


class DomainConflictDialog(QDialog):
    def __init__(self, conflicts: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("域名名单冲突")
        self.resize(520, 300)
        layout = QVBoxLayout(self)
        heading = QLabel("白名单与黑名单存在重叠")
        heading.setObjectName("sectionHeading")
        layout.addWidget(heading)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText("\n".join(f"{direct}  ↔  {proxy}" for direct, proxy in conflicts))
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def standard_icon(widget: QWidget, pixmap: QStyle.StandardPixmap) -> QIcon:
    return widget.style().standardIcon(pixmap)


def horizontal_rule() -> QFrame:
    rule = QFrame()
    rule.setFrameShape(QFrame.Shape.HLine)
    rule.setStyleSheet("color: #e0e5e2;")
    return rule


def page_scroll(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    return scroll


class MainWindow(QMainWindow):
    def __init__(self, background: bool = False, screenshot_path: str | None = None) -> None:
        super().__init__()
        self.background_requested = background
        self.screenshot_path = screenshot_path
        self.settings = backend.load_settings()
        self.thread_pool = QThreadPool.globalInstance()
        self.tasks: dict[str, Task] = {}
        self.core_generation = 0
        self.core_transaction: tuple[str, int] | None = None
        self.core_active_hint = False
        self.busy_actions: set[str] = set()
        self.last_snapshot: dict[str, Any] = {}
        self.policy_rows: list[tuple[QFrame, QLabel, QComboBox, QLabel, QPushButton]] = []
        self.policy_smart_status: dict[str, dict[str, Any]] = {}
        self.allow_exit = False
        self.closing = False
        self.exiting = False
        self.exit_wait_timer: QTimer | None = None
        self.updating_stack = False
        self.mirror_summary: dict[str, Any] | None = None
        self.mirror_benchmark_busy = False
        self.operation_key: str | None = None
        self.operation_message = "等待操作"
        self.operation_state = "idle"
        self.saved_domain_rules: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
        self.domain_dirty = False
        self.saved_subscription_url = str(self.settings.get("subscription_url", ""))
        self.saved_endpoint_fields: tuple[int, str, int, str] | None = None
        self.endpoint_dirty = False
        self.endpoint_busy = False
        self.updating_mode = False
        self.updating_policies = False
        self.log_auto_refresh_tick = 0
        self.last_log_refresh_summary = "尚未刷新"
        self.last_log_refresh_error = ""
        self.log_content = ""
        self.last_traffic_time: float | None = None
        self.last_upload_total = 0
        self.last_download_total = 0
        self.last_connectivity_check = 0.0
        self.theme = str(self.settings.get("theme", "light"))
        self.tray_retry_attempts = 0
        self.tray_retry_timer: QTimer | None = None

        application = QApplication.instance()
        if isinstance(application, QApplication):
            apply_application_theme(application, self.theme)

        self.setObjectName("mainWindow")
        self.setWindowTitle(f"{APP_TITLE} {backend.APP_VERSION}")
        self.setMinimumSize(920, 660)
        self.resize(1160, 780)
        icon = self.find_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.app_icon = icon

        self.build_ui()
        self.traffic_chart.set_theme(self.theme)
        self.update_theme_accents()
        self.build_tray(icon)
        self.load_settings_into_ui()
        self.load_domain_editors()
        self.set_operation_state(self.operation_message, self.operation_state)

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(POLL_INTERVAL_MS)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start()
        QTimer.singleShot(100, self.poll)
        QTimer.singleShot(300, lambda: self.refresh_policies(silent=True))

        if screenshot_path:
            QTimer.singleShot(1400, self.save_screenshot)
        elif background:
            if self.tray_icon is not None:
                QTimer.singleShot(0, self.hide)
            else:
                self.start_tray_retry()

    def find_icon(self) -> QIcon:
        candidates = [
            Path(__file__).resolve().parent / "icons/smart-box.png",
            Path(__file__).resolve().parent.parent / "icons/smart-box.png",
            Path("/usr/local/share/icons/hicolor/192x192/apps/smart-box.png"),
        ]
        for path in candidates:
            if path.is_file():
                return QIcon(str(path))
        return self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)

    def build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("windowRoot")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("appHeader")
        header.setFixedHeight(66)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(22, 0, 22, 0)
        header_layout.setSpacing(8)
        brand = QLabel("smart-box")
        brand.setObjectName("brand")
        brand.setMinimumWidth(104)
        brand_version = QLabel(f"Linux {backend.APP_VERSION}")
        brand_version.setObjectName("brandVersion")
        header_layout.addWidget(brand)
        header_layout.addWidget(brand_version)
        header_layout.addSpacing(20)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        nav_items = [
            ("状态", QStyle.StandardPixmap.SP_ComputerIcon),
            ("分流策略", QStyle.StandardPixmap.SP_FileDialogDetailedView),
            ("域名名单", QStyle.StandardPixmap.SP_DialogApplyButton),
            ("运行日志", QStyle.StandardPixmap.SP_FileDialogContentsView),
            ("设置", QStyle.StandardPixmap.SP_FileDialogInfoView),
        ]
        for index, (label, icon_id) in enumerate(nav_items):
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setIcon(standard_icon(self, icon_id))
            button.setIconSize(QSize(18, 18))
            button.setShortcut(QKeySequence(f"Ctrl+{index + 1}"))
            button.setToolTip(f"打开{label} · Ctrl+{index + 1}")
            button.clicked.connect(lambda checked=False, page=index: self.select_page(page))
            self.nav_group.addButton(button, index)
            header_layout.addWidget(button)
            if index == 0:
                button.setChecked(True)
        header_layout.addStretch(1)
        self.top_activity = QLabel("")
        self.top_activity.setObjectName("muted")
        self.top_activity.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status_pill = StatusPill()
        header_layout.addWidget(self.top_activity)
        header_layout.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(header)

        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        self.pages.addWidget(self.build_status_page())
        self.pages.addWidget(self.build_policy_page())
        self.pages.addWidget(self.build_domain_page())
        self.pages.addWidget(self.build_logs_page())
        self.pages.addWidget(self.build_settings_page())
        outer.addWidget(self.pages, 1)

        self.find_action = QAction("查找当前页面", self)
        self.find_action.setShortcut(QKeySequence.StandardKey.Find)
        self.find_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.find_action.triggered.connect(self.focus_current_filter)
        self.addAction(self.find_action)

    def content_widget(self) -> tuple[QWidget, QVBoxLayout]:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(27, 23, 27, 27)
        layout.setSpacing(16)
        return content, layout

    def build_status_page(self) -> QWidget:
        content, layout = self.content_widget()

        control = QFrame()
        control.setObjectName("controllerBar")
        control_layout = QHBoxLayout(control)
        control_layout.setContentsMargins(18, 14, 18, 14)
        control_layout.setSpacing(10)
        state_column = QVBoxLayout()
        state_column.setSpacing(3)
        self.core_state_label = QLabel("代理核心未运行")
        self.core_state_label.setObjectName("sectionHeading")
        self.interface_state_label = QLabel("SmartBox TUN 未建立")
        self.interface_state_label.setObjectName("muted")
        state_column.addWidget(self.core_state_label)
        state_column.addWidget(self.interface_state_label)
        control_layout.addLayout(state_column, 1)
        self.restart_button = QPushButton("重启")
        self.restart_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_BrowserReload))
        self.restart_button.clicked.connect(self.restart_service)
        self.restart_button.setEnabled(False)
        control_layout.addWidget(self.restart_button)
        self.connectivity_button = QPushButton("立即验网")
        self.connectivity_button.setIcon(
            standard_icon(self, QStyle.StandardPixmap.SP_DriveNetIcon)
        )
        self.connectivity_button.setToolTip(
            "检查国内、基础公网、Smart 代理、GitHub 和 Telegram 五条路径"
        )
        self.connectivity_button.clicked.connect(
            lambda: self.check_connectivity_background(manual=True)
        )
        control_layout.addWidget(self.connectivity_button)
        log_button = QPushButton("日志")
        log_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_FileDialogContentsView))
        log_button.clicked.connect(lambda: self.select_page(3))
        control_layout.addWidget(log_button)
        self.power_button = QPushButton("开启 TUN")
        self.power_button.setObjectName("tunSwitch")
        self.power_button.setCheckable(True)
        self.power_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaPlay))
        self.power_button.setMinimumWidth(132)
        self.power_button.clicked.connect(self.toggle_service)
        control_layout.addWidget(self.power_button)
        layout.addWidget(control)

        self.operation_banner = QLabel("就绪：等待操作")
        self.operation_banner.setObjectName("operationBanner")
        self.operation_banner.setWordWrap(True)
        layout.addWidget(self.operation_banner)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.upload_card = StatCard("实时上传")
        self.download_card = StatCard("实时下载")
        self.connection_card = StatCard("活动连接")
        self.memory_card = StatCard("内存占用")
        self.upload_card.set_secondary("累计 --")
        self.download_card.set_secondary("累计 --")
        self.connection_card.set_secondary("核心实时统计")
        self.memory_card.set_secondary(f"core {backend.CORE_VERSION}")
        for card in (
            self.upload_card,
            self.download_card,
            self.connection_card,
            self.memory_card,
        ):
            stats.addWidget(card, 1)
        layout.addLayout(stats)

        traffic_panel = QFrame()
        traffic_panel.setObjectName("trafficPanel")
        traffic_layout = QVBoxLayout(traffic_panel)
        traffic_layout.setContentsMargins(16, 12, 16, 14)
        traffic_layout.setSpacing(8)
        traffic_header = QHBoxLayout()
        traffic_title = QLabel("实时流量")
        traffic_title.setObjectName("sectionHeading")
        self.download_legend = QLabel("下载")
        self.upload_legend = QLabel("上传")
        traffic_header.addWidget(traffic_title)
        traffic_header.addStretch(1)
        traffic_header.addWidget(self.download_legend)
        traffic_header.addSpacing(12)
        traffic_header.addWidget(self.upload_legend)
        traffic_layout.addLayout(traffic_header)
        self.traffic_chart = TrafficChart()
        traffic_layout.addWidget(self.traffic_chart)
        layout.addWidget(traffic_panel)

        mode_card = QFrame()
        mode_card.setObjectName("modeCard")
        mode_card_layout = QVBoxLayout(mode_card)
        mode_card_layout.setContentsMargins(16, 13, 16, 15)
        mode_card_layout.setSpacing(10)
        mode_label = QLabel("运行模式")
        mode_label.setObjectName("sectionHeading")
        mode_card_layout.addWidget(mode_label)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(9)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QPushButton] = {}
        for mode in backend.VALID_MODES:
            title, summary, tooltip = mode_presentation(mode)
            button = QPushButton(f"{title}\n{summary}")
            button.setObjectName("modeButton")
            button.setCheckable(True)
            button.setFixedHeight(64)
            button.setToolTip(tooltip)
            button.setAccessibleName(f"{title}：{summary}")
            button.setProperty("modeValue", mode)
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            button.clicked.connect(lambda checked=False, selected=mode: self.change_mode(selected))
            mode_row.addWidget(button, 1)
        mode_card_layout.addLayout(mode_row)
        layout.addWidget(mode_card)

        details = QFrame()
        details.setObjectName("toolPanel")
        details_layout = QGridLayout(details)
        details_layout.setContentsMargins(20, 17, 20, 17)
        details_layout.setHorizontalSpacing(26)
        details_layout.setVerticalSpacing(13)
        labels = (
            ("本地混合代理", backend.MIXED_ADDRESS),
            ("控制接口", "127.0.0.1:20809"),
            ("配置更新时间", "尚未拉取"),
            ("当前网络接管", "未检测"),
            ("配置节点", "--"),
            ("联网验收", "尚未验证"),
        )
        self.detail_values: dict[str, QLabel] = {}
        for index, (name, value) in enumerate(labels):
            row, column = divmod(index, 2)
            item = QVBoxLayout()
            item.setSpacing(3)
            name_label = QLabel(name)
            name_label.setObjectName("statLabel")
            value_label = QLabel(value)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            item.addWidget(name_label)
            item.addWidget(value_label)
            details_layout.addLayout(item, row, column)
            self.detail_values[name] = value_label
        layout.addWidget(details)

        action_row = QHBoxLayout()
        self.pull_quick_button = QPushButton("拉取并校验")
        self.pull_quick_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_BrowserReload))
        self.pull_quick_button.clicked.connect(self.pull_profile)
        policy_button = QPushButton("刷新策略")
        policy_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_FileDialogDetailedView))
        policy_button.clicked.connect(self.refresh_policies)
        action_row.addWidget(self.pull_quick_button)
        action_row.addWidget(policy_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        layout.addStretch(1)
        return page_scroll(content)

    def build_policy_page(self) -> QWidget:
        content, layout = self.content_widget()
        toolbar = QHBoxLayout()
        self.policy_search = QLineEdit()
        self.policy_search.setPlaceholderText("搜索策略、当前节点或测速状态")
        self.policy_search.setClearButtonEnabled(True)
        self.policy_search.setMinimumWidth(280)
        self.policy_search.setMaximumWidth(380)
        self.policy_search.setAccessibleName("筛选分流策略")
        self.policy_search.textChanged.connect(self.filter_policies)
        refresh = QPushButton("刷新")
        refresh.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_BrowserReload))
        refresh.clicked.connect(self.refresh_policies)
        self.policy_source_label = QLabel("等待配置")
        self.policy_source_label.setObjectName("muted")
        self.policy_filter_status = QLabel("0 项策略")
        self.policy_filter_status.setObjectName("muted")
        self.policy_filter_status.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        toolbar.addWidget(self.policy_search)
        toolbar.addWidget(refresh)
        toolbar.addStretch(1)
        toolbar.addWidget(self.policy_filter_status)
        toolbar.addSpacing(12)
        toolbar.addWidget(self.policy_source_label)
        layout.addLayout(toolbar)

        self.policy_scroll = QScrollArea()
        self.policy_scroll.setWidgetResizable(True)
        self.policy_container = QWidget()
        self.policy_layout = QVBoxLayout(self.policy_container)
        self.policy_layout.setContentsMargins(0, 0, 5, 0)
        self.policy_layout.setSpacing(7)
        self.policy_layout.addStretch(1)
        self.policy_scroll.setWidget(self.policy_container)
        layout.addWidget(self.policy_scroll, 1)
        return content

    def build_domain_page(self) -> QWidget:
        content, layout = self.content_widget()
        editors = QHBoxLayout()
        editors.setSpacing(14)

        allow_panel = QFrame()
        allow_panel.setObjectName("editorPanel")
        allow_layout = QVBoxLayout(allow_panel)
        allow_layout.setContentsMargins(16, 15, 16, 16)
        allow_header = QHBoxLayout()
        allow_title = QLabel("白名单 · 强制直连")
        allow_title.setObjectName("sectionHeading")
        self.allow_count = QLabel("0")
        self.allow_count.setObjectName("muted")
        self.allow_count.setToolTip("重复规则和已被父域覆盖的子域会自动合并")
        allow_header.addWidget(allow_title)
        allow_header.addStretch(1)
        allow_header.addWidget(self.allow_count)
        self.allow_editor = QTextEdit()
        self.allow_editor.setAcceptRichText(False)
        self.allow_editor.setTabChangesFocus(True)
        self.allow_editor.setPlaceholderText("example.cn\ninternal.example.com")
        self.allow_editor.setAccessibleName("强制直连域名白名单")
        self.allow_editor.setAccessibleDescription("每行一个域名；按 Tab 移动到下一个控件")
        self.allow_editor.textChanged.connect(self.update_domain_counts)
        allow_layout.addLayout(allow_header)
        allow_layout.addWidget(self.allow_editor)
        editors.addWidget(allow_panel, 1)

        proxy_panel = QFrame()
        proxy_panel.setObjectName("editorPanel")
        proxy_layout = QVBoxLayout(proxy_panel)
        proxy_layout.setContentsMargins(16, 15, 16, 16)
        proxy_header = QHBoxLayout()
        proxy_title = QLabel("黑名单 · 强制 Smart")
        proxy_title.setObjectName("sectionHeading")
        self.proxy_count = QLabel("0")
        self.proxy_count.setObjectName("muted")
        self.proxy_count.setToolTip("重复规则和已被父域覆盖的子域会自动合并")
        proxy_header.addWidget(proxy_title)
        proxy_header.addStretch(1)
        proxy_header.addWidget(self.proxy_count)
        self.proxy_editor = QTextEdit()
        self.proxy_editor.setAcceptRichText(False)
        self.proxy_editor.setTabChangesFocus(True)
        self.proxy_editor.setPlaceholderText("example.com\n*.service.example.net")
        self.proxy_editor.setAccessibleName("强制 Smart 域名黑名单")
        self.proxy_editor.setAccessibleDescription("每行一个域名；按 Tab 移动到下一个控件")
        self.proxy_editor.textChanged.connect(self.update_domain_counts)
        proxy_layout.addLayout(proxy_header)
        proxy_layout.addWidget(self.proxy_editor)
        editors.addWidget(proxy_panel, 1)
        layout.addLayout(editors, 1)

        action_row = QHBoxLayout()
        self.domain_reset_button = QPushButton("恢复已保存内容")
        self.domain_reset_button.setIcon(
            standard_icon(self, QStyle.StandardPixmap.SP_ArrowBack)
        )
        self.domain_reset_button.clicked.connect(self.load_domain_editors)
        self.domain_save_button = QPushButton("保存并应用")
        self.domain_save_button.setObjectName("primaryButton")
        self.domain_save_button.setIcon(
            standard_icon(self, QStyle.StandardPixmap.SP_DialogSaveButton)
        )
        self.domain_save_button.clicked.connect(self.save_domain_rules)
        self.domain_validation_label = QLabel("")
        self.domain_validation_label.setObjectName("muted")
        action_row.addWidget(self.domain_reset_button)
        action_row.addStretch(1)
        action_row.addWidget(self.domain_validation_label)
        action_row.addWidget(self.domain_save_button)
        layout.addLayout(action_row)
        return content

    def build_logs_page(self) -> QWidget:
        content, layout = self.content_widget()
        toolbar = QHBoxLayout()
        self.log_refresh_button = QPushButton("刷新")
        self.log_refresh_button.setIcon(
            standard_icon(self, QStyle.StandardPixmap.SP_BrowserReload)
        )
        self.log_refresh_button.clicked.connect(self.refresh_logs)
        self.log_clear_button = QPushButton("清空视图")
        self.log_clear_button.setIcon(
            standard_icon(self, QStyle.StandardPixmap.SP_TrashIcon)
        )
        self.log_clear_button.clicked.connect(self.clear_log_view)
        self.log_copy_button = QPushButton("复制结果")
        self.log_copy_button.setIcon(
            standard_icon(self, QStyle.StandardPixmap.SP_FileIcon)
        )
        self.log_copy_button.setToolTip("复制当前筛选后可见的日志行")
        self.log_copy_button.setEnabled(False)
        self.log_copy_button.clicked.connect(self.copy_visible_logs)
        self.live_logs = StateCheckBox("自动刷新")
        self.live_logs.setChecked(bool(self.settings.get("log_auto_refresh", True)))
        self.live_logs.toggled.connect(self.toggle_log_auto_refresh)
        self.log_refresh_status = QLabel("")
        self.log_refresh_status.setObjectName("muted")
        self.log_refresh_status.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        toolbar.addWidget(self.log_refresh_button)
        toolbar.addWidget(self.log_clear_button)
        toolbar.addWidget(self.log_copy_button)
        toolbar.addWidget(self.live_logs)
        toolbar.addStretch(1)
        toolbar.addWidget(self.log_refresh_status)
        layout.addLayout(toolbar)
        search_row = QHBoxLayout()
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("搜索日志，例如 error、DNS、TUN")
        self.log_search.setClearButtonEnabled(True)
        self.log_search.setAccessibleName("筛选运行日志")
        self.log_search.textChanged.connect(self.filter_logs)
        self.log_filter_status = QLabel("0 行")
        self.log_filter_status.setObjectName("muted")
        self.log_filter_status.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        search_row.addWidget(self.log_search, 1)
        search_row.addWidget(self.log_filter_status)
        layout.addLayout(search_row)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.log_view, 1)
        self.update_log_refresh_status()
        return content

    def build_settings_page(self) -> QWidget:
        content, layout = self.content_widget()

        endpoint = QFrame()
        endpoint.setObjectName("toolPanel")
        endpoint_layout = QVBoxLayout(endpoint)
        endpoint_layout.setContentsMargins(18, 16, 18, 18)
        endpoint_layout.setSpacing(11)
        endpoint_title = QLabel("订阅转换")
        endpoint_title.setObjectName("sectionHeading")
        endpoint_layout.addWidget(endpoint_title)

        fields = QHBoxLayout()
        fields.setSpacing(10)
        protocol_column = QVBoxLayout()
        protocol_column.setSpacing(4)
        protocol_label = QLabel("协议")
        protocol_label.setObjectName("muted")
        self.protocol_box = QComboBox()
        self.protocol_box.addItems(["HTTP", "HTTPS"])
        self.protocol_box.setFixedWidth(105)
        self.protocol_box.setAccessibleName("协议")
        protocol_label.setBuddy(self.protocol_box)
        protocol_column.addWidget(protocol_label)
        protocol_column.addWidget(self.protocol_box)
        fields.addLayout(protocol_column)

        host_column = QVBoxLayout()
        host_column.setSpacing(4)
        host_label = QLabel("域名或 IP")
        host_label.setObjectName("muted")
        self.host_edit = QLineEdit()
        self.host_edit.setAccessibleName("域名或 IP")
        host_label.setBuddy(self.host_edit)
        host_column.addWidget(host_label)
        host_column.addWidget(self.host_edit)
        fields.addLayout(host_column, 1)

        port_column = QVBoxLayout()
        port_column.setSpacing(4)
        port_label = QLabel("端口")
        port_label.setObjectName("muted")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setFixedWidth(112)
        self.port_spin.setAccessibleName("端口")
        port_label.setBuddy(self.port_spin)
        port_column.addWidget(port_label)
        port_column.addWidget(self.port_spin)
        fields.addLayout(port_column)
        endpoint_layout.addLayout(fields)

        path_label = QLabel("私密订阅路径")
        path_label.setObjectName("muted")
        self.path_edit = QLineEdit()
        self.path_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.path_edit.setClearButtonEnabled(True)
        self.path_edit.setAccessibleName("私密订阅路径")
        path_label.setBuddy(self.path_edit)
        self.protocol_box.currentIndexChanged.connect(self.update_endpoint_state)
        self.host_edit.textChanged.connect(self.update_endpoint_state)
        self.port_spin.valueChanged.connect(self.update_endpoint_state)
        self.path_edit.textChanged.connect(self.update_endpoint_state)
        self.reveal_action = self.path_edit.addAction(
            standard_icon(self, QStyle.StandardPixmap.SP_DialogOpenButton),
            QLineEdit.ActionPosition.TrailingPosition,
        )
        self.reveal_action.setToolTip("显示或隐藏私密路径")
        self.reveal_action.triggered.connect(self.toggle_path_visibility)
        self.path_visibility_timer = QTimer(self)
        self.path_visibility_timer.setSingleShot(True)
        self.path_visibility_timer.setInterval(15_000)
        self.path_visibility_timer.timeout.connect(
            lambda: self.set_path_visible(False)
        )
        self.set_path_visible(False)
        endpoint_layout.addWidget(path_label)
        endpoint_layout.addWidget(self.path_edit)

        endpoint_actions = QHBoxLayout()
        self.save_endpoint_button = QPushButton("仅保存")
        self.save_endpoint_button.setIcon(
            standard_icon(self, QStyle.StandardPixmap.SP_DialogSaveButton)
        )
        self.save_endpoint_button.clicked.connect(self.save_endpoint)
        self.pull_button = QPushButton("保存并拉取")
        self.pull_button.setObjectName("primaryButton")
        self.pull_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_BrowserReload))
        self.pull_button.clicked.connect(self.pull_profile)
        self.profile_status_label = QLabel("尚未拉取配置")
        self.profile_status_label.setObjectName("muted")
        self.endpoint_validation_label = QLabel("")
        self.endpoint_validation_label.setObjectName("muted")
        endpoint_actions.addWidget(self.save_endpoint_button)
        endpoint_actions.addWidget(self.pull_button)
        endpoint_actions.addWidget(self.endpoint_validation_label)
        endpoint_actions.addStretch(1)
        endpoint_actions.addWidget(self.profile_status_label)
        endpoint_layout.addLayout(endpoint_actions)
        layout.addWidget(endpoint)

        options = QFrame()
        options.setObjectName("toolPanel")
        options_layout = QVBoxLayout(options)
        options_layout.setContentsMargins(18, 15, 18, 17)
        options_layout.setSpacing(13)
        options_title = QLabel("本机行为")
        options_title.setObjectName("sectionHeading")
        self.autostart_check = StateCheckBox("登录时打开客户端")
        self.autostart_check.toggled.connect(self.toggle_autostart)
        self.night_mode_check = StateCheckBox("夜间模式")
        self.night_mode_check.toggled.connect(self.toggle_night_mode)
        stack_row = QHBoxLayout()
        stack_label = QLabel("TUN 栈")
        self.stack_box = QComboBox()
        self.stack_box.addItem("System", "system")
        self.stack_box.addItem("Mixed", "mixed")
        self.stack_box.addItem("gVisor", "gvisor")
        self.stack_box.setFixedWidth(150)
        self.stack_box.setToolTip("更改后在下次启动或重启核心时生效")
        self.stack_box.currentIndexChanged.connect(self.save_stack_setting)
        stack_row.addWidget(stack_label)
        stack_row.addStretch(1)
        stack_row.addWidget(self.stack_box)
        options_layout.addWidget(options_title)
        options_layout.addWidget(self.autostart_check)
        options_layout.addWidget(self.night_mode_check)
        options_layout.addLayout(stack_row)
        layout.addWidget(options)

        mirrors = QFrame()
        mirrors.setObjectName("toolPanel")
        mirrors_layout = QVBoxLayout(mirrors)
        mirrors_layout.setContentsMargins(18, 15, 18, 17)
        mirrors_layout.setSpacing(10)
        mirrors_title = QLabel("系统源测速")
        mirrors_title.setObjectName("sectionHeading")
        mirrors_layout.addWidget(mirrors_title)
        mirrors_hint = QLabel(
            "pacman 与 paru 的官方包共用 Arch 源；CachyOS 源单独测速。测速只读，应用排序需要 root 授权。"
        )
        mirrors_hint.setObjectName("muted")
        mirrors_hint.setWordWrap(True)
        mirrors_layout.addWidget(mirrors_hint)
        mirrors_row = QHBoxLayout()
        mirrors_row.setSpacing(9)
        self.mirror_repo_box = QComboBox()
        self.mirror_repo_box.addItem("pacman / paru 官方源", "arch")
        self.mirror_repo_box.addItem("CachyOS 源", "cachyos")
        self.mirror_repo_box.setFixedWidth(220)
        self.mirror_repo_box.currentIndexChanged.connect(
            self.mirror_repository_changed
        )
        self.mirror_probe_button = QPushButton("测速")
        self.mirror_probe_button.setIcon(
            standard_icon(self, QStyle.StandardPixmap.SP_BrowserReload)
        )
        self.mirror_probe_button.clicked.connect(self.benchmark_mirrors)
        self.mirror_apply_button = QPushButton("应用最快源")
        self.mirror_apply_button.setIcon(
            standard_icon(self, QStyle.StandardPixmap.SP_DialogApplyButton)
        )
        self.mirror_apply_button.setEnabled(False)
        self.mirror_apply_button.clicked.connect(self.apply_mirror_ranking)
        self.mirror_status_label = QLabel("尚未测速")
        self.mirror_status_label.setObjectName("muted")
        self.mirror_status_label.setWordWrap(True)
        mirrors_row.addWidget(self.mirror_repo_box)
        mirrors_row.addWidget(self.mirror_probe_button)
        mirrors_row.addWidget(self.mirror_apply_button)
        mirrors_row.addWidget(self.mirror_status_label, 1)
        mirrors_layout.addLayout(mirrors_row)
        layout.addWidget(mirrors)

        maintenance = QFrame()
        maintenance.setObjectName("toolPanel")
        maintenance_layout = QVBoxLayout(maintenance)
        maintenance_layout.setContentsMargins(18, 15, 18, 17)
        maintenance_layout.setSpacing(12)
        maintenance_title = QLabel("本机组件")
        maintenance_title.setObjectName("sectionHeading")
        maintenance_layout.addWidget(maintenance_title)
        info_grid = QGridLayout()
        info_grid.setHorizontalSpacing(20)
        info_grid.setVerticalSpacing(7)
        info_values = [
            ("代理核心", str(backend.find_core())),
            ("配置目录", str(backend.CONFIG_DIR)),
            ("状态目录", str(backend.STATE_DIR)),
            ("用户服务", backend.SERVICE_UNIT),
        ]
        for row, (key, value) in enumerate(info_values):
            key_label = QLabel(key)
            key_label.setObjectName("muted")
            value_label = QLabel(value)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value_label.setWordWrap(True)
            info_grid.addWidget(key_label, row, 0)
            info_grid.addWidget(value_label, row, 1)
        maintenance_layout.addLayout(info_grid)
        maintenance_actions = QHBoxLayout()
        validate = QPushButton("校验运行配置")
        validate.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_DialogApplyButton))
        validate.clicked.connect(self.validate_runtime)
        open_data = QPushButton("打开配置目录")
        open_data.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_DirOpenIcon))
        open_data.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(backend.CONFIG_DIR)))
        )
        copy_proxy = QPushButton("复制代理地址")
        copy_proxy.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_FileIcon))
        copy_proxy.clicked.connect(self.copy_proxy_address)
        maintenance_actions.addWidget(validate)
        maintenance_actions.addWidget(open_data)
        maintenance_actions.addWidget(copy_proxy)
        maintenance_actions.addStretch(1)
        maintenance_layout.addLayout(maintenance_actions)
        layout.addWidget(maintenance)
        layout.addStretch(1)
        return page_scroll(content)

    def build_tray(self, icon: QIcon) -> bool:
        self.tray_icon: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable() or self.screenshot_path:
            return False
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip("smart-box")
        menu = QMenu()
        show_action = QAction("显示 smart-box", menu)
        show_action.triggered.connect(self.show_from_tray)
        toggle_action = QAction("启动 / 停止", menu)
        toggle_action.triggered.connect(self.toggle_service)
        policies_action = QAction("分流策略", menu)
        policies_action.triggered.connect(lambda: self.show_page_from_tray(1))
        quit_action = QAction("退出客户端", menu)
        quit_action.triggered.connect(self.exit_application)
        menu.addAction(show_action)
        menu.addAction(toggle_action)
        menu.addAction(policies_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self.tray_activated)
        tray.show()
        self.tray_icon = tray
        return True

    def start_tray_retry(self) -> None:
        """Wait briefly for Plasma's notification area during session startup."""
        if self.screenshot_path or self.tray_icon is not None:
            return
        if self.tray_retry_timer is None:
            self.tray_retry_timer = QTimer(self)
            self.tray_retry_timer.setInterval(TRAY_RETRY_INTERVAL_MS)
            self.tray_retry_timer.timeout.connect(self.retry_tray)
        self.tray_retry_attempts = 0
        self.tray_retry_timer.start()

    def retry_tray(self) -> None:
        if self.tray_icon is not None:
            if self.tray_retry_timer is not None:
                self.tray_retry_timer.stop()
            return
        self.tray_retry_attempts += 1
        if self.build_tray(self.app_icon):
            if self.tray_retry_timer is not None:
                self.tray_retry_timer.stop()
            if self.background_requested:
                self.hide()
            return
        if self.tray_retry_attempts >= TRAY_RETRY_LIMIT:
            if self.tray_retry_timer is not None:
                self.tray_retry_timer.stop()
            if self.background_requested:
                self.show()
                self.notify("系统托盘未就绪，已显示主窗口", error=True)

    def select_page(self, index: int) -> None:
        if index != 4 and hasattr(self, "path_edit"):
            self.set_path_visible(False)
        self.pages.setCurrentIndex(index)
        button = self.nav_group.button(index)
        if button is not None:
            button.setChecked(True)
        if index == 1:
            self.refresh_policies()
        elif index == 3:
            self.refresh_logs()

    def show_page_from_tray(self, index: int) -> None:
        self.show_from_tray()
        self.select_page(index)

    def focus_current_filter(self) -> None:
        target = {
            1: self.policy_search,
            3: self.log_search,
        }.get(self.pages.currentIndex())
        if target is None:
            self.statusBar().showMessage("当前页面没有搜索框", 2500)
            return
        target.setFocus(Qt.FocusReason.ShortcutFocusReason)
        target.selectAll()

    def run_task(
        self,
        key: str,
        function: Callable[[], Any],
        callback: Callable[[Any, Exception | None], None],
        activity: str = "",
    ) -> bool:
        if self.exiting or self.closing:
            return False
        if key in self.tasks:
            return False
        task = Task(function)
        self.tasks[key] = task
        if activity:
            self.top_activity.setText(activity)
            self.operation_key = key
            self.set_operation_state(activity.rstrip("…"), "working")

        def completed(result: Any, error: Exception | None) -> None:
            self.tasks.pop(key, None)
            if activity and self.top_activity.text() == activity:
                self.top_activity.clear()
            callback(result, error)
            if activity and self.operation_key == key:
                label = activity.rstrip("…")
                if label.startswith("正在"):
                    label = label[2:]
                self.set_operation_state(
                    f"{label}{'失败' if error else '已完成'}",
                    "error" if error else "success",
                )
                self.operation_key = None

        task.signals.completed.connect(completed)
        self.thread_pool.start(task)
        return True

    def run_core_task(
        self,
        key: str,
        function: Callable[[], Any],
        callback: Callable[[Any, Exception | None], None],
        activity: str = "",
    ) -> bool:
        """Serialize live-core mutations and invalidate older network probes."""
        if self.core_transaction is not None:
            return False
        generation = self.core_generation + 1
        transaction = (key, generation)
        self.core_generation = generation
        self.core_transaction = transaction
        self.refresh_core_action_availability()

        def finished(result: Any, error: Exception | None) -> None:
            try:
                callback(result, error)
            finally:
                if self.core_transaction == transaction:
                    self.core_transaction = None
                self.refresh_core_action_availability()

        try:
            started = self.run_task(key, function, finished, activity)
        except Exception:
            if self.core_transaction == transaction:
                self.core_transaction = None
            self.refresh_core_action_availability()
            raise
        if not started and self.core_transaction == transaction:
            self.core_transaction = None
            self.refresh_core_action_availability()
        return started

    def core_operation_available(self) -> bool:
        if self.core_transaction is None:
            return True
        self.statusBar().showMessage("已有核心操作进行中，请等待完成", 3500)
        return False

    def refresh_core_action_availability(self) -> None:
        """Recompute every control that can mutate or inspect the live core."""
        core_busy = self.core_transaction is not None
        service_busy = "service-switch" in self.tasks
        self.power_button.setEnabled(not core_busy and not service_busy)
        self.restart_button.setEnabled(
            not core_busy and not service_busy and self.core_active_hint
        )
        mode_busy = "mode-change" in self.tasks
        for button in self.mode_buttons.values():
            button.setEnabled(not core_busy and not mode_busy)
        for _row, label, combo, _delay_label, probe_button in self.policy_rows:
            name = label.text()
            combo.setEnabled(
                not core_busy and f"policy:{name}" not in self.tasks
            )
            probe_button.setEnabled(
                not core_busy
                and bool(probe_button.property("smart_box_online"))
                and f"policy-delay:{name}" not in self.tasks
            )
        self.stack_box.setEnabled(
            not core_busy and "stack-change" not in self.tasks
        )
        self.update_domain_counts()
        self.update_endpoint_state()

    def set_operation_state(self, message: str, state: str = "success") -> None:
        self.operation_message = message
        self.operation_state = state
        values = (
            {
                "working": ("处理中", "#29394d", "#456485", "#c5ddfb"),
                "success": ("完成", "#183a30", "#326b59", "#a7e0cc"),
                "error": ("失败", "#45252a", "#78434b", "#f1bdc3"),
                "idle": ("就绪", "#252c29", "#46514d", "#b8c2be"),
            }
            if self.theme == "dark"
            else {
                "working": ("处理中", "#e8f1fb", "#bad2ee", "#245b91"),
                "success": ("完成", "#e7f5ef", "#a9d8c8", "#176b56"),
                "error": ("失败", "#fbecee", "#e3b6bc", "#963741"),
                "idle": ("就绪", "#f0f3f5", "#d6dde2", "#52616c"),
            }
        )
        prefix, background, border, color = values.get(state, values["success"])
        self.operation_banner.setText(f"{prefix}：{message}")
        self.operation_banner.setStyleSheet(
            f"background: {background}; border: 1px solid {border}; "
            f"border-radius: 5px; color: {color}; min-height: 34px; padding: 0 12px;"
        )

    def set_label_state(self, label: QLabel, state: str = "default") -> None:
        label.setProperty("semanticState", state)
        colors = (
            {
                "success": "#62c4a6",
                "warning": "#e7bd78",
                "error": "#f1bdc3",
                "muted": "#9ba8a3",
            }
            if self.theme == "dark"
            else {
                "success": "#147d64",
                "warning": "#8a5a00",
                "error": "#a43e46",
                "muted": "#52616c",
            }
        )
        color = colors.get(state)
        label.setStyleSheet(f"color: {color};" if color else "")

    def update_theme_accents(self) -> None:
        download = "#69a9ff" if self.theme == "dark" else "#1769d2"
        upload = "#62c4a6" if self.theme == "dark" else "#16856b"
        self.download_legend.setStyleSheet(f"color: {download}; font-weight: 650;")
        self.upload_legend.setStyleSheet(f"color: {upload}; font-weight: 650;")
        for label in self.findChildren(QLabel):
            state = label.property("semanticState")
            if isinstance(state, str) and state:
                self.set_label_state(label, state)

    def notify(self, message: str, error: bool = False) -> None:
        self.operation_key = None
        self.set_operation_state(message, "error" if error else "success")
        self.statusBar().setStyleSheet(
            "QStatusBar { background: %s; color: white; padding: 3px 10px; }"
            % ("#a43e46" if error else "#263b35")
        )
        self.statusBar().showMessage(message, 5000)
        if self.tray_icon is not None and self.isHidden():
            icon = (
                QSystemTrayIcon.MessageIcon.Critical
                if error
                else QSystemTrayIcon.MessageIcon.Information
            )
            self.tray_icon.showMessage("smart-box", message, icon, 3500)

    def poll(self) -> None:
        self.run_task("status", backend.status_snapshot, self.status_updated)
        if self.pages.currentIndex() == 3 and self.live_logs.isChecked():
            self.log_auto_refresh_tick += 1
            if self.log_auto_refresh_tick >= 2:
                self.log_auto_refresh_tick = 0
                self.refresh_logs()

    def status_updated(self, result: Any, error: Exception | None) -> None:
        if error is not None or not isinstance(result, dict):
            return
        self.last_snapshot = result
        active = bool(result.get("active"))
        self.core_active_hint = active
        api = bool(result.get("api"))
        telemetry = bool(result.get("telemetry"))
        tun = bool(result.get("tun"))
        flclash = bool(result.get("flclash"))
        if active and api and tun:
            self.status_pill.set_state("running")
            self.core_state_label.setText("代理核心运行正常")
            self.interface_state_label.setText("SmartBox TUN 已接管网络")
        elif active:
            self.status_pill.set_state("error")
            self.core_state_label.setText("代理核心正在启动")
            self.interface_state_label.setText("等待控制接口与 TUN")
        else:
            self.status_pill.set_state("stopped")
            self.core_state_label.setText("代理核心未运行")
            self.interface_state_label.setText(
                "FlClash 当前接管网络" if flclash else "SmartBox TUN 未建立"
            )

        if "service-switch" not in self.tasks:
            self.power_button.blockSignals(True)
            self.power_button.setChecked(active)
            self.power_button.blockSignals(False)
            self.power_button.setText("关闭 TUN" if active else "开启 TUN")
            self.power_button.setIcon(
                standard_icon(
                    self,
                    QStyle.StandardPixmap.SP_MediaStop
                    if active
                    else QStyle.StandardPixmap.SP_MediaPlay,
                )
            )
            self.power_button.setEnabled(self.core_transaction is None)
            self.restart_button.setEnabled(
                active and self.core_transaction is None
            )
            self.power_button.style().unpolish(self.power_button)
            self.power_button.style().polish(self.power_button)

        upload_total = int(result.get("upload", 0))
        download_total = int(result.get("download", 0))
        now = time.monotonic()
        upload_rate = 0.0
        download_rate = 0.0
        if active and api and telemetry:
            self.connection_card.set_value(str(result.get("connections", 0)))
            self.connection_card.set_secondary("核心实时统计")
            if self.last_traffic_time is not None:
                elapsed = max(0.001, now - self.last_traffic_time)
                if upload_total >= self.last_upload_total:
                    upload_rate = (upload_total - self.last_upload_total) / elapsed
                if download_total >= self.last_download_total:
                    download_rate = (download_total - self.last_download_total) / elapsed
            self.last_traffic_time = now
            self.last_upload_total = upload_total
            self.last_download_total = download_total
            self.traffic_chart.add_sample(upload_rate, download_rate)
            self.upload_card.set_value(f"{backend.format_bytes(int(upload_rate))}/s")
            self.upload_card.set_secondary(f"累计 {backend.format_bytes(upload_total)}")
            self.download_card.set_value(f"{backend.format_bytes(int(download_rate))}/s")
            self.download_card.set_secondary(f"累计 {backend.format_bytes(download_total)}")
        elif active and api:
            # Keep the last valid counters and chart baseline.  The API returns
            # zero-filled placeholders when /connections briefly fails; using
            # them would turn the next successful total into a giant rate spike.
            self.connection_card.set_value("--")
            self.connection_card.set_secondary("核心遥测暂不可用")
            self.upload_card.set_value("--/s")
            self.download_card.set_value("--/s")
            if self.last_traffic_time is None:
                self.upload_card.set_secondary("等待核心遥测")
                self.download_card.set_secondary("等待核心遥测")
            else:
                self.upload_card.set_secondary(
                    f"累计 {backend.format_bytes(self.last_upload_total)} · 暂不可用"
                )
                self.download_card.set_secondary(
                    f"累计 {backend.format_bytes(self.last_download_total)} · 暂不可用"
                )
        else:
            self.last_traffic_time = None
            self.last_upload_total = 0
            self.last_download_total = 0
            self.traffic_chart.reset()
            self.connection_card.set_value("--" if active else "0")
            self.connection_card.set_secondary(
                "等待控制接口" if active else "核心实时统计"
            )
            self.upload_card.set_value("0 B/s")
            self.upload_card.set_secondary("累计 0 B")
            self.download_card.set_value("0 B/s")
            self.download_card.set_secondary("累计 0 B")
        self.memory_card.set_value(backend.format_bytes(int(result.get("memory", 0))))
        self.detail_values["当前网络接管"].setText(
            "smart-box" if tun else ("FlClash" if flclash else "无")
        )
        mode = result.get("mode", self.settings.get("mode", "Rule"))
        if mode in self.mode_buttons:
            self.updating_mode = True
            self.mode_buttons[mode].setChecked(True)
            self.updating_mode = False

        if backend.PROFILE_PATH.is_file():
            try:
                summary_path = (
                    backend.RUNTIME_PATH
                    if backend.RUNTIME_PATH.is_file()
                    else backend.PROFILE_PATH
                )
                summary = backend.profile_summary(backend.load_json(summary_path))
                self.detail_values["配置节点"].setText(str(summary["nodes"]))
            except Exception:  # noqa: BLE001 - a bad profile is reflected as unavailable
                self.detail_values["配置节点"].setText("--")
        else:
            self.detail_values["配置节点"].setText("--")
        self.update_profile_labels()

        if (
            active
            and api
            and tun
            and self.core_transaction is None
            and now - self.last_connectivity_check >= 60
        ):
            self.check_connectivity_background()

    def update_profile_labels(self) -> None:
        last_pull = self.settings.get("last_pull_utc")
        display = "尚未拉取"
        if isinstance(last_pull, str) and last_pull:
            try:
                value = dt.datetime.fromisoformat(last_pull)
                display = value.astimezone().strftime("%Y-%m-%d %H:%M")
            except ValueError:
                display = last_pull
        self.detail_values["配置更新时间"].setText(display)
        self.profile_status_label.setText(
            f"已更新 {display}" if backend.PROFILE_PATH.is_file() else "尚未拉取配置"
        )

    def check_connectivity_background(self, manual: bool = False) -> None:
        if self.core_transaction is not None:
            if manual:
                self.set_operation_state("核心操作进行中，完成后再验网", "working")
            return
        first_check = self.last_connectivity_check == 0
        mode = str(self.settings.get("mode", "Rule"))
        probe_generation = self.core_generation
        if manual:
            self.connectivity_button.setEnabled(False)
            self.connectivity_button.setText("验网中…")
            self.set_operation_state("正在检查当前网络路径…", "working")

        def probe() -> dict[str, Any]:
            if manual and not backend.unit_active(backend.SERVICE_UNIT):
                direct = backend.probe_direct_connectivity()
                return {
                    **direct,
                    "probe_scope": "direct",
                    "passed": 1 if direct.get("online") else 0,
                    "total": 1,
                    "checks": [
                        {
                            **direct,
                            "key": "direct",
                            "label": "物理直连",
                        }
                    ],
                }
            return backend.probe_connectivity_guard(mode=mode)

        def done(result: Any, error: Exception | None) -> None:
            if manual:
                self.connectivity_button.setEnabled(True)
                self.connectivity_button.setText("立即验网")
            if (
                probe_generation != self.core_generation
                or self.core_transaction is not None
            ):
                if manual:
                    self.set_operation_state("网络状态已变化，已忽略过期验网结果", "working")
                return
            self.last_connectivity_check = time.monotonic()
            if error is not None or not isinstance(result, dict):
                message = str(error or "无有效结果")
                self.detail_values["联网验收"].setText(f"失败：{message}")
                self.set_label_state(self.detail_values["联网验收"], "error")
                self.set_operation_state(f"联网复检失败：{message}", "error")
                return
            if result.get("probe_scope") == "direct":
                latency = int(result.get("latency_ms", 0))
                if result.get("online"):
                    summary = f"直连可用 · {latency} ms"
                    self.detail_values["联网验收"].setText(summary)
                    self.set_label_state(self.detail_values["联网验收"], "success")
                    self.set_operation_state(f"直连网络已验证：{latency} ms", "success")
                else:
                    message = str(result.get("error") or "物理直连请求失败")
                    self.detail_values["联网验收"].setText(f"直连失败：{message}")
                    self.set_label_state(self.detail_values["联网验收"], "error")
                    self.set_operation_state(f"直连网络验证失败：{message}", "error")
                return
            summary = backend.format_connectivity_result(result)
            if backend.connectivity_is_usable_for_mode(result, mode):
                self.detail_values["联网验收"].setText(summary)
                if result.get("online"):
                    self.set_label_state(self.detail_values["联网验收"], "success")
                else:
                    self.set_label_state(self.detail_values["联网验收"], "warning")
                if (first_check or manual) and result.get("online"):
                    self.set_operation_state(f"网络已验证：{summary}", "success")
                elif not result.get("online"):
                    self.set_operation_state(
                        f"关键路径可用，但部分站点降级：{summary}", "working"
                    )
            elif result.get("guard_confirmed"):
                self.detail_values["联网验收"].setText(f"保护触发：{summary}")
                self.set_label_state(self.detail_values["联网验收"], "error")
                self.disable_unusable_tun(result, probe_generation)
            else:
                message = str(result.get("error") or "公网请求失败")
                self.detail_values["联网验收"].setText(f"失败：{message}")
                self.set_label_state(self.detail_values["联网验收"], "error")
                self.set_operation_state(f"联网复检失败：{message}", "error")

        started = self.run_task(
            "connectivity-status",
            probe,
            done,
        )
        if manual and not started:
            self.connectivity_button.setEnabled(True)
            self.connectivity_button.setText("立即验网")
            self.set_operation_state("已有联网检查正在进行", "working")

    def disable_unusable_tun(
        self,
        probe: dict[str, Any],
        probe_generation: int | None = None,
    ) -> None:
        """Return the host to direct networking after a confirmed TUN outage."""
        generation = self.core_generation if probe_generation is None else probe_generation
        if (
            generation != self.core_generation
            or self.core_transaction is not None
            or not backend.unit_active(backend.SERVICE_UNIT)
        ):
            return
        self.set_switch_busy(True)

        def recover() -> dict[str, Any]:
            recovery = backend.recover_failed_switch(False)
            return {
                "message": (
                    "关键网络路径连续两轮不可用，SmartBox 已自动关闭；"
                    "系统直连和 DNS 已恢复"
                ),
                "probe": probe,
                "recovery": recovery,
            }

        def done(result: Any, error: Exception | None) -> None:
            self.set_switch_busy(False)
            self.last_connectivity_check = 0
            if error is not None:
                self.notify(f"联网保护停止 TUN 失败：{error}", error=True)
                self.select_page(3)
                self.refresh_logs()
            else:
                value = result if isinstance(result, dict) else {"message": str(result)}
                failed_probe = value.get("probe", probe)
                if isinstance(failed_probe, dict):
                    summary = backend.format_connectivity_result(failed_probe)
                    self.detail_values["联网验收"].setText(f"已自动关闭：{summary}")
                    self.set_label_state(self.detail_values["联网验收"], "error")
                self.notify(str(value.get("message", "SmartBox 已自动关闭")), error=True)
                self.refresh_policies(silent=True)
            self.poll()

        started = self.run_core_task(
            "service-switch",
            recover,
            done,
            "检测到持续断网，正在撤销 TUN…",
        )
        if not started:
            self.set_switch_busy(False)

    def toggle_service(self) -> None:
        if "service-switch" in self.tasks:
            return
        if self.last_snapshot.get("active") or backend.unit_active(backend.SERVICE_UNIT):
            self.stop_service()
        else:
            self.start_service()

    def start_service(self) -> None:
        if not self.core_operation_available():
            return
        if not backend.PROFILE_PATH.is_file():
            self.select_page(4)
            self.set_operation_state("请先保存并拉取订阅配置", "error")
            QMessageBox.information(self, "缺少配置", "请先保存并拉取订阅配置。")
            return
        self.set_switch_busy(True)

        def sequence() -> dict[str, Any]:
            restore_flclash = backend.flclash_conflict()
            try:
                self.ensure_flclash_stopped()
                start = backend.systemctl_service(
                    "start", backend.SERVICE_UNIT, timeout=SERVICE_START_TIMEOUT
                )
                if start.returncode != 0:
                    raise backend.SmartBoxError(start.stdout.strip() or "smart-box 服务启动失败")
                probe = self.verify_service_ready_then_probe()
                self.require_usable_connectivity(
                    probe, str(self.settings.get("mode", "Rule"))
                )
            except Exception as error:
                raise self.failed_switch_error(error, restore_flclash) from error
            if not probe.get("stable"):
                return {
                    "message": (
                        "smart-box 已接管网络；公网复检暂未稳定："
                        f"{backend.format_connectivity_result(probe)}"
                    ),
                    "probe": probe,
                    "degraded": True,
                }
            return {
                "message": (
                    "smart-box 已接管网络，网络已验证："
                    f"{backend.format_connectivity_result(probe)}"
                ),
                "probe": probe,
            }

        if not self.run_core_task(
            "service-switch",
            sequence,
            self.service_switch_done,
            "正在关闭 FlClash、启动 TUN 并验证网络…",
        ):
            self.set_switch_busy(False)

    @staticmethod
    def ensure_flclash_stopped() -> None:
        backend.stop_flclash(timeout=30)
        backend.SWITCH_STATE_PATH.unlink(missing_ok=True)

    @staticmethod
    def failed_switch_error(error: Exception, restore_flclash: bool) -> backend.SmartBoxError:
        try:
            recovery = backend.recover_failed_switch(restore_flclash)
        except Exception as recovery_error:  # noqa: BLE001 - preserve both failure causes
            return backend.SmartBoxError(f"{error}；自动回退失败：{recovery_error}")
        return backend.SmartBoxError(f"{error}；{recovery}")

    @staticmethod
    def api_available() -> bool:
        try:
            backend.api_request("/version", timeout=0.5)
            return True
        except backend.SmartBoxError:
            return False

    def verify_service_online(self) -> dict[str, Any]:
        self.verify_service_ready()
        last_result = backend.probe_connectivity_stable(
            required_successes=2,
            max_attempts=4,
            timeout=6,
            interval=1,
        )
        if last_result.get("stable"):
            return last_result
        reason = str(last_result.get("error") or "关键网络路径没有稳定通过")
        raise backend.SmartBoxError(f"TUN 已建立，但网络验收失败：{reason}")

    def verify_service_ready(self) -> None:
        ready = backend.wait_for(
            lambda: backend.unit_active(backend.SERVICE_UNIT)
            and backend.interface_exists(backend.TUN_INTERFACE)
            and self.api_available(),
            25,
            0.4,
        )
        if not ready:
            raise backend.SmartBoxError("核心未在限定时间内建立 TUN 和控制接口")

    def verify_service_ready_then_probe(self) -> dict[str, Any]:
        """Leave a ready TUN online while remote routes finish warming up."""
        self.verify_service_ready()
        return backend.probe_connectivity_stable(
            required_successes=2,
            max_attempts=4,
            timeout=6,
            interval=1,
        )

    @staticmethod
    def require_usable_connectivity(probe: dict[str, Any], mode: str = "Rule") -> None:
        if backend.connectivity_is_usable_for_mode(probe, mode):
            return
        reason = str(probe.get("error") or backend.format_connectivity_result(probe))
        raise backend.SmartBoxError(
            f"TUN 已建立，但关键网络路径不可用：{reason}"
        )

    def restart_service(self) -> None:
        if not self.core_operation_available():
            return
        if "service-switch" in self.tasks:
            return
        if not backend.unit_active(backend.SERVICE_UNIT):
            self.start_service()
            return
        self.set_switch_busy(True)

        def sequence() -> dict[str, Any]:
            restore_flclash = backend.flclash_conflict()
            try:
                self.ensure_flclash_stopped()
                restarted = backend.systemctl_service(
                    "restart", backend.SERVICE_UNIT, timeout=SERVICE_START_TIMEOUT
                )
                if restarted.returncode != 0:
                    raise backend.SmartBoxError(
                        restarted.stdout.strip() or "smart-box 服务重启失败"
                    )
                probe = self.verify_service_ready_then_probe()
                self.require_usable_connectivity(
                    probe, str(self.settings.get("mode", "Rule"))
                )
            except Exception as error:
                raise self.failed_switch_error(error, restore_flclash) from error
            if not probe.get("stable"):
                return {
                    "message": (
                        "smart-box 已重启并保持接管；公网复检暂未稳定："
                        f"{backend.format_connectivity_result(probe)}"
                    ),
                    "probe": probe,
                    "degraded": True,
                }
            return {
                "message": (
                    "smart-box 已重启，网络已验证："
                    f"{backend.format_connectivity_result(probe)}"
                ),
                "probe": probe,
            }

        if not self.run_core_task(
            "service-switch",
            sequence,
            self.service_switch_done,
            "正在重启 TUN 并验证网络…",
        ):
            self.set_switch_busy(False)

    def stop_service(self) -> None:
        if not self.core_operation_available():
            return
        self.set_switch_busy(True)

        def sequence() -> dict[str, Any]:
            self.ensure_flclash_stopped()
            stopped = backend.systemctl_service("stop", backend.SERVICE_UNIT, timeout=45)
            if stopped.returncode != 0:
                raise backend.SmartBoxError(stopped.stdout.strip() or "停止 smart-box 失败")
            clean = backend.wait_for(
                lambda: not backend.unit_active(backend.SERVICE_UNIT)
                and not backend.interface_exists(backend.TUN_INTERFACE),
                15,
            )
            if not clean:
                raise backend.SmartBoxError("服务已停止，但 SmartBox TUN 未及时清理")
            backend.SWITCH_STATE_PATH.unlink(missing_ok=True)
            return {"message": "smart-box 已停止，FlClash 保持关闭"}

        if not self.run_core_task(
            "service-switch", sequence, self.service_switch_done, "正在停止核心…"
        ):
            self.set_switch_busy(False)

    def set_switch_busy(self, busy: bool) -> None:
        if not busy:
            self.core_active_hint = backend.unit_active(backend.SERVICE_UNIT)
        self.power_button.setEnabled(not busy)
        self.restart_button.setEnabled(not busy and self.core_active_hint)
        if busy:
            self.status_pill.set_state("starting")
            self.power_button.setText("处理中…")

    def service_switch_done(self, result: Any, error: Exception | None) -> None:
        self.set_switch_busy(False)
        if error is not None:
            self.notify(str(error), error=True)
            self.select_page(3)
            self.refresh_logs()
        else:
            value = result if isinstance(result, dict) else {"message": str(result)}
            probe = value.get("probe")
            if isinstance(probe, dict):
                summary = backend.format_connectivity_result(probe)
                self.detail_values["联网验收"].setText(summary)
                self.set_label_state(self.detail_values["联网验收"], "success")
                self.last_connectivity_check = time.monotonic()
            else:
                self.detail_values["联网验收"].setText("服务已停止")
                self.set_label_state(self.detail_values["联网验收"], "muted")
                self.last_connectivity_check = 0
            self.notify(str(value.get("message", "操作完成")))
            self.refresh_policies(silent=True)
        self.poll()

    def change_mode(self, mode: str) -> None:
        if self.updating_mode or mode not in backend.VALID_MODES:
            return
        if not self.core_operation_available():
            previous_button = self.mode_buttons.get(
                str(self.settings.get("mode", "Rule"))
            )
            if previous_button is not None:
                self.updating_mode = True
                previous_button.setChecked(True)
                self.updating_mode = False
            return
        if "mode-change" in self.tasks:
            previous_button = self.mode_buttons.get(self.settings.get("mode", "Rule"))
            if previous_button is not None:
                self.updating_mode = True
                previous_button.setChecked(True)
                self.updating_mode = False
            return
        cached_previous = str(self.settings.get("mode", "Rule"))
        if mode == cached_previous:
            return
        previous_value: dict[str, str] = {}

        def persist_mode(settings: dict[str, Any]) -> None:
            previous_value["mode"] = str(settings.get("mode", "Rule"))
            settings["mode"] = mode

        try:
            self.settings = backend.mutate_settings(persist_mode)
        except OSError as error:
            self.updating_mode = True
            previous_button = self.mode_buttons.get(cached_previous)
            if previous_button is not None:
                previous_button.setChecked(True)
            self.updating_mode = False
            self.notify(f"保存运行模式失败：{error}", error=True)
            return
        previous = previous_value.get("mode", cached_previous)
        previous_title = mode_presentation(previous)[0]
        if previous == mode:
            return
        committed_settings = copy.deepcopy(self.settings)
        for button in self.mode_buttons.values():
            button.setEnabled(False)

        def restore_previous() -> None:
            def restore_mode(settings: dict[str, Any]) -> None:
                if settings.get("mode") == mode:
                    settings["mode"] = previous

            try:
                self.settings = backend.mutate_settings(restore_mode)
            except OSError as save_error:
                self.notify(f"恢复运行模式设置失败：{save_error}", error=True)
            self.updating_mode = True
            restored_mode = str(self.settings.get("mode", previous))
            restored_button = self.mode_buttons.get(
                restored_mode, self.mode_buttons.get("Rule")
            )
            if restored_button is not None:
                restored_button.setChecked(True)
            self.updating_mode = False

        def apply_mode() -> dict[str, Any]:
            if backend.unit_active(backend.SERVICE_UNIT):
                core_change_attempted = False
                try:
                    # A timed-out PATCH may still have reached the core, so every
                    # downstream failure must make a best-effort rollback.
                    core_change_attempted = True
                    backend.api_request(
                        "/configs", method="PATCH", payload={"mode": mode}
                    )
                    probe = backend.probe_connectivity_guard(mode=mode)
                    if not backend.connectivity_is_usable_for_mode(probe, mode):
                        reason = str(
                            probe.get("error")
                            or backend.format_connectivity_result(probe)
                        )
                        raise backend.SmartBoxError(
                            f"新模式关键网络路径不可用：{reason}"
                        )
                except Exception as error:
                    if not core_change_attempted:
                        raise
                    try:
                        backend.api_request(
                            "/configs",
                            method="PATCH",
                            payload={"mode": previous},
                        )
                    except Exception as rollback_error:
                        try:
                            recovery = backend.recover_failed_switch(False)
                        except Exception as recovery_error:
                            raise backend.SmartBoxError(
                                f"{error}；恢复核心模式失败：{rollback_error}；"
                                f"停止异常核心也失败：{recovery_error}"
                            ) from recovery_error
                        raise backend.SmartBoxError(
                            f"{error}；恢复核心模式失败：{rollback_error}；"
                            f"{recovery}，系统已回到直连"
                        ) from rollback_error
                    raise backend.SmartBoxError(
                        f"{error}；已恢复{previous_title}"
                    ) from error
                return {"mode": mode, "probe": probe}
            elif backend.PROFILE_PATH.is_file():
                backend.prepare_runtime(settings=committed_settings)
            return {"mode": mode}

        def done(result: Any, error: Exception | None) -> None:
            for button in self.mode_buttons.values():
                button.setEnabled(True)
            if error is not None:
                restore_previous()
                self.notify(f"切换模式失败：{error}", error=True)
            else:
                value = result if isinstance(result, dict) else {"mode": str(result)}
                selected_mode = str(value.get("mode", mode))
                selected_title = mode_presentation(selected_mode)[0]
                probe = value.get("probe")
                if isinstance(probe, dict):
                    summary = backend.format_connectivity_result(probe)
                    self.detail_values["联网验收"].setText(summary)
                    self.last_connectivity_check = time.monotonic()
                    if probe.get("online"):
                        self.set_label_state(self.detail_values["联网验收"], "success")
                        self.notify(f"运行模式已切换为{selected_title}，网络已验证：{summary}")
                    else:
                        message = (
                            f"运行模式已切换为{selected_title}；"
                            f"关键路径可用，但部分站点降级：{summary}"
                        )
                        self.set_label_state(self.detail_values["联网验收"], "warning")
                        self.notify(message)
                        self.set_operation_state(message, "working")
                else:
                    self.notify(f"运行模式已切换为{selected_title}，将在下次启动时应用")

        if not self.run_core_task("mode-change", apply_mode, done, "正在切换模式…"):
            restore_previous()
            for button in self.mode_buttons.values():
                button.setEnabled(True)

    def refresh_policies(self, silent: bool = False) -> None:
        def collect() -> tuple[list[dict[str, Any]], bool]:
            offline = backend.profile_selectors()
            if not backend.unit_active(backend.SERVICE_UNIT):
                return offline, False
            try:
                response = backend.api_request("/proxies", timeout=3)
            except backend.SmartBoxError:
                return offline, False
            proxies = response.get("proxies", {}) if isinstance(response, dict) else {}
            return merge_online_policy_selectors(offline, proxies), True

        self.run_task(
            "policies",
            collect,
            self.policies_updated,
            "" if silent else "正在读取策略…",
        )

    def policies_updated(self, result: Any, error: Exception | None) -> None:
        if error is not None:
            self.policy_source_label.setText("读取失败")
            self.notify(f"读取分流策略失败：{error}", error=True)
            return
        selectors, online = result
        self.policy_source_label.setText("核心实时状态" if online else "本机配置预览")
        self.updating_policies = True
        while self.policy_layout.count() > 0:
            item = self.policy_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self.policy_rows.clear()
        self.policy_smart_status.clear()
        for selector in selectors:
            name = selector.get("name", "")
            choices = [choice for choice in selector.get("all", []) if isinstance(choice, str)]
            current = selector.get("now", "")
            row = QFrame()
            row.setObjectName("policyRow")
            row.setMinimumHeight(68)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(15, 9, 12, 9)
            label = QLabel(name)
            label.setMinimumWidth(180)
            label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            combo = QComboBox()
            combo.setMinimumWidth(260)
            combo.addItems(choices)
            for index, choice in enumerate(choices):
                combo.setItemData(index, choice, Qt.ItemDataRole.UserRole)
            if current in choices:
                combo.setCurrentText(current)
            current_choice = str(
                combo.currentData(Qt.ItemDataRole.UserRole) or combo.currentText()
            )
            combo.setProperty("smart_box_previous_choice", current_choice)
            combo.setEnabled(self.core_transaction is None)
            combo.setToolTip(
                "立即切换并保存" if online else "保存选择，将在下次启动时应用"
            )
            combo.setAccessibleName(f"{name} 当前节点")
            combo.setAccessibleDescription(combo.toolTip())
            combo.currentIndexChanged.connect(
                lambda _index, selector_name=name, selector_combo=combo: self.select_policy(
                    selector_name,
                    str(
                        selector_combo.currentData(Qt.ItemDataRole.UserRole)
                        or selector_combo.currentText()
                    ),
                )
            )
            delay_label = QLabel("未测速")
            delay_label.setObjectName("muted")
            delay_label.setTextFormat(Qt.TextFormat.PlainText)
            delay_label.setWordWrap(True)
            delay_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            delay_label.setMinimumWidth(220)
            delay_label.setMaximumWidth(280)
            delay_label.setAccessibleName(f"{name} 测速状态")
            smart_status = normalize_smart_status(selector.get("smart_status"))
            if smart_status is not None:
                self.policy_smart_status[name] = smart_status
                delay_label.setText(format_smart_status_summary(smart_status))
                delay_label.setToolTip(format_smart_status_tooltip(smart_status))
                delay_label.setAccessibleName(f"{name} Smart 节点评分与测速状态")
            else:
                delay_label.setToolTip("手动测速结果")
            probe_button = QPushButton("测速")
            probe_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaPlay))
            probe_button.setMinimumWidth(78)
            probe_button.setAccessibleName(f"测试 {name} 节点延迟")
            probe_button.setProperty("smart_box_online", online)
            probe_button.setEnabled(online and self.core_transaction is None)
            if not online:
                probe_button.setToolTip("smart-box 核心未运行，启动后才能测速")
            elif smart_status is not None:
                probe_button.setToolTip(
                    f"测试当前 {current_choice} 的物理候选并刷新质量分；"
                    "不会改变手动地区选择"
                )
            else:
                probe_button.setToolTip(
                    "测试该分组内所有节点的延迟；只显示结果，不会切换当前节点"
                )
            probe_button.clicked.connect(
                lambda _checked=False, selector_name=name: self.probe_policy(selector_name)
            )
            row_layout.addWidget(label, 1)
            row_layout.addWidget(combo)
            row_layout.addWidget(delay_label)
            row_layout.addWidget(probe_button)
            self.policy_layout.addWidget(row)
            self.policy_rows.append((row, label, combo, delay_label, probe_button))
        if not selectors:
            empty = QLabel("尚无可用策略，请先拉取订阅配置。")
            empty.setObjectName("muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setMinimumHeight(180)
            self.policy_layout.addWidget(empty)
        self.policy_layout.addStretch(1)
        self.updating_policies = False
        self.filter_policies(self.policy_search.text())

    def filter_policies(self, query: str) -> None:
        normalized = query.strip().lower()
        visible_count = 0
        for row, label, combo, delay_label, probe_button in self.policy_rows:
            haystack = f"{label.text()} {combo.currentText()} {delay_label.text()}".lower()
            matches = not normalized or normalized in haystack
            row.setVisible(matches)
            visible_count += int(matches)
        total = len(self.policy_rows)
        if normalized and total and visible_count == 0:
            self.policy_filter_status.setText(f"未找到匹配策略 · 共 {total} 项")
            self.set_label_state(self.policy_filter_status, "error")
            self.policy_search.setToolTip("没有策略名称、当前节点或测速状态包含此关键词")
        elif normalized:
            self.policy_filter_status.setText(f"显示 {visible_count} / 共 {total} 项")
            self.set_label_state(self.policy_filter_status)
            self.policy_search.setToolTip("")
        else:
            self.policy_filter_status.setText(f"共 {total} 项策略")
            self.set_label_state(self.policy_filter_status)
            self.policy_search.setToolTip("")

    def _policy_row(self, name: str) -> tuple[QFrame, QLabel, QComboBox, QLabel, QPushButton] | None:
        for row_data in self.policy_rows:
            if row_data[1].text() == name:
                return row_data
        return None

    def probe_policy(self, name: str) -> None:
        if not self.core_operation_available():
            return
        row_data = self._policy_row(name)
        if row_data is None or not name:
            return
        _row, _label, combo, delay_label, probe_button = row_data
        choices = [
            str(combo.itemData(index, Qt.ItemDataRole.UserRole) or combo.itemText(index))
            for index in range(combo.count())
        ]
        selected_choice = str(
            combo.currentData(Qt.ItemDataRole.UserRole) or combo.currentText()
        )
        previous_smart_status = self.policy_smart_status.get(name)
        smart_candidates = (
            [
                str(candidate["name"])
                for candidate in previous_smart_status.get("candidates", [])
                if isinstance(candidate, dict) and isinstance(candidate.get("name"), str)
            ]
            if previous_smart_status is not None
            else []
        )
        probe_name = selected_choice if previous_smart_status is not None else name
        expected_probe_names = smart_candidates if previous_smart_status is not None else choices
        task_key = f"policy-delay:{name}"
        if task_key in self.tasks:
            return
        probe_button.setEnabled(False)
        probe_button.setText("测速中…")
        delay_label.setText("物理节点测速中…" if previous_smart_status is not None else "测试中…")
        delay_label.setToolTip(
            f"正在测试 {selected_choice} 的物理候选"
            if previous_smart_status is not None
            else "正在测试该分组内所有节点"
        )

        def measure() -> dict[str, Any]:
            if not backend.unit_active(backend.SERVICE_UNIT):
                raise backend.SmartBoxError("smart-box 核心未运行，无法测速")
            measurement = backend.probe_group_delays(probe_name, expected_probe_names)
            if previous_smart_status is None:
                return measurement
            try:
                response = backend.api_request("/proxies", timeout=3)
                proxies = response.get("proxies", {}) if isinstance(response, dict) else {}
                refreshed = smart_status_for_selector(proxies, name, selected_choice)
                if refreshed is None:
                    measurement["smart_status_refresh_error"] = "核心未返回有效 Smart 状态"
                else:
                    measurement["smart_status"] = refreshed
            except backend.SmartBoxError as refresh_error:
                measurement["smart_status_refresh_error"] = str(refresh_error)
            return measurement

        def done(result: Any, error: Exception | None) -> None:
            current = self._policy_row(name)
            if current is None:
                return
            _row, _label, current_combo, current_label, current_button = current
            current_button.setEnabled(True)
            current_button.setText("测速")
            if error is not None:
                current_label.setText("测速失败")
                current_label.setToolTip(str(error))
                self.notify(f"{name} 测速失败：{error}", error=True)
                return
            if not isinstance(result, dict):
                current_label.setText("无结果")
                current_label.setToolTip("测速接口没有返回有效结果")
                return
            delays = result.get("delays", {})
            if not isinstance(delays, dict):
                delays = {}
            if previous_smart_status is not None:
                refreshed_status = normalize_smart_status(result.get("smart_status"))
                displayed_status = refreshed_status or previous_smart_status
                self.policy_smart_status[name] = displayed_status
                current_label.setText(format_smart_status_summary(displayed_status))
                tooltip = format_smart_status_tooltip(displayed_status)
                details = backend.format_group_delay_details(result)
                if details:
                    tooltip += (
                        f"\n\n本轮 {selected_choice} 物理节点测速：\n"
                        + details
                    )
                refresh_error = result.get("smart_status_refresh_error")
                if isinstance(refresh_error, str) and refresh_error:
                    tooltip += f"\n\nSmart 状态刷新失败：{refresh_error}"
                    self.notify(f"{name} 物理节点测速完成，但 Smart 状态刷新失败：{refresh_error}", True)
                else:
                    self.notify(f"{name} 物理节点测速完成，Smart 节点评分已刷新")
                current_label.setToolTip(tooltip)
                return
            current_combo.blockSignals(True)
            for index in range(current_combo.count()):
                choice = str(
                    current_combo.itemData(index, Qt.ItemDataRole.UserRole)
                    or current_combo.itemText(index)
                )
                delay = delays.get(choice)
                suffix = f"{delay} ms" if isinstance(delay, int) else "失败"
                current_combo.setItemText(index, f"{choice} · {suffix}")
                current_combo.setItemData(index, choice, Qt.ItemDataRole.UserRole)
                current_combo.setItemData(
                    index, f"{choice}: {suffix}", Qt.ItemDataRole.ToolTipRole
                )
            current_combo.blockSignals(False)
            current_label.setText(backend.format_group_delay_summary(result))
            current_label.setToolTip(backend.format_group_delay_details(result))
            self.notify(f"{name} 测速完成：{backend.format_group_delay_summary(result)}")

        activity = (
            f"正在测试 {selected_choice} 的物理节点…"
            if previous_smart_status is not None
            else f"正在测试 {name}…"
        )
        self.run_task(task_key, measure, done, activity)

    def select_policy(self, name: str, choice: str) -> None:
        if self.updating_policies or not name or not choice:
            return
        task_key = f"policy:{name}"
        row_data = self._policy_row(name)
        combo = row_data[2] if row_data is not None else None
        if task_key in self.tasks:
            if combo is not None:
                previous = str(
                    combo.property("smart_box_pending_choice")
                    or combo.property("smart_box_previous_choice")
                    or combo.currentData(Qt.ItemDataRole.UserRole)
                    or combo.currentText()
                )
                combo.blockSignals(True)
                previous_index = combo.findData(previous, Qt.ItemDataRole.UserRole)
                if previous_index < 0:
                    previous_index = combo.findText(previous)
                if previous_index >= 0:
                    combo.setCurrentIndex(previous_index)
                combo.blockSignals(False)
            return
        if not self.core_operation_available():
            self.refresh_policies(silent=True)
            return
        previous_choice = ""
        if combo is not None:
            previous_choice = str(
                combo.property("smart_box_previous_choice")
                or combo.currentData(Qt.ItemDataRole.UserRole)
                or combo.currentText()
            )
            combo.setProperty("smart_box_previous_choice", previous_choice)
            combo.setProperty("smart_box_pending_choice", choice)
            combo.setEnabled(False)

        def apply_selection() -> str:
            online = backend.unit_active(backend.SERVICE_UNIT)
            core_change_attempted = False
            settings_committed = False
            missing_override = object()
            previous_override: dict[str, Any] = {"value": missing_override}
            try:
                if online:
                    encoded = urllib.parse.quote(name, safe="")
                    # Treat a timeout as ambiguous: the core may have accepted
                    # the selection before the response was lost.
                    core_change_attempted = True
                    backend.api_request(
                        f"/proxies/{encoded}",
                        method="PUT",
                        payload={"name": choice},
                        timeout=4,
                    )

                def persist_override(settings: dict[str, Any]) -> None:
                    overrides = settings.setdefault("selector_overrides", {})
                    if not isinstance(overrides, dict):
                        overrides = {}
                        settings["selector_overrides"] = overrides
                    previous_override["value"] = overrides.get(name, missing_override)
                    overrides[name] = choice

                settings = backend.mutate_settings(persist_override)
                settings_committed = True
                if not online and backend.PROFILE_PATH.is_file():
                    backend.prepare_runtime(settings=settings)
            except Exception as error:
                rollback_failures: list[str] = []
                if core_change_attempted and previous_choice:
                    try:
                        encoded = urllib.parse.quote(name, safe="")
                        backend.api_request(
                            f"/proxies/{encoded}",
                            method="PUT",
                            payload={"name": previous_choice},
                            timeout=4,
                        )
                    except Exception as rollback_error:
                        rollback_failures.append(f"恢复核心选择失败：{rollback_error}")
                if settings_committed:
                    try:
                        def restore_override(settings: dict[str, Any]) -> None:
                            overrides = settings.setdefault("selector_overrides", {})
                            if not isinstance(overrides, dict):
                                return
                            if overrides.get(name, missing_override) != choice:
                                return
                            old_value = previous_override["value"]
                            if old_value is missing_override:
                                overrides.pop(name, None)
                            else:
                                overrides[name] = old_value

                        backend.mutate_settings(restore_override)
                    except Exception as rollback_error:
                        rollback_failures.append(f"恢复策略设置失败：{rollback_error}")
                if rollback_failures:
                    raise backend.SmartBoxError(
                        f"{error}；" + "；".join(rollback_failures)
                    ) from error
                if core_change_attempted:
                    raise backend.SmartBoxError(
                        f"{error}；核心与设置已恢复到“{previous_choice}”"
                    ) from error
                raise
            return f"{name} → {choice}"

        def done(result: Any, error: Exception | None) -> None:
            current = self._policy_row(name)
            if current is not None:
                current[2].setEnabled(True)
            if error is not None:
                if combo is not None:
                    combo.setProperty("smart_box_pending_choice", None)
                if combo is not None and previous_choice:
                    combo.blockSignals(True)
                    previous_index = combo.findData(previous_choice, Qt.ItemDataRole.UserRole)
                    if previous_index < 0:
                        previous_index = combo.findText(previous_choice)
                    if previous_index >= 0:
                        combo.setCurrentIndex(previous_index)
                    combo.blockSignals(False)
                self.notify(f"策略切换失败：{error}", error=True)
                self.refresh_policies(silent=True)
            else:
                if combo is not None:
                    combo.setProperty("smart_box_previous_choice", choice)
                    combo.setProperty("smart_box_pending_choice", None)
                self.settings = backend.load_settings()
                self.notify(str(result))
                self.refresh_policies(silent=True)

        if not self.run_core_task(task_key, apply_selection, done, "正在切换策略…"):
            current = self._policy_row(name)
            if current is not None:
                current[2].setEnabled(True)

    def load_domain_editors(self) -> None:
        self.settings = backend.load_settings()
        saved_allow, _ = backend.parse_domain_text(
            "\n".join(self.settings.get("allow_domains", []))
        )
        saved_proxy, _ = backend.parse_domain_text(
            "\n".join(self.settings.get("proxy_domains", []))
        )
        self.saved_domain_rules = (tuple(saved_allow), tuple(saved_proxy))
        self.allow_editor.blockSignals(True)
        self.proxy_editor.blockSignals(True)
        self.allow_editor.setPlainText("\n".join(self.settings.get("allow_domains", [])))
        self.proxy_editor.setPlainText("\n".join(self.settings.get("proxy_domains", [])))
        self.allow_editor.blockSignals(False)
        self.proxy_editor.blockSignals(False)
        self.update_domain_counts()

    def update_domain_counts(self) -> None:
        allow, allow_invalid = backend.parse_domain_text(self.allow_editor.toPlainText())
        proxy, proxy_invalid = backend.parse_domain_text(self.proxy_editor.toPlainText())
        self.allow_count.setText(f"{len(allow)} 条生效规则")
        self.proxy_count.setText(f"{len(proxy)} 条生效规则")
        invalid_count = len(allow_invalid) + len(proxy_invalid)
        conflicts = backend.domain_conflicts(allow, proxy)
        changed = (
            (tuple(allow), tuple(proxy)) != self.saved_domain_rules
            or invalid_count > 0
        )
        self.domain_dirty = changed
        busy = (
            "domain-rules" in self.tasks
            or self.core_transaction is not None
        )
        can_save = changed and not invalid_count and not conflicts and not busy
        self.domain_save_button.setEnabled(can_save)
        self.domain_reset_button.setEnabled(changed and not busy)
        if invalid_count:
            self.domain_validation_label.setText(f"{invalid_count} 项格式无效 · 无法保存")
            self.set_label_state(self.domain_validation_label, "error")
            invalid = allow_invalid + proxy_invalid
            self.domain_validation_label.setToolTip("格式无效：\n" + "\n".join(invalid[:20]))
        elif conflicts:
            self.domain_validation_label.setText(f"{len(conflicts)} 组冲突 · 无法保存")
            self.set_label_state(self.domain_validation_label, "error")
            self.domain_validation_label.setToolTip(
                "同时命中直连与 Smart：\n"
                + "\n".join(f"{left} ↔ {right}" for left, right in conflicts[:20])
            )
        elif changed:
            self.domain_validation_label.setText("格式有效 · 有未保存更改")
            self.set_label_state(self.domain_validation_label, "warning")
            self.domain_validation_label.setToolTip("保存后将重新生成运行配置；核心运行时会安全重载。")
        else:
            self.domain_validation_label.setText("已保存 · 无待应用更改")
            self.set_label_state(self.domain_validation_label, "success")
            self.domain_validation_label.setToolTip("")

    def save_domain_rules(self) -> None:
        if not self.core_operation_available():
            return
        allow, allow_invalid = backend.parse_domain_text(self.allow_editor.toPlainText())
        proxy, proxy_invalid = backend.parse_domain_text(self.proxy_editor.toPlainText())
        invalid = allow_invalid + proxy_invalid
        if invalid:
            QMessageBox.warning(
                self,
                "域名格式无效",
                "以下内容不是有效域名：\n\n" + "\n".join(invalid[:20]),
            )
            return
        conflicts = backend.domain_conflicts(allow, proxy)
        if conflicts:
            DomainConflictDialog(conflicts, self).exec()
            return
        was_active = backend.unit_active(backend.SERVICE_UNIT)

        def apply_rules() -> str:
            previous_domains: dict[str, list[str]] = {}

            def persist_domains(settings: dict[str, Any]) -> None:
                previous_domains["allow"] = copy.deepcopy(
                    settings.get("allow_domains", [])
                )
                previous_domains["proxy"] = copy.deepcopy(
                    settings.get("proxy_domains", [])
                )
                settings["allow_domains"] = allow
                settings["proxy_domains"] = proxy

            settings = backend.mutate_settings(persist_domains)
            core_restart_attempted = False
            try:
                if backend.PROFILE_PATH.is_file():
                    backend.prepare_runtime(settings=settings)
                if was_active:
                    self.ensure_flclash_stopped()
                    # A failed or timed-out restart can still have changed the
                    # live core, so every later rollback must be fail-open safe.
                    core_restart_attempted = True
                    restarted = backend.systemctl_service(
                        "restart", backend.SERVICE_UNIT, timeout=SERVICE_START_TIMEOUT
                    )
                    if restarted.returncode != 0:
                        raise backend.SmartBoxError(
                            restarted.stdout.strip() or "重新加载核心失败"
                        )
                    probe = self.verify_service_ready_then_probe()
                    self.require_usable_connectivity(
                        probe, str(settings.get("mode", "Rule"))
                    )
                    if not probe.get("stable"):
                        return (
                            "域名名单已应用，核心保持接管；公网复检暂未稳定："
                            f"{backend.format_connectivity_result(probe)}"
                        )
            except Exception as error:
                recovery_failures: list[str] = []
                restored_settings: dict[str, Any] | None = None
                try:
                    def restore_domains(current: dict[str, Any]) -> None:
                        if (
                            current.get("allow_domains") != allow
                            or current.get("proxy_domains") != proxy
                        ):
                            return
                        current["allow_domains"] = previous_domains["allow"]
                        current["proxy_domains"] = previous_domains["proxy"]

                    restored_settings = backend.mutate_settings(restore_domains)
                except Exception as recovery_error:  # noqa: BLE001 - retain all causes
                    recovery_failures.append(f"恢复旧设置失败：{recovery_error}")
                if backend.PROFILE_PATH.is_file() and not recovery_failures:
                    try:
                        backend.prepare_runtime(settings=restored_settings)
                    except Exception as recovery_error:  # noqa: BLE001
                        recovery_failures.append(f"恢复旧运行配置失败：{recovery_error}")
                if core_restart_attempted and not recovery_failures:
                    try:
                        restored = backend.systemctl_service(
                            "restart",
                            backend.SERVICE_UNIT,
                            timeout=SERVICE_START_TIMEOUT,
                        )
                        if restored.returncode != 0:
                            raise backend.SmartBoxError(
                                restored.stdout.strip() or "恢复旧域名名单核心失败"
                            )
                        self.verify_service_ready()
                    except Exception as recovery_error:  # noqa: BLE001
                        recovery_failures.append(f"恢复旧核心失败：{recovery_error}")
                if recovery_failures:
                    combined = (
                        f"{error}；恢复旧域名名单或核心失败："
                        + "；".join(recovery_failures)
                    )
                    if not core_restart_attempted:
                        raise backend.SmartBoxError(combined) from error
                    try:
                        recovery = backend.recover_failed_switch(False)
                    except Exception as fail_open_error:  # noqa: BLE001
                        raise backend.SmartBoxError(
                            f"{combined}；自动回到直连也失败：{fail_open_error}"
                        ) from fail_open_error
                    raise backend.SmartBoxError(
                        f"{combined}；{recovery}，系统已回到直连"
                    ) from error
                restored_state = (
                    "旧域名名单和核心已恢复"
                    if core_restart_attempted
                    else "旧域名名单已恢复，当前核心未重启"
                )
                raise backend.SmartBoxError(f"{error}；{restored_state}") from error
            return f"域名名单已应用：直连 {len(allow)}，Smart {len(proxy)}"

        def done(result: Any, error: Exception | None) -> None:
            try:
                if error is not None:
                    self.notify(f"应用域名名单失败：{error}", error=True)
                    self.update_domain_counts()
                else:
                    self.settings = backend.load_settings()
                    self.load_domain_editors()
                    self.notify(str(result))
                self.poll()
            finally:
                self.allow_editor.setEnabled(True)
                self.proxy_editor.setEnabled(True)

        self.allow_editor.setEnabled(False)
        self.proxy_editor.setEnabled(False)
        self.domain_save_button.setEnabled(False)
        self.domain_reset_button.setEnabled(False)
        if not self.run_core_task(
            "domain-rules", apply_rules, done, "正在应用域名名单…"
        ):
            self.allow_editor.setEnabled(True)
            self.proxy_editor.setEnabled(True)
            self.update_domain_counts()

    def update_log_refresh_status(self) -> None:
        mode = "自动刷新开启 · 每 5 秒" if self.live_logs.isChecked() else "自动刷新暂停"
        self.log_refresh_status.setText(f"{mode} · {self.last_log_refresh_summary}")
        self.log_refresh_status.setToolTip(self.last_log_refresh_error)
        self.set_label_state(
            self.log_refresh_status,
            "error" if self.last_log_refresh_error else "muted",
        )

    def toggle_log_auto_refresh(self, checked: bool) -> None:
        self.log_auto_refresh_tick = 0
        self.last_log_refresh_error = ""
        try:
            self.settings = backend.mutate_settings(
                lambda settings: settings.__setitem__("log_auto_refresh", checked)
            )
        except OSError as error:
            self.live_logs.blockSignals(True)
            self.live_logs.setChecked(not checked)
            self.live_logs.blockSignals(False)
            self.last_log_refresh_summary = f"保存失败：{error}"
            self.last_log_refresh_error = str(error)
            self.update_log_refresh_status()
            return
        self.last_log_refresh_summary = "开关已生效"
        self.update_log_refresh_status()
        if checked:
            self.refresh_logs()

    def clear_log_view(self) -> None:
        self.log_content = ""
        self.filter_logs(self.log_search.text())
        self.last_log_refresh_summary = "视图已清空"
        self.last_log_refresh_error = ""
        self.update_log_refresh_status()

    def filter_logs(self, query: str) -> None:
        """Filter the cached log without losing it on screen or during refresh."""
        all_lines = self.log_content.splitlines() if self.log_content else []
        normalized = query.strip().casefold()
        visible_lines = (
            [line for line in all_lines if normalized in line.casefold()]
            if normalized
            else all_lines
        )
        scrollbar = self.log_view.verticalScrollBar()
        was_at_bottom = scrollbar.value() >= scrollbar.maximum() - 8
        self.log_view.setPlainText("\n".join(visible_lines))
        self.log_copy_button.setEnabled(bool(visible_lines))
        if was_at_bottom or not self.log_view.hasFocus():
            scrollbar.setValue(scrollbar.maximum())
        if normalized and not visible_lines:
            self.log_filter_status.setText(f"未找到 · 共 {len(all_lines)} 行")
            self.set_label_state(self.log_filter_status, "error")
        elif normalized:
            self.log_filter_status.setText(
                f"显示 {len(visible_lines)} / 共 {len(all_lines)} 行"
            )
            self.set_label_state(self.log_filter_status)
        else:
            self.log_filter_status.setText(f"共 {len(all_lines)} 行")
            self.set_label_state(self.log_filter_status)

    def copy_visible_logs(self) -> None:
        content = self.log_view.toPlainText()
        if not content:
            self.log_copy_button.setEnabled(False)
            return
        QApplication.clipboard().setText(content)
        self.notify(f"已复制当前可见日志：{len(content.splitlines())} 行")

    def refresh_logs(self) -> bool:
        if "logs" in self.tasks:
            return False
        self.log_refresh_button.setEnabled(False)
        self.log_refresh_button.setText("刷新中…")
        self.last_log_refresh_summary = "正在读取"
        self.last_log_refresh_error = ""
        self.update_log_refresh_status()

        def done(result: Any, error: Exception | None) -> None:
            self.log_refresh_button.setEnabled(True)
            self.log_refresh_button.setText("刷新")
            if error is not None:
                line_count = len(self.log_content.splitlines()) if self.log_content else 0
                self.last_log_refresh_summary = (
                    f"刷新失败 · 已保留 {line_count} 行"
                    if line_count
                    else "刷新失败 · 无可保留日志"
                )
                self.last_log_refresh_error = str(error)
                self.update_log_refresh_status()
                return
            content = str(result).rstrip()
            self.log_content = content
            self.filter_logs(self.log_search.text())
            line_count = len(content.splitlines()) if content else 0
            refreshed = dt.datetime.now().strftime("%H:%M:%S")
            self.last_log_refresh_summary = f"{refreshed} · {line_count} 行"
            self.last_log_refresh_error = ""
            self.update_log_refresh_status()

        started = self.run_task("logs", lambda: backend.read_service_log(500), done)
        if not started:
            self.log_refresh_button.setEnabled(True)
            self.log_refresh_button.setText("刷新")
        return started

    def load_settings_into_ui(self) -> None:
        self.settings = backend.load_settings()
        url = self.settings.get("subscription_url", "")
        self.saved_subscription_url = str(url)
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            parsed = urllib.parse.SplitResult("", "", "", "", "")
        scheme = parsed.scheme if parsed.scheme in ("http", "https") else "http"
        self.protocol_box.setCurrentIndex(1 if scheme == "https" else 0)
        self.host_edit.setText(parsed.hostname or "")
        try:
            port = parsed.port
        except ValueError:
            port = None
        self.port_spin.setValue(port or (443 if scheme == "https" else 80))
        private_path = parsed.path or ""
        if parsed.query:
            private_path += "?" + parsed.query
        self.path_edit.setText(private_path)
        self.autostart_check.blockSignals(True)
        self.autostart_check.setChecked(backend.gui_autostart_enabled())
        self.autostart_check.blockSignals(False)
        theme = str(self.settings.get("theme", "light"))
        self.night_mode_check.blockSignals(True)
        self.night_mode_check.setChecked(theme == "dark")
        self.night_mode_check.blockSignals(False)
        self.live_logs.blockSignals(True)
        self.live_logs.setChecked(bool(self.settings.get("log_auto_refresh", True)))
        self.live_logs.blockSignals(False)
        self.update_log_refresh_status()
        stack = self.settings.get("tun_stack", "gvisor")
        stack_index = self.stack_box.findData(stack)
        self.updating_stack = True
        self.stack_box.blockSignals(True)
        self.stack_box.setCurrentIndex(max(0, stack_index))
        self.stack_box.blockSignals(False)
        self.updating_stack = False
        mode = self.settings.get("mode", "Rule")
        if mode in self.mode_buttons:
            self.mode_buttons[mode].setChecked(True)
        self.update_profile_labels()
        self.saved_endpoint_fields = self.endpoint_field_snapshot()
        self.update_endpoint_state()

    def endpoint_field_snapshot(self) -> tuple[int, str, int, str]:
        return (
            self.protocol_box.currentIndex(),
            self.host_edit.text(),
            self.port_spin.value(),
            self.path_edit.text(),
        )

    def build_subscription_url(self) -> str:
        scheme = "https" if self.protocol_box.currentIndex() == 1 else "http"
        host = self.host_edit.text().strip().strip("[]")
        if not host or any(character.isspace() or character == "/" for character in host):
            raise backend.SmartBoxError("请填写有效的域名或 IP")
        display_host = f"[{host}]" if ":" in host else host
        port = self.port_spin.value()
        path = self.path_edit.text().strip()
        if not path:
            raise backend.SmartBoxError("请填写私密订阅路径")
        if not path.startswith("/"):
            path = "/" + path
        if "#" in path:
            raise backend.SmartBoxError("私密订阅路径不能包含片段标识")
        path_part, separator, query = path.partition("?")
        url = urllib.parse.urlunsplit(
            (scheme, f"{display_host}:{port}", path_part, query if separator else "", "")
        )
        return backend.validate_subscription_url(url)

    def update_endpoint_state(self, *_args: Any) -> None:
        current_fields = self.endpoint_field_snapshot()
        raw_changed = (
            self.saved_endpoint_fields is not None
            and current_fields != self.saved_endpoint_fields
        )
        try:
            url = self.build_subscription_url()
        except backend.SmartBoxError as error:
            self.endpoint_validation_label.setText(str(error))
            self.set_label_state(self.endpoint_validation_label, "error")
            self.endpoint_validation_label.setToolTip(str(error))
            valid = False
            changed = False
            self.endpoint_dirty = raw_changed
        else:
            valid = True
            changed = url != self.saved_subscription_url
            self.endpoint_dirty = changed
            self.endpoint_validation_label.setText(
                "地址有效 · 有未保存更改" if changed else "地址有效 · 已保存"
            )
            self.set_label_state(
                self.endpoint_validation_label,
                "warning" if changed else "success",
            )
            self.endpoint_validation_label.setToolTip(
                "拉取时会先保存此地址" if changed else "当前输入与已保存地址一致"
            )
        core_busy = self.core_transaction is not None
        self.save_endpoint_button.setEnabled(
            valid and changed and not self.endpoint_busy
        )
        self.pull_button.setEnabled(
            valid and not self.endpoint_busy and not core_busy
        )
        self.pull_quick_button.setEnabled(
            valid and not self.endpoint_busy and not core_busy
        )

    def save_endpoint(self) -> None:
        try:
            url = self.build_subscription_url()
        except backend.SmartBoxError as error:
            self.notify(str(error), error=True)
            return
        try:
            self.settings = backend.mutate_settings(
                lambda settings: settings.__setitem__("subscription_url", url)
            )
        except OSError as error:
            self.notify(f"保存订阅地址失败：{error}", error=True)
            return
        self.saved_subscription_url = url
        self.saved_endpoint_fields = self.endpoint_field_snapshot()
        self.update_endpoint_state()
        self.notify("订阅地址已保存")

    def pull_profile(self) -> None:
        if not self.core_operation_available():
            return
        try:
            url = self.build_subscription_url()
        except backend.SmartBoxError as error:
            self.select_page(4)
            self.notify(str(error), error=True)
            return
        was_active = backend.unit_active(backend.SERVICE_UNIT)
        profile_path = backend.PROFILE_PATH
        runtime_path = backend.RUNTIME_PATH
        previous_profile = profile_path.read_bytes() if profile_path.is_file() else None
        previous_runtime = runtime_path.read_bytes() if runtime_path.is_file() else None
        previous_settings = backend.load_settings()
        self.set_pull_busy(True)

        def pull() -> dict[str, Any]:
            restore_flclash = backend.flclash_conflict()
            core_restart_attempted = False
            fetch_succeeded = False
            pull_settings_after: dict[str, Any] | None = None
            rollback_receipt: backend.ProfileUpdateReceipt | None = None

            try:
                summary = backend.fetch_profile(url)
                fetch_succeeded = True
                candidate_receipt = getattr(summary, "rollback_receipt", None)
                if isinstance(candidate_receipt, backend.ProfileUpdateReceipt):
                    rollback_receipt = candidate_receipt
                pull_settings_after = backend.load_settings()
                if was_active:
                    self.ensure_flclash_stopped()
                    # From this point the restart may have partially changed the
                    # live core even when systemctl reports a failure.
                    core_restart_attempted = True
                    restarted = backend.systemctl_service(
                        "restart", backend.SERVICE_UNIT, timeout=SERVICE_START_TIMEOUT
                    )
                    if restarted.returncode != 0:
                        raise backend.SmartBoxError(
                            restarted.stdout.strip() or "新配置保存成功，但核心重启失败"
                        )
                    probe = self.verify_service_ready_then_probe()
                    self.require_usable_connectivity(
                        probe,
                        str(
                            (pull_settings_after or self.settings).get(
                                "mode", "Rule"
                            )
                        ),
                    )
                    summary = {
                        **summary,
                        "degraded": not probe.get("stable"),
                        "connectivity": probe,
                    }
                return summary
            except Exception as error:
                newer_bundle_preserved = False
                try:
                    if core_restart_attempted:
                        stopped = backend.systemctl_service(
                            "stop", backend.SERVICE_UNIT, timeout=45
                        )
                        if stopped.returncode != 0:
                            raise backend.SmartBoxError(
                                stopped.stdout.strip() or "停止新配置核心失败"
                            )
                        backend.wait_for(
                            lambda: not backend.unit_active(backend.SERVICE_UNIT)
                            and not backend.interface_exists(backend.TUN_INTERFACE),
                            15,
                            0.25,
                        )
                    if rollback_receipt is not None:
                        newer_bundle_preserved = not backend.rollback_profile_update(
                            rollback_receipt
                        )
                    elif fetch_succeeded:
                        # Compatibility path for test doubles and older backend
                        # implementations that do not return a receipt.
                        for path, content in (
                            (profile_path, previous_profile),
                            (runtime_path, previous_runtime),
                        ):
                            if content is None:
                                path.unlink(missing_ok=True)
                            else:
                                backend.atomic_write_bytes(path, content)
                        if pull_settings_after is not None:
                            expected_url = pull_settings_after.get(
                                "subscription_url"
                            )
                            expected_pull = pull_settings_after.get(
                                "last_pull_utc"
                            )

                            def restore_pull_settings(
                                settings: dict[str, Any]
                            ) -> None:
                                if (
                                    settings.get("subscription_url")
                                    != expected_url
                                    or settings.get("last_pull_utc")
                                    != expected_pull
                                ):
                                    return
                                settings["subscription_url"] = (
                                    previous_settings.get(
                                        "subscription_url", ""
                                    )
                                )
                                settings["last_pull_utc"] = (
                                    previous_settings.get("last_pull_utc")
                                )

                            backend.mutate_settings(restore_pull_settings)
                    if core_restart_attempted:
                        self.ensure_flclash_stopped()
                        restored = backend.systemctl_service(
                            "start", backend.SERVICE_UNIT, timeout=SERVICE_START_TIMEOUT
                        )
                        if restored.returncode != 0:
                            raise backend.SmartBoxError(
                                restored.stdout.strip() or "恢复更新前核心失败"
                            )
                        self.verify_service_ready()
                except Exception as recovery_error:
                    combined = backend.SmartBoxError(
                        f"{error}；恢复更新前配置失败：{recovery_error}"
                    )
                    raise self.failed_switch_error(combined, restore_flclash) from error
                if newer_bundle_preserved:
                    restored_state = (
                        "检测到更晚的配置更新，未覆盖；核心已按当前配置恢复"
                        if core_restart_attempted
                        else "检测到更晚的配置更新，未覆盖；当前核心未重启"
                    )
                elif not fetch_succeeded:
                    restored_state = (
                        "订阅配置未发布，当前核心未重启"
                        if was_active
                        else "订阅配置未发布"
                    )
                else:
                    restored_state = (
                        "旧配置和核心已恢复"
                        if core_restart_attempted
                        else (
                            "旧配置已恢复，当前核心未重启"
                            if was_active
                            else "旧配置已恢复"
                        )
                    )
                raise backend.SmartBoxError(f"{error}；{restored_state}") from error

        def done(result: Any, error: Exception | None) -> None:
            try:
                self.settings = backend.load_settings()
                self.saved_subscription_url = str(
                    self.settings.get("subscription_url", "")
                )
                if error is None:
                    self.saved_endpoint_fields = self.endpoint_field_snapshot()
                self.update_profile_labels()
                if error is not None:
                    self.notify(f"拉取失败：{error}", error=True)
                else:
                    self.detail_values["配置节点"].setText(
                        str(result.get("nodes", 0))
                    )
                    message = (
                        f"配置已更新：{result.get('nodes', 0)} 个节点，"
                        f"{result.get('selectors', 0)} 个策略"
                    )
                    if result.get("degraded"):
                        connectivity = result.get("connectivity", {})
                        message += (
                            "；核心保持接管，公网复检暂未稳定："
                            f"{backend.format_connectivity_result(connectivity)}"
                        )
                    self.notify(message, error=bool(result.get("degraded")))
                    self.refresh_policies(silent=True)
                self.poll()
            finally:
                self.set_pull_busy(False)

        if not self.run_core_task(
            "profile-pull", pull, done, "正在拉取并校验配置…"
        ):
            self.set_pull_busy(False)

    def set_pull_busy(self, busy: bool) -> None:
        self.endpoint_busy = busy
        for widget in (
            self.protocol_box,
            self.host_edit,
            self.port_spin,
            self.path_edit,
        ):
            widget.setEnabled(not busy)
        self.reveal_action.setEnabled(not busy)
        if busy:
            self.set_path_visible(False)
        self.update_endpoint_state()
        self.pull_button.setText("正在拉取…" if busy else "保存并拉取")

    def toggle_path_visibility(self) -> None:
        self.set_path_visible(
            self.path_edit.echoMode() == QLineEdit.EchoMode.Password
        )

    def set_path_visible(self, visible: bool) -> None:
        self.path_edit.setEchoMode(
            QLineEdit.EchoMode.Normal
            if visible
            else QLineEdit.EchoMode.Password
        )
        if visible:
            self.path_visibility_timer.start()
            self.reveal_action.setToolTip("隐藏私密路径（15 秒后自动隐藏）")
            self.path_edit.setAccessibleDescription(
                "私密订阅路径当前可见，15 秒后自动隐藏"
            )
        else:
            self.path_visibility_timer.stop()
            self.reveal_action.setToolTip("临时显示私密路径 15 秒")
            self.path_edit.setAccessibleDescription("私密订阅路径已隐藏")

    def save_stack_setting(self) -> None:
        if self.updating_stack:
            return
        if self.core_transaction is not None:
            current_settings = backend.load_settings()
            current_stack = str(current_settings.get("tun_stack", "gvisor"))
            self.updating_stack = True
            self.stack_box.blockSignals(True)
            self.stack_box.setCurrentIndex(
                max(0, self.stack_box.findData(current_stack))
            )
            self.stack_box.blockSignals(False)
            self.updating_stack = False
            self.core_operation_available()
            return
        stack = self.stack_box.currentData()
        if stack not in backend.VALID_TUN_STACKS:
            return
        previous_settings = backend.load_settings()
        previous_stack = str(previous_settings.get("tun_stack", "gvisor"))
        previous_index = self.stack_box.findData(previous_stack)
        task_key = "stack-change"
        if task_key in self.tasks:
            self.updating_stack = True
            self.stack_box.blockSignals(True)
            self.stack_box.setCurrentIndex(max(0, previous_index))
            self.stack_box.blockSignals(False)
            self.updating_stack = False
            return
        self.stack_box.setEnabled(False)

        def apply_stack() -> str:
            previous_persisted: dict[str, str] = {}
            settings_committed = False
            try:
                def persist_stack(settings: dict[str, Any]) -> None:
                    previous_persisted["stack"] = str(
                        settings.get("tun_stack", "gvisor")
                    )
                    settings["tun_stack"] = stack

                settings = backend.mutate_settings(persist_stack)
                settings_committed = True
                if (
                    backend.PROFILE_PATH.is_file()
                    and not backend.unit_active(backend.SERVICE_UNIT)
                ):
                    backend.prepare_runtime(settings=settings)
            except Exception as error:
                if not settings_committed:
                    raise
                try:
                    def restore_stack(settings: dict[str, Any]) -> None:
                        if settings.get("tun_stack") == stack:
                            settings["tun_stack"] = previous_persisted["stack"]

                    backend.mutate_settings(restore_stack)
                except Exception as rollback_error:
                    raise backend.SmartBoxError(
                        f"{error}；恢复 TUN 栈设置失败：{rollback_error}"
                    ) from error
                raise
            return str(stack)

        def done(result: Any, error: Exception | None) -> None:
            self.settings = backend.load_settings()
            if error is not None:
                self.updating_stack = True
                self.stack_box.blockSignals(True)
                current_stack = str(self.settings.get("tun_stack", previous_stack))
                current_index = self.stack_box.findData(current_stack)
                self.stack_box.setCurrentIndex(max(0, current_index))
                self.stack_box.blockSignals(False)
                self.updating_stack = False
                self.notify(f"更新 TUN 栈失败：{error}", error=True)
            else:
                self.notify(f"TUN 栈已设置为 {result}")

        if not self.run_core_task(
            task_key, apply_stack, done, "正在更新 TUN 栈…"
        ):
            self.updating_stack = True
            self.stack_box.blockSignals(True)
            self.stack_box.setCurrentIndex(max(0, previous_index))
            self.stack_box.blockSignals(False)
            self.updating_stack = False
            self.refresh_core_action_availability()

    def toggle_autostart(self, checked: bool) -> None:
        try:
            backend.set_gui_autostart(checked)
        except OSError as error:
            self.autostart_check.blockSignals(True)
            self.autostart_check.setChecked(backend.gui_autostart_enabled())
            self.autostart_check.blockSignals(False)
            self.notify(f"更新登录启动失败：{error}", error=True)
            return
        self.notify("登录启动已开启" if checked else "登录启动已关闭")

    def toggle_night_mode(self, checked: bool) -> None:
        theme = "dark" if checked else "light"
        try:
            self.settings = backend.mutate_settings(
                lambda settings: settings.__setitem__("theme", theme)
            )
        except OSError as error:
            self.night_mode_check.blockSignals(True)
            self.night_mode_check.setChecked(not checked)
            self.night_mode_check.blockSignals(False)
            self.notify(f"更新夜间模式失败：{error}", error=True)
            return
        self.theme = theme
        application = QApplication.instance()
        if isinstance(application, QApplication):
            apply_application_theme(application, theme)
        self.traffic_chart.set_theme(theme)
        self.update_theme_accents()
        self.set_operation_state(self.operation_message, self.operation_state)
        self.notify("夜间模式已开启" if checked else "夜间模式已关闭")

    def benchmark_mirrors(self) -> None:
        repo = self.mirror_repo_box.currentData()
        if repo not in backend.MIRROR_PROFILES:
            return
        self.mirror_probe_button.setEnabled(False)
        self.mirror_apply_button.setEnabled(False)
        self.mirror_repo_box.setEnabled(False)
        self.mirror_probe_button.setText("测速中…")
        self.mirror_status_label.setText("正在读取并测试启用的 Server…")

        def done(result: Any, error: Exception | None) -> None:
            self.mirror_probe_button.setEnabled(True)
            self.mirror_repo_box.setEnabled(True)
            self.mirror_probe_button.setText("测速")
            if error is not None:
                summaries = (
                    self.mirror_summary.setdefault("summaries", {})
                    if isinstance(self.mirror_summary, dict)
                    else {}
                )
                if isinstance(summaries, dict):
                    summaries.pop(repo, None)
                self.mirror_status_label.setText("测速失败")
                self.notify(f"系统源测速失败：{error}", error=True)
                return
            result_summary = result if isinstance(result, dict) else {}
            selected = result_summary.get("summaries", {}).get(repo, {})
            if not isinstance(self.mirror_summary, dict):
                self.mirror_summary = {"summaries": {}}
            cached = self.mirror_summary.setdefault("summaries", {})
            if isinstance(cached, dict):
                cached[repo] = selected
            has_best = isinstance(selected, dict) and isinstance(selected.get("best"), dict)
            self.mirror_apply_button.setEnabled(has_best)
            self.mirror_status_label.setText(
                backend.format_mirror_benchmark_summary(
                    {"summaries": {repo: selected}}
                )
            )
            self.notify(
                "系统源测速完成，可应用最快源" if has_best else "系统源测速完成，但没有可用源",
                error=not has_best,
            )

        if not self.run_task(
            "mirror-benchmark",
            lambda: backend.benchmark_mirror_sources(repo=str(repo)),
            done,
            "正在测速系统源…",
        ):
            self.mirror_probe_button.setEnabled(True)
            self.mirror_repo_box.setEnabled(True)
            self.mirror_probe_button.setText("测速")
            self.mirror_status_label.setText("已有源测速任务进行中")

    def mirror_repository_changed(self, *_args: Any) -> None:
        repo = self.mirror_repo_box.currentData()
        summary = self.mirror_summary or {}
        selected = summary.get("summaries", {}).get(repo, {})
        has_best = isinstance(selected, dict) and isinstance(selected.get("best"), dict)
        busy = "mirror-benchmark" in self.tasks or "mirror-apply" in self.tasks
        self.mirror_apply_button.setEnabled(has_best and not busy)
        if busy:
            return
        if has_best:
            self.mirror_status_label.setText(
                backend.format_mirror_benchmark_summary(
                    {"summaries": {str(repo): selected}}
                )
            )
        else:
            label = self.mirror_repo_box.currentText()
            self.mirror_status_label.setText(f"{label} 尚未测速")

    def apply_mirror_ranking(self) -> None:
        repo = self.mirror_repo_box.currentData()
        summary = self.mirror_summary or {}
        selected = summary.get("summaries", {}).get(repo)
        if not isinstance(selected, dict) or not isinstance(selected.get("best"), dict):
            self.notify("请先完成源测速", error=True)
            return
        self.mirror_apply_button.setEnabled(False)
        self.mirror_probe_button.setEnabled(False)
        self.mirror_repo_box.setEnabled(False)
        self.mirror_status_label.setText("正在请求 root 授权并应用排序…")

        def done(result: Any, error: Exception | None) -> None:
            self.mirror_probe_button.setEnabled(True)
            self.mirror_repo_box.setEnabled(True)
            self.mirror_apply_button.setEnabled(error is not None)
            if error is not None:
                self.mirror_status_label.setText("应用失败")
                self.notify(f"应用最快源失败：{error}", error=True)
                return
            target = result.get("target", "") if isinstance(result, dict) else ""
            self.mirror_status_label.setText(f"已应用最快源：{target}")
            self.notify("最快源已应用；下次 pacman/paru 将优先使用它")

        if not self.run_task(
            "mirror-apply",
            lambda: backend.apply_mirror_ranking(selected),
            done,
            "正在应用最快源…",
        ):
            self.mirror_apply_button.setEnabled(True)
            self.mirror_probe_button.setEnabled(True)
            self.mirror_repo_box.setEnabled(True)
            self.mirror_status_label.setText("已有源应用任务进行中")

    def validate_runtime(self) -> None:
        def validate() -> str:
            path = backend.prepare_runtime()
            backend.validate_config(path)
            return "运行配置校验通过"

        self.run_task(
            "validate-runtime",
            validate,
            lambda result, error: self.notify(str(error), True)
            if error
            else self.notify(str(result)),
            "正在校验运行配置…",
        )

    def copy_proxy_address(self) -> None:
        QApplication.clipboard().setText(backend.MIXED_ADDRESS)
        self.notify("代理地址已复制")

    def tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_from_tray()

    def show_from_tray(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def handle_instance_command(self, command: str) -> bool:
        if command != INSTANCE_SHOW_COMMAND:
            return False
        self.show_from_tray()
        return True

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        if (self.exiting or self.closing) and not self.allow_exit:
            event.ignore()
            return
        if self.allow_exit:
            event.accept()
            return
        if self.tray_icon is None:
            self.exit_application()
            event.ignore()
            return
        event.ignore()
        self.hide()
        self.tray_icon.showMessage(
            "smart-box",
            "客户端仍在后台运行",
            QSystemTrayIcon.MessageIcon.Information,
            1800,
        )

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        if hasattr(self, "path_edit"):
            self.set_path_visible(False)
        super().hideEvent(event)

    def unsaved_edit_labels(self) -> list[str]:
        labels: list[str] = []
        if self.domain_dirty:
            labels.append("域名名单")
        if self.endpoint_dirty:
            labels.append("订阅地址")
        return labels

    def confirm_discard_unsaved_edits(self) -> bool:
        labels = self.unsaved_edit_labels()
        if not labels:
            return True
        if not self.isVisible():
            self.show_from_tray()
        choice = QMessageBox.question(
            self,
            "有未保存更改",
            "以下内容尚未保存："
            + "、".join(labels)
            + "。\n\n是否放弃更改并退出客户端？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice == QMessageBox.StandardButton.Yes:
            return True
        self.show_from_tray()
        self.select_page(2 if self.domain_dirty else 4)
        target = self.allow_editor if self.domain_dirty else self.host_edit
        target.setFocus(Qt.FocusReason.OtherFocusReason)
        return False

    def exit_application(self, _checked: bool = False) -> None:
        if self.exiting or self.closing:
            return
        if not self.confirm_discard_unsaved_edits():
            return
        self.closing = True
        self.exiting = True
        self.poll_timer.stop()
        if self.tray_icon is not None:
            self.tray_icon.hide()
        if not self.tasks and self.thread_pool.activeThreadCount() == 0:
            self.allow_exit = True
            QApplication.quit()
            return
        self.set_operation_state("正在等待后台操作完成…", "working")
        self.exit_wait_timer = QTimer(self)
        self.exit_wait_timer.setInterval(25)
        self.exit_wait_timer.timeout.connect(self._finish_exit_when_idle)
        self.exit_wait_timer.start()

    def _finish_exit_when_idle(self) -> None:
        if self.tasks or self.thread_pool.activeThreadCount() > 0:
            return
        if self.exit_wait_timer is not None:
            self.exit_wait_timer.stop()
        self.allow_exit = True
        QApplication.quit()

    def save_screenshot(self) -> None:
        if not self.screenshot_path:
            return
        self.show()
        QApplication.processEvents()
        target = Path(self.screenshot_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        self.grab().save(str(target), "PNG")
        self.exit_application()


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="smart-box")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--screenshot", metavar="PATH")
    parser.add_argument("--version", action="version", version=f"smart-box {backend.APP_VERSION}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    application = QApplication.instance() or QApplication(sys.argv[:1])
    instance_lock = acquire_instance_lock()
    if instance_lock is None:
        command = instance_command_for_arguments(arguments)
        if command is None:
            return 0
        return 0 if send_instance_command(command) else 1
    application.setApplicationName(APP_TITLE)
    application.setApplicationDisplayName(APP_TITLE)
    application.setApplicationVersion(backend.APP_VERSION)
    application.setOrganizationName("smart-box")
    application.setQuitOnLastWindowClosed(False)
    try:
        backend.recover_profile_transaction()
        settings = backend.load_settings()
    except backend.SmartBoxError as error:
        QMessageBox.critical(
            None,
            "配置恢复失败",
            f"无法安全恢复上次中断的配置更新：\n\n{error}",
        )
        release_instance_lock(instance_lock)
        return 1
    apply_application_theme(application, str(settings.get("theme", "light")))
    window: MainWindow | None = None
    command_server = InstanceCommandServer(
        lambda command: window.handle_instance_command(command) if window is not None else False
    )
    command_server_ready = command_server.listen()
    try:
        window = MainWindow(
            arguments.background and command_server_ready,
            arguments.screenshot,
        )
        if not command_server_ready:
            window.set_operation_state(
                "后台唤醒通道不可用，窗口将保持显示",
                "error",
            )
        if not arguments.background or arguments.screenshot or not command_server_ready:
            window.show()
        return application.exec()
    finally:
        command_server.close()
        release_instance_lock(instance_lock)


if __name__ == "__main__":
    raise SystemExit(main())
