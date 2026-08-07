from __future__ import annotations

import argparse
import base64
import heapq
import hashlib
import json
import os
import re
import sys

# Let a normal Python installation expose its own pywin32 runtime without
# assuming a specific Python minor version. Frozen builds bundle these DLLs.
if not getattr(sys, "frozen", False):
    try:
        import pywin32_system32  # type: ignore
        os.add_dll_directory(os.path.dirname(os.path.abspath(pywin32_system32.__file__)))
    except (ImportError, OSError, AttributeError, TypeError):
        pass

import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from app_paths import CONFIG_PATH, DATA_DIR, NOTES_DB, RESOURCE_DIR, STATIC_DIR
from repair_sources import atomic_write_config, repair, source_status
from source_adapters import (
    EXTRA_SOURCES,
    SOURCE_LABELS,
    configured_custom_sources,
    configured_extra_sources,
    discover_extra_sources,
    load_custom_source,
    load_extra_source,
)


APP_DIR = RESOURCE_DIR
CORE_SOURCES = ("hermes", "codex", "workbuddy")
SOURCES = CORE_SOURCES + EXTRA_SOURCES
LOCAL_TZ = timezone(timedelta(hours=8))
DAILY_PROMPT_VERSION = 14
HUB_SCHEMA_VERSION = 15
APP_VERSION = "0.20.4"
BACKUP_FORMAT_VERSION = 1
BACKUP_TABLES = (
    "notes", "daily_summaries", "conversation_relations",
)


def load_source_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def configured_path(config: dict[str, Any], key: str, env_name: str, default: Path) -> Path:
    candidates = [os.environ.get(env_name), config.get(key)]
    for candidate in candidates:
        if candidate and Path(candidate).expanduser().exists():
            return Path(candidate).expanduser()
    for candidate in candidates:
        if candidate:
            return Path(candidate).expanduser()
    return DATA_DIR / ".unconfigured" / default.name


SOURCE_CONFIG = load_source_config()


def source_is_enabled(source: str, config: dict[str, Any] | None = None) -> bool:
    selected = config if isinstance(config, dict) else SOURCE_CONFIG
    if source in CORE_SOURCES:
        core_values = selected.get("core_sources")
        if isinstance(core_values, dict) and source in core_values:
            raw = core_values[source]
            if isinstance(raw, dict):
                return bool(raw.get("enabled", True))
            return bool(raw)
        return True
    external = configured_extra_sources(selected, with_counts=False).get(source)
    if external is not None:
        return bool(external.get("enabled"))
    custom = configured_custom_sources(selected, with_counts=False).get(source)
    return bool(custom and custom.get("enabled"))


def reload_source_registry() -> None:
    global SOURCES
    SOURCES = CORE_SOURCES + EXTRA_SOURCES + tuple(
        configured_custom_sources(SOURCE_CONFIG, with_counts=False)
    )


reload_source_registry()
HERMES_DB = configured_path(
    SOURCE_CONFIG,
    "hermes_db",
    "CONVERSATION_HUB_HERMES_DB",
    Path.home() / ".hermes" / "state.db",
)
CODEX_DB = configured_path(
    SOURCE_CONFIG,
    "codex_db",
    "CONVERSATION_HUB_CODEX_DB",
    Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "state_5.sqlite",
)
WORKBUDDY_HOME = configured_path(
    SOURCE_CONFIG,
    "workbuddy_home",
    "WORKBUDDY_HOME",
    Path.home() / ".workbuddy",
)
WORKBUDDY_DB = WORKBUDDY_HOME / "workbuddy.db"
WORKBUDDY_PROJECTS = WORKBUDDY_HOME / "projects"
SUMMARY_API_URL = os.environ.get(
    "CONVERSATION_HUB_SUMMARY_API_URL",
    SOURCE_CONFIG.get("summary_api_url", ""),
).strip()
SUMMARY_API_KEY = os.environ.get("CONVERSATION_HUB_SUMMARY_API_KEY", "").strip()
SUMMARY_MODEL = os.environ.get(
    "CONVERSATION_HUB_SUMMARY_MODEL",
    SOURCE_CONFIG.get("summary_model", ""),
).strip()


def reload_source_paths() -> None:
    global SOURCE_CONFIG, HERMES_DB, CODEX_DB, WORKBUDDY_HOME, WORKBUDDY_DB, WORKBUDDY_PROJECTS
    global SUMMARY_API_URL, SUMMARY_API_KEY, SUMMARY_MODEL
    SOURCE_CONFIG = load_source_config()
    reload_source_registry()
    HERMES_DB = configured_path(
        SOURCE_CONFIG,
        "hermes_db",
        "CONVERSATION_HUB_HERMES_DB",
        Path.home() / ".hermes" / "state.db",
    )
    CODEX_DB = configured_path(
        SOURCE_CONFIG,
        "codex_db",
        "CONVERSATION_HUB_CODEX_DB",
        Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "state_5.sqlite",
    )
    WORKBUDDY_HOME = configured_path(
        SOURCE_CONFIG,
        "workbuddy_home",
        "WORKBUDDY_HOME",
        Path.home() / ".workbuddy",
    )
    WORKBUDDY_DB = WORKBUDDY_HOME / "workbuddy.db"
    WORKBUDDY_PROJECTS = WORKBUDDY_HOME / "projects"
    SUMMARY_API_URL = os.environ.get(
        "CONVERSATION_HUB_SUMMARY_API_URL",
        SOURCE_CONFIG.get("summary_api_url", ""),
    ).strip()
    SUMMARY_API_KEY = os.environ.get("CONVERSATION_HUB_SUMMARY_API_KEY", "").strip()
    SUMMARY_MODEL = os.environ.get(
        "CONVERSATION_HUB_SUMMARY_MODEL",
        SOURCE_CONFIG.get("summary_model", ""),
    ).strip()


def setup_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    selected: dict[str, Any] = dict(config or load_source_config())
    environment_paths = {
        "hermes_db": os.environ.get("CONVERSATION_HUB_HERMES_DB", ""),
        "codex_db": os.environ.get("CONVERSATION_HUB_CODEX_DB", ""),
        "workbuddy_home": os.environ.get("WORKBUDDY_HOME", ""),
    }
    for key, value in environment_paths.items():
        if value:
            selected[key] = value
    status = source_status(selected)
    for source in CORE_SOURCES:
        status[source]["enabled"] = source_is_enabled(source, selected)
    extra_status = configured_extra_sources(selected)
    for source, item in extra_status.items():
        status[source] = {
            "path": item["path"],
            "valid": item["valid"],
            "conversations": item["conversations"],
            "enabled": item["enabled"],
            "detected": item["detected"],
            "detail": item["detail"],
            "label": item["label"],
        }
    custom_status = configured_custom_sources(selected)
    for source, item in custom_status.items():
        status[source] = {
            "path": item["path"],
            "valid": item["valid"],
            "conversations": item["conversations"],
            "enabled": item["enabled"],
            "detected": item["detected"],
            "detail": item["detail"],
            "label": item["label"],
            "format": item["format"],
            "custom": True,
        }
    has_enabled_extra = any(
        item["enabled"] and item["valid"] for item in extra_status.values()
    )
    has_enabled_custom = any(
        item["enabled"] and item["valid"] for item in custom_status.values()
    )
    return {
        "required": not (
            any(
                status[source]["enabled"] and status[source]["valid"]
                for source in CORE_SOURCES
            )
            or has_enabled_extra
            or has_enabled_custom
        ),
        "sources": status,
        "config_path": str(CONFIG_PATH),
        "data_dir": str(DATA_DIR),
        "version": HUB_SCHEMA_VERSION,
        "platform": "macos" if sys.platform == "darwin" else ("windows" if os.name == "nt" else "linux"),
    }


def save_setup(payload: dict[str, Any]) -> dict[str, Any]:
    current: dict[str, Any] = {}
    try:
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            current = loaded
    except (OSError, ValueError, TypeError):
        pass
    for key in ("hermes_db", "codex_db", "workbuddy_home"):
        if key in payload:
            current[key] = clean_text(payload.get(key), 2000)
    if isinstance(payload.get("core_sources"), dict):
        core_values = dict(current.get("core_sources") or {})
        for source in CORE_SOURCES:
            if source in payload["core_sources"]:
                raw = payload["core_sources"][source]
                core_values[source] = bool(
                    raw.get("enabled", True) if isinstance(raw, dict) else raw
                )
        current["core_sources"] = core_values
    if isinstance(payload.get("extra_sources"), dict):
        extra_values: dict[str, dict[str, Any]] = {}
        for source in EXTRA_SOURCES:
            raw = payload["extra_sources"].get(source)
            raw = raw if isinstance(raw, dict) else {}
            extra_values[source] = {
                "enabled": bool(raw.get("enabled", False)),
                "path": clean_text(raw.get("path"), 2000),
            }
        current["extra_sources"] = extra_values
    if isinstance(payload.get("custom_sources"), list):
        custom_values: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in payload["custom_sources"][:50]:
            raw = raw if isinstance(raw, dict) else {}
            source = clean_text(raw.get("id"), 60).casefold()
            if (
                not re.fullmatch(r"custom_[a-z0-9_]{1,48}", source)
                or source in seen
            ):
                raise ValueError("自定义 Agent 标识无效或重复")
            seen.add(source)
            custom_values.append(
                {
                    "id": source,
                    "label": clean_text(raw.get("label"), 80),
                    "format": clean_text(raw.get("format"), 20).casefold(),
                    "path": clean_text(raw.get("path"), 2000),
                    "enabled": bool(raw.get("enabled", False)),
                }
            )
        current["custom_sources"] = custom_values
    current["config_version"] = 4
    status = source_status(current)
    supplied = {
        key: status[key] for key in CORE_SOURCES
        if current.get({"hermes": "hermes_db", "codex": "codex_db", "workbuddy": "workbuddy_home"}[key])
    }
    invalid = [key for key, value in supplied.items() if not value["valid"]]
    if invalid:
        raise ValueError(f"这些数据源路径未通过只读结构验证：{', '.join(invalid)}")
    extra_status = configured_extra_sources(current)
    invalid_extra = [
        source
        for source, value in extra_status.items()
        if value["enabled"] and not value["valid"]
    ]
    if invalid_extra:
        raise ValueError(f"这些扩展数据源路径未通过只读结构验证：{', '.join(invalid_extra)}")
    custom_status = configured_custom_sources(current)
    invalid_custom = [
        item["label"]
        for item in custom_status.values()
        if item["enabled"] and not item["valid"]
    ]
    if invalid_custom:
        raise ValueError(f"这些自定义 Agent 未通过只读结构验证：{', '.join(invalid_custom)}")
    if not (
        any(
            status[source]["valid"] and source_is_enabled(source, current)
            for source in CORE_SOURCES
        )
        or any(value["enabled"] and value["valid"] for value in extra_status.values())
        or any(value["enabled"] and value["valid"] for value in custom_status.values())
    ):
        raise ValueError("至少需要配置一个有效数据源")
    atomic_write_config(current)
    reload_source_paths()
    INDEX.refresh()
    return {"ok": True, **setup_status(current), "summary": INDEX.summary()}


def set_source_enabled(payload: dict[str, Any]) -> dict[str, Any]:
    source = clean_text(payload.get("source"), 60).casefold()
    if source not in SOURCES:
        raise ValueError("未知的数据来源")
    enabled = bool(payload.get("enabled"))
    current = load_source_config()
    if source in CORE_SOURCES:
        core_values = dict(current.get("core_sources") or {})
        core_values[source] = enabled
        current["core_sources"] = core_values
    elif source in EXTRA_SOURCES:
        extra_values = dict(current.get("extra_sources") or {})
        source_values = dict(extra_values.get(source) or {})
        source_values["enabled"] = enabled
        extra_values[source] = source_values
        current["extra_sources"] = extra_values
    else:
        custom_values = list(current.get("custom_sources") or [])
        found = False
        for item in custom_values:
            if isinstance(item, dict) and clean_text(item.get("id"), 60).casefold() == source:
                item["enabled"] = enabled
                found = True
                break
        if not found:
            raise ValueError("未找到自定义数据来源")
        current["custom_sources"] = custom_values
    status = setup_status(current)
    available = [
        key for key, item in status["sources"].items()
        if item.get("enabled") and item.get("valid")
    ]
    if not available:
        raise ValueError("至少需要保留一个有效的数据来源")
    current["config_version"] = max(5, int(current.get("config_version") or 0))
    atomic_write_config(current)
    reload_source_paths()
    INDEX.refresh()
    return {
        "ok": True,
        "source": source,
        "enabled": enabled,
        "enabled_sources": available,
        **INDEX.source_health(),
    }


def discover_setup(extra_roots: list[str]) -> dict[str, Any]:
    discovered = repair(extra_roots, apply=False)
    extras = discover_extra_sources(discovered, extra_roots)
    discovered["extra_sources"] = {
        source: {
            "enabled": bool(item["enabled"]),
            "path": str(item["path"]),
        }
        for source, item in extras.items()
    }
    return setup_status(discovered)


