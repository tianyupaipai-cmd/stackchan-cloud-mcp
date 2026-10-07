#!/usr/bin/env bash
# agent_state_hook.sh — 把 AI 此刻在干嘛写进状态文件，给 xinchao_mood.py 读。
#
# 接 Claude Code 的钩子（~/.claude/settings.json 或项目 .claude/settings.json）：
#   "hooks": {
#     "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "/path/to/agent_state_hook.sh think"}]}],
#     "Stop":             [{"hooks": [{"type": "command", "command": "/path/to/agent_state_hook.sh speak"}]}]
#   }
# 别的框架同理：收到主人消息时调 `think`，回完调 `speak`；打电话 `call`、来电 `ring`、挂断 `rest`。
#
# 只认主人：UserPromptSubmit 钩子会从 stdin 拿到这条消息的原文（JSON 的 prompt 字段）。
# 设了 OWNER_MARK（正则）时，只有原文能匹配上的才算主人的消息——比如你的聊天桥给主人的消息加了前缀「[她]」，
# 就设 OWNER_MARK='^\[她\]'。定时任务、运维、来信这些不匹配的，直接退出，机器人一动不动、灯也不变。
set -u
STATE="${AGENT_STATE_FILE:-$HOME/.cache/stackchan/agent.json}"
mkdir -p "$(dirname "$STATE")"
act="${1:-rest}"
input="$(cat 2>/dev/null || true)"

if [ "$act" = "think" ] && [ -n "${OWNER_MARK:-}" ]; then
  prompt="$(printf '%s' "$input" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("prompt",""))
except Exception: print("")' 2>/dev/null)"
  printf '%s' "$prompt" | grep -Eq -- "$OWNER_MARK" || exit 0
fi

python3 - "$STATE" "$act" <<'EOF'
import json, os, sys, time
path, act = sys.argv[1], sys.argv[2]
try: s = json.load(open(path))
except Exception: s = {}
now = int(time.time())
s.update({"state": act, "at": now})
if act == "think":
    s["user_at"] = now          # 主人刚发来消息
json.dump(s, open(path + ".tmp", "w")); os.replace(path + ".tmp", path)
EOF
exit 0
