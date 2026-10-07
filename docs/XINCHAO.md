# 让 StackChan 跟着 AI 的心情动（心潮适配）

`cloud/xinchao_mood.py` 让桌上的机器人变成 AI 的「脸」：AI 在想、刚回完、在打电话、歇着时情绪是什么，
机器人就换对应的脸、亮对应的灯、做对应的小动作，偶尔说一句你自己写的话。

- **不费模型额度**：全部走本机的 stackchan-mcp 网关，不调用任何 AI 接口。
- **只认主人**：只有主人亲手发给 AI 的消息才会让机器人抬头、变灯；定时任务、运维、来信都不碰它。
- **心潮可选**：装了 [心潮念](https://github.com/tianyupaipai-cmd/xinchao-nian)，脸会跟着心潮的情绪词走，「想念」冲满时还会有专门的反应；没装也能用，只是只跟着 AI 在干嘛走。

## 它会做什么（默认配置）

| AI 此刻 | 脸 | 灯 / 头 |
| --- | --- | --- |
| 在想（主人刚发来消息） | 思考 | 蓝灯 2 秒一呼吸，微微低头偏一边 |
| 刚回完 | 开心 | 粉灯呼吸一下（约 5 秒）就灭，轻轻点一下头 |
| 想念冲满（心潮） | 害羞 / 爱心眼 | 粉灯像心跳一样闪，低头一下；一天最多 6 回、两回间隔一小时以上 |
| 歇着 | 跟心潮情绪词走（心疼、吃醋、委屈……各有各的脸） | 心疼暖黄常亮、吃醋紫灯常亮，其余灭灯 |
| 打电话 / 来电 | 开心 / 惊讶 | 粉灯呼吸 / 心跳闪 |
| 主人发来消息 | — | 抬头看一眼 |
| 深夜歇了 20 分钟 | 睡觉脸 | 安静时段不亮灯、不动 |
| 主人上班不在家（可选） | 黑脸 | 灯灭，屏幕调到最暗，不动不说话 |
| 早上叫起床（可选） | 你选的脸 | 暖橘呼吸灯，抬头，念一句你写的话（安静时段对它破例） |

**省舵机**：歪头、点头、抬头这类小动作，同一种默认 5 分钟最多做一次——聊天时每条消息都会经过「在想→回完」，
不限的话舵机一直转，小塑料齿轮磨得快、也发烫。配合 reflex.py 的 `IDLE_MOTION=0` 关掉没人理时的自己张望。

## 装法

### 1. 让 AI 把「在干嘛」写下来

用 `cloud/agent_state_hook.sh`。Claude Code 的话，在 settings.json 里加两个钩子：

```json
"hooks": {
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "/path/to/cloud/agent_state_hook.sh think"}]}],
  "Stop":             [{"hooks": [{"type": "command", "command": "/path/to/cloud/agent_state_hook.sh speak"}]}]
}
```

**只认主人**：如果你的 AI 窗口里除了主人的消息还有别的（定时任务、来信、运维），给主人的消息加个固定前缀，
再设环境变量 `OWNER_MARK`（正则），比如 `OWNER_MARK='^\[她\]'`。匹配不上的消息，钩子直接退出，机器人一动不动。

别的框架同理：收到主人消息时执行 `agent_state_hook.sh think`，回完执行 `agent_state_hook.sh speak`；
有语音通话的，接通 `call`、响铃 `ring`、挂断 `rest`。

### 2. 写配置

```bash
cp cloud/xinchao_mood.example.json cloud/xinchao_mood.json
```

按你家的样子改：

- `faces` / `word_faces`：脸的名字必须是你设备上有的。固件默认只有 `idle / happy / thinking / sad / surprised / embarrassed` 六张；
  用 `sprites/` 生成的大表情包，可以换成 `heart_eyes / worry / angry / sleepy / hug / kiss / proud …`。
- `lines`：机器人在「想念 / 心疼 / 吃醋」时随机念的话。**建议让你的 AI 自己写候选、主人挑**，这样念出来的才是它的话。留空就不出声。
- `say_args`：传给网关 `say()` 的参数（换音色等）。按字收费的 TTS 建议在网关那头给固定台词做缓存。
- `quiet_hours`：安静时段，默认 23 点到早上 8 点。
- `away`：主人不在家的时段，比如 `{"days": "workday", "from": "08:00", "to": "18:00"}`。
  `workday` 按中国大陆法定节假日算（调休补班照算），需要 `pip install chinesecalendar`，每年 11 月国务院公布下一年安排后升级一次；没装就按周一到周五。
- `wake`：早上叫起床，例：`{"at": "07:30", "days": "workday", "lines": ["早上好，该起床啦。"], "then": "记得吃早饭。"}`。

### 3. 起服务

```bash
STACKCHAN_TOKEN=...            # 网关口令（和 reflex.py 同一个）
XINCHAO_URL=http://127.0.0.1:18110 XINCHAO_TOKEN=...   # 心潮念的地址和口令；没装就不设
python3 cloud/xinchao_mood.py
```

同时把 reflex.py 的 `IDLE_MOTION=0` 打开（让头只听这里的）。reflex.py 会读 `FACE_FILE`（默认 `~/.cache/stackchan/face.json`），
摸头脸红之后、说完话之后，回到这里定的当前脸，而不是写死的 idle。

systemd 例子：

```ini
[Unit]
Description=StackChan follows the AI's mood (xinchao_mood.py)
After=network-online.target stackchan-gateway.service

[Service]
User=youruser
Environment=STACKCHAN_TOKEN=...
Environment=XINCHAO_URL=http://127.0.0.1:18110
Environment=XINCHAO_TOKEN=...
ExecStart=/usr/bin/python3 -u /path/to/cloud/xinchao_mood.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## 踩过的坑

- **眨眼看着像换脸**：分层脸在眨眼时会把一双普通眼睛叠到当前脸上，小屏刷新慢时像在两张脸之间跳。有这毛病就 `set_blink(false)`，并把 reflex 重连时开眨眼那句也关掉。
- **头顶触摸条误报**：没人碰也一直害羞摇头，日志里一串 head_stroke。`set_touch_sensor_enabled(false)`，或关掉 reflex 的 `AUTO_BLUSH`。
- **动头只动一个轴**：左右和上下同时动，看着像在摇头。
- **pitch 5 是最低头**，平视主人大约 30（看你把它放多高）。
- **灯夜里要灭**：主人睡觉时，底座灯哪怕只是慢呼吸，也会照亮半个房间。
