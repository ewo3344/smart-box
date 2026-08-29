package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	ruleSetMaxBytes    int64 = 4 << 20
	ruleSetCacheMaxAge       = 24 * time.Hour
	ruleSetWorkers           = 8
)

type RuleSetSpec struct {
	Tag string
	URL string
}

type RuleSetStatus struct {
	Total      int       `json:"total"`
	Downloaded int       `json:"downloaded"`
	Cached     int       `json:"cached"`
	Fallback   int       `json:"fallback"`
	Missing    int       `json:"missing"`
	CheckedAt  time.Time `json:"checked_at"`
}

type ruleSetEntry struct {
	Content  []byte
	Hash     string
	Modified time.Time
}

type RuleSetStore struct {
	cacheDir string
	client   *http.Client
	specs    []RuleSetSpec
	maxAge   time.Duration
	maxBytes int64

	mu      sync.RWMutex
	entries map[string]ruleSetEntry
	status  RuleSetStatus
}

var requiredRuleSets = []RuleSetSpec{
	dustinRuleSet("private", "private"),
	dustinRuleSet("ads", "ads"),
	dustinRuleSet("applications", "applications"),
	dustinRuleSet("games", "games"),
	dustinRuleSet("games-cn", "games-cn"),
	dustinRuleSet("netflix", "netflix"),
	dustinRuleSet("netflix-ip", "netflixip"),
	dustinRuleSet("disney", "disney"),
	dustinRuleSet("max", "max"),
	dustinRuleSet("prime-video", "primevideo"),
	dustinRuleSet("apple-tv", "appletv"),
	dustinRuleSet("youtube", "youtube"),
	dustinRuleSet("tiktok", "tiktok"),
	dustinRuleSet("bilibili", "bilibili"),
	dustinRuleSet("spotify", "spotify"),
	dustinRuleSet("media", "media"),
	dustinRuleSet("media-ip", "mediaip"),
	dustinRuleSet("network-test", "networktest"),
	dustinRuleSet("cn", "cn"),
	dustinRuleSet("cn-ip", "cnip"),
	dustinRuleSet("telegram-ip", "telegramip"),
	sagerRuleSet("ai", "category-ai-!cn"),
	sagerRuleSet("telegram", "telegram"),
	sagerRuleSet("discord", "discord"),
	sagerRuleSet("twitter", "twitter"),
	sagerRuleSet("facebook", "facebook"),
	sagerRuleSet("instagram", "instagram"),
	sagerRuleSet("whatsapp", "whatsapp"),
	sagerRuleSet("github", "github"),
	sagerRuleSet("developer", "category-dev"),
	sagerRuleSet("docker", "docker"),
	sagerRuleSet("npmjs", "npmjs"),
	sagerRuleSet("apple", "apple"),
	sagerRuleSet("apple-cn", "apple@cn"),
	sagerRuleSet("microsoft", "microsoft"),
	sagerRuleSet("microsoft-cn", "microsoft@cn"),
	sagerRuleSet("google", "google"),
	sagerRuleSet("google-cn", "google@cn"),
	sagerRuleSet("douyin", "douyin"),
}

func dustinRuleSet(tag, asset string) RuleSetSpec {
	return RuleSetSpec{
		Tag: tag,
		URL: "https://github.com/DustinWin/ruleset_geodata/releases/download/sing-box-ruleset/" + asset + ".srs",
	}
}

func sagerRuleSet(tag, asset string) RuleSetSpec {
	return RuleSetSpec{
		Tag: tag,
		URL: "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-" + asset + ".srs",
	}
}

func newRuleSetStore(cacheDir string, client *http.Client, specs []RuleSetSpec) *RuleSetStore {
	if client == nil {
		client = &http.Client{Timeout: 60 * time.Second}
	}
	return &RuleSetStore{
		cacheDir: cacheDir,
		client:   client,
		specs:    append([]RuleSetSpec(nil), specs...),
		maxAge:   ruleSetCacheMaxAge,
		maxBytes: ruleSetMaxBytes,
		entries:  make(map[string]ruleSetEntry, len(specs)),
	}
}

