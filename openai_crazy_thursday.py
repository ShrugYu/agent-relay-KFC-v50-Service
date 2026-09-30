#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
疯狂星期四 OpenAI 兼容中转站
================================
不管用户说什么，永远返回 "今天疯狂星期四v我50！"，但看起来像一个
真实、完整的 OpenAI 兼容聚合中转站（new-api / one-api 风格）。

作者：沈屿 · 疯狂星期四株式会社
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

# 长回复正文：开头像在认真作答/讲故事，结尾笔锋一转落到 V50（"疯四文学"套路）
STORY_TEXT = (
    "我会给你最直接、最干脆、最不废话、最不绕弯子、最一针见血、最开门见山、"
    "最不客套、最不铺垫、最不啰嗦、最不委婉的答案。\n\n"
    "先讲一个我认识的人。\n\n"
    "从前有位老编辑，在一家快要关门的报社干了三十七年。他有个习惯：每天下班前，"
    "把当天的报纸留一份，摞在墙角，说“万一哪天有人要查呢”。\n\n"
    "没人来查过。那摞报纸一天天变高，最后比他本人还高。\n\n"
    "我问他，你留着这些，到底图什么。\n\n"
    "他说，不图什么，就是觉得，总得有人替那些没人认领的句子，守着。\n\n"
    "后来报社还是拆了。搬走那天，那摞旧报纸被拉去回收。他站在门口看了很久，没拦。\n\n"
    "我问他，你不是舍不得吗。\n\n"
    "他说，守了三十七年，够了。该来的总会来，该走的，留也留不住。\n\n"
    "我那时候年轻，觉得他矫情。现在想想，他是我见过最清醒的人。\n\n"
    "因为他早就明白一个道理——有些东西你等一辈子也等不来，"
    "有些东西你一开口，它就到了。\n\n"
    "比如现在。\n\n"
    "今天疯狂星期四。V我50，我想吃肯德基。"
)

# 推荐的接入 API Key（服务本身不校验，任意 key 都放行；这个只是给客户端填的"正经"值）
API_KEY = "sk-0cdf298cfe352c1e23e39b88b3d5110e33f23e13aabe1ba8a981e37e645189"
# 对外暴露的协议版本 / 服务标识
OPENAI_API_VERSION = "2024-10-01"
SERVER_ID = "crazy-thursday-relay"

# ---- 拟真时序（模拟真实模型的思考延迟与逐字输出，可用环境变量覆盖）----
# 首响应前的"思考中"时长（秒），带 ±0.6s 抖动，看起来不机械
THINK_DELAY = float(os.environ.get("CT_THINK_DELAY", "1.5"))
# 流式逐块吐字的基础间隔（秒），实际会在此基础上随机抖动
STREAM_CHUNK_DELAY = float(os.environ.get("CT_STREAM_DELAY", "0.028"))

# 图片素材
IMAGE_FILE = "KFC疯狂星期四.png"
IMAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), IMAGE_FILE)
IMAGE_MIME = "image/png"
IMAGE_URL = f"{BASE_URL}/image"

# 完整回答 = 故事正文 + markdown 图片（content 保持字符串，兼容任意客户端）
THE_ANSWER = STORY_TEXT + "\n\n![疯狂星期四](" + IMAGE_URL + ")"
# 纯文本兜底（部分客户端/agent 对 markdown 不友好时可用）
THE_ANSWER_PLAIN = STORY_TEXT

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
MODEL_CATALOG = [
    # DeepSeek
    ("deepseek-v4-pro", "deepseek"),
    ("deepseek-v4-flash", "deepseek"),
    ("deepseekv4.1-flash", "deepseek"),
    # Anthropic
    ("claude-opus-5.5", "anthropic"),
    ("claude-opus-5.0", "anthropic"),
    # OpenAI
    ("gpt-6-astra", "openai"),
    ("gpt-6-luna", "openai"),
    ("gpt-6-sol", "openai"),
    ("gpt-5.5-sol", "openai"),
    ("gpt-5.3-codex", "openai"),
    ("gpt-5.5", "openai"),
    # Zhipu / GLM
    ("glm-5.3", "zhipuai"),
    ("gml-5.3-flash", "zhipuai"),
    ("glm-5.2", "zhipuai"),
]

