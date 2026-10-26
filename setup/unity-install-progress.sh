#!/usr/bin/env bash
# 查看 Unity 编辑器安装进度（适配 Unity 中国镜像下载）
D="$HOME/Library/Application Support/UnityHub/downloads"
E="/Applications/Unity/Hub/Editor"
TOTAL=4724062679   # Unity-6000.0.22f1.pkg 总字节数

echo "时间: $(date '+%H:%M:%S')"
echo "================================"
echo "【结论】"

if ls -d "$E"/*/ >/dev/null 2>&1; then
  for d in "$E"/*/; do echo "  ✅ 已安装: $(basename "$d")  ← 装好了！";
  done
  echo "================================"
  exit 0
fi

F="$D/Unity-6000.0.22f1.pkg"
if [ -f "$F" ]; then
  s=$(stat -f%z "$F" 2>/dev/null)
  echo "  ⏳ 正在下载 Unity 编辑器本体 … $((s*100/TOTAL))%   ($((s/1024/1024)) MB / $((TOTAL/1024/1024)) MB)"
  if [ -f "$D/.speed" ]; then :; fi
  # 估算速度：与两秒后再采样一次比较
  sleep 3
  s2=$(stat -f%z "$F" 2>/dev/null)
  spd=$(( (s2-s)/3 ))
  if [ "$spd" -gt 0 ]; then
    echo "     速度约 $((spd/1024/1024)) MB/s，预计还需 $(( (TOTAL-s2)/spd/60 )) 分钟"
  fi
else
  echo "  ⏳ 尚未开始下载编辑器本体"
fi

echo "================================"
echo "其它正在下载的文件："
ls -lh "$D" 2>/dev/null | awk 'NR>1 && $9!~/.bad/ {print "  "$5"\t"$9}'
echo
echo "（再次运行本脚本即可看到进度变化；出现 ✅ 就是装好了）"
