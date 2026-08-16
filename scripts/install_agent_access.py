#!/usr/bin/env python3
"""Register Conversation Hub CLI/MCP access for Grok, Codex, and local skills."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parents[1]
HUB_AGENT = HUB_ROOT / "hub_agent.py"
SKILL_SRC = HUB_ROOT / "skills" / "conversation-hub"
SKILL_FILE = SKILL_SRC / "SKILL.md"
PYTHON = Path(sys.executable)


def copy_skill(target: Path) -> None:
    shutil.copytree(
        SKILL_SRC,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    print("skill ->", target)


def upsert_grok_mcp() -> None:
    path = Path.home() / ".grok" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if "[mcp_servers.conversation-hub]" in text:
        print("grok mcp already registered")
        return
    block = (
        "\n[mcp_servers.conversation-hub]\n"
        f'command = "{PYTHON.as_posix()}"\n'
        f'args = ["{HUB_AGENT.as_posix()}", "mcp"]\n'
        "enabled = true\n"
    )
    path.write_text(text.rstrip() + "\n" + block, encoding="utf-8")
    print("grok mcp ->", path)


def upsert_codex_mcp() -> None:
    path = Path.home() / ".codex" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if "[mcp_servers.conversation-hub]" in text:
        print("codex mcp already registered")
        return
    block = (
        "\n[mcp_servers.conversation-hub]\n"
        f'command = "{PYTHON.as_posix()}"\n'
        f'args = ["{HUB_AGENT.as_posix()}", "mcp"]\n'
    )
    path.write_text(text.rstrip() + "\n" + block, encoding="utf-8")
    print("codex mcp ->", path)


def write_hermes_hint() -> None:
    path = Path.home() / ".hermes" / "conversation-hub.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Conversation Hub CLI (read-only history; repository contracts authorize work):\n"
        f'  {PYTHON} {HUB_AGENT} search "关键词" --json\n'
        f"  {PYTHON} {HUB_AGENT} show <source> <id> --json\n"
        f"  {PYTHON} {HUB_AGENT} handoff <source> <id> --json\n",
        encoding="utf-8",
    )
    print("hermes hint ->", path)


def main() -> int:
    if not HUB_AGENT.is_file() or not SKILL_FILE.is_file():
        print("hub_agent.py or SKILL.md missing", file=sys.stderr)
        return 1
    copy_skill(Path.home() / ".grok" / "skills" / "conversation-hub")
    copy_skill(Path.home() / ".agents" / "skills" / "conversation-hub")
    upsert_grok_mcp()
    upsert_codex_mcp()
    write_hermes_hint()
    print("done. restart Grok/Codex to load MCP.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
