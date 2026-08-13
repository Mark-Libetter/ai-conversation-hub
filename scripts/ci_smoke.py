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
        [sys.executable, "server.py", "--no-open", "--no-tray", "--port", str(PORT)],
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
            if (
                payload
                and payload.get("app") == "AIConversationHub"
                and payload.get("index", {}).get("status") in {"ready", "error"}
            ):
                break
            time.sleep(1)
        if not payload:
            print("FAIL: server did not become healthy")
            return 1
        assert payload["index"]["status"] == "ready", payload["index"]
        print("PASS: /api/health ->", payload.get("app"), payload.get("platform"))

        sys.path.insert(0, str(REPO))
        import source_adapters

        assert source_adapters.default_candidates("zcode"), "zcode candidate missing"
        assert source_adapters.default_candidates("qoderwork"), "qoderwork candidate missing"
        print("PASS: adapter discovery helpers return candidates on", sys.platform)

        # macOS 路径形式验证：确认 default_candidates 在 darwin 上走 ~/Library 路径
        if sys.platform == "darwin":
            cands = source_adapters.default_candidates("qoderwork")
            joined = " ".join(str(p) for p in cands)
            assert "Library" in joined or "Application Support" in joined, \
                f"macOS candidates should use ~/Library/Application Support, got: {cands}"
            print("PASS: macOS path form (~/Library/Application Support) confirmed")

        # 深入端点测试：验证 server 不只是活着，而是各只读 API 都能正常响应
        import json as _json
        base = f"http://127.0.0.1:{PORT}"

        def _get(path):
            with urllib.request.urlopen(base + path, timeout=5) as r:
                return _json.loads(r.read().decode("utf-8"))

        src = _get("/api/sources")
        # /api/sources 可能是 list 或 dict（含 sources/items 键），都能处理即可
        src_items = src if isinstance(src, list) else (src.get("sources") or src.get("items") or [])
        print(f"PASS: /api/sources responded with {len(src_items)} source(s)")

        srch = _get("/agent/search?q=test&limit=1")
        assert "results" in srch and "total" in srch, "/agent/search missing keys"
        print(f"PASS: /agent/search responded, total={srch['total']}")

        daily = _get("/agent/daily")
        assert "day" in daily, "/agent/daily missing 'day'"
        print(f"PASS: /agent/daily responded, day={daily['day']}")

        warmup_deadline = time.time() + 20
        while time.time() < warmup_deadline:
            payload = health() or payload
            if payload.get("warmup", {}).get("status") in {"done", "skipped", "error"}:
                break
            time.sleep(0.2)
        assert payload["warmup"]["status"] in {"done", "skipped"}, payload["warmup"]
        assert payload["warmup"]["errors"] == [], payload["warmup"]
        print(f"PASS: startup warmup -> {payload['warmup']['status']}")

        print("ALL PASS on", sys.platform)
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
