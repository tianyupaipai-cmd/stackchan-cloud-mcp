# Railway 部署并连接 Kelivo

适用于 M5Stack 官方 StackChan，以及支持本项目控制接口的配套固件。
手机 Kelivo 通过远程 HTTP MCP 调用工具，机器人通过 Wi-Fi 主动连接 Railway：

```text
Kelivo ── HTTPS /mcp ──→ Railway ── WebSocket ──→ StackChan
                            ↑                      │
                            └──── HTTPS 素材下载 ──┘
```

本方案使用 `STACKCHAN_TOKEN` 密钥鉴权，Kelivo 无需改源码。
不启动 Claude 专用的 OAuth 代理，也不需要 Cloudflare Tunnel。

## 1. 从 GitHub 创建 Railway 服务

1. 确认 Railway 选择的 GitHub 分支包含根目录的 `Dockerfile`、`railway.toml` 和本文档。
2. Railway 中选择 **New Project → Deploy from GitHub repo**，选择本仓库和该分支。
3. 在服务的 **Variables** 中设置：

   | 变量 | 内容 |
   | --- | --- |
   | `STACKCHAN_TOKEN` | 自己生成的长随机密钥，后面在 Kelivo 和机器人上填同一串 |
   | `PORT` | `8080` |

   可在自己电脑的终端生成密钥：

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   密钥只填入 Railway 变量、Kelivo 请求头和机器人网关配置，不提交到 GitHub。

4. 在 **Settings → Networking → Generate Domain** 生成公网域名，目标端口选择 `8080`。
   程序自动读取 Railway 的 `RAILWAY_PUBLIC_DOMAIN`。
   如果平台没有注入该变量，或使用自己的域名，再手动添加：

   ```text
   STACKCHAN_PUBLIC_URL=https://你的域名
   ```

   填域名根地址，不加 `/mcp`、`/ws` 或其他路径。

5. 保存变量和域名后重新部署。首次自动部署如果发生在变量配置之前，会拒绝启动；配置好后重部署即可。
6. 打开 `https://你的域名/healthz`，返回 `{"ok":true}` 表示云端服务就绪。
   **这个检查不要求机器人已经连接**。

`railway.toml` 已设置 Docker 构建、健康检查、单副本、关闭休眠和故障重启。
保持单副本运行，确保手机和机器人连接到同一网关进程。
Railway 负责公网 HTTPS/WSS，容器内部只需一个公开的 HTTP 端口。

## 2. Kelivo 添加 MCP

在 Kelivo 的 MCP 设置中添加服务：

| 字段 | 内容 |
| --- | --- |
| 名称 | `StackChan` |
| 传输类型 | `HTTP`（Streamable HTTP） |
| URL | `https://你的域名/mcp` |
| 自定义请求头的键 | `Authorization` |
| 自定义请求头的值 | `Bearer 你的STACKCHAN_TOKEN` |

`Bearer` 后面有一个空格。不需要填写 OAuth Client ID，也不要选择 SSE 或 STDIO 类型。

连接成功后会显示 `get_status`、`say`、`set_avatar`、`move_head` 等工具。
在聊天会话中启用该 MCP，并选择支持工具调用的模型。先让 AI 调用 `get_status`；
机器人尚未连接时，`connected: false` 是正常结果。

## 3. 机器人连接云端

