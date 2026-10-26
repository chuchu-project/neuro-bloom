#!/usr/bin/env bash
# NeuroBloom Mac 开发机一键环境脚本
# 用法: bash setup/macos-dev-setup.sh
set -euo pipefail

echo "==> [1/6] 检查 Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  echo "未检测到 Homebrew，请先安装: https://brew.sh"
  exit 1
fi

echo "==> [2/6] 安装基础工具 (node / python@3.12 / git)"
brew list node        >/dev/null 2>&1 || brew install node
brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12
brew list git         >/dev/null 2>&1 || brew install git

echo "==> [3/6] 创建 Python 虚拟环境 (3.12)"
PY312="$(brew --prefix)/opt/python@3.12/bin/python3.12"
if [ ! -x "$PY312" ]; then
  echo "未找到 python3.12，请先: brew install python@3.12"
  exit 1
fi
"$PY312" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip

echo "==> [4/6] 安装 Python 依赖"
pip install -r requirements.txt

echo "==> [5/6] 安装 Claude Code (开发用 harness)"
if ! command -v claude >/dev/null 2>&1; then
  npm install -g @anthropic-ai/claude-code || echo "Claude Code 安装失败，可稍后手动安装"
fi

echo "==> [6/6] 生成 .env"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "已生成 .env，请填入书生 API Key / Base URL / 模型名"
else
  echo ".env 已存在，跳过"
fi

echo
echo "✅ 环境就绪。接下来："
echo "   source .venv/bin/activate"
echo "   编辑 .env 填入书生大模型密钥"
echo "   可选: docker compose up -d"
echo "   启动: python eeg_service/main.py"
