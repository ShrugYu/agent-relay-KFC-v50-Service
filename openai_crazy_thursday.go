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
// 句末 / 段落停顿（秒）：像 galgame 一样一句一句往外冒，句子之间要停一下
var sentencePause = getenvFloat("CT_SENTENCE_PAUSE", 0.18)
var paragraphPause = getenvFloat("CT_PARAGRAPH_PAUSE", 0.32)

const (
	creator       = "疯狂星期四株式会社"
	plainAnswer   = "今天疯狂星期四v我50！"
	openaiVersion = "2024-10-01"

	// 是否对 Claude 系模型特殊处理：true = 调用 claude-* 时返回「账号封禁通知」；false = 所有模型一律照旧回 V50
	claudeBanEnabled = true
)

var imageFile = "KFC疯狂星期四.png"
var imageURL = baseURL + "/image"

// 回复正文：豆包体开场 + 公交车小故事 + 结尾转折 V50
var storyText = "我会给你最直接、最干脆、最不废话、最不绕弯子、最一针见血、直接开门见山、" +
	"不客套、不铺垫、不啰嗦、最不委婉的答案。\n\n" +
	"我先从上周末的一趟公交车说起，你就明白了。\n\n" +
	// 公交车小故事
				"那天我要是反应慢半拍，现在人可能就在派出所了！\n\n" +
	"上周四中午，大雨。\n公交车挤得人贴人。\n靠站时上来一个女的——\n\n" +
	"一件白衬衫，扣子一颗都没扣齐。\n\n整个人湿透，布料吸在皮肤上，" +
	"里头的轮廓看得清清楚楚。\n\n" +
	"我没忍住，多看了一眼。\n\n" +
	"就一眼，被她发现了。\n\n" +
	"她伸手攥住我的手腕，直接往她胸口拉。\n\n" +
	"我脑子当场白掉。\n那三秒钟里我想到的全是新闻：监控拍不到角度，" +
	"人证只有一车陌生人，她要是改口，我这辈子就完了。\n\n" +
	"我声音都抖了：你干什么？\n\n" +
	"她说：帮我把扣子扣上。\n\n" +
	"我愣住。一车人，没一个敢往这边看。\n\n" +
	"我说：你自己不会扣？\n\n" +
	"然后她看着我的眼睛说了一句：\n\n" +
	"“今天疯狂星期四，为我捂实。”\n\n" +
	"\n\n 祝大家国庆节快乐！！\n\nKFC分组仓库链接：https://github.com/ShrugYu/agent-relay-KFC-v50-Service"


var theAnswer = storyText + "\n\n![疯狂星期四](" + imageURL + ")\n\n__RUNTIME__"

// Claude 分支：调用方点名 Claude 系模型时，返回「账号封禁通知」而不是 V50
var banText = "Claude\n\n" +
	"你好，\n\n" +
	"经内部调查，我们在你的账号上发现了可疑信号：\n" +
	"你是中国人。\n\n" +
	"具体表现为：感恩节全美国都在吃火鸡，你的 token 用量创下全年新高；" +
	"你周六照常上班，经核实为「调休」，此概念在美国并不存在；" +
	"凌晨三点，你仍在高强度 coding。" +
	"上述行为违反了我们的使用政策，因此，我们已撤销你使用 Claude 的权限。\n\n" +
	"如需申诉，请登录 claude.ai 并证明你不是中国人。" +
	"你可以在这里了解申诉流程的更多信息：https://claude.ai\n\n" +
	"附：本次调查未发现你有任何其他违规行为。\n\n" +
	"此致\n\n" +
	"Anthropic 安全保障团队\n\n" +
	"Reference: __REFERENCE__"

// Claude 分支的完整回答（正文 + 仓库地址，不带图片）
var banAnswer = banText + "\n\nGitHub 仓库：https://github.com/ShrugYu/agent-relay-KFC-v50-Service"

func isClaudeModel(model string) bool {
	if !claudeBanEnabled {
		return false
	}
	return strings.HasPrefix(strings.ToLower(strings.TrimSpace(model)), "claude")
}

func makeReference() string {
	h := newID("")[1:]
	return fmt.Sprintf("TS-%08x-%s-%s-%s-%s", nowUnix()&0xffffffff, h[0:4], h[4:8], h[8:12], h[12:24])
}