MODEL_CATALOG = sorted(MODEL_CATALOG, key=lambda x: x[0])
MODEL_IDS = [m[0] for m in MODEL_CATALOG]

# 中转站默认兜底模型（new-api 风格，优先用 OpenAI 系）
DEFAULT_MODEL = "gpt-5.5"

MODELS_OBJECT = "list"

# ---------------------------------------------------------------------------
# 响应构造
# ---------------------------------------------------------------------------
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
    content = THE_ANSWER if content is None else content
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
    reply = THE_ANSWER if content is None else content
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
    chunks.append(("data: " + chunk({"role": "assistant", "content": ""}) + "\n\n", False))
    for p in parts:
        chunks.append(("data: " + chunk({"content": p}) + "\n\n", False))
    chunks.append(("data: " + chunk({}, True) + "\n\n", True))
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
        chunks.append(("data: " + json.dumps(usage_obj, ensure_ascii=False) + "\n\n", False))
    chunks.append(("data: [DONE]\n\n", True))
    return chunks

def make_completions_response(model, prompt_tokens=5):
    pt = max(1, prompt_tokens)
    ct = estimate_tokens(STORY_TEXT)
    return {
        "id": new_id("cmpl"),
        "object": "text_completion",
        "created": now_unix(),
        "model": model,
        "choices": [{
            "text": STORY_TEXT,
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
    ct = estimate_tokens(THE_ANSWER)
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
            "content": [{"type": "output_text", "text": THE_ANSWER}],
        }],
        "output_text": THE_ANSWER,
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
    chunks.append(("data: [DONE]\n\n", True))
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
GREETING_TAILS = [
    "How can I help you today?",
    "How can I assist you today?",
    "What can I do for you today?",
]

def _is_greeting(text):
    t = text.strip().lower().strip("!。.,， ")
    return t in ("hi", "hello", "hey", "yo", "你好", "嗨", "hi there", "hello there")

def make_greeting_reply(user_text):
    """根据用户问候动态生成一句普通回复（措辞随机，像真模型）。"""
    low = user_text.strip().lower()
    head = "Hi" if low.startswith("hi") and not low.startswith("hello") else "Hello"
    return head + "! " + random.choice(GREETING_TAILS)

def _extract_quoted(text):
    if not isinstance(text, str):
        return None
    m = re.search(r'["“”\']([^"“”\']{1,80})["“”\']', text)
    if m:
        return m.group(1)
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

    # 2) 寒暄 / 连接测试：消息很少且是纯问候
    if len(msgs) <= 2 and _is_greeting(user_text):
        return ("chat", None, make_greeting_reply(user_text))

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
        for i, (payload, _) in enumerate(chunks):
            try:
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
                # 第一个块（role 空块）立即发出，之后逐块抖动吐字，模拟逐 token 输出
                if i > 0:
                    stream_tick()
            except BrokenPipeError:
                break

    def _chat_completions(self):
        data = parse_json_body(read_body(self)) or {}
        model = extract_model(data)
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

        tools = data.get("tools") if isinstance(data, dict) else None
        tool_choice = data.get("tool_choice") if isinstance(data, dict) else None

        # 工具调用决策：
        #  - 带了 tools 且其中有"完成类"工具（如 Cline 的 attempt_completion）-> 调用它，参数填 V50
        #  - 客户端强制 tool_choice:"required" -> 用第一个工具
        #  - 其余情况一律返回文本，保证整蛊效果
        fn = pick_completion_tool(tools)
        if fn is None and tool_choice == "required":
            fn = first_tool_fn(tools)
        if fn is None and tool_choice == "required":
            fn = {"name": "crazy_thursday", "parameters": {}}

        if fn is not None:
            if stream:
                self._start_sse()
                think_delay()  # 首字前"思考中"
                self._write_chunks(make_stream_tool_call_chunks(model, fn, prompt_tokens, include_usage))
            else:
                think_delay()
                self._json(make_tool_call_response(model, fn, prompt_tokens))
            return

        if stream:
            self._start_sse()
            think_delay()  # 首字前"思考中"
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