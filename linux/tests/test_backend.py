#!/usr/bin/python3

from __future__ import annotations

import copy
import json
import multiprocessing
import os
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import smart_box_backend as backend


def increment_settings_worker(
    config_dir_text: str,
    state_dir_text: str,
    start: object,
    iterations: int,
) -> None:
    config_dir = Path(config_dir_text)
    backend.CONFIG_DIR = config_dir
    backend.STATE_DIR = Path(state_dir_text)
    backend.SETTINGS_PATH = config_dir / "settings.json"
    start.wait()  # type: ignore[attr-defined]
    for _ in range(iterations):
        def increment(settings: dict) -> None:
            settings["concurrent_counter"] = int(
                settings.get("concurrent_counter", 0)
            ) + 1

        backend.mutate_settings(increment)


def sample_profile() -> dict:
    return {
        "log": {"level": "warn", "timestamp": True},
        "inbounds": [
            {
                "type": "tun",
                "tag": "tun-in",
                "address": ["172.19.0.1/30"],
                "auto_route": True,
                "strict_route": True,
                "stack": "mixed",
            },
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": "0.0.0.0",
                "listen_port": 9999,
                "set_system_proxy": True,
            },
        ],
        "outbounds": [
            {"type": "direct", "tag": "DIRECT"},
            {"type": "block", "tag": "REJECT"},
            {"type": "shadowsocks", "tag": "jp-node"},
            {
                "type": "selector",
                "tag": "🎯 基准 Smart",
                "outbounds": ["🚀 全局 Smart", "🇯🇵 日本 Smart", "DIRECT"],
                "default": "🚀 全局 Smart",
            },
            {
                "type": "selector",
                "tag": "🤖 AI Smart",
                "outbounds": ["🤖 AI Fallback", "🇯🇵 日本 Smart", "DIRECT"],
                "default": "🤖 AI Fallback",
            },
            {
                "type": "smart",
                "tag": "🚀 全局 Smart",
                "outbounds": ["jp-node"],
            },
            {
                "type": "smart",
                "tag": "🇯🇵 日本 Smart",
                "outbounds": ["jp-node"],
            },
            {
                "type": "smart",
                "tag": "🤖 AI Fallback",
                "outbounds": ["jp-node"],
            },
        ],
        "dns": {
            "servers": [
                {"type": "local", "tag": "local"},
                {"type": "https", "tag": "baseline-dns", "server": "1.1.1.1"},
            ],
            "rules": [
                {"clash_mode": "Direct", "action": "route", "server": "local"},
                {
                    "clash_mode": "Global",
                    "action": "route",
                    "server": "baseline-dns",
                },
                {"rule_set": ["private"], "action": "route", "server": "local"},
            ],
            "final": "baseline-dns",
        },
        "route": {
            "rules": [
                {"action": "sniff"},
                {"protocol": "dns", "action": "hijack-dns"},
                {"clash_mode": "Direct", "action": "route", "outbound": "DIRECT"},
                {
                    "clash_mode": "Global",
                    "action": "route",
                    "outbound": "🚀 全局 Smart",
                },
                {"ip_is_private": True, "action": "route", "outbound": "DIRECT"},
            ],
            "final": "🎯 基准 Smart",
        },
        "experimental": {"cache_file": {"enabled": True}, "clash_api": {}},
    }


class DomainParsingTest(unittest.TestCase):
    def test_normalizes_idn_wildcards_and_removes_covered_children(self) -> None:
        domains, invalid = backend.parse_domain_text(
            "*.Example.COM, api.example.com；例子.测试.\nhttps://bad.example 127.0.0.1"
        )
        self.assertEqual(domains, ["example.com", "xn--fsqu00a.xn--0zwm56d"])
        self.assertEqual(invalid, ["https://bad.example", "127.0.0.1"])

    def test_rejects_reserved_and_single_label_names(self) -> None:
        domains, invalid = backend.parse_domain_text(
            "localhost foo printer.lan service.home.arpa valid.example"
        )
        self.assertEqual(domains, ["valid.example"])
        self.assertEqual(
            invalid, ["localhost", "foo", "printer.lan", "service.home.arpa"]
        )

    def test_detects_parent_child_conflicts(self) -> None:
        self.assertEqual(
            backend.domain_conflicts(["example.com"], ["api.example.com"]),
            [("example.com", "api.example.com")],
        )


class DesktopProxyTest(unittest.TestCase):
    def test_kde_proxy_update_preserves_unrelated_settings(self) -> None:
        original = (
            "[Proxy Settings]\n"
            "NoProxyFor=localhost,127.*\n"
            "ProxyType=1\n"
            "httpProxy=http://127.0.0.1:7890\n"
            "httpsProxy=https://127.0.0.1:7890\n"
            "socksProxy=socks://127.0.0.1:7890\n"
            "\n[Other]\nKeep=1\n"
        ).encode()
        updated = backend._kioslaverc_proxy_content(original).decode()
        self.assertIn("NoProxyFor=localhost,127.*", updated)
        self.assertIn("httpProxy=http://127.0.0.1:20808", updated)
        self.assertIn("httpsProxy=http://127.0.0.1:20808", updated)
        self.assertIn("socksProxy=socks://127.0.0.1:20808", updated)
        self.assertIn("[Other]\nKeep=1", updated)

    def test_install_and_restore_round_trip_kde_proxy_file(self) -> None:
        original = b"[Proxy Settings]\nProxyType=0\n\n[Other]\nKeep=1\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "kioslaverc"
            state = root / "desktop-proxy.json"
            backup = root / "kioslaverc.before-smart-box"
            config.write_bytes(original)
            original_stat = config.stat()
            with mock.patch.object(backend, "DESKTOP_PROXY_CONFIG_PATH", config), mock.patch.object(
                backend, "DESKTOP_PROXY_STATE_PATH", state
            ), mock.patch.object(backend, "DESKTOP_PROXY_BACKUP_PATH", backup), mock.patch.object(
                backend, "STATE_DIR", root
            ):
                self.assertTrue(backend.desktop_proxy_install())
                self.assertIn(b"20808", config.read_bytes())
                proxy_state = json.loads(state.read_text(encoding="utf-8"))
                self.assertEqual(proxy_state["original_uid"], original_stat.st_uid)
                self.assertEqual(proxy_state["original_gid"], original_stat.st_gid)
                self.assertEqual(proxy_state["original_mode"], original_stat.st_mode & 0o7777)
                self.assertTrue(backend.desktop_proxy_restore())
            self.assertEqual(config.read_bytes(), original)
            restored_stat = config.stat()
            self.assertEqual(restored_stat.st_uid, original_stat.st_uid)
            self.assertEqual(restored_stat.st_gid, original_stat.st_gid)
            self.assertEqual(restored_stat.st_mode & 0o7777, original_stat.st_mode & 0o7777)
            self.assertFalse(state.exists())
            self.assertFalse(backup.exists())

    def test_restore_preserves_user_change_after_install(self) -> None:
        original = b"[Proxy Settings]\nProxyType=0\n"
        changed = b"[Proxy Settings]\nProxyType=0\nhttpProxy=http://127.0.0.1:9999\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "kioslaverc"
            state = root / "desktop-proxy.json"
            backup = root / "kioslaverc.before-smart-box"
            config.write_bytes(original)
            with mock.patch.object(backend, "DESKTOP_PROXY_CONFIG_PATH", config), mock.patch.object(
                backend, "DESKTOP_PROXY_STATE_PATH", state
            ), mock.patch.object(backend, "DESKTOP_PROXY_BACKUP_PATH", backup), mock.patch.object(
                backend, "STATE_DIR", root
            ):
                backend.desktop_proxy_install()
                config.write_bytes(changed)
                self.assertTrue(backend.desktop_proxy_restore())
            self.assertEqual(config.read_bytes(), changed)
            self.assertFalse(state.exists())

    def test_install_restores_existing_file_when_state_write_fails(self) -> None:
        original = b"[Proxy Settings]\nProxyType=0\n\n[Other]\nKeep=1\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "kioslaverc"
            state = root / "desktop-proxy.json"
            backup = root / "kioslaverc.before-smart-box"
            config.write_bytes(original)
            config.chmod(0o640)
            original_stat = config.stat()

            def fail_state_write(*_args: object, **_kwargs: object) -> None:
                state.write_bytes(b"partially-written state")
                raise OSError("injected state write failure")

            with mock.patch.object(backend, "DESKTOP_PROXY_CONFIG_PATH", config), mock.patch.object(
                backend, "DESKTOP_PROXY_STATE_PATH", state
            ), mock.patch.object(backend, "DESKTOP_PROXY_BACKUP_PATH", backup), mock.patch.object(
                backend, "STATE_DIR", root
            ), mock.patch.object(
                backend, "atomic_write_json", side_effect=fail_state_write
            ):
                with self.assertRaisesRegex(
                    backend.SmartBoxError, "injected state write failure"
                ):
                    backend.desktop_proxy_install()

            self.assertEqual(config.read_bytes(), original)
            restored_stat = config.stat()
            self.assertEqual(restored_stat.st_uid, original_stat.st_uid)
            self.assertEqual(restored_stat.st_gid, original_stat.st_gid)
            self.assertEqual(
                restored_stat.st_mode & 0o7777, original_stat.st_mode & 0o7777
            )
            self.assertFalse(state.exists())
            self.assertFalse(backup.exists())

    def test_install_removes_new_file_when_state_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "kioslaverc"
            state = root / "desktop-proxy.json"
            backup = root / "kioslaverc.before-smart-box"

            def fail_state_write(*_args: object, **_kwargs: object) -> None:
                state.write_bytes(b"partially-written state")
                raise OSError("injected state write failure")

            with mock.patch.object(backend, "DESKTOP_PROXY_CONFIG_PATH", config), mock.patch.object(
                backend, "DESKTOP_PROXY_STATE_PATH", state
            ), mock.patch.object(backend, "DESKTOP_PROXY_BACKUP_PATH", backup), mock.patch.object(
                backend, "STATE_DIR", root
            ), mock.patch.object(
                backend, "atomic_write_json", side_effect=fail_state_write
            ):
                with self.assertRaisesRegex(
                    backend.SmartBoxError, "injected state write failure"
                ):
                    backend.desktop_proxy_install()

            self.assertFalse(config.exists())
            self.assertFalse(state.exists())
            self.assertFalse(backup.exists())

    def test_install_reports_state_and_compensation_failures(self) -> None:
        original = b"[Proxy Settings]\nProxyType=0\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "kioslaverc"
            state = root / "desktop-proxy.json"
            backup = root / "kioslaverc.before-smart-box"
            config.write_bytes(original)
            config.chmod(0o640)
            original_stat = config.stat()
            real_atomic_write_bytes = backend.atomic_write_bytes
            state_error = OSError("injected state write failure")

            def fail_restore(path: Path, content: bytes, mode: int = 0o600) -> None:
                if path == config and content == original:
                    raise OSError("injected content restore failure")
                real_atomic_write_bytes(path, content, mode)

            def fail_state_write(*_args: object, **_kwargs: object) -> None:
                state.write_bytes(b"partially-written state")
                raise state_error

            with mock.patch.object(backend, "DESKTOP_PROXY_CONFIG_PATH", config), mock.patch.object(
                backend, "DESKTOP_PROXY_STATE_PATH", state
            ), mock.patch.object(backend, "DESKTOP_PROXY_BACKUP_PATH", backup), mock.patch.object(
                backend, "STATE_DIR", root
            ), mock.patch.object(
                backend, "atomic_write_bytes", side_effect=fail_restore
            ), mock.patch.object(
                backend, "atomic_write_json", side_effect=fail_state_write
            ):
                with self.assertRaises(backend.SmartBoxError) as raised:
                    backend.desktop_proxy_install()

            message = str(raised.exception)
            self.assertIn("injected state write failure", message)
            self.assertIn("injected content restore failure", message)
            self.assertIs(raised.exception.__cause__, state_error)
            self.assertIn(b"20808", config.read_bytes())
            self.assertEqual(config.stat().st_mode & 0o7777, original_stat.st_mode & 0o7777)
            self.assertFalse(state.exists())
            self.assertFalse(backup.exists())


