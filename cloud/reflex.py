#!/usr/bin/env python3
"""Stack-chan reflex daemon (cloud edition) / 反射弧守护进程（云端版）.

Runs on the VPS next to the stackchan-mcp gateway and talks to it over the
local MCP endpoint (127.0.0.1:8767). It adds the "alive" behaviours that the
gateway itself does not provide:

1. Device reconnect  -> re-push the avatar archive + re-enable blinking, so a
   power cycle never leaves the device on the firmware default face.
2. Speech watchdog   -> return to the `idle` face a few seconds after a `say()`
   finishes, in case the driving window forgets to reset it. A newer `say()`
   simply postpones the deadline.
3. Touch feedback    -> blush face + pink LEDs for a few seconds when the head
   is touched (optional, see AUTO_BLUSH).
4. Idle micro-motion -> occasional head turns while nobody interacts, so the
   device does not look frozen.
5. Standby           -> dim the screen after a period of no interaction, and
   restore brightness on the next event.

Environment variables
---------------------
STACKCHAN_TOKEN    REQUIRED. Bearer token of the local MCP gateway. The daemon
                   will refuse to start without it.
STACKCHAN_MCP_URL  Optional. MCP endpoint, default http://127.0.0.1:8767/mcp
AVATAR_SET_PATH    Optional. Avatar archive pushed on reconnect,
                   default /root/avatar_set.bin
GATEWAY_UNIT       Optional. systemd unit followed via journalctl,
                   default stackchan-gateway (upstream unit name)

Deploy as a systemd unit named `stackchan-reflex`.
"""
import json, os, re, subprocess, threading, time, urllib.request

# --------------------------------------------------------------------------
# Configuration / 配置
# --------------------------------------------------------------------------
BASE = os.environ.get("STACKCHAN_MCP_URL", "http://127.0.0.1:8767/mcp")
TOKEN = os.environ.get("STACKCHAN_TOKEN", "")   # REQUIRED, see module docstring
BIN = os.environ.get("AVATAR_SET_PATH", "/root/avatar_set.bin")
GATEWAY_UNIT = os.environ.get("GATEWAY_UNIT", "stackchan-gateway")

# --------------------------------------------------------------------------
# Tunables / 可调参数
# All values below are field-tested defaults; adjust to taste.
# --------------------------------------------------------------------------
TOUCH_COOLDOWN = 15      # s   min gap between two touch reactions; long enough
                         #     that occasional false positives are not annoying
AUTO_BLUSH = True        #     touch feedback on/off / 触摸反馈（可关）
STANDBY_AFTER = 600      # s   no interaction -> dim the screen to save power

BLUSH_RGB = (244, 114, 160)   # LED colour used for the blush reaction
BLUSH_HOLD_S = 6         # s   how long the blush face + LEDs stay on
BLUSH_RECOVER_S = 3      # s   `happy` face lingers before returning to idle

IDLE_GRACE_S = 4.0       # s   extra delay after say() duration before idle reset
IDLE_MOTION_AFTER = 40   # s   idle time before micro-motions start
IDLE_MOTION_MIN = 45     # s   lower bound of the random micro-motion interval
IDLE_MOTION_SPAN = 60    # s   random span added on top of IDLE_MOTION_MIN
IDLE_YAWS = [-25, -12, 0, 12, 25]   # deg  horizontal look-around angles
IDLE_PITCHES = [20, 25, 30]         # deg  downward tilt; keeps the gaze at a
                                    #      seated user's eye level rather than
                                    #      pointing at the ceiling
IDLE_MOTION_SPEED = 80              #      servo speed for micro-motions

# Device preferences re-applied after every reconnect (a device reboot resets
# them). 用户偏好档（device preferences）
PREF_VOLUME = 100        # 0-100
PREF_BRIGHTNESS = 40     # 0-100; deliberately low to reduce heat
STANDBY_BRIGHTNESS = 20  # 0-100; screen brightness while in standby
TORQUE_RELEASE_MS = 3000 # ms  auto-release servo torque after a move; holding
                         #     torque is the main source of heat

