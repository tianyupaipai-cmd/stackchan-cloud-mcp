# 部署指南 / Deployment

从零把一台 StackChan 接到 claude.ai。整套跑在一台 1C2G 的小 VPS 上就够。

## 你需要什么

- 一台 VPS（本文假设 Ubuntu 22.04、root）
- 一个域名 + Cloudflare 账号（走 Tunnel，不需要 VPS 开放 80/443）
- 一台 StackChan（M5Stack CoreS3），刷 [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) 系固件
- 设备与 VPS 之间：**VPS 需要开放设备连接端口**（默认 `8765`，云厂商安全组也要放行）

## 1. 装网关

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install "stackchan-mcp[stt-faster-whisper]" --with opuslib
```

`--with opuslib` 不能省：缺它会出现"TTS 合成成功、设备不出声"的静默失败（见 `patches/README.md`）。

国内下载慢可以用镜像：

```bash
export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
```

### systemd unit

`/etc/systemd/system/stackchan-gateway.service`：

```ini
[Unit]
Description=StackChan MCP Gateway
After=network-online.target

[Service]
Environment=HOME=/root
Environment=PATH=/root/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=STACKCHAN_TOKEN=<自己生成一串随机字符串>
Environment=STACKCHAN_TTS_ENGINE=edge-tts
Environment=STACKCHAN_EDGE_TTS_DEFAULT_VOICE=<voice-id>
# 中国大陆：STT 模型走镜像，且必须禁用 Xet 后端（见 patches/README.md）
Environment=HF_ENDPOINT=https://hf-mirror.com
Environment=HF_HUB_DISABLE_XET=1
ExecStart=/root/.local/bin/stackchan-mcp serve --transport streamable-http
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now stackchan-gateway
```

网关会监听：设备 WebSocket `0.0.0.0:8765`、MCP HTTP `127.0.0.1:8767`。

## 2. 打补丁

```bash
sh patches/apply_patches.sh
systemctl restart stackchan-gateway
```

**每次 `uv tool install --force` 重装上游后都要重打**，或直接跑 `sh cloud/restore.sh`。
这一步不做的话：设备约 90 秒掉线一次，且自定义表情包会被拒。

## 3. 设备连上来

按你固件的方式把设备的服务端地址指向 `ws://<VPS_IP>:8765/`。
连上后网关日志会出现：

```
ESP32 ready: device=<mac> tools=NN
```

没出现就先查云厂商安全组是否放行 8765。

## 4. OAuth 门（让 claude.ai 能连）

claude.ai 的自定义连接器只认 OAuth 2.1（DCR + Authorization Code + PKCE），
不接受静态 token，所以需要这个门。

`/etc/systemd/system/stackchan-oauth.service`：

```ini
[Unit]
Description=StackChan MCP OAuth Proxy
After=stackchan-gateway.service
Wants=stackchan-gateway.service

[Service]
# 授权页门禁密钥：只有知道它的人能拿到 access_token。自己生成，别用弱口令
Environment=MCP_GATE_KEY=<openssl rand -base64 32>
Environment=MCP_ISSUER=https://<你的域名>
# 网关强制校验 Bearer；门在转发时会把 Authorization 改写成这个值
Environment=STACKCHAN_GATEWAY_TOKEN=<与网关 STACKCHAN_TOKEN 相同>
ExecStart=/usr/bin/python3 -u /root/stackchan/oauth_proxy.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

需要 `aiohttp`。若系统 python 没有，可以直接用网关那套：
`/root/.local/share/uv/tools/stackchan-mcp/bin/python3`。

监听 `127.0.0.1:8770`。

## 5. Cloudflare Tunnel

```bash
cloudflared tunnel login
cloudflared tunnel create stackchan
```

`/root/.cloudflared/config.yml`：

```yaml
tunnel: <tunnel-uuid>
credentials-file: /root/.cloudflared/<tunnel-uuid>.json
ingress:
  - hostname: <你的域名>
    service: http://127.0.0.1:8770
  - service: http_status:404
```

```bash
cloudflared tunnel route dns stackchan <你的域名>
```

做成 systemd 服务跑 `cloudflared tunnel --config /root/.cloudflared/config.yml run`。

> **同一条隧道不要在两台机器上同时跑**：Cloudflare 会当成 HA 副本随机路由，
> 请求会一半落到另一台上。迁移时记得把旧的停掉并禁用自启。

验证：

```bash
curl https://<你的域名>/.well-known/oauth-authorization-server   # 应返回 JSON
curl -o /dev/null -w '%{http_code}\n' https://<你的域名>/mcp       # 应返回 401
```

## 6. 反射弧（强烈建议）

没有它：设备断电重连后表情丢失、音量亮度被重置、表情停在最后一次设置的脸上。

先把表情包放到 VPS（生成方法见 `sprites/README.md`）：

```bash
node sprites/gen_avatar_v3.js && scp avatar_set.bin root@<vps>:/root/avatar_set.bin
```

`/etc/systemd/system/stackchan-reflex.service`：

```ini
[Unit]
Description=StackChan Reflex
After=stackchan-gateway.service
Wants=stackchan-gateway.service

[Service]
Environment=STACKCHAN_TOKEN=<与网关相同>
Environment=AVATAR_SET_PATH=/root/avatar_set.bin
ExecStart=/usr/bin/python3 -u /root/stackchan/reflex.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

它会：设备重连 → 重推表情包 + 通电眨眼 + 强制回 idle + 恢复设备偏好（音量/亮度/自动松扭矩）；
`say` 结束 → 延时收回 idle；摸头 → 表情+灯反馈；长时间无互动 → 待机小动作与降亮度。

## 7. 加进 claude.ai

设置 → 连接器 → 添加自定义连接器 → URL 填 `https://<你的域名>/mcp`。
授权时会弹出一个密钥页，输入 `MCP_GATE_KEY`。

连上后可用工具包括：`say`、`listen`、`set_avatar`、`load_avatar_set`、
`move_head`、`set_all_leds`、`take_photo` 等。

> OAuth 门重启会清掉已发放的 access_token（内存态），需要在 claude.ai 里重新授权一次。
> 想避免的话可以把 `_tokens` 换成持久化存储。

## 常见故障

| 现象 | 排查 |
| --- | --- |
| 设备约 90 秒掉一次线 | 心跳补丁没打，见 `patches/` |
| `say` 返回成功但没声音 | 缺 `opuslib`；日志里没有 `send_pcm_audio` |
| 设备显示固件默认表情，`set_avatar` 无效 | 表情包尺寸校验没放开；日志搜 `size_mismatch` |
| `listen` 卡住不返回 | STT 模型下不动；查 `HF_ENDPOINT` / `HF_HUB_DISABLE_XET` |
| claude.ai 连上但工具调用 401 | 门没设 `STACKCHAN_GATEWAY_TOKEN`，或与网关 token 不一致 |
| 重装上游后一切退化 | 跑 `sh cloud/restore.sh` |

## 发热

舵机维持姿势会持续通电，是主要热源。建议开启自动松扭矩
（`set_auto_torque_release(enabled=true, timeout_ms=3000)`，反射弧会自动设），
并适当降低屏幕亮度、不用时关闭灯环。
