"""Tests for plain text file watcher."""

from __future__ import annotations

from queue import Queue
from typing import TYPE_CHECKING

from tpet.monitor.text_watcher import TextFileWatcher

if TYPE_CHECKING:
    from pathlib import Path

    from tpet.monitor.parser import SessionEvent


class TestTextFileWatcher:
    """Tests for TextFileWatcher."""

    def test_reads_new_lines(self, tmp_path: Path) -> None:
        """New lines appended after start are emitted as events."""
        text_file = tmp_path / "test.txt"
        text_file.write_text("existing line\n", encoding="utf-8")

        queue: Queue[SessionEvent] = Queue()
        watcher = TextFileWatcher(file_path=text_file, event_queue=queue)
        watcher.start()

        # Append new content
        with text_file.open("a", encoding="utf-8") as f:
            f.write("hello world\n")

        # Stop observer first to prevent race with manual processing
        watcher.stop()
        # Manually trigger processing (don't rely on watchdog timing)
        watcher._process_new_lines()

        assert not queue.empty()
        event = queue.get()
        assert event.role == "text"
        assert event.event_type == "text"
        assert event.summary == "hello world"

    def test_skips_existing_content(self, tmp_path: Path) -> None:
        """Content present before start is not emitted."""
        text_file = tmp_path / "test.txt"
        text_file.write_text("old content\n", encoding="utf-8")

        queue: Queue[SessionEvent] = Queue()
        watcher = TextFileWatcher(file_path=text_file, event_queue=queue)
        watcher.start()

        # Stop observer first to prevent race with manual processing
        watcher.stop()
        # Process without appending — should find nothing new
        watcher._process_new_lines()

        assert queue.empty()

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        """Blank lines are not emitted as events."""
        text_file = tmp_path / "test.txt"
        text_file.write_text("", encoding="utf-8")

        queue: Queue[SessionEvent] = Queue()
        watcher = TextFileWatcher(file_path=text_file, event_queue=queue)
        watcher.start()

        with text_file.open("a", encoding="utf-8") as f:
            f.write("\n\n  \n")

        watcher.stop()
        watcher._process_new_lines()

        assert queue.empty()

    def test_truncates_long_lines(self, tmp_path: Path) -> None:
        """Lines longer than MAX_SUMMARY_LENGTH are truncated."""
        text_file = tmp_path / "test.txt"
        text_file.write_text("", encoding="utf-8")

        queue: Queue[SessionEvent] = Queue()
        watcher = TextFileWatcher(file_path=text_file, event_queue=queue)
        watcher.start()

        long_line = "x" * 200
        with text_file.open("a", encoding="utf-8") as f:
            f.write(long_line + "\n")

        watcher.stop()
        watcher._process_new_lines()

        event = queue.get()
        assert len(event.summary) <= 150
        assert event.summary.endswith("...")

    def test_handles_missing_file(self, tmp_path: Path) -> None:
        """Watcher handles file not existing at start."""
        text_file = tmp_path / "nonexistent.txt"

        queue: Queue[SessionEvent] = Queue()
        watcher = TextFileWatcher(file_path=text_file, event_queue=queue)
        watcher.start()

        # Stop observer first to prevent race with manual processing
        watcher.stop()
        # Processing a missing file should not raise
        watcher._process_new_lines()

        assert queue.empty()

    def test_multiple_lines_produce_multiple_events(self, tmp_path: Path) -> None:
        """Multiple new lines each produce a separate event."""
        text_file = tmp_path / "test.txt"
        text_file.write_text("", encoding="utf-8")

        queue: Queue[SessionEvent] = Queue()
        watcher = TextFileWatcher(file_path=text_file, event_queue=queue)
        watcher.start()

        with text_file.open("a", encoding="utf-8") as f:
            f.write("line one\nline two\nline three\n")

        watcher.stop()
        watcher._process_new_lines()

        events = []
        while not queue.empty():
            events.append(queue.get())
        assert len(events) == 3
        assert events[0].summary == "line one"
        assert events[1].summary == "line two"
        assert events[2].summary == "line three"
