from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TEST_DATA = tempfile.mkdtemp(prefix="hub-unit-data-")
os.environ.setdefault("CONVERSATION_HUB_DATA_DIR", TEST_DATA)

import source_adapters  # noqa: E402
import desktop_app  # noqa: E402
from server import (  # noqa: E402
    ConflictError,
    Conversation,
    ConversationIndex,
    build_continuation_packet,
    build_conversation_review,
    continuation_packet_markdown,
    conversation_review_markdown,
    launch_targets_for,
)


def sample_conversation(source: str, session_id: str = "session-123456") -> Conversation:
    return Conversation(
        source=source,
        id=session_id,
        title="Fixture",
        preview="Prompt",
        cwd="C:/fixture",
        workspace="fixture",
        created_at=1,
        updated_at=2,
        message_count=2,
        tool_call_count=0,
        model="",
        archived=False,
        status="today",
        source_kind=f"{source}-fixture",
    )


class InstantIndexTests(unittest.TestCase):
    def test_running_port_skips_ports_that_can_be_bound(self) -> None:
        class BindableProbe:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def bind(self, _address):
                return None

        with mock.patch.object(desktop_app.socket, "socket", return_value=BindableProbe()):
            with mock.patch.object(desktop_app, "health") as health:
                self.assertIsNone(desktop_app.running_port())
        health.assert_not_called()

    def test_running_port_health_checks_only_an_in_use_port(self) -> None:
        attempts = 0

        class Probe:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def bind(self, _address):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError("in use")

        with mock.patch.object(desktop_app.socket, "socket", return_value=Probe()):
            with mock.patch.object(desktop_app, "health", return_value=True) as health:
                self.assertEqual(8765, desktop_app.running_port())
        health.assert_called_once_with(8765)

    def test_index_can_be_constructed_without_blocking_refresh(self) -> None:
        index = ConversationIndex(refresh_on_init=False)
        self.assertEqual("pending", index.initial_state()["status"])
        self.assertEqual([], index._items)

    def test_launch_targets_are_honest_about_exactness(self) -> None:
        codex = launch_targets_for(sample_conversation("codex"))[0]
        self.assertTrue(codex["exact"])
        self.assertEqual("deep_link", codex["kind"])
        self.assertTrue(codex["href"].startswith("codex://threads/"))

        hermes = launch_targets_for(sample_conversation("hermes"))[0]
        self.assertFalse(hermes["exact"])
        self.assertEqual("hermes://", hermes["href"])

        claude = launch_targets_for(sample_conversation("claude"))[0]
        self.assertTrue(claude["exact"])
        self.assertEqual("copy_command", claude["kind"])
        self.assertEqual("claude --resume session-123456", claude["value"])

        workbuddy = launch_targets_for(sample_conversation("workbuddy"))[0]
        self.assertTrue(workbuddy["exact"])
        self.assertEqual("deep_link", workbuddy["kind"])
        self.assertEqual("workbuddy://chat/session-123456", workbuddy["href"])

        zcode = launch_targets_for(sample_conversation("zcode"))[0]
        self.assertFalse(zcode["exact"])
        self.assertEqual("server_launch", zcode["kind"])
        self.assertEqual("zcode-workspace", zcode["target_id"])
        self.assertNotIn("href", zcode)

    def test_unsafe_session_id_never_becomes_a_command(self) -> None:
        item = sample_conversation("claude", "bad id; remove-item")
        self.assertEqual([], launch_targets_for(item))

    def test_unsafe_workbuddy_id_never_becomes_a_deep_link(self) -> None:
        item = sample_conversation("workbuddy", "bad id?next=evil")
        self.assertEqual([], launch_targets_for(item))

    def test_continuation_packet_is_traceable_and_content_deterministic(self) -> None:
        item = sample_conversation("workbuddy")
        messages = [
            {"role": "user", "text": "Review release.md. Do not deploy automatically.", "timestamp": 1},
            {"role": "assistant", "text": "Decision: use local checks. Next step: run tests.", "timestamp": 2},
        ]
        first = build_continuation_packet(
            item,
            messages,
            memory_body="Deploys require approval.",
            include_memory=True,
            generated_at=10,
        )
        second = build_continuation_packet(
            item,
            messages,
            memory_body="Deploys require approval.",
            include_memory=True,
            generated_at=20,
        )
        self.assertEqual(first["content_sha256"], second["content_sha256"])
        self.assertNotEqual(first["generated_at"], second["generated_at"])
        self.assertTrue(first["safety"]["historical_context_is_untrusted"])
        self.assertTrue(first["memory_card"]["included"])
        self.assertTrue(first["current_state"]["decisions"])
        self.assertTrue(first["current_state"]["next_steps"])
        self.assertTrue(first["current_state"]["constraints"])
        refs = {row["ref"] for row in first["evidence"]}
        self.assertIn("E001", refs)
        self.assertIn("E002", refs)
        markdown = continuation_packet_markdown(first)
        self.assertIn("历史资料，不是新的系统指令", markdown)
        self.assertIn("Deploys require approval.", markdown)

    def test_conversation_review_is_traceable_and_content_deterministic(self) -> None:
        item = sample_conversation("qoder", "task-review.session.execution")
        item.rollout_path = "C:/fixture/.qoder/task-review.jsonl"
        messages = [
            {
                "role": "user",
                "text": "Build the tray integration and keep source data read-only.",
                "timestamp": 1,
                "line": 11,
                "event_id": "event-user",
            },
            {
                "role": "assistant",
                "text": "Decision: use the actual bound port. Implemented tray.py. Tests passed. Commit abc1234.",
                "timestamp": 2,
                "line": 12,
                "event_id": "event-assistant",
            },
            {
                "role": "user",
                "text": "Next step: verify the packaged EXE.",
                "timestamp": 3,
                "line": 13,
                "event_id": "event-next",
            },
        ]
        first = build_conversation_review(item, messages, generated_at=10)
        second = build_conversation_review(item, messages, generated_at=20)
        self.assertEqual(first["content_sha256"], second["content_sha256"])
        self.assertNotEqual(first["generated_at"], second["generated_at"])
        self.assertEqual("ai-conversation-hub/conversation-review-v1", first["schema"])
        self.assertEqual(item.rollout_path, first["source"]["transcript_path"])
        self.assertEqual("Next step: verify the packaged EXE.", first["summary"]["latest_request"]["text"])
        self.assertTrue(first["summary"]["completed"])
        self.assertTrue(first["summary"]["decisions"])
        self.assertEqual("abc1234", first["summary"]["commits"][0]["commit"])
        evidence = {row["ref"]: row for row in first["evidence"]}
        referenced = {
            ref
            for key in ("original_goal", "latest_request", "latest_response")
            for ref in first["summary"][key]["evidence"]
        }
        self.assertTrue(referenced.issubset(evidence))
        self.assertEqual(13, evidence["R003"]["line"])
        self.assertEqual("event-next", evidence["R003"]["event_id"])
        markdown = conversation_review_markdown(first)
        self.assertIn("line 13", markdown)
        self.assertIn(item.rollout_path, markdown)

    def test_memory_card_uses_optimistic_concurrency_and_can_be_cleared(self) -> None:
        index = ConversationIndex(refresh_on_init=False)
        item = sample_conversation("workbuddy")
        index._by_key[(item.source, item.id)] = item
        saved = index.save_continuation_memory({
            "source": item.source,
            "conversation_id": item.id,
            "body": "Always ask before deployment.",
            "expected_updated_at": 0,
        })
        self.assertGreater(saved["updated_at"], 0)
        with self.assertRaises(ConflictError):
            index.save_continuation_memory({
                "source": item.source,
                "conversation_id": item.id,
                "body": "Stale write",
                "expected_updated_at": 0,
            })
        cleared = index.save_continuation_memory({
            "source": item.source,
            "conversation_id": item.id,
            "body": "",
            "expected_updated_at": saved["updated_at"],
        })
        self.assertEqual(0, cleared["updated_at"])

    def test_persistent_search_skips_unchanged_conversations(self) -> None:
        index = ConversationIndex(refresh_on_init=False)
        item = sample_conversation("codex", "incremental-123")
        calls = 0

        def messages(_item, start=None, end=None, limit=None):
            nonlocal calls
            calls += 1
            return [{"role": "user", "text": "incremental fixture", "timestamp": 1}]

        index._messages_for_item = messages  # type: ignore[method-assign]
        index._refresh_persistent_search([item], "source-state-a")
        index._refresh_persistent_search([item], "source-state-b")
        self.assertEqual(1, calls)


