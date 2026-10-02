# 疯狂星期四 OpenAI 兼容中转站

一个**看起来完全真实**的 OpenAI 兼容聚合中转站（new-api / one-api 风格），可直接被任意 OpenAI 兼容
客户端 / agent（Cursor、Cline、Cherry Studio、NextChat、LiteLLM、DSH 等）当作正常模型使用，
在"还没发消息"的探测阶段就会被识别为可用中转站。

而它实际上的行为是：**不管用户说什么，都回一句「今天疯狂星期四，V我50」**。

## 特性

- **OpenAI 全兼容**：`/v1/chat/completions`（流式 / 非流式 / 工具调用）、`/v1/completions`、
  `/v1/embeddings`、`/v1/responses`、`/v1/moderations`、`/v1/images/generations`
- **new-api 站点面**：`/api/models`、`/api/pricing`、`/api/ratio_config`、`/api/group`、
  `/api/about`、`/api/notice`、`/api/token`、`/api/status`、`/api/user/self`
- **拟真时序**：首字延迟、逐字吐字、句末 / 段末停顿（galgame 式节奏），全部可调
- **双分支回复**：默认「疯四文学」；Claude 系可切换为「账号封禁通知」
- **指纹探针兼容层**：常见的模型自检 / 身份询问类请求会得到标准化的模型自述，而不是异常文本
- **零依赖**：Python 版仅用标准库；Go 版同样只用标准库，可编译成单文件二进制

## 快速开始

### Python（推荐）

```bash
python3 openai_crazy_thursday.py     # 前台运行，默认监听 127.0.0.1:8788
sh start.sh                          # 或：一键后台启动
sh stop.sh                           # 停止
```

### Go

```bash
go build -o kfcgo .                  # 需要 Go 1.20+
./kfcgo
```

## 接入参数

| 配置项 | 值 |
| --- | --- |
| BaseURL | `http://127.0.0.1:8788/v1`（填不带 `/v1` 的也行，两种都兼容） |
| API Key | 任意值（服务不校验） |
| Model | 下表 16 个之一，默认兜底 `gpt-5.5` |

```bash
curl http://127.0.0.1:8788/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-anything" \
  -d '{"model":"gpt-5.5","messages":[{"role":"user","content":"随便问什么"}]}'
```

## 模型列表（16 个）

- **Anthropic / Claude**：`claude-opus-5.5`、`claude-opus-5.0`、`claude-fable-5.1`、`claude-fable-5.0`
- **OpenAI / GPT**：`gpt-6-astra`、`gpt-6-luna`、`gpt-6-sol`、`gpt-5.5-sol`、`gpt-5.5`、`gpt-5.3-codex`
- **Zhipu / GLM**：`glm-5.3`、`gml-5.3-flash`、`glm-5.2`
- **DeepSeek**：`deepseekv4.1-flash`、`deepseek-v4-pro`、`deepseek-v4-flash`

`/v1/models`、`/api/models`、`/api/pricing` 三处返回同一份名单，不会互相矛盾。

## 端点

**探测 / 状态**

| 路径 | 说明 |
| --- | --- |
| `GET /v1/models`、`GET /models` | 模型列表（带标准 `permission` 字段） |
| `GET /v1/models/{id}` | 单模型详情；不存在返回 404 `The model 'xxx' does not exist` |
| `GET /v1/me`、`GET /api/user/self`、`GET /v1/user` | 用户 / 令牌信息 |
| `GET /api/status`、`GET /v1/status` | 站点状态 |
| `GET /v1/dashboard/billing/subscription` `/usage` `/credit_grants` | 计费信息 |
| `GET /health`、`/healthz`、`/ping` | 健康探活 |

**new-api / one-api 兼容**

`/api/models`、`/api/models/enabled`、`/api/channel/models`、`/api/channel/models_enabled`、
`/api/pricing`、`/api/ratio_config`、`/api/group`、`/api/about`、`/api/notice`、`/api/token`

**生成**

| 路径 | 说明 |
| --- | --- |
| `POST /v1/chat/completions` | 支持 SSE 流式、`stream_options.include_usage`、工具调用 |
| `POST /v1/completions` | legacy 补全 |
| `POST /v1/embeddings` | 向量接口（支持 `dimensions`，默认 1536 维） |
| `POST /v1/responses` | 新版 OpenAI Responses API |
| `POST /v1/moderations` | 内容审核 |
| `POST /v1/images/generations` | 图片生成接口 |

其它：`GET /image`（图片素材）、`GET /`（HTML 首页）。

