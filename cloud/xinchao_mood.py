#!/usr/bin/env python3
"""xinchao_mood.py — 让 StackChan 跟着你的 AI「此刻在干嘛 + 心潮情绪」换脸、亮灯、动头、说话。

Make the robot follow your AI's live state: what it is doing right now (thinking / just replied /
on a call / resting) plus its 心潮 (Xinchao) emotion and "missing you" drive. No model calls —
everything goes through the local stackchan-mcp gateway.

两个状态来源（都在本机，不出网）:
  1. 心潮念快照  GET {XINCHAO_URL}/v1/dashboard/snapshot（Bearer XINCHAO_TOKEN）
     取 emotion.shown/label（此刻情绪词）和 drives 里 key=possess 的那股（想念，0–1）。
     没装心潮念就不设 XINCHAO_URL，只按 AI 状态动。
  2. AI 状态文件  AGENT_STATE_FILE（默认 ~/.cache/stackchan/agent.json），由 agent_state_hook.sh 写：
     {"state": "think" | "speak" | "call" | "ring" | "rest", "at": 秒, "user_at": 秒}
     - think：收到「主人」的消息、AI 开始想（Claude Code 的 UserPromptSubmit 钩子）
     - speak：AI 回完（Stop 钩子），之后几秒算"刚回完"
     - user_at：主人最后一次发消息的时间，用来"她来消息就抬头"
     钩子只认主人的消息（见 agent_state_hook.sh 的 OWNER_MARK），定时任务、运维、来信都不碰机器人。

和 reflex.py 分工：摸头脸红、断线重连补表情还归 reflex；它要"回到平常脸"时读 FACE_FILE，
回到这里定的当前脸（reflex.py 已支持，见 cur_face()）。

所有可调的东西在 JSON 配置里（XINCHAO_MOOD_CONFIG，默认同目录 xinchao_mood.json，模板见 xinchao_mood.example.json）：
脸怎么配、灯什么颜色、台词、安静时段、"上班不在家"时段、早上叫起床……都按你家的样子改。
"""
import json, math, os, random, threading, time, urllib.request
from datetime import datetime, timedelta, timezone

GW = os.environ.get("STACKCHAN_MCP_URL", "http://127.0.0.1:8767/mcp")
GW_TOKEN = os.environ.get("STACKCHAN_TOKEN", "")
XINCHAO_URL = os.environ.get("XINCHAO_URL", "").rstrip("/")
XINCHAO_TOKEN = os.environ.get("XINCHAO_TOKEN", "")
AGENT_STATE_FILE = os.path.expanduser(os.environ.get("AGENT_STATE_FILE", "~/.cache/stackchan/agent.json"))
FACE_FILE = os.path.expanduser(os.environ.get("FACE_FILE", "~/.cache/stackchan/face.json"))
CONFIG = os.environ.get("XINCHAO_MOOD_CONFIG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "xinchao_mood.json"))
TZ = timezone(timedelta(hours=float(os.environ.get("TZ_OFFSET_HOURS", "8"))))

DEFAULT = {
    # 脸：默认只用固件自带的 6 张（idle/happy/thinking/sad/surprised/embarrassed）。
    # 用了 sprites/ 生成的大表情包，就把这里换成 heart_eyes / worry / angry / sleepy ... 这些名字。
    "faces": {"think": "thinking", "speak": "happy", "call": "happy", "ring": "surprised",
              "miss": "embarrassed", "sleep": "idle", "away": "idle", "rest": "idle"},
    "word_faces": {"心动": "embarrassed", "害羞": "embarrassed", "想念": "embarrassed", "骄傲": "happy", "得意": "happy",
                   "雀跃": "happy", "心疼": "sad", "委屈": "sad", "失落": "sad", "低落": "sad", "自责": "sad",
                   "吃醋": "surprised", "生气": "surprised", "不平": "surprised", "烦躁": "surprised", "不安": "sad"},
    "word_leds": {"心疼": [255, 190, 60], "吃醋": [170, 80, 255]},
    "colors": {"think": [60, 120, 255], "speak": [244, 114, 160], "miss": [244, 114, 160], "call": [244, 114, 160],
               "sleep": [120, 50, 10], "wake": [255, 150, 60]},
    "look_pitch": 30,                # 平视主人的抬头角度（5 最低，85 最高）
    "speak_window_s": 8,             # AI 回完之后多少秒算"刚回完"
    "move_every_s": 300,             # 歪头/点头/抬头这类小动作，同一种几秒内最多一次（舵机是小塑料齿轮，少转少磨）
    "quiet_hours": [23, 8],          # 安静时段：不转头、不说话、底座灯全灭（起床那一下除外）
    "sleep_after_rest_min": 20,      # 安静时段里 AI 歇了这么久 → 睡觉脸
    "miss": {"threshold": 0.85, "per_day": 6, "gap_s": 3600, "hold_s": 60},
    "lines": {},                     # {"miss": ["…"], "心疼": ["…"], "吃醋": ["…"]}，空 = 不出声
    "line_gap_s": 3600,
    "say_args": {},                  # 传给 say() 的额外参数（比如 voice / speaker_id）
    "away": None,                    # {"days": "workday", "from": "08:00", "to": "18:00"}：主人不在家，机器人休眠
    "wake": None,                    # {"at": "07:30", "days": "workday", "lines": ["…"], "then": "…", "face": "happy", "hold_s": 1200}
    "brightness": {"normal": 40, "away": 1},
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT))
    try:
        user = json.load(open(CONFIG))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except FileNotFoundError:
        pass
    return cfg


