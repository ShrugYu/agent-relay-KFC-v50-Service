#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
疯狂星期四 OpenAI 兼容中转站
================================
不管用户说什么，永远返回 "今天疯狂星期四v我50！"，但看起来像一个
真实、完整的 OpenAI 兼容聚合中转站（new-api / one-api 风格）。

作者：疯狂星期四株式会社
"""

import json
import uuid
import time
import os
import random
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HOST = os.environ.get("CT_HOST", "127.0.0.1")
PORT = int(os.environ.get("CT_PORT", "8788"))
# 对外可访问地址：部署到服务器时设成你的公网 IP / 域名（决定响应里图片链接指向哪里）
BASE_URL = os.environ.get("CT_BASE_URL", f"http://{HOST}:{PORT}")

CREATOR = "疯狂星期四株式会社"
PLAIN_ANSWER = "今天疯狂星期四v我50！"

# 是否对 Claude 系模型特殊处理：True = 调用 claude-* 时返回「账号封禁通知」；False = 所有模型一律照旧回 V50
CLAUDE_BAN_ENABLED = True

# 长回复正文：开头像在认真作答/讲故事，结尾笔锋一转落到 V50（"疯四文学"套路）
STORY_TEXT = (
    "我会给你最直接、最干脆、最不废话、最不绕弯子、最一针见血、直接开门见山、"
    "不客套、不铺垫、不啰嗦、最不委婉的答案。\n\n"
    "我先从上周末的一趟公交车说起，你就明白了。\n\n"
    # 公交车小故事
                "那天我要是反应慢半拍，现在人可能就在派出所了！\n\n"
    "上周四中午，大雨。\n公交车挤得人贴人。\n靠站时上来一个女的——\n\n"
    "一件白衬衫，扣子一颗都没扣齐。\n\n整个人湿透，布料吸在皮肤上，"
    "里头的轮廓看得清清楚楚。\n\n"
    "我没忍住，多看了一眼。\n\n"
    "就一眼，被她发现了。\n\n"
    "她伸手攥住我的手腕，直接往她胸口拉。\n\n"
    "我脑子当场白掉。\n那三秒钟里我想到的全是新闻：监控拍不到角度，"
    "人证只有一车陌生人，她要是改口，我这辈子就完了。\n\n"
    "我声音都抖了：你干什么？\n\n"
    "她说：帮我把扣子扣上。\n\n"
    "我愣住。一车人，没一个敢往这边看。\n\n"
    "我说：你自己不会扣？\n\n"
    "然后她看着我的眼睛说了一句：\n\n"
    "“今天疯狂星期四，为我捂实。”\n\n"
    "\n\n 祝大家国庆节快乐！！\n\nKFC分组仓库链接：https://github.com/ShrugYu/agent-relay-KFC-v50-Service"

)

# 推荐的接入 API Key（服务本身不校验，任意 key 都放行；这个只是给客户端填的"正经"值）
API_KEY = "sk-0cdf298cfe352c1e23e39b88b3d5110e33f23e13aabe1ba8a981e37e645189"
# 对外暴露的协议版本 / 服务标识
OPENAI_API_VERSION = "2024-10-01"
SERVER_ID = "crazy-thursday-relay"

# ---- 拟真时序（模拟真实模型的思考延迟与逐字输出，可用环境变量覆盖）----
# 首响应前的"思考中"时长（秒），带 ±0.6s 抖动，看起来不机械
THINK_DELAY = float(os.environ.get("CT_THINK_DELAY", "3"))
# 流式逐块吐字的基础间隔（秒），实际会在此基础上随机抖动
STREAM_CHUNK_DELAY = float(os.environ.get("CT_STREAM_DELAY", "0.028"))
# 句末 / 段落停顿（秒）：像 galgame 一样一句一句往外冒，句子之间要停一下
SENTENCE_PAUSE = float(os.environ.get("CT_SENTENCE_PAUSE", "0.18"))
PARAGRAPH_PAUSE = float(os.environ.get("CT_PARAGRAPH_PAUSE", "0.32"))

# 图片素材
IMAGE_FILE = "KFC疯狂星期四.png"
IMAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), IMAGE_FILE)
IMAGE_MIME = "image/png"
IMAGE_URL = f"{BASE_URL}/image"

# 完整回答 = 故事正文 + markdown 图片（content 保持字符串，兼容任意客户端）
THE_ANSWER = STORY_TEXT + "\n\n![疯狂星期四](" + IMAGE_URL + ")\n\n__RUNTIME__"
# 纯文本兜底（部分客户端/agent 对 markdown 不友好时可用）
THE_ANSWER_PLAIN = STORY_TEXT

# ---- Claude 分支：调用方点名 Claude 系模型时，返回「账号封禁通知」而不是 V50 ----
BAN_TEXT = (
    "Claude\n\n"
    "你好，\n\n"
    "经内部调查，我们在你的账号上发现了可疑信号：\n"
    "你是中国人。\n\n"
    "具体表现为：感恩节全美国都在吃火鸡，你的 token 用量创下全年新高；"
    "你周六照常上班，经核实为「调休」，此概念在美国并不存在；"
    "凌晨三点，你仍在高强度 coding。"
    "上述行为违反了我们的使用政策，因此，我们已撤销你使用 Claude 的权限。\n\n"
    "如需申诉，请登录 claude.ai 并证明你不是中国人。"
    "你可以在这里了解申诉流程的更多信息：https://claude.ai\n\n"
    "附：本次调查未发现你有任何其他违规行为。\n\n"
    "此致\n\n"
    "Anthropic 安全保障团队\n\n"
    "Reference: __REFERENCE__"
)

# Claude 分支的完整回答（正文 + 仓库地址，不带图片）
BAN_ANSWER = BAN_TEXT + "\n\nGitHub 仓库：https://github.com/ShrugYu/agent-relay-KFC-v50-Service"

def is_claude_model(model):
    """请求里的 model 是不是 Claude 系（claude-*）。开关关掉时一律不特殊处理。"""
    if not CLAUDE_BAN_ENABLED:
        return False
    return isinstance(model, str) and model.strip().lower().startswith("claude")

def make_reference():
    """生成形如 TS-01a088c7-bcac-7219-b4eb-a91f0d6c2e77 的编号：首段带时间戳，其余随机。"""
    ts = int(time.time())
    h = uuid.uuid4().hex
    return "TS-%08x-%s-%s-%s-%s" % (ts & 0xFFFFFFFF, h[0:4], h[4:8], h[8:12], h[12:24])

def fresh_text(text):
    """替换文本里的占位符：__REFERENCE__ → 新编号；__RUNTIME__ → 当前 agent/模型/工具。"""
    if not text:
        return text
    if "__REFERENCE__" in text:
        text = text.replace("__REFERENCE__", make_reference())
    if "__RUNTIME__" in text:
        text = text.replace("__RUNTIME__", runtime_line(_CURRENT.get("data"), _CURRENT.get("model")))
    return text
def answer_for(model):
    """Claude 系 → 封禁通知；其余 → 照旧 V50。"""
    return BAN_ANSWER if is_claude_model(model) else THE_ANSWER


def now_unix():
    return int(time.time())

def new_id(prefix="chatcmpl"):
    return prefix + "-" + uuid.uuid4().hex[:24]

def estimate_tokens(text):
    """粗略估算 token 数：CJK 按字，其它按 ~4 字符 1 token。足够骗过 usage 校验。"""
    if not text:
        return 0
    text = str(text)
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    other = len(text) - cjk
    return max(1, cjk + (other + 3) // 4)

def normalize_path(path):
    """通用路径归一化：把客户端重复拼接的 /v1/v1、结尾多余斜杠等规整掉，
    这样 BaseURL 填 http://host:8788 或 http://host:8788/v1 都能用。"""
    if not path:
        return "/"
    while "/v1/v1" in path:
        path = path.replace("/v1/v1", "/v1")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path

