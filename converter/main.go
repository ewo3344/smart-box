package main

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/tls"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"gopkg.in/yaml.v3"
)

type Config struct {
	Listen          string        `json:"listen"`
	PublicURL       string        `json:"public_url"`
	PublicPath      string        `json:"public_path"`
	RefreshInterval time.Duration `json:"-"`
	RefreshText     string        `json:"refresh_interval"`
	CacheDir        string        `json:"cache_dir"`
	Sources         []Source      `json:"sources"`
}

type Source struct {
	Name     string `json:"name"`
	URL      string `json:"url"`
	Insecure bool   `json:"insecure,omitempty"`
}

type App struct {
	config Config
	client *http.Client
	rules  *RuleSetStore
	mu     sync.RWMutex
	state  Snapshot
}

type Snapshot struct {
	Config       map[string]any `json:"-"`
	GeneratedAt  time.Time      `json:"generated_at"`
	NodeCount    int            `json:"node_count"`
	SourceStatus []SourceStatus `json:"source_status"`
	RuleSets     RuleSetStatus  `json:"rule_sets"`
	Hash         string         `json:"hash"`
}

type SourceStatus struct {
	Name       string `json:"name"`
	Status     string `json:"status"`
	NodeCount  int    `json:"node_count"`
	LiveCount  int    `json:"live_count"`
	CacheUsed  bool   `json:"cache_used"`
	ErrorClass string `json:"error,omitempty"`
}

type Node struct {
	Tag       string
	Name      string
	Source    string
	Region    string
	RegionKey string
	Protocol  string
	Server    string
	Port      int
	Outbound  map[string]any
	Live      bool
}

func main() {
	path := os.Getenv("SMART_BOX_CONFIG")
	if path == "" {
		path = os.Getenv("SING_BOX_SMART_CONFIG")
	}
	if path == "" {
		path = "config.json"
	}
	cfg, err := loadConfig(path)
	if err != nil {
		log.Fatal(err)
	}
	client := &http.Client{Timeout: 60 * time.Second}
	rules := newRuleSetStore(filepath.Join(cfg.CacheDir, "rules"), client, requiredRuleSets)
	if _, err := rules.loadCache(); err != nil {
		log.Printf("rule-set cache is not complete; initial refresh will fill it")
	}
	app := &App{config: cfg, client: client, rules: rules}
	loadedFromCache := app.bootstrapCache()
	go func() {
		if !loadedFromCache {
			if err := app.refresh(context.Background()); err != nil {
				log.Printf("initial refresh failed: %v", err)
			}
		}
		app.refreshLoop()
	}()

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", app.health)
	mux.HandleFunc("/api/v1/status", app.status)
	mux.HandleFunc("/subscription/", app.subscription)
	mux.HandleFunc("/rule-set/", app.ruleSet)
	server := &http.Server{Addr: cfg.Listen, Handler: requestLog(mux)}
	log.Printf("smart-box-converter listening on %s", cfg.Listen)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}

func loadConfig(path string) (Config, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return Config{}, fmt.Errorf("read config: %w", err)
	}
	var cfg Config
	if err := json.Unmarshal(b, &cfg); err != nil {
		return Config{}, fmt.Errorf("parse config: %w", err)
	}
	if cfg.Listen == "" || cfg.PublicURL == "" || cfg.PublicPath == "" || len(cfg.Sources) == 0 {
		return Config{}, errors.New("listen, public_url, public_path and at least one source are required")
	}
	if len(cfg.PublicPath) < 24 || strings.ContainsAny(cfg.PublicPath, "/?&") {
		return Config{}, errors.New("public_path must be an unguessable token of at least 24 characters")
	}
	cfg.PublicURL, err = normalizePublicURL(cfg.PublicURL)
	if err != nil {
		return Config{}, err
	}
	if cfg.RefreshText == "" {
		cfg.RefreshText = "30m"
	}
	cfg.RefreshInterval, err = time.ParseDuration(cfg.RefreshText)
	if err != nil || cfg.RefreshInterval < time.Minute {
		return Config{}, errors.New("refresh_interval must be at least 1m")
	}
	if cfg.CacheDir == "" {
		cfg.CacheDir = "cache"
	}
	if err := os.MkdirAll(cfg.CacheDir, 0700); err != nil {
		return Config{}, fmt.Errorf("create cache dir: %w", err)
	}
	return cfg, nil
}

