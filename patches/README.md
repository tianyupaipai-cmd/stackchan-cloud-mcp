# 上游网关补丁 / Upstream Gateway Patches

针对上游项目 **`kisaragi-mochi/stackchan-mcp`**（PyPI 包名 `stackchan-mcp`，版本 **0.17.0**）的 4 处修改。
Four patches against upstream `stackchan-mcp` **0.17.0**, required to run the full stack on a public cloud gateway.

这些修改都是**已在生产验证有效**的。上游本身没有 bug——只是它默认面向"设备和网关在同一个局域网（LAN）、素材是官方规格、依赖装全"的场景；一旦把网关搬上公网 VPS、用自制表情包、并且在中国大陆拉模型，就会分别踩到下面 4 个坑。

> These are not upstream bugs. Upstream assumes a LAN deployment with stock assets and a complete dependency set. Moving the gateway to a public VPS, using custom sprite sheets, and pulling models from mainland China each break a different assumption.

---

## 安装路径 / Install path

用 `uv tool install` 安装时，包目录形如：

```
/root/.local/share/uv/tools/stackchan-mcp/lib/python3.12/site-packages/stackchan_mcp/
```

下文统称 `$PKG`。Python 小版本（`python3.12`）随环境变化，请按实际情况替换。
All paths below are relative to `$PKG`. The Python minor version segment varies by environment.

**一键打补丁 / One-shot apply:**

```bash
PKG=/root/.local/share/uv/tools/stackchan-mcp/lib/python3.12/site-packages/stackchan_mcp \
  bash apply_patches.sh
```

脚本是**幂等的（idempotent）**，重复运行不会重复修改、不会报错。

---

## 补丁总览 / Summary

| # | 文件 / 位置 | 类型 | 一句话 |
|---|---|---|---|
| 1 | `esp32_client.py` | 源码 (source) | WebSocket 心跳放密，穿透 NAT 空闲回收 |
| 2 | `gateway.py` | 源码 (source) | 表情包（avatar set）尺寸校验从"精确等于"放开为"白名单" |
| 3 | 依赖 (dependency) | 安装 | 补装 `opuslib`，否则 TTS 音频编不出来 |
| 4 | 环境变量 (env) | 部署 | HuggingFace 镜像 + 禁用 Xet 后端，否则 STT 模型下不下来 |

补丁 1、2 改的是**已安装的包目录**——`uv tool upgrade` / 重装之后会被覆盖，需要重新执行 `apply_patches.sh`。
Patches 1 and 2 modify the installed package tree; re-run the script after any upgrade or reinstall.

---

## 1. `esp32_client.py` — WebSocket 密心跳 / Denser WebSocket keepalive

### 症状 / Symptom

设备（ESP32）跟公网网关的 WebSocket 连接**稳定在大约 90 秒断开**，然后进入重连循环。不是随机掉线，是像上了闹钟一样的规律掉线。

网关日志特征：

```
close_class=ConnectionClosedError  rcvd_code=None  sent_code=None  lifetime_s≈90
```

关键在 `rcvd_code=None sent_code=None`：**两端都没有发过 WebSocket 关闭帧（close frame）**。正常的关闭——无论是设备主动断、网关主动断、还是心跳超时——都会留下一个 close code。两边都是 `None`，说明连接是在 TCP 层被**第三方原始掐断（raw teardown）**的，网关和设备都只是"发现"了它已经没了。

### 根因 / Root cause

中间的 NAT 设备回收了空闲映射（idle NAT mapping reclamation）。

设备经家用路由器 + 运营商 NAT（CGNAT）连到公网网关，链路上每一跳 NAT 都为这条 TCP 连接维护一条映射表项，并给它一个**空闲超时（idle timeout）**。上游默认心跳是 `ping_interval=30s`，但只要这 30 秒里恰好没有业务流量、并且某一跳 NAT 的空闲阈值比实际报文间隔更紧，映射就被回收——之后双向报文全部被丢弃，两端谁也收不到 close frame。实测这条链路的稳定断点在 **~90 秒**。

这是纯 LAN 部署碰不到的问题：局域网里没有 NAT 会去回收连接。

### 修改 / Change

文件 `$PKG/esp32_client.py`，模块顶部常量（约 42–43 行）：

**Before**

```python
# Timeout for waiting for ESP32 responses
RESPONSE_TIMEOUT = 10.0
WEBSOCKET_PING_INTERVAL_S = 30
WEBSOCKET_PING_TIMEOUT_S = 90
```

**After**

```python
# Timeout for waiting for ESP32 responses
RESPONSE_TIMEOUT = 10.0
WEBSOCKET_PING_INTERVAL_S = 20   # patched: keep NAT mapping alive (was 30)
WEBSOCKET_PING_TIMEOUT_S = 60    # patched: fail faster on dead links (was 90)
```

