package main

import (
	"maps"
	"slices"
	"testing"
)

func TestProfileReferencesAreComplete(t *testing.T) {
	profile := buildProfile([]Node{{
		Tag: "node", RegionKey: "🇯🇵",
		Outbound: map[string]any{"type": "socks", "tag": "node", "server": "192.0.2.1", "server_port": 1080},
	}}, testRuleBaseURL)

	outboundTags := make(map[string]bool)
	for _, raw := range profile["outbounds"].([]any) {
		outbound := raw.(map[string]any)
		tag, _ := outbound["tag"].(string)
		if tag == "" || outboundTags[tag] {
			t.Fatalf("missing or duplicate outbound tag %q", tag)
		}
		outboundTags[tag] = true
		if tag == directTag && outbound["domain_resolver"] != "local" {
			t.Errorf("DIRECT must be non-empty and use local DNS so rule-set HTTP detours can use it")
		}
	}
	for _, raw := range profile["outbounds"].([]any) {
		outbound := raw.(map[string]any)
		for _, referenced := range stringsFrom(outbound["outbounds"]) {
			if !outboundTags[referenced] {
				t.Errorf("outbound %s references missing outbound %s", outbound["tag"], referenced)
			}
		}
	}

	route := profile["route"].(map[string]any)
	definedRuleSets := make(map[string]bool)
	for _, raw := range route["rule_set"].([]any) {
		for _, tag := range stringsFrom(raw.(map[string]any)["tag"]) {
			if definedRuleSets[tag] {
				t.Errorf("duplicate rule-set tag %s", tag)
			}
			definedRuleSets[tag] = true
		}
	}
	if len(definedRuleSets) != len(requiredRuleSets) {
		t.Fatalf("defined rule sets = %d, want %d", len(definedRuleSets), len(requiredRuleSets))
	}
	for _, raw := range route["rules"].([]any) {
		rule := raw.(map[string]any)
		if outbound, ok := rule["outbound"].(string); ok && !outboundTags[outbound] {
			t.Errorf("route rule references missing outbound %s", outbound)
		}
		assertRuleSetsDefined(t, rule, definedRuleSets)
	}
	if final := route["final"].(string); !outboundTags[final] {
		t.Errorf("route final references missing outbound %s", final)
	}

	dns := profile["dns"].(map[string]any)
	if _, deprecated := dns["independent_cache"]; deprecated {
		t.Fatal("independent_cache is unnecessary and deprecated in core 1.14")
	}
	dnsServers := make(map[string]bool)
	for _, raw := range dns["servers"].([]any) {
		server := raw.(map[string]any)
		tag := server["tag"].(string)
		if dnsServers[tag] {
			t.Errorf("duplicate DNS server %s", tag)
		}
		dnsServers[tag] = true
		if detour, ok := server["detour"].(string); ok && !outboundTags[detour] {
			t.Errorf("DNS server %s references missing outbound %s", tag, detour)
		}
	}
	for _, raw := range dns["rules"].([]any) {
		rule := raw.(map[string]any)
		if server, ok := rule["server"].(string); ok && !dnsServers[server] {
			t.Errorf("DNS rule references missing server %s", server)
		}
		assertRuleSetsDefined(t, rule, definedRuleSets)
	}
	if final := dns["final"].(string); !dnsServers[final] {
		t.Errorf("DNS final references missing server %s", final)
	}
}