未知的 `/v1/*`、`/api/*` 返回 OpenAI 风格 404，而不是"全部 200"的玩具站。

## 回复行为

**默认回复**：一段约 400 字的「疯四文学」——豆包体开场（"我会给你最直接、最干脆……的答案"），
中间讲一个小故事铺垫，最后笔锋一转落到「今天疯狂星期四，V我50」，末尾附图片、仓库链接，以及一行**运行时信息**（当前识别到的 Agent / 模型 / 可用工具与能力），例如：

`当前 Agent：Cline ｜ 模型：GPT 5.5 ｜ 工具（9）：execute_command、read_file… ｜ 能力：CLI · 文件 · 检索`

Agent 的识别来自三处：`tools` 里的工具名指纹（最准）、请求头（User-Agent 等）、system 提示里的自我介绍。

**Claude 分支**：请求的 `model` 以 `claude` 开头时，返回一封「Anthropic 账号封禁通知」
（邮件体正文 + 动态生成的 Reference 编号）。

**指纹探针兼容层**：对模型自检、身份询问、有害请求、知识截止日期等常见探针类请求，
返回标准化的模型自述 / 拒答话术（中英双语各若干条随机）；身份自述会带上请求里那个模型名与厂商。
若从请求头 / system 提示 / 工具名里识别出当前客户端（Cline、Cursor、Cherry Studio、Open WebUI 等），
还会顺口提一句"你现在是在 XX 里跟我说话"。

**能力清单应答**：当问题里出现 `cli` / `toolcall` / `mcp` / `skill` / “能调用什么” 等字眼时，
会把本次请求上下文里声明的能力原样列回去——工具名 + 描述、MCP 服务器、system 提示里的 skill，
以及归纳出的能力标签（CLI / 文件 / 检索 / 浏览器 / MCP…），末尾接固定文案。

**Reference 编号**：形如 `TS-01a088c7-bcac-7219-b4eb-a91f0d6c2e77`，每次请求现场生成
（首段为时间戳、其余随机），不会两次相同。

## 开关（都在文件靠前的配置区）

| 开关 | 默认 | 作用 |
| --- | --- | --- |
| `CLAUDE_BAN_ENABLED` / `claudeBanEnabled` | `True` | Claude 系是否走「封禁通知」分支；置 `False` 则一律走默认回复 |
| `PROBE_DEFENSE` / `probeDefense` | `True` | 是否启用指纹探针兼容层 |

## 拟真时序

| 环境变量 | 默认 | 作用 |
| --- | --- | --- |
| `CT_THINK_DELAY` | `3` | 非流式"思考"时长（秒，带 ±0.6s 抖动） |
| `CT_STREAM_DELAY` | `0.028` | 流式每小块之间的间隔（模拟逐 token 输出） |
| `CT_SENTENCE_PAUSE` | `0.18` | 句末停顿（galgame 式节奏） |
| `CT_PARAGRAPH_PAUSE` | `0.32` | 段落停顿 |

探测类端点（`/v1/models` 等）保持秒回，不影响客户端识别速度。

## 其它配置

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `CT_HOST` | `127.0.0.1` | 监听地址；对外提供服务改成 `0.0.0.0` |
| `CT_PORT` | `8788` | 监听端口 |
| `CT_BASE_URL` | `http://<host>:<port>` | 对外地址，决定响应里图片链接指向哪里 |

## 通用格式适配

- BaseURL 带不带 `/v1` 都能用；自动归一化客户端重复拼接的 `/v1/v1`、结尾多余斜杠
- API Key 不校验：不填、乱填、填正确值都放行
- 响应头带 `X-Request-Id`、`OpenAI-Version`、`OpenAI-Processing-Ms`、`X-RateLimit-*`；`Server` 头不泄露语言 / 版本
- 全量 CORS（含 `OPTIONS` 预检）

## 目录结构

```
openai_crazy_thursday.py   Python 版服务（零依赖，仅标准库）
openai_crazy_thursday.go   Go 版服务（零依赖，仅标准库）
go.mod                     Go 模块定义
start.sh / stop.sh         后台启停脚本
KFC疯狂星期四.png          回复附图素材（由 /image 端点输出）
DEPLOY.md                  服务器部署教程（域名 + HTTPS 反向代理）
```

## 部署

本机运行见上文「快速开始」。要在有域名的服务器上以 `https://你的域名/v1` 对外提供接口，
（systemd 开机自启、Nginx 反代 + HTTPS 证书）见 **[DEPLOY.md](DEPLOY.md)**。

---

— 疯狂星期四株式会社