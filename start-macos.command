#!/bin/bash
# macOS 双击启动脚本：自动寻找 python3 并启动 AI Conversation Hub Lite。
cd "$(dirname "$0")" || exit 1

PY=""
for candidate in python3 /usr/local/bin/python3 /opt/homebrew/bin/python3 /usr/bin/python3; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done

if [ -z "$PY" ]; then
  osascript -e 'display dialog "未找到 python3，请先安装 Python 3.10+（如 brew install python）。" buttons {"好"} default button 1 with icon stop'
  exit 1
fi

"$PY" launcher.py

# 暂停以便查看报错；正常时浏览器已打开，可直接关闭此窗口。
read -r -p "按回车键关闭窗口…" _