func TestSmartScoreIdentitiesIsolateOnlyChangedNode(t *testing.T) {
	first := Node{
		Tag: "node-a", RegionKey: "🇯🇵",
		Outbound: map[string]any{
			"type": "socks", "tag": "node-a", "server": "192.0.2.1",
			"server_port": 1080, "password": "first-secret",
		},
	}
	second := Node{
		Tag: "node-b", RegionKey: "🇸🇬",
		Outbound: map[string]any{
			"type": "socks", "tag": "node-b", "server": "192.0.2.2",
			"server_port": 1080, "password": "second-secret",
		},
	}

	forward := nodeScoreIdentities([]Node{first, second})
	reversed := nodeScoreIdentities([]Node{second, first})
	if !maps.Equal(forward, reversed) {
		t.Fatalf("node reordering changed score identities: %#v != %#v", forward, reversed)
	}
	changed := second
	changed.Outbound = map[string]any{
		"type": "socks", "tag": "node-b", "server": "198.51.100.8",
		"server_port": 1080, "password": "second-secret",
	}
	changedIdentities := nodeScoreIdentities([]Node{first, changed})
	if changedIdentities["node-a"] != forward["node-a"] {
		t.Fatal("changing node-b discarded node-a score identity")
	}
	if changedIdentities["node-b"] == forward["node-b"] {
		t.Fatal("endpoint change retained node-b's stale score identity")
	}

	profile := buildProfile([]Node{first, second}, testRuleBaseURL)
	for _, raw := range profile["outbounds"].([]any) {
		outbound := raw.(map[string]any)
		if outbound["type"] != "smart" {
			continue
		}
		if outbound["score_namespace"] != smartScoreNamespace {
			t.Fatalf("Smart %q namespace = %q, want %q", outbound["tag"], outbound["score_namespace"], smartScoreNamespace)
		}
		identities := outbound["score_identities"].(map[string]string)
		for _, tag := range outbound["outbounds"].([]string) {
			if expected := forward[tag]; expected != "" && identities[tag] != expected {
				t.Fatalf("Smart %q identity for %q = %q, want %q", outbound["tag"], tag, identities[tag], expected)
			}
		}
	}
}

func TestPolicyOrderAndSharedSmartPools(t *testing.T) {
	profile := buildProfile([]Node{{
		Tag: "node", RegionKey: "🇭🇰",
		Outbound: map[string]any{"type": "socks", "tag": "node", "server": "192.0.2.1", "server_port": 1080},
	}}, testRuleBaseURL)
	expectedPolicies := []string{
		aiPolicy, telegramPolicy, netflixPolicy, disneyPolicy, maxPolicy, primeVideoPolicy,
		appleTVPolicy, youtubePolicy, tiktokPolicy, douyinPolicy, bilibiliPolicy, spotifyPolicy,
		mediaPolicy,
		socialPolicy, gamePolicy, githubPolicy, developerPolicy, applePolicy, microsoftPolicy,
		googlePolicy, networkTestPolicy, downloadPolicy, cnDomainPolicy, cnIPPolicy, adsPolicy,
	}
	var actualPolicies []string
	var smartTags []string
	for _, raw := range profile["outbounds"].([]any) {
		outbound := raw.(map[string]any)
		tag, _ := outbound["tag"].(string)
		switch outbound["type"] {
		case "selector":
			if tag != baselineTag {
				actualPolicies = append(actualPolicies, tag)
			}
		case "smart":
			smartTags = append(smartTags, tag)
		}
	}
	if !slices.Equal(actualPolicies, expectedPolicies) {
		t.Fatalf("policy order = %#v, want %#v", actualPolicies, expectedPolicies)
	}
	for _, tag := range []string{globalSmartTag, "🇭🇰 香港 Smart", aiFallbackTag, mediaFallbackTag, telegramFallbackTag, gameFallbackTag} {
		if !slices.Contains(smartTags, tag) {
			t.Errorf("missing shared Smart pool %s", tag)
		}
	}
	if len(smartTags) != 6 {
		t.Fatalf("Smart pool count = %d, want 6 with one region; per-service pools would duplicate probes", len(smartTags))
	}
	if got := outboundByTag(t, profile, aiFallbackTag)["outbounds"].([]string); !slices.Equal(got, []string{directTag}) {
		t.Fatalf("AI Fallback with only Hong Kong nodes = %#v, want DIRECT", got)
	}
}

