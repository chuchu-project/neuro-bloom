#!/bin/bash
# NeuroBloom 一键开始实验
# 双击运行：起全部后台服务（含录制）→ 打开控制台。
# 关掉这个黑色窗口不影响服务；想彻底停止请看最后一行提示。

set -u
# 这个脚本既放在工程里，也被拷到桌面。找项目目录要两头都能找到：
# 先看脚本自己所在处，再看上一层（工程内双击），最后看桌面双击时的常见位置。
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT=""
for cand in "$HERE" "$HERE/.." "$HOME/DeepWorks/NeuroBloom" "$HOME/Documents/NeuroBloom"; do
  if [ -x "$cand/.venv/bin/python" ] && [ -f "$cand/bridge/server.py" ]; then
    ROOT="$(cd "$cand" && pwd)"; break
  fi
done
if [ -z "$ROOT" ]; then
  echo "找不到 NeuroBloom 工程目录（需要里面有 .venv 和 bridge/server.py）。"
  echo "当前脚本位置：$HERE"
  read -n 1 -s -r -p "按任意键关闭…"
  exit 1
fi
STAMP=$(date +%Y-%m-%d_%H-%M-%S)
REC_DIR="$ROOT/.data/rec/$STAMP"

cd "$ROOT" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "找不到 .venv，项目可能被移动过。"
  read -n 1 -s -r -p "按任意键关闭…"; exit 1
fi
mkdir -p .data/logs

echo "==============================================="
echo "  NeuroBloom 开始实验   $STAMP"
echo "  原始脑电会存到: .data/rec/$STAMP"
echo "==============================================="
echo

echo "[1/5] 清掉上一次的服务"
pkill -f "eeg_service/main.py" 2>/dev/null
pkill -f "agent_service/agent.py" 2>/dev/null
pkill -f "bridge/server.py" 2>/dev/null
sleep 1

echo "[2/5] 启动 桥接"
nohup .venv/bin/python -u bridge/server.py > .data/logs/bridge.log 2>&1 &
sleep 2

echo "[3/5] 启动 读脑（板卡 6 · 192.168.4.1:1112 · 边跑边录）"
EEG_BOARD_ID=6 \
EEG_IP=192.168.4.1 \
EEG_IP_PORT=1112 \
EEG_IP_PROTOCOL=2 \
EEG_RECORD_DIR="$REC_DIR" \
nohup .venv/bin/python -u eeg_service/main.py > .data/logs/eeg.log 2>&1 &
sleep 2

echo "[4/5] 启动 智能体"
nohup .venv/bin/python -u agent_service/agent.py > .data/logs/agent.log 2>&1 &
sleep 4

echo "[5/5] 让 Unity 开始播放（画面会自动出现在控制台）"
.venv/bin/python tools/ensure_unity_playing.py

echo
echo "--- 自检 ---"
if pgrep -f "eeg_service/main.py" >/dev/null; then
  echo "脑电进程：在跑"
else
  echo "脑电进程：已退出 ← 多半是板子不在网上（见 .data/logs/eeg.log）"
fi
tail -n 3 .data/logs/eeg.log | sed 's/^/    /'

bash dashboard/ensure-servers.sh >/dev/null 2>&1
open -a "/Users/chujingyue/Applications/NeuroBloom 控制台.app" 2>/dev/null || open "http://127.0.0.1:8899/"

echo
echo "已在 NeuroBloom 窗口打开（没有地址栏）。"
echo "停止全部服务:  pkill -f eeg_service; pkill -f bridge/server.py; pkill -f agent_service"
echo
read -n 1 -s -r -p "按任意键关闭这个窗口（服务继续在后台跑）"
echo