class RuntimeProfileTest(unittest.TestCase):
    def test_runtime_settings_snapshot_covers_every_runtime_input(self) -> None:
        self.assertEqual(
            set(backend.RUNTIME_SETTINGS_FIELDS),
            {
                "mode",
                "tun_stack",
                "log_level",
                "allow_domains",
                "proxy_domains",
                "selector_overrides",
            },
        )
        baseline = backend.runtime_settings_snapshot(backend.DEFAULT_SETTINGS)
        changes = {
            "mode": "Global",
            "tun_stack": "system",
            "log_level": "debug",
            "allow_domains": ["direct.example"],
            "proxy_domains": ["proxy.example"],
            "selector_overrides": {"group": "node"},
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                changed = copy.deepcopy(backend.DEFAULT_SETTINGS)
                changed[field] = value
                self.assertNotEqual(
                    backend.runtime_settings_snapshot(changed), baseline
                )

    def test_smart_score_identities_isolate_node_changes(self) -> None:
        source = sample_profile()
        node = next(item for item in source["outbounds"] if item.get("tag") == "jp-node")
        node.update({"server": "192.0.2.10", "server_port": 443, "password": "secret"})
        baseline = backend.apply_runtime_overrides(
            copy.deepcopy(source), copy.deepcopy(backend.DEFAULT_SETTINGS)
        )
        changed_settings = copy.deepcopy(backend.DEFAULT_SETTINGS)
        changed_settings["selector_overrides"] = {
            "🎯 基准 Smart": "🇯🇵 日本 Smart",
        }
        changed_selector = backend.apply_runtime_overrides(
            copy.deepcopy(source), changed_settings
        )

        baseline_namespaces = {
            item.get("score_namespace")
            for item in baseline["outbounds"]
            if item.get("type") == "smart"
        }
        changed_namespaces = {
            item.get("score_namespace")
            for item in changed_selector["outbounds"]
            if item.get("type") == "smart"
        }
        self.assertEqual(len(baseline_namespaces), 1)
        self.assertEqual(baseline_namespaces, changed_namespaces)
        namespace = baseline_namespaces.pop()
        self.assertEqual(namespace, backend.SMART_SCORE_NAMESPACE)
        self.assertNotEqual(
            baseline["experimental"]["cache_file"]["cache_id"],
            changed_selector["experimental"]["cache_file"]["cache_id"],
        )

        changed_node = copy.deepcopy(source)
        changed_node_entry = next(
            item for item in changed_node["outbounds"] if item.get("tag") == "jp-node"
        )
        changed_node_entry["server"] = "198.51.100.25"
        changed_runtime = backend.apply_runtime_overrides(
            changed_node, copy.deepcopy(backend.DEFAULT_SETTINGS)
        )
        baseline_global = next(
            item
            for item in baseline["outbounds"]
            if item.get("tag") == "🚀 全局 Smart"
        )
        changed_global = next(
            item
            for item in changed_runtime["outbounds"]
            if item.get("tag") == "🚀 全局 Smart"
        )
        selector_global = next(
            item
            for item in changed_selector["outbounds"]
            if item.get("tag") == "🚀 全局 Smart"
        )
        baseline_identity = baseline_global["score_identities"]["jp-node"]
        self.assertEqual(
            baseline_identity,
            selector_global["score_identities"]["jp-node"],
        )
        self.assertNotEqual(
            baseline_identity,
            changed_global["score_identities"]["jp-node"],
        )

    def test_selector_cache_namespace_tracks_local_defaults(self) -> None:
        source = sample_profile()
        baseline = backend.apply_runtime_overrides(
            copy.deepcopy(source), copy.deepcopy(backend.DEFAULT_SETTINGS)
        )
        changed_settings = copy.deepcopy(backend.DEFAULT_SETTINGS)
        changed_settings["selector_overrides"] = {
            "🎯 基准 Smart": "🇯🇵 日本 Smart",
        }
        changed = backend.apply_runtime_overrides(
            copy.deepcopy(source), changed_settings
        )
        baseline_id = baseline["experimental"]["cache_file"]["cache_id"]
        changed_id = changed["experimental"]["cache_file"]["cache_id"]
        self.assertTrue(baseline_id.startswith("smart-box-linux-v2-"))
        self.assertNotEqual(baseline_id, changed_id)

    def test_domain_rules_follow_modes_and_precede_private_rules(self) -> None:
        result = backend.apply_domain_rules(
            copy.deepcopy(sample_profile()), ["direct.example"], ["proxy.example"]
        )
        route_rules = result["route"]["rules"]
        self.assertEqual([rule.get("action") for rule in route_rules[:2]], ["sniff", "hijack-dns"])
        self.assertEqual([rule.get("clash_mode") for rule in route_rules[2:4]], ["Direct", "Global"])
        self.assertEqual(route_rules[4]["outbound"], "DIRECT")
        self.assertEqual(route_rules[4]["domain_suffix"], ["direct.example"])
        self.assertEqual(route_rules[5]["outbound"], "🎯 基准 Smart")
        self.assertEqual(route_rules[5]["domain_suffix"], ["proxy.example"])
        self.assertTrue(route_rules[6]["ip_is_private"])

        dns_rules = result["dns"]["rules"]
        self.assertEqual([rule.get("clash_mode") for rule in dns_rules[:2]], ["Direct", "Global"])
        self.assertEqual(dns_rules[2]["server"], "local")
        self.assertEqual(dns_rules[3]["server"], "baseline-dns")
        self.assertEqual(dns_rules[4]["rule_set"], ["private"])

    def test_runtime_overrides_are_linux_local_only(self) -> None:
        source = sample_profile()
        original = copy.deepcopy(source)
        settings = copy.deepcopy(backend.DEFAULT_SETTINGS)
        settings.update(
            {
                "mode": "节能",
                "allow_domains": ["direct.example"],
                "proxy_domains": ["proxy.example"],
                "selector_overrides": {"🎯 基准 Smart": "🇯🇵 日本 Smart"},
            }
        )
        result = backend.apply_runtime_overrides(source, settings)
        self.assertEqual(source, original)
        tun = next(item for item in result["inbounds"] if item["type"] == "tun")
        mixed = next(item for item in result["inbounds"] if item["type"] == "mixed")
        self.assertEqual(tun["interface_name"], "SmartBox")
        self.assertEqual(tun["stack"], "gvisor")
        self.assertEqual(
            tun["iproute2_table_index"], backend.SMART_BOX_ROUTE_TABLE_INDEX
        )
        self.assertEqual(
            tun["iproute2_rule_index"], backend.SMART_BOX_ROUTE_RULE_INDEX
        )
        self.assertEqual(
            tun["auto_redirect_iproute2_fallback_rule_index"],
            backend.SMART_BOX_AUTO_REDIRECT_FALLBACK_RULE_INDEX,
        )
        self.assertEqual(
            tun["route_exclude_address"],
            list(backend.LINUX_TUN_ROUTE_EXCLUDE_ADDRESSES),
        )
        self.assertEqual(mixed["listen"], "127.0.0.1")
        self.assertEqual(mixed["listen_port"], 20808)
        self.assertFalse(mixed["set_system_proxy"])
        self.assertEqual(
            result["experimental"]["clash_api"]["external_controller"],
            "127.0.0.1:20809",
        )
        self.assertEqual(result["experimental"]["clash_api"]["default_mode"], "节能")
        self.assertEqual(result["log"]["level"], "info")
        bootstrap_servers = [
            server
            for server in result["dns"]["servers"]
            if server.get("tag") == backend.BOOTSTRAP_DNS
        ]
        self.assertEqual(
            bootstrap_servers,
            [
                {
                    "type": "https",
                    "tag": "bootstrap-dns",
                    "server": "223.5.5.5",
                    "server_port": 443,
                    "path": "/dns-query",
                    "detour": "DIRECT",
                    "tls": {
                        "enabled": True,
                        "server_name": "dns.alidns.com",
                    },
                }
            ],
        )
        self.assertEqual(
            result["route"]["default_domain_resolver"],
            {"server": "bootstrap-dns", "strategy": "ipv4_only"},
        )
        self.assertEqual(
            result["dns"]["optimistic"],
            {"enabled": True, "timeout": backend.BOOTSTRAP_OPTIMISTIC_TIMEOUT},
        )
        self.assertEqual(
            result["route"]["rules"][6]["domain_suffix"],
            list(backend.RELIABILITY_PROXY_DOMAINS),
        )
        baseline = next(
            item for item in result["outbounds"] if item.get("tag") == "🎯 基准 Smart"
        )
        self.assertEqual(baseline["default"], "🇯🇵 日本 Smart")

    def test_runtime_excludes_lan_link_local_and_multicast_routes(self) -> None:
        source = sample_profile()
        tun = next(item for item in source["inbounds"] if item["type"] == "tun")
        tun["route_exclude_address"] = [
            "203.0.113.0/24",
            "192.168.0.0/16",
        ]

        first = backend.apply_runtime_overrides(source, backend.DEFAULT_SETTINGS)
        second = backend.apply_runtime_overrides(first, backend.DEFAULT_SETTINGS)
        result_tun = next(item for item in second["inbounds"] if item["type"] == "tun")

        self.assertEqual(
            result_tun["route_exclude_address"],
            [
                "203.0.113.0/24",
                "192.168.0.0/16",
                "10.0.0.0/8",
                "172.16.0.0/12",
                "169.254.0.0/16",
                "224.0.0.0/4",
                "255.255.255.255/32",
                "fc00::/7",
                "fe80::/10",
                "ff00::/8",
            ],
        )
        self.assertEqual(source["inbounds"][0]["route_exclude_address"], [
            "203.0.113.0/24",
            "192.168.0.0/16",
        ])
        exclusions = set(result_tun["route_exclude_address"])
        self.assertTrue(
            {
                "10.0.0.0/8",
                "172.16.0.0/12",
                "192.168.0.0/16",
                "fc00::/7",
            }.issubset(exclusions)
        )
        self.assertTrue({"169.254.0.0/16", "fe80::/10"}.issubset(exclusions))
        self.assertTrue(
            {"224.0.0.0/4", "255.255.255.255/32", "ff00::/8"}.issubset(
                exclusions
            )
        )

    def test_runtime_does_not_preempt_subscription_routing_with_generic_ipv6_rule(
        self,
    ) -> None:
        result = backend.apply_runtime_overrides(sample_profile(), backend.DEFAULT_SETTINGS)

        self.assertFalse(
            any(
                rule.get("ip_version") == 6
                and rule.get("action") == "route"
                and rule.get("outbound") == backend.BASELINE_OUTBOUND
                for rule in result["route"]["rules"]
            )
        )

    def test_runtime_routes_local_multicast_direct_and_is_idempotent(self) -> None:
        first = backend.apply_runtime_overrides(
            copy.deepcopy(sample_profile()), backend.DEFAULT_SETTINGS
        )
        second = backend.apply_runtime_overrides(first, backend.DEFAULT_SETTINGS)
        expected = {
            "ip_cidr": list(backend.LINUX_LOCAL_MULTICAST_CIDRS),
            "action": "route",
            "outbound": backend.DIRECT_OUTBOUND,
        }
        first_rules = first["route"]["rules"]
        second_rules = second["route"]["rules"]
        self.assertEqual(first_rules.count(expected), 1)
        self.assertEqual(second_rules.count(expected), 1)
        self.assertEqual(first_rules[-1], expected)
        self.assertEqual(second_rules[-1], expected)

    def test_reliability_rules_precede_subscription_ads_and_cn_rules(self) -> None:
        source = sample_profile()
        source["route"]["rules"].extend(
            [
                {"rule_set": ["ads"], "action": "route", "outbound": "REJECT"},
                {"rule_set": ["cn"], "action": "route", "outbound": "DIRECT"},
            ]
        )
        source["dns"]["rules"].extend(
            [
                {"rule_set": ["ads"], "action": "route", "server": "local"},
                {"rule_set": ["cn"], "action": "route", "server": "local"},
            ]
        )
        result = backend.apply_runtime_overrides(source, backend.DEFAULT_SETTINGS)
        route_rules = result["route"]["rules"]
        dns_rules = result["dns"]["rules"]
        route_guard = next(
            index
            for index, rule in enumerate(route_rules)
            if rule.get("domain_suffix") == list(backend.RELIABILITY_PROXY_DOMAINS)
        )
        ads_route = next(
            index for index, rule in enumerate(route_rules) if rule.get("rule_set") == ["ads"]
        )
        dns_guard = next(
            index
            for index, rule in enumerate(dns_rules)
            if rule.get("domain_suffix") == list(backend.RELIABILITY_PROXY_DOMAINS)
        )
        route_guard_rule = route_rules[route_guard]
        dns_guard_rule = dns_rules[dns_guard]
        ads_dns = next(
            index for index, rule in enumerate(dns_rules) if rule.get("rule_set") == ["ads"]
        )
        cn_route = next(
            index for index, rule in enumerate(route_rules) if rule.get("rule_set") == ["cn"]
        )
        cn_dns = next(
            index for index, rule in enumerate(dns_rules) if rule.get("rule_set") == ["cn"]
        )
        self.assertEqual(route_guard_rule["outbound"], backend.BASELINE_OUTBOUND)
        self.assertEqual(dns_guard_rule["server"], backend.BASELINE_DNS)
        self.assertLess(route_guard, ads_route)
        self.assertLess(route_guard, cn_route)
        self.assertLess(dns_guard, ads_dns)
        self.assertLess(dns_guard, cn_dns)

    def test_reliability_rules_never_fall_back_to_direct_or_local(self) -> None:
        no_proxy = sample_profile()
        no_proxy["outbounds"] = [
            item
            for item in no_proxy["outbounds"]
            if item.get("type") not in ("selector", "smart")
        ]
        no_proxy["route"]["final"] = backend.DIRECT_OUTBOUND
        with self.assertRaisesRegex(backend.SmartBoxError, "Smart 出站"):
            backend.apply_reliability_proxy_rules(no_proxy)

        no_proxy_dns = sample_profile()
        no_proxy_dns["dns"]["servers"] = [{"type": "local", "tag": "local"}]
        no_proxy_dns["dns"]["final"] = backend.LOCAL_DNS
        with self.assertRaisesRegex(backend.SmartBoxError, "Smart DNS"):
            backend.apply_reliability_proxy_rules(no_proxy_dns)

    def test_linux_telegram_process_rules_are_idempotent(self) -> None:
        source = sample_profile()
        source["route"]["rules"].append(
            {
                "rule_set": [backend.TELEGRAM_IP_RULE_SET],
                "action": "route",
                "outbound": backend.TELEGRAM_OUTBOUND,
            }
        )
        source["outbounds"].append(
            {
                "type": "selector",
                "tag": backend.TELEGRAM_OUTBOUND,
                "outbounds": ["🎯 基准 Smart"],
                "default": "🎯 基准 Smart",
            }
        )
        source["dns"]["servers"].append(
            {"type": "https", "tag": backend.TELEGRAM_DNS, "server": "1.1.1.1"}
        )
        first = backend.apply_runtime_overrides(source, backend.DEFAULT_SETTINGS)
        second = backend.apply_runtime_overrides(first, backend.DEFAULT_SETTINGS)

        for result in (first, second):
            route_rules = result["route"]["rules"]
            dns_rules = result["dns"]["rules"]
            process_route = {
                "process_name": list(backend.LINUX_TELEGRAM_PROCESSES),
                "action": "route",
                "outbound": backend.TELEGRAM_OUTBOUND,
            }
            process_dns = {
                "process_name": list(backend.LINUX_TELEGRAM_PROCESSES),
                "action": "route",
                "server": backend.TELEGRAM_DNS,
            }
            telegram_ip_route = {
                "rule_set": [backend.TELEGRAM_IP_RULE_SET],
                "action": "route",
                "outbound": backend.TELEGRAM_OUTBOUND,
            }
            self.assertEqual(route_rules.count(process_route), 1)
            self.assertEqual(dns_rules.count(process_dns), 1)
            self.assertEqual(route_rules.count(telegram_ip_route), 1)
            private_index = next(
                index
                for index, rule in enumerate(route_rules)
                if rule.get("ip_is_private") is True
            )
            self.assertLess(route_rules.index(telegram_ip_route), private_index)
            self.assertGreater(route_rules.index(process_route), private_index)
        self.assertEqual(first, second)

    def test_runtime_replaces_existing_bootstrap_dns_without_duplicates(self) -> None:
        source = sample_profile()
        source["dns"]["servers"].extend(
            [
                {"type": "local", "tag": "bootstrap-dns"},
                {"type": "udp", "tag": "bootstrap-dns", "server": "8.8.8.8"},
            ]
        )
        first = backend.apply_runtime_overrides(source, backend.DEFAULT_SETTINGS)
        second = backend.apply_runtime_overrides(first, backend.DEFAULT_SETTINGS)
        for result in (first, second):
            bootstrap_servers = [
                server
                for server in result["dns"]["servers"]
                if server.get("tag") == backend.BOOTSTRAP_DNS
            ]
            self.assertEqual(len(bootstrap_servers), 1)
            self.assertEqual(bootstrap_servers[0]["detour"], "DIRECT")
        self.assertEqual(first, second)

    def test_runtime_removes_informational_provider_nodes(self) -> None:
        source = sample_profile()
        status_tag = "Provider 距离下次重置剩余：3 天"
        source["outbounds"].insert(
            2,
            {
                "type": "vless",
                "tag": status_tag,
                "server": "status.example",
                "server_port": 443,
            },
        )
        global_smart = next(
            item for item in source["outbounds"] if item.get("tag") == "🚀 全局 Smart"
        )
        global_smart["outbounds"].insert(0, status_tag)
        result = backend.apply_runtime_overrides(source, backend.DEFAULT_SETTINGS)
        tags = {item.get("tag") for item in result["outbounds"]}
        self.assertNotIn(status_tag, tags)
        filtered_global = next(
            item for item in result["outbounds"] if item.get("tag") == "🚀 全局 Smart"
        )
        self.assertNotIn(status_tag, filtered_global["outbounds"])

    def test_tun_dns_gateways_follow_configured_networks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "runtime.json"
            profile = sample_profile()
            tun = next(item for item in profile["inbounds"] if item["type"] == "tun")
            tun["address"] = ["172.19.0.1/30", "fdfe:dcba:9876::1/126"]
            path.write_text(json.dumps(profile), encoding="utf-8")
            self.assertEqual(
                backend.tun_dns_addresses(path),
                ["172.19.0.2", "fdfe:dcba:9876::2"],
            )

    def test_prepare_is_atomic_and_validates_before_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            profile_path.write_text(json.dumps(sample_profile()), encoding="utf-8")
            runtime_path.write_text('{"old":true}\n', encoding="utf-8")
            settings = copy.deepcopy(backend.DEFAULT_SETTINGS)
            settings["mode"] = "Global"
            with mock.patch.object(backend, "validate_config", return_value="") as validate:
                result = backend.prepare_runtime(
                    profile_path=profile_path,
                    runtime_path=runtime_path,
                    settings=settings,
                )
            self.assertEqual(result, runtime_path)
            validate.assert_called_once()
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            self.assertEqual(
                runtime["experimental"]["clash_api"]["default_mode"], "Global"
            )

    def test_prepare_revalidates_after_concurrent_runtime_setting_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            profile_path.write_text(json.dumps(sample_profile()), encoding="utf-8")

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                initial = backend.mutate_settings(
                    lambda settings: settings.__setitem__("tun_stack", "gvisor")
                )
                validations: list[Path] = []

                def validate(candidate: Path) -> str:
                    validations.append(candidate)
                    if len(validations) == 1:
                        backend.mutate_settings(
                            lambda settings: settings.__setitem__(
                                "tun_stack", "system"
                            )
                        )
                    return ""

                with mock.patch.object(
                    backend, "validate_config", side_effect=validate
                ):
                    backend.prepare_runtime(
                        profile_path=profile_path,
                        runtime_path=runtime_path,
                        settings=initial,
                    )

            self.assertEqual(len(validations), 2)
            self.assertEqual(len(set(validations)), 2)
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            tun = next(
                inbound
                for inbound in runtime["inbounds"]
                if inbound.get("type") == "tun"
            )
            self.assertEqual(tun["stack"], "system")
            self.assertFalse(
                any(".prepare." in path.name for path in root.iterdir())
            )

    def test_prepare_rebuilds_when_profile_changes_during_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            original_profile = sample_profile()
            newer_profile = sample_profile()
            newer_node = next(
                item
                for item in newer_profile["outbounds"]
                if item.get("tag") == "jp-node"
            )
            newer_node["server"] = "203.0.113.77"
            profile_path.write_text(json.dumps(original_profile), encoding="utf-8")

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                validations = 0

                def validate(_candidate: Path) -> str:
                    nonlocal validations
                    validations += 1
                    if validations == 1:
                        backend.atomic_write_json(profile_path, newer_profile)
                    return ""

                with mock.patch.object(
                    backend, "validate_config", side_effect=validate
                ):
                    backend.prepare_runtime(
                        profile_path=profile_path,
                        runtime_path=runtime_path,
                    )

            self.assertEqual(validations, 2)
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            node = next(
                item
                for item in runtime["outbounds"]
                if item.get("tag") == "jp-node"
            )
            self.assertEqual(node["server"], "203.0.113.77")

    def test_fetch_rebuilds_runtime_after_concurrent_setting_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            response = mock.MagicMock()
            response.status = 200
            response.read.return_value = json.dumps(sample_profile()).encode()
            response.__enter__.return_value = response
            response.__exit__.return_value = False

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                backend.mutate_settings(
                    lambda settings: settings.__setitem__("tun_stack", "gvisor")
                )
                runtime_validations: list[Path] = []

                def validate(candidate: Path) -> str:
                    if candidate.name.startswith(".runtime.json.fetch."):
                        runtime_validations.append(candidate)
                        if len(runtime_validations) == 1:
                            backend.mutate_settings(
                                lambda settings: settings.__setitem__(
                                    "tun_stack", "system"
                                )
                            )
                    return ""

                with mock.patch.object(
                    backend.urllib.request, "urlopen", return_value=response
                ), mock.patch.object(
                    backend, "validate_config", side_effect=validate
                ):
                    summary = backend.fetch_profile("https://example.test/private")

                settings = backend.load_settings()

            self.assertGreater(summary["nodes"], 0)
            self.assertEqual(len(runtime_validations), 2)
            self.assertEqual(len(set(runtime_validations)), 2)
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            tun = next(
                inbound
                for inbound in runtime["inbounds"]
                if inbound.get("type") == "tun"
            )
            self.assertEqual(tun["stack"], "system")
            self.assertEqual(settings["tun_stack"], "system")
            self.assertEqual(
                settings["subscription_url"], "https://example.test/private"
            )
            self.assertIsInstance(settings["last_pull_utc"], str)
            self.assertFalse(any(".fetch." in path.name for path in root.iterdir()))

    def test_fetch_preserves_unrelated_concurrent_setting_without_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            response = mock.MagicMock()
            response.status = 200
            response.read.return_value = json.dumps(sample_profile()).encode()
            response.__enter__.return_value = response
            response.__exit__.return_value = False

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                runtime_validations = 0

                def validate(candidate: Path) -> str:
                    nonlocal runtime_validations
                    if candidate.name.startswith(".runtime.json.fetch."):
                        runtime_validations += 1
                        backend.mutate_settings(
                            lambda settings: settings.__setitem__("theme", "dark")
                        )
                    return ""

                with mock.patch.object(
                    backend.urllib.request, "urlopen", return_value=response
                ), mock.patch.object(
                    backend, "validate_config", side_effect=validate
                ):
                    backend.fetch_profile("https://example.test/private")
                settings = backend.load_settings()

            self.assertEqual(runtime_validations, 1)
            self.assertEqual(settings["theme"], "dark")

    def test_fetch_aborts_cleanly_when_runtime_settings_never_stabilize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            old_profile = b'{"profile":"old"}\n'
            old_runtime = b'{"runtime":"old"}\n'
            profile_path.write_bytes(old_profile)
            runtime_path.write_bytes(old_runtime)
            response = mock.MagicMock()
            response.status = 200
            response.read.return_value = json.dumps(sample_profile()).encode()
            response.__enter__.return_value = response
            response.__exit__.return_value = False

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                def initialize(settings: dict) -> None:
                    settings["subscription_url"] = "https://old.example/private"
                    settings["last_pull_utc"] = "2026-08-20T00:00:00+00:00"

                backend.mutate_settings(initialize)
                runtime_validations = 0

                def validate(candidate: Path) -> str:
                    nonlocal runtime_validations
                    if candidate.name.startswith(".runtime.json.fetch."):
                        runtime_validations += 1

                        def toggle(settings: dict) -> None:
                            settings["tun_stack"] = (
                                "system"
                                if settings.get("tun_stack") != "system"
                                else "gvisor"
                            )

                        backend.mutate_settings(toggle)
                    return ""

                with mock.patch.object(
                    backend.urllib.request, "urlopen", return_value=response
                ), mock.patch.object(
                    backend, "validate_config", side_effect=validate
                ):
                    with self.assertRaisesRegex(
                        backend.SmartBoxError, "本地设置持续变化"
                    ):
                        backend.fetch_profile("https://new.example/private")
                settings = backend.load_settings()

            self.assertEqual(
                runtime_validations, backend.CONFIG_SNAPSHOT_MAX_RETRIES
            )
            self.assertEqual(profile_path.read_bytes(), old_profile)
            self.assertEqual(runtime_path.read_bytes(), old_runtime)
            self.assertEqual(
                settings["subscription_url"], "https://old.example/private"
            )
            self.assertEqual(
                settings["last_pull_utc"], "2026-08-20T00:00:00+00:00"
            )

    def test_two_concurrent_fetches_publish_one_complete_matching_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            profiles: dict[str, dict] = {}
            expected_servers: dict[str, str] = {}
            for label, server in (("first", "192.0.2.11"), ("second", "192.0.2.22")):
                url = f"https://{label}.example/private"
                profile = sample_profile()
                node = next(
                    item
                    for item in profile["outbounds"]
                    if item.get("tag") == "jp-node"
                )
                node["server"] = server
                profiles[url] = profile
                expected_servers[url] = server

            def urlopen(request: object, **_kwargs: object) -> mock.MagicMock:
                url = str(getattr(request, "full_url"))
                response = mock.MagicMock()
                response.status = 200
                response.read.return_value = json.dumps(profiles[url]).encode()
                response.__enter__.return_value = response
                response.__exit__.return_value = False
                return response

            runtime_barrier = threading.Barrier(2)

            def validate(candidate: Path) -> str:
                if candidate.name.startswith(".runtime.json.fetch."):
                    runtime_barrier.wait(timeout=5)
                return ""

            errors: list[Exception] = []

            def fetch(url: str) -> None:
                try:
                    backend.fetch_profile(url)
                except Exception as error:  # noqa: BLE001 - reported after join
                    errors.append(error)

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path), mock.patch.object(
                backend.urllib.request, "urlopen", side_effect=urlopen
            ), mock.patch.object(backend, "validate_config", side_effect=validate):
                threads = [
                    threading.Thread(target=fetch, args=(url,))
                    for url in profiles
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)
                self.assertTrue(all(not thread.is_alive() for thread in threads))
                settings = backend.load_settings()

            self.assertEqual(errors, [])
            final_url = settings["subscription_url"]
            expected_server = expected_servers[final_url]
            stored_profile = json.loads(profile_path.read_text(encoding="utf-8"))
            stored_runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            for document in (stored_profile, stored_runtime):
                node = next(
                    item
                    for item in document["outbounds"]
                    if item.get("tag") == "jp-node"
                )
                self.assertEqual(node["server"], expected_server)
            self.assertFalse(any(".fetch." in path.name for path in root.iterdir()))

    def test_fetch_commit_failure_restores_all_previous_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            old_profile = b'{"profile":"old"}\n'
            old_runtime = b'{"runtime":"old"}\n'
            profile_path.write_bytes(old_profile)
            runtime_path.write_bytes(old_runtime)
            response = mock.MagicMock()
            response.status = 200
            response.read.return_value = json.dumps(sample_profile()).encode()
            response.__enter__.return_value = response
            response.__exit__.return_value = False

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                backend.mutate_settings(
                    lambda settings: settings.__setitem__("theme", "dark")
                )
                old_settings = settings_path.read_bytes()
                with mock.patch.object(
                    backend.urllib.request, "urlopen", return_value=response
                ), mock.patch.object(
                    backend, "validate_config", return_value=""
                ), mock.patch.object(
                    backend,
                    "_save_settings_unlocked",
                    side_effect=OSError("injected settings commit failure"),
                ):
                    with self.assertRaisesRegex(
                        backend.SmartBoxError,
                        "injected settings commit failure.*旧配置已恢复",
                    ):
                        backend.fetch_profile("https://example.test/private")

            self.assertEqual(profile_path.read_bytes(), old_profile)
            self.assertEqual(runtime_path.read_bytes(), old_runtime)
            self.assertEqual(settings_path.read_bytes(), old_settings)
            self.assertFalse(any(".fetch." in path.name for path in root.iterdir()))

    def test_profile_receipt_rolls_back_owned_bundle_and_preserves_theme(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            old_profile = b'{"profile":"old"}\n'
            old_runtime = b'{"runtime":"old"}\n'
            profile_path.write_bytes(old_profile)
            runtime_path.write_bytes(old_runtime)
            response = mock.MagicMock()
            response.status = 200
            response.read.return_value = json.dumps(sample_profile()).encode()
            response.__enter__.return_value = response
            response.__exit__.return_value = False

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                def initialize(settings: dict) -> None:
                    settings["subscription_url"] = "https://old.example/private"
                    settings["last_pull_utc"] = "2026-08-20T00:00:00+00:00"

                backend.mutate_settings(initialize)
                with mock.patch.object(
                    backend.urllib.request, "urlopen", return_value=response
                ), mock.patch.object(backend, "validate_config", return_value=""):
                    result = backend.fetch_profile(
                        "https://new.example/private"
                    )
                self.assertIsInstance(result, backend.ProfileUpdateResult)
                self.assertNotIn("rollback_receipt", json.dumps(result))
                backend.mutate_settings(
                    lambda settings: settings.__setitem__("theme", "dark")
                )
                restored = backend.rollback_profile_update(
                    result.rollback_receipt
                )
                settings = backend.load_settings()

            self.assertTrue(restored)
            self.assertEqual(profile_path.read_bytes(), old_profile)
            self.assertEqual(runtime_path.read_bytes(), old_runtime)
            self.assertEqual(
                settings["subscription_url"], "https://old.example/private"
            )
            self.assertEqual(
                settings["last_pull_utc"], "2026-08-20T00:00:00+00:00"
            )
            self.assertEqual(settings["theme"], "dark")

    def test_stale_profile_receipt_never_overwrites_newer_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            first_profile = sample_profile()
            second_profile = sample_profile()
            second_node = next(
                item
                for item in second_profile["outbounds"]
                if item.get("tag") == "jp-node"
            )
            second_node["server"] = "198.51.100.42"

            def response_for(profile: dict) -> mock.MagicMock:
                response = mock.MagicMock()
                response.status = 200
                response.read.return_value = json.dumps(profile).encode()
                response.__enter__.return_value = response
                response.__exit__.return_value = False
                return response

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path), mock.patch.object(
                backend.urllib.request,
                "urlopen",
                side_effect=[response_for(first_profile), response_for(second_profile)],
            ), mock.patch.object(backend, "validate_config", return_value=""):
                first = backend.fetch_profile("https://first.example/private")
                second = backend.fetch_profile("https://second.example/private")
                second_profile_bytes = profile_path.read_bytes()
                second_runtime_bytes = runtime_path.read_bytes()
                self.assertFalse(
                    backend.rollback_profile_update(first.rollback_receipt)
                )
                settings = backend.load_settings()

            self.assertIsInstance(second, backend.ProfileUpdateResult)
            self.assertEqual(profile_path.read_bytes(), second_profile_bytes)
            self.assertEqual(runtime_path.read_bytes(), second_runtime_bytes)
            self.assertEqual(
                settings["subscription_url"], "https://second.example/private"
            )
            stored_profile = json.loads(second_profile_bytes)
            stored_node = next(
                item
                for item in stored_profile["outbounds"]
                if item.get("tag") == "jp-node"
            )
            self.assertEqual(stored_node["server"], "198.51.100.42")

    def test_invalid_overlap_never_writes_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            profile_path.write_text(json.dumps(sample_profile()), encoding="utf-8")
            settings = copy.deepcopy(backend.DEFAULT_SETTINGS)
            settings["allow_domains"] = ["example.com"]
            settings["proxy_domains"] = ["api.example.com"]
            with self.assertRaises(backend.SmartBoxError):
                backend.prepare_runtime(
                    profile_path=profile_path,
                    runtime_path=runtime_path,
                    settings=settings,
                    check=False,
                )
            self.assertFalse(runtime_path.exists())


