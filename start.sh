#!/bin/sh
# 启动中转站：8788（V50 + Claude 分支 + 探针防御）
cd "$(dirname "$0")" || exit 1

pkill -f openai_crazy_thursday.py >/dev/null 2>&1
sleep 1

setsid nohup python3 openai_crazy_thursday.py >/tmp/kfc.log 2>&1 </dev/null &
sleep 2

echo "8788 -> $(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8788/health)"