此项目的上游是 [kisaragi-mochi/stackchan-mcp](https://github.com/kisaragi-mochi/stackchan-mcp)，
提供面向官方 StackChan 的配套固件。包装上的型号不能确定正在运行的固件版本；
出厂能连接小智也不能确认已经具备这里的控制接口。
先核对当前固件与上游要求，再按上游说明配置或更新；首次连接不能依赖远程刷机工具完成。

在配套固件的 Wi-Fi 配网页面 **Advanced / 高级** 中设置：

| 配置 | 内容 |
| --- | --- |
| WebSocket Gateway URL | `wss://你的域名/ws` |
| Gateway Token | 与 Railway 的 `STACKCHAN_TOKEN` 相同，不带 `Bearer ` 前缀 |
| OTA 检查地址（若固件提供此设置） | `https://你的域名/ota/` |

机器人保持连接 2.4 GHz Wi-Fi。保存后让它重新连接，再从 Kelivo 调用 `get_status`。
应看到 `connected: true`、`initialized: true`，并列出设备工具。

Railway 默认表情包适配上游已发布的 `firmware-v1.16.0`：14 帧、160×120、RGB565，
共 537,600 字节，包含 `idle`、`happy`、`thinking`、`sad`、`surprised`、`embarrassed`
六种表情及眼睛、嘴型帧。机器人上线后，反射弧会延迟 20 秒自动加载。
需要手动重试时，在 Kelivo 调用 `load_avatar_set`：

```json
{"archive_path":"/app/sprites/avatar_set_v1_16.bin","mode":"layered","timeout":120}
```

确认返回 `ok: true` 后再调用 `set_avatar`。`/app/sprites/avatar_set.bin` 是保留给扩展固件的
40 帧表情包；发布版 `firmware-v1.16.0` 无法直接加载它。

可以用这句话做首次实机检查：

> 先检查 StackChan 是否在线。在线的话，让它说“你好，我是你的桌面小伙伴”，再换成 happy 表情。

默认 TTS 使用 Edge TTS，中文音色为 `zh-CN-XiaoxiaoNeural`。可以通过
`STACKCHAN_EDGE_TTS_DEFAULT_VOICE` 更换音色；Edge TTS 外部服务可用性需要实机部署时验证。

要让聊天回复从机器人播放，可在 Kelivo 助手提示词中加入：

> 正常回复时，通过 StackChan 的 say 工具播报要对我说的正文；按情绪选用 set_avatar。
> 工具连接失败时告诉我实际状态。

## 可选变量

| 变量 | 默认 / 用途 |
| --- | --- |
| `STACKCHAN_ENABLE_REFLEX` | `1`，启动断线补表情、触摸反馈和说完收表情；设 `0` 可关闭 |
| `AVATAR_SET_PATH` | `/app/sprites/avatar_set_v1_16.bin`，适配发布版 `firmware-v1.16.0`；使用扩展固件时可覆盖 |
| `IDLE_MOTION` | 此部署默认 `0`；设 `1` 开启待机小动作 |
| `STACKCHAN_TTS_ENGINE` | `edge-tts` |
| `STACKCHAN_EDGE_TTS_DEFAULT_VOICE` | `zh-CN-XiaoxiaoNeural` |
| `STACKCHAN_FASTER_WHISPER_MODEL` | 上游默认 `base`；首次使用 `listen` 时才下载 |
| `HF_HOME` | 可指定已配置写权限的持久化模型缓存目录 |

Whisper Python 依赖已经安装，镜像构建时不下载模型。模型下载、转写、外部 TTS 和真实机器人行为
需要在实际部署后验证。先跑通手机发起的 `say`、表情和动作，再按需要启用机器人听写。
Kelivo 里的模型负责对话，Railway 网关负责工具执行；这里没有配置唤醒词后自主对话的 LLM 服务。

## 排查

| 现象 | 检查 |
| --- | --- |
| `dockerfile invalid` 且提到 `--mount=type=secret` | 正在构建旧版 Dockerfile；部署此分支最新提交，避免重试旧提交的快照 |
| 部署立即退出 | 日志是否提示缺少 `STACKCHAN_TOKEN` 或公网域名 |
| `/healthz` 失败 | 端口是否为 `8080`，查看 Railway 网关启动日志 |
| Kelivo 返回 401 | 请求头名称、`Bearer ` 空格、密钥是否与 Railway 一致 |
| 有工具但机器人不在线 | 固件是否匹配，`wss://.../ws`、网关密钥和 Wi-Fi 是否正确 |
| 机器人连接后无法下载表情 | `STACKCHAN_PUBLIC_URL` / 公网域名是否正确，机器人能否访问 HTTPS |
| 表情加载返回 `content_length_mismatch` | 部署保留下载响应 `Content-Length` 的最新转发代码；机器人会在读取文件前核对这个长度 |
| 表情加载返回 `load_failed` | 检查表情包与固件布局；`firmware-v1.16.0` 使用 `avatar_set_v1_16.bin` 的 14 帧包，不能使用 40 帧包 |
| 表情加载返回 `fetch_in_progress` | 机器人已有加载任务；暂停重复调用，查看 `Device avatar result` 和 USB 串口的 `AvatarSetFetcher` 日志 |
| 表情请求超时、后台显示 `invalid response` | 使用修复后台并发请求编号的最新版本；触摸轮询与表情加载必须使用不同的 JSON-RPC 请求编号 |
| 日志显示 avatar not loaded | 根据错误排查连接、素材或固件；不会把失败记录成上传成功 |
| `say` 没有声音 | 查看 Edge TTS 联网及 Opus 日志，确认配套固件使用 WebSocket 协议 v1 |

`Serving avatar set` / HTTP 200 只说明文件下载接口返回了响应，不能确认机器人已经加载。
转发器会记录 `Avatar download forwarded` 的长度和字节数，以及 `Device avatar result` 的
`ok` / `error`；即使原工具调用已超时，机器人迟到的结果仍会记录。以设备返回 `ok: true`
和实机显示为准。USB 串口日志来自电脑上连接机器人 COM 端口的终端窗口。

## 本地验证与实现

```bash
pip install --require-hashes -r deploy/requirements.txt
PKG="$(python -c 'import stackchan_mcp, pathlib; print(pathlib.Path(stackchan_mcp.__file__).parent)')" \
  bash patches/apply_patches.sh
node sprites/gen_avatar_v3.js
python -m unittest discover -s tests -v
docker build -t stackchan-cloud-mcp:railway .
```

在本机启动时，设置 `STACKCHAN_TOKEN`、`PORT` 和 `STACKCHAN_PUBLIC_URL` 后运行
`python -m cloud.railway_server`。只有本地测试允许 `http://127.0.0.1:<PORT>`；公网必须使用 HTTPS。

Railway 使用标准 Dockerfile 和官方 Python 基础镜像，不使用特殊构建挂载。
构建保留 TLS、系统包签名和 Python 依赖哈希校验。
本地开发若经过自签证书的受信任代理，应在独立的本地测试基础镜像中配置信任根证书，
保持仓库的 Railway Dockerfile 不变，不通过跳过证书验证来解决网络问题。

`cloud/railway_server.py` 管理上游网关和可选反射弧进程，在公开的 `$PORT` 上转发：

| 路径 | 用途 |
| --- | --- |
| `/mcp` | Streamable HTTP MCP，保留会话头和 SSE 流 |
| `/ws` | 机器人 WebSocket，保留文字和音频二进制帧 |
| `/capture`、`/avatar_set/...`、`/pcm` | 上游图像、表情包和音频接口，各自保留原有鉴权 |
| `/status` | 带密钥的网关状态 |
| `/ota/` | 复用假 OTA 响应 |
| `/healthz` | 云端进程就绪检查 |

反射弧通过 stdin 接收实时网关日志，原有 VPS/systemd 使用方式继续有效。
必须的子进程退出时容器返回失败，由 Railway 重启；停止时关闭连接并回收子进程。