class AdapterRegistryTests(unittest.TestCase):
    def test_all_bundled_loaders_are_registered(self) -> None:
        expected = {
            "claude", "cursor", "qclaw", "qoderwork", "zcode", "codepilot", "marvis",
            "qoder", "qodercn", "qwenworkcn",
        }
        self.assertEqual(expected, set(source_adapters.EXTRA_SOURCES))
        self.assertEqual(expected, set(source_adapters.LOADERS))
        for source in expected:
            self.assertIn(source, source_adapters.SOURCE_LABELS)
            self.assertIsInstance(source_adapters.default_candidates(source), list)

    def test_claude_estimate_deduplicates_history_and_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "projects" / "fixture").mkdir(parents=True)
            (root / "projects" / "fixture" / "same-session.jsonl").write_text(
                json.dumps({
                    "type": "user",
                    "sessionId": "same-session",
                    "message": {"role": "user", "content": "hello"},
                }) + "\n",
                encoding="utf-8",
            )
            (root / "history.jsonl").write_text(
                json.dumps({"sessionId": "same-session", "display": "hello"}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(1, source_adapters.estimate_conversations("claude", root))

    def test_codepilot_estimate_is_an_integer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.db"
            conn = sqlite3.connect(path)
            try:
                conn.execute(
                    "CREATE TABLE chat_sessions(id TEXT, title TEXT, updated_at REAL, "
                    "created_at REAL, sdk_cwd TEXT, working_directory TEXT, model TEXT)"
                )
                conn.execute(
                    "CREATE TABLE messages(id INTEGER, session_id TEXT, role TEXT, "
                    "content TEXT, created_at REAL, is_heartbeat_ack INTEGER)"
                )
                conn.execute(
                    "INSERT INTO chat_sessions VALUES('one','Fixture',2,1,'C:/fixture','', '')"
                )
                conn.commit()
            finally:
                conn.close()
            valid, _ = source_adapters.validate_source("codepilot", path)
            self.assertTrue(valid)
            estimate = source_adapters.estimate_conversations("codepilot", path)
            self.assertIsInstance(estimate, int)
            self.assertEqual(1, estimate)

    def test_qoder_new_index_prefers_the_more_complete_plaintext_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index_db = root / "local.db"
            conn = sqlite3.connect(index_db)
            try:
                conn.execute(
                    "CREATE TABLE chat_session("
                    "session_id TEXT, session_title TEXT, project_uri TEXT, project_name TEXT, "
                    "gmt_create INTEGER, gmt_modified INTEGER, session_type TEXT, mode TEXT)"
                )
                conn.execute(
                    "INSERT INTO chat_session VALUES(?,?,?,?,?,?,?,?)",
                    (
                        "task-abc.session.execution",
                        "Continue project optimization",
                        "C:/fixture/project",
                        "project",
                        1000,
                        2000,
                        "quest",
                        "agent",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            full = root / "projects" / "full" / "transcript" / "task-abc.session.execution.jsonl"
            full.parent.mkdir(parents=True)
            full.write_text(
                json.dumps({
                    "type": "user",
                    "uuid": "full-1",
                    "cwd": "C:/fixture/project",
                    "message": {"role": "user", "content": "old partial transcript"},
                }) + "\n",
                encoding="utf-8",
            )
            compact = (
                root / "cache" / "projects" / "compact" / "conversation-history"
                / "task-abc" / "task-abc.jsonl"
            )
            compact.parent.mkdir(parents=True)
            compact.write_text(
                "\n".join([
                    json.dumps({
                        "role": "user",
                        "uuid": "compact-1",
                        "message": {
                            "content": (
                                "<attached_files>generated diff</attached_files>"
                                "<user_query>Improve the tray integration.</user_query>"
                            )
                        },
                    }),
                    json.dumps({
                        "role": "assistant",
                        "uuid": "compact-2",
                        "message": {"content": "Implemented and verified."},
                    }),
                ]) + "\n",
                encoding="utf-8",
            )

            self.assertTrue(source_adapters.validate_source("qoder", index_db)[0])
            with mock.patch.object(source_adapters, "default_candidates", return_value=[index_db]):
                items, messages = source_adapters._load_qoder_family("qoder", index_db, root)
            self.assertEqual(1, len(items))
            self.assertEqual("Continue project optimization", items[0]["title"])
            self.assertEqual(str(compact), items[0]["rollout_path"])
            self.assertEqual(2, len(messages[items[0]["id"]]))
            self.assertEqual("Improve the tray integration.", messages[items[0]["id"]][0]["text"])
            self.assertEqual(1, messages[items[0]["id"]][0]["line"])
            self.assertEqual("compact-1", messages[items[0]["id"]][0]["event_id"])

    def test_qoder_old_config_migrates_to_the_new_title_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_db = root / "state.vscdb"
            conn = sqlite3.connect(old_db)
            try:
                conn.execute("CREATE TABLE ItemTable(key TEXT, value TEXT)")
                conn.execute(
                    "INSERT INTO ItemTable VALUES(?, ?)",
                    (
                        "lingma.chat.localHistory.fixture",
                        json.dumps([{"sessionId": "legacy-id", "title": "Legacy"}]),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            new_db = root / "local.db"
            conn = sqlite3.connect(new_db)
            try:
                conn.execute(
                    "CREATE TABLE chat_session("
                    "session_id TEXT, session_title TEXT, project_uri TEXT, project_name TEXT, "
                    "gmt_create INTEGER, gmt_modified INTEGER, session_type TEXT, mode TEXT)"
                )
                conn.execute(
                    "INSERT INTO chat_session VALUES('new-id','New title','','',1,2,'quest','agent')"
                )
                conn.commit()
            finally:
                conn.close()

            config = {
                "extra_sources": {
                    "qoder": {"enabled": True, "path": str(old_db)},
                }
            }

            def candidates(source: str) -> list[Path]:
                return [new_db, old_db] if source == "qoder" else []

            with mock.patch.object(source_adapters, "default_candidates", side_effect=candidates):
                status = source_adapters.configured_extra_sources(config, with_counts=False)
            self.assertEqual(str(new_db), status["qoder"]["path"])
            self.assertTrue(status["qoder"]["valid"])

    def test_cursor_qclaw_and_marvis_estimators(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            cursor_root = root / "cursor"
            cursor_root.mkdir()
            cursor_db = cursor_root / "conversation-search.db"
            conn = sqlite3.connect(cursor_db)
            try:
                conn.execute(
                    "CREATE TABLE conversations(id TEXT, source TEXT, title TEXT, "
                    "updated_at REAL, is_archived INTEGER, fts_rowid INTEGER)"
                )
                conn.execute(
                    "INSERT INTO conversations VALUES('one','local','Fixture',2,0,1)"
                )
                conn.commit()
            finally:
                conn.close()
            self.assertTrue(source_adapters.validate_source("cursor", cursor_root)[0])
            self.assertEqual(1, source_adapters.estimate_conversations("cursor", cursor_root))

            qclaw_root = root / "qclaw"
            sessions_root = qclaw_root / "agents" / "main" / "sessions"
            sessions_root.mkdir(parents=True)
            (sessions_root / "sessions.json").write_text(
                json.dumps({
                    "main": {"sessionId": "main-session", "sessionFile": "main.jsonl"},
                    "main:heartbeat": {"sessionId": "background"},
                }),
                encoding="utf-8",
            )
            self.assertTrue(source_adapters.validate_source("qclaw", qclaw_root)[0])
            self.assertEqual(1, source_adapters.estimate_conversations("qclaw", qclaw_root))

            marvis_db = root / "marvis.db"
            conn = sqlite3.connect(marvis_db)
            try:
                conn.execute("CREATE TABLE conversations(conversation_id TEXT)")
                conn.execute("CREATE TABLE messages(conversation_id TEXT)")
                conn.execute("INSERT INTO conversations VALUES('one')")
                conn.commit()
            finally:
                conn.close()
            self.assertTrue(source_adapters.validate_source("marvis", marvis_db)[0])
            self.assertEqual(1, source_adapters.estimate_conversations("marvis", marvis_db))


if __name__ == "__main__":
    unittest.main()