func (s *RuleSetStore) loadCache() (RuleSetStatus, error) {
	if err := os.MkdirAll(s.cacheDir, 0o700); err != nil {
		return RuleSetStatus{}, fmt.Errorf("create rule-set cache: %w", err)
	}

	entries := make(map[string]ruleSetEntry, len(s.specs))
	missing := make([]string, 0)
	for _, spec := range s.specs {
		entry, err := s.readCache(spec.Tag)
		if err != nil {
			missing = append(missing, spec.Tag)
			continue
		}
		entries[spec.Tag] = entry
	}
	status := RuleSetStatus{
		Total:     len(s.specs),
		Cached:    len(entries),
		Missing:   len(missing),
		CheckedAt: time.Now().UTC(),
	}
	s.mu.Lock()
	s.entries = entries
	s.status = status
	s.mu.Unlock()
	if len(missing) > 0 {
		return status, fmt.Errorf("missing or invalid cached rule sets: %s", strings.Join(missing, ", "))
	}
	return status, nil
}

func (s *RuleSetStore) refresh(ctx context.Context, force bool) (RuleSetStatus, error) {
	type result struct {
		spec  RuleSetSpec
		entry ruleSetEntry
		err   error
	}

	now := time.Now().UTC()
	s.mu.RLock()
	current := make(map[string]ruleSetEntry, len(s.entries))
	for tag, entry := range s.entries {
		current[tag] = entry
	}
	s.mu.RUnlock()

	toDownload := make([]RuleSetSpec, 0, len(s.specs))
	for _, spec := range s.specs {
		entry, exists := current[spec.Tag]
		if force || !exists || now.Sub(entry.Modified) >= s.maxAge {
			toDownload = append(toDownload, spec)
		}
	}

	results := make(chan result, len(toDownload))
	workerLimit := make(chan struct{}, ruleSetWorkers)
	var wg sync.WaitGroup
	for _, spec := range toDownload {
		spec := spec
		wg.Add(1)
		go func() {
			defer wg.Done()
			select {
			case workerLimit <- struct{}{}:
				defer func() { <-workerLimit }()
			case <-ctx.Done():
				results <- result{spec: spec, err: ctx.Err()}
				return
			}
			content, err := s.fetch(ctx, spec)
			if err != nil {
				results <- result{spec: spec, err: err}
				return
			}
			if err := s.writeCache(spec.Tag, content); err != nil {
				results <- result{spec: spec, err: fmt.Errorf("cache write: %w", err)}
				return
			}
			hash := sha256.Sum256(content)
			results <- result{spec: spec, entry: ruleSetEntry{
				Content:  content,
				Hash:     hex.EncodeToString(hash[:]),
				Modified: now,
			}}
		}()
	}
	wg.Wait()
	close(results)

	downloaded := 0
	fallback := 0
	failedWithoutCache := make([]string, 0)
	failureClasses := make(map[string]int)
	for result := range results {
		if result.err == nil {
			current[result.spec.Tag] = result.entry
			downloaded++
			continue
		}
		if _, exists := current[result.spec.Tag]; exists {
			fallback++
			failureClasses[classifyRuleSetError(result.err)]++
			continue
		}
		failureClasses[classifyRuleSetError(result.err)]++
		failedWithoutCache = append(failedWithoutCache, result.spec.Tag)
	}

	sort.Strings(failedWithoutCache)
	status := RuleSetStatus{
		Total:      len(s.specs),
		Downloaded: downloaded,
		Cached:     len(s.specs) - len(toDownload),
		Fallback:   fallback,
		Missing:    len(failedWithoutCache),
		CheckedAt:  now,
	}
	s.mu.Lock()
	s.entries = current
	s.status = status
	s.mu.Unlock()
	if len(failedWithoutCache) > 0 {
		return status, fmt.Errorf("required rule sets unavailable (%s): %s", formatFailureClasses(failureClasses), strings.Join(failedWithoutCache, ", "))
	}
	return status, nil
}

func classifyRuleSetError(err error) string {
	message := strings.ToLower(err.Error())
	switch {
	case strings.Contains(message, "cache write"), strings.Contains(message, "permission denied"), strings.Contains(message, "read-only file system"):
		return "cache"
	case strings.Contains(message, "invalid srs"), strings.Contains(message, "truncated srs"), strings.Contains(message, "unsupported srs"):
		return "format"
	case strings.Contains(message, "status"):
		return "http"
	case strings.Contains(message, "timeout"), strings.Contains(message, "deadline exceeded"):
		return "timeout"
	case strings.Contains(message, "x509"), strings.Contains(message, "tls"):
		return "tls"
	case strings.Contains(message, "no such host"), strings.Contains(message, "server misbehaving"):
		return "dns"
	case strings.Contains(message, "redirect"):
		return "redirect"
	default:
		return "network"
	}
}

