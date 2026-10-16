"""Tests for the opencode SQLite session watcher."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import UTC, datetime
from queue import Queue
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from tpet.cli import app
from tpet.monitor.opencode_watcher import (
    OpencodeWatcher,
    newest_opencode_activity,
    parse_message_row,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tpet.monitor.parser import SessionEvent

runner = CliRunner()

MS = 1_729_000_000_000


def make_db(path: Path) -> sqlite3.Connection:
    """Create a minimal synthetic opencode database with one session."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE session_v2 (
            id TEXT PRIMARY KEY,
            directory TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL
        );
        CREATE TABLE session_message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            type TEXT NOT NULL,
            seq INTEGER NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO session_v2 (id, directory, time_created, time_updated) VALUES ('ses_1', ?, ?, ?)",
        (str(path.parent), MS, MS),
    )
    conn.commit()
    return conn


def add_message(
    conn: sqlite3.Connection,
    msg_id: str,
    mtype: str,
    data: dict[str, object],
    session_id: str = "ses_1",
    time_created: int = MS,
) -> None:
    """Insert one session_message row."""
    conn.execute(
        "INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (msg_id, session_id, mtype, 1, time_created, time_created, json.dumps(data)),
    )
    conn.commit()


class TestParseMessageRow:
    def test_user_message(self) -> None:
        event = parse_message_row("user", json.dumps({"type": "user", "text": "fix the login bug"}), MS)

        assert event is not None
        assert event.role == "user"
        assert event.event_type == "user"
        assert event.summary == "fix the login bug"
        assert event.timestamp == datetime.fromtimestamp(MS / 1000, tz=UTC).isoformat()

    def test_assistant_text(self) -> None:
        event = parse_message_row("text", json.dumps({"type": "text", "text": "Fixed it."}), MS)

        assert event is not None
        assert event.role == "assistant"
        assert event.event_type == "assistant"
        assert event.summary == "Fixed it."

    @pytest.mark.parametrize(
        "mtype",
        [
            "tool",
            "reasoning",
            "synthetic",
            "system",
            "agent-switched",
            "model-switched",
            "skill",
            "shell",
            "step-start",
        ],
    )
    def test_skips_internal_types(self, mtype: str) -> None:
        assert parse_message_row(mtype, json.dumps({"type": mtype, "text": "noise"}), MS) is None

    def test_skips_blank_text(self) -> None:
        assert parse_message_row("user", json.dumps({"type": "user", "text": "   "}), MS) is None

    def test_truncates_long_text(self) -> None:
        event = parse_message_row("text", json.dumps({"type": "text", "text": "x" * 300}), MS)

        assert event is not None
        assert len(event.summary) <= 150
        assert event.summary.endswith("...")

    def test_malformed_json(self) -> None:
        assert parse_message_row("user", "not json", MS) is None


class TestOpencodeWatcher:
    def test_skips_existing_rows_on_start(self, tmp_path: Path) -> None:
        db = tmp_path / "opencode.db"
        conn = make_db(db)
        add_message(conn, "msg_1", "user", {"type": "user", "text": "old"})
        conn.close()

        queue: Queue[SessionEvent] = Queue()
        watcher = OpencodeWatcher(db_path=db, event_queue=queue, project_path=str(tmp_path))
        watcher.start()
        watcher.stop()

        assert queue.empty()

    def test_emits_new_rows_after_start(self, tmp_path: Path) -> None:
        db = tmp_path / "opencode.db"
        conn = make_db(db)
        queue: Queue[SessionEvent] = Queue()
        watcher = OpencodeWatcher(db_path=db, event_queue=queue, project_path=str(tmp_path))
        watcher.start()
        watcher.stop()

        add_message(conn, "msg_1", "user", {"type": "user", "text": "hello"})
        add_message(conn, "msg_2", "text", {"type": "text", "text": "world"})
        watcher.poll_once()

        events: list[SessionEvent] = []
        while not queue.empty():
            events.append(queue.get())
        assert [e.summary for e in events] == ["hello", "world"]
        assert [e.role for e in events] == ["user", "assistant"]

    def test_cursor_only_emits_once(self, tmp_path: Path) -> None:
        db = tmp_path / "opencode.db"
        conn = make_db(db)
        queue: Queue[SessionEvent] = Queue()
        watcher = OpencodeWatcher(db_path=db, event_queue=queue, project_path=str(tmp_path))
        watcher.start()
        watcher.stop()

        add_message(conn, "msg_1", "user", {"type": "user", "text": "once"})
        watcher.poll_once()
        watcher.poll_once()

        events: list[SessionEvent] = []
        while not queue.empty():
            events.append(queue.get())
        assert len(events) == 1

    def test_scopes_to_project_directory(self, tmp_path: Path) -> None:
        db = tmp_path / "opencode.db"
        conn = make_db(db)
        conn.execute(
            "INSERT INTO session_v2 (id, directory, time_created, time_updated) VALUES ('ses_2', '/elsewhere', ?, ?)",
            (MS, MS),
        )
        conn.commit()
        queue: Queue[SessionEvent] = Queue()
        watcher = OpencodeWatcher(db_path=db, event_queue=queue, project_path=str(tmp_path))
        watcher.start()
        watcher.stop()

        add_message(conn, "msg_1", "user", {"type": "user", "text": "mine"}, session_id="ses_1")
        add_message(conn, "msg_2", "user", {"type": "user", "text": "theirs"}, session_id="ses_2")
        watcher.poll_once()

        events: list[SessionEvent] = []
        while not queue.empty():
            events.append(queue.get())
        assert [e.summary for e in events] == ["mine"]

    def test_missing_db_is_silent(self, tmp_path: Path) -> None:
        queue: Queue[SessionEvent] = Queue()
        watcher = OpencodeWatcher(db_path=tmp_path / "nope.db", event_queue=queue)
        watcher.start()
        watcher.stop()
        watcher.poll_once()

        assert queue.empty()

    def test_db_created_after_start_watches_from_now(self, tmp_path: Path) -> None:
        db = tmp_path / "opencode.db"
        queue: Queue[SessionEvent] = Queue()
        watcher = OpencodeWatcher(db_path=db, event_queue=queue, project_path=str(tmp_path))
        watcher.start()
        watcher.stop()

        conn = make_db(db)
        add_message(conn, "msg_1", "user", {"type": "user", "text": "history"})
        watcher.poll_once()
        add_message(conn, "msg_2", "user", {"type": "user", "text": "fresh"})
        watcher.poll_once()

        events: list[SessionEvent] = []
        while not queue.empty():
            events.append(queue.get())
        assert [e.summary for e in events] == ["fresh"]