func normalizePublicURL(raw string) (string, error) {
	if strings.TrimSpace(raw) != raw {
		return "", errors.New("public_url must not contain surrounding whitespace")
	}
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.Hostname() == "" {
		return "", errors.New("public_url must be an absolute http or https origin")
	}
	if u.User != nil || u.Opaque != "" || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" {
		return "", errors.New("public_url must not contain credentials, query or fragment")
	}
	if (u.Path != "" && u.Path != "/") || u.RawPath != "" {
		return "", errors.New("public_url must not contain a path")
	}
	u.Path = ""
	return strings.TrimSuffix(u.String(), "/"), nil
}

func (a *App) refreshLoop() {
	ticker := time.NewTicker(a.config.RefreshInterval)
	defer ticker.Stop()
	for range ticker.C {
		if err := a.refresh(context.Background()); err != nil {
			log.Printf("refresh failed, keeping last good snapshot: %v", err)
		}
	}
}

func (a *App) refresh(ctx context.Context) error {
	ruleStatus, err := a.rules.refresh(ctx, false)
	if err != nil {
		return fmt.Errorf("refresh rule sets: %w", err)
	}
	if ruleStatus.Fallback > 0 {
		log.Printf("rule-set refresh used %d last-known-good files", ruleStatus.Fallback)
	}
	sourceNodes := make([][]Node, len(a.config.Sources))
	statuses := make([]SourceStatus, len(a.config.Sources))
	var wg sync.WaitGroup
	for index, source := range a.config.Sources {
		wg.Add(1)
		go func(index int, source Source) {
			defer wg.Done()
			status := SourceStatus{Name: source.Name}
			nodes, err := a.fetchSource(ctx, source)
			if err != nil {
				cached, cacheErr := a.readCache(source)
				if cacheErr != nil {
					status.Status, status.ErrorClass = "error", classifyError(err)
					statuses[index] = status
					log.Printf("source %s unavailable (%s)", source.Name, classifyError(err))
					return
				}
				nodes, status.CacheUsed, status.Status = cached, true, "cache"
			} else {
				status.Status = "fresh"
				_ = a.writeCache(source, nodes)
			}
			nodes = filterInformationalNodes(nodes)
			status.NodeCount = len(nodes)
			live := filterLive(nodes)
			status.LiveCount = len(live)
			sourceNodes[index] = live
			statuses[index] = status
		}(index, source)
	}
	wg.Wait()
	var all []Node
	for _, nodes := range sourceNodes {
		all = append(all, nodes...)
	}
	all = dedupeNodes(all)
	all = uniqueTags(all)
	if len(all) == 0 {
		return errors.New("all sources returned zero live nodes")
	}
	profile := buildProfile(all, a.ruleSetBaseURL())
	b, _ := json.Marshal(profile)
	h := sha256.Sum256(b)
	snapshot := Snapshot{Config: profile, GeneratedAt: time.Now().UTC(), NodeCount: len(all), SourceStatus: statuses, RuleSets: ruleStatus, Hash: hex.EncodeToString(h[:])}
	a.mu.Lock()
	a.state = snapshot
	a.mu.Unlock()
	log.Printf("refresh complete: %d live nodes, hash %.12s", len(all), snapshot.Hash)
	return nil
}

func (a *App) bootstrapCache() bool {
	if a.rules == nil || !a.rules.ready() {
		return false
	}
	var all []Node
	statuses := make([]SourceStatus, len(a.config.Sources))
	for i, source := range a.config.Sources {
		nodes, err := a.readCache(source)
		nodes = filterInformationalNodes(nodes)
		status := SourceStatus{Name: source.Name, Status: "no-cache"}
		if err == nil && len(nodes) > 0 {
			status.Status, status.CacheUsed, status.NodeCount, status.LiveCount = "cache", true, len(nodes), len(nodes)
			all = append(all, nodes...)
		}
		statuses[i] = status
	}
	all = uniqueTags(dedupeNodes(all))
	if len(all) == 0 {
		return false
	}
	profile := buildProfile(all, a.ruleSetBaseURL())
	b, _ := json.Marshal(profile)
	h := sha256.Sum256(b)
	a.mu.Lock()
	a.state = Snapshot{Config: profile, GeneratedAt: time.Now().UTC(), NodeCount: len(all), SourceStatus: statuses, RuleSets: a.rules.currentStatus(), Hash: hex.EncodeToString(h[:])}
	a.mu.Unlock()
	log.Printf("loaded %d cached nodes; next refresh in %s", len(all), a.config.RefreshInterval)
	return true
}

