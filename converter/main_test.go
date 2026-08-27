package main

import (
	"encoding/json"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
)

const testRuleBaseURL = "http://192.0.2.10:38473/rule-set/test-private-token-123456789"

func TestParseClashProtocolsAndRegions(t *testing.T) {
	yaml := `proxies:
  - {name: "🇭🇰 HK 01", type: anytls, server: hk.example.com, port: 443, password: secret, sni: edge.example.com, client-fingerprint: chrome}
  - {name: "🇯🇵 JP 01", type: hysteria2, server: jp.example.com, port: 8443, password: secret, skip-cert-verify: true, obfs: salamander, obfs-password: obfs-secret}
  - {name: "No Flag", type: vless, server: us.example.com, port: 443, uuid: 00000000-0000-0000-0000-000000000001, tls: true, network: ws, ws-opts: {path: /ws, headers: {Host: cdn.example.com}}}
  - {name: "🇸🇬 SG", type: trojan, server: sg.example.com, port: 443, password: secret}
  - {name: "🇺🇸 US", type: ss, server: us2.example.com, port: 8388, cipher: aes-128-gcm, password: secret}
`
	nodes, err := parseClash([]byte(yaml), "test")
	if err != nil {
		t.Fatal(err)
	}
	if len(nodes) != 5 {
		t.Fatalf("got %d nodes", len(nodes))
	}
	if nodes[4].Outbound["type"] != "shadowsocks" {
		t.Fatalf("ss was not mapped to shadowsocks")
	}
	if nodes[0].RegionKey != "🇭🇰" || nodes[2].RegionKey != "unknown" {
		t.Fatalf("unexpected regions: %q %q", nodes[0].RegionKey, nodes[2].RegionKey)
	}
	for _, index := range []int{0, 1, 3} {
		tls, ok := nodes[index].Outbound["tls"].(map[string]any)
		if !ok || tls["enabled"] != true {
			t.Fatalf("node %d should force TLS", index)
		}
	}
}

func TestParseClashFiltersInformationalProxies(t *testing.T) {
	yaml := `proxies:
  - {name: "剩余流量：34.67 GB", type: vless, server: status.example.com, port: 443, uuid: 00000000-0000-0000-0000-000000000001}
  - {name: "上次订阅更新：2026-08-19", type: vless, server: update.example.com, port: 443, uuid: 00000000-0000-0000-0000-000000000002}
  - {name: "小火箭无法使用请看教程下载ClashMi使用", type: vless, server: tutorial.example.com, port: 443, uuid: 00000000-0000-0000-0000-000000000003}
  - {name: "🇯🇵 JP Remaining Traffic Relay", type: vless, server: english.example.com, port: 443, uuid: 00000000-0000-0000-0000-000000000004}
  - {name: "🇯🇵 JP-01", type: vless, server: jp.example.com, port: 443, uuid: 00000000-0000-0000-0000-000000000005}
`
	nodes, err := parseClash([]byte(yaml), "test")
	if err != nil {
		t.Fatal(err)
	}
	if len(nodes) != 1 || nodes[0].Name != "🇯🇵 JP-01" {
		t.Fatalf("informational proxies were not filtered: %#v", nodes)
	}
}

func TestDedupeAndUniqueTags(t *testing.T) {
	a := Node{Tag: "same", Outbound: map[string]any{"type": "ss", "tag": "same", "server": "a", "server_port": 1, "password": "x"}}
	b := Node{Tag: "other", Outbound: map[string]any{"type": "ss", "tag": "other", "server": "a", "server_port": 1, "password": "x"}}
	c := Node{Tag: "same", Outbound: map[string]any{"type": "ss", "tag": "same", "server": "b", "server_port": 1, "password": "x"}}
	nodes := uniqueTags(dedupeNodes([]Node{a, b, c}))
	if len(nodes) != 2 {
		t.Fatalf("expected 2, got %d", len(nodes))
	}
	if nodes[0].Tag == nodes[1].Tag {
		t.Fatal("duplicate tags remain")
	}
}

