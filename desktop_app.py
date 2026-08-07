from __future__ import annotations

import json
import socket
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


def available_port() -> int:
    for port in range(8765, 8796):
        if health(port):
            return port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def launch() -> None:
    from server import run_server

    port = remembered_port()
    server_thread = None

    if not port or not health(port):
        port = available_port()

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
            time.sleep(0.2)
        else:
            raise RuntimeError("AI 对话中心未能启动，请重新安装或查看日志。")
        INSTANCE_PATH.write_text(
            json.dumps({"port": port}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    webbrowser.open(f"http://127.0.0.1:{port}/")

    # 保持主进程存活（server 线程是 daemon，主进程退出则全部停止）
    if server_thread and server_thread.is_alive():
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