func (a *App) ruleSetBaseURL() string {
	return a.config.PublicURL + "/rule-set/" + url.PathEscape(a.config.PublicPath)
}

func (a *App) fetchSource(ctx context.Context, source Source) ([]Node, error) {
	u, err := url.Parse(source.URL)
	if err != nil || u.Scheme != "https" {
		return nil, errors.New("source URL must use https")
	}
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, source.URL, nil)
	req.Header.Set("User-Agent", "Clash.Meta/1.18.10")
	client := a.client
	if source.Insecure {
		client = &http.Client{
			Timeout: 60 * time.Second,
			Transport: &http.Transport{TLSClientConfig: &tls.Config{
				InsecureSkipVerify: true, // Scoped to a provider explicitly marked insecure.
			}},
		}
	}
	var resp *http.Response
	for attempt := 0; attempt < 2; attempt++ {
		resp, err = client.Do(req.Clone(ctx))
		if err == nil {
			break
		}
		if attempt < 1 {
			time.Sleep(time.Duration(attempt+1) * time.Second)
		}
	}
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("provider status %d", resp.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	if err != nil || len(strings.TrimSpace(string(body))) == 0 {
		return nil, errors.New("empty provider response")
	}
	return parseClash(body, source.Name)
}

func (a *App) cachePath(source Source) string {
	h := sha256.Sum256([]byte(source.Name + "\x00" + source.URL))
	return filepath.Join(a.config.CacheDir, hex.EncodeToString(h[:])+".json")
}

func (a *App) writeCache(source Source, nodes []Node) error {
	b, err := json.Marshal(nodes)
	if err != nil {
		return err
	}
	tmp := a.cachePath(source) + ".tmp"
	if err = os.WriteFile(tmp, b, 0600); err != nil {
		return err
	}
	return os.Rename(tmp, a.cachePath(source))
}

func (a *App) readCache(source Source) ([]Node, error) {
	b, err := os.ReadFile(a.cachePath(source))
	if err != nil {
		return nil, err
	}
	var nodes []Node
	err = json.Unmarshal(b, &nodes)
	return nodes, err
}

func classifyError(err error) string {
	s := strings.ToLower(err.Error())
	switch {
	case strings.Contains(s, "empty"):
		return "empty"
	case strings.Contains(s, "status"):
		return "http"
	case strings.Contains(s, "timeout"):
		return "timeout"
	case strings.Contains(s, "missing proxies"):
		return "format"
	case strings.Contains(s, "no supported"):
		return "unsupported"
	case strings.Contains(s, "yaml"):
		return "parse"
	default:
		return "network"
	}
}

func requestLog(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		next.ServeHTTP(w, r)
		path := r.URL.Path
		if strings.HasPrefix(path, "/subscription/") {
			path = "/subscription/<redacted>"
		} else if strings.HasPrefix(path, "/rule-set/") {
			path = "/rule-set/<redacted>"
		}
		log.Printf("%s %s %s", r.Method, path, time.Since(start).Round(time.Millisecond))
	})
}

func (a *App) health(w http.ResponseWriter, r *http.Request) {
	a.mu.RLock()
	defer a.mu.RUnlock()
	if a.state.Config == nil {
		http.Error(w, "not ready", 503)
		return
	}
	w.WriteHeader(http.StatusOK)
	_, _ = io.WriteString(w, "ok\n")
}

func (a *App) status(w http.ResponseWriter, r *http.Request) {
	a.mu.RLock()
	defer a.mu.RUnlock()
	w.Header().Set("Content-Type", "application/json")
	if a.state.Config == nil {
		http.Error(w, `{"status":"not_ready"}`, 503)
		return
	}
	_ = json.NewEncoder(w).Encode(a.state)
}