C = load_config()


def is_workday(day):
    """法定节假日表（中国大陆）用 chinesecalendar 包；没装或没有那一年就按周一到周五。"""
    try:
        import chinese_calendar
        return chinese_calendar.is_workday(day)
    except Exception:
        return day.weekday() < 5


def day_ok(rule, day):
    return {"workday": is_workday(day), "weekday": day.weekday() < 5, "everyday": True}.get(rule or "everyday", True)


def quiet(h):
    a, b = C["quiet_hours"]
    return (h >= a or h < b) if a > b else (a <= h < b)


def away_now(now):
    a = C.get("away")
    if not a or not day_ok(a.get("days"), now.date()):
        return False
    return a["from"] <= now.strftime("%H:%M") < a["to"]


# ---------- 网关 ----------
_sid = None
_lock = threading.Lock()
HUSH = [0.0]   # 说话期间灯线程先不发指令：灯一秒几条，和声音挤同一条 WebSocket 到设备，声音会一卡一卡


def _post(payload, timeout=20):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if GW_TOKEN:
        h["Authorization"] = "Bearer " + GW_TOKEN
    if _sid:
        h["mcp-session-id"] = _sid
    r = urllib.request.urlopen(urllib.request.Request(GW, json.dumps(payload).encode(), h), timeout=timeout)
    return r.headers.get("mcp-session-id"), r.read().decode()


def call(tool, args=None, want=False):
    global _sid
    with _lock:
        for _ in range(2):
            try:
                if _sid is None:
                    _sid, _ = _post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "xinchao-mood", "version": "1"}}})
                    _post({"jsonrpc": "2.0", "method": "notifications/initialized"})
                _, body = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": args or {}}})
                return body if want else True
            except Exception:
                _sid = None
        return None if want else False


def say(text):
    """说一句；说的这段时间里灯停在当前颜色。"""
    HUSH[0] = time.time() + 10
    body = call("say", {"text": text, **C["say_args"]}, want=True) or ""
    m = __import__("re").search(r'duration_ms\\?"?: ?(\d+)', body)
    HUSH[0] = time.time() + (int(m.group(1)) if m else 3000) / 1000 + 0.8


def online():
    body = call("get_status", want=True) or ""
    return '\\"connected\\": true' in body or '"connected": true' in body