func TestAIRoutingExcludesHongKong(t *testing.T) {
	tests := []struct {
		name  string
		nodes []Node
		want  []string
	}{
		{
			name: "preferred regions",
			nodes: []Node{
				{Tag: "hk", RegionKey: hongKongRegionKey, Outbound: map[string]any{"type": "socks", "tag": "hk"}},
				{Tag: "jp", RegionKey: "🇯🇵", Outbound: map[string]any{"type": "socks", "tag": "jp"}},
				{Tag: "au", RegionKey: "🇦🇺", Outbound: map[string]any{"type": "socks", "tag": "au"}},
			},
			want: []string{"jp"},
		},
		{
			name: "non preferred last resort",
			nodes: []Node{
				{Tag: "hk", RegionKey: hongKongRegionKey, Outbound: map[string]any{"type": "socks", "tag": "hk"}},
				{Tag: "au", RegionKey: "🇦🇺", Outbound: map[string]any{"type": "socks", "tag": "au"}},
			},
			want: []string{"au"},
		},
		{
			name: "only Hong Kong fails closed",
			nodes: []Node{
				{Tag: "hk", RegionKey: hongKongRegionKey, Outbound: map[string]any{"type": "socks", "tag": "hk"}},
			},
			want: []string{directTag},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			profile := buildProfile(test.nodes, testRuleBaseURL)
			fallback := outboundByTag(t, profile, aiFallbackTag)
			if got := fallback["outbounds"].([]string); !slices.Equal(got, test.want) {
				t.Fatalf("AI Fallback outbounds = %#v, want %#v", got, test.want)
			}
			if slices.Contains(fallback["outbounds"].([]string), "hk") {
				t.Fatal("AI Fallback must never contain a Hong Kong node")
			}

			selector := outboundByTag(t, profile, aiPolicy)
			if selector["default"] != aiFallbackTag {
				t.Fatalf("AI default = %#v, want %q", selector["default"], aiFallbackTag)
			}
			choices := selector["outbounds"].([]string)
			if slices.Contains(choices, "🇭🇰 香港 Smart") {
				t.Fatalf("AI choices expose Hong Kong: %#v", choices)
			}
		})
	}
}

func TestTelegramRoutingUsesServiceAwareFallback(t *testing.T) {
	profile := buildProfile([]Node{
		{Tag: "hk", RegionKey: hongKongRegionKey, Outbound: map[string]any{"type": "socks", "tag": "hk"}},
		{Tag: "jp", RegionKey: "🇯🇵", Outbound: map[string]any{"type": "socks", "tag": "jp"}},
		{Tag: "au", RegionKey: "🇦🇺", Outbound: map[string]any{"type": "socks", "tag": "au"}},
	}, testRuleBaseURL)
	fallback := outboundByTag(t, profile, telegramFallbackTag)
	if got := fallback["outbounds"].([]string); !slices.Equal(got, []string{"jp"}) {
		t.Fatalf("Telegram Fallback outbounds = %#v, want only preferred non-Hong-Kong nodes", got)
	}
	if fallback["url"] != telegramTestURL {
		t.Fatalf("Telegram Fallback probe URL = %#v, want %q", fallback["url"], telegramTestURL)
	}
	selector := outboundByTag(t, profile, telegramPolicy)
	if selector["default"] != telegramFallbackTag {
		t.Fatalf("Telegram default = %#v, want %q", selector["default"], telegramFallbackTag)
	}
	if !slices.Contains(selector["outbounds"].([]string), "🇭🇰 香港 Smart") {
		t.Fatal("Telegram selector must keep Hong Kong available for manual selection")
	}

	hongKongOnly := buildProfile([]Node{
		{Tag: "hk", RegionKey: hongKongRegionKey, Outbound: map[string]any{"type": "socks", "tag": "hk"}},
	}, testRuleBaseURL)
	if got := outboundByTag(t, hongKongOnly, telegramFallbackTag)["outbounds"].([]string); !slices.Equal(got, []string{"hk"}) {
		t.Fatalf("Telegram Hong-Kong-only last resort = %#v, want [hk]", got)
	}
}

func TestPolicyDNSUsesMatchingOutbound(t *testing.T) {
	profile := buildProfile(nil, testRuleBaseURL)
	expected := map[string]string{
		"global-dns":       globalSmartTag,
		"baseline-dns":     baselineTag,
		"ai-dns":           aiPolicy,
		"telegram-dns":     telegramPolicy,
		"netflix-dns":      netflixPolicy,
		"disney-dns":       disneyPolicy,
		"max-dns":          maxPolicy,
		"prime-video-dns":  primeVideoPolicy,
		"apple-tv-dns":     appleTVPolicy,
		"youtube-dns":      youtubePolicy,
		"tiktok-dns":       tiktokPolicy,
		"douyin-dns":       douyinPolicy,
		"bilibili-dns":     bilibiliPolicy,
		"spotify-dns":      spotifyPolicy,
		"media-dns":        mediaPolicy,
		"social-dns":       socialPolicy,
		"game-dns":         gamePolicy,
		"github-dns":       githubPolicy,
		"developer-dns":    developerPolicy,
		"apple-dns":        applePolicy,
		"microsoft-dns":    microsoftPolicy,
		"google-dns":       googlePolicy,
		"network-test-dns": networkTestPolicy,
		"download-dns":     downloadPolicy,
		"cn-domain-dns":    cnDomainPolicy,
	}
	for _, raw := range profile["dns"].(map[string]any)["servers"].([]any) {
		server := raw.(map[string]any)
		tag := server["tag"].(string)
		want, exists := expected[tag]
		if !exists {
			continue
		}
		if server["detour"] != want {
			t.Errorf("DNS server %s detour = %#v, want %s", tag, server["detour"], want)
		}
		delete(expected, tag)
	}
	if len(expected) > 0 {
		t.Fatalf("missing policy DNS servers: %#v", expected)
	}
}

