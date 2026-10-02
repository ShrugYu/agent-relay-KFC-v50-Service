# 服务器部署教程（域名 + HTTPS）

把 **agent-relay-KFC-v50-Service**（疯狂星期四 OpenAI 兼容中转站）部署到有域名的 Linux 服务器上，通过 `https://你的域名/v1` 对外提供 OpenAI 兼容接口。

## 整体结构

```
客户端 (Cline / Cursor / SDK ...)
        │  https://你的域名/v1/...
        ▼
     Nginx (443, SSL)   ← 反代、干掉了缓冲，SSE 才能逐字
        │  http://127.0.0.1:8788
        ▼
 openai_crazy_thursday.py   ← 常驻进程，只监听本机
```

服务**不直接暴露端口**，只监听 `127.0.0.1`，由 Nginx 对外。这样更安全，也方便挂 HTTPS。

---

## 0. 准备

- 一台 Linux 服务器（Ubuntu 22.04 推荐）
- 一个域名，已把 A 记录解析到服务器公网 IP
  - 例：`api.example.com` → `123.45.67.89`
  - 解析生效检查：`ping api.example.com` 应返回你的服务器 IP
- Python 3.8+（脚本零第三方依赖）
- root / sudo

检查：

```bash
python3 --version        # 应 ≥ 3.8
ping api.example.com     # 应解析到你的服务器 IP
```

---

## 1. 部署代码

```bash
sudo mkdir -p /opt/agent-relay-KFC-v50-Service
cd /opt
sudo git clone https://github.com/ShrugYu/agent-relay-KFC-v50-Service.git
# 或者直接下载 zip 解压到 /opt/agent-relay-KFC-v50-Service
```

---

## 2. 先用域名跑起来（前台测试）

```bash
cd /opt/agent-relay-KFC-v50-Service
CT_BASE_URL=https://api.example.com python3 openai_crazy_thursday.py
```

- 服务监听 `127.0.0.1:8788`（默认，无需改）
- `CT_BASE_URL` 设成你的域名——它决定回复里图片链接指向哪里，**这一步别漏**

前台确认没报错后，`Ctrl+C` 停掉，进入后台常驻。

---

## 3. systemd 常驻 + 开机自启

```bash
sudo nano /etc/systemd/system/kfc-v50.service
```

内容（域名换成你的）：

```ini
[Unit]
Description=agent-relay-KFC-v50-Service (KFC v50 OpenAI Relay)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/agent-relay-KFC-v50-Service
Environment=CT_HOST=127.0.0.1
Environment=CT_PORT=8788
Environment=CT_BASE_URL=https://api.example.com
Environment=CT_THINK_DELAY=1.5
Environment=CT_STREAM_DELAY=0.028
ExecStart=/usr/bin/python3 openai_crazy_thursday.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kfc-v50
sudo systemctl status kfc-v50       # 应看到 active (running)
```

---

## 4. Nginx 反向代理

```bash
sudo apt update && sudo apt install -y nginx
sudo nano /etc/nginx/conf.d/kfc-v50.conf
```

内容（`server_name` 换成你的域名）：

```nginx
server {
    listen 80;
    server_name api.example.com;

    location / {
        proxy_pass http://127.0.0.1:8788;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # === SSE 流式必须项 ===
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
        chunked_transfer_encoding on;
    }
}
```

检查并生效：

```bash
sudo nginx -t && sudo systemctl reload nginx
```

此时 `http://api.example.com` 已能访问（下一步上 HTTPS）。

---

## 5. 申请 HTTPS 证书（certbot）

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d api.example.com
```

certbot 会自动改好 Nginx 配置、配好 443 与自动续期。完成后：

```bash
curl https://api.example.com/v1/models    # 应返回 14 个模型的 JSON
```

> 证书自动续期由 certbot 的 timer 负责，无需手动；验证：`sudo certbot renew --dry-run`

---

## 6. 验证部署

```bash
# 模型列表
curl https://api.example.com/v1/models

# 聊天（非流式）
curl https://api.example.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.5","messages":[{"role":"user","content":"你好"}]}'

# 流式（-N 关闭 curl 缓冲）
curl -N https://api.example.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.5","messages":[{"role":"user","content":"你好"}],"stream":true}'
```

---

## 7. 环境变量一览

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CT_HOST` | `127.0.0.1` | 监听地址；有 Nginx 反代时保持默认即可 |
| `CT_PORT` | `8788` | 监听端口 |
| `CT_BASE_URL` | `http://{HOST}:{PORT}` | **对外地址，设成 `https://你的域名`** |
| `CT_THINK_DELAY` | `1.5` | 首字前"思考"秒数（±0.6s 抖动） |
| `CT_STREAM_DELAY` | `0.028` | 流式分块间隔（秒） |

改完环境变量后：

```bash
sudo systemctl daemon-reload && sudo systemctl restart kfc-v50
```

---

## 8. 客户端接入

| 配置项 | 值 |
| --- | --- |
| BaseURL | `https://api.example.com/v1` |
| API Key | 任意（服务不校验） |
| Model | `gpt-5.5` / `claude-opus-5.5` / `deepseek-v4-pro` 等 14 个之一 |

Cline / Cursor / Cherry Studio / NextChat / LiteLLM / 任意 OpenAI SDK 都可直接填这个 BaseURL。

---

## 附：Go 版（可选）

项目同时提供 Go 实现，接口与 Python 版完全一致。服务器上没有 Python、或想要单文件二进制时用它：

```bash
sudo apt install -y golang-go          # 装 Go（≥1.20）
cd /opt/agent-relay-KFC-v50-Service
go build -o kfcgo .
sudo ./kfcgo                            # 或 go run .
```

systemd 里把 `ExecStart` 换成二进制即可（环境变量不变）：

```ini
ExecStart=/opt/agent-relay-KFC-v50-Service/kfcgo
```

---

## 9. 常见问题

**域名能开、但 /v1 报 502**
- 服务没起来：`sudo systemctl status kfc-v50`、`sudo journalctl -u kfc-v50 -n 50`

**流式不逐字、一卡一卡**
- Nginx 缓冲没关，确认 `proxy_buffering off;` 后 `sudo systemctl reload nginx`

**回复里的图片打不开**
- `CT_BASE_URL` 没设成域名（还在用 127.0.0.1），改掉并重启

**证书没生效 / 续期失败**
- `sudo certbot renew --dry-run` 看报错；确认 80 端口能被外网访问

**临时没域名，想用 IP 直连测试**
```bash
CT_HOST=0.0.0.0 CT_BASE_URL=http://你的IP:8788 python3 openai_crazy_thursday.py
```
并放行端口：`sudo ufw allow 8788/tcp`（云控制台安全组也要放行）。

**改端口**
```bash
CT_PORT=9000 python3 openai_crazy_thursday.py
```

---

— 疯狂星期四株式会社