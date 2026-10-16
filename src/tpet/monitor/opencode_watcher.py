"""Opencode session watcher polling the opencode SQLite database."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tpet.monitor.parser import MAX_SUMMARY_LENGTH, SessionEvent

if TYPE_CHECKING:
    from queue import Queue

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
POLL_INTERVAL_SECONDS = 2.0

# Message types carrying user or assistant prose. The opencode session_message
# data is a JSON tagged union on `type`; everything else (tool, reasoning,
# synthetic, system, ...) is internal noise, same filter philosophy as the
# Claude JSONL parser.
_TEXT_TYPE_ROLES = {
    "user": "user",
    "text": "assistant",
}


def _truncate(text: str) -> str:
    if len(text) <= MAX_SUMMARY_LENGTH:
        return text
    return text[: MAX_SUMMARY_LENGTH - 3] + "..."


def parse_message_row(mtype: str, data_json: str, time_created: int) -> SessionEvent | None:
    """Parse one session_message row into a SessionEvent.

    Only user prompts and assistant text parts become events; internal types
    (tool calls, reasoning, synthetic/system notices) are filtered out.

    Args:
        mtype: The ``session_message.type`` discriminator value.
        data_json: The ``session_message.data`` JSON blob.
        time_created: Row creation time as epoch milliseconds.

    Returns:
        SessionEvent if the row is relevant, None otherwise.
    """
    role = _TEXT_TYPE_ROLES.get(mtype)
    if role is None:
        return None

    try:
        data = json.loads(data_json)
    except (json.JSONDecodeError, ValueError):
        logger.debug("Failed to parse opencode message data")
        return None

    if not isinstance(data, dict):
        return None

    text = data.get("text", "")
    if not isinstance(text, str) or not text.strip():
        return None

    return SessionEvent(
        event_type=role,
        role=role,
        summary=_truncate(text.strip()),
        timestamp=datetime.fromtimestamp(time_created / 1000, tz=UTC).isoformat(),
    )


def newest_opencode_activity(db_path: Path) -> float | None:
    """Return the newest session_message time_created as epoch seconds.

    Args:
        db_path: Path to the opencode SQLite database.

    Returns:
        Epoch seconds of the newest message, or None when the database is
        missing, unreadable, or has no messages.
    """
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=2.0)
        try:
            row = conn.execute("SELECT max(time_created) FROM session_message").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not row or row[0] is None:
        return None
    return row[0] / 1000.0


class OpencodeWatcher:
    """Watches the opencode SQLite session database for new messages.

    Polls session_message with a high-water-mark cursor on the time-ordered
    msg_ id instead of watching the file with watchdog, since SQLite WAL
    writes do not reliably surface as file events. Existing history is
    skipped: the cursor initializes to the newest id at start.
    """

    def __init__(
        self,
        db_path: Path,
        event_queue: Queue[SessionEvent],
        project_path: str | None = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self._db_path = db_path
        self._event_queue = event_queue
        self._project_path = str(Path(project_path).resolve()) if project_path else None
        self._poll_interval = poll_interval
        self._cursor = ""
        self._initialized = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start watching; existing messages are not re-emitted."""
        self._init_cursor()
        self._thread = threading.Thread(target=self._poll_loop, name="opencode-watcher", daemon=True)
        self._thread.start()
        logger.info("Watching opencode database: %s", self._db_path)

    def stop(self) -> None:
        """Stop watching."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
            logger.info("Stopped opencode watcher")

    def poll_once(self) -> None:
        """Fetch rows past the cursor and enqueue events.

        Public so tests can drive a poll deterministically without the
        background thread (same pattern as the other watchers' manual
        reprocess hooks). Also initializes the cursor lazily so a database
        created after start() is picked up without emitting history.
        """
        if not self._initialized:
            self._init_cursor()
            if not self._initialized:
                return
        for msg_id, mtype, data_json, row_time in self._fetch_new_rows():
            self._cursor = msg_id
            event = parse_message_row(mtype, data_json, row_time)
            if event is not None:
                self._event_queue.put(event)

    def _init_cursor(self) -> None:
        """Snap the cursor to the newest existing message id, if any."""
        if not self._db_path.exists():
            return
        try:
            conn = sqlite3.connect(self._db_path.as_uri() + "?mode=ro", uri=True, timeout=2.0)
            try:
                row = conn.execute("SELECT max(id) FROM session_message").fetchone()
            finally:
                conn.close()
            self._cursor = row[0] or ""
            self._initialized = True
        except sqlite3.Error:
            logger.warning("Opencode database not readable, will retry: %s", self._db_path)

    def _fetch_new_rows(self) -> list[tuple[str, str, str, int]]:
        """Return (id, type, data, time_created) rows past the cursor."""
        if not self._db_path.exists():
            return []
        try:
            conn = sqlite3.connect(self._db_path.as_uri() + "?mode=ro", uri=True, timeout=2.0)
            try:
                if self._project_path is not None:
                    rows = conn.execute(
                        """SELECT m.id, m.type, m.data, m.time_created
                           FROM session_message m
                           JOIN session_v2 s ON m.session_id = s.id
                           WHERE m.id > ? AND s.directory = ?
                           ORDER BY m.id ASC""",
                        (self._cursor, self._project_path),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, type, data, time_created FROM session_message WHERE id > ? ORDER BY id ASC",
                        (self._cursor,),
                    ).fetchall()
            finally:
                conn.close()
            return rows
        except sqlite3.Error:
            logger.exception("Error reading opencode database %s", self._db_path)
            return []

    def _poll_loop(self) -> None:
        while not self._stop.wait(self._poll_interval):
            self.poll_once()
