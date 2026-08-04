from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

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


def server_command(port: int) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--serve", "--port", str(port)]
    return [sys.executable, str(Path(__file__).resolve()), "--serve", "--port", str(port)]


def start_server(port: int) -> None:
    kwargs = {
        "cwd": str(DATA_DIR),
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(server_command(port), **kwargs)


def launch() -> None:
    port = remembered_port()
    if not port or not health(port):
        port = available_port()
        start_server(port)
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.serve:
        from server import run_server
        run_server(args.port, open_browser=False)
    else:
        launch()


if __name__ == "__main__":
    main()