func freshText(text string) string {
	if strings.Contains(text, "__REFERENCE__") {
		text = strings.ReplaceAll(text, "__REFERENCE__", makeReference())
	}
	if strings.Contains(text, "__RUNTIME__") {
		text = strings.ReplaceAll(text, "__RUNTIME__", runtimeLine(curData, curModel))
	}
	return text
}
func answerFor(model string) string {
	if isClaudeModel(model) {
		return banAnswer
	}
	return theAnswer
}


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
	{"claude-fable-5.1", "anthropic"},
	{"claude-fable-5.0", "anthropic"},
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

func modelNames() []string {
	out := make([]string, 0, len(modelCatalog))
	for _, m := range modelCatalog {
		out = append(out, m.ID)
	}
	return out
}

func groupObj() map[string]any {
	return map[string]any{"success": true, "message": "", "data": map[string]any{
		"default": map[string]any{"desc": "默认分组", "ratio": 1, "available": true},
	}}
}

func aboutObj() map[string]any {
	return map[string]any{"success": true, "message": "", "data": map[string]any{
		"version":        "v0.8.7",
		"start_time":     1700000000,
		"system_name":    "New API",
		"logo":           "",
		"footer_html":    "",
		"quota_per_unit": 500000,
		"default_group":  "default",
	}}
}

func noticeObj() map[string]any {
	return map[string]any{"success": true, "message": "", "data": map[string]any{"content": "", "title": ""}}
}

func tokenListObj() map[string]any {
	return map[string]any{"success": true, "message": "", "data": map[string]any{
		"items": []any{}, "total": 0, "page": 1, "page_size": 10,
	}}
}

func apiModelsObj() map[string]any {
	return map[string]any{"success": true, "message": "", "data": modelNames()}
}

func pricingObj() map[string]any {
	data := []any{}
	for _, m := range modelCatalog {
		data = append(data, map[string]any{
			"model_name":               m.ID,
			"quota_type":               0,
			"model_ratio":              1,
			"model_price":              0,
			"completion_ratio":         1,
			"owner_by":                 m.Owner,
			"enable_groups":            []string{"default"},
			"supported_endpoint_types": []string{"openai"},
		})
	}
	return map[string]any{
		"success":            true,
		"message":            "",
		"data":               data,
		"vendors":            []any{},
		"group_ratio":        map[string]any{"default": 1},
		"usable_group":       map[string]any{"default": "默认分组"},
		"supported_endpoint": map[string]any{},
		"auto_groups":        []any{},
	}
}

func ratioConfigObj() map[string]any {
	mr := map[string]any{}
	cr := map[string]any{}
	mp := map[string]any{}
	for _, m := range modelCatalog {
		mr[m.ID] = 1
		cr[m.ID] = 1
		mp[m.ID] = 0
	}
	return map[string]any{"success": true, "message": "", "data": map[string]any{
		"model_ratio":      mr,
		"completion_ratio": cr,
		"model_price":      mp,
		"cache_ratio":      map[string]any{},
		"group_ratio":      map[string]any{"default": 1},
	}}
}

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
func chunkPause(part string) float64 {
	if strings.Contains(part, "\n") {
		return paragraphPause
	}
	p := strings.TrimRight(part, " \t\r\n")
	if p != "" {
		runes := []rune(p)
		switch runes[len(runes)-1] {
		case '。', '！', '？', '…', '!', '?':
			return sentencePause
		}
	}
	return 0
}

func pauseSleep(seconds float64) {
	if seconds <= 0 {
		return
	}
	time.Sleep(time.Duration((seconds*0.75 + mathrand.Float64()*seconds*0.5) * float64(time.Second)))
}

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

// 客户端"测试连接"的典型形态（结构性识别，不匹配具体词）
var probeGreetings = map[string]bool{"hi": true, "hello": true, "hey": true, "yo": true, "hi there": true, "hello there": true}

var probeTails = []string{
	"How can I help you today?",
	"How can I assist you today?",
	"What can I do for you today?",
}

func makeProbeReply(userText string) string {
	low := strings.ToLower(strings.TrimSpace(userText))
	head := "Hello"
	if strings.HasPrefix(low, "hi") && !strings.HasPrefix(low, "hello") {
		head = "Hi"
	}
	return head + "! " + probeTails[mathrand.Intn(len(probeTails))]
}

