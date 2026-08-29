package main

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

const (
	energySavingMode = "节能"
	clashModeDirect  = "Direct"
	clashModeGlobal  = "Global"

	directTag           = "DIRECT"
	rejectTag           = "REJECT"
	globalSmartTag      = "🚀 全局 Smart"
	baselineTag         = "🎯 基准 Smart"
	aiFallbackTag       = "🤖 AI Fallback"
	mediaFallbackTag    = "📺 流媒体 Fallback"
	telegramFallbackTag = "✈️ Telegram Fallback"
	gameFallbackTag     = "🎮 游戏 Fallback"
	hongKongRegionKey   = "🇭🇰"
	defaultSmartTestURL = "https://www.gstatic.com/generate_204"
	telegramTestURL     = "https://telegram.org"
	smartScoreNamespace = "smart-box-nodes-v1"
)

const (
	aiPolicy          = "🤖 AI Smart"
	telegramPolicy    = "✈️ Telegram Smart"
	netflixPolicy     = "🎥 Netflix Smart"
	disneyPolicy      = "📽️ Disney+ Smart"
	maxPolicy         = "🎞️ Max Smart"
	primeVideoPolicy  = "🎬 Prime Video Smart"
	appleTVPolicy     = "🍎 Apple TV+ Smart"
	youtubePolicy     = "📹 YouTube Smart"
	tiktokPolicy      = "🎵 TikTok Smart"
	douyinPolicy      = "🇨🇳 抖音 Smart"
	bilibiliPolicy    = "📺 Bilibili Smart"
	spotifyPolicy     = "🎶 Spotify Smart"
	mediaPolicy       = "📺 流媒体 Smart"
	socialPolicy      = "💬 社交 Smart"
	gamePolicy        = "🎮 游戏 Smart"
	githubPolicy      = "🐙 GitHub Smart"
	developerPolicy   = "🧑‍💻 开发服务 Smart"
	applePolicy       = "🍎 Apple Smart"
	microsoftPolicy   = "🪟 Microsoft Smart"
	googlePolicy      = "🇬 Google Smart"
	networkTestPolicy = "📈 测速 Smart"
	downloadPolicy    = "⬇️ 下载策略"
	cnDomainPolicy    = "🇨🇳 国内域名策略"
	cnIPPolicy        = "🀄 国内 IP 策略"
	adsPolicy         = "🛡️ 广告策略"
)

type applicationMatch struct {
	Outbound  string
	DNSServer string
	Packages  []any
	Processes []any
}

var applicationMatches = []applicationMatch{
	{Outbound: aiPolicy, DNSServer: "ai-dns", Packages: []any{"com.openai.chatgpt", "com.anthropic.claude", "com.google.android.apps.bard", "ai.perplexity.app.android"}, Processes: []any{"ChatGPT.exe", "Claude.exe"}},
	{Outbound: telegramPolicy, DNSServer: "telegram-dns", Packages: []any{"org.telegram.messenger", "org.telegram.messenger.web", "org.thunderdog.challegram", "nekox.messenger"}, Processes: []any{"Telegram.exe"}},
	{Outbound: netflixPolicy, DNSServer: "netflix-dns", Packages: []any{"com.netflix.mediaclient"}},
	{Outbound: disneyPolicy, DNSServer: "disney-dns", Packages: []any{"com.disney.disneyplus"}},
	{Outbound: maxPolicy, DNSServer: "max-dns", Packages: []any{"com.wbd.stream", "com.hbo.hbonow"}},
	{Outbound: primeVideoPolicy, DNSServer: "prime-video-dns", Packages: []any{"com.amazon.avod.thirdpartyclient"}},
	{Outbound: youtubePolicy, DNSServer: "youtube-dns", Packages: []any{"com.google.android.youtube", "com.google.android.apps.youtube.music"}},
	{Outbound: tiktokPolicy, DNSServer: "tiktok-dns", Packages: []any{"com.zhiliaoapp.musically", "com.ss.android.ugc.trill"}},
	{Outbound: bilibiliPolicy, DNSServer: "bilibili-dns", Packages: []any{"tv.danmaku.bili", "com.bilibili.app.in"}},
	{Outbound: spotifyPolicy, DNSServer: "spotify-dns", Packages: []any{"com.spotify.music"}, Processes: []any{"Spotify.exe"}},
	{Outbound: socialPolicy, DNSServer: "social-dns", Packages: []any{"com.discord", "com.twitter.android", "com.facebook.katana", "com.instagram.android", "com.whatsapp"}, Processes: []any{"Discord.exe", "WhatsApp.exe"}},
	{Outbound: gamePolicy, DNSServer: "game-dns", Processes: []any{"steam.exe", "EpicGamesLauncher.exe", "Battle.net.exe", "RiotClientServices.exe"}},
	{Outbound: githubPolicy, DNSServer: "github-dns", Packages: []any{"com.github.android"}, Processes: []any{"GitHubDesktop.exe"}},
	{Outbound: networkTestPolicy, DNSServer: "network-test-dns", Packages: []any{"org.zwanoo.android.speedtest", "com.ookla.speedtest"}, Processes: []any{"Speedtest.exe"}},
}