func formatFailureClasses(classes map[string]int) string {
	keys := make([]string, 0, len(classes))
	for class := range classes {
		keys = append(keys, class)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, class := range keys {
		parts = append(parts, fmt.Sprintf("%s=%d", class, classes[class]))
	}
	return strings.Join(parts, ", ")
}

func (s *RuleSetStore) fetch(ctx context.Context, spec RuleSetSpec) ([]byte, error) {
	u, err := url.Parse(spec.URL)
	if err != nil || u.Scheme != "https" || u.Host == "" {
		return nil, errors.New("rule-set source must use https")
	}

	var lastErr error
	for attempt := 0; attempt < 2; attempt++ {
		req, requestErr := http.NewRequestWithContext(ctx, http.MethodGet, spec.URL, nil)
		if requestErr != nil {
			return nil, requestErr
		}
		req.Header.Set("User-Agent", "smart-box-converter/1")
		resp, requestErr := s.client.Do(req)
		if requestErr != nil {
			lastErr = requestErr
			continue
		}
		content, readErr := s.readResponse(resp)
		_ = resp.Body.Close()
		if readErr == nil {
			return content, nil
		}
		lastErr = readErr
		if resp.StatusCode >= 400 && resp.StatusCode < 500 {
			break
		}
	}
	return nil, lastErr
}

func (s *RuleSetStore) readResponse(resp *http.Response) ([]byte, error) {
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("rule-set source status %d", resp.StatusCode)
	}
	if resp.Request == nil || resp.Request.URL == nil || resp.Request.URL.Scheme != "https" {
		return nil, errors.New("rule-set source redirected away from https")
	}
	if resp.ContentLength > s.maxBytes {
		return nil, fmt.Errorf("rule-set exceeds %d bytes", s.maxBytes)
	}
	content, err := io.ReadAll(io.LimitReader(resp.Body, s.maxBytes+1))
	if err != nil {
		return nil, err
	}
	if int64(len(content)) > s.maxBytes {
		return nil, fmt.Errorf("rule-set exceeds %d bytes", s.maxBytes)
	}
	if err := validateSRS(content); err != nil {
		return nil, err
	}
	return content, nil
}

func validateSRS(content []byte) error {
	if len(content) < 4 {
		return errors.New("truncated SRS file")
	}
	if string(content[:3]) != "SRS" {
		return errors.New("invalid SRS magic")
	}
	if content[3] < 1 || content[3] > 5 {
		return fmt.Errorf("unsupported SRS version %d", content[3])
	}
	return nil
}

func (s *RuleSetStore) readCache(tag string) (ruleSetEntry, error) {
	path, err := s.cachePath(tag)
	if err != nil {
		return ruleSetEntry{}, err
	}
	info, err := os.Stat(path)
	if err != nil {
		return ruleSetEntry{}, err
	}
	if !info.Mode().IsRegular() || info.Size() > s.maxBytes {
		return ruleSetEntry{}, errors.New("invalid cached rule-set file")
	}
	content, err := os.ReadFile(path)
	if err != nil {
		return ruleSetEntry{}, err
	}
	if err := validateSRS(content); err != nil {
		return ruleSetEntry{}, err
	}
	hash := sha256.Sum256(content)
	return ruleSetEntry{
		Content:  content,
		Hash:     hex.EncodeToString(hash[:]),
		Modified: info.ModTime().UTC(),
	}, nil
}

func (s *RuleSetStore) writeCache(tag string, content []byte) error {
	path, err := s.cachePath(tag)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(s.cacheDir, 0o700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(s.cacheDir, "."+tag+"-*.tmp")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if err := tmp.Chmod(0o600); err != nil {
		_ = tmp.Close()
		return err
	}
	if _, err := tmp.Write(content); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, path)
}

func (s *RuleSetStore) cachePath(tag string) (string, error) {
	if !validRuleSetTag(tag) {
		return "", errors.New("invalid rule-set tag")
	}
	return filepath.Join(s.cacheDir, tag+".srs"), nil
}

func validRuleSetTag(tag string) bool {
	if tag == "" {
		return false
	}
	for _, char := range tag {
		if (char >= 'a' && char <= 'z') || (char >= '0' && char <= '9') || char == '-' {
			continue
		}
		return false
	}
	return true
}

func (s *RuleSetStore) get(tag string) (ruleSetEntry, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	entry, exists := s.entries[tag]
	return entry, exists
}

func (s *RuleSetStore) ready() bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, spec := range s.specs {
		if _, exists := s.entries[spec.Tag]; !exists {
			return false
		}
	}
	return true
}

func (s *RuleSetStore) currentStatus() RuleSetStatus {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.status
}

func ruleSetTags() []string {
	tags := make([]string, 0, len(requiredRuleSets))
	for _, spec := range requiredRuleSets {
		tags = append(tags, spec.Tag)
	}
	return tags
}
