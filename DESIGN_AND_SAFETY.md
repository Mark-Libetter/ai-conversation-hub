# Unified Hermes + Codex conversation management

## Recommended division of responsibility

| Need | Best surface |
| --- | --- |
| Continue a recently active task | Native Hermes/Codex sidebar |
| Find an old conversation | Conversation Hub full-text search |
| Understand what a conversation was doing | Traceable overview + recent context |
| Review what happened on a calendar day | Daily review with source-conversation links |
| Add personal status, tags, or notes | Conversation Hub companion database |
| Rename, pin, or archive a Codex task | Codex native task actions |
| Delete or prune Hermes history | Separate confirmed cleanup workflow |

## Why the hub is read-only toward source data

Hermes and Codex each own their conversation lifecycle and may change schemas.
Writing directly into either product's database would bypass application
invariants and can make a task disappear or become unreadable. The hub therefore
reads product state and stores user-authored management data separately.

## Search boundaries

- Hermes: title, working directory, notes/tags, and user/assistant message FTS.
- Codex: title, preview, working directory, notes/tags, and a local FTS copy of
  user/assistant text from top-level rollout JSONL. Threads marked as subagents,
  explorers, workers, or approval guardians by their actual spawn metadata are
  excluded. A user-visible task continued from another task is retained even if
  its lineage field alone says `thread_source=subagent`.
- Excluded: system/developer prompts, tool calls, command output, credentials,
  and configuration files.

## Daily review boundaries

- Calendar grouping uses the message timestamp in the Asia/Shanghai timezone,
  not only the conversation's last-modified date.
- Only user and assistant text is eligible for daily-review input.
- Known environment, delegation, system-reminder, and injected instruction
  wrappers are removed before summarization.
- Rule-generated conclusions are intentionally cautious and always link back to
  the source conversation when possible.
- A configured model is called only after an explicit user action.
- Keys saved through the settings dialog are protected by Windows DPAPI or the
  macOS Keychain for the current user. The database stores only the protected
  Windows blob or a Keychain reference marker.
- The key is never returned by the settings API, written to `sources.json`, or
  included in model-summary input.
- Model output is constrained to source conversation IDs and cached separately
  from all source-product databases.

## Future safe extensions

1. Add duplicate/series detection using lineage IDs before using title heuristics.
2. Add import dry-runs and topology validation before accepting external
   conversation archives.
3. Add controlled native actions only through supported product APIs, never by
   editing product databases directly.

## v10 information architecture

- Primary navigation is limited to finding conversations, daily review, and
  projects.
- Knowledge review, export, Context Packs, model settings, and auditing remain
  available as secondary tools rather than competing primary workspaces.
- Project version comparison, project files, Context Packs, and the activity
  ledger are collapsed and loaded on demand.
- Mobile keeps an explicit source selector because the desktop source rail is
  hidden at narrow widths.

## v11 cross-Agent project identity

- A project ID is canonical and source-independent. Codex, Hermes, and
  WorkBuddy assignments point to that same ID instead of creating Agent-specific
  copies.
- User-maintained detection rules live only in `hub_notes.sqlite`. They combine
  include keywords, exclusion vetoes, workspace aliases, normalized path
  fragments, and a minimum confidence.
- A rule preview scans the in-memory read-only conversation index and reports
  matches, moves, conflicts, locked assignments, and source distribution.
- Saving a rule may update only unlocked derived assignments. Manual locked
  assignments always win, and close rule scores remain unassigned.

## v12 pluggable local sources

- `source_adapters.py` owns discovery, validation, read-only parsing, and safe
  user/assistant text extraction for optional Agents.
- Sources are enabled explicitly in `sources.json`. Standard user-profile and
  application-data locations are portable across computers; an extra root is
  scanned only after the user selects it.
- Search, snippets, daily review, projects, notes, and Markdown export consume
  one normalized conversation contract and do not require a model.
- Claude Code sidechains, Cursor subagents, QClaw heartbeat/cron/dreaming/
  trajectory/subagent records, and all system/reasoning/tool messages are
  excluded. Cursor metadata-only records remain visibly metadata-only until
  local message bodies exist.
- Adapter failures are isolated per source and reported in Settings. One broken
  Agent cannot prevent the other sources from loading.

## Portable installation boundary

- A frozen build reads HTML/CSS/JavaScript from its immutable application
  directory and writes only to `%LOCALAPPDATA%\AIConversationHub\UserData` on
  Windows or `~/Library/Application Support/AIConversationHub/UserData` on
  macOS.
- A source checkout remains backward compatible by using its own directory
  unless `CONVERSATION_HUB_DATA_DIR` is set.
- First-run discovery checks standard locations directly. Recursive discovery
  happens only inside a directory explicitly selected by the user.
- Candidate databases must pass source-specific SQLite schema validation before
  their paths are saved.
- Installer upgrades replace program files but preserve UserData. Uninstall also
  preserves UserData unless the user removes it separately after export.

## Auditable derived knowledge and project files

- Knowledge changes append immutable revision snapshots. Rejection, restoration,
  superseding, expiration, and revocation remain visible.
- Evidence can be rechecked against the read-only source and conflicts require
  an explicit resolution.
- The activity ledger stores operation type, status, timestamps, counts, and
  artifact hashes; it does not store export or Context Pack bodies.
- Project roots require explicit confirmation. Scans are metadata-only,
  bounded by time/depth/count, and skip links, junctions, sensitive names,
  secret-like extensions, caches, and application data.

## Knowledge, export, and continuation boundaries

- Knowledge extraction creates candidates only. A candidate becomes approved
  knowledge only after an explicit user review action.
- Every available evidence link points to an original source conversation; Hub
  summaries are never presented as immutable source facts.
- Markdown and JSONL exports contain only user/assistant text already eligible
  for the Hub, plus user-authored Hub metadata.
- Context Packs use approved project/global knowledge and safe project summary
  fields. They are previewed locally and are never sent to a model by the Hub.
- Project batch assignment and merging modify only Hub-owned tables. A permanent
  alias maps merged rule/workspace project IDs to the retained project.
- Model-assisted Obsidian preparation still creates pending candidates first;
  the model never writes directly into a vault.
- Manual Obsidian export accepts only approved, active, non-restricted knowledge.
  Vault/subfolder paths are resolved and containment-checked, and an existing
  Markdown note is updated only when it contains the matching `ai_hub_id`.