var douyinPackages = []any{
	"com.ss.android.ugc.aweme",
	"com.ss.android.ugc.aweme.lite",
}

func buildProfile(nodes []Node, ruleBaseURL string) map[string]any {
	scoreIdentities := nodeScoreIdentities(nodes)
	groups := make(map[string][]string)
	for _, node := range nodes {
		groups[node.RegionKey] = append(groups[node.RegionKey], node.Tag)
	}
	for key := range groups {
		sort.Strings(groups[key])
	}

	outs := make([]any, 0, len(nodes)+48)
	for _, node := range nodes {
		outs = append(outs, node.Outbound)
	}
	outs = append(outs,
		map[string]any{"type": "direct", "tag": directTag, "domain_resolver": "local"},
		map[string]any{"type": "block", "tag": rejectTag},
	)

	regionSmart := make(map[string]string, len(groups))
	regionKeys := make([]string, 0, len(groups))
	for key := range groups {
		regionKeys = append(regionKeys, key)
	}
	sort.Strings(regionKeys)
	for _, key := range regionKeys {
		if len(groups[key]) == 0 {
			continue
		}
		tag := regionLabel(key) + " Smart"
		regionSmart[key] = tag
		outs = append(outs, smartOutbound(tag, groups[key]))
	}

	allTags := make([]string, 0, len(nodes))
	for _, node := range nodes {
		allTags = append(allTags, node.Tag)
	}
	sort.Strings(allTags)
	outs = append(outs, smartOutbound(globalSmartTag, allTags))

	aiTags := collectGroups(groups, []string{"🇸🇬", "🇯🇵", "🇺🇸", "🇹🇼", "🇰🇷", "🇨🇦", "🇬🇧", "🇩🇪", "🇫🇷"})
	if len(aiTags) == 0 {
		for _, key := range regionKeys {
			if key != hongKongRegionKey {
				aiTags = append(aiTags, groups[key]...)
			}
		}
	}
	if len(aiTags) == 0 {
		aiTags = []string{directTag}
	}
	telegramTags := collectGroups(groups, []string{"🇸🇬", "🇺🇸", "🇯🇵", "🇹🇼"})
	if len(telegramTags) == 0 {
		for _, key := range regionKeys {
			if key != hongKongRegionKey {
				telegramTags = append(telegramTags, groups[key]...)
			}
		}
	}
	if len(telegramTags) == 0 {
		telegramTags = allTags
	}
	gameTags := collectGroups(groups, []string{"🇯🇵", "🇸🇬", "🇭🇰", "🇹🇼", "🇰🇷"})
	if len(gameTags) == 0 {
		gameTags = allTags
	}
	outs = append(outs,
		smartOutbound(aiFallbackTag, aiTags),
		smartOutbound(mediaFallbackTag, allTags),
		smartOutboundWithURL(telegramFallbackTag, telegramTags, telegramTestURL),
		smartOutbound(gameFallbackTag, gameTags),
	)

	selectableSmartTags := []string{globalSmartTag}
	for _, key := range regionKeys {
		if tag := regionSmart[key]; tag != "" {
			selectableSmartTags = append(selectableSmartTags, tag)
		}
	}
	baselineChoices := append(append([]string(nil), selectableSmartTags...), directTag)
	outs = append(outs, selectorOutbound(baselineTag, globalSmartTag, baselineChoices...))

	policyDefinitions := []struct {
		tag       string
		defaultTo string
		fallback  string
	}{
		{aiPolicy, aiFallbackTag, aiFallbackTag},
		{telegramPolicy, telegramFallbackTag, telegramFallbackTag},
		{netflixPolicy, baselineTag, mediaFallbackTag},
		{disneyPolicy, baselineTag, mediaFallbackTag},
		{maxPolicy, baselineTag, mediaFallbackTag},
		{primeVideoPolicy, baselineTag, mediaFallbackTag},
		{appleTVPolicy, baselineTag, mediaFallbackTag},
		{youtubePolicy, baselineTag, mediaFallbackTag},
		{tiktokPolicy, baselineTag, mediaFallbackTag},
		{douyinPolicy, directTag, ""},
		{bilibiliPolicy, directTag, mediaFallbackTag},
		{spotifyPolicy, baselineTag, mediaFallbackTag},
		{mediaPolicy, baselineTag, mediaFallbackTag},
		{socialPolicy, baselineTag, globalSmartTag},
		{gamePolicy, baselineTag, gameFallbackTag},
		{githubPolicy, baselineTag, globalSmartTag},
		{developerPolicy, baselineTag, globalSmartTag},
		{applePolicy, baselineTag, globalSmartTag},
		{microsoftPolicy, baselineTag, globalSmartTag},
		{googlePolicy, baselineTag, globalSmartTag},
		{networkTestPolicy, baselineTag, globalSmartTag},
		{downloadPolicy, directTag, ""},
		{cnDomainPolicy, directTag, ""},
		{cnIPPolicy, directTag, ""},
		{adsPolicy, rejectTag, ""},
	}
	for _, policy := range policyDefinitions {
		var choices []string
		if policy.tag == aiPolicy {
			choices = []string{aiFallbackTag}
			for _, key := range regionKeys {
				if key != hongKongRegionKey {
					choices = append(choices, regionSmart[key])
				}
			}
			choices = append(choices, directTag)
			outs = append(outs, selectorOutbound(policy.tag, policy.defaultTo, choices...))
			continue
		}
		switch policy.defaultTo {
		case directTag:
			choices = []string{directTag, baselineTag, policy.fallback}
			choices = append(choices, selectableSmartTags...)
		case rejectTag:
			choices = []string{rejectTag, directTag, baselineTag}
			choices = append(choices, selectableSmartTags...)
		default:
			choices = []string{baselineTag, policy.fallback}
			choices = append(choices, selectableSmartTags...)
			choices = append(choices, directTag)
		}
		outs = append(outs, selectorOutbound(policy.tag, policy.defaultTo, choices...))
	}
	for _, raw := range outs {
		outbound, loaded := raw.(map[string]any)
		if loaded && outbound["type"] == "smart" {
			outbound["score_namespace"] = smartScoreNamespace
			identities := make(map[string]string)
			for _, tag := range outbound["outbounds"].([]string) {
				if identity := scoreIdentities[tag]; identity != "" {
					identities[tag] = identity
				}
			}
			outbound["score_identities"] = identities
		}
	}

	profile := map[string]any{
		"log":      map[string]any{"level": "warn", "timestamp": true},
		"dns":      buildDNS(),
		"inbounds": buildInbounds(),
		"experimental": map[string]any{
			"cache_file": map[string]any{"enabled": true, "cache_id": "smart-box"},
			"clash_api":  map[string]any{},
		},
		"outbounds": outs,
		"route": map[string]any{
			"auto_detect_interface":   true,
			"default_domain_resolver": "local",
			"rules":                   detailedRules(),
			"rule_set":                remoteRuleSets(ruleBaseURL),
			"final":                   baselineTag,
		},
	}
	return profile
}

