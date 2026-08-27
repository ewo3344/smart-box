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
	"sync/atomic"
	"testing"
	"time"
)

func TestGeneratedProfileAcceptedByCore(t *testing.T) {
	corePath := os.Getenv("SMART_BOX_CORE")
	if corePath == "" {
		t.Skip("set SMART_BOX_CORE to run the core compatibility check")
	}

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
	corePath := os.Getenv("SMART_BOX_CORE")
	if corePath == "" {
		t.Skip("set SMART_BOX_CORE to run the core startup compatibility check")
	}

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