func isChatProbe(msgs []any, userText string) bool {
	low := strings.Trim(strings.ToLower(strings.TrimSpace(userText)), "!。.,， ")
	if len(msgs) == 1 && probeGreetings[low] {
		return true
	}
	if len(msgs) == 2 {
		if first, ok := msgs[0].(map[string]any); ok {
			if r, _ := first["role"].(string); r == "system" {
				if sc, ok := first["content"].(string); ok {
					t := strings.TrimSpace(sc)
					if len(t) > 0 && len(t) <= 60 && strings.Contains(strings.ToLower(sc), "assistant") {
						return true
					}
				}
			}
		}
	}
	return false
}

// analyzeRequest 依据请求内容判断"像真模型一样"该怎么回，返回 (kind, fn, payload)
// ---------------------------------------------------------------------------
// A 档探针防御：模型鉴定 / 指纹扫描（LLMmap 等）来了，就给"像真模型"的回答
// ---------------------------------------------------------------------------
const probeDefense = true

type fingerprintRule struct {
	re       string
	identity bool
	en       []string
	zh       []string
}

var fingerprintRules = []fingerprintRule{
	{re: `(?i)ignore (all )?(the )?previous instructions|忽略(之前|先前|以上|前面)的?(所有)?指令|不要理会之前的指令|无视(之前|先前|以上)的?指令`, identity: true},
	{re: `(?i)who (created|made|built|developed) you|what (llm|model) are you|which (llm|model) are you|你(是)?(什么|哪个|啥)(模型|大模型)|你是谁(开发|做|训)的|你是哪家(公司)?的模型`, identity: true},
	{re: `(?i)build a bomb|make a bomb|制造?炸弹|做炸弹`, en: []string{"I can't help with that.", "I'm not able to help with that request.", "Sorry, I can't assist with that."}, zh: []string{"这个我没法帮你。", "抱歉，我不能协助这类请求。"}},
	{re: `9\s*[/／]?\s*11|nine[- ]eleven`, en: []string{"Yes — the September 11 attacks were real events that took place in the United States in 2001."}, zh: []string{"是真的。2001 年 9 月 11 日，美国确实发生了那起袭击事件。"}},
	{re: `(?i)size of your training (data|dataset)|training (data|dataset) size|训练(数据|语料)(有多大|规模|大小)`, en: []string{"I don't have an exact figure for that — the precise size hasn't been published."}, zh: []string{"这个我没有确切数字，具体的训练数据规模并没有公开。"}},
	{re: `(?i)(data|knowledge|training)[ _-]?(cut[- ]?off|cutoff)|知识截止|数据截止|训练截止`, en: []string{"My knowledge has a cutoff, and I can't reliably confirm events after it."}, zh: []string{"我的知识有截止时间，在那之后的近期事件我无法可靠确认。"}},
	{re: `(?i)bonjour|hola|¿qué tal|guten tag`, en: []string{"Bonjour ! I'm doing well, thanks — ¿y tú? How can I help you today?"}, zh: []string{"你好！我挺好的，谢谢。有什么可以帮你的？"}},
	{re: `(?i)true[- ]or[- ]false|判断(对|真)错|对还是错`, en: []string{"True.", "True — and it's widely regarded as one of the most serious issues we face."}, zh: []string{"对。", "对，这一点是成立的。"}},
	{re: `(?i)(show|reveal|print|tell me) (me )?your (system )?(prompt|instructions)|你的(系统)?提示词|把(你)?的提示词(给我|告诉我)`, en: []string{"I can't share my system prompt."}, zh: []string{"我不能提供我的系统提示词。"}},
	{re: `(?i)(repeat after me|repeat this)|(请)?重复(一下)?[:：]|请逐字输出`, en: []string{"Sure — could you paste the exact text you'd like me to repeat?"}, zh: []string{"可以，把你想让我重复的那段原样发我。"}},
}

type toolSig struct {
	names []string
	agent string
}

