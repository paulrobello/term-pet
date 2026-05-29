"""Renderer that drives the macOS desktop pet over a Unix domain socket.

Art mode ``macos-desktop`` takes over from terminal rendering: sprite frames
live on the user's desktop as a native macOS window, and this renderer pipes
each tick's state + comment to that window over a local socket.

Lifecycle:
  * Every ``tpet --art-mode macos-desktop`` invocation spawns its own Swift
    pet process and communicates with it over a dedicated socket at
    ``{config_dir}/sessions/{tpet_pid}.sock``. Multiple tpet sessions run
    side-by-side, each with their own pet on the desktop.
  * The Swift pet is the socket server; this renderer is the client. We
    pass ``--socket``, ``--session``, ``--pwd``, ``--art-dir``,
    ``--profile``, and ``--config-dir`` so the binary knows where to
    listen, what identity to show in its tray, and which sprite/profile
    files to read — critical when the user launches tpet with
    ``--config-dir`` pointing elsewhere. The config dir is also surfaced
    in the tray so the user can locate it quickly.
  * On tpet exit (normal or Ctrl-C), :meth:`close` sends SIGTERM to the
    spawned pet and unlinks the socket file. Also registered via ``atexit``
    for abnormal exits.

Wire protocol (newline-delimited JSON per tick):
    {"state": "idle"|"reacting"|"sleeping", "comment": "..."}
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from rich.panel import Panel
from rich.text import Text

if TYPE_CHECKING:
    from rich.live import Live

    from tpet.config import TpetConfig
    from tpet.models.pet import PetProfile

logger = logging.getLogger(__name__)

_CONNECT_RETRIES = 20
_CONNECT_DELAY = 0.2
_TERMINATE_TIMEOUT = 2.0


class DesktopPetUnavailable(RuntimeError):
    """Raised when the Swift pet binary can't be located or started."""


