#!/bin/sh
# 停止中转站
pkill -f openai_crazy_thursday.py >/dev/null 2>&1
sleep 1
echo "8788 -> $(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8788/health) (000 = 已停)"