TOUCH_POLL_S = 2         # s   touch-state polling interval
TOUCH_FRESH_MS = 4000    # ms  a touch event newer than this counts as activity
TOUCH_REACT_MS = 3500    # ms  a touch event newer than this triggers a reaction
RECONNECT_DELAY_S = 20.0 # s   wait after "ESP32 ready" before pushing avatars


def post(payload, session=None, timeout=180):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "Authorization": "Bearer " + TOKEN}
    if session:
        headers["mcp-session-id"] = session
    req = urllib.request.Request(BASE, json.dumps(payload).encode(), headers)
    resp = urllib.request.urlopen(req, timeout=timeout)
    return resp.headers.get("mcp-session-id"), resp.read().decode()


def _parse(body):
    """Extract the JSON payload from an MCP response (plain JSON or SSE)."""
    for line in body.splitlines():
        if line.startswith("data:"):
            body = line[5:].strip()
            break
    try:
        d = json.loads(body)
        return json.loads(d["result"]["content"][0]["text"])
    except Exception:
        return None


_SID = None
def call(tool, args=None, timeout=180):
    """Call an MCP tool. Returns the parsed result dict, or None on parse failure.

    The MCP session is created lazily and reset whenever a request fails, so the
    next call re-initializes instead of reusing a dead session id.
    """
    global _SID
    try:
        if _SID is None:
            _SID, _ = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "reflex-vps", "version": "0.2"}}}, timeout=timeout)
            post({"jsonrpc": "2.0", "method": "notifications/initialized"}, _SID, timeout=timeout)
        _, body = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": tool, "arguments": args or {}}}, _SID, timeout=timeout)
        return _parse(body)
    except Exception:
        _SID = None
        raise


def restore_settings():
    """Re-apply device preferences (volume / brightness / torque release / LEDs off).

    A device reboot resets these to firmware defaults, so they are pushed again
    after every reconnect.
    """
    for tool, args in (
        ("set_volume", {"volume": PREF_VOLUME}),
        ("set_brightness", {"brightness": PREF_BRIGHTNESS}),
        ("set_auto_torque_release", {"enabled": True, "timeout_ms": TORQUE_RELEASE_MS}),
        ("clear_leds", {}),
    ):
        try:
            call(tool, args, timeout=20)
        except Exception:
            pass
    print(f"[reflex] preferences restored (volume={PREF_VOLUME} "
          f"brightness={PREF_BRIGHTNESS} torque_release={TORQUE_RELEASE_MS}ms leds=off)",
          flush=True)


def push_avatar():
    """Re-upload the avatar archive and bring the face animation back online."""
    try:
        call("load_avatar_set", {"archive_path": BIN, "mode": "layered", "timeout": 120})
        print("[reflex] avatar set pushed", flush=True)
        call("set_blink", {"enabled": True})
        print("[reflex] blink/animation enabled", flush=True)
        # load_avatar_set only replaces frames; it does not reset the current
        # face index, so after a reconnect the device may still show the old
        # face (e.g. happy). Force it back to idle.
        call("set_avatar", {"face": "idle"})
        restore_settings()
    except Exception as e:
        print("[reflex] avatar push failed:", e, flush=True)


# ---------- Speech watchdog: back to idle a few seconds after say() ----------
_idle_due = 0.0
_idle_lock = threading.Lock()


def idle_watch():
    global _idle_due
    while True:
        time.sleep(1)
        with _idle_lock:
            due = _idle_due
        if due and time.time() >= due:
            with _idle_lock:
                if _idle_due != due:
                    continue
                _idle_due = 0.0
            try:
                call("set_avatar", {"face": "idle"}, timeout=30)
                print("[reflex] watchdog reset face to idle", flush=True)
            except Exception as e:
                print("[reflex] idle reset failed:", e, flush=True)


def schedule_idle(duration_ms):
    """Arm the watchdog for `duration_ms` + grace. A later call simply postpones it."""
    global _idle_due
    with _idle_lock:
        _idle_due = time.time() + duration_ms / 1000.0 + IDLE_GRACE_S


# ---------- Touch reaction / idle motion / standby ----------
_last_idle_motion = 0.0
_idle_gap = 60.0


