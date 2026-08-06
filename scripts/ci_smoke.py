"""Cross-platform CI smoke test.

Boots the hub headless on a free port, polls /api/health, sanity-checks the
source-adapter discovery helpers, then shuts the server down. Runs identically
on Linux / macOS / Windows runners. Exits non-zero on any failure.
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("HUB_CI_PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8765))


def health() -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=2) as r:
            return __import__("json").loads(r.read().decode("utf-8"))
    except Exception:
        return None


def main() -> int:
    import tempfile

    data_dir = tempfile.mkdtemp(prefix="hub-ci-data-")
    env = dict(os.environ)
    env["CONVERSATION_HUB_DATA_DIR"] = data_dir
    proc = subprocess.Popen(
        [sys.executable, "server.py", "--no-open", "--port", str(PORT)],
        cwd=REPO,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 45
        payload = None
        while time.time() < deadline:
            payload = health()
            if payload and payload.get("app") == "AIConversationHub":
                break
            time.sleep(1)
        if not payload:
            print("FAIL: server did not become healthy")
            return 1
        print("PASS: /api/health ->", payload.get("app"), payload.get("platform"))

        sys.path.insert(0, str(REPO))
        import source_adapters

        assert source_adapters.default_candidates("zcode"), "zcode candidate missing"
        assert source_adapters.default_candidates("qoderwork"), "qoderwork candidate missing"
        print("PASS: adapter discovery helpers return candidates on", sys.platform)
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
