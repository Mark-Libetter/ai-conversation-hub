from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from app_paths import DATA_DIR


INSTANCE_PATH = DATA_DIR / "instance.json"


def health(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return (
                response.status == 200
                and payload.get("app") == "AIConversationHub"
                and str(payload.get("data_dir", "")).casefold() == str(DATA_DIR).casefold()
            )
    except (OSError, ValueError, urllib.error.URLError):
        return False


def remembered_port() -> int | None:
    try:
        payload = json.loads(INSTANCE_PATH.read_text(encoding="utf-8"))
        port = int(payload.get("port", 0))
        return port if 1 <= port <= 65535 else None
    except (OSError, ValueError, TypeError):
        return None


def remember_port(port: int) -> None:
    try:
        INSTANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        INSTANCE_PATH.write_text(
            json.dumps({"port": port}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def running_port() -> int | None:
    """在默认端口段里找一个已经在跑、且数据目录一致的实例；没有则返回 None。"""
    for port in range(8765, 8796):
        if health(port):
            return port
    return None


def free_port() -> int:
    """找一个真正空闲、可以绑定的端口。被占用的端口一律跳过。"""
    for port in range(8765, 8796):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def ensure_firewall_allowed() -> None:
    """首次运行时添加 Windows 防火墙入站规则（仅 Windows，仅首次弹 UAC）。"""
    if sys.platform != "win32":
        return
    exe = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)
    rule_name = "AIConversationHub (Inbound)"
    # 检查规则是否已存在（不需要管理员权限）
    try:
        check = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"],
            capture_output=True, text=True, creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        if check.returncode == 0 and rule_name in (check.stdout or ""):
            return  # 规则已存在，跳过
    except (OSError, subprocess.SubprocessError):
        pass
    # 需要添加规则：用 VBScript 中介提权（弹 UAC，避免黑窗）
    vbs = (
        'Set s=CreateObject("Shell.Application")\n'
        f's.ShellExecute "netsh","advfirewall firewall add rule name=\"{rule_name}\" '
        f'dir=in action=allow program=\"{exe}\" enable=yes profile=any","","runas",0\n'
    )
    vbs_path = os.path.join(os.environ.get("TEMP", "."), "_hub_firewall.vbs")
    try:
        with open(vbs_path, "w", encoding="utf-8") as f:
            f.write(vbs)
        subprocess.run(["wscript", vbs_path], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        pass
    finally:
        try:
            os.remove(vbs_path)
        except OSError:
            pass
    time.sleep(2)


def launch() -> None:
    from server import run_server

    # 已有实例就复用：先认 instance.json 记住的端口，再扫默认端口段。
    # 复用时绝不能再起一个 server 去 bind 同一端口（否则 WinError 10048）。
    port = remembered_port()
    if not port or not health(port):
        port = running_port()

    server_thread = None
    if not port:
        ensure_firewall_allowed()
        port = free_port()

        # 线程内启动 server，不再 spawn 子进程（避免防火墙拦跨进程 TCP）
        server_thread = threading.Thread(
            target=run_server, args=(port,), kwargs={"open_browser": False},
            daemon=True,
        )
        server_thread.start()

        # 等待 server 就绪（进程内 loopback 不被防火墙拦）
        for _ in range(60):
            if health(port):
                break
            if not server_thread.is_alive():
                raise RuntimeError(
                    f"服务线程在启动过程中退出（端口 {port}），请查看上方的错误信息。"
                )
            time.sleep(0.2)
        else:
            raise RuntimeError("AI 对话中心未能启动，请重新安装或查看日志。")

    remember_port(port)
    webbrowser.open(f"http://127.0.0.1:{port}/")

    # 复用已有实例时 server_thread 为 None：浏览器已打开，本进程直接退出即可。
    # 自己启动的实例才需要保持主进程存活（server 线程是 daemon）。
    if server_thread:
        try:
            server_thread.join()
        except KeyboardInterrupt:
            pass


def main() -> None:
    try:
        launch()
    except Exception as exc:  # 让双击失败时窗口停住、能看到原因
        print(f"\n启动失败：{exc}")
        print("排查提示：")
        print("  1. 请先把整个文件夹从压缩包完整解压，再运行 AIConversationHub.exe；")
        print("  2. 若被杀毒软件/SmartScreen 拦截，请选择「仍要运行」或添加信任；")
        print("  3. 确认 _internal 文件夹与 AIConversationHub.exe 在同一目录。")
        try:
            input("\n按回车键退出…")
        except (EOFError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    main()
