# stackchan-cloud-mcp

把 [StackChan](https://github.com/stack-chan/stack-chan) 桌面机器人接到 **claude.ai**，
让它从"要连着一台电脑"变成**一台自己在线的设备**：手机上跟 Claude 说话，桌上的机器人开口、换表情、转头、亮灯。

Turn a StackChan desk robot into a device claude.ai can talk to directly —
no laptop in the loop. Speak to Claude on your phone; the robot on your desk speaks back.

```
claude.ai / phone
      │  MCP over HTTPS (OAuth 2.1: DCR + PKCE)
      ▼
Cloudflare Tunnel  →  oauth_proxy :8770  →  stackchan-mcp gateway :8767
                                                    │  WebSocket
                                                    ▼
                                          StackChan (M5Stack CoreS3)
```

整套跑在一台便宜 VPS 上。设备直连 VPS，家里那台电脑可以关机。

## 这个仓库解决什么

上游 [kisaragi-mochi/stackchan-mcp](https://github.com/kisaragi-mochi/stackchan-mcp)
已经把"网关 ↔ 设备"这段做得很好。这个仓库补的是**把它搬上公网、接进 claude.ai 之后**遇到的那一层问题：

| 问题 | 症状 | 在这里的解法 |
| --- | --- | --- |
| claude.ai 只认 OAuth 2.1 | 静态 token 连不上自定义连接器 | `cloud/oauth_proxy.py` — 最小可用 OAuth 授权服务器（DCR + PKCE + 单租户门禁） |
| NAT 回收长连接 | 设备稳定 ~90 秒掉线，两端都没发关闭帧 | 密心跳 20s/60s（`patches/`） |
| 音频推不出去 | TTS 合成成功但设备不出声 | 缺 `opuslib`（`patches/`） |
| 自定义表情包被拒 | 设备退回固件默认表情，`set_avatar` 全无效 | 放开网关尺寸校验（`patches/`） |
| STT 模型下不动（中国大陆） | `listen` 卡在 Loading model | HF 镜像 + 禁 Xet（`patches/`） |
| 开机版本检查等超时 | 设备开机后一两分钟才连上网关 | `cloud/fake_ota.py` — 永远回"已是最新版" |
| 断线后一身"裸奔" | 重连后表情丢失、音量亮度被重置 | `cloud/reflex.py` — 反射弧守护进程 |
| 机器人不知道 AI 在干嘛 | 只会被动反应，和 AI 此刻的状态、情绪对不上 | `cloud/xinchao_mood.py` — 跟着 AI 状态 + 心潮情绪换脸亮灯动头（见 [`docs/XINCHAO.md`](docs/XINCHAO.md)） |

踩坑全录在 [`patches/README.md`](patches/README.md)，每条都有症状 / 根因 / 修改 / 验证。
这部分大概是这个仓库最值钱的东西——照着走能省掉一整天。

## 组成

| 目录 | 内容 |
| --- | --- |
| `cloud/oauth_proxy.py` | OAuth 2.1 门：让 claude.ai 自定义连接器能连上你的网关。单租户，一个门禁密钥。 |
| `cloud/fake_ota.py` | 假 OTA 端点：让设备开机的版本检查秒过，不然它会等到超时才连网关。 |
| `cloud/reflex.py` | 反射弧守护进程：重连自动补表情/恢复设备偏好、说完自动收表情、摸头反馈、待机小动作与省电。 |
| `cloud/xinchao_mood.py` | 心潮适配：AI 在想 / 刚回完 / 打电话 / 歇着、心潮情绪词、想念冲满 → 换脸、亮灯、小动作、念你写的台词；安静时段、不在家休眠、早上叫起床都可配。不费模型额度。 |
| `cloud/agent_state_hook.sh` | 给 AI 框架的钩子（Claude Code 的 UserPromptSubmit / Stop），把「在想 / 回完」写成状态文件，只认主人的消息。 |
| `cloud/restore.sh` | 上游重装（`uv tool install --force`）会冲掉包目录里的补丁，这个脚本一键打回来。 |
| `patches/` | 对上游网关的 4 处修改：说明 + 幂等打补丁脚本。 |
| `sprites/` | 像素表情包生成器（纯 JS 无依赖）：16 表情 × A/B 帧，RGB565，直接生成设备能吃的 `.bin`。 |
| `docs/` | 部署指南。 |

## 快速开始

前置：一台能被公网访问的 VPS、一个域名（走 Cloudflare Tunnel 则不需要公网 80/443）、
一台刷了 [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) 系固件的 StackChan。

```bash
# 1. 装上游网关（含 STT 与 opus 编码）
uv tool install "stackchan-mcp[stt-faster-whisper]" --with opuslib

# 2. 打补丁（NAT 心跳 / 表情包尺寸校验）
sh patches/apply_patches.sh

# 3. 起 OAuth 门（环境变量见 docs/DEPLOY.md）
MCP_GATE_KEY=... MCP_ISSUER=https://your.domain python3 cloud/oauth_proxy.py

# 4. 起反射弧
STACKCHAN_TOKEN=... AVATAR_SET_PATH=/root/avatar_set.bin python3 cloud/reflex.py
```

完整步骤（systemd unit、Cloudflare Tunnel、claude.ai 连接器怎么加）见 [`docs/DEPLOY.md`](docs/DEPLOY.md)。

## 安全须知

- **所有密钥走环境变量**，仓库里没有任何真实 token、域名、IP。部署时自己设。
- OAuth 门是**单租户**设计：`MCP_GATE_KEY` 是唯一门禁，只有知道它的人能拿到 access_token。
  这不是给多用户 SaaS 用的，请勿直接用于公开服务。
- 网关的 `STACKCHAN_TOKEN` 会被强制校验；OAuth 门转发时会改写 `Authorization`，
  所以 claude.ai 拿到的 token 和网关内部 token 是分开的。
- 机器人带麦克风和摄像头。上麦克风/摄像头能力前，想清楚你的隐私边界；
  推荐做法：拍照时给出可见指示（屏幕提示或灯环变色）、不落盘、一次调用只拍一张。

## 状态

在真实设备上跑通并长期使用：说话（Edge TTS）、听写（faster-whisper）、字幕、
16 表情 + 场景图、头部动作、灯环、断线自愈、claude.ai 手机端驱动。

已知边界：
- Edge TTS 是白嫖微软免费接口，国内 VPS 会被间歇限流，生产环境建议换付费或自建 TTS。
- "唤醒词 → 本地大脑自动接话"需要网关侧能访问一个 LLM；如果你的 VPS 到 API 不通，
  这条链走不通（本仓库的用法是把大脑放在 claude.ai 端，VPS 只做中继）。

## 致谢

- [kisaragi-mochi/stackchan-mcp](https://github.com/kisaragi-mochi/stackchan-mcp) — 网关与设备协议
- [stack-chan](https://github.com/stack-chan/stack-chan) — 机器人本体
- [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) — 固件基座

## License

MIT
