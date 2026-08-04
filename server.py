from __future__ import annotations

import argparse
import base64
import heapq
import hashlib
import json
import os
import platform
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
import urllib.error
import urllib.request
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
DAILY_PROMPT_VERSION = 10
HUB_SCHEMA_VERSION = 15
APP_VERSION = "0.19.2"
BACKUP_FORMAT_VERSION = 1
BACKUP_TABLES = (
    "notes", "daily_summaries", "projects", "project_assignments",
    "project_milestones", "project_daily_summaries", "project_aliases",
    "project_detection_rules", "knowledge_items", "knowledge_evidence",
    "knowledge_revisions", "knowledge_relations", "project_roots",
    "skill_management", "skill_project_links", "project_plans",
    "knowledge_exports", "conversation_summaries",
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
        CREATE TABLE IF NOT EXISTS projects (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'active',
          origin TEXT NOT NULL DEFAULT 'auto',
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_assignments (
          source TEXT NOT NULL,
          conversation_id TEXT NOT NULL,
          project_id TEXT NOT NULL,
          confidence REAL NOT NULL DEFAULT 0,
          method TEXT NOT NULL DEFAULT 'auto',
          locked INTEGER NOT NULL DEFAULT 0,
          updated_at REAL NOT NULL,
          PRIMARY KEY (source, conversation_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_milestones (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          version TEXT NOT NULL,
          title TEXT NOT NULL,
          summary TEXT NOT NULL DEFAULT '',
          occurred_at REAL NOT NULL,
          status TEXT NOT NULL DEFAULT 'done',
          evidence_json TEXT NOT NULL DEFAULT '[]',
          origin TEXT NOT NULL DEFAULT 'auto',
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_daily_summaries (
          project_id TEXT NOT NULL,
          day TEXT NOT NULL,
          source_hash TEXT NOT NULL,
          summary_json TEXT NOT NULL,
          generator TEXT NOT NULL,
          model TEXT NOT NULL DEFAULT '',
          prompt_version INTEGER NOT NULL,
          generated_at REAL NOT NULL,
          updated_at REAL NOT NULL,
          PRIMARY KEY (project_id, day)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_aliases (
          source_project_id TEXT PRIMARY KEY,
          target_project_id TEXT NOT NULL,
          created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_detection_rules (
          project_id TEXT PRIMARY KEY,
          include_keywords TEXT NOT NULL DEFAULT '[]',
          exclude_keywords TEXT NOT NULL DEFAULT '[]',
          workspace_aliases TEXT NOT NULL DEFAULT '[]',
          path_patterns TEXT NOT NULL DEFAULT '[]',
          min_score REAL NOT NULL DEFAULT 0.78,
          enabled INTEGER NOT NULL DEFAULT 1,
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
        CREATE TABLE IF NOT EXISTS knowledge_items (
          id TEXT PRIMARY KEY,
          fingerprint TEXT NOT NULL UNIQUE,
          type TEXT NOT NULL,
          title TEXT NOT NULL,
          content TEXT NOT NULL,
          scope TEXT NOT NULL DEFAULT 'project',
          project_id TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'pending',
          confidence REAL NOT NULL DEFAULT 0,
          origin TEXT NOT NULL DEFAULT 'summary',
          source_day TEXT NOT NULL DEFAULT '',
          supersedes_id TEXT NOT NULL DEFAULT '',
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL,
          reviewed_at REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_evidence (
          knowledge_id TEXT NOT NULL,
          source TEXT NOT NULL,
          conversation_id TEXT NOT NULL,
          message_index INTEGER NOT NULL DEFAULT -1,
          quote TEXT NOT NULL DEFAULT '',
          created_at REAL NOT NULL,
          PRIMARY KEY (knowledge_id, source, conversation_id, message_index)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_revisions (
          id TEXT PRIMARY KEY,
          knowledge_id TEXT NOT NULL,
          revision_no INTEGER NOT NULL,
          action TEXT NOT NULL,
          snapshot_json TEXT NOT NULL,
          changed_at REAL NOT NULL,
          UNIQUE (knowledge_id, revision_no)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_relations (
          source_knowledge_id TEXT NOT NULL,
          target_knowledge_id TEXT NOT NULL,
          relation TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'open',
          reason TEXT NOT NULL DEFAULT '',
          created_at REAL NOT NULL,
          resolved_at REAL NOT NULL DEFAULT 0,
          PRIMARY KEY (source_knowledge_id, target_knowledge_id, relation)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_runs (
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          title TEXT NOT NULL,
          status TEXT NOT NULL,
          project_id TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT '',
          conversation_id TEXT NOT NULL DEFAULT '',
          model TEXT NOT NULL DEFAULT '',
          summary TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          error TEXT NOT NULL DEFAULT '',
          started_at REAL NOT NULL,
          ended_at REAL NOT NULL,
          duration_ms INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS artifacts (
          id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL DEFAULT '',
          project_id TEXT NOT NULL DEFAULT '',
          kind TEXT NOT NULL,
          name TEXT NOT NULL,
          path TEXT NOT NULL DEFAULT '',
          mime TEXT NOT NULL DEFAULT '',
          size INTEGER NOT NULL DEFAULT 0,
          content_hash TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_summaries (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL DEFAULT '',
          model TEXT NOT NULL DEFAULT '',
          focus TEXT NOT NULL DEFAULT '',
          source_refs_json TEXT NOT NULL DEFAULT '[]',
          content_md TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'ready',
          error TEXT NOT NULL DEFAULT '',
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_roots (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          root_path TEXT NOT NULL,
          canonical_path TEXT NOT NULL,
          origin TEXT NOT NULL DEFAULT 'conversation_cwd',
          enabled INTEGER NOT NULL DEFAULT 0,
          confirmed_at REAL NOT NULL DEFAULT 0,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL,
          UNIQUE (project_id, canonical_path)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_file_scans (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          status TEXT NOT NULL,
          root_count INTEGER NOT NULL DEFAULT 0,
          visited_count INTEGER NOT NULL DEFAULT 0,
          returned_count INTEGER NOT NULL DEFAULT 0,
          excluded_count INTEGER NOT NULL DEFAULT 0,
          error_count INTEGER NOT NULL DEFAULT 0,
          truncated INTEGER NOT NULL DEFAULT 0,
          error TEXT NOT NULL DEFAULT '',
          started_at REAL NOT NULL,
          finished_at REAL NOT NULL,
          duration_ms INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_files (
          id TEXT NOT NULL UNIQUE,
          project_id TEXT NOT NULL,
          path TEXT NOT NULL,
          root_path TEXT NOT NULL,
          name TEXT NOT NULL,
          extension TEXT NOT NULL DEFAULT '',
          category TEXT NOT NULL DEFAULT 'other',
          size INTEGER NOT NULL DEFAULT 0,
          modified_at REAL NOT NULL,
          first_seen_at REAL NOT NULL,
          last_seen_at REAL NOT NULL,
          previous_modified_at REAL NOT NULL DEFAULT 0,
          change_state TEXT NOT NULL DEFAULT 'seen',
          pinned INTEGER NOT NULL DEFAULT 0,
          role TEXT NOT NULL DEFAULT 'support',
          user_label TEXT NOT NULL DEFAULT '',
          exists_now INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY (project_id, path)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_management (
          instance_id TEXT PRIMARY KEY,
          canonical_name TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT '',
          favorite INTEGER NOT NULL DEFAULT 0,
          tags TEXT NOT NULL DEFAULT '[]',
          note TEXT NOT NULL DEFAULT '',
          updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_project_links (
          instance_id TEXT NOT NULL,
          project_id TEXT NOT NULL,
          locked INTEGER NOT NULL DEFAULT 1,
          updated_at REAL NOT NULL,
          PRIMARY KEY (instance_id, project_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_plans (
          project_id TEXT PRIMARY KEY,
          plan_json TEXT NOT NULL DEFAULT '{}',
          source_hash TEXT NOT NULL DEFAULT '',
          generator TEXT NOT NULL DEFAULT 'template',
          model TEXT NOT NULL DEFAULT '',
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_exports (
          knowledge_id TEXT NOT NULL,
          destination TEXT NOT NULL,
          path TEXT NOT NULL,
          content_hash TEXT NOT NULL DEFAULT '',
          exported_at REAL NOT NULL,
          PRIMARY KEY (knowledge_id, destination)
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

    ensure_column("knowledge_items", "valid_from", "REAL NOT NULL DEFAULT 0")
    ensure_column("knowledge_items", "valid_until", "REAL NOT NULL DEFAULT 0")
    ensure_column("knowledge_items", "revoked_at", "REAL NOT NULL DEFAULT 0")
    ensure_column("knowledge_items", "last_used_at", "REAL NOT NULL DEFAULT 0")
    ensure_column("knowledge_items", "usage_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column("knowledge_items", "sensitivity", "TEXT NOT NULL DEFAULT 'normal'")
    ensure_column("knowledge_items", "review_note", "TEXT NOT NULL DEFAULT ''")
    ensure_column("knowledge_evidence", "evidence_status", "TEXT NOT NULL DEFAULT 'unchecked'")
    ensure_column("knowledge_evidence", "content_hash", "TEXT NOT NULL DEFAULT ''")
    ensure_column("knowledge_evidence", "checked_at", "REAL NOT NULL DEFAULT 0")
    ensure_column("project_files", "id", "TEXT NOT NULL DEFAULT ''")
    for row in conn.execute("SELECT project_id,path FROM project_files WHERE id='' OR id IS NULL"):
        file_key = f"{row['project_id']}:{row['path']}"
        conn.execute(
            "UPDATE project_files SET id=? WHERE project_id=? AND path=?",
            (
                f"file-{hashlib.sha1(file_key.encode('utf-8')).hexdigest()[:24]}",
                row["project_id"],
                row["path"],
            ),
        )
    for row in conn.execute(
        """
        SELECT k.* FROM knowledge_items k
        WHERE NOT EXISTS(
          SELECT 1 FROM knowledge_revisions r WHERE r.knowledge_id=k.id
        )
        """
    ).fetchall():
        snapshot = {
            key: row[key]
            for key in (
                "id", "type", "title", "content", "scope", "project_id", "status",
                "confidence", "origin", "source_day", "supersedes_id", "valid_from",
                "valid_until", "revoked_at", "sensitivity", "review_note", "updated_at",
            )
        }
        conn.execute(
            """
            INSERT OR IGNORE INTO knowledge_revisions(
              id,knowledge_id,revision_no,action,snapshot_json,changed_at
            ) VALUES(?,?,1,'migration',?,?)
            """,
            (
                f"rev-{row['id']}-1-migration",
                row["id"],
                json.dumps(snapshot, ensure_ascii=False),
                float(row["updated_at"]),
            ),
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_assignments_project ON project_assignments(project_id)")
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_milestones_project ON project_milestones(project_id,occurred_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge_items(status,updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_project ON knowledge_items(project_id,status,updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_evidence_conversation ON knowledge_evidence(source,conversation_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_revisions_item ON knowledge_revisions(knowledge_id,revision_no)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_relations_source ON knowledge_relations(source_knowledge_id,status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_runs_time ON activity_runs(started_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_runs_project ON activity_runs(project_id,started_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_project ON artifacts(project_id,created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_files_recent ON project_files(project_id,pinned DESC,modified_at DESC)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_project_files_id ON project_files(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_roots_project ON project_roots(project_id,enabled)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_file_scans_project ON project_file_scans(project_id,started_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_management_status ON skill_management(status,favorite,updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_project_links_project ON skill_project_links(project_id,instance_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_exports_time ON knowledge_exports(exported_at DESC)")
    conn.execute(
        """
        INSERT OR IGNORE INTO schema_migrations(version,description,applied_at)
        VALUES(10,'auditable knowledge, activity ledger, and project files',?)
        """,
        (time.time(),),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO schema_migrations(version,description,applied_at)
        VALUES(11,'cross-agent project detection rules and dry-run preview',?)
        """,
        (time.time(),),
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
    conn.execute(
        """
        INSERT OR IGNORE INTO schema_migrations(version,description,applied_at)
        VALUES(14,'skill inventory, management metadata, and project links',?)
        """,
        (time.time(),),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO schema_migrations(version,description,applied_at)
        VALUES(15,'project coach plans and reviewed Obsidian knowledge export',?)
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


def protect_secret(value: str) -> str:
    if not value:
        return ""
    if sys.platform == "darwin":
        account = os.environ.get("USER") or Path.home().name
        try:
            result = subprocess.run(
                [
                    "/usr/bin/security", "add-generic-password",
                    "-a", account,
                    "-s", "AIConversationHub.summary_api_key",
                    "-w", value,
                    "-U",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"无法写入 macOS 钥匙串：{exc}") from exc
        if result.returncode != 0:
            raise ValueError(f"无法写入 macOS 钥匙串：{clean_text(result.stderr, 300)}")
        return "macos-keychain-v1"
    if os.name == "nt":
        try:
            import win32crypt

            encrypted = win32crypt.CryptProtectData(
                value.encode("utf-8"),
                "AIConversationHub",
                None,
                None,
                None,
                0,
            )
        except (ImportError, OSError) as exc:
            raise ValueError(f"无法使用 Windows DPAPI 加密密钥：{exc}") from exc
        return base64.b64encode(encrypted).decode("ascii")
    raise ValueError("当前系统不支持安全保存 API 密钥，请改用环境变量")


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if sys.platform == "darwin" and value == "macos-keychain-v1":
        account = os.environ.get("USER") or Path.home().name
        try:
            result = subprocess.run(
                [
                    "/usr/bin/security", "find-generic-password",
                    "-a", account,
                    "-s", "AIConversationHub.summary_api_key",
                    "-w",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return result.stdout.rstrip("\r\n") if result.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""
    if os.name == "nt":
        try:
            import win32crypt

            encrypted = base64.b64decode(value.encode("ascii"), validate=True)
            result = win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)
            decrypted = result[1] if isinstance(result, tuple) else result
            return decrypted.decode("utf-8")
        except (ImportError, OSError, ValueError):
            return ""
    return ""


def clear_protected_secret(value: str) -> None:
    if sys.platform != "darwin" or value != "macos-keychain-v1":
        return
    account = os.environ.get("USER") or Path.home().name
    try:
        subprocess.run(
            [
                "/usr/bin/security", "delete-generic-password",
                "-a", account,
                "-s", "AIConversationHub.summary_api_key",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


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


def detect_model_provider(api_url: str) -> str:
    parsed = urllib.parse.urlsplit(str(api_url or "").strip())
    hostname = (parsed.hostname or "").casefold()
    try:
        port = parsed.port
    except ValueError:
        port = None
    if hostname.endswith("paratera.com"):
        return "paratera"
    if hostname.endswith("agentrouter.org"):
        return "agentrouter"
    if hostname in {"127.0.0.1", "localhost", "::1"} and port == 11434:
        return "ollama"
    if hostname in {"127.0.0.1", "localhost", "::1"} and port == 1234:
        return "lmstudio"
    if hostname.endswith("openai.com"):
        return "openai"
    return "custom"


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


def summary_runtime_config() -> dict[str, Any]:
    settings = read_app_settings()
    saved_secret = settings.get("summary_api_key_dpapi", "")
    saved_key = unprotect_secret(saved_secret)
    api_url = settings.get("summary_api_url", SUMMARY_API_URL).strip()
    model = settings.get("summary_model", SUMMARY_MODEL).strip()
    default_enabled = bool(SUMMARY_API_URL and SUMMARY_MODEL)
    enabled = settings.get("summary_enabled", "1" if default_enabled else "0") == "1"
    api_key = saved_key or SUMMARY_API_KEY
    try:
        temperature = min(1.0, max(0.0, float(settings.get("summary_temperature", "0.2"))))
    except ValueError:
        temperature = 0.2
    try:
        max_tokens = min(8192, max(256, int(settings.get("summary_max_tokens", "2400"))))
    except ValueError:
        max_tokens = 2400
    try:
        timeout = min(300, max(10, int(settings.get("summary_timeout", "120"))))
    except ValueError:
        timeout = 120
    provider = settings.get("summary_provider", "").strip() or detect_model_provider(api_url)
    cached_models: list[dict[str, Any]] = []
    if settings.get("summary_models_api_url") == api_url:
        try:
            value = json.loads(settings.get("summary_models_json", "[]"))
            if isinstance(value, list):
                cached_models = [item for item in value if isinstance(item, dict)][:1000]
        except (TypeError, ValueError, json.JSONDecodeError):
            cached_models = []
    return {
        "enabled": enabled,
        "provider": provider,
        "api_url": api_url,
        "model": model,
        "fallback_model": settings.get("summary_fallback_model", "").strip(),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": timeout,
        "api_key": api_key,
        "has_api_key": bool(api_key),
        "key_source": (
            "keychain"
            if saved_key and sys.platform == "darwin"
            else ("dpapi" if saved_key else ("environment" if SUMMARY_API_KEY else "none"))
        ),
        "secret_marker": saved_secret,
        "saved": "summary_api_url" in settings or "summary_model" in settings,
        "models": cached_models,
        "models_updated_at": float(settings.get("summary_models_updated_at", "0") or 0),
    }


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
    return text[:limit].rstrip(" ：:；;，,。.!！？?\"'“”「」") or "梳理今天的重点工作"


def safe_filename(value: Any, fallback: str = "conversation-export") -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(value or "")).strip(" .-")
    text = re.sub(r"\s+", " ", text)
    return clean_text(text, 80) or fallback


def markdown_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


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


def redact_model_text(value: str) -> str:
    text = str(value or "")
    patterns = (
        (r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", "Bearer [REDACTED]"),
        (r"\b(?:sk|ghp|github_pat|xox[baprs])[-_A-Za-z0-9]{12,}", "[REDACTED_TOKEN]"),
        (
            r"(?i)\b(api[_ -]?key|access[_ -]?token|secret|password)\b(\s*[:=]\s*)[^\s,;\"']{6,}",
            r"\1\2[REDACTED]",
        ),
    )
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


def claim_text(value: str, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?])\s*", text) if part.strip()]
    preferred = next((part for part in parts if 12 <= len(part) <= limit), parts[0] if parts else text)
    return clean_text(preferred, limit)


SearchNode = tuple[Any, ...]


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
            tokens.append(("TERM", word.casefold()))
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
        self._skill_inventory_cache: dict[str, Any] = {
            "signature": "",
            "built_at": 0.0,
            "items": [],
        }
        self._skill_fingerprint_cache: dict[str, dict[str, Any]] = {}
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
            self._sync_projects()
            threading.Thread(
                target=self._background_index_maintenance,
                args=(list(items), signature),
                name="hub-index-maintenance",
                daemon=True,
            ).start()

    @staticmethod
    def _configured_project_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        projects = {
            str(row["id"]): str(row["name"])
            for row in conn.execute("SELECT id,name FROM projects")
        }
        configured: list[dict[str, Any]] = []
        configured_ids: set[str] = set()
        for row in conn.execute("SELECT * FROM project_detection_rules"):
            project_id = str(row["project_id"])
            if project_id not in projects:
                continue
            configured_ids.add(project_id)
            if not bool(row["enabled"]):
                continue
            try:
                include_keywords = json.loads(row["include_keywords"] or "[]")
                exclude_keywords = json.loads(row["exclude_keywords"] or "[]")
                workspace_aliases = json.loads(row["workspace_aliases"] or "[]")
                path_patterns = json.loads(row["path_patterns"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            configured.append(
                {
                    "id": project_id,
                    "name": projects[project_id],
                    "include_keywords": include_keywords,
                    "exclude_keywords": exclude_keywords,
                    "workspace_aliases": workspace_aliases,
                    "path_patterns": path_patterns,
                    "min_score": float(row["min_score"]),
                    "method": "rule",
                }
            )
        for rule in PROJECT_RULES:
            if rule["id"] in configured_ids:
                continue
            configured.append(
                {
                    "id": rule["id"],
                    "name": rule["name"],
                    "include_keywords": list(rule["keywords"]),
                    "exclude_keywords": [],
                    "workspace_aliases": [],
                    "path_patterns": [],
                    "min_score": 0.78,
                    "method": "keyword",
                }
            )
        return configured

    @staticmethod
    def _project_rule_evidence(
        item: Conversation,
        rules: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        if rules is None:
            rules = [
                {
                    "id": rule["id"],
                    "name": rule["name"],
                    "include_keywords": list(rule["keywords"]),
                    "exclude_keywords": [],
                    "workspace_aliases": [],
                    "path_patterns": [],
                    "min_score": 0.78,
                    "method": "keyword",
                }
                for rule in PROJECT_RULES
            ]
        fields = {
            "标题": item.title.casefold(),
            "摘要": item.preview.casefold(),
            "备注/标签": f"{item.note}\n{' '.join(item.tags or [])}".casefold(),
        }
        all_text = "\n".join(fields.values())
        workspace = (item.workspace or "").strip().casefold()
        path = normalized_project_path(item.cwd)
        matches: list[dict[str, Any]] = []
        for rule in rules:
            excluded = [
                str(keyword)
                for keyword in rule.get("exclude_keywords", [])
                if str(keyword).strip().casefold() in all_text
            ]
            if excluded:
                continue
            evidence: list[dict[str, Any]] = []
            score = 0.0
            semantic_match = False
            field_weights = {"标题": 0.82, "摘要": 0.58, "备注/标签": 0.84}
            for keyword in rule.get("include_keywords", []):
                keyword_value = str(keyword).strip()
                if not keyword_value:
                    continue
                locations = [
                    name for name, value in fields.items()
                    if keyword_value.casefold() in value
                ]
                if locations:
                    evidence.append({"kind": "关键词", "keyword": keyword_value, "locations": locations})
                    score += max(field_weights[name] for name in locations)
                    semantic_match = True
            if not semantic_match:
                continue
            for alias in rule.get("workspace_aliases", []):
                alias_value = str(alias).strip().casefold()
                if alias_value and (
                    workspace == alias_value
                    or normalized_project_path(item.cwd).endswith(f"/{normalized_project_path(alias_value)}")
                ):
                    evidence.append({"kind": "工作区别名", "keyword": str(alias), "locations": ["工作区"]})
                    score += 0.92
            for pattern in rule.get("path_patterns", []):
                pattern_value = normalized_project_path(str(pattern))
                if pattern_value and pattern_value in path:
                    evidence.append({"kind": "路径特征", "keyword": str(pattern), "locations": ["路径"]})
                    score += 0.88
            confidence = min(0.99, score)
            if evidence and confidence >= float(rule.get("min_score", 0.78)):
                matches.append(
                    {
                        "score": score,
                        "confidence": confidence,
                        "rule": rule,
                        "evidence": evidence,
                    }
                )
        if not matches:
            return None
        matches.sort(key=lambda value: value["score"], reverse=True)
        if len(matches) > 1 and matches[0]["score"] - matches[1]["score"] < 0.12:
            return {
                "ambiguous": True,
                "candidates": [
                    value["rule"]["name"]
                    for value in matches
                    if matches[0]["score"] - value["score"] < 0.12
                ],
                "score": matches[0]["score"],
            }
        winner = matches[0]
        rule = winner["rule"]
        return {
            "ambiguous": False,
            "project_id": rule["id"],
            "project_name": rule["name"],
            "confidence": float(winner["confidence"]),
            "score": float(winner["score"]),
            "method": str(rule.get("method") or "rule"),
            "evidence": winner["evidence"],
        }

    @classmethod
    def _project_rule(
        cls,
        item: Conversation,
        rules: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str, float, str] | None:
        detail = cls._project_rule_evidence(item, rules)
        if not detail or detail.get("ambiguous"):
            return None
        return (
            str(detail["project_id"]),
            str(detail["project_name"]),
            float(detail["confidence"]),
            str(detail.get("method") or "rule"),
        )

    def _sync_projects(self) -> None:
        now = time.time()
        with self._lock:
            items = list(self._items)
        with notes_db() as conn:
            locked = {
                (row["source"], row["conversation_id"]): row
                for row in conn.execute("SELECT * FROM project_assignments WHERE locked=1")
            }
            alias_map = {
                str(row["source_project_id"]): str(row["target_project_id"])
                for row in conn.execute("SELECT source_project_id,target_project_id FROM project_aliases")
            }
            existing_projects = {
                str(row["id"]): dict(row)
                for row in conn.execute("SELECT id,name,origin FROM projects")
            }
            project_rules = self._configured_project_rules(conn)
            known_project_ids = {row["id"] for row in conn.execute("SELECT id FROM projects")}
            assignments: dict[tuple[str, str], str] = {
                key: str(row["project_id"]) for key, row in locked.items()
            }
            project_names: dict[str, tuple[str, str]] = {
                rule["id"]: (rule["name"], "rule") for rule in PROJECT_RULES
            }
            for item in items:
                key = (item.source, item.id)
                if key in locked:
                    continue
                matched = self._project_rule(item, project_rules)
                if matched:
                    project_id, project_name, confidence, method = matched
                    original_project_id = project_id
                    visited_aliases: set[str] = set()
                    while project_id in alias_map and project_id not in visited_aliases:
                        visited_aliases.add(project_id)
                        project_id = alias_map[project_id]
                    if project_id != original_project_id and project_id in existing_projects:
                        project_name = str(existing_projects[project_id]["name"])
                    project_names[project_id] = (project_name, "rule")
                else:
                    conn.execute(
                        "DELETE FROM project_assignments WHERE source=? AND conversation_id=? AND locked=0",
                        key,
                    )
                    continue
                assignments[key] = project_id
                conn.execute(
                    """
                    INSERT INTO project_assignments(
                      source,conversation_id,project_id,confidence,method,locked,updated_at
                    ) VALUES(?,?,?,?,?,0,?)
                    ON CONFLICT(source,conversation_id) DO UPDATE SET
                      project_id=excluded.project_id,
                      confidence=excluded.confidence,
                      method=excluded.method,
                      updated_at=excluded.updated_at
                    WHERE project_assignments.locked=0
                    """,
                    (item.source, item.id, project_id, confidence, method, now),
                )
            for project_id, (name, origin) in project_names.items():
                if project_id not in assignments.values() and project_id not in known_project_ids:
                    continue
                conn.execute(
                    """
                    INSERT INTO projects(id,name,description,status,origin,created_at,updated_at)
                    VALUES(?,?,?,'active',?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                      name=CASE WHEN projects.origin='manual' THEN projects.name ELSE excluded.name END,
                      updated_at=excluded.updated_at
                    """,
                    (project_id, name, "", origin, now, now),
                )
            project_items: dict[str, list[Conversation]] = {}
            for item in items:
                project_id = assignments.get((item.source, item.id))
                if project_id:
                    project_items.setdefault(project_id, []).append(item)
            for project_id, grouped in project_items.items():
                last_activity = max(item.updated_at for item in grouped)
                auto_status = "active" if now - last_activity <= 14 * 86400 else "maintenance"
                conn.execute(
                    """
                    UPDATE projects
                    SET status=CASE WHEN origin='manual' THEN status ELSE ? END,updated_at=?
                    WHERE id=?
                    """,
                    (auto_status, now, project_id),
                )
                self._rebuild_project_milestones(conn, project_id, grouped, now)
            conn.commit()

    def _sync_conversation_relations(self) -> None:
        """Build conservative cross-Agent continuation links in the Hub database."""
        now = time.time()
        with self._lock:
            items = list(self._items)
        by_key = {(item.source, item.id): item for item in items}
        with notes_db() as conn:
            assignments = {
                (str(row["source"]), str(row["conversation_id"])): str(row["project_id"])
                for row in conn.execute(
                    "SELECT source,conversation_id,project_id FROM project_assignments"
                )
            }
            locked_pairs = {
                (
                    str(row["source_a"]),
                    str(row["conversation_id_a"]),
                    str(row["source_b"]),
                    str(row["conversation_id_b"]),
                )
                for row in conn.execute(
                    "SELECT source_a,conversation_id_a,source_b,conversation_id_b "
                    "FROM conversation_relations WHERE locked=1"
                )
            }
            conn.execute("DELETE FROM conversation_relations WHERE locked=0")

            project_groups: dict[str, list[Conversation]] = {}
            for item in items:
                project_id = assignments.get((item.source, item.id))
                if project_id:
                    project_groups.setdefault(project_id, []).append(item)

            candidates: list[tuple[float, tuple[str, str], tuple[str, str], dict[str, Any]]] = []
            for project_id, grouped in project_groups.items():
                if len(grouped) < 2:
                    continue
                for index, left in enumerate(grouped):
                    for right in grouped[index + 1 :]:
                        if left.source == right.source:
                            continue
                        title_score = text_similarity(left.title, right.title)
                        preview_score = text_similarity(left.preview, right.preview)
                        same_workspace = bool(
                            left.workspace
                            and right.workspace
                            and left.workspace.casefold() == right.workspace.casefold()
                            and left.workspace.casefold() not in GENERIC_WORKSPACES
                        )
                        seconds_apart = abs(left.updated_at - right.updated_at)
                        explicit = (
                            right.id in f"{left.title}\n{left.preview}\n{left.note}"
                            or left.id in f"{right.title}\n{right.preview}\n{right.note}"
                        )
                        project_continuity = (
                            seconds_apart <= 2 * 86400
                            or (same_workspace and seconds_apart <= 14 * 86400)
                        )
                        if (
                            not explicit
                            and not project_continuity
                            and title_score < 0.55
                            and preview_score < 0.64
                        ):
                            continue
                        confidence = (
                            (0.64 if project_continuity else 0.35)
                            + title_score * 0.28
                            + preview_score * 0.08
                        )
                        if same_workspace:
                            confidence += 0.08
                        if seconds_apart <= 7 * 86400:
                            confidence += 0.07
                        if explicit:
                            confidence = 0.99
                        confidence = min(0.99, confidence)
                        if confidence < 0.72:
                            continue
                        left_key = (left.source, left.id)
                        right_key = (right.source, right.id)
                        key_a, key_b = sorted((left_key, right_key))
                        evidence = {
                            "project_id": project_id,
                            "title_similarity": round(title_score, 3),
                            "preview_similarity": round(preview_score, 3),
                            "same_workspace": same_workspace,
                            "days_apart": round(seconds_apart / 86400, 2),
                            "explicit_reference": explicit,
                            "project_continuity": project_continuity,
                        }
                        candidates.append((confidence, key_a, key_b, evidence))

            per_item: dict[tuple[str, str], int] = {}
            accepted: set[tuple[str, str, str, str]] = set()
            for confidence, key_a, key_b, evidence in sorted(candidates, reverse=True):
                relation_key = (key_a[0], key_a[1], key_b[0], key_b[1])
                if relation_key in accepted or relation_key in locked_pairs:
                    continue
                if per_item.get(key_a, 0) >= 3 or per_item.get(key_b, 0) >= 3:
                    continue
                if key_a not in by_key or key_b not in by_key:
                    continue
                conn.execute(
                    """
                    INSERT INTO conversation_relations(
                      source_a,conversation_id_a,source_b,conversation_id_b,
                      relation,confidence,evidence_json,locked,updated_at
                    ) VALUES(?,?,?,?,'continuation',?,?,0,?)
                    """,
                    (
                        key_a[0],
                        key_a[1],
                        key_b[0],
                        key_b[1],
                        confidence,
                        json.dumps(evidence, ensure_ascii=False),
                        now,
                    ),
                )
                accepted.add(relation_key)
                per_item[key_a] = per_item.get(key_a, 0) + 1
                per_item[key_b] = per_item.get(key_b, 0) + 1
            conn.commit()

    @staticmethod
    def _rebuild_project_milestones(
        conn: sqlite3.Connection,
        project_id: str,
        items: list[Conversation],
        now: float,
    ) -> None:
        conn.execute("DELETE FROM project_milestones WHERE project_id=? AND origin='auto'", (project_id,))
        ordered = sorted(items, key=lambda item: (item.updated_at, item.created_at))
        special_meta: dict[str, tuple[str, str]] = {}
        explicit: dict[str, list[Conversation]] = {}
        for item in ordered:
            matches = re.findall(r"(?i)(?<![a-z0-9])v\s*(\d+(?:\.\d+)*)", f"{item.title} {item.preview}")
            if matches:
                explicit.setdefault(f"v{matches[-1]}", []).append(item)
        groups: list[tuple[str, list[Conversation]]]
        if project_id == "ai-conversation-hub" and ordered:
            special = (
                ("v1", "统一搜索", "建立跨来源对话索引与统一搜索入口。"),
                ("v5", "WorkBuddy 接入", "接入 WorkBuddy 主对话并完善来源修复与打开方式。"),
                ("v7", "每日回顾与模型摘要", "按消息日期生成可追溯日报，并接入可配置摘要模型。"),
                ("v9", "项目、知识与续接", "加入多项目归类、知识审核、批量导出和 Context Pack。"),
                ("v10", "审计、便携与高级检索", "加入知识修订、运行账本、项目文件、首次配置和布尔搜索。"),
                ("v11", "跨 Agent 项目规则", "用包含词、排除词、工作区别名和路径特征统一识别多个 Agent 中的同一项目。"),
                ("v12", "可插拔本地数据源", "接入 Claude Code、CodePilot、Cursor、Marvis、QClaw 和 QoderWork，并支持首次选择与中途增删。"),
                ("v13", "可靠性与备份", "加入来源健康检查、持久搜索、会话关系、管理数据备份和更新检查。"),
                ("v14", "Skill 资产库", "统一发现 Skill，展示详情、来源、状态和项目关系。"),
                ("v15", "自定义 Agent", "允许用户添加未预置的本地 Agent 数据源，并在新电脑重新配置。"),
                ("v16", "Claude 与助理来源增强", "强化 Claude Code 历史正文判定，并接入 WorkBuddy 助理对话。"),
                ("v17", "项目教练与知识归档", "加入新手项目计划，并把模型生成、人工审核与 Obsidian 归档连成安全流程。"),
                ("v18", "macOS 第一版", "加入 macOS 数据目录、来源发现、Keychain、Finder、双架构应用构建与 DMG 安装流程。"),
            )
            groups = []
            for index, (version, title, summary) in enumerate(special):
                item_index = round(index * (len(ordered) - 1) / max(1, len(special) - 1))
                groups.append((version, [ordered[item_index]]))
                special_meta[version] = (title, summary)
        elif len(explicit) >= 2:
            groups = sorted(
                explicit.items(),
                key=lambda value: max(item.updated_at for item in value[1]),
            )[-5:]
        else:
            bucket_count = min(5, max(1, len(ordered)))
            groups = []
            for index in range(bucket_count):
                start = round(index * len(ordered) / bucket_count)
                end = round((index + 1) * len(ordered) / bucket_count)
                chunk = ordered[start:end]
                if chunk:
                    groups.append((f"阶段 {index + 1}", chunk))
        for version, grouped in groups:
            latest = max(grouped, key=lambda item: item.updated_at)
            occurred_at = max(item.updated_at for item in grouped)
            active = now - occurred_at <= 7 * 86400 and not any(item.user_status == "done" for item in grouped)
            evidence = [
                {
                    "source": item.source,
                    "id": item.id,
                    "title": item.title,
                    "updated_at": item.updated_at,
                }
                for item in sorted(grouped, key=lambda item: item.updated_at, reverse=True)[:8]
            ]
            milestone_id = hashlib.sha1(f"{project_id}|{version}".encode("utf-8")).hexdigest()[:20]
            special_title, special_summary = special_meta.get(
                version,
                (clean_text(latest.title, 160), clean_text(latest.preview, 420)),
            )
            conn.execute(
                """
                INSERT INTO project_milestones(
                  id,project_id,version,title,summary,occurred_at,status,
                  evidence_json,origin,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,'auto',?,?)
                """,
                (
                    milestone_id,
                    project_id,
                    version,
                    special_title,
                    special_summary,
                    occurred_at,
                    "in_progress" if active else "done",
                    json.dumps(evidence, ensure_ascii=False),
                    now,
                    now,
                ),
            )

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
                    text = redact_model_text(text)
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
            assignment = conn.execute(
                "SELECT * FROM project_assignments WHERE source=? AND conversation_id=?",
                (source, conversation_id),
            ).fetchone()
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
                "text": clean_text(redact_model_text(str(message.get("text") or "")), 5000),
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
        }
        if reason:
            result["reason"] = clean_text(reason, 300)
        if next_action:
            result["next_action"] = clean_text(next_action, 300)
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
            candidate = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", candidate)
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
            return compact_focus_text(candidate)

        activities: list[dict[str, str]] = []
        achievements: list[dict[str, str]] = []
        decisions: list[dict[str, str]] = []
        unfinished: list[dict[str, str]] = []
        ongoing: list[dict[str, str]] = []
        blocked: list[dict[str, str]] = []
        next_actions: list[dict[str, str]] = []
        focus_scores: list[tuple[float, dict[str, Any], str]] = []

        for entry in entries:
            request = claim_text(entry["latest_user"], 160)
            response = claim_text(entry["latest_assistant"], 180)
            assistant_messages = [
                message["text"] for message in entry["messages"] if message["role"] == "assistant"
            ]
            all_messages = [message["text"] for message in entry["messages"]]
            title = clean_text(entry["title"], 120)
            topic = readable_topic(title, request)
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
            focus_scores.append((score, entry, topic))

        focus_scores.sort(key=lambda item: item[0], reverse=True)
        main_entry = focus_scores[0][1]
        main_topic = focus_scores[0][2]
        main_focus = [self._daily_ref(main_entry, main_topic)]

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
        for _, _, topic in focus_scores[:3]:
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
    def _normalise_daily_summary(
        raw: dict[str, Any],
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        valid = {(entry["source"], entry["id"]) for entry in entries}
        overview = clean_text(raw.get("overview_sentence") or raw.get("overview"), 1600)
        result: dict[str, Any] = {
            "overview": overview,
            "overview_sentence": overview,
            "narrative": clean_text(raw.get("narrative"), 2400),
            "completion_summary": clean_text(raw.get("completion_summary"), 1000),
            "unfinished_summary": clean_text(raw.get("unfinished_summary"), 1200),
            "next_step_summary": clean_text(raw.get("next_step_summary"), 600),
        }
        keys = (
            "main_focus",
            "activities",
            "achievements",
            "decisions",
            "unfinished",
            "first_step",
            "ongoing",
            "blocked",
            "next_actions",
        )
        for key in keys:
            values = []
            raw_values = raw.get(key) or []
            if isinstance(raw_values, (str, dict)):
                raw_values = [raw_values]
            for item in raw_values:
                if isinstance(item, str):
                    text = clean_text(item, 220)
                    source = ""
                    conversation_id = ""
                    reason = ""
                    next_action = ""
                elif isinstance(item, dict):
                    text = clean_text(item.get("text"), 220)
                    source = clean_text(item.get("source"), 30)
                    conversation_id = clean_text(item.get("conversation_id"), 200)
                    reason = clean_text(item.get("reason"), 180)
                    next_action = clean_text(item.get("next_action"), 180)
                else:
                    continue
                if key == "main_focus":
                    text = compact_focus_text(text)
                if not text:
                    continue
                if (source, conversation_id) not in valid:
                    source = ""
                    conversation_id = ""
                value = {
                    "text": text,
                    "source": source,
                    "conversation_id": conversation_id,
                }
                if reason:
                    value["reason"] = reason
                if next_action:
                    value["next_action"] = next_action
                values.append(value)
            limit = 1 if key in {"main_focus", "first_step"} else (3 if key == "achievements" else 12)
            result[key] = values[:limit]
        if not result["overview"]:
            result["overview"] = (
                result["main_focus"][0]["text"]
                if result["main_focus"]
                else "当天摘要已生成，请结合证据对话核对。"
            )
            result["overview_sentence"] = result["overview"]
        if not result["activities"]:
            result["activities"] = result["main_focus"]
        if not result["ongoing"]:
            result["ongoing"] = result["unfinished"]
        if not result["next_actions"]:
            result["next_actions"] = result["first_step"]
        if not result["completion_summary"]:
            result["completion_summary"] = (
                "已经完成的工作包括：" + " ".join(item["text"] for item in result["achievements"])
                if result["achievements"]
                else "今天暂时没有识别到可以核验的完成成果。"
            )
        if not result["unfinished_summary"]:
            result["unfinished_summary"] = (
                "仍需继续处理的事项包括：" + " ".join(item["text"] for item in result["unfinished"])
                if result["unfinished"]
                else "目前没有识别到明确遗留的未完成事项。"
            )
        if not result["next_step_summary"]:
            result["next_step_summary"] = (
                result["first_step"][0]["text"]
                if result["first_step"]
                else "下一步先核对当天结果，再确定最优先的续接动作。"
            )
        if not result["narrative"]:
            result["narrative"] = "\n\n".join(
                value for value in (
                    result["overview"],
                    result["completion_summary"],
                    result["unfinished_summary"],
                    result["next_step_summary"],
                ) if value
            )
        return result

    @staticmethod
    def _chat_completion(
        config: dict[str, Any],
        messages: list[dict[str, str]],
        timeout: int | None = None,
        max_tokens: int | None = None,
    ) -> str:
        api_url, model = validate_summary_endpoint(
            config.get("api_url", ""),
            config.get("model", ""),
            config.get("api_key", ""),
        )
        endpoint = api_url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        body: dict[str, Any] = {
            "model": model,
            "temperature": min(1.0, max(0.0, float(config.get("temperature", 0.2)))),
            "messages": messages,
        }
        effective_max_tokens = max_tokens or int(config.get("max_tokens") or 0)
        if effective_max_tokens:
            body["max_tokens"] = min(8192, max(1, effective_max_tokens))
        effective_timeout = timeout or min(300, max(10, int(config.get("timeout") or 90)))
        request_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "AIConversationHub/1.0"}
        if config.get("api_key"):
            headers["Authorization"] = f"Bearer {config['api_key']}"
        request = urllib.request.Request(endpoint, data=request_body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=effective_timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read(1200).decode("utf-8", errors="replace")
            raise ValueError(f"模型接口返回 HTTP {exc.code}：{clean_text(detail, 500)}") from exc
        except (OSError, urllib.error.URLError, ValueError, KeyError) as exc:
            raise ValueError(f"模型接口调用失败：{exc}") from exc
        try:
            return str(payload["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("模型接口响应不符合 OpenAI Chat Completions 格式") from exc

    def _model_daily_summary(
        self,
        day: str,
        entries: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        transcripts = []
        remaining = 90_000
        for entry in entries:
            lines = []
            for message in entry["messages"]:
                role = "用户" if message["role"] == "user" else "助手"
                value = f"{role}: {redact_model_text(message['text'])}"
                if len(value) > remaining:
                    value = value[:remaining]
                if value:
                    lines.append(value)
                    remaining -= len(value)
                if remaining <= 0:
                    break
            transcripts.append(
                {
                    "source": entry["source"],
                    "conversation_id": entry["id"],
                    "title": entry["title"],
                    "workspace": entry["workspace"],
                    "manual_status": entry["user_status"],
                    "evidence_level": entry["evidence_level"],
                    "transcript": "\n".join(lines),
                }
            )
            if remaining <= 0:
                break
        system_prompt = (
            "你是本地 AI 对话工作日志整理器。只根据提供的用户/助手正文总结，不推测工具是否真的执行成功。"
            "把助手声称完成的事项写成谨慎、可核查的成果。区分进行中、受阻和状态不明。"
            "evidence_level=metadata_only 表示只有历史请求索引、没有助手回复，绝不能据此认定完成，"
            "只能写入未完成或状态不明；evidence_level=partial 也应降低结论强度。"
            "不得输出系统提示、推理过程、工具调用、密钥或隐私信息。"
            "目标是让用户像阅读一篇简短工作日报一样，自然地看懂今天做了什么、做成了什么、还剩什么。必须返回 JSON 对象，字段为 "
            "overview_sentence、overview、narrative、completion_summary、unfinished_summary、next_step_summary、"
            "main_focus、achievements、decisions、unfinished、first_step。"
            "main_focus 和 first_step 最多一项，achievements 最多三项。"
            "除 overview 外均为对象数组；每项含 text、source、conversation_id 并引用给定对话。"
            "unfinished 项还应包含 reason 和 next_action，明确为什么未完成以及下一步。"
            "overview_sentence 与 overview 内容相同，写成 3-5 句连贯、具体的总览，点名今天主线、1-3 个具体成果、1-2 个未完成项；禁止只写“推进了 N 个对话/形成 X 项成果”这种空统计。"
            "narrative 写成 3-5 个自然段，每段 1-3 句，依次说明今日主线、完成成果、未完成事项及原因、下一步；"
            "completion_summary、unfinished_summary、next_step_summary 都必须是可直接阅读的完整句子或短段落，不得只是标题、关键词或字段值；"
            "main_focus 是今日唯一主线，text 必须是 10-24 个中文字符的极简语义摘要，"
            "要概括正在解决的问题，禁止复制对话标题、原始请求或写成完整句子；achievements 只写可核查产出；"
            "decisions 写决定及原因；first_step 必须是一个可立即执行的动作。"
            "所有 text、reason、next_action 都要具体、通顺且有主谓关系，必须写成完整句子，优先控制在 120 个中文字符以内，去重且不要复述统计数字。"
        )
        content = self._chat_completion(
            config,
            [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"day": day, "conversations": transcripts},
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        try:
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(content).strip(), flags=re.IGNORECASE)
            raw = json.loads(content)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("日报模型没有返回有效 JSON") from exc
        return self._normalise_daily_summary(raw, entries)

    def summary_config(self) -> dict[str, Any]:
        config = summary_runtime_config()
        return {
            "enabled": config["enabled"],
            "provider": config["provider"],
            "api_url": config["api_url"],
            "model": config["model"],
            "fallback_model": config["fallback_model"],
            "temperature": config["temperature"],
            "max_tokens": config["max_tokens"],
            "timeout": config["timeout"],
            "has_api_key": config["has_api_key"],
            "key_source": config["key_source"],
            "saved": config["saved"],
            "models": config["models"],
            "models_updated_at": config["models_updated_at"],
            "secret_storage": secret_storage_label(),
        }

    def save_summary_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        enabled = bool(payload.get("enabled"))
        provider = clean_text(payload.get("provider"), 40) or "custom"
        api_url = str(payload.get("api_url") or "").strip().rstrip("/")
        model = clean_text(payload.get("model"), 200)
        fallback_model = clean_text(payload.get("fallback_model"), 200)
        new_api_key = str(payload.get("api_key") or "").strip()
        clear_api_key = bool(payload.get("clear_api_key"))
        current = summary_runtime_config()
        effective_key = "" if clear_api_key else (new_api_key or current["api_key"])
        if enabled or api_url or model:
            api_url, model = validate_summary_endpoint(api_url, model, effective_key)
        if fallback_model == model:
            fallback_model = ""
        try:
            temperature = min(1.0, max(0.0, float(payload.get("temperature", 0.2))))
            max_tokens = min(8192, max(256, int(payload.get("max_tokens", 2400))))
            timeout = min(300, max(10, int(payload.get("timeout", 120))))
        except (TypeError, ValueError) as exc:
            raise ValueError("摘要参数格式不正确") from exc
        now = time.time()
        values = {
            "summary_enabled": "1" if enabled else "0",
            "summary_provider": provider,
            "summary_api_url": api_url,
            "summary_model": model,
            "summary_fallback_model": fallback_model,
            "summary_temperature": str(temperature),
            "summary_max_tokens": str(max_tokens),
            "summary_timeout": str(timeout),
        }
        with notes_db() as conn:
            for key, value in values.items():
                conn.execute(
                    """
                    INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
                    """,
                    (key, value, now),
                )
            if clear_api_key:
                clear_protected_secret(str(current.get("secret_marker") or ""))
                conn.execute("DELETE FROM app_settings WHERE key='summary_api_key_dpapi'")
            elif new_api_key:
                encrypted = protect_secret(new_api_key)
                conn.execute(
                    """
                    INSERT INTO app_settings(key,value,updated_at) VALUES('summary_api_key_dpapi',?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
                    """,
                    (encrypted, now),
                )
            conn.commit()
        return {"ok": True, **self.summary_config()}

    def discover_summary_models(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = summary_runtime_config()
        api_url = str(payload.get("api_url") or current["api_url"]).strip().rstrip("/")
        supplied_key = str(payload.get("api_key") or "").strip()
        api_key = supplied_key or ("" if payload.get("clear_api_key") else current["api_key"])
        api_url = validate_api_base(api_url, api_key)
        endpoint = api_url if api_url.endswith("/models") else f"{api_url}/models"
        headers = {"Accept": "application/json", "User-Agent": "AIConversationHub/1.0"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(endpoint, headers=headers, method="GET")
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                raw_body = response.read(4_000_000)
                result = json.loads(raw_body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read(1200).decode("utf-8", errors="replace")
            raise ValueError(f"读取模型列表返回 HTTP {exc.code}：{clean_text(detail, 500)}") from exc
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"读取模型列表失败：{exc}") from exc
        rows = result.get("data", result.get("models", result)) if isinstance(result, dict) else result
        if not isinstance(rows, list):
            raise ValueError("模型列表响应不是可识别的 OpenAI 兼容格式")
        models: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows[:2000]:
            if isinstance(row, str):
                model_id = clean_text(row, 240)
                owned_by = ""
            elif isinstance(row, dict):
                model_id = clean_text(row.get("id") or row.get("name") or row.get("model"), 240)
                owned_by = clean_text(row.get("owned_by") or row.get("provider"), 120)
            else:
                continue
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            family, capability, summary_compatible = model_capability(model_id)
            models.append(
                {
                    "id": model_id,
                    "owned_by": owned_by,
                    "family": family,
                    "capability": capability,
                    "summary_compatible": summary_compatible,
                }
            )
        if not models:
            raise ValueError("接口没有返回任何可识别的模型")
        models.sort(key=lambda item: (not item["summary_compatible"], item["family"].casefold(), item["id"].casefold()))
        now = time.time()
        with notes_db() as conn:
            values = {
                "summary_models_json": json.dumps(models, ensure_ascii=False),
                "summary_models_api_url": api_url,
                "summary_models_updated_at": str(now),
            }
            for key, value in values.items():
                conn.execute(
                    """
                    INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
                    """,
                    (key, value, now),
                )
            conn.commit()
        return {
            "ok": True,
            "provider": clean_text(payload.get("provider"), 40) or detect_model_provider(api_url),
            "api_url": api_url,
            "models": models,
            "count": len(models),
            "summary_compatible_count": sum(1 for item in models if item["summary_compatible"]),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "updated_at": now,
        }

    def test_summary_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = summary_runtime_config()
        api_url = str(payload.get("api_url") or current["api_url"]).strip().rstrip("/")
        model = clean_text(payload.get("model") or current["model"], 200)
        supplied_key = str(payload.get("api_key") or "").strip()
        api_key = supplied_key or ("" if payload.get("clear_api_key") else current["api_key"])
        try:
            temperature = min(1.0, max(0.0, float(payload.get("temperature", current["temperature"]))))
        except (TypeError, ValueError):
            temperature = current["temperature"]
        started = time.perf_counter()
        reply = self._chat_completion(
            {
                "api_url": api_url,
                "model": model,
                "api_key": api_key,
                "temperature": temperature,
            },
            [
                {"role": "system", "content": "You are a connection test. Reply with only OK."},
                {"role": "user", "content": "OK"},
            ],
            timeout=45,
            max_tokens=12,
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        return {
            "ok": True,
            "model": model,
            "elapsed_ms": elapsed_ms,
            "reply": clean_text(reply, 120),
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
        model_config = summary_runtime_config()
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
                    "model_available": bool(
                        model_config["enabled"]
                        and model_config["api_url"]
                        and model_config["model"]
                    ),
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
            "model_available": bool(
                model_config["enabled"]
                and model_config["api_url"]
                and model_config["model"]
            ),
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

    def generate_daily_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        day, entries, source_hash = self._daily_entries(str(payload.get("day") or ""))
        use_model = bool(payload.get("use_model", False))
        model_config = summary_runtime_config()
        generator = "rules"
        model = ""
        warning = ""
        if (
            use_model
            and model_config["enabled"]
            and model_config["api_url"]
            and model_config["model"]
            and entries
        ):
            try:
                summary = self._model_daily_summary(day, entries, model_config)
                generator = "model"
                model = model_config["model"]
            except ValueError as exc:
                primary_error = str(exc)
                fallback_model = model_config.get("fallback_model", "")
                if fallback_model and fallback_model != model_config["model"]:
                    fallback_config = {**model_config, "model": fallback_model}
                    try:
                        summary = self._model_daily_summary(day, entries, fallback_config)
                        generator = "model_fallback"
                        model = fallback_model
                        warning = f"主模型失败，已切换备用模型：{primary_error}"
                    except ValueError as fallback_exc:
                        summary = self._rules_daily_summary(day, entries)
                        generator = "rules_after_model_error"
                        warning = f"主模型失败：{primary_error}；备用模型失败：{fallback_exc}"
                else:
                    summary = self._rules_daily_summary(day, entries)
                    generator = "rules_after_model_error"
                    warning = primary_error
        else:
            summary = self._rules_daily_summary(day, entries)
        _, _, light_hash = self._daily_light_signature(day)
        self._store_daily_summary(day, light_hash or source_hash, summary, generator, model)
        result = self.daily_summary(day)
        if warning:
            result["warning"] = warning
        self.record_activity(
            "daily_summary",
            "生成每日回顾",
            model=model,
            summary=f"{day} · {generator}",
            metadata={
                "day": day,
                "generator": generator,
                "conversation_count": len(entries),
                "warning": bool(warning),
            },
        )
        return result

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
        self.record_activity(
            "daily_note",
            "更新每日回顾备注",
            summary=day,
            metadata={"day": day, "has_note": bool(note), "character_count": len(note)},
        )
        return {"ok": True, "updated_at": now, "manual_note": note}

    # ---- 对话总结 / 内容分析（模型生成，保存在 hub_notes 自有库） ----

    def conversation_summaries_list(self) -> dict[str, Any]:
        with notes_db() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id,title,model,focus,source_refs_json,status,error,
                           created_at,updated_at,length(content_md) AS content_len
                    FROM conversation_summaries
                    WHERE status != 'archived'
                    ORDER BY created_at DESC
                    LIMIT 500
                    """
                )
            ]
        items = []
        for row in rows:
            try:
                refs = json.loads(row.get("source_refs_json") or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                refs = []
            items.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "model": row["model"],
                    "focus": row["focus"],
                    "status": row["status"],
                    "conversation_count": len(refs) if isinstance(refs, list) else 0,
                    "content_len": int(row.get("content_len") or 0),
                    "created_at": float(row["created_at"]),
                    "updated_at": float(row["updated_at"]),
                }
            )
        return {"items": items, "total": len(items)}

    def conversation_summary_detail(self, summary_id: str) -> dict[str, Any] | None:
        with notes_db() as conn:
            row = conn.execute(
                "SELECT * FROM conversation_summaries WHERE id=?",
                (summary_id,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["source_refs"] = json.loads(data.get("source_refs_json") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            data["source_refs"] = []
        return data

    def generate_conversation_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = summary_runtime_config()
        if not config.get("enabled") or not config.get("has_api_key"):
            raise ValueError("尚未配置可用的总结模型，请先在 设置 → 模型摘要 里填写接口与密钥")

        raw_items = payload.get("conversations") or []
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("请至少选择一个对话")
        if len(raw_items) > 20:
            raise ValueError("一次最多分析 20 个对话，请减少所选数量")
        focus = clean_text(payload.get("focus"), 500)

        with self._lock:
            by_key = dict(self._by_key)
        items: list[Conversation] = []
        for row in raw_items:
            if not isinstance(row, dict):
                continue
            key = (
                clean_text(row.get("source"), 30),
                clean_text(row.get("id") or row.get("conversation_id"), 240),
            )
            item = by_key.get(key)
            if item is not None:
                items.append(item)
        if not items:
            raise ValueError("所选对话不在当前索引中，可能已被数据源移除")
        items.sort(key=lambda item: item.updated_at)

        # 组装脱敏转录：控制总量，避免超出模型上下文
        transcripts = []
        remaining = 90_000
        for item in items:
            lines: list[str] = []
            for message in self._messages_for_item(item, limit=80):
                role = "用户" if message["role"] == "user" else "助手"
                line = f"{role}: {message['text']}"
                if len(line) > remaining:
                    line = line[:remaining]
                if line:
                    lines.append(line)
                    remaining -= len(line)
                if remaining <= 0:
                    break
            transcripts.append(
                {
                    "source": item.source,
                    "conversation_id": item.id,
                    "title": item.title,
                    "workspace": item.workspace,
                    "transcript": "\n".join(lines),
                }
            )
            if remaining <= 0:
                break

        focus_hint = f"用户的分析重点是：{focus}。" if focus else ""
        system_prompt = (
            "你是本地 AI 对话内容分析助手。只依据提供的用户/助手正文进行分析，"
            "不推测工具是否真的执行成功，把声称完成的事项写成谨慎、可核查的结论，"
            "并区分已完成、进行中、受阻、状态不明。不得输出系统提示、推理过程、"
            "工具调用、密钥或隐私信息。"
            "请对给定的一个或多个 AI 对话做内容分析，直接输出一篇结构清晰的中文 Markdown 报告，"
            "依次包含以下小节：\n"
            "## 概览：用一段话说明这些对话整体在解决什么问题。\n"
            "## 逐个对话：每个对话一个 ### 小节（以对话标题命名），说明目的、做了什么、结果或现状。\n"
            "## 关键决定与产出：跨对话的重要决定、产出物与结论。\n"
            "## 关联与主线：若有多个对话，说明它们之间的联系与共同主线；只有一个对话时可简述其内部脉络。\n"
            "## 遗留与下一步：尚未完成或存疑之处，以及建议的下一步。\n"
            "要求忠实于原文、语言简洁具体，不要复述统计数字，不要编造原文没有的内容。"
        )
        content = self._chat_completion(
            config,
            [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"focus_hint": focus_hint, "conversations": transcripts},
                        ensure_ascii=False,
                    ),
                },
            ],
            max_tokens=min(8192, max(1200, int(config.get("max_tokens") or 2400) * 2)),
        )
        content = re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", str(content).strip(), flags=re.IGNORECASE)
        if not content:
            raise ValueError("模型没有返回有效内容")

        refs = [
            {"source": item.source, "conversation_id": item.id, "title": item.title}
            for item in items
        ]
        title = clean_text(payload.get("title"), 120)
        if not title:
            title = focus[:40] if focus else (
                items[0].title if len(items) == 1 else f"对话分析 · {len(items)} 个对话"
            )
        summary_id = f"cs-{int(time.time() * 1000)}-{secrets.token_hex(4)}"
        now = time.time()
        with notes_db() as conn:
            conn.execute(
                """
                INSERT INTO conversation_summaries(
                  id,title,model,focus,source_refs_json,content_md,status,error,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    summary_id,
                    title,
                    str(config.get("model") or ""),
                    focus,
                    json.dumps(refs, ensure_ascii=False),
                    content,
                    "ready",
                    "",
                    now,
                    now,
                ),
            )
            conn.commit()
        self.record_activity(
            "conversation_summary",
            "生成对话分析",
            summary=title,
            model=str(config.get("model") or ""),
            metadata={"conversation_count": len(items), "summary_id": summary_id},
        )
        detail = self.conversation_summary_detail(summary_id) or {}
        return {"ok": True, "summary": detail}

    def archive_conversation_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        summary_id = clean_text(payload.get("id"), 120)
        if not summary_id:
            raise ValueError("缺少要归档的记录 id")
        now = time.time()
        with notes_db() as conn:
            cursor = conn.execute(
                "UPDATE conversation_summaries SET status='archived',updated_at=? WHERE id=?",
                (now, summary_id),
            )
            conn.commit()
            if not cursor.rowcount:
                raise ValueError("记录不存在或已被归档")
        self.record_activity(
            "conversation_summary",
            "归档对话分析",
            metadata={"summary_id": summary_id},
        )
        return {"ok": True, "archived": summary_id}

    def projects(self) -> dict[str, Any]:
        with notes_db() as conn:
            project_rows = [dict(row) for row in conn.execute("SELECT * FROM projects ORDER BY name")]
            assignment_rows = [dict(row) for row in conn.execute("SELECT * FROM project_assignments")]
        assignments = {
            (row["source"], row["conversation_id"]): row
            for row in assignment_rows
            if (row["source"], row["conversation_id"]) in self._by_key
        }
        grouped: dict[str, list[Conversation]] = {}
        for key, assignment in assignments.items():
            grouped.setdefault(assignment["project_id"], []).append(self._by_key[key])
        projects = []
        for row in project_rows:
            items = grouped.get(row["id"], [])
            if not items and row["origin"] != "manual":
                continue
            last_activity = max((item.updated_at for item in items), default=float(row["updated_at"]))
            pending = sum(
                1
                for item in items
                if float(assignments[(item.source, item.id)]["confidence"]) < 0.8
                and not assignments[(item.source, item.id)]["locked"]
            )
            projects.append(
                {
                    **row,
                    "conversation_count": len(items),
                    "last_activity": last_activity,
                    "pending_count": pending,
                    "sources": {
                        source: sum(1 for item in items if item.source == source)
                        for source in SOURCES
                    },
                }
            )
        projects.sort(key=lambda row: (-float(row["last_activity"]), row["name"]))
        with self._lock:
            all_keys = {(item.source, item.id) for item in self._items}
        unassigned = len(all_keys - set(assignments))
        return {
            "projects": projects,
            "unassigned_count": unassigned,
            "pending_count": sum(int(row["pending_count"]) for row in projects),
            "refreshed_at": self.refreshed_at,
        }

    def _project_daily_entries(
        self,
        project_id: str,
        day_value: str | None = None,
    ) -> tuple[str, list[dict[str, Any]], str]:
        day, entries, global_hash = self._daily_entries_cached(day_value)
        with notes_db() as conn:
            keys = {
                (row["source"], row["conversation_id"])
                for row in conn.execute(
                    "SELECT source,conversation_id FROM project_assignments WHERE project_id=?",
                    (project_id,),
                )
            }
        filtered = [entry for entry in entries if (entry["source"], entry["id"]) in keys]
        signature = hashlib.sha256(
            "\n".join(
                [
                    global_hash,
                    project_id,
                    *sorted(f"{source}:{conversation_id}" for source, conversation_id in keys),
                ]
            ).encode("utf-8")
        ).hexdigest()
        return day, filtered, signature

    def _project_daily_result(
        self,
        project_id: str,
        day: str,
        entries: list[dict[str, Any]],
        source_hash: str,
    ) -> tuple[dict[str, Any], str, str, float]:
        with notes_db() as conn:
            row = conn.execute(
                "SELECT * FROM project_daily_summaries WHERE project_id=? AND day=?",
                (project_id, day),
            ).fetchone()
        if (
            row
            and row["source_hash"] == source_hash
            and int(row["prompt_version"]) == DAILY_PROMPT_VERSION
        ):
            try:
                summary = json.loads(row["summary_json"])
                return summary, row["generator"], row["model"], float(row["generated_at"])
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return self._rules_daily_summary(day, entries), "rules", "", time.time()

    def _store_project_daily_summary(
        self,
        project_id: str,
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
                INSERT INTO project_daily_summaries(
                  project_id,day,source_hash,summary_json,generator,model,
                  prompt_version,generated_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id,day) DO UPDATE SET
                  source_hash=excluded.source_hash,summary_json=excluded.summary_json,
                  generator=excluded.generator,model=excluded.model,
                  prompt_version=excluded.prompt_version,generated_at=excluded.generated_at,
                  updated_at=excluded.updated_at
                """,
                (
                    project_id,
                    day,
                    source_hash,
                    json.dumps(summary, ensure_ascii=False),
                    generator,
                    model,
                    DAILY_PROMPT_VERSION,
                    now,
                    now,
                ),
            )
            conn.commit()

    def generate_project_daily_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_text(payload.get("project_id"), 120)
        if not project_id:
            raise ValueError("缺少项目 ID")
        with notes_db() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ValueError("项目不存在")
        day, entries, source_hash = self._project_daily_entries(project_id, str(payload.get("day") or ""))
        use_model = bool(payload.get("use_model", False))
        config = summary_runtime_config()
        generator = "rules"
        model = ""
        warning = ""
        if use_model and config["enabled"] and config["api_url"] and config["model"] and entries:
            try:
                summary = self._model_daily_summary(day, entries, config)
                generator = "model"
                model = config["model"]
            except ValueError as exc:
                fallback = config.get("fallback_model", "")
                if fallback and fallback != config["model"]:
                    try:
                        summary = self._model_daily_summary(day, entries, {**config, "model": fallback})
                        generator = "model_fallback"
                        model = fallback
                        warning = f"主模型失败，已切换备用模型：{exc}"
                    except ValueError as fallback_exc:
                        summary = self._rules_daily_summary(day, entries)
                        generator = "rules_after_model_error"
                        warning = f"主模型失败：{exc}；备用模型失败：{fallback_exc}"
                else:
                    summary = self._rules_daily_summary(day, entries)
                    generator = "rules_after_model_error"
                    warning = str(exc)
        else:
            summary = self._rules_daily_summary(day, entries)
        self._store_project_daily_summary(project_id, day, source_hash, summary, generator, model)
        result = {
            "ok": True,
            "project_id": project_id,
            "day": day,
            "summary": summary,
            "generator": generator,
            "model": model,
            "generated_at": time.time(),
        }
        if warning:
            result["warning"] = warning
        self.record_activity(
            "project_summary",
            "生成项目每日摘要",
            project_id=project_id,
            model=model,
            summary=f"{day} · {generator}",
            metadata={
                "day": day,
                "generator": generator,
                "conversation_count": len(entries),
                "warning": bool(warning),
            },
        )
        return result

    @staticmethod
    def _agent_skill_roots() -> dict[str, Path]:
        return {
            "hermes": HERMES_DB.parent / "skills",
            "codex": CODEX_DB.parent / "skills",
            "workbuddy": WORKBUDDY_HOME / "skills",
        }

    @classmethod
    def _skill_roots(cls) -> list[dict[str, Any]]:
        home = Path.home()
        values = [
            {
                "agent": "hermes",
                "root_id": "hermes-local",
                "path": HERMES_DB.parent / "skills",
                "origin": "Hermes 本地技能",
                "source_kind": "local",
            },
            {
                "agent": "codex",
                "root_id": "codex-local",
                "path": CODEX_DB.parent / "skills",
                "origin": "Codex 个人技能",
                "source_kind": "local",
            },
            {
                "agent": "codex",
                "root_id": "codex-system",
                "path": CODEX_DB.parent / "skills" / ".system",
                "origin": "Codex 内置技能",
                "source_kind": "system",
            },
            {
                "agent": "codex",
                "root_id": "codex-plugins",
                "path": CODEX_DB.parent / "plugins" / "cache",
                "origin": "Codex 插件",
                "source_kind": "plugin",
            },
            {
                "agent": "workbuddy",
                "root_id": "workbuddy-local",
                "path": WORKBUDDY_HOME / "skills",
                "origin": "WorkBuddy 本地技能",
                "source_kind": "local",
            },
            {
                "agent": "claude",
                "root_id": "claude-local",
                "path": home / ".claude" / "skills",
                "origin": "Claude Code 本地技能",
                "source_kind": "local",
            },
            {
                "agent": "qclaw",
                "root_id": "qclaw-local",
                "path": home / ".qclaw" / "skills",
                "origin": "QClaw 本地技能",
                "source_kind": "local",
            },
            {
                "agent": "qclaw",
                "root_id": "qclaw-workspace",
                "path": home / ".qclaw" / "workspace" / "skills",
                "origin": "QClaw 工作区技能",
                "source_kind": "workspace",
            },
            {
                "agent": "qoderwork",
                "root_id": "qoderwork-local",
                "path": home / ".qoderworkcn" / "skills",
                "origin": "QoderWork 本地技能",
                "source_kind": "local",
            },
        ]
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in values:
            path = Path(value["path"])
            key = str(path).casefold()
            if key in seen or not path.is_dir():
                continue
            seen.add(key)
            result.append({**value, "path": path})
        return result

    def _skill_inventory(self) -> list[dict[str, Any]]:
        cached = self._skill_inventory_cache
        if cached.get("signature") and time.time() - float(cached.get("built_at") or 0) < 30:
            return list(cached.get("items") or [])
        roots = self._skill_roots()
        markers: list[tuple[dict[str, Any], Path]] = []
        signature_parts: list[str] = []
        for root_info in roots:
            agent = str(root_info["agent"])
            root = Path(root_info["path"])
            try:
                candidates = list(root.rglob("SKILL.md"))
            except OSError:
                continue
            for marker in candidates:
                relative_parts = marker.relative_to(root).parts
                if any(
                    part.startswith(".")
                    or "backup" in part.casefold()
                    or "deprecated" in part.casefold()
                    for part in relative_parts
                ):
                    continue
                try:
                    stat = marker.stat()
                except OSError:
                    continue
                markers.append((root_info, marker))
                signature_parts.append(
                    f"{root_info['root_id']}:{marker}:{stat.st_size}:{stat.st_mtime_ns}"
                )
        signature = hashlib.sha256("\n".join(sorted(signature_parts)).encode("utf-8")).hexdigest()
        cached = self._skill_inventory_cache
        if cached.get("signature") == signature:
            return list(cached.get("items") or [])
        inventory: list[dict[str, Any]] = []
        for root_info, marker in markers:
            agent = str(root_info["agent"])
            root = Path(root_info["path"])
            try:
                text = marker.read_text(encoding="utf-8", errors="replace")[:24000]
                marker_stat = marker.stat()
            except OSError:
                continue
            name_match = re.search(r"(?mi)^\s*name\s*:\s*['\"]?([^'\"\r\n]+)", text)
            description_match = re.search(
                r"(?mi)^\s*description\s*:\s*['\"]?([^'\"\r\n]+)",
                text,
            )
            name = clean_text(name_match.group(1) if name_match else marker.parent.name, 120)
            description = clean_text(
                description_match.group(1) if description_match else "",
                500,
            )
            relative_path = marker.parent.relative_to(root).as_posix()
            instance_key = f"{agent}|{root_info['root_id']}|{relative_path.casefold()}"
            instance_id = hashlib.sha1(instance_key.encode("utf-8")).hexdigest()[:24]
            inventory.append(
                {
                    "instance_id": instance_id,
                    "agent": agent,
                    "name": name,
                    "key": name.casefold().replace("_", "-"),
                    "description": description,
                    "path": marker.parent,
                    "marker_path": marker,
                    "relative_path": relative_path,
                    "root_id": str(root_info["root_id"]),
                    "origin": str(root_info["origin"]),
                    "source_kind": str(root_info["source_kind"]),
                    "marker_fingerprint": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "modified_at": marker_stat.st_mtime,
                    "search_text": f"{name}\n{description}\n{text}".casefold(),
                }
            )
        self._skill_inventory_cache = {
            "signature": signature,
            "built_at": time.time(),
            "items": inventory,
        }
        return list(inventory)

    def _skill_fingerprint(self, root: Path) -> dict[str, Any]:
        allowed = {".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml"}
        sensitive = ("secret", "token", "password", "credential", ".env", "auth")
        files: list[Path] = []
        try:
            for path in root.rglob("*"):
                if len(files) >= 350:
                    break
                if not path.is_file() or path.suffix.casefold() not in allowed:
                    continue
                relative = path.relative_to(root)
                if any(
                    part.startswith(".")
                    or part.casefold() in {"__pycache__", "node_modules", ".git"}
                    or any(marker in part.casefold() for marker in sensitive)
                    for part in relative.parts
                ):
                    continue
                files.append(path)
        except OSError:
            files = []
        signature_parts: list[str] = []
        latest = 0.0
        total_size = 0
        for path in sorted(files):
            try:
                stat = path.stat()
            except OSError:
                continue
            total_size += stat.st_size
            latest = max(latest, stat.st_mtime)
            signature_parts.append(
                f"{path.relative_to(root).as_posix()}:{stat.st_size}:{stat.st_mtime_ns}"
            )
        signature = hashlib.sha256("\n".join(signature_parts).encode("utf-8")).hexdigest()
        cached = self._skill_fingerprint_cache.get(str(root))
        if cached and cached.get("signature") == signature:
            return dict(cached)
        digest = hashlib.sha256()
        indexed_files = 0
        indexed_bytes = 0
        for path in sorted(files):
            if indexed_bytes >= 8_000_000:
                break
            try:
                data = path.read_bytes()
            except OSError:
                continue
            remaining = 8_000_000 - indexed_bytes
            data = data[:remaining]
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(data)
            indexed_bytes += len(data)
            indexed_files += 1
        result = {
            "signature": signature,
            "fingerprint": digest.hexdigest(),
            "modified_at": latest,
            "file_count": indexed_files,
            "total_size": total_size,
        }
        self._skill_fingerprint_cache[str(root)] = result
        return dict(result)

    @staticmethod
    def _skill_capabilities(name: str, description: str, text: str = "") -> list[str]:
        value = f"{name}\n{description}\n{text}".casefold()
        rules = (
            ("消息读取", ("message", "聊天", "qq", "微信", "wechat", "群消息")),
            ("日报摘要", ("日报", "daily", "digest", "周报", "summary")),
            ("知识库与笔记", ("obsidian", "知识库", "笔记", "note", "vault", "ima")),
            ("金融投研", ("股票", "金融", "投资", "行情", "stock", "market", "tushare")),
            ("浏览器与自动化", ("browser", "chrome", "自动化", "automation", "网页")),
            ("文件与数据处理", ("json", "excel", "spreadsheet", "pdf", "文档", "文件")),
            ("开发与运维", ("github", "code", "编程", "部署", "skill", "mcp", "维护")),
            ("图像与多媒体", ("image", "图片", "ocr", "vision", "音频", "视频")),
        )
        result = [
            label
            for label, keywords in rules
            if any(keyword in value for keyword in keywords)
        ]
        return result[:4] or ["其他"]

    @staticmethod
    def _skill_sensitive_path(path: Path, root: Path) -> bool:
        sensitive = ("secret", "token", "password", "credential", ".env", "auth", "private")
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            return True
        return any(
            part.startswith(".")
            or part.casefold() in {"__pycache__", "node_modules", ".git"}
            or any(marker in part.casefold() for marker in sensitive)
            for part in parts
        )

    def _auto_skill_projects(self, skill: dict[str, Any]) -> list[dict[str, Any]]:
        with notes_db() as conn:
            projects = [dict(row) for row in conn.execute("SELECT * FROM projects")]
            rules = {
                str(row["project_id"]): dict(row)
                for row in conn.execute("SELECT * FROM project_detection_rules")
            }
        skill_text = f"{skill['name']}\n{skill['description']}\n{skill['search_text']}".casefold()
        skill_tokens = knowledge_tokens(skill_text)
        daily_skill = any(
            marker in skill["key"]
            for marker in (
                "daily", "digest", "chat-message-reader", "image-reader",
                "ai-chat-digest-json", "qq-chat", "wechat-chat", "obsidian",
            )
        )
        links: list[dict[str, Any]] = []
        for project in projects:
            rule = rules.get(str(project["id"]), {})
            try:
                keywords = json.loads(rule.get("include_keywords") or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                keywords = []
            project_text = (
                f"{project['name']}\n{project['description']}\n{' '.join(keywords)}"
            ).casefold()
            overlap = len(skill_tokens & knowledge_tokens(project_text))
            score = overlap / max(6, min(24, len(knowledge_tokens(project_text))))
            if daily_skill and any(
                marker in project_text
                for marker in ("日报", "群聊", "学习强国", "daily digest")
            ):
                score += 0.72
            if skill["key"] in project_text:
                score += 0.65
            if score >= 0.22:
                links.append(
                    {
                        "project_id": str(project["id"]),
                        "name": str(project["name"]),
                        "origin": "auto",
                        "confidence": round(min(0.99, score), 3),
                    }
                )
        links.sort(key=lambda value: value["confidence"], reverse=True)
        return links[:8]

    def skills_catalog(
        self,
        query: str = "",
        agent: str = "all",
        capability: str = "all",
        status: str = "all",
        favorites: bool = False,
    ) -> dict[str, Any]:
        inventory = self._skill_inventory()
        with notes_db() as conn:
            management = {
                str(row["instance_id"]): dict(row)
                for row in conn.execute("SELECT * FROM skill_management")
            }
            project_counts = {
                str(row["instance_id"]): int(row["count"])
                for row in conn.execute(
                    "SELECT instance_id,count(*) AS count FROM skill_project_links GROUP BY instance_id"
                )
            }
        canonical_groups: dict[str, list[dict[str, Any]]] = {}
        for item in inventory:
            saved = management.get(item["instance_id"], {})
            canonical = clean_text(saved.get("canonical_name"), 120) or item["key"]
            canonical_groups.setdefault(canonical.casefold(), []).append(item)
        group_states: dict[str, dict[str, Any]] = {}
        for canonical, values in canonical_groups.items():
            fingerprints = {value["marker_fingerprint"] for value in values}
            agents = {value["agent"] for value in values}
            group_states[canonical] = {
                "copy_count": len(values),
                "agent_count": len(agents),
                "drift": len(values) > 1 and len(fingerprints) > 1,
            }
        needle = query.strip().casefold()
        rows: list[dict[str, Any]] = []
        for item in inventory:
            saved = management.get(item["instance_id"], {})
            try:
                tags = json.loads(saved.get("tags") or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                tags = []
            canonical = clean_text(saved.get("canonical_name"), 120) or item["key"]
            capabilities = self._skill_capabilities(
                item["name"], item["description"], item["search_text"][:8000]
            )
            user_status = str(saved.get("status") or "")
            favorite = bool(saved.get("favorite"))
            haystack = "\n".join(
                [
                    item["name"],
                    item["description"],
                    item["origin"],
                    item["agent"],
                    canonical,
                    " ".join(tags),
                    str(saved.get("note") or ""),
                    " ".join(capabilities),
                ]
            ).casefold()
            if needle and needle not in haystack:
                continue
            if agent != "all" and item["agent"] != agent:
                continue
            if capability != "all" and capability not in capabilities:
                continue
            if status != "all" and user_status != status:
                continue
            if favorites and not favorite:
                continue
            group_state = group_states.get(canonical.casefold(), {})
            rows.append(
                {
                    "instance_id": item["instance_id"],
                    "agent": item["agent"],
                    "name": item["name"],
                    "description": item["description"],
                    "origin": item["origin"],
                    "source_kind": item["source_kind"],
                    "relative_path": item["relative_path"],
                    "modified_at": item["modified_at"],
                    "marker_fingerprint": item["marker_fingerprint"],
                    "capabilities": capabilities,
                    "canonical_name": canonical,
                    "status": user_status,
                    "favorite": favorite,
                    "tags": tags,
                    "note": clean_text(saved.get("note"), 300),
                    "project_count": project_counts.get(item["instance_id"], 0),
                    **group_state,
                }
            )
        rows.sort(
            key=lambda value: (
                not value["favorite"],
                not value.get("drift", False),
                -float(value["modified_at"]),
                value["name"].casefold(),
            )
        )
        return {
            "items": rows,
            "total": len(rows),
            "counts": {
                "all": len(inventory),
                "by_agent": {
                    source: sum(1 for item in inventory if item["agent"] == source)
                    for source in SOURCES
                },
                "favorites": sum(
                    1
                    for item in inventory
                    if bool(management.get(item["instance_id"], {}).get("favorite"))
                ),
                "drift_groups": sum(1 for value in group_states.values() if value["drift"]),
            },
            "capabilities": sorted(
                {
                    value
                    for item in inventory
                    for value in self._skill_capabilities(
                        item["name"], item["description"], item["search_text"][:8000]
                    )
                }
            ),
            "read_only_sources": True,
        }

    def _skill_item(self, instance_id: str) -> dict[str, Any]:
        item = next(
            (
                value
                for value in self._skill_inventory()
                if value["instance_id"] == instance_id
            ),
            None,
        )
        if not item:
            raise ValueError("Skill 不存在或来源已移动")
        return item

    def skill_detail(self, instance_id: str) -> dict[str, Any]:
        item = self._skill_item(instance_id)
        with notes_db() as conn:
            saved_row = conn.execute(
                "SELECT * FROM skill_management WHERE instance_id=?",
                (instance_id,),
            ).fetchone()
            saved = dict(saved_row) if saved_row else {}
            manual_project_ids = {
                str(row["project_id"])
                for row in conn.execute(
                    "SELECT project_id FROM skill_project_links WHERE instance_id=?",
                    (instance_id,),
                )
            }
            project_names = {
                str(row["id"]): str(row["name"])
                for row in conn.execute("SELECT id,name FROM projects")
            }
            management = {
                str(row["instance_id"]): dict(row)
                for row in conn.execute("SELECT * FROM skill_management")
            }
        canonical = clean_text(saved.get("canonical_name"), 120) or item["key"]
        copies = []
        for candidate in self._skill_inventory():
            candidate_saved = management.get(candidate["instance_id"], {})
            candidate_canonical = (
                clean_text(candidate_saved.get("canonical_name"), 120) or candidate["key"]
            )
            if candidate_canonical.casefold() != canonical.casefold():
                continue
            copies.append(
                {
                    "instance_id": candidate["instance_id"],
                    "agent": candidate["agent"],
                    "name": candidate["name"],
                    "origin": candidate["origin"],
                    "path": str(candidate["path"]),
                    **self._skill_fingerprint(Path(candidate["path"])),
                }
            )
        root = Path(item["path"])
        marker = Path(item["marker_path"])
        try:
            marker_text = marker.read_text(encoding="utf-8", errors="replace")
        except OSError:
            marker_text = ""
        sections = [
            {"level": len(match.group(1)), "title": clean_text(match.group(2), 160)}
            for match in re.finditer(r"(?m)^(#{1,3})\s+(.+?)\s*$", marker_text)
        ][:40]
        files = []
        allowed = {".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml"}
        try:
            candidates = root.rglob("*")
            for path in candidates:
                if len(files) >= 180:
                    break
                if (
                    not path.is_file()
                    or path.suffix.casefold() not in allowed
                    or self._skill_sensitive_path(path, root)
                ):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                files.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "size": stat.st_size,
                        "modified_at": stat.st_mtime,
                        "kind": path.suffix.casefold().lstrip("."),
                    }
                )
        except OSError:
            pass
        auto_projects = self._auto_skill_projects(item)
        project_links = {
            value["project_id"]: value for value in auto_projects
        }
        for project_id in manual_project_ids:
            project_links[project_id] = {
                "project_id": project_id,
                "name": project_names.get(project_id, project_id),
                "origin": "manual",
                "confidence": 1.0,
            }
        try:
            tags = json.loads(saved.get("tags") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            tags = []
        fingerprints = {value["fingerprint"] for value in copies}
        return {
            "skill": {
                "instance_id": item["instance_id"],
                "agent": item["agent"],
                "name": item["name"],
                "description": item["description"],
                "origin": item["origin"],
                "source_kind": item["source_kind"],
                "path": str(item["path"]),
                "relative_path": item["relative_path"],
                "capabilities": self._skill_capabilities(
                    item["name"], item["description"], marker_text[:12000]
                ),
                **self._skill_fingerprint(root),
            },
            "management": {
                "canonical_name": canonical,
                "status": str(saved.get("status") or ""),
                "favorite": bool(saved.get("favorite")),
                "tags": tags,
                "note": str(saved.get("note") or ""),
            },
            "sections": sections,
            "files": files,
            "copies": sorted(copies, key=lambda value: (value["agent"], value["origin"])),
            "copies_drift": len(copies) > 1 and len(fingerprints) > 1,
            "projects": sorted(
                project_links.values(),
                key=lambda value: (value["origin"] != "manual", -value["confidence"]),
            ),
            "manual_project_ids": sorted(manual_project_ids),
            "all_projects": [
                {"id": project_id, "name": name}
                for project_id, name in sorted(project_names.items(), key=lambda value: value[1])
            ],
            "source_read_only": True,
        }

    def save_skill_management(self, payload: dict[str, Any]) -> dict[str, Any]:
        instance_id = clean_text(payload.get("instance_id"), 80)
        self._skill_item(instance_id)
        status = clean_text(payload.get("status"), 40)
        if status not in {"", "active", "watching", "needs_sync", "deprecated"}:
            raise ValueError("Skill 状态无效")
        canonical_name = clean_text(payload.get("canonical_name"), 120)
        tags = self._project_rule_terms(payload.get("tags"), "标签")
        note = clean_text(payload.get("note"), 4000)
        now = time.time()
        with notes_db() as conn:
            conn.execute(
                """
                INSERT INTO skill_management(
                  instance_id,canonical_name,status,favorite,tags,note,updated_at
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(instance_id) DO UPDATE SET
                  canonical_name=excluded.canonical_name,status=excluded.status,
                  favorite=excluded.favorite,tags=excluded.tags,note=excluded.note,
                  updated_at=excluded.updated_at
                """,
                (
                    instance_id,
                    canonical_name,
                    status,
                    int(bool(payload.get("favorite"))),
                    json.dumps(tags, ensure_ascii=False),
                    note,
                    now,
                ),
            )
            conn.commit()
        return {"ok": True, **self.skill_detail(instance_id)}

    def save_skill_projects(self, payload: dict[str, Any]) -> dict[str, Any]:
        instance_id = clean_text(payload.get("instance_id"), 80)
        self._skill_item(instance_id)
        project_ids = {
            clean_text(value, 120)
            for value in (payload.get("project_ids") or [])
            if clean_text(value, 120)
        }
        with notes_db() as conn:
            valid_ids = {
                str(row["id"])
                for row in conn.execute("SELECT id FROM projects")
            }
            if not project_ids.issubset(valid_ids):
                raise ValueError("包含不存在的项目")
            conn.execute(
                "DELETE FROM skill_project_links WHERE instance_id=?",
                (instance_id,),
            )
            now = time.time()
            conn.executemany(
                """
                INSERT INTO skill_project_links(instance_id,project_id,locked,updated_at)
                VALUES(?,?,1,?)
                """,
                [(instance_id, project_id, now) for project_id in sorted(project_ids)],
            )
            conn.commit()
        return {"ok": True, **self.skill_detail(instance_id)}

    def reveal_skill(self, payload: dict[str, Any]) -> dict[str, Any]:
        instance_id = clean_text(payload.get("instance_id"), 80)
        item = self._skill_item(instance_id)
        path = Path(item["path"])
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return {"ok": True, "path": str(path)}

    @staticmethod
    def _vault_root_from_path(value: str) -> str:
        path = clean_text(value.strip().strip("'\""), 900)
        if not path:
            return ""
        candidate = Path(path).expanduser()
        parts = candidate.parts
        for index, part in enumerate(parts):
            if part.casefold() == "obsidian_lsj":
                return str(Path(*parts[: index + 1]))
        return str(candidate)

    def _agent_vault_candidates(
        self,
        selected_skills: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        results: dict[str, list[dict[str, Any]]] = {
            agent: [] for agent in self._agent_skill_roots()
        }
        hermes_env = HERMES_DB.parent / ".env"
        if hermes_env.is_file():
            try:
                for line in hermes_env.read_text(encoding="utf-8", errors="replace").splitlines():
                    match = re.match(
                        r"\s*(OBSIDIAN_VAULT_PATH|OBSIDIAN_VAULT)\s*=\s*(.+?)\s*$",
                        line,
                        re.IGNORECASE,
                    )
                    if match:
                        value = self._vault_root_from_path(match.group(2))
                        if value:
                            results["hermes"].append(
                                {"path": value, "origin": match.group(1), "source": str(hermes_env)}
                            )
            except OSError:
                pass
        path_pattern = re.compile(
            r"""(?ix)
            (?:OBSIDIAN_VAULT_PATH|OBSIDIAN_VAULT|VAULT_DIR|OUTPUT_DIR)
            \s*=\s*r?["']([^"'\r\n]+)["']
            """
        )
        for skill in selected_skills:
            root = Path(skill["path"])
            scanned = 0
            try:
                candidates = root.rglob("*")
                for path in candidates:
                    if scanned >= 80:
                        break
                    if (
                        not path.is_file()
                        or path.suffix.casefold() not in {".py", ".md", ".yaml", ".yml", ".toml"}
                        or any(part.startswith(".") for part in path.relative_to(root).parts)
                    ):
                        continue
                    scanned += 1
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")[:160000]
                    except OSError:
                        continue
                    for match in path_pattern.finditer(text):
                        value = self._vault_root_from_path(match.group(1))
                        if "obsidian" not in value.casefold():
                            continue
                        results.setdefault(skill["agent"], []).append(
                            {
                                "path": value,
                                "origin": f"{skill['name']} · {path.name}",
                                "source": str(path),
                            }
                        )
            except OSError:
                continue
        for agent, values in results.items():
            deduplicated: dict[str, dict[str, Any]] = {}
            for value in values:
                key = normalized_project_path(value["path"])
                deduplicated.setdefault(key, value)
            results[agent] = list(deduplicated.values())[:6]
        return results

    def _project_config_audit(
        self,
        project: sqlite3.Row,
        detection_rule: dict[str, Any],
        items: list[Conversation],
    ) -> dict[str, Any]:
        project_text = "\n".join(
            [
                str(project["name"] or ""),
                str(project["description"] or ""),
                " ".join(detection_rule.get("include_keywords") or []),
                *[f"{item.title}\n{item.preview}" for item in items[:35]],
            ]
        ).casefold()
        project_tokens = knowledge_tokens(project_text)
        daily_project = any(
            marker in project_text
            for marker in ("学习强国", "群聊日报", "群日报", "日报", "daily digest")
        )
        daily_skill_markers = (
            "investment-daily",
            "chat-message-reader",
            "image-reader",
            "ai-chat-digest-json",
            "qq-chat-digest",
            "wechat-chat-digest",
            "qq-daily-report",
            "obsidian",
        )
        scored: list[tuple[float, dict[str, Any]]] = []
        for skill in self._skill_inventory():
            if skill["agent"] not in {"hermes", "codex", "workbuddy"}:
                continue
            skill_tokens = knowledge_tokens(skill["search_text"])
            overlap = len(project_tokens & skill_tokens)
            score = overlap / max(8, min(len(project_tokens), 45))
            if skill["key"] in project_text:
                score += 0.65
            daily_match = any(marker in skill["key"] for marker in daily_skill_markers)
            if daily_project:
                if not daily_match:
                    continue
                if daily_match:
                    score += 0.72
            if score >= 0.16:
                scored.append((score, skill))
        selected: list[dict[str, Any]] = []
        per_agent: dict[str, int] = {}
        for score, skill in sorted(scored, key=lambda value: value[0], reverse=True):
            if per_agent.get(skill["agent"], 0) >= 14:
                continue
            selected.append({**skill, "relevance": round(min(0.99, score), 3)})
            per_agent[skill["agent"]] = per_agent.get(skill["agent"], 0) + 1
        expected_agents = [
            agent
            for agent in ("hermes", "codex", "workbuddy")
            if any(item.source == agent for item in items)
            and self._agent_skill_roots()[agent].is_dir()
        ]
        if len(expected_agents) < 2:
            expected_agents = [
                agent for agent, root in self._agent_skill_roots().items() if root.is_dir()
            ][:2]
        comparison_agents = (
            ["hermes", "codex"]
            if {"hermes", "codex"}.issubset(expected_agents)
            else expected_agents
        )
        groups: dict[str, list[dict[str, Any]]] = {}
        for skill in selected:
            fingerprint = self._skill_fingerprint(Path(skill["path"]))
            public = {
                "agent": skill["agent"],
                "name": skill["name"],
                "path": str(skill["path"]),
                "relevance": skill["relevance"],
                **fingerprint,
            }
            groups.setdefault(skill["key"], []).append(public)
        symmetric_names = {
            "investment-daily",
            "chat-message-reader",
            "image-reader",
            "ai-chat-digest-json",
        }
        rows: list[dict[str, Any]] = []
        warning_count = 0
        for key, versions in sorted(
            groups.items(),
            key=lambda pair: max(value["relevance"] for value in pair[1]),
            reverse=True,
        ):
            present_agents = {value["agent"] for value in versions}
            fingerprints = {value["fingerprint"] for value in versions}
            missing_agents = [
                agent
                for agent in comparison_agents
                if agent not in present_agents and key in symmetric_names
            ]
            if missing_agents:
                status = "missing"
            elif len(versions) >= 2 and len(fingerprints) > 1:
                status = "drift"
            elif len(versions) >= 2:
                status = "aligned"
            else:
                status = "single"
            if status in {"missing", "drift"}:
                warning_count += 1
            newest = max(versions, key=lambda value: value["modified_at"])
            rows.append(
                {
                    "key": key,
                    "name": versions[0]["name"],
                    "status": status,
                    "missing_agents": missing_agents,
                    "newest_agent": newest["agent"],
                    "newest_at": newest["modified_at"],
                    "versions": sorted(versions, key=lambda value: value["agent"]),
                }
            )
        vault_candidates = self._agent_vault_candidates(selected)
        vault_rows = []
        active_vaults: dict[str, str] = {}
        for agent in expected_agents:
            candidates = vault_candidates.get(agent) or []
            preferred = next(
                (value for value in candidates if value["origin"].casefold().startswith("obsidian_vault")),
                candidates[0] if candidates else None,
            )
            if preferred:
                active_vaults[agent] = preferred["path"]
            vault_rows.append(
                {
                    "agent": agent,
                    "preferred": preferred,
                    "candidates": candidates,
                }
            )
        vault_mismatch = len(
            {normalized_project_path(value) for value in active_vaults.values()}
        ) > 1
        if vault_mismatch:
            warning_count += 1
        return {
            "expected_agents": expected_agents,
            "comparison_agents": comparison_agents,
            "skills": rows[:24],
            "vaults": vault_rows,
            "vault_mismatch": vault_mismatch,
            "warning_count": warning_count,
            "checked_at": time.time(),
            "read_only": True,
            "logic": (
                "按项目名称、归类关键词和最近对话识别相关 Skill；"
                "只比较文件指纹、修改时间和知识库路径，不读取或展示密钥。"
            ),
        }

    @staticmethod
    def _project_plan_source_hash(
        project: dict[str, Any] | sqlite3.Row,
        items: list[Conversation],
        milestones: list[dict[str, Any]],
    ) -> str:
        parts = [
            str(project["id"]),
            str(project["name"]),
            str(project["description"]),
            *(
                f"{item.source}:{item.id}:{item.updated_at}:{item.user_status}"
                for item in sorted(items, key=lambda value: (value.source, value.id))
            ),
            *(f"{row['id']}:{row['updated_at']}" for row in milestones),
        ]
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _normalise_project_plan(raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raw = {}

        def strings(key: str, limit: int = 8, length: int = 240) -> list[str]:
            values = raw.get(key) or []
            if isinstance(values, str):
                values = [line for line in values.splitlines() if line.strip()]
            return [clean_text(value, length) for value in values if clean_text(value, length)][:limit]

        milestones = []
        for value in (raw.get("milestones") or [])[:12]:
            if not isinstance(value, dict):
                continue
            title = clean_text(value.get("title"), 120)
            if not title:
                continue
            status = clean_text(value.get("status"), 30)
            if status not in {"todo", "in_progress", "done", "blocked"}:
                status = "todo"
            milestones.append(
                {
                    "title": title,
                    "outcome": clean_text(value.get("outcome"), 400),
                    "acceptance": clean_text(value.get("acceptance"), 400),
                    "status": status,
                    "target_date": clean_text(value.get("target_date"), 20),
                    "dependencies": clean_text(value.get("dependencies"), 240),
                }
            )
        risks = []
        for value in (raw.get("risks") or [])[:8]:
            if not isinstance(value, dict):
                continue
            risk = clean_text(value.get("risk"), 240)
            if risk:
                risks.append({"risk": risk, "mitigation": clean_text(value.get("mitigation"), 320)})
        stage = clean_text(raw.get("current_stage"), 30)
        if stage not in {"discover", "plan", "build", "verify", "ship", "maintain"}:
            stage = "discover"
        return {
            "objective": clean_text(raw.get("objective"), 500),
            "success_criteria": strings("success_criteria", 8, 260),
            "current_stage": stage,
            "scope_in": strings("scope_in", 8, 240),
            "scope_out": strings("scope_out", 8, 240),
            "milestones": milestones,
            "risks": risks,
            "next_action": clean_text(raw.get("next_action"), 400),
            "open_questions": strings("open_questions", 8, 260),
        }

    def _template_project_plan(
        self,
        project: dict[str, Any],
        items: list[Conversation],
        milestones: list[dict[str, Any]],
    ) -> dict[str, Any]:
        done = [row for row in milestones if str(row.get("status")) == "done"]
        stage = "build" if items else "discover"
        if done and len(done) == len(milestones):
            stage = "verify"
        objective = clean_text(project.get("description"), 500) or f"明确并推进“{project['name']}”的可验收成果"
        planned = [
            {
                "title": "明确目标与边界",
                "outcome": "写清要解决的问题、目标用户和不做什么",
                "acceptance": "目标、成功标准和范围均有明确文字记录",
                "status": "done" if items else "in_progress",
                "target_date": "",
                "dependencies": "",
            },
            {
                "title": "形成可运行的第一版",
                "outcome": "完成最小可用成果并保留验证证据",
                "acceptance": "核心流程可由使用者独立走通一次",
                "status": "in_progress" if items else "todo",
                "target_date": "",
                "dependencies": "目标与范围已确认",
            },
            {
                "title": "验证与交付",
                "outcome": "修复关键问题，形成安装、使用或交付说明",
                "acceptance": "验收清单通过且遗留项有明确去向",
                "status": "todo",
                "target_date": "",
                "dependencies": "第一版可运行",
            },
        ]
        return self._normalise_project_plan(
            {
                "objective": objective,
                "success_criteria": ["核心目标可以被实际验证", "重要结论能回到对话或成果文件核对"],
                "current_stage": stage,
                "scope_in": ["当前已归入该项目的对话与成果"],
                "scope_out": ["未经确认的扩展需求"],
                "milestones": planned,
                "risks": [{"risk": "对话很多但缺少明确验收口径", "mitigation": "每个里程碑只保留一个可核查结果"}],
                "next_action": "补充一句话目标，并确认第一个里程碑的验收标准",
                "open_questions": ["这个项目交付给谁使用？", "什么结果出现时可以算第一版完成？"],
            }
        )

    def save_project_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_text(payload.get("project_id"), 120)
        plan = self._normalise_project_plan(payload.get("plan") or {})
        if not project_id or not plan["objective"]:
            raise ValueError("请选择项目并填写一句话目标")
        with notes_db() as conn:
            project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise ValueError("项目不存在")
            assignments = list(conn.execute(
                "SELECT source,conversation_id FROM project_assignments WHERE project_id=?",
                (project_id,),
            ))
            milestones = [dict(row) for row in conn.execute(
                "SELECT * FROM project_milestones WHERE project_id=? ORDER BY occurred_at",
                (project_id,),
            )]
        items = [
            self._by_key[(row["source"], row["conversation_id"])]
            for row in assignments if (row["source"], row["conversation_id"]) in self._by_key
        ]
        source_hash = self._project_plan_source_hash(project, items, milestones)
        now = time.time()
        generator = clean_text(payload.get("generator"), 30) or "manual"
        model = clean_text(payload.get("model"), 200)
        with notes_db() as conn:
            conn.execute(
                """
                INSERT INTO project_plans(project_id,plan_json,source_hash,generator,model,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET
                  plan_json=excluded.plan_json,source_hash=excluded.source_hash,
                  generator=excluded.generator,model=excluded.model,updated_at=excluded.updated_at
                """,
                (project_id, json.dumps(plan, ensure_ascii=False), source_hash, generator, model, now, now),
            )
            conn.commit()
        self.record_activity(
            "project_plan", "保存项目计划", project_id=project_id,
            summary=plan["objective"], metadata={"generator": generator, "model": model},
        )
        return {"ok": True, "plan": plan, "generator": generator, "model": model, "updated_at": now}

    def generate_project_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_text(payload.get("project_id"), 120)
        detail = self.project_detail(project_id)
        if not detail:
            raise ValueError("项目不存在")
        base = detail["project_plan"]
        config = summary_runtime_config()
        use_model = bool(payload.get("use_model", False))
        generator = "template"
        model = ""
        warning = ""
        plan = base
        if use_model and config["enabled"] and config["api_url"] and config["model"]:
            context = {
                "project": {
                    "name": detail["project"]["name"],
                    "description": detail["project"]["description"],
                    "status": detail["project"]["status"],
                },
                "today_summary": detail["today_summary"],
                "milestones": [
                    {key: row.get(key) for key in ("version", "title", "summary", "status")}
                    for row in detail["milestones"][-12:]
                ],
                "recent_conversations": detail["recent_conversations"][:20],
                "current_plan": base,
            }
            prompt = (
                "你是面向新手的项目规划教练。只根据给定资料制定一份短而可执行的项目计划，"
                "区分事实和建议；不得编造日期、预算、已完成状态或工具执行结果。"
                "把任务控制在新手一眼能读懂的粒度，每个里程碑必须有结果和验收标准。"
                "返回纯 JSON，字段必须为 objective,success_criteria,current_stage,scope_in,scope_out,"
                "milestones,risks,next_action,open_questions。current_stage 只能是 "
                "discover/plan/build/verify/ship/maintain；milestones 每项含 title,outcome,acceptance,"
                "status,target_date,dependencies，status 只能是 todo/in_progress/done/blocked；"
                "risks 每项含 risk,mitigation。没有证据时状态宁可保守，target_date 留空。"
            )
            try:
                content = self._chat_completion(
                    config,
                    [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                    max_tokens=3000,
                )
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
                plan = self._normalise_project_plan(json.loads(content))
                generator = "model"
                model = config["model"]
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                warning = f"模型规划失败，已保留基础模板：{exc}"
        result = self.save_project_plan(
            {"project_id": project_id, "plan": plan, "generator": generator, "model": model}
        )
        result["warning"] = warning
        return result

    @staticmethod
    def _obsidian_target(vault_value: str, subfolder_value: str) -> tuple[Path, Path, str]:
        vault = Path(vault_value).expanduser()
        if not vault.is_absolute() or not vault.exists() or not vault.is_dir():
            raise ValueError("请选择一个已经存在的 Obsidian 仓库目录")
        vault = vault.resolve()
        subfolder = clean_text(subfolder_value, 240).strip().replace("\\", "/").strip("/")
        if not subfolder:
            subfolder = "AI 对话中心"
        relative = Path(subfolder)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("知识库子目录必须位于所选 Obsidian 仓库内")
        target = (vault / relative).resolve()
        try:
            target.relative_to(vault)
        except ValueError as exc:
            raise ValueError("知识库子目录超出了所选 Obsidian 仓库") from exc
        return vault, target, subfolder

    def obsidian_config(self, project_id: str = "") -> dict[str, Any]:
        settings = read_app_settings()
        vault_value = settings.get("obsidian_vault_path", "")
        subfolder = settings.get("obsidian_subfolder", "AI 对话中心")
        enabled = settings.get("obsidian_enabled", "0") == "1"
        valid = False
        is_vault = False
        error = ""
        if vault_value:
            try:
                vault, _, subfolder = self._obsidian_target(vault_value, subfolder)
                valid = True
                is_vault = (vault / ".obsidian").is_dir()
            except ValueError as exc:
                error = str(exc)
        approved_count = exported_count = 0
        if project_id:
            with notes_db() as conn:
                approved_count = int(conn.execute(
                    """
                    SELECT COUNT(*) FROM knowledge_items
                    WHERE project_id=? AND status='approved' AND revoked_at=0
                      AND (valid_until=0 OR valid_until>?)
                    """,
                    (project_id, time.time()),
                ).fetchone()[0])
                exported_count = int(conn.execute(
                    """
                    SELECT COUNT(DISTINCT e.knowledge_id)
                    FROM knowledge_exports e JOIN knowledge_items k ON k.id=e.knowledge_id
                    WHERE k.project_id=? AND e.destination='obsidian'
                    """,
                    (project_id,),
                ).fetchone()[0])
        return {
            "enabled": enabled,
            "vault_path": vault_value,
            "subfolder": subfolder,
            "valid": valid,
            "is_obsidian_vault": is_vault,
            "error": error,
            "approved_count": approved_count,
            "exported_count": exported_count,
            "policy": "仅手动导出已审核、未撤销的知识卡；原始对话保持只读",
        }

    def save_obsidian_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        enabled = bool(payload.get("enabled"))
        vault_value = str(payload.get("vault_path") or "").strip()
        subfolder_value = str(payload.get("subfolder") or "AI 对话中心").strip()
        if enabled or vault_value:
            vault, _, subfolder = self._obsidian_target(vault_value, subfolder_value)
            vault_value = str(vault)
        else:
            subfolder = clean_text(subfolder_value, 240) or "AI 对话中心"
        now = time.time()
        with notes_db() as conn:
            for key, value in {
                "obsidian_enabled": "1" if enabled else "0",
                "obsidian_vault_path": vault_value,
                "obsidian_subfolder": subfolder,
            }.items():
                conn.execute(
                    """
                    INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
                    """,
                    (key, value, now),
                )
            conn.commit()
        return {"ok": True, **self.obsidian_config(clean_text(payload.get("project_id"), 120))}

    @staticmethod
    def _obsidian_note_content(
        item: dict[str, Any],
        project_name: str,
        evidence: list[dict[str, Any]],
    ) -> str:
        type_labels = {
            "achievement": "成果", "decision": "决策", "task": "待办",
            "project_state": "项目状态", "method": "方法", "fact": "事实", "preference": "偏好",
        }
        updated = datetime.fromtimestamp(float(item["updated_at"]), LOCAL_TZ).isoformat(timespec="seconds")
        lines = [
            "---",
            f'ai_hub_id: {json.dumps(str(item["id"]), ensure_ascii=False)}',
            f'project: {json.dumps(project_name, ensure_ascii=False)}',
            f'type: {json.dumps(str(item["type"]), ensure_ascii=False)}',
            'status: "approved"',
            f'source_day: {json.dumps(str(item["source_day"]), ensure_ascii=False)}',
            f'updated: {json.dumps(updated, ensure_ascii=False)}',
            "tags:",
            '  - "AI对话中心"',
            f'  - {json.dumps(type_labels.get(str(item["type"]), str(item["type"])), ensure_ascii=False)}',
            "---",
            "",
            f"# {redact_model_text(str(item['title']))}",
            "",
            markdown_text(redact_model_text(str(item["content"]))),
            "",
            "## 证据",
            "",
        ]
        if evidence:
            for row in evidence:
                quote = redact_model_text(str(row.get("quote") or ""))
                lines.append(
                    f"- `{row['source']}:{row['conversation_id']}`"
                    + (f" — {markdown_text(quote)}" if quote else "")
                )
        else:
            lines.append("- 此知识卡没有可定位的对话证据，请结合审核记录使用。")
        lines.extend([
            "",
            "> 由 AI 对话中心从用户/助手正文整理，经人工审核后导出；不包含系统提示、推理或工具输出。",
            "",
        ])
        return "\n".join(lines)

    def export_knowledge_to_obsidian(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_text(payload.get("project_id"), 120)
        if not project_id:
            raise ValueError("请选择要归档的项目")
        config = self.obsidian_config(project_id)
        if not config["enabled"]:
            raise ValueError("请先启用并保存 Obsidian 归档设置")
        vault, target_root, _ = self._obsidian_target(config["vault_path"], config["subfolder"])
        with notes_db() as conn:
            project = conn.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise ValueError("项目不存在")
            items = [dict(row) for row in conn.execute(
                """
                SELECT * FROM knowledge_items
                WHERE project_id=? AND status='approved' AND revoked_at=0
                  AND sensitivity!='restricted'
                  AND (valid_until=0 OR valid_until>?)
                ORDER BY updated_at
                """,
                (project_id, time.time()),
            )]
            evidence_rows = [dict(row) for row in conn.execute(
                """
                SELECT e.* FROM knowledge_evidence e
                JOIN knowledge_items k ON k.id=e.knowledge_id
                WHERE k.project_id=? AND k.status='approved'
                ORDER BY e.knowledge_id,e.source,e.conversation_id
                """,
                (project_id,),
            )]
        if not items:
            raise ValueError("当前项目没有可导出的已审核知识卡")
        evidence_map: dict[str, list[dict[str, Any]]] = {}
        for row in evidence_rows:
            evidence_map.setdefault(str(row["knowledge_id"]), []).append(row)
        project_dir = (target_root / safe_filename(str(project["name"]))).resolve()
        try:
            project_dir.relative_to(vault)
        except ValueError as exc:
            raise ValueError("项目归档目录超出 Obsidian 仓库") from exc
        project_dir.mkdir(parents=True, exist_ok=True)
        written = skipped = 0
        exported: list[tuple[str, str, str, float]] = []
        for item in items:
            filename = f"{safe_filename(str(item['title']))}-{str(item['id'])[-8:]}.md"
            path = (project_dir / filename).resolve()
            try:
                path.relative_to(project_dir)
            except ValueError as exc:
                raise ValueError("知识卡文件名超出项目归档目录") from exc
            content = self._obsidian_note_content(
                item, str(project["name"]), evidence_map.get(str(item["id"]), [])
            )
            marker = f'ai_hub_id: {json.dumps(str(item["id"]), ensure_ascii=False)}'
            if path.exists():
                existing = path.read_text(encoding="utf-8", errors="replace")
                if marker not in existing:
                    skipped += 1
                    continue
                if existing == content:
                    exported.append((str(item["id"]), str(path), hashlib.sha256(content.encode("utf-8")).hexdigest(), time.time()))
                    continue
            temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
            temp.write_text(content, encoding="utf-8", newline="\n")
            os.replace(temp, path)
            written += 1
            exported.append((str(item["id"]), str(path), hashlib.sha256(content.encode("utf-8")).hexdigest(), time.time()))
        with notes_db() as conn:
            conn.executemany(
                """
                INSERT INTO knowledge_exports(knowledge_id,destination,path,content_hash,exported_at)
                VALUES(?,'obsidian',?,?,?)
                ON CONFLICT(knowledge_id,destination) DO UPDATE SET
                  path=excluded.path,content_hash=excluded.content_hash,exported_at=excluded.exported_at
                """,
                exported,
            )
            conn.commit()
        self.record_activity(
            "obsidian_export", "导出已审核知识到 Obsidian", project_id=project_id,
            summary=f"写入 {written} 条，登记 {len(exported)} 条",
            metadata={"path": str(project_dir), "written": written, "skipped": skipped},
        )
        return {
            "ok": True, "path": str(project_dir), "written": written,
            "exported": len(exported), "skipped": skipped,
            **self.obsidian_config(project_id),
        }

    def project_detail(self, project_id: str) -> dict[str, Any] | None:
        with notes_db() as conn:
            visited: set[str] = set()
            while project_id not in visited:
                visited.add(project_id)
                alias = conn.execute(
                    "SELECT target_project_id FROM project_aliases WHERE source_project_id=?",
                    (project_id,),
                ).fetchone()
                if not alias:
                    break
                project_id = str(alias["target_project_id"])
            project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                return None
            assignment_rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_assignments WHERE project_id=?",
                    (project_id,),
                )
            ]
            milestone_rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_milestones WHERE project_id=? ORDER BY occurred_at",
                    (project_id,),
                )
            ]
            detection_row = conn.execute(
                "SELECT * FROM project_detection_rules WHERE project_id=?",
                (project_id,),
            ).fetchone()
            configured_rules = self._configured_project_rules(conn)
            plan_row = conn.execute(
                "SELECT * FROM project_plans WHERE project_id=?", (project_id,)
            ).fetchone()
            daily_summary_row = conn.execute(
                "SELECT MAX(updated_at) AS updated_at FROM project_daily_summaries WHERE project_id=?",
                (project_id,),
            ).fetchone()
        items = [
            self._by_key[(row["source"], row["conversation_id"])]
            for row in assignment_rows
            if (row["source"], row["conversation_id"]) in self._by_key
        ]
        items.sort(key=lambda item: item.updated_at, reverse=True)
        project_cache_key = hashlib.sha256(
            "\n".join(
                [
                    project_id,
                    datetime.now(LOCAL_TZ).date().isoformat(),
                    f"project:{float(project['updated_at']):.6f}",
                    f"rule:{float(detection_row['updated_at']) if detection_row else 0:.6f}",
                    f"plan:{float(plan_row['updated_at']) if plan_row else 0:.6f}",
                    f"daily:{float(daily_summary_row['updated_at'] or 0):.6f}",
                    *[
                        f"assignment:{row['source']}:{row['conversation_id']}:{float(row['updated_at']):.6f}"
                        for row in assignment_rows
                    ],
                    *[
                        f"milestone:{row['id']}:{float(row['updated_at']):.6f}"
                        for row in milestone_rows
                    ],
                    *[
                        f"{item.source}:{item.id}:{item.updated_at:.6f}"
                        for item in items
                    ],
                ]
            ).encode("utf-8")
        ).hexdigest()
        with self._lock:
            cached_project = self._project_detail_cache.get(project_id)
            if (
                cached_project
                and cached_project.get("key") == project_cache_key
                and time.time() - float(cached_project.get("built_at") or 0) < 60
                and isinstance(cached_project.get("payload"), dict)
            ):
                return cached_project["payload"]
        assignment_map = {
            (row["source"], row["conversation_id"]): row for row in assignment_rows
        }
        milestones = []
        for row in milestone_rows:
            try:
                evidence = json.loads(row["evidence_json"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                evidence = []
            milestones.append({**row, "evidence": evidence})
        workstream_names = [name for name, _ in WORKSTREAM_RULES] + ["其他"]
        workstreams = []
        for workstream in workstream_names:
            cells = []
            for milestone in milestones:
                evidence_keys = {
                    (str(value.get("source") or ""), str(value.get("id") or ""))
                    for value in milestone["evidence"]
                    if isinstance(value, dict)
                }
                candidates = [
                    item
                    for item in items
                    if conversation_workstream(item) == workstream
                    and (item.source, item.id) in evidence_keys
                ]
                latest = max(candidates, key=lambda item: item.updated_at) if candidates else None
                cells.append(
                    {
                        "milestone_id": milestone["id"],
                        "version": milestone["version"],
                        "title": clean_text(latest.title, 80) if latest else "",
                        "status": (
                            "done"
                            if latest and latest.user_status == "done"
                            else ("in_progress" if latest else "empty")
                        ),
                        "source": latest.source if latest else "",
                        "conversation_id": latest.id if latest else "",
                        "count": len(candidates),
                    }
                )
            if any(cell["title"] for cell in cells) or workstream != "其他":
                workstreams.append({"name": workstream, "cells": cells})
        if project_id == "ai-conversation-hub" and milestones:
            feature_matrix = {
                "数据接入": {
                    "v1": "Hermes 与 Codex 统一索引",
                    "v5": "WorkBuddy 主对话接入",
                    "v10": "跨电脑首次配置与结构验证",
                    "v11": "来源无关的规范项目身份",
                    "v12": "六类新增 Agent 的可插拔适配器",
                },
                "搜索整理": {
                    "v1": "跨来源全文搜索",
                    "v9": "项目归类与安全批量导出",
                    "v10": "Agent 范围、布尔语法与可调详情栏",
                    "v11": "项目规则试算与跨 Agent 合并",
                    "v12": "九来源统一全文检索与本地索引",
                    "v15": "自定义 Agent 适配与中途增删",
                    "v16": "Claude 历史正文可靠性增强",
                    "v18": "macOS 标准目录与双架构来源发现",
                },
                "日报摘要": {
                    "v7": "每日回顾与证据链接",
                    "v9": "五段式摘要与知识候选",
                    "v17": "已审核知识安全归档到 Obsidian",
                },
                "模型配置": {
                    "v7": "OpenAI 兼容接口配置",
                    "v9": "Paratera 模型自动发现",
                    "v18": "macOS Keychain 密钥保护",
                },
                "UI体验": {
                    "v5": "筛选、详情与会话内查找",
                    "v9": "对话指挥中心与项目页面",
                    "v10": "精简导航与按需展开高级功能",
                    "v14": "Skill 资产库与项目关联",
                    "v18": "Finder、跨平台路径与首次安装提示",
                },
                "安全审计": {
                    "v9": "只读来源与独立管理数据库",
                    "v10": "知识修订、操作账本与成果指纹",
                    "v13": "来源健康、备份恢复与更新检查",
                    "v17": "路径约束与仅审核知识导出",
                    "v18": "macOS Application Support 与系统凭据隔离",
                },
                "项目规划": {
                    "v11": "跨 Agent 自动归类规则",
                    "v17": "目标、阶段、里程碑、验收、风险与下一步",
                },
                "发行适配": {
                    "v10": "Windows 便携数据与安装骨架",
                    "v13": "更新清单与哈希校验",
                    "v18": "Apple Silicon / Intel 的 app、DMG 与 CI 构建",
                },
            }
            workstreams = []
            for name, features in feature_matrix.items():
                cells = []
                for milestone in milestones:
                    evidence = milestone["evidence"][0] if milestone["evidence"] else {}
                    title = features.get(milestone["version"], "")
                    cells.append(
                        {
                            "milestone_id": milestone["id"],
                            "version": milestone["version"],
                            "title": title,
                            "status": "in_progress" if milestone["version"] == "v18" else ("done" if title else "empty"),
                            "source": str(evidence.get("source") or ""),
                            "conversation_id": str(evidence.get("id") or ""),
                            "count": len(milestone["evidence"]) if title else 0,
                        }
                    )
                workstreams.append({"name": name, "cells": cells})
        today, project_daily_entries, project_daily_hash = self._project_daily_entries(project_id)
        project_daily_summary, project_daily_generator, project_daily_model, project_daily_generated_at = (
            self._project_daily_result(
                project_id,
                today,
                project_daily_entries,
                project_daily_hash,
            )
        )
        project_model_config = summary_runtime_config()
        method_counts = {
            method: sum(1 for row in assignment_rows if str(row["method"]) == method)
            for method in ("manual", "rule", "keyword", "workspace")
        }
        detection_rule = {
            "project_id": project_id,
            "include_keywords": [],
            "exclude_keywords": [],
            "workspace_aliases": [],
            "path_patterns": [],
            "min_score": 0.78,
            "enabled": True,
            "configured": False,
        }
        if detection_row:
            detection_rule.update(
                {
                    "include_keywords": json.loads(detection_row["include_keywords"] or "[]"),
                    "exclude_keywords": json.loads(detection_row["exclude_keywords"] or "[]"),
                    "workspace_aliases": json.loads(detection_row["workspace_aliases"] or "[]"),
                    "path_patterns": json.loads(detection_row["path_patterns"] or "[]"),
                    "min_score": float(detection_row["min_score"]),
                    "enabled": bool(detection_row["enabled"]),
                    "configured": True,
                }
            )
        elif project_id in {str(rule["id"]) for rule in PROJECT_RULES}:
            builtin = next(rule for rule in PROJECT_RULES if str(rule["id"]) == project_id)
            detection_rule["include_keywords"] = list(builtin["keywords"])
        config_audit = self._project_config_audit(project, detection_rule, items)
        plan_source_hash = self._project_plan_source_hash(project, items, milestones)
        project_plan = self._template_project_plan(dict(project), items, milestones)
        plan_generator = "template"
        plan_model = ""
        plan_updated_at = 0.0
        if plan_row:
            try:
                project_plan = self._normalise_project_plan(json.loads(plan_row["plan_json"]))
                plan_generator = str(plan_row["generator"])
                plan_model = str(plan_row["model"])
                plan_updated_at = float(plan_row["updated_at"])
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        recent_conversations = []
        for item in items[:30]:
            assignment = assignment_map[(item.source, item.id)]
            method = str(assignment["method"])
            evidence = (
                self._project_rule_evidence(item, configured_rules)
                if method in {"rule", "keyword"}
                else None
            )
            matched_keywords = [
                str(value["keyword"]) for value in (evidence or {}).get("evidence", [])[:4]
            ]
            recent_conversations.append(
                {
                    "source": item.source,
                    "id": item.id,
                    "title": item.title,
                    "workspace": item.workspace,
                    "updated_at": item.updated_at,
                    "status": item.user_status or item.status,
                    "confidence": float(assignment["confidence"]),
                    "locked": bool(assignment["locked"]),
                    "method": method,
                    "matched_keywords": matched_keywords,
                }
            )
        result = {
            "project": dict(project),
            "conversation_count": len(items),
            "pending_count": sum(
                1
                for row in assignment_rows
                if float(row["confidence"]) < 0.8 and not row["locked"]
            ),
            "milestones": milestones,
            "workstreams": workstreams,
            "today": today,
            "today_summary": project_daily_summary,
            "today_generator": project_daily_generator,
            "today_model": project_daily_model,
            "today_generated_at": project_daily_generated_at,
            "today_model_available": bool(
                project_model_config["enabled"]
                and project_model_config["api_url"]
                and project_model_config["model"]
            ),
            "today_stats": self._daily_stats(project_daily_entries),
            "detection_rule": detection_rule,
            "config_audit": config_audit,
            "project_plan": project_plan,
            "plan_generator": plan_generator,
            "plan_model": plan_model,
            "plan_updated_at": plan_updated_at,
            "plan_stale": bool(plan_row and str(plan_row["source_hash"]) != plan_source_hash),
            "obsidian": self.obsidian_config(project_id),
            "classification": {
                "manual_count": method_counts["manual"],
                "rule_count": method_counts["rule"],
                "keyword_count": method_counts["keyword"] + method_counts["rule"],
                "workspace_count": method_counts["workspace"],
                "locked_count": sum(1 for row in assignment_rows if bool(row["locked"])),
                "sources": {
                    source: sum(1 for item in items if item.source == source)
                    for source in SOURCES
                },
                "average_confidence": (
                    sum(float(row["confidence"]) for row in assignment_rows) / len(assignment_rows)
                    if assignment_rows else 0
                ),
                "logic": [
                    "人工确认并锁定的归类优先，自动识别不会覆盖",
                    "同一套任务规则扫描全部 Agent；标题、摘要、备注或标签命中后统一进入当前项目",
                    "Agent 自带文件夹只作为“原生项目”筛选维度，不再自动创建 Hub 项目",
                    "工作区别名和路径特征只会增强已有任务关键词的置信度，不能单独决定归类",
                    "多个项目得分接近时不自动归类，进入未归属列表等待确认",
                ],
            },
            "recent_conversations": recent_conversations,
        }
        with self._lock:
            self._project_detail_cache[project_id] = {
                "key": project_cache_key,
                "built_at": time.time(),
                "payload": result,
            }
            if len(self._project_detail_cache) > 12:
                oldest_project_id = next(iter(self._project_detail_cache))
                if oldest_project_id != project_id:
                    self._project_detail_cache.pop(oldest_project_id, None)
        return result

    def save_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = clean_text(payload.get("name"), 120)
        if not name:
            raise ValueError("请填写项目名称")
        project_id = clean_text(payload.get("id"), 120) or f"manual-{secrets.token_hex(6)}"
        status = clean_text(payload.get("status"), 30) or "active"
        if status not in {"active", "maintenance", "paused", "done"}:
            raise ValueError("项目状态无效")
        description = clean_text(payload.get("description"), 1200)
        now = time.time()
        with notes_db() as conn:
            conn.execute(
                """
                INSERT INTO projects(id,name,description,status,origin,created_at,updated_at)
                VALUES(?,?,?,?, 'manual',?,?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name,description=excluded.description,
                  status=excluded.status,origin='manual',updated_at=excluded.updated_at
                """,
                (project_id, name, description, status, now, now),
            )
            conn.commit()
        self.record_activity(
            "project",
            "保存项目",
            project_id=project_id,
            summary=name,
            metadata={"status": status, "has_description": bool(description)},
        )
        return {"ok": True, "project_id": project_id, **self.projects()}

    @staticmethod
    def _project_rule_terms(value: Any, field_name: str) -> list[str]:
        raw_values = value if isinstance(value, list) else re.split(r"[,，;\n]+", str(value or ""))
        result: list[str] = []
        seen: set[str] = set()
        for raw in raw_values:
            term = clean_text(raw, 120).strip()
            key = term.casefold()
            if not term or key in seen:
                continue
            seen.add(key)
            result.append(term)
        if len(result) > 40:
            raise ValueError(f"{field_name}最多 40 项")
        return result

    def _project_rule_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_text(payload.get("project_id"), 120)
        with notes_db() as conn:
            project = conn.execute("SELECT id,name FROM projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            raise ValueError("项目不存在")
        try:
            min_score = float(payload.get("min_score", 0.78))
        except (TypeError, ValueError):
            raise ValueError("最低置信度无效") from None
        if not 0.5 <= min_score <= 0.99:
            raise ValueError("最低置信度需在 0.50 到 0.99 之间")
        rule = {
            "project_id": project_id,
            "project_name": str(project["name"]),
            "include_keywords": self._project_rule_terms(payload.get("include_keywords"), "包含词"),
            "exclude_keywords": self._project_rule_terms(payload.get("exclude_keywords"), "排除词"),
            "workspace_aliases": self._project_rule_terms(payload.get("workspace_aliases"), "工作区别名"),
            "path_patterns": self._project_rule_terms(payload.get("path_patterns"), "路径特征"),
            "min_score": min_score,
            "enabled": bool(payload.get("enabled", True)),
        }
        if rule["enabled"] and not any(
            rule[key]
            for key in ("include_keywords", "workspace_aliases", "path_patterns")
        ):
            raise ValueError("启用规则时至少填写一个包含词、工作区别名或路径特征")
        return rule

    def preview_project_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        rule = self._project_rule_payload(payload)
        with notes_db() as conn:
            rules = [
                value
                for value in self._configured_project_rules(conn)
                if str(value["id"]) != rule["project_id"]
            ]
            current = {
                (str(row["source"]), str(row["conversation_id"])): dict(row)
                for row in conn.execute("SELECT * FROM project_assignments")
            }
        if rule["enabled"]:
            rules.append(
                {
                    "id": rule["project_id"],
                    "name": rule["project_name"],
                    "include_keywords": rule["include_keywords"],
                    "exclude_keywords": rule["exclude_keywords"],
                    "workspace_aliases": rule["workspace_aliases"],
                    "path_patterns": rule["path_patterns"],
                    "min_score": rule["min_score"],
                    "method": "rule",
                }
            )
        preview = {
            "matched_count": 0,
            "added_count": 0,
            "moved_count": 0,
            "locked_skipped": 0,
            "conflict_count": 0,
            "by_source": {source: 0 for source in SOURCES},
            "samples": [],
        }
        with self._lock:
            items = list(self._items)
        for item in items:
            key = (item.source, item.id)
            assignment = current.get(key)
            detail = self._project_rule_evidence(item, rules)
            if assignment and bool(assignment["locked"]):
                if detail and detail.get("project_id") == rule["project_id"]:
                    preview["locked_skipped"] += 1
                continue
            if detail and detail.get("ambiguous"):
                if rule["project_name"] in detail.get("candidates", []):
                    preview["conflict_count"] += 1
                continue
            if not detail or str(detail.get("project_id")) != rule["project_id"]:
                continue
            preview["matched_count"] += 1
            preview["by_source"][item.source] += 1
            old_project = str(assignment["project_id"]) if assignment else ""
            if not old_project:
                preview["added_count"] += 1
            elif old_project != rule["project_id"]:
                preview["moved_count"] += 1
            if len(preview["samples"]) < 12:
                preview["samples"].append(
                    {
                        "source": item.source,
                        "id": item.id,
                        "title": item.title,
                        "workspace": item.workspace,
                        "old_project_id": old_project,
                        "confidence": float(detail["confidence"]),
                        "evidence": detail.get("evidence", [])[:4],
                    }
                )
        return {"ok": True, "rule": rule, "preview": preview}

    def save_project_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        rule = self._project_rule_payload(payload)
        now = time.time()
        with notes_db() as conn:
            conn.execute(
                """
                INSERT INTO project_detection_rules(
                  project_id,include_keywords,exclude_keywords,workspace_aliases,
                  path_patterns,min_score,enabled,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET
                  include_keywords=excluded.include_keywords,
                  exclude_keywords=excluded.exclude_keywords,
                  workspace_aliases=excluded.workspace_aliases,
                  path_patterns=excluded.path_patterns,
                  min_score=excluded.min_score,
                  enabled=excluded.enabled,
                  updated_at=excluded.updated_at
                """,
                (
                    rule["project_id"],
                    json.dumps(rule["include_keywords"], ensure_ascii=False),
                    json.dumps(rule["exclude_keywords"], ensure_ascii=False),
                    json.dumps(rule["workspace_aliases"], ensure_ascii=False),
                    json.dumps(rule["path_patterns"], ensure_ascii=False),
                    rule["min_score"],
                    int(rule["enabled"]),
                    now,
                ),
            )
            conn.commit()
        self._sync_projects()
        self.record_activity(
            "project_rule",
            "更新项目自动识别规则",
            project_id=rule["project_id"],
            summary=f"{len(rule['include_keywords'])} 个包含词 · {len(rule['workspace_aliases'])} 个工作区别名",
            metadata={
                "exclude_count": len(rule["exclude_keywords"]),
                "path_pattern_count": len(rule["path_patterns"]),
                "min_score": rule["min_score"],
                "enabled": rule["enabled"],
            },
        )
        return {"ok": True, "project_id": rule["project_id"], **self.projects()}

    def project_rule_suggestions(self, project_id: str) -> dict[str, Any]:
        project_id = clean_text(project_id, 160)
        if not project_id:
            raise ValueError("缺少项目 ID")
        with notes_db() as conn:
            project = conn.execute("SELECT id,name FROM projects WHERE id=?", (project_id,)).fetchone()
            rows = list(
                conn.execute(
                    """
                    SELECT source,conversation_id,locked FROM project_assignments
                    WHERE project_id=? ORDER BY locked DESC,updated_at DESC
                    """,
                    (project_id,),
                )
            )
        if not project:
            raise ValueError("项目不存在")
        with self._lock:
            items = [
                self._by_key[(str(row["source"]), str(row["conversation_id"]))]
                for row in rows
                if (str(row["source"]), str(row["conversation_id"])) in self._by_key
            ]
        if not items:
            return {
                "project_id": project_id,
                "include_keywords": [str(project["name"])],
                "workspace_aliases": [],
                "path_patterns": [],
                "exclude_keywords": [],
                "confidence": 0.4,
                "rationale": "项目暂时没有可用于学习规则的对话。",
            }
        token_counts: dict[str, int] = {}
        for item in items:
            for token in knowledge_tokens(f"{item.title}\n{item.preview}"):
                if 2 <= len(token) <= 40:
                    token_counts[token] = token_counts.get(token, 0) + 1
        threshold = 1 if len(items) < 3 else 2
        stop = {"今天", "好的", "这个", "然后", "继续", "可以", "问题", "项目", "对话", "功能", "目前"}
        keywords = [
            token
            for token, count in sorted(
                token_counts.items(), key=lambda pair: (-pair[1], -len(pair[0]), pair[0])
            )
            if count >= threshold and token not in stop
        ][:8]
        project_name = str(project["name"]).strip()
        if project_name and project_name.casefold() not in {value.casefold() for value in keywords}:
            keywords.insert(0, project_name)
        workspaces = sorted(
            {
                item.workspace.strip()
                for item in items
                if item.workspace.strip().casefold() not in GENERIC_WORKSPACES
            }
        )[:8]
        paths: list[str] = []
        for item in items:
            path = normalized_project_path(item.cwd)
            parts = [part for part in path.split("/") if part]
            if len(parts) >= 2:
                candidate = "/".join(parts[-2:])
                if candidate not in paths:
                    paths.append(candidate)
        return {
            "project_id": project_id,
            "include_keywords": keywords,
            "workspace_aliases": workspaces,
            "path_patterns": paths[:8],
            "exclude_keywords": [],
            "confidence": round(min(0.92, 0.55 + len(items) * 0.035), 2),
            "rationale": (
                f"根据该项目 {len(items)} 个已归类对话中的标题、摘要、工作区和路径生成；"
                "仅填入表单，不会自动保存。"
            ),
        }

    def assign_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = clean_text(payload.get("source"), 30)
        conversation_id = clean_text(payload.get("conversation_id"), 240)
        project_id = clean_text(payload.get("project_id"), 120)
        if (source, conversation_id) not in self._by_key:
            raise ValueError("对话不存在")
        with notes_db() as conn:
            if project_id and not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ValueError("项目不存在")
            if project_id:
                conn.execute(
                    """
                    INSERT INTO project_assignments(
                      source,conversation_id,project_id,confidence,method,locked,updated_at
                    ) VALUES(?,?,?,1,'manual',1,?)
                    ON CONFLICT(source,conversation_id) DO UPDATE SET
                      project_id=excluded.project_id,confidence=1,method='manual',
                      locked=1,updated_at=excluded.updated_at
                    """,
                    (source, conversation_id, project_id, time.time()),
                )
            else:
                conn.execute(
                    "DELETE FROM project_assignments WHERE source=? AND conversation_id=?",
                    (source, conversation_id),
                )
            conn.commit()
        self._sync_projects()
        self.record_activity(
            "project_assign",
            "确认对话项目归属",
            project_id=project_id,
            source=source,
            conversation_id=conversation_id,
            summary="已锁定项目归属" if project_id else "已移出项目",
            metadata={"target_project_id": project_id, "locked": bool(project_id)},
        )
        return {"ok": True, **self.projects()}

    def refresh_projects(self) -> dict[str, Any]:
        self._sync_projects()
        return {"ok": True, **self.projects()}

    def record_activity(
        self,
        kind: str,
        title: str,
        *,
        status: str = "completed",
        project_id: str = "",
        source: str = "",
        conversation_id: str = "",
        model: str = "",
        summary: str = "",
        metadata: dict[str, Any] | None = None,
        error: str = "",
        started_at: float | None = None,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> str:
        now = time.time()
        started = float(started_at or now)
        run_id = f"run-{int(now * 1000):x}-{secrets.token_hex(5)}"
        safe_metadata = metadata or {}
        try:
            metadata_json = json.dumps(safe_metadata, ensure_ascii=False)
        except (TypeError, ValueError):
            metadata_json = "{}"
        try:
            with notes_db() as conn:
                conn.execute(
                    """
                    INSERT INTO activity_runs(
                      id,kind,title,status,project_id,source,conversation_id,model,
                      summary,metadata_json,error,started_at,ended_at,duration_ms
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        run_id,
                        clean_text(kind, 60),
                        clean_text(title, 180),
                        status if status in {"completed", "failed", "cancelled"} else "completed",
                        clean_text(project_id, 120),
                        clean_text(source, 30),
                        clean_text(conversation_id, 240),
                        clean_text(model, 200),
                        clean_text(summary, 1200),
                        metadata_json,
                        clean_text(error, 1200),
                        started,
                        now,
                        max(0, round((now - started) * 1000)),
                    ),
                )
                for artifact in artifacts or []:
                    artifact_id = f"art-{int(now * 1000):x}-{secrets.token_hex(5)}"
                    artifact_metadata = artifact.get("metadata") or {}
                    conn.execute(
                        """
                        INSERT INTO artifacts(
                          id,run_id,project_id,kind,name,path,mime,size,content_hash,
                          metadata_json,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            artifact_id,
                            run_id,
                            clean_text(project_id, 120),
                            clean_text(artifact.get("kind"), 60) or "output",
                            clean_text(artifact.get("name"), 240),
                            clean_text(artifact.get("path"), 1200),
                            clean_text(artifact.get("mime"), 160),
                            max(0, int(artifact.get("size") or 0)),
                            clean_text(artifact.get("content_hash"), 128),
                            json.dumps(artifact_metadata, ensure_ascii=False),
                            now,
                        ),
                    )
                conn.commit()
        except (sqlite3.DatabaseError, TypeError, ValueError):
            return ""
        return run_id

    def activity_feed(
        self,
        project_id: str = "",
        kind: str = "",
        limit: int = 120,
    ) -> dict[str, Any]:
        clauses = []
        values: list[Any] = []
        if project_id:
            clauses.append("project_id=?")
            values.append(project_id)
        if kind:
            clauses.append("kind=?")
            values.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row_limit = min(300, max(1, limit))
        with notes_db() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM activity_runs {where} ORDER BY started_at DESC LIMIT ?",
                    (*values, row_limit),
                )
            ]
            run_ids = [str(row["id"]) for row in rows]
            artifact_rows: list[dict[str, Any]] = []
            if run_ids:
                placeholders = ",".join("?" for _ in run_ids)
                artifact_rows = [
                    dict(row)
                    for row in conn.execute(
                        f"SELECT * FROM artifacts WHERE run_id IN ({placeholders}) ORDER BY created_at DESC",
                        run_ids,
                    )
                ]
            pinned_clauses = ["pinned=1", "exists_now=1"]
            pinned_values: list[Any] = []
            if project_id:
                pinned_clauses.append("project_id=?")
                pinned_values.append(project_id)
            pinned_files = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT * FROM project_files
                    WHERE {' AND '.join(pinned_clauses)}
                    ORDER BY modified_at DESC LIMIT 100
                    """,
                    pinned_values,
                )
            ]
        artifacts_map: dict[str, list[dict[str, Any]]] = {}
        for artifact in artifact_rows:
            try:
                artifact["metadata"] = json.loads(artifact.pop("metadata_json") or "{}")
            except (TypeError, ValueError):
                artifact["metadata"] = {}
            artifacts_map.setdefault(str(artifact["run_id"]), []).append(artifact)
        counts: dict[str, int] = {}
        for row in rows:
            try:
                row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
            except (TypeError, ValueError):
                row["metadata"] = {}
            row["artifacts"] = artifacts_map.get(str(row["id"]), [])
            counts[str(row["kind"])] = counts.get(str(row["kind"]), 0) + 1
        return {"runs": rows, "pinned_files": pinned_files, "counts": counts}

    @staticmethod
    def _knowledge_snapshot(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        fields = (
            "id", "type", "title", "content", "scope", "project_id", "status",
            "confidence", "origin", "source_day", "supersedes_id", "valid_from",
            "valid_until", "revoked_at", "sensitivity", "review_note", "updated_at",
        )
        return {field: row[field] for field in fields if field in row.keys()}

    def _store_knowledge_revision(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row | dict[str, Any],
        action: str,
    ) -> None:
        knowledge_id = str(row["id"])
        revision_no = int(
            conn.execute(
                "SELECT COALESCE(MAX(revision_no),0)+1 FROM knowledge_revisions WHERE knowledge_id=?",
                (knowledge_id,),
            ).fetchone()[0]
        )
        conn.execute(
            """
            INSERT INTO knowledge_revisions(
              id,knowledge_id,revision_no,action,snapshot_json,changed_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                f"rev-{knowledge_id}-{revision_no}-{secrets.token_hex(3)}",
                knowledge_id,
                revision_no,
                clean_text(action, 60),
                json.dumps(self._knowledge_snapshot(row), ensure_ascii=False),
                time.time(),
            ),
        )

    def _detect_knowledge_conflicts(
        self,
        conn: sqlite3.Connection,
        knowledge_id: str,
    ) -> int:
        row = conn.execute("SELECT * FROM knowledge_items WHERE id=?", (knowledge_id,)).fetchone()
        if not row or row["status"] not in {"pending", "approved"}:
            return 0
        clauses = [
            "id<>?",
            "type=?",
            "status='approved'",
            "revoked_at=0",
            "(valid_until=0 OR valid_until>?)",
        ]
        values: list[Any] = [knowledge_id, row["type"], time.time()]
        if row["scope"] == "project" and row["project_id"]:
            clauses.append("project_id=?")
            values.append(row["project_id"])
        else:
            clauses.append("scope=?")
            values.append(row["scope"])
        created = 0
        for candidate in conn.execute(
            f"SELECT * FROM knowledge_items WHERE {' AND '.join(clauses)}",
            values,
        ):
            similarity = text_similarity(row["content"], candidate["content"])
            if similarity < 0.34 or str(row["content"]).strip() == str(candidate["content"]).strip():
                continue
            source_id, target_id = sorted((knowledge_id, str(candidate["id"])))
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO knowledge_relations(
                  source_knowledge_id,target_knowledge_id,relation,status,reason,created_at
                ) VALUES(?,?,'possible_conflict','open',?,?)
                """,
                (
                    source_id,
                    target_id,
                    f"同类型、同作用域内容相似度 {similarity:.0%}，建议确认是否为更新或冲突。",
                    time.time(),
                ),
            )
            created += int(cursor.rowcount > 0)
        return created

    def knowledge_history(self, knowledge_id: str) -> dict[str, Any]:
        with notes_db() as conn:
            item = conn.execute("SELECT * FROM knowledge_items WHERE id=?", (knowledge_id,)).fetchone()
            if not item:
                raise ValueError("Knowledge item not found")
            revisions = [dict(row) for row in conn.execute(
                "SELECT * FROM knowledge_revisions WHERE knowledge_id=? ORDER BY revision_no DESC",
                (knowledge_id,),
            )]
            relations = [dict(row) for row in conn.execute(
                """
                SELECT * FROM knowledge_relations
                WHERE source_knowledge_id=? OR target_knowledge_id=?
                ORDER BY created_at DESC
                """,
                (knowledge_id, knowledge_id),
            )]
            evidence = [dict(row) for row in conn.execute(
                "SELECT * FROM knowledge_evidence WHERE knowledge_id=? ORDER BY created_at",
                (knowledge_id,),
            )]
        for revision in revisions:
            try:
                revision["snapshot"] = json.loads(revision.pop("snapshot_json"))
            except (TypeError, ValueError):
                revision["snapshot"] = {}
        return {
            "item": dict(item),
            "revisions": revisions,
            "relations": relations,
            "evidence": evidence,
        }

    @staticmethod
    def _is_reparse_path(path: Path) -> bool:
        try:
            info = path.stat(follow_symlinks=False)
            return bool(getattr(info, "st_file_attributes", 0) & 0x400)
        except OSError:
            return True

    @staticmethod
    def _validated_project_root(value: str) -> Path | None:
        raw = str(value or "").strip()
        if not raw or raw.startswith(("\\\\.\\", "\\\\?\\", "\\\\")):
            return None
        try:
            path = Path(raw).resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if not path.is_absolute() or not path.is_dir() or path.is_symlink():
            return None
        if ConversationIndex._is_reparse_path(path):
            return None
        if path == Path(path.anchor):
            return None
        home = Path.home().resolve()
        blocked_exact = {
            home,
            APP_DIR.resolve(),
        }
        if path in blocked_exact or path.name.casefold() in GENERIC_WORKSPACES:
            return None
        blocked_trees = (
            (home / "AppData").resolve(strict=False),
            (home / "Library" / "Application Support").resolve(strict=False),
            (home / ".codex").resolve(strict=False),
            (home / ".workbuddy").resolve(strict=False),
            (home / ".claude").resolve(strict=False),
            (home / ".hermes").resolve(strict=False),
            (home / ".qclaw").resolve(strict=False),
            (home / ".ssh").resolve(strict=False),
            (home / ".gnupg").resolve(strict=False),
        )
        for blocked in blocked_trees:
            try:
                path.relative_to(blocked)
                return None
            except ValueError:
                pass
        return path

    def discover_project_roots(self, project_id: str) -> dict[str, Any]:
        with notes_db() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ValueError("Project not found")
            keys = {
                (str(row["source"]), str(row["conversation_id"]))
                for row in conn.execute(
                    "SELECT source,conversation_id FROM project_assignments WHERE project_id=?",
                    (project_id,),
                )
            }
        with self._lock:
            candidates = [item.cwd for item in self._items if (item.source, item.id) in keys and item.cwd]
        roots: dict[str, Path] = {}
        for candidate in candidates:
            path = self._validated_project_root(candidate)
            if path:
                roots[os.path.normcase(str(path))] = path
        now = time.time()
        with notes_db() as conn:
            for path in roots.values():
                root_id = f"root-{hashlib.sha1(f'{project_id}:{path}'.encode('utf-8')).hexdigest()[:20]}"
                conn.execute(
                    """
                    INSERT INTO project_roots(
                      id,project_id,root_path,canonical_path,origin,enabled,
                      confirmed_at,created_at,updated_at
                    ) VALUES(?,?,?,?, 'conversation_cwd',0,0,?,?)
                    ON CONFLICT(project_id,canonical_path) DO UPDATE SET
                      root_path=excluded.root_path,updated_at=excluded.updated_at
                    """,
                    (root_id, project_id, str(path), str(path), now, now),
                )
            conn.commit()
            rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_roots WHERE project_id=? ORDER BY enabled DESC,root_path",
                    (project_id,),
                )
            ]
        return {"project_id": project_id, "roots": rows}

    def add_project_root(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_text(payload.get("project_id"), 120)
        raw_path = clean_text(payload.get("path"), 1200)
        path = self._validated_project_root(raw_path)
        if not path:
            raise ValueError("目录不存在、范围过宽，或属于受保护目录")
        now = time.time()
        root_id = f"root-{hashlib.sha1(f'{project_id}:{path}'.encode('utf-8')).hexdigest()[:20]}"
        enabled = 1 if payload.get("enabled", True) else 0
        with notes_db() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ValueError("Project not found")
            conn.execute(
                """
                INSERT INTO project_roots(
                  id,project_id,root_path,canonical_path,origin,enabled,
                  confirmed_at,created_at,updated_at
                ) VALUES(?,?,?,?,'manual',?,?,?,?)
                ON CONFLICT(project_id,canonical_path) DO UPDATE SET
                  enabled=excluded.enabled,
                  confirmed_at=excluded.confirmed_at,
                  updated_at=excluded.updated_at
                """,
                (
                    root_id,
                    project_id,
                    str(path),
                    str(path),
                    enabled,
                    now if enabled else 0,
                    now,
                    now,
                ),
            )
            conn.commit()
        self.record_activity(
            "project_root",
            "添加项目文件目录",
            project_id=project_id,
            summary=path.name,
            metadata={"root_id": root_id, "enabled": bool(enabled), "origin": "manual"},
        )
        return {"ok": True, **self.project_files(project_id)}

    def confirm_project_root(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_text(payload.get("project_id"), 120)
        root_id = clean_text(payload.get("root_id"), 120)
        enabled = 1 if payload.get("enabled") else 0
        with notes_db() as conn:
            row = conn.execute(
                "SELECT * FROM project_roots WHERE id=? AND project_id=?",
                (root_id, project_id),
            ).fetchone()
            if not row:
                raise ValueError("Project root not found")
            validated = self._validated_project_root(str(row["canonical_path"]))
            if enabled and not validated:
                raise ValueError("该路径过宽、不可访问或属于受保护目录")
            now = time.time()
            conn.execute(
                """
                UPDATE project_roots SET enabled=?,confirmed_at=?,updated_at=?
                WHERE id=? AND project_id=?
                """,
                (enabled, now if enabled else 0, now, root_id, project_id),
            )
            conn.commit()
        self.record_activity(
            "project_root",
            "确认项目文件目录" if enabled else "停用项目文件目录",
            project_id=project_id,
            summary="项目文件目录设置已更新",
            metadata={"root_id": root_id, "enabled": bool(enabled)},
        )
        return {"ok": True, **self.project_files(project_id)}

    def project_files(self, project_id: str, limit: int = 200) -> dict[str, Any]:
        roots_result = self.discover_project_roots(project_id)
        with notes_db() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM project_files
                    WHERE project_id=? AND exists_now=1
                    ORDER BY pinned DESC,modified_at DESC LIMIT ?
                    """,
                    (project_id, min(300, max(1, limit))),
                )
            ]
            scan = conn.execute(
                """
                SELECT * FROM project_file_scans
                WHERE project_id=? ORDER BY started_at DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        return {
            "project_id": project_id,
            "roots": roots_result["roots"],
            "files": rows,
            "scan": dict(scan) if scan else None,
            "pinned_count": sum(1 for row in rows if row["pinned"]),
        }

    def refresh_project_files(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_text(payload.get("project_id"), 120)
        started = time.time()
        scan_id = f"scan-{int(started * 1000):x}-{secrets.token_hex(4)}"
        with notes_db() as conn:
            roots = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_roots WHERE project_id=? AND enabled=1",
                    (project_id,),
                )
            ]
        if not roots:
            raise ValueError("请先确认至少一个项目文件目录")
        heap: list[tuple[int, int, dict[str, Any]]] = []
        visited = excluded = errors = 0
        truncated = False
        sequence = 0
        deadline = started + 3.0
        for root_row in roots[:8]:
            root = self._validated_project_root(str(root_row["canonical_path"]))
            if not root:
                errors += 1
                continue
            stack: list[tuple[Path, int]] = [(root, 0)]
            while stack:
                if time.time() >= deadline or visited >= 20000:
                    truncated = True
                    break
                directory, depth = stack.pop()
                try:
                    with os.scandir(directory) as iterator:
                        entries = list(iterator)
                except OSError:
                    errors += 1
                    continue
                for entry in entries:
                    visited += 1
                    if time.time() >= deadline or visited >= 20000:
                        truncated = True
                        break
                    name_lower = entry.name.casefold()
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        errors += 1
                        continue
                    if entry.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400):
                        excluded += 1
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if (
                            depth >= 8
                            or name_lower in FILE_SCAN_SKIP_DIRS
                            or name_lower.startswith(".")
                        ):
                            excluded += 1
                            continue
                        stack.append((Path(entry.path), depth + 1))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        excluded += 1
                        continue
                    suffix = Path(entry.name).suffix.casefold()
                    sensitive = (
                        any(term in name_lower for term in FILE_SCAN_SENSITIVE_NAMES)
                        or suffix in FILE_SCAN_SENSITIVE_EXTENSIONS
                        or name_lower.endswith(("-wal", "-shm", ".lock", ".tmp", ".swp", "~"))
                    )
                    if sensitive:
                        excluded += 1
                        continue
                    sequence += 1
                    item = {
                        "path": os.path.abspath(entry.path),
                        "root_path": str(root),
                        "name": entry.name,
                        "extension": suffix,
                        "category": file_category(Path(entry.name)),
                        "size": int(info.st_size),
                        "modified_at": float(info.st_mtime),
                        "modified_ns": int(getattr(info, "st_mtime_ns", int(info.st_mtime * 1e9))),
                    }
                    marker = (item["modified_ns"], sequence, item)
                    if len(heap) < 200:
                        heapq.heappush(heap, marker)
                    elif marker[:2] > heap[0][:2]:
                        heapq.heapreplace(heap, marker)
                if truncated:
                    break
            if truncated:
                break
        found = [entry[2] for entry in sorted(heap, reverse=True)]
        now = time.time()
        with notes_db() as conn:
            previous = {
                str(row["path"]): dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_files WHERE project_id=?",
                    (project_id,),
                )
            }
            if not truncated and errors == 0:
                conn.execute("UPDATE project_files SET exists_now=0 WHERE project_id=?", (project_id,))
            for item in found:
                old = previous.get(item["path"])
                if not old:
                    change_state = "new"
                    previous_modified = 0
                elif (
                    float(old["modified_at"]) != item["modified_at"]
                    or int(old["size"]) != item["size"]
                ):
                    change_state = "modified"
                    previous_modified = float(old["modified_at"])
                else:
                    change_state = "seen"
                    previous_modified = float(old["previous_modified_at"])
                file_key = f"{project_id}:{item['path']}"
                file_id = f"file-{hashlib.sha1(file_key.encode('utf-8')).hexdigest()[:24]}"
                conn.execute(
                    """
                    INSERT INTO project_files(
                      id,project_id,path,root_path,name,extension,category,size,
                      modified_at,first_seen_at,last_seen_at,previous_modified_at,
                      change_state,pinned,role,user_label,exists_now
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,'support','',1)
                    ON CONFLICT(project_id,path) DO UPDATE SET
                      id=excluded.id,root_path=excluded.root_path,name=excluded.name,
                      extension=excluded.extension,category=excluded.category,size=excluded.size,
                      modified_at=excluded.modified_at,last_seen_at=excluded.last_seen_at,
                      previous_modified_at=excluded.previous_modified_at,
                      change_state=excluded.change_state,exists_now=1
                    """,
                    (
                        file_id,
                        project_id,
                        item["path"],
                        item["root_path"],
                        item["name"],
                        item["extension"],
                        item["category"],
                        item["size"],
                        item["modified_at"],
                        now if not old else float(old["first_seen_at"]),
                        now,
                        previous_modified,
                        change_state,
                    ),
                )
            conn.execute(
                """
                INSERT INTO project_file_scans(
                  id,project_id,status,root_count,visited_count,returned_count,
                  excluded_count,error_count,truncated,error,started_at,finished_at,duration_ms
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    scan_id,
                    project_id,
                    "completed" if found or not errors else "partial",
                    len(roots),
                    visited,
                    len(found),
                    excluded,
                    errors,
                    int(truncated),
                    "",
                    started,
                    now,
                    max(0, round((now - started) * 1000)),
                ),
            )
            conn.commit()
        self.record_activity(
            "file_scan",
            "刷新项目最近文件",
            project_id=project_id,
            summary=f"返回 {len(found)} 个最近文件",
            metadata={
                "root_count": len(roots),
                "visited": visited,
                "returned": len(found),
                "excluded": excluded,
                "errors": errors,
                "truncated": truncated,
            },
            started_at=started,
        )
        return {"ok": True, **self.project_files(project_id)}

    def pin_project_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_text(payload.get("project_id"), 120)
        file_id = clean_text(payload.get("file_id"), 120)
        role = clean_text(payload.get("role"), 30) or "support"
        if role not in {"final", "support", "reference"}:
            raise ValueError("Invalid file role")
        pinned = 1 if payload.get("pinned") else 0
        label = clean_text(payload.get("label"), 180)
        with notes_db() as conn:
            row = conn.execute(
                "SELECT * FROM project_files WHERE id=? AND project_id=? AND exists_now=1",
                (file_id, project_id),
            ).fetchone()
            if not row:
                raise ValueError("Project file not found")
            conn.execute(
                """
                UPDATE project_files SET pinned=?,role=?,user_label=?
                WHERE id=? AND project_id=?
                """,
                (pinned, role, label, file_id, project_id),
            )
            conn.commit()
        self.record_activity(
            "artifact_pin",
            "确认项目成果文件" if pinned else "取消项目成果文件",
            project_id=project_id,
            summary=label or str(row["name"]),
            metadata={"file_id": file_id, "role": role, "pinned": bool(pinned)},
            artifacts=[
                {
                    "kind": "project_file",
                    "name": label or str(row["name"]),
                    "path": str(row["path"]),
                    "size": int(row["size"]),
                    "metadata": {"file_id": file_id, "role": role},
                }
            ] if pinned else None,
        )
        return {"ok": True, **self.project_files(project_id)}

    def reveal_project_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_text(payload.get("project_id"), 120)
        file_id = clean_text(payload.get("file_id"), 120)
        with notes_db() as conn:
            row = conn.execute(
                """
                SELECT f.*,r.canonical_path,r.enabled FROM project_files f
                JOIN project_roots r
                  ON r.project_id=f.project_id AND r.canonical_path=f.root_path
                WHERE f.id=? AND f.project_id=? AND f.exists_now=1
                """,
                (file_id, project_id),
            ).fetchone()
        if not row or not row["enabled"]:
            raise ValueError("Project file is not available")
        root = self._validated_project_root(str(row["canonical_path"]))
        try:
            path = Path(str(row["path"])).resolve(strict=True)
            if not root or not path.is_file() or path.is_symlink() or path.relative_to(root) is None:
                raise ValueError
        except (OSError, RuntimeError, ValueError):
            raise ValueError("文件已移动或不再位于已确认目录内") from None
        if os.name == "nt":
            subprocess.Popen(
                ["explorer.exe", f"/select,{path}"],
                cwd=str(root),
                shell=False,
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        elif sys.platform == "darwin":
            subprocess.Popen(["/usr/bin/open", "-R", str(path)], close_fds=True)
        else:
            subprocess.Popen(["xdg-open", str(path.parent)], close_fds=True)
        self.record_activity(
            "file_reveal",
            "在资源管理器中显示项目文件",
            project_id=project_id,
            summary=str(row["name"]),
            metadata={"file_id": file_id},
        )
        return {"ok": True, "file_id": file_id}

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
            text = redact_model_text(text)
            if role in {"user", "assistant"} and text:
                result.append(
                    {
                        "role": role,
                        "text": text,
                        "timestamp": float(message.get("timestamp") or 0),
                    }
                )
        return result

    def classification_inbox(
        self,
        mode: str = "unassigned",
        project_id: str = "",
        limit: int = 300,
    ) -> dict[str, Any]:
        if mode not in {"unassigned", "pending", "project"}:
            raise ValueError("Invalid classification inbox mode")
        with notes_db() as conn:
            assignment_rows = {
                (row["source"], row["conversation_id"]): dict(row)
                for row in conn.execute("SELECT * FROM project_assignments")
            }
            project_rows = {
                str(row["id"]): dict(row)
                for row in conn.execute("SELECT id,name,status FROM projects ORDER BY name")
            }
            configured_rules = self._configured_project_rules(conn)
        with self._lock:
            source_items = list(self._items)
        items = []
        for item in source_items:
            assignment = assignment_rows.get((item.source, item.id))
            if mode == "unassigned" and assignment:
                continue
            if mode == "pending" and (
                not assignment
                or bool(assignment["locked"])
                or float(assignment["confidence"]) >= 0.8
            ):
                continue
            if mode == "project" and (
                not assignment or str(assignment["project_id"]) != project_id
            ):
                continue
            method = str(assignment["method"]) if assignment else ""
            if method == "manual":
                reason = "已由你手动确认并锁定"
            elif method in {"keyword", "rule"}:
                evidence = self._project_rule_evidence(item, configured_rules) or {}
                hits = [
                    f"「{value['keyword']}」({ '/'.join(value['locations']) })"
                    for value in evidence.get("evidence", [])[:3]
                ]
                reason = f"命中项目规则：{'、'.join(hits)}" if hits else "命中项目识别规则"
            elif method == "workspace":
                reason = f"同一工作区「{item.workspace or '未命名'}」至少出现 2 个对话"
            else:
                evidence = self._project_rule_evidence(item, configured_rules)
                if evidence and evidence.get("ambiguous"):
                    reason = f"同时命中多个项目：{'、'.join(evidence.get('candidates', []))}"
                elif evidence:
                    reason = f"可能属于「{evidence['project_name']}」，建议人工确认"
                else:
                    reason = "未找到关键词、稳定工作区或人工归类线索"
            items.append(
                {
                    "source": item.source,
                    "id": item.id,
                    "title": item.title,
                    "workspace": item.workspace,
                    "updated_at": item.updated_at,
                    "preview": clean_text(item.preview, 260),
                    "project_id": str(assignment["project_id"]) if assignment else "",
                    "project_name": project_rows.get(
                        str(assignment["project_id"]) if assignment else "", {}
                    ).get("name", ""),
                    "confidence": float(assignment["confidence"]) if assignment else 0,
                    "locked": bool(assignment["locked"]) if assignment else False,
                    "method": method,
                    "reason": reason,
                }
            )
        items.sort(key=lambda row: row["updated_at"], reverse=True)
        return {
            "mode": mode,
            "project_id": project_id,
            "total": len(items),
            "items": items[: min(500, max(1, limit))],
            "projects": list(project_rows.values()),
        }

    def assign_projects_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_text(payload.get("project_id"), 120)
        raw_items = payload.get("conversations") or []
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("请选择至少一个对话")
        if len(raw_items) > 500:
            raise ValueError("一次最多处理 500 个对话")
        keys: list[tuple[str, str]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise ValueError("对话选择格式无效")
            key = (
                clean_text(raw.get("source"), 30),
                clean_text(raw.get("id") or raw.get("conversation_id"), 240),
            )
            if key not in self._by_key:
                raise ValueError(f"对话不存在：{key[0]}:{key[1]}")
            keys.append(key)
        now = time.time()
        with notes_db() as conn:
            if project_id and not conn.execute(
                "SELECT 1 FROM projects WHERE id=?", (project_id,)
            ).fetchone():
                raise ValueError("目标项目不存在")
            if project_id:
                conn.executemany(
                    """
                    INSERT INTO project_assignments(
                      source,conversation_id,project_id,confidence,method,locked,updated_at
                    ) VALUES(?,?,?,1,'manual',1,?)
                    ON CONFLICT(source,conversation_id) DO UPDATE SET
                      project_id=excluded.project_id,confidence=1,method='manual',
                      locked=1,updated_at=excluded.updated_at
                    """,
                    [(source, conversation_id, project_id, now) for source, conversation_id in keys],
                )
            else:
                conn.executemany(
                    "DELETE FROM project_assignments WHERE source=? AND conversation_id=?",
                    keys,
                )
            conn.commit()
        self._sync_projects()
        self.record_activity(
            "project_assign",
            "批量确认项目归属",
            project_id=project_id,
            summary=f"处理 {len(keys)} 个对话",
            metadata={
                "conversation_count": len(keys),
                "target_project_id": project_id,
                "locked": bool(project_id),
            },
        )
        return {"ok": True, "updated": len(keys), **self.projects()}

    def merge_projects(self, payload: dict[str, Any]) -> dict[str, Any]:
        source_id = clean_text(payload.get("source_project_id"), 120)
        target_id = clean_text(payload.get("target_project_id"), 120)
        if not source_id or not target_id or source_id == target_id:
            raise ValueError("请选择两个不同的项目")
        now = time.time()
        with notes_db() as conn:
            source = conn.execute("SELECT * FROM projects WHERE id=?", (source_id,)).fetchone()
            target = conn.execute("SELECT * FROM projects WHERE id=?", (target_id,)).fetchone()
            if not source or not target:
                raise ValueError("待合并项目不存在")
            conn.execute(
                """
                INSERT INTO project_aliases(source_project_id,target_project_id,created_at)
                VALUES(?,?,?)
                ON CONFLICT(source_project_id) DO UPDATE SET
                  target_project_id=excluded.target_project_id,created_at=excluded.created_at
                """,
                (source_id, target_id, now),
            )
            conn.execute(
                "UPDATE project_aliases SET target_project_id=? WHERE target_project_id=?",
                (target_id, source_id),
            )
            conn.execute(
                "UPDATE project_assignments SET project_id=?,updated_at=? WHERE project_id=?",
                (target_id, now, source_id),
            )
            conn.execute(
                "UPDATE knowledge_items SET project_id=?,updated_at=? WHERE project_id=?",
                (target_id, now, source_id),
            )
            conn.execute(
                "UPDATE activity_runs SET project_id=? WHERE project_id=?",
                (target_id, source_id),
            )
            conn.execute(
                "UPDATE artifacts SET project_id=? WHERE project_id=?",
                (target_id, source_id),
            )
            conn.execute(
                "UPDATE project_milestones SET project_id=?,updated_at=? WHERE project_id=? AND origin='manual'",
                (target_id, now, source_id),
            )
            conn.execute(
                "DELETE FROM project_milestones WHERE project_id=? AND origin='auto'",
                (source_id,),
            )
            conn.execute("DELETE FROM project_daily_summaries WHERE project_id=?", (source_id,))
            conn.execute("DELETE FROM project_files WHERE project_id=?", (source_id,))
            conn.execute("DELETE FROM project_file_scans WHERE project_id=?", (source_id,))
            conn.execute("DELETE FROM project_roots WHERE project_id=?", (source_id,))
            conn.execute(
                "UPDATE projects SET origin='manual',updated_at=? WHERE id=?",
                (now, target_id),
            )
            conn.execute("DELETE FROM projects WHERE id=?", (source_id,))
            conn.commit()
        self._sync_projects()
        self.record_activity(
            "project_merge",
            "合并项目",
            project_id=target_id,
            summary=f"{source_id} → {target_id}",
            metadata={"source_project_id": source_id, "target_project_id": target_id},
        )
        return {
            "ok": True,
            "source_project_id": source_id,
            "target_project_id": target_id,
            **self.projects(),
        }

    @staticmethod
    def _knowledge_title(kind: str, text: str) -> str:
        labels = {
            "achievement": "成果",
            "decision": "决策",
            "task": "待办",
            "project_state": "项目状态",
            "method": "方法",
            "fact": "事实",
            "preference": "偏好",
        }
        return f"{labels.get(kind, '知识')} · {claim_text(text, 56)}"

    def knowledge_items(
        self,
        status: str = "pending",
        project_id: str = "",
        limit: int = 200,
    ) -> dict[str, Any]:
        if status not in {
            "all", "pending", "approved", "rejected", "superseded", "revoked", "expired"
        }:
            raise ValueError("Invalid knowledge status")
        clauses = []
        values: list[Any] = []
        now = time.time()
        if status == "approved":
            clauses.extend(["status='approved'", "revoked_at=0", "(valid_until=0 OR valid_until>?)"])
            values.append(now)
        elif status == "expired":
            clauses.extend(["status='approved'", "valid_until>0", "valid_until<=?"])
            values.append(now)
        elif status != "all":
            clauses.append("status=?")
            values.append(status)
        if project_id:
            clauses.append("project_id=?")
            values.append(project_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with notes_db() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM knowledge_items {where} ORDER BY updated_at DESC LIMIT ?",
                    (*values, min(500, max(1, limit))),
                )
            ]
            raw_counts = {
                str(row["status"]): int(row["count"])
                for row in conn.execute(
                    "SELECT status,COUNT(*) AS count FROM knowledge_items GROUP BY status"
                )
            }
            expired_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM knowledge_items
                    WHERE status='approved' AND valid_until>0 AND valid_until<=?
                    """,
                    (now,),
                ).fetchone()[0]
            )
            counts = {
                **raw_counts,
                "expired": expired_count,
                "approved": max(0, raw_counts.get("approved", 0) - expired_count),
            }
            project_names = {
                str(row["id"]): str(row["name"])
                for row in conn.execute("SELECT id,name FROM projects")
            }
            item_ids = [str(row["id"]) for row in rows]
            evidence_rows: list[dict[str, Any]] = []
            revision_counts: dict[str, int] = {}
            relation_rows: list[dict[str, Any]] = []
            related_titles: dict[str, str] = {}
            if item_ids:
                placeholders = ",".join("?" for _ in item_ids)
                evidence_rows = [
                    dict(row)
                    for row in conn.execute(
                        f"""
                        SELECT * FROM knowledge_evidence
                        WHERE knowledge_id IN ({placeholders}) ORDER BY created_at
                        """,
                        item_ids,
                    )
                ]
                revision_counts = {
                    str(row["knowledge_id"]): int(row["revision_no"])
                    for row in conn.execute(
                        f"""
                        SELECT knowledge_id,MAX(revision_no) AS revision_no
                        FROM knowledge_revisions
                        WHERE knowledge_id IN ({placeholders})
                        GROUP BY knowledge_id
                        """,
                        item_ids,
                    )
                }
                relation_rows = [
                    dict(row)
                    for row in conn.execute(
                        f"""
                        SELECT * FROM knowledge_relations
                        WHERE source_knowledge_id IN ({placeholders})
                           OR target_knowledge_id IN ({placeholders})
                        ORDER BY created_at DESC
                        """,
                        (*item_ids, *item_ids),
                    )
                ]
                related_ids = {
                    str(row["source_knowledge_id"]) for row in relation_rows
                } | {
                    str(row["target_knowledge_id"]) for row in relation_rows
                }
                if related_ids:
                    related_placeholders = ",".join("?" for _ in related_ids)
                    related_titles = {
                        str(row["id"]): str(row["title"])
                        for row in conn.execute(
                            f"SELECT id,title FROM knowledge_items WHERE id IN ({related_placeholders})",
                            list(related_ids),
                        )
                    }
        evidence_map: dict[str, list[dict[str, Any]]] = {}
        for evidence in evidence_rows:
            evidence_map.setdefault(str(evidence["knowledge_id"]), []).append(evidence)
        relations_map: dict[str, list[dict[str, Any]]] = {}
        for relation in relation_rows:
            left = str(relation["source_knowledge_id"])
            right = str(relation["target_knowledge_id"])
            for knowledge_id, other_id in ((left, right), (right, left)):
                shaped = {**relation, "other_id": other_id, "other_title": related_titles.get(other_id, "")}
                relations_map.setdefault(knowledge_id, []).append(shaped)
        for row in rows:
            row["evidence"] = evidence_map.get(str(row["id"]), [])
            row["project_name"] = project_names.get(str(row["project_id"]), "")
            row["revision_no"] = revision_counts.get(str(row["id"]), 0)
            row["relations"] = relations_map.get(str(row["id"]), [])
            row["open_conflict_count"] = sum(
                1 for relation in row["relations"]
                if relation["relation"] == "possible_conflict" and relation["status"] == "open"
            )
            row["effective_status"] = (
                "expired"
                if row["status"] == "approved" and row["valid_until"] and row["valid_until"] <= now
                else row["status"]
            )
        return {"items": rows, "counts": counts}

    def generate_knowledge_candidates(self, payload: dict[str, Any]) -> dict[str, Any]:
        day = str(payload.get("day") or "")
        project_id = clean_text(payload.get("project_id"), 120)
        if project_id:
            if bool(payload.get("use_model")):
                self.generate_project_daily_summary(
                    {"project_id": project_id, "day": day, "use_model": True}
                )
            parsed_day, entries, source_hash = self._project_daily_entries(project_id, day)
            summary, generator, _, _ = self._project_daily_result(
                project_id, parsed_day, entries, source_hash
            )
            day = parsed_day
        else:
            daily = self.daily_summary(day)
            day = daily["day"]
            summary = daily["summary"]
            generator = daily["generator"]
        mapping = (
            ("main_focus", "project_state"),
            ("achievements", "achievement"),
            ("decisions", "decision"),
            ("unfinished", "task"),
            ("first_step", "task"),
        )
        now = time.time()
        created = 0
        with notes_db() as conn:
            for key, kind in mapping:
                raw_values = summary.get(key) or []
                if isinstance(raw_values, (str, dict)):
                    raw_values = [raw_values]
                for raw in raw_values:
                    if isinstance(raw, str):
                        text = clean_text(raw, 2000)
                        source = ""
                        conversation_id = ""
                        quote = text
                    elif isinstance(raw, dict):
                        text = clean_text(raw.get("text"), 1600)
                        reason = clean_text(raw.get("reason"), 500)
                        next_action = clean_text(raw.get("next_action"), 500)
                        if reason:
                            text += f"\n原因：{reason}"
                        if next_action:
                            text += f"\n下一步：{next_action}"
                        source = clean_text(raw.get("source"), 30)
                        conversation_id = clean_text(raw.get("conversation_id"), 240)
                        quote = clean_text(raw.get("text"), 600)
                    else:
                        continue
                    if not text:
                        continue
                    fingerprint = hashlib.sha256(
                        f"{project_id}\n{day}\n{kind}\n{text}".encode("utf-8")
                    ).hexdigest()
                    knowledge_id = f"kn-{fingerprint[:20]}"
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO knowledge_items(
                          id,fingerprint,type,title,content,scope,project_id,status,
                          confidence,origin,source_day,created_at,updated_at
                        ) VALUES(?,?,?,?,?,'project',?,'pending',?,?,?, ?,?)
                        """,
                        (
                            knowledge_id,
                            fingerprint,
                            kind,
                            self._knowledge_title(kind, text),
                            text,
                            project_id,
                            0.86 if str(generator).startswith("model") else 0.68,
                            f"summary:{generator}",
                            day,
                            now,
                            now,
                        ),
                    )
                    created += int(cursor.rowcount > 0)
                    if source and conversation_id:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO knowledge_evidence(
                              knowledge_id,source,conversation_id,message_index,quote,created_at
                            ) VALUES(?,?,?,-1,?,?)
                            """,
                            (knowledge_id, source, conversation_id, quote, now),
                        )
                    if cursor.rowcount > 0:
                        created_row = conn.execute(
                            "SELECT * FROM knowledge_items WHERE id=?", (knowledge_id,)
                        ).fetchone()
                        self._store_knowledge_revision(conn, created_row, "create")
                        self._detect_knowledge_conflicts(conn, knowledge_id)
            conn.commit()
        self.record_activity(
            "knowledge_extract",
            "从摘要提取知识候选",
            project_id=project_id,
            summary=f"新增 {created} 条候选",
            metadata={"day": day, "created": created, "generator": str(generator)},
        )
        return {"ok": True, "created": created, **self.knowledge_items("pending", project_id)}

    def review_knowledge(self, payload: dict[str, Any]) -> dict[str, Any]:
        knowledge_id = clean_text(payload.get("id"), 80)
        action = clean_text(payload.get("action"), 30)
        status_map = {
            "approve": "approved",
            "reject": "rejected",
            "restore": "pending",
            "supersede": "superseded",
            "revoke": "revoked",
        }
        if action not in status_map:
            raise ValueError("Invalid knowledge review action")
        kind = clean_text(payload.get("type"), 40)
        scope = clean_text(payload.get("scope"), 40)
        if kind and kind not in {
            "achievement", "decision", "task", "project_state", "method", "fact", "preference"
        }:
            raise ValueError("Invalid knowledge type")
        if scope and scope not in {"global", "project", "workspace", "agent"}:
            raise ValueError("Invalid knowledge scope")
        sensitivity = clean_text(payload.get("sensitivity"), 30)
        if sensitivity and sensitivity not in {"normal", "sensitive", "restricted"}:
            raise ValueError("Invalid knowledge sensitivity")
        valid_until_raw = payload.get("valid_until")
        valid_until = 0.0
        if valid_until_raw:
            try:
                if isinstance(valid_until_raw, (int, float)):
                    valid_until = float(valid_until_raw)
                else:
                    _, _, valid_until = parse_day(str(valid_until_raw))
                    valid_until -= 1
            except (TypeError, ValueError):
                raise ValueError("有效期格式无效") from None
        now = time.time()
        with notes_db() as conn:
            row = conn.execute("SELECT * FROM knowledge_items WHERE id=?", (knowledge_id,)).fetchone()
            if not row:
                raise ValueError("Knowledge item not found")
            current_revision = int(
                conn.execute(
                    "SELECT COALESCE(MAX(revision_no),0) FROM knowledge_revisions WHERE knowledge_id=?",
                    (knowledge_id,),
                ).fetchone()[0]
            )
            expected_revision = payload.get("expected_revision_no")
            if expected_revision not in (None, "") and int(expected_revision) != current_revision:
                raise ConflictError("知识卡已在其他页面更新，请刷新后重试")
            title = clean_text(payload.get("title"), 180) or str(row["title"])
            content = clean_text(payload.get("content"), 5000) or str(row["content"])
            project_id = (
                clean_text(payload.get("project_id"), 120)
                if "project_id" in payload
                else str(row["project_id"])
            )
            if project_id and not conn.execute(
                "SELECT 1 FROM projects WHERE id=?", (project_id,)
            ).fetchone():
                raise ValueError("Project not found")
            supersedes_id = clean_text(payload.get("supersedes_id"), 80)
            if supersedes_id:
                if supersedes_id == knowledge_id:
                    raise ValueError("知识卡不能替代自身")
                replaced = conn.execute(
                    "SELECT * FROM knowledge_items WHERE id=?", (supersedes_id,)
                ).fetchone()
                if not replaced:
                    raise ValueError("被替代的知识卡不存在")
            conn.execute(
                """
                UPDATE knowledge_items SET
                  type=?,title=?,content=?,scope=?,project_id=?,status=?,
                  supersedes_id=?,valid_from=?,valid_until=?,revoked_at=?,
                  sensitivity=?,review_note=?,updated_at=?,reviewed_at=?
                WHERE id=?
                """,
                (
                    kind or row["type"],
                    title,
                    content,
                    scope or row["scope"],
                    project_id,
                    status_map[action],
                    supersedes_id,
                    now if action == "approve" else float(row["valid_from"]),
                    0 if action == "restore" else (valid_until or float(row["valid_until"])),
                    now if action == "revoke" else (0 if action == "restore" else float(row["revoked_at"])),
                    sensitivity or str(row["sensitivity"]),
                    clean_text(payload.get("review_note"), 1000),
                    now,
                    now,
                    knowledge_id,
                ),
            )
            if supersedes_id:
                conn.execute(
                    """
                    UPDATE knowledge_items
                    SET status='superseded',updated_at=?,reviewed_at=?
                    WHERE id=?
                    """,
                    (now, now, supersedes_id),
                )
                replaced_after = conn.execute(
                    "SELECT * FROM knowledge_items WHERE id=?", (supersedes_id,)
                ).fetchone()
                self._store_knowledge_revision(conn, replaced_after, "superseded")
                conn.execute(
                    """
                    INSERT OR REPLACE INTO knowledge_relations(
                      source_knowledge_id,target_knowledge_id,relation,status,reason,
                      created_at,resolved_at
                    ) VALUES(?,?,'supersedes','resolved',?,?,?)
                    """,
                    (
                        knowledge_id,
                        supersedes_id,
                        clean_text(payload.get("review_note"), 500) or "用户确认新知识替代旧知识",
                        now,
                        now,
                    ),
                )
            updated = conn.execute(
                "SELECT * FROM knowledge_items WHERE id=?", (knowledge_id,)
            ).fetchone()
            self._store_knowledge_revision(conn, updated, action)
            conflicts_created = self._detect_knowledge_conflicts(conn, knowledge_id)
            conn.commit()
        self.record_activity(
            "knowledge_review",
            "审核知识卡",
            project_id=project_id,
            summary=f"{action} · {title}",
            metadata={
                "knowledge_id": knowledge_id,
                "action": action,
                "revision_no": current_revision + 1,
                "conflicts_created": conflicts_created,
            },
        )
        return {
            "ok": True,
            "id": knowledge_id,
            "status": status_map[action],
            "revision_no": current_revision + 1,
            "conflicts_created": conflicts_created,
        }

    def verify_knowledge_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        knowledge_id = clean_text(payload.get("knowledge_id"), 80)
        if not knowledge_id:
            raise ValueError("请选择知识卡")
        started = time.time()
        with notes_db() as conn:
            item = conn.execute("SELECT * FROM knowledge_items WHERE id=?", (knowledge_id,)).fetchone()
            if not item:
                raise ValueError("Knowledge item not found")
            evidence_rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM knowledge_evidence WHERE knowledge_id=?",
                    (knowledge_id,),
                )
            ]
        checked = []
        for evidence in evidence_rows:
            key = (str(evidence["source"]), str(evidence["conversation_id"]))
            conversation = self._by_key.get(key)
            status = "missing"
            content_hash = ""
            if conversation:
                status = "linked"
                quote = clean_text(evidence.get("quote"), 600)
                if quote:
                    messages = self._messages_for_item(conversation, limit=None)
                    combined = "\n".join(message["text"] for message in messages)
                    normalized_quote = re.sub(r"\s+", "", quote)
                    normalized_combined = re.sub(r"\s+", "", combined)
                    if normalized_quote and normalized_quote[:80] in normalized_combined:
                        status = "verified"
                        content_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
            checked.append((status, content_hash, time.time(), knowledge_id, *key))
        with notes_db() as conn:
            conn.executemany(
                """
                UPDATE knowledge_evidence
                SET evidence_status=?,content_hash=?,checked_at=?
                WHERE knowledge_id=? AND source=? AND conversation_id=?
                """,
                checked,
            )
            updated = conn.execute(
                "SELECT * FROM knowledge_items WHERE id=?", (knowledge_id,)
            ).fetchone()
            self._store_knowledge_revision(conn, updated, "verify_evidence")
            conn.commit()
        counts: dict[str, int] = {}
        for status, *_ in checked:
            counts[status] = counts.get(status, 0) + 1
        self.record_activity(
            "evidence_verify",
            "核验知识证据",
            project_id=str(item["project_id"]),
            summary=f"核验 {len(checked)} 条证据",
            metadata={"knowledge_id": knowledge_id, "counts": counts},
            started_at=started,
        )
        return {"ok": True, "knowledge_id": knowledge_id, "counts": counts}

    def resolve_knowledge_relation(self, payload: dict[str, Any]) -> dict[str, Any]:
        source_id = clean_text(payload.get("source_knowledge_id"), 80)
        target_id = clean_text(payload.get("target_knowledge_id"), 80)
        relation = clean_text(payload.get("relation"), 40) or "possible_conflict"
        action = clean_text(payload.get("action"), 30)
        if action not in {"resolve", "dismiss", "reopen"}:
            raise ValueError("Invalid relation action")
        status = {"resolve": "resolved", "dismiss": "dismissed", "reopen": "open"}[action]
        with notes_db() as conn:
            cursor = conn.execute(
                """
                UPDATE knowledge_relations SET status=?,resolved_at=?
                WHERE source_knowledge_id=? AND target_knowledge_id=? AND relation=?
                """,
                (
                    status,
                    0 if status == "open" else time.time(),
                    source_id,
                    target_id,
                    relation,
                ),
            )
            if cursor.rowcount < 1:
                raise ValueError("Knowledge relation not found")
            conn.commit()
        self.record_activity(
            "knowledge_conflict",
            "处理知识冲突",
            summary=f"{action} · {source_id} ↔ {target_id}",
            metadata={"source_id": source_id, "target_id": target_id, "status": status},
        )
        return {"ok": True, "status": status}

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
            project_id = clean_text(payload.get("project_id"), 120)
            if not project_id:
                raise ValueError("请选择项目")
            with notes_db() as conn:
                keys = [
                    (str(row["source"]), str(row["conversation_id"]))
                    for row in conn.execute(
                        "SELECT source,conversation_id FROM project_assignments WHERE project_id=?",
                        (project_id,),
                    )
                ]
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
        include_knowledge = bool(payload.get("include_knowledge", True))
        project_id = clean_text(payload.get("project_id"), 120)
        now = time.time()
        project_name = ""
        knowledge = []
        with notes_db() as conn:
            if project_id:
                project = conn.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone()
                project_name = str(project["name"]) if project else ""
            if include_knowledge:
                if project_id:
                    knowledge = [
                        dict(row) for row in conn.execute(
                            """
                            SELECT id,type,title,content,scope,project_id,status,source_day
                            FROM knowledge_items
                            WHERE status='approved' AND project_id=?
                              AND revoked_at=0
                              AND (valid_from=0 OR valid_from<=?)
                              AND (valid_until=0 OR valid_until>?)
                            ORDER BY updated_at DESC
                            """,
                            (project_id, now, now),
                        )
                    ]
                else:
                    knowledge = [
                        dict(row) for row in conn.execute(
                            """
                            SELECT id,type,title,content,scope,project_id,status,source_day
                            FROM knowledge_items
                            WHERE status='approved' AND scope='global'
                              AND revoked_at=0
                              AND (valid_from=0 OR valid_from<=?)
                              AND (valid_until=0 OR valid_until>?)
                            ORDER BY updated_at DESC LIMIT 100
                            """,
                            (now, now),
                        )
                    ]
        for row in knowledge:
            row["title"] = redact_model_text(str(row.get("title") or ""))
            row["content"] = redact_model_text(str(row.get("content") or ""))
        bundles = []
        for item in items:
            messages = self._messages_for_item(item, start, end, None) if include_messages else []
            bundles.append(
                {
                    "conversation": {
                        "source": item.source,
                        "id": item.id,
                        "title": redact_model_text(item.title),
                        "workspace": redact_model_text(item.workspace),
                        "created_at": item.created_at,
                        "updated_at": item.updated_at,
                        "model": item.model,
                        "tags": [redact_model_text(tag) for tag in (item.tags or [])],
                        "user_status": item.user_status,
                        "favorite": item.favorite,
                        "note": redact_model_text(item.note) if include_notes else "",
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
                    "content_policy": "user_assistant_only_redacted",
                }
            ]
            rows.extend({"record_type": "knowledge", **row} for row in knowledge)
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
            lines = [
                f"# {project_name or 'AI 对话导出'}",
                "",
                f"> 导出时间：{datetime.fromtimestamp(now, LOCAL_TZ).isoformat(timespec='seconds')}",
                f"> 范围：{scope}；对话数：{len(bundles)}",
                "> 安全边界：仅包含用户/助手正文，并对常见密钥样式进行脱敏。",
                "",
            ]
            if knowledge:
                lines.extend(["## 已确认知识", ""])
                for row in knowledge:
                    lines.extend([f"### {row['title']}", "", markdown_text(row["content"]), ""])
            lines.extend(["## 对话", ""])
            for bundle in bundles:
                item = bundle["conversation"]
                lines.extend(
                    [
                        f"### {item['title']}",
                        "",
                        f"- 来源：{item['source']}",
                        f"- 对话 ID：`{item['id']}`",
                        f"- 工作区：{item['workspace'] or '未命名'}",
                        f"- 最近活动：{datetime.fromtimestamp(item['updated_at'], LOCAL_TZ).isoformat(timespec='seconds')}",
                    ]
                )
                if include_notes and item["note"]:
                    lines.extend([f"- 备注：{markdown_text(item['note'])}"])
                if item["tags"]:
                    lines.extend([f"- 标签：{', '.join(item['tags'])}"])
                lines.append("")
                for message in bundle["messages"]:
                    role = "用户" if message["role"] == "user" else "助手"
                    timestamp = (
                        datetime.fromtimestamp(message["timestamp"], LOCAL_TZ).isoformat(timespec="seconds")
                        if message["timestamp"] else ""
                    )
                    lines.extend(
                        [
                            f"#### {role}{f' · {timestamp}' if timestamp else ''}",
                            "",
                            markdown_text(message["text"]),
                            "",
                        ]
                    )
            content = "\n".join(lines).rstrip() + "\n"
            filename = f"{base_name}.md"
            mime = "text/markdown;charset=utf-8"
        if len(content.encode("utf-8")) > 25_000_000:
            raise ValueError("导出内容超过 25 MB，请缩小项目或日期范围")
        byte_count = len(content.encode("utf-8"))
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.record_activity(
            "export",
            "生成安全对话导出",
            project_id=project_id,
            summary=f"{output_format.upper()} · {len(bundles)} 个对话",
            metadata={
                "scope": scope,
                "format": output_format,
                "conversation_count": len(bundles),
                "knowledge_count": len(knowledge),
                "bytes": byte_count,
                "filename": filename,
            },
            started_at=now,
            artifacts=[
                {
                    "kind": "export",
                    "name": filename,
                    "mime": mime,
                    "size": byte_count,
                    "content_hash": content_hash,
                    "metadata": {"scope": scope, "format": output_format},
                }
            ],
        )
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

    def context_pack(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_text(payload.get("project_id"), 120)
        if not project_id:
            raise ValueError("请选择要续接的项目")
        destination = clean_text(payload.get("destination"), 40) or "通用 Agent"
        day = str(payload.get("day") or "")
        with notes_db() as conn:
            project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise ValueError("项目不存在")
            assignment_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM project_assignments
                    WHERE project_id=? ORDER BY updated_at DESC
                    """,
                    (project_id,),
                )
            ]
            approved = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM knowledge_items
                    WHERE status='approved' AND (project_id=? OR scope='global')
                      AND revoked_at=0
                      AND (valid_from=0 OR valid_from<=?)
                      AND (valid_until=0 OR valid_until>?)
                    ORDER BY updated_at DESC LIMIT 120
                    """,
                    (project_id, time.time(), time.time()),
                )
            ]
            approved_evidence = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT e.* FROM knowledge_evidence e
                    JOIN knowledge_items k ON k.id=e.knowledge_id
                    WHERE k.status='approved' AND (k.project_id=? OR k.scope='global')
                      AND k.revoked_at=0
                      AND (k.valid_from=0 OR k.valid_from<=?)
                      AND (k.valid_until=0 OR k.valid_until>?)
                    ORDER BY e.created_at DESC LIMIT 200
                    """,
                    (project_id, time.time(), time.time()),
                )
            ]
        parsed_day, entries, source_hash = self._project_daily_entries(project_id, day)
        daily, generator, model, generated_at = self._project_daily_result(
            project_id, parsed_day, entries, source_hash
        )
        assignment_map = {
            (row["source"], row["conversation_id"]): row for row in assignment_rows
        }
        with self._lock:
            recent_items = [
                item for item in self._items
                if (item.source, item.id) in assignment_map
            ][:12]
        evidence: list[dict[str, str]] = []

        def summary_values(key: str) -> list[str]:
            result = []
            for raw in daily.get(key) or []:
                if isinstance(raw, dict):
                    text = redact_model_text(clean_text(raw.get("text"), 1000))
                    source = clean_text(raw.get("source"), 30)
                    conversation_id = clean_text(raw.get("conversation_id"), 240)
                    if source and conversation_id:
                        evidence.append(
                            {
                                "source": source,
                                "conversation_id": conversation_id,
                                "label": text,
                            }
                        )
                    reason = redact_model_text(clean_text(raw.get("reason"), 400))
                    next_action = redact_model_text(clean_text(raw.get("next_action"), 400))
                    if reason:
                        text += f"；原因：{reason}"
                    if next_action:
                        text += f"；下一步：{next_action}"
                else:
                    text = redact_model_text(clean_text(raw, 1000))
                if text:
                    result.append(text)
            return result

        knowledge_by_type: dict[str, list[str]] = {}
        for row in approved:
            knowledge_by_type.setdefault(str(row["type"]), []).append(
                redact_model_text(str(row["content"]))
            )
        for row in approved_evidence:
            evidence.append(
                {
                    "source": clean_text(row.get("source"), 30),
                    "conversation_id": clean_text(row.get("conversation_id"), 240),
                    "label": redact_model_text(clean_text(row.get("quote"), 600)) or "已确认知识证据",
                }
            )
        sections = {
            "objective": [
                redact_model_text(clean_text(project["description"], 1600))
                or f"继续推进「{redact_model_text(project['name'])}」"
            ],
            "current_state": summary_values("main_focus"),
            "completed": summary_values("achievements") + knowledge_by_type.get("achievement", []),
            "decisions": summary_values("decisions") + knowledge_by_type.get("decision", []),
            "constraints": (
                knowledge_by_type.get("fact", [])
                + knowledge_by_type.get("preference", [])
                + knowledge_by_type.get("method", [])
            ),
            "pending": summary_values("unfinished") + knowledge_by_type.get("task", []),
            "next_step": summary_values("first_step"),
            "recent_conversations": [
                f"{item.title}（{item.source}:{item.id}，{local_day(item.updated_at)}）"
                for item in recent_items
            ],
        }
        labels = (
            ("objective", "目标"),
            ("current_state", "当前状态"),
            ("completed", "已完成"),
            ("decisions", "重要决定"),
            ("constraints", "约束与已确认事实"),
            ("pending", "未完成与阻塞"),
            ("next_step", "推荐下一步"),
            ("recent_conversations", "最近相关对话"),
        )
        lines = [
            f"# {project['name']} · 任务续接包",
            "",
            f"> 目标 Agent：{destination}",
            f"> 生成时间：{datetime.now(LOCAL_TZ).isoformat(timespec='seconds')}",
            f"> 项目日报：{parsed_day}；生成方式：{generator}{f' / {model}' if model else ''}",
            "> 仅使用已确认知识、项目摘要和用户/助手对话元数据；交接前请人工检查。",
            "",
        ]
        for key, label in labels:
            lines.extend([f"## {label}", ""])
            values = list(dict.fromkeys(value for value in sections[key] if value))[:20]
            lines.extend([f"- {markdown_text(value)}" for value in values] or ["- 暂无明确记录"])
            lines.append("")
        unique_evidence = []
        seen = set()
        for row in evidence:
            key = (row["source"], row["conversation_id"])
            if key not in seen:
                seen.add(key)
                unique_evidence.append(row)
        lines.extend(["## 证据索引", ""])
        lines.extend(
            [
                f"- `{row['source']}:{row['conversation_id']}` — {clean_text(row['label'], 160)}"
                for row in unique_evidence[:40]
            ]
            or ["- 暂无可定位证据"]
        )
        markdown = "\n".join(lines).rstrip() + "\n"
        structured = {
            "schema_version": HUB_SCHEMA_VERSION,
            "project": {"id": project_id, "name": project["name"], "status": project["status"]},
            "destination": destination,
            "generated_at": time.time(),
            "source_day": parsed_day,
            "sections": sections,
            "evidence": unique_evidence,
            "source_hash": source_hash,
            "privacy": "approved_knowledge_and_safe_metadata_only",
        }
        context_json = json.dumps(structured, ensure_ascii=False, indent=2)
        filename = f"{safe_filename(project['name'])}-context-pack-{parsed_day}.md"
        now = time.time()
        if approved:
            with notes_db() as conn:
                conn.executemany(
                    """
                    UPDATE knowledge_items
                    SET last_used_at=?,usage_count=usage_count+1
                    WHERE id=?
                    """,
                    [(now, str(row["id"])) for row in approved],
                )
                conn.commit()
        self.record_activity(
            "context_pack",
            "生成任务续接包",
            project_id=project_id,
            model=model,
            summary=f"{destination} · {len(unique_evidence)} 条证据",
            metadata={
                "destination": destination,
                "source_day": parsed_day,
                "character_count": len(markdown),
                "estimated_tokens": max(1, len(markdown) // 2),
                "evidence_count": len(unique_evidence),
                "approved_knowledge_count": len(approved),
            },
            artifacts=[
                {
                    "kind": "context_pack",
                    "name": filename,
                    "mime": "text/markdown;charset=utf-8",
                    "size": len(markdown.encode("utf-8")),
                    "content_hash": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                    "metadata": {"destination": destination, "source_day": parsed_day},
                }
            ],
        )
        return {
            "ok": True,
            "filename": filename,
            "markdown": markdown,
            "json": context_json,
            "sections": sections,
            "evidence": unique_evidence,
            "character_count": len(markdown),
            "estimated_tokens": max(1, len(markdown) // 2),
            "generated_at": generated_at,
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
        self.record_activity(
            "conversation_note",
            "更新对话管理信息",
            source=source,
            conversation_id=conversation_id,
            summary="备注、标签或状态已更新",
            metadata={
                "has_note": bool(note),
                "tag_count": len(tags),
                "status": user_status,
                "favorite": bool(favorite),
            },
        )
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
        self._sync_projects()
        self._sync_conversation_relations()
        return {"ok": True, "inserted": inserted, "updated": updated, "preview": preview}

    @staticmethod
    def _version_tuple(value: str) -> tuple[int, ...]:
        numbers = re.findall(r"\d+", value)
        return tuple(int(number) for number in numbers[:4]) or (0,)

    @staticmethod
    def _update_url(value: Any) -> str:
        url = clean_text(value, 1200)
        parsed = urllib.parse.urlsplit(url)
        if not url or parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("更新地址必须是 HTTPS")
        return url

    def update_config(self) -> dict[str, Any]:
        settings = read_app_settings()
        return {
            "current_version": APP_VERSION,
            "manifest_url": settings.get("update_manifest_url", ""),
            "auto_check": settings.get("update_auto_check", "0") == "1",
        }

    def save_update_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = clean_text(payload.get("manifest_url"), 1200)
        if url:
            self._update_url(url)
        now = time.time()
        values = {
            "update_manifest_url": url,
            "update_auto_check": "1" if payload.get("auto_check") else "0",
        }
        with notes_db() as conn:
            for key, value in values.items():
                conn.execute(
                    """
                    INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
                    """,
                    (key, value, now),
                )
            conn.commit()
        return {"ok": True, **self.update_config()}

    def check_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = self._update_url(payload.get("manifest_url") or self.update_config()["manifest_url"])
        request = urllib.request.Request(url, headers={"User-Agent": f"AIConversationHub/{APP_VERSION}"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read(1_000_001)
        except (OSError, urllib.error.URLError) as exc:
            raise ValueError(f"检查更新失败：{exc}") from exc
        if len(raw) > 1_000_000:
            raise ValueError("更新清单超过 1 MB")
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("更新清单不是有效 JSON") from exc
        if not isinstance(manifest, dict):
            raise ValueError("更新清单格式无效")
        version = clean_text(manifest.get("version"), 40)
        architecture = platform.machine().casefold()
        platform_key = (
            f"macos-{'arm64' if architecture in {'arm64', 'aarch64'} else 'x86_64'}"
            if sys.platform == "darwin"
            else (
                f"windows-{'arm64' if architecture in {'arm64', 'aarch64'} else 'x86_64'}"
                if os.name == "nt"
                else f"linux-{architecture or 'unknown'}"
            )
        )
        assets = manifest.get("assets")
        selected = assets.get(platform_key) if isinstance(assets, dict) else None
        if selected is None and os.name == "nt":
            selected = {"url": manifest.get("url"), "sha256": manifest.get("sha256")}
        if not isinstance(selected, dict):
            raise ValueError(f"更新清单没有适用于 {platform_key} 的安装包")
        download_url = self._update_url(selected.get("url"))
        sha256 = clean_text(selected.get("sha256"), 80).casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("更新清单缺少有效 SHA-256")
        return {
            "current_version": APP_VERSION,
            "version": version,
            "available": self._version_tuple(version) > self._version_tuple(APP_VERSION),
            "url": download_url,
            "sha256": sha256,
            "notes": clean_text(manifest.get("notes"), 2000),
            "signature": clean_text(manifest.get("signature"), 4000),
            "platform": platform_key,
        }

    def download_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = self._update_url(payload.get("url"))
        expected = clean_text(payload.get("sha256"), 80).casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("缺少有效 SHA-256")
        target_dir = DATA_DIR / "Updates"
        target_dir.mkdir(parents=True, exist_ok=True)
        fallback_name = (
            "AIConversationHub-update.dmg"
            if sys.platform == "darwin"
            else "AIConversationHub-update.exe"
        )
        filename = safe_filename(Path(urllib.parse.urlsplit(url).path).name, fallback_name)
        target = target_dir / filename
        request = urllib.request.Request(url, headers={"User-Agent": f"AIConversationHub/{APP_VERSION}"})
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(request, timeout=90) as response, target.open("wb") as handle:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    handle.write(chunk)
        except (OSError, urllib.error.URLError) as exc:
            target.unlink(missing_ok=True)
            raise ValueError(f"下载更新失败：{exc}") from exc
        actual = digest.hexdigest()
        if actual != expected:
            target.unlink(missing_ok=True)
            raise ValueError("更新包 SHA-256 校验失败，文件已删除")
        return {
            "ok": True,
            "path": str(target),
            "sha256": actual,
            "executed": False,
            "message": "更新包已校验并保存；不会自动执行。",
        }


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
        # 重计算阶段延后：服务重启后前 15 秒内点开项目走原冷计算（约 3-4 秒），
        # 避免与预热撞车导致等待翻倍；15 秒后详情缓存就绪，点开即毫秒级。
        WARMUP_PHASE_DELAY = 15.0
        time.sleep(WARMUP_PHASE_DELAY)
        parts["idle_before_projects"] = WARMUP_PHASE_DELAY

        def warm_projects() -> None:
            payload = INDEX.projects()
            project_rows = payload.get("projects") or []
            for position, row in enumerate(project_rows):
                project_id = str(row.get("id") or "")
                if project_id:
                    INDEX.project_detail(project_id)
                # 项目详情是重计算，逐个之间让出 GIL，给用户请求插队机会。
                if position < len(project_rows) - 1:
                    time.sleep(0.25)

        run("projects", warm_projects)
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
            path in {"/api/summary", "/api/sources", "/api/projects", "/api/daily", "/api/conversations"}
            or path.startswith("/api/conversation/")
            or path.startswith("/api/conversation-messages/")
            or path.startswith("/api/project/")
        ):
            INDEX.maybe_refresh()
        if path == "/api/token":
            self._json({"token": CSRF_TOKEN})
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
        if path == "/api/update":
            self._json(INDEX.update_config())
            return
        if path == "/api/summary-config":
            self._json(INDEX.summary_config())
            return
        if path == "/api/obsidian-config":
            self._json(INDEX.obsidian_config((params.get("project_id") or [""])[0]))
            return
        if path == "/api/projects":
            self._json(INDEX.projects())
            return
        if path == "/api/skills":
            self._json(
                INDEX.skills_catalog(
                    query=(params.get("q") or [""])[0],
                    agent=(params.get("agent") or ["all"])[0],
                    capability=(params.get("capability") or ["all"])[0],
                    status=(params.get("status") or ["all"])[0],
                    favorites=(params.get("favorites") or ["0"])[0] == "1",
                )
            )
            return
        if path.startswith("/api/skill/"):
            instance_id = urllib.parse.unquote(path.removeprefix("/api/skill/"))
            try:
                self._json(INDEX.skill_detail(instance_id))
            except ValueError as exc:
                self._json({"error": str(exc)}, 404)
            return
        if path == "/api/project/classification":
            try:
                self._json(
                    INDEX.classification_inbox(
                        mode=(params.get("mode") or ["unassigned"])[0],
                        project_id=(params.get("project_id") or [""])[0],
                        limit=min(500, max(1, int((params.get("limit") or ["300"])[0]))),
                    )
                )
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
            return
        if path == "/api/knowledge":
            try:
                self._json(
                    INDEX.knowledge_items(
                        status=(params.get("status") or ["pending"])[0],
                        project_id=(params.get("project_id") or [""])[0],
                        limit=min(500, max(1, int((params.get("limit") or ["200"])[0]))),
                    )
                )
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
            return
        if path == "/api/knowledge/history":
            try:
                self._json(INDEX.knowledge_history((params.get("id") or [""])[0]))
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
            return
        if path == "/api/activity":
            try:
                self._json(
                    INDEX.activity_feed(
                        project_id=(params.get("project_id") or [""])[0],
                        kind=(params.get("kind") or [""])[0],
                        limit=min(300, max(1, int((params.get("limit") or ["120"])[0]))),
                    )
                )
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
            return
        if path == "/api/project/files":
            try:
                self._json(
                    INDEX.project_files(
                        project_id=(params.get("project_id") or [""])[0],
                        limit=min(300, max(1, int((params.get("limit") or ["200"])[0]))),
                    )
                )
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
            return
        if path.startswith("/api/project/"):
            project_id = urllib.parse.unquote(path.removeprefix("/api/project/"))
            result = INDEX.project_detail(project_id)
            self._json(result if result else {"error": "Project not found"}, 200 if result else 404)
            return
        if path == "/api/conversation-summaries":
            self._json(INDEX.conversation_summaries_list())
            return
        if path.startswith("/api/conversation-summary/"):
            summary_id = urllib.parse.unquote(path.removeprefix("/api/conversation-summary/"))
            result = INDEX.conversation_summary_detail(summary_id)
            self._json(result if result else {"error": "Summary not found"}, 200 if result else 404)
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
        failure_kinds = {
            "/api/daily/generate": "daily_summary",
            "/api/conversation-summary/generate": "conversation_summary",
            "/api/project/daily/generate": "project_summary",
            "/api/project/plan/generate": "project_plan",
            "/api/project/plan/save": "project_plan",
            "/api/project/assign": "project_assign",
            "/api/project/assign-batch": "project_assign",
            "/api/project/merge": "project_merge",
            "/api/project/rule/preview": "project_rule",
            "/api/project/rule": "project_rule",
            "/api/skill/manage": "skill_manage",
            "/api/skill/projects": "skill_manage",
            "/api/skill/reveal": "skill_manage",
            "/api/project/rule/suggestions": "project_rule",
            "/api/backup/preview": "backup",
            "/api/backup/restore": "backup",
            "/api/update/check": "update",
            "/api/update/download": "update",
            "/api/knowledge/generate": "knowledge_extract",
            "/api/knowledge/review": "knowledge_review",
            "/api/knowledge/evidence/verify": "evidence_verify",
            "/api/knowledge/relation": "knowledge_conflict",
            "/api/obsidian-config": "obsidian_config",
            "/api/obsidian/export": "obsidian_export",
            "/api/export": "export",
            "/api/context-pack": "context_pack",
            "/api/project/root/confirm": "project_root",
            "/api/project/root/add": "project_root",
            "/api/project/files/refresh": "file_scan",
            "/api/project/file/pin": "artifact_pin",
            "/api/project/file/reveal": "file_reveal",
        }
        try:
            if path == "/api/note":
                self._json(INDEX.save_note(payload))
                return
            if path == "/api/daily/generate":
                self._json(INDEX.generate_daily_summary(payload))
                return
            if path == "/api/daily/note":
                self._json(INDEX.save_daily_note(payload))
                return
            if path == "/api/conversation-summary/generate":
                self._json(INDEX.generate_conversation_summary(payload))
                return
            if path == "/api/conversation-summary/archive":
                self._json(INDEX.archive_conversation_summary(payload))
                return
            if path == "/api/summary-config":
                self._json(INDEX.save_summary_config(payload))
                return
            if path == "/api/summary-config/test":
                self._json(INDEX.test_summary_config(payload))
                return
            if path == "/api/summary-config/models":
                self._json(INDEX.discover_summary_models(payload))
                return
            if path == "/api/projects/refresh":
                self._json(INDEX.refresh_projects())
                return
            if path == "/api/project":
                self._json(INDEX.save_project(payload))
                return
            if path == "/api/project/assign":
                self._json(INDEX.assign_project(payload))
                return
            if path == "/api/project/assign-batch":
                self._json(INDEX.assign_projects_batch(payload))
                return
            if path == "/api/project/merge":
                self._json(INDEX.merge_projects(payload))
                return
            if path == "/api/project/rule/preview":
                self._json(INDEX.preview_project_rule(payload))
                return
            if path == "/api/project/rule":
                self._json(INDEX.save_project_rule(payload))
                return
            if path == "/api/skill/manage":
                self._json(INDEX.save_skill_management(payload))
                return
            if path == "/api/skill/projects":
                self._json(INDEX.save_skill_projects(payload))
                return
            if path == "/api/skill/reveal":
                self._json(INDEX.reveal_skill(payload))
                return
            if path == "/api/project/rule/suggestions":
                self._json(INDEX.project_rule_suggestions(clean_text(payload.get("project_id"), 160)))
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
            if path == "/api/update":
                self._json(INDEX.save_update_config(payload))
                return
            if path == "/api/update/check":
                self._json(INDEX.check_update(payload))
                return
            if path == "/api/update/download":
                self._json(INDEX.download_update(payload))
                return
            if path == "/api/sources/diagnose":
                INDEX.refresh()
                self._json({"ok": True, **INDEX.source_health()})
                return
            if path == "/api/sources/enabled":
                self._json(set_source_enabled(payload))
                return
            if path == "/api/project/daily/generate":
                self._json(INDEX.generate_project_daily_summary(payload))
                return
            if path == "/api/project/plan/generate":
                self._json(INDEX.generate_project_plan(payload))
                return
            if path == "/api/project/plan/save":
                self._json(INDEX.save_project_plan(payload))
                return
            if path == "/api/knowledge/generate":
                self._json(INDEX.generate_knowledge_candidates(payload))
                return
            if path == "/api/knowledge/review":
                self._json(INDEX.review_knowledge(payload))
                return
            if path == "/api/knowledge/evidence/verify":
                self._json(INDEX.verify_knowledge_evidence(payload))
                return
            if path == "/api/knowledge/relation":
                self._json(INDEX.resolve_knowledge_relation(payload))
                return
            if path == "/api/obsidian-config":
                self._json(INDEX.save_obsidian_config(payload))
                return
            if path == "/api/obsidian/export":
                self._json(INDEX.export_knowledge_to_obsidian(payload))
                return
            if path == "/api/export":
                self._json(INDEX.export_bundle(payload))
                return
            if path == "/api/context-pack":
                self._json(INDEX.context_pack(payload))
                return
            if path == "/api/project/root/confirm":
                self._json(INDEX.confirm_project_root(payload))
                return
            if path == "/api/project/root/add":
                self._json(INDEX.add_project_root(payload))
                return
            if path == "/api/project/files/refresh":
                self._json(INDEX.refresh_project_files(payload))
                return
            if path == "/api/project/file/pin":
                self._json(INDEX.pin_project_file(payload))
                return
            if path == "/api/project/file/reveal":
                self._json(INDEX.reveal_project_file(payload))
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
            kind = failure_kinds.get(path)
            if kind:
                INDEX.record_activity(
                    kind,
                    "操作冲突",
                    status="failed",
                    project_id=clean_text(payload.get("project_id"), 120),
                    error=str(exc),
                    metadata={"route": path, "outcome": "conflict"},
                )
            self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
            return
        except (ValueError, sqlite3.DatabaseError) as exc:
            kind = failure_kinds.get(path)
            if kind:
                INDEX.record_activity(
                    kind,
                    "操作失败",
                    status="failed",
                    project_id=clean_text(
                        payload.get("project_id") or payload.get("target_project_id"), 120
                    ),
                    error=str(exc),
                    metadata={"route": path, "outcome": "failure"},
                )
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