class ClosingConnection(sqlite3.Connection):
    """Commit or roll back like sqlite3.Connection, then release the file handle."""

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def readonly_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro",
        uri=True,
        timeout=8,
        factory=ClosingConnection,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def notes_db() -> sqlite3.Connection:
    conn = sqlite3.connect(NOTES_DB, timeout=8, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
          source TEXT NOT NULL,
          conversation_id TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          tags TEXT NOT NULL DEFAULT '[]',
          user_status TEXT NOT NULL DEFAULT '',
          favorite INTEGER NOT NULL DEFAULT 0,
          updated_at REAL NOT NULL,
          PRIMARY KEY (source, conversation_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS codex_search_meta (
          conversation_id TEXT PRIMARY KEY,
          signature TEXT NOT NULL,
          indexed_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS codex_search USING fts5(
          conversation_id UNINDEXED,
          content,
          tokenize='trigram'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workbuddy_search_meta (
          conversation_id TEXT PRIMARY KEY,
          signature TEXT NOT NULL,
          indexed_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS workbuddy_search USING fts5(
          conversation_id UNINDEXED,
          content,
          tokenize='trigram'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_summaries (
          day TEXT PRIMARY KEY,
          source_hash TEXT NOT NULL,
          summary_json TEXT NOT NULL,
          generator TEXT NOT NULL,
          model TEXT NOT NULL DEFAULT '',
          prompt_version INTEGER NOT NULL,
          manual_note TEXT NOT NULL DEFAULT '',
          generated_at REAL NOT NULL,
          updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_profiles (
          source TEXT PRIMARY KEY,
          adapter_version TEXT NOT NULL DEFAULT '',
          schema_fingerprint TEXT NOT NULL DEFAULT '',
          completeness TEXT NOT NULL DEFAULT 'unknown',
          status TEXT NOT NULL DEFAULT 'unknown',
          conversation_count INTEGER NOT NULL DEFAULT 0,
          message_count INTEGER NOT NULL DEFAULT 0,
          metadata_only_count INTEGER NOT NULL DEFAULT 0,
          excluded_count INTEGER NOT NULL DEFAULT 0,
          detail_json TEXT NOT NULL DEFAULT '{}',
          error TEXT NOT NULL DEFAULT '',
          checked_at REAL NOT NULL,
          updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_relations (
          source_a TEXT NOT NULL,
          conversation_id_a TEXT NOT NULL,
          source_b TEXT NOT NULL,
          conversation_id_b TEXT NOT NULL,
          relation TEXT NOT NULL DEFAULT 'related',
          confidence REAL NOT NULL DEFAULT 0,
          evidence_json TEXT NOT NULL DEFAULT '[]',
          locked INTEGER NOT NULL DEFAULT 0,
          updated_at REAL NOT NULL,
          PRIMARY KEY(source_a,conversation_id_a,source_b,conversation_id_b,relation)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS search_index_state (
          source TEXT PRIMARY KEY,
          source_signature TEXT NOT NULL DEFAULT '',
          conversation_count INTEGER NOT NULL DEFAULT 0,
          message_count INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'pending',
          error TEXT NOT NULL DEFAULT '',
          built_at REAL NOT NULL DEFAULT 0,
          updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS conversation_search USING fts5(
          source UNINDEXED,
          conversation_id UNINDEXED,
          role UNINDEXED,
          content,
          tokenize='unicode61 remove_diacritics 2',
          prefix='2 3'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          description TEXT NOT NULL,
          applied_at REAL NOT NULL
        )
        """
    )

    def ensure_column(table: str, column: str, definition: str) -> None:
        existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_conversation_relations_a
        ON conversation_relations(source_a,conversation_id_a,confidence DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_conversation_relations_b
        ON conversation_relations(source_b,conversation_id_b,confidence DESC)
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO schema_migrations(version,description,applied_at)
        VALUES(12,'portable pluggable conversation source adapters',?)
        """,
        (time.time(),),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO schema_migrations(version,description,applied_at)
        VALUES(13,'source quality, persistent search, relations, backup, and updates',?)
        """,
        (time.time(),),
    )
    conn.execute("PRAGMA optimize")
    conn.execute(f"PRAGMA user_version={HUB_SCHEMA_VERSION}")
    conn.commit()
    return conn


def read_app_settings() -> dict[str, str]:
    with notes_db() as conn:
        return {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM app_settings")}


def secret_storage_label() -> str:
    if sys.platform == "darwin":
        return "macOS 钥匙串（当前用户）"
    if os.name == "nt":
        return "Windows DPAPI（当前用户）"
    return "环境变量"


def validate_api_base(api_url: str, api_key: str = "") -> str:
    url = str(api_url or "").strip().rstrip("/")
    if not url:
        raise ValueError("请填写接口地址")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("接口地址必须是有效的 http:// 或 https:// 地址")
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme == "http" and api_key and hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("带 API 密钥的远程接口必须使用 HTTPS")
    return url


def validate_summary_endpoint(api_url: str, model: str, api_key: str = "") -> tuple[str, str]:
    url = validate_api_base(api_url, api_key)
    model_name = clean_text(model, 200)
    if not model_name:
        raise ValueError("请选择或填写模型名称")
    return url, model_name


def model_capability(model_id: str) -> tuple[str, str, bool]:
    value = model_id.casefold()
    family = next(
        (
            label
            for token, label in (
                ("deepseek", "DeepSeek"),
                ("glm", "GLM"),
                ("qwen", "Qwen"),
                ("qwq", "Qwen"),
                ("doubao", "Doubao"),
                ("minimax", "MiniMax"),
                ("ernie", "ERNIE"),
                ("baichuan", "Baichuan"),
                ("claude", "Claude"),
                ("gemini", "Gemini"),
                ("gpt", "OpenAI"),
                ("llama", "Llama"),
                ("mistral", "Mistral"),
            )
            if token in value
        ),
        "其他",
    )
    if any(token in value for token in ("embedding", "embed", "bge-", "e5-", "text2vec")):
        return family, "embedding", False
    if "rerank" in value:
        return family, "rerank", False
    if any(token in value for token in ("seedance", "hailuo", "t2v", "i2v", "video")):
        return family, "video", False
    if any(token in value for token in ("cogview", "wanx", "flux", "stable-diffusion", "image-gen")):
        return family, "image", False
    if any(token in value for token in ("tts", "asr", "speech", "audio")):
        return family, "audio", False
    if any(token in value for token in ("coder", "code")):
        return family, "coding", True
    if any(token in value for token in ("reasoner", "thinking", "deepseek-r1", "qwq", "glm-z1")):
        return family, "reasoning", True
    if any(token in value for token in ("-vl", "vision", "4.5v")):
        return family, "vision", True
    return family, "text", True


def clean_text(value: Any, limit: int = 800) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def compact_focus_text(value: Any, limit: int = 28) -> str:
    """Turn a title-like focus into a short work theme instead of a copied conversation name."""
    text = clean_text(value, 240)
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", text)
    text = re.sub(r"(?:https?|thread|file)://\S+", " ", text)
    text = text.replace("`", "").replace("*", "")
    text = re.sub(r"^今天(?:的)?工作主要围绕[“「]?|[”」]?展开[。.]?$", "", text)
    text = re.sub(
        r"^(?:请|麻烦|帮我|你帮我|你看一下|看一下|我想|我希望|继续|接着)\s*",
        "",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip(" ：:；;，,。.!！？?\"'“”「」")
    clauses = [
        part.strip(" ：:；;，,。.!！？?\"'“”「」")
        for part in re.split(r"[。！？!?；;，,]\s*", text)
        if len(part.strip()) >= 4
    ]
    if clauses:
        action_terms = (
            "优化", "完善", "修复", "处理", "排查", "调整", "改为", "改成",
            "增加", "新增", "整理", "分析", "验证", "迁移", "折叠", "摘要",
            "总结", "汇总", "搭建", "制作", "接入",
        )
        clauses.sort(
            key=lambda part: (
                any(term in part for term in action_terms),
                -abs(len(part) - 18),
            ),
            reverse=True,
        )
        text = clauses[0]
    text = re.sub(r"^(?:这个|那个|这里|那里)(?:也|还|就)?", "", text)
    text = re.sub(r"(?:可以|能不能|是否|应该|需要)(?:帮忙)?", "", text)
    text = re.sub(r"(?:怎么样|怎么办|怎么做|怎么处理|是什么问题)$", "", text)
    text = re.sub(r"\s+", "", text).strip(" ：:；;，,。.!！？?\"'“”「」")
    trimmed = text[:limit].rstrip(" ：:；;，,。.!！？?\"'“”「」的了地得和与及")
    return trimmed if len(trimmed) >= 4 else "梳理今天的重点工作"


def safe_filename(value: Any, fallback: str = "conversation-export") -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(value or "")).strip(" .-")
    text = re.sub(r"\s+", " ", text)
    return clean_text(text, 80) or fallback


def markdown_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def anonymize_home_paths(text: str) -> str:
    """Replace the local user home directory with ~ so exports do not leak
    machine-specific paths and stay readable in knowledge bases."""
    home = Path.home()
    variants = {str(home), home.as_posix()}
    posix = home.as_posix()
    drive_match = re.match(r"([A-Za-z]):/(.*)", posix)
    if drive_match:
        variants.add(f"/{drive_match.group(1).lower()}/{drive_match.group(2)}")
    for variant in sorted(variants, key=len, reverse=True):
        if variant:
            text = text.replace(variant, "~")
    return text


_EXPORT_WIN_PATH = re.compile(r"(?:[A-Za-z]:|~)\\[^\s`\"'<>|，。；！？、）】」』]+")
_EXPORT_UNIX_PATH = re.compile(
    r"(?<![\w/.])(?:~/(?:[\w.@-]+/)*[\w.@-]+"
    r"|/(?:Users|home|tmp|var|etc|opt|private)/(?:[\w.@-]+/)*[\w.@-]+)"
)


def _wrap_paths_outside_code(line: str) -> str:
    parts = re.split(r"(`[^`]*`)", line)
    for index in range(0, len(parts), 2):
        part = _EXPORT_WIN_PATH.sub(lambda m: f"`{m.group(0)}`", parts[index])
        part = _EXPORT_UNIX_PATH.sub(lambda m: f"`{m.group(0)}`", part)
        parts[index] = part
    return "".join(parts)


def markdown_safe_paths(text: str) -> str:
    """Wrap bare absolute paths in inline code so Markdown backslash escapes
    do not mangle Windows paths when the export lands in notes/knowledge bases."""
    out: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence or stripped.startswith(("\t", "    ")):
            out.append(line)
            continue
        out.append(_wrap_paths_outside_code(line))
    return "\n".join(out)


def _ensure_projects(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_projects (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          created_at REAL NOT NULL DEFAULT 0,
          updated_at REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_project_items (
          project_id TEXT NOT NULL,
          source TEXT NOT NULL,
          conversation_id TEXT NOT NULL,
          added_at REAL NOT NULL DEFAULT 0,
          note TEXT NOT NULL DEFAULT '',
          PRIMARY KEY (project_id, source, conversation_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_project_notes (
          project_id TEXT PRIMARY KEY,
          body TEXT NOT NULL DEFAULT '',
          updated_at REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_project_tasks (
          project_id TEXT NOT NULL,
          id TEXT NOT NULL,
          title TEXT NOT NULL,
          done INTEGER NOT NULL DEFAULT 0,
          created_at REAL NOT NULL DEFAULT 0,
          position INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (project_id, id)
        )
        """
    )
    # 兼容旧表：补 status / note 列
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(user_projects)")}
    if "status" not in cols:
        conn.execute("ALTER TABLE user_projects ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    item_cols = {row["name"] for row in conn.execute("PRAGMA table_info(user_project_items)")}
    if "note" not in item_cols:
        conn.execute("ALTER TABLE user_project_items ADD COLUMN note TEXT NOT NULL DEFAULT ''")


def projects_list() -> list[dict[str, Any]]:
    with notes_db() as conn:
        _ensure_projects(conn)
        rows = conn.execute(
            """
            SELECT p.*,
              (SELECT COUNT(*) FROM user_project_items i WHERE i.project_id = p.id) AS count
            FROM user_projects p ORDER BY p.updated_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def project_detail(project_id: str) -> dict[str, Any]:
    with notes_db() as conn:
        _ensure_projects(conn)
        proj = conn.execute(
            "SELECT * FROM user_projects WHERE id=?", (project_id,)
        ).fetchone()
        if not proj:
            raise ValueError("项目不存在")
        items = conn.execute(
            "SELECT source, conversation_id, added_at, note FROM user_project_items "
            "WHERE project_id=? ORDER BY added_at DESC",
            (project_id,),
        ).fetchall()
        note_row = conn.execute(
            "SELECT body FROM user_project_notes WHERE project_id=?", (project_id,)
        ).fetchone()
        tasks = conn.execute(
            "SELECT id, title, done, created_at, position FROM user_project_tasks "
            "WHERE project_id=? ORDER BY position, created_at",
            (project_id,),
        ).fetchall()
    members = []
    for item in items:
        conv = INDEX._by_key.get((item["source"], item["conversation_id"]))
        members.append(
            {
                "source": item["source"],
                "id": item["conversation_id"],
                "added_at": item["added_at"],
                "note": item["note"] or "",
                "present": bool(conv),
                "title": conv.title if conv else "（已不在索引）",
                "updated_at": conv.updated_at if conv else 0,
                "workspace": conv.workspace if conv else "",
                "message_count": conv.message_count if conv else 0,
            }
        )
    return {
        **dict(proj),
        "items": members,
        "note": note_row["body"] if note_row else "",
        "tasks": [dict(t) for t in tasks],
    }


def projects_mutate(payload: dict[str, Any]) -> dict[str, Any]:
    action = clean_text(payload.get("action"), 20)
    project_id = clean_text(payload.get("id"), 64)
    now = time.time()
    with notes_db() as conn:
        _ensure_projects(conn)
        if action == "create":
            name = clean_text(payload.get("name"), 80)
            if not name:
                raise ValueError("项目需要名字")
            project_id = secrets.token_urlsafe(8)
            conn.execute(
                "INSERT INTO user_projects(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
                (project_id, name, clean_text(payload.get("description"), 2000), now, now),
            )
        elif action == "update":
            if not project_id:
                raise ValueError("缺少项目 id")
            conn.execute(
                "UPDATE user_projects SET name=?, description=?, updated_at=? WHERE id=?",
                (
                    clean_text(payload.get("name"), 80) or "未命名项目",
                    clean_text(payload.get("description"), 2000),
                    now,
                    project_id,
                ),
            )
        elif action == "delete":
            if not project_id:
                raise ValueError("缺少项目 id")
            conn.execute("DELETE FROM user_project_items WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM user_projects WHERE id=?", (project_id,))
        elif action in {"add", "remove"}:
            if not project_id:
                raise ValueError("缺少项目 id")
            if not conn.execute(
                "SELECT 1 FROM user_projects WHERE id=?", (project_id,)
            ).fetchone():
                raise ValueError("项目不存在")
            for entry in payload.get("conversations") or []:
                source = clean_text(entry.get("source"), 40)
                conversation_id = clean_text(entry.get("id"), 200)
                if not source or not conversation_id:
                    continue
                if action == "add":
                    conn.execute(
                        "INSERT OR IGNORE INTO user_project_items(project_id,source,conversation_id,added_at) "
                        "VALUES(?,?,?,?)",
                        (project_id, source, conversation_id, now),
                    )
                else:
                    conn.execute(
                        "DELETE FROM user_project_items WHERE project_id=? AND source=? AND conversation_id=?",
                        (project_id, source, conversation_id),
                    )
            conn.execute(
                "UPDATE user_projects SET updated_at=? WHERE id=?", (now, project_id)
            )
        elif action == "set_status":
            if not project_id:
                raise ValueError("缺少项目 id")
            status = clean_text(payload.get("status"), 20) or "active"
            if status not in ("active", "done", "paused"):
                raise ValueError("无效状态")
            conn.execute(
                "UPDATE user_projects SET status=?, updated_at=? WHERE id=?",
                (status, now, project_id),
            )
        elif action == "save_note":
            if not project_id:
                raise ValueError("缺少项目 id")
            body = clean_text(payload.get("body"), 20000)
            conn.execute(
                "INSERT INTO user_project_notes(project_id, body, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(project_id) DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at",
                (project_id, body, now),
            )
            conn.execute(
                "UPDATE user_projects SET updated_at=? WHERE id=?", (now, project_id)
            )
        elif action == "add_task":
            if not project_id:
                raise ValueError("缺少项目 id")
            title = clean_text(payload.get("title"), 200)
            if not title:
                raise ValueError("任务需要标题")
            task_id = secrets.token_urlsafe(8)
            pos = (conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM user_project_tasks WHERE project_id=?",
                (project_id,)
            ).fetchone()[0])
            conn.execute(
                "INSERT INTO user_project_tasks(project_id, id, title, done, created_at, position) "
                "VALUES(?,?,?,?,?,?)",
                (project_id, task_id, title, 0, now, pos),
            )
            conn.execute(
                "UPDATE user_projects SET updated_at=? WHERE id=?", (now, project_id)
            )
        elif action == "toggle_task":
            if not project_id:
                raise ValueError("缺少项目 id")
            task_id = clean_text(payload.get("task_id"), 64)
            conn.execute(
                "UPDATE user_project_tasks SET done = CASE done WHEN 0 THEN 1 ELSE 0 END "
                "WHERE project_id=? AND id=?",
                (project_id, task_id),
            )
        elif action == "delete_task":
            if not project_id:
                raise ValueError("缺少项目 id")
            task_id = clean_text(payload.get("task_id"), 64)
            conn.execute(
                "DELETE FROM user_project_tasks WHERE project_id=? AND id=?",
                (project_id, task_id),
            )
        elif action == "annotate_item":
            if not project_id:
                raise ValueError("缺少项目 id")
            source = clean_text(payload.get("source"), 40)
            conversation_id = clean_text(payload.get("conversation_id"), 200)
            note = clean_text(payload.get("note"), 500)
            conn.execute(
                "UPDATE user_project_items SET note=? WHERE project_id=? AND source=? AND conversation_id=?",
                (note, project_id, source, conversation_id),
            )
        else:
            raise ValueError("不支持的项目操作")
        conn.commit()
    return {"ok": True, "id": project_id}


def knowledge_tokens(value: Any) -> set[str]:
    text = re.sub(r"\s+", "", str(value or "").casefold())
    words = set(re.findall(r"[a-z0-9][a-z0-9_.-]{1,}", text))
    chinese = re.sub(r"[^\u3400-\u9fff]", "", text)
    words.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return {token for token in words if token}


def text_similarity(left: Any, right: Any) -> float:
    left_tokens = knowledge_tokens(left)
    right_tokens = knowledge_tokens(right)
    if not left_tokens or not right_tokens:
        return 0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


FILE_CATEGORY_EXTENSIONS: dict[str, set[str]] = {
    "code": {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss", ".go",
        ".rs", ".java", ".kt", ".c", ".cpp", ".h", ".hpp", ".cs", ".php", ".rb",
        ".sh", ".ps1", ".sql", ".ipynb",
    },
    "document": {
        ".md", ".txt", ".doc", ".docx", ".pdf", ".ppt", ".pptx", ".xls", ".xlsx",
        ".csv", ".rtf", ".tex",
    },
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff"},
    "data": {".json", ".jsonl", ".xml", ".yaml", ".yml", ".toml", ".parquet", ".feather"},
    "archive": {".zip", ".7z", ".rar", ".tar", ".gz"},
}
FILE_SCAN_SKIP_DIRS = {
    ".git", ".svn", ".hg", ".idea", ".vscode", "node_modules", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".cache", "dist", "build",
    "coverage", ".next", ".nuxt", "target", "vendor", "appdata", "windows",
    "program files", "program files (x86)", "temp",
}
FILE_SCAN_SENSITIVE_NAMES = (
    ".env", "credential", "credentials", "secret", "secrets", "token", "password",
    "private_key", "id_rsa", "id_ed25519",
)
FILE_SCAN_SENSITIVE_EXTENSIONS = {
    ".pem", ".key", ".p12", ".pfx", ".kdbx", ".sqlite", ".sqlite3", ".db", ".ldb",
}


class ConflictError(ValueError):
    pass


def file_category(path: Path) -> str:
    extension = path.suffix.casefold()
    for category, extensions in FILE_CATEGORY_EXTENSIONS.items():
        if extension in extensions:
            return category
    return "other"


def basename(path: str | None) -> str:
    if not path:
        return "无工作区"
    normalized = path.rstrip("\\/")
    return normalized.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] or normalized


_GENERIC_PATH_SEGMENTS = frozenset({
    "", ".", "..", "home", "users", "appdata", "local", "localdata",
    "temp", "tmp", "desktop", "downloads", "documents",
    "programdata", "program files", "program files (x86)",
    "windows", "system32", "var", "etc", "opt", "usr", "bin", "sbin",
    "c:", "d:", "e:", "f:", "g:",
})


def native_project_from_cwd(cwd: str) -> str:
    """Return the Agent's own project folder without inventing a Hub project."""
    if not cwd:
        return ""
    normalized = cwd.replace("\\\\?\\", "").replace("\\", "/").strip()
    segments = [s.strip() for s in normalized.split("/") if s.strip()]
    if not segments:
        return ""
    leaf = segments[-1]
    generic = {
        *_GENERIC_PATH_SEGMENTS,
        Path.home().name.casefold(),
        "inbox_project",
        "codexfiles",
        "workbuddy_inbox",
        "project_new",
        "workspace",
    }
    if (
        leaf.casefold() in generic
        or leaf.replace("-", "").replace(".", "").isnumeric()
    ):
        return ""
    return leaf


def bucket(updated_at: float) -> str:
    days = max(0, (time.time() - updated_at) / 86400)
    if days <= 3:
        return "active"
    if days <= 7:
        return "week"
    if days <= 14:
        return "recent"
    if days <= 30:
        return "archive"
    return "history"


def range_matches(updated_at: float, value: str, now: float | None = None) -> bool:
    if not value or value == "all":
        return True
    current = now if now is not None else time.time()
    if value == "today":
        start = datetime.fromtimestamp(current).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return updated_at >= start
    seconds = {"3d": 3 * 86400, "7d": 7 * 86400, "30d": 30 * 86400}.get(value)
    return seconds is not None and updated_at >= current - seconds


def match_snippet(text: str, query: str, radius: int = 120) -> str:
    value = str(text or "").replace("\x00", "").strip()
    needle = query.casefold().strip()
    if not value or not needle:
        return ""
    index = value.casefold().find(needle)
    if index < 0:
        return clean_text(value, radius * 2)
    start = max(0, index - radius)
    end = min(len(value), index + len(query) + radius)
    prefix = "…" if start else ""
    suffix = "…" if end < len(value) else ""
    return prefix + value[start:end].strip() + suffix


def parse_day(value: str | None) -> tuple[str, float, float]:
    raw = str(value or "").strip()
    try:
        selected = date.fromisoformat(raw) if raw else datetime.now(LOCAL_TZ).date()
    except ValueError as exc:
        raise ValueError("Invalid date; expected YYYY-MM-DD") from exc
    if selected < date(2020, 1, 1) or selected > datetime.now(LOCAL_TZ).date() + timedelta(days=1):
        raise ValueError("Date is outside the supported range")
    start_dt = datetime(selected.year, selected.month, selected.day, tzinfo=LOCAL_TZ)
    return selected.isoformat(), start_dt.timestamp(), (start_dt + timedelta(days=1)).timestamp()


def local_day(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, LOCAL_TZ).date().isoformat()


def sanitize_daily_text(value: Any, role: str, limit: int = 12000) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if not text:
        return ""
    delegated = re.search(r"<codex_delegation>.*?<input>(.*?)</input>.*?</codex_delegation>", text, re.DOTALL)
    if delegated:
        text = delegated.group(1).strip()
    text = re.sub(
        r"<(?:environment_context|recommended_plugins|system-reminder)\b[^>]*>.*?</(?:environment_context|recommended_plugins|system-reminder)>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()
    if role == "user" and (
        text.startswith("<environment_context")
        or text.startswith("<recommended_plugins")
        or text.startswith("# AGENTS.md instructions")
        or text.casefold().startswith("[system:")
        or text.casefold().startswith("system: the active model")
    ):
        return ""
    return clean_text(text, limit)


def claim_text(value: str, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?])\s*", text) if part.strip()]
    preferred = next((part for part in parts if 12 <= len(part) <= limit), parts[0] if parts else text)
    return clean_text(preferred, limit)


SearchNode = tuple[Any, ...]


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF      # CJK 基本区
        or 0x3400 <= code <= 0x4DBF   # 扩展 A
        or 0xF900 <= code <= 0xFAFF   # 兼容表意
        or 0x3040 <= code <= 0x30FF   # 日文假名
        or 0xAC00 <= code <= 0xD7AF   # 谚文
    )


def split_mixed_word(word: str) -> list[str]:
    """把「修复VPN」这类中英混写词按文字边界拆成多段；纯中文/纯西文原样返回。"""
    if not any(_is_cjk(ch) for ch in word):
        return [word]
    parts: list[str] = []
    current: list[str] = []
    mode: str | None = None
    for ch in word:
        ch_mode = "cjk" if _is_cjk(ch) else "other"
        if mode is not None and ch_mode != mode:
            parts.append("".join(current))
            current = []
        mode = ch_mode
        current.append(ch)
    if current:
        parts.append("".join(current))
    # 丢弃纯标点段（如「修复VPN。」里的「。」），保留含文字字符的段
    kept = [p for p in parts if any(ch.isalnum() or _is_cjk(ch) for ch in p)]
    return kept or [word]


def search_tokens(query: str) -> list[tuple[str, str]]:
    value = clean_text(query, 500)
    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(value):
        if value[index].isspace():
            index += 1
            continue
        if value[index] in "()":
            tokens.append((value[index], value[index]))
            index += 1
            continue
        if value[index] == '"':
            index += 1
            phrase: list[str] = []
            while index < len(value) and value[index] != '"':
                if value[index] == "\\" and index + 1 < len(value):
                    index += 1
                phrase.append(value[index])
                index += 1
            if index >= len(value):
                raise ValueError("搜索语法：引号没有闭合")
            index += 1
            text = "".join(phrase).strip()
            if not text:
                raise ValueError("搜索语法：精确短语不能为空")
            tokens.append(("TERM", text.casefold()))
            continue
        end = index
        while end < len(value) and not value[end].isspace() and value[end] not in "()":
            end += 1
        word = value[index:end]
        upper = word.upper()
        if upper in {"AND", "OR", "NOT"}:
            tokens.append((upper, upper))
        elif word.startswith("-") and len(word) > 1:
            tokens.extend((("NOT", "NOT"), ("TERM", word[1:].casefold())))
        else:
            for part in split_mixed_word(word):
                tokens.append(("TERM", part.casefold()))
        index = end
    if len(tokens) > 48:
        raise ValueError("搜索条件过多，请精简到 48 个符号以内")
    return tokens


class SearchParser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.index = 0

    def current(self) -> str:
        return self.tokens[self.index][0] if self.index < len(self.tokens) else "EOF"

    def take(self, kind: str) -> tuple[str, str]:
        if self.current() != kind:
            raise ValueError(f"搜索语法：这里需要 {kind}")
        token = self.tokens[self.index]
        self.index += 1
        return token

    def parse(self) -> SearchNode | None:
        if not self.tokens:
            return None
        node = self.parse_or()
        if self.current() != "EOF":
            raise ValueError("搜索语法：请检查运算符或括号")
        return node

    def parse_or(self) -> SearchNode:
        node = self.parse_and()
        while self.current() == "OR":
            self.take("OR")
            node = ("OR", node, self.parse_and())
        return node

    def parse_and(self) -> SearchNode:
        node = self.parse_not()
        while self.current() in {"AND", "NOT", "TERM", "("}:
            if self.current() == "AND":
                self.take("AND")
            node = ("AND", node, self.parse_not())
        return node

    def parse_not(self) -> SearchNode:
        if self.current() == "NOT":
            self.take("NOT")
            return ("NOT", self.parse_not())
        return self.parse_primary()

    def parse_primary(self) -> SearchNode:
        if self.current() == "TERM":
            return ("TERM", self.take("TERM")[1])
        if self.current() == "(":
            self.take("(")
            node = self.parse_or()
            self.take(")")
            return node
        raise ValueError("搜索语法：运算符后缺少关键词")


def parse_search(query: str) -> SearchNode | None:
    return SearchParser(search_tokens(query)).parse()


def search_terms(node: SearchNode | None, *, positive_only: bool = False) -> list[str]:
    found: list[str] = []

    def visit(value: SearchNode | None, negated: bool = False) -> None:
        if not value:
            return
        if value[0] == "TERM":
            if not positive_only or not negated:
                found.append(str(value[1]))
            return
        if value[0] == "NOT":
            visit(value[1], not negated)
            return
        visit(value[1], negated)
        visit(value[2], negated)

    visit(node)
    return list(dict.fromkeys(found))


def search_matches(node: SearchNode | None, matcher: Any) -> bool:
    if not node:
        return True
    if node[0] == "TERM":
        return bool(matcher(str(node[1])))
    if node[0] == "NOT":
        return not search_matches(node[1], matcher)
    if node[0] == "AND":
        return search_matches(node[1], matcher) and search_matches(node[2], matcher)
    if node[0] == "OR":
        return search_matches(node[1], matcher) or search_matches(node[2], matcher)
    return False


PROJECT_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "ai-conversation-hub",
        "name": "AI 对话中心",
        "keywords": (
            "ai 对话中心",
            "ai对话中心",
            "conversation hub",
            "conversation_hub",
            "对话管理看板",
            "对话指挥中心",
            "每日回顾",
            "摘要模型设置",
            "模型接口配置",
        ),
    },
    {
        "id": "investment-knowledge-base",
        "name": "投资学习知识库",
        "keywords": ("投资学习", "投资知识库", "投研笔记", "obsidian", "知识库搭建"),
    },
    {
        "id": "group-daily-digest",
        "name": "群聊日报",
        "keywords": ("群聊日报", "群日报", "qq 日报", "微信日报", "qce", "聊天摘要"),
    },
    {
        "id": "a-share-toolkit",
        "name": "A股工具箱",
        "keywords": ("a股", "股票工具", "tushare", "通达信", "选股", "模拟交易"),
    },
    {
        "id": "hermes-skill-maintenance",
        "name": "Hermes 技能维护",
        "keywords": ("hermes skill", "hermes 技能", "hermes技能", "技能安装", "技能维护"),
    },
)

WORKSTREAM_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("数据接入", ("接入", "数据源", "workbuddy", "hermes", "codex", "索引")),
    ("搜索整理", ("搜索", "筛选", "标签", "收藏", "备注", "导出", "归类")),
    ("日报摘要", ("日报", "每日回顾", "摘要", "总结", "模版", "模板")),
    ("模型配置", ("模型", "api", "paratera", "ollama", "接口", "密钥")),
    ("UI体验", ("ui", "界面", "布局", "设计", "看板", "指挥中心")),
)

GENERIC_WORKSPACES = {
    "",
    "无工作区",
    "inbox_project",
    "codexfiles",
    "home",
    "desktop",
    "workspace",
}


def auto_project_id(name: str) -> str:
    digest = hashlib.sha1(name.casefold().encode("utf-8")).hexdigest()[:12]
    return f"workspace-{digest}"


def normalized_project_path(value: str) -> str:
    return re.sub(r"/+", "/", str(value or "").replace("\\", "/").strip().casefold()).rstrip("/")


def conversation_workstream(item: "Conversation") -> str:
    text = f"{item.title}\n{item.preview}\n{item.note}".casefold()
    scores = [
        (sum(1 for keyword in keywords if keyword.casefold() in text), name)
        for name, keywords in WORKSTREAM_RULES
    ]
    score, name = max(scores, default=(0, "其他"), key=lambda value: value[0])
    return name if score else "其他"


@dataclass
class Conversation:
    source: str
    id: str
    title: str
    preview: str
    cwd: str
    workspace: str
    created_at: float
    updated_at: float
    message_count: int
    tool_call_count: int
    model: str
    archived: bool
    status: str
    source_kind: str
    rollout_path: str = ""
    parent_id: str = ""
    favorite: bool = False
    user_status: str = ""
    tags: list[str] | None = None
    note: str = ""
    native_project: str = ""


class ConversationIndex:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._refresh_guard = threading.RLock()
        self._items: list[Conversation] = []
        self._by_key: dict[tuple[str, str], Conversation] = {}
        self._excluded_codex_background = 0
        self._external_messages: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._external_source_errors: dict[str, str] = {}
        self._source_signature = ""
        self._last_change_check = 0.0
        self._signature_cache_at = 0.0
        self._signature_cache_value = ""
        self._refresh_in_progress = False
        self._summary_cache = {"key": "", "payload": None}
        self._daily_entries_cache: dict[str, tuple[str, list[dict[str, Any]], str]] = {}
        self._project_detail_cache: dict[str, dict[str, Any]] = {}
        self.refreshed_at = 0.0
        self.refresh()


    @staticmethod
    def _path_fingerprint(path: Path) -> str:
        try:
            stat = path.stat()
            return f"{path}:{stat.st_size}:{stat.st_mtime_ns}"
        except OSError:
            return f"{path}:missing"

    @staticmethod
    def _directory_fingerprint(root: Path, *, patterns: tuple[str, ...] = (), max_files: int = 80) -> str:
        """Cheap change detector for large trees. Avoid full recursive content scans on request path."""
        parts: list[str] = [ConversationIndex._path_fingerprint(root)]
        if not root.is_dir():
            return "|".join(parts)
        try:
            children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            return "|".join(parts)
        for child in children[:120]:
            parts.append(ConversationIndex._path_fingerprint(child))
            if child.is_dir():
                try:
                    grand = sorted(child.iterdir(), key=lambda item: item.name.casefold())[:24]
                except OSError:
                    grand = []
                for item in grand:
                    parts.append(ConversationIndex._path_fingerprint(item))
        if patterns:
            matched = 0
            for pattern in patterns:
                try:
                    for path in root.glob(pattern):
                        parts.append(ConversationIndex._path_fingerprint(path))
                        matched += 1
                        if matched >= max_files:
                            break
                except OSError:
                    continue
                if matched >= max_files:
                    break
        return "|".join(parts)

    def _current_source_signature(self, *, max_age: float = 2.0) -> str:
        now = time.time()
        if self._signature_cache_value and now - self._signature_cache_at < max_age:
            return self._signature_cache_value
        value = self._compute_source_signature()
        self._signature_cache_value = value
        self._signature_cache_at = now
        return value

    @staticmethod
    def _compute_source_signature() -> str:
        values: list[str] = []
        core_paths = {
            "hermes": HERMES_DB,
            "codex": CODEX_DB,
            "workbuddy": WORKBUDDY_DB,
        }
        for source, path in core_paths.items():
            if not source_is_enabled(source):
                continue
            for candidate in (path, Path(f"{path}-wal")):
                values.append(f"{source}:{ConversationIndex._path_fingerprint(candidate)}")
        for source, item in configured_extra_sources(SOURCE_CONFIG, with_counts=False).items():
            if not item["enabled"] or not item["path"]:
                continue
            root = Path(str(item["path"]))
            if root.is_file():
                values.append(f"{source}:{ConversationIndex._path_fingerprint(root)}")
                values.append(f"{source}:{ConversationIndex._path_fingerprint(Path(str(root) + '-wal'))}")
                continue
            if source == "cursor":
                for candidate in (
                    root / "state.vscdb",
                    root / "state.vscdb-wal",
                    root / "conversation-search.db",
                    root / "conversation-search.db-wal",
                ):
                    values.append(f"{source}:{ConversationIndex._path_fingerprint(candidate)}")
            elif source == "qclaw":
                session_root = root / "agents" / "main" / "sessions"
                values.append(
                    f"{source}:{ConversationIndex._directory_fingerprint(session_root, patterns=('*.jsonl',), max_files=40)}"
                )
                values.append(f"{source}:{ConversationIndex._path_fingerprint(session_root / 'sessions.json')}")
            elif source == "claude":
                projects = root / "projects"
                values.append(
                    f"{source}:{ConversationIndex._directory_fingerprint(projects, patterns=('*/sessions-index.json',), max_files=60)}"
                )
                values.append(f"{source}:{ConversationIndex._path_fingerprint(root / 'history.jsonl')}")
            else:
                values.append(f"{source}:{ConversationIndex._directory_fingerprint(root)}")
        for source, item in configured_custom_sources(SOURCE_CONFIG, with_counts=False).items():
            if not item["enabled"] or not item["path"]:
                continue
            root = Path(str(item["path"]))
            format_name = str(item["format"])
            if root.is_file():
                values.append(f"{source}:{ConversationIndex._path_fingerprint(root)}")
                if format_name == "sqlite":
                    values.append(f"{source}:{ConversationIndex._path_fingerprint(Path(str(root) + '-wal'))}")
            elif format_name == "jsonl":
                values.append(
                    f"{source}:{ConversationIndex._directory_fingerprint(root, patterns=('*.jsonl',), max_files=80)}"
                )
            elif format_name == "markdown":
                values.append(
                    f"{source}:{ConversationIndex._directory_fingerprint(root, patterns=('*.md',), max_files=80)}"
                )
            else:
                values.append(f"{source}:{ConversationIndex._directory_fingerprint(root)}")
        return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


    def maybe_refresh(self, min_interval: float = 45.0, *, block: bool = False) -> bool:
        now = time.time()
        with self._refresh_guard:
            if now - self._last_change_check < min_interval:
                return False
            self._last_change_check = now
            signature = self._current_source_signature()
            if signature == self._source_signature:
                return False
            if self._refresh_in_progress:
                return False
            if not block:
                self._refresh_in_progress = True

                def worker() -> None:
                    try:
                        self.refresh()
                    finally:
                        with self._refresh_guard:
                            self._refresh_in_progress = False

                threading.Thread(target=worker, name="hub-source-refresh", daemon=True).start()
                return True
        self.refresh()
        return True

    def refresh(self) -> None:
        with self._refresh_guard:
            items: list[Conversation] = []
            if source_is_enabled("hermes"):
                items.extend(self._load_hermes())
            if source_is_enabled("codex"):
                items.extend(self._load_codex())
            if source_is_enabled("workbuddy"):
                items.extend(self._load_workbuddy())
            external_messages: dict[tuple[str, str], list[dict[str, Any]]] = {}
            external_errors: dict[str, str] = {}
            for source, source_config in configured_extra_sources(
                SOURCE_CONFIG,
                with_counts=False,
            ).items():
                if not source_config["enabled"] or not source_config["valid"]:
                    continue
                records, messages, error = load_extra_source(
                    source,
                    Path(str(source_config["path"])),
                )
                if error:
                    external_errors[source] = error
                for record in records:
                    record["status"] = bucket(float(record.get("updated_at") or 0))
                    items.append(Conversation(**record))
                external_messages.update(
                    {
                        (source, conversation_id): values
                        for conversation_id, values in messages.items()
                    }
                )
            for source, source_config in configured_custom_sources(
                SOURCE_CONFIG,
                with_counts=False,
            ).items():
                if not source_config["enabled"] or not source_config["valid"]:
                    continue
                records, messages, error = load_custom_source(source, source_config)
                if error:
                    external_errors[source] = error
                for record in records:
                    record["status"] = bucket(float(record.get("updated_at") or 0))
                    items.append(Conversation(**record))
                external_messages.update(
                    {
                        (source, conversation_id): values
                        for conversation_id, values in messages.items()
                    }
                )
            if source_is_enabled("codex"):
                self._refresh_codex_search([item for item in items if item.source == "codex"])
            if source_is_enabled("workbuddy"):
                self._refresh_workbuddy_search([item for item in items if item.source == "workbuddy"])
            note_map = self._load_notes()
            for item in items:
                saved = note_map.get((item.source, item.id))
                if saved:
                    item.favorite = bool(saved["favorite"])
                    item.user_status = saved["user_status"]
                    item.tags = json.loads(saved["tags"] or "[]")
                    item.note = saved["note"]
                else:
                    item.tags = []
            items.sort(key=lambda item: item.updated_at, reverse=True)
            with self._lock:
                self._items = items
                self._by_key = {(item.source, item.id): item for item in items}
                self._external_messages = external_messages
                self._external_source_errors = external_errors
                self.refreshed_at = time.time()
                self._summary_cache = {"key": "", "payload": None}
                self._project_detail_cache.clear()
            signature = self._current_source_signature(max_age=0)
            self._source_signature = signature
            self._last_change_check = time.time()
            # Keep the request path light: metadata stays inline; full-text rebuild
            # and relation sync can finish in the background.
            self._refresh_source_profiles(items)
            threading.Thread(
                target=self._background_index_maintenance,
                args=(list(items), signature),
                name="hub-index-maintenance",
                daemon=True,
            ).start()


    def _sync_conversation_relations(self) -> None:
        """Build conservative cross-Agent continuation links in the Hub database.

        lite 版移除了项目归类（project_assignments），跨 Agent 续接链接依赖
        项目分组，因此这里直接返回，不再写入 conversation_relations。
        """
        return

    def _refresh_codex_search(self, items: list[Conversation]) -> None:
        current_ids = {item.id for item in items}
        with notes_db() as conn:
            known = {
                row["conversation_id"]: row["signature"]
                for row in conn.execute("SELECT conversation_id,signature FROM codex_search_meta")
            }
            for stale_id in set(known) - current_ids:
                conn.execute("DELETE FROM codex_search WHERE conversation_id=?", (stale_id,))
                conn.execute("DELETE FROM codex_search_meta WHERE conversation_id=?", (stale_id,))
            for item in items:
                path = Path(item.rollout_path)
                if not item.rollout_path or not path.exists():
                    continue
                stat = path.stat()
                signature = f"safe-v2:{stat.st_size}:{stat.st_mtime_ns}"
                if known.get(item.id) == signature:
                    continue
                content = self._codex_search_text(path)
                conn.execute("DELETE FROM codex_search WHERE conversation_id=?", (item.id,))
                conn.execute(
                    "INSERT INTO codex_search(conversation_id,content) VALUES(?,?)",
                    (item.id, content),
                )
                conn.execute(
                    """
                    INSERT INTO codex_search_meta(conversation_id,signature,indexed_at)
                    VALUES(?,?,?)
                    ON CONFLICT(conversation_id) DO UPDATE SET
                      signature=excluded.signature,indexed_at=excluded.indexed_at
                    """,
                    (item.id, signature, time.time()),
                )
            conn.commit()

    def _refresh_workbuddy_search(self, items: list[Conversation]) -> None:
        current_ids = {item.id for item in items}
        with notes_db() as conn:
            known = {
                row["conversation_id"]: row["signature"]
                for row in conn.execute("SELECT conversation_id,signature FROM workbuddy_search_meta")
            }
            for stale_id in set(known) - current_ids:
                conn.execute("DELETE FROM workbuddy_search WHERE conversation_id=?", (stale_id,))
                conn.execute("DELETE FROM workbuddy_search_meta WHERE conversation_id=?", (stale_id,))
            for item in items:
                path = Path(item.rollout_path)
                if not item.rollout_path or not path.exists():
                    continue
                stat = path.stat()
                signature = f"{stat.st_size}:{stat.st_mtime_ns}"
                if known.get(item.id) == signature:
                    continue
                content = self._workbuddy_search_text(path)
                conn.execute("DELETE FROM workbuddy_search WHERE conversation_id=?", (item.id,))
                conn.execute(
                    "INSERT INTO workbuddy_search(conversation_id,content) VALUES(?,?)",
                    (item.id, content),
                )
                conn.execute(
                    """
                    INSERT INTO workbuddy_search_meta(conversation_id,signature,indexed_at)
                    VALUES(?,?,?)
                    ON CONFLICT(conversation_id) DO UPDATE SET
                      signature=excluded.signature,indexed_at=excluded.indexed_at
                    """,
                    (item.id, signature, time.time()),
                )
            conn.commit()


    def _background_index_maintenance(self, items: list[Conversation], signature: str) -> None:
        try:
            self._refresh_persistent_search(items, signature)
            self._sync_conversation_relations()
        except Exception as exc:  # pragma: no cover - defensive background path
            try:
                with notes_db() as conn:
                    conn.execute(
                        """
                        INSERT INTO search_index_state(
                          source,source_signature,status,error,updated_at
                        ) VALUES('__all__',?,'failed',?,?)
                        ON CONFLICT(source) DO UPDATE SET
                          source_signature=excluded.source_signature,status='failed',
                          error=excluded.error,updated_at=excluded.updated_at
                        """,
                        (signature, clean_text(exc, 800), time.time()),
                    )
                    conn.commit()
            except Exception:
                pass

    def _refresh_persistent_search(
        self,
        items: list[Conversation],
        source_signature: str,
    ) -> None:
        now = time.time()
        try:
            with notes_db() as conn:
                current = conn.execute(
                    "SELECT * FROM search_index_state WHERE source='__all__'"
                ).fetchone()
                if (
                    current
                    and str(current["source_signature"]) == source_signature
                    and int(current["conversation_count"]) == len(items)
                    and str(current["status"]) == "ready"
                ):
                    return
            rows: list[tuple[str, str, str, str]] = []
            source_counts = {
                source: {"conversations": 0, "messages": 0}
                for source in SOURCES
            }
            for item in items:
                source_counts[item.source]["conversations"] += 1
                for message in self._messages_for_item(item, limit=None):
                    text = clean_text(message.get("text"), 20000)
                    if not text:
                        continue
                    rows.append((item.source, item.id, str(message["role"]), text))
                    source_counts[item.source]["messages"] += 1
            with notes_db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM conversation_search")
                conn.executemany(
                    """
                    INSERT INTO conversation_search(source,conversation_id,role,content)
                    VALUES(?,?,?,?)
                    """,
                    rows,
                )
                for source, counts in source_counts.items():
                    conn.execute(
                        """
                        INSERT INTO search_index_state(
                          source,source_signature,conversation_count,message_count,
                          status,error,built_at,updated_at
                        ) VALUES(?,?,?,?, 'ready','',?,?)
                        ON CONFLICT(source) DO UPDATE SET
                          source_signature=excluded.source_signature,
                          conversation_count=excluded.conversation_count,
                          message_count=excluded.message_count,status='ready',error='',
                          built_at=excluded.built_at,updated_at=excluded.updated_at
                        """,
                        (
                            source,
                            source_signature,
                            counts["conversations"],
                            counts["messages"],
                            now,
                            now,
                        ),
                    )
                conn.execute(
                    """
                    INSERT INTO search_index_state(
                      source,source_signature,conversation_count,message_count,
                      status,error,built_at,updated_at
                    ) VALUES('__all__',?,?,?,'ready','',?,?)
                    ON CONFLICT(source) DO UPDATE SET
                      source_signature=excluded.source_signature,
                      conversation_count=excluded.conversation_count,
                      message_count=excluded.message_count,status='ready',error='',
                      built_at=excluded.built_at,updated_at=excluded.updated_at
                    """,
                    (source_signature, len(items), len(rows), now, now),
                )
                conn.commit()
        except (OSError, sqlite3.DatabaseError, ValueError) as exc:
            try:
                with notes_db() as conn:
                    conn.execute(
                        """
                        INSERT INTO search_index_state(
                          source,source_signature,status,error,updated_at
                        ) VALUES('__all__',?,'failed',?,?)
                        ON CONFLICT(source) DO UPDATE SET
                          source_signature=excluded.source_signature,status='failed',
                          error=excluded.error,updated_at=excluded.updated_at
                        """,
                        (source_signature, clean_text(exc, 800), now),
                    )
                    conn.commit()
            except sqlite3.DatabaseError:
                pass

    @staticmethod
    def _sqlite_schema_fingerprint(paths: list[Path]) -> str:
        definitions: list[str] = []
        for path in paths:
            if not path.is_file():
                continue
            try:
                with readonly_db(path) as conn:
                    definitions.extend(
                        f"{path.name}:{row['type']}:{row['name']}:{row['sql'] or ''}"
                        for row in conn.execute(
                            """
                            SELECT type,name,sql FROM sqlite_master
                            WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'
                            ORDER BY type,name
                            """
                        )
                    )
            except sqlite3.DatabaseError:
                continue
        return hashlib.sha256("\n".join(definitions).encode("utf-8")).hexdigest() if definitions else ""

    def _source_schema_fingerprint(self, source: str) -> str:
        if source == "hermes":
            return self._sqlite_schema_fingerprint([HERMES_DB])
        if source == "codex":
            return self._sqlite_schema_fingerprint([CODEX_DB])
        if source == "workbuddy":
            return self._sqlite_schema_fingerprint([WORKBUDDY_DB])
        config = configured_extra_sources(SOURCE_CONFIG, with_counts=False).get(source, {})
        custom_config = configured_custom_sources(SOURCE_CONFIG, with_counts=False).get(source, {})
        if custom_config:
            path = Path(str(custom_config.get("path") or ""))
            if custom_config.get("format") == "sqlite":
                return self._sqlite_schema_fingerprint([path])
            marker = f"custom:{custom_config.get('format')}:{path.suffix.casefold()}"
            return hashlib.sha256(marker.encode("utf-8")).hexdigest()
        path = Path(str(config.get("path") or ""))
        if source == "cursor":
            return self._sqlite_schema_fingerprint(
                [path / "state.vscdb", path / "conversation-search.db"]
            )
        if path.is_file():
            return self._sqlite_schema_fingerprint([path])
        markers: list[str] = []
        if source == "qclaw":
            marker = path / "agents" / "main" / "sessions" / "sessions.json"
            if marker.is_file():
                markers.append("qclaw:sessions.json")
        elif source == "claude":
            markers.extend(["claude:history.jsonl", "claude:projects-jsonl"])
        return hashlib.sha256("\n".join(markers).encode("utf-8")).hexdigest() if markers else ""

    def _refresh_source_profiles(self, items: list[Conversation]) -> None:
        now = time.time()
        grouped = {
            source: [item for item in items if item.source == source]
            for source in SOURCES
        }
        with notes_db() as conn:
            index_counts = {
                str(row["source"]): dict(row)
                for row in conn.execute(
                    "SELECT * FROM search_index_state WHERE source!='__all__'"
                )
            }
            previous = {
                str(row["source"]): dict(row)
                for row in conn.execute("SELECT * FROM source_profiles")
            }
            extra_status = configured_extra_sources(SOURCE_CONFIG, with_counts=False)
            custom_status = configured_custom_sources(SOURCE_CONFIG, with_counts=False)
            external_status = {**extra_status, **custom_status}
            for source in SOURCES:
                source_items = grouped[source]
                metadata_only = sum(
                    1 for item in source_items if "metadata-only" in item.source_kind
                )
                partial_items = sum(
                    1 for item in source_items if "partial" in item.source_kind
                )
                completeness = (
                    "waiting"
                    if not source_items
                    else (
                        "metadata_only"
                        if source_items and metadata_only == len(source_items)
                        else ("partial" if metadata_only or partial_items else "full")
                    )
                )
                fingerprint = self._source_schema_fingerprint(source)
                prior_fingerprint = str(previous.get(source, {}).get("schema_fingerprint") or "")
                schema_changed = bool(
                    prior_fingerprint and fingerprint and prior_fingerprint != fingerprint
                )
                error = self._external_source_errors.get(source, "")
                if not source_is_enabled(source):
                    status = "disabled"
                    completeness = "disabled"
                elif source in external_status:
                    config = external_status.get(source, {})
                    if not config or not config.get("valid"):
                        status = "missing"
                    elif error:
                        status = "error"
                    elif schema_changed:
                        status = "schema_changed"
                    else:
                        status = "healthy"
                else:
                    path_ok = {
                        "hermes": HERMES_DB.exists(),
                        "codex": CODEX_DB.exists(),
                        "workbuddy": WORKBUDDY_DB.exists() and WORKBUDDY_PROJECTS.exists(),
                    }[source]
                    status = "schema_changed" if schema_changed else ("healthy" if path_ok else "missing")
                excluded = self._excluded_codex_background if source == "codex" else 0
                index_row = index_counts.get(source, {})
                details = {
                    "indexed": str(index_row.get("status") or "pending"),
                    "source_kind": sorted({item.source_kind for item in source_items}),
                    "subsource_counts": dict(
                        sorted(Counter(item.source_kind or "unknown" for item in source_items).items())
                    ),
                    "partial_count": partial_items,
                    "schema_changed": schema_changed,
                }
                conn.execute(
                    """
                    INSERT INTO source_profiles(
                      source,adapter_version,schema_fingerprint,completeness,status,
                      conversation_count,message_count,metadata_only_count,excluded_count,
                      detail_json,error,checked_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source) DO UPDATE SET
                      adapter_version=excluded.adapter_version,
                      schema_fingerprint=excluded.schema_fingerprint,
                      completeness=excluded.completeness,status=excluded.status,
                      conversation_count=excluded.conversation_count,
                      message_count=excluded.message_count,
                      metadata_only_count=excluded.metadata_only_count,
                      excluded_count=excluded.excluded_count,
                      detail_json=excluded.detail_json,error=excluded.error,
                      checked_at=excluded.checked_at,updated_at=excluded.updated_at
                    """,
                    (
                        source,
                        "v16",
                        fingerprint,
                        completeness,
                        status,
                        len(source_items),
                        int(index_row.get("message_count") or 0),
                        metadata_only,
                        excluded,
                        json.dumps(details, ensure_ascii=False),
                        clean_text(error, 800),
                        now,
                        now,
                    ),
                )
            conn.commit()

    def _search_persistent_messages(self, source: str, query: str) -> dict[str, str]:
        value = query.strip()
        if not value:
            return {}
        try:
            with notes_db() as conn:
                if len(value) >= 2:
                    rows = conn.execute(
                        """
                        SELECT conversation_id,
                               snippet(conversation_search,3,'','',' … ',36) AS match_text
                        FROM conversation_search
                        WHERE conversation_search MATCH ? AND source=?
                        LIMIT 1200
                        """,
                        (f'"{value.replace(chr(34), chr(34) * 2)}"', source),
                    )
                else:
                    rows = conn.execute(
                        """
                        SELECT conversation_id,
                               substr(content,max(1,instr(lower(content),lower(?))-120),300)
                        FROM conversation_search
                        WHERE source=? AND content LIKE ? LIMIT 1200
                        """,
                        (value, source, f"%{value}%"),
                    )
                matches: dict[str, str] = {}
                for row in rows:
                    matches.setdefault(str(row[0]), clean_text(row[1], 360))
                return matches
        except sqlite3.DatabaseError:
            return {}

    def _codex_search_text(self, path: Path) -> str:
        parts: list[str] = []
        size = 0
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") != "response_item":
                        continue
                    payload = event.get("payload") or {}
                    if payload.get("type") != "message" or payload.get("role") not in {"user", "assistant"}:
                        continue
                    message_parts: list[str] = []
                    for part in payload.get("content") or []:
                        if part.get("type") not in {"input_text", "output_text"} or not part.get("text"):
                            continue
                        message_parts.append(str(part["text"]))
                    text = sanitize_daily_text(
                        "\n".join(message_parts),
                        str(payload.get("role") or ""),
                        20000,
                    )
                    if not text:
                        continue
                    remaining = 2_000_000 - size
                    if remaining <= 0:
                        return "\n".join(parts)
                    text = text[:remaining]
                    parts.append(text)
                    size += len(text)
        except OSError:
            return ""
        return "\n".join(parts)

    def _workbuddy_text_parts(self, event: dict[str, Any]) -> list[str]:
        if event.get("type") != "message" or event.get("role") not in {"user", "assistant"}:
            return []
        allowed = {"input_text"} if event.get("role") == "user" else {"output_text"}
        parts: list[str] = []
        for part in event.get("content") or []:
            if part.get("type") not in allowed or not part.get("text"):
                continue
            text = str(part["text"]).replace("\x00", "")
            if event.get("role") == "user" and "<user_query>" in text:
                text = text.split("<user_query>", 1)[1].split("</user_query>", 1)[0]
            elif event.get("role") == "user" and text.lstrip().startswith("<system-reminder"):
                continue
            text = text.strip()
            if text:
                parts.append(text)
        return parts

    def _workbuddy_search_text(self, path: Path) -> str:
        parts: list[str] = []
        size = 0
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for text in self._workbuddy_text_parts(event):
                        remaining = 2_000_000 - size
                        if remaining <= 0:
                            return "\n".join(parts)
                        value = text[:remaining]
                        parts.append(value)
                        size += len(value)
        except OSError:
            return ""
        return "\n".join(parts)

    def _load_notes(self) -> dict[tuple[str, str], sqlite3.Row]:
        with notes_db() as conn:
            return {(row["source"], row["conversation_id"]): row for row in conn.execute("SELECT * FROM notes")}

    def _load_hermes(self) -> list[Conversation]:
        if not HERMES_DB.exists():
            return []
        sql = """
        WITH last_message AS (
          SELECT session_id, MAX(timestamp) AS updated_at
          FROM messages
          WHERE active = 1
          GROUP BY session_id
        )
        SELECT s.id, s.title, s.cwd, s.started_at, s.ended_at,
               s.message_count, s.tool_call_count, s.model, s.source,
               s.archived, s.parent_session_id, lm.updated_at,
               (
                 SELECT m.content FROM messages m
                 WHERE m.session_id=s.id AND m.role='user' AND m.active=1
                 ORDER BY m.timestamp, m.id LIMIT 1
               ) AS preview
        FROM sessions s
        LEFT JOIN last_message lm ON lm.session_id=s.id
        """
        result: list[Conversation] = []
        with readonly_db(HERMES_DB) as conn:
            for row in conn.execute(sql):
                updated = float(row["updated_at"] or row["ended_at"] or row["started_at"] or 0)
                created = float(row["started_at"] or updated)
                title = clean_text(row["title"], 240) or clean_text(row["preview"], 80) or row["id"]
                cwd = row["cwd"] or ""
                result.append(
                    Conversation(
                        source="hermes",
                        id=row["id"],
                        title=title,
                        preview=clean_text(row["preview"], 420),
                        cwd=cwd,
                        workspace=basename(cwd),
                        created_at=created,
                        updated_at=updated,
                        message_count=int(row["message_count"] or 0),
                        tool_call_count=int(row["tool_call_count"] or 0),
                        model=clean_text(row["model"], 100),
                        archived=bool(row["archived"]),
                        status=bucket(updated),
                       source_kind=clean_text(row["source"], 80),
                       parent_id=row["parent_session_id"] or "",
                       native_project=native_project_from_cwd(cwd),
                    )
                )
        return result

    def _load_codex(self) -> list[Conversation]:
        if not CODEX_DB.exists():
            self._excluded_codex_background = 0
            return []
        result: list[Conversation] = []
        excluded = 0
        with readonly_db(CODEX_DB) as conn:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(threads)")
            }
            optional = [
                "thread_source" if "thread_source" in columns else "'' AS thread_source",
                "agent_path" if "agent_path" in columns else "'' AS agent_path",
                "agent_role" if "agent_role" in columns else "'' AS agent_role",
            ]
            sql = f"""
            SELECT id, title, preview, first_user_message, cwd, created_at, updated_at,
                   created_at_ms, updated_at_ms, archived, source, model, model_provider,
                   rollout_path, {", ".join(optional)}
            FROM threads
            """
            for row in conn.execute(sql):
                raw_source = str(row["source"] or "")
                is_subagent = (
                    '"subagent"' in raw_source.casefold()
                    or (
                        str(row["agent_path"] or "").startswith("/root/")
                        and bool(str(row["agent_role"] or ""))
                    )
                )
                if is_subagent:
                    excluded += 1
                    continue
                updated = float(row["updated_at_ms"] or 0) / 1000 or float(row["updated_at"] or 0)
                created = float(row["created_at_ms"] or 0) / 1000 or float(row["created_at"] or updated)
                raw_preview = row["preview"] or row["first_user_message"]
                preview = sanitize_daily_text(raw_preview, "user", 420) or clean_text(raw_preview, 420)
                raw_title = clean_text(row["title"], 240)
                if raw_title.lstrip().startswith(("<codex_delegation", "<environment_context", "# AGENTS.md")):
                    full_context = sanitize_daily_text(
                        row["first_user_message"] or raw_preview,
                        "user",
                        420,
                    )
                    title = claim_text(full_context, 120) or "Codex 对话"
                else:
                    title = raw_title or clean_text(preview, 80) or row["id"]
                # 仅做格式清理（不改写内容）：
                # 1) markdown 链接保留可见文字：[@文字](thread://...) → 文字
                title = re.sub(r"\[([^\]]*)\]\((?:thread|https?|file)://[^)]+\)", r"\1", title)
                # 2) 去残留的控制字符/多余空白
                title = re.sub(r"[\x00-\x1f]+", " ", title)
                title = re.sub(r"\s+", " ", title).strip(" ：:；;,，。.!！？?\"'“”「」")
                # 3) 超长（>42字）按句号断句取前一段 + 省略号，不硬切
                if len(title) > 42:
                    first_clause = re.split(r"[。！？!？；;]", title)[0]
                    title = (first_clause if len(first_clause) >= 8 else title[:40]).strip()
                    if len(title) > 42:
                        title = title[:40]
                    title += "…"
                cwd = row["cwd"] or ""
                result.append(
                    Conversation(
                        source="codex",
                        id=row["id"],
                        title=title,
                        preview=preview,
                        cwd=cwd,
                        workspace=basename(cwd),
                        created_at=created,
                        updated_at=updated,
                        message_count=0,
                        tool_call_count=0,
                        model=clean_text(row["model"] or row["model_provider"], 100),
                        archived=bool(row["archived"]),
                        status=bucket(updated),
                        source_kind=clean_text(row["source"], 80),
                        rollout_path=row["rollout_path"] or "",
                        native_project=native_project_from_cwd(cwd),
                    )
                )
        self._excluded_codex_background = excluded
        return result

    def _workbuddy_file_metadata(self, path: Path) -> tuple[str, int]:
        preview = ""
        message_count = 0
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    parts = self._workbuddy_text_parts(event)
                    if not parts:
                        continue
                    message_count += 1
                    if not preview and event.get("role") == "user":
                        preview = clean_text("\n".join(parts), 420)
        except OSError:
            pass
        return preview, message_count

    def _workbuddy_preview(self, path: Path) -> str:
        return self._workbuddy_file_metadata(path)[0]

    @staticmethod
    def _workbuddy_source_kind(cwd: str, path: Path, fallback: str) -> str:
        workspace = basename(cwd).casefold()
        project_folder = path.parent.name.casefold()
        if workspace == "claw" or project_folder.endswith("-claw"):
            return "assistant"
        return clean_text(fallback, 80)

    def _load_workbuddy(self) -> list[Conversation]:
        if not WORKBUDDY_DB.exists() or not WORKBUDDY_PROJECTS.exists():
            return []
        session_files = {
            path.stem: path
            for project in WORKBUDDY_PROJECTS.iterdir()
            if project.is_dir()
            for path in project.glob("*.jsonl")
            if path.is_file()
        }
        sql = """
        SELECT id, cwd, title, custom_title, status, created_at, updated_at,
               last_activity_at, model, source_mode
        FROM sessions
        WHERE deleted_at IS NULL
          AND COALESCE(is_background_automation, 0)=0
        """
        result: list[Conversation] = []
        with readonly_db(WORKBUDDY_DB) as conn:
            for row in conn.execute(sql):
                path = session_files.get(row["id"])
                if not path:
                    continue
                updated_ms = float(row["last_activity_at"] or row["updated_at"] or 0)
                created_ms = float(row["created_at"] or updated_ms)
                updated = updated_ms / 1000 if updated_ms > 10_000_000_000 else updated_ms
                created = created_ms / 1000 if created_ms > 10_000_000_000 else created_ms
                preview, message_count = self._workbuddy_file_metadata(path)
                title = clean_text(row["custom_title"] or row["title"], 240) or clean_text(preview, 80) or row["id"]
                cwd = row["cwd"] or ""
                result.append(
                    Conversation(
                        source="workbuddy",
                        id=row["id"],
                        title=title,
                        preview=preview,
                        cwd=cwd,
                        workspace=basename(cwd),
                        created_at=created,
                        updated_at=updated,
                        message_count=message_count,
                        tool_call_count=0,
                        model=clean_text(row["model"], 100),
                        archived=False,
                        status=bucket(updated),
                        source_kind=self._workbuddy_source_kind(
                            cwd,
                            path,
                            row["source_mode"] or row["status"],
                        ),
                        rollout_path=str(path),
                        native_project=path.parent.name,
                    )
                )
        return result

    def list(
        self,
        source: str,
        query: str,
        time_range: str,
        status: str,
        workspace: str,
        native_project: str,
        favorites: bool,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        query_node = parse_search(query)
        all_query_terms = search_terms(query_node)
        positive_terms = search_terms(query_node, positive_only=True)
        body_matches: dict[str, dict[str, dict[str, str]]] = {
            source_name: {} for source_name in SOURCES
        }
        for term in all_query_terms:
            for source_name in SOURCES:
                if source in {"all", source_name}:
                    body_matches[source_name][term] = self._search_persistent_messages(
                        source_name,
                        term,
                    )
        with self._lock:
            items = list(self._items)
        filtered: list[Conversation] = []
        for item in items:
            if source != "all" and item.source != source:
                continue
            if not range_matches(item.updated_at, time_range):
                continue
            if status and status != "all" and item.user_status != status:
                continue
            if workspace and workspace != "all" and item.workspace != workspace:
                continue
            if (
                native_project
                and native_project != "all"
                and item.native_project != native_project
            ):
                continue
            if favorites and not item.favorite:
                continue
            if query_node:
                haystack = "\n".join(
                    [
                        item.title, item.preview, item.cwd, item.workspace, item.note,
                        " ".join(item.tags or []), item.source, item.model,
                    ]
                ).casefold()
                if not search_matches(
                    query_node,
                    lambda term: term in haystack or item.id in body_matches[item.source].get(term, {}),
                ):
                    continue
            filtered.append(item)
        page = filtered[offset : offset + limit]
        result_items: list[dict[str, Any]] = []
        for item in page:
            value = asdict(item)
            snippet = ""
            if query_node:
                for term in positive_terms:
                    snippet = body_matches[item.source].get(term, {}).get(item.id, "")
                    if snippet:
                        break
                if not snippet:
                    for term in positive_terms:
                        for candidate in (
                            item.title, item.preview, item.note, " ".join(item.tags or []), item.cwd
                        ):
                            if term in candidate.casefold():
                                snippet = match_snippet(candidate, term)
                                break
                        if snippet:
                            break
            value["match_snippet"] = clean_text(snippet, 360)
            result_items.append(value)
        return {
            "items": result_items,
            "total": len(filtered),
            "offset": offset,
            "limit": limit,
            "refreshed_at": self.refreshed_at,
            "query_terms": positive_terms,
        }

    def _search_hermes_messages(self, query: str) -> dict[str, str]:
        if not HERMES_DB.exists() or not query.strip():
            return {}
        value = query.strip()
        try:
            with readonly_db(HERMES_DB) as conn:
                if len(value) >= 3:
                    rows = conn.execute(
                        """
                        SELECT m.session_id, m.content
                        FROM messages_fts_trigram f
                        JOIN messages m ON m.id=f.rowid
                        WHERE messages_fts_trigram MATCH ? AND m.role IN ('user','assistant')
                        ORDER BY m.timestamp DESC
                        LIMIT 1000
                        """,
                        (f'"{value.replace(chr(34), chr(34) * 2)}"',),
                    )
                else:
                    rows = conn.execute(
                        """
                        SELECT session_id, content FROM messages
                        WHERE role IN ('user','assistant') AND content LIKE ?
                        ORDER BY timestamp DESC
                        LIMIT 1000
                        """,
                        (f"%{value}%",),
                    )
                matches: dict[str, str] = {}
                for row in rows:
                    matches.setdefault(row[0], match_snippet(row[1], value))
                return matches
        except sqlite3.DatabaseError:
            return {}

    def _search_codex_messages(self, query: str) -> dict[str, str]:
        value = query.strip()
        if not value:
            return {}
        try:
            with notes_db() as conn:
                if len(value) >= 3:
                    rows = conn.execute(
                        """
                        SELECT conversation_id,
                               snippet(codex_search, 1, '', '', ' … ', 36) AS match_text
                        FROM codex_search
                        WHERE codex_search MATCH ? LIMIT 1000
                        """,
                        (f'"{value.replace(chr(34), chr(34) * 2)}"',),
                    )
                else:
                    rows = conn.execute(
                        """
                        SELECT conversation_id,
                               substr(content, max(1, instr(lower(content), lower(?)) - 120), 300) AS match_text
                        FROM codex_search WHERE content LIKE ? LIMIT 1000
                        """,
                        (value, f"%{value}%"),
                    )
                return {row[0]: clean_text(row[1], 360) for row in rows}
        except sqlite3.DatabaseError:
            return {}

    def _search_workbuddy_messages(self, query: str) -> dict[str, str]:
        value = query.strip()
        if not value:
            return {}
        try:
            with notes_db() as conn:
                if len(value) >= 3:
                    rows = conn.execute(
                        """
                        SELECT conversation_id,
                               snippet(workbuddy_search, 1, '', '', ' … ', 36) AS match_text
                        FROM workbuddy_search
                        WHERE workbuddy_search MATCH ? LIMIT 1000
                        """,
                        (f'"{value.replace(chr(34), chr(34) * 2)}"',),
                    )
                else:
                    rows = conn.execute(
                        """
                        SELECT conversation_id,
                               substr(content, max(1, instr(lower(content), lower(?)) - 120), 300) AS match_text
                        FROM workbuddy_search WHERE content LIKE ? LIMIT 1000
                        """,
                        (value, f"%{value}%"),
                    )
                return {row[0]: clean_text(row[1], 360) for row in rows}
        except sqlite3.DatabaseError:
            return {}

    def _search_external_messages(self, source: str, query: str) -> dict[str, str]:
        value = query.strip().casefold()
        if not value:
            return {}
        with self._lock:
            rows = [
                (conversation_id, list(messages))
                for (message_source, conversation_id), messages in self._external_messages.items()
                if message_source == source
            ]
        matches: dict[str, str] = {}
        for conversation_id, messages in rows:
            for message in messages:
                text = str(message.get("text") or "")
                if value in text.casefold():
                    matches[conversation_id] = match_snippet(text, query)
                    break
        return matches

    def get(self, source: str, conversation_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._by_key.get((source, conversation_id))
        if not item:
            return None
        messages = self._messages_for_item(item, limit=40)
        first_user = item.preview if source == "workbuddy" else next(
            (m["text"] for m in messages if m["role"] == "user"),
            item.preview,
        )
        latest_user = next((m["text"] for m in reversed(messages) if m["role"] == "user"), "")
        latest_assistant = next((m["text"] for m in reversed(messages) if m["role"] == "assistant"), "")
        with notes_db() as conn:
            try:
                assignment = conn.execute(
                    "SELECT * FROM project_assignments WHERE source=? AND conversation_id=?",
                    (source, conversation_id),
                ).fetchone()
            except sqlite3.OperationalError:
                assignment = None
            try:
                relation_rows = list(
                    conn.execute(
                        """
                        SELECT * FROM conversation_relations
                        WHERE (source_a=? AND conversation_id_a=?)
                           OR (source_b=? AND conversation_id_b=?)
                        ORDER BY confidence DESC
                        """,
                        (source, conversation_id, source, conversation_id),
                    )
                )
            except sqlite3.OperationalError:
                relation_rows = []
        related: list[dict[str, Any]] = []
        with self._lock:
            for row in relation_rows:
                if row["source_a"] == source and row["conversation_id_a"] == conversation_id:
                    other_key = (str(row["source_b"]), str(row["conversation_id_b"]))
                else:
                    other_key = (str(row["source_a"]), str(row["conversation_id_a"]))
                other = self._by_key.get(other_key)
                if not other:
                    continue
                try:
                    evidence = json.loads(row["evidence_json"] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    evidence = {}
                related.append(
                    {
                        "source": other.source,
                        "id": other.id,
                        "title": other.title,
                        "workspace": other.workspace,
                        "updated_at": other.updated_at,
                        "relation": str(row["relation"]),
                        "confidence": float(row["confidence"]),
                        "evidence": evidence,
                    }
                )
        return {
            "conversation": asdict(item),
            "messages": messages[-16:],
            "project_assignment": dict(assignment) if assignment else None,
            "related_conversations": related,
            "overview": {
                "goal": clean_text(first_user, 700),
                "latest_request": clean_text(latest_user, 700),
                "latest_response": clean_text(latest_assistant, 900),
            },
        }

    def conversation_messages(
        self,
        source: str,
        conversation_id: str,
        limit: int = 300,
    ) -> dict[str, Any] | None:
        with self._lock:
            item = self._by_key.get((source, conversation_id))
        if not item:
            return None
        safe_limit = min(500, max(40, int(limit)))
        messages = self._messages_for_item(item, limit=safe_limit)
        return {
            "source": source,
            "conversation_id": conversation_id,
            "messages": messages,
            "returned": len(messages),
            "estimated_total": int(item.message_count or len(messages)),
            "truncated": int(item.message_count or 0) > len(messages),
        }

    def _hermes_messages(
        self,
        session_id: str,
        start: float | None = None,
        end: float | None = None,
        limit: int | None = 40,
    ) -> list[dict[str, Any]]:
        clauses = [
            "session_id=?",
            "active=1",
            "role IN ('user','assistant')",
            "content IS NOT NULL",
            "trim(content) <> ''",
        ]
        values: list[Any] = [session_id]
        if start is not None:
            clauses.append("timestamp>=?")
            values.append(start)
        if end is not None:
            clauses.append("timestamp<?")
            values.append(end)
        suffix = f" LIMIT {int(limit)}" if limit else ""
        with readonly_db(HERMES_DB) as conn:
            rows = conn.execute(
                f"""
                SELECT role, content, timestamp FROM messages
                WHERE {' AND '.join(clauses)}
                ORDER BY timestamp DESC, id DESC{suffix}
                """,
                values,
            ).fetchall()[::-1]
        return [
            {"role": row["role"], "text": clean_text(row["content"], 5000), "timestamp": float(row["timestamp"])}
            for row in rows
        ]

    def _codex_messages(
        self,
        rollout_path: str,
        start: float | None = None,
        end: float | None = None,
        limit: int | None = 40,
    ) -> list[dict[str, Any]]:
        path = Path(rollout_path)
        if not rollout_path or not path.exists():
            return []
        messages: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") != "response_item":
                        continue
                    payload = event.get("payload") or {}
                    if payload.get("type") != "message" or payload.get("role") not in {"user", "assistant"}:
                        continue
                    parts = []
                    for part in payload.get("content") or []:
                        if part.get("type") in {"input_text", "output_text"} and part.get("text"):
                            parts.append(part["text"])
                    text = clean_text("\n".join(parts), 5000)
                    if text:
                        timestamp = event.get("timestamp")
                        try:
                            ts = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).timestamp()
                        except (TypeError, ValueError):
                            ts = 0.0
                        if start is not None and ts < start:
                            continue
                        if end is not None and ts >= end:
                            continue
                        messages.append({"role": payload["role"], "text": text, "timestamp": ts})
        except OSError:
            return []
        return messages[-limit:] if limit else messages

    def _workbuddy_messages(
        self,
        rollout_path: str,
        start: float | None = None,
        end: float | None = None,
        limit: int | None = 80,
    ) -> list[dict[str, Any]]:
        path = Path(rollout_path)
        if not rollout_path or not path.exists():
            return []
        messages: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    role = event.get("role")
                    parts = self._workbuddy_text_parts(event)
                    if role not in {"user", "assistant"} or not parts:
                        continue
                    timestamp = float(event.get("timestamp") or 0)
                    if timestamp > 10_000_000_000:
                        timestamp /= 1000
                    if start is not None and timestamp < start:
                        continue
                    if end is not None and timestamp >= end:
                        continue
                    messages.append(
                        {
                            "role": role,
                            "text": clean_text("\n".join(parts), 5000),
                            "timestamp": timestamp,
                        }
                    )
        except OSError:
            return []
        return messages[-limit:] if limit else messages

    def _external_messages_for_item(
        self,
        item: Conversation,
        start: float | None = None,
        end: float | None = None,
        limit: int | None = 80,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._external_messages.get((item.source, item.id), []))
        messages = [
            {
                "role": str(message.get("role") or ""),
                "text": clean_text(str(message.get("text") or ""), 5000),
                "timestamp": float(message.get("timestamp") or 0),
            }
            for message in rows
            if str(message.get("role") or "") in {"user", "assistant"}
            and (start is None or float(message.get("timestamp") or 0) >= start)
            and (end is None or float(message.get("timestamp") or 0) < end)
        ]
        return messages[-limit:] if limit else messages


    def _daily_light_signature(self, day_value: str | None) -> tuple[str, list[Conversation], str]:
        day, start, end = parse_day(day_value)
        with self._lock:
            candidates = [
                item
                for item in self._items
                if item.created_at < end and item.updated_at >= start
            ]
        parts = [day, str(DAILY_PROMPT_VERSION)]
        for item in candidates:
            parts.extend(
                [
                    item.source,
                    item.id,
                    f"{item.updated_at:.6f}",
                    str(item.message_count),
                    item.user_status or "",
                    item.note or "",
                    item.title or "",
                    item.source_kind or "",
                ]
            )
        return day, candidates, hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

    def _daily_entries_for_candidates(
        self,
        day: str,
        candidates: list[Conversation],
        start: float,
        end: float,
    ) -> tuple[list[dict[str, Any]], str]:
        entries: list[dict[str, Any]] = []
        signature_parts: list[str] = [day, str(DAILY_PROMPT_VERSION)]
        for item in candidates:
            if item.source == "hermes":
                messages = self._hermes_messages(item.id, start, end, None)
            elif item.source == "codex":
                messages = self._codex_messages(item.rollout_path, start, end, None)
            elif item.source == "workbuddy":
                messages = self._workbuddy_messages(item.rollout_path, start, end, None)
            else:
                messages = self._external_messages_for_item(item, start, end, None)
            safe_messages = []
            for message in messages:
                text_value = sanitize_daily_text(message.get("text"), str(message.get("role") or ""))
                if not text_value:
                    continue
                digest = hashlib.sha1(text_value.encode("utf-8", errors="ignore")).hexdigest()[:16]
                safe = {
                    "role": message["role"],
                    "text": text_value,
                    "timestamp": float(message.get("timestamp") or 0),
                }
                safe_messages.append(safe)
                signature_parts.extend(
                    [item.source, item.id, safe["role"], f"{safe['timestamp']:.6f}", digest]
                )
            if not safe_messages:
                continue
            latest_user = next(
                (message["text"] for message in reversed(safe_messages) if message["role"] == "user"),
                "",
            )
            latest_assistant = next(
                (message["text"] for message in reversed(safe_messages) if message["role"] == "assistant"),
                "",
            )
            title = item.title
            if title.lstrip().startswith(("<codex_delegation", "<environment_context", "# AGENTS.md")):
                title = claim_text(latest_user, 120) or f"{item.source} 对话"
            entries.append(
                {
                    "source": item.source,
                    "source_kind": item.source_kind,
                    "evidence_level": (
                        "metadata_only"
                        if "metadata-only" in item.source_kind
                        else ("partial" if "partial" in item.source_kind else "full")
                    ),
                    "id": item.id,
                    "title": title,
                    "workspace": item.workspace,
                    "updated_at": item.updated_at,
                    "user_status": item.user_status,
                    "note": item.note,
                    "messages": safe_messages,
                    "latest_user": latest_user,
                    "latest_assistant": latest_assistant,
                    "last_role": safe_messages[-1]["role"],
                }
            )
            signature_parts.extend([item.user_status, item.note])
        entries.sort(key=lambda entry: entry["updated_at"], reverse=True)
        source_hash = hashlib.sha256("\n".join(signature_parts).encode("utf-8")).hexdigest()
        return entries, source_hash

    def _daily_entries(self, day_value: str | None) -> tuple[str, list[dict[str, Any]], str]:
        day, start, end = parse_day(day_value)
        with self._lock:
            candidates = [
                item
                for item in self._items
                if item.created_at < end and item.updated_at >= start
            ]
        entries, source_hash = self._daily_entries_for_candidates(day, candidates, start, end)
        return day, entries, source_hash

    def _daily_entries_cached(self, day_value: str | None) -> tuple[str, list[dict[str, Any]], str]:
        """Lightweight-cached _daily_entries: skips message reading when nothing changed."""
        day, candidates, light_hash = self._daily_light_signature(day_value)
        cached = self._daily_entries_cache.get(light_hash)
        if cached is not None:
            return cached
        _, entries, source_hash = self._daily_entries(day_value)
        result = (day, entries, source_hash)
        self._daily_entries_cache[light_hash] = result
        if len(self._daily_entries_cache) > 8:
            oldest = list(self._daily_entries_cache.keys())[:-4]
            for key in oldest:
                del self._daily_entries_cache[key]
        return result

    @staticmethod
    def _daily_ref(
        entry: dict[str, Any],
        text: str,
        reason: str = "",
        next_action: str = "",
    ) -> dict[str, str]:
        result = {
            "text": clean_text(text, 420),
            "source": entry["source"],
            "conversation_id": entry["id"],
            "title": clean_text(entry.get("title"), 120),
        }
        if reason:
            result["reason"] = clean_text(reason, 300)
        if next_action:
            result["next_action"] = clean_text(next_action, 300)
        # 注入最近对话原文（供前端展开直接展示，无需再请求）
        msgs = entry.get("messages") or []
        last_user = next((m["text"] for m in reversed(msgs) if m.get("role") == "user"), "")
        last_asst = next((m["text"] for m in reversed(msgs) if m.get("role") == "assistant"), "")
        if last_user:
            result["last_user"] = clean_text(last_user, 200)
        if last_asst:
            result["last_reply"] = clean_text(last_asst, 280)
        return result


    def _rules_daily_summary(self, day: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
        if not entries:
            return {
                "overview": "当天没有读取到可用于总结的用户/助手对话正文。",
                "overview_sentence": "今天没有可总结的有效对话。",
                "narrative": "今天没有读取到足够的对话正文，因此暂时无法形成可靠的工作总结。",
                "completion_summary": "今天没有识别到可以核验的完成成果。",
                "unfinished_summary": "今天没有识别到明确的未完成事项。",
                "next_step_summary": "下一步可以先补充当天的工作记录，再重新生成摘要。",
                "main_focus": [],
                "activities": [],
                "achievements": [],
                "decisions": [],
                "unfinished": [],
                "first_step": [],
                "ongoing": [],
                "blocked": [],
                "next_actions": [],
            }

        done_terms = (
            "已完成", "完成了", "已处理", "已修复", "已实现", "已接入", "已同步", "已打包",
            "验证通过", "通过验证", "成功", "已经同步", "可用", "落地", "发布", "安装包",
        )
        blocked_terms = (
            "无法", "失败", "阻塞", "未找到", "需要你", "请确认", "权限不足", "被策略拦",
            "卡住", "报错", "timeout", "超时",
        )
        ongoing_terms = (
            "下一步", "继续", "尚未", "还需", "待处理", "进行中", "需要进一步", "TODO",
            "待办", "未完成", "之后", "接下来",
        )
        decision_terms = (
            "决定", "确定", "采用", "选择", "改为", "优先", "暂不", "方案", "先做", "改成",
            "推荐", "按这个", "就用",
        )
        achievement_patterns = (
            r"(?:已|已经)?(?:完成|实现|修复|接入|同步|打包|验证|落地|发布|生成|安装|优化|提速)[^。！？\n]{4,80}",
            r"(?:Windows|macOS|安装包|Setup|摘要模板|项目页|搜索|皮肤|Skill)[^。！？\n]{0,40}(?:完成|可用|通过|就绪|落地)",
        )
        next_patterns = (
            r"(?:下一步|接下来|随后|之后|待办|仍需|还要)[^。！？\n]{4,80}",
            r"(?:先|继续)[^。！？\n]{4,60}",
        )

        def pick_snippet(texts: list[str], terms: tuple[str, ...], limit: int = 120) -> str:
            for value in reversed(texts):
                if any(term.casefold() in value.casefold() for term in terms):
                    return claim_text(value, limit)
            return ""

        def pick_regex(texts: list[str], patterns: tuple[str, ...], limit: int = 120) -> str:
            for value in reversed(texts):
                for pattern in patterns:
                    match = re.search(pattern, value, flags=re.IGNORECASE)
                    if match:
                        return clean_text(match.group(0), limit)
            return ""

        def concrete_action(text: str, fallback: str = "") -> str:
            value = claim_text(text, 100) or clean_text(fallback, 100)
            value = re.sub(r"^(继续完善|帮我|请|麻烦|我想|我希望)", "", value).strip(" ：:。")
            return clean_text(value, 90)

        def plain_clause(text: str, limit: int = 120) -> str:
            value = str(text or "")
            value = re.sub(r"```[\s\S]*?```", " ", value)
            value = re.sub(r"\[[^\]]+\]\((?:thread|https?|file):[^)]+\)", " ", value)
            value = re.sub(r"(?:https?|thread|file)://\S+", " ", value)
            value = re.sub(r"(?m)^\s*(?:#{1,6}|[-*+]>?)\s*", "", value)
            value = value.replace("`", "").replace("*", "")
            value = re.sub(r"\s+", " ", value).strip()
            candidates = [
                part.strip(" ：；，。")
                for part in re.split(r"[。！？!?]\s*", value)
                if 8 <= len(part.strip()) <= 180
            ]
            return clean_text(candidates[0] if candidates else value, limit)

        def readable_topic(title: str, request: str) -> str:
            candidate = request or title
            if (
                len(candidate) > 72
                or candidate.lstrip().startswith(("[", "@", "<", "#"))
                or "thread://" in candidate
            ):
                candidate = request or title
            candidate = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", candidate)
            candidate = re.sub(r"^[\s@#\[\]：:]+|[\s@#\[\]：:]+$", "", candidate)
            candidate = re.sub(r"\s+", " ", candidate)
            combined = f"{title} {request}"
            if "今天工作焦点" in combined and any(term in combined for term in ("极简摘要", "对话的名称", "对话名称")):
                candidate = "让工作焦点显示极简摘要"
            elif "找对话" in combined and any(term in combined for term in ("折叠", "挡住", "收起", "不打开")):
                candidate = "让对话详情按需折叠展开"
            elif "每日" in candidate and "摘要" in candidate:
                candidate = "完善每日工作摘要的内容与可读性"
            elif "找对话" in candidate and any(term in candidate for term in ("搜索框", "拖动", "近3天", "近 3 天")):
                candidate = "优化找对话页面的筛选、性能与会话内搜索"
            elif "排版" in candidate and any(term in candidate for term in ("项目符号", "父子", "最重要")):
                candidate = "优化每日摘要的信息层级与阅读体验"
            elif "项目" in candidate and any(term in candidate for term in ("拖动", "大小", "宽度")):
                candidate = "完善项目页面的可调整布局"
            if not candidate or len(candidate.strip()) < 4:
                candidate = title
            request_focus = compact_focus_text(candidate)
            title_focus = compact_focus_text(title)
            if request_focus != title_focus:
                action_terms = (
                    "优化", "完善", "修复", "处理", "排查", "调整", "改为", "改成",
                    "增加", "新增", "整理", "分析", "验证", "迁移", "折叠", "摘要",
                    "总结", "汇总", "搭建", "制作", "接入",
                )
                request_has = any(term in request_focus for term in action_terms)
                title_has = any(term in title_focus for term in action_terms)
                if title_has and not request_has:
                    return title_focus
                if request_has == title_has:
                    if len(title_focus) < len(request_focus):
                        return title_focus
                    if len(title_focus) == len(request_focus):
                        return title_focus
            return request_focus

        activities: list[dict[str, str]] = []
        achievements: list[dict[str, str]] = []
        decisions: list[dict[str, str]] = []
        unfinished: list[dict[str, str]] = []
        ongoing: list[dict[str, str]] = []
        blocked: list[dict[str, str]] = []
        next_actions: list[dict[str, str]] = []
        focus_scores: list[tuple[float, dict[str, Any], str]] = []
        meta_continuations: set[tuple[str, str]] = set()

        for entry in entries:
            request = claim_text(entry["latest_user"], 160)
            response = claim_text(entry["latest_assistant"], 180)
            assistant_messages = [
                message["text"] for message in entry["messages"] if message["role"] == "assistant"
            ]
            all_messages = [message["text"] for message in entry["messages"]]
            title = clean_text(entry["title"], 120)
            topic = readable_topic(title, request)
            # 无效 topic（太短/省略号/口语碎片）回退到对话标题，避免"你看一下…"这类无意义事项
            stripped_topic = re.sub(r"[….\s]+", "", topic)
            if len(stripped_topic) < 5 or topic.endswith(("…", "...", "。")):
                topic = title or topic
            repeated_request = request and (
                request.casefold() in title.casefold() or title.casefold() in request.casefold()
            )
            activity = topic if repeated_request or not request else f"{topic}：{request}"
            activities.append(self._daily_ref(entry, activity))

            combined = "\n".join([assistant_messages[-1] if assistant_messages else "", entry.get("note") or ""])
            evidence = entry.get("evidence_level") or "full"
            is_blocked = any(term in combined for term in blocked_terms)
            last_assistant = assistant_messages[-1] if assistant_messages else ""
            has_done_signal = any(term.casefold() in last_assistant.casefold() for term in done_terms)
            has_ongoing_signal = any(term.casefold() in last_assistant.casefold() for term in ongoing_terms)
            explicit_done = entry.get("user_status") == "done" or (
                has_done_signal and not has_ongoing_signal
            )
            is_done = explicit_done and evidence == "full" and bool(last_assistant)
            is_ongoing = (
                entry.get("user_status") == "todo"
                or entry.get("last_role") == "user"
                or any(term in last_assistant for term in ongoing_terms)
                or not is_done
            )

            achievement_text = plain_clause(last_assistant, 140) if is_done else ""
            if is_done and achievement_text:
                # Prefer a full sentence over a short regex fragment.
                if len(achievement_text) < 18:
                    achievement_text = "相关工作已经处理完成，并形成了可以继续使用或核查的结果"
                achievements.append(self._daily_ref(entry, f"{topic} → {achievement_text}"))

            decision_text = pick_snippet(
                [entry.get("latest_user") or "", last_assistant, entry.get("note") or ""],
                decision_terms,
                160,
            )
            if decision_text:
                decisions.append(self._daily_ref(entry, f"{topic}：{plain_clause(decision_text, 120)}"))

            next_text = (
                pick_regex(assistant_messages, next_patterns)
                or claim_text(entry.get("note") or "", 100)
                or pick_snippet(assistant_messages, ongoing_terms, 100)
            )
            if is_blocked:
                blocked.append(
                    self._daily_ref(
                        entry,
                        topic,
                        reason=claim_text(response or "对话显示仍有阻塞项", 120),
                        next_action=concrete_action(next_text or request or "确认阻塞原因并继续处理"),
                    )
                )
            if is_ongoing or is_blocked or not is_done:
                if entry.get("last_role") == "user":
                    reason = "对话停在用户最新请求，还没有可核查结果"
                elif is_blocked:
                    reason = "对话中出现了失败或阻塞信号，尚未看到问题被解除"
                elif evidence != "full":
                    reason = "正文不完整，只能确认推进方向，不能确认完成"
                else:
                    reason = "任务仍在推进，尚未形成明确的收尾或验收结论"
                action = (
                    f"先解除“{topic}”的阻塞并重新验证"
                    if is_blocked
                    else f"继续处理“{topic}”，并核对最终结果"
                )
                unfinished.append(
                    self._daily_ref(
                        entry,
                        topic if len(topic) >= 4 else concrete_action(request or title),
                        reason=reason,
                        next_action=action,
                    )
                )
                ongoing.append(self._daily_ref(entry, f"{topic}：{claim_text(request or response or reason, 100)}"))
                if action:
                    next_actions.append(self._daily_ref(entry, action))

            score = float(entry.get("updated_at") or 0) + min(40, len(entry.get("messages") or [])) * 1000
            if entry.get("user_status") == "todo":
                score += 50_000
            if "对话中心" in (title + request + response) or "AIConversationHub" in (title + request):
                score += 80_000
            # 引用对话继续/系统注入类元消息不参与焦点竞选，避免焦点变成空洞链接碎片
            if (
                (request or "").lstrip().startswith(("[", "@", "<"))
                or "thread://" in (title + (request or ""))
            ):
                meta_continuations.add((entry["source"], entry["id"]))
            focus_status = "done" if is_done else ("blocked" if is_blocked else "ongoing")
            focus_scores.append((score, entry, topic, focus_status))

        focus_scores.sort(key=lambda item: item[0], reverse=True)
        candidates = [
            item for item in focus_scores
            if (item[1]["source"], item[1]["id"]) not in meta_continuations
        ] or focus_scores
        main_entry = candidates[0][1]
        main_topic = candidates[0][2]
        main_status = candidates[0][3] if len(candidates[0]) > 3 else "ongoing"
        main_focus = [self._daily_ref(main_entry, main_topic)]
        main_focus[0]["status"] = main_status

        def dedupe(items: list[dict[str, str]], limit: int) -> list[dict[str, str]]:
            seen: set[str] = set()
            result: list[dict[str, str]] = []
            for item in items:
                key = re.sub(r"\s+", "", item.get("text", "")).casefold()
                if not key or key in seen:
                    continue
                seen.add(key)
                result.append(item)
                if len(result) >= limit:
                    break
            return result

        achievements = dedupe(achievements, 3)
        decisions = dedupe(decisions, 5)
        low_signal_topics = ("say ok", "积分了吗", "消息获取数量限制")
        unfinished = [
            item for item in dedupe(unfinished, 8)
            if not any(term in item.get("text", "").casefold() for term in low_signal_topics)
        ][:4]
        next_actions = [
            item for item in dedupe(next_actions, 8)
            if not any(term in item.get("text", "").casefold() for term in low_signal_topics)
        ][:4]

        def readable_sentence(value: str) -> str:
            text = clean_text(value, 260).strip(" ·→：:；;，,。")
            if not text:
                return ""
            return text if text.endswith(("。", "！", "？", ".", "!", "?")) else f"{text}。"

        def split_topic(value: str) -> tuple[str, str]:
            parts = re.split(r"\s*(?:→|：|:)\s*", clean_text(value, 260), maxsplit=1)
            return (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])

        for item in achievements:
            topic, outcome = split_topic(item["text"])
            if topic:
                item["text"] = readable_sentence(f"围绕“{topic}”，今天已经取得明确成果：{outcome}")
            else:
                item["text"] = readable_sentence(f"今天已经完成：{outcome}")

        for item in decisions:
            topic, decision = split_topic(item["text"])
            if topic:
                item["text"] = readable_sentence(f"关于“{topic}”，今天明确了：{decision}")
            else:
                item["text"] = readable_sentence(f"今天明确了：{decision}")

        for item in unfinished:
            topic = clean_text(item.get("text"), 120).strip(" ·→：:；;，,。")
            reason = clean_text(item.get("reason"), 180).strip(" ·→：:；;，,。")
            action = clean_text(item.get("next_action"), 180).strip(" ·→：:；;，,。")
            sentence = f"“{topic}”目前还没有完成"
            if reason:
                sentence += f"，主要因为{reason}"
            sentence += "。"
            if action:
                sentence += (
                    f" 接下来将{action}。"
                    if action.startswith(("先", "继续", "重新", "核对", "确认"))
                    else f" 接下来将先{action}。"
                )
            item["text"] = clean_text(sentence, 420)

        for item in next_actions:
            action = clean_text(item.get("text"), 180).strip(" ·→：:；;，,。")
            item["text"] = readable_sentence(
                f"下一步将{action}"
                if action.startswith(("先", "继续", "重新", "核对", "确认"))
                else f"下一步先{action}"
            )

        first_step = next_actions[:1]
        if not first_step and unfinished:
            first = unfinished[0]
            first_step = [
                {
                    "text": first.get("next_action") or first["text"],
                    "source": first["source"],
                    "conversation_id": first["conversation_id"],
                }
            ]

        top_topics = []
        for item in candidates[:3]:
            topic = item[2] if len(item) > 2 else ""
            if topic and topic not in top_topics:
                top_topics.append(topic)
        done_bits = [
            clean_text(item["text"].split("→", 1)[-1].split("：", 1)[-1], 40)
            for item in achievements[:2]
        ]
        open_bits = [clean_text(item["text"], 28) for item in unfinished[:2]]
        if top_topics:
            head = f"今天主要围绕「{' / '.join(top_topics[:2])}」推进"
        else:
            head = f"今天推进了 {len(entries)} 个有效对话"
        if done_bits:
            middle = f"，已形成：{'；'.join(done_bits)}"
        else:
            middle = "，暂无足够稳健的可核查成果"
        if open_bits:
            tail = f"；仍待：{'；'.join(open_bits)}"
        else:
            tail = "；当前没有明确遗留项"
        overview = clean_text(head + middle + tail + "。", 420)
        focus_sentence = readable_sentence(f"今天的工作主要围绕“{main_topic}”展开")
        completion_summary = (
            "已经完成的工作包括：" + " ".join(item["text"] for item in achievements)
            if achievements
            else "今天推进了相关工作，但暂时没有识别到足够明确、可以核验的完成成果。"
        )
        unfinished_summary = (
            "仍需继续处理的事项包括：" + " ".join(item["text"] for item in unfinished[:4])
            if unfinished
            else "目前没有识别到明确遗留的未完成事项。"
        )
        next_step_summary = (
            first_step[0]["text"]
            if first_step
            else "下一步先核对今天的工作结果，再确定最优先的续接动作。"
        )
        narrative = "\n\n".join(
            [
                focus_sentence,
                completion_summary,
                unfinished_summary,
                next_step_summary,
            ]
        )
        overview = clean_text(
            " ".join([focus_sentence, completion_summary, unfinished_summary]),
            1200,
        )

        return {
            "overview": overview,
            "overview_sentence": overview,
            "narrative": narrative,
            "completion_summary": completion_summary,
            "unfinished_summary": unfinished_summary,
            "next_step_summary": next_step_summary,
            "main_focus": main_focus,
            "activities": dedupe(activities, 12),
            "achievements": achievements,
            "decisions": decisions,
            "unfinished": unfinished,
            "first_step": first_step[:1],
            "ongoing": dedupe(ongoing, 10),
            "blocked": dedupe(blocked, 8),
            "next_actions": next_actions,
        }


    @staticmethod
    def _daily_stats(entries: list[dict[str, Any]]) -> dict[str, Any]:
        by_source = {source: 0 for source in SOURCES}
        message_count = 0
        for entry in entries:
            by_source[entry["source"]] += 1
            message_count += len(entry["messages"])
        return {
            "conversations": len(entries),
            "messages": message_count,
            "by_source": by_source,
            "workspaces": len({entry["workspace"] for entry in entries if entry["workspace"]}),
        }

    def _store_daily_summary(
        self,
        day: str,
        source_hash: str,
        summary: dict[str, Any],
        generator: str,
        model: str = "",
    ) -> None:
        now = time.time()
        with notes_db() as conn:
            conn.execute(
                """
                INSERT INTO daily_summaries(
                  day,source_hash,summary_json,generator,model,prompt_version,
                  manual_note,generated_at,updated_at
                )
                VALUES(?,?,?,?,?,?,?, ?,?)
                ON CONFLICT(day) DO UPDATE SET
                  source_hash=excluded.source_hash,
                  summary_json=excluded.summary_json,
                  generator=excluded.generator,
                  model=excluded.model,
                  prompt_version=excluded.prompt_version,
                  generated_at=excluded.generated_at,
                  updated_at=excluded.updated_at
                """,
                (
                    day,
                    source_hash,
                    json.dumps(summary, ensure_ascii=False),
                    generator,
                    model,
                    DAILY_PROMPT_VERSION,
                    "",
                    now,
                    now,
                ),
            )
            conn.commit()

    def daily_summary(self, day_value: str | None) -> dict[str, Any]:
        day, candidates, light_hash = self._daily_light_signature(day_value)
        with notes_db() as conn:
            row = conn.execute("SELECT * FROM daily_summaries WHERE day=?", (day,)).fetchone()

        # Fast path: reuse a fresh cached summary without reloading all day transcripts.
        if (
            row
            and int(row["prompt_version"] or 0) == DAILY_PROMPT_VERSION
            and str(row["source_hash"] or "") == light_hash
        ):
            try:
                summary = json.loads(row["summary_json"])
            except (TypeError, ValueError):
                summary = None
            if summary is not None:
                conversations = [
                    {
                        "source": item.source,
                        "source_kind": item.source_kind,
                        "id": item.id,
                        "title": item.title,
                        "workspace": item.workspace,
                        "message_count": item.message_count,
                        "latest_user": claim_text(item.preview, 240),
                        "updated_at": item.updated_at,
                    }
                    for item in candidates
                ]
                return {
                    "day": day,
                    "is_today": day == datetime.now(LOCAL_TZ).date().isoformat(),
                    "is_stale": False,
                    "summary": summary,
                    "stats": {
                        "conversations": len(candidates),
                        "messages": sum(int(item.message_count or 0) for item in candidates),
                        "by_source": {
                            source: sum(1 for item in candidates if item.source == source)
                            for source in SOURCES
                        },
                        "workspaces": len({item.workspace for item in candidates if item.workspace}),
                    },
                    "conversations": conversations,
                    "generator": row["generator"],
                    "model": row["model"],
                    "model_available": False,
                    "generated_at": float(row["generated_at"] or 0),
                    "manual_note": row["manual_note"] or "",
                    "prompt_version": DAILY_PROMPT_VERSION,
                    "template": {
                        "name": "daily_review_v5",
                        "sections": [
                            "overview",
                            "main_focus",
                            "achievements",
                            "decisions",
                            "unfinished",
                            "first_step",
                        ],
                    },
                }

        day, entries, source_hash = self._daily_entries(day_value)
        cache_hash = light_hash or source_hash
        needs_rules = (
            not row
            or int(row["prompt_version"] or 0) != DAILY_PROMPT_VERSION
            or (
                str(row["source_hash"] or "") not in {source_hash, light_hash}
                and str(row["generator"] or "").startswith("rules")
            )
        )
        if needs_rules:
            summary = self._rules_daily_summary(day, entries)
            self._store_daily_summary(day, cache_hash, summary, "rules")
            with notes_db() as conn:
                row = conn.execute("SELECT * FROM daily_summaries WHERE day=?", (day,)).fetchone()
        if row:
            try:
                summary = json.loads(row["summary_json"])
            except (TypeError, ValueError):
                summary = self._rules_daily_summary(day, entries)
            generator = row["generator"]
            model = row["model"]
            generated_at = float(row["generated_at"] or 0)
            manual_note = row["manual_note"] or ""
            is_stale = str(row["source_hash"] or "") not in {source_hash, light_hash}
        else:
            summary = self._rules_daily_summary(day, entries)
            self._store_daily_summary(day, cache_hash, summary, "rules")
            generator = "rules"
            model = ""
            generated_at = time.time()
            manual_note = ""
            is_stale = False
        conversations = [
            {
                "source": entry["source"],
                "source_kind": entry["source_kind"],
                "id": entry["id"],
                "title": entry["title"],
                "workspace": entry["workspace"],
                "message_count": len(entry["messages"]),
                "latest_user": claim_text(entry["latest_user"], 240),
                "updated_at": entry["updated_at"],
            }
            for entry in entries
        ]
        return {
            "day": day,
            "is_today": day == datetime.now(LOCAL_TZ).date().isoformat(),
            "is_stale": is_stale,
            "summary": summary,
            "stats": self._daily_stats(entries),
            "conversations": conversations,
            "generator": generator,
            "model": model,
            "model_available": False,
            "generated_at": generated_at,
            "manual_note": manual_note,
            "prompt_version": DAILY_PROMPT_VERSION,
            "template": {
                "name": "daily_review_v5",
                "sections": [
                    "overview",
                    "main_focus",
                    "achievements",
                    "decisions",
                    "unfinished",
                    "first_step",
                ],
            },
        }


    def save_daily_note(self, payload: dict[str, Any]) -> dict[str, Any]:
        day, _, _ = parse_day(str(payload.get("day") or ""))
        note = clean_text(payload.get("manual_note"), 20000)
        self.daily_summary(day)
        now = time.time()
        with notes_db() as conn:
            conn.execute(
                "UPDATE daily_summaries SET manual_note=?,updated_at=? WHERE day=?",
                (note, now, day),
            )
            conn.commit()
        return {"ok": True, "updated_at": now, "manual_note": note}

    def _messages_for_item(
        self,
        item: Conversation,
        start: float | None = None,
        end: float | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if item.source == "hermes":
            messages = self._hermes_messages(item.id, start, end, limit)
        elif item.source == "codex":
            messages = self._codex_messages(item.rollout_path, start, end, limit)
        elif item.source == "workbuddy":
            messages = self._workbuddy_messages(item.rollout_path, start, end, limit)
        else:
            messages = self._external_messages_for_item(item, start, end, limit)
        result = []
        for message in messages:
            role = str(message.get("role") or "")
            text = sanitize_daily_text(message.get("text"), role, 20000)
            if role in {"user", "assistant"} and text:
                result.append(
                    {
                        "role": role,
                        "text": text,
                        "timestamp": float(message.get("timestamp") or 0),
                    }
                )
        return result

    def _resolve_export_items(
        self, payload: dict[str, Any]
    ) -> tuple[str, list[Conversation], float | None, float | None]:
        scope = clean_text(payload.get("scope"), 30) or "project"
        start = end = None
        with self._lock:
            by_key = dict(self._by_key)
            all_items = list(self._items)
        if scope == "conversation":
            keys = [
                (
                    clean_text(payload.get("source"), 30),
                    clean_text(payload.get("conversation_id") or payload.get("id"), 240),
                )
            ]
        elif scope == "selected":
            raw_items = payload.get("conversations") or []
            if not isinstance(raw_items, list) or len(raw_items) > 300:
                raise ValueError("请选择 1–300 个对话")
            keys = [
                (
                    clean_text(row.get("source"), 30),
                    clean_text(row.get("id") or row.get("conversation_id"), 240),
                )
                for row in raw_items if isinstance(row, dict)
            ]
        elif scope == "project":
            # 项目归类在 lite 版已移除：project 范围不再可用。
            raise ValueError("项目归类在 lite 版不可用，请改用对话、日期或所选范围导出")
        elif scope == "day":
            day, start, end = parse_day(str(payload.get("day") or ""))
            keys = [
                (item.source, item.id)
                for item in all_items
                if item.created_at < end and item.updated_at >= start
            ]
        else:
            raise ValueError("Invalid export scope")
        items = [by_key[key] for key in keys if key in by_key]
        items.sort(key=lambda item: item.updated_at)
        if not items:
            raise ValueError("当前范围没有可导出的对话")
        if len(items) > 300:
            raise ValueError("一次最多导出 300 个对话，请缩小范围")
        return scope, items, start, end

    def export_bundle(self, payload: dict[str, Any]) -> dict[str, Any]:
        scope, items, start, end = self._resolve_export_items(payload)
        output_format = clean_text(payload.get("format"), 20) or "markdown"
        if output_format not in {"markdown", "jsonl"}:
            raise ValueError("仅支持 Markdown 或 JSONL")
        include_messages = payload.get("include_messages", True) is not False
        include_notes = payload.get("include_notes", True) is not False
        anonymize_paths = payload.get("anonymize_paths", True) is not False
        project_id = clean_text(payload.get("project_id"), 120)
        now = time.time()
        project_name = ""
        knowledge: list[dict[str, Any]] = []
        bundles = []
        for item in items:
            messages = self._messages_for_item(item, start, end, None) if include_messages else []
            bundles.append(
                {
                    "conversation": {
                        "source": item.source,
                        "id": item.id,
                        "title": item.title,
                        "workspace": item.workspace,
                        "created_at": item.created_at,
                        "updated_at": item.updated_at,
                        "model": item.model,
                        "tags": list(item.tags or []),
                        "user_status": item.user_status,
                        "favorite": item.favorite,
                        "note": item.note if include_notes else "",
                    },
                    "messages": messages,
                }
            )
        label = project_name or (
            str(payload.get("day") or "") if scope == "day" else items[0].title
        )
        base_name = safe_filename(f"{label or scope}-{datetime.now(LOCAL_TZ).date().isoformat()}")
        if output_format == "jsonl":
            rows = [
                {
                    "record_type": "export_manifest",
                    "schema_version": HUB_SCHEMA_VERSION,
                    "exported_at": now,
                    "scope": scope,
                    "project_id": project_id,
                    "project_name": project_name,
                    "conversation_count": len(bundles),
                    "content_policy": "user_assistant_only",
                }
            ]
            for bundle in bundles:
                conversation = bundle["conversation"]
                rows.append({"record_type": "conversation", **conversation})
                rows.extend(
                    {
                        "record_type": "message",
                        "source": conversation["source"],
                        "conversation_id": conversation["id"],
                        "index": index,
                        **message,
                    }
                    for index, message in enumerate(bundle["messages"])
                )
            content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
            filename = f"{base_name}.jsonl"
            mime = "application/x-ndjson;charset=utf-8"
        else:
            def prep(text: Any) -> str:
                value = markdown_text(text)
                if anonymize_paths:
                    value = anonymize_home_paths(value)
                return markdown_safe_paths(value)

            total_messages = sum(len(bundle["messages"]) for bundle in bundles)
            scope_label = f"day={payload.get('day')}" if scope == "day" else scope
            export_lines = [
                "---",
                f"title: {json.dumps(str(project_name or 'AI 对话导出'), ensure_ascii=False)}",
                "generator: AI Conversation Hub Lite",
                f"exported_at: {datetime.fromtimestamp(now, LOCAL_TZ).isoformat(timespec='seconds')}",
                f"scope: {scope_label}",
                f"conversations: {len(bundles)}",
                f"messages: {total_messages}",
                "content_policy: user_assistant_only",
                "---",
                "",
                f"# {project_name or 'AI 对话导出'}",
                "",
                f"> 导出时间：{datetime.fromtimestamp(now, LOCAL_TZ).isoformat(timespec='seconds')}",
                f"> 范围：{scope_label}；对话数：{len(bundles)}；消息数：{total_messages}",
                "> 仅包含用户/助手对话正文。",
                "",
                "",
            ]
            for bundle in bundles:
                item = bundle["conversation"]
                export_lines.extend(
                    [
                        f"### {prep(item['title'])}",
                        "",
                        f"- 来源：{item['source']}",
                        f"- 对话 ID：`{item['id']}`",
                        f"- 工作区：{prep(item['workspace']) or '未命名'}",
                        f"- 最近活动：{datetime.fromtimestamp(item['updated_at'], LOCAL_TZ).isoformat(timespec='seconds')}",
                    ]
                )
                if include_notes and item["note"]:
                    export_lines.extend([f"- 备注：{prep(item['note'])}"])
                if item["tags"]:
                    export_lines.extend([f"- 标签：{', '.join(item['tags'])}"])
                export_lines.append("")
                for message in bundle["messages"]:
                    role = "用户" if message["role"] == "user" else "助手"
                    timestamp = (
                        datetime.fromtimestamp(message["timestamp"], LOCAL_TZ).isoformat(timespec="seconds")
                        if message["timestamp"] else ""
                    )
                    export_lines.extend(
                        [
                            f"#### {role}{f' · {timestamp}' if timestamp else ''}",
                            "",
                            prep(message["text"]),
                            "",
                        ]
                    )
            content = "\n".join(export_lines).rstrip() + "\n"
            filename = f"{base_name}.md"
            mime = "text/markdown;charset=utf-8"
        if len(content.encode("utf-8")) > 25_000_000:
            raise ValueError("导出内容超过 25 MB，请缩小项目或日期范围")
        byte_count = len(content.encode("utf-8"))
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return {
            "ok": True,
            "filename": filename,
            "mime": mime,
            "content": content,
            "preview": content[:16000],
            "conversation_count": len(bundles),
            "knowledge_count": len(knowledge),
            "bytes": byte_count,
            "content_hash": content_hash,
        }


    def save_note(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = str(payload.get("source") or "")
        conversation_id = str(payload.get("id") or "")
        if (source, conversation_id) not in self._by_key:
            raise ValueError("Unknown conversation")
        note = clean_text(payload.get("note"), 20000)
        tags = [clean_text(tag, 60) for tag in (payload.get("tags") or []) if clean_text(tag, 60)]
        tags = list(dict.fromkeys(tags))[:20]
        user_status = clean_text(payload.get("user_status"), 40)
        favorite = 1 if payload.get("favorite") else 0
        now = time.time()
        with notes_db() as conn:
            conn.execute(
                """
                INSERT INTO notes(source,conversation_id,note,tags,user_status,favorite,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(source,conversation_id) DO UPDATE SET
                  note=excluded.note, tags=excluded.tags, user_status=excluded.user_status,
                  favorite=excluded.favorite, updated_at=excluded.updated_at
                """,
                (source, conversation_id, note, json.dumps(tags, ensure_ascii=False), user_status, favorite, now),
            )
            conn.commit()
        with self._lock:
            item = self._by_key[(source, conversation_id)]
            item.note = note
            item.tags = tags
            item.user_status = user_status
            item.favorite = bool(favorite)
        return {"ok": True, "updated_at": now}

    def summary(self) -> dict[str, Any]:
        with self._lock:
            cache_key = f"{self.refreshed_at:.6f}:{len(self._items)}:{self._source_signature}"
            cached = self._summary_cache
            if cached.get("key") == cache_key and isinstance(cached.get("payload"), dict):
                return cached["payload"]
            items = list(self._items)
        now = time.time()
        range_names = ("today", "3d", "7d", "30d")
        status_names = ("active", "week", "recent", "archive", "history")
        user_status_names = ("todo", "done", "reference", "archive_candidate")
        by_source = {name: 0 for name in SOURCES}
        by_status = {name: 0 for name in status_names}
        workspaces: dict[str, int] = {}
        workspaces_by_source: dict[str, dict[str, int]] = {source: {} for source in SOURCES}
        native_projects: dict[str, int] = {}
        native_projects_by_source: dict[str, dict[str, int]] = {
            source: {} for source in SOURCES
        }
        by_source_status = {source: {status: 0 for status in status_names} for source in SOURCES}
        by_range = {value: 0 for value in range_names}
        by_source_range = {source: {value: 0 for value in range_names} for source in SOURCES}
        by_user_status = {value: 0 for value in user_status_names}
        by_source_user_status = {source: {value: 0 for value in user_status_names} for source in SOURCES}
        for item in items:
            source = item.source
            by_source[source] = by_source.get(source, 0) + 1
            if item.status in by_status:
                by_status[item.status] += 1
            source_status = by_source_status.setdefault(source, {status: 0 for status in status_names})
            if item.status in source_status:
                source_status[item.status] += 1
            workspaces[item.workspace] = workspaces.get(item.workspace, 0) + 1
            source_workspaces = workspaces_by_source.setdefault(source, {})
            source_workspaces[item.workspace] = source_workspaces.get(item.workspace, 0) + 1
            if item.native_project:
                native_projects[item.native_project] = native_projects.get(item.native_project, 0) + 1
                source_native_projects = native_projects_by_source.setdefault(source, {})
                source_native_projects[item.native_project] = (
                    source_native_projects.get(item.native_project, 0) + 1
                )
            if item.user_status in by_user_status:
                by_user_status[item.user_status] += 1
                source_user = by_source_user_status.setdefault(source, {value: 0 for value in user_status_names})
                source_user[item.user_status] = source_user.get(item.user_status, 0) + 1
            source_range = by_source_range.setdefault(source, {value: 0 for value in range_names})
            for value in range_names:
                if range_matches(item.updated_at, value, now):
                    by_range[value] += 1
                    source_range[value] += 1
        result = {
            "total": len(items),
            "by_source": by_source,
            "by_status": by_status,
            "by_source_status": by_source_status,
            "by_range": by_range,
            "by_source_range": by_source_range,
            "by_user_status": by_user_status,
            "by_source_user_status": by_source_user_status,
            "favorites": sum(1 for item in items if item.favorite),
            "favorites_by_source": {
                source: sum(1 for item in items if item.source == source and item.favorite)
                for source in SOURCES
            },
            "workspaces": sorted(workspaces.items(), key=lambda pair: (-pair[1], pair[0])),
            "workspaces_by_source": {
                source: sorted(values.items(), key=lambda pair: (-pair[1], pair[0]))
                for source, values in workspaces_by_source.items()
            },
            "native_projects": sorted(
                native_projects.items(), key=lambda pair: (-pair[1], pair[0])
            ),
            "native_projects_by_source": {
                source: sorted(values.items(), key=lambda pair: (-pair[1], pair[0]))
                for source, values in native_projects_by_source.items()
            },
            "refreshed_at": self.refreshed_at,
        }
        with self._lock:
            cache_key = f"{self.refreshed_at:.6f}:{len(self._items)}:{self._source_signature}"
            self._summary_cache = {"key": cache_key, "payload": result}
        return result

    def source_health(self) -> dict[str, Any]:
        with self._lock:
            counts = {
                source: sum(1 for item in self._items if item.source == source)
                for source in SOURCES
            }
            workbuddy_subsources = {
                "assistant": sum(
                    1
                    for item in self._items
                    if item.source == "workbuddy" and item.source_kind == "assistant"
                ),
                "desktop": sum(
                    1
                    for item in self._items
                    if item.source == "workbuddy" and item.source_kind != "assistant"
                ),
            }
            external_errors = dict(self._external_source_errors)
        sources: dict[str, dict[str, Any]] = {
                "hermes": {
                    "path": str(HERMES_DB),
                    "exists": HERMES_DB.exists(),
                    "enabled": source_is_enabled("hermes"),
                    "label": "Hermes",
                    "conversations": counts["hermes"],
                },
                "codex": {
                    "path": str(CODEX_DB),
                    "exists": CODEX_DB.exists(),
                    "enabled": source_is_enabled("codex"),
                    "label": "Codex",
                    "conversations": counts["codex"],
                    "excluded": self._excluded_codex_background,
                },
                "workbuddy": {
                    "path": str(WORKBUDDY_HOME),
                    "exists": WORKBUDDY_DB.exists() and WORKBUDDY_PROJECTS.exists(),
                    "enabled": source_is_enabled("workbuddy"),
                    "label": "WorkBuddy",
                    "conversations": counts["workbuddy"],
                    "subsources": workbuddy_subsources,
                },
        }
        for source, item in configured_extra_sources(SOURCE_CONFIG, with_counts=False).items():
            sources[source] = {
                "path": item["path"],
                "exists": item["valid"],
                "enabled": item["enabled"],
                "label": item["label"],
                "detail": item["detail"],
                "error": external_errors.get(source, ""),
                "conversations": counts[source],
            }
        for source, item in configured_custom_sources(SOURCE_CONFIG, with_counts=False).items():
            sources[source] = {
                "path": item["path"],
                "exists": item["valid"],
                "enabled": item["enabled"],
                "label": item["label"],
                "format": item["format"],
                "custom": True,
                "detail": item["detail"],
                "error": external_errors.get(source, ""),
                "conversations": counts[source],
            }
        with notes_db() as conn:
            profiles = {
                str(row["source"]): dict(row)
                for row in conn.execute("SELECT * FROM source_profiles")
            }
        for source, profile in profiles.items():
            if source not in sources:
                continue
            try:
                detail = json.loads(profile.pop("detail_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                detail = {}
            sources[source].update(profile)
            sources[source]["quality_detail"] = detail
            sources[source]["schema_fingerprint_short"] = str(
                profile.get("schema_fingerprint") or ""
            )[:12]
        return {
            "sources": sources,
            "config_path": str(CONFIG_PATH),
            "data_dir": str(DATA_DIR),
        }

    @staticmethod
    def _backup_primary_keys(conn: sqlite3.Connection, table: str) -> list[str]:
        return [
            str(row["name"])
            for row in conn.execute(f'PRAGMA table_info("{table}")')
            if int(row["pk"])
        ]

    def backup_export(self) -> dict[str, Any]:
        with notes_db() as conn:
            tables = {
                table: [dict(row) for row in conn.execute(f'SELECT * FROM "{table}"')]
                for table in BACKUP_TABLES
            }
        return {
            "format": "ai-conversation-hub-backup",
            "format_version": BACKUP_FORMAT_VERSION,
            "hub_schema_version": HUB_SCHEMA_VERSION,
            "exported_at": time.time(),
            "contains_secrets": False,
            "tables": tables,
        }

    def backup_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        backup = payload.get("backup", payload)
        if not isinstance(backup, dict) or backup.get("format") != "ai-conversation-hub-backup":
            raise ValueError("不是有效的 AI 对话中心备份")
        if int(backup.get("format_version") or 0) != BACKUP_FORMAT_VERSION:
            raise ValueError("备份格式版本不受支持")
        tables = backup.get("tables")
        if not isinstance(tables, dict):
            raise ValueError("备份缺少数据表")
        unknown = set(tables) - set(BACKUP_TABLES)
        if unknown:
            raise ValueError(f"备份包含不允许的数据表：{', '.join(sorted(unknown))}")
        result: dict[str, Any] = {"rows": 0, "new": 0, "conflicts": 0, "tables": {}}
        with notes_db() as conn:
            for table in BACKUP_TABLES:
                rows = tables.get(table, [])
                if not isinstance(rows, list):
                    raise ValueError(f"{table} 数据格式无效")
                columns = {
                    str(row["name"]) for row in conn.execute(f'PRAGMA table_info("{table}")')
                }
                primary_keys = self._backup_primary_keys(conn, table)
                table_new = 0
                table_conflicts = 0
                for row in rows:
                    if not isinstance(row, dict) or not set(row).issubset(columns):
                        raise ValueError(f"{table} 包含无效字段")
                    if not primary_keys or any(key not in row for key in primary_keys):
                        raise ValueError(f"{table} 缺少主键")
                    where = " AND ".join(f'"{key}"=?' for key in primary_keys)
                    exists = conn.execute(
                        f'SELECT 1 FROM "{table}" WHERE {where}',
                        tuple(row[key] for key in primary_keys),
                    ).fetchone()
                    if exists:
                        table_conflicts += 1
                    else:
                        table_new += 1
                result["tables"][table] = {
                    "rows": len(rows),
                    "new": table_new,
                    "conflicts": table_conflicts,
                }
                result["rows"] += len(rows)
                result["new"] += table_new
                result["conflicts"] += table_conflicts
        result["safe_scope"] = "仅管理信息；不含 API 密钥、原始对话、搜索索引或本机路径配置"
        return result

    def backup_restore(self, payload: dict[str, Any]) -> dict[str, Any]:
        preview = self.backup_preview(payload)
        backup = payload.get("backup", payload)
        mode = clean_text(payload.get("mode"), 30) or "keep_existing"
        if mode not in {"keep_existing", "merge_newer"}:
            raise ValueError("恢复模式无效")
        inserted = 0
        updated = 0
        with notes_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for table in BACKUP_TABLES:
                columns = {
                    str(row["name"]) for row in conn.execute(f'PRAGMA table_info("{table}")')
                }
                primary_keys = self._backup_primary_keys(conn, table)
                for raw in backup.get("tables", {}).get(table, []):
                    row = {key: value for key, value in raw.items() if key in columns}
                    names = list(row)
                    quoted = ", ".join(f'"{name}"' for name in names)
                    placeholders = ", ".join("?" for _ in names)
                    where = " AND ".join(f'"{key}"=?' for key in primary_keys)
                    existing = conn.execute(
                        f'SELECT * FROM "{table}" WHERE {where}',
                        tuple(row[key] for key in primary_keys),
                    ).fetchone()
                    if not existing:
                        conn.execute(
                            f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})',
                            tuple(row[name] for name in names),
                        )
                        inserted += 1
                        continue
                    if (
                        mode == "merge_newer"
                        and "updated_at" in row
                        and float(row.get("updated_at") or 0) > float(existing["updated_at"] or 0)
                    ):
                        mutable = [name for name in names if name not in primary_keys]
                        if mutable:
                            conn.execute(
                                f'UPDATE "{table}" SET '
                                + ", ".join(f'"{name}"=?' for name in mutable)
                                + f" WHERE {where}",
                                tuple(row[name] for name in mutable)
                                + tuple(row[key] for key in primary_keys),
                            )
                            updated += 1
            conn.commit()
        self._sync_conversation_relations()
        return {"ok": True, "inserted": inserted, "updated": updated, "preview": preview}


INDEX = ConversationIndex()
CSRF_TOKEN = secrets.token_urlsafe(32)

# 启动预热状态：pending -> running -> done/skipped/error
# 通过 /api/health 的 warmup 字段对外可见，便于验收与诊断。
WARMUP_STATE: dict[str, Any] = {
    "status": "pending",
    "started_at": 0.0,
    "finished_at": 0.0,
    "seconds": 0.0,
    "parts": {},
    "errors": [],
}


def startup_warmup() -> None:
    """后台预热：把前端首屏与首次交互要用的端点提前算好。

    覆盖 boot() 的真实调用链：summary -> conversations -> daily ->
    sources -> projects -> 各项目详情。全部走既有缓存与锁，
    与用户首批请求并发安全；任何一步失败只记录、不中断。
    """
    if os.name == "nt":
        try:
            import ctypes

            # BELOW_NORMAL：与用户请求争抢 GIL 时让出优先权，
            # 避免"点开的项目正好在预热"时请求耗时翻倍。
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.SetThreadPriority(kernel32.GetCurrentThread(), 1)
        except Exception:  # noqa: BLE001 - 降优先级失败不影响预热本身
            pass
    WARMUP_STATE["status"] = "running"
    WARMUP_STATE["started_at"] = time.time()
    parts: dict[str, float] = {}

    def run(name: str, func: Any) -> None:
        started = time.time()
        try:
            func()
            parts[name] = round(time.time() - started, 3)
        except Exception as exc:  # noqa: BLE001 - 预热失败不能影响服务
            parts[name] = -1.0
            WARMUP_STATE["errors"].append(f"{name}: {exc}")
        # 让出片刻，避免预热线程独占 GIL 时把用户首批请求饿死。
        time.sleep(0.1)

    try:
        if setup_status().get("required"):
            WARMUP_STATE["status"] = "skipped"
            return
        # 服务启动后数据源常有新增写入；先由预热吸收这次全量刷新，
        # 别让用户的首开请求撞上后台 refresh 抢占 GIL。
        run("source_refresh", lambda: INDEX.maybe_refresh(block=True))
        run("summary", INDEX.summary)
        run(
            "conversations",
            lambda: INDEX.list(
                source="all",
                query="",
                time_range="all",
                status="all",
                workspace="all",
                native_project="all",
                favorites=False,
                limit=120,
                offset=0,
            ),
        )
        run("daily", lambda: INDEX.daily_summary(""))
        run("sources", INDEX.source_health)
        WARMUP_STATE["status"] = "done"
    except Exception as exc:  # noqa: BLE001
        WARMUP_STATE["status"] = "error"
        WARMUP_STATE["errors"].append(str(exc))
    finally:
        WARMUP_STATE["parts"] = parts
        WARMUP_STATE["finished_at"] = time.time()
        WARMUP_STATE["seconds"] = round(WARMUP_STATE["finished_at"] - WARMUP_STATE["started_at"], 2)


class Handler(BaseHTTPRequestHandler):
    server_version = "ConversationHub/17"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[hub] {self.address_string()} {fmt % args}")

    def _local_request(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").casefold()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            self.send_error(HTTPStatus.FORBIDDEN)
            return False
        origin = self.headers.get("Origin", "")
        if origin:
            parsed = urllib.parse.urlsplit(origin)
            if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                self.send_error(HTTPStatus.FORBIDDEN)
                return False
        return True

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._local_request():
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        if path in {"/", "/index.html"}:
            self._file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if path == "/app.css":
            self._file(STATIC_DIR / "app.css", "text/css; charset=utf-8")
            return
        if path == "/app.js":
            self._file(STATIC_DIR / "app.js", "text/javascript; charset=utf-8")
            return
        if (
            path in {"/api/summary", "/api/sources", "/api/daily", "/api/conversations"}
            or path.startswith("/api/conversation/")
            or path.startswith("/api/conversation-messages/")
        ):
            INDEX.maybe_refresh()
        if path == "/api/token":
            self._json({"token": CSRF_TOKEN})
            return
        if path == "/api/projects":
            self._json({"ok": True, "projects": projects_list()})
            return
        if path.startswith("/api/projects/"):
            project_id = urllib.parse.unquote(path[len("/api/projects/"):])
            self._json({"ok": True, **project_detail(project_id)})
            return
        if path == "/api/health":
            self._json({
                "ok": True,
                "app": "AIConversationHub",
                "version": HUB_SCHEMA_VERSION,
                "app_version": APP_VERSION,
                "platform": "macos" if sys.platform == "darwin" else ("windows" if os.name == "nt" else "linux"),
                "setup_required": setup_status()["required"],
                "data_dir": str(DATA_DIR),
                "warmup": {
                    "status": WARMUP_STATE["status"],
                    "seconds": WARMUP_STATE["seconds"],
                    "parts": WARMUP_STATE["parts"],
                    "errors": WARMUP_STATE["errors"],
                },
            })
            return
        if path == "/api/setup/status":
            self._json(setup_status())
            return
        if path == "/api/summary":
            self._json(INDEX.summary())
            return
        if path == "/api/sources":
            self._json(INDEX.source_health())
            return
        if path == "/api/daily":
            try:
                self._json(INDEX.daily_summary((params.get("date") or [""])[0]))
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
            return
        if path == "/api/conversations":
            try:
                result = INDEX.list(
                    source=(params.get("source") or ["all"])[0],
                    query=(params.get("q") or [""])[0],
                    time_range=(params.get("range") or ["all"])[0],
                    status=(params.get("status") or ["all"])[0],
                    workspace=(params.get("workspace") or ["all"])[0],
                    native_project=(params.get("native_project") or ["all"])[0],
                    favorites=(params.get("favorites") or ["0"])[0] == "1",
                    limit=min(500, max(1, int((params.get("limit") or ["120"])[0]))),
                    offset=max(0, int((params.get("offset") or ["0"])[0])),
                )
                self._json(result)
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
            return
        if path.startswith("/api/conversation-messages/"):
            parts = path.split("/", 4)
            if len(parts) != 5:
                self._json({"error": "Invalid conversation path"}, 400)
                return
            source = urllib.parse.unquote(parts[3])
            conversation_id = urllib.parse.unquote(parts[4])
            try:
                limit = min(500, max(40, int((params.get("limit") or ["300"])[0])))
            except ValueError:
                limit = 300
            result = INDEX.conversation_messages(source, conversation_id, limit)
            self._json(result if result else {"error": "Not found"}, 200 if result else 404)
            return
        if path.startswith("/api/conversation/"):
            parts = path.split("/", 4)
            if len(parts) != 5:
                self._json({"error": "Invalid conversation path"}, 400)
                return
            source = urllib.parse.unquote(parts[3])
            conversation_id = urllib.parse.unquote(parts[4])
            result = INDEX.get(source, conversation_id)
            self._json(result if result else {"error": "Not found"}, 200 if result else 404)
            return
        if path == "/api/export-notes":
            with notes_db() as conn:
                notes = [dict(row) for row in conn.execute("SELECT * FROM notes ORDER BY updated_at DESC")]
            self._json({"version": 1, "exported_at": time.time(), "notes": notes})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if not self._local_request():
            return
        if self.headers.get("X-Hub-Token") != CSRF_TOKEN:
            self._json({"error": "Invalid local request token"}, HTTPStatus.FORBIDDEN)
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            self._json({"error": "JSON required"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 10_000_000)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "Invalid JSON"}, 400)
            return
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path == "/api/note":
                self._json(INDEX.save_note(payload))
                return
            if path == "/api/projects":
                self._json(projects_mutate(payload))
                return
            if path == "/api/daily/note":
                self._json(INDEX.save_daily_note(payload))
                return
            if path == "/api/backup/export":
                self._json(INDEX.backup_export())
                return
            if path == "/api/backup/preview":
                self._json(INDEX.backup_preview(payload))
                return
            if path == "/api/backup/restore":
                self._json(INDEX.backup_restore(payload))
                return
            if path == "/api/sources/diagnose":
                INDEX.refresh()
                self._json({"ok": True, **INDEX.source_health()})
                return
            if path == "/api/sources/enabled":
                self._json(set_source_enabled(payload))
                return
            if path == "/api/export":
                self._json(INDEX.export_bundle(payload))
                return
            if path == "/api/refresh":
                INDEX.refresh()
                self._json({"ok": True, **INDEX.summary()})
                return
            if path == "/api/setup/discover":
                self._json(
                    discover_setup(
                        [str(value) for value in payload.get("roots", []) if value]
                    )
                )
                return
            if path == "/api/setup/save":
                self._json(save_setup(payload))
                return
            if path == "/api/reload-sources":
                reload_source_paths()
                INDEX.refresh()
                self._json({"ok": True, **INDEX.source_health()})
                return
        except ConflictError as exc:
            self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
            return
        except (ValueError, sqlite3.DatabaseError) as exc:
            self._json({"error": str(exc)}, 400)
            return
        self.send_error(404)


def run_server(port: int = 8765, *, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"AI Conversation Hub {APP_VERSION}: {url}")
    print("Local-only. Press Ctrl+C to stop.")
    # 首开提速：端口就绪后立即在后台预热首屏端点，与首批请求并发。
    threading.Thread(target=startup_warmup, name="hub-startup-warmup", daemon=True).start()
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Private local AI conversation hub")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    run_server(args.port, open_browser=not args.no_open)


if __name__ == "__main__":
    main()