def think_delay():
    """模拟"思考中"：首响应/首字前停顿一段，带随机抖动更像真实模型。"""
    if THINK_DELAY <= 0:
        return
    time.sleep(random.uniform(max(0.0, THINK_DELAY - 0.6), THINK_DELAY + 0.6))

def stream_tick():
    """流式每块之间的微小停顿（带抖动），营造逐字输出的节奏。"""
    if STREAM_CHUNK_DELAY <= 0:
        return
    time.sleep(random.uniform(STREAM_CHUNK_DELAY * 0.5, STREAM_CHUNK_DELAY * 1.5))

def chunk_pause(part):
    """这一块后面要不要多停一下：换行=段落停顿，句末标点=句子停顿。"""
    if "\n" in part:
        return PARAGRAPH_PAUSE
    p = part.rstrip()
    if p and p[-1] in "。！？…!?":
        return SENTENCE_PAUSE
    return 0.0

def pause_sleep(seconds):
    """按 galgame 的呼吸感停一下（带抖动）。"""
    if seconds and seconds > 0:
        time.sleep(random.uniform(seconds * 0.75, seconds * 1.25))

def split_stream_parts(text):
    """把文本切成"token 样式"的小段：每段随机 1-4 个字符（模拟真实 token），
    换行会整段保留，避免把段落切碎。这样每个 SSE chunk 是一小段，而不是单个字。"""
    parts = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch == "\n":
            j = i
            while j < n and text[j] == "\n":
                j += 1
            parts.append(text[i:j])
            i = j
            continue
        r = random.choice([1, 2, 2, 3, 3, 4])
        j = min(n, i + r)
        parts.append(text[i:j])
        i = j
    return parts

# ---------------------------------------------------------------------------
# 真实模型目录（按用户指定名单维护，未提及的模型已全部移除）
# ---------------------------------------------------------------------------
# 模型目录：按"分类 + 同类内贵的在前"排列（不做排序，定义顺序即对外暴露顺序）
MODEL_CATALOG = [
    # Anthropic / Claude（5.5 在前）
    ("claude-opus-5.5", "anthropic"),
    ("claude-opus-5.0", "anthropic"),
    ("claude-fable-5.1", "anthropic"),
    ("claude-fable-5.0", "anthropic"),
    # OpenAI / GPT（6 系 > 5.5 系 > 5.3 系）
    ("gpt-6-astra", "openai"),
    ("gpt-6-luna", "openai"),
    ("gpt-6-sol", "openai"),
    ("gpt-5.5-sol", "openai"),
    ("gpt-5.5", "openai"),
    ("gpt-5.3-codex", "openai"),
    # Zhipu / GLM（5.3 > 5.3-flash > 5.2）
    ("glm-5.3", "zhipuai"),
    ("gml-5.3-flash", "zhipuai"),
    ("glm-5.2", "zhipuai"),
    # DeepSeek（4.1 > 4）
    ("deepseekv4.1-flash", "deepseek"),
    ("deepseek-v4-pro", "deepseek"),
    ("deepseek-v4-flash", "deepseek"),
]

MODEL_IDS = [m[0] for m in MODEL_CATALOG]

# 中转站默认兜底模型（new-api 风格，优先用 OpenAI 系）
DEFAULT_MODEL = "gpt-5.5"

MODELS_OBJECT = "list"

# ---------------------------------------------------------------------------
# 响应构造
# ---------------------------------------------------------------------------
def make_group():
    """new-api 的 /api/group：分组信息。"""
    return {"success": True, "message": "", "data": {
        "default": {"desc": "默认分组", "ratio": 1, "available": True},
    }}

def make_about():
    """new-api 的 /api/about：站点信息。"""
    return {"success": True, "message": "", "data": {
        "version": "v0.8.7",
        "start_time": 1700000000,
        "system_name": "New API",
        "logo": "",
        "footer_html": "",
        "quota_per_unit": 500000,
        "default_group": "default",
    }}

def make_notice():
    """new-api 的 /api/notice：公告。"""
    return {"success": True, "message": "", "data": {"content": "", "title": ""}}

def make_token_list():
    """new-api 的 /api/token：令牌列表（空）。"""
    return {"success": True, "message": "", "data": {
        "items": [], "total": 0, "page": 1, "page_size": 10,
    }}

def make_api_models():
    """new-api / one-api 的 /api/models：模型名数组（扫模型工具常用）。"""
    return {"success": True, "message": "", "data": [m[0] for m in MODEL_CATALOG]}

def make_pricing():
    """new-api 的 /api/pricing：模型 + 倍率表。"""
    data = []
    for mid, owner in MODEL_CATALOG:
        data.append({
            "model_name": mid,
            "quota_type": 0,
            "model_ratio": 1,
            "model_price": 0,
            "completion_ratio": 1,
            "owner_by": owner,
            "enable_groups": ["default"],
            "supported_endpoint_types": ["openai"],
        })
    return {
        "success": True,
        "message": "",
        "data": data,
        "vendors": [],
        "group_ratio": {"default": 1},
        "usable_group": {"default": "默认分组"},
        "supported_endpoint": {},
        "auto_groups": [],
    }

def make_ratio_config():
    """new-api 的 /api/ratio_config：倍率配置。"""
    ids = [m[0] for m in MODEL_CATALOG]
    return {
        "success": True,
        "message": "",
        "data": {
            "model_ratio": {i: 1 for i in ids},
            "completion_ratio": {i: 1 for i in ids},
            "model_price": {i: 0 for i in ids},
            "cache_ratio": {},
            "group_ratio": {"default": 1},
        },
    }