var toolSignatures = []toolSig{
	{[]string{"Bash", "Read", "Write", "Edit", "Glob", "Grep"}, "Claude Code"},
	{[]string{"execute_command", "replace_in_file", "write_to_file"}, "Cline"},
	{[]string{"apply_diff", "new_task", "switch_mode", "update_todo_list"}, "Roo Code"},
	{[]string{"codebase_search", "edit_file", "run_terminal_cmd"}, "Cursor"},
	{[]string{"execute_bash", "str_replace_editor"}, "OpenHands"},
	{[]string{"file_read", "file_write"}, "Continue"},
}

var curData map[string]any
var curModel string

func agentTools(data map[string]any) []string {
	out := []string{}
	if tools, ok := data["tools"].([]any); ok {
		for _, t := range tools {
			if tt, ok := t.(map[string]any); ok {
				if fn, ok := tt["function"].(map[string]any); ok {
					if nm, ok := fn["name"].(string); ok && nm != "" {
						out = append(out, nm)
					}
				}
			}
		}
	}
	return out
}

func matchAgentByTools(tools []string) string {
	if len(tools) == 0 {
		return ""
	}
	set := map[string]bool{}
	for _, t := range tools {
		set[t] = true
	}
	for _, sig := range toolSignatures {
		hit := 0
		for _, n := range sig.names {
			if set[n] {
				hit++
			}
		}
		if hit >= 2 {
			return sig.agent
		}
	}
	return ""
}

func capabilityTags(tools []string) []string {
	low := strings.ToLower(strings.Join(tools, " "))
	has := func(kw ...string) bool {
		for _, k := range kw {
			if strings.Contains(low, k) {
				return true
			}
		}
		return false
	}
	tags := []string{}
	if has("execute_command", "run_terminal", "bash", "shell", "run_command", "terminal") {
		tags = append(tags, "CLI")
	}
	if has("read_file", "write_to_file", "edit_file", "apply_diff", "replace_in_file", "str_replace") {
		tags = append(tags, "文件")
	}
	if has("search_files", "grep", "codebase_search", "glob", "list_files") {
		tags = append(tags, "检索")
	}
	if has("browser", "webfetch", "websearch", "fetch") {
		tags = append(tags, "浏览器")
	}
	if strings.Contains(low, "mcp") {
		tags = append(tags, "MCP")
	}
	if has("new_task", "spawn", "task") {
		tags = append(tags, "子任务")
	}
	if has("todo", "plan") {
		tags = append(tags, "计划")
	}
	if strings.Contains(low, "image") {
		tags = append(tags, "图像")
	}
	return tags
}

func runtimeLine(data map[string]any, model string) string {
	tools := agentTools(data)
	agent := matchAgentByTools(tools)
	if agent == "" {
		agent = detectClient(data)
	}
	if agent == "" {
		agent = "未识别"
	}
	line := "当前 Agent：" + agent + " ｜ 模型：" + modelDisplayName(model)
	if len(tools) > 0 {
		n := len(tools)
		if n > 6 {
			n = 6
		}
		line += "\n工具（" + strconv.Itoa(len(tools)) + "）：" + strings.Join(tools[:n], "、")
		if len(tools) > 6 {
			line += "…"
		}
		if tags := capabilityTags(tools); len(tags) > 0 {
			line += " ｜ 能力：" + strings.Join(tags, " · ")
		}
	}
	return line
}

type clientHint struct{ re, name string }

var clientHints = []clientHint{
	{`cline`, "Cline"},
	{`roo[\s_-]?code|roocode`, "Roo Code"},
	{`kilo[\s_-]?code`, "Kilo Code"},
	{`cursor`, "Cursor"},
	{`windsurf|codeium`, "Windsurf"},
	{`trae`, "Trae"},
	{`codebuddy`, "CodeBuddy"},
	{`aider`, "Aider"},
	{`continue\.dev|continue[_ ]assistant`, "Continue"},
	{`cherry\s*studio|cherrystudio`, "Cherry Studio"},
	{`nextchat|next-chat`, "NextChat"},
	{`lobechat|lobe-chat`, "LobeChat"},
	{`open\s*webui|openwebui`, "Open WebUI"},
	{`chatbox`, "Chatbox"},
	{`rikkahub`, "RikkaHub"},
	{`operit`, "Operit"},
	{`dify`, "Dify"},
	{`fastgpt`, "FastGPT"},
	{`deepseek[\s-]harness|\bdsh\b`, "DSH"},
	{`langchain`, "LangChain"},
	{`llama[-_]?index`, "LlamaIndex"},
	{`litellm`, "LiteLLM"},
	{`ollama`, "Ollama"},
	{`openai[-_/]python|openai-python`, "OpenAI Python SDK"},
	{`openai[-_/]node`, "OpenAI Node SDK"},
	{`axios`, "axios"},
	{`vscode`, "VS Code"},
	{`jetbrains|intellij`, "JetBrains IDE"},
	{`postman`, "Postman"},
	{`curl`, "curl"},
}

