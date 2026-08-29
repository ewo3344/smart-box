package main

import (
	"bytes"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"sync/atomic"
	"testing"
	"time"
)

func requireCore(t *testing.T) string {
	t.Helper()
	if path := os.Getenv("SMART_BOX_CORE"); path != "" {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("SMART_BOX_CORE=%s is not usable: %v", path, err)
		}
		return path
	}
	const installed = "/usr/local/lib/smart-box/smart-box-core"
	if info, err := os.Stat(installed); err == nil && !info.IsDir() {
		return installed
	}
	t.Skip("set SMART_BOX_CORE to run the core compatibility check")
	return ""
}

func TestGeneratedProfileAcceptedByCore(t *testing.T) {
	corePath := requireCore(t)

	tests := map[string][]Node{
		"mixed regions": {
			{Tag: "hk", RegionKey: hongKongRegionKey, Outbound: map[string]any{"type": "socks", "tag": "hk", "server": "192.0.2.1", "server_port": 1080}},
			{Tag: "unknown", RegionKey: "unknown", Outbound: map[string]any{"type": "socks", "tag": "unknown", "server": "192.0.2.2", "server_port": 1080}},
		},
		"only Hong Kong": {
			{Tag: "hk", RegionKey: hongKongRegionKey, Outbound: map[string]any{"type": "socks", "tag": "hk", "server": "192.0.2.1", "server_port": 1080}},
		},
	}
	for name, nodes := range tests {
		t.Run(name, func(t *testing.T) {
			content, err := json.Marshal(buildProfile(nodes, testRuleBaseURL))
			if err != nil {
				t.Fatal(err)
			}
			configPath := filepath.Join(t.TempDir(), "profile.json")
			if err := os.WriteFile(configPath, content, 0o600); err != nil {
				t.Fatal(err)
			}

			output, err := exec.Command(corePath, "check", "-c", configPath).CombinedOutput()
			if err != nil {
				t.Fatalf("core rejected generated profile: %v\n%s", err, output)
			}
		})
	}
}

func TestGeneratedProfileStartsWithRemoteRuleSets(t *testing.T) {
	corePath := requireCore(t)

	directory := t.TempDir()
	sourcePath := filepath.Join(directory, "rule-set.json")
	binaryPath := filepath.Join(directory, "rule-set.srs")
	source := []byte(`{"version":5,"rules":[{"domain_suffix":["smart-box-validation.invalid"]}]}`)
	if err := os.WriteFile(sourcePath, source, 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command(corePath, "rule-set", "compile", sourcePath, "-o", binaryPath).CombinedOutput(); err != nil {
		t.Fatalf("compile rule-set fixture: %v\n%s", err, output)
	}
	ruleContent, err := os.ReadFile(binaryPath)
	if err != nil {
		t.Fatal(err)
	}

	var requestCount atomic.Int32
	ruleServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requestCount.Add(1)
		w.Header().Set("Content-Type", "application/octet-stream")
		_, _ = w.Write(ruleContent)
	}))
	defer ruleServer.Close()

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	listenPort := listener.Addr().(*net.TCPAddr).Port
	_ = listener.Close()

	nodes := []Node{{
		Tag: "test-node", RegionKey: "🇯🇵",
		Outbound: map[string]any{"type": "socks", "tag": "test-node", "server": "192.0.2.1", "server_port": 1080},
	}}
	profile := buildProfile(nodes, ruleServer.URL+"/rule-set/test-private-token-123456789")
	profile["inbounds"] = []any{map[string]any{
		"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": listenPort,
	}}
	profile["experimental"].(map[string]any)["cache_file"] = map[string]any{
		"enabled": true, "path": filepath.Join(directory, "cache.db"), "cache_id": "startup-test",
	}
	content, err := json.Marshal(profile)
	if err != nil {
		t.Fatal(err)
	}
	configPath := filepath.Join(directory, "profile.json")
	if err := os.WriteFile(configPath, content, 0o600); err != nil {
		t.Fatal(err)
	}

	command := exec.Command(corePath, "run", "-c", configPath)
	var output bytes.Buffer
	command.Stdout = &output
	command.Stderr = &output
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	wait := make(chan error, 1)
	go func() { wait <- command.Wait() }()
	stopped := false
	defer func() {
		if stopped {
			return
		}
		_ = command.Process.Signal(os.Interrupt)
		select {
		case <-wait:
		case <-time.After(5 * time.Second):
			_ = command.Process.Kill()
			<-wait
		}
	}()

	deadline := time.NewTimer(15 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(25 * time.Millisecond)
	defer ticker.Stop()
	for requestCount.Load() < int32(len(requiredRuleSets)) {
		select {
		case err := <-wait:
			stopped = true
			t.Fatalf("core exited during remote rule-set initialization: %v\n%s", err, output.String())
		case <-deadline.C:
			t.Fatalf("core loaded %d/%d remote rule sets before timeout\n%s", requestCount.Load(), len(requiredRuleSets), output.String())
		case <-ticker.C:
		}
	}

	select {
	case err := <-wait:
		stopped = true
		t.Fatalf("core exited after loading remote rule sets: %v\n%s", err, output.String())
	case <-time.After(500 * time.Millisecond):
	}
	t.Logf("core stayed running after loading %d remote rule sets on port %d", requestCount.Load(), listenPort)
}