func nodeScoreIdentities(nodes []Node) map[string]string {
	identities := make(map[string]string, len(nodes))
	for _, node := range nodes {
		identityOutbound := make(map[string]any, len(node.Outbound))
		for key, value := range node.Outbound {
			if key != "tag" {
				identityOutbound[key] = value
			}
		}
		content, err := json.Marshal(identityOutbound)
		if err != nil {
			// buildProfile will reject the same non-JSON value when the completed
			// profile is encoded; retaining the tag keeps this helper deterministic.
			content = []byte(node.Tag)
		}
		digest := sha256.Sum256(content)
		identities[node.Tag] = fmt.Sprintf("node-v1-%x", digest[:12])
	}
	return identities
}

func buildInbounds() []any {
	return []any{
		map[string]any{"type": "tun", "tag": "tun-in", "address": []any{"172.19.0.1/30", "fdfe:dcba:9876::1/126"}, "auto_route": true, "strict_route": true, "stack": "mixed"},
		map[string]any{"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": 20808, "set_system_proxy": false},
	}
}

func buildDNS() map[string]any {
	servers := []any{map[string]any{"type": "local", "tag": "local"}}
	servers = append(servers,
		cloudflareDNS("global-dns", globalSmartTag),
		cloudflareDNS("baseline-dns", baselineTag),
		cloudflareDNS("ai-dns", aiPolicy),
		cloudflareDNS("telegram-dns", telegramPolicy),
		cloudflareDNS("netflix-dns", netflixPolicy),
		cloudflareDNS("disney-dns", disneyPolicy),
		cloudflareDNS("max-dns", maxPolicy),
		cloudflareDNS("prime-video-dns", primeVideoPolicy),
		cloudflareDNS("apple-tv-dns", appleTVPolicy),
		cloudflareDNS("youtube-dns", youtubePolicy),
		cloudflareDNS("tiktok-dns", tiktokPolicy),
		aliDNS("douyin-dns", douyinPolicy),
		aliDNS("bilibili-dns", bilibiliPolicy),
		cloudflareDNS("spotify-dns", spotifyPolicy),
		cloudflareDNS("media-dns", mediaPolicy),
		cloudflareDNS("social-dns", socialPolicy),
		cloudflareDNS("game-dns", gamePolicy),
		cloudflareDNS("github-dns", githubPolicy),
		cloudflareDNS("developer-dns", developerPolicy),
		cloudflareDNS("apple-dns", applePolicy),
		cloudflareDNS("microsoft-dns", microsoftPolicy),
		cloudflareDNS("google-dns", googlePolicy),
		cloudflareDNS("network-test-dns", networkTestPolicy),
		aliDNS("download-dns", downloadPolicy),
		aliDNS("cn-domain-dns", cnDomainPolicy),
	)

	rules := []any{
		dnsRoute(map[string]any{"clash_mode": clashModeDirect}, "local"),
		dnsRoute(map[string]any{"clash_mode": clashModeGlobal}, "global-dns"),
		dnsRoute(map[string]any{"clash_mode": energySavingMode, "rule_set": []any{"ai"}}, "ai-dns"),
		dnsRoute(map[string]any{"clash_mode": energySavingMode, "rule_set": []any{"telegram"}}, "telegram-dns"),
	}
	for _, match := range applicationMatches[:2] {
		if len(match.Packages) > 0 {
			rules = append(rules, dnsRoute(map[string]any{"clash_mode": energySavingMode, "package_name": match.Packages}, match.DNSServer))
		}
		if len(match.Processes) > 0 {
			rules = append(rules, dnsRoute(map[string]any{"clash_mode": energySavingMode, "process_name": match.Processes}, match.DNSServer))
		}
	}
	rules = append(rules,
		dnsRoute(map[string]any{"clash_mode": energySavingMode}, "local"),
		dnsRoute(map[string]any{"domain_suffix": []any{"localhost", "local", "lan", "home.arpa"}}, "local"),
		dnsRoute(map[string]any{"rule_set": []any{"private"}}, "local"),
		dnsRoute(map[string]any{"package_name": douyinPackages}, "douyin-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"douyin"}}, "douyin-dns"),
		// The ad selector defaults to REJECT. It must not be a DoH detour:
		// block outbounds return EPERM, which turns every blocked ad lookup
		// into a noisy DNS processing error. Connections remain controlled by
		// the independently selectable ad policy below.
		dnsRoute(map[string]any{"rule_set": []any{"ads"}}, "baseline-dns"),
	)
	for _, match := range applicationMatches {
		if len(match.Packages) > 0 {
			rules = append(rules, dnsRoute(map[string]any{"package_name": match.Packages}, match.DNSServer))
		}
		if len(match.Processes) > 0 {
			rules = append(rules, dnsRoute(map[string]any{"process_name": match.Processes}, match.DNSServer))
		}
	}
	rules = append(rules,
		dnsRoute(map[string]any{"rule_set": []any{"ai"}}, "ai-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"telegram"}}, "telegram-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"netflix"}}, "netflix-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"disney"}}, "disney-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"max"}}, "max-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"prime-video"}}, "prime-video-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"apple-tv"}}, "apple-tv-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"youtube"}}, "youtube-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"tiktok"}}, "tiktok-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"bilibili"}}, "bilibili-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"spotify"}}, "spotify-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"discord", "twitter", "facebook", "instagram", "whatsapp"}}, "social-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"games-cn"}}, "download-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"games"}}, "game-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"github"}}, "github-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"developer", "docker", "npmjs"}}, "developer-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"network-test"}}, "network-test-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"apple-cn", "microsoft-cn", "google-cn"}}, "cn-domain-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"applications"}}, "download-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"apple"}}, "apple-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"microsoft"}}, "microsoft-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"google"}}, "google-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"media"}}, "media-dns"),
		dnsRoute(map[string]any{"rule_set": []any{"cn"}}, "cn-domain-dns"),
	)
	return map[string]any{
		"strategy":       "ipv4_only",
		"cache_capacity": 4096,
		"servers":        servers,
		"rules":          rules,
		"final":          "baseline-dns",
	}
}