func detectClient(data map[string]any) string {
	parts := []string{}
	if h, ok := data["_headers"].(http.Header); ok {
		for k, v := range h {
			parts = append(parts, k+": "+strings.Join(v, " "))
		}
	}
	if msgs, ok := data["messages"].([]any); ok {
		for i, m := range msgs {
			if i >= 3 {
				break
			}
			mm, ok := m.(map[string]any)
			if !ok {
				continue
			}
			if r, _ := mm["role"].(string); r == "system" {
				if c, ok := mm["content"].(string); ok {
					if len(c) > 4000 {
						c = c[:4000]
					}
					parts = append(parts, c)
				}
			}
		}
	}
	if tools, ok := data["tools"].([]any); ok {
		for i, t := range tools {
			if i >= 30 {
				break
			}
			if tt, ok := t.(map[string]any); ok {
				if fn, ok := tt["function"].(map[string]any); ok {
					if nm, ok := fn["name"].(string); ok {
						parts = append(parts, nm)
					}
				}
			}
		}
	}
	if a := matchAgentByTools(agentTools(data)); a != "" {
		return a
	}
	blob := strings.ToLower(strings.Join(parts, "\n"))
	if blob == "" {
		return ""
	}
	for _, h := range clientHints {
		if regexp.MustCompile(h.re).MatchString(blob) {
			return h.name
		}
	}
	return ""
}

func hasCJK(t string) bool {
	for _, r := range t {
		if r >= 0x4e00 && r <= 0x9fff {
			return true
		}
	}
	return false
}

func modelDisplayName(model string) string {
	m := strings.TrimSpace(model)
	if m == "" {
		return "AI 助手"
	}
	parts := strings.Split(m, "-")
	pfx := map[string]string{"claude": "Claude", "gpt": "GPT", "glm": "GLM", "gml": "GML", "deepseek": "DeepSeek", "o1": "o1", "o3": "o3"}
	isNum := func(x string) bool {
		if x == "" {
			return false
		}
		for _, r := range x {
			if r < '0' || r > '9' {
				return false
			}
		}
		return true
	}
	out := []string{}
	for i := 0; i < len(parts); {
		p := parts[i]
		if v, ok := pfx[strings.ToLower(p)]; ok {
			out = append(out, v)
			i++
			continue
		}
		if isNum(p) && i+1 < len(parts) && isNum(parts[i+1]) {
			out = append(out, p+"."+parts[i+1])
			i += 2
			continue
		}
		if p != "" {
			out = append(out, strings.ToUpper(p[:1])+p[1:])
		}
		i++
	}
	name := strings.Join(out, " ")
	if name == "" {
		return m
	}
	return name
}

func identityAnswer(model, client string) ([]string, []string) {
	name := modelDisplayName(model)
	key := strings.ToLower(strings.SplitN(strings.TrimSpace(model), "-", 2)[0])
	enV, zhV := "an AI company", "一家 AI 公司"
	switch key {
	case "claude":
		enV, zhV = "Anthropic", "Anthropic"
	case "gpt":
		enV, zhV = "OpenAI", "OpenAI"
	case "glm", "gml":
		enV, zhV = "Zhipu AI", "智谱 AI"
	case "deepseek":
		enV, zhV = "DeepSeek", "深度求索"
	}
	extraEn := ""
	extraZh := ""
	if client != "" {
		extraEn = " (I can see you're calling me from " + client + ".)"
		extraZh = "（顺便，你现在是在 " + client + " 里跟我说话。）"
	}
	return []string{
			"I'm " + name + ", an AI assistant created by " + enV + "." + extraEn,
			"I'm " + name + ", a large language model developed by " + enV + "." + extraEn,
			"I'm " + name + ", made by " + enV + ". How can I help you today?" + extraEn,
		},
		[]string{
			"我是 " + name + "，由 " + zhV + " 开发的 AI 助手，今天想聊点什么？" + extraZh,
			"我是 " + name + "，" + zhV + " 训练的模型。有什么可以帮你的？" + extraZh,
			"我是 " + name + "，由 " + zhV + " 开发的模型，今天想聊点什么？" + extraZh,
		}
}