这两个常量在同一文件里被 `websockets` 的连接调用消费：

```python
ping_interval=WEBSOCKET_PING_INTERVAL_S,
ping_timeout=WEBSOCKET_PING_TIMEOUT_S,
```

**为什么是 20 秒**：`ping_interval` 是**服务器主动发 ping 的间隔**，它保证链路上永远不会出现超过 20 秒的静默。20s 明显小于常见 NAT 空闲阈值（通常 30–120s），留了足够余量。同步把 `ping_timeout` 从 90 收到 60，是因为既然心跳变密了，就没必要等一分半才判定对端已死——早点判死、早点重连。

> `ping_interval` is the server-initiated keepalive period; it guarantees the link is never silent for longer than 20s, comfortably under typical NAT idle thresholds. `ping_timeout` is tightened in step so a genuinely dead link is detected sooner.

### 验证 / Verification

```bash
# 1. 确认常量已生效
grep -n 'WEBSOCKET_PING_' "$PKG/esp32_client.py"

# 2. 重启网关服务后，让设备保持连接 10 分钟以上不做任何操作
#    然后在日志里数 ConnectionClosedError 的条数
journalctl -u <你的网关服务名> --since "10 min ago" | grep -c ConnectionClosedError
```

**预期**：静置 10 分钟以上，`ConnectionClosedError` 计数为 0；连接的 `lifetime_s` 不再聚集在 90 附近。打补丁前是每 ~90 秒一条，打完之后掉线消失。

---

## 2. `gateway.py` — 表情包尺寸校验放开 / Relax avatar-set size validation

### 症状 / Symptom

推送自制表情包（custom avatar set）时被网关拒绝，返回：

```
size_mismatch: got=1536000 expected=537600 (mode=layered)
```

连带后果：设备**退回固件内置的默认 emoji 脸**，并且之后所有 `set_avatar` 调用**全部无效**——因为设备上根本没有加载成功的表情集可切。这个连带效应容易误导排查方向，让人去查 `set_avatar` 本身。

### 根因 / Root cause

上游对上传的 raw RGB565 素材包做了**精确尺寸匹配（exact-size match）**：每个模式只认一个字节数。

单帧大小 `kimg_bytes = 160 * 120 * 2 = 38,400` 字节（160×120 像素，RGB565 每像素 2 字节），对应固件里的 `AvatarSet::kImageBytes`。上游写死：

- `layered` 模式 = 14 帧 = `537,600` 字节
- `matrix` 模式 = 90 帧 = `3,456,000` 字节

而自制的 40 帧分层表情包结构是：

```
16 帧脸 A (face A)  +  3 帧眼 (eyes)  +  5 帧嘴 (mouth)  +  16 帧脸 B (face B)  =  40 帧
40 × 38,400 = 1,536,000 字节
```

40 帧同样是合法的 `layered` 布局，只是帧数比上游模板多——但精确匹配一刀切掉了。

### 修改 / Change

文件 `$PKG/gateway.py`，约 259–270 行。把"单一期望值"换成"**合法尺寸白名单（valid-size set）**"。

**Before**

```python
        kimg_bytes = 160 * 120 * 2  # 38_400 — matches AvatarSet::kImageBytes
        expected = {
            "layered": 14 * kimg_bytes,   # 537_600
            "matrix":  90 * kimg_bytes,   # 3_456_000
        }.get(mode)
        if expected is None:
            return {"ok": False, "error": f"unknown_mode: {mode}"}
        if len(payload) != expected:
            return {
                "ok": False,
                "error": f"size_mismatch: got={len(payload)} expected={expected} (mode={mode})",
            }
```

**After**

```python
        kimg_bytes = 160 * 120 * 2  # 38_400 — matches AvatarSet::kImageBytes
        # patched: accept a set of valid sizes instead of one exact size,
        # so custom 40-frame layered sets (16 faceA + 3 eyes + 5 mouth + 16 faceB)
        # are not rejected.
        valid = {
            "layered": {14 * kimg_bytes, 40 * kimg_bytes},  # 537_600 / 1_536_000
            "matrix":  {90 * kimg_bytes},                   # 3_456_000
        }.get(mode)
        if valid is None:
            return {"ok": False, "error": f"unknown_mode: {mode}"}
        if len(payload) not in valid:
            return {
                "ok": False,
                "error": (
                    f"size_mismatch: got={len(payload)} "
                    f"expected one of {sorted(valid)} (mode={mode})"
                ),
            }
```

要点 / Key points：

- `expected`（标量 int）→ `valid`（`set[int]`）；`!=` → `not in`。
- 保留 `unknown_mode` 分支语义不变（`.get(mode)` 返回 `None` 时仍然报未知模式）。
- 报错信息改成列出所有合法值，排查时一眼能看出自己差在哪。
- 下游 `stage_avatar_set(...)` 和 `expected_size=len(payload)` **不需要改**——它们本来就用实际长度，不依赖这个常量。

