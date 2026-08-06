"""Companion (mini-program) LAN API.

A narrow, token-gated HTTP listener bound to 0.0.0.0 so a paired phone on the
same Wi-Fi can read *derived, redacted* data (daily review / pending list) and
push capture notes back. Raw conversation bodies never leave the machine.

Disabled by default. Enable from the localhost main server (/api/companion) or
by setting app_settings companion_enabled=1. The companion token is generated
locally and only ever shown on the desktop (localhost), never over the LAN.
"""
from __future__ import annotations

import json
import secrets
import socket
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app_paths import NOTES_DB

DEFAULT_PORT = 8766
LOCAL_TZ = timezone(timedelta(hours=8))

_state: dict[str, Any] = {"server": None, "thread": None, "index": None}
_lock = threading.Lock()


# -------------------------------------------------------------------------- config
def _settings_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(NOTES_DB, timeout=8)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at REAL)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS companion_captures (
          id TEXT PRIMARY KEY,
          day TEXT NOT NULL DEFAULT '',
          text TEXT NOT NULL,
          created_at REAL NOT NULL
        )
        """
    )
    return conn


def _get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def _set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, time.time()),
    )
    conn.commit()


def ensure_token() -> str:
    with _settings_conn() as conn:
        token = _get_setting(conn, "companion_token")
        if not token:
            token = secrets.token_urlsafe(24)
            _set_setting(conn, "companion_token", token)
        return token


def read_config() -> dict[str, Any]:
    with _settings_conn() as conn:
        return {
            "enabled": _get_setting(conn, "companion_enabled", "0") == "1",
            "port": int(_get_setting(conn, "companion_port", str(DEFAULT_PORT)) or DEFAULT_PORT),
            "token": _get_setting(conn, "companion_token", ""),
        }


def set_enabled(enabled: bool) -> None:
    with _settings_conn() as conn:
        _set_setting(conn, "companion_enabled", "1" if enabled else "0")


def regenerate_token() -> str:
    with _settings_conn() as conn:
        token = secrets.token_urlsafe(24)
        _set_setting(conn, "companion_token", token)
        return token


def lan_ip() -> str:
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("223.5.5.5", 80))  # AliDNS; no data is sent.
        ip = probe.getsockname()[0]
        probe.close()
        return ip
    except OSError:
        return "127.0.0.1"


# -------------------------------------------------------------------------- helpers
def _trim(text: Any, limit: int = 300) -> str:
    return str(text or "").strip()[:limit]


def _slim_item(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": entry.get("source"),
        "title": _trim(entry.get("title"), 80),
        "text": _trim(entry.get("text"), 200),
        "status": entry.get("status", ""),
        "conversation_id": entry.get("conversation_id") or entry.get("id") or "",
    }


def slim_daily(raw: dict[str, Any]) -> dict[str, Any]:
    summary = raw.get("summary") or {}
    return {
        "day": raw.get("day"),
        "is_today": bool(raw.get("is_today")),
        "stats": raw.get("stats") or {},
        "overview": _trim(summary.get("overview_sentence") or summary.get("overview"), 400),
        "main_focus": [_slim_item(x) for x in (summary.get("main_focus") or [])[:3]],
        "achievements": [_slim_item(x) for x in (summary.get("achievements") or [])[:5]],
        "unfinished": [_slim_item(x) for x in (summary.get("unfinished") or [])[:8]],
        "next_actions": [_slim_item(x) for x in (summary.get("next_actions") or [])[:5]],
    }


# -------------------------------------------------------------------------- handler
class CompanionHandler(BaseHTTPRequestHandler):
    server_version = "HubCompanion/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # keep the console quiet
        pass

    # -- auth
    def _authorized(self) -> bool:
        token = read_config()["token"]
        provided = self.headers.get("X-Hub-Token") or ""
        if not provided and self.headers.get("Authorization", "").startswith("Bearer "):
            provided = self.headers["Authorization"][7:]
        return bool(token) and secrets.compare_digest(provided, token)

    # -- io
    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, UnicodeDecodeError):
            return {}

    # -- routes
    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/companion/health":
            self._json({"ok": True, "app": "AIConversationHub-companion"})
            return
        if not self._authorized():
            self._json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        index = _state["index"]
        if path == "/companion/daily":
            day = query.get("day") or datetime.now(LOCAL_TZ).date().isoformat()
            self._json({"ok": True, **slim_daily(index.daily_summary(day))})
        elif path == "/companion/pending":
            day = query.get("day") or datetime.now(LOCAL_TZ).date().isoformat()
            summary = (index.daily_summary(day).get("summary")) or {}
            pending = (summary.get("unfinished") or []) + (summary.get("next_actions") or [])
            seen, items = set(), []
            for entry in pending:
                key = (entry.get("source"), entry.get("conversation_id") or entry.get("id"))
                if key in seen:
                    continue
                seen.add(key)
                items.append(_slim_item(entry))
            self._json({"ok": True, "day": day, "items": items[:12]})
        elif path == "/companion/captures":
            with _settings_conn() as conn:
                rows = conn.execute(
                    "SELECT id,day,text,created_at FROM companion_captures ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
            self._json({"ok": True, "items": [dict(r) for r in rows]})
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        if not self._authorized():
            self._json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return
        if parsed.path == "/companion/capture":
            payload = self._body()
            text = _trim(payload.get("text"), 2000)
            if not text:
                self._json({"error": "text required"}, 400)
                return
            capture_id = secrets.token_hex(8)
            with _settings_conn() as conn:
                conn.execute(
                    "INSERT INTO companion_captures(id,day,text,created_at) VALUES(?,?,?,?)",
                    (capture_id, str(payload.get("day") or ""), text, time.time()),
                )
                conn.commit()
            self._json({"ok": True, "id": capture_id})
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)




# -------------------------------------------------------------------------- lifecycle
def start(index: Any, port: int = DEFAULT_PORT) -> bool:
    with _lock:
        if _state["server"] is not None:
            return True
        _state["index"] = index
        try:
            server = ThreadingHTTPServer(("0.0.0.0", port), CompanionHandler)
        except OSError:
            return False
        thread = threading.Thread(target=server.serve_forever, name="hub-companion", daemon=True)
        _state["server"] = server
        _state["thread"] = thread
        thread.start()
        return True


def stop() -> None:
    with _lock:
        server = _state["server"]
        _state["server"] = None
        _state["thread"] = None
    if server is not None:
        server.shutdown()
        server.server_close()


def sync(index: Any) -> dict[str, Any]:
    """Start or stop the listener to match the stored config; return status."""
    config = read_config()
    if config["enabled"]:
        ensure_token()
        start(index, config["port"])
    else:
        stop()
    return status()


def status() -> dict[str, Any]:
    config = read_config()
    return {
        "enabled": config["enabled"],
        "running": _state["server"] is not None,
        "port": config["port"],
        "lan_ip": lan_ip(),
        "token": config["token"] if config["enabled"] else "",
    }
