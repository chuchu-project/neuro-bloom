#!/bin/bash
# 双击运行：实时通道体检（边按电极边看哪一路变干净）
cd "$(dirname "$0")/.." || exit 1
export EEG_IP_PORT="${EEG_IP_PORT:-1112}"
export EEG_SECONDS="${EEG_SECONDS:-180}"
echo "目标: ${EEG_IP:-192.168.4.1}:${EEG_IP_PORT}  板卡 ${EEG_BOARD:-6}"
echo "开始前请确认：Mac 已连上 Wi-Fi 热点 ExpandableEEG AP"
echo "按 Ctrl-C 可随时退出。"
echo
./.venv/bin/python tools/eeg_channel_check.py
echo
echo "结束。按回车关闭窗口。"
read -r _