func TestHighImpactRoutePrioritiesAcceptedByCore(t *testing.T) {
	corePath := requireCore(t)
	nodes := []Node{
		{Tag: "hk-node", RegionKey: hongKongRegionKey, Outbound: map[string]any{"type": "socks", "tag": "hk-node", "server": "192.0.2.1", "server_port": 1080}},
		{Tag: "jp-node", RegionKey: "🇯🇵", Outbound: map[string]any{"type": "socks", "tag": "jp-node", "server": "192.0.2.2", "server_port": 1080}},
		{Tag: "sg-node", RegionKey: "🇸🇬", Outbound: map[string]any{"type": "socks", "tag": "sg-node", "server": "192.0.2.3", "server_port": 1080}},
		{Tag: "us-node", RegionKey: "🇺🇸", Outbound: map[string]any{"type": "socks", "tag": "us-node", "server": "192.0.2.4", "server_port": 1080}},
	}

	content, err := json.Marshal(buildProfile(nodes, testRuleBaseURL))
	if err != nil {
		t.Fatal(err)
	}
	configPath := filepath.Join(t.TempDir(), "profile.json")
	if err := os.WriteFile(configPath, content, 0o600); err != nil {
		t.Fatal(err)
	}

	output, err := exec.Command(corePath, "check", "--disable-color", "-c", configPath).CombinedOutput()
	if err != nil {
		t.Fatalf("core rejected generated profile: %v\n%s", err, output)
	}

	checked, err := os.ReadFile(configPath)
	if err != nil {
		t.Fatal(err)
	}
	var accepted map[string]any
	if err := json.Unmarshal(checked, &accepted); err != nil {
		t.Fatalf("decode checked profile: %v", err)
	}

	assertHighImpactRoutePriorities(t, accepted)
}

