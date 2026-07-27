#!/usr/bin/env bash
#
# apply_patches.sh — 给上游 stackchan-mcp (0.17.0) 的安装目录打补丁
#                    Patch an installed stackchan-mcp (0.17.0) tree.
#
# 幂等 (idempotent)：重复运行不会重复修改、不会报错。
# 每处修改前写 .orig 备份（已存在则保留最初的上游原件，不覆盖）。
#
# 用法 / Usage:
#   bash apply_patches.sh                 # 打补丁 1、2；检查 3、4
#   DRY_RUN=1 bash apply_patches.sh       # 只报告，不写任何文件
#   PKG=/path/to/stackchan_mcp bash apply_patches.sh
#   APPLY_DEPS=1 bash apply_patches.sh    # 顺带执行 uv tool install（补丁 3）
#   APPLY_ENV=1 UNIT=my-gateway bash apply_patches.sh   # 写 systemd drop-in（补丁 4）
#
# 退出码 / Exit codes: 0 = 全部就绪或已成功修改；1 = 有补丁无法自动应用（需人工处理）
#
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# 配置 / Configuration — 按你的环境改这里
# ─────────────────────────────────────────────────────────────────────────────

# 已安装的包目录。Python 小版本 (python3.12) 随环境变化。
PKG="${PKG:-/root/.local/share/uv/tools/stackchan-mcp/lib/python3.12/site-packages/stackchan_mcp}"

# 补丁 1：WebSocket 心跳（秒）
PING_INTERVAL="${PING_INTERVAL:-20}"   # 上游默认 30
PING_TIMEOUT="${PING_TIMEOUT:-60}"     # 上游默认 90

# 补丁 2：额外放行的 layered 帧数（上游只认 14 帧）
#         40 = 16 faceA + 3 eyes + 5 mouth + 16 faceB
EXTRA_LAYERED_FRAMES="${EXTRA_LAYERED_FRAMES:-40}"

# 补丁 4：HuggingFace 镜像与后端开关
HF_ENDPOINT_VALUE="${HF_ENDPOINT_VALUE:-https://hf-mirror.com}"
HF_DISABLE_XET_VALUE="${HF_DISABLE_XET_VALUE:-1}"

# 可选行为开关 / Optional behaviour flags
DRY_RUN="${DRY_RUN:-0}"        # 1 = 只报告不写
APPLY_DEPS="${APPLY_DEPS:-0}"  # 1 = 真的跑 uv tool install
APPLY_ENV="${APPLY_ENV:-0}"    # 1 = 真的写 systemd drop-in（需要同时设 UNIT）
UNIT="${UNIT:-}"               # systemd 服务名（不带 .service）

# ─────────────────────────────────────────────────────────────────────────────
# 工具函数 / Helpers
# ─────────────────────────────────────────────────────────────────────────────

