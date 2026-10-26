#!/usr/bin/env bash
# NeuroBloom 一键停止
cd "$(dirname "$0")" || exit 1
pkill -f "eeg_service/main.py" 2>/dev/null
pkill -f "bridge/server.py"    2>/dev/null
pkill -f "agent_service/agent.py" 2>/dev/null
pkill -f "http.server 8899" 2>/dev/null
echo "已停止所有 NeuroBloom 服务。"