func assertHighImpactRoutePriorities(t *testing.T, profile map[string]any) {
	t.Helper()

	selectors := map[string]map[string]any{}
	fallbacks := map[string]map[string]any{}
	rawOutbounds, ok := profile["outbounds"].([]any)
	if !ok {
		t.Fatal("checked profile is missing outbounds")
	}
	for _, raw := range rawOutbounds {
		outbound, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		tag, _ := outbound["tag"].(string)
		switch outbound["type"] {
		case "selector":
			selectors[tag] = outbound
		case "smart":
			fallbacks[tag] = outbound
		}
	}

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
	for tag, want := range wantDefaults {
		selector := selectors[tag]
		if selector == nil {
			t.Errorf("missing selector %s", tag)
			continue
		}
		if selector["default"] != want {
			t.Errorf("%s default = %#v, want %q", tag, selector["default"], want)
		}
	}

	aiFallback := fallbacks[aiFallbackTag]
	if aiFallback == nil {
		t.Fatal("missing AI Fallback")
	}
	aiNodes := stringsFrom(aiFallback["outbounds"])
	if slices.Contains(aiNodes, "hk-node") || slices.Contains(aiNodes, "🇭🇰 香港 Smart") {
		t.Fatalf("AI Fallback includes Hong Kong: %#v", aiNodes)
	}
	if len(aiNodes) == 0 {
		t.Fatal("AI Fallback has no outbounds")
	}
	aiChoices := stringsFrom(selectors[aiPolicy]["outbounds"])
	if slices.Contains(aiChoices, "🇭🇰 香港 Smart") {
		t.Fatalf("AI selector exposes Hong Kong: %#v", aiChoices)
	}

	telegramFallback := fallbacks[telegramFallbackTag]
	if telegramFallback == nil {
		t.Fatal("missing Telegram Fallback")
	}
	if telegramFallback["url"] != telegramTestURL {
		t.Fatalf("Telegram Fallback probe URL = %#v, want %q", telegramFallback["url"], telegramTestURL)
	}
	if selectors[telegramPolicy]["default"] != telegramFallbackTag {
		t.Fatalf("Telegram default = %#v, want %q", selectors[telegramPolicy]["default"], telegramFallbackTag)
	}

	route, _ := profile["route"].(map[string]any)
	if route["final"] != baselineTag {
		t.Fatalf("route final = %#v, want %q", route["final"], baselineTag)
	}
	rules := mapsFrom(route["rules"])
	if len(rules) == 0 {
		t.Fatal("checked profile has no route rules")
	}

	directMode := firstRuleIndex(rules, func(rule map[string]any) bool {
		return rule["clash_mode"] == clashModeDirect && rule["outbound"] == directTag
	})
	globalMode := firstRuleIndex(rules, func(rule map[string]any) bool {
		return rule["clash_mode"] == clashModeGlobal && rule["outbound"] == globalSmartTag
	})
	privateIP := firstRuleIndex(rules, func(rule map[string]any) bool {
		return rule["ip_is_private"] == true && rule["outbound"] == directTag
	})
	localDomain := firstRuleIndex(rules, func(rule map[string]any) bool {
		return rule["outbound"] == directTag && containsAll(stringsFrom(rule["domain_suffix"]), "localhost", "local", "lan", "home.arpa")
	})
	privateSet := firstRuleIndex(rules, func(rule map[string]any) bool {
		return rule["outbound"] == directTag && slices.Contains(stringsFrom(rule["rule_set"]), "private")
	})
	douyin := firstRuleIndex(rules, func(rule map[string]any) bool {
		return rule["clash_mode"] == nil && rule["outbound"] == douyinPolicy && slices.Contains(stringsFrom(rule["rule_set"]), "douyin")
	})
	ads := firstRuleIndex(rules, func(rule map[string]any) bool {
		return rule["outbound"] == adsPolicy && slices.Contains(stringsFrom(rule["rule_set"]), "ads")
	})
	tiktok := firstRuleIndex(rules, func(rule map[string]any) bool {
		return rule["clash_mode"] == nil && rule["outbound"] == tiktokPolicy && slices.Contains(stringsFrom(rule["rule_set"]), "tiktok")
	})
	cnDomain := firstRuleIndex(rules, func(rule map[string]any) bool {
		return rule["outbound"] == cnDomainPolicy && slices.Contains(stringsFrom(rule["rule_set"]), "cn")
	})
	cnIP := firstRuleIndex(rules, func(rule map[string]any) bool {
		return rule["outbound"] == cnIPPolicy && slices.Contains(stringsFrom(rule["rule_set"]), "cn-ip")
	})

	if directMode < 0 || globalMode < 0 || privateIP < 0 || localDomain < 0 || privateSet < 0 {
		t.Fatalf("missing private/LAN DIRECT rules: direct=%d global=%d privateIP=%d local=%d privateSet=%d", directMode, globalMode, privateIP, localDomain, privateSet)
	}
	if douyin < 0 || ads < 0 || tiktok < 0 || cnDomain < 0 || cnIP < 0 {
		t.Fatalf("missing high-impact rules: douyin=%d ads=%d tiktok=%d cnDomain=%d cnIP=%d", douyin, ads, tiktok, cnDomain, cnIP)
	}
	if !(directMode < privateIP && globalMode < privateIP && privateIP < douyin && localDomain < douyin && privateSet < douyin) {
		t.Fatalf("private/LAN DIRECT must precede Douyin: direct=%d global=%d privateIP=%d local=%d privateSet=%d douyin=%d", directMode, globalMode, privateIP, localDomain, privateSet, douyin)
	}
	if !(douyin < ads && douyin < tiktok && ads < tiktok) {
		t.Fatalf("Douyin must precede ads and overseas TikTok: douyin=%d ads=%d tiktok=%d", douyin, ads, tiktok)
	}
	if !(ads < cnDomain && cnDomain < cnIP) {
		t.Fatalf("domestic domain must precede domestic IP: ads=%d cnDomain=%d cnIP=%d", ads, cnDomain, cnIP)
	}
}

func mapsFrom(value any) []map[string]any {
	raw, _ := value.([]any)
	result := make([]map[string]any, 0, len(raw))
	for _, item := range raw {
		if rule, ok := item.(map[string]any); ok {
			result = append(result, rule)
		}
	}
	return result
}

func firstRuleIndex(rules []map[string]any, match func(map[string]any) bool) int {
	for index, rule := range rules {
		if match(rule) {
			return index
		}
	}
	return -1
}

func containsAll(values []string, want ...string) bool {
	for _, item := range want {
		if !slices.Contains(values, item) {
			return false
		}
	}
	return true
}