# ---------- 灯：常亮 / 呼吸 / 心跳，安静时段一律灭 ----------
class Leds(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.set("off")
        self.sent = None

    def set(self, mode, rgb=(0, 0, 0), period=2.0, hold=0, night=False):
        self.mode, self.rgb, self.period, self.night_ok = mode, tuple(rgb), period, night
        self.until = time.time() + hold if hold else 0
        self.sent = None

    def _out(self, k):
        if quiet(datetime.now(TZ).hour) and not self.night_ok:
            k = 0
        c = tuple(int(v * k) for v in self.rgb)
        if c != self.sent:
            self.sent = c
            call("clear_leds") if c == (0, 0, 0) else call("set_all_leds", {"r": c[0], "g": c[1], "b": c[2]})

    def run(self):
        last_solid = 0
        while True:
            now = time.time()
            if now < HUSH[0]:
                time.sleep(0.2)
                continue
            if self.until and now > self.until:
                self.set("off")
            if self.mode == "off":
                self._out(0)
            elif self.mode == "solid":
                if now - last_solid > 30:          # reflex 摸头会清灯，隔一阵补一次
                    self.sent, last_solid = None, now
                self._out(1)
            elif self.mode == "breathe":
                self._out(0.12 + 0.88 * (0.5 - 0.5 * math.cos(2 * math.pi * (now % self.period) / self.period)))
            elif self.mode == "heart":             # 咚咚——停
                ph = now % 1.4
                self._out(1 if ph < 0.18 or 0.32 < ph < 0.5 else 0.08)
            time.sleep(0.3)


# ---------- 状态来源 ----------
def read_xinchao():
    if not XINCHAO_URL:
        return "", 0.0
    try:
        req = urllib.request.Request(XINCHAO_URL + "/v1/dashboard/snapshot", headers={"Authorization": "Bearer " + XINCHAO_TOKEN})
        s = json.load(urllib.request.urlopen(req, timeout=5))
        e = s.get("emotion") or {}
        miss = next((d.get("value", 0) for d in s.get("drives") or [] if d.get("key") == "possess"), 0)
        return e.get("shown") or e.get("label") or "", float(miss or 0)
    except Exception:
        return "", 0.0


def read_agent():
    try:
        return json.load(open(AGENT_STATE_FILE))
    except Exception:
        return {}


# ---------- 主循环 ----------
class Robot:
    def __init__(self):
        self.leds = Leds(); self.leds.start()
        self.scene = self.face = self.word = self.user_at = None
        self.miss_hi, self.miss_log, self.miss_until = False, [], 0
        self.wake_day, self.wake_until = None, 0
        self.marks, self.said = {}, {}
        self.was_online = False

    def every(self, key, gap):
        if time.time() - self.marks.get(key, 0) < gap:
            return False
        self.marks[key] = time.time()
        return True

    def show(self, face):
        if face != self.face and call("set_avatar", {"face": face}):
            self.face = face
        os.makedirs(os.path.dirname(FACE_FILE), exist_ok=True)
        with open(FACE_FILE + ".tmp", "w") as f:
            json.dump({"face": face, "scene": self.scene, "at": int(time.time())}, f)
        os.replace(FACE_FILE + ".tmp", FACE_FILE)

    def head(self, yaw, pitch, speed=40, key=None, back=False):
        """转头；key 相同的小动作 move_every_s 秒内只做一次；back=True 做完再回到平视（点头、低头一下）。"""
        if quiet(datetime.now(TZ).hour) or (key and not self.every(key, C["move_every_s"])):
            return
        call("move_head", {"yaw": yaw, "pitch": pitch, "speed": speed})
        if back:
            time.sleep(0.8)
            call("move_head", {"yaw": 0, "pitch": C["look_pitch"], "speed": speed})

    def speak(self, key, force=False):
        lines = C["lines"].get(key) or []
        if not lines or (not force and (quiet(datetime.now(TZ).hour) or time.time() - self.said.get(key, 0) < C["line_gap_s"])):
            return
        self.said[key] = time.time()
        say(random.choice(lines))

    def enter(self, scene, word):
        if self.scene == "away" and scene != "away":
            call("set_brightness", {"brightness": C["brightness"]["normal"]})
        self.scene = scene
        F, K, L, LOOK = C["faces"], C["colors"], self.leds, C["look_pitch"]
        if scene == "think":
            self.show(F["think"]); L.set("breathe", K["think"], 2.0); self.head(-12, LOOK - 8, key="think")
        elif scene == "speak":
            self.show(F["speak"]); L.set("breathe", K["speak"], 2.5, hold=5)
            self.head(0, LOOK - 8, 60, key="nod", back=True)
        elif scene == "call":
            self.show(F["call"]); L.set("breathe", K["call"], 3.0)
        elif scene == "ring":
            self.show(F["ring"]); L.set("heart", K["call"])
        elif scene == "miss":
            self.show(F["miss"]); L.set("heart", K["miss"], hold=6)
            self.head(0, LOOK - 10, 20, key="miss", back=True); self.speak("miss")
        elif scene == "sleep":
            self.show(F["sleep"]); L.set("breathe", K["sleep"], 8.0)
            call("move_head", {"yaw": 0, "pitch": 5, "speed": 10}) if not quiet(datetime.now(TZ).hour) else None
        elif scene == "away":
            self.show(F["away"]); L.set("off"); call("set_brightness", {"brightness": C["brightness"]["away"]})
        else:                                   # rest：脸跟心潮情绪词
            fresh = self.word is not None and word != self.word
            self.word = word
            self.show(C["word_faces"].get(word, F["rest"]))
            if word in C["word_leds"]:
                L.set("solid", C["word_leds"][word], hold=900)
            elif L.mode != "solid":
                L.set("off")
            if fresh and word in C["lines"]:
                self.speak(word)
        print(f"[xinchao-mood] {scene} · {self.face} · {word}", flush=True)

    def tick(self):
        now_dt = datetime.now(TZ)
        if not online():
            self.was_online, self.face = False, None
            return
        if not self.was_online:                 # 刚上线：等 reflex 把表情包推完
            self.was_online = True
            time.sleep(40)
            self.scene = self.face = None
        word, miss = read_xinchao()
        ag = read_agent()
        now = time.time()

        w = C.get("wake")                       # 早上叫起床（安静时段对它破例）
        if w and now_dt.strftime("%H:%M") == w["at"] and self.wake_day != now_dt.date() and day_ok(w.get("days"), now_dt.date()):
            self.wake_day, self.scene = now_dt.date(), "wake"
            self.show(w.get("face", C["faces"]["speak"])); self.leds.set("breathe", C["colors"]["wake"], 3.0, hold=w.get("hold_s", 1200), night=True)
            call("move_head", {"yaw": 0, "pitch": C["look_pitch"], "speed": 20})
            if w.get("lines"):
                say(random.choice(w["lines"]))
            if w.get("then"):
                time.sleep(1.5); say(w["then"])
            self.wake_until = now + w.get("hold_s", 1200)
        if now < self.wake_until:
            return

        away = away_now(now_dt)
        user_at = ag.get("user_at")             # 主人来消息：抬头看一眼
        if self.user_at is not None and user_at and user_at != self.user_at and not away:
            self.head(0, C["look_pitch"] + 8, 50, key="look_up", back=True)
        self.user_at = user_at

        st = ag.get("state", "rest")
        if st == "speak" and now - ag.get("at", 0) > C["speak_window_s"]:
            st = "rest"
        if away:
            scene = "away"
        elif st in ("think", "speak", "call", "ring"):
            scene = st
        elif quiet(now_dt.hour) and now - ag.get("at", now) > C["sleep_after_rest_min"] * 60:
            scene = "sleep"
        else:
            scene = "rest"

        M = C["miss"]                           # 想念冲满：一天最多几回、两回之间隔多久
        self.miss_log = [t for t in self.miss_log if now - t < 86400]
        if miss >= M["threshold"] and not self.miss_hi and scene == "rest" and not quiet(now_dt.hour) \
                and len(self.miss_log) < M["per_day"] and (not self.miss_log or now - self.miss_log[-1] > M["gap_s"]):
            self.miss_log.append(now); self.miss_until = now + M["hold_s"]
            self.enter("miss", word)
        self.miss_hi = miss >= M["threshold"]
        if scene == "rest" and now < self.miss_until:
            return
        if scene != self.scene or (scene == "rest" and word != self.word):
            self.enter(scene, word)


def main():
    print("[xinchao-mood] 上线", flush=True)
    r = Robot()
    while True:
        try:
            r.tick()
        except Exception as e:
            print("[xinchao-mood] 这一轮没成：", str(e)[:120], flush=True)
            time.sleep(5)
        time.sleep(1)


if __name__ == "__main__":
    main()
