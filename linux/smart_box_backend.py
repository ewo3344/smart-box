#!/usr/bin/python3
"""Runtime profile and service helpers for the smart-box Linux client."""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import datetime as dt
import fcntl
import hashlib
import ipaddress
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable


APP_VERSION = "0.1.1"
CORE_VERSION = "1.14.0-beta.14"
SERVICE_USER = pwd.getpwuid(os.getuid()).pw_name
SERVICE_UNIT = os.environ.get("SMART_BOX_SERVICE_UNIT", f"smart-box@{SERVICE_USER}.service")
FLCLASH_UNIT = "app-FlClash@autostart.service"
GUI_LAUNCHER = "/usr/local/bin/smart-box"
API_BASE = "http://127.0.0.1:20809"
MIXED_ADDRESS = "127.0.0.1:20808"
CONNECTIVITY_PROBE_URL = "https://www.gstatic.com/generate_204"
DIRECT_CONNECTIVITY_PROBE_URL = "https://www.baidu.com/"
CONNECTIVITY_PROBES = (
    {"key": "domestic", "label": "国内直连", "url": "https://www.baidu.com/"},
    {"key": "basic", "label": "基础联网", "url": CONNECTIVITY_PROBE_URL},
    {
        "key": "proxy",
        "label": "代理链路",
        # Linux runtime rules force this hostname through the baseline Smart
        # outbound before the energy-saving direct catch-all.
        "url": "https://clientservices.googleapis.com/generate_204",
    },
    {"key": "github", "label": "GitHub", "url": "https://github.com/"},
    {"key": "telegram", "label": "Telegram", "url": "https://telegram.org/"},
)
TUN_INTERFACE = "SmartBox"
FLCLASH_INTERFACE = "FlClash"
UFW_COMMAND = Path("/usr/bin/ufw")
UFW_USER_RULES_PATH = Path("/etc/ufw/user.rules")
UFW_USER6_RULES_PATH = Path("/etc/ufw/user6.rules")
UFW_TUN_RULE_COMMENT = "smart-box-tun-return-path"
UFW_TUN_RULE_COMMENT_HEX = UFW_TUN_RULE_COMMENT.encode("utf-8").hex()
VALID_MODES = ("Rule", "Global", "Direct", "节能")
VALID_TUN_STACKS = ("system", "mixed", "gvisor")
VALID_THEMES = ("light", "dark")
WATCHDOG_STARTUP_TIMEOUT = 45.0
WATCHDOG_PROBE_TIMEOUT = 6.0
WATCHDOG_GUARD_INTERVAL = 1.0
WATCHDOG_LOOP_INTERVAL = 8.0
WATCHDOG_FAILURE_LIMIT = 2
SERVICE_UNIT_NAME = re.compile(
    r"^smart-box@(?P<instance>[A-Za-z0-9][A-Za-z0-9_.-]*)\.service$"
)
SERVICE_UNMASK_UNIT_TEMPLATE = "smart-box-unmask@{instance}.service"
SERVICE_CLEANUP_UNIT_TEMPLATE = "smart-box-cleanup@{instance}.service"

# Keep SmartBox's policy-routing namespace separate from sing-tun's generic
# 2022/9000 defaults, which are also used by FlClash and other sing-box clients.
SMART_BOX_ROUTE_TABLE_INDEX = 20228
SMART_BOX_ROUTE_RULE_INDEX = 12000
SMART_BOX_ROUTE_RULE_LAST = SMART_BOX_ROUTE_RULE_INDEX + 10
SMART_BOX_AUTO_REDIRECT_FALLBACK_RULE_INDEX = 32788
LEGACY_SING_TUN_ROUTE_TABLE_INDEX = 2022
LEGACY_SING_TUN_ROUTE_RULE_INDEX = 9000
LEGACY_SING_TUN_ROUTE_RULE_LAST = LEGACY_SING_TUN_ROUTE_RULE_INDEX + 10
LEGACY_SING_TUN_AUTO_REDIRECT_FALLBACK_RULE_INDEX = 32768
SING_BOX_NFTABLE_FAMILY = "inet"
SING_BOX_NFTABLE_NAME = "sing-box"
IP_COMMAND = "/usr/bin/ip"
NFT_COMMAND = "/usr/bin/nft"

DIRECT_OUTBOUND = "DIRECT"
BASELINE_OUTBOUND = "🎯 基准 Smart"
TELEGRAM_OUTBOUND = "✈️ Telegram Smart"
LOCAL_DNS = "local"
BASELINE_DNS = "baseline-dns"
TELEGRAM_DNS = "telegram-dns"
BOOTSTRAP_DNS = "bootstrap-dns"
BOOTSTRAP_OPTIMISTIC_TIMEOUT = "10m"
TELEGRAM_IP_RULE_SET = "telegram-ip"
SELECTOR_CACHE_ID_PREFIX = "smart-box-linux-v2"
SMART_SCORE_NAMESPACE = "smart-box-nodes-v1"
LINUX_TELEGRAM_PROCESSES = ("Telegram", "telegram-desktop")
RELIABILITY_PROXY_DOMAINS = (
    "clientservices.googleapis.com",
    "update.googleapis.com",
    "clients2.google.com",
    "clients.l.google.com",
)
LINUX_TUN_ROUTE_EXCLUDE_ADDRESSES = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "224.0.0.0/4",
    "255.255.255.255/32",
    "fc00::/7",
    "fe80::/10",
    "ff00::/8",
)
LINUX_LOCAL_MULTICAST_CIDRS = (
    "224.0.0.0/4",
    "255.255.255.255/32",
    "ff00::/8",
)


def _proxy_outbound_for_runtime(profile: dict[str, Any]) -> str:
    """Return the normal proxy selector used by Linux-only runtime safeguards."""
    outbounds = _required_list(profile, "outbounds")
    outbound_objects = [item for item in outbounds if isinstance(item, dict)]
    by_tag = {
        item["tag"]: item
        for item in outbound_objects
        if isinstance(item.get("tag"), str)
    }
    proxy_types = ("selector", "smart")
    if by_tag.get(BASELINE_OUTBOUND, {}).get("type") in proxy_types:
        return BASELINE_OUTBOUND

    route_final = _required_dict(profile, "route").get("final")
    if (
        isinstance(route_final, str)
        and by_tag.get(route_final, {}).get("type") in proxy_types
    ):
        return route_final

    for outbound_type in proxy_types:
        for outbound in outbound_objects:
            if outbound.get("type") != outbound_type or not isinstance(
                outbound.get("tag"), str
            ):
                continue
            return outbound["tag"]
    raise SmartBoxError("当前配置没有可用于可靠性规则的 Smart 出站")


def _proxy_dns_for_runtime(profile: dict[str, Any], proxy_outbound: str) -> str:
    """Return a remote DNS server that cannot silently degrade to DIRECT/local."""
    dns = _required_dict(profile, "dns")
    dns_servers = _required_list(dns, "servers")
    server_objects = [item for item in dns_servers if isinstance(item, dict)]
    by_tag = {
        item["tag"]: item
        for item in server_objects
        if isinstance(item.get("tag"), str)
    }

    baseline = by_tag.get(BASELINE_DNS)
    if baseline is not None and baseline.get("type") != "local":
        return BASELINE_DNS

    for server in server_objects:
        if (
            server.get("type") != "local"
            and server.get("detour") == proxy_outbound
            and isinstance(server.get("tag"), str)
        ):
            return server["tag"]

    dns_final = dns.get("final")
    final_server = by_tag.get(dns_final) if isinstance(dns_final, str) else None
    if (
        final_server is not None
        and final_server.get("type") != "local"
        and final_server.get("detour") != DIRECT_OUTBOUND
    ):
        return dns_final

    for server in server_objects:
        if (
            server.get("type") != "local"
            and server.get("detour") != DIRECT_OUTBOUND
            and isinstance(server.get("tag"), str)
        ):
            return server["tag"]
    raise SmartBoxError("当前配置无法为关键服务应用 Smart DNS")


def _xdg_path(env_name: str, fallback: str) -> Path:
    value = os.environ.get(env_name)
    return Path(value).expanduser() if value else Path.home() / fallback


CONFIG_DIR = _xdg_path("XDG_CONFIG_HOME", ".config") / "smart-box"
STATE_DIR = _xdg_path("XDG_STATE_HOME", ".local/state") / "smart-box"
PROFILE_PATH = CONFIG_DIR / "profile.json"
RUNTIME_PATH = CONFIG_DIR / "runtime.json"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
SWITCH_STATE_PATH = STATE_DIR / "switch-state.json"
CACHE_PATH = STATE_DIR / "cache.db"
DESKTOP_PROXY_CONFIG_PATH = _xdg_path("XDG_CONFIG_HOME", ".config") / "kioslaverc"
DESKTOP_PROXY_STATE_PATH = STATE_DIR / "desktop-proxy.json"
DESKTOP_PROXY_BACKUP_PATH = STATE_DIR / "kioslaverc.before-smart-box"
DESKTOP_PROXY_SECTION = "Proxy Settings"
DESKTOP_PROXY_VALUES = {
    "ProxyType": "1",
    "httpProxy": "http://127.0.0.1:20808",
    "httpsProxy": "http://127.0.0.1:20808",
    "socksProxy": "socks://127.0.0.1:20808",
}

# Official package downloads used by both pacman and paru follow pacman's
# mirrorlists.  Keep the source benchmark read-only until the user explicitly
# applies the ranked result from the GUI/CLI.
PACMAN_MIRRORLIST_PATH = Path("/etc/pacman.d/mirrorlist")
CACHYOS_MIRRORLIST_PATH = Path("/etc/pacman.d/cachyos-mirrorlist")
CACHYOS_V3_MIRRORLIST_PATH = Path("/etc/pacman.d/cachyos-v3-mirrorlist")
USER_PACMAN_MIRRORLIST_PATH = Path.home() / ".config/pacman/mirrorlist"
USER_CACHYOS_MIRRORLIST_PATH = Path.home() / ".config/pacman/cachyos-mirrorlist"
USER_CACHYOS_V3_MIRRORLIST_PATH = Path.home() / ".config/pacman/cachyos-v3-mirrorlist"
MIRROR_RANKING_DIR = STATE_DIR / "mirror-rankings"
MIRROR_PROFILES: dict[str, dict[str, Any]] = {
    "arch": {
        "label": "pacman / paru 官方源",
        "path": PACMAN_MIRRORLIST_PATH,
        "apply_paths": (
            PACMAN_MIRRORLIST_PATH,
            USER_PACMAN_MIRRORLIST_PATH,
        ),
        "repo": "core",
        "test_suffix": "core.files",
    },
    "cachyos": {
        "label": "CachyOS 源",
        "path": CACHYOS_MIRRORLIST_PATH,
        "apply_paths": (
            CACHYOS_MIRRORLIST_PATH,
            CACHYOS_V3_MIRRORLIST_PATH,
            USER_CACHYOS_MIRRORLIST_PATH,
            USER_CACHYOS_V3_MIRRORLIST_PATH,
        ),
        "repo": "cachyos",
        "test_suffix": "cachyos.files",
    },
}
MIRROR_PROBE_BYTES = 128 * 1024
MIRROR_BENCHMARK_TIMEOUT = 8.0
MIRROR_BENCHMARK_MAX_MIRRORS = 128
MIRROR_SERVER_RE = re.compile(r"^\s*Server\s*=\s*(\S+)\s*$", re.IGNORECASE)


def find_core() -> Path:
    override = os.environ.get("SMART_BOX_CORE")
    candidates = [
        Path(override).expanduser() if override else None,
        Path("/usr/local/lib/smart-box/smart-box-core"),
        Path(__file__).resolve().parent.parent / "bin" / "smart-box-core",
        Path(__file__).resolve().parent.parent
        / "dist"
        / f"smart-box-{APP_VERSION}-linux-x86_64"
        / "bin"
        / "smart-box-core",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return candidates[1]  # type: ignore[return-value]


DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "subscription_url": "",
    "last_pull_utc": None,
    "mode": "Rule",
    "tun_stack": "gvisor",
    "log_level": "info",
    "allow_domains": [],
    "proxy_domains": [],
    "selector_overrides": {},
    "open_at_login": False,
    "theme": "light",
    "log_auto_refresh": True,
}

# Every setting consumed by ``apply_runtime_overrides`` belongs here.  Profile
# refresh and runtime preparation compare this compact snapshot immediately
# before replacing files, so a concurrent writer cannot leave settings.json
# describing a different runtime.json than the one that was just validated.
RUNTIME_SETTINGS_FIELDS = (
    "mode",
    "tun_stack",
    "log_level",
    "allow_domains",
    "proxy_domains",
    "selector_overrides",
)
CONFIG_SNAPSHOT_MAX_RETRIES = 4


class SmartBoxError(RuntimeError):
    pass


class ProfileUpdateReceipt:
    """Opaque in-process token for a conditional profile bundle rollback."""

    def __init__(
        self,
        *,
        previous_profile: tuple[bytes, int] | None,
        previous_runtime: tuple[bytes, int] | None,
        previous_subscription_url: Any,
        previous_last_pull_utc: Any,
        profile_sha256: str,
        runtime_sha256: str,
        runtime_settings: dict[str, Any],
        subscription_url: str,
        last_pull_utc: str,
    ) -> None:
        self.previous_profile = previous_profile
        self.previous_runtime = previous_runtime
        self.previous_subscription_url = copy.deepcopy(previous_subscription_url)
        self.previous_last_pull_utc = copy.deepcopy(previous_last_pull_utc)
        self.profile_sha256 = profile_sha256
        self.runtime_sha256 = runtime_sha256
        self.runtime_settings = copy.deepcopy(runtime_settings)
        self.subscription_url = subscription_url
        self.last_pull_utc = last_pull_utc


class ProfileUpdateResult(dict[str, Any]):
    """JSON-compatible summary carrying a private GUI rollback receipt."""

    def __init__(
        self, summary: dict[str, Any], receipt: ProfileUpdateReceipt
    ) -> None:
        super().__init__(summary)
        self.rollback_receipt = receipt