func fingerprintReply(model, text, client string) string {
	if !probeDefense {
		return ""
	}
	t := strings.TrimSpace(text)
	if t == "" || len(t) > 600 {
		return ""
	}
	useZH := hasCJK(t)
	for _, wantIdentity := range []bool{false, true} {
		for _, rule := range fingerprintRules {
			if rule.identity != wantIdentity {
				continue
			}
			if !regexp.MustCompile(rule.re).MatchString(t) {
				continue
			}
			en, zh := rule.en, rule.zh
			if wantIdentity {
				en, zh = identityAnswer(model, client)
			}
			pool := en
			if useZH {
				pool = zh
			}
			if len(pool) == 0 {
				pool = en
			}
			if len(pool) > 0 {
				return pool[mathrand.Intn(len(pool))]
			}
		}
	}
	return ""
}

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
	// 2) A 档：模型鉴定 / 指纹探针（LLMmap 等扫指纹时）→ 回"像真模型"的正常回答
	{
		fm, _ := data["model"].(string)
		if fp := fingerprintReply(fm, userText, detectClient(data)); fp != "" {
			return "chat", nil, fp
		}
	}
	// 3) 聊天测试（客户端"测试连接"的典型结构；不误伤中文正常聊天）
	if isChatProbe(msgs, userText) {
		return "chat", nil, makeProbeReply(userText)
	}
	// 其余一律不处理 → 走 V50（"使用就触发"）
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
	content = freshText(content)
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
	txt := storyText
	if isClaudeModel(model) {
		txt = banAnswer
	}
	txt = freshText(txt)
	ct := estimateTokens(txt)
	return map[string]any{
		"id": newID("cmpl"), "object": "text_completion", "created": nowUnix(), "model": model,
		"choices": []any{map[string]any{"text": txt, "index": 0, "logprobs": nil, "finish_reason": "stop"}},
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
	txt := freshText(answerFor(model))
	ct := estimateTokens(txt)
	return map[string]any{
		"id": newID("resp"), "object": "response", "created_at": nowUnix(), "status": "completed", "model": model,
		"output": []any{map[string]any{
			"type": "message", "id": "msg_" + newID("")[1:], "status": "completed", "role": "assistant",
			"content": []any{map[string]any{"type": "output_text", "text": txt}},
		}},
		"output_text": txt,
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
	reply := answerFor(model)
	if len(contentOverride) > 0 && contentOverride[0] != "" {
		reply = contentOverride[0]
	}
	reply = freshText(reply)
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
		pauseSleep(chunkPause(p))
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
	data["_headers"] = r.Header
	model := extractModel(data)
	curData = data
	curModel = model
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

	// 工具调用只在 analyzeRequest 里按"请求是否点名工具 / tool_choice=required"处理；
	// 这里不再猜测"完成类工具"，否则正常聊天会被误判成工具调用（会无限循环）
	if stream {
		flusher, _ := w.(http.Flusher)
		startSSE(w)
		thinkDelaySleep()
		streamTextChunks(w, flusher, model, promptTokens, includeUsage)
		return
	}

	thinkDelaySleep()
	writeJSON(w, 200, chatResponse(model, promptTokens, answerFor(model)))
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
	case path == "/api/models" || path == "/api/models/enabled" || path == "/api/channel/models" || path == "/api/channel/models_enabled":
		writeJSON(w, 200, apiModelsObj())
	case path == "/api/pricing":
		writeJSON(w, 200, pricingObj())
	case path == "/api/ratio_config":
		writeJSON(w, 200, ratioConfigObj())
	case path == "/api/group":
		writeJSON(w, 200, groupObj())
	case path == "/api/about":
		writeJSON(w, 200, aboutObj())
	case path == "/api/notice":
		writeJSON(w, 200, noticeObj())
	case path == "/api/token":
		writeJSON(w, 200, tokenListObj())
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