func (a *App) subscription(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if !secureTokenEqual(strings.TrimPrefix(r.URL.Path, "/subscription/"), a.config.PublicPath) {
		http.NotFound(w, r)
		return
	}
	a.mu.RLock()
	profile := a.state.Config
	hash := a.state.Hash
	a.mu.RUnlock()
	if profile == nil {
		http.Error(w, "not ready", 503)
		return
	}
	etag := `"` + hash + `"`
	w.Header().Set("ETag", etag)
	if r.Header.Get("If-None-Match") == etag {
		w.WriteHeader(http.StatusNotModified)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "private, max-age=300")
	if r.Method == http.MethodHead {
		return
	}
	_ = json.NewEncoder(w).Encode(profile)
}

func (a *App) ruleSet(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/rule-set/"), "/")
	if len(parts) != 2 || !secureTokenEqual(parts[0], a.config.PublicPath) || !strings.HasSuffix(parts[1], ".srs") {
		http.NotFound(w, r)
		return
	}
	tag := strings.TrimSuffix(parts[1], ".srs")
	if tag+".srs" != parts[1] || a.rules == nil {
		http.NotFound(w, r)
		return
	}
	entry, exists := a.rules.get(tag)
	if !exists {
		http.NotFound(w, r)
		return
	}
	etag := `"` + entry.Hash + `"`
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Cache-Control", "private, max-age=3600")
	w.Header().Set("ETag", etag)
	w.Header().Set("Last-Modified", entry.Modified.Format(http.TimeFormat))
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if r.Header.Get("If-None-Match") == etag {
		w.WriteHeader(http.StatusNotModified)
		return
	}
	if r.Method == http.MethodHead {
		return
	}
	_, _ = w.Write(entry.Content)
}

func secureTokenEqual(got, expected string) bool {
	return subtle.ConstantTimeCompare([]byte(got), []byte(expected)) == 1
}

var _ = regexp.MustCompile

func filterLive(nodes []Node) []Node {
	result := make([]Node, 0, len(nodes))
	results := make(chan Node, len(nodes))
	var wg sync.WaitGroup
	for i := range nodes {
		n := nodes[i]
		if n.Server == "" || n.Port < 1 || n.Port > 65535 {
			continue
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			healthProbeLimit <- struct{}{}
			defer func() { <-healthProbeLimit }()
			n.Live = probeNode(n)
			if n.Live {
				results <- n
			}
		}()
	}
	wg.Wait()
	close(results)
	for n := range results {
		result = append(result, n)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Tag < result[j].Tag })
	return result
}

var healthProbeLimit = make(chan struct{}, 32)

func probeNode(n Node) bool {
	addr := net.JoinHostPort(n.Server, fmt.Sprint(n.Port))
	if n.Protocol == "hysteria2" || n.Protocol == "tuic" {
		c, err := net.DialTimeout("udp", addr, 2*time.Second)
		if err == nil {
			_ = c.Close()
			return true
		}
		return false
	}
	c, err := net.DialTimeout("tcp", addr, 3*time.Second)
	if err == nil {
		_ = c.Close()
		return true
	}
	return false
}

func dedupeNodes(nodes []Node) []Node {
	seen := map[string]bool{}
	result := make([]Node, 0, len(nodes))
	for _, n := range nodes {
		canonical := make(map[string]any, len(n.Outbound))
		for key, value := range n.Outbound {
			if key != "tag" {
				canonical[key] = value
			}
		}
		b, _ := json.Marshal(canonical)
		key := string(b)
		if seen[key] {
			continue
		}
		seen[key] = true
		result = append(result, n)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Tag < result[j].Tag })
	return result
}

func uniqueTags(nodes []Node) []Node {
	counts := map[string]int{}
	for i := range nodes {
		base := nodes[i].Tag
		counts[base]++
		if counts[base] > 1 {
			nodes[i].Tag = fmt.Sprintf("%s #%d", base, counts[base])
		}
		nodes[i].Outbound["tag"] = nodes[i].Tag
	}
	return nodes
}

func parseClash(body []byte, source string) ([]Node, error) {
	var doc map[string]any
	if err := yaml.Unmarshal(body, &doc); err != nil {
		return nil, fmt.Errorf("yaml: %w", err)
	}
	raw, ok := doc["proxies"].([]any)
	if !ok {
		return nil, errors.New("missing proxies")
	}
	result := make([]Node, 0, len(raw))
	for i, item := range raw {
		m, ok := item.(map[string]any)
		if !ok {
			continue
		}
		n, err := normalizeProxy(m, source, i)
		if err != nil {
			continue
		}
		result = append(result, n)
	}
	if len(result) == 0 {
		return nil, errors.New("no supported proxies")
	}
	return result, nil
}