func cloudflareDNS(tag, detour string) map[string]any {
	return map[string]any{"type": "https", "tag": tag, "server": "1.1.1.1", "server_port": 443, "path": "/dns-query", "detour": detour}
}

func aliDNS(tag, detour string) map[string]any {
	return map[string]any{
		"type": "https", "tag": tag, "server": "223.5.5.5", "server_port": 443,
		"path": "/dns-query", "detour": detour,
		"tls": map[string]any{"enabled": true, "server_name": "dns.alidns.com"},
	}
}

func dnsRoute(match map[string]any, server string) map[string]any {
	rule := make(map[string]any, len(match)+2)
	for key, value := range match {
		rule[key] = value
	}
	rule["action"] = "route"
	rule["server"] = server
	return rule
}

func detailedRules() []any {
	rules := []any{
		map[string]any{"action": "sniff"},
		map[string]any{"protocol": "dns", "action": "hijack-dns"},
		routeMatch(map[string]any{"clash_mode": clashModeDirect}, directTag),
		routeMatch(map[string]any{"clash_mode": clashModeGlobal}, globalSmartTag),
		routeMatch(map[string]any{"ip_is_private": true}, directTag),
		routeMatch(map[string]any{"domain_suffix": []any{"localhost", "local", "lan", "home.arpa"}}, directTag),
		routeMatch(map[string]any{"rule_set": []any{"private"}}, directTag),
		routeMatch(map[string]any{"clash_mode": energySavingMode, "package_name": douyinPackages}, directTag),
		routeMatch(map[string]any{"clash_mode": energySavingMode, "rule_set": []any{"douyin"}}, directTag),
		routeMatch(map[string]any{"package_name": douyinPackages}, douyinPolicy),
		routeRuleSet(douyinPolicy, "douyin"),
		routeMatch(map[string]any{"rule_set": []any{"ads"}}, adsPolicy),
		routeMatch(map[string]any{"clash_mode": energySavingMode, "rule_set": []any{"ai"}}, aiPolicy),
		routeMatch(map[string]any{"clash_mode": energySavingMode, "rule_set": []any{"telegram"}}, telegramPolicy),
	}
	for _, match := range applicationMatches[:2] {
		if len(match.Packages) > 0 {
			rules = append(rules, routeMatch(map[string]any{"clash_mode": energySavingMode, "package_name": match.Packages}, match.Outbound))
		}
		if len(match.Processes) > 0 {
			rules = append(rules, routeMatch(map[string]any{"clash_mode": energySavingMode, "process_name": match.Processes}, match.Outbound))
		}
	}
	rules = append(rules, routeMatch(map[string]any{"clash_mode": energySavingMode}, directTag))
	for _, match := range applicationMatches {
		if len(match.Packages) > 0 {
			rules = append(rules, routeMatch(map[string]any{"package_name": match.Packages}, match.Outbound))
		}
		if len(match.Processes) > 0 {
			rules = append(rules, routeMatch(map[string]any{"process_name": match.Processes}, match.Outbound))
		}
	}
	rules = append(rules,
		routeRuleSet(aiPolicy, "ai"),
		routeRuleSet(telegramPolicy, "telegram"),
		routeRuleSet(netflixPolicy, "netflix"),
		routeRuleSet(disneyPolicy, "disney"),
		routeRuleSet(maxPolicy, "max"),
		routeRuleSet(primeVideoPolicy, "prime-video"),
		routeRuleSet(appleTVPolicy, "apple-tv"),
		routeRuleSet(youtubePolicy, "youtube"),
		routeRuleSet(tiktokPolicy, "tiktok"),
		routeRuleSet(bilibiliPolicy, "bilibili"),
		routeRuleSet(spotifyPolicy, "spotify"),
		routeRuleSet(socialPolicy, "discord", "twitter", "facebook", "instagram", "whatsapp"),
		routeRuleSet(downloadPolicy, "games-cn"),
		routeRuleSet(gamePolicy, "games"),
		routeRuleSet(githubPolicy, "github"),
		routeRuleSet(developerPolicy, "developer", "docker", "npmjs"),
		routeRuleSet(networkTestPolicy, "network-test"),
		routeRuleSet(cnDomainPolicy, "apple-cn", "microsoft-cn", "google-cn"),
		routeRuleSet(downloadPolicy, "applications"),
		routeRuleSet(applePolicy, "apple"),
		routeRuleSet(microsoftPolicy, "microsoft"),
		routeRuleSet(googlePolicy, "google"),
		routeRuleSet(mediaPolicy, "media"),
		routeRuleSet(cnDomainPolicy, "cn"),
		map[string]any{"network": []any{"tcp", "udp"}, "action": "resolve"},
		routeRuleSet(telegramPolicy, "telegram-ip"),
		routeRuleSet(netflixPolicy, "netflix-ip"),
		routeRuleSet(mediaPolicy, "media-ip"),
		routeRuleSet(cnIPPolicy, "cn-ip"),
	)
	return rules
}

