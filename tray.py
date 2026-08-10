# -*- coding: utf-8 -*-
"""AI 对话中心 · 托盘常驻（纯标准库，零第三方依赖）。

双击托盘图标打开中心；右键菜单：打开中心 / 开机自启 / 退出。
服务器未运行时自动拉起（pythonw server.py --no-open）。
独立小进程：崩溃不影响主服务；单实例互斥。
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8765"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

# 64 位下 WPARAM/LPARAM 是 64 位值，必须显式声明，否则转发时按 32 位截断溢出
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_long  # LRESULT

WM_USER = 0x0400
WM_TRAYICON = WM_USER + 20
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_DESTROY = 0x0002
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
TPM_RIGHTALIGN, TPM_RETURNCMD, TPM_NONOTIFY = 0x0008, 0x0100, 0x0040
MF_SEPARATOR, MF_CHECKED, MF_UNCHECKED = 0x0800, 0x0008, 0x0000
IDI_APPLICATION = 32512
IDM_OPEN, IDM_AUTOSTART, IDM_EXIT = 1001, 1002, 1003
DETACHED_PROCESS = 0x00000008

STARTUP_DIR = (
    Path(os.environ.get("APPDATA", ""))
    / r"Microsoft\Windows\Start Menu\Programs\Startup"
)
AUTOSTART_LNK = STARTUP_DIR / "AI Conversation Hub Tray.lnk"


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wt.HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HANDLE),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


def server_alive() -> bool:
    try:
        urllib.request.urlopen(URL + "/api/health", timeout=2).read()
        return True
    except Exception:
        return False


def ensure_server() -> None:
    if server_alive():
        return
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.is_file():
        pyw = Path(sys.executable)
    subprocess.Popen(
        [str(pyw), "server.py", "--no-open"],
        cwd=str(REPO),
        creationflags=DETACHED_PROCESS,
    )


def open_center() -> None:
    ensure_server()
    try:
        os.startfile(URL)  # type: ignore[attr-defined]
    except Exception:
        pass


def autostart_on() -> bool:
    return AUTOSTART_LNK.is_file()


def set_autostart(on: bool) -> None:
    # .lnk 生成借助系统自带 PowerShell/WScript，不引入任何第三方依赖
    if on:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        script = (
            "$ws = New-Object -ComObject WScript.Shell; "
            f"$s = $ws.CreateShortcut('{AUTOSTART_LNK}'); "
            f"$s.TargetPath = '{pyw}'; "
            f"$s.Arguments = '\"{REPO / 'tray.py'}\"'; "
            f"$s.WorkingDirectory = '{REPO}'; $s.Save()"
        )
    else:
        script = f"Remove-Item -LiteralPath '{AUTOSTART_LNK}' -Force -ErrorAction SilentlyContinue"
    subprocess.Popen(
        ["powershell", "-NoProfile", "-Command", script],
        creationflags=DETACHED_PROCESS,
    )


class Tray:
    def __init__(self) -> None:
        self.hwnd = None
        self.nid = None
        self.proc = WNDPROC(self._wnd_proc)  # 防 GC

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAYICON:
            if lparam == WM_LBUTTONDBLCLK:
                open_center()
            elif lparam == WM_RBUTTONUP:
                self._show_menu()
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self) -> None:
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0, IDM_OPEN, "打开对话中心")
        user32.AppendMenuW(
            menu,
            MF_CHECKED if autostart_on() else MF_UNCHECKED,
            IDM_AUTOSTART,
            "开机自启",
        )
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, "")
        user32.AppendMenuW(menu, 0, IDM_EXIT, "退出托盘")
        pos = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pos))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(
            menu,
            TPM_RIGHTALIGN | TPM_RETURNCMD | TPM_NONOTIFY,
            pos.x, pos.y, 0, self.hwnd, None,
        )
        if cmd == IDM_OPEN:
            open_center()
        elif cmd == IDM_AUTOSTART:
            set_autostart(not autostart_on())
        elif cmd == IDM_EXIT:
            user32.PostQuitMessage(0)
        user32.DestroyMenu(menu)

    def run(self) -> None:
        # 单实例互斥
        kernel32.CreateMutexW(None, False, "AIConversationHubTray")
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return

        ensure_server()

        wc = WNDCLASSW()
        wc.lpfnWndProc = self.proc
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = "AIHubTrayMsgClass"
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(
            0, "AIHubTrayMsgClass", "AIHubTray", 0, 0, 0, 0, 0,
            wt.HWND(-3), None, wc.hInstance, None,  # HWND_MESSAGE
        )

        self.nid = NOTIFYICONDATAW()
        self.nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.nid.hWnd = self.hwnd
        self.nid.uID = 1
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.nid.uCallbackMessage = WM_TRAYICON
        self.nid.hIcon = user32.LoadIconW(None, IDI_APPLICATION)
        self.nid.szTip = "AI 对话中心 · 双击打开"
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid))

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))


def main() -> None:
    Tray().run()


if __name__ == "__main__":
    main()