func TestBootstrapCacheDefersInitialRefreshOnlyWithUsableCache(t *testing.T) {
	directory := t.TempDir()
	source := Source{Name: "test", URL: "https://provider.example/subscription"}
	app := &App{
		config: Config{
			PublicURL:       "http://192.0.2.10:38473",
			PublicPath:      "test-private-token-123456789",
			RefreshInterval: 24 * time.Hour,
			CacheDir:        directory,
			Sources:         []Source{source},
		},
		rules: newRuleSetStore(filepath.Join(directory, "rules"), nil, nil),
	}
	if app.bootstrapCache() {
		t.Fatal("missing provider cache must require an initial refresh")
	}
	nodes := []Node{
		{
			Tag:       "cached",
			Name:      "cached",
			Source:    source.Name,
			Region:    "Unknown",
			RegionKey: "unknown",
			Protocol:  "shadowsocks",
			Outbound: map[string]any{
				"type": "shadowsocks", "tag": "cached", "server": "192.0.2.1",
				"server_port": 443, "method": "aes-128-gcm", "password": "test",
			},
		},
		{
			Tag:       "test 剩余流量：1 GB",
			Name:      "剩余流量：1 GB",
			Source:    source.Name,
			Region:    "Unknown",
			RegionKey: "unknown",
			Protocol:  "shadowsocks",
			Outbound: map[string]any{
				"type": "shadowsocks", "tag": "test 剩余流量：1 GB", "server": "192.0.2.2",
				"server_port": 443, "method": "aes-128-gcm", "password": "test",
			},
		},
	}
	if err := app.writeCache(source, nodes); err != nil {
		t.Fatal(err)
	}
	if !app.bootstrapCache() {
		t.Fatal("valid provider cache must suppress the initial refresh")
	}
	if app.state.Config == nil || app.state.NodeCount != 1 {
		t.Fatalf("cached snapshot was not loaded: %#v", app.state)
	}
	if app.state.SourceStatus[0].NodeCount != 1 {
		t.Fatalf("informational cached proxy was not filtered: %#v", app.state.SourceStatus[0])
	}
}