func routeRuleSet(outbound string, tags ...string) map[string]any {
	values := make([]any, len(tags))
	for index, tag := range tags {
		values[index] = tag
	}
	return routeMatch(map[string]any{"rule_set": values}, outbound)
}

func routeMatch(match map[string]any, outbound string) map[string]any {
	rule := make(map[string]any, len(match)+2)
	for key, value := range match {
		rule[key] = value
	}
	rule["action"] = "route"
	rule["outbound"] = outbound
	return rule
}

func remoteRuleSets(baseURL string) []any {
	if baseURL == "" {
		return nil
	}
	return []any{map[string]any{
		"type":            "remote",
		"tag":             ruleSetTags(),
		"format":          "binary",
		"url":             strings.TrimRight(baseURL, "/") + "/{tag}.srs",
		"http_client":     map[string]any{"detour": directTag},
		"update_interval": "24h",
	}}
}

func smartOutbound(tag string, tags []string) map[string]any {
	return smartOutboundWithURL(tag, tags, defaultSmartTestURL)
}

func smartOutboundWithURL(tag string, tags []string, testURL string) map[string]any {
	return map[string]any{
		"type": "smart", "tag": tag, "outbounds": tags,
		"url": testURL, "interval": "5m", "tolerance": 50,
		"memory_timeout": "1h", "failure_penalty": 500, "interrupt_exist_connections": false,
	}
}

func selectorOutbound(tag, defaultChoice string, choices ...string) map[string]any {
	unique := make([]string, 0, len(choices))
	seen := make(map[string]bool, len(choices))
	for _, choice := range choices {
		if choice == "" || seen[choice] {
			continue
		}
		seen[choice] = true
		unique = append(unique, choice)
	}
	return map[string]any{"type": "selector", "tag": tag, "outbounds": unique, "default": defaultChoice}
}

func collectGroups(groups map[string][]string, keys []string) []string {
	var result []string
	for _, key := range keys {
		result = append(result, groups[key]...)
	}
	return result
}

func regionLabel(key string) string {
	if key == "unknown" {
		return "❓ 未识别"
	}
	if name, ok := flagNames[key]; ok {
		return key + " " + name
	}
	return key
}