var informationalProxyNameFragments = []string{
	"剩余流量", "流量剩余", "套餐到期", "到期时间", "过期时间", "订阅到期",
	"订阅更新", "上次更新", "官网", "官方网站", "请看教程", "使用教程",
	"下载clash", "下载 clash", "客户端教程", "联系客服",
	"remaining traffic", "traffic remaining", "subscription update", "updated at",
	"official website", "download clash", "tutorial", "expiry", "expiration", "expires at",
}

func isInformationalProxyName(name string) bool {
	normalized := strings.ToLower(strings.TrimSpace(name))
	for _, fragment := range informationalProxyNameFragments {
		if strings.Contains(normalized, fragment) {
			return true
		}
	}
	return false
}

func filterInformationalNodes(nodes []Node) []Node {
	result := make([]Node, 0, len(nodes))
	for _, node := range nodes {
		name := node.Name
		if name == "" {
			name = node.Tag
		}
		if isInformationalProxyName(name) {
			continue
		}
		result = append(result, node)
	}
	return result
}

func str(m map[string]any, key string) string {
	if v, ok := m[key]; ok {
		return fmt.Sprint(v)
	}
	return ""
}
func intValue(m map[string]any, key string, fallback int) int {
	if v, ok := m[key]; ok {
		switch x := v.(type) {
		case int:
			return x
		case int64:
			return int(x)
		case float64:
			return int(x)
		case string:
			var n int
			_, _ = fmt.Sscan(x, &n)
			if n > 0 {
				return n
			}
		}
	}
	return fallback
}

func normalizeProxy(m map[string]any, source string, index int) (Node, error) {
	t := strings.ToLower(str(m, "type"))
	if t == "" {
		return Node{}, errors.New("missing type")
	}
	server := str(m, "server")
	port := intValue(m, "port", 0)
	if server == "" || port == 0 {
		return Node{}, errors.New("missing endpoint")
	}
	name := str(m, "name")
	if name == "" {
		name = fmt.Sprintf("%s-%02d", source, index+1)
	}
	if isInformationalProxyName(name) {
		return Node{}, errors.New("informational proxy entry")
	}
	region, regionKey := classifyRegion(name)
	tag := sanitizeTag(source + " " + name)
	if runes := []rune(tag); len(runes) > 90 {
		tag = string(runes[:90])
	}
	base := map[string]any{"type": t, "tag": tag, "server": server, "server_port": port}
	copyTLS(base, m)
	switch t {
	case "ss":
		base["type"] = "shadowsocks"
		base["method"], base["password"] = str(m, "cipher"), str(m, "password")
	case "vmess":
		base["uuid"], base["security"] = str(m, "uuid"), firstOr(str(m, "cipher"), "auto")
		if aid := intValue(m, "alterId", 0); aid > 0 {
			base["alter_id"] = aid
		}
		addTransport(base, m)
	case "vless":
		base["uuid"] = str(m, "uuid")
		if flow := str(m, "flow"); flow != "" {
			base["flow"] = flow
		}
		addTransport(base, m)
	case "trojan":
		base["password"] = str(m, "password")
		ensureTLS(base)
		addTransport(base, m)
	case "hysteria2":
		base["password"] = firstOr(str(m, "password"), str(m, "auth"))
		ensureTLS(base)
		addHysteria2(base, m)
	case "tuic":
		base["uuid"], base["password"] = str(m, "uuid"), str(m, "password")
		ensureTLS(base)
		if cc := str(m, "congestion-controller"); cc != "" {
			base["congestion_control"] = cc
		}
	case "anytls":
		base["password"] = str(m, "password")
		ensureTLS(base)
	default:
		return Node{}, fmt.Errorf("unsupported type %s", t)
	}
	if base["type"] == "" || (base["type"] != "shadowsocks" && base["type"] != "vmess" && base["type"] != "vless" && base["type"] != "trojan" && base["type"] != "hysteria2" && base["type"] != "tuic" && base["type"] != "anytls") {
		return Node{}, errors.New("invalid outbound")
	}
	return Node{Tag: tag, Name: name, Source: source, Region: region, RegionKey: regionKey, Protocol: t, Server: server, Port: port, Outbound: base}, nil
}