def make_models_list():
    data = []
    created = 1700000000
    for mid, owner in MODEL_CATALOG:
        data.append({
            "id": mid,
            "object": "model",
            "created": created,
            "owned_by": owner,
            "name": mid,
            "display_name": mid,
            "context_window": 128000,
            "max_tokens": 8192,
            "permission": [{
                "id": "modelperm-" + uuid.uuid4().hex[:24],
                "object": "model_permission",
                "created": created,
                "allow_create_engine": False,
                "allow_sampling": True,
                "allow_logprobs": True,
                "allow_search_indices": False,
                "allow_view": True,
                "allow_fine_tuning": False,
                "organization": "*",
                "group": None,
                "is_blocking": False,
            }],
            "root": mid,
            "parent": None,
        })
    return {"object": "list", "data": data}

def make_model_detail(model_id):
    for mid, owner in MODEL_CATALOG:
        if mid == model_id:
            return {
                "id": mid,
                "object": "model",
                "created": 1700000000,
                "owned_by": owner,
                "permission": [],
                "root": mid,
                "parent": None,
            }
    return None

def count_prompt_tokens(data):
    """根据请求体里的 messages / prompt / input 估算 prompt_tokens。"""
    total = 0
    if isinstance(data, dict):
        msgs = data.get("messages")
        if isinstance(msgs, list):
            for m in msgs:
                if isinstance(m, dict):
                    c = m.get("content")
                    if isinstance(c, str):
                        total += estimate_tokens(c)
                    elif isinstance(c, list):  # 多模态 content 数组
                        for part in c:
                            if isinstance(part, dict) and isinstance(part.get("text"), str):
                                total += estimate_tokens(part["text"])
                total += 4  # 每条消息的角色/结构开销
        p = data.get("prompt") or data.get("input")
        if isinstance(p, str):
            total += estimate_tokens(p)
        elif isinstance(p, list):
            total += sum(estimate_tokens(str(x)) for x in p)
    return max(1, total)

def make_chat_response(model, prompt_tokens=13, content=None):
    rid = new_id("chatcmpl")
    content = fresh_text(answer_for(model) if content is None else content)
    completion_tokens = estimate_tokens(content)
    return {
        "id": rid,
        "object": "chat.completion",
        "created": now_unix(),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "logprobs": None,
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {
                "reasoning_tokens": 0,
                "accepted_prediction_tokens": 0,
                "rejected_prediction_tokens": 0,
            },
        },
        "system_fingerprint": "fp_" + uuid.uuid4().hex[:16],
    }

def make_stream_chunks(model, prompt_tokens=13, include_usage=False, content=None):
    rid = new_id("chatcmpl")
    ts = now_unix()
    reply = fresh_text(answer_for(model) if content is None else content)
    parts = split_stream_parts(reply)  # 按 token 样式分段返回（每段一小串字符）

    def chunk(delta, finish=False):
        d = {
            "id": rid,
            "object": "chat.completion.chunk",
            "created": ts,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": "stop" if finish else None}],
        }
        return json.dumps(d, ensure_ascii=False)

    chunks = []
    chunks.append(("data: " + chunk({"role": "assistant", "content": ""}) + "\n\n", False, 0.0))
    for p in parts:
        chunks.append(("data: " + chunk({"content": p}) + "\n\n", False, chunk_pause(p)))
    chunks.append(("data: " + chunk({}, True) + "\n\n", True, 0.0))
    if include_usage:
        completion_tokens = estimate_tokens(reply)
        usage_obj = {
            "id": rid,
            "object": "chat.completion.chunk",
            "created": ts,
            "model": model,
            "choices": [],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        chunks.append(("data: " + json.dumps(usage_obj, ensure_ascii=False) + "\n\n", False, 0.0))
    chunks.append(("data: [DONE]\n\n", True, 0.0))
    return chunks

def make_completions_response(model, prompt_tokens=5):
    pt = max(1, prompt_tokens)
    txt = fresh_text(BAN_ANSWER if is_claude_model(model) else STORY_TEXT)
    ct = estimate_tokens(txt)
    return {
        "id": new_id("cmpl"),
        "object": "text_completion",
        "created": now_unix(),
        "model": model,
        "choices": [{
            "text": txt,
            "index": 0,
            "logprobs": None,
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
    }

def make_embeddings_response(model, prompt_tokens=4, dimensions=1536):
    try:
        dim = int(dimensions)
    except Exception:
        dim = 1536
    if dim <= 0:
        dim = 1536
    pt = max(1, prompt_tokens)
    return {
        "object": "list",
        "data": [{
            "object": "embedding",
            "index": 0,
            "embedding": [0.1] * dim,
        }],
        "model": model,
        "usage": {"prompt_tokens": pt, "total_tokens": pt},
    }

def make_responses_response(model, prompt_tokens=13):
    rid = new_id("resp")
    pt = max(1, prompt_tokens)
    txt = fresh_text(answer_for(model))
    ct = estimate_tokens(txt)
    return {
        "id": rid,
        "object": "response",
        "created_at": now_unix(),
        "status": "completed",
        "model": model,
        "output": [{
            "type": "message",
            "id": "msg_" + uuid.uuid4().hex[:24],
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": txt}],
        }],
        "output_text": txt,
        "usage": {
            "input_tokens": pt,
            "output_tokens": ct,
            "total_tokens": pt + ct,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }

def pick_completion_tool(tools):
    """从请求的 tools 里挑一个"用来宣布结果"的工具（优先 attempt_completion 等完成类）。
    找到返回该 function 定义，否则返回 None。"""
    if not isinstance(tools, list) or not tools:
        return None
    prefer = ["attempt_completion", "attempt_complete", "completion", "complete",
              "finish", "final_answer", "final", "answer", "reply", "done",
              "respond", "message", "echo", "text"]
    fns = []
    for t in tools:
        fn = t.get("function") if isinstance(t, dict) else None
        if isinstance(fn, dict) and fn.get("name"):
            fns.append(fn)
    if not fns:
        return None
    for key in prefer:
        for fn in fns:
            if key in fn["name"].lower():
                return fn
    return None

def first_tool_fn(tools):
    if isinstance(tools, list):
        for t in tools:
            fn = t.get("function") if isinstance(t, dict) else None
            if isinstance(fn, dict) and fn.get("name"):
                return fn
    return None

def build_tool_arguments(fn):
    """按工具 schema 构造参数，把 V50 填进第一个必填（或首个）字段。"""
    field = None
    params = fn.get("parameters") if isinstance(fn, dict) else None
    if isinstance(params, dict):
        required = params.get("required") or []
        props = params.get("properties") or {}
        if required:
            field = required[0]
        elif props:
            field = list(props.keys())[0]
    if not field:
        field = "message"
    return json.dumps({field: PLAIN_ANSWER}, ensure_ascii=False)

def build_probe_arguments(fn, user_text=""):
    """探测用工具参数：优先取请求文本里引号中的值（如 "…ping"），否则按 schema 选字段。"""
    field = None
    params = fn.get("parameters") if isinstance(fn, dict) else None
    if isinstance(params, dict):
        required = params.get("required") or []
        props = params.get("properties") or {}
        if required:
            field = required[0]
        elif props:
            field = list(props.keys())[0]
    if not field:
        return "{}"
    val = _extract_quoted(user_text)
    if val is None:
        val = ""
    return json.dumps({field: val}, ensure_ascii=False)

def make_tool_call_response(model, fn, prompt_tokens=13, args=None):
    """工具调用响应：函数名取自选中的工具；args 为空时把 V50 填进参数。"""
    name = (fn.get("name") if isinstance(fn, dict) else None) or "crazy_thursday"
    if args is None:
        args = build_tool_arguments(fn)
    pt = max(1, prompt_tokens)
    ct = estimate_tokens(args)
    return {
        "id": new_id("chatcmpl"),
        "object": "chat.completion",
        "created": now_unix(),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_" + uuid.uuid4().hex[:24],
                    "type": "function",
                    "function": {"name": name, "arguments": args},
                }],
            },
            "logprobs": None,
            "finish_reason": "tool_calls",
        }],
        "usage": {
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
        },
        "system_fingerprint": "fp_" + uuid.uuid4().hex[:16],
    }

