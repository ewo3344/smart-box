package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestRequiredRuleSetCatalog(t *testing.T) {
	seen := make(map[string]bool, len(requiredRuleSets))
	for _, spec := range requiredRuleSets {
		if !validRuleSetTag(spec.Tag) {
			t.Errorf("invalid tag %q", spec.Tag)
		}
		if seen[spec.Tag] {
			t.Errorf("duplicate tag %q", spec.Tag)
		}
		seen[spec.Tag] = true
		if !strings.HasPrefix(spec.URL, "https://") || !strings.HasSuffix(spec.URL, ".srs") {
			t.Errorf("invalid source URL for %s", spec.Tag)
		}
	}
	for _, required := range []string{"ads", "ai", "telegram", "netflix", "media-ip", "developer", "apple-cn", "google", "douyin", "cn", "cn-ip"} {
		if !seen[required] {
			t.Errorf("catalog is missing %s", required)
		}
	}
}

func TestValidateSRS(t *testing.T) {
	for version := byte(1); version <= 5; version++ {
		if err := validateSRS([]byte{'S', 'R', 'S', version, 1}); err != nil {
			t.Errorf("version %d rejected: %v", version, err)
		}
	}
	for name, content := range map[string][]byte{
		"empty":        nil,
		"truncated":    {'S', 'R', 'S'},
		"wrong magic":  {'N', 'O', 'T', 1},
		"version zero": {'S', 'R', 'S', 0},
		"version six":  {'S', 'R', 'S', 6},
	} {
		t.Run(name, func(t *testing.T) {
			if err := validateSRS(content); err == nil {
				t.Fatal("invalid SRS was accepted")
			}
		})
	}
}

func TestRuleSetRefreshUsesLastKnownGoodCache(t *testing.T) {
	var responseMode atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch responseMode.Load() {
		case 1:
			http.Error(w, "temporary failure", http.StatusServiceUnavailable)
		case 2:
			_, _ = io.WriteString(w, "not-an-srs")
		default:
			_, _ = w.Write(testSRS(1, r.URL.Path))
		}
	}))
	defer server.Close()

	specs := []RuleSetSpec{
		{Tag: "first", URL: server.URL + "/first.srs"},
		{Tag: "second", URL: server.URL + "/second.srs"},
	}
	store := newRuleSetStore(filepath.Join(t.TempDir(), "rules"), server.Client(), specs)
	status, err := store.refresh(context.Background(), true)
	if err != nil {
		t.Fatal(err)
	}
	if status.Downloaded != 2 || status.Fallback != 0 || !store.ready() {
		t.Fatalf("initial status = %#v", status)
	}
	first, _ := store.get("first")

	responseMode.Store(1)
	status, err = store.refresh(context.Background(), true)
	if err != nil {
		t.Fatalf("HTTP failure should retain cache: %v", err)
	}
	if status.Fallback != 2 || status.Missing != 0 {
		t.Fatalf("HTTP fallback status = %#v", status)
	}
	if current, _ := store.get("first"); !bytes.Equal(current.Content, first.Content) {
		t.Fatal("HTTP failure replaced the last-known-good rule set")
	}

	responseMode.Store(2)
	status, err = store.refresh(context.Background(), true)
	if err != nil {
		t.Fatalf("invalid response should retain cache: %v", err)
	}
	if status.Fallback != 2 || status.Missing != 0 {
		t.Fatalf("invalid-SRS fallback status = %#v", status)
	}
	if current, _ := store.get("first"); !bytes.Equal(current.Content, first.Content) {
		t.Fatal("invalid SRS replaced the last-known-good rule set")
	}

	reloaded := newRuleSetStore(store.cacheDir, server.Client(), specs)
	status, err = reloaded.loadCache()
	if err != nil || status.Cached != 2 || !reloaded.ready() {
		t.Fatalf("reload status = %#v, err = %v", status, err)
	}
	info, err := os.Stat(filepath.Join(store.cacheDir, "first.srs"))
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("cache permissions = %o, want 600", info.Mode().Perm())
	}
}

func TestRuleSetRefreshFailsWithoutAnyValidCopy(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
	}))
	defer server.Close()

	store := newRuleSetStore(filepath.Join(t.TempDir(), "rules"), server.Client(), []RuleSetSpec{{Tag: "missing", URL: server.URL + "/missing.srs"}})
	status, err := store.refresh(context.Background(), true)
	if err == nil {
		t.Fatal("first refresh without a valid rule set must fail")
	}
	if status.Missing != 1 || store.ready() {
		t.Fatalf("status = %#v", status)
	}
}

func TestRuleSetReadyRequiresEveryConfiguredTag(t *testing.T) {
	store := newRuleSetStore(t.TempDir(), nil, []RuleSetSpec{
		{Tag: "first", URL: "https://example.com/first.srs"},
		{Tag: "second", URL: "https://example.com/second.srs"},
	})
	store.entries["first"] = ruleSetEntry{}
	store.entries["unexpected"] = ruleSetEntry{}
	if store.ready() {
		t.Fatal("same-sized entry map with a missing configured tag must not be ready")
	}
	store.entries["second"] = ruleSetEntry{}
	if !store.ready() {
		t.Fatal("store with every configured tag must be ready")
	}
}

