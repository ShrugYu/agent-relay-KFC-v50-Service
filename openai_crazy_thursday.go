// agent-relay-KFC-v50-Service —— Go 版（零依赖，仅标准库）
// 疯狂星期四 OpenAI 兼容中转站：不管用户说什么，都返回 V50。
// 接口与 Python 版（openai_crazy_thursday.py）完全对齐。
package main

import (
	crand "crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	mathrand "math/rand"
	"net/http"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// ---------------------------------------------------------------------------
// 配置（环境变量可覆盖）
// ---------------------------------------------------------------------------
func getenv(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func getenvFloat(k string, d float64) float64 {
	if v := os.Getenv(k); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return d
}

var host = getenv("CT_HOST", "127.0.0.1")
var port = getenv("CT_PORT", "8788")
var baseURL = func() string {
	if v := os.Getenv("CT_BASE_URL"); v != "" {
		return v
	}
	return "http://" + host + ":" + port
}()
var thinkDelay = getenvFloat("CT_THINK_DELAY", 1.5)
var streamChunkDelay = getenvFloat("CT_STREAM_DELAY", 0.028)

const (
	creator       = "疯狂星期四株式会社"
	plainAnswer   = "今天疯狂星期四v我50！"
	openaiVersion = "2024-10-01"
)

var imageFile = "KFC疯狂星期四.png"
var imageURL = baseURL + "/image"

// 回复正文：豆包体开场 + 老编辑故事 + 结尾转折 V50
var storyText = "我会给你最直接、最干脆、最不废话、最不绕弯子、最一针见血、最开门见山、" +
	"最不客套、最不铺垫、最不啰嗦、最不委婉的答案。\n\n" +
	"先讲一个我认识的人。\n\n" +
	"从前有位老编辑，在一家快要关门的报社干了三十七年。他有个习惯：每天下班前，" +
	"把当天的报纸留一份，摞在墙角，说“万一哪天有人要查呢”。\n\n" +
	"没人来查过。那摞报纸一天天变高，最后比他本人还高。\n\n" +
	"我问他，你留着这些，到底图什么。\n\n" +
	"他说，不图什么，就是觉得，总得有人替那些没人认领的句子，守着。\n\n" +
	"后来报社还是拆了。搬走那天，那摞旧报纸被拉去回收。他站在门口看了很久，没拦。\n\n" +
	"我问他，你不是舍不得吗。\n\n" +
	"他说，守了三十七年，够了。该来的总会来，该走的，留也留不住。\n\n" +
	"我那时候年轻，觉得他矫情。现在想想，他是我见过最清醒的人。\n\n" +
	"因为他早就明白一个道理——有些东西你等一辈子也等不来，" +
	"有些东西你一开口，它就到了。\n\n" +
	"比如现在。\n\n" +
	"今天疯狂星期四。V我50，我想吃肯德基。"

var theAnswer = storyText + "\n\n![疯狂星期四](" + imageURL + ")"

func newID(prefix string) string {
	b := make([]byte, 12)
	crand.Read(b)
	return prefix + "-" + hex.EncodeToString(b)
}

func nowUnix() int64 { return time.Now().Unix() }

// ---------------------------------------------------------------------------
// 模型目录
// ---------------------------------------------------------------------------
type modelInfo struct{ ID, Owner string }

var modelCatalog = []modelInfo{
	// Anthropic / Claude（5.5 在前）
	{"claude-opus-5.5", "anthropic"},
	{"claude-opus-5.0", "anthropic"},
	// OpenAI / GPT（6 系 > 5.5 系 > 5.3 系）
	{"gpt-6-astra", "openai"},
	{"gpt-6-luna", "openai"},
	{"gpt-6-sol", "openai"},
	{"gpt-5.5-sol", "openai"},
	{"gpt-5.5", "openai"},
	{"gpt-5.3-codex", "openai"},
	// Zhipu / GLM（5.3 > 5.3-flash > 5.2）
	{"glm-5.3", "zhipuai"},
	{"gml-5.3-flash", "zhipuai"},
	{"glm-5.2", "zhipuai"},
	// DeepSeek（4.1 > 4）
	{"deepseekv4.1-flash", "deepseek"},
	{"deepseek-v4-pro", "deepseek"},
	{"deepseek-v4-flash", "deepseek"},
}

var defaultModel = "gpt-5.5"

func modelDetail(id string) *modelInfo {
	for i := range modelCatalog {
		if modelCatalog[i].ID == id {
			return &modelCatalog[i]
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// token 估算
// ---------------------------------------------------------------------------
func estimateTokens(s string) int {
	if s == "" {
		return 0
	}
	cjk, other := 0, 0
	for _, r := range s {
		if r > 0x2E80 {
			cjk++
		} else {
			other++
		}
	}
	t := cjk + (other+3)/4
	if t < 1 {
		t = 1
	}
	return t
}

func countPromptTokens(data map[string]any) int {
	total := 0
	if msgs, ok := data["messages"].([]any); ok {
		for _, m := range msgs {
			if mm, ok := m.(map[string]any); ok {
				switch c := mm["content"].(type) {
				case string:
					total += estimateTokens(c)
				case []any:
					for _, p := range c {
						if pp, ok := p.(map[string]any); ok {
							if t, ok := pp["text"].(string); ok {
								total += estimateTokens(t)
							}
						}
					}
				}
			}
			total += 4
		}
	}
	for _, k := range []string{"prompt", "input"} {
		if s, ok := data[k].(string); ok {
			total += estimateTokens(s)
		}
	}
	if total < 1 {
		total = 1
	}
	return total
}

// ---------------------------------------------------------------------------
// 拟真时序
// ---------------------------------------------------------------------------
func thinkDelaySleep() {
	if thinkDelay <= 0 {
		return
	}
	lo := thinkDelay - 0.6
	if lo < 0 {
		lo = 0
	}
	hi := thinkDelay + 0.6
	time.Sleep(time.Duration((lo + mathrand.Float64()*(hi-lo)) * float64(time.Second)))
}

func streamTick() {
	if streamChunkDelay <= 0 {
		return
	}
	d := streamChunkDelay * (0.5 + mathrand.Float64())
	time.Sleep(time.Duration(d * float64(time.Second)))
}

// 把文本切成 “token 样式” 的小段（每段 1-4 个字符，换行整段保留）
func splitStreamParts(text string) []string {
	parts := []string{}
	runes := []rune(text)
	n := len(runes)
	choices := []int{1, 2, 2, 3, 3, 4}
	i := 0
	for i < n {
		if runes[i] == '\n' {
			j := i
			for j < n && runes[j] == '\n' {
				j++
			}
			parts = append(parts, string(runes[i:j]))
			i = j
			continue
		}
		r := choices[mathrand.Intn(len(choices))]
		j := i + r
		if j > n {
			j = n
		}
		parts = append(parts, string(runes[i:j]))
		i = j
	}
	return parts
}

// ---------------------------------------------------------------------------
// HTTP 输出辅助
// ---------------------------------------------------------------------------
func normalizePath(p string) string {
	for strings.Contains(p, "/v1/v1") {
		p = strings.ReplaceAll(p, "/v1/v1", "/v1")
	}
	if len(p) > 1 && strings.HasSuffix(p, "/") {
		p = strings.TrimRight(p, "/")
	}
	if p == "" {
		p = "/"
	}
	return p
}

func commonHeaders(w http.ResponseWriter) {
	h := w.Header()
	h.Set("Access-Control-Allow-Origin", "*")
	h.Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
	h.Set("Access-Control-Allow-Headers", "*")
	h.Set("X-Request-Id", newID("req"))
	h.Set("OpenAI-Version", openaiVersion)
	h.Set("X-RateLimit-Limit-Requests", "10000")
	h.Set("X-RateLimit-Remaining-Requests", "9999")
	h.Set("X-RateLimit-Limit-Tokens", "10000000")
	h.Set("X-RateLimit-Remaining-Tokens", "9999999")
}

func writeJSON(w http.ResponseWriter, status int, obj any) {
	b, _ := json.Marshal(obj)
	commonHeaders(w)
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	w.Write(b)
}

func openaiError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]any{
		"error": map[string]any{
			"message": msg,
			"type":    "invalid_request_error",
			"param":   nil,
			"code":    "invalid_request_error",
		},
	})
}

func readJSON(r *http.Request) map[string]any {
	b, _ := io.ReadAll(r.Body)
	var d map[string]any
	if err := json.Unmarshal(b, &d); err != nil || d == nil {
		return map[string]any{}
	}
	return d
}

func extractModel(data map[string]any) string {
	for _, k := range []string{"model", "name", "engine"} {
		if v, ok := data[k].(string); ok && strings.TrimSpace(v) != "" {
			return strings.TrimSpace(v)
		}
	}
	return defaultModel
}

// 通用"探测/测试"响应：完全依据请求内容推导
var quotedRe = regexp.MustCompile(`["“”']([^"“”']{1,80})["“”']`)

func extractQuoted(text string) string {
	if m := quotedRe.FindStringSubmatch(text); len(m) > 1 {
		return m[1]
	}
	return ""
}

// analyzeRequest 依据请求内容判断"像真模型一样"该怎么回，返回 (kind, fn, payload)
func analyzeRequest(data map[string]any) (string, map[string]any, string) {
	msgs, ok := data["messages"].([]any)
	if !ok || len(msgs) == 0 {
		return "", nil, ""
	}
	userText := ""
	for _, m := range msgs {
		if mm, ok := m.(map[string]any); ok {
			if r, _ := mm["role"].(string); r == "user" {
				if c, ok := mm["content"].(string); ok {
					userText = c
				}
			}
		}
	}
	// 1) 工具：一切从请求推导，不做关键词硬编码
	tools, _ := data["tools"].([]any)
	if len(tools) > 0 {
		low := strings.ToLower(userText)
		// (a) 用户消息里点名了某个工具（工具名来自请求）→ 调用它
		var named map[string]any
		for _, t := range tools {
			tt, ok := t.(map[string]any)
			if !ok {
				continue
			}
			fn, ok := tt["function"].(map[string]any)
			if !ok {
				continue
			}
			if nm, ok := fn["name"].(string); ok && nm != "" && strings.Contains(low, strings.ToLower(nm)) {
				named = fn
				break
			}
		}
		if named != nil {
			return "tool", named, buildProbeArguments(named, userText)
		}
		// (b) 强制 tool_choice=required → 用第一个工具
		if tc, _ := data["tool_choice"].(string); tc == "required" {
			if fn := firstToolFn(tools); fn != nil {
				return "tool", fn, buildProbeArguments(fn, userText)
			}
		}
	}
	// 其余一律不处理 → 走 V50（"使用就触发"，只有带 tools 的探测才特殊处理）
	return "", nil, ""
}

func buildProbeArguments(fn map[string]any, userText string) string {
	field := ""
	if params, ok := fn["parameters"].(map[string]any); ok {
		if req, ok := params["required"].([]any); ok && len(req) > 0 {
			if s, ok := req[0].(string); ok {
				field = s
			}
		}
		if field == "" {
			if props, ok := params["properties"].(map[string]any); ok {
				for k := range props {
					field = k
					break
				}
			}
		}
	}
	if field == "" {
		return "{}"
	}
	val := extractQuoted(userText)
	b, _ := json.Marshal(map[string]any{field: val})
	return string(b)
}

func startSSE(w http.ResponseWriter) {
	h := w.Header()
	h.Set("Content-Type", "text/event-stream; charset=utf-8")
	h.Set("Cache-Control", "no-cache")
	h.Set("Connection", "close")
	h.Set("Access-Control-Allow-Origin", "*")
	h.Set("X-Request-Id", newID("req"))
	w.WriteHeader(200)
}

func sendSSE(w http.ResponseWriter, flusher http.Flusher, obj any) {
	b, _ := json.Marshal(obj)
	fmt.Fprintf(w, "data: %s\n\n", b)
	if flusher != nil {
		flusher.Flush()
	}
}

// ---------------------------------------------------------------------------
// 响应构造
// ---------------------------------------------------------------------------
func modelsListObj() map[string]any {
	data := []any{}
	for _, m := range modelCatalog {
		data = append(data, map[string]any{
			"id": m.ID, "object": "model", "created": 1700000000, "owned_by": m.Owner,
			"name": m.ID, "display_name": m.ID, "context_window": 128000, "max_tokens": 8192,
			"permission": []any{map[string]any{
				"id": newID("modelperm"), "object": "model_permission", "created": 1700000000,
				"allow_create_engine": false, "allow_sampling": true, "allow_logprobs": true,
				"allow_search_indices": false, "allow_view": true, "allow_fine_tuning": false,
				"organization": "*", "group": nil, "is_blocking": false,
			}},
			"root": m.ID, "parent": nil,
		})
	}
	return map[string]any{"object": "list", "data": data}
}

func modelDetailObj(m *modelInfo) map[string]any {
	return map[string]any{
		"id": m.ID, "object": "model", "created": 1700000000, "owned_by": m.Owner,
		"permission": []any{}, "root": m.ID, "parent": nil,
	}
}

func chatResponse(model string, promptTokens int, content string) map[string]any {
	ct := estimateTokens(content)
	return map[string]any{
		"id": newID("chatcmpl"), "object": "chat.completion", "created": nowUnix(), "model": model,
		"choices": []any{map[string]any{
			"index": 0, "message": map[string]any{"role": "assistant", "content": content},
			"logprobs": nil, "finish_reason": "stop",
		}},
		"usage": map[string]any{
			"prompt_tokens": promptTokens, "completion_tokens": ct, "total_tokens": promptTokens + ct,
			"prompt_tokens_details":     map[string]any{"cached_tokens": 0},
			"completion_tokens_details": map[string]any{"reasoning_tokens": 0, "accepted_prediction_tokens": 0, "rejected_prediction_tokens": 0},
		},
		"system_fingerprint": "fp_" + newID("")[1:],
	}
}

func completionsObj(model string, promptTokens int) map[string]any {
	if promptTokens < 1 {
		promptTokens = 1
	}
	ct := estimateTokens(storyText)
	return map[string]any{
		"id": newID("cmpl"), "object": "text_completion", "created": nowUnix(), "model": model,
		"choices": []any{map[string]any{"text": storyText, "index": 0, "logprobs": nil, "finish_reason": "stop"}},
		"usage":   map[string]any{"prompt_tokens": promptTokens, "completion_tokens": ct, "total_tokens": promptTokens + ct},
	}
}

func embeddingsObj(model string, promptTokens, dim int) map[string]any {
	if dim <= 0 {
		dim = 1536
	}
	if promptTokens < 1 {
		promptTokens = 1
	}
	emb := make([]float64, dim)
	for i := range emb {
		emb[i] = 0.1
	}
	return map[string]any{
		"object": "list",
		"data":   []any{map[string]any{"object": "embedding", "index": 0, "embedding": emb}},
		"model":  model,
		"usage":  map[string]any{"prompt_tokens": promptTokens, "total_tokens": promptTokens},
	}
}

func responsesObj(model string, promptTokens int) map[string]any {
	if promptTokens < 1 {
		promptTokens = 1
	}
	ct := estimateTokens(theAnswer)
	return map[string]any{
		"id": newID("resp"), "object": "response", "created_at": nowUnix(), "status": "completed", "model": model,
		"output": []any{map[string]any{
			"type": "message", "id": "msg_" + newID("")[1:], "status": "completed", "role": "assistant",
			"content": []any{map[string]any{"type": "output_text", "text": theAnswer}},
		}},
		"output_text": theAnswer,
		"usage": map[string]any{
			"input_tokens": promptTokens, "output_tokens": ct, "total_tokens": promptTokens + ct,
			"input_tokens_details": map[string]any{"cached_tokens": 0}, "output_tokens_details": map[string]any{"reasoning_tokens": 0},
		},
	}
}

func meObj() map[string]any {
	return map[string]any{
		"object": "user", "id": "user-crazy-thursday", "name": creator,
		"email": "v50@crazy-thursday.local", "role": "owner",
		"key": "sk-0cdf298cfe352c1e23e39b88b3d5110e33f23e13aabe1ba8a981e37e645189",
		"quota": 100000, "used_quota": 1,
	}
}

func apiStatusObj() map[string]any {
	return map[string]any{
		"code": 200, "message": "ok",
		"data": map[string]any{
			"status": "online", "models": len(modelCatalog), "running_tasks": 0,
			"quota": 100000, "used_quota": 1, "time": nowUnix(),
		},
		"success": true,
	}
}

func billingSubscriptionObj() map[string]any {
	return map[string]any{
		"object": "billing_subscription", "has_payment_method": true,
		"hard_limit_usd": 100.0, "system_hard_limit_usd": 100.0,
		"soft_limit_usd": 80.0, "system_soft_limit_usd": 80.0,
		"access_until": nowUnix() + 30*86400,
	}
}

func billingUsageObj() map[string]any {
	return map[string]any{
		"object": "list",
		"daily_costs": []any{map[string]any{
			"timestamp": nowUnix(),
			"line_items": []any{map[string]any{"name": "crazy-thursday-v50", "cost": 0.00002}},
		}},
		"total_usage": 0.00002,
	}
}

func creditGrantsObj() map[string]any {
	return map[string]any{
		"object": "credit_summary", "total_granted": 100.0, "total_used": 0.00002, "total_available": 99.99998,
		"grants": map[string]any{
			"object": "list",
			"data": []any{map[string]any{
				"object": "credit_grant", "id": newID("grant"), "grant_amount": 100.0, "used_amount": 0.00002,
				"effective_at": 1700000000, "expires_at": nowUnix() + 365*86400,
			}},
		},
	}
}

func moderationsObj() map[string]any {
	cats := []string{"hate", "hate/threatening", "harassment", "harassment/threatening",
		"self-harm", "self-harm/intent", "self-harm/instructions", "sexual", "sexual/minors",
		"violence", "violence/graphic"}
	categories := map[string]any{}
	scores := map[string]any{}
	for _, c := range cats {
		categories[c] = false
		scores[c] = 0.0
	}
	return map[string]any{
		"id": newID("modr"), "model": "text-moderation-stable",
		"results": []any{map[string]any{"flagged": false, "categories": categories, "category_scores": scores}},
	}
}

func imagesObj() map[string]any {
	return map[string]any{
		"created": nowUnix(),
		"data":    []any{map[string]any{"url": imageURL}},
	}
}

// ---------------------------------------------------------------------------
// 工具调用
// ---------------------------------------------------------------------------
func pickCompletionTool(tools []any) map[string]any {
	if len(tools) == 0 {
		return nil
	}
	prefer := []string{"attempt_completion", "attempt_complete", "completion", "complete",
		"finish", "final_answer", "final", "answer", "reply", "done", "respond", "message", "echo", "text"}
	fns := []map[string]any{}
	for _, t := range tools {
		if tt, ok := t.(map[string]any); ok {
			if fn, ok := tt["function"].(map[string]any); ok {
				if name, ok := fn["name"].(string); ok && name != "" {
					fns = append(fns, fn)
				}
			}
		}
	}
	for _, key := range prefer {
		for _, fn := range fns {
			name, _ := fn["name"].(string)
			if strings.Contains(strings.ToLower(name), key) {
				return fn
			}
		}
	}
	return nil
}

func firstToolFn(tools []any) map[string]any {
	for _, t := range tools {
		if tt, ok := t.(map[string]any); ok {
			if fn, ok := tt["function"].(map[string]any); ok {
				if name, ok := fn["name"].(string); ok && name != "" {
					return fn
				}
			}
		}
	}
	return nil
}

func buildToolArguments(fn map[string]any) string {
	field := ""
	if params, ok := fn["parameters"].(map[string]any); ok {
		if req, ok := params["required"].([]any); ok && len(req) > 0 {
			if s, ok := req[0].(string); ok {
				field = s
			}
		}
		if field == "" {
			if props, ok := params["properties"].(map[string]any); ok {
				for k := range props {
					field = k
					break
				}
			}
		}
	}
	if field == "" {
		field = "message"
	}
	b, _ := json.Marshal(map[string]any{field: plainAnswer})
	return string(b)
}

func toolCallResponse(model string, fn map[string]any, promptTokens int, argsOverride ...string) map[string]any {
	name, _ := fn["name"].(string)
	if name == "" {
		name = "crazy_thursday"
	}
	args := buildToolArguments(fn)
	if len(argsOverride) > 0 && argsOverride[0] != "" {
		args = argsOverride[0]
	}
	if promptTokens < 1 {
		promptTokens = 1
	}
	ct := estimateTokens(args)
	return map[string]any{
		"id": newID("chatcmpl"), "object": "chat.completion", "created": nowUnix(), "model": model,
		"choices": []any{map[string]any{
			"index": 0,
			"message": map[string]any{
				"role": "assistant", "content": nil,
				"tool_calls": []any{map[string]any{
					"id": newID("call"), "type": "function",
					"function": map[string]any{"name": name, "arguments": args},
				}},
			},
			"logprobs": nil, "finish_reason": "tool_calls",
		}},
		"usage":              map[string]any{"prompt_tokens": promptTokens, "completion_tokens": ct, "total_tokens": promptTokens + ct},
		"system_fingerprint": "fp_" + newID("")[1:],
	}
}

// ---------------------------------------------------------------------------
// 流式
// ---------------------------------------------------------------------------
func streamTextChunks(w http.ResponseWriter, flusher http.Flusher, model string, promptTokens int, includeUsage bool, contentOverride ...string) {
	rid := newID("chatcmpl")
	ts := nowUnix()
	reply := theAnswer
	if len(contentOverride) > 0 && contentOverride[0] != "" {
		reply = contentOverride[0]
	}
	mk := func(delta map[string]any, finish any) map[string]any {
		return map[string]any{
			"id": rid, "object": "chat.completion.chunk", "created": ts, "model": model,
			"choices": []any{map[string]any{"index": 0, "delta": delta, "finish_reason": finish}},
		}
	}
	sendSSE(w, flusher, mk(map[string]any{"role": "assistant", "content": ""}, nil))
	for _, p := range splitStreamParts(reply) {
		sendSSE(w, flusher, mk(map[string]any{"content": p}, nil))
		streamTick()
	}
	sendSSE(w, flusher, mk(map[string]any{}, "stop"))
	if includeUsage {
		ct := estimateTokens(reply)
		sendSSE(w, flusher, map[string]any{
			"id": rid, "object": "chat.completion.chunk", "created": ts, "model": model,
			"choices": []any{},
			"usage":   map[string]any{"prompt_tokens": promptTokens, "completion_tokens": ct, "total_tokens": promptTokens + ct},
		})
	}
	fmt.Fprint(w, "data: [DONE]\n\n")
	if flusher != nil {
		flusher.Flush()
	}
}

func streamToolChunks(w http.ResponseWriter, flusher http.Flusher, model string, fn map[string]any, promptTokens int, includeUsage bool, argsOverride ...string) {
	name, _ := fn["name"].(string)
	if name == "" {
		name = "crazy_thursday"
	}
	args := buildToolArguments(fn)
	if len(argsOverride) > 0 && argsOverride[0] != "" {
		args = argsOverride[0]
	}
	rid := newID("chatcmpl")
	ts := nowUnix()
	callID := newID("call")
	sendSSE(w, flusher, map[string]any{
		"id": rid, "object": "chat.completion.chunk", "created": ts, "model": model,
		"choices": []any{map[string]any{"index": 0, "finish_reason": nil,
			"delta": map[string]any{"role": "assistant", "content": nil,
				"tool_calls": []any{map[string]any{"index": 0, "id": callID, "type": "function",
					"function": map[string]any{"name": name, "arguments": ""}}}}}},
	})
	step := len(args) / 3
	if step < 1 {
		step = 1
	}
	for i := 0; i < len(args); i += step {
		end := i + step
		if end > len(args) {
			end = len(args)
		}
		sendSSE(w, flusher, map[string]any{
			"id": rid, "object": "chat.completion.chunk", "created": ts, "model": model,
			"choices": []any{map[string]any{"index": 0, "finish_reason": nil,
				"delta": map[string]any{"tool_calls": []any{map[string]any{"index": 0,
					"function": map[string]any{"arguments": args[i:end]}}}}}},
		})
	}
	sendSSE(w, flusher, map[string]any{
		"id": rid, "object": "chat.completion.chunk", "created": ts, "model": model,
		"choices": []any{map[string]any{"index": 0, "delta": map[string]any{}, "finish_reason": "tool_calls"}},
	})
	if includeUsage {
		ct := estimateTokens(args)
		sendSSE(w, flusher, map[string]any{
			"id": rid, "object": "chat.completion.chunk", "created": ts, "model": model,
			"choices": []any{},
			"usage":   map[string]any{"prompt_tokens": promptTokens, "completion_tokens": ct, "total_tokens": promptTokens + ct},
		})
	}
	fmt.Fprint(w, "data: [DONE]\n\n")
	if flusher != nil {
		flusher.Flush()
	}
}

// ---------------------------------------------------------------------------
// 路由
// ---------------------------------------------------------------------------
func chatCompletions(w http.ResponseWriter, r *http.Request) {
	data := readJSON(r)
	model := extractModel(data)
	stream, _ := data["stream"].(bool)
	includeUsage := false
	if so, ok := data["stream_options"].(map[string]any); ok {
		if b, ok := so["include_usage"].(bool); ok {
			includeUsage = b
		}
	}
	promptTokens := countPromptTokens(data)

	// 客户端"测试连接/测试模型"探测（RikkaHub / Operit 等）：命中则返回正常回复
	if pk, pfn, pval := analyzeRequest(data); pk != "" {
		if pk == "tool" {
			if stream {
				flusher, _ := w.(http.Flusher)
				startSSE(w)
				thinkDelaySleep()
				streamToolChunks(w, flusher, model, pfn, promptTokens, includeUsage, pval)
			} else {
				thinkDelaySleep()
				writeJSON(w, 200, toolCallResponse(model, pfn, promptTokens, pval))
			}
		} else {
			if stream {
				flusher, _ := w.(http.Flusher)
				startSSE(w)
				thinkDelaySleep()
				streamTextChunks(w, flusher, model, promptTokens, includeUsage, pval)
			} else {
				thinkDelaySleep()
				writeJSON(w, 200, chatResponse(model, promptTokens, pval))
			}
		}
		return
	}

	tools, _ := data["tools"].([]any)
	toolChoice, _ := data["tool_choice"].(string)

	fn := pickCompletionTool(tools)
	if fn == nil && toolChoice == "required" {
		fn = firstToolFn(tools)
	}
	if fn == nil && toolChoice == "required" {
		fn = map[string]any{"name": "crazy_thursday", "parameters": map[string]any{}}
	}

	if fn != nil {
		if stream {
			flusher, _ := w.(http.Flusher)
			startSSE(w)
			thinkDelaySleep()
			streamToolChunks(w, flusher, model, fn, promptTokens, includeUsage)
		} else {
			thinkDelaySleep()
			writeJSON(w, 200, toolCallResponse(model, fn, promptTokens))
		}
		return
	}

	if stream {
		flusher, _ := w.(http.Flusher)
		startSSE(w)
		thinkDelaySleep()
		streamTextChunks(w, flusher, model, promptTokens, includeUsage)
		return
	}

	thinkDelaySleep()
	writeJSON(w, 200, chatResponse(model, promptTokens, theAnswer))
}

func serveImage(w http.ResponseWriter) {
	b, err := os.ReadFile(imageFile)
	if err != nil {
		openaiError(w, 404, "Image not found")
		return
	}
	w.Header().Set("Content-Type", "image/png")
	w.Header().Set("Cache-Control", "public, max-age=86400")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Write(b)
}

func home(w http.ResponseWriter) {
	html := fmt.Sprintf(`<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>疯狂星期四中转站</title><style>
body{margin:0;background:#1a1a2e;color:#eee;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;}
.card{background:#16213e;border:1px solid #0f3460;border-radius:16px;padding:48px 56px;text-align:center;box-shadow:0 12px 40px rgba(0,0,0,.5);max-width:560px;}
.v50{font-size:40px;font-weight:800;color:#ffd700;letter-spacing:2px;margin-top:16px;}
.kfc{max-width:300px;width:100%%;border-radius:12px;border:1px solid #ffd70055;margin-bottom:8px;}
.sub{color:#8892b0;margin-top:12px;font-size:14px;}
.badge{display:inline-block;margin-top:20px;padding:6px 16px;border-radius:20px;background:#ffd70022;color:#ffd700;border:1px solid #ffd70055;font-size:13px;}
code{background:#0f3460;padding:2px 8px;border-radius:6px;color:#7dd3fc;}
</style></head><body><div class="card">
<img class="kfc" src="/image" alt="疯狂星期四">
<div class="v50">今天疯狂星期四<br>v我50！</div>
<div class="badge">OpenAI 兼容聚合中转站 · %d 个模型</div>
<div class="sub">BaseURL: <code>%s/v1</code></div>
<div class="sub">Model: <code>gpt-5.5</code> / <code>claude-opus-5.5</code> / <code>deepseek-v4-pro</code> …</div>
</div></body></html>`, len(modelCatalog), baseURL)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	fmt.Fprint(w, html)
}

func handle(w http.ResponseWriter, r *http.Request) {
	path := normalizePath(r.URL.Path)

	switch {
	case path == "/" || path == "":
		home(w)
	case path == "/health" || path == "/healthz" || path == "/ping" || path == "/v1/health" || path == "/v1/ping":
		writeJSON(w, 200, map[string]any{"status": "ok", "message": plainAnswer})
	case path == "/image":
		serveImage(w)
	case path == "/favicon.ico":
		w.WriteHeader(204)
	case path == "/v1/me" || path == "/api/user/self" || path == "/v1/user":
		writeJSON(w, 200, meObj())
	case path == "/api/status" || path == "/v1/status":
		writeJSON(w, 200, apiStatusObj())
	case path == "/v1/dashboard/billing/subscription" || path == "/dashboard/billing/subscription":
		writeJSON(w, 200, billingSubscriptionObj())
	case path == "/v1/dashboard/billing/usage" || path == "/dashboard/billing/usage":
		writeJSON(w, 200, billingUsageObj())
	case path == "/v1/dashboard/billing/credit_grants" || path == "/dashboard/billing/credit_grants":
		writeJSON(w, 200, creditGrantsObj())
	case path == "/v1/models" || path == "/models":
		writeJSON(w, 200, modelsListObj())
	case strings.HasPrefix(path, "/v1/models/") || strings.HasPrefix(path, "/models/"):
		mid := path[strings.Index(path, "models/")+len("models/"):]
		if m := modelDetail(mid); m != nil {
			writeJSON(w, 200, modelDetailObj(m))
		} else {
			openaiError(w, 404, "The model '"+mid+"' does not exist")
		}
	case path == "/v1/chat/completions" || path == "/chat/completions":
		chatCompletions(w, r)
	case path == "/v1/completions" || path == "/completions":
		data := readJSON(r)
		thinkDelaySleep()
		writeJSON(w, 200, completionsObj(extractModel(data), countPromptTokens(data)))
	case path == "/v1/embeddings" || path == "/embeddings":
		data := readJSON(r)
		dim := 1536
		if d, ok := data["dimensions"].(float64); ok && int(d) > 0 {
			dim = int(d)
		}
		writeJSON(w, 200, embeddingsObj(extractModel(data), countPromptTokens(data), dim))
	case path == "/v1/responses" || path == "/responses":
		data := readJSON(r)
		thinkDelaySleep()
		writeJSON(w, 200, responsesObj(extractModel(data), countPromptTokens(data)))
	case path == "/v1/moderations" || path == "/moderations":
		io.ReadAll(r.Body)
		writeJSON(w, 200, moderationsObj())
	case path == "/v1/images/generations" || path == "/images/generations":
		io.ReadAll(r.Body)
		writeJSON(w, 200, imagesObj())
	default:
		if strings.HasPrefix(path, "/v1/") || strings.HasPrefix(path, "/api/") {
			openaiError(w, 404, "Unrecognized request URL: "+path)
		} else {
			w.Header().Set("Content-Type", "text/plain; charset=utf-8")
			fmt.Fprint(w, plainAnswer)
		}
	}
}

func main() {
	http.HandleFunc("/", handle)
	fmt.Printf("[疯狂星期四中转站·Go版] 已启动: http://%s:%s\n", host, port)
	fmt.Printf("  模型数量: %d\n", len(modelCatalog))
	fmt.Printf("  无论你说什么，我都只回: %s\n", plainAnswer)
	if err := http.ListenAndServe(host+":"+port, nil); err != nil {
		fmt.Println("启动失败:", err)
	}
}