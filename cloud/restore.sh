#!/bin/sh
# Re-apply local customizations to the installed stackchan-mcp package.
# 重装网关后（尤其 `uv tool install --force`）一键恢复对 stackchan-mcp 源码的定制。
#
# Only the two things that live *inside* the installed package directory are
# handled here, because those are the ones a reinstall overwrites. Standalone
# files (the OAuth proxy, the reflex daemon) live outside the package directory
# and survive a reinstall, so they need no restoration.
#
# Usage (on the VPS, as root):  sh restore.sh
set -e
PKG=${STACKCHAN_PKG_DIR:-/root/.local/share/uv/tools/stackchan-mcp/lib/python3.12/site-packages/stackchan_mcp}

# 1) Aggressive WebSocket keepalive: ping every 20s, 60s timeout.
#    Home NAT devices drop idle long-lived connections after roughly 90s; these
#    values keep the device link alive through that.
sed -i 's/^WEBSOCKET_PING_INTERVAL_S = .*/WEBSOCKET_PING_INTERVAL_S = 20/' "$PKG/esp32_client.py"
sed -i 's/^WEBSOCKET_PING_TIMEOUT_S = .*/WEBSOCKET_PING_TIMEOUT_S = 60/'  "$PKG/esp32_client.py"

# 2) opuslib: PCM -> Opus encoder binding. Without it TTS synthesis "succeeds"
#    but no audio is ever pushed to the device (say() reports OK, device silent).
HOME=/root /root/.local/bin/uv tool install "stackchan-mcp[stt-faster-whisper]" --with opuslib >/dev/null 2>&1 || true

# 3) Avatar archive size validation: upstream only accepts a 14-frame layered
#    set (537_600 bytes). Relax it to also accept 40-frame sets (1_536_000).
#    Without this patch load_avatar_set is rejected with size_mismatch, the
#    device falls back to the firmware default face and set_avatar has no effect.
python3 - "$PKG/gateway.py" <<'PYEOF'
import sys
p=sys.argv[1]; s=open(p).read()
old='''        expected = {
            "layered": 14 * kimg_bytes,   # 537_600
            "matrix":  90 * kimg_bytes,   # 3_456_000
        }.get(mode)
        if expected is None:
            return {"ok": False, "error": f"unknown_mode: {mode}"}
        if len(payload) != expected:
            return {
                "ok": False,
                "error": f"size_mismatch: got={len(payload)} expected={expected} (mode={mode})",
            }'''
new='''        valid = {
            "layered": {14 * kimg_bytes, 40 * kimg_bytes},
            "matrix":  {90 * kimg_bytes},
        }.get(mode)
        if valid is None:
            return {"ok": False, "error": f"unknown_mode: {mode}"}
        if len(payload) not in valid:
            return {
                "ok": False,
                "error": f"size_mismatch: got={len(payload)} expected={sorted(valid)} (mode={mode})",
            }'''
if "40 * kimg_bytes" not in s and old in s:
    open(p,"w").write(s.replace(old,new)); print("gateway.py: avatar-size patched")
else:
    print("gateway.py: already patched or pattern changed")
PYEOF

# 4) Apply
systemctl restart stackchan-gateway
sleep 3
grep -E '^WEBSOCKET_PING' "$PKG/esp32_client.py"
systemctl is-active stackchan-gateway
echo "restore done: keepalive 20/60 + opuslib"

# Notes (not handled by this script - these live outside the package directory
# and are therefore not lost on reinstall):
#  - TTS engine / voice: environment variables in
#      /etc/systemd/system/stackchan-gateway.service
#      STACKCHAN_TTS_ENGINE=edge-tts
#      STACKCHAN_EDGE_TTS_DEFAULT_VOICE=<voice-id>
#      PATH must include /root/.local/bin
#  - Subtitles: gateway 0.17.0 forwards say() text to the device and the
#    firmware renders it natively; no gateway-side change needed.
#  - OAuth gateway: /root/stackchan/oauth_proxy.py  (systemd unit stackchan-oauth)
#  - Reflex daemon: /root/stackchan/reflex.py       (systemd unit stackchan-reflex)