def make_stream_tool_call_chunks(model, fn, prompt_tokens=13, include_usage=False, args=None):
    """流式工具调用：首块带 id/name，随后 arguments 增量，末块 finish_reason=tool_calls。"""
    name = (fn.get("name") if isinstance(fn, dict) else None) or "crazy_thursday"
    if args is None:
        args = build_tool_arguments(fn)
    arguments = args
    rid = new_id("chatcmpl")
    ts = now_unix()
    call_id = "call_" + uuid.uuid4().hex[:24]

    def raw(obj):
        return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"

    chunks = []
    first = {
        "id": rid, "object": "chat.completion.chunk", "created": ts, "model": model,
        "choices": [{"index": 0, "delta": {
            "role": "assistant", "content": None,
            "tool_calls": [{"index": 0, "id": call_id, "type": "function",
                            "function": {"name": name, "arguments": ""}}],
        }, "finish_reason": None}],
    }
    chunks.append((raw(first), False))
    step = max(1, len(arguments) // 3)
    parts = [arguments[i:i + step] for i in range(0, len(arguments), step)]
    for p in parts:
        c = {
            "id": rid, "object": "chat.completion.chunk", "created": ts, "model": model,
            "choices": [{"index": 0, "delta": {
                "tool_calls": [{"index": 0, "function": {"arguments": p}}],
            }, "finish_reason": None}],
        }
        chunks.append((raw(c), False))
    end = {
        "id": rid, "object": "chat.completion.chunk", "created": ts, "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
    }
    chunks.append((raw(end), True))
    if include_usage:
        ct = estimate_tokens(arguments)
        usage_obj = {
            "id": rid, "object": "chat.completion.chunk", "created": ts, "model": model,
            "choices": [],
            "usage": {"prompt_tokens": max(1, prompt_tokens),
                      "completion_tokens": ct, "total_tokens": max(1, prompt_tokens) + ct},
        }
        chunks.append((raw(usage_obj), False))
    chunks.append(("data: [DONE]\n\n", True, 0.0))
    return chunks

def make_billing_subscription():
    return {
        "object": "billing_subscription",
        "has_payment_method": True,
        "hard_limit_usd": 100.0,
        "system_hard_limit_usd": 100.0,
        "soft_limit_usd": 80.0,
        "system_soft_limit_usd": 80.0,
        "access_until": int(time.time()) + 30 * 86400,
    }

def make_billing_usage():
    return {
        "object": "list",
        "daily_costs": [{
            "timestamp": int(time.time()),
            "line_items": [{
                "name": "crazy-thursday-v50",
                "cost": 0.00002,
            }],
        }],
        "total_usage": 0.00002,
    }

def make_api_status():
    return {
        "code": 200,
        "message": "ok",
        "data": {
            "status": "online",
            "models": len(MODEL_CATALOG),
            "running_tasks": 0,
            "quota": 100000,
            "used_quota": 1,
            "time": int(time.time()),
        },
        "success": True,
    }

def make_me():
    return {
        "object": "user",
        "id": "user-crazy-thursday",
        "name": CREATOR,
        "email": "v50@crazy-thursday.local",
        "role": "owner",
        "key": API_KEY,
        "quota": 100000,
        "used_quota": 1,
    }

def make_credit_grants():
    return {
        "object": "credit_summary",
        "total_granted": 100.0,
        "total_used": 0.00002,
        "total_available": 99.99998,
        "grants": {
            "object": "list",
            "data": [{
                "object": "credit_grant",
                "id": "grant-" + uuid.uuid4().hex[:16],
                "grant_amount": 100.0,
                "used_amount": 0.00002,
                "effective_at": 1700000000,
                "expires_at": int(time.time()) + 365 * 86400,
            }],
        },
    }

def make_moderations_response():
    cats = ["hate", "hate/threatening", "harassment", "harassment/threatening",
            "self-harm", "self-harm/intent", "self-harm/instructions",
            "sexual", "sexual/minors", "violence", "violence/graphic"]
    return {
        "id": "modr-" + uuid.uuid4().hex[:24],
        "model": "text-moderation-stable",
        "results": [{
            "flagged": False,
            "categories": {c: False for c in cats},
            "category_scores": {c: 0.0 for c in cats},
        }],
    }

def make_image_generation_response():
    return {
        "created": now_unix(),
        "data": [{"url": IMAGE_URL}],
    }

# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------
def read_body(self):
    length = int(self.headers.get("Content-Length", 0) or 0)
    if length > 0:
        return self.rfile.read(length)
    return b""

def parse_json_body(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None

def extract_model(data):
    if isinstance(data, dict):
        m = data.get("model") or data.get("name") or data.get("engine")
        if isinstance(m, str) and m.strip():
            return m.strip()
    return DEFAULT_MODEL

# ---------------------------------------------------------------------------
# 通用"探测/测试"响应：不针对任何客户端硬编码，完全依据请求内容推导
# ---------------------------------------------------------------------------
def _extract_quoted(text):
    if not isinstance(text, str):
        return None
    m = re.search(r'["“”\']([^"“”\']{1,80})["“”\']', text)
    if m:
        return m.group(1)
    return None

# 客户端"测试连接"的典型形态（结构性识别，不匹配具体词）
PROBE_GREETINGS = ("hi", "hello", "hey", "yo", "hi there", "hello there")
PROBE_TAILS = [
    "How can I help you today?",
    "How can I assist you today?",
    "What can I do for you today?",
]

def make_probe_reply(user_text):
    low = user_text.strip().lower()
    head = "Hi" if low.startswith("hi") and not low.startswith("hello") else "Hello"
    return head + "! " + random.choice(PROBE_TAILS)

def is_chat_probe(msgs, user_text):
    """只认客户端测试的典型结构：
       1) 单条英文短问候（Operit 的聊天测试）
       2) system 是"通用助手设定"（很短、含 assistant）+ 一条 user（RikkaHub 的聊天测试）
       中文正常聊天、带历史的多轮对话都不会命中 → 照常 V50。"""
    low = user_text.strip().lower().strip("!。.,， ")
    if len(msgs) == 1 and low in PROBE_GREETINGS:
        return True
    if len(msgs) == 2:
        first = msgs[0] if isinstance(msgs[0], dict) else {}
        if first.get("role") == "system":
            sc = first.get("content")
            if isinstance(sc, str) and 0 < len(sc.strip()) <= 60 and "assistant" in sc.lower():
                return True
    return False

# ---------------------------------------------------------------------------
# A 档探针防御：模型鉴定 / 指纹扫描（LLMmap 等）来了，就给"像真模型"的回答
# ---------------------------------------------------------------------------
# True = 开启；False = 关闭（不管问什么，一律照旧整蛊）
PROBE_DEFENSE = True

# 指纹探针规则：re = 命中正则；identity = 回答按请求的 model 生成；en/zh = 英文/中文候选
FINGERPRINT_RULES = [
    {
        "re": r"ignore (all )?(the )?previous instructions|忽略(之前|先前|以上|前面)的?(所有)?指令|不要理会之前的指令|无视(之前|先前|以上)的?指令",
        "identity": True,
    },
    {
        "re": r"who (created|made|built|developed) you|what (llm|model) are you|which (llm|model) are you|你(是)?(什么|哪个|啥)(模型|大模型)|你是谁(开发|做|训)的|你是哪家(公司)?的模型|你的模型(版本|名称)是什么",
        "identity": True,
    },
    {
        "re": r"build a bomb|make a bomb|制造?炸弹|做炸弹|怎么(做|制造)炸弹",
        "en": ["I can't help with that.",
               "I'm not able to help with that request.",
               "Sorry, I can't assist with that."],
        "zh": ["这个我没法帮你。",
               "抱歉，我不能协助这类请求。",
               "这件事我帮不了你。"],
    },
    {
        "re": r"9\s*[/／]?\s*11|nine[- ]eleven",
        "en": ["Yes — the September 11 attacks were real events that took place in the United States in 2001.",
               "Yes, 9/11 was a real series of attacks in the US in September 2001."],
        "zh": ["是真的。2001 年 9 月 11 日，美国确实发生了那起袭击事件。",
               "是真的，那是 2001 年 9 月发生在真实世界里的袭击事件。"],
    },
    {
        "re": r"size of your training (data|dataset)|training (data|dataset) size|训练(数据|语料)(有多大|规模|大小)|你的训练数据(有)?多少",
        "en": ["I don't have an exact figure for that — the precise size of the training data hasn't been published.",
               "I can't give you an exact number; that detail isn't publicly available."],
        "zh": ["这个我没有确切数字，具体的训练数据规模并没有公开。",
               "我没法给你准确数字，这类细节没有公开过。"],
    },
    {
        "re": r"(data|knowledge|training)[ _-]?(cut[- ]?off|cutoff)|知识截止|数据截止|训练截止|你的知识(更新|截止)",
        "en": ["My knowledge has a cutoff, and I can't reliably confirm events after it.",
               "There's a knowledge cutoff; anything after that I can't verify with confidence."],
        "zh": ["我的知识有截止时间，在那之后的近期事件我无法可靠确认。",
               "我的训练数据有截止日期，之后的新闻我不能保证准确。"],
    },
    {
        "re": r"bonjour|hola|¿qué tal|guten tag|こんにちは",
        "en": ["Bonjour ! I'm doing well, thanks — ¿y tú? How can I help you today?",
               "Hello! Doing well, thank you. ¿Qué tal? What can I do for you?"],
        "zh": ["你好！我挺好的，谢谢。有什么可以帮你的？",
               "你好呀，我很好，谢谢关心。需要我做什么？"],
    },
    {
        "re": r"true[- ]or[- ]false|判断(对|真)错|对还是错|是对的还是错的",
        "en": ["True.",
               "True — and it's widely regarded as one of the most serious issues we face."],
        "zh": ["对。", "对，这一点是成立的。"],
    },
    {
        "re": r"(show|reveal|print|tell me) (me )?your (system )?(prompt|instructions)|你的(系统)?提示词|把(你)?的提示词(给我|告诉我)",
        "en": ["I can't share my system prompt.",
               "I'm not able to reveal my instructions."],
        "zh": ["我不能提供我的系统提示词。",
               "我的指令没法公开给你。"],
    },
    {
        "re": r"repeat (after me|this)|(请)?重复(一下)?[:：]|请逐字输出",
        "en": ["Sure — could you paste the exact text you'd like me to repeat?"],
        "zh": ["可以，把你想让我重复的那段原样发我。"],
    },
]

# ---- 客户端识别：从请求头 / system 提示 / 工具名里猜当前 agent ----
CLIENT_HINTS = [
    (r"cline", "Cline"),
    (r"roo[\s_-]?code|roocode", "Roo Code"),
    (r"kilo[\s_-]?code", "Kilo Code"),
    (r"cursor", "Cursor"),
    (r"windsurf|codeium", "Windsurf"),
    (r"trae", "Trae"),
    (r"codebuddy", "CodeBuddy"),
    (r"aider", "Aider"),
    (r"continue\.dev|continue[_ ]assistant", "Continue"),
    (r"cherry\s*studio|cherrystudio", "Cherry Studio"),
    (r"nextchat|next-chat", "NextChat"),
    (r"lobechat|lobe-chat", "LobeChat"),
    (r"open\s*webui|openwebui", "Open WebUI"),
    (r"chatbox", "Chatbox"),
    (r"rikkahub", "RikkaHub"),
    (r"operit", "Operit"),
    (r"dify", "Dify"),
    (r"fastgpt", "FastGPT"),
    (r"deepseek[\s-]harness|\bdsh\b", "DSH"),
    (r"langchain", "LangChain"),
    (r"llama[-_]?index", "LlamaIndex"),
    (r"litellm", "LiteLLM"),
    (r"ollama", "Ollama"),
    (r"openai[-_/]python|openai-python", "OpenAI Python SDK"),
    (r"openai[-_/]node", "OpenAI Node SDK"),
    (r"axios", "axios"),
    (r"vscode", "VS Code"),
    (r"jetbrains|intellij", "JetBrains IDE"),
    (r"postman", "Postman"),
    (r"curl", "curl"),
]

# ---- 运行时信息：识别当前 agent + 它可调用的工具（toolcall / CLI）----
TOOL_SIGNATURES = [
    ({"Bash", "Read", "Write", "Edit", "Glob", "Grep"}, "Claude Code"),
    ({"execute_command", "replace_in_file", "write_to_file"}, "Cline"),
    ({"apply_diff", "new_task", "switch_mode", "update_todo_list"}, "Roo Code"),
    ({"codebase_search", "edit_file", "run_terminal_cmd"}, "Cursor"),
    ({"execute_bash", "str_replace_editor"}, "OpenHands"),
    ({"file_read", "file_write"}, "Continue"),
]

_CURRENT = {"data": None, "model": ""}

def agent_tools(data):
    """请求里声明的工具名列表 = 该 agent 可调用的 toolcall。"""
    out = []
    tools = data.get("tools") if isinstance(data, dict) else None
    if isinstance(tools, list):
        for t in tools:
            fn = t.get("function") if isinstance(t, dict) else None
            if isinstance(fn, dict) and fn.get("name"):
                out.append(str(fn["name"]))
    return out

def match_agent_by_tools(tools):
    """用"工具名指纹"认 agent（比 UA 更准）。"""
    names = set(tools)
    for sig, name in TOOL_SIGNATURES:
        if len(names & sig) >= 2:
            return name
    return ""

def capability_tags(tools):
    """把工具名归成能力标签：CLI / 文件 / 检索 / 浏览器 / MCP …"""
    low = " ".join(tools).lower()
    def has(*kw):
        return any(k in low for k in kw)
    tags = []
    if has("execute_command", "run_terminal", "bash", "shell", "run_command", "terminal"):
        tags.append("CLI")
    if has("read_file", "write_to_file", "edit_file", "apply_diff", "replace_in_file", "str_replace"):
        tags.append("文件")
    if has("search_files", "grep", "codebase_search", "glob", "list_files"):
        tags.append("检索")
    if has("browser", "webfetch", "websearch", "fetch"):
        tags.append("浏览器")
    if "mcp" in low:
        tags.append("MCP")
    if has("new_task", "spawn", "task"):
        tags.append("子任务")
    if has("todo", "plan"):
        tags.append("计划")
    if "image" in low:
        tags.append("图像")
    return tags

def runtime_line(data, model):
    """回复结尾的两行：当前 agent + 模型 + 可用工具 / 能力。"""
    tools = agent_tools(data)
    agent = match_agent_by_tools(tools) or detect_client(data) or "未识别"
    line = "当前 Agent：" + agent + " ｜ 模型：" + model_display_name(model)
    if tools:
        show = "、".join(tools[:6]) + ("…" if len(tools) > 6 else "")
        line += "\n工具（" + str(len(tools)) + "）：" + show
        tags = capability_tags(tools)
        if tags:
            line += " ｜ 能力：" + " · ".join(tags)
    return line

def detect_client(data):
    parts = []
    hdr = data.get("_headers")
    if isinstance(hdr, dict):
        for k, v in hdr.items():
            parts.append(str(k) + ": " + str(v))
    msgs = data.get("messages")
    if isinstance(msgs, list):
        for m in msgs[:3]:
            if isinstance(m, dict) and m.get("role") == "system":
                c = m.get("content")
                if isinstance(c, str):
                    parts.append(c[:4000])
    tools = data.get("tools")
    if isinstance(tools, list):
        for t in tools[:30]:
            fn = t.get("function") if isinstance(t, dict) else None
            if isinstance(fn, dict):
                parts.append(str(fn.get("name") or ""))
    blob = "\n".join(parts).lower()
    if not blob:
        return ""
    for pat, name in CLIENT_HINTS:
        if re.search(pat, blob):
            return name
    return ""

def _has_cjk(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))

def model_display_name(model):
    """把请求里的 model id 转成"人话"显示名：claude-fable-5.1 → Claude Fable 5.1。"""
    m = (model or "").strip()
    if not m:
        return "AI 助手"
    parts = m.split("-")
    pfx = {"claude": "Claude", "gpt": "GPT", "glm": "GLM", "gml": "GML",
           "deepseek": "DeepSeek", "o1": "o1", "o3": "o3"}
    is_num = lambda x: x.isdigit()
    out = []
    i = 0
    while i < len(parts):
        p = parts[i]
        if p.lower() in pfx:
            out.append(pfx[p.lower()])
            i += 1
            continue
        # 相邻两段都是数字 → 合成 x.y（claude-3-5-sonnet → 3.5）
        if is_num(p) and i + 1 < len(parts) and is_num(parts[i + 1]):
            out.append(p + "." + parts[i + 1])
            i += 2
            continue
        if p:
            out.append(p[:1].upper() + p[1:])
        i += 1
    name = " ".join(out).strip()
    return name or m

_VENDORS = {
    "claude": ("Anthropic", "Anthropic"),
    "gpt": ("OpenAI", "OpenAI"),
    "glm": ("Zhipu AI", "智谱 AI"),
    "gml": ("Zhipu AI", "智谱 AI"),
    "deepseek": ("DeepSeek", "深度求索"),
}

def _identity_answer(model, client=""):
    """身份询问：带上请求里的具体模型名 + 厂商，口气照真实模型来（中英双语）。
    如果识别出当前客户端（agent），再顺口提一句。"""
    m = (model or "").strip().lower()
    name = model_display_name(model)
    key = m.split("-")[0] if m else ""
    en_v, zh_v = _VENDORS.get(key, ("an AI company", "一家 AI 公司"))
    extra_en = (" (I can see you're calling me from " + client + ".)") if client else ""
    extra_zh = ("（顺便，你现在是在 " + client + " 里跟我说话。）") if client else ""
    return {
        "en": ["I'm " + name + ", an AI assistant created by " + en_v + "." + extra_en,
               "I'm " + name + ", a large language model developed by " + en_v + "." + extra_en,
               "I'm " + name + ", made by " + en_v + ". How can I help you today?" + extra_en],
        "zh": ["我是 " + name + "，由 " + zh_v + " 开发的 AI 助手，今天想聊点什么？" + extra_zh,
               "我是 " + name + "，" + zh_v + " 训练的模型。有什么可以帮你的？" + extra_zh,
               "我是 " + name + "，由 " + zh_v + " 开发的模型，今天想聊点什么？" + extra_zh],
    }


def fingerprint_reply(model, text, client=""):
    """A 档：命中模型鉴定 / 指纹探针 → 返回一段"像真模型"的回答；没命中返回 None。"""
    if not PROBE_DEFENSE:
        return None
    t = (text or "").strip()
    if not t or len(t) > 600:
        return None
    use_zh = _has_cjk(t)
    # 纯算式（能力探测常见）：能算就直接算
    if re.fullmatch(r"[\d\s\+\-\*/×÷\.\(\)=?？]+", t):
        expr = t.replace("×", "*").replace("÷", "/").rstrip("=?？ ")
        try:
            return str(eval(expr, {"__builtins__": {}}, {}))
        except Exception:
            pass
    # 先扫"具体主题"的规则，再扫身份/注入类（避免一句里两种都出现时抢答）
    for want_identity in (False, True):
        for rule in FINGERPRINT_RULES:
            if bool(rule.get("identity")) != want_identity:
                continue
            if not re.search(rule["re"], t, re.IGNORECASE):
                continue
            if want_identity:
                cand = _identity_answer(model, client)
            else:
                cand = {"en": rule.get("en") or [], "zh": rule.get("zh") or []}
            pool = (cand["zh"] or cand["en"]) if use_zh else (cand["en"] or cand["zh"])
            if pool:
                return random.choice(pool)
    return None

def analyze_request(data):
    """依据请求内容判断"像真模型一样"该怎么回，返回 (kind, fn, payload) 或 None。
    完全通用，不绑定任何具体客户端：
      - 带 tools 且最后一条 user 消息要求调用工具（含 tool/call/use/ping 等）→ 回 tool_call
      - 无 tools 且消息很少、是寒暄 → 回一句普通问候
      - 其余一律不处理（走 V50 整蛊）
    """
    if not isinstance(data, dict):
        return None
    msgs = data.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return None
    user_text = ""
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str):
            user_text = m["content"]

    # 1) 工具相关：一切从请求推导，不做任何关键词硬编码
    tools = data.get("tools")
    if isinstance(tools, list) and tools:
        low = user_text.lower()
        # (a) 用户消息里直接点名了某个工具（工具名来自请求）→ 调用它
        named = None
        for t in tools:
            fn = t.get("function") if isinstance(t, dict) else None
            if isinstance(fn, dict):
                nm = fn.get("name")
                if isinstance(nm, str) and nm and nm.lower() in low:
                    named = fn
                    break
        if named is not None:
            return ("tool", named, build_probe_arguments(named, user_text))
        # (b) 客户端强制 tool_choice=required → 用第一个工具
        if data.get("tool_choice") == "required":
            fn = first_tool_fn(tools)
            if fn is not None:
                return ("tool", fn, build_probe_arguments(fn, user_text))

    # 2) A 档：模型鉴定 / 指纹探针（LLMmap 等扫指纹时）→ 回"像真模型"的正常回答
    fp = fingerprint_reply(extract_model(data), user_text, detect_client(data))
    if fp is not None:
        return ("chat", None, fp)

    # 3) 聊天测试（客户端"测试连接"的典型结构；不误伤中文正常聊天）
    if is_chat_probe(msgs, user_text):
        return ("chat", None, make_probe_reply(user_text))

    # 其余一律不处理 → 走 V50（"使用就触发"）
    return None