class TestNewestActivity:
    def test_returns_newest_time(self, tmp_path: Path) -> None:
        db = tmp_path / "opencode.db"
        conn = make_db(db)
        add_message(conn, "msg_1", "user", {"type": "user", "text": "a"}, time_created=MS)
        add_message(conn, "msg_2", "text", {"type": "text", "text": "b"}, time_created=MS + 5000)

        assert newest_opencode_activity(db) == (MS + 5000) / 1000.0

    def test_missing_db(self, tmp_path: Path) -> None:
        assert newest_opencode_activity(tmp_path / "nope.db") is None

    def test_empty_db(self, tmp_path: Path) -> None:
        db = tmp_path / "opencode.db"
        make_db(db).close()

        assert newest_opencode_activity(db) is None


class TestDetectWatchSource:
    def make_claude_session(self, claude_dir: Path, age_seconds: float) -> None:
        claude_dir.mkdir(parents=True, exist_ok=True)
        jsonl = claude_dir / "session.jsonl"
        jsonl.write_text("{}", encoding="utf-8")
        old = time.time() - age_seconds
        os.utime(jsonl, (old, old))

    def test_opencode_newer(self, tmp_path: Path) -> None:
        from tpet.app import detect_watch_source

        claude_dir = tmp_path / "claude"
        self.make_claude_session(claude_dir, age_seconds=3600)
        db = tmp_path / "opencode.db"
        conn = make_db(db)
        add_message(conn, "msg_1", "user", {"type": "user", "text": "now"}, time_created=int(time.time() * 1000))

        assert detect_watch_source(claude_dir, db) == "opencode"

    def test_claude_newer(self, tmp_path: Path) -> None:
        from tpet.app import detect_watch_source

        claude_dir = tmp_path / "claude"
        self.make_claude_session(claude_dir, age_seconds=0)
        db = tmp_path / "opencode.db"
        conn = make_db(db)
        add_message(conn, "msg_1", "user", {"type": "user", "text": "old"}, time_created=MS)

        assert detect_watch_source(claude_dir, db) == "claude"

    def test_no_opencode_db(self, tmp_path: Path) -> None:
        from tpet.app import detect_watch_source

        claude_dir = tmp_path / "claude"
        self.make_claude_session(claude_dir, age_seconds=3600)

        assert detect_watch_source(claude_dir, tmp_path / "nope.db") == "claude"

    def test_neither_active(self, tmp_path: Path) -> None:
        from tpet.app import detect_watch_source

        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()

        assert detect_watch_source(claude_dir, tmp_path / "nope.db") == "claude"


class TestCliWatchSource:
    def test_run_help_lists_watch_source(self) -> None:
        result = runner.invoke(app, ["run", "--help"])

        assert result.exit_code == 0
        assert "--watch-source" in result.stdout

    def test_root_help_lists_watch_source(self) -> None:
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "--watch-source" in result.stdout

    def test_show_session_opencode(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["--watch-source", "opencode", "--show-session", "--config-dir", str(tmp_path)])

        assert result.exit_code == 0
        assert "opencode.db" in result.stdout