class ProfileTransactionRecoveryTest(unittest.TestCase):
    class SimulatedProcessDeath(BaseException):
        pass

    @staticmethod
    def response(profile: dict | None = None) -> mock.MagicMock:
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(
            profile or sample_profile()
        ).encode()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        return response

    @staticmethod
    def initialize_settings(settings: dict) -> None:
        settings["subscription_url"] = "https://old.example/private"
        settings["last_pull_utc"] = "2026-08-20T00:00:00+00:00"
        settings["theme"] = "dark"

    def test_prepared_crash_points_restore_exact_old_bundle(self) -> None:
        for crash_point in (
            "prepared",
            "profile-replaced",
            "runtime-replaced",
            "settings-replaced",
        ):
            with self.subTest(crash_point=crash_point), tempfile.TemporaryDirectory() as temporary_dir:
                root = Path(temporary_dir)
                profile_path = root / "profile.json"
                runtime_path = root / "runtime.json"
                settings_path = root / "settings.json"
                old_profile = b'{"profile":"old"}\n'
                old_runtime = b'{"runtime":"old"}\n'
                profile_path.write_bytes(old_profile)
                runtime_path.write_bytes(old_runtime)

                with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                    backend, "STATE_DIR", root / "state"
                ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                    backend, "RUNTIME_PATH", runtime_path
                ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                    backend.mutate_settings(self.initialize_settings)
                    old_settings = settings_path.read_bytes()

                    def checkpoint(stage: str) -> None:
                        if stage == crash_point:
                            raise self.SimulatedProcessDeath(stage)

                    with mock.patch.object(
                        backend.urllib.request,
                        "urlopen",
                        return_value=self.response(),
                    ), mock.patch.object(
                        backend, "validate_config", return_value=""
                    ), mock.patch.object(
                        backend,
                        "_profile_transaction_checkpoint",
                        side_effect=checkpoint,
                    ):
                        with self.assertRaises(self.SimulatedProcessDeath):
                            backend.fetch_profile(
                                "https://new.example/private"
                            )

                    self.assertTrue(
                        backend._profile_transaction_journal_path().is_file()
                    )
                    self.assertEqual(
                        backend.recover_profile_transaction(), "rolled-back"
                    )
                    restored_settings = backend.load_settings()
                    self.assertFalse(
                        backend._profile_transaction_journal_path().exists()
                    )
                    self.assertTrue(
                        all(
                            not path.exists()
                            for path in backend._profile_transaction_backup_paths().values()
                        )
                    )

                self.assertEqual(profile_path.read_bytes(), old_profile)
                self.assertEqual(runtime_path.read_bytes(), old_runtime)
                self.assertEqual(settings_path.read_bytes(), old_settings)
                self.assertEqual(
                    restored_settings["subscription_url"],
                    "https://old.example/private",
                )
                self.assertEqual(restored_settings["theme"], "dark")

    def test_sigkill_after_runtime_replace_is_recovered_by_next_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            incoming_path = root / "incoming.json"
            old_profile = b'{"profile":"old"}\n'
            old_runtime = b'{"runtime":"old"}\n'
            profile_path.write_bytes(old_profile)
            runtime_path.write_bytes(old_runtime)
            incoming_path.write_text(
                json.dumps(sample_profile()), encoding="utf-8"
            )

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                backend.mutate_settings(self.initialize_settings)
                old_settings = settings_path.read_bytes()
                child_code = """
import os
import signal
import sys
from pathlib import Path
from unittest import mock

import smart_box_backend as backend

root = Path(sys.argv[1])
backend.CONFIG_DIR = root
backend.STATE_DIR = root / "state"
backend.PROFILE_PATH = root / "profile.json"
backend.RUNTIME_PATH = root / "runtime.json"
backend.SETTINGS_PATH = root / "settings.json"

response = mock.MagicMock()
response.status = 200
response.read.return_value = (root / "incoming.json").read_bytes()
response.__enter__.return_value = response
response.__exit__.return_value = False

def checkpoint(stage):
    if stage == "runtime-replaced":
        os.kill(os.getpid(), signal.SIGKILL)

with mock.patch.object(backend.urllib.request, "urlopen", return_value=response), \
     mock.patch.object(backend, "validate_config", return_value=""), \
     mock.patch.object(backend, "_profile_transaction_checkpoint", side_effect=checkpoint):
    backend.fetch_profile("https://new.example/private")
"""
                environment = dict(os.environ)
                environment["PYTHONPATH"] = str(
                    Path(backend.__file__).resolve().parent
                )
                child = subprocess.run(
                    [sys.executable, "-c", child_code, str(root)],
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(child.returncode, -signal.SIGKILL, child.stdout)
                self.assertTrue(
                    backend._profile_transaction_journal_path().is_file()
                )
                self.assertEqual(
                    backend.recover_profile_transaction(), "rolled-back"
                )

            self.assertEqual(profile_path.read_bytes(), old_profile)
            self.assertEqual(runtime_path.read_bytes(), old_runtime)
            self.assertEqual(settings_path.read_bytes(), old_settings)

    def test_committed_crash_point_keeps_new_bundle_and_only_finalizes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            profile_path.write_bytes(b'{"profile":"old"}\n')
            runtime_path.write_bytes(b'{"runtime":"old"}\n')

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                backend.mutate_settings(self.initialize_settings)

                def checkpoint(stage: str) -> None:
                    if stage == "committed":
                        raise self.SimulatedProcessDeath(stage)

                with mock.patch.object(
                    backend.urllib.request,
                    "urlopen",
                    return_value=self.response(),
                ), mock.patch.object(
                    backend, "validate_config", return_value=""
                ), mock.patch.object(
                    backend,
                    "_profile_transaction_checkpoint",
                    side_effect=checkpoint,
                ):
                    with self.assertRaises(self.SimulatedProcessDeath):
                        backend.fetch_profile("https://new.example/private")

                new_profile = profile_path.read_bytes()
                new_runtime = runtime_path.read_bytes()
                new_settings = settings_path.read_bytes()
                self.assertEqual(
                    backend.recover_profile_transaction(), "committed"
                )
                self.assertEqual(profile_path.read_bytes(), new_profile)
                self.assertEqual(runtime_path.read_bytes(), new_runtime)
                self.assertEqual(settings_path.read_bytes(), new_settings)
                settings = backend.load_settings()
                self.assertEqual(
                    settings["subscription_url"],
                    "https://new.example/private",
                )
                self.assertFalse(
                    backend._profile_transaction_journal_path().exists()
                )
                self.assertTrue(
                    all(
                        not path.exists()
                        for path in backend._profile_transaction_backup_paths().values()
                    )
                )

    def test_load_settings_recovers_prepared_transaction_before_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            profile_path.write_bytes(b'{"profile":"old"}\n')
            runtime_path.write_bytes(b'{"runtime":"old"}\n')

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                backend.mutate_settings(self.initialize_settings)

                def checkpoint(stage: str) -> None:
                    if stage == "settings-replaced":
                        raise self.SimulatedProcessDeath(stage)

                with mock.patch.object(
                    backend.urllib.request,
                    "urlopen",
                    return_value=self.response(),
                ), mock.patch.object(
                    backend, "validate_config", return_value=""
                ), mock.patch.object(
                    backend,
                    "_profile_transaction_checkpoint",
                    side_effect=checkpoint,
                ):
                    with self.assertRaises(self.SimulatedProcessDeath):
                        backend.fetch_profile("https://new.example/private")

                settings = backend.load_settings()
                self.assertEqual(
                    settings["subscription_url"],
                    "https://old.example/private",
                )
                self.assertFalse(
                    backend._profile_transaction_journal_path().exists()
                )

    def test_load_settings_waits_for_inflight_bundle_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            profile_path.write_bytes(b'{"profile":"old"}\n')
            runtime_path.write_bytes(b'{"runtime":"old"}\n')
            transaction_paused = threading.Event()
            release_transaction = threading.Event()
            reader_finished = threading.Event()
            fetched: list[object] = []
            observed: list[dict] = []
            errors: list[BaseException] = []

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                backend.mutate_settings(self.initialize_settings)

                def checkpoint(stage: str) -> None:
                    if stage == "settings-replaced":
                        transaction_paused.set()
                        if not release_transaction.wait(timeout=5):
                            raise AssertionError("timed out waiting to finish transaction")

                def fetch() -> None:
                    try:
                        fetched.append(
                            backend.fetch_profile("https://new.example/private")
                        )
                    except BaseException as error:  # noqa: BLE001 - asserted below
                        errors.append(error)

                def read() -> None:
                    try:
                        observed.append(backend.load_settings())
                    except BaseException as error:  # noqa: BLE001 - asserted below
                        errors.append(error)
                    finally:
                        reader_finished.set()

                with mock.patch.object(
                    backend.urllib.request,
                    "urlopen",
                    return_value=self.response(),
                ), mock.patch.object(
                    backend, "validate_config", return_value=""
                ), mock.patch.object(
                    backend,
                    "_profile_transaction_checkpoint",
                    side_effect=checkpoint,
                ):
                    fetch_thread = threading.Thread(target=fetch)
                    fetch_thread.start()
                    self.assertTrue(transaction_paused.wait(timeout=5))
                    reader_thread = threading.Thread(target=read)
                    reader_thread.start()
                    self.assertFalse(reader_finished.wait(timeout=0.05))
                    release_transaction.set()
                    fetch_thread.join(timeout=5)
                    reader_thread.join(timeout=5)

                self.assertFalse(fetch_thread.is_alive())
                self.assertFalse(reader_thread.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(len(fetched), 1)
                self.assertEqual(
                    observed[0]["subscription_url"],
                    "https://new.example/private",
                )

    def test_interrupted_receipt_rollback_aborts_to_owned_new_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            old_profile = b'{"profile":"old"}\n'
            old_runtime = b'{"runtime":"old"}\n'
            profile_path.write_bytes(old_profile)
            runtime_path.write_bytes(old_runtime)

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path), mock.patch.object(
                backend.urllib.request,
                "urlopen",
                return_value=self.response(),
            ), mock.patch.object(backend, "validate_config", return_value=""):
                backend.mutate_settings(self.initialize_settings)
                result = backend.fetch_profile(
                    "https://new.example/private"
                )
                new_profile = profile_path.read_bytes()
                new_runtime = runtime_path.read_bytes()
                new_settings = settings_path.read_bytes()

                def checkpoint(stage: str) -> None:
                    if stage == "runtime-replaced":
                        raise self.SimulatedProcessDeath(stage)

                with mock.patch.object(
                    backend,
                    "_profile_transaction_checkpoint",
                    side_effect=checkpoint,
                ):
                    with self.assertRaises(self.SimulatedProcessDeath):
                        backend.rollback_profile_update(
                            result.rollback_receipt
                        )

                self.assertEqual(
                    backend.recover_profile_transaction(), "rolled-back"
                )
                self.assertEqual(profile_path.read_bytes(), new_profile)
                self.assertEqual(runtime_path.read_bytes(), new_runtime)
                self.assertEqual(settings_path.read_bytes(), new_settings)
                self.assertTrue(
                    backend.rollback_profile_update(result.rollback_receipt)
                )

            self.assertEqual(profile_path.read_bytes(), old_profile)
            self.assertEqual(runtime_path.read_bytes(), old_runtime)

    def test_corrupt_journal_and_backup_fail_closed_without_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            profile_path.write_bytes(b'{"profile":"old"}\n')
            runtime_path.write_bytes(b'{"runtime":"old"}\n')

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                backend.mutate_settings(self.initialize_settings)
                journal_path = backend._profile_transaction_journal_path()
                journal_path.write_bytes(b"not-json\n")
                with self.assertRaisesRegex(
                    backend.SmartBoxError, "读取配置事务 journal 失败"
                ):
                    backend.load_settings()
                with self.assertRaisesRegex(
                    backend.SmartBoxError, "读取配置事务 journal 失败"
                ):
                    backend.mutate_settings(
                        lambda settings: settings.__setitem__("theme", "light")
                    )
                self.assertEqual(journal_path.read_bytes(), b"not-json\n")

            with tempfile.TemporaryDirectory() as second_temporary_dir:
                second_root = Path(second_temporary_dir)
                profile_path = second_root / "profile.json"
                runtime_path = second_root / "runtime.json"
                settings_path = second_root / "settings.json"
                profile_path.write_bytes(b'{"profile":"old"}\n')
                runtime_path.write_bytes(b'{"runtime":"old"}\n')
                with mock.patch.object(backend, "CONFIG_DIR", second_root), mock.patch.object(
                    backend, "STATE_DIR", second_root / "state"
                ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                    backend, "RUNTIME_PATH", runtime_path
                ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                    backend.mutate_settings(self.initialize_settings)

                    def checkpoint(stage: str) -> None:
                        if stage == "profile-replaced":
                            raise self.SimulatedProcessDeath(stage)

                    with mock.patch.object(
                        backend.urllib.request,
                        "urlopen",
                        return_value=self.response(),
                    ), mock.patch.object(
                        backend, "validate_config", return_value=""
                    ), mock.patch.object(
                        backend,
                        "_profile_transaction_checkpoint",
                        side_effect=checkpoint,
                    ):
                        with self.assertRaises(self.SimulatedProcessDeath):
                            backend.fetch_profile(
                                "https://new.example/private"
                            )

                    partial_profile = profile_path.read_bytes()
                    backend._profile_transaction_backup_paths()[
                        "profile"
                    ].write_bytes(b"corrupt")
                    with self.assertRaisesRegex(
                        backend.SmartBoxError, "profile 备份校验失败"
                    ):
                        backend.recover_profile_transaction()
                    self.assertEqual(profile_path.read_bytes(), partial_profile)
                    self.assertTrue(
                        backend._profile_transaction_journal_path().exists()
                    )

    def test_committed_marker_with_tampered_target_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            profile_path = root / "profile.json"
            runtime_path = root / "runtime.json"
            settings_path = root / "settings.json"
            profile_path.write_bytes(b'{"profile":"old"}\n')
            runtime_path.write_bytes(b'{"runtime":"old"}\n')

            with mock.patch.object(backend, "CONFIG_DIR", root), mock.patch.object(
                backend, "STATE_DIR", root / "state"
            ), mock.patch.object(backend, "PROFILE_PATH", profile_path), mock.patch.object(
                backend, "RUNTIME_PATH", runtime_path
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                backend.mutate_settings(self.initialize_settings)

                def checkpoint(stage: str) -> None:
                    if stage == "committed":
                        raise self.SimulatedProcessDeath(stage)

                with mock.patch.object(
                    backend.urllib.request,
                    "urlopen",
                    return_value=self.response(),
                ), mock.patch.object(
                    backend, "validate_config", return_value=""
                ), mock.patch.object(
                    backend,
                    "_profile_transaction_checkpoint",
                    side_effect=checkpoint,
                ):
                    with self.assertRaises(self.SimulatedProcessDeath):
                        backend.fetch_profile("https://new.example/private")

                runtime_path.write_bytes(b'{"runtime":"tampered"}\n')
                with self.assertRaisesRegex(
                    backend.SmartBoxError,
                    "已提交配置事务的目标文件校验失败：runtime",
                ):
                    backend.recover_profile_transaction()
                self.assertTrue(
                    backend._profile_transaction_journal_path().exists()
                )


class ConnectivityProbeTest(unittest.TestCase):
    def test_reports_http_success(self) -> None:
        response = mock.MagicMock()
        response.status = 204
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with mock.patch.object(backend.urllib.request, "urlopen", return_value=response):
            result = backend.probe_connectivity(timeout=1.0)
        self.assertTrue(result["online"])
        self.assertEqual(result["http_status"], 204)
        self.assertEqual(result["error"], "")

    def test_reports_network_failure_without_raising(self) -> None:
        with mock.patch.object(
            backend.urllib.request,
            "urlopen",
            side_effect=backend.urllib.error.URLError("offline"),
        ):
            result = backend.probe_connectivity(timeout=1.0)
        self.assertFalse(result["online"])
        self.assertEqual(result["http_status"], 0)
        self.assertIn("offline", result["error"])

    def test_treats_reachable_http_client_error_as_online(self) -> None:
        error = backend.urllib.error.HTTPError(
            "https://example.invalid", 403, "forbidden", {}, None
        )
        with mock.patch.object(backend.urllib.request, "urlopen", side_effect=error):
            result = backend.probe_connectivity(timeout=1.0)
        self.assertTrue(result["online"])
        self.assertEqual(result["http_status"], 403)

    def test_connectivity_is_usable_requires_forced_smart_and_remote_quorum(self) -> None:
        checks = [
            {"key": "domestic", "online": True},
            {"key": "basic", "online": True},
            {"key": "proxy", "online": False},
            {"key": "github", "online": True},
            {"key": "telegram", "online": False},
        ]
        self.assertFalse(backend.connectivity_is_usable({"checks": checks}))
        checks[2]["online"] = True
        self.assertTrue(backend.connectivity_is_usable({"checks": checks}))
        checks[1]["online"] = False
        self.assertTrue(backend.connectivity_is_usable({"checks": checks}))
        checks[3]["online"] = False
        self.assertFalse(backend.connectivity_is_usable({"checks": checks}))

    def test_energy_mode_uses_forced_smart_probe_before_direct_catch_all(self) -> None:
        checks = [
            {"key": "domestic", "online": True},
            {"key": "basic", "online": False},
            {"key": "proxy", "online": True},
            {"key": "github", "online": False},
            {"key": "telegram", "online": False},
        ]
        self.assertTrue(
            backend.connectivity_is_usable_for_mode({"checks": checks}, "节能")
        )
        checks[2]["online"] = False
        self.assertFalse(
            backend.connectivity_is_usable_for_mode({"checks": checks}, "节能")
        )

    def test_energy_saving_mode_requires_domestic_and_forced_proxy(self) -> None:
        checks = [
            {"key": "domestic", "online": True},
            {"key": "basic", "online": False},
            {"key": "proxy", "online": True},
            {"key": "github", "online": False},
            {"key": "telegram", "online": False},
        ]
        result = {"online": False, "passed": 2, "total": 5, "checks": checks}

        # 节能模式刻意让普通境外流量直连；强制 Smart 的 proxy 探针仍须可用。
        self.assertTrue(backend.connectivity_is_usable_for_mode(result, "节能"))
        checks[2]["online"] = False
        self.assertFalse(backend.connectivity_is_usable_for_mode(result, "节能"))
        checks[2]["online"] = True
        checks[0]["online"] = False
        self.assertFalse(backend.connectivity_is_usable_for_mode(result, "节能"))

    def test_watchdog_accepts_two_independent_remote_paths_without_forced_proxy(self) -> None:
        checks = [
            {"key": "domestic", "online": True},
            {"key": "basic", "online": True},
            {"key": "proxy", "online": False},
            {"key": "github", "online": True},
            {"key": "telegram", "online": False},
        ]
        self.assertTrue(
            backend.connectivity_is_usable_for_watchdog({"checks": checks}, "Rule")
        )
        checks[1]["online"] = False
        self.assertFalse(
            backend.connectivity_is_usable_for_watchdog({"checks": checks}, "Rule")
        )

    def test_matrix_requires_every_named_path(self) -> None:
        def fake_probe(url: str, timeout: float) -> dict:
            online = "telegram.org" not in url
            return {
                "online": online,
                "url": url,
                "http_status": 200 if online else 0,
                "latency_ms": 10,
                "error": "timeout" if not online else "",
            }

        with mock.patch.object(backend, "probe_connectivity", side_effect=fake_probe):
            result = backend.probe_connectivity_matrix(timeout=1.0)
        self.assertFalse(result["online"])
        self.assertEqual(result["passed"], 4)
        self.assertEqual(result["total"], 5)
        self.assertIn("Telegram", result["error"])

    def test_stability_requires_two_consecutive_complete_rounds(self) -> None:
        failed = {
            "online": False,
            "passed": 4,
            "total": 5,
            "checks": [],
            "latency_ms": 20,
            "error": "Telegram（timeout）",
        }
        passed = {
            "online": True,
            "passed": 5,
            "total": 5,
            "checks": [],
            "latency_ms": 30,
            "error": "",
        }
        with mock.patch.object(
            backend,
            "probe_connectivity_matrix",
            side_effect=[failed, passed, passed],
        ), mock.patch.object(backend.time, "sleep"):
            result = backend.probe_connectivity_stable(
                required_successes=2, max_attempts=4, timeout=1, interval=0
            )
        self.assertTrue(result["stable"])
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(result["consecutive_passes"], 2)
        self.assertEqual(
            backend.format_connectivity_result(result),
            "5/5 路 · 最慢 30 ms · 连续 2 轮",
        )

    def test_guard_returns_after_one_usable_round_without_sleeping(self) -> None:
        usable = {
            "online": False,
            "passed": 3,
            "total": 5,
            "checks": [
                {"key": "domestic", "online": True},
                {"key": "basic", "online": True},
                {"key": "proxy", "online": True},
            ],
            "latency_ms": 42,
            "error": "Telegram（timeout）",
        }
        with mock.patch.object(
            backend, "probe_connectivity_matrix", return_value=usable
        ) as matrix, mock.patch.object(backend.time, "sleep") as sleep:
            result = backend.probe_connectivity_guard(timeout=3.5, interval=2.0)

        matrix.assert_called_once_with(timeout=3.5)
        sleep.assert_not_called()
        self.assertEqual(result["guard_attempts"], 1)
        self.assertFalse(result["guard_confirmed"])
        self.assertEqual(result["latency_ms"], 42)

    def test_guard_rechecks_transient_unusable_result_before_confirming(self) -> None:
        first = {
            "online": False,
            "passed": 2,
            "total": 5,
            "checks": [
                {"key": "domestic", "online": True},
                {"key": "basic", "online": True},
                {"key": "proxy", "online": False},
            ],
            "latency_ms": 200,
            "error": "代理链路（timeout）",
        }
        second = {
            "online": False,
            "passed": 3,
            "total": 5,
            "checks": [
                {"key": "domestic", "online": True},
                {"key": "basic", "online": True},
                {"key": "proxy", "online": True},
                {"key": "github", "online": True},
            ],
            "latency_ms": 65,
            "error": "Telegram（timeout）",
        }
        with mock.patch.object(
            backend, "probe_connectivity_matrix", side_effect=[first, second]
        ) as matrix, mock.patch.object(backend.time, "sleep") as sleep:
            result = backend.probe_connectivity_guard(timeout=4.0, interval=0.25)

        self.assertEqual(matrix.call_args_list, [mock.call(timeout=4.0)] * 2)
        sleep.assert_called_once_with(0.25)
        self.assertEqual(result["guard_attempts"], 2)
        self.assertFalse(result["guard_confirmed"])
        self.assertEqual(result["latency_ms"], 65)

    def test_guard_confirms_only_when_second_round_is_unusable(self) -> None:
        unusable = {
            "online": False,
            "passed": 2,
            "total": 5,
            "checks": [
                {"key": "domestic", "online": True},
                {"key": "basic", "online": False},
                {"key": "proxy", "online": True},
            ],
            "latency_ms": 310,
            "error": "基础联网（timeout）",
        }
        with mock.patch.object(
            backend, "probe_connectivity_matrix", side_effect=[unusable, unusable]
        ) as matrix, mock.patch.object(backend.time, "sleep") as sleep:
            result = backend.probe_connectivity_guard(timeout=1.5, interval=0)

        self.assertEqual(matrix.call_args_list, [mock.call(timeout=1.5)] * 2)
        sleep.assert_not_called()
        self.assertEqual(result["guard_attempts"], 2)
        self.assertTrue(result["guard_confirmed"])
        self.assertEqual(result["error"], "基础联网（timeout）")


class WatchdogTest(unittest.TestCase):
    def test_loop_keeps_tun_when_one_remote_probe_times_out(self) -> None:
        degraded = {
            "online": False,
            "passed": 4,
            "total": 5,
            "checks": [
                {"key": "domestic", "online": True},
                {"key": "basic", "online": True},
                {"key": "proxy", "online": False},
                {"key": "github", "online": True},
                {"key": "telegram", "online": True},
            ],
            "latency_ms": 6009,
            "error": "代理链路（TLS 握手超时）",
            "guard_confirmed": True,
        }
        with mock.patch.object(backend.os, "geteuid", return_value=0), mock.patch.object(
            backend, "managed_service_active", side_effect=[True, False]
        ), mock.patch.object(
            backend, "configured_mode", return_value="Rule"
        ), mock.patch.object(
            backend, "interface_exists", return_value=True
        ), mock.patch.object(
            backend, "probe_connectivity_guard", return_value=degraded
        ), mock.patch.object(backend, "systemctl_service") as systemctl, mock.patch.object(
            backend.time, "sleep"
        ), mock.patch.object(backend, "_watchdog_log"):
            result = backend.watchdog_loop(
                service_unit="smart-box@e.service",
                interval=0.5,
                failure_limit=1,
                probe_timeout=1.5,
                guard_interval=0.1,
            )

        self.assertEqual(result, 0)
        systemctl.assert_not_called()

    def test_startup_failure_cleans_tun_synchronously(self) -> None:
        parser = backend.build_parser()
        arguments = parser.parse_args(["watchdog", "--startup"])
        with mock.patch.object(
            backend,
            "watchdog_startup_check",
            side_effect=backend.SmartBoxError("代理链路验收失败"),
        ), mock.patch.object(backend, "cleanup_tun") as cleanup:
            with self.assertRaisesRegex(backend.SmartBoxError, "代理链路验收失败"):
                backend._cli_watchdog(arguments)
        cleanup.assert_called_once_with()

    def test_startup_requires_two_usable_rounds(self) -> None:
        usable = {
            "online": False,
            "passed": 3,
            "total": 5,
            "checks": [
                {"key": "domestic", "online": True},
                {"key": "basic", "online": True},
                {"key": "proxy", "online": True},
                {"key": "github", "online": True},
            ],
            "latency_ms": 55,
            "error": "Telegram（timeout）",
        }
        with mock.patch.object(backend.time, "monotonic", return_value=0.0), mock.patch.object(
            backend, "wait_for", return_value=True
        ) as wait_for, mock.patch.object(
            backend, "configured_mode", return_value="Rule"
        ), mock.patch.object(
            backend, "probe_connectivity_guard", side_effect=[usable, usable]
        ) as guard, mock.patch.object(backend.time, "sleep") as sleep, mock.patch.object(
            backend, "_watchdog_log"
        ):
            result = backend.watchdog_startup_check(
                timeout=5.0, probe_timeout=1.25, interval=0.2
            )

        wait_for.assert_called_once()
        self.assertEqual(
            guard.call_args_list,
            [
                mock.call(timeout=1.25, interval=0.2, mode="Rule"),
                mock.call(timeout=1.25, interval=0.2, mode="Rule"),
            ],
        )
        sleep.assert_called_once_with(0.2)
        self.assertEqual(result["mode"], "Rule")
        self.assertEqual(result["startup_attempts"], 2)
        self.assertEqual(result["startup_consecutive"], 2)

    def test_loop_resets_a_single_confirmed_failure_after_recovery(self) -> None:
        failed = {
            "online": False,
            "passed": 2,
            "total": 5,
            "checks": [
                {"key": "domestic", "online": True},
                {"key": "basic", "online": True},
                {"key": "proxy", "online": False},
            ],
            "latency_ms": 110,
            "error": "代理链路（timeout）",
            "guard_confirmed": True,
        }
        recovered = {
            "online": False,
            "passed": 3,
            "total": 5,
            "checks": [
                {"key": "domestic", "online": True},
                {"key": "basic", "online": True},
                {"key": "proxy", "online": True},
                {"key": "telegram", "online": True},
            ],
            "latency_ms": 72,
            "error": "GitHub（timeout）",
            "guard_confirmed": False,
        }
        with mock.patch.object(backend.os, "geteuid", return_value=0), mock.patch.object(
            backend, "managed_service_active", side_effect=[True, True, False]
        ), mock.patch.object(
            backend, "configured_mode", return_value="Rule"
        ), mock.patch.object(
            backend, "interface_exists", return_value=True
        ), mock.patch.object(
            backend, "probe_connectivity_guard", side_effect=[failed, recovered]
        ) as guard, mock.patch.object(backend, "systemctl_service") as systemctl, mock.patch.object(
            backend.time, "sleep"
        ), mock.patch.object(backend, "_watchdog_log"):
            result = backend.watchdog_loop(
                service_unit="smart-box@e.service",
                interval=0.5,
                failure_limit=2,
                probe_timeout=1.5,
                guard_interval=0.1,
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            guard.call_args_list,
            [
                mock.call(timeout=1.5, interval=0.1, mode="Rule"),
                mock.call(timeout=1.5, interval=0.1, mode="Rule"),
            ],
        )
        systemctl.assert_not_called()

    def test_loop_restarts_instead_of_stopping_on_probe_exception(self) -> None:
        with mock.patch.object(backend.os, "geteuid", return_value=0), mock.patch.object(
            backend, "managed_service_active", return_value=True
        ), mock.patch.object(
            backend, "configured_mode", return_value="Rule"
        ), mock.patch.object(
            backend, "interface_exists", return_value=True
        ), mock.patch.object(
            backend, "probe_connectivity_guard", side_effect=RuntimeError("internal error")
        ), mock.patch.object(
            backend, "systemctl_service"
        ) as systemctl, mock.patch.object(backend, "_watchdog_log"):
            with self.assertRaisesRegex(backend.SmartBoxError, "联网守护探测异常"):
                backend.watchdog_loop(
                    service_unit="smart-box@e.service",
                    interval=0.5,
                    probe_timeout=1.5,
                    guard_interval=0.1,
                )

        systemctl.assert_not_called()

    def test_managed_service_query_errors_are_not_treated_as_stopped(self) -> None:
        unknown = mock.Mock(returncode=4, stdout="Unit smart-box@e.service not found")
        with mock.patch.object(backend, "systemctl_service", return_value=unknown):
            with self.assertRaisesRegex(backend.SmartBoxError, "读取 smart-box@e.service 状态失败"):
                backend.managed_service_active("smart-box@e.service")

        with mock.patch.object(
            backend,
            "systemctl_service",
            side_effect=subprocess.TimeoutExpired(["systemctl"], 5),
        ):
            with self.assertRaisesRegex(backend.SmartBoxError, "读取 smart-box@e.service 状态失败"):
                backend.managed_service_active("smart-box@e.service")


class WatchdogLoopAndCleanupTest(unittest.TestCase):
    def test_cleanup_removes_a_lingering_tun_after_reverting_dns(self) -> None:
        completed = mock.Mock(returncode=0, stdout="")
        with mock.patch.object(backend, "revert_link_dns") as revert_dns, mock.patch.object(
            backend, "cleanup_policy_routing"
        ), mock.patch.object(
            backend, "cleanup_sing_box_nftables"
        ), mock.patch.object(
            backend, "smart_box_policy_residuals", return_value=[]
        ), mock.patch.object(
            backend, "interface_exists", return_value=True
        ), mock.patch.object(
            backend, "run_command", return_value=completed
        ) as run_command, mock.patch.object(
            backend, "wait_for", return_value=True
        ) as wait_for, mock.patch.object(backend, "flush_dns_cache") as flush:
            backend.cleanup_tun(timeout=4)

        revert_dns.assert_called_once_with(backend.TUN_INTERFACE)
        run_command.assert_called_once_with(
            [backend.IP_COMMAND, "link", "delete", "dev", backend.TUN_INTERFACE], timeout=10
        )
        wait_for.assert_called_once()
        flush.assert_called_once_with()

    def test_cleanup_rejects_a_tun_that_ip_cannot_delete(self) -> None:
        failed = mock.Mock(returncode=1, stdout="operation not permitted")
        with mock.patch.object(backend, "revert_link_dns"), mock.patch.object(
            backend, "cleanup_policy_routing"
        ), mock.patch.object(
            backend, "cleanup_sing_box_nftables"
        ), mock.patch.object(
            backend, "smart_box_policy_residuals", return_value=[]
        ), mock.patch.object(
            backend, "interface_exists", return_value=True
        ), mock.patch.object(backend, "run_command", return_value=failed):
            with self.assertRaisesRegex(backend.SmartBoxError, "删除 SmartBox TUN 失败"):
                backend.cleanup_tun()

    def test_loop_stops_with_no_block_after_repeated_confirmed_failure(self) -> None:
        failed = {
            "online": False,
            "passed": 2,
            "total": 5,
            "checks": [
                {"key": "domestic", "online": True},
                {"key": "basic", "online": True},
                {"key": "proxy", "online": False},
            ],
            "latency_ms": 140,
            "error": "代理链路（timeout）",
            "guard_confirmed": True,
        }
        completed = mock.Mock(returncode=0, stdout="")
        with mock.patch.object(backend.os, "geteuid", return_value=0), mock.patch.object(
            backend, "managed_service_active", side_effect=[True, True]
        ), mock.patch.object(
            backend, "configured_mode", return_value="Rule"
        ), mock.patch.object(
            backend, "interface_exists", return_value=True
        ), mock.patch.object(
            backend, "probe_connectivity_guard", side_effect=[failed, failed]
        ), mock.patch.object(
            backend, "systemctl_service", return_value=completed
        ) as systemctl, mock.patch.object(backend.time, "sleep"), mock.patch.object(
            backend, "_watchdog_log"
        ):
            result = backend.watchdog_loop(
                service_unit="smart-box@e.service",
                interval=0.5,
                failure_limit=2,
                probe_timeout=1.5,
                guard_interval=0.1,
            )

        self.assertEqual(result, 0)
        systemctl.assert_called_once_with(
            "stop", "--no-block", "smart-box@e.service", timeout=10
        )

    def test_loop_direct_mode_does_not_stop_for_overseas_only_failure(self) -> None:
        direct_usable = {
            "online": False,
            "passed": 1,
            "total": 5,
            "checks": [
                {"key": "domestic", "online": True},
                {"key": "basic", "online": False},
                {"key": "proxy", "online": False},
                {"key": "github", "online": False},
                {"key": "telegram", "online": False},
            ],
            "latency_ms": 90,
            "error": "基础联网（timeout）；代理链路（timeout）",
            # A stale/incorrect guard flag must not override Direct's domestic rule.
            "guard_confirmed": True,
        }
        with mock.patch.object(backend.os, "geteuid", return_value=0), mock.patch.object(
            backend, "managed_service_active", side_effect=[True, False]
        ), mock.patch.object(
            backend, "configured_mode", return_value="Direct"
        ), mock.patch.object(
            backend, "interface_exists", return_value=True
        ), mock.patch.object(
            backend, "probe_connectivity_guard", return_value=direct_usable
        ) as guard, mock.patch.object(backend, "systemctl_service") as systemctl, mock.patch.object(
            backend.time, "sleep"
        ), mock.patch.object(backend, "_watchdog_log"):
            result = backend.watchdog_loop(
                service_unit="smart-box@e.service",
                interval=0.5,
                failure_limit=1,
                probe_timeout=1.5,
                guard_interval=0.1,
            )

        self.assertEqual(result, 0)
        guard.assert_called_once_with(timeout=1.5, interval=0.1, mode="Direct")
        systemctl.assert_not_called()


class ServiceUnitFailOpenTest(unittest.TestCase):
    def test_stop_timeout_covers_synchronous_cleanup(self) -> None:
        service = Path(__file__).resolve().parents[1] / "smart-box@.service"
        watchdog = Path(__file__).resolve().parents[1] / "smart-box-watchdog@.service"
        self.assertIn("TimeoutStopSec=60s", service.read_text(encoding="utf-8"))
        self.assertIn("TimeoutStopSec=60s", watchdog.read_text(encoding="utf-8"))

    def test_startup_probe_precedes_resolved_dns_takeover(self) -> None:
        unit = Path(__file__).resolve().parents[1] / "smart-box@.service"
        content = unit.read_text(encoding="utf-8")
        self.assertLess(
            content.index("watchdog --startup"),
            content.index("dns install"),
        )

    def test_destructive_cleanup_commands_are_marked_as_service_lifecycle(self) -> None:
        linux_directory = Path(__file__).resolve().parents[1]
        service = (linux_directory / "smart-box@.service").read_text(
            encoding="utf-8"
        )
        cleanup = (linux_directory / "smart-box-cleanup@.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("cleanup --service-lifecycle", service)
        self.assertIn(
            "cleanup --verify-direct --service-lifecycle", service
        )
        self.assertIn(
            "desktop-proxy restore --service-lifecycle", service
        )
        self.assertIn("cleanup --service-lifecycle", cleanup)


class ServiceUnitRuntimeMaskTest(unittest.TestCase):
    @staticmethod
    def result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], returncode, stdout)

    def test_unmasks_only_runtime_masked_main_and_watchdog_then_verifies(self) -> None:
        main, watchdog = backend.smart_box_system_units()
        helper = backend.smart_box_unmask_helper_unit()
        masked = "LoadState=masked\nUnitFileState=masked-runtime\n"
        main_ready = "LoadState=loaded\nUnitFileState=disabled\n"
        watchdog_ready = "LoadState=loaded\nUnitFileState=static\n"
        with mock.patch.object(
            backend,
            "run_command",
            side_effect=[
                self.result(masked),
                self.result(masked),
                self.result(),
                self.result(main_ready),
                self.result(watchdog_ready),
            ],
        ) as run_command:
            states = backend.ensure_runtime_service_units_unmasked(timeout=7)

        self.assertEqual(states[main]["UnitFileState"], "disabled")
        self.assertEqual(states[watchdog]["UnitFileState"], "static")
        self.assertEqual(
            run_command.call_args_list,
            [
                mock.call(
                    [
                        "systemctl",
                        "show",
                        "--property=LoadState",
                        "--property=UnitFileState",
                        "--",
                        main,
                    ],
                    timeout=7,
                ),
                mock.call(
                    [
                        "systemctl",
                        "show",
                        "--property=LoadState",
                        "--property=UnitFileState",
                        "--",
                        watchdog,
                    ],
                    timeout=7,
                ),
                mock.call(
                    ["systemctl", "start", "--", helper],
                    timeout=7,
                ),
                mock.call(
                    [
                        "systemctl",
                        "show",
                        "--property=LoadState",
                        "--property=UnitFileState",
                        "--",
                        main,
                    ],
                    timeout=7,
                ),
                mock.call(
                    [
                        "systemctl",
                        "show",
                        "--property=LoadState",
                        "--property=UnitFileState",
                        "--",
                        watchdog,
                    ],
                    timeout=7,
                ),
            ],
        )

    def test_loaded_units_are_read_only(self) -> None:
        main, watchdog = backend.smart_box_system_units()
        with mock.patch.object(
            backend,
            "run_command",
            side_effect=[
                self.result("LoadState=loaded\nUnitFileState=disabled\n"),
                self.result("LoadState=loaded\nUnitFileState=static\n"),
            ],
        ) as run_command:
            states = backend.ensure_runtime_service_units_unmasked(timeout=4)

        self.assertEqual(set(states), {main, watchdog})
        self.assertEqual(run_command.call_count, 2)
        self.assertFalse(
            any("unmask" in call.args[0] for call in run_command.call_args_list)
        )

    def test_persistent_mask_is_reported_and_never_unmasked(self) -> None:
        main, watchdog = backend.smart_box_system_units()
        with mock.patch.object(
            backend,
            "run_command",
            side_effect=[
                self.result("LoadState=masked\nUnitFileState=masked\n"),
                self.result("LoadState=loaded\nUnitFileState=static\n"),
            ],
        ) as run_command:
            with self.assertRaises(backend.ServiceUnitMaskError) as raised:
                backend.ensure_runtime_service_units_unmasked(timeout=5)

        self.assertEqual(raised.exception.stage, "persistent-mask")
        self.assertEqual(raised.exception.units, (main,))
        self.assertEqual(
            raised.exception.states[main],
            {"LoadState": "masked", "UnitFileState": "masked"},
        )
        self.assertIn("自动修复仅解除 masked-runtime", str(raised.exception))
        self.assertEqual(run_command.call_count, 2)
        self.assertNotIn(watchdog, raised.exception.units)

    def test_helper_failure_has_structured_unit_state_and_detail(self) -> None:
        main, watchdog = backend.smart_box_system_units()
        helper = backend.smart_box_unmask_helper_unit()
        masked = "LoadState=masked\nUnitFileState=masked-runtime\n"
        with mock.patch.object(
            backend,
            "run_command",
            side_effect=[
                self.result(masked),
                self.result("LoadState=loaded\nUnitFileState=static\n"),
                self.result("access denied", returncode=1),
            ],
        ):
            with self.assertRaises(backend.ServiceUnitMaskError) as raised:
                backend.ensure_runtime_service_units_unmasked(timeout=6)

        error = raised.exception
        self.assertEqual(error.stage, "runtime-unmask-helper")
        self.assertEqual(error.units, (main,))
        self.assertEqual(error.detail, f"启动 {helper} 失败：access denied")
        self.assertEqual(error.states[main]["UnitFileState"], "masked-runtime")
        self.assertNotIn(watchdog, error.units)

    def test_helper_uses_the_same_validated_instance(self) -> None:
        with mock.patch.object(backend, "SERVICE_UNIT", "smart-box@alice.test.service"):
            self.assertEqual(
                backend.smart_box_system_units(),
                (
                    "smart-box@alice.test.service",
                    "smart-box-watchdog@alice.test.service",
                ),
            )
            self.assertEqual(
                backend.smart_box_unmask_helper_unit(),
                "smart-box-unmask@alice.test.service",
            )

    def test_unrelated_unit_is_rejected_without_systemctl(self) -> None:
        with mock.patch.object(backend, "run_command") as run_command:
            with self.assertRaises(backend.ServiceUnitMaskError) as raised:
                backend.service_unit_mask_state("ssh.service")

        self.assertEqual(raised.exception.stage, "target-validation")
        self.assertEqual(raised.exception.units, ("ssh.service",))
        run_command.assert_not_called()

    def test_start_and_restart_prepare_masks_but_other_actions_do_not(self) -> None:
        completed = self.result()
        with mock.patch.object(
            backend, "ensure_runtime_service_units_unmasked"
        ) as ensure_unmasked, mock.patch.object(
            backend, "run_command", return_value=completed
        ) as run_command:
            self.assertIs(
                backend.systemctl_service(
                    "start", backend.SERVICE_UNIT, timeout=30
                ),
                completed,
            )
            backend.systemctl_service(
                "restart", "--no-block", backend.SERVICE_UNIT, timeout=20
            )
            backend.systemctl_service("stop", backend.SERVICE_UNIT, timeout=9)
            backend.systemctl_service("start", "ssh.service", timeout=8)

        self.assertEqual(
            ensure_unmasked.call_args_list,
            [mock.call(timeout=15.0), mock.call(timeout=15.0)],
        )
        self.assertEqual(
            run_command.call_args_list,
            [
                mock.call(
                    ["systemctl", "start", backend.SERVICE_UNIT], timeout=30
                ),
                mock.call(
                    [
                        "systemctl",
                        "restart",
                        "--no-block",
                        backend.SERVICE_UNIT,
                    ],
                    timeout=20,
                ),
                mock.call(
                    ["systemctl", "stop", backend.SERVICE_UNIT], timeout=9
                ),
                mock.call(["systemctl", "start", "ssh.service"], timeout=8),
            ],
        )

    def test_start_preflight_recognizes_systemctl_unit_option_forms(self) -> None:
        completed = self.result()
        with mock.patch.object(
            backend, "ensure_runtime_service_units_unmasked"
        ) as ensure_unmasked, mock.patch.object(
            backend, "run_command", return_value=completed
        ):
            backend.systemctl_service(
                "start", "--unit", backend.SERVICE_UNIT, timeout=8
            )
            backend.systemctl_service(
                "restart", f"--unit={backend.SERVICE_UNIT}", timeout=9
            )

        self.assertEqual(
            ensure_unmasked.call_args_list,
            [mock.call(timeout=8.0), mock.call(timeout=9.0)],
        )


class TunCleanupIntegrationTest(unittest.TestCase):
    def test_cleanup_reverts_dns_deletes_tun_and_confirms_absence(self) -> None:
        events: list[tuple[str, object]] = []
        command_result = mock.Mock(returncode=0, stdout="")

        def fake_interface_exists(name: str) -> bool:
            events.append(("interface_exists", name))
            return len([event for event in events if event[0] == "interface_exists"]) == 1

        def fake_wait_for(predicate: object, timeout: float, interval: float) -> bool:
            events.append(("wait_for", (timeout, interval)))
            return predicate()  # type: ignore[operator]

        def fake_revert(name: str) -> None:
            events.append(("revert_link_dns", name))

        def fake_run(command: list[str], timeout: float) -> mock.Mock:
            events.append(("run_command", (command, timeout)))
            return command_result

        def fake_flush() -> bool:
            events.append(("flush_dns_cache", ""))
            return True

        with mock.patch.object(backend, "revert_link_dns", side_effect=fake_revert), mock.patch.object(
            backend, "cleanup_policy_routing"
        ), mock.patch.object(
            backend, "cleanup_sing_box_nftables"
        ), mock.patch.object(
            backend, "smart_box_policy_residuals", return_value=[]
        ), mock.patch.object(
            backend, "interface_exists", side_effect=fake_interface_exists
        ), mock.patch.object(
            backend, "run_command", side_effect=fake_run
        ) as run_command, mock.patch.object(
            backend, "wait_for", side_effect=fake_wait_for
        ) as wait_for, mock.patch.object(
            backend, "flush_dns_cache", side_effect=fake_flush
        ):
            backend.cleanup_tun("SmartBox", timeout=7.0)

        run_command.assert_called_once_with(
            [backend.IP_COMMAND, "link", "delete", "dev", "SmartBox"], timeout=10
        )
        wait_for.assert_called_once()
        self.assertEqual(
            events,
            [
                ("revert_link_dns", "SmartBox"),
                ("interface_exists", "SmartBox"),
                (
                    "run_command",
                    ([backend.IP_COMMAND, "link", "delete", "dev", "SmartBox"], 10),
                ),
                ("wait_for", (7.0, 0.2)),
                ("interface_exists", "SmartBox"),
                ("flush_dns_cache", ""),
            ],
        )


class UfwTunRuleTest(unittest.TestCase):
    @staticmethod
    def runtime_path(root: Path) -> Path:
        path = root / "runtime.json"
        profile = sample_profile()
        tun = next(item for item in profile["inbounds"] if item["type"] == "tun")
        tun["address"] = ["172.19.0.1/30", "fdfe:dcba:9876::1/126"]
        path.write_text(json.dumps(profile), encoding="utf-8")
        return path

    @staticmethod
    def user_rule(
        destination: str,
        source: str,
        comment: str = "",
    ) -> str:
        suffix = f" comment={comment}" if comment else ""
        return (
            "### tuple ### allow any any "
            f"{destination} any {source} in_SmartBox{suffix}\n"
        )

    def test_ufw_status_uses_c_locale_and_only_accepts_active_state(self) -> None:
        completed = mock.Mock(returncode=0, stdout="Status: active\n")
        with tempfile.TemporaryDirectory() as temporary_dir:
            command = Path(temporary_dir) / "ufw"
            command.touch(mode=0o755)
            with mock.patch.object(backend, "UFW_COMMAND", command), mock.patch.object(
                backend, "run_command", return_value=completed
            ) as run:
                self.assertTrue(backend.ufw_enabled())

        run.assert_called_once_with(
            ["/usr/bin/env", "LC_ALL=C", "LANG=C", str(command), "status"],
            timeout=10,
        )

    def test_ufw_inactive_state_is_not_modified(self) -> None:
        completed = mock.Mock(returncode=0, stdout="Status: inactive\n")
        with tempfile.TemporaryDirectory() as temporary_dir:
            command = Path(temporary_dir) / "ufw"
            command.touch(mode=0o755)
            with mock.patch.object(backend, "UFW_COMMAND", command), mock.patch.object(
                backend, "run_command", return_value=completed
            ):
                self.assertFalse(backend.ufw_enabled())

    def test_installs_only_current_tun_addresses_when_ufw_is_active(self) -> None:
        completed = mock.Mock(returncode=0, stdout="")
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            runtime = self.runtime_path(root)
            rules4 = root / "user.rules"
            rules6 = root / "user6.rules"
            rules4.write_text("*filter\n", encoding="utf-8")
            rules6.write_text("*filter\n", encoding="utf-8")
            with mock.patch.object(backend, "UFW_USER_RULES_PATH", rules4), mock.patch.object(
                backend, "UFW_USER6_RULES_PATH", rules6
            ), mock.patch.object(
                backend, "ufw_enabled", return_value=True
            ), mock.patch.object(
                backend.os, "geteuid", return_value=0
            ), mock.patch.object(backend, "run_command", return_value=completed) as run:
                result = backend.install_ufw_tun_rules(runtime)

        self.assertEqual(result, ["172.19.0.1", "fdfe:dcba:9876::1"])
        self.assertEqual(
            run.call_args_list,
            [
                mock.call(
                    [
                        str(backend.UFW_COMMAND),
                        "allow",
                        "in",
                        "on",
                        backend.TUN_INTERFACE,
                        "to",
                        "172.19.0.1",
                        "comment",
                        backend.UFW_TUN_RULE_COMMENT,
                    ],
                    timeout=20,
                ),
                mock.call(
                    [
                        str(backend.UFW_COMMAND),
                        "allow",
                        "in",
                        "on",
                        backend.TUN_INTERFACE,
                        "to",
                        "fdfe:dcba:9876::1",
                        "comment",
                        backend.UFW_TUN_RULE_COMMENT,
                    ],
                    timeout=20,
                ),
            ],
        )

    def test_does_not_change_an_existing_user_tun_allow_rule(self) -> None:
        completed = mock.Mock(returncode=0, stdout="")
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            runtime = self.runtime_path(root)
            rules4 = root / "user.rules"
            rules6 = root / "user6.rules"
            rules4.write_text(
                self.user_rule("0.0.0.0/0", "0.0.0.0/0", "user-owned"),
                encoding="utf-8",
            )
            rules6.write_text(
                self.user_rule("::/0", "::/0", "user-owned"), encoding="utf-8"
            )
            with mock.patch.object(backend, "UFW_USER_RULES_PATH", rules4), mock.patch.object(
                backend, "UFW_USER6_RULES_PATH", rules6
            ), mock.patch.object(
                backend, "ufw_enabled", return_value=True
            ), mock.patch.object(
                backend.os, "geteuid", return_value=0
            ), mock.patch.object(backend, "run_command", return_value=completed) as run:
                result = backend.install_ufw_tun_rules(runtime)

        self.assertEqual(result, [])
        run.assert_not_called()

    def test_skips_all_firewall_writes_when_ufw_is_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir, mock.patch.object(
            backend, "ufw_enabled", return_value=False
        ), mock.patch.object(backend, "run_command") as run:
            result = backend.install_ufw_tun_rules(self.runtime_path(Path(temporary_dir)))

        self.assertEqual(result, [])
        run.assert_not_called()

    def test_rolls_back_addresses_added_before_a_ufw_failure(self) -> None:
        completed = mock.Mock(returncode=0, stdout="")
        failed = mock.Mock(returncode=1, stdout="ufw write failed")
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            runtime = self.runtime_path(root)
            rules4 = root / "user.rules"
            rules6 = root / "user6.rules"
            rules4.write_text("*filter\n", encoding="utf-8")
            rules6.write_text("*filter\n", encoding="utf-8")
            with mock.patch.object(backend, "UFW_USER_RULES_PATH", rules4), mock.patch.object(
                backend, "UFW_USER6_RULES_PATH", rules6
            ), mock.patch.object(
                backend, "ufw_enabled", return_value=True
            ), mock.patch.object(
                backend.os, "geteuid", return_value=0
            ), mock.patch.object(
                backend, "run_command", side_effect=[completed, failed, completed]
            ) as run:
                with self.assertRaisesRegex(backend.SmartBoxError, "ufw write failed"):
                    backend.install_ufw_tun_rules(runtime)

        self.assertEqual(
            [
                (
                    call.args[0][1],
                    call.args[0][6] if call.args[0][1] == "allow" else call.args[0][7],
                )
                for call in run.call_args_list
            ],
            [
                ("allow", "172.19.0.1"),
                ("allow", "fdfe:dcba:9876::1"),
                ("delete", "172.19.0.1"),
            ],
        )

    def test_removes_only_rules_with_the_smartbox_marker(self) -> None:
        completed = mock.Mock(returncode=0, stdout="")
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            rules4 = root / "user.rules"
            rules6 = root / "user6.rules"
            rules4.write_text(
                self.user_rule(
                    "172.19.0.1",
                    "0.0.0.0/0",
                    backend.UFW_TUN_RULE_COMMENT_HEX,
                )
                + self.user_rule("172.19.0.2", "0.0.0.0/0", "user-owned"),
                encoding="utf-8",
            )
            rules6.write_text(
                self.user_rule(
                    "fdfe:dcba:9876::1",
                    "::/0",
                    backend.UFW_TUN_RULE_COMMENT_HEX,
                ),
                encoding="utf-8",
            )
            with mock.patch.object(backend, "UFW_USER_RULES_PATH", rules4), mock.patch.object(
                backend, "UFW_USER6_RULES_PATH", rules6
            ), mock.patch.object(
                backend, "ufw_enabled", return_value=True
            ), mock.patch.object(
                backend.os, "geteuid", return_value=0
            ), mock.patch.object(backend, "run_command", return_value=completed) as run:
                result = backend.remove_ufw_tun_rules()

        self.assertEqual(result, ["172.19.0.1", "fdfe:dcba:9876::1"])
        self.assertEqual(len(run.call_args_list), 2)
        self.assertTrue(all(call.args[0][1] == "delete" for call in run.call_args_list))
        self.assertNotIn("172.19.0.2", str(run.call_args_list))

    def test_cleanup_cli_removes_managed_rules_after_tun_cleanup(self) -> None:
        with mock.patch.object(backend, "managed_service_active", return_value=False), mock.patch.object(
            backend, "cleanup_tun"
        ) as cleanup, mock.patch.object(
            backend, "remove_ufw_tun_rules", return_value=["172.19.0.1"]
        ) as remove, mock.patch.object(backend, "desktop_proxy_restore") as restore_proxy:
            self.assertEqual(backend._cli_cleanup(mock.Mock(verify_direct=False)), 0)

        cleanup.assert_called_once_with(verify_direct=False)
        remove.assert_called_once_with()
        restore_proxy.assert_called_once_with()

    def test_cleanup_cli_still_removes_managed_rules_after_tun_failure(self) -> None:
        with mock.patch.object(backend, "managed_service_active", return_value=False), mock.patch.object(
            backend, "cleanup_tun", side_effect=backend.SmartBoxError("TUN delete failed")
        ), mock.patch.object(backend, "remove_ufw_tun_rules") as remove, mock.patch.object(
            backend, "desktop_proxy_restore"
        ) as restore_proxy:
            with self.assertRaisesRegex(backend.SmartBoxError, "TUN delete failed"):
                backend._cli_cleanup(mock.Mock(verify_direct=False))

        remove.assert_called_once_with()
        restore_proxy.assert_called_once_with()

    def test_cleanup_cli_refuses_to_mutate_live_service_state(self) -> None:
        with mock.patch.object(backend, "managed_service_active", return_value=True), mock.patch.object(
            backend, "cleanup_tun"
        ) as cleanup, mock.patch.object(
            backend, "remove_ufw_tun_rules"
        ) as remove, mock.patch.object(
            backend, "desktop_proxy_restore"
        ) as restore_proxy:
            with self.assertRaisesRegex(
                backend.SmartBoxError, "核心仍在运行.*拒绝清理"
            ):
                backend._cli_cleanup(mock.Mock(verify_direct=False))

        cleanup.assert_not_called()
        remove.assert_not_called()
        restore_proxy.assert_not_called()

    def test_desktop_proxy_cli_refuses_restore_while_service_is_active(self) -> None:
        with mock.patch.object(backend, "managed_service_active", return_value=True), mock.patch.object(
            backend, "desktop_proxy_restore"
        ) as restore_proxy:
            with self.assertRaisesRegex(
                backend.SmartBoxError, "核心仍在运行.*拒绝恢复 KDE 代理"
            ):
                backend._cli_desktop_proxy(mock.Mock(action="restore"))

        restore_proxy.assert_not_called()

    def test_service_lifecycle_cleanup_may_run_during_systemd_transition(self) -> None:
        arguments = mock.Mock(verify_direct=False, service_lifecycle=True)
        with mock.patch.object(backend, "managed_service_active") as active, mock.patch.object(
            backend, "cleanup_tun"
        ) as cleanup, mock.patch.object(
            backend, "remove_ufw_tun_rules"
        ) as remove, mock.patch.object(
            backend, "desktop_proxy_restore"
        ) as restore_proxy:
            self.assertEqual(backend._cli_cleanup(arguments), 0)

        cleanup.assert_called_once_with(verify_direct=False)
        remove.assert_called_once_with()
        restore_proxy.assert_called_once_with()
        active.assert_not_called()

    def test_cleanup_and_proxy_restore_fail_closed_when_service_state_is_unknown(self) -> None:
        state_error = backend.SmartBoxError("injected systemd query failure")
        with mock.patch.object(
            backend, "managed_service_active", side_effect=state_error
        ), mock.patch.object(backend, "cleanup_tun") as cleanup, mock.patch.object(
            backend, "remove_ufw_tun_rules"
        ) as remove, mock.patch.object(
            backend, "desktop_proxy_restore"
        ) as restore_proxy:
            with self.assertRaisesRegex(
                backend.SmartBoxError, "injected systemd query failure"
            ):
                backend._cli_cleanup(mock.Mock(verify_direct=False))
            with self.assertRaisesRegex(
                backend.SmartBoxError, "injected systemd query failure"
            ):
                backend._cli_desktop_proxy(mock.Mock(action="restore"))

        cleanup.assert_not_called()
        remove.assert_not_called()
        restore_proxy.assert_not_called()


class CliParserTest(unittest.TestCase):
    def test_service_prepare_does_not_require_interactive_dns_authentication(self) -> None:
        arguments = mock.Mock(
            require_no_flclash=True,
            no_check=True,
            quiet=True,
        )
        with mock.patch.object(backend, "flclash_conflict", return_value=False), mock.patch.object(
            backend, "flush_dns_cache"
        ) as flush, mock.patch.object(
            backend, "prepare_runtime", return_value=Path("/tmp/runtime.json")
        ) as prepare:
            result = backend._cli_prepare(arguments)

        self.assertEqual(result, 0)
        flush.assert_not_called()
        prepare.assert_called_once_with(check=False)

    def test_parser_exposes_cleanup_and_watchdog_loop(self) -> None:
        parser = backend.build_parser()

        cleanup = parser.parse_args(["cleanup"])
        firewall = parser.parse_args(["firewall", "install"])
        watchdog = parser.parse_args(
            [
                "watchdog",
                "--loop",
                "--service-unit",
                "smart-box@e.service",
                "--interval",
                "12.5",
                "--failure-limit",
                "3",
                "--probe-timeout",
                "2.5",
                "--guard-interval",
                "0.4",
            ]
        )

        self.assertIs(cleanup.handler, backend._cli_cleanup)
        self.assertFalse(cleanup.verify_direct)
        self.assertFalse(cleanup.service_lifecycle)
        self.assertTrue(parser.parse_args(["cleanup", "--verify-direct"]).verify_direct)
        self.assertTrue(
            parser.parse_args(["cleanup", "--service-lifecycle"]).service_lifecycle
        )
        self.assertIs(firewall.handler, backend._cli_firewall)
        self.assertEqual(firewall.action, "install")
        self.assertIs(watchdog.handler, backend._cli_watchdog)
        self.assertTrue(watchdog.loop)
        self.assertFalse(watchdog.startup)
        self.assertEqual(watchdog.service_unit, "smart-box@e.service")
        self.assertEqual(watchdog.interval, 12.5)
        self.assertEqual(watchdog.failure_limit, 3)
        self.assertEqual(watchdog.probe_timeout, 2.5)
        self.assertEqual(watchdog.guard_interval, 0.4)


class GroupDelayProbeTest(unittest.TestCase):
    def test_encodes_group_and_returns_ordered_delay_result(self) -> None:
        calls: list[tuple[str, float]] = []

        def fake_api(path: str, timeout: float) -> dict[str, int]:
            calls.append((path, timeout))
            return {"节点/1": 240, "节点/2": 120, "ignored": 0, "bad": "x"}  # type: ignore[return-value]

        with mock.patch.object(backend, "api_request", side_effect=fake_api):
            result = backend.probe_group_delays(
                "🎯 / 基准", ["节点/1", "节点/2", "失败节点"]
            )

        self.assertEqual(len(calls), 1)
        path, timeout = calls[0]
        self.assertIn("/group/%F0%9F%8E%AF%20%2F%20%E5%9F%BA%E5%87%86/delay?", path)
        query = backend.urllib.parse.parse_qs(path.split("?", 1)[1])
        self.assertEqual(query["url"], [backend.CONNECTIVITY_PROBE_URL])
        self.assertEqual(query["timeout"], ["8000"])
        self.assertEqual(timeout, 10.0)
        self.assertEqual(result["delays"], {"节点/2": 120, "节点/1": 240})
        self.assertEqual(result["failed"], ["失败节点"])
        self.assertEqual(result["tested"], 2)
        self.assertEqual(result["total"], 3)
        self.assertEqual(backend.format_group_delay_summary(result), "最快 120 ms · 2/3")
        details = backend.format_group_delay_details(result)
        self.assertIn("节点/1: 240 ms", details)
        self.assertIn("节点/2: 120 ms", details)
        self.assertIn("失败节点: 失败", details)

    def test_rejects_invalid_group_or_api_shape(self) -> None:
        with self.assertRaises(backend.SmartBoxError):
            backend.probe_group_delays("", [])
        with self.assertRaises(backend.SmartBoxError):
            backend.probe_group_delays("group", timeout_ms=999)
        with mock.patch.object(backend, "api_request", return_value=["bad"]):
            with self.assertRaises(backend.SmartBoxError):
                backend.probe_group_delays("group")

    def test_empty_group_result_is_visible_as_all_failed(self) -> None:
        with mock.patch.object(backend, "api_request", return_value={}):
            result = backend.probe_group_delays("group", ["A", "B"])
        self.assertEqual(backend.format_group_delay_summary(result), "无可用节点 · 0/2")
        self.assertEqual(backend.format_group_delay_details(result), "A: 失败\nB: 失败")


class MirrorBenchmarkTest(unittest.TestCase):
    def test_benchmark_sorts_successful_mirrors_and_keeps_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            source_path = Path(temporary_dir) / "mirrorlist"
            source_path.write_text(
                "# local\nServer = https://slow.example/$repo/os/$arch\n"
                "Server = https://fast.example/$repo/os/$arch\n"
                "Server = https://down.example/$repo/os/$arch\n",
                encoding="utf-8",
            )
            ranking_dir = Path(temporary_dir) / "rankings"

            def fake_probe(server: str, repo: str, timeout: float) -> dict[str, object]:
                if "down" in server:
                    return {"server": server, "ok": False, "latency_ms": 800, "speed_kib_s": 0.0}
                return {
                    "server": server,
                    "ok": True,
                    "latency_ms": 40 if "fast" in server else 200,
                    "speed_kib_s": 300.0 if "fast" in server else 30.0,
                }

            with mock.patch.object(backend, "MIRROR_RANKING_DIR", ranking_dir), mock.patch.object(
                backend, "_probe_mirror_server", side_effect=fake_probe
            ):
                result = backend.benchmark_mirror_sources(
                    "arch", source_paths={"arch": source_path}, max_mirrors=10
                )

            summary = result["summaries"]["arch"]
            self.assertEqual(summary["tested"], 3)
            self.assertEqual(summary["successful"], 2)
            self.assertEqual(summary["failed"], 1)
            self.assertIn("fast.example", summary["best"]["server"])
            self.assertIn("down.example", summary["results"][-1]["server"])
            self.assertTrue((ranking_dir / "arch.json").is_file())
            self.assertIn("Server = https://fast.example", backend.ranked_mirrorlist_content(summary).decode())

    def test_benchmark_rejects_unknown_repo_and_invalid_limits(self) -> None:
        with self.assertRaises(backend.SmartBoxError):
            backend.benchmark_mirror_sources("unknown")
        with self.assertRaises(backend.SmartBoxError):
            backend.benchmark_mirror_sources("arch", timeout=0.1)
        with self.assertRaises(backend.SmartBoxError):
            backend.benchmark_mirror_sources("arch", max_mirrors=0)

    def test_parser_exposes_read_only_benchmark_and_explicit_apply(self) -> None:
        parser = backend.build_parser()
        benchmark = parser.parse_args(["mirror-benchmark", "--repo", "cachyos", "--max-mirrors", "3"])
        apply = parser.parse_args(["mirror-apply", "--repo", "arch"])
        self.assertIs(benchmark.handler, backend._cli_mirror_benchmark)
        self.assertEqual(benchmark.repo, "cachyos")
        self.assertEqual(benchmark.max_mirrors, 3)
        self.assertIs(apply.handler, backend._cli_mirror_apply)
        self.assertEqual(apply.repo, "arch")

    def test_ranked_cachyos_v3_content_preserves_architecture_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            source_path = Path(temporary_dir) / "cachyos-v3-mirrorlist"
            source_path.write_text(
                "Server = https://slow.example/repo/$arch_v3/$repo\n"
                "Server = https://fast.example/repo/$arch/$repo\n",
                encoding="utf-8",
            )
            summary = {
                "repo": "cachyos",
                "source_path": str(source_path),
                "results": [
                    {"server": "https://fast.example/repo/$arch/$repo"},
                    {"server": "https://slow.example/repo/$arch/$repo"},
                ],
            }
            content = backend.ranked_mirrorlist_content(summary).decode()
            self.assertEqual(
                content.splitlines(),
                [
                    "Server = https://fast.example/repo/$arch_v3/$repo",
                    "Server = https://slow.example/repo/$arch_v3/$repo",
                ],
            )

    def test_apply_mirror_ranking_updates_all_existing_related_lists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            generic = root / "cachyos-mirrorlist"
            v3 = root / "cachyos-v3-mirrorlist"
            generic.write_text(
                "Server = https://slow.example/repo/$arch/$repo\n"
                "Server = https://fast.example/repo/$arch/$repo\n",
                encoding="utf-8",
            )
            v3.write_text(
                "Server = https://slow.example/repo/$arch_v3/$repo\n"
                "Server = https://fast.example/repo/$arch_v3/$repo\n",
                encoding="utf-8",
            )
            ranking_dir = root / "rankings"
            profile = {
                "label": "CachyOS 源",
                "path": generic,
                "apply_paths": (generic, v3),
                "repo": "cachyos",
                "test_suffix": "cachyos.files",
            }
            summary = {
                "repo": "cachyos",
                "source_path": str(generic),
                "results": [
                    {"server": "https://fast.example/repo/$arch/$repo"},
                    {"server": "https://slow.example/repo/$arch/$repo"},
                ],
            }
            with mock.patch.object(backend, "MIRROR_PROFILES", {"cachyos": profile}), mock.patch.object(
                backend, "MIRROR_RANKING_DIR", ranking_dir
            ):
                result = backend.apply_mirror_ranking(summary)
            self.assertIn(str(generic), result["targets"])
            self.assertIn(str(v3), result["targets"])
            self.assertIn("fast.example", generic.read_text(encoding="utf-8").splitlines()[0])
            self.assertIn("$arch_v3", v3.read_text(encoding="utf-8").splitlines()[0])
            self.assertTrue((ranking_dir / "cachyos.0.before-apply").is_file())
            self.assertTrue((ranking_dir / "cachyos.1.before-apply").is_file())


class FailedSwitchRecoveryTest(unittest.TestCase):
    def test_recovery_stops_smart_box_and_restores_flclash(self) -> None:
        completed = mock.Mock(returncode=0, stdout="")
        with tempfile.TemporaryDirectory() as temporary_dir, mock.patch.object(
            backend, "SWITCH_STATE_PATH", Path(temporary_dir) / "switch-state.json"
        ), mock.patch.object(
            backend, "systemctl_service", return_value=completed
        ) as stop_service, mock.patch.object(
            backend, "run_privileged_cleanup"
        ) as privileged_cleanup, mock.patch.object(
            backend, "verify_fail_open", return_value={}
        ) as verify, mock.patch.object(
            backend, "start_flclash"
        ) as start_flclash:
            message = backend.recover_failed_switch(True, timeout=5)
        stop_service.assert_called_once_with("stop", backend.SERVICE_UNIT, timeout=5)
        privileged_cleanup.assert_called_once_with(timeout=10.0)
        verify.assert_called_once_with(timeout=5.0)
        start_flclash.assert_called_once_with(timeout=5)
        self.assertEqual(message, "smart-box 已停止，FlClash 已恢复")


class FlClashServiceTest(unittest.TestCase):
    def test_lists_active_dynamic_and_autostart_units(self) -> None:
        completed = mock.Mock(
            returncode=0,
            stdout=(
                "app-flclash@dynamic-id.service loaded active running FlClash\n"
                "app-FlClash@autostart.service loaded active running FlClash\n"
                "app-flclash-helper.service loaded active running helper\n"
                "unrelated.service loaded active running Other\n"
            ),
        )
        with mock.patch.object(
            backend, "systemctl_user", return_value=completed
        ) as systemctl_user:
            units = backend.active_flclash_units()
        self.assertEqual(
            units,
            [
                "app-flclash@dynamic-id.service",
                "app-FlClash@autostart.service",
            ],
        )
        systemctl_user.assert_called_once_with(
            "list-units",
            "--type=service",
            "--state=active",
            "--no-legend",
            "--plain",
            "--no-pager",
            timeout=5,
        )

    def test_dynamic_unit_is_a_flclash_conflict_without_tun(self) -> None:
        with mock.patch.object(
            backend, "interface_exists", return_value=False
        ), mock.patch.object(
            backend,
            "active_flclash_units",
            return_value=["app-flclash@dynamic-id.service"],
        ), mock.patch.object(backend, "unit_active") as fixed_unit_active:
            self.assertTrue(backend.flclash_conflict())
        fixed_unit_active.assert_not_called()

    def test_stop_flclash_stops_every_active_unit_and_confirms_cleanup(self) -> None:
        units = [
            "app-flclash@first.service",
            "app-flclash@second.service",
        ]
        completed = mock.Mock(returncode=0, stdout="")

        def wait_immediately(predicate: object, timeout: float, interval: float) -> bool:
            self.assertEqual(timeout, 8)
            self.assertEqual(interval, 0.25)
            return predicate()  # type: ignore[operator]

        with mock.patch.object(
            backend, "active_flclash_units", side_effect=[units, []]
        ), mock.patch.object(
            backend, "unit_active", return_value=False
        ), mock.patch.object(
            backend, "interface_exists", return_value=False
        ), mock.patch.object(
            backend, "systemctl_user", return_value=completed
        ) as systemctl_user, mock.patch.object(
            backend, "wait_for", side_effect=wait_immediately
        ):
            stopped_units = backend.stop_flclash(timeout=8)
        self.assertEqual(stopped_units, units)
        systemctl_user.assert_called_once_with("stop", *units, timeout=8)

    def test_stop_flclash_rejects_a_lingering_tun(self) -> None:
        with mock.patch.object(
            backend, "active_flclash_units", return_value=[]
        ), mock.patch.object(
            backend, "unit_active", return_value=False
        ), mock.patch.object(
            backend, "wait_for", return_value=False
        ):
            with self.assertRaisesRegex(backend.SmartBoxError, "TUN 未及时清理"):
                backend.stop_flclash(timeout=5)


class GuiAutostartTest(unittest.TestCase):
    def test_writes_absolute_launcher_and_removes_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_home = Path(temporary_dir) / "config"
            with mock.patch.object(
                backend, "_xdg_path", return_value=config_home
            ):
                backend.set_gui_autostart(True)
                target = config_home / "autostart/smart-box.desktop"
                content = target.read_text(encoding="utf-8")
                self.assertIn(
                    "Exec=/usr/local/bin/smart-box --background", content
                )
                self.assertIn("TryExec=/usr/local/bin/smart-box", content)
                self.assertTrue(backend.gui_autostart_enabled())

                backend.set_gui_autostart(False)
                self.assertFalse(target.exists())
                self.assertFalse(backend.gui_autostart_enabled())


class LocalUiSettingsTest(unittest.TestCase):
    def test_mutate_settings_is_linearizable_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_dir = root / "config"
            state_dir = root / "state"
            settings_path = config_dir / "settings.json"
            with mock.patch.object(backend, "CONFIG_DIR", config_dir), mock.patch.object(
                backend, "STATE_DIR", state_dir
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                backend.save_settings(copy.deepcopy(backend.DEFAULT_SETTINGS))

            context = multiprocessing.get_context("fork")
            start = context.Event()
            processes = [
                context.Process(
                    target=increment_settings_worker,
                    args=(str(config_dir), str(state_dir), start, 40),
                )
                for _ in range(4)
            ]
            for process in processes:
                process.start()
            start.set()
            for process in processes:
                process.join(15)
                self.assertEqual(process.exitcode, 0)

            with mock.patch.object(backend, "CONFIG_DIR", config_dir), mock.patch.object(
                backend, "STATE_DIR", state_dir
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                loaded = backend.load_settings()
                lock_mode = backend._settings_lock_path().stat().st_mode & 0o777
            self.assertEqual(loaded["concurrent_counter"], 160)
            self.assertEqual(lock_mode, 0o600)

    def test_mutator_exception_preserves_bytes_and_releases_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_dir = root / "config"
            state_dir = root / "state"
            settings_path = config_dir / "settings.json"
            with mock.patch.object(backend, "CONFIG_DIR", config_dir), mock.patch.object(
                backend, "STATE_DIR", state_dir
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                backend.save_settings(copy.deepcopy(backend.DEFAULT_SETTINGS))
                before = settings_path.read_bytes()

                def fail_after_change(settings: dict) -> None:
                    settings["theme"] = "dark"
                    raise RuntimeError("stop before commit")

                with self.assertRaisesRegex(RuntimeError, "stop before commit"):
                    backend.mutate_settings(fail_after_change)
                self.assertEqual(settings_path.read_bytes(), before)

                updated = backend.mutate_settings(
                    lambda settings: settings.__setitem__("theme", "dark")
                )
            self.assertEqual(updated["theme"], "dark")
            self.assertEqual(json.loads(settings_path.read_text(encoding="utf-8"))["theme"], "dark")

    def test_reader_uses_existing_lock_without_writable_config_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_dir = root / "config"
            state_dir = root / "state"
            settings_path = config_dir / "settings.json"
            with mock.patch.object(backend, "CONFIG_DIR", config_dir), mock.patch.object(
                backend, "STATE_DIR", state_dir
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                backend.mutate_settings(
                    lambda settings: settings.__setitem__("theme", "dark")
                )
                with mock.patch.object(
                    backend,
                    "ensure_directories",
                    side_effect=AssertionError("reader requested writable setup"),
                ):
                    loaded = backend.load_settings()

            self.assertEqual(loaded["theme"], "dark")

    def test_theme_and_log_refresh_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_dir = root / "config"
            state_dir = root / "state"
            settings_path = config_dir / "settings.json"
            with mock.patch.object(backend, "CONFIG_DIR", config_dir), mock.patch.object(
                backend, "STATE_DIR", state_dir
            ), mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                settings = copy.deepcopy(backend.DEFAULT_SETTINGS)
                settings["theme"] = "dark"
                settings["log_auto_refresh"] = False
                backend.save_settings(settings)
                loaded = backend.load_settings()
            self.assertEqual(loaded["theme"], "dark")
            self.assertFalse(loaded["log_auto_refresh"])

    def test_invalid_ui_settings_use_visible_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            settings_path = Path(temporary_dir) / "settings.json"
            settings_path.write_text(
                json.dumps({"theme": "unknown", "log_auto_refresh": "yes"}),
                encoding="utf-8",
            )
            with mock.patch.object(backend, "SETTINGS_PATH", settings_path):
                loaded = backend.load_settings()
            self.assertEqual(loaded["theme"], "light")
            self.assertTrue(loaded["log_auto_refresh"])


if __name__ == "__main__":
    unittest.main()
