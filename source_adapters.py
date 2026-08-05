from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator


EXTRA_SOURCES = ("qoderwork",)
CUSTOM_SOURCE_PREFIX = "custom_"
CUSTOM_FORMATS = {"jsonl", "markdown", "sqlite"}
SOURCE_LABELS = {
    "qoderwork": "QoderWork",
}
SKIP_DISCOVERY_DIRS = {
    ".git", ".svn", "__pycache__", "node_modules", ".venv", "venv", "cache",
    "caches", "backup", "backups", "temp", "tmp", "$recycle.bin",
}
SECRET_PATTERNS = (
    (r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", "Bearer [REDACTED]"),
    (r"\b(?:sk|ghp|github_pat|xox[baprs])[-_A-Za-z0-9]{12,}", "[REDACTED_TOKEN]"),
    (
        r"(?i)\b(api[_ -]?key|access[_ -]?token|secret|password)\b"
        r"(\s*[:=]\s*)[^\s,;\"']{6,}",
        r"\1\2[REDACTED]",
    ),
)
MARKDOWN_ROLE_MARKER = re.compile(
    r"(?im)^(?:#{1,6}\s*)?(user|assistant|human|ai|用户|助手)\s*[:：]?\s*$"
)


@contextmanager
def readonly_db(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        yield conn
    finally:
        conn.close()


def sqlite_tables(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        with readonly_db(path) as conn:
            return {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
    except (OSError, sqlite3.DatabaseError):
        return set()


def redact(value: Any, limit: int = 20000) -> str:
    text = str(value or "").replace("\x00", "").strip()
    for pattern, replacement in SECRET_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text[:limit].rstrip()


def epoch(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        while number > 10_000_000_000:
            number /= 1000
        return number
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        try:
            return epoch(float(text))
        except ValueError:
            return 0.0


def basename(value: Any) -> str:
    text = str(value or "").rstrip("\\/")
    return text.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] or "无工作区"


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
                continue
            if not isinstance(block, dict):
                continue
            kind = str(block.get("type") or "").casefold()
            if kind in {
                "thinking", "reasoning", "tool_use", "tool_result", "function",
                "function_call", "computer_initialize_state", "server_tool_use",
            }:
                continue
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        return redact("\n".join(parts))
    if isinstance(value, dict):
        for key in ("text", "content", "value"):
            if key in value:
                return content_text(value[key])
    return ""


def json_value(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def conversation(
    source: str,
    conversation_id: Any,
    title: Any,
    messages: list[dict[str, Any]],
    *,
    cwd: Any = "",
    created_at: Any = 0,
    updated_at: Any = 0,
    model: Any = "",
    archived: bool = False,
    status: str = "active",
    source_kind: str = "",
    rollout_path: Any = "",
) -> dict[str, Any]:
    user_messages = [item for item in messages if item["role"] == "user" and item["text"]]
    assistant_messages = [
        item for item in messages if item["role"] == "assistant" and item["text"]
    ]
    first_user = user_messages[0]["text"] if user_messages else ""
    safe_title = (
        redact(title, 240)
        or redact(first_user, 120)
        or f"{SOURCE_LABELS.get(source, '自定义 Agent')} 对话"
    )
    created = epoch(created_at) or min(
        (float(item["timestamp"]) for item in messages if item["timestamp"]),
        default=0,
    )
    updated = epoch(updated_at) or max(
        (float(item["timestamp"]) for item in messages if item["timestamp"]),
        default=created,
    )
    return {
        "source": source,
        "id": str(conversation_id),
        "title": safe_title,
        "preview": redact(first_user, 900) or safe_title,
        "cwd": str(cwd or ""),
        "workspace": basename(cwd),
        "created_at": created or updated,
        "updated_at": updated or created,
        "message_count": len(user_messages) + len(assistant_messages),
        "tool_call_count": 0,
        "model": redact(model, 120),
        "archived": bool(archived),
        "status": status,
        "source_kind": source_kind,
        "rollout_path": str(rollout_path or ""),
        "parent_id": "",
    }


def default_candidates(source: str) -> list[Path]:
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA") or (home / "AppData" / "Roaming"))
    application_support = (
        home / "Library" / "Application Support"
        if sys.platform == "darwin"
        else appdata
    )
    if source == "qoderwork":
        return [
            application_support / "QoderWork CN" / "data" / "agents.db",
            application_support / "QoderWork" / "data" / "agents.db",
        ]
    return []


def validate_source(source: str, path: Path) -> tuple[bool, str]:
    try:
        if source == "qoderwork":
            valid = {"projects", "chats", "sub_chats", "messages"}.issubset(sqlite_tables(path))
            return valid, "QoderWork 会话数据库" if valid else "数据库结构不匹配"
    except (OSError, sqlite3.DatabaseError):
        return False, "读取失败"
    return False, "未知来源"


def estimate_conversations(source: str, path: Path | None) -> int:
    if not path:
        return 0
    try:
        if source == "qoderwork":
            with readonly_db(path) as conn:
                return int(
                    conn.execute(
                        """
                        SELECT count(*) FROM chats
                        WHERE deleted_at IS NULL AND coalesce(chat_type,'task')='task'
                        """
                    ).fetchone()[0]
                )
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError):
        return 0
    return 0


def configured_extra_sources(
    config: dict[str, Any],
    *,
    with_counts: bool = True,
) -> dict[str, dict[str, Any]]:
    raw = config.get("extra_sources")
    raw = raw if isinstance(raw, dict) else {}
    result: dict[str, dict[str, Any]] = {}
    for source in EXTRA_SOURCES:
        value = raw.get(source)
        value = value if isinstance(value, dict) else {}
        path = Path(str(value.get("path") or "")).expanduser() if value.get("path") else None
        if not path:
            path = next((item for item in default_candidates(source) if validate_source(source, item)[0]), None)
        valid, detail = validate_source(source, path) if path else (False, "未发现")
        result[source] = {
            "enabled": bool(value.get("enabled", False)),
            "path": str(path or ""),
            "valid": valid,
            "detected": bool(path),
            "detail": detail,
            "label": SOURCE_LABELS[source],
            "conversations": estimate_conversations(source, path) if valid and with_counts else 0,
        }
    return result


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({quote_identifier(table)})")]


def quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _first_column(columns: Iterable[str], candidates: Iterable[str]) -> str:
    lookup = {str(column).casefold(): str(column) for column in columns}
    return next((lookup[name.casefold()] for name in candidates if name.casefold() in lookup), "")


def detect_custom_sqlite(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        with readonly_db(path) as conn:
            tables = [
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            message_tables = sorted(
                tables,
                key=lambda name: (
                    name.casefold() not in {"messages", "chat_messages", "conversation_messages"},
                    name.casefold(),
                ),
            )
            for message_table in message_tables:
                message_columns = _sqlite_columns(conn, message_table)
                role = _first_column(
                    message_columns,
                    ("role", "sender_role", "author_role", "message_role", "author"),
                )
                content = _first_column(
                    message_columns,
                    ("content", "text", "body", "message", "searchable_text"),
                )
                conversation_id = _first_column(
                    message_columns,
                    (
                        "conversation_id", "session_id", "thread_id", "chat_id",
                        "conversationId", "sessionId", "threadId", "chatId",
                    ),
                )
                if not (role and content and conversation_id):
                    continue
                timestamp = _first_column(
                    message_columns,
                    ("created_at", "timestamp", "time", "updated_at", "createdAt"),
                )
                conversation_table = ""
                conversation_key = ""
                for table in sorted(
                    tables,
                    key=lambda name: (
                        name.casefold() not in {
                            "conversations", "sessions", "threads", "chats", "chat_sessions",
                        },
                        name.casefold(),
                    ),
                ):
                    if table == message_table:
                        continue
                    columns = _sqlite_columns(conn, table)
                    key = _first_column(
                        columns,
                        (
                            conversation_id, "id", "conversation_id", "session_id",
                            "thread_id", "chat_id", "uuid",
                        ),
                    )
                    if key:
                        conversation_table = table
                        conversation_key = key
                        break
                return {
                    "message_table": message_table,
                    "message_conversation_id": conversation_id,
                    "role": role,
                    "content": content,
                    "message_timestamp": timestamp,
                    "conversation_table": conversation_table,
                    "conversation_key": conversation_key,
                }
    except (OSError, sqlite3.DatabaseError):
        return {}
    return {}


def _custom_files(path: Path, pattern: str) -> list[Path]:
    if path.is_file():
        return [path] if path.match(pattern) else []
    if not path.is_dir():
        return []
    result: list[Path] = []
    for candidate in path.rglob(pattern):
        if not candidate.is_file():
            continue
        try:
            relative_parts = candidate.relative_to(path).parts[:-1]
        except ValueError:
            relative_parts = ()
        if any(part.casefold() in SKIP_DISCOVERY_DIRS for part in relative_parts):
            continue
        result.append(candidate)
    return result


def validate_custom_source(config: dict[str, Any], path: Path) -> tuple[bool, str]:
    format_name = str(config.get("format") or "").casefold()
    if format_name not in CUSTOM_FORMATS:
        return False, "请选择 JSONL、Markdown 或 SQLite"
    try:
        if format_name == "jsonl":
            count = len(_custom_files(path, "*.jsonl"))
            return (count > 0, f"{count} 个 JSONL 文件" if count else "未找到 JSONL 文件")
        if format_name == "markdown":
            count = 0
            for file_path in _custom_files(path, "*.md"):
                try:
                    text = file_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if MARKDOWN_ROLE_MARKER.search(text):
                    count += 1
            return (
                count > 0,
                (
                    f"{count} 个带用户/助手角色的 Markdown 对话"
                    if count
                    else "未找到带 User/Assistant 或 用户/助手标题的 Markdown 对话"
                ),
            )
        mapping = detect_custom_sqlite(path)
        return (
            bool(mapping),
            (
                f"SQLite：{mapping.get('conversation_table') or '无元数据表'}"
                f" + {mapping.get('message_table')}"
                if mapping
                else "未识别到会话ID、角色和正文字段"
            ),
        )
    except OSError:
        return False, "读取失败"


def estimate_custom_conversations(config: dict[str, Any], path: Path) -> int:
    format_name = str(config.get("format") or "").casefold()
    try:
        if format_name == "markdown":
            count = 0
            for file_path in _custom_files(path, "*.md"):
                try:
                    if MARKDOWN_ROLE_MARKER.search(
                        file_path.read_text(encoding="utf-8", errors="ignore")
                    ):
                        count += 1
                except OSError:
                    continue
            return count
        if format_name == "jsonl":
            session_ids: set[str] = set()
            for file_path in _custom_files(path, "*.jsonl"):
                with file_path.open("r", encoding="utf-8", errors="ignore") as stream:
                    for line in stream:
                        try:
                            event = json.loads(line)
                        except (ValueError, json.JSONDecodeError):
                            continue
                        if not isinstance(event, dict):
                            continue
                        if any(
                            bool(event.get(key))
                            for key in ("isSidechain", "is_subagent", "isSubagent", "is_background")
                        ):
                            continue
                        event_type = str(event.get("type") or "").casefold()
                        if event_type in {
                            "system", "developer", "reasoning", "thinking", "tool", "tool_call",
                            "tool_result", "function_call", "function_call_result", "snapshot",
                        }:
                            continue
                        message = event.get("message") if isinstance(event.get("message"), dict) else {}
                        role = normalize_role(
                            message.get("role") or event.get("role") or event_type
                        )
                        if not role:
                            continue
                        session_ids.add(
                            str(
                                event.get("sessionId")
                                or event.get("session_id")
                                or event.get("conversationId")
                                or event.get("conversation_id")
                                or event.get("thread_id")
                                or event.get("chat_id")
                                or file_path.stem
                            )
                        )
            return len(session_ids)
        mapping = detect_custom_sqlite(path)
        if not mapping:
            return 0
        with readonly_db(path) as conn:
            column = quote_identifier(mapping["message_conversation_id"])
            table = quote_identifier(mapping["message_table"])
            return int(conn.execute(f"SELECT count(DISTINCT {column}) FROM {table}").fetchone()[0])
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError):
        return 0


def configured_custom_sources(
    config: dict[str, Any],
    *,
    with_counts: bool = True,
) -> dict[str, dict[str, Any]]:
    raw = config.get("custom_sources")
    raw = raw if isinstance(raw, list) else []
    result: dict[str, dict[str, Any]] = {}
    reserved = set(EXTRA_SOURCES) | {"all", "hermes", "codex", "workbuddy"}
    for value in raw[:50]:
        if not isinstance(value, dict):
            continue
        source = str(value.get("id") or "").casefold()
        if (
            not re.fullmatch(r"custom_[a-z0-9_]{1,48}", source)
            or source in reserved
            or source in result
        ):
            continue
        label = redact(value.get("label"), 80)
        format_name = str(value.get("format") or "").casefold()
        path = Path(str(value.get("path") or "")).expanduser()
        normalized = {
            "id": source,
            "label": label or "自定义 Agent",
            "format": format_name,
            "path": str(path) if str(value.get("path") or "") else "",
            "enabled": bool(value.get("enabled", False)),
        }
        valid, detail = (
            validate_custom_source(normalized, path)
            if normalized["path"]
            else (False, "尚未配置路径")
        )
        result[source] = {
            **normalized,
            "valid": valid,
            "detected": bool(normalized["path"]),
            "detail": detail,
            "conversations": (
                estimate_custom_conversations(normalized, path)
                if valid and with_counts
                else 0
            ),
        }
    return result


def _candidate_filenames(source: str) -> tuple[str, ...]:
    return {
        "qoderwork": ("agents.db",),
    }.get(source, ())


def discover_in_roots(source: str, roots: Iterable[Path]) -> Path | None:
    filenames = {value.casefold() for value in _candidate_filenames(source)}
    for root in roots:
        if not root.is_dir():
            continue
        for current, dirs, files in os.walk(root):
            dirs[:] = [
                name for name in dirs
                if name.casefold() not in SKIP_DISCOVERY_DIRS and not name.startswith("$")
            ]
            for filename in files:
                if filename.casefold() not in filenames:
                    continue
                candidate = Path(current) / filename
                if validate_source(source, candidate)[0]:
                    return candidate.resolve()
    return None


def discover_extra_sources(
    config: dict[str, Any],
    extra_roots: Iterable[str] = (),
) -> dict[str, dict[str, Any]]:
    selected = configured_extra_sources(config)
    roots = [Path(value).expanduser() for value in extra_roots if value and Path(value).expanduser().is_dir()]
    unresolved = {source for source in EXTRA_SOURCES if not selected[source]["valid"]}
    filename_sources: dict[str, set[str]] = {}
    for source in unresolved:
        for filename in _candidate_filenames(source):
            filename_sources.setdefault(filename.casefold(), set()).add(source)
    for root in roots:
        if not unresolved:
            break
        for current, dirs, files in os.walk(root):
            dirs[:] = [
                name for name in dirs
                if name.casefold() not in SKIP_DISCOVERY_DIRS and not name.startswith("$")
            ]
            for filename in files:
                possible = filename_sources.get(filename.casefold(), set()) & unresolved
                if not possible:
                    continue
                file_path = Path(current) / filename
                for source in tuple(possible):
                    candidates: list[Path] = [file_path]
                    candidate = next(
                        (value for value in candidates if validate_source(source, value)[0]),
                        None,
                    )
                    if not candidate:
                        continue
                    valid, detail = validate_source(source, candidate)
                    selected[source].update(
                        {
                            "path": str(candidate.resolve()),
                            "valid": valid,
                            "detected": True,
                            "detail": detail,
                            "conversations": estimate_conversations(source, candidate),
                        }
                    )
                    unresolved.discard(source)
            if not unresolved:
                break
    return selected


def _load_qoderwork(path: Path) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    items: list[dict[str, Any]] = []
    messages_by_id: dict[str, list[dict[str, Any]]] = {}
    with readonly_db(path) as conn:
        rows = conn.execute(
            """
            SELECT c.*,p.name AS project_name,p.path AS project_path
            FROM chats c JOIN projects p ON p.id=c.project_id
            WHERE c.deleted_at IS NULL AND coalesce(c.chat_type,'task')='task'
            ORDER BY c.updated_at DESC
            """
        )
        for row in rows:
            message_rows = conn.execute(
                """
                SELECT role,searchable_text,parts,created_at
                FROM messages
                WHERE chat_id=? AND role IN ('user','assistant')
                ORDER BY sequence,created_at
                """,
                (row["id"],),
            )
            messages: list[dict[str, Any]] = []
            for message in message_rows:
                text = redact(message["searchable_text"])
                if not text:
                    parts = json_value(message["parts"], [])
                    text = content_text(parts)
                if text:
                    messages.append(
                        {
                            "role": str(message["role"]),
                            "text": text,
                            "timestamp": epoch(message["created_at"]),
                        }
                    )
            if not any(message["role"] == "user" for message in messages):
                continue
            session_id = str(row["id"])
            messages_by_id[session_id] = messages
            cwd = row["worktree_path"] or row["project_path"]
            items.append(
                conversation(
                    "qoderwork", session_id, row["name"], messages, cwd=cwd,
                    created_at=row["created_at"], updated_at=row["updated_at"],
                    source_kind=str(row["source"] or "qoderwork-sqlite"),
                    rollout_path=path,
                )
            )
    return items, messages_by_id


def normalize_role(value: Any) -> str:
    role = str(value or "").casefold().strip()
    if role in {"user", "human", "用户", "person"}:
        return "user"
    if role in {"assistant", "ai", "bot", "助手", "model"}:
        return "assistant"
    return ""


def custom_content(value: Any) -> str:
    if isinstance(value, str) and value.lstrip().startswith(("[", "{")):
        parsed = json_value(value, value)
        if parsed is not value:
            return content_text(parsed)
    return content_text(value)


def _load_custom_jsonl(
    source: str,
    config: dict[str, Any],
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    root = path if path.is_dir() else path.parent
    for file_path in _custom_files(path, "*.jsonl"):
        fallback_id = (
            file_path.relative_to(root).with_suffix("").as_posix()
            if path.is_dir()
            else file_path.stem
        )
        try:
            with file_path.open("r", encoding="utf-8", errors="ignore") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if not isinstance(event, dict) or any(
                        bool(event.get(key))
                        for key in ("isSidechain", "is_subagent", "isSubagent", "is_background")
                    ):
                        continue
                    event_type = str(event.get("type") or "").casefold()
                    if event_type in {
                        "system", "developer", "reasoning", "thinking", "tool", "tool_call",
                        "tool_result", "function_call", "function_call_result", "snapshot",
                    }:
                        continue
                    message = event.get("message") if isinstance(event.get("message"), dict) else {}
                    role = normalize_role(message.get("role") or event.get("role") or event_type)
                    if not role:
                        continue
                    text = custom_content(
                        message.get("content")
                        if "content" in message
                        else event.get("content", event.get("text"))
                    )
                    if not text:
                        continue
                    session_id = str(
                        event.get("sessionId")
                        or event.get("session_id")
                        or event.get("conversationId")
                        or event.get("conversation_id")
                        or event.get("threadId")
                        or event.get("thread_id")
                        or event.get("chatId")
                        or event.get("chat_id")
                        or fallback_id
                    )
                    timestamp = epoch(
                        event.get("timestamp")
                        or event.get("created_at")
                        or event.get("createdAt")
                        or message.get("timestamp")
                    )
                    grouped.setdefault(session_id, []).append(
                        {"role": role, "text": text, "timestamp": timestamp}
                    )
                    info = metadata.setdefault(
                        session_id,
                        {"path": file_path, "title": "", "cwd": "", "model": ""},
                    )
                    info["title"] = (
                        event.get("title")
                        or event.get("aiTitle")
                        or message.get("title")
                        or info["title"]
                    )
                    info["cwd"] = (
                        event.get("cwd")
                        or event.get("workspace")
                        or event.get("project_path")
                        or info["cwd"]
                    )
                    info["model"] = message.get("model") or event.get("model") or info["model"]
        except OSError:
            continue
    items: list[dict[str, Any]] = []
    messages_by_id: dict[str, list[dict[str, Any]]] = {}
    for session_id, messages in grouped.items():
        messages.sort(key=lambda item: float(item["timestamp"] or 0))
        if not any(message["role"] == "user" for message in messages):
            continue
        info = metadata[session_id]
        messages_by_id[session_id] = messages
        items.append(
            conversation(
                source,
                session_id,
                info["title"],
                messages,
                cwd=info["cwd"],
                model=info["model"],
                source_kind="custom-jsonl",
                rollout_path=info["path"],
            )
        )
    return items, messages_by_id


def _markdown_messages(text: str, timestamp: float) -> tuple[str, list[dict[str, Any]]]:
    body = text.replace("\x00", "")
    if body.startswith("---"):
        parts = body.split("---", 2)
        if len(parts) == 3:
            body = parts[2].lstrip()
    title_match = re.search(r"(?m)^#\s+(.+?)\s*$", body)
    title = redact(title_match.group(1), 240) if title_match else ""
    matches = list(MARKDOWN_ROLE_MARKER.finditer(body))
    messages: list[dict[str, Any]] = []
    if not matches:
        return title, messages
    for index, match in enumerate(matches):
        role = normalize_role(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        value = redact(body[start:end])
        if role and value:
            messages.append(
                {"role": role, "text": value, "timestamp": timestamp + index / 1000}
            )
    return title, messages


def _load_custom_markdown(
    source: str,
    config: dict[str, Any],
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    items: list[dict[str, Any]] = []
    messages_by_id: dict[str, list[dict[str, Any]]] = {}
    root = path if path.is_dir() else path.parent
    for file_path in _custom_files(path, "*.md"):
        try:
            timestamp = file_path.stat().st_mtime
            title, messages = _markdown_messages(
                file_path.read_text(encoding="utf-8", errors="ignore"),
                timestamp,
            )
        except OSError:
            continue
        if not any(message["role"] == "user" for message in messages):
            continue
        session_id = (
            file_path.relative_to(root).with_suffix("").as_posix()
            if path.is_dir()
            else file_path.stem
        )
        messages_by_id[session_id] = messages
        items.append(
            conversation(
                source,
                session_id,
                title or file_path.stem,
                messages,
                cwd=file_path.parent,
                created_at=timestamp,
                updated_at=timestamp,
                source_kind="custom-markdown",
                rollout_path=file_path,
            )
        )
    return items, messages_by_id


def _row_value(row: dict[str, Any], aliases: Iterable[str]) -> Any:
    lookup = {key.casefold(): value for key, value in row.items()}
    return next((lookup[name.casefold()] for name in aliases if name.casefold() in lookup), "")


def _load_custom_sqlite(
    source: str,
    config: dict[str, Any],
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    mapping = detect_custom_sqlite(path)
    if not mapping:
        return [], {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    conversation_rows: dict[str, dict[str, Any]] = {}
    with readonly_db(path) as conn:
        if mapping["conversation_table"]:
            table = quote_identifier(mapping["conversation_table"])
            key = mapping["conversation_key"]
            for row in conn.execute(f"SELECT * FROM {table}"):
                value = dict(row)
                session_id = str(value.get(key) or "")
                if session_id:
                    conversation_rows[session_id] = value
        select = [
            f"{quote_identifier(mapping['message_conversation_id'])} AS conversation_id",
            f"{quote_identifier(mapping['role'])} AS role",
            f"{quote_identifier(mapping['content'])} AS content",
        ]
        if mapping["message_timestamp"]:
            select.append(
                f"{quote_identifier(mapping['message_timestamp'])} AS message_timestamp"
            )
        else:
            select.append("0 AS message_timestamp")
        query = f"SELECT {', '.join(select)} FROM {quote_identifier(mapping['message_table'])}"
        if mapping["message_timestamp"]:
            query += f" ORDER BY {quote_identifier(mapping['message_timestamp'])}"
        for row in conn.execute(query):
            role = normalize_role(row["role"])
            text = custom_content(row["content"])
            session_id = str(row["conversation_id"] or "")
            if role and text and session_id:
                grouped.setdefault(session_id, []).append(
                    {
                        "role": role,
                        "text": text,
                        "timestamp": epoch(row["message_timestamp"]),
                    }
                )
    items: list[dict[str, Any]] = []
    messages_by_id: dict[str, list[dict[str, Any]]] = {}
    for session_id, messages in grouped.items():
        if not any(message["role"] == "user" for message in messages):
            continue
        metadata = conversation_rows.get(session_id, {})
        if any(
            bool(_row_value(metadata, aliases))
            for aliases in (
                ("is_subagent", "isSidechain", "is_background_automation", "is_background"),
                ("parent_session_id", "parent_thread_id"),
            )
        ):
            continue
        title = _row_value(metadata, ("title", "name", "custom_title", "subject"))
        cwd = _row_value(
            metadata,
            ("cwd", "workspace", "working_directory", "project_path", "worktree_path"),
        )
        created = _row_value(metadata, ("created_at", "createdAt", "started_at"))
        updated = _row_value(
            metadata,
            ("updated_at", "updatedAt", "last_activity_at", "ended_at"),
        )
        model = _row_value(metadata, ("model", "model_id", "model_name", "provider"))
        messages_by_id[session_id] = messages
        items.append(
            conversation(
                source,
                session_id,
                title,
                messages,
                cwd=cwd,
                created_at=created,
                updated_at=updated,
                model=model,
                source_kind="custom-sqlite",
                rollout_path=path,
            )
        )
    return items, messages_by_id


def load_custom_source(
    source: str,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], str]:
    path = Path(str(config.get("path") or "")).expanduser()
    valid, detail = validate_custom_source(config, path)
    if not valid:
        return [], {}, detail
    loader = {
        "jsonl": _load_custom_jsonl,
        "markdown": _load_custom_markdown,
        "sqlite": _load_custom_sqlite,
    }.get(str(config.get("format") or "").casefold())
    if not loader:
        return [], {}, "不支持的数据格式"
    try:
        items, messages = loader(source, config, path)
        return items, messages, ""
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError) as exc:
        return [], {}, f"{type(exc).__name__}: {exc}"


LOADERS = {
    "qoderwork": _load_qoderwork,
}


def load_extra_source(
    source: str,
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], str]:
    valid, detail = validate_source(source, path)
    if not valid:
        return [], {}, detail
    try:
        items, messages = LOADERS[source](path)
        return items, messages, ""
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError) as exc:
        return [], {}, f"{type(exc).__name__}: {exc}"
