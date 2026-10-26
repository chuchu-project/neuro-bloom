#!/usr/bin/env bash
# NeuroBloom 查看向导输出：确保服务在跑，然后实时跟踪日志（-F 可自动跟住被替换的文件）
cd "$(dirname "$0")" || exit 1
LOG=".data/logs/agent.log"

if ! pgrep -f "agent_service/agent.py" >/dev/null 2>&1; then
  echo "==> 服务没在跑，先启动…"
  bash "$(dirname "$0")/start.sh" &
  sleep 8
fi

if [ ! -f "$LOG" ]; then
  echo "!! 还没看到日志文件 $LOG"
  echo "   请改用: bash start.sh  查看报错"
  exit 1
fi

echo "文件: $(pwd)/$LOG"
echo "现有内容行数: $(wc -l < "$LOG" | tr -d ' ')"
echo "------------------------------------------------------------"
echo "下面实时滚动；按 Control+C 只是退出查看，服务仍在后台运行。"
echo "------------------------------------------------------------"
tail -F -n 30 "$LOG"