if [ -t 1 ]; then
  C_OK=$'\033[32m'; C_SKIP=$'\033[36m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_OFF=$'\033[0m'
else
  C_OK=''; C_SKIP=''; C_WARN=''; C_ERR=''; C_OFF=''
fi

FAILED=0
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s  [ok]%s      %s\n'   "$C_OK"   "$C_OFF" "$*"; }
skip() { printf '%s  [skip]%s    %s\n'   "$C_SKIP" "$C_OFF" "$*"; }
warn() { printf '%s  [warn]%s    %s\n'   "$C_WARN" "$C_OFF" "$*"; }
fail() { printf '%s  [FAIL]%s    %s\n'   "$C_ERR"  "$C_OFF" "$*"; FAILED=1; }
hdr()  { printf '\n──── %s\n' "$*"; }

# GNU sed / BSD sed 兼容的就地编辑
sed_i() {
  if sed --version >/dev/null 2>&1; then sed -i "$@"; else sed -i '' "$@"; fi
}

# 首次修改前留一份原件；已有 .orig 就不动（保住最初的上游版本）
backup_once() {
  local f="$1"
  if [ ! -f "${f}.orig" ]; then
    cp -p "$f" "${f}.orig"
    say "            备份 backup -> ${f}.orig"
  fi
}

PYTHON_BIN="$(command -v python3 || command -v python || true)"

# ─────────────────────────────────────────────────────────────────────────────
# 前置检查 / Preflight
# ─────────────────────────────────────────────────────────────────────────────

hdr "环境 / Environment"
say "  PKG      = $PKG"
say "  DRY_RUN  = $DRY_RUN"
[ -d "$PKG" ] || { fail "包目录不存在 / package dir not found: $PKG"; say ""; say "请用 PKG=... 指定正确路径。"; exit 1; }
[ -n "$PYTHON_BIN" ] || { fail "找不到 python3 / python3 not found"; exit 1; }
ok "包目录存在 / package dir present"

# ─────────────────────────────────────────────────────────────────────────────
# 补丁 1 — esp32_client.py：WebSocket 密心跳
# ─────────────────────────────────────────────────────────────────────────────

hdr "补丁 1 / Patch 1 — esp32_client.py  WebSocket keepalive"

P1_FILE="$PKG/esp32_client.py"
if [ ! -f "$P1_FILE" ]; then
  fail "文件不存在 / missing: $P1_FILE"
else
  p1_changed=0
  p1_error=0
  # 先读当前值，判断是否已经打过
  for spec in "WEBSOCKET_PING_INTERVAL_S=$PING_INTERVAL" "WEBSOCKET_PING_TIMEOUT_S=$PING_TIMEOUT"; do
    name="${spec%%=*}"; want="${spec##*=}"
    cur="$(sed -n -E "s/^${name}[[:space:]]*=[[:space:]]*([0-9]+(\.[0-9]+)?).*/\1/p" "$P1_FILE" | head -1)"
    if [ -z "$cur" ]; then
      fail "找不到常量 / constant not found: $name（上游结构可能已变，请人工核对）"
      p1_error=1
      continue
    fi
    if [ "$cur" = "$want" ]; then
      skip "$name 已是 $want / already $want"
      continue
    fi
    if [ "$DRY_RUN" = "1" ]; then
      warn "[dry-run] 会把 $name 从 $cur 改为 $want"
      continue
    fi
    [ "$p1_changed" = "1" ] || backup_once "$P1_FILE"
    sed_i -E "s|^${name}[[:space:]]*=[[:space:]]*[0-9]+(\.[0-9]+)?.*|${name} = ${want}  # patched: was ${cur}|" "$P1_FILE"
    ok "$name: $cur -> $want"
    p1_changed=1
  done

  if [ "$p1_changed" = "1" ]; then
    if "$PYTHON_BIN" - "$P1_FILE" <<'PY'
import ast, sys
src = open(sys.argv[1], encoding="utf-8").read()
ast.parse(src)
PY
    then ok "语法自检通过 / syntax check passed"
    else fail "语法自检失败，请从 ${P1_FILE}.orig 恢复 / syntax check failed"
    fi
  fi
  [ "$p1_error" = "1" ] || [ "$p1_changed" = "1" ] || true
fi

# ─────────────────────────────────────────────────────────────────────────────
# 补丁 2 — gateway.py：表情包尺寸校验放开
# ─────────────────────────────────────────────────────────────────────────────

hdr "补丁 2 / Patch 2 — gateway.py  avatar-set size whitelist"

P2_FILE="$PKG/gateway.py"
P2_MARKER="patched: accept a set of valid sizes"

if [ ! -f "$P2_FILE" ]; then
  fail "文件不存在 / missing: $P2_FILE"
elif grep -q "$P2_MARKER" "$P2_FILE"; then
  skip "已打过补丁 / already patched"
else
  # 备份放在 python 里、紧挨着写入之前，这样"无法应用"时不会留下多余的 .orig
  set +e
  "$PYTHON_BIN" - "$P2_FILE" "$EXTRA_LAYERED_FRAMES" "$P2_MARKER" "$DRY_RUN" <<'PY'
import ast, re, sys

path, extra_frames, marker, dry_run = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4] == "1"
src = open(path, encoding="utf-8").read()
lines = src.splitlines(keepends=True)

# 1) 定位 `expected = {` 那一行
start = None
indent = ""
for i, ln in enumerate(lines):
    m = re.match(r"^([ \t]*)expected = \{[ \t]*$", ln)
    if m:
        start, indent = i, m.group(1)
        break
if start is None:
    print("        找不到 `expected = {` 块 / block not found — 上游结构可能已变，请人工核对", file=sys.stderr)
    sys.exit(2)

# 2) 抓出上游写死的帧数，尽量沿用而不是硬编码
window = "".join(lines[start:start + 8])
lay = re.search(r'"layered"\s*:\s*(\d+)\s*\*\s*kimg_bytes', window)
mat = re.search(r'"matrix"\s*:\s*(\d+)\s*\*\s*kimg_bytes', window)
if not (lay and mat):
    print("        块内没找到 layered/matrix * kimg_bytes / unexpected block shape", file=sys.stderr)
    sys.exit(2)
lay_n, mat_n = int(lay.group(1)), int(mat.group(1))

# 3) 定位块尾：size_mismatch 之后第一行只有 `}` 的行
mismatch = None
for i in range(start, min(start + 30, len(lines))):
    if "size_mismatch" in lines[i]:
        mismatch = i
        break
if mismatch is None:
    print("        找不到 size_mismatch 分支 / size_mismatch branch not found", file=sys.stderr)
    sys.exit(2)
