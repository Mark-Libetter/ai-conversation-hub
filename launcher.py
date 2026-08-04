from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8765/"


def process_options(*, detached: bool = False) -> dict:
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if detached:
            flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        return {"creationflags": flags}
    return {"start_new_session": detached}


def get_json(path: str) -> dict | None:
    try:
        with urllib.request.urlopen(URL.rstrip("/") + path, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None


def reload_running_server() -> bool:
    token_data = get_json("/api/token")
    if not token_data:
        return False
    request = urllib.request.Request(
        URL.rstrip("/") + "/api/reload-sources",
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Hub-Token": str(token_data["token"]),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def main() -> None:
    subprocess.run(
        [sys.executable, str(APP_DIR / "repair_sources.py"), "--quiet"],
        cwd=APP_DIR,
        check=False,
        **process_options(),
    )
    if get_json("/api/sources"):
        reload_running_server()
    else:
        subprocess.Popen(
            [sys.executable, str(APP_DIR / "server.py"), "--no-open"],
            cwd=APP_DIR,
            **process_options(detached=True),
        )
        for _ in range(40):
            if get_json("/api/sources"):
                break
            time.sleep(0.25)
    webbrowser.open(URL)


if __name__ == "__main__":
    main()
