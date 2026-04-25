"""Main application loop for tpet."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from queue import Empty, Queue
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live

from tpet.animation.engine import PetAnimator
from tpet.commentary.generator import get_session_usage, submit_comment, submit_idle_chatter
from tpet.config import ArtMode
from tpet.monitor.text_watcher import TextFileWatcher
from tpet.monitor.watcher import SessionWatcher, encode_project_path
from tpet.profile.storage import save_profile
from tpet.renderer.display import build_display_layout

if TYPE_CHECKING:
    from concurrent.futures import Future

    from tpet.config import TpetConfig
    from tpet.models.pet import PetProfile
    from tpet.monitor.parser import SessionEvent

logger = logging.getLogger(__name__)

COMMENT_HISTORY_MAX = 20


def _build_renderer(config: TpetConfig, pet_name: str) -> object:
    """Select and construct the appropriate renderer for the active art mode.

    For pixel/sixel modes, tries in order:
    1. HalfblockRenderer (ANSI halfblock) if PNG files exist
    2. AsciiRenderer fallback

    For macos-desktop mode, strictly requires all 10 PNG frames (0..9) and
    errors out early if any are missing — unlike the other modes it does not
    fall back.

    Args:
        config: Application configuration.
        pet_name: Name of the pet (used to check for existing art files).

    Returns:
        A renderer instance implementing the ``Renderer`` protocol.
    """
    from tpet.renderer.protocol import AsciiRenderer, HalfblockRenderer

    if config.art_mode == ArtMode.MACOS_DESKTOP:
        from tpet.art.storage import missing_macos_desktop_frames
        from tpet.renderer.macos_desktop import MacosDesktopRenderer

        missing = missing_macos_desktop_frames(config.pet_data_dir, pet_name)
        if missing:
            art_dir = config.pet_data_dir / "art"
            indices = ", ".join(str(i) for i in missing)
            raise SystemExit(
                f"macos-desktop art mode requires all 10 sprite frames (0..9) for "
                f"{pet_name}. Missing frame(s): {indices}. Expected files in "
                f"{art_dir}/ matching `{pet_name}_frame_<N>.png`. "
                f"Run `tpet art` to regenerate, or drop the files in manually."
            )
        logger.info("macos-desktop renderer selected for %s", pet_name)
        return MacosDesktopRenderer(config)

    if config.art_mode == ArtMode.SIXEL_ART:
        data_dir = config.pet_data_dir

        # Try halfblock (PNG or .hblk files)
        from tpet.art.storage import has_halfblock_art, has_png_frames

        if has_png_frames(data_dir, pet_name) or has_halfblock_art(data_dir, pet_name):
            logger.info("Half-block renderer selected for %s", pet_name)
            return HalfblockRenderer(config)

        logger.info("No graphical art for %s, falling back to ASCII", pet_name)

    return AsciiRenderer(config)


def run_app(
    config: TpetConfig,
    pet: PetProfile,
    profile_path: Path,
    project_path: str,
    watch_dir: str | None = None,
    follow_file: str | None = None,
    quiet: bool = False,
) -> None:
    """Run the main tpet application loop.

    Args:
        config: Application configuration.
        pet: Loaded pet profile.
        profile_path: Path to save profile updates.
        project_path: Project directory being monitored.
        watch_dir: Optional override for session watch directory.
        follow_file: Optional path to a plain text file to follow instead of Claude sessions.
        quiet: If True, suppress informational messages printed before the live display starts.
    """
    console = Console()

    # Initialize watcher
    event_queue: Queue[SessionEvent] = Queue()
    watcher: SessionWatcher | TextFileWatcher

    if follow_file:
        file_path = Path(follow_file)
        if not quiet:
            console.print(f"[cyan]Following text file: {file_path}[/cyan]")
        watcher = TextFileWatcher(file_path=file_path, event_queue=event_queue)
    else:
        if watch_dir:
            session_dir = Path(watch_dir)
        else:
            claude_dir = Path.home() / ".claude" / "projects"
            encoded = encode_project_path(project_path)
            session_dir = claude_dir / encoded

        if not session_dir.exists():
            logger.warning("Session directory not found: %s", session_dir)
            if not quiet:
                console.print(f"[yellow]Session directory not found: {session_dir}[/yellow]")
                console.print("[dim]Will watch for it to appear...[/dim]")

        watcher = SessionWatcher(session_dir=session_dir, event_queue=event_queue)

    # Determine frame count: use PNG frame count if available, else ASCII art count
    from tpet.art.storage import get_frame_count_png

    png_frame_count = get_frame_count_png(config.pet_data_dir, pet.name)
    frame_count = png_frame_count if png_frame_count > 0 else len(pet.ascii_art)

    animator = PetAnimator(
        frame_count=frame_count,
        idle_duration=config.idle_duration_seconds,
        reaction_duration=config.reaction_duration_seconds,
        sleep_threshold=config.sleep_threshold_seconds,
    )

    # Select renderer (detects terminal capabilities)
    renderer = _build_renderer(config, pet.name)

    # Commentary state
    current_comment = pet.last_comment
    comment_count = 0
    last_comment_time = 0.0
    last_idle_time = time.monotonic()
    last_user_event: SessionEvent | None = None
    # Pending background LLM futures — at most one in-flight per type
    _pending_comment: Future[str | None] | None = None
    _pending_idle: Future[str | None] | None = None

    # Display change tracking
    last_rendered_frame = -1
    last_rendered_comment: str | None = None

    watcher.start()

    try:
        with Live(
            build_display_layout(pet, animator.current_frame, current_comment),
            console=console,
            auto_refresh=False,
            transient=False,
        ) as live:
            while True:
                # --- Harvest completed comment future ---
                if _pending_comment is not None and _pending_comment.done():
                    try:
                        comment = _pending_comment.result()
                        if comment:
                            current_comment = comment
                            _record_comment(pet, comment)
                            comment_count += 1
                            last_comment_time = time.monotonic()
                            logger.info("Comment: %s", comment)
                    except RuntimeError:
                        logger.exception("Comment future raised an exception")
                    _pending_comment = None

                # --- Harvest completed idle-chatter future ---
                if _pending_idle is not None and _pending_idle.done():
                    try:
                        idle_text = _pending_idle.result()
                        if idle_text:
                            current_comment = idle_text
                            _record_comment(pet, idle_text)
                            comment_count += 1
                    except RuntimeError:
                        logger.exception("Idle chatter future raised an exception")
                    _pending_idle = None
                    last_idle_time = time.monotonic()

                # --- Process one pending session event (non-blocking) ---
                try:
                    event = event_queue.get_nowait()
                    now = time.monotonic()
                    animator.react()

                    if event.role == "user":
                        last_user_event = event

                    if (
                        _pending_comment is None
                        and now - last_comment_time >= config.comment_interval_seconds
                        and _within_comment_budget(config, comment_count)
                    ):
                        _pending_comment = submit_comment(
                            pet,
                            event,
                            config=config,
                            max_length=config.max_comment_length,
                            last_user_event=last_user_event,
                        )
                except Empty:
                    pass

                # --- Submit idle chatter if due ---
                now = time.monotonic()
                if (
                    _pending_idle is None
                    and now - last_idle_time >= config.idle_chatter_interval_seconds
                    and _within_comment_budget(config, comment_count)
                ):
                    _pending_idle = submit_idle_chatter(pet, config=config, max_length=config.max_idle_length)
                    last_idle_time = now  # prevent re-submitting while in flight

                # --- Advance animation state ---
                animator.tick()

                # --- Delegate rendering to the selected renderer ---
                frame_idx = animator.current_frame
                frame_changed = frame_idx != last_rendered_frame
                comment_changed = current_comment != last_rendered_comment

                renderer.render(  # type: ignore[union-attr]
                    live,
                    pet,
                    frame_idx,
                    current_comment,
                    frame_changed,
                    comment_changed,
                )

                # Update change-tracking cursors
                if frame_changed:
                    last_rendered_frame = frame_idx
                if comment_changed:
                    last_rendered_comment = current_comment

                time.sleep(0.25)

    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop()
        save_profile(pet, profile_path)
        close_fn = getattr(renderer, "close", None)
        if callable(close_fn):
            close_fn()
        _print_session_summary(console, comment_count)


def _print_session_summary(console: Console, comment_count: int) -> None:
    """Print token usage and cost summary on exit.

    Args:
        console: Rich console for output.
        comment_count: Number of comments generated this session.
    """
    usage = get_session_usage()
    if usage.api_calls == 0:
        console.print("\n[dim]tpet session ended — no API calls made.[/dim]")
        return

    console.print(
        f"\n[dim]tpet session ended — "
        f"{comment_count} comments, "
        f"{usage.api_calls} API calls, "
        f"{usage.input_tokens:,} in / {usage.output_tokens:,} out tokens[/dim]"
    )


def _within_comment_budget(config: TpetConfig, comment_count: int) -> bool:
    """Return True if another comment may be generated within the configured budget.

    Args:
        config: Application configuration containing max_comments_per_session.
        comment_count: Number of comments generated so far this session.

    Returns:
        True when the session limit is disabled (0) or not yet reached.
    """
    return config.max_comments_per_session == 0 or comment_count < config.max_comments_per_session


def _record_comment(pet: PetProfile, comment: str) -> None:
    """Record a comment in the pet's history.

    Args:
        pet: Pet profile to update.
        comment: Comment text.
    """
    pet.last_comment = comment
    pet.comment_history.append(comment)
    if len(pet.comment_history) > COMMENT_HISTORY_MAX:
        pet.comment_history = pet.comment_history[-COMMENT_HISTORY_MAX:]
