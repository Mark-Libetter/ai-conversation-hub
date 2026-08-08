#!/bin/bash
# AI Conversation Hub · macOS 首次运行引导
# 双击此文件，会弹出图形对话框引导你打开 App（无需打开终端敲命令）。
cd "$(dirname "$0")" || exit 1

APP="AIConversationHub.app"

# 1. 找到 .app
if [ ! -d "$APP" ]; then
  osascript -e 'display dialog "没有找到 AIConversationHub.app。\n\n请确认：你已经把整个压缩包【完整解压】到一个文件夹，然后把这个文件（start-mac.command）和 AIConversationHub.app 放在同一个文件夹里。" with title "AI Conversation Hub" buttons {"好"} default button 1 with icon stop' 2>/dev/null
  exit 1
fi

# 2. 确保有可执行权限（解压后可能丢失）
chmod +x "$APP/Contents/MacOS/AIConversationHub" 2>/dev/null
chmod +x "$APP" 2>/dev/null

# 3. 弹图形对话框，引导用户右键→打开（首次放行 Gatekeeper）
osascript <<'APPLESCRIPT'
tell application "Finder"
    activate
    set folderPath to (POSIX file (do shell script "pwd")) as alias
    open folderPath
    select file "AIConversationHub.app" of folderPath
end tell

display dialog "AI Conversation Hub 已就绪！\n\n接下来：\n1. 在刚打开的文件夹窗口里，找到 AIConversationHub.app\n2. 【右键】点击它（或按住 Control 点一下）\n3. 选「打开」\n4. 弹出提示时，再点「打开」\n\n（首次需要这样操作一次，以后双击就能直接用了）" with title "AI Conversation Hub · 首次运行" buttons {"我知道了"} default button 1 with icon note
APPLESCRIPT

# 4. 用户点完「我知道了」后，直接尝试启动一次（多数情况下右键打开过的能直接起）
open "$APP" 2>/dev/null
