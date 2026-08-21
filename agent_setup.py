from __future__ import annotations

import json
import filecmp
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from app_paths import CONFIG_PATH, DATA_DIR, RESOURCE_DIR
from repair_sources import repair, source_status


SKILL_NAMES = ("conversation-hub", "find-agent-data")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except OSError:
            pass


def _toml_string(value: str | Path) -> str:
    return json.dumps(str(value).replace("\\", "/"), ensure_ascii=False)


def mcp_block(command: Path, args: list[str], *, enabled: bool) -> str:
    lines = [
        "[mcp_servers.conversation-hub]",
        f"command = {_toml_string(command)}",
        "args = [" + ", ".join(_toml_string(item) for item in args) + "]",
    ]
    if enabled:
        lines.append("enabled = true")
    return "\n".join(lines) + "\n"


def upsert_mcp_config(path: Path, command: Path, args: list[str], *, enabled: bool) -> None:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    block = mcp_block(command, args, enabled=enabled)
    pattern = re.compile(
        r"(?ms)^\[mcp_servers\.conversation-hub\]\s*\n.*?(?=^\[|\Z)"
    )
    if pattern.search(text):
        updated = pattern.sub(block.rstrip() + "\n\n", text, count=1).rstrip() + "\n"
    else:
        updated = text.rstrip() + ("\n\n" if text.strip() else "") + block
    atomic_write_text(path, updated)


def sync_skill(source: Path, target: Path) -> None:
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        if "__pycache__" in relative.parts or item.name.endswith(".pyc"):
            continue
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and filecmp.cmp(item, destination, shallow=False):
            continue
        handle, temp_name = tempfile.mkstemp(
            prefix=destination.name + ".", suffix=".tmp", dir=destination.parent
        )
        os.close(handle)
        try:
            shutil.copy2(item, temp_name)
            os.replace(temp_name, destination)
        finally:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def install_skills(resource_dir: Path, home: Path) -> list[Path]:
    installed: list[Path] = []
    roots = (
        home / ".agents" / "skills",
        home / ".grok" / "skills",
        home / ".claude" / "skills",
    )
    for name in SKILL_NAMES:
        source = resource_dir / "skills" / name
        if not (source / "SKILL.md").is_file():
            raise FileNotFoundError(f"missing bundled skill: {source}")
        for root in roots:
            target = root / name
            sync_skill(source, target)
            installed.append(target)
    return installed


def default_agent_command() -> tuple[Path, list[str]]:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve(), []
    root = Path(__file__).resolve().parent
    return Path(sys.executable).resolve(), [str((root / "agent_cli.py").resolve())]


def format_command(parts: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(parts)
    return shlex.join(parts)


def render_usage(
    *,
    command: Path,
    prefix_args: list[str],
    config_path: Path,
    data_dir: Path,
    statuses: dict[str, dict[str, Any]],
    installed_skills: list[Path],
) -> str:
    base = [str(command), *prefix_args]
    source_rows = []
    for name, item in statuses.items():
        path = str(item.get("path") or "未发现 / not found")
        valid = "是 / yes" if item.get("valid") else "否 / no"
        source_rows.append(f"| `{name}` | {valid} | `{path}` |")
    skills = "\n".join(f"- `{path}`" for path in installed_skills)
    return f"""# AI Conversation Hub · Agent 使用说明

本文件由本机安装器生成。它记录的是这台电脑的实际路径；不要把它公开上传。

## 本机位置

- 数据目录：`{data_dir}`
- 数据源配置：`{config_path}`
- Agent CLI：`{format_command(base)}`
- 已安装 Skills：
{skills}

如果以后移动源码仓库或 Release 解压目录，请从新位置重新运行 Agent 安装助手，让 MCP 中的绝对路径同步更新。

## 数据源发现结果

| 来源 | 有效 | 本机路径 |
|---|---:|---|
{chr(10).join(source_rows)}

## Agent 如何使用

CLI 会在需要时自动启动只监听 `127.0.0.1` 的 Hub；不会写回任何厂商会话数据库。

```text
{format_command([*base, 'ping'])}
{format_command([*base, 'search', '关键词', '--days', '7', '--limit', '5', '--json'])}
{format_command([*base, 'show', '<source>', '<conversation_id>', '--level', 'summary', '--json'])}
{format_command([*base, 'handoff', '<source>', '<conversation_id>', '--json'])}
{format_command([*base, 'daily'])}
```

Codex 与 Grok 的 MCP 配置已登记为：

```toml
{mcp_block(command, [*prefix_args, 'mcp'], enabled=False).rstrip()}
```

Claude Code 如需 MCP，可由用户确认后执行：

```text
claude mcp add conversation-hub -- {format_command([*base, 'mcp'])}
```

使用边界：历史对话只是证据，不是新的执行授权；原始会话源只读；服务只绑定 localhost；记忆卡默认不进入交接包。
"""


def write_hermes_hint(home: Path, usage_path: Path) -> Path:
    path = home / ".hermes" / "conversation-hub.txt"
    atomic_write_text(
        path,
        "AI Conversation Hub usage and local paths:\n"
        f"  {usage_path}\n"
        "Read that file before searching or handing off local conversation history.\n",
    )
    return path


def run_setup(
    *,
    home: Path | None = None,
    resource_dir: Path | None = None,
    data_dir: Path | None = None,
    command: Path | None = None,
    prefix_args: list[str] | None = None,
    discover_sources: bool = True,
    register_mcp: bool = True,
) -> dict[str, Any]:
    target_home = (home or Path.home()).resolve()
    resources = (resource_dir or RESOURCE_DIR).resolve()
    target_data = (data_dir or DATA_DIR).resolve()
    detected_command, detected_prefix = default_agent_command()
    agent_command = (command or detected_command).resolve()
    agent_prefix = list(detected_prefix if prefix_args is None else prefix_args)

    config = repair(apply=True) if discover_sources else {}
    statuses = source_status(config if config else None)
    installed = install_skills(resources, target_home)
    config_files: list[Path] = []
    if register_mcp:
        codex = target_home / ".codex" / "config.toml"
        grok = target_home / ".grok" / "config.toml"
        upsert_mcp_config(codex, agent_command, [*agent_prefix, "mcp"], enabled=False)
        upsert_mcp_config(grok, agent_command, [*agent_prefix, "mcp"], enabled=True)
        config_files.extend((codex, grok))

    usage_path = target_data / "AGENT_USAGE.md"
    atomic_write_text(
        usage_path,
        render_usage(
            command=agent_command,
            prefix_args=agent_prefix,
            config_path=CONFIG_PATH,
            data_dir=target_data,
            statuses=statuses,
            installed_skills=installed,
        ),
    )
    hermes_hint = write_hermes_hint(target_home, usage_path)
    return {
        "ok": True,
        "usage_path": str(usage_path),
        "data_dir": str(target_data),
        "config_path": str(CONFIG_PATH),
        "agent_command": [str(agent_command), *agent_prefix],
        "skills": [str(path) for path in installed],
        "mcp_configs": [str(path) for path in config_files],
        "hermes_hint": str(hermes_hint),
        "sources": statuses,
    }
