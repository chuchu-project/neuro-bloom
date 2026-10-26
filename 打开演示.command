#!/bin/zsh
#
#  NeuroBloom 演示 —— 双击这个文件就能打开
#
#  它会做这几件事：
#    1. 把上一次残留的服务清干净（不然端口会打架）
#    2. 启动「桥接」(bridge, 8765 端口)
#    3. 启动「回放」：把那段 874 秒真实脑电里最干净的 415~510 秒循环播出
#    4. 启动「大模型话术」(agent_service)：字牌上的陪伴话术由它实时生成
#    5. Unity 没开着的话，顺手帮你打开
#
#  然后你只要切到 Unity 窗口，点一下 ▶ Play，画面就会跟着脑电变。
#
#  想停下来：回到这个黑窗口，按 Control+C —— 服务和查看会一起停干净。
#  （Unity 窗口会留着，只是画面不再动了。）
#
#  大模型话术这一路（agent_service）现在默认就启动：
#    · 它**不会**覆盖画面参数 —— 花开/阳光/雾/水晶这四个参数是脑电链路写的，
#      大模型只负责"说话"，只回传 script/cue。
#      （早期注释说它跟回放抢参数、画面会乱跳，那个问题早修好了。）
#    · 字牌开场先显示建造时那句操作提示「点一下地面，种下一束光」，
#      等用户真的种下第一株之后，字牌才交给大模型接管 ——
#      免得话术一上来就把"怎么操作"的提示顶掉。
#    · 连不上网 / 没有 API Key 都有兜底话术，不会开天窗。
#    不想启这一路：双击前先在终端里 export NBL_NO_AGENT=1
#
set -u

# 这个脚本写死了项目位置，所以放哪儿双击都能用（拖到桌面也行）。
PROJ="/Users/chujingyue/DeepWorks/NeuroBloom"
PY="$PROJ/.venv/bin/python"
BDF_DEFAULT="$HOME/Documents/OpenBCI_GUI/Recordings/OpenBCI-BDF-2026-10-14_20-37-57.bdf"
UNITY_APP="/Applications/Unity/Hub/Editor/6000.0.22f1/Unity.app"
LOGS="$PROJ/.data/logs"

cd "$PROJ" || { echo "❌ 找不到项目目录：$PROJ"; echo "按回车关闭…"; read -r; exit 1; }
if [ ! -x "$PY" ]; then
  echo "❌ 找不到 Python 环境：$PY"
  echo "   先执行一次：bash setup/macos-dev-setup.sh"
  echo "按回车关闭…"; read -r; exit 1
fi

mkdir -p "$LOGS"

# 加个闩：多个信号可能几乎同时到达（比如 Control+C 会同时触发 INT 和 EXIT），
# 保证清理只做一次、提示只打一遍。
CLEANED=0
cleanup() {
  [ "$CLEANED" = "1" ] && return
  CLEANED=1
  echo
  echo "==> 正在停止…"
  [ -n "${WATCH_PID:-}" ] && kill "$WATCH_PID" 2>/dev/null
  pkill -f "replay_service.py"      2>/dev/null
  pkill -f "eeg_service/main.py"    2>/dev/null
  pkill -f "bridge/server.py"       2>/dev/null
  pkill -f "agent_service/agent.py" 2>/dev/null
  sleep 1
  echo "✅ 已全部停止（Unity 还开着，只是画面不动了）。"
}
# INT/TERM = Control+C、被系统结束
# HUP      = 直接关掉这个终端窗口
# EXIT     = 任何原因退出（兜底，保证不留孤儿进程）
trap cleanup INT TERM HUP EXIT

# 每 3 秒看一眼「Unity 到底接上没有」，状态一变就说一声。
# 为什么需要这个：回放不管有没有人在听都会一直播、一直打印，
# 所以「终端在滚」证明不了 Unity 收到了 —— 必须单独看这一条。
# 数法：一个 WebSocket 连接在 lsof 里算 2 行（桥接端 + 客户端）。
# 只有回放时是 2 行；Unity 也接上就是 4 行。
watch_unity() {
  local last="" n now
  while true; do
    # 只数 Unity 自己的那条连接。注意不能数总行数：回放和 agent_service
    # 也各自连在 8765 上（两条连接 = 4 行），总数早就大于 4 了，
    # 那样会在 Unity 根本没 Play 的时候骗你说"已接上"。
    n=$(lsof -nP -iTCP:8765 2>/dev/null | grep ESTABLISHED | grep -c "^Unity")
    if [ "${n:-0}" -ge 1 ]; then now="on"; else now="off"; fi
    if [ "$now" != "$last" ]; then
      if [ "$now" = "on" ]; then
        echo "🟢 Unity 已接上，画面开始跟着脑电走"
      else
        echo "⚪ 还在等 Unity —— 去 Unity 窗口点 ▶ Play"
      fi
      last="$now"
    fi
    sleep 3
  done
}

# ---------- 找脑电记录文件 ----------
find_newest_bdf() {
  local newest="" newest_t=0 f t
  for f in "$1"/OpenBCI-BDF-*.bdf; do
    [ -f "$f" ] || continue
    t=$(stat -f %m "$f")
    if [ "$t" -gt "$newest_t" ]; then newest_t="$t"; newest="$f"; fi
  done
  echo "$newest"
}

BDF="$BDF_DEFAULT"
[ -f "$BDF" ] || BDF="$(find_newest_bdf "$HOME/Documents/OpenBCI_GUI/Recordings")"

echo "============================================================"
echo "  🌸 NeuroBloom 演示"
echo "============================================================"
echo

