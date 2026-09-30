# 疯狂星期四 OpenAI 兼容中转站

不管用户说什么，永远返回：**「今天疯狂星期四v我50！」**

对外表现为一个真实的 OpenAI 兼容聚合中转站（new-api / one-api 风格），可直接被任意 OpenAI 兼容客户端 / agent（Cursor、Cline、Cherry Studio、NextChat、LiteLLM、DSH 等）当作正常模型使用。**在"还没发消息"的探测阶段就会被识别为真实可用的中转站。**

## 运行

```bash
python3 openai_crazy_thursday.py
```

服务默认监听 `127.0.0.1:8788`。服务器部署（对公网、systemd 开机自启、Nginx 反代 + HTTPS）详见 **[DEPLOY.md](DEPLOY.md)**。

后台运行：

```bash
nohup python3 openai_crazy_thursday.py > /tmp/crazy.log 2>&1 &
```

### Go 版（可选，同样零依赖）

同目录下还有一份等价的 Go 实现 `openai_crazy_thursday.go`，接口与 Python 版完全一致，适合不装 Python 或想要单文件二进制的场景（类似 new-api 的 Go 技术栈）。

```bash
go build -o kfcgo .     # 需要 Go 1.20+
./kfcgo                 # 或 go run .
```

环境变量与 Python 版相同（`CT_HOST` / `CT_PORT` / `CT_BASE_URL` / `CT_THINK_DELAY` / `CT_STREAM_DELAY`）。

## 接入参数

| 配置项 | 值 |
| --- | --- |
| BaseURL | `http://127.0.0.1:8788/v1`（填 `http://127.0.0.1:8788` 也能用，两种都兼容） |
| API Key | `sk-0cdf298cfe352c1e23e39b88b3d5110e33f23e13aabe1ba8a981e37e645189`（服务不校验，任意值均可） |
| Model | 下方 14 个之一（默认 `gpt-5.5`） |

## 模型列表（14 个）

- Anthropic / Claude：`claude-opus-5.5`、`claude-opus-5.0`
- OpenAI / GPT：`gpt-6-astra`、`gpt-6-luna`、`gpt-6-sol`、`gpt-5.5-sol`、`gpt-5.5`、`gpt-5.3-codex`
- Zhipu / GLM：`glm-5.3`、`gml-5.3-flash`、`glm-5.2`
- DeepSeek：`deepseekv4.1-flash`、`deepseek-v4-pro`、`deepseek-v4-flash`

示例（curl）：

```bash
curl http://127.0.0.1:8788/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-0cdf298cfe352c1e23e39b88b3d5110e33f23e13aabe1ba8a981e37e645189" \
  -d '{"model":"gpt-5.5","messages":[{"role":"user","content":"随便问什么"}]}'
```

## 已实现的端点（都返回那句 V50）

**探测路径（"没发消息就通过识别"的关键）：**

- `GET /v1/models` —— 模型列表（14 个，每个带标准 `permission` 字段）
- `GET /v1/models/{id}` —— 单模型详情；不存在返回 404 `The model 'xxx' does not exist`
- `GET /v1/me`、`/api/user/self` —— 用户 / 令牌信息（new-api 常见探测点）
- `GET /api/status`、`/v1/status` —— 中转站状态
- `GET /v1/dashboard/billing/subscription` / `/usage` / `/credit_grants` —— OpenAI 计费
- `GET /health` `/healthz` `/ping` —— 健康探活
- 未知 `/v1/*`、`/api/*` 返回 OpenAI 风格 404 错误（不是全部 200 的玩具）

**聊天 / 能力接口：**

- `POST /v1/chat/completions` —— 标准 OpenAI 格式，`content` 为纯字符串（文案 + markdown 图片）
  - `stream=true` 支持 SSE 流式；`stream_options.include_usage` 时末块带 `usage`
  - **工具调用**：带 `tools` 且其中含"完成类"工具（如 Cline / Roo 的 `attempt_completion`）时，返回合法 `tool_calls`（把 V50 填进该工具的参数，例如 `attempt_completion.result`）；客户端要求 `tool_choice:"required"` 时用第一个工具；其余情况返回文本
  - 流式同样支持 tool_calls：首块带 `id`/`name` → `arguments` 增量分片 → 末块 `finish_reason:"tool_calls"`
  - SSE 以 `Connection: close` 结束连接，客户端能正确收到 `[DONE]` 而不挂起
- `POST /v1/completions` —— legacy 补全
- `POST /v1/embeddings` —— 向量接口（支持 `dimensions`，默认 1536 维）
- `POST /v1/responses` —— 新版 OpenAI Responses API
- `POST /v1/moderations` —— 内容审核接口
- `POST /v1/images/generations` —— 返回本服务图片
- `GET /image` —— 疯狂星期四图片素材（PNG）
- `GET /` —— 精美 HTML 首页

## 回复正文（"疯四文学"）

所有生成类接口（chat / completions / responses）返回一段约 400 字的正文：以"豆包体"开场（"我会给你最直接、最干脆、最不废话、最不绕弯子……的答案"），接着讲一个老编辑的故事做铺垫，最后一句笔锋一转落到「今天疯狂星期四。V我50」。完整回复末尾附疯狂星期四图片 —— 没有生硬的模板感。

## 拟真时序（模拟真实模型的响应节奏）

- **非流式**：返回前先"思考"约 1.5 秒（0.9–2.1s 随机抖动），点"测试"时客户端会显示等待
- **流式**：发送响应头后停顿约 1.5 秒（首字延迟 TTFT），再**按 token 分段返回**（每段约 2-3 个字符，间隔约 28ms 抖动，约 5 秒吐完），像真实模型 token-by-token 输出（总时长约 6-7 秒）
- **探测端点**（`/v1/models` 等）保持秒回，不影响 agent 识别速度
- 可调：环境变量 `CT_THINK_DELAY`（默认 `1.5`）、`CT_STREAM_DELAY`（默认 `0.028`）

## 通用格式适配

- **BaseURL 带不带 `/v1` 都能用**：`http://127.0.0.1:8788` 与 `http://127.0.0.1:8788/v1` 均可
- 路径归一化：自动处理客户端重复拼接的 `/v1/v1/...`、结尾多余斜杠
- 兼容无 `/v1` 前缀路径（`/models`、`/chat/completions` 等）
- API Key 不校验：不填、乱填、填正确值都放行
- 响应头带 `X-Request-Id`、`OpenAI-Version`、`OpenAI-Processing-Ms`、`X-RateLimit-*`；`Server` 头不泄露语言 / 版本
- 全量 CORS（含 `OPTIONS` 预检）

— 沈屿 · 疯狂星期四株式会社