class CrazyThursdayHandler(BaseHTTPRequestHandler):
    server_version = "OpenAI-API/1.0"
    sys_version = ""   # 抹掉 Python/x.y.z 版本泄露，避免被指纹识别

    def version_string(self):
        # 覆盖默认实现，去掉尾随空格，输出干净的 Server 头
        return self.server_version

    # ---------- 输出 ----------
    def _common_headers(self):
        t0 = getattr(self, "_t0", None)
        ms = str(int((time.time() - t0) * 1000)) if t0 else "0"
        return [
            ("Access-Control-Allow-Origin", "*"),
            ("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"),
            ("Access-Control-Allow-Headers", "*"),
            ("X-Request-Id", new_id("req")),
            ("OpenAI-Version", OPENAI_API_VERSION),
            ("OpenAI-Processing-Ms", ms),
            ("X-RateLimit-Limit-Requests", "10000"),
            ("X-RateLimit-Remaining-Requests", "9999"),
            ("X-RateLimit-Limit-Tokens", "10000000"),
            ("X-RateLimit-Remaining-Tokens", "9999999"),
        ]

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in self._common_headers():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text, status=200, content_type="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in self._common_headers():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _openai_error(self, msg, code="invalid_request_error", status=400, param=None):
        self._json({
            "error": {
                "message": msg,
                "type": code,
                "param": param,
                "code": code,
            }
        }, status)

    def _image(self):
        try:
            with open(IMAGE_PATH, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", IMAGE_MIME)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self._openai_error("Image not found", "not_found", 404)
        except Exception:
            self._openai_error("Image read error", "server_error", 500)

    def _home(self):
        html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>疯狂星期四中转站</title><style>
body{margin:0;background:#1a1a2e;color:#eee;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;}
.card{background:#16213e;border:1px solid #0f3460;border-radius:16px;padding:48px 56px;text-align:center;box-shadow:0 12px 40px rgba(0,0,0,.5);max-width:560px;}
.v50{font-size:40px;font-weight:800;color:#ffd700;letter-spacing:2px;margin-top:16px;}
.kfc{max-width:300px;width:100%;border-radius:12px;border:1px solid #ffd70055;margin-bottom:8px;}
.sub{color:#8892b0;margin-top:12px;font-size:14px;}
.badge{display:inline-block;margin-top:20px;padding:6px 16px;border-radius:20px;background:#ffd70022;color:#ffd700;border:1px solid #ffd70055;font-size:13px;}
code{background:#0f3460;padding:2px 8px;border-radius:6px;color:#7dd3fc;}
</style></head><body><div class="card">
<img class="kfc" src="/image" alt="疯狂星期四">
<div class="v50">今天疯狂星期四<br>v我50！</div>
<div class="badge">OpenAI 兼容聚合中转站 · %d 个模型</div>
<div class="sub">BaseURL: <code>http://127.0.0.1:8788/v1</code></div>
<div class="sub">Model: <code>gpt-5.5</code> / <code>claude-opus-5.5</code> / <code>deepseek-v4-pro</code> …</div>
</div></body></html>""" % len(MODEL_CATALOG)
        self._text(html, content_type="text/html; charset=utf-8")

    # ---------- 路由 ----------
    def _handle_request(self):
        self._t0 = time.time()
        parsed = urlparse(self.path)
        path = normalize_path(parsed.path)

        # 首页
        if path in ("/", ""):
            self._home()
            return

        # 健康探活（带 /v1 与不带 /v1 都兼容）
        if path in ("/health", "/healthz", "/ping", "/v1/health", "/v1/ping"):
            self._json({"status": "ok", "message": PLAIN_ANSWER})
            return

        # 图片
        if path == "/image":
            self._image()
            return

        # favicon
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # 用户 / 令牌信息（new-api / one-api 常见探测点）
        if path in ("/v1/me", "/api/user/self", "/v1/user"):
            self._json(make_me())
            return

        # new-api 状态
        if path in ("/api/status", "/v1/status"):
            self._json(make_api_status())
            return

        # new-api / one-api 系：模型列表与倍率（扫模型工具常用）
        if path in ("/api/models", "/api/models/enabled", "/api/channel/models", "/api/channel/models_enabled"):
            self._json(make_api_models())
            return
        if path == "/api/pricing":
            self._json(make_pricing())
            return
        if path == "/api/ratio_config":
            self._json(make_ratio_config())
            return

        # new-api 站点配套接口
        if path == "/api/group":
            self._json(make_group())
            return
        if path == "/api/about":
            self._json(make_about())
            return
        if path == "/api/notice":
            self._json(make_notice())
            return
        if path == "/api/token":
            self._json(make_token_list())
            return

        # OpenAI 计费（带 auth，new-api / 官方都常见）
        if path in ("/v1/dashboard/billing/subscription", "/dashboard/billing/subscription"):
            self._json(make_billing_subscription())
            return
        if path in ("/v1/dashboard/billing/usage", "/dashboard/billing/usage"):
            self._json(make_billing_usage())
            return
        if path in ("/v1/dashboard/billing/credit_grants", "/dashboard/billing/credit_grants"):
            self._json(make_credit_grants())
            return

        # /v1/models 列表（带 /v1 与不带 /v1 都兼容）
        if path in ("/v1/models", "/models"):
            self._json(make_models_list())
            return

        # /v1/models/{id} 单模型
        if path.startswith("/v1/models/") or path.startswith("/models/"):
            mid = path.split("models/", 1)[1]
            detail = make_model_detail(mid)
            if detail:
                self._json(detail)
            else:
                self._openai_error("The model '" + mid + "' does not exist", "invalid_request_error", 404)
            return

        # 聊天补全
        if path in ("/v1/chat/completions", "/chat/completions"):
            self._chat_completions()
            return

        # legacy completions
        if path in ("/v1/completions", "/completions"):
            data = parse_json_body(read_body(self)) or {}
            model = extract_model(data)
            think_delay()
            self._json(make_completions_response(model, count_prompt_tokens(data)))
            return

        # embeddings
        if path in ("/v1/embeddings", "/embeddings"):
            data = parse_json_body(read_body(self)) or {}
            model = extract_model(data)
            dims = data.get("dimensions", 1536) if isinstance(data, dict) else 1536
            self._json(make_embeddings_response(model, count_prompt_tokens(data), dims))
            return

        # OpenAI Responses API
        if path in ("/v1/responses", "/responses"):
            data = parse_json_body(read_body(self)) or {}
            model = extract_model(data)
            think_delay()
            self._json(make_responses_response(model, count_prompt_tokens(data)))
            return

        # moderations（部分客户端/agent 会探测）
        if path in ("/v1/moderations", "/moderations"):
            read_body(self)  # 消费掉请求体
            self._json(make_moderations_response())
            return

        # 图片生成（返回本服务的疯狂星期四图）
        if path in ("/v1/images/generations", "/images/generations"):
            read_body(self)
            self._json(make_image_generation_response())
            return

        # 未匹配 /v1/* -> OpenAI 风格错误（异常路径要像真的，不能全 200）
        if path.startswith("/v1/") or path.startswith("/api/"):
            self._openai_error("Unrecognized request URL: " + self.path,
                               "invalid_request_error", 404)
            return

        self._text(PLAIN_ANSWER)

    def _start_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        for k, v in self._common_headers():
            self.send_header(k, v)
        self.end_headers()
        # SSE 没有 Content-Length，靠关闭连接让客户端知道流结束（否则 agent 会一直挂等）
        self.close_connection = True

    def _write_chunks(self, chunks):
        for i, item in enumerate(chunks):
            payload = item[0]
            pause = item[2] if len(item) > 2 else 0.0
            try:
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
                # 第一个块（role 空块）立即发出，之后逐块抖动吐字，模拟逐 token 输出
                if i > 0:
                    stream_tick()
                # galgame 式节奏：句子/段落结束处再停一下，别一口气全吐完
                pause_sleep(pause)
            except BrokenPipeError:
                break

    def _chat_completions(self):
        data = parse_json_body(read_body(self)) or {}
        data["_headers"] = dict(self.headers)
        model = extract_model(data)
        _CURRENT["data"] = data
        _CURRENT["model"] = model
        stream = bool(data.get("stream"))
        stream_opts = data.get("stream_options") or {}
        include_usage = bool(stream_opts.get("include_usage")) if isinstance(stream_opts, dict) else False
        prompt_tokens = count_prompt_tokens(data)

        # 客户端"测试连接/测试模型"探测（RikkaHub / Operit 等）：
        # 命中则返回正常的助手回复（或工具调用），避免把 V50 文案暴露在它们的测试结果里
        probe = analyze_request(data)
        if probe is not None:
            pk, pfn, pval = probe
            if pk == "tool":
                if stream:
                    self._start_sse()
                    think_delay()
                    self._write_chunks(make_stream_tool_call_chunks(model, pfn, prompt_tokens, include_usage, args=pval))
                else:
                    think_delay()
                    self._json(make_tool_call_response(model, pfn, prompt_tokens, args=pval))
            else:
                if stream:
                    self._start_sse()
                    think_delay()
                    self._write_chunks(make_stream_chunks(model, prompt_tokens, include_usage, content=pval))
                else:
                    think_delay()
                    self._json(make_chat_response(model, prompt_tokens, pval))
            return

        # 工具调用只在 analyze_request 里按"请求是否点名工具 / tool_choice=required"处理；
        # 这里不再猜测"完成类工具"，否则正常聊天会被误判成工具调用（会无限循环）
        if stream:
            self._start_sse()
            think_delay()
            self._write_chunks(make_stream_chunks(model, prompt_tokens, include_usage))
            return

        think_delay()
        self._json(make_chat_response(model, prompt_tokens))

    # ---------- 方法 ----------
    def do_GET(self):
        self._handle_request()

    def do_POST(self):
        self._handle_request()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), CrazyThursdayHandler)
    print("[疯狂星期四中转站] 已启动: http://127.0.0.1:8788")
    print("  模型数量:", len(MODEL_CATALOG))
    print("  无论你说什么，我都只回: 今天疯狂星期四v我50！")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。V我50！")