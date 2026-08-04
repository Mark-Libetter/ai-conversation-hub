# AI Conversation Hub v18

A local-first Windows and macOS dashboard for finding, reviewing, organizing, and safely
exporting conversations from Hermes, Codex, WorkBuddy, Claude Code, CodePilot,
Cursor, Marvis, QClaw, and QoderWork.

## Product shape

The primary navigation deliberately stays small:

- **Find** — cross-source full-text search, excerpts, filters, saved views,
  resizable conversation detail, favorites, tags, notes, and Markdown export.
- **Daily review** — a rule-based review that works offline, plus an optional
  user-triggered model summary with achievements, current work, blockers, and
  next actions linked back to source conversations.
- **Projects** — multi-project classification, beginner-friendly planning,
  current state, version timeline, project summaries, and optional recent-file
  metadata.

Lower-frequency knowledge review, batch export, Context Packs, audit history,
model settings, and source settings live under **Tools** or **Settings**.

Search can be scoped to one Agent from inside the search box. Whitespace means
`AND`; quoted phrases, `OR`, `NOT`, `-excluded`, and parentheses are supported.
For example: `(日报 OR 周报) API NOT 股票`.

Search and its persistent SQLite FTS index are fully local and deterministic. No model is required to
discover paths, parse conversations, run Boolean queries, show snippets, or
export Markdown.

Project classification is deterministic and explainable. Manually locked
assignments win. Each canonical project can then define source-independent
include keywords, exclude keywords, workspace aliases, path patterns, and a
minimum confidence. The same rule scans every enabled Agent, so work performed
by different tools can share one project timeline. A dry run shows
new, moved, locked, conflicting, and per-Agent matches before a rule is saved.
Repeated non-generic workspaces remain the final fallback.

## v18 highlights

- First macOS application and DMG build for both Apple Silicon and Intel.
- macOS Application Support UserData isolation, Keychain-backed API secrets,
  Finder reveal, and system-specific update assets.
- Standard macOS discovery for Codex, Hermes, WorkBuddy, Claude Code, Cursor,
  and the other supported local adapters, with manual-path fallback.
- Windows and macOS CI plus packaged first-run smoke tests; the Windows portable
  build remains regression-tested.

## v17 highlights

- A built-in project coach turns each canonical project into a concise objective,
  stage, success criteria, milestones, acceptance checks, risks, open questions,
  and one immediately executable next action. It works from a safe template
  offline and can be explicitly refreshed with the configured model.
- Model-assisted knowledge extraction reuses the existing OpenAI-compatible
  endpoint and always creates review candidates first.
- Approved, non-revoked knowledge can be manually exported into a user-selected
  Obsidian vault. Hub-owned Markdown files use stable IDs and include traceable
  source-conversation evidence.
- Obsidian export is path-contained, never exports pending cards, skips
  restricted cards, and never modifies original Agent conversation stores.

## Install on Windows

The intended public distribution is a per-user Windows installer. It contains
its own Python runtime, so the target computer does not need Python, Git, or a
matching folder layout.

On first launch, the setup dialog:

1. checks common locations for all bundled source adapters;
2. lets the user choose which detected Agents to enable;
3. lets the user choose an extra directory to scan or paste exact paths;
4. validates each database or JSONL layout using read-only access; and
5. builds the first index after at least one enabled source passes validation.

Program files install to:

```text
%LOCALAPPDATA%\Programs\AIConversationHub
```

Personal configuration and Hub-owned data remain in:

```text
%LOCALAPPDATA%\AIConversationHub\UserData
```

Upgrading or uninstalling the program does not silently remove UserData.

## Install on macOS

The macOS build is a normal `AI Conversation Hub.app` distributed in a DMG.
Copy the app to `Applications`, open it, and complete the same first-run source
selection used on Windows. Personal data is stored separately at:

```text
~/Library/Application Support/AIConversationHub/UserData
```

The first macOS release is ad-hoc signed. Until a Developer ID and notarization
are configured, use Control-click → **Open** once if Gatekeeper warns about the
downloaded app. The app is not sandboxed because it must read user-approved
local Agent histories; all source stores are still opened read-only.

Standard discovery covers `~/.codex/state_5.sqlite`,
`~/.hermes/state.db`, `~/.workbuddy`, `~/.claude`, and supported Agent data
under `~/Library/Application Support`. If a vendor uses a different build or
channel path, paste the exact path in first-run setup or choose an extra search
directory.

## Run from source

Requires Python 3.11+ on Windows or macOS:

```powershell
python -m pip install -r requirements.txt
python desktop_app.py
```

For the existing development workflow, `server.py --port 8765` is still
supported. Set `CONVERSATION_HUB_DATA_DIR` to test against an isolated data
directory.

## Build the Windows installer

Install Python 3.11 and Inno Setup 6 on the build computer, then run:

```powershell
.\scripts\build.ps1
```

The script creates an isolated build environment, builds an onedir executable
with PyInstaller, performs a packaged first-run smoke test, and compiles the
per-user installer. Use `-SkipInstaller` to build only the portable directory.

## Build the macOS app and DMG

Run this on the target architecture’s Mac:

```bash
bash scripts/build_macos.sh
```

This builds and smoke-tests the `.app`, applies an ad-hoc signature by default,
and creates an architecture-specific DMG in `release/`. Set
`MACOS_SIGNING_IDENTITY` to a Developer ID Application identity when public
signing is available.

The `Build macOS` GitHub Actions workflow produces separate Apple Silicon
(`arm64`) and Intel (`x86_64`) DMGs. PyInstaller builds must run on macOS; the
Windows development computer cannot cross-compile a valid macOS app bundle.

## Privacy and safety

- The web server binds only to `127.0.0.1`.
- Original source databases are opened read-only and are never renamed,
  archived, deleted, or modified.
- Only top-level user/assistant text is indexed. System/developer prompts,
  reasoning, tool calls/output, background automation, heartbeats, trajectories,
  Codex subagents/guardian threads, WorkBuddy/QClaw/Claude side agents, and
  common secret patterns are excluded.
- User-visible tasks continued or delegated from another task remain indexed
  even when Codex labels their lineage as `thread_source=subagent`; filtering
  requires actual background-agent metadata rather than that label alone.
- Notes, project assignments, knowledge decisions, summaries, and audit
  metadata live in the separate `hub_notes.sqlite`.
- Model calls happen only after an explicit user action.
- API keys saved in the UI use Windows DPAPI or the macOS Keychain for the
  current user and are never written to `sources.json` or returned to the browser.
- Project-file scanning is opt-in, metadata-only, time/size bounded, and skips
  secrets, links, junctions, caches, and sensitive directories.

See [DESIGN_AND_SAFETY.md](DESIGN_AND_SAFETY.md) and [SECURITY.md](SECURITY.md).

## v14 highlights

- Skill asset library across Hermes, Codex, WorkBuddy, Claude Code, QClaw,
  QoderWork, Codex system skills, and installed Codex plugins.
- Skill detail pages with capabilities, provenance, safe file metadata,
  fingerprints, cross-Agent copy comparison, and project relationships.
- Hub-owned Skill favorites, lifecycle status, canonical grouping, tags, notes,
  and manually locked project links; original Skill directories stay read-only.
- Project-level Skill and Obsidian-vault drift auditing.

## v13 highlights

- Data-source quality center with schema fingerprints, message completeness,
  excluded-thread counts, and adapter health for every enabled source.
- Persistent Hub-owned full-text index plus conservative cross-Agent
  continuation links inside canonical projects.
- Secret-free management-data backup, conflict preview, and safe merge restore.
- Explainable project-rule suggestions and a one-sentence daily outcome template.
- Five persistent local themes, led by an original asset-safe Dream Skin inspired
  glass theme, with no injection into Codex or third-party artwork.
- HTTPS update manifests, SHA-256 verified downloads, signing preparation, and
  second-computer acceptance documentation.

## v12 highlights

- Pluggable, source-independent adapters for Claude Code, CodePilot, Cursor,
  Marvis, QClaw, and QoderWork in addition to the original three sources.
- First-run opt-in selection and later enable/disable/path changes from Settings.
- Cross-computer standard-path discovery plus a bounded user-selected-root
  fallback; no dependency on Everything, a model, or a fixed drive letter.
- Source-specific schema validation and filtering of system prompts, reasoning,
  tool output, heartbeats, automation, trajectories, and subagents.
- Local in-memory full-text matching for adapter messages, using the same
  Boolean query language and Markdown export pipeline as existing sources.

## v11 highlights

- Cross-Agent canonical project rules shared by Codex, Hermes, and WorkBuddy.
- Editable include/exclude keywords, workspace aliases, path patterns, and
  confidence thresholds.
- Read-only rule preview with per-Agent match counts and movement/conflict
  warnings before applying changes.
- Manual project assignments remain locked and always override automation.

## v10 highlights

- Auditable knowledge revisions, expiry/revocation, evidence verification, and
  conflict handling.
- Operation and artifact ledger that stores metadata and hashes, not exported
  content.
- Explicit project-root confirmation and bounded recent-file metadata.
- Reduced information architecture with lazy-loaded advanced panels.
- Portable resource/UserData separation, first-run source setup, dynamic local
  port selection, health signature, and packaging scaffolding.

## Configuration

Do not commit a real `sources.json`. Start from `sources.example.json` or use
the first-run UI. Environment overrides are available for managed setups:

```text
CONVERSATION_HUB_DATA_DIR=<Hub UserData directory>
CONVERSATION_HUB_HERMES_DB=<path to state.db>
CONVERSATION_HUB_CODEX_DB=<path to state_5.sqlite>
WORKBUDDY_HOME=<directory containing workbuddy.db and projects>
CONVERSATION_HUB_SUMMARY_API_URL=<OpenAI-compatible base URL>
CONVERSATION_HUB_SUMMARY_MODEL=<model ID>
CONVERSATION_HUB_SUMMARY_API_KEY=<optional secret>
```

## Contributing and license

Contribution guidance is in [CONTRIBUTING.md](CONTRIBUTING.md). A public license
has intentionally not been selected yet; choose one before the first public
release so downstream users know what they may do with the code.
