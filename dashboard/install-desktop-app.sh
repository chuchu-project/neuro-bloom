#!/bin/bash
# 把「NeuroBloom 控制台」装到桌面。
# 项目被移动/删掉之后，重新跑一次这个脚本就能恢复桌面图标。
# 用法: bash dashboard/install-desktop-app.sh
set -eu

cd "$(dirname "$0")/.." || exit 1
ROOT="$PWD"
APP="$HOME/Desktop/NeuroBloom.app"
CMD_NAME="NeuroBloom 开始实验.command"

echo "项目路径: $ROOT"

# 1) 图标（没有就现生成）
if [ ! -f "$ROOT/dashboard/AppIcon.icns" ]; then
  echo "生成图标…"
  "$ROOT/.venv/bin/python" "$ROOT/tools/make_app_icon.py"
fi

# 2) AppleScript 小应用：先确保服务在跑，再打开控制台
echo "打包 $APP"
rm -rf "$APP"
osacompile -o "$APP" \
  -e "do shell script \"/bin/bash '$ROOT/dashboard/ensure-servers.sh'\"" \
  -e 'delay 0.5' \
  -e 'open location "http://127.0.0.1:8899/"'

cp "$ROOT/dashboard/AppIcon.icns" "$APP/Contents/Resources/applet.icns"
# 换过图标之后原来的签名就废了，重新做一次 ad-hoc 签名，否则 macOS 会拒绝打开
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
touch "$APP"

# 3) 一键开始实验
cp "$ROOT/tools/一键实验.command" "$HOME/Desktop/$CMD_NAME"
chmod +x "$HOME/Desktop/$CMD_NAME"

echo
echo "完成："
echo "  桌面 / NeuroBloom.app            ← 双击打开控制台"
echo "  桌面 / $CMD_NAME   ← 双击开始采集并录制"