end = None
for i in range(mismatch, min(mismatch + 10, len(lines))):
    if lines[i].strip() == "}":
        end = i
        break
if end is None:
    print("        找不到 size_mismatch 返回块的结尾 / closing brace not found", file=sys.stderr)
    sys.exit(2)

# 4) 生成替换块
lay_set = sorted({lay_n, extra_frames})
lay_expr = ", ".join(f"{n} * kimg_bytes" for n in lay_set)
lay_cmt = " / ".join(f"{n * 160 * 120 * 2:_}" for n in lay_set)
i1, i2, i3, i4 = indent, indent + "    ", indent + "        ", indent + "            "
new = (
    f"{i1}# {marker} instead of one exact size,\n"
    f"{i1}# so custom {extra_frames}-frame layered sets are not rejected.\n"
    f"{i1}valid = {{\n"
    f'{i2}"layered": {{{lay_expr}}},  # {lay_cmt}\n'
    f'{i2}"matrix":  {{{mat_n} * kimg_bytes}},  # {mat_n * 160 * 120 * 2:_}\n'
    f"{i1}}}.get(mode)\n"
    f"{i1}if valid is None:\n"
    f'{i2}return {{"ok": False, "error": f"unknown_mode: {{mode}}"}}\n'
    f"{i1}if len(payload) not in valid:\n"
    f"{i2}return {{\n"
    f'{i3}"ok": False,\n'
    f'{i3}"error": (\n'
    f'{i4}f"size_mismatch: got={{len(payload)}} "\n'
    f'{i4}f"expected one of {{sorted(valid)}} (mode={{mode}})"\n'
    f"{i3}),\n"
    f"{i2}}}\n"
)

out = "".join(lines[:start]) + new + "".join(lines[end + 1:])

# 5) 写回前先做语法自检
try:
    ast.parse(out)
except SyntaxError as exc:
    print(f"        生成的代码语法错误，未写入 / generated code invalid: {exc}", file=sys.stderr)
    sys.exit(2)

if dry_run:
    print(f"        [dry-run] 会把 layered 合法帧数改为 {lay_set}（原 [{lay_n}]），行 {start + 1}-{end + 1}")
    sys.exit(3)

# 6) 确认能写之后再留备份，避免"无法应用"时留下多余的 .orig
import os, shutil
if not os.path.exists(path + ".orig"):
    shutil.copy2(path, path + ".orig")
    print(f"        备份 backup -> {path}.orig")

open(path, "w", encoding="utf-8").write(out)
print(f"        layered 合法帧数 {lay_set}（原 [{lay_n}]），matrix [{mat_n}] 保持不变")
sys.exit(0)
PY
  rc=$?
  set -e
  case "$rc" in
    0) ok "尺寸校验已放开 / size validation relaxed（重启网关后生效）" ;;
    3) warn "[dry-run] 未写入 / not written" ;;
    *) fail "补丁 2 无法自动应用，需人工修改 / could not apply automatically" ;;
  esac
fi

# ─────────────────────────────────────────────────────────────────────────────
# 补丁 3 — opuslib（Python 绑定缺失）
# ─────────────────────────────────────────────────────────────────────────────

hdr "补丁 3 / Patch 3 — opuslib Python binding"

# 从 $PKG 反推出 uv tool 环境的解释器：<venv>/lib/pythonX.Y/site-packages/stackchan_mcp
TOOL_PY=""
for cand in \
  "$(cd "$PKG/../../../.." 2>/dev/null && pwd)/bin/python" \
  "$(cd "$PKG/../../../.." 2>/dev/null && pwd)/bin/python3"
do
  [ -x "$cand" ] && { TOOL_PY="$cand"; break; }
done

INSTALL_CMD='uv tool install "stackchan-mcp[stt-faster-whisper]" --with opuslib'

if [ -z "$TOOL_PY" ]; then
  warn "推断不出 uv tool 的解释器 / could not locate the tool venv python"
  say  "            请手动确认：<venv>/bin/python -c 'import opuslib'"
elif "$TOOL_PY" -c "import opuslib" >/dev/null 2>&1; then
  skip "opuslib 已安装 / already installed（$TOOL_PY）"
else
  if [ "$APPLY_DEPS" = "1" ] && [ "$DRY_RUN" != "1" ]; then
    say "            执行 / running: $INSTALL_CMD"
    if eval "$INSTALL_CMD"; then
      if "$TOOL_PY" -c "import opuslib" >/dev/null 2>&1; then
        ok "opuslib 安装成功 / installed（记得重启网关服务）"
      else
        # uv 可能重建了环境，解释器路径变了
        warn "安装命令已执行，但在旧路径下仍导入不到；请重新验证 / re-verify after env rebuild"
      fi
    else
      fail "安装失败 / install failed"
    fi
  else
    fail "opuslib 缺失 / missing — TTS 合成得出 PCM 但推不出音频"
    say  "            修复 / fix:  $INSTALL_CMD"
    say  "            （自动执行请加 APPLY_DEPS=1；装完必须重启网关服务）"
  fi