func TestProfileHasSmartGroupsAndNoProviderURL(t *testing.T) {
	nodes := []Node{
		{Tag: "hk", RegionKey: "🇭🇰", Outbound: map[string]any{"type": "ss", "tag": "hk", "server": "a", "server_port": 1, "method": "none", "password": "x"}},
		{Tag: "unknown", RegionKey: "unknown", Outbound: map[string]any{"type": "ss", "tag": "unknown", "server": "b", "server_port": 2, "method": "none", "password": "y"}},
	}
	profile := buildProfile(nodes, testRuleBaseURL)
	outbounds := profile["outbounds"].([]any)
	wantDefaults := map[string]string{
		baselineTag:       globalSmartTag,
		aiPolicy:          aiFallbackTag,
		telegramPolicy:    telegramFallbackTag,
		netflixPolicy:     baselineTag,
		disneyPolicy:      baselineTag,
		maxPolicy:         baselineTag,
		primeVideoPolicy:  baselineTag,
		appleTVPolicy:     baselineTag,
		youtubePolicy:     baselineTag,
		tiktokPolicy:      baselineTag,
		douyinPolicy:      directTag,
		bilibiliPolicy:    directTag,
		spotifyPolicy:     baselineTag,
		mediaPolicy:       baselineTag,
		socialPolicy:      baselineTag,
		gamePolicy:        baselineTag,
		githubPolicy:      baselineTag,
		developerPolicy:   baselineTag,
		applePolicy:       baselineTag,
		microsoftPolicy:   baselineTag,
		googlePolicy:      baselineTag,
		networkTestPolicy: baselineTag,
		downloadPolicy:    directTag,
		cnDomainPolicy:    directTag,
		cnIPPolicy:        directTag,
		adsPolicy:         rejectTag,
	}
	policies := make(map[string]map[string]any, len(wantDefaults))
	for _, raw := range outbounds {
		outbound := raw.(map[string]any)
		if tag, ok := outbound["tag"].(string); ok {
			if _, wanted := wantDefaults[tag]; wanted {
				policies[tag] = outbound
			}
		}
	}
	for tag, defaultChoice := range wantDefaults {
		policy := policies[tag]
		if policy == nil {
			t.Errorf("missing selector %s", tag)
			continue
		}
		if policy["default"] != defaultChoice {
			t.Errorf("%s default = %#v, want %q", tag, policy["default"], defaultChoice)
		}
		choices := policy["outbounds"].([]string)
		if tag == aiPolicy {
			continue
		}
		for _, want := range []string{globalSmartTag, "❓ 未识别 Smart", "🇭🇰 香港 Smart"} {
			if !slices.Contains(choices, want) {
				t.Errorf("%s does not expose %s: %#v", tag, want, choices)
			}
		}
	}
	if got := policies[aiPolicy]["outbounds"].([]string); !slices.Equal(got, []string{aiFallbackTag, "❓ 未识别 Smart", directTag}) {
		t.Fatalf("AI choices = %#v", got)
	}
	if got := policies[adsPolicy]["outbounds"].([]string); !slices.Equal(got, []string{rejectTag, directTag, baselineTag, globalSmartTag, "❓ 未识别 Smart", "🇭🇰 香港 Smart"}) {
		t.Fatalf("ads choices = %#v", got)
	}

	b, err := json.Marshal(profile)
	if err != nil {
		t.Fatal(err)
	}
	s := string(b)
	for _, want := range []string{globalSmartTag, "🇭🇰 香港 Smart", "❓ 未识别 Smart", aiPolicy, baselineTag, developerPolicy, adsPolicy} {
		if !strings.Contains(s, want) {
			t.Fatalf("missing %s", want)
		}
	}
	if !strings.Contains(s, `"final":"`+baselineTag+`"`) {
		t.Fatal("ordinary traffic must use the baseline Smart selector")
	}
	experimental := profile["experimental"].(map[string]any)
	if experimental["clash_api"] == nil || experimental["cache_file"].(map[string]any)["enabled"] != true {
		t.Fatal("traffic manager and persistent rule-set cache must be enabled")
	}
	rules := profile["route"].(map[string]any)["rules"].([]any)
	if rules[0].(map[string]any)["action"] != "sniff" {
		t.Fatal("the first route rule must sniff domains before policy matching")
	}
	if strings.Contains(s, "provider.example") || strings.Contains(s, "subscription") || strings.Contains(s, "github.com/DustinWin") || strings.Contains(s, "raw.githubusercontent.com") {
		t.Fatal("profile leaked provider metadata")
	}
	ruleSets := profile["route"].(map[string]any)["rule_set"].([]any)
	if len(ruleSets) != 1 {
		t.Fatalf("rule-set definitions = %d, want one templated mirror definition", len(ruleSets))
	}
	definition := ruleSets[0].(map[string]any)
	if definition["url"] != testRuleBaseURL+"/{tag}.srs" {
		t.Fatalf("rule-set URL = %#v", definition["url"])
	}
	if client := definition["http_client"].(map[string]any); client["detour"] != directTag {
		t.Fatalf("rule sets must use the explicit direct HTTP client, got %#v", client)
	}
	if tags := definition["tag"].([]string); len(tags) != len(requiredRuleSets) {
		t.Fatalf("mirrored tags = %d, want %d", len(tags), len(requiredRuleSets))
	}
}