func firstOr(a, b string) string {
	if a != "" {
		return a
	}
	return b
}
func copyTLS(base map[string]any, m map[string]any) {
	tlsEnabled := boolValue(m, "tls") || boolValue(m, "skip-cert-verify") || str(m, "servername") != "" || str(m, "sni") != ""
	if !tlsEnabled {
		return
	}
	tls := map[string]any{"enabled": true}
	if v := firstOr(str(m, "servername"), str(m, "sni")); v != "" {
		tls["server_name"] = v
	}
	if boolValue(m, "skip-cert-verify") {
		tls["insecure"] = true
	}
	if raw, ok := m["alpn"].([]any); ok {
		tls["alpn"] = raw
	}
	if fp := str(m, "client-fingerprint"); fp != "" {
		tls["utls"] = map[string]any{"enabled": true, "fingerprint": fp}
	}
	if reality, ok := m["reality-opts"].(map[string]any); ok {
		publicKey := firstOr(str(reality, "public-key"), str(reality, "public_key"))
		shortID := firstOr(str(reality, "short-id"), str(reality, "short_id"))
		if publicKey != "" {
			tls["reality"] = map[string]any{"enabled": true, "public_key": publicKey, "short_id": shortID}
		}
	}
	base["tls"] = tls
}

func ensureTLS(base map[string]any) {
	if _, ok := base["tls"]; !ok {
		base["tls"] = map[string]any{"enabled": true}
	}
}
func boolValue(m map[string]any, key string) bool {
	v, ok := m[key]
	if !ok {
		return false
	}
	switch x := v.(type) {
	case bool:
		return x
	case string:
		return strings.EqualFold(x, "true")
	}
	return false
}
func addTransport(base map[string]any, m map[string]any) {
	network := strings.ToLower(str(m, "network"))
	if network == "" {
		return
	}
	opts, _ := m[network+"-opts"].(map[string]any)
	tr := map[string]any{"type": network}
	switch network {
	case "ws":
		tr["type"] = "ws"
		tr["path"] = str(opts, "path")
		if h, ok := opts["headers"].(map[string]any); ok {
			tr["headers"] = h
		}
	case "grpc":
		tr["type"] = "grpc"
		tr["service_name"] = firstOr(str(opts, "grpc-service-name"), str(opts, "serviceName"))
	case "http":
		tr["type"] = "http"
		tr["path"] = str(opts, "path")
	}
	base["transport"] = tr
}
func addHysteria2(base map[string]any, m map[string]any) {
	if obfs := str(m, "obfs"); obfs != "" {
		o := map[string]any{"type": obfs}
		if p := str(m, "obfs-password"); p != "" {
			o["password"] = p
		}
		base["obfs"] = o
	}
	if n := intValue(m, "up", 0); n > 0 {
		base["up_mbps"] = n
	}
	if n := intValue(m, "down", 0); n > 0 {
		base["down_mbps"] = n
	}
}

func sanitizeTag(s string) string {
	s = strings.Join(strings.Fields(s), " ")
	s = strings.ReplaceAll(s, "\u0000", "")
	return s
}

var flagRe = regexp.MustCompile("[🇦-🇿]{2}")
var flagNames = map[string]string{"🇭🇰": "香港", "🇹🇼": "台湾", "🇯🇵": "日本", "🇸🇬": "新加坡", "🇺🇸": "美国", "🇰🇷": "韩国", "🇬🇧": "英国", "🇩🇪": "德国", "🇫🇷": "法国", "🇨🇦": "加拿大", "🇦🇺": "澳大利亚", "🇳🇱": "荷兰", "🇷🇺": "俄罗斯", "🇮🇳": "印度", "🇹🇷": "土耳其", "🇮🇹": "意大利", "🇪🇸": "西班牙", "🇦🇹": "奥地利", "🇨🇭": "瑞士", "🇸🇪": "瑞典", "🇳🇴": "挪威", "🇫🇮": "芬兰", "🇵🇱": "波兰", "🇮🇱": "以色列", "🇧🇷": "巴西"}

func classifyRegion(name string) (string, string) {
	flag := flagRe.FindString(name)
	if flag == "" {
		return "未识别", "unknown"
	}
	if region, ok := flagNames[flag]; ok {
		return region, flag
	}
	return "未识别", "unknown"
}
