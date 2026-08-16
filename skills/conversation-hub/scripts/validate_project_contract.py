#!/usr/bin/env python3
"""Validate the minimal repository contract used for cross-agent relay."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED_ROOT_FILES = ("PROJECT.md", "DECISIONS.md", "TASKS.md")
REQUIRED_FRONTMATTER = ("task_id", "status", "owner_role", "updated")
REQUIRED_SECTIONS = (
    "Goal",
    "Scope",
    "Do not touch",
    "Inputs",
    "Acceptance criteria",
    "Git delivery",
    "Attempts and evidence",
    "Files changed",
    "Current status",
    "Next step",
)
REQUIRED_GIT_FIELDS = (
    "repo_root",
    "base_branch",
    "start_commit",
    "work_branch",
    "commit_policy",
    "push_policy",
    "push_target",
    "integration_owner",
    "end_commit",
    "dirty_state",
)
TASK_STATUSES = {"planned", "active", "review", "blocked", "done", "cancelled"}
COMMIT_POLICIES = {"required", "optional", "forbidden"}
PUSH_POLICIES = {"allowed", "approval_required", "forbidden"}


def parse_frontmatter(text: str) -> dict[str, str]:
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def markdown_sections(text: str) -> set[str]:
    text = text.replace("\r\n", "\n")
    return {
        match.group(1).strip()
        for match in re.finditer(r"^##\s+(.+?)\s*$", text, flags=re.MULTILINE)
    }


def git_fields(text: str) -> dict[str, str]:
    text = text.replace("\r\n", "\n")
    match = re.search(
        r"^##\s+Git delivery\s*$([\s\S]*?)(?=^##\s+|\Z)",
        text,
        flags=re.MULTILINE,
    )
    if not match:
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        item = re.match(r"^\s*-\s+([a-z_]+):\s*(.*?)\s*$", line)
        if item:
            values[item.group(1)] = item.group(2)
    return values


def validate(root: Path) -> dict[str, object]:
    errors: list[str] = []
    for name in REQUIRED_ROOT_FILES:
        if not (root / name).is_file():
            errors.append(f"missing root contract file: {name}")

    handoff_dir = root / "handoffs"
    handoffs = sorted(handoff_dir.glob("T-*.md")) if handoff_dir.is_dir() else []
    if not handoffs:
        errors.append("missing handoff files: handoffs/T-*.md")

    task_path = root / "TASKS.md"
    task_text = task_path.read_text(encoding="utf-8") if task_path.is_file() else ""
    checked: list[str] = []
    for path in handoffs:
        text = path.read_text(encoding="utf-8")
        frontmatter = parse_frontmatter(text)
        for key in REQUIRED_FRONTMATTER:
            if not frontmatter.get(key):
                errors.append(f"{path.name}: missing frontmatter field {key}")
        task_id = frontmatter.get("task_id", path.stem)
        if task_id not in task_text:
            errors.append(f"{path.name}: {task_id} is not listed in TASKS.md")
        status = frontmatter.get("status")
        if status and status not in TASK_STATUSES:
            errors.append(f"{path.name}: invalid status {status}")

        sections = markdown_sections(text)
        for section in REQUIRED_SECTIONS:
            if section not in sections:
                errors.append(f"{path.name}: missing section {section}")

        delivery = git_fields(text)
        for key in REQUIRED_GIT_FIELDS:
            if not delivery.get(key):
                errors.append(f"{path.name}: missing Git delivery field {key}")
        if delivery.get("commit_policy") not in COMMIT_POLICIES:
            errors.append(f"{path.name}: invalid commit_policy {delivery.get('commit_policy', '')}")
        if delivery.get("push_policy") not in PUSH_POLICIES:
            errors.append(f"{path.name}: invalid push_policy {delivery.get('push_policy', '')}")
        checked.append(path.relative_to(root).as_posix())

    return {
        "schema": "conversation-hub/project-contract-v1",
        "root": str(root.resolve()),
        "valid": not errors,
        "handoffs_checked": checked,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="repository root")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()
    result = validate(Path(args.root))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["valid"]:
        print(f"project contract valid: {result['root']}")
        for item in result["handoffs_checked"]:
            print(f"  checked {item}")
    else:
        print(f"project contract invalid: {result['root']}", file=sys.stderr)
        for error in result["errors"]:
            print(f"  - {error}", file=sys.stderr)
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