func TestRuleSetEndpointRequiresTokenAndSupportsETag(t *testing.T) {
	content := testSRS(5, "endpoint")
	hash := sha256Hex(content)
	store := newRuleSetStore(t.TempDir(), nil, []RuleSetSpec{{Tag: "test", URL: "https://example.com/test.srs"}})
	store.entries["test"] = ruleSetEntry{Content: content, Hash: hash, Modified: time.Unix(1_700_000_000, 0).UTC()}
	app := &App{config: Config{PublicPath: "abcdefghijklmnopqrstuvwxyz123456"}, rules: store}

	request := httptest.NewRequest(http.MethodGet, "/rule-set/abcdefghijklmnopqrstuvwxyz123456/test.srs", nil)
	response := httptest.NewRecorder()
	app.ruleSet(response, request)
	if response.Code != http.StatusOK || !bytes.Equal(response.Body.Bytes(), content) {
		t.Fatalf("valid response = %d %q", response.Code, response.Body.Bytes())
	}
	etag := response.Header().Get("ETag")
	if etag == "" || response.Header().Get("Cache-Control") == "" {
		t.Fatal("rule-set response is missing cache headers")
	}

	request = httptest.NewRequest(http.MethodGet, "/rule-set/abcdefghijklmnopqrstuvwxyz123456/test.srs", nil)
	request.Header.Set("If-None-Match", etag)
	response = httptest.NewRecorder()
	app.ruleSet(response, request)
	if response.Code != http.StatusNotModified || response.Body.Len() != 0 {
		t.Fatalf("conditional response = %d with %d bytes", response.Code, response.Body.Len())
	}

	for _, path := range []string{
		"/rule-set/wrong-token/test.srs",
		"/rule-set/abcdefghijklmnopqrstuvwxyz123456/unknown.srs",
		"/rule-set/abcdefghijklmnopqrstuvwxyz123456/test.srs/extra",
	} {
		response = httptest.NewRecorder()
		app.ruleSet(response, httptest.NewRequest(http.MethodGet, path, nil))
		if response.Code != http.StatusNotFound {
			t.Errorf("%s returned %d, want 404", path, response.Code)
		}
	}
}

func TestLoadConfigValidatesPublicOrigin(t *testing.T) {
	tests := []struct {
		name      string
		publicURL string
		wantError bool
	}{
		{name: "http TEST-NET origin", publicURL: "http://192.0.2.102:38473/"},
		{name: "https origin", publicURL: "https://box.example.com"},
		{name: "path", publicURL: "https://box.example.com/converter", wantError: true},
		{name: "query", publicURL: "https://box.example.com?x=1", wantError: true},
		{name: "credentials", publicURL: "https://user:pass@box.example.com", wantError: true},
		{name: "wrong scheme", publicURL: "ftp://box.example.com", wantError: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			directory := t.TempDir()
			config := map[string]any{
				"listen": "127.0.0.1:38473", "public_url": test.publicURL,
				"public_path": "abcdefghijklmnopqrstuvwxyz123456", "cache_dir": filepath.Join(directory, "cache"),
				"sources": []any{map[string]any{"name": "test", "url": "https://provider.example/test"}},
			}
			content, err := json.Marshal(config)
			if err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(directory, "config.json")
			if err := os.WriteFile(path, content, 0o600); err != nil {
				t.Fatal(err)
			}
			loaded, err := loadConfig(path)
			if test.wantError {
				if err == nil {
					t.Fatalf("invalid public URL was accepted as %q", loaded.PublicURL)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if strings.HasSuffix(loaded.PublicURL, "/") {
				t.Fatalf("public URL was not normalized: %q", loaded.PublicURL)
			}
		})
	}
}

func TestLiveRuleSetSourcesAcceptedByCore(t *testing.T) {
	if os.Getenv("SMART_BOX_LIVE_RULESETS") != "1" {
		t.Skip("set SMART_BOX_LIVE_RULESETS=1 to verify every upstream rule set")
	}
	corePath := os.Getenv("SMART_BOX_CORE")
	if corePath == "" {
		t.Fatal("SMART_BOX_CORE is required for live rule-set validation")
	}
	store := newRuleSetStore(filepath.Join(t.TempDir(), "rules"), &http.Client{Timeout: 60 * time.Second}, requiredRuleSets)
	status, err := store.refresh(context.Background(), true)
	if err != nil {
		t.Fatalf("download live rule sets: %v (status %#v)", err, status)
	}
	if status.Downloaded != len(requiredRuleSets) || !store.ready() {
		t.Fatalf("live rule-set status = %#v", status)
	}
	for _, spec := range requiredRuleSets {
		path, err := store.cachePath(spec.Tag)
		if err != nil {
			t.Fatal(err)
		}
		output, err := exec.Command(corePath, "rule-set", "match", "-f", "binary", path, "smart-box-validation.invalid").CombinedOutput()
		if err != nil {
			t.Errorf("core rejected %s: %v\n%s", spec.Tag, err, output)
		}
	}
}

func testSRS(version byte, marker string) []byte {
	return append([]byte{'S', 'R', 'S', version}, marker...)
}

func sha256Hex(content []byte) string {
	hash := sha256.Sum256(content)
	return hex.EncodeToString(hash[:])
}
