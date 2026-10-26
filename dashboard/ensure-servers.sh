#!/bin/bash
# 确保「桥接」和「控制台网页」都在运行。已经在跑就什么都不做。
# 由桌面上的 NeuroBloom.app 自动调用，平时不用手动执行。
set -u

cd "$(dirname "$0")/.." || exit 1
ROOT="$PWD"
LSOF=/usr/sbin/lsof
mkdir -p .data/logs

port_up() { "$LSOF" -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# 1) 桥接：脑电和 Unity 都靠它转发
if ! port_up 8765; then
  nohup "$ROOT/.venv/bin/python" -u "$ROOT/bridge/server.py" >> .data/logs/bridge.log 2>&1 &
  echo "已启动 桥接 (8765)"
else
  echo "桥接 已在运行"
fi

# 2) 网页服务：用 http 打开，WebSocket 才不会被 Safari 拦
if ! port_up 8899; then
  nohup /usr/bin/python3 -m http.server 8899 --bind 127.0.0.1 --directory "$ROOT/dashboard" \
    >> .data/logs/dashboard.log 2>&1 &
  echo "已启动 控制台网页 (8899)"
else
  echo "控制台网页 已在运行"
fi

# 给服务一点启动时间，免得浏览器抢先打开拿到空白页
for _ in 1 2 3 4 5 6 7 8 9 10; do
  port_up 8765 && port_up 8899 && exit 0
  sleep 0.4
done
exit 0