func TestProfileHasDefaultAndEnergySavingModes(t *testing.T) {
	profile := buildProfile(nil, testRuleBaseURL)
	b, err := json.Marshal(profile)
	if err != nil {
		t.Fatal(err)
	}
	s := string(b)
	for _, mode := range []string{"Direct", "Global", "节能"} {
		if !strings.Contains(s, `"clash_mode":"`+mode+`"`) {
			t.Fatalf("missing clash_mode rules for %s", mode)
		}
	}

	rules := profile["route"].(map[string]any)["rules"].([]any)
	directIndex := -1
	globalIndex := -1
	energyDirectIndex := -1
	streamingIndex := -1
	for i, raw := range rules {
		rule := raw.(map[string]any)
		if rule["clash_mode"] == clashModeDirect && rule["action"] == "route" && rule["outbound"] == "DIRECT" && len(rule) == 3 {
			directIndex = i
		}
		if rule["clash_mode"] == clashModeGlobal && rule["action"] == "route" && rule["outbound"] == "🚀 全局 Smart" && len(rule) == 3 {
			globalIndex = i
		}
		if rule["clash_mode"] == energySavingMode && rule["action"] == "route" && rule["outbound"] == "DIRECT" && len(rule) == 3 {
			energyDirectIndex = i
		}
		if rule["action"] == "route" && rule["outbound"] == mediaPolicy {
			streamingIndex = i
		}
	}
	if directIndex < 0 || globalIndex < 0 {
		t.Fatal("missing Direct/Global catch-all route rules")
	}
	if directIndex > globalIndex {
		t.Fatal("Direct mode catch-all must come before Global mode catch-all")
	}
	if energyDirectIndex < 0 {
		t.Fatal("missing energy-saving direct catch-all rule")
	}
	if streamingIndex >= 0 && energyDirectIndex > streamingIndex {
		t.Fatal("energy-saving catch-all must appear before streaming proxy rules so non-essential traffic stays direct")
	}

	dnsRules := profile["dns"].(map[string]any)["rules"].([]any)
	foundDirectDNS := false
	foundGlobalDNS := false
	foundLocalFallback := false
	foundRemoteWhitelist := false
	for _, raw := range dnsRules {
		rule := raw.(map[string]any)
		switch rule["clash_mode"] {
		case clashModeDirect:
			if rule["server"] == "local" && len(rule) == 3 {
				foundDirectDNS = true
			}
		case clashModeGlobal:
			if rule["server"] == "global-dns" && len(rule) == 3 {
				foundGlobalDNS = true
			}
		case energySavingMode:
			if rule["server"] == "local" && len(rule) == 3 {
				foundLocalFallback = true
			}
			if (rule["server"] == "ai-dns" || rule["server"] == "telegram-dns") && rule["rule_set"] != nil {
				foundRemoteWhitelist = true
			}
		}
	}
	if !foundDirectDNS || !foundGlobalDNS {
		t.Fatal("Direct/Global modes must use local/remote DNS respectively")
	}
	if !foundLocalFallback || !foundRemoteWhitelist {
		t.Fatal("energy-saving mode must use local DNS by default and remote DNS for proxied essentials")
	}
}

func TestProfileUsesPlatformDNSForNodeBootstrap(t *testing.T) {
	profile := buildProfile(nil, testRuleBaseURL)
	dns := profile["dns"].(map[string]any)
	servers := dns["servers"].([]any)
	if dns["strategy"] != "ipv4_only" {
		t.Fatalf("DNS strategy = %#v, want ipv4_only for proxy compatibility", dns["strategy"])
	}

	var local map[string]any
	for _, raw := range servers {
		server := raw.(map[string]any)
		if server["tag"] == "local" {
			local = server
		}
	}
	if local == nil {
		t.Fatal("missing local bootstrap DNS server")
	}
	if local["type"] != "local" {
		t.Fatalf("bootstrap DNS type = %#v, want local", local["type"])
	}
	if _, exists := local["server"]; exists {
		t.Fatalf("platform bootstrap DNS must not override the network DNS server: %#v", local)
	}
	if _, exists := local["server_port"]; exists {
		t.Fatalf("platform bootstrap DNS must not override the network DNS port: %#v", local)
	}
	if _, exists := local["detour"]; exists {
		t.Fatalf("bootstrap DNS must stay on the platform network, got detour %#v", local["detour"])
	}
}
