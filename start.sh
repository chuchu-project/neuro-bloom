#!/usr/bin/env bash
# NeuroBloom 一键启动：后台拉起三个服务，并实时显示"脑境向导"输出
# 用法: bash start.sh
cd "$(dirname "$0")" || exit 1

if [ ! -d .venv ]; then
  echo "未找到 .venv，请先执行: bash setup/macos-dev-setup.sh"
  exit 1
fi
source .venv/bin/activate
mkdir -p .data/logs

echo "==> 停止上一次的服务（如有）"
pkill -f "eeg_service/main.py" 2>/dev/null
pkill -f "bridge/server.py"    2>/dev/null
pkill -f "agent_service/agent.py" 2>/dev/null
sleep 1

echo "==> 启动 桥接(bridge)"
nohup python -u bridge/server.py > .data/logs/bridge.log 2>&1 &
sleep 2
echo "==> 启动 读脑(eeg)"
nohup python -u eeg_service/main.py > .data/logs/eeg.log 2>&1 &
sleep 1
echo "==> 启动 动脑(agent)"
nohup python -u agent_service/agent.py > .data/logs/agent.log 2>&1 &
sleep 3

echo
echo "✅ 三个服务已在后台运行。"
echo "   下面实时显示『脑境向导』输出（按 Control+C 只是停止查看，服务仍在跑）："
echo "   ------------------------------------------------------------"
tail -F .data/logs/agent.log
