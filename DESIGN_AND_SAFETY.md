# Design and safety notes

AI Conversation Hub Lite aggregates conversations from local AI coding agents
for search and daily review. This document records the safety boundaries the
implementation follows.

## Read-only toward source data

Each Agent owns its conversation lifecycle and may change schemas. Writing
into an Agent's database would bypass application invariants and could make a
task disappear or become unreadable. The Hub therefore opens every source
read-only (`mode=ro` plus `PRAGMA query_only`) and stores user-authored
management data (favorites, tags, notes, daily metadata) separately in
`hub_notes.sqlite`.

## Search boundaries

- Hermes: title, working directory, notes/tags, and user/assistant message text.
- Codex: title, preview, working directory, notes/tags, and user/assistant text
  from top-level rollout JSONL. Threads marked as subagents by their actual
  spawn metadata are excluded; a user-visible task continued from another task
  is retained even if its lineage field alone says `thread_source=subagent`.
- WorkBuddy: main sessions and user/assistant text; assistant/side-agent
  records are labelled as such instead of being mixed into primary sessions.
- Claude Code: completed transcript JSONL plus history/session indexes; files
  above a safe size are degraded to metadata-only rather than parsed blindly.
  Sidechains are excluded.
- QoderWork: task chats and user/assistant message text. Renamed product
  directories (QoderWork/QwenWorkCN/QwenWork) are merged by chat id so a
  product rename never loses conversations.
- ZCode: top-level sessions only; subagent sessions (`parent_id` set) and
  archived sessions are excluded. Only `real_user` prompts and non-system
  assistant responses are indexed; reasoning, tool, and timeline parts are
  skipped.
- Excluded everywhere: system/developer prompts, reasoning, tool calls,
  command output, credentials, and configuration files. Common secret
  patterns are redacted before anything is stored in the index.

## Daily review boundaries

- Calendar grouping uses the message timestamp in the Asia/Shanghai timezone,
  not only the conversation's last-modified date, so long conversations are
  split across the correct days.
- Only user and assistant text is eligible for daily-review input.
- Known environment wrappers, system reminders, and injected instruction
  blocks are removed before summarization.
- Rule-generated conclusions are intentionally cautious and always link back
  to the source conversation when possible.
- The Lite build performs no model calls at all; the daily review is a
  deterministic rule template.

## Custom source framework

Users can add their own Agents through JSONL, Markdown, or SQLite custom
sources without code changes. The same redaction and role normalization apply;
SQLite sources must expose recognizable session/role/text fields and are
validated read-only before first use.

## Failure isolation

Adapter failures are isolated per source and reported in Settings. One broken
Agent cannot prevent the other sources from loading or searching.