fi

# 底层 C 库（存在 ≠ Python 绑定存在，别混淆）
if command -v ldconfig >/dev/null 2>&1; then
  if ldconfig -p 2>/dev/null | grep -qi 'libopus\.so'; then
    ok "底层 libopus 在位 / native libopus present"
  else
    warn "找不到 libopus.so / native libopus missing — Debian/Ubuntu: apt-get install -y libopus0"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 补丁 4 — STT 模型下载环境变量
# ─────────────────────────────────────────────────────────────────────────────

hdr "补丁 4 / Patch 4 — HuggingFace mirror + Xet off (STT model download)"

HF_CACHE="${HF_HOME:-/root/.cache/huggingface}"

if [ -d "$HF_CACHE" ] && [ -n "$(ls -A "$HF_CACHE" 2>/dev/null)" ]; then
  ok "模型缓存已存在 / model cache present: $HF_CACHE ($(du -sh "$HF_CACHE" 2>/dev/null | cut -f1))"
else
  warn "模型缓存为空 / model cache empty: $HF_CACHE（首次 listen 会触发下载）"
fi

if [ -n "$UNIT" ] && command -v systemctl >/dev/null 2>&1; then
  UNIT_ENV="$(systemctl show "$UNIT" -p Environment 2>/dev/null || true)"
else
  UNIT_ENV=""
fi

need_env=0
case "$UNIT_ENV" in *"HF_ENDPOINT="*) ;; *) need_env=1 ;; esac
case "$UNIT_ENV" in *"HF_HUB_DISABLE_XET="*) ;; *) need_env=1 ;; esac

if [ -n "$UNIT" ] && [ "$need_env" = "0" ]; then
  skip "两个环境变量都已在服务里 / both env vars already set on $UNIT"
else
  DROPIN_DIR="/etc/systemd/system/${UNIT:-<your-gateway-unit>}.service.d"
  if [ "$APPLY_ENV" = "1" ] && [ -n "$UNIT" ] && [ "$DRY_RUN" != "1" ]; then
    mkdir -p "$DROPIN_DIR"
    cat > "$DROPIN_DIR/10-hf-mirror.conf" <<EOF
[Service]
Environment=HF_ENDPOINT=${HF_ENDPOINT_VALUE}
Environment=HF_HUB_DISABLE_XET=${HF_DISABLE_XET_VALUE}
EOF
    ok "已写入 / wrote $DROPIN_DIR/10-hf-mirror.conf"
    say "            接着执行 / then run: systemctl daemon-reload && systemctl restart $UNIT"
  else
    warn "环境变量未确认在位 / env vars not confirmed（两个缺一不可）"
    say  "            在 systemd unit 的 [Service] 段加："
    say  "              Environment=HF_ENDPOINT=${HF_ENDPOINT_VALUE}"
    say  "              Environment=HF_HUB_DISABLE_XET=${HF_DISABLE_XET_VALUE}"
    say  "            然后 systemctl daemon-reload && systemctl restart <unit>"
    say  "            （自动写 drop-in：APPLY_ENV=1 UNIT=<服务名>）"
    say  "            注意：只设镜像不够——新版 huggingface_hub 走 Xet 后端会绕开镜像并报 401。"
  fi
fi

say "            建议预热 / pre-warm (避开反代 60s 超时导致的 504)："
say "              HF_ENDPOINT=${HF_ENDPOINT_VALUE} HF_HUB_DISABLE_XET=${HF_DISABLE_XET_VALUE} \\"
say "                python3 -c \"from faster_whisper import WhisperModel; WhisperModel('base')\""

# ─────────────────────────────────────────────────────────────────────────────
# 收尾 / Wrap-up
# ─────────────────────────────────────────────────────────────────────────────

hdr "完成 / Done"
if [ "$DRY_RUN" = "1" ]; then
  say "  DRY-RUN：没有写入任何文件 / nothing was written."
  say "  去掉 DRY_RUN=1 再跑一次即可实际打补丁。"
elif [ "$FAILED" = "0" ]; then
  say "  全部补丁已就绪 / all patches in place."
else
  say "  有补丁需要人工处理，见上面的 [FAIL] / some patches need manual attention."
fi
say "  源码补丁（1、2）改的是已安装的包目录——"
say "  uv tool upgrade / 重装之后会被覆盖，请重新运行本脚本。"
say "  回滚 / rollback:  cp \$PKG/<file>.orig \$PKG/<file>"
say "  改完源码务必重启网关服务 / restart the gateway service after source patches."

exit "$FAILED"