**需要更多帧数？** 往对应 mode 的集合里加一项即可，例如 `{14 * kimg_bytes, 24 * kimg_bytes, 40 * kimg_bytes}`。注意：帧数必须和设备固件实际期望的布局对得上，网关放开的只是**尺寸门禁**，不会替你转换布局。

### 验证 / Verification

```bash
# 1. 确认补丁已在位
grep -n 'valid = {' "$PKG/gateway.py"
python3 -c "import ast,sys; ast.parse(open('$PKG/gateway.py').read())"  # 语法自检

# 2. 确认素材本身尺寸正确（40 帧应为 1536000）
stat -c %s /path/to/your/avatar_set.rgb565     # Linux
# stat -f %z /path/to/your/avatar_set.rgb565   # macOS
```

**预期**：重启网关后推送 40 帧素材返回 `ok: true`，日志里出现 `bytes_transferred=1536000`；设备显示自制表情而不是固件默认 emoji 脸；随后 `set_avatar` 切换各个表情**逐个生效**（这是最终判据——尺寸过了但 `set_avatar` 还是无效，说明是布局问题不是本补丁的问题）。

---

## 3. 缺失依赖 `opuslib` / Missing `opuslib` binding

### 症状 / Symptom

**TTS 明明成功了，但设备一声不吭。**

- `say()` 调用返回成功，没有任何报错。
- 网关日志里有 `synthesised N bytes PCM`——说明语音合成这步是好的，PCM 数据已经生成。
- 但日志里**没有** `send_pcm_audio`——音频从来没被推给设备。

一头一尾都正常、中间那步无声无息地消失，是这个问题最迷惑的地方：它不抛异常，只是静默地什么也不做。

### 根因 / Root cause

**PCM → Opus 编码这一步缺 Python 绑定。**

链路是：`TTS 合成 PCM` → `Opus 编码` → `WebSocket 推给设备`。设备只吃 Opus。

VPS 上底层的 C 库 `libopus.so.0` **是存在的**（很多系统包会顺带装上），所以 `ldconfig -p | grep opus` 一查有货，很容易误判成"依赖已满足"。但真正缺的是 **Python 绑定 `opuslib`**——上游把它列为可选依赖（optional / extra），基础安装不带。缺了它，编码这步被跳过，管道在中间断掉。

> The native `libopus.so.0` is usually present, which makes this look satisfied. The missing piece is the Python binding `opuslib`, an optional dependency upstream. Without it the PCM→Opus stage is skipped silently.

### 修改 / Change

用 `--with` 把 `opuslib` 注入到 uv tool 的隔离环境里（**注意：不能用普通 `pip install`，uv tool 的环境是独立的**）：

```bash
uv tool install "stackchan-mcp[stt-faster-whisper]" --with opuslib
```

如果工具已经装过，同一条命令会重建环境；也可以用 `uv tool install --force ...` 强制。装完**必须重启网关服务**，进程要重新导入。

补充：底层 C 库若真的缺失，先补系统包（Debian/Ubuntu：`apt-get install -y libopus0`），再装 Python 绑定。

### 验证 / Verification

```bash
# 1. Python 绑定能导入（在 uv tool 的环境里查，不是系统 python）
"$(dirname "$PKG")/../../bin/python" -c "import opuslib; print('opuslib OK', opuslib.__file__)"
# 或者更省事：
uv tool run --from stackchan-mcp python -c "import opuslib; print('opuslib OK')"

# 2. 底层 C 库在位
ldconfig -p | grep -i opus
```

**预期**：`import opuslib` 不报 `ModuleNotFoundError`。然后重启服务、调一次 `say()`，日志里必须**同时**出现 `synthesised ... bytes PCM` **和** `send_pcm_audio`——后者是真正的判据。设备出声。

---

## 4. STT 模型下载（中国大陆网络）/ STT model download from mainland China

### 症状 / Symptom

`listen` 调用**卡住不返回**，日志停在这一行再无下文：

```
Loading faster-whisper model=base
```

不是报错，是**挂起（hang）**——它在等一个永远不会完成的网络下载。

### 根因 / Root cause

两层问题，只解决第一层不够。

