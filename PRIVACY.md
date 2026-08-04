# Privacy

AI Conversation Hub is local-first. Its server binds to `127.0.0.1`; original
conversation stores are opened read-only. The Hub indexes only top-level user
and assistant text and excludes system/developer prompts, reasoning, tool
input/output, background automation and subagent-only records.

Hub-owned notes, tags, favorites, projects, summaries and knowledge decisions
are stored separately. Management backups deliberately exclude original
conversations, API keys, search indexes and machine-specific source settings.

No model is required for search or organization. A configured model endpoint is
contacted only after the user explicitly requests a model-backed summary or
connection test. Saved API keys are protected with Windows DPAPI or the macOS
Keychain for the current user and are never returned to the browser.

The update checker contacts only the HTTPS manifest URL configured by the user.
Downloaded packages are SHA-256 verified and are never executed automatically.