func TestAdsDNSDoesNotUseRejectSelector(t *testing.T) {
	dns := buildDNS()
	for _, raw := range dns["servers"].([]any) {
		server := raw.(map[string]any)
		if server["detour"] == adsPolicy {
			t.Fatal("ad DNS must not detour through the REJECT-capable selector")
		}
	}

	for _, raw := range dns["rules"].([]any) {
		rule := raw.(map[string]any)
		if slices.Contains(stringsFrom(rule["rule_set"]), "ads") {
			if rule["server"] != "baseline-dns" {
				t.Fatalf("ad DNS rule server = %#v, want baseline-dns", rule["server"])
			}
			return
		}
	}
	t.Fatal("missing ad DNS rule")
}

func TestDouyinPrecedesAdsAndOverlappingTikTokRules(t *testing.T) {
	dnsDouyin, dnsAds, dnsTikTok := -1, -1, -1
	dnsPackage := -1
	for index, raw := range buildDNS()["rules"].([]any) {
		rule := raw.(map[string]any)
		sets := stringsFrom(rule["rule_set"])
		switch {
		case slices.Contains(sets, "douyin") && rule["clash_mode"] == nil:
			dnsDouyin = index
		case slices.Contains(sets, "ads"):
			dnsAds = index
		case slices.Contains(sets, "tiktok"):
			dnsTikTok = index
		}
		if slices.Contains(stringsFrom(rule["package_name"]), "com.ss.android.ugc.aweme") && rule["clash_mode"] == nil {
			dnsPackage = index
		}
	}
	if dnsDouyin < 0 || dnsPackage < 0 || dnsAds < 0 || dnsTikTok < 0 || dnsDouyin >= dnsAds || dnsPackage >= dnsAds || dnsDouyin >= dnsTikTok {
		t.Fatalf("invalid DNS priority: package=%d douyin=%d ads=%d tiktok=%d", dnsPackage, dnsDouyin, dnsAds, dnsTikTok)
	}

	routeDouyin, routeAds, routeTikTok := -1, -1, -1
	routePackage := -1
	for index, raw := range detailedRules() {
		rule := raw.(map[string]any)
		sets := stringsFrom(rule["rule_set"])
		switch {
		case slices.Contains(sets, "douyin") && rule["clash_mode"] == nil:
			routeDouyin = index
		case slices.Contains(sets, "ads"):
			routeAds = index
		case slices.Contains(sets, "tiktok"):
			routeTikTok = index
		}
		if slices.Contains(stringsFrom(rule["package_name"]), "com.ss.android.ugc.aweme") && rule["clash_mode"] == nil {
			routePackage = index
		}
	}
	if routeDouyin < 0 || routePackage < 0 || routeAds < 0 || routeTikTok < 0 || routeDouyin >= routeAds || routePackage >= routeAds || routeDouyin >= routeTikTok {
		t.Fatalf("invalid route priority: package=%d douyin=%d ads=%d tiktok=%d", routePackage, routeDouyin, routeAds, routeTikTok)
	}
}

func outboundByTag(t *testing.T, profile map[string]any, tag string) map[string]any {
	t.Helper()
	for _, raw := range profile["outbounds"].([]any) {
		outbound := raw.(map[string]any)
		if outbound["tag"] == tag {
			return outbound
		}
	}
	t.Fatalf("missing outbound %s", tag)
	return nil
}