**第一层：`huggingface.co` 在中国大陆不通。**

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://huggingface.co     # → 000（连不上）
curl -s -o /dev/null -w '%{http_code}\n' https://hf-mirror.com      # → 200（通）
```

`000` 表示连接根本没建立起来。首次 `listen` 触发模型下载，下载卡死，调用就一直挂着。

**第二层：光设镜像不够——Xet 后端绕过了镜像。**

新版 `huggingface_hub` 默认走 **Xet 存储后端**，实际文件内容从 `cas-server.xethub.hf.co` 拉取。镜像站**不代理这个后端**，于是请求打到原始域名，返回：

```
401 Unauthorized
```

这一步很容易误判成"镜像地址配错了"或"要登录/要 token"——其实都不是，是**下载路径压根没走镜像**。必须显式把 Xet 关掉，让 `huggingface_hub` 退回传统的 HTTP 文件下载路径，那条路径才认 `HF_ENDPOINT`。

> Setting the mirror alone is insufficient: modern `huggingface_hub` resolves file content through the Xet backend, which the mirror does not proxy — yielding a misleading 401. Xet must be explicitly disabled so the classic HTTP download path (which honours `HF_ENDPOINT`) is used.

### 修改 / Change

**两个环境变量必须同时设置，缺一不可。** 写进 systemd unit 的 `[Service]` 段（不要只在交互 shell 里 `export`——服务不继承）：

```ini
[Service]
Environment=HF_ENDPOINT=https://hf-mirror.com
Environment=HF_HUB_DISABLE_XET=1
```

然后：

```bash
systemctl daemon-reload
systemctl restart <你的网关服务名>
```

模型缓存目录：`/root/.cache/huggingface`（`base` 模型约 **142 MB**）。缓存一旦落盘，之后启动不再联网。

**建议：先后台预热（pre-warm）。**

```bash
HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \
  python3 -c "from faster_whisper import WhisperModel; WhisperModel('base'); print('warmed')"
```

### 关于 504：那不是失败 / On 504: not a failure

首次 `listen` 会触发模型加载，耗时可能**超过反向代理默认的 60 秒超时**，于是客户端收到：

```
504 Gateway Timeout
```

**这是反向代理的超时，不是转写失败。** 网关侧的任务照常跑完，**转写结果在网关日志里能找到**。别看到 504 就以为 STT 坏了去回滚配置——先去翻日志。

规避办法：按上面的方式**先预热模型**，让首次真实 `listen` 不再背负下载和加载的开销；或者把反向代理对该路由的读超时调大。

### 验证 / Verification

```bash
# 1. 镜像可达、原站不可达（确认问题成立）
curl -s -o /dev/null -w 'mirror=%{http_code}\n' https://hf-mirror.com

# 2. 环境变量确实进到了服务进程里（关键：查进程，不是查 shell）
systemctl show <你的网关服务名> -p Environment
tr '\0' '\n' < /proc/$(pgrep -f stackchan-mcp | head -1)/environ | grep -E 'HF_ENDPOINT|HF_HUB_DISABLE_XET'

# 3. 模型缓存已落盘
du -sh /root/.cache/huggingface
find /root/.cache/huggingface -name '*.bin' -o -name '*.safetensors' | head
```

**预期**：缓存目录出现 `models--Systran--faster-whisper-base` 之类的条目、总量约 142 MB；调 `listen` 在合理时间内返回转写文本，日志中 `Loading faster-whisper model=base` **后面有后续行**（模型加载完成），而不是停在那里。

---

## 排查速查表 / Troubleshooting quick reference

| 你看到的现象 | 大概率是 | 去看 |
|---|---|---|
| 连接每 ~90 秒断一次，`sent_code=None rcvd_code=None` | NAT 回收空闲连接 | 补丁 1 |
| `size_mismatch: got=1536000 expected=537600` | 表情包精确尺寸校验 | 补丁 2 |
| 设备退回默认 emoji 脸，`set_avatar` 全部无效 | 表情包压根没加载成功 | 补丁 2 |
| 有 `synthesised N bytes PCM`，没有 `send_pcm_audio`，设备不出声 | `opuslib` 缺失 | 补丁 3 |
| `listen` 卡在 `Loading faster-whisper model=base` | 模型下不下来 | 补丁 4 |
| 拉模型报 `401 Unauthorized` | Xet 后端绕过了镜像 | 补丁 4（`HF_HUB_DISABLE_XET=1`）|
| 首次 `listen` 返回 `504` | 反代超时，**不是失败** | 补丁 4（预热；去日志里找结果）|

---

## 维护提示 / Maintenance notes

- 补丁 1、2 直接改**已安装的包目录**。任何 `uv tool upgrade` / `uv tool install --force` / 重装都会覆盖掉，需要**重新运行 `apply_patches.sh`**。建议把它挂进部署流程的最后一步。
- `apply_patches.sh` 每次修改前会写 `.orig` 备份（已存在则不覆盖，保留最初的上游原件）。回滚：把 `.orig` 拷回去即可。
- 补丁 3、4 属于**环境层**（依赖 + 环境变量），不会被包升级冲掉，但换机器 / 重建容器时要重做。
- 版本升级时请重新核对补丁 2 的上下文：上游若改动了 `kimg_bytes` 或 mode 定义，脚本的匹配会失败并**明确报错退出**（不会静默跳过），此时需人工重新对齐。