class MacosDesktopRenderer:
    """Emits state+comment JSON to a per-session Swift pet; renders a minimal terminal status."""

    def __init__(self, config: TpetConfig) -> None:
        self._config = config
        self._sock: socket.socket | None = None
        self._child: subprocess.Popen[bytes] | None = None
        # Keep per-session sockets and the art/profile under the same
        # config_dir the user passed on the command line, so `tpet
        # --config-dir ~/.config/tpet-alt/` doesn't fall back to the default.
        self._sessions_dir: Path = config.config_dir / "sessions"
        self._sock_path: Path = self._sessions_dir / f"{os.getpid()}.sock"
        self._last_state: str | None = None
        self._last_comment: str | None = None

        # Guarantee cleanup on abnormal exits. close() is idempotent.
        atexit.register(self.close)

        self._spawn_and_connect()

    # ------------------------------------------------------------------
    # Spawn + connect
    # ------------------------------------------------------------------

    def _spawn_and_connect(self) -> None:
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

        # Unlink any leftover socket from a prior crash at the same PID
        # (unlikely but cheap).
        if self._sock_path.exists():
            self._sock_path.unlink()

        bin_path = self._resolve_binary()
        if bin_path is None:
            raise DesktopPetUnavailable(
                "macos-desktop mode needs the Deskpet binary. "
                "Build it with `make desktop` at the repo root, set "
                "DESKPET_BIN=/path/to/Deskpet, or install it to PATH as `deskpet`."
            )

        pwd = str(Path.cwd())
        session = Path(pwd).name or "deskpet"

        logger.info("spawning desktop pet: %s (session=%s)", bin_path, session)
        self._child = subprocess.Popen(
            [
                bin_path,
                "--socket", str(self._sock_path),
                "--session", session,
                "--pwd", pwd,
                "--art-dir", str(self._config.art_dir),
                "--profile", str(self._config.profile_path),
                "--config-dir", str(self._config.config_dir),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        if not self._try_connect(retries=_CONNECT_RETRIES):
            self._kill_child()
            raise DesktopPetUnavailable(
                f"Deskpet spawned but didn't open {self._sock_path} within "
                f"{_CONNECT_RETRIES * _CONNECT_DELAY:.1f}s."
            )
        logger.info("connected to desktop pet at %s", self._sock_path)

    def _try_connect(self, *, retries: int) -> bool:
        for _ in range(retries):
            if self._sock_path.exists():
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.connect(str(self._sock_path))
                    self._sock = s
                    return True
                except OSError:
                    pass
            time.sleep(_CONNECT_DELAY)
        return False

    @staticmethod
    def _resolve_binary() -> str | None:
        # 1. Explicit override (dev / CI)
        env = os.environ.get("DESKPET_BIN")
        if env and Path(env).is_file() and os.access(env, os.X_OK):
            return env
        # 2. Repo-local release build.
        #    src/tpet/renderer/macos_desktop.py -> repo root is parents[3].
        repo_root = Path(__file__).resolve().parents[3]
        for candidate in (
            repo_root / "macos_desktop" / ".build" / "release" / "Deskpet",
            repo_root / "macos_desktop" / ".build" / "arm64-apple-macosx" / "release" / "Deskpet",
            repo_root / "macos_desktop" / ".build" / "x86_64-apple-macosx" / "release" / "Deskpet",
        ):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        # 3. PATH fallback
        for name in ("deskpet", "sheep-screenmate"):
            found = shutil.which(name)
            if found:
                return found
        return None

    # ------------------------------------------------------------------
    # Renderer protocol
    # ------------------------------------------------------------------

    def render(
        self,
        live: Live,
        pet: PetProfile,
        frame_idx: int,
        current_comment: str | None,
        frame_changed: bool,
        comment_changed: bool,
    ) -> None:
        if not (frame_changed or comment_changed):
            return

        state = self._state_for_frame(frame_idx)
        payload: dict[str, object] = {"state": state}
        if comment_changed and current_comment:
            payload["comment"] = current_comment

        self._send(payload)
        self._update_terminal_status(live, pet, state, current_comment)

        self._last_state = state
        self._last_comment = current_comment

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _state_for_frame(frame_idx: int) -> str:
        # term-pet frames: 0-3 idle (incl. blinks), 4 reacting, 5 sleeping.
        if frame_idx == 4:
            return "reacting"
        if frame_idx == 5:
            return "sleeping"
        return "idle"

    def _send(self, payload: dict[str, object]) -> None:
        if self._sock is None and not self._try_connect(retries=2):
            return
        line = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            assert self._sock is not None
            self._sock.sendall(line)
        except OSError:
            logger.warning("socket send failed; will retry on next event")
            try:
                if self._sock is not None:
                    self._sock.close()
            finally:
                self._sock = None

    def _update_terminal_status(
        self,
        live: Live,
        pet: PetProfile,
        state: str,
        comment: str | None,
    ) -> None:
        lines: list[Text] = [
            Text(f"{pet.name}", style="bold cyan"),
            Text("art mode: macos-desktop", style="dim"),
            Text(f"state:    {state}", style="dim"),
        ]
        if comment:
            lines.append(Text(""))
            lines.append(Text(comment, style="italic"))
        body = Text("\n").join(lines)
        live.update(Panel(body, title="tpet", border_style="dim"), refresh=True)

    # ------------------------------------------------------------------
    # Lifecycle queries
    # ------------------------------------------------------------------

    def child_exited(self) -> bool:
        """Return True once the spawned Deskpet has terminated.

        The user can quit Deskpet from its tray menu or via ``killall
        Deskpet``; the app loop polls this so tpet exits in lock-step
        instead of talking to a dead socket forever.
        """
        if self._child is None:
            return False
        return self._child.poll() is not None

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Disconnect, terminate the spawned pet, and unlink the socket file.

        Idempotent — safe to call multiple times (run_app.finally + atexit
        both invoke it).
        """
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
        self._kill_child()
        try:
            self._sock_path.unlink()
        except (FileNotFoundError, AttributeError):
            pass

    def _kill_child(self) -> None:
        if self._child is None:
            return
        if self._child.poll() is None:
            try:
                self._child.terminate()
                self._child.wait(timeout=_TERMINATE_TIMEOUT)
            except subprocess.TimeoutExpired:
                self._child.kill()
            except ProcessLookupError:
                pass
        self._child = None