# ---------- 1. 清掉残留 ----------
echo "==> 清掉上一次残留的服务"
pkill -f "replay_service.py"      2>/dev/null
pkill -f "eeg_service/main.py"    2>/dev/null
pkill -f "bridge/server.py"       2>/dev/null
pkill -f "agent_service/agent.py" 2>/dev/null
sleep 1

# ---------- 2. 桥接 ----------
echo "==> 启动 桥接 (bridge)"
nohup "$PY" -u bridge/server.py > "$LOGS/bridge.log" 2>&1 &
sleep 2
if ! lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "❌ 桥接没起来，最后几行日志："
  tail -12 "$LOGS/bridge.log"
  echo "按回车关闭…"; read -r; exit 1
fi
echo "    桥接已监听 8765 ✓"

# ---------- 3. 回放（有真实数据就用真实数据） ----------
if [ -n "$BDF" ] && [ -f "$BDF" ]; then
  echo "==> 启动 回放（真实脑电）"
  echo "    文件：$(basename "$BDF")"
  # --round-seconds 10：只在演示里用 10 秒一轮（默认是 20 秒）。
  # 原因是实测出来的：策略是在**轮次边界那一刻**按状态定的，20 秒一轮时
  # 边界几乎全落在「中性」上 → 一直选 P2，而 P2 的四个旋钮都是中间值，
  # 画面上几乎看不出做了什么。10 秒一轮能采到那段偏紧张的窗口，
  # 「轻缓陪伴式（偏暗）」才会真的亮出来 —— 策略层才看得见。
  # 真实使用时保持 20 秒：一套氛围要让人待住，才评得出感受。
  nohup "$PY" -u eeg_service/replay_service.py \
      --file "$BDF" --from 415 --to 510 --speed 2 --loop \
      --round-seconds 10 \
      > "$LOGS/replay.log" 2>&1 &
  SRC="真实脑电回放"
else
  echo "⚠️  没找到 OpenBCI 录的脑电文件，改用合成板假曲线演示。"
  echo "    （这条曲线是编出来的，不含脑电 —— 界面里会一直标注。）"
  DEMO_MODE=1 EEG_BOARD_ID=-1 nohup "$PY" -u eeg_service/main.py \
      > "$LOGS/replay.log" 2>&1 &
  SRC="合成板假曲线（非真实脑电）"
fi
sleep 3
if grep -qE "错误|Traceback" "$LOGS/replay.log" 2>/dev/null; then
  echo "❌ 数据源没起来，日志："
  tail -15 "$LOGS/replay.log"
  echo "按回车关闭…"; read -r; exit 1
fi

# ---------- 4. 大模型话术 ----------
AGENT_MSG="未启动"
if [ "${NBL_NO_AGENT:-0}" = "1" ]; then
  AGENT_MSG="按 NBL_NO_AGENT=1 跳过"
  echo "==> 按 NBL_NO_AGENT=1 跳过 大模型话术"
  echo "    字牌会一直显示开场那句操作提示。"
elif [ -f "$PROJ/.env" ] && grep -q "^SHUSHENG_API_KEY=." "$PROJ/.env"; then
  echo "==> 启动 大模型话术 (agent_service)"
  nohup "$PY" -u agent_service/agent.py > "$LOGS/agent.log" 2>&1 &
  sleep 3
  if grep -q "已连接 bridge world 通道" "$LOGS/agent.log" 2>/dev/null; then
    AGENT_MSG="已接上桥接 ✓（日志 $LOGS/agent.log）"
    echo "    大模型话术已启动 ✓（连不上网会自动用兜底话术）"
  else
    AGENT_MSG="启动了但日志里没看到连接成功，看 $LOGS/agent.log"
    echo "    ⚠️  agent 起来了但没看到连接成功的日志，最后几行："
    tail -5 "$LOGS/agent.log"
  fi
else
  AGENT_MSG="缺 SHUSHENG_API_KEY，已跳过"
  echo "==> ⚠️  .env 里没有 SHUSHENG_API_KEY —— 跳过 大模型话术"
  echo "    字牌会一直显示开场那句操作提示。"
fi

# ---------- 5. Unity ----------
if pgrep -f "$UNITY_APP/Contents/MacOS/Unity" >/dev/null 2>&1; then
  echo "==> Unity 已经开着 —— 但必须切过去点 ▶ Play 才会连上"
else
  echo "==> 正在打开 Unity（第一次打开要等一两分钟）"
  open -a "$UNITY_APP" --args -projectPath "$PROJ/UnityVR"
fi

echo
echo "------------------------------------------------------------"
echo "  数据源：$SRC"
echo "  话术  ：$AGENT_MSG"
echo "  日志  ：$LOGS/replay.log   $LOGS/bridge.log   $LOGS/agent.log"
echo
echo "  👉 Unity 窗口点 ▶ Play 之后，下面会出现『🟢 Unity 已接上』"
echo "  👉 想停下来，就在这个窗口按 Control+C"
echo
echo "  ⚠️  这个项目的『Run In Background』是关的，所以"
echo "     Unity 一失去焦点就会冻住 —— 你盯着这个终端看的时候，"
echo "     画面就是停的。想两个一起看，去 Unity 菜单："
echo "     Edit → Project Settings → Player → 搜 Run In Background → 勾上"
echo "------------------------------------------------------------"
echo

# ---------- 5. 实时显示判定结果 + Unity 连接状态 ----------
watch_unity &
WATCH_PID=$!
tail -F "$LOGS/replay.log"