def touch_loop():
    last_reaction = 0.0
    last_activity = time.time()
    standby = False
    while True:
        try:
            t = call("get_touch_state", timeout=20) or {}
            age = t.get("last_event_age_ms")
            event = t.get("last_event")
            now = time.time()
            if age is not None and age < TOUCH_FRESH_MS:
                last_activity = now
                if standby:
                    standby = False
                    try:
                        call("set_brightness", {"brightness": PREF_BRIGHTNESS}, timeout=20)
                        print("[reflex] activity detected, leaving standby", flush=True)
                    except Exception:
                        pass
            if (not standby and now - last_activity > STANDBY_AFTER):
                standby = True
                try:
                    call("set_brightness", {"brightness": STANDBY_BRIGHTNESS}, timeout=20)
                    call("set_avatar", {"face": "idle"}, timeout=20)
                    print("[reflex] idle timeout, entering standby", flush=True)
                except Exception:
                    pass
            # Idle micro-motion: occasional look-around so the device does not
            # look frozen while nobody interacts with it.
            global _last_idle_motion, _idle_gap
            if (now - last_activity > IDLE_MOTION_AFTER
                    and now - _last_idle_motion > _idle_gap):
                _last_idle_motion = now
                # pseudo-random interval, IDLE_MOTION_MIN .. +IDLE_MOTION_SPAN
                _idle_gap = IDLE_MOTION_MIN + (int(now) % IDLE_MOTION_SPAN)
                try:
                    yaw = IDLE_YAWS[int(now) % len(IDLE_YAWS)]
                    pitch = IDLE_PITCHES[int(now // 7) % len(IDLE_PITCHES)]
                    call("move_head", {"yaw": yaw, "pitch": pitch,
                                       "speed": IDLE_MOTION_SPEED}, timeout=20)
                    print(f"[reflex] idle look-around yaw={yaw}", flush=True)
                except Exception:
                    pass
            # Touch feedback. Real touches and false positives look identical to
            # the sensor, so the cooldown is kept long (TOUCH_COOLDOWN).
            if (AUTO_BLUSH and age is not None and age < TOUCH_REACT_MS
                    and event in ("stroke", "press")
                    and now - last_reaction > TOUCH_COOLDOWN):
                last_reaction = now
                print(f"[reflex] touch detected ({event}), playing reaction", flush=True)
                try:
                    call("set_avatar", {"face": "embarrassed"}, timeout=20)
                    call("set_all_leds", {"r": BLUSH_RGB[0], "g": BLUSH_RGB[1],
                                          "b": BLUSH_RGB[2]}, timeout=20)
                    time.sleep(BLUSH_HOLD_S)
                    call("set_avatar", {"face": "happy"}, timeout=20)
                    call("clear_leds", timeout=20)
                    time.sleep(BLUSH_RECOVER_S)
                    call("set_avatar", {"face": "idle"}, timeout=20)
                except Exception as e:
                    print("[reflex] reaction failed:", e, flush=True)
        except Exception:
            pass
        time.sleep(TOUCH_POLL_S)


# ---------- Gateway log watcher: reconnect + speech watchdog triggers ----------
def watch_log():
    p = subprocess.Popen(
        ["stdbuf", "-oL", "-eL", "journalctl", "-fu", GATEWAY_UNIT, "-n", "0", "--no-pager"],
        stdout=subprocess.PIPE, text=True, bufsize=1)
    for line in p.stdout:
        if "ESP32 ready" in line:
            print(f"[reflex] device online, pushing avatars in {RECONNECT_DELAY_S:.0f}s",
                  flush=True)
            threading.Timer(RECONNECT_DELAY_S, push_avatar).start()
        m = re.search(r"say\(\):.*duration_ms=(\d+)", line)
        if m:
            schedule_idle(int(m.group(1)))


if __name__ == "__main__":
    if not TOKEN:
        print("!!! STACKCHAN_TOKEN is not set, refusing to start", flush=True)
        raise SystemExit(1)
    print("[reflex] cloud reflex daemon online", flush=True)
    time.sleep(3)
    try:
        push_avatar()
    except Exception:
        pass
    threading.Thread(target=idle_watch, daemon=True).start()
    threading.Thread(target=touch_loop, daemon=True).start()
    watch_log()