func TestEnergyDNSApplicationFallbackPrecedesCatchAll(t *testing.T) {
	rules := buildDNS()["rules"].([]any)
	catchAll := -1
	aiPackage := -1
	telegramPackage := -1
	gamesCN := -1
	for index, raw := range rules {
		rule := raw.(map[string]any)
		if rule["clash_mode"] == energySavingMode && rule["server"] == "local" && len(rule) == 3 {
			catchAll = index
		}
		if rule["clash_mode"] == energySavingMode && rule["package_name"] != nil {
			switch rule["server"] {
			case "ai-dns":
				aiPackage = index
			case "telegram-dns":
				telegramPackage = index
			}
		}
		if slices.Contains(stringsFrom(rule["rule_set"]), "games-cn") && rule["server"] == "download-dns" {
			gamesCN = index
		}
	}
	if catchAll < 0 || aiPackage < 0 || telegramPackage < 0 || aiPackage >= catchAll || telegramPackage >= catchAll {
		t.Fatalf("energy DNS order is invalid: AI=%d Telegram=%d catch-all=%d", aiPackage, telegramPackage, catchAll)
	}
	if gamesCN < 0 {
		t.Fatal("games-cn DNS must follow the download policy")
	}
}

func TestDetailedRoutePriority(t *testing.T) {
	rules := detailedRules()
	index := func(outbound, ruleSet string) int {
		for i, raw := range rules {
			rule := raw.(map[string]any)
			if rule["clash_mode"] == nil && rule["outbound"] == outbound && slices.Contains(stringsFrom(rule["rule_set"]), ruleSet) {
				return i
			}
		}
		return -1
	}
	resolveIndex := -1
	energyCatchAll := -1
	for i, raw := range rules {
		rule := raw.(map[string]any)
		if rule["action"] == "resolve" {
			resolveIndex = i
		}
		if rule["clash_mode"] == energySavingMode && rule["outbound"] == directTag && len(rule) == 3 {
			energyCatchAll = i
		}
	}

	adsIndex := index(adsPolicy, "ads")
	aiIndex := index(aiPolicy, "ai")
	mediaIndex := index(mediaPolicy, "media")
	telegramIPIndex := index(telegramPolicy, "telegram-ip")
	cnIPIndex := index(cnIPPolicy, "cn-ip")
	if adsIndex < 0 || aiIndex < 0 || energyCatchAll < 0 || mediaIndex < 0 || resolveIndex < 0 || telegramIPIndex < 0 || cnIPIndex < 0 {
		t.Fatalf("missing priority marker: ads=%d ai=%d energy=%d media=%d resolve=%d telegramIP=%d cnIP=%d", adsIndex, aiIndex, energyCatchAll, mediaIndex, resolveIndex, telegramIPIndex, cnIPIndex)
	}
	if !(adsIndex < energyCatchAll && energyCatchAll < aiIndex && aiIndex < mediaIndex && mediaIndex < resolveIndex && resolveIndex < telegramIPIndex && telegramIPIndex < cnIPIndex) {
		t.Fatal("route rules do not preserve mode, domain, resolve and IP priority")
	}
	for _, pair := range [][4]string{
		{douyinPolicy, "douyin", tiktokPolicy, "tiktok"},
		{appleTVPolicy, "apple-tv", applePolicy, "apple"},
		{youtubePolicy, "youtube", googlePolicy, "google"},
		{bilibiliPolicy, "bilibili", mediaPolicy, "media"},
		{cnDomainPolicy, "apple-cn", applePolicy, "apple"},
	} {
		if specific, broad := index(pair[0], pair[1]), index(pair[2], pair[3]); specific < 0 || broad < 0 || specific >= broad {
			t.Errorf("specific rule %s/%s must precede %s/%s", pair[0], pair[1], pair[2], pair[3])
		}
	}
}

func assertRuleSetsDefined(t *testing.T, rule map[string]any, defined map[string]bool) {
	t.Helper()
	for _, tag := range stringsFrom(rule["rule_set"]) {
		if !defined[tag] {
			t.Errorf("rule references undefined rule set %s", tag)
		}
	}
}

func stringsFrom(value any) []string {
	switch values := value.(type) {
	case []string:
		return values
	case []any:
		result := make([]string, 0, len(values))
		for _, value := range values {
			if text, ok := value.(string); ok {
				result = append(result, text)
			}
		}
		return result
	default:
		return nil
	}
}