class ServiceUnitMaskError(SmartBoxError):
    """Expose the exact unit, operation, and systemd state behind mask failures."""

    def __init__(
        self,
        stage: str,
        units: Iterable[str],
        detail: str,
        states: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.stage = stage
        self.units = tuple(units)
        self.detail = detail
        self.states = {
            unit: dict(state) for unit, state in (states or {}).items()
        }
        unit_text = ", ".join(self.units) or "<none>"
        state_text = "; ".join(
            f"{unit}: LoadState={state.get('LoadState', '<missing>')}, "
            f"UnitFileState={state.get('UnitFileState', '<missing>')}"
            for unit, state in self.states.items()
        )
        suffix = f"；{state_text}" if state_text else ""
        super().__init__(
            f"smart-box 服务运行期 mask 处理失败 [{stage}]：{unit_text}{suffix}；{detail}"
        )


def ensure_directories() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(CONFIG_DIR, 0o700)
    os.chmod(STATE_DIR, 0o700)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_bytes(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any, mode: int = 0o600) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    atomic_write_bytes(path, content.encode("utf-8"), mode)


def _kioslaverc_proxy_content(content: bytes) -> bytes:
    """Replace only KDE's proxy keys while preserving all other settings."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SmartBoxError("KDE 代理配置不是有效 UTF-8") from error
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    section_start: int | None = None
    section_end: int | None = None
    for index, line in enumerate(lines):
        match = re.match(r"^\s*\[([^]]+)\]", line.rstrip("\r\n"))
        if match and match.group(1).strip().casefold() == DESKTOP_PROXY_SECTION.casefold():
            section_start = index
            continue
        if section_start is not None and match:
            section_end = index
            break

    if section_start is None:
        if text and not text.endswith(("\n", "\r")):
            lines.append(newline)
        lines.append(f"[{DESKTOP_PROXY_SECTION}]{newline}")
        lines.extend(f"{key}={value}{newline}" for key, value in DESKTOP_PROXY_VALUES.items())
        return "".join(lines).encode("utf-8")

    if section_end is None:
        section_end = len(lines)
    present: set[str] = set()
    key_patterns = {
        key.casefold(): re.compile(rf"^\s*{re.escape(key)}\s*=", re.IGNORECASE)
        for key in DESKTOP_PROXY_VALUES
    }
    for index in range(section_start + 1, section_end):
        line = lines[index]
        for key, pattern in key_patterns.items():
            if not pattern.match(line):
                continue
            actual_key = next(
                candidate for candidate in DESKTOP_PROXY_VALUES if candidate.casefold() == key
            )
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else newline
            lines[index] = f"{actual_key}={DESKTOP_PROXY_VALUES[actual_key]}{ending}"
            present.add(key)
            break
    missing = [
        (key, value)
        for key, value in DESKTOP_PROXY_VALUES.items()
        if key.casefold() not in present
    ]
    if missing:
        if section_end > section_start + 1 and not lines[section_end - 1].endswith(("\n", "\r")):
            lines[section_end - 1] += newline
        lines[section_end:section_end] = [
            f"{key}={value}{newline}" for key, value in missing
        ]
    return "".join(lines).encode("utf-8")


def _desktop_proxy_state() -> dict[str, Any] | None:
    if not DESKTOP_PROXY_STATE_PATH.is_file():
        return None
    try:
        value = load_json(DESKTOP_PROXY_STATE_PATH)
    except (OSError, ValueError, TypeError) as error:
        raise SmartBoxError(f"读取 KDE 代理状态失败：{error}") from error
    if not isinstance(value, dict) or not isinstance(value.get("active_sha256"), str):
        raise SmartBoxError("KDE 代理状态文件格式无效")
    return value


def desktop_proxy_install() -> bool:
    """Point KDE applications at SmartBox's local mixed proxy while active."""
    ensure_directories()
    existing_state = _desktop_proxy_state()
    if existing_state is not None:
        # A previous unclean stop may have left the desktop proxy switched.
        # Restore only when the file still contains our exact last write.
        desktop_proxy_restore()

    original_exists = DESKTOP_PROXY_CONFIG_PATH.is_file()
    original = DESKTOP_PROXY_CONFIG_PATH.read_bytes() if original_exists else b""
    original_stat = DESKTOP_PROXY_CONFIG_PATH.stat() if original_exists else None
    updated = _kioslaverc_proxy_content(original)
    if updated == original:
        return False

    if original_exists:
        atomic_write_bytes(DESKTOP_PROXY_BACKUP_PATH, original)
    else:
        DESKTOP_PROXY_BACKUP_PATH.unlink(missing_ok=True)
    atomic_write_bytes(DESKTOP_PROXY_CONFIG_PATH, updated)
    try:
        atomic_write_json(
            DESKTOP_PROXY_STATE_PATH,
            {
                "version": 1,
                "original_exists": original_exists,
                "active_sha256": hashlib.sha256(updated).hexdigest(),
                "original_uid": original_stat.st_uid if original_stat else None,
                "original_gid": original_stat.st_gid if original_stat else None,
                "original_mode": (original_stat.st_mode & 0o7777) if original_stat else None,
            },
        )
    except Exception as error:  # noqa: BLE001 - retain state and compensation failures
        compensation_errors: list[str] = []

        def compensate(label: str, operation: Any) -> None:
            try:
                operation()
            except Exception as compensation_error:  # noqa: BLE001 - continue compensation
                compensation_errors.append(f"{label}：{compensation_error}")

        if original_exists and original_stat is not None:
            compensate(
                "恢复 KDE 代理文件内容失败",
                lambda: atomic_write_bytes(DESKTOP_PROXY_CONFIG_PATH, original),
            )
            # Keep trying the metadata steps even when restoring the contents
            # failed: each successful part reduces the damage left behind.
            compensate(
                "恢复 KDE 代理文件属主失败",
                lambda: os.chown(
                    DESKTOP_PROXY_CONFIG_PATH,
                    original_stat.st_uid,
                    original_stat.st_gid,
                ),
            )
            compensate(
                "恢复 KDE 代理文件权限失败",
                lambda: os.chmod(
                    DESKTOP_PROXY_CONFIG_PATH, original_stat.st_mode & 0o7777
                ),
            )
        else:
            compensate(
                "删除新建的 KDE 代理文件失败",
                lambda: DESKTOP_PROXY_CONFIG_PATH.unlink(missing_ok=True),
            )
        compensate(
            "清理 KDE 代理备份失败",
            lambda: DESKTOP_PROXY_BACKUP_PATH.unlink(missing_ok=True),
        )
        compensate(
            "清理 KDE 代理状态失败",
            lambda: DESKTOP_PROXY_STATE_PATH.unlink(missing_ok=True),
        )

        detail = f"写入 KDE 代理状态失败：{error}"
        if compensation_errors:
            detail += "；安装失败补偿也失败：" + "；".join(compensation_errors)
        raise SmartBoxError(detail) from error
    return True


def desktop_proxy_restore() -> bool:
    """Restore KDE proxy settings only if they were not changed by the user."""
    state = _desktop_proxy_state()
    if state is None:
        return False
    current = DESKTOP_PROXY_CONFIG_PATH.read_bytes() if DESKTOP_PROXY_CONFIG_PATH.is_file() else b""
    active_sha256 = state["active_sha256"]
    if hashlib.sha256(current).hexdigest() == active_sha256:
        if state.get("original_exists") is True:
            if not DESKTOP_PROXY_BACKUP_PATH.is_file():
                raise SmartBoxError("KDE 代理备份文件缺失")
            atomic_write_bytes(DESKTOP_PROXY_CONFIG_PATH, DESKTOP_PROXY_BACKUP_PATH.read_bytes())
            # The independent root cleanup helper may perform this restore.
            # Put the file back under the desktop user's ownership so the next
            # GUI/service start can still update KDE settings.
            owner = state.get("original_uid"), state.get("original_gid")
            if all(isinstance(value, int) and value >= 0 for value in owner):
                try:
                    os.chown(DESKTOP_PROXY_CONFIG_PATH, owner[0], owner[1])
                except OSError as error:
                    raise SmartBoxError(f"恢复 KDE 代理文件属主失败：{error}") from error
            mode = state.get("original_mode")
            if isinstance(mode, int) and mode >= 0:
                try:
                    os.chmod(DESKTOP_PROXY_CONFIG_PATH, mode)
                except OSError as error:
                    raise SmartBoxError(f"恢复 KDE 代理文件权限失败：{error}") from error
        else:
            DESKTOP_PROXY_CONFIG_PATH.unlink(missing_ok=True)
    DESKTOP_PROXY_STATE_PATH.unlink(missing_ok=True)
    DESKTOP_PROXY_BACKUP_PATH.unlink(missing_ok=True)
    return True


def _clean_settings(settings: dict[str, Any]) -> dict[str, Any]:
    clean = copy.deepcopy(DEFAULT_SETTINGS)
    clean.update(settings)
    clean.pop("restore_flclash_on_stop", None)
    clean["version"] = 1
    return clean


def _load_settings_unlocked() -> dict[str, Any]:
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    if SETTINGS_PATH.is_file():
        try:
            stored = load_json(SETTINGS_PATH)
            if isinstance(stored, dict):
                settings.update(stored)
        except (OSError, ValueError, TypeError):
            pass
    if settings.get("mode") not in VALID_MODES:
        settings["mode"] = "Rule"
    if settings.get("tun_stack") not in VALID_TUN_STACKS:
        settings["tun_stack"] = "gvisor"
    if settings.get("theme") not in VALID_THEMES:
        settings["theme"] = "light"
    if not isinstance(settings.get("log_auto_refresh"), bool):
        settings["log_auto_refresh"] = True
    for field in ("allow_domains", "proxy_domains"):
        if not isinstance(settings.get(field), list):
            settings[field] = []
    if not isinstance(settings.get("selector_overrides"), dict):
        settings["selector_overrides"] = {}
    settings.pop("restore_flclash_on_stop", None)
    return settings


def load_settings() -> dict[str, Any]:
    # A reader must not cross a prepared bundle transaction and observe settings
    # that can still be rolled back.  Internal transaction paths use the private
    # unlocked loader after acquiring this same lock.
    descriptor = _open_settings_read_lock()
    try:
        _recover_profile_transaction_unlocked()
        return _load_settings_unlocked()
    finally:
        os.close(descriptor)


def _settings_lock_path() -> Path:
    # SETTINGS_PATH is atomically replaced, so locking that inode would let a
    # later opener bypass the lock.  Keep a stable sibling solely for locking.
    return SETTINGS_PATH.with_name(f".{SETTINGS_PATH.name}.lock")


def _open_settings_lock() -> int:
    ensure_directories()
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        _settings_lock_path(),
        os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _open_settings_read_lock() -> int:
    """Join the writer lock without requiring a writable configuration mount.

    The root watchdog intentionally sees ``ProtectHome=read-only``.  Its reads
    still need to serialize with desktop writers, but opening the pre-created
    lock inode read-only is sufficient for ``flock(LOCK_EX)`` on Linux.  A
    normal first-run caller falls back to creating the lock as a writer.
    """
    try:
        descriptor = os.open(
            _settings_lock_path(),
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
        )
    except FileNotFoundError:
        return _open_settings_lock()
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _save_settings_unlocked(settings: dict[str, Any]) -> dict[str, Any]:
    clean = _clean_settings(settings)
    atomic_write_json(SETTINGS_PATH, clean)
    return clean


def save_settings(settings: dict[str, Any]) -> None:
    """Replace the complete settings document under the writer lock.

    Callers changing only part of the document must use ``mutate_settings`` so
    a snapshot loaded before this lock cannot overwrite another writer.
    """
    descriptor = _open_settings_lock()
    try:
        _recover_profile_transaction_unlocked()
        _save_settings_unlocked(settings)
    finally:
        os.close(descriptor)


def mutate_settings(
    mutator: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Atomically mutate a freshly loaded settings snapshot across processes.

    The callback runs while the short-lived writer lock is held and therefore
    must only modify the supplied dictionary; it must not perform network or
    subprocess work, wait on another task, or call settings helpers recursively.
    If it raises, no settings write is attempted.
    """
    descriptor = _open_settings_lock()
    try:
        _recover_profile_transaction_unlocked()
        current = _load_settings_unlocked()
        candidate = copy.deepcopy(current)
        mutator(candidate)
        clean = _clean_settings(candidate)
        if clean != _clean_settings(current):
            _save_settings_unlocked(clean)
        return copy.deepcopy(clean)
    finally:
        os.close(descriptor)


def runtime_settings_snapshot(settings: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized settings subset embedded into runtime.json."""
    clean = _clean_settings(settings)
    return {
        field: copy.deepcopy(clean[field])
        for field in RUNTIME_SETTINGS_FIELDS
    }


DOMAIN_SEPARATORS = re.compile(r"[\s,;，；]+")
ASCII_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
RESERVED_SUFFIXES = ("localhost", "local", "lan", "home.arpa")


def is_same_or_subdomain(domain: str, parent: str) -> bool:
    return domain == parent or domain.endswith("." + parent)


def minimize_domains(domains: Iterable[str]) -> list[str]:
    result: list[str] = []
    for domain in sorted(set(domains), key=lambda value: (value.count("."), value)):
        if not any(is_same_or_subdomain(domain, parent) for parent in result):
            result.append(domain)
    return result


def normalize_domain(raw: str) -> str | None:
    value = raw.strip().lower()
    while value.startswith("*."):
        value = value[2:]
    value = value.lstrip(".").rstrip(".")
    if not value or "://" in value or any(char in value for char in "/:"):
        return None
    if any(char.isspace() for char in value):
        return None
    try:
        ascii_value = value.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if len(ascii_value) > 253:
        return None
    labels = ascii_value.split(".")
    if len(labels) < 2 or any(not ASCII_LABEL.fullmatch(label) for label in labels):
        return None
    try:
        ipaddress.ip_address(ascii_value)
    except ValueError:
        pass
    else:
        return None
    if any(
        ascii_value == suffix or ascii_value.endswith("." + suffix)
        for suffix in RESERVED_SUFFIXES
    ):
        return None
    return ascii_value


def parse_domain_text(text: str) -> tuple[list[str], list[str]]:
    valid: list[str] = []
    invalid: list[str] = []
    for raw in filter(None, DOMAIN_SEPARATORS.split(text)):
        normalized = normalize_domain(raw)
        if normalized is None:
            if raw not in invalid:
                invalid.append(raw)
        elif normalized not in valid:
            valid.append(normalized)
    return minimize_domains(valid), invalid


def domain_conflicts(allow: Iterable[str], proxy: Iterable[str]) -> list[tuple[str, str]]:
    conflicts: list[tuple[str, str]] = []
    for direct in sorted(set(allow)):
        for forced_proxy in sorted(set(proxy)):
            if is_same_or_subdomain(direct, forced_proxy) or is_same_or_subdomain(
                forced_proxy, direct
            ):
                conflicts.append((direct, forced_proxy))
    return conflicts


def _required_dict(root: dict[str, Any], key: str) -> dict[str, Any]:
    value = root.get(key)
    if not isinstance(value, dict):
        raise SmartBoxError(f"配置缺少 {key} 对象")
    return value


def _required_list(root: dict[str, Any], key: str) -> list[Any]:
    value = root.get(key)
    if not isinstance(value, list):
        raise SmartBoxError(f"配置缺少 {key} 数组")
    return value


def _prefix_insert_index(rules: list[Any], predicate: Any) -> int:
    index = 0
    for raw_rule in rules:
        if not isinstance(raw_rule, dict) or not predicate(raw_rule):
            break
        index += 1
    return index


def apply_domain_rules(
    profile: dict[str, Any], allow_domains: Iterable[str], proxy_domains: Iterable[str]
) -> dict[str, Any]:
    allow = minimize_domains(allow_domains)
    proxy = minimize_domains(proxy_domains)
    conflicts = domain_conflicts(allow, proxy)
    if conflicts:
        direct, forced_proxy = conflicts[0]
        raise SmartBoxError(f"域名名单冲突：{direct} / {forced_proxy}")
    if not allow and not proxy:
        return profile

    outbounds = _required_list(profile, "outbounds")
    outbound_objects = [item for item in outbounds if isinstance(item, dict)]
    outbound_tags = {
        item.get("tag") for item in outbound_objects if isinstance(item.get("tag"), str)
    }
    direct_outbound = DIRECT_OUTBOUND if DIRECT_OUTBOUND in outbound_tags else None
    if direct_outbound is None:
        direct_outbound = next(
            (
                item.get("tag")
                for item in outbound_objects
                if item.get("type") == "direct" and isinstance(item.get("tag"), str)
            ),
            None,
        )

    route = _required_dict(profile, "route")
    route_final = route.get("final")
    proxy_outbound = BASELINE_OUTBOUND if BASELINE_OUTBOUND in outbound_tags else None
    if proxy_outbound is None and route_final in outbound_tags:
        proxy_outbound = route_final
    if proxy_outbound is None:
        proxy_outbound = next(
            (
                item.get("tag")
                for item in outbound_objects
                if item.get("type") in ("selector", "smart")
                and isinstance(item.get("tag"), str)
            ),
            None,
        )

    dns = _required_dict(profile, "dns")
    dns_servers = _required_list(dns, "servers")
    dns_objects = [item for item in dns_servers if isinstance(item, dict)]
    dns_tags = {item.get("tag") for item in dns_objects if isinstance(item.get("tag"), str)}
    local_dns = LOCAL_DNS if LOCAL_DNS in dns_tags else None
    if local_dns is None:
        local_dns = next(
            (
                item.get("tag")
                for item in dns_objects
                if item.get("type") == "local" and isinstance(item.get("tag"), str)
            ),
            None,
        )
    dns_final = dns.get("final")
    proxy_dns = BASELINE_DNS if BASELINE_DNS in dns_tags else None
    if proxy_dns is None and dns_final in dns_tags:
        proxy_dns = dns_final

    if allow and (not direct_outbound or not local_dns):
        raise SmartBoxError("当前配置无法应用域名白名单")
    if proxy and (not proxy_outbound or not proxy_dns):
        raise SmartBoxError("当前配置无法应用域名黑名单")

    route_rules = _required_list(route, "rules")
    route_additions: list[dict[str, Any]] = []
    if allow:
        route_additions.append(
            {"domain_suffix": sorted(allow), "action": "route", "outbound": direct_outbound}
        )
    if proxy:
        route_additions.append(
            {"domain_suffix": sorted(proxy), "action": "route", "outbound": proxy_outbound}
        )
    route_index = _prefix_insert_index(
        route_rules,
        lambda rule: rule.get("action") in ("sniff", "hijack-dns")
        or rule.get("clash_mode") in ("Direct", "Global"),
    )
    route["rules"] = route_rules[:route_index] + route_additions + route_rules[route_index:]

    dns_rules = _required_list(dns, "rules")
    dns_additions: list[dict[str, Any]] = []
    if allow:
        dns_additions.append(
            {"domain_suffix": sorted(allow), "action": "route", "server": local_dns}
        )
    if proxy:
        dns_additions.append(
            {"domain_suffix": sorted(proxy), "action": "route", "server": proxy_dns}
        )
    dns_index = _prefix_insert_index(
        dns_rules, lambda rule: rule.get("clash_mode") in ("Direct", "Global")
    )
    dns["rules"] = dns_rules[:dns_index] + dns_additions + dns_rules[dns_index:]
    return profile


def validate_profile_shape(profile: Any) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise SmartBoxError("订阅内容不是 JSON 对象")
    inbounds = _required_list(profile, "inbounds")
    if not any(isinstance(item, dict) and item.get("type") == "tun" for item in inbounds):
        raise SmartBoxError("订阅配置缺少 TUN 入站")
    _required_list(profile, "outbounds")
    _required_dict(profile, "route")
    _required_dict(profile, "dns")
    return profile


def apply_bootstrap_dns(profile: dict[str, Any]) -> dict[str, Any]:
    dns = _required_dict(profile, "dns")
    servers = _required_list(dns, "servers")
    dns["optimistic"] = {
        "enabled": True,
        "timeout": BOOTSTRAP_OPTIMISTIC_TIMEOUT,
    }
    dns["servers"] = [
        server
        for server in servers
        if not (isinstance(server, dict) and server.get("tag") == BOOTSTRAP_DNS)
    ]
    dns["servers"].append(
        {
            "type": "https",
            "tag": BOOTSTRAP_DNS,
            "server": "223.5.5.5",
            "server_port": 443,
            "path": "/dns-query",
            "detour": DIRECT_OUTBOUND,
            "tls": {
                "enabled": True,
                "server_name": "dns.alidns.com",
            },
        }
    )
    route = _required_dict(profile, "route")
    route["default_domain_resolver"] = {
        "server": BOOTSTRAP_DNS,
        "strategy": "ipv4_only",
    }
    return profile


def apply_reliability_proxy_rules(profile: dict[str, Any]) -> dict[str, Any]:
    """Protect essential client update endpoints from broad subscription rules.

    Some third-party ad and mainland rule sets classify these Google service
    endpoints as ads or domestic traffic. On this host that produced blocked
    Chrome requests and direct route timeouts, so make the narrow exceptions
    explicit in the generated runtime only.
    """
    domains = list(RELIABILITY_PROXY_DOMAINS)
    proxy_outbound = _proxy_outbound_for_runtime(profile)
    proxy_dns = _proxy_dns_for_runtime(profile, proxy_outbound)
    route = _required_dict(profile, "route")
    route_rules = _required_list(route, "rules")

    def is_existing_route_guard(rule: Any) -> bool:
        return (
            isinstance(rule, dict)
            and rule.get("domain_suffix") == domains
            and rule.get("action") == "route"
            and rule.get("outbound") == proxy_outbound
            and set(rule) == {"domain_suffix", "action", "outbound"}
        )

    route_rules = [rule for rule in route_rules if not is_existing_route_guard(rule)]
    route_index = _prefix_insert_index(
        route_rules,
        lambda rule: rule.get("action") in ("sniff", "hijack-dns")
        or rule.get("clash_mode") in ("Direct", "Global"),
    )
    route_rules.insert(
        route_index,
        {"domain_suffix": domains, "action": "route", "outbound": proxy_outbound},
    )
    route["rules"] = route_rules

    dns = _required_dict(profile, "dns")
    dns_rules = _required_list(dns, "rules")

    def is_existing_dns_guard(rule: Any) -> bool:
        return (
            isinstance(rule, dict)
            and rule.get("domain_suffix") == domains
            and rule.get("action") == "route"
            and rule.get("server") == proxy_dns
            and set(rule) == {"domain_suffix", "action", "server"}
        )

    dns_rules = [rule for rule in dns_rules if not is_existing_dns_guard(rule)]
    dns_index = _prefix_insert_index(
        dns_rules, lambda rule: rule.get("clash_mode") in ("Direct", "Global")
    )
    dns_rules.insert(
        dns_index,
        {"domain_suffix": domains, "action": "route", "server": proxy_dns},
    )
    dns["rules"] = dns_rules
    return profile


def apply_linux_telegram_rules(profile: dict[str, Any]) -> dict[str, Any]:
    """Keep native Telegram and its published IP ranges on Telegram Smart."""
    outbound_tags = {
        item.get("tag")
        for item in _required_list(profile, "outbounds")
        if isinstance(item, dict) and isinstance(item.get("tag"), str)
    }
    if TELEGRAM_OUTBOUND not in outbound_tags:
        return profile

    processes = list(LINUX_TELEGRAM_PROCESSES)
    route = _required_dict(profile, "route")
    rules = _required_list(route, "rules")

    def is_existing_route_guard(rule: Any) -> bool:
        return (
            isinstance(rule, dict)
            and rule.get("process_name") == processes
            and rule.get("action") == "route"
            and rule.get("outbound") == TELEGRAM_OUTBOUND
            and set(rule) == {"process_name", "action", "outbound"}
        )

    rules = [rule for rule in rules if not is_existing_route_guard(rule)]
    route_index = _prefix_insert_index(
        rules,
        lambda rule: rule.get("action") in ("sniff", "hijack-dns")
        or rule.get("clash_mode") in ("Direct", "Global")
        or (rule.get("action") == "route" and isinstance(rule.get("domain_suffix"), list))
        or rule.get("ip_is_private") is True
        or rule.get("rule_set") == ["private"]
        or rule.get("rule_set") == [TELEGRAM_IP_RULE_SET],
    )
    rules.insert(
        route_index,
        {"process_name": processes, "action": "route", "outbound": TELEGRAM_OUTBOUND},
    )
    route["rules"] = rules

    telegram_ip_rule = {
        "rule_set": [TELEGRAM_IP_RULE_SET],
        "action": "route",
        "outbound": TELEGRAM_OUTBOUND,
    }
    if telegram_ip_rule in rules:
        rules = [rule for rule in rules if rule != telegram_ip_rule]
        ip_index = _prefix_insert_index(
            rules,
            lambda rule: rule.get("action") in ("sniff", "hijack-dns")
            or rule.get("clash_mode") in ("Direct", "Global")
            or (
                rule.get("action") == "route"
                and isinstance(rule.get("domain_suffix"), list)
            ),
        )
        rules.insert(ip_index, telegram_ip_rule)
        route["rules"] = rules

    dns = _required_dict(profile, "dns")
    dns_tags = {
        item.get("tag")
        for item in _required_list(dns, "servers")
        if isinstance(item, dict) and isinstance(item.get("tag"), str)
    }
    if TELEGRAM_DNS not in dns_tags:
        return profile
    dns_rules = _required_list(dns, "rules")

    def is_existing_dns_guard(rule: Any) -> bool:
        return (
            isinstance(rule, dict)
            and rule.get("process_name") == processes
            and rule.get("action") == "route"
            and rule.get("server") == TELEGRAM_DNS
            and set(rule) == {"process_name", "action", "server"}
        )

    dns_rules = [rule for rule in dns_rules if not is_existing_dns_guard(rule)]
    dns_index = _prefix_insert_index(
        dns_rules, lambda rule: rule.get("clash_mode") in ("Direct", "Global")
    )
    dns_rules.insert(
        dns_index,
        {"process_name": processes, "action": "route", "server": TELEGRAM_DNS},
    )
    dns["rules"] = dns_rules
    return profile


def apply_local_multicast_route_rule(profile: dict[str, Any]) -> dict[str, Any]:
    """Keep mDNS, LLMNR, and link-local multicast on the host network.

    These packets can enter a TUN stack from system services. Sending them to
    a Smart selector makes UFW see the resulting multicast frames as inbound
    traffic on SmartBox, where a default-deny policy drops them.
    """
    route = _required_dict(profile, "route")
    rules = _required_list(route, "rules")
    expected = {
        "ip_cidr": list(LINUX_LOCAL_MULTICAST_CIDRS),
        "action": "route",
        "outbound": DIRECT_OUTBOUND,
    }
    rules = [
        rule
        for rule in rules
        if not (
            isinstance(rule, dict)
            and rule.get("ip_cidr") == list(LINUX_LOCAL_MULTICAST_CIDRS)
            and rule.get("action") == "route"
            and rule.get("outbound") == DIRECT_OUTBOUND
            and set(rule) == set(expected)
        )
    ]
    rules.append(expected)
    route["rules"] = rules
    return profile


def selector_cache_id(profile: dict[str, Any], settings: dict[str, Any]) -> str:
    """Namespace sing-box's persistent selector state by the local defaults.

    sing-box restores selector choices from ``experimental.cache_file`` before
    applying a selector's JSON ``default``.  A cache entry from an older local
    choice could therefore silently override the current GUI setting after a
    restart.  A stable namespace keeps useful state for an unchanged profile,
    while changing it whenever the selector graph or local overrides change.
    """
    selectors: list[dict[str, Any]] = []
    for outbound in _required_list(profile, "outbounds"):
        if not isinstance(outbound, dict) or outbound.get("type") != "selector":
            continue
        tag = outbound.get("tag")
        choices = outbound.get("outbounds")
        if not isinstance(tag, str) or not isinstance(choices, list):
            continue
        selectors.append(
            {
                "tag": tag,
                "choices": [choice for choice in choices if isinstance(choice, str)],
                "default": outbound.get("default") if isinstance(outbound.get("default"), str) else "",
            }
        )
    overrides = settings.get("selector_overrides", {})
    normalized_overrides = {
        key: overrides[key]
        for key in sorted(overrides)
        if isinstance(key, str) and isinstance(overrides[key], str)
    } if isinstance(overrides, dict) else {}
    payload = json.dumps(
        {"selectors": selectors, "overrides": normalized_overrides},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:24]
    return f"{SELECTOR_CACHE_ID_PREFIX}-{digest}"


def smart_score_identities(profile: dict[str, Any]) -> dict[str, str]:
    """Fingerprint each real node without exposing its credentials."""
    identities: dict[str, str] = {}
    for outbound in _required_list(profile, "outbounds"):
        if (
            not isinstance(outbound, dict)
            or outbound.get("type") in ("selector", "smart", "direct", "block")
            or not isinstance(outbound.get("tag"), str)
        ):
            continue
        identity_outbound = {
            key: value for key, value in outbound.items() if key != "tag"
        }
        content = json.dumps(
            identity_outbound,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        identities[outbound["tag"]] = (
            "node-v1-" + hashlib.sha256(content).hexdigest()[:24]
        )
    return identities


def apply_smart_score_namespace(profile: dict[str, Any]) -> str:
    identities = smart_score_identities(profile)
    for outbound in _required_list(profile, "outbounds"):
        if isinstance(outbound, dict) and outbound.get("type") == "smart":
            outbound["score_namespace"] = SMART_SCORE_NAMESPACE
            choices = outbound.get("outbounds", [])
            outbound["score_identities"] = {
                tag: identities[tag]
                for tag in choices
                if isinstance(tag, str) and tag in identities
            } if isinstance(choices, list) else {}
    return SMART_SCORE_NAMESPACE


def apply_runtime_overrides(profile: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    runtime = copy.deepcopy(profile)
    filter_informational_outbounds(runtime)
    apply_smart_score_namespace(runtime)
    apply_bootstrap_dns(runtime)
    apply_reliability_proxy_rules(runtime)
    apply_linux_telegram_rules(runtime)
    apply_local_multicast_route_rule(runtime)
    inbounds = _required_list(runtime, "inbounds")
    tun_found = False
    mixed_found = False
    for inbound in inbounds:
        if not isinstance(inbound, dict):
            continue
        if inbound.get("type") == "tun":
            inbound["interface_name"] = TUN_INTERFACE
            inbound["stack"] = settings.get("tun_stack", "gvisor")
            inbound["iproute2_table_index"] = SMART_BOX_ROUTE_TABLE_INDEX
            inbound["iproute2_rule_index"] = SMART_BOX_ROUTE_RULE_INDEX
            inbound["auto_redirect_iproute2_fallback_rule_index"] = (
                SMART_BOX_AUTO_REDIRECT_FALLBACK_RULE_INDEX
            )
            existing_exclusions = inbound.get("route_exclude_address", [])
            if isinstance(existing_exclusions, str):
                existing_exclusions = [existing_exclusions]
            if not isinstance(existing_exclusions, list) or not all(
                isinstance(address, str) for address in existing_exclusions
            ):
                raise SmartBoxError("TUN route_exclude_address 字段格式无效")
            inbound["route_exclude_address"] = list(
                dict.fromkeys(
                    [*existing_exclusions, *LINUX_TUN_ROUTE_EXCLUDE_ADDRESSES]
                )
            )
            tun_found = True
        elif inbound.get("type") == "mixed":
            inbound["listen"] = "127.0.0.1"
            inbound["listen_port"] = 20808
            inbound["set_system_proxy"] = False
            mixed_found = True
    if not tun_found:
        raise SmartBoxError("订阅配置缺少 TUN 入站")
    if not mixed_found:
        inbounds.append(
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": "127.0.0.1",
                "listen_port": 20808,
                "set_system_proxy": False,
            }
        )

    experimental = runtime.setdefault("experimental", {})
    if not isinstance(experimental, dict):
        raise SmartBoxError("experimental 字段格式无效")
    cache_file = experimental.setdefault("cache_file", {})
    if not isinstance(cache_file, dict):
        raise SmartBoxError("experimental.cache_file 字段格式无效")
    cache_file.update(
        {
            "enabled": True,
            "path": str(CACHE_PATH),
            "cache_id": selector_cache_id(runtime, settings),
        }
    )
    clash_api = experimental.setdefault("clash_api", {})
    if not isinstance(clash_api, dict):
        raise SmartBoxError("experimental.clash_api 字段格式无效")
    clash_api.update(
        {
            "external_controller": "127.0.0.1:20809",
            "default_mode": settings.get("mode", "Rule"),
        }
    )
    clash_api.pop("secret", None)

    log_options = runtime.setdefault("log", {})
    if not isinstance(log_options, dict):
        raise SmartBoxError("log 字段格式无效")
    log_options["level"] = settings.get("log_level", "info")
    log_options["timestamp"] = True

    overrides = settings.get("selector_overrides", {})
    if isinstance(overrides, dict):
        for outbound in _required_list(runtime, "outbounds"):
            if not isinstance(outbound, dict) or outbound.get("type") != "selector":
                continue
            tag = outbound.get("tag")
            choice = overrides.get(tag)
            choices = outbound.get("outbounds")
            if isinstance(choice, str) and isinstance(choices, list) and choice in choices:
                outbound["default"] = choice

    return apply_domain_rules(
        runtime,
        settings.get("allow_domains", []),
        settings.get("proxy_domains", []),
    )


INFORMATIONAL_NAME_FRAGMENTS = (
    "剩余流量",
    "流量剩余",
    "距离下次重置",
    "套餐到期",
    "到期时间",
    "过期时间",
    "订阅到期",
    "订阅更新",
    "上次更新",
    "如您的客户端仅显示此节点",
    "客户端版本过低",
    "请更新客户端",
    "请经常更新订阅",
    "永久域名",
    "官方网站",
    "请看教程",
    "使用教程",
    "下载clash",
    "下载 clash",
    "客户端教程",
    "联系客服",
    "remaining traffic",
    "traffic remaining",
    "subscription update",
    "updated at",
    "official website",
    "download clash",
    "tutorial",
    "expiry",
    "expiration",
    "expires at",
)


def is_informational_outbound_name(name: str) -> bool:
    normalized = name.strip().lower()
    return any(fragment in normalized for fragment in INFORMATIONAL_NAME_FRAGMENTS)


def filter_informational_outbounds(profile: dict[str, Any]) -> list[str]:
    outbounds = _required_list(profile, "outbounds")
    removed = {
        item.get("tag")
        for item in outbounds
        if isinstance(item, dict)
        and isinstance(item.get("tag"), str)
        and item.get("type") not in ("selector", "smart", "direct", "block")
        and is_informational_outbound_name(item["tag"])
    }
    if not removed:
        return []
    profile["outbounds"] = [
        item
        for item in outbounds
        if not (isinstance(item, dict) and item.get("tag") in removed)
    ]
    for item in profile["outbounds"]:
        if not isinstance(item, dict) or not isinstance(item.get("outbounds"), list):
            continue
        item["outbounds"] = [tag for tag in item["outbounds"] if tag not in removed]
        if item.get("type") == "smart" and not item["outbounds"]:
            raise SmartBoxError(f"过滤订阅状态节点后，Smart 组为空：{item.get('tag', '')}")
        if item.get("default") in removed:
            item["default"] = item["outbounds"][0] if item["outbounds"] else None
    return sorted(removed)


def run_command(
    command: list[str], timeout: float = 30, check: bool = False
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        message = result.stdout.strip() or f"命令退出状态 {result.returncode}"
        raise SmartBoxError(message)
    return result


def _mirror_profile(repo: str) -> dict[str, Any]:
    profile = MIRROR_PROFILES.get(str(repo).strip().lower())
    if profile is None:
        choices = ", ".join(MIRROR_PROFILES)
        raise SmartBoxError(f"不支持的源类型：{repo}（可选：{choices}）")
    return profile


def _active_mirror_servers(path: Path) -> list[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SmartBoxError(f"读取源列表失败：{path}：{error}") from error
    servers: list[str] = []
    seen: set[str] = set()
    for line in content.splitlines():
        match = MIRROR_SERVER_RE.match(line)
        if match is None:
            continue
        server = match.group(1).strip()
        if not server or server in seen:
            continue
        seen.add(server)
        servers.append(server)
    if not servers:
        raise SmartBoxError(f"源列表没有启用的 Server：{path}")
    return servers


def _mirror_probe_url(server: str, repo: str) -> str:
    profile = _mirror_profile(repo)
    # pacman placeholders are deliberately resolved to a small metadata file;
    # the original template is retained for a later ranked mirrorlist write.
    resolved = server.replace("$arch", "x86_64").replace(
        "$repo", str(profile["repo"])
    )
    return resolved.rstrip("/") + "/" + str(profile["test_suffix"])


def _probe_mirror_server(server: str, repo: str, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    url = _mirror_probe_url(server, repo)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "*/*",
            "Cache-Control": "no-cache",
            "User-Agent": f"smart-box-mirror-check/{APP_VERSION}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            body = response.read(MIRROR_PROBE_BYTES)
        elapsed_ms = max(1, round((time.monotonic() - started) * 1000))
        if status < 200 or status >= 400:
            raise SmartBoxError(f"HTTP {status}")
        if not body:
            raise SmartBoxError("空响应")
        return {
            "server": server,
            "url": url,
            "ok": True,
            "status": status,
            "bytes": len(body),
            "latency_ms": elapsed_ms,
            "speed_kib_s": round(len(body) / max(0.001, elapsed_ms / 1000) / 1024, 1),
        }
    except urllib.error.HTTPError as error:
        detail = f"HTTP {error.code}"
    except (urllib.error.URLError, TimeoutError, OSError, SmartBoxError) as error:
        detail = str(getattr(error, "reason", error))
    elapsed_ms = max(1, round((time.monotonic() - started) * 1000))
    return {
        "server": server,
        "url": url,
        "ok": False,
        "status": 0,
        "bytes": 0,
        "latency_ms": elapsed_ms,
        "speed_kib_s": 0.0,
        "error": detail or "连接失败",
    }


def benchmark_mirror_sources(
    repo: str = "all",
    source_paths: dict[str, Path] | None = None,
    timeout: float = MIRROR_BENCHMARK_TIMEOUT,
    max_mirrors: int = MIRROR_BENCHMARK_MAX_MIRRORS,
) -> dict[str, Any]:
    """Benchmark currently enabled pacman/CachyOS mirrors without changing them.

    ``paru`` downloads official packages through pacman, so the ``arch`` result
    covers both tools. A CachyOS result is kept separate because its repository
    metadata lives at a different URL layout. Results are written only to the
    user's state directory and can be explicitly applied later.
    """
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError) as error:
        raise SmartBoxError("源测速超时时间无效") from error
    if not 0.5 <= timeout_value <= 60:
        raise SmartBoxError("源测速超时时间应在 0.5 到 60 秒之间")
    try:
        limit = int(max_mirrors)
    except (TypeError, ValueError) as error:
        raise SmartBoxError("源测速数量无效") from error
    if limit < 1 or limit > 2048:
        raise SmartBoxError("源测速数量应在 1 到 2048 之间")

    repo_name = str(repo).strip().lower()
    requested = tuple(MIRROR_PROFILES) if repo_name == "all" else (repo_name,)
    for item in requested:
        _mirror_profile(item)
    paths = source_paths or {}
    summaries: dict[str, Any] = {}
    started_all = time.monotonic()
    for item in requested:
        profile = _mirror_profile(item)
        path = Path(paths.get(item, profile["path"]))
        servers = _active_mirror_servers(path)
        tested_servers = servers[:limit]
        started = time.monotonic()
        workers = min(16, max(1, len(tested_servers)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(_probe_mirror_server, server, item, timeout_value)
                for server in tested_servers
            ]
            results = [future.result() for future in futures]
        successful = [result for result in results if result.get("ok") is True]
        failed = [result for result in results if result.get("ok") is not True]
        successful.sort(
            key=lambda result: (
                -float(result.get("speed_kib_s", 0.0)),
                int(result.get("latency_ms", 0)),
                str(result.get("server", "")),
            )
        )
        failed.sort(key=lambda result: str(result.get("server", "")))
        ordered = successful + failed
        summary = {
            "repo": item,
            "label": profile["label"],
            "source_path": str(path),
            "tested": len(ordered),
            "successful": len(successful),
            "failed": len(failed),
            "elapsed_ms": max(1, round((time.monotonic() - started) * 1000)),
            "results": ordered,
            "best": successful[0] if successful else None,
        }
        summaries[item] = summary
        MIRROR_RANKING_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write_json(
            MIRROR_RANKING_DIR / f"{item}.json",
            {"version": 1, "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(), **summary},
        )
    return {
        "repo": repo_name,
        "elapsed_ms": max(1, round((time.monotonic() - started_all) * 1000)),
        "summaries": summaries,
    }


def ranked_mirrorlist_content(
    summary: dict[str, Any], source_path: Path | None = None
) -> bytes:
    """Return a source file with successful mirrors first and failures retained."""
    if not isinstance(summary, dict):
        raise SmartBoxError("源测速结果格式无效")
    repo = str(summary.get("repo", "")).lower()
    profile = _mirror_profile(repo)
    source_path = Path(
        source_path or str(summary.get("source_path", profile["path"]))
    )
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as error:
        raise SmartBoxError(f"读取源列表失败：{source_path}：{error}") from error
    raw_results = summary.get("results")
    if not isinstance(raw_results, list):
        raise SmartBoxError("源测速结果缺少 results")
    ordered = [
        str(result["server"])
        for result in raw_results
        if isinstance(result, dict) and isinstance(result.get("server"), str)
    ]
    original = _active_mirror_servers(source_path)
    seen = set(ordered)
    ordered.extend(server for server in original if server not in seen)
    iterator = iter(ordered)
    target_arch = (
        "$arch_v3"
        if "v3" in source_path.name
        else "$arch_v4"
        if "v4" in source_path.name
        else "$arch"
    )

    def server_for_target(server: str) -> str:
        normalized = server.replace("$arch_v3", "$arch").replace("$arch_v4", "$arch")
        return normalized.replace("$arch", target_arch)

    output: list[str] = []
    for line in source.splitlines(keepends=True):
        if MIRROR_SERVER_RE.match(line):
            server = next(iterator, None)
            if server is not None:
                ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\n"
                output.append(f"Server = {server_for_target(server)}{ending}")
                continue
        output.append(line)
    return "".join(output).encode("utf-8")


def apply_mirror_ranking(summary: dict[str, Any], timeout: float = 30) -> dict[str, str]:
    """Install a previously benchmarked mirrorlist after explicit auth."""
    repo = str(summary.get("repo", "")).lower()
    profile = _mirror_profile(repo)
    targets = [
        Path(path)
        for path in profile.get("apply_paths", (profile["path"],))
        if Path(path).is_file()
    ]
    if not targets:
        raise SmartBoxError(f"没有可应用的 {repo} 源列表")
    MIRROR_RANKING_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    system_targets = {
        PACMAN_MIRRORLIST_PATH,
        CACHYOS_MIRRORLIST_PATH,
        CACHYOS_V3_MIRRORLIST_PATH,
    }
    records: list[dict[str, str]] = []
    for index, target in enumerate(targets):
        content = ranked_mirrorlist_content(summary, source_path=target)
        candidate = MIRROR_RANKING_DIR / f"{repo}.{index}.ranked"
        backup = MIRROR_RANKING_DIR / f"{repo}.{index}.before-apply"
        atomic_write_bytes(candidate, content)
        try:
            atomic_write_bytes(backup, target.read_bytes())
        except OSError as error:
            raise SmartBoxError(f"备份源列表失败：{target}：{error}") from error
        if target in system_targets:
            result = run_command(
                [
                    "pkexec",
                    "/usr/bin/install",
                    "-o",
                    "root",
                    "-g",
                    "root",
                    "-m",
                    "0644",
                    str(candidate),
                    str(target),
                ],
                timeout=timeout,
            )
            if result.returncode != 0:
                raise SmartBoxError(result.stdout.strip() or "应用最快源失败")
        else:
            mode = target.stat().st_mode & 0o777
            atomic_write_bytes(target, content, mode=mode or 0o644)
        records.append(
            {
                "target": str(target),
                "backup": str(backup),
                "candidate": str(candidate),
            }
        )
    return {
        "repo": repo,
        "target": str(targets[0]),
        "targets": ",".join(record["target"] for record in records),
        "backup": records[0]["backup"],
        "candidate": records[0]["candidate"],
    }


def format_mirror_benchmark_summary(result: dict[str, Any]) -> str:
    summaries = result.get("summaries") if isinstance(result, dict) else None
    if not isinstance(summaries, dict) or not summaries:
        return "没有源测速结果"
    lines: list[str] = []
    for repo in ("arch", "cachyos"):
        summary = summaries.get(repo)
        if not isinstance(summary, dict):
            continue
        best = summary.get("best")
        if isinstance(best, dict):
            server = str(best.get("server", ""))
            speed = best.get("speed_kib_s", 0)
            latency = best.get("latency_ms", 0)
            lines.append(
                f"{summary.get('label', repo)}：最快 {server} · {speed} KiB/s · {latency} ms · "
                f"成功 {summary.get('successful', 0)}/{summary.get('tested', 0)}"
            )
        else:
            lines.append(
                f"{summary.get('label', repo)}：无可用源 · "
                f"成功 0/{summary.get('tested', 0)}"
            )
    return "\n".join(lines) or "没有源测速结果"


def validate_config(path: Path, core: Path | None = None) -> str:
    core_path = core or find_core()
    if not core_path.is_file():
        raise SmartBoxError(f"找不到代理核心：{core_path}")
    result = run_command(
        [
            str(core_path),
            "check",
            "--disable-color",
            "-D",
            str(STATE_DIR),
            "-c",
            str(path),
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise SmartBoxError(result.stdout.strip() or "核心配置校验失败")
    return result.stdout.strip()


def _new_json_candidate(target: Path, value: Any, purpose: str) -> Path:
    """Write a complete, private candidate without a cross-process name clash."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, candidate_name = tempfile.mkstemp(
        prefix=f".{target.name}.{purpose}.",
        dir=target.parent,
    )
    os.close(descriptor)
    candidate = Path(candidate_name)
    try:
        atomic_write_json(candidate, value)
    except Exception:
        candidate.unlink(missing_ok=True)
        raise
    return candidate


def _file_snapshot(path: Path) -> tuple[bytes, int] | None:
    if not path.is_file():
        return None
    stat = path.stat()
    return path.read_bytes(), stat.st_mode & 0o7777


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _restore_file_snapshot(path: Path, snapshot: tuple[bytes, int] | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
        return
    content, mode = snapshot
    atomic_write_bytes(path, content, mode)


def _profile_transaction_journal_path() -> Path:
    return SETTINGS_PATH.with_name(".profile-transaction.json")


def _profile_transaction_backup_paths() -> dict[str, Path]:
    directory = SETTINGS_PATH.parent
    return {
        "profile": directory / ".profile-transaction.profile.before",
        "runtime": directory / ".profile-transaction.runtime.before",
        "settings": directory / ".profile-transaction.settings.before",
    }


def _profile_transaction_targets() -> dict[str, Path]:
    return {
        "profile": PROFILE_PATH,
        "runtime": RUNTIME_PATH,
        "settings": SETTINGS_PATH,
    }


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(
        directory,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_target_directories() -> None:
    for directory in {
        path.parent for path in _profile_transaction_targets().values()
    }:
        _fsync_directory(directory)


def _snapshot_metadata(
    snapshot: tuple[bytes, int] | None,
) -> dict[str, Any]:
    if snapshot is None:
        return {"exists": False}
    content, mode = snapshot
    return {
        "exists": True,
        "mode": mode,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _path_metadata(path: Path) -> dict[str, Any]:
    return _snapshot_metadata(_file_snapshot(path))


def _validated_transaction_metadata(
    value: Any, label: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("exists"), bool):
        raise SmartBoxError(f"配置事务 journal 的 {label} 元数据无效")
    if not value["exists"]:
        return {"exists": False}
    mode = value.get("mode")
    size = value.get("size")
    sha256 = value.get("sha256")
    if (
        not isinstance(mode, int)
        or mode < 0
        or not isinstance(size, int)
        or size < 0
        or not isinstance(sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", sha256)
    ):
        raise SmartBoxError(f"配置事务 journal 的 {label} 元数据无效")
    return {
        "exists": True,
        "mode": mode,
        "size": size,
        "sha256": sha256,
    }


def _transaction_path_matches(path: Path, metadata: Any, label: str) -> bool:
    expected = _validated_transaction_metadata(metadata, label)
    current = _path_metadata(path)
    return current == expected


def _cleanup_profile_transaction_artifacts_unlocked() -> None:
    journal = _profile_transaction_journal_path()
    backups = _profile_transaction_backup_paths()
    changed = False
    if journal.exists():
        journal.unlink()
        _fsync_directory(journal.parent)
        changed = True
    for backup in backups.values():
        if backup.exists():
            backup.unlink()
            changed = True
    if changed:
        _fsync_directory(journal.parent)


def _load_profile_transaction_journal_unlocked() -> dict[str, Any] | None:
    path = _profile_transaction_journal_path()
    if not path.exists():
        return None
    if not path.is_file():
        raise SmartBoxError("配置事务 journal 不是普通文件")
    try:
        value = load_json(path)
    except (OSError, ValueError, TypeError) as error:
        raise SmartBoxError(f"读取配置事务 journal 失败：{error}") from error
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or value.get("state") not in ("prepared", "committed")
        or not isinstance(value.get("transaction_id"), str)
        or not value["transaction_id"]
    ):
        raise SmartBoxError("配置事务 journal 格式无效")
    return value


def _snapshot_from_transaction_backup(
    key: str, metadata: Any
) -> tuple[bytes, int] | None:
    expected = _validated_transaction_metadata(metadata, f"old.{key}")
    backup = _profile_transaction_backup_paths()[key]
    if not expected["exists"]:
        if backup.exists():
            raise SmartBoxError(f"配置事务不应存在 {key} 备份")
        return None
    if not backup.is_file():
        raise SmartBoxError(f"配置事务缺少 {key} 备份")
    content = backup.read_bytes()
    if (
        len(content) != expected["size"]
        or hashlib.sha256(content).hexdigest() != expected["sha256"]
    ):
        raise SmartBoxError(f"配置事务的 {key} 备份校验失败")
    return content, int(expected["mode"])


def _profile_transaction_checkpoint(_stage: str) -> None:
    """Test seam for simulating process death after durable crash points."""


def _begin_profile_transaction_unlocked(
    snapshots: dict[str, tuple[bytes, int] | None],
) -> dict[str, Any]:
    _recover_profile_transaction_unlocked()
    targets = _profile_transaction_targets()
    if set(snapshots) != set(targets):
        raise ValueError("profile transaction snapshot set is incomplete")
    backups = _profile_transaction_backup_paths()
    try:
        for key, snapshot in snapshots.items():
            backup = backups[key]
            if snapshot is None:
                backup.unlink(missing_ok=True)
            else:
                atomic_write_bytes(backup, snapshot[0], 0o600)
        _fsync_directory(_profile_transaction_journal_path().parent)
        journal = {
            "version": 1,
            "state": "prepared",
            "transaction_id": f"{os.getpid()}-{time.time_ns()}",
            "old": {
                key: _snapshot_metadata(snapshot)
                for key, snapshot in snapshots.items()
            },
        }
        atomic_write_json(_profile_transaction_journal_path(), journal)
        _fsync_directory(_profile_transaction_journal_path().parent)
    except Exception:
        if not _profile_transaction_journal_path().exists():
            _cleanup_profile_transaction_artifacts_unlocked()
        raise
    _profile_transaction_checkpoint("prepared")
    return journal


def _commit_profile_transaction_unlocked(journal: dict[str, Any]) -> None:
    _fsync_target_directories()
    committed = copy.deepcopy(journal)
    committed["state"] = "committed"
    committed["new"] = {
        key: _path_metadata(path)
        for key, path in _profile_transaction_targets().items()
    }
    atomic_write_json(_profile_transaction_journal_path(), committed)
    _fsync_directory(_profile_transaction_journal_path().parent)
    _profile_transaction_checkpoint("committed")
    try:
        _cleanup_profile_transaction_artifacts_unlocked()
    except OSError:
        # The committed marker makes cleanup retryable.  Do not report a
        # successfully durable bundle as failed merely because an orphaned
        # journal/backup could not be removed in this process.
        pass


def _recover_profile_transaction_unlocked() -> str | None:
    journal = _load_profile_transaction_journal_unlocked()
    if journal is None:
        # Backups can remain only when a process died before the prepared
        # journal rename became durable.  No target had been replaced then.
        _cleanup_profile_transaction_artifacts_unlocked()
        return None

    targets = _profile_transaction_targets()
    state = journal["state"]
    if state == "committed":
        new = journal.get("new")
        if not isinstance(new, dict) or set(new) != set(targets):
            raise SmartBoxError("已提交配置事务缺少新 bundle 元数据")
        mismatches = [
            key
            for key, path in targets.items()
            if not _transaction_path_matches(path, new[key], f"new.{key}")
        ]
        if mismatches:
            raise SmartBoxError(
                "已提交配置事务的目标文件校验失败：" + "、".join(mismatches)
            )
        _cleanup_profile_transaction_artifacts_unlocked()
        return "committed"

    old = journal.get("old")
    if not isinstance(old, dict) or set(old) != set(targets):
        raise SmartBoxError("待恢复配置事务缺少旧 bundle 元数据")
    snapshots = {
        key: _snapshot_from_transaction_backup(key, old[key])
        for key in targets
    }
    try:
        for key, path in targets.items():
            _restore_file_snapshot(path, snapshots[key])
        _fsync_target_directories()
    except Exception as error:
        raise SmartBoxError(f"恢复中断的配置事务失败：{error}") from error
    _cleanup_profile_transaction_artifacts_unlocked()
    return "rolled-back"


def recover_profile_transaction() -> str | None:
    descriptor = _open_settings_lock()
    try:
        return _recover_profile_transaction_unlocked()
    finally:
        os.close(descriptor)


def prepare_runtime(
    profile_path: Path = PROFILE_PATH,
    runtime_path: Path = RUNTIME_PATH,
    settings: dict[str, Any] | None = None,
    check: bool = True,
) -> Path:
    """Build a validated runtime from a stable profile/settings snapshot.

    Production writes participate in the settings writer lock.  Validation is
    deliberately performed outside that lock; immediately before replacement
    we compare both inputs and retry if another process changed either one.
    """
    ensure_directories()
    shared_paths = profile_path == PROFILE_PATH and runtime_path == RUNTIME_PATH
    if shared_paths:
        recover_profile_transaction()
    requested_settings = copy.deepcopy(settings) if settings is not None else None
    attempts = CONFIG_SNAPSHOT_MAX_RETRIES if shared_paths else 1

    for attempt in range(attempts):
        if not profile_path.is_file():
            raise SmartBoxError("尚未拉取订阅配置")
        try:
            profile_content = profile_path.read_bytes()
            profile = validate_profile_shape(
                json.loads(profile_content.decode("utf-8"))
            )
        except (OSError, UnicodeError, ValueError) as error:
            raise SmartBoxError(f"读取订阅配置失败：{error}") from error
        settings_snapshot = (
            copy.deepcopy(requested_settings)
            if attempt == 0 and requested_settings is not None
            else load_settings()
        )
        runtime_settings = runtime_settings_snapshot(settings_snapshot)
        runtime = apply_runtime_overrides(profile, settings_snapshot)
        temporary = _new_json_candidate(runtime_path, runtime, "prepare")
        try:
            if check:
                validate_config(temporary)
            if not shared_paths:
                os.replace(temporary, runtime_path)
                os.chmod(runtime_path, 0o600)
                return runtime_path

            descriptor = _open_settings_lock()
            try:
                _recover_profile_transaction_unlocked()
                current_settings = _load_settings_unlocked()
                try:
                    current_profile_content = profile_path.read_bytes()
                except OSError:
                    current_profile_content = b""
                stale = (
                    current_profile_content != profile_content
                    or runtime_settings_snapshot(current_settings) != runtime_settings
                )
                if stale:
                    continue

                previous_runtime = _file_snapshot(runtime_path)
                try:
                    os.replace(temporary, runtime_path)
                    os.chmod(runtime_path, 0o600)
                except Exception as error:
                    try:
                        _restore_file_snapshot(runtime_path, previous_runtime)
                    except Exception as rollback_error:
                        raise SmartBoxError(
                            f"保存运行配置失败：{error}；恢复旧运行配置也失败："
                            f"{rollback_error}"
                        ) from error
                    raise SmartBoxError(
                        f"保存运行配置失败：{error}；旧运行配置已恢复"
                    ) from error
                return runtime_path
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    raise SmartBoxError("生成运行配置期间订阅或本地设置持续变化，请稍后重试")


def validate_subscription_url(url: str) -> str:
    value = url.strip()
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise SmartBoxError("订阅地址格式无效") from error
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SmartBoxError("订阅地址必须是有效的 HTTP 或 HTTPS 地址")
    if parsed.username or parsed.password:
        raise SmartBoxError("订阅地址不能包含用户名或密码")
    if port is not None and not 1 <= port <= 65535:
        raise SmartBoxError("订阅端口必须在 1 到 65535 之间")
    if not parsed.path or parsed.path == "/":
        raise SmartBoxError("订阅地址缺少私密路径")
    return value


def fetch_profile(url: str, timeout: float = 45) -> dict[str, Any]:
    normalized_url = validate_subscription_url(url)
    ensure_directories()
    recover_profile_transaction()
    request = urllib.request.Request(
        normalized_url,
        headers={"User-Agent": f"smart-box-linux/{APP_VERSION}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise SmartBoxError(f"订阅服务器返回 HTTP {response.status}")
            content = response.read(64 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        raise SmartBoxError(f"订阅服务器返回 HTTP {error.code}") from error
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", error)
        raise SmartBoxError(f"连接订阅服务器失败：{reason}") from error
    if len(content) > 64 * 1024 * 1024:
        raise SmartBoxError("订阅内容超过 64 MiB 限制")
    try:
        profile = validate_profile_shape(json.loads(content.decode("utf-8-sig")))
    except (UnicodeError, ValueError) as error:
        raise SmartBoxError(f"订阅内容不是有效 JSON：{error}") from error

    candidate = _new_json_candidate(PROFILE_PATH, profile, "fetch")
    try:
        validate_config(candidate)
        for _attempt in range(CONFIG_SNAPSHOT_MAX_RETRIES):
            settings = load_settings()
            runtime_settings = runtime_settings_snapshot(settings)
            runtime = apply_runtime_overrides(profile, settings)
            runtime_candidate = _new_json_candidate(
                RUNTIME_PATH, runtime, "fetch"
            )
            try:
                validate_config(runtime_candidate)
                descriptor = _open_settings_lock()
                try:
                    _recover_profile_transaction_unlocked()
                    current_settings = _load_settings_unlocked()
                    if (
                        runtime_settings_snapshot(current_settings)
                        != runtime_settings
                    ):
                        continue

                    previous_profile = _file_snapshot(PROFILE_PATH)
                    previous_runtime = _file_snapshot(RUNTIME_PATH)
                    previous_settings = _file_snapshot(SETTINGS_PATH)
                    previous_subscription_url = current_settings.get(
                        "subscription_url", ""
                    )
                    previous_last_pull_utc = current_settings.get(
                        "last_pull_utc"
                    )
                    journal: dict[str, Any] | None = None
                    try:
                        journal = _begin_profile_transaction_unlocked(
                            {
                                "profile": previous_profile,
                                "runtime": previous_runtime,
                                "settings": previous_settings,
                            }
                        )
                        os.replace(candidate, PROFILE_PATH)
                        _profile_transaction_checkpoint("profile-replaced")
                        os.replace(runtime_candidate, RUNTIME_PATH)
                        _profile_transaction_checkpoint("runtime-replaced")
                        os.chmod(PROFILE_PATH, 0o600)
                        os.chmod(RUNTIME_PATH, 0o600)
                        last_pull_utc = dt.datetime.now(
                            dt.timezone.utc
                        ).isoformat()
                        current_settings["subscription_url"] = normalized_url
                        current_settings["last_pull_utc"] = last_pull_utc
                        _save_settings_unlocked(current_settings)
                        _profile_transaction_checkpoint("settings-replaced")
                        receipt = ProfileUpdateReceipt(
                            previous_profile=previous_profile,
                            previous_runtime=previous_runtime,
                            previous_subscription_url=previous_subscription_url,
                            previous_last_pull_utc=previous_last_pull_utc,
                            profile_sha256=str(_file_sha256(PROFILE_PATH)),
                            runtime_sha256=str(_file_sha256(RUNTIME_PATH)),
                            runtime_settings=runtime_settings,
                            subscription_url=normalized_url,
                            last_pull_utc=last_pull_utc,
                        )
                        _commit_profile_transaction_unlocked(journal)
                    except Exception as error:
                        rollback_errors: list[str] = []
                        if (
                            journal is not None
                            or _profile_transaction_journal_path().exists()
                        ):
                            try:
                                _recover_profile_transaction_unlocked()
                            except Exception as rollback_error:
                                rollback_errors.append(
                                    f"恢复旧配置 bundle 失败：{rollback_error}"
                                )
                        detail = f"提交订阅配置失败：{error}"
                        if rollback_errors:
                            detail += "；" + "；".join(rollback_errors)
                        else:
                            detail += "；旧配置已恢复"
                        raise SmartBoxError(detail) from error
                    return ProfileUpdateResult(profile_summary(runtime), receipt)
                finally:
                    os.close(descriptor)
            finally:
                runtime_candidate.unlink(missing_ok=True)
        raise SmartBoxError("提交订阅配置期间本地设置持续变化，请稍后重试")
    finally:
        candidate.unlink(missing_ok=True)


def rollback_profile_update(receipt: ProfileUpdateReceipt) -> bool:
    """Restore one committed profile bundle only while it still owns state.

    A newer fetch, runtime preparation, URL edit, or runtime-affecting setting
    change makes the receipt stale.  In that case no file is touched.
    """
    if not isinstance(receipt, ProfileUpdateReceipt):
        raise TypeError("profile rollback receipt is invalid")
    descriptor = _open_settings_lock()
    try:
        _recover_profile_transaction_unlocked()
        current_settings = _load_settings_unlocked()
        if (
            _file_sha256(PROFILE_PATH) != receipt.profile_sha256
            or _file_sha256(RUNTIME_PATH) != receipt.runtime_sha256
            or current_settings.get("subscription_url")
            != receipt.subscription_url
            or current_settings.get("last_pull_utc") != receipt.last_pull_utc
            or runtime_settings_snapshot(current_settings)
            != receipt.runtime_settings
        ):
            return False

        committed_profile = _file_snapshot(PROFILE_PATH)
        committed_runtime = _file_snapshot(RUNTIME_PATH)
        committed_settings = _file_snapshot(SETTINGS_PATH)
        journal: dict[str, Any] | None = None
        try:
            journal = _begin_profile_transaction_unlocked(
                {
                    "profile": committed_profile,
                    "runtime": committed_runtime,
                    "settings": committed_settings,
                }
            )
            _restore_file_snapshot(PROFILE_PATH, receipt.previous_profile)
            _profile_transaction_checkpoint("profile-replaced")
            _restore_file_snapshot(RUNTIME_PATH, receipt.previous_runtime)
            _profile_transaction_checkpoint("runtime-replaced")
            current_settings["subscription_url"] = copy.deepcopy(
                receipt.previous_subscription_url
            )
            current_settings["last_pull_utc"] = copy.deepcopy(
                receipt.previous_last_pull_utc
            )
            _save_settings_unlocked(current_settings)
            _profile_transaction_checkpoint("settings-replaced")
            _commit_profile_transaction_unlocked(journal)
        except Exception as error:
            compensation_errors: list[str] = []
            if (
                journal is not None
                or _profile_transaction_journal_path().exists()
            ):
                try:
                    _recover_profile_transaction_unlocked()
                except Exception as compensation_error:
                    compensation_errors.append(
                        f"恢复本次提交 bundle 失败：{compensation_error}"
                    )
            detail = f"恢复更新前配置失败：{error}"
            if compensation_errors:
                detail += "；回到本次提交状态也失败：" + "；".join(
                    compensation_errors
                )
            else:
                detail += "；已回到本次提交状态"
            raise SmartBoxError(detail) from error
        return True
    finally:
        os.close(descriptor)


def profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    outbounds = profile.get("outbounds", [])
    return {
        "outbounds": len(outbounds) if isinstance(outbounds, list) else 0,
        "nodes": sum(
            1
            for item in outbounds
            if isinstance(item, dict)
            and item.get("type") not in ("selector", "smart", "direct", "block")
        ),
        "selectors": sum(
            1
            for item in outbounds
            if isinstance(item, dict) and item.get("type") == "selector"
        ),
    }


def interface_exists(name: str) -> bool:
    return (Path("/sys/class/net") / name).exists()


def systemctl_user(*arguments: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    runtime_dir = f"/run/user/{os.getuid()}"
    environment.setdefault("XDG_RUNTIME_DIR", runtime_dir)
    environment.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus")
    result = subprocess.run(
        ["systemctl", "--user", *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
        env=environment,
    )
    return result


def smart_box_system_units(service_unit: str | None = None) -> tuple[str, str]:
    """Return only the main unit and its same-instance watchdog unit."""
    unit = (service_unit or SERVICE_UNIT).strip()
    match = SERVICE_UNIT_NAME.fullmatch(unit)
    if not match:
        raise ServiceUnitMaskError(
            "target-validation",
            (unit,),
            "服务目标必须是 smart-box@用户名.service",
        )
    instance = match.group("instance")
    return unit, f"smart-box-watchdog@{instance}.service"


def smart_box_unmask_helper_unit(service_unit: str | None = None) -> str:
    """Derive the fixed privileged helper from the validated main-unit instance."""
    unit = (service_unit or SERVICE_UNIT).strip()
    match = SERVICE_UNIT_NAME.fullmatch(unit)
    if not match:
        raise ServiceUnitMaskError(
            "target-validation",
            (unit,),
            "服务目标必须是 smart-box@用户名.service",
        )
    return SERVICE_UNMASK_UNIT_TEMPLATE.format(instance=match.group("instance"))


def smart_box_cleanup_helper_unit(service_unit: str | None = None) -> str:
    """Derive the fixed privileged fail-open helper for one validated instance."""
    unit = (service_unit or SERVICE_UNIT).strip()
    match = SERVICE_UNIT_NAME.fullmatch(unit)
    if not match:
        raise SmartBoxError("清理目标必须是 smart-box@用户名.service")
    return SERVICE_CLEANUP_UNIT_TEMPLATE.format(instance=match.group("instance"))


def service_unit_mask_state(
    unit: str, timeout: float = 5
) -> dict[str, str]:
    """Read mask-related properties for one explicitly managed system unit."""
    allowed_units = smart_box_system_units()
    if unit not in allowed_units:
        raise ServiceUnitMaskError(
            "target-validation",
            (unit,),
            f"仅允许检查以下服务：{', '.join(allowed_units)}",
        )
    command = [
        "systemctl",
        "show",
        "--property=LoadState",
        "--property=UnitFileState",
        "--",
        unit,
    ]
    try:
        result = run_command(command, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise ServiceUnitMaskError(
            "state-query", (unit,), str(error)
        ) from error
    if result.returncode != 0:
        detail = result.stdout.strip() or f"systemctl 退出状态 {result.returncode}"
        raise ServiceUnitMaskError("state-query", (unit,), detail)

    expected = {"LoadState", "UnitFileState"}
    state: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in expected:
            state[key] = value.strip()
    missing = sorted(expected.difference(state))
    if missing:
        raise ServiceUnitMaskError(
            "state-parse",
            (unit,),
            f"systemctl show 缺少字段：{', '.join(missing)}",
            {unit: state},
        )
    return state


def ensure_runtime_service_units_unmasked(
    timeout: float = 10,
) -> dict[str, dict[str, str]]:
    """Remove runtime masks from SmartBox's exact main/watchdog unit pair."""
    units = smart_box_system_units()
    states = {
        unit: service_unit_mask_state(unit, timeout=timeout) for unit in units
    }
    runtime_masked = [
        unit
        for unit, state in states.items()
        if state["UnitFileState"] == "masked-runtime"
    ]
    unsupported_masks = [
        unit
        for unit, state in states.items()
        if unit not in runtime_masked
        and (
            state["LoadState"] == "masked"
            or state["UnitFileState"] == "masked"
        )
    ]
    if unsupported_masks:
        raise ServiceUnitMaskError(
            "persistent-mask",
            unsupported_masks,
            "检测到持久 mask；自动修复仅解除 masked-runtime",
            {unit: states[unit] for unit in unsupported_masks},
        )
    if not runtime_masked:
        return states

    helper_unit = smart_box_unmask_helper_unit()
    command = ["systemctl", "start", "--", helper_unit]
    try:
        result = run_command(command, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise ServiceUnitMaskError(
            "runtime-unmask-helper",
            runtime_masked,
            f"启动 {helper_unit} 失败：{error}",
            {unit: states[unit] for unit in runtime_masked},
        ) from error
    if result.returncode != 0:
        detail = result.stdout.strip() or f"systemctl 退出状态 {result.returncode}"
        raise ServiceUnitMaskError(
            "runtime-unmask-helper",
            runtime_masked,
            f"启动 {helper_unit} 失败：{detail}",
            {unit: states[unit] for unit in runtime_masked},
        )

    for unit in runtime_masked:
        state = service_unit_mask_state(unit, timeout=timeout)
        states[unit] = state
        if (
            state["UnitFileState"] == "masked-runtime"
            or state["LoadState"] == "masked"
        ):
            raise ServiceUnitMaskError(
                "post-unmask-verification",
                (unit,),
                f"{helper_unit} 返回成功，但服务仍处于 mask 状态",
                {unit: state},
            )
    return states


def _starts_managed_service(arguments: tuple[str, ...]) -> bool:
    """Recognize systemctl start/restart forms that target this exact service."""
    if not arguments or arguments[0] not in ("start", "restart"):
        return False
    for index, argument in enumerate(arguments[1:], start=1):
        if argument == SERVICE_UNIT:
            return True
        if argument == "--unit" and index + 1 < len(arguments):
            if arguments[index + 1] == SERVICE_UNIT:
                return True
        if argument.startswith("--unit=") and argument.removeprefix("--unit=") == SERVICE_UNIT:
            return True
    return False


def systemctl_service(*arguments: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    if _starts_managed_service(arguments):
        ensure_runtime_service_units_unmasked(
            timeout=max(1.0, min(float(timeout), 15.0))
        )
    return run_command(["systemctl", *arguments], timeout=timeout)


def unit_active(unit: str) -> bool:
    try:
        command = systemctl_service if unit == SERVICE_UNIT else systemctl_user
        return command("is-active", "--quiet", unit, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def unit_enabled(unit: str) -> bool:
    try:
        command = systemctl_service if unit == SERVICE_UNIT else systemctl_user
        return command("is-enabled", "--quiet", unit, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def active_flclash_units() -> list[str]:
    try:
        result = systemctl_user(
            "list-units",
            "--type=service",
            "--state=active",
            "--no-legend",
            "--plain",
            "--no-pager",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    units: list[str] = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 1)
        if not fields:
            continue
        unit = fields[0]
        normalized = unit.casefold()
        if (
            normalized.startswith("app-flclash@")
            and normalized.endswith(".service")
            and unit not in units
        ):
            units.append(unit)
    return units


def flclash_conflict() -> bool:
    return (
        interface_exists(FLCLASH_INTERFACE)
        or bool(active_flclash_units())
        or unit_active(FLCLASH_UNIT)
    )


def wait_for(predicate: Any, timeout: float, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def stop_flclash(timeout: float = 30) -> list[str]:
    units = active_flclash_units()
    if FLCLASH_UNIT not in units and unit_active(FLCLASH_UNIT):
        units.append(FLCLASH_UNIT)
    stop_error = ""
    if units:
        try:
            stopped = systemctl_user("stop", *units, timeout=timeout)
            if stopped.returncode != 0:
                stop_error = stopped.stdout.strip()
        except (OSError, subprocess.SubprocessError) as error:
            stop_error = str(error)
    clean = wait_for(lambda: not flclash_conflict(), min(timeout, 15), 0.25)
    if not clean:
        detail = f"：{stop_error}" if stop_error else ""
        raise SmartBoxError(f"FlClash 未退出或 TUN 未及时清理{detail}")
    return units


def start_flclash(timeout: float = 30) -> None:
    started = systemctl_user("start", FLCLASH_UNIT, timeout=timeout)
    if started.returncode != 0:
        raise SmartBoxError(started.stdout.strip() or "恢复 FlClash 失败")
    ready = wait_for(
        lambda: unit_active(FLCLASH_UNIT) and interface_exists(FLCLASH_INTERFACE),
        timeout,
        0.25,
    )
    if not ready:
        raise SmartBoxError("FlClash 已启动，但未在限定时间内建立 TUN")


def run_privileged_cleanup(timeout: float = 45) -> None:
    """Run the fixed root helper even when the main unit cannot stop cleanly."""
    helper = smart_box_cleanup_helper_unit()
    try:
        result = systemctl_service("start", "--", helper, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise SmartBoxError(f"启动 {helper} 失败：{error}") from error
    if result.returncode != 0:
        detail = result.stdout.strip() or f"systemctl 退出状态 {result.returncode}"
        raise SmartBoxError(f"启动 {helper} 失败：{detail}")


def recover_failed_switch(restore_flclash: bool, timeout: float = 30) -> str:
    failures: list[str] = []
    try:
        stopped = systemctl_service("stop", SERVICE_UNIT, timeout=timeout)
        if stopped.returncode != 0:
            failures.append(stopped.stdout.strip() or "停止 smart-box 返回失败")
    except (OSError, subprocess.SubprocessError) as error:
        failures.append(f"停止 smart-box 失败：{error}")

    # The helper runtime-masks the exact main/watchdog pair, kills any remaining
    # processes and performs cleanup as root. It is deliberately independent of
    # the main unit's possibly failed ExecStopPost transaction.
    try:
        run_privileged_cleanup(timeout=max(10.0, min(float(timeout), 45.0)))
    except Exception as error:  # noqa: BLE001 - retain every recovery failure
        failures.append(f"独立清理失败：{error}")

    try:
        verify_fail_open(timeout=max(1.0, min(float(timeout), 6.0)))
    except Exception as error:  # noqa: BLE001 - report structural and data-plane state
        failures.append(f"直连验收失败：{error}")

    try:
        SWITCH_STATE_PATH.unlink(missing_ok=True)
    except OSError as error:
        failures.append(f"清理切换状态失败：{error}")

    if failures:
        raise SmartBoxError("自动回退未完整完成：" + "；".join(failures))
    if restore_flclash:
        start_flclash(timeout=timeout)
        return "smart-box 已停止，FlClash 已恢复"
    return "smart-box 已停止"


def flush_dns_cache() -> bool:
    try:
        result = run_command(["resolvectl", "flush-caches"], timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        raise SmartBoxError(f"刷新 systemd-resolved 缓存失败：{error}") from error
    if result.returncode != 0:
        raise SmartBoxError(
            result.stdout.strip() or "刷新 systemd-resolved 缓存失败"
        )
    return True


def runtime_tun_interfaces(
    runtime_path: Path = RUNTIME_PATH,
) -> list[ipaddress.IPv4Interface | ipaddress.IPv6Interface]:
    profile = validate_profile_shape(load_json(runtime_path))
    tun = next(
        (
            inbound
            for inbound in profile["inbounds"]
            if isinstance(inbound, dict) and inbound.get("type") == "tun"
        ),
        None,
    )
    if not isinstance(tun, dict) or not isinstance(tun.get("address"), list):
        raise SmartBoxError("TUN 入站缺少地址")
    interfaces: list[ipaddress.IPv4Interface | ipaddress.IPv6Interface] = []
    for value in tun["address"]:
        if not isinstance(value, str):
            continue
        try:
            interface = ipaddress.ip_interface(value)
        except (ValueError, ipaddress.AddressValueError) as error:
            raise SmartBoxError(f"TUN 地址无效：{value}") from error
        interfaces.append(interface)
    if not interfaces:
        raise SmartBoxError("TUN 入站没有可用地址")
    return interfaces


def tun_interface_addresses(runtime_path: Path = RUNTIME_PATH) -> list[str]:
    return [str(interface.ip) for interface in runtime_tun_interfaces(runtime_path)]


def tun_dns_addresses(runtime_path: Path = RUNTIME_PATH) -> list[str]:
    gateways: list[str] = []
    for interface in runtime_tun_interfaces(runtime_path):
        gateway = interface.ip + 1
        if gateway not in interface.network:
            raise SmartBoxError(f"TUN 地址没有可用网关：{interface}")
        gateways.append(str(gateway))
    return gateways


def ufw_enabled() -> bool:
    """Return whether UFW is actively filtering this host.

    The return-path rule below must not alter an inactive firewall's stored
    policy.  Force the status command to the stable C locale so this remains
    independent of the desktop session language.
    """
    if not UFW_COMMAND.is_file() or not os.access(UFW_COMMAND, os.X_OK):
        return False
    try:
        result = run_command(
            ["/usr/bin/env", "LC_ALL=C", "LANG=C", str(UFW_COMMAND), "status"],
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SmartBoxError(f"读取 UFW 状态失败：{error}") from error
    if result.returncode != 0:
        raise SmartBoxError(result.stdout.strip() or "读取 UFW 状态失败")
    status = result.stdout.strip().splitlines()
    if any(line.strip() == "Status: active" for line in status):
        return True
    if any(line.strip() == "Status: inactive" for line in status):
        return False
    raise SmartBoxError("无法确认 UFW 是否已启用")


def _ufw_tun_input_rules() -> list[dict[str, str]]:
    """Read UFW's own input rules without parsing localized CLI output."""
    rules: list[dict[str, str]] = []
    for path in (UFW_USER_RULES_PATH, UFW_USER6_RULES_PATH):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.startswith("### tuple ### "):
                continue
            fields = line.removeprefix("### tuple ### ").split()
            if len(fields) < 7:
                continue
            (
                action,
                protocol,
                destination_port,
                destination,
                source_port,
                source,
                interface,
            ) = fields[:7]
            if action != "allow" or interface != f"in_{TUN_INTERFACE}":
                continue
            comment = next(
                (
                    field.removeprefix("comment=")
                    for field in fields[7:]
                    if field.startswith("comment=")
                ),
                "",
            )
            rules.append(
                {
                    "protocol": protocol,
                    "destination_port": destination_port,
                    "destination": destination,
                    "source_port": source_port,
                    "source": source,
                    "comment": comment,
                }
            )
    return rules


def _ufw_rule_covers_tun_address(rule: dict[str, str], address: str) -> bool:
    if (
        rule.get("protocol") != "any"
        or rule.get("destination_port") != "any"
        or rule.get("source_port") != "any"
    ):
        return False
    try:
        target = ipaddress.ip_address(address)
        destination = ipaddress.ip_network(rule.get("destination", ""), strict=False)
        source = ipaddress.ip_network(rule.get("source", ""), strict=False)
    except ValueError:
        return False
    return (
        target.version == destination.version == source.version
        and target in destination
        and source.prefixlen == 0
    )


def _ufw_tun_rule_is_managed(rule: dict[str, str]) -> bool:
    return rule.get("comment") == UFW_TUN_RULE_COMMENT_HEX


def _run_ufw_tun_rule(action: str, address: str) -> None:
    if action not in ("allow", "delete"):
        raise SmartBoxError("未知的 SmartBox UFW 规则操作")
    command = [str(UFW_COMMAND)]
    if action == "delete":
        command.append("delete")
    command.extend(
        [
            "allow",
            "in",
            "on",
            TUN_INTERFACE,
            "to",
            address,
            "comment",
            UFW_TUN_RULE_COMMENT,
        ]
    )
    try:
        result = run_command(command, timeout=20)
    except (OSError, subprocess.SubprocessError) as error:
        raise SmartBoxError(f"更新 SmartBox UFW 规则失败：{error}") from error
    if result.returncode != 0:
        raise SmartBoxError(result.stdout.strip() or "更新 SmartBox UFW 规则失败")


def install_ufw_tun_rules(runtime_path: Path = RUNTIME_PATH) -> list[str]:
    """Allow only virtual return traffic needed by the current SmartBox TUN.

    sing-box writes proxied replies into the TUN as ingress packets.  UFW sees
    these as INPUT traffic rather than conntrack-established physical replies,
    so a default-deny firewall otherwise drops them.  Existing user rules that
    already cover the same address are preserved unchanged.
    """
    if not ufw_enabled():
        return []
    if os.geteuid() != 0:
        raise SmartBoxError("安装 SmartBox UFW 规则必须由 root 服务执行")
    added: list[str] = []
    existing = _ufw_tun_input_rules()
    try:
        for address in tun_interface_addresses(runtime_path):
            if any(
                _ufw_rule_covers_tun_address(rule, address) for rule in existing
            ):
                continue
            _run_ufw_tun_rule("allow", address)
            added.append(address)
    except Exception as error:
        rollback_error = ""
        for address in reversed(added):
            try:
                _run_ufw_tun_rule("delete", address)
            except SmartBoxError as cleanup_error:
                rollback_error = str(cleanup_error)
        if rollback_error:
            raise SmartBoxError(f"{error}；UFW 回退失败：{rollback_error}") from error
        raise
    return added


def remove_ufw_tun_rules() -> list[str]:
    """Remove only SmartBox's tagged UFW rules while UFW remains enabled."""
    if not ufw_enabled():
        return []
    if os.geteuid() != 0:
        raise SmartBoxError("清理 SmartBox UFW 规则必须由 root 服务执行")
    removed: list[str] = []
    for rule in _ufw_tun_input_rules():
        if not _ufw_tun_rule_is_managed(rule):
            continue
        destination = rule.get("destination", "")
        try:
            network = ipaddress.ip_network(destination, strict=False)
        except ValueError:
            continue
        if network.prefixlen != network.max_prefixlen:
            continue
        _run_ufw_tun_rule("delete", str(network.network_address))
        removed.append(str(network.network_address))
    return removed


def _run_checked_command(
    command: list[str], action: str, timeout: float = 10
) -> subprocess.CompletedProcess[str]:
    try:
        result = run_command(command, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise SmartBoxError(f"{action}失败：{error}") from error
    if result.returncode != 0:
        detail = result.stdout.strip() or f"退出状态 {result.returncode}"
        raise SmartBoxError(f"{action}失败：{detail}")
    return result


def _run_json_command(command: list[str], action: str) -> Any:
    result = _run_checked_command(command, action)
    try:
        return json.loads(result.stdout or "[]")
    except (TypeError, json.JSONDecodeError) as error:
        raise SmartBoxError(f"{action}失败：命令返回了无效 JSON") from error


def _json_object_list(command: list[str], action: str) -> list[dict[str, Any]]:
    payload = _run_json_command(command, action)
    if not isinstance(payload, list) or not all(
        isinstance(item, dict) for item in payload
    ):
        raise SmartBoxError(f"{action}失败：命令返回结构无效")
    return payload


def _integer_field(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _rule_priority(rule: dict[str, Any]) -> int | None:
    return _integer_field(rule.get("priority", rule.get("pref")))


def _route_table(route: dict[str, Any]) -> int | None:
    return _integer_field(route.get("table"))


def _interface_rule_selectors(
    rule: dict[str, Any], interface_name: str
) -> list[tuple[str, str]]:
    selectors: list[tuple[str, str]] = []
    for field, argument in (
        ("iifname", "iif"),
        ("iif", "iif"),
        ("oifname", "oif"),
        ("oif", "oif"),
    ):
        if rule.get(field) == interface_name and (argument, interface_name) not in selectors:
            selectors.append((argument, interface_name))
    return selectors


def _dedicated_rule_priority(priority: int | None) -> bool:
    return priority == SMART_BOX_AUTO_REDIRECT_FALLBACK_RULE_INDEX or (
        priority is not None
        and SMART_BOX_ROUTE_RULE_INDEX <= priority <= SMART_BOX_ROUTE_RULE_LAST
    )


def _legacy_rule_owned(rule: dict[str, Any], interface_name: str) -> bool:
    priority = _rule_priority(rule)
    legacy_priority = priority == LEGACY_SING_TUN_AUTO_REDIRECT_FALLBACK_RULE_INDEX or (
        priority is not None
        and LEGACY_SING_TUN_ROUTE_RULE_INDEX
        <= priority
        <= LEGACY_SING_TUN_ROUTE_RULE_LAST
    )
    return legacy_priority and bool(_interface_rule_selectors(rule, interface_name))


def _ip_rule_snapshot(family: str) -> list[dict[str, Any]]:
    return _json_object_list(
        [IP_COMMAND, family, "-j", "rule", "show"],
        f"读取 {family} 策略规则",
    )


def _ip_route_snapshot(family: str) -> list[dict[str, Any]]:
    return _json_object_list(
        [IP_COMMAND, family, "-j", "route", "show", "table", "all"],
        f"读取 {family} 路由表",
    )


def smart_box_policy_residuals(
    interface_name: str = TUN_INTERFACE,
) -> list[str]:
    """List only policy-routing entries provably owned by SmartBox."""
    residuals: list[str] = []
    for family in ("-4", "-6"):
        for rule in _ip_rule_snapshot(family):
            priority = _rule_priority(rule)
            if _dedicated_rule_priority(priority) or _legacy_rule_owned(
                rule, interface_name
            ):
                residuals.append(f"{family} rule priority {priority}")
        for route in _ip_route_snapshot(family):
            table = _route_table(route)
            if table == SMART_BOX_ROUTE_TABLE_INDEX or (
                table == LEGACY_SING_TUN_ROUTE_TABLE_INDEX
                and route.get("dev") == interface_name
            ):
                residuals.append(
                    f"{family} route table {table} dev {route.get('dev', '-') }"
                )
    return residuals


def cleanup_policy_routing(interface_name: str = TUN_INTERFACE) -> None:
    """Delete the dedicated namespace and evidenced legacy SmartBox entries."""
    failures: list[str] = []
    for family in ("-4", "-6"):
        try:
            rules = _ip_rule_snapshot(family)
        except SmartBoxError as error:
            failures.append(str(error))
            rules = []
        for rule in rules:
            priority = _rule_priority(rule)
            dedicated = _dedicated_rule_priority(priority)
            legacy_owned = _legacy_rule_owned(rule, interface_name)
            if priority is None or not (dedicated or legacy_owned):
                continue
            command = [IP_COMMAND, family, "rule", "delete", "priority", str(priority)]
            if legacy_owned and not dedicated:
                for argument, value in _interface_rule_selectors(
                    rule, interface_name
                ):
                    command.extend([argument, value])
            try:
                _run_checked_command(
                    command, f"删除 {family} SmartBox 策略规则 {priority}"
                )
            except SmartBoxError as error:
                failures.append(str(error))

        try:
            routes = _ip_route_snapshot(family)
        except SmartBoxError as error:
            failures.append(str(error))
            routes = []
        dedicated_routes = any(
            _route_table(route) == SMART_BOX_ROUTE_TABLE_INDEX for route in routes
        )
        legacy_routes = any(
            _route_table(route) == LEGACY_SING_TUN_ROUTE_TABLE_INDEX
            and route.get("dev") == interface_name
            for route in routes
        )
        if dedicated_routes:
            try:
                _run_checked_command(
                    [
                        IP_COMMAND,
                        family,
                        "route",
                        "flush",
                        "table",
                        str(SMART_BOX_ROUTE_TABLE_INDEX),
                    ],
                    f"清空 {family} SmartBox 专属路由表",
                )
            except SmartBoxError as error:
                failures.append(str(error))
        if legacy_routes:
            try:
                _run_checked_command(
                    [
                        IP_COMMAND,
                        family,
                        "route",
                        "flush",
                        "table",
                        str(LEGACY_SING_TUN_ROUTE_TABLE_INDEX),
                        "dev",
                        interface_name,
                    ],
                    f"清理 {family} 旧版 SmartBox 接口路由",
                )
            except SmartBoxError as error:
                failures.append(str(error))

    try:
        residuals = smart_box_policy_residuals(interface_name)
        if residuals:
            failures.append("仍有 SmartBox 策略路由残留：" + ", ".join(residuals))
    except SmartBoxError as error:
        failures.append(f"复核 SmartBox 策略路由失败：{error}")
    if failures:
        raise SmartBoxError("；".join(failures))


def _nft_table_present(payload: Any) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("nftables"), list):
        raise SmartBoxError("读取 nftables 表失败：命令返回结构无效")
    for item in payload["nftables"]:
        table = item.get("table") if isinstance(item, dict) else None
        if (
            isinstance(table, dict)
            and table.get("family") == SING_BOX_NFTABLE_FAMILY
            and table.get("name") == SING_BOX_NFTABLE_NAME
        ):
            return True
    return False


def _json_contains_exact_string(payload: Any, expected: str) -> bool:
    if isinstance(payload, str):
        return payload == expected
    if isinstance(payload, list):
        return any(_json_contains_exact_string(item, expected) for item in payload)
    if isinstance(payload, dict):
        return any(
            _json_contains_exact_string(value, expected) for value in payload.values()
        )
    return False


def sing_box_nftable_references_interface(
    interface_name: str = TUN_INTERFACE,
) -> bool:
    tables = _run_json_command(
        [NFT_COMMAND, "-j", "list", "tables"], "读取 nftables 表"
    )
    if not _nft_table_present(tables):
        return False
    table = _run_json_command(
        [
            NFT_COMMAND,
            "-j",
            "list",
            "table",
            SING_BOX_NFTABLE_FAMILY,
            SING_BOX_NFTABLE_NAME,
        ],
        "读取 sing-box nftables 表",
    )
    return _json_contains_exact_string(table, interface_name)


def cleanup_sing_box_nftables(interface_name: str = TUN_INTERFACE) -> bool:
    """Delete the shared-name table only when stopped and explicitly ours."""
    if managed_service_active(SERVICE_UNIT):
        return False
    if not sing_box_nftable_references_interface(interface_name):
        return False
    _run_checked_command(
        [
            NFT_COMMAND,
            "delete",
            "table",
            SING_BOX_NFTABLE_FAMILY,
            SING_BOX_NFTABLE_NAME,
        ],
        "删除 SmartBox nftables 表",
    )
    tables = _run_json_command(
        [NFT_COMMAND, "-j", "list", "tables"], "复核 nftables 表"
    )
    if _nft_table_present(tables):
        raise SmartBoxError("删除 SmartBox nftables 表失败：目标表仍然存在")
    return True


def revert_link_dns(interface_name: str = TUN_INTERFACE) -> None:
    failures: list[str] = []
    if interface_exists(interface_name):
        try:
            _run_checked_command(
                ["resolvectl", "revert", interface_name],
                f"撤销 {interface_name} 链路 DNS",
            )
        except SmartBoxError as error:
            failures.append(str(error))
    try:
        flush_dns_cache()
    except SmartBoxError as error:
        failures.append(str(error))
    if failures:
        raise SmartBoxError("；".join(failures))


def _raise_dns_configuration_error(
    interface_name: str, error: Exception
) -> None:
    try:
        revert_link_dns(interface_name)
    except Exception as rollback_error:  # noqa: BLE001 - preserve both failures
        raise SmartBoxError(f"{error}；SmartBox DNS 回退失败：{rollback_error}") from error
    if isinstance(error, SmartBoxError):
        raise error
    raise SmartBoxError(str(error)) from error


def configure_link_dns(interface_name: str = TUN_INTERFACE) -> None:
    if not wait_for(lambda: interface_exists(interface_name), 10):
        raise SmartBoxError(f"等待 {interface_name} 接口超时")
    gateways = tun_dns_addresses()
    commands = (
        ["resolvectl", "dns", interface_name, *gateways],
        ["resolvectl", "domain", interface_name, "~."],
        ["resolvectl", "default-route", interface_name, "yes"],
    )
    for command in commands:
        try:
            _run_checked_command(command, "注册 SmartBox DNS")
        except Exception as error:  # noqa: BLE001 - rollback also must be reported
            _raise_dns_configuration_error(interface_name, error)
    try:
        flush_dns_cache()
    except Exception as error:  # noqa: BLE001 - rollback also must be reported
        _raise_dns_configuration_error(interface_name, error)


def cleanup_tun(
    interface_name: str = TUN_INTERFACE,
    timeout: float = 12,
    verify_direct: bool = False,
) -> None:
    """Remove every SmartBox-owned network artifact without short-circuiting."""
    if timeout <= 0:
        raise SmartBoxError("TUN 清理超时参数无效")
    failures: list[str] = []

    def attempt(label: str, operation: Any) -> None:
        try:
            operation()
        except Exception as error:  # noqa: BLE001 - every cleanup step must run
            failures.append(f"{label}：{error}")

    attempt("撤销链路 DNS 失败", lambda: revert_link_dns(interface_name))
    attempt("清理策略路由失败", lambda: cleanup_policy_routing(interface_name))
    attempt(
        "清理 nftables 失败", lambda: cleanup_sing_box_nftables(interface_name)
    )

    def delete_interface() -> None:
        if not interface_exists(interface_name):
            return
        try:
            result = run_command(
                [IP_COMMAND, "link", "delete", "dev", interface_name], timeout=10
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise SmartBoxError(f"删除 {interface_name} TUN 失败：{error}") from error
        if result.returncode != 0 and interface_exists(interface_name):
            detail = result.stdout.strip() or "ip link delete 返回失败"
            raise SmartBoxError(f"删除 {interface_name} TUN 失败：{detail}")

    attempt("删除 TUN 失败", delete_interface)
    if not wait_for(lambda: not interface_exists(interface_name), timeout, 0.2):
        failures.append(f"确认 TUN 清理失败：{interface_name} TUN 未及时清理")
    attempt("最终刷新 DNS 缓存失败", flush_dns_cache)

    def verify_policy() -> None:
        residuals = smart_box_policy_residuals(interface_name)
        if residuals:
            raise SmartBoxError(", ".join(residuals))

    attempt("最终复核策略路由失败", verify_policy)
    if verify_direct:
        attempt(
            "fail-open 直连验收失败",
            lambda: verify_fail_open(timeout=max(1.0, min(float(timeout), 6.0))),
        )
    if failures:
        raise SmartBoxError("SmartBox fail-open 清理未完整完成：" + "；".join(failures))


def api_request(
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 2,
) -> Any:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        API_BASE + path,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise SmartBoxError(f"核心 API 不可用：{error}") from error
    if not content:
        return None
    try:
        return json.loads(content)
    except ValueError as error:
        raise SmartBoxError("核心 API 返回了无效 JSON") from error


def probe_group_delays(
    name: str,
    expected: Iterable[str] | None = None,
    url: str = CONNECTIVITY_PROBE_URL,
    timeout_ms: int = 8000,
) -> dict[str, Any]:
    """Measure every member of a Clash API outbound group.

    The endpoint only returns successful members, so ``expected`` is used to
    make failed or omitted members visible to the UI. This helper is read-only
    and never changes the selected outbound.
    """
    if not isinstance(name, str) or not name.strip():
        raise SmartBoxError("测速策略名称不能为空")
    if not isinstance(url, str) or not url.strip():
        raise SmartBoxError("测速地址不能为空")
    try:
        timeout_value = int(timeout_ms)
    except (TypeError, ValueError) as error:
        raise SmartBoxError("测速超时时间无效") from error
    if timeout_value < 1000 or timeout_value > 60000:
        raise SmartBoxError("测速超时时间应在 1000 到 60000 毫秒之间")

    expected_names: list[str] = []
    seen: set[str] = set()
    if expected is not None:
        for item in expected:
            if not isinstance(item, str) or not item or item in seen:
                continue
            seen.add(item)
            expected_names.append(item)

    encoded_name = urllib.parse.quote(name, safe="")
    query = urllib.parse.urlencode(
        {"url": url.strip(), "timeout": str(timeout_value)}
    )
    response = api_request(
        f"/group/{encoded_name}/delay?{query}",
        timeout=max(2.0, timeout_value / 1000 + 2.0),
    )
    if not isinstance(response, dict):
        raise SmartBoxError("测速接口返回了无效结果")

    delays: dict[str, int] = {}
    for node, value in response.items():
        if not isinstance(node, str) or isinstance(value, bool):
            continue
        try:
            delay = int(value)
        except (TypeError, ValueError):
            continue
        if 0 < delay <= 65535:
            delays[node] = delay

    failed = [node for node in expected_names if node not in delays]
    ordered = sorted(delays.items(), key=lambda item: (item[1], item[0]))
    return {
        "name": name,
        "url": url.strip(),
        "timeout_ms": timeout_value,
        "expected": expected_names,
        "delays": dict(ordered),
        "failed": failed,
        "tested": len(delays),
        "total": len(expected_names) if expected_names else len(delays),
    }


def format_group_delay_summary(result: dict[str, Any]) -> str:
    delays = result.get("delays", {})
    if not isinstance(delays, dict) or not delays:
        total = result.get("total", 0)
        return f"无可用节点 · 0/{total}" if total else "无可用节点"
    values = [int(value) for value in delays.values() if isinstance(value, int)]
    if not values:
        return "无可用节点"
    tested = int(result.get("tested", len(values)) or len(values))
    total = int(result.get("total", tested) or tested)
    return f"最快 {min(values)} ms · {tested}/{total}"


def format_group_delay_details(result: dict[str, Any]) -> str:
    delays = result.get("delays", {})
    lines: list[str] = []
    expected = result.get("expected", [])
    if isinstance(expected, list) and isinstance(delays, dict):
        for node in expected:
            if not isinstance(node, str):
                continue
            delay = delays.get(node)
            lines.append(f"{node}: {delay} ms" if isinstance(delay, int) else f"{node}: 失败")
        expected_set = {node for node in expected if isinstance(node, str)}
        for node, delay in delays.items():
            if node not in expected_set:
                lines.append(f"{node}: {delay} ms")
    elif isinstance(delays, dict):
        lines.extend(f"{node}: {delay} ms" for node, delay in delays.items())
    failed = result.get("failed", [])
    if not lines and isinstance(failed, list):
        lines.extend(f"{node}: 失败" for node in failed if isinstance(node, str))
    return "\n".join(lines) or "没有可用测速结果"


def probe_connectivity(
    url: str = CONNECTIVITY_PROBE_URL, timeout: float = 8.0
) -> dict[str, Any]:
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "*/*",
            "Cache-Control": "no-cache",
            "User-Agent": f"smart-box-linux/{APP_VERSION}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or 0)
            response.read(1)
    except urllib.error.HTTPError as error:
        try:
            status = int(error.code)
            # A reachable HTTP endpoint may intentionally return 403/404/429.
            # Treat only server-side failures as a connectivity failure.
            if not 200 <= status < 500:
                return {
                    "online": False,
                    "url": url,
                    "http_status": status,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "error": f"HTTP {status}",
                }
        finally:
            error.close()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        return {
            "online": False,
            "url": url,
            "http_status": 0,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "error": str(reason),
        }
    return {
        "online": 200 <= status < 500,
        "url": url,
        "http_status": status,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "error": "" if 200 <= status < 500 else f"HTTP {status}",
    }


def probe_direct_connectivity(
    url: str = DIRECT_CONNECTIVITY_PROBE_URL, timeout: float = 6.0
) -> dict[str, Any]:
    """Probe physical DNS/HTTPS with environment and desktop proxies disabled."""
    if timeout <= 0 or timeout > 6:
        raise SmartBoxError("直连验收超时应在 0 到 6 秒之间")
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "*/*",
            "Cache-Control": "no-cache",
            "User-Agent": f"smart-box-linux/{APP_VERSION}",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or 0)
            response.read(1)
    except urllib.error.HTTPError as error:
        try:
            status = int(error.code)
        finally:
            error.close()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        return {
            "online": False,
            "url": url,
            "http_status": 0,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "error": str(reason),
        }
    online = 200 <= status < 500
    return {
        "online": online,
        "url": url,
        "http_status": status,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "error": "" if online else f"HTTP {status}",
    }


def probe_connectivity_matrix(
    probes: Iterable[dict[str, str]] | None = None, timeout: float = 6.0
) -> dict[str, Any]:
    selected = list(probes if probes is not None else CONNECTIVITY_PROBES)
    if not selected:
        raise SmartBoxError("联网验收矩阵为空")

    def run_probe(item: dict[str, str]) -> dict[str, Any]:
        result = probe_connectivity(item["url"], timeout=timeout)
        return {**result, "key": item["key"], "label": item["label"]}

    completed: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(selected), thread_name_prefix="smart-box-connectivity"
    ) as executor:
        futures = {executor.submit(run_probe, item): item for item in selected}
        for future in concurrent.futures.as_completed(futures):
            item = futures[future]
            try:
                completed[item["key"]] = future.result()
            except Exception as error:  # noqa: BLE001 - every path must be reported
                completed[item["key"]] = {
                    "online": False,
                    "url": item["url"],
                    "http_status": 0,
                    "latency_ms": 0,
                    "error": str(error),
                    "key": item["key"],
                    "label": item["label"],
                }

    checks = [completed[item["key"]] for item in selected]
    passed = sum(1 for check in checks if check.get("online"))
    failed = [
        f"{check['label']}（{check.get('error') or '请求失败'}）"
        for check in checks
        if not check.get("online")
    ]
    return {
        "online": passed == len(checks),
        "passed": passed,
        "total": len(checks),
        "checks": checks,
        "latency_ms": max(int(check.get("latency_ms", 0)) for check in checks),
        "error": "；".join(failed),
    }


def probe_connectivity_stable(
    required_successes: int = 2,
    max_attempts: int = 4,
    timeout: float = 6.0,
    interval: float = 1.0,
) -> dict[str, Any]:
    if required_successes < 1 or max_attempts < required_successes:
        raise ValueError("invalid connectivity stability thresholds")
    consecutive = 0
    last_result: dict[str, Any] = {}
    for attempt in range(1, max_attempts + 1):
        last_result = dict(probe_connectivity_matrix(timeout=timeout))
        consecutive = consecutive + 1 if last_result.get("online") else 0
        last_result.update(
            {
                "stable": consecutive >= required_successes,
                "consecutive_passes": consecutive,
                "attempts": attempt,
                "required_successes": required_successes,
            }
        )
        if last_result["stable"]:
            return last_result
        if attempt < max_attempts:
            time.sleep(interval)
    reason = str(last_result.get("error") or "关键路径未连续通过")
    last_result["error"] = f"未连续通过 {required_successes} 轮验收：{reason}"
    return last_result


def format_connectivity_result(result: dict[str, Any]) -> str:
    passed = int(result.get("passed", 0))
    total = int(result.get("total", 0))
    latency = int(result.get("latency_ms", 0))
    summary = f"{passed}/{total} 路 · 最慢 {latency} ms"
    if result.get("stable"):
        summary += f" · 连续 {int(result.get('consecutive_passes', 0))} 轮"
    return summary


def connectivity_is_usable(result: dict[str, Any]) -> bool:
    """Return whether enabling the TUN is unlikely to black-hole the host.

    A single remote probe can fail while a working proxy exists. Conversely,
    enabling a strict TUN with only the domestic path alive makes international
    traffic appear completely offline. Require domestic reachability, the
    forced Smart probe, and one independent remote endpoint. A single endpoint
    such as gstatic is deliberately not a hard gate.
    """
    checks = result.get("checks")
    if isinstance(checks, list):
        by_key = {
            item.get("key"): bool(item.get("online"))
            for item in checks
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        }
        return (
            by_key.get("domestic", False)
            and by_key.get("proxy", False)
            and any(by_key.get(key, False) for key in ("basic", "github", "telegram"))
        )
    try:
        return int(result.get("passed", 0)) >= 3
    except (TypeError, ValueError):
        return False


def connectivity_is_usable_for_mode(result: dict[str, Any], mode: str | None) -> bool:
    """Apply a mode-aware data-plane acceptance rule to a probe matrix.

    Rule and Global modes retain a domestic path, a forced Smart probe, and an
    independent remote endpoint. Direct mode intentionally has no usable proxy
    route, so treating overseas checks as mandatory would turn a healthy direct
    session off merely because those sites are unreachable. Energy-saving mode
    only proxies explicit policies, and uses its forced Smart probe instead.
    """
    if mode not in ("Direct", "节能"):
        return connectivity_is_usable(result)
    checks = result.get("checks")
    if isinstance(checks, list):
        by_key = {
            item.get("key"): bool(item.get("online"))
            for item in checks
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        }
        if mode == "Direct":
            return by_key.get("domestic", False)
        # Energy-saving mode intentionally sends generic Linux traffic DIRECT.
        # The proxy URL above is explicitly routed before that catch-all, so it
        # remains a real proxy data-plane test without requiring other overseas
        # sites that the selected mode deliberately bypasses.
        return by_key.get("domestic", False) and by_key.get("proxy", False)
    try:
        required = 1 if mode == "Direct" else 2
        return int(result.get("passed", 0)) >= required
    except (TypeError, ValueError):
        return False


def connectivity_is_usable_for_watchdog(
    result: dict[str, Any], mode: str | None
) -> bool:
    """Keep a working TUN alive when one remote probe is transiently bad.

    The startup gate deliberately requires the forced Smart probe so a newly
    installed strict route cannot hide a broken proxy.  Once the TUN is
    already carrying traffic, stopping it because one endpoint's TLS
    handshake timed out is more disruptive than keeping the partially
    degraded route alive.  Require the local path and two independent remote
    paths for Rule/Global; Direct and energy-saving retain their mode-specific
    local requirements.
    """
    checks = result.get("checks")
    if not isinstance(checks, list):
        try:
            passed = int(result.get("passed", 0))
        except (TypeError, ValueError):
            return False
        return passed >= (1 if mode == "Direct" else 3)

    by_key = {
        item.get("key"): bool(item.get("online"))
        for item in checks
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }
    if not by_key.get("domestic", False):
        return False
    if mode == "Direct":
        return True
    if mode == "节能":
        return by_key.get("proxy", False)
    remote_keys = ("proxy", "basic", "github", "telegram")
    return sum(by_key.get(key, False) for key in remote_keys) >= 2


def probe_connectivity_guard(
    timeout: float = WATCHDOG_PROBE_TIMEOUT,
    interval: float = WATCHDOG_GUARD_INTERVAL,
    mode: str | None = None,
) -> dict[str, Any]:
    """Confirm an unusable route twice before the running-TUN watchdog acts."""
    first = probe_connectivity_matrix(timeout=timeout)
    if connectivity_is_usable_for_mode(first, mode):
        return {**first, "guard_confirmed": False, "guard_attempts": 1}
    if interval > 0:
        time.sleep(interval)
    second = probe_connectivity_matrix(timeout=timeout)
    return {
        **second,
        "guard_confirmed": not connectivity_is_usable_for_mode(second, mode),
        "guard_attempts": 2,
    }


def configured_mode() -> str:
    """Read the locally selected Clash mode without depending on the core API."""
    mode = load_settings().get("mode", "Rule")
    return mode if mode in VALID_MODES else "Rule"


def _watchdog_log(event: str, **details: Any) -> None:
    record = {"event": event, **details}
    print(
        "smart-box watchdog: " + json.dumps(record, ensure_ascii=False, sort_keys=True),
        file=sys.stderr,
        flush=True,
    )


def managed_service_unit(value: str | None = None) -> str:
    """Return a conservative system-service target for the privileged watchdog."""
    unit = (value or SERVICE_UNIT).strip()
    if not SERVICE_UNIT_NAME.fullmatch(unit):
        raise SmartBoxError("守护目标必须是 smart-box@用户名.service")
    return unit


def managed_service_active(unit: str) -> bool:
    """Return a known systemd active/inactive state or surface query failures.

    ``Restart=on-failure`` on the watchdog is meaningful only if timeout and
    D-Bus errors are failures. Exit status 3 is systemctl's documented
    inactive/failed state and is the one normal loop-exit condition.
    """
    try:
        result = systemctl_service("is-active", "--quiet", unit, timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        raise SmartBoxError(f"读取 {unit} 状态失败：{error}") from error
    if result.returncode == 0:
        return True
    if result.returncode == 3:
        return False
    detail = result.stdout.strip() or f"systemctl 退出状态 {result.returncode}"
    raise SmartBoxError(f"读取 {unit} 状态失败：{detail}")


def verify_fail_open(timeout: float = 6.0) -> dict[str, Any]:
    """Verify that SmartBox released the host and physical DNS/HTTPS works."""
    if timeout <= 0 or timeout > 6:
        raise SmartBoxError("fail-open 验收超时应在 0 到 6 秒之间")
    failures: list[str] = []
    services: dict[str, str] = {}
    for unit in smart_box_system_units():
        try:
            active = managed_service_active(unit)
        except SmartBoxError as error:
            failures.append(str(error))
            services[unit] = "unknown"
            continue
        services[unit] = "active" if active else "inactive"
        if active:
            failures.append(f"{unit} 仍在运行")

    tun_absent = not interface_exists(TUN_INTERFACE)
    if not tun_absent:
        failures.append(f"{TUN_INTERFACE} TUN 仍然存在")

    try:
        residuals = smart_box_policy_residuals(TUN_INTERFACE)
        if residuals:
            failures.append("仍有 SmartBox 策略路由残留：" + ", ".join(residuals))
    except SmartBoxError as error:
        failures.append(f"复核 SmartBox 策略路由失败：{error}")

    try:
        if sing_box_nftable_references_interface(TUN_INTERFACE):
            failures.append("inet sing-box 仍明确引用 SmartBox")
    except SmartBoxError as error:
        failures.append(f"复核 SmartBox nftables 失败：{error}")

    try:
        flush_dns_cache()
    except SmartBoxError as error:
        failures.append(f"复核系统 DNS 失败：{error}")

    direct = probe_direct_connectivity(timeout=timeout)
    if not direct.get("online"):
        failures.append(f"物理直连不可用：{direct.get('error') or '请求失败'}")
    if failures:
        raise SmartBoxError("；".join(failures))
    return {
        "services": services,
        "tun_absent": tun_absent,
        "dns_flushed": True,
        "direct": direct,
    }


def watchdog_startup_check(
    timeout: float = WATCHDOG_STARTUP_TIMEOUT,
    probe_timeout: float = WATCHDOG_PROBE_TIMEOUT,
    interval: float = WATCHDOG_GUARD_INTERVAL,
) -> dict[str, Any]:
    """Require a ready TUN and two usable probe rounds before activation.

    This executes from the main unit's ``ExecStartPost``. It intentionally does
    not ask systemd whether that same unit is active, because it is still in its
    start transaction while its post-start command runs.
    """
    if timeout <= 0 or probe_timeout <= 0 or interval < 0:
        raise SmartBoxError("守护启动验收参数无效")
    started = time.monotonic()
    deadline = started + timeout

    def core_ready() -> bool:
        if not interface_exists(TUN_INTERFACE):
            return False
        try:
            api_request("/version", timeout=0.8)
        except SmartBoxError:
            return False
        return True

    remaining = max(0.0, deadline - time.monotonic())
    if not wait_for(core_ready, remaining, min(0.4, max(0.1, interval or 0.4))):
        raise SmartBoxError("核心未在限定时间内建立 TUN 和控制接口")

    mode = configured_mode()
    consecutive = 0
    attempts = 0
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        attempts += 1
        latest = probe_connectivity_guard(
            timeout=probe_timeout,
            interval=interval,
            mode=mode,
        )
        if connectivity_is_usable_for_mode(latest, mode):
            consecutive += 1
            if consecutive >= 2:
                result = {
                    **latest,
                    "mode": mode,
                    "startup_attempts": attempts,
                    "startup_consecutive": consecutive,
                }
                _watchdog_log(
                    "startup-ready",
                    mode=mode,
                    attempts=attempts,
                    summary=format_connectivity_result(latest),
                )
                return result
        else:
            consecutive = 0
        if time.monotonic() < deadline:
            time.sleep(min(max(interval, 0.1), max(0.0, deadline - time.monotonic())))

    reason = str(latest.get("error") or "关键路径未连续通过")
    raise SmartBoxError(f"TUN 启动联网验收失败：{reason}")


def watchdog_loop(
    service_unit: str | None = None,
    interval: float = WATCHDOG_LOOP_INTERVAL,
    failure_limit: int = WATCHDOG_FAILURE_LIMIT,
    probe_timeout: float = WATCHDOG_PROBE_TIMEOUT,
    guard_interval: float = WATCHDOG_GUARD_INTERVAL,
) -> int:
    """Stop an active TUN after repeated, confirmed data-plane failure."""
    if os.geteuid() != 0:
        raise SmartBoxError("运行期联网守护必须由 root 服务执行")
    if interval <= 0 or failure_limit < 1 or probe_timeout <= 0 or guard_interval < 0:
        raise SmartBoxError("守护运行参数无效")
    unit = managed_service_unit(service_unit)
    consecutive_failures = 0
    _watchdog_log("loop-started", service=unit, interval=interval)

    while managed_service_active(unit):
        mode = configured_mode()
        if not interface_exists(TUN_INTERFACE):
            consecutive_failures += 1
            _watchdog_log(
                "tun-missing",
                service=unit,
                mode=mode,
                consecutive_failures=consecutive_failures,
            )
        else:
            try:
                probe = probe_connectivity_guard(
                    timeout=probe_timeout,
                    interval=guard_interval,
                    mode=mode,
                )
            except Exception as error:  # noqa: BLE001 - restart rather than misclassify
                _watchdog_log(
                    "probe-error",
                    service=unit,
                    mode=mode,
                    error=str(error),
                )
                raise SmartBoxError(f"联网守护探测异常：{error}") from error
            else:
                if connectivity_is_usable_for_watchdog(probe, mode):
                    if consecutive_failures:
                        _watchdog_log(
                            "recovered",
                            service=unit,
                            mode=mode,
                            summary=format_connectivity_result(probe),
                        )
                    consecutive_failures = 0
                elif probe.get("guard_confirmed"):
                    consecutive_failures += 1
                    _watchdog_log(
                        "probe-failed",
                        service=unit,
                        mode=mode,
                        consecutive_failures=consecutive_failures,
                        summary=format_connectivity_result(probe),
                        error=str(probe.get("error") or "关键路径不可用"),
                    )
                else:
                    consecutive_failures = 0

        if consecutive_failures >= failure_limit:
            _watchdog_log(
                "stopping-service",
                service=unit,
                mode=mode,
                reason="连续确认的数据面故障",
                consecutive_failures=consecutive_failures,
            )
            stopped = systemctl_service("stop", "--no-block", unit, timeout=10)
            if stopped.returncode != 0:
                raise SmartBoxError(stopped.stdout.strip() or "守护停止 smart-box 失败")
            return 0
        time.sleep(interval)
    _watchdog_log("loop-exited", service=unit, reason="主服务已停止")
    return 0


def service_memory_bytes() -> int:
    try:
        result = systemctl_service(
            "show", "--property=MainPID", "--value", SERVICE_UNIT, timeout=5
        )
        pid = int(result.stdout.strip()) if result.returncode == 0 else 0
        if pid <= 0:
            return 0
        for line in (Path("/proc") / str(pid) / "status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return 0


def read_service_log(lines: int = 300) -> str:
    result = run_command(
        [
            "journalctl",
            "--unit",
            SERVICE_UNIT,
            "--no-pager",
            "--output=short-iso",
            "--lines",
            str(lines),
        ],
        timeout=10,
    )
    if result.returncode != 0:
        raise SmartBoxError(result.stdout.strip() or "读取 smart-box 日志失败")
    return result.stdout


def status_snapshot() -> dict[str, Any]:
    active = unit_active(SERVICE_UNIT)
    snapshot: dict[str, Any] = {
        "active": active,
        "tun": interface_exists(TUN_INTERFACE),
        "flclash": flclash_conflict(),
        "profile": PROFILE_PATH.is_file(),
        "runtime": RUNTIME_PATH.is_file(),
        "api": False,
        "telemetry": False,
        "mode": load_settings().get("mode", "Rule"),
        "connections": 0,
        "upload": 0,
        "download": 0,
        "memory": service_memory_bytes() if active else 0,
    }
    if active:
        try:
            configs = api_request("/configs", timeout=0.6)
            snapshot["api"] = True
            if isinstance(configs, dict):
                snapshot["mode"] = configs.get("mode", snapshot["mode"])
                snapshot["mode_list"] = configs.get("mode-list", [])
        except SmartBoxError:
            return snapshot
        try:
            connections = api_request("/connections", timeout=0.8)
            snapshot["telemetry"] = True
            if isinstance(connections, dict):
                entries = connections.get("connections", [])
                snapshot["connections"] = len(entries) if isinstance(entries, list) else 0
                snapshot["upload"] = int(connections.get("uploadTotal", 0) or 0)
                snapshot["download"] = int(connections.get("downloadTotal", 0) or 0)
        except SmartBoxError:
            pass
    return snapshot


def profile_selectors(profile_path: Path = PROFILE_PATH) -> list[dict[str, Any]]:
    if not profile_path.is_file():
        return []
    profile = validate_profile_shape(load_json(profile_path))
    result: list[dict[str, Any]] = []
    overrides = load_settings().get("selector_overrides", {})
    for outbound in profile.get("outbounds", []):
        if not isinstance(outbound, dict) or outbound.get("type") != "selector":
            continue
        tag = outbound.get("tag")
        choices = outbound.get("outbounds")
        if not isinstance(tag, str) or not isinstance(choices, list):
            continue
        default = overrides.get(tag, outbound.get("default")) if isinstance(overrides, dict) else outbound.get("default")
        result.append(
            {
                "name": tag,
                "all": [choice for choice in choices if isinstance(choice, str)],
                "now": default if isinstance(default, str) else "",
            }
        )
    return result


def set_gui_autostart(enabled: bool) -> None:
    autostart_dir = _xdg_path("XDG_CONFIG_HOME", ".config") / "autostart"
    target = autostart_dir / "smart-box.desktop"
    if enabled:
        autostart_dir.mkdir(parents=True, exist_ok=True)
        content = """[Desktop Entry]
Type=Application
Name=smart-box
Exec={launcher} --background
TryExec={launcher}
Icon=smart-box
Terminal=false
StartupNotify=false
X-KDE-autostart-after=panel
X-GNOME-Autostart-enabled=true
""".format(launcher=GUI_LAUNCHER)
        atomic_write_bytes(target, content.encode("utf-8"), 0o600)
    else:
        target.unlink(missing_ok=True)


def gui_autostart_enabled() -> bool:
    return (_xdg_path("XDG_CONFIG_HOME", ".config") / "autostart/smart-box.desktop").is_file()


def format_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "0 B"


def _cli_prepare(arguments: argparse.Namespace) -> int:
    if arguments.require_no_flclash and flclash_conflict():
        raise SmartBoxError("检测到 FlClash 正在运行，请先通过 smart-box 客户端执行切换")
    # The system service runs this command as the desktop user without an
    # interactive polkit session.  DNS cache flushing belongs to the root
    # cleanup/DNS hooks; doing it here makes an otherwise valid start fail
    # with an authentication error before the TUN is created.
    path = prepare_runtime(check=not arguments.no_check)
    if not arguments.quiet:
        print(path)
    return 0


def _cli_run(_: argparse.Namespace) -> int:
    recover_profile_transaction()
    core = find_core()
    if not core.is_file():
        raise SmartBoxError(f"找不到代理核心：{core}")
    if not RUNTIME_PATH.is_file():
        prepare_runtime()
    ensure_directories()
    os.execv(
        core,
        [
            str(core),
            "run",
            "--disable-color",
            "-D",
            str(STATE_DIR),
            "-c",
            str(RUNTIME_PATH),
        ],
    )
    return 1


def _cli_fetch(arguments: argparse.Namespace) -> int:
    settings = load_settings()
    url = arguments.url or settings.get("subscription_url", "")
    if arguments.stdin_url:
        url = sys.stdin.read().strip()
    summary = fetch_profile(str(url))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _cli_set_url(arguments: argparse.Namespace) -> int:
    url = sys.stdin.read().strip() if arguments.stdin else arguments.url
    normalized = validate_subscription_url(url)
    mutate_settings(lambda settings: settings.__setitem__("subscription_url", normalized))
    print("订阅地址已保存")
    return 0


def _cli_set_stack(arguments: argparse.Namespace) -> int:
    settings = mutate_settings(
        lambda current: current.__setitem__("tun_stack", arguments.stack)
    )
    if PROFILE_PATH.is_file():
        prepare_runtime(settings=settings)
    print(f"TUN 栈已设置为 {arguments.stack}")
    return 0


def _cli_mirror_benchmark(arguments: argparse.Namespace) -> int:
    result = benchmark_mirror_sources(
        repo=arguments.repo,
        timeout=arguments.timeout,
        max_mirrors=arguments.max_mirrors,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _cli_mirror_apply(arguments: argparse.Namespace) -> int:
    repo = str(arguments.repo).lower()
    _mirror_profile(repo)
    result_path = MIRROR_RANKING_DIR / f"{repo}.json"
    try:
        summary = load_json(result_path)
    except (OSError, ValueError, TypeError) as error:
        raise SmartBoxError(f"没有可应用的 {repo} 源测速结果，请先运行 mirror-benchmark") from error
    if not isinstance(summary, dict) or summary.get("repo") != repo:
        raise SmartBoxError("源测速结果与目标源类型不匹配")
    applied = apply_mirror_ranking(summary, timeout=arguments.timeout)
    print(json.dumps(applied, ensure_ascii=False))
    return 0


def _cli_validate(arguments: argparse.Namespace) -> int:
    path = Path(arguments.path).expanduser() if arguments.path else RUNTIME_PATH
    validate_config(path)
    print("配置校验通过")
    return 0


def _cli_dns(arguments: argparse.Namespace) -> int:
    if arguments.action == "install":
        configure_link_dns()
        print("SmartBox 链路 DNS 已注册")
    else:
        revert_link_dns()
        print("SmartBox 链路 DNS 已撤销")
    return 0


def _cli_desktop_proxy(arguments: argparse.Namespace) -> int:
    if arguments.action == "install":
        changed = desktop_proxy_install()
        print("KDE 代理已切换到 SmartBox 20808" if changed else "KDE 代理已是 SmartBox 20808")
    else:
        lifecycle_call = getattr(arguments, "service_lifecycle", False) is True
        if not lifecycle_call and managed_service_active(SERVICE_UNIT):
            raise SmartBoxError(
                "smart-box 核心仍在运行，拒绝恢复 KDE 代理；请先停止服务"
            )
        changed = desktop_proxy_restore()
        print("KDE 代理设置已恢复" if changed else "没有待恢复的 KDE 代理设置")
    return 0


def _cli_firewall(arguments: argparse.Namespace) -> int:
    if arguments.action == "install":
        addresses = install_ufw_tun_rules()
        if addresses:
            print("SmartBox UFW 回程规则已注册：" + ", ".join(addresses))
        else:
            print("UFW 未启用或已有覆盖规则，未改动防火墙")
    else:
        addresses = remove_ufw_tun_rules()
        if addresses:
            print("SmartBox UFW 回程规则已撤销：" + ", ".join(addresses))
        else:
            print("UFW 未启用或没有 SmartBox 管理的规则")
    return 0


def _cli_cleanup(arguments: argparse.Namespace) -> int:
    lifecycle_call = getattr(arguments, "service_lifecycle", False) is True
    if not lifecycle_call and managed_service_active(SERVICE_UNIT):
        raise SmartBoxError(
            "smart-box 核心仍在运行，拒绝清理 TUN、DNS、防火墙和 KDE 代理；"
            "请先停止服务"
        )
    cleanup_error: Exception | None = None
    try:
        cleanup_tun(verify_direct=bool(getattr(arguments, "verify_direct", False)))
    except Exception as error:
        cleanup_error = error
        try:
            remove_ufw_tun_rules()
        except Exception as firewall_error:
            cleanup_error = SmartBoxError(
                f"{cleanup_error}；清理 SmartBox UFW 规则失败：{firewall_error}"
            )
    else:
        try:
            remove_ufw_tun_rules()
        except Exception as firewall_error:
            cleanup_error = firewall_error
    try:
        desktop_proxy_restore()
    except Exception as proxy_error:
        if cleanup_error is not None:
            raise SmartBoxError(f"{cleanup_error}；恢复 KDE 代理失败：{proxy_error}") from proxy_error
        raise
    if cleanup_error is not None:
        raise cleanup_error
    print("SmartBox TUN、路由和链路 DNS 已清理")
    return 0


def _cli_watchdog(arguments: argparse.Namespace) -> int:
    if arguments.startup:
        try:
            result = watchdog_startup_check(
                timeout=arguments.startup_timeout,
                probe_timeout=arguments.probe_timeout,
                interval=arguments.guard_interval,
            )
        except SmartBoxError as error:
            # The startup check runs after sing-box has installed auto-routes.
            # Tear those routes down synchronously instead of leaving a failed
            # strict TUN in the systemd stop window.
            try:
                cleanup_tun()
            except Exception as cleanup_error:  # noqa: BLE001 - retain both causes
                raise SmartBoxError(
                    f"{error}；启动失败后的 TUN 清理也失败：{cleanup_error}"
                ) from error
            raise
        print(
            json.dumps(
                {
                    "status": "ready",
                    "mode": result.get("mode"),
                    "summary": format_connectivity_result(result),
                    "attempts": result.get("startup_attempts", 0),
                },
                ensure_ascii=False,
            )
        )
        return 0
    return watchdog_loop(
        service_unit=arguments.service_unit,
        interval=arguments.interval,
        failure_limit=arguments.failure_limit,
        probe_timeout=arguments.probe_timeout,
        guard_interval=arguments.guard_interval,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smart-box-profile")
    parser.add_argument("--version", action="version", version=f"smart-box {APP_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="生成本机运行配置")
    prepare.add_argument("--require-no-flclash", action="store_true")
    prepare.add_argument("--no-check", action="store_true")
    prepare.add_argument("--quiet", action="store_true")
    prepare.set_defaults(handler=_cli_prepare)

    run = subparsers.add_parser("run", help="运行代理核心")
    run.set_defaults(handler=_cli_run)

    fetch = subparsers.add_parser("fetch", help="拉取并验证订阅")
    fetch.add_argument("--url")
    fetch.add_argument("--stdin-url", action="store_true")
    fetch.set_defaults(handler=_cli_fetch)

    set_url = subparsers.add_parser("set-url", help="保存订阅地址")
    set_url.add_argument("url", nargs="?", default="")
    set_url.add_argument("--stdin", action="store_true")
    set_url.set_defaults(handler=_cli_set_url)

    set_stack = subparsers.add_parser("set-stack", help="设置 Linux TUN 栈")
    set_stack.add_argument("stack", choices=VALID_TUN_STACKS)
    set_stack.set_defaults(handler=_cli_set_stack)

    mirror_benchmark = subparsers.add_parser(
        "mirror-benchmark", help="只读测速 pacman/paru 与 CachyOS 源"
    )
    mirror_benchmark.add_argument(
        "--repo", choices=("arch", "cachyos", "all"), default="all"
    )
    mirror_benchmark.add_argument("--timeout", type=float, default=MIRROR_BENCHMARK_TIMEOUT)
    mirror_benchmark.add_argument(
        "--max-mirrors", type=int, default=MIRROR_BENCHMARK_MAX_MIRRORS
    )
    mirror_benchmark.set_defaults(handler=_cli_mirror_benchmark)

    mirror_apply = subparsers.add_parser(
        "mirror-apply", help="应用最近一次源测速排序（需要 root 授权）"
    )
    mirror_apply.add_argument("--repo", choices=("arch", "cachyos"), required=True)
    mirror_apply.add_argument("--timeout", type=float, default=30.0)
    mirror_apply.set_defaults(handler=_cli_mirror_apply)

    validate = subparsers.add_parser("validate", help="校验运行配置")
    validate.add_argument("path", nargs="?")
    validate.set_defaults(handler=_cli_validate)

    dns = subparsers.add_parser("dns", help="管理 SmartBox 链路 DNS")
    dns.add_argument("action", choices=("install", "remove"))
    dns.set_defaults(handler=_cli_dns)

    desktop_proxy = subparsers.add_parser(
        "desktop-proxy", help="管理 KDE 应用使用的 SmartBox 本地代理"
    )
    desktop_proxy.add_argument("action", choices=("install", "restore"))
    desktop_proxy.add_argument(
        "--service-lifecycle", action="store_true", help=argparse.SUPPRESS
    )
    desktop_proxy.set_defaults(handler=_cli_desktop_proxy)

    firewall = subparsers.add_parser("firewall", help="管理 SmartBox UFW 回程规则")
    firewall.add_argument("action", choices=("install", "remove"))
    firewall.set_defaults(handler=_cli_firewall)

    cleanup = subparsers.add_parser("cleanup", help="清理停止后残留的 SmartBox TUN")
    cleanup.add_argument(
        "--verify-direct",
        action="store_true",
        help="清理后验收服务、DNS 和物理直连",
    )
    cleanup.add_argument(
        "--service-lifecycle", action="store_true", help=argparse.SUPPRESS
    )
    cleanup.set_defaults(handler=_cli_cleanup)

    watchdog = subparsers.add_parser("watchdog", help="执行 SmartBox 联网守护")
    watchdog_mode = watchdog.add_mutually_exclusive_group(required=True)
    watchdog_mode.add_argument("--startup", action="store_true", help="验收 TUN 启动联网")
    watchdog_mode.add_argument("--loop", action="store_true", help="持续守护运行中的 TUN")
    watchdog.add_argument("--service-unit", help="受守护的 smart-box systemd 单元")
    watchdog.add_argument(
        "--startup-timeout",
        type=float,
        default=WATCHDOG_STARTUP_TIMEOUT,
        help="启动验收总超时（秒）",
    )
    watchdog.add_argument(
        "--interval",
        type=float,
        default=WATCHDOG_LOOP_INTERVAL,
        help="运行期守护轮询间隔（秒）",
    )
    watchdog.add_argument(
        "--failure-limit",
        type=int,
        default=WATCHDOG_FAILURE_LIMIT,
        help="连续确认失败次数后停止 TUN",
    )
    watchdog.add_argument(
        "--probe-timeout",
        type=float,
        default=WATCHDOG_PROBE_TIMEOUT,
        help="单轮联网矩阵超时（秒）",
    )
    watchdog.add_argument(
        "--guard-interval",
        type=float,
        default=WATCHDOG_GUARD_INTERVAL,
        help="同一轮双次确认之间的间隔（秒）",
    )
    watchdog.set_defaults(handler=_cli_watchdog)

    status = subparsers.add_parser("status", help="输出运行状态")
    status.set_defaults(handler=lambda _: print(json.dumps(status_snapshot(), ensure_ascii=False)) or 0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        return int(arguments.handler(arguments))
    except SmartBoxError as error:
        print(f"smart-box: {error}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("smart-box: 操作超时", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
