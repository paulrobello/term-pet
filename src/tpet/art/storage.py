"""Art file storage for PNG and half-block frames."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from tpet.animation.engine import FRAME_COUNT_CURRENT, FRAME_COUNT_LEGACY, FRAME_COUNT_MACOS_DESKTOP

if TYPE_CHECKING:
    from pathlib import Path

    from PIL import Image

logger = logging.getLogger(__name__)


def get_art_dir(config_dir: Path) -> Path:
    """Get the art directory path, creating it if needed.

    Args:
        config_dir: Base configuration directory.

    Returns:
        Path to the art directory.
    """
    art_dir = config_dir / "art"
    art_dir.mkdir(parents=True, exist_ok=True)
    return art_dir


def sanitize_name(pet_name: str) -> str:
    """Sanitize pet name for use as a filename.

    Replaces non-alphanumeric characters with underscores, strips leading dots
    to prevent hidden-file creation, and falls back to "pet" if the result is
    empty or too short to be meaningful.

    Args:
        pet_name: Raw pet name string.

    Returns:
        Filename-safe string with at least one character.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in pet_name)
    # Strip leading dots (LLM-generated names must not create hidden files)
    safe = safe.lstrip(".")
    # Collapse runs of underscores/hyphens to a single underscore
    safe = re.sub(r"[_\-]{2,}", "_", safe)
    # Ensure the result is at least 1 character long
    return safe if safe else "pet"


# ---------------------------------------------------------------------------
# Generic frame helpers — shared logic for all frame types
# ---------------------------------------------------------------------------


def _get_frame_path(config_dir: Path, pet_name: str, frame_index: int, extension: str) -> Path:
    """Get the file path for a frame of any type.

    Args:
        config_dir: Base configuration directory.
        pet_name: Pet name (used as filename prefix).
        frame_index: Frame index.
        extension: File extension including dot (e.g. ".png", ".hblk").

    Returns:
        Path to the frame file.
    """
    safe_name = sanitize_name(pet_name)
    return get_art_dir(config_dir) / f"{safe_name}_frame_{frame_index}{extension}"


def _save_text_frame(config_dir: Path, pet_name: str, frame_index: int, data: str, extension: str, label: str) -> Path:
    """Save a text-based frame (half-block) to disk.

    Args:
        config_dir: Base configuration directory.
        pet_name: Pet name.
        frame_index: Frame index.
        data: Frame data string.
        extension: File extension including dot.
        label: Human-readable frame type for logging.

    Returns:
        Path to the saved file.
    """
    path = _get_frame_path(config_dir, pet_name, frame_index, extension)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    logger.debug("Saved %s frame %d to %s", label, frame_index, path)
    return path


def _load_text_frame(config_dir: Path, pet_name: str, frame_index: int, extension: str) -> str | None:
    """Load a text-based frame from disk.

    Args:
        config_dir: Base configuration directory.
        pet_name: Pet name.
        frame_index: Frame index.
        extension: File extension including dot.

    Returns:
        Frame data string, or None if file doesn't exist.
    """
    path = _get_frame_path(config_dir, pet_name, frame_index, extension)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _detect_frame_count(config_dir: Path, pet_name: str, extension: str) -> int:
    """Detect how many frames exist for a pet (4 or 6).

    Checks for frame files 0-5. Returns the count found if frames are
    contiguous from 0 (accepts 4 or 6), otherwise 0.

    Args:
        config_dir: Base configuration directory.
        pet_name: Pet name.
        extension: File extension including dot (e.g. ".png", ".hblk").

    Returns:
        6 if all 6 frames exist, 4 if all 4 exist, 0 otherwise.
    """
    safe_name = sanitize_name(pet_name)
    art_dir = get_art_dir(config_dir)
    count = 0
    for i in range(FRAME_COUNT_CURRENT):
        if (art_dir / f"{safe_name}_frame_{i}{extension}").exists():
            count = i + 1
        else:
            break
    return count if count in (FRAME_COUNT_LEGACY, FRAME_COUNT_CURRENT) else 0


def _has_frames(config_dir: Path, pet_name: str, extension: str) -> bool:
    """Check if at least 4 frame files exist for a pet.

    Args:
        config_dir: Base configuration directory.
        pet_name: Pet name.
        extension: File extension including dot.

    Returns:
        True if at least 4 frame files exist.
    """
    return _detect_frame_count(config_dir, pet_name, extension) >= 4


def _delete_frames(config_dir: Path, pet_name: str, extension: str, label: str) -> None:
    """Delete all frame files for a pet of a given type.

    Args:
        config_dir: Base configuration directory.
        pet_name: Pet name.
        extension: File extension including dot.
        label: Human-readable frame type for logging.
    """
    for i in range(FRAME_COUNT_CURRENT):
        path = _get_frame_path(config_dir, pet_name, i, extension)
        if path.exists():
            path.unlink()
            logger.debug("Deleted %s frame %d: %s", label, i, path)


# ---------------------------------------------------------------------------
# Prompt storage — save generation prompt alongside art
# ---------------------------------------------------------------------------


def save_prompt(config_dir: Path, pet_name: str, prompt: str) -> Path:
    """Save the image generation prompt to the art directory.

    Args:
        config_dir: Base configuration directory.
        pet_name: Pet name.
        prompt: The full prompt string used for image generation.

    Returns:
        Path to the saved prompt file.
    """
    safe_name = sanitize_name(pet_name)
    path = get_art_dir(config_dir) / f"{safe_name}_prompt.txt"
    path.write_text(prompt, encoding="utf-8")
    logger.debug("Saved prompt to %s", path)
    return path


def load_prompt(config_dir: Path, pet_name: str) -> str | None:
    """Load the image generation prompt from the art directory.

    Args:
        config_dir: Base configuration directory.
        pet_name: Pet name.

    Returns:
        Prompt string, or None if no prompt file exists.
    """
    safe_name = sanitize_name(pet_name)
    path = get_art_dir(config_dir) / f"{safe_name}_prompt.txt"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Raw PNG frame storage — full-resolution frames for runtime scaling
# ---------------------------------------------------------------------------


def get_png_frame_path(config_dir: Path, pet_name: str, frame_index: int) -> Path:
    """Get the file path for a raw PNG frame."""
    return _get_frame_path(config_dir, pet_name, frame_index, ".png")


def save_png_frame(config_dir: Path, pet_name: str, frame_index: int, image: Image.Image) -> Path:
    """Save a raw PNG frame to disk at full resolution.

    Args:
        config_dir: Base configuration directory.
        pet_name: Pet name.
        frame_index: Frame index.
        image: PIL Image to save.

    Returns:
        Path to the saved PNG file.
    """
    path = get_png_frame_path(config_dir, pet_name, frame_index)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    logger.debug("Saved PNG frame %d to %s (%dx%d)", frame_index, path, image.width, image.height)
    return path


def get_frame_count_png(config_dir: Path, pet_name: str) -> int:
    """Get the number of PNG frames available for a pet.

    Returns:
        6 if all 6 PNG frames exist, 4 for legacy 4-frame, 0 if none.
    """
    return _detect_frame_count(config_dir, pet_name, ".png")


def has_png_frames(config_dir: Path, pet_name: str) -> bool:
    """Check if raw PNG frame files exist for a pet (4 or 6 frames)."""
    return _has_frames(config_dir, pet_name, ".png")


def delete_png_frames(config_dir: Path, pet_name: str) -> None:
    """Delete all raw PNG frame files for a pet."""
    _delete_frames(config_dir, pet_name, ".png", "PNG")


def missing_macos_desktop_frames(config_dir: Path, pet_name: str) -> list[int]:
    """Return the list of frame indices (0..9) that are missing for the macos-desktop art mode.

    Unlike ``_detect_frame_count`` this does not short-circuit on the first gap —
    it reports every missing frame so callers can surface a full list.
    """
    safe_name = sanitize_name(pet_name)
    art_dir = get_art_dir(config_dir)
    return [
        i
        for i in range(FRAME_COUNT_MACOS_DESKTOP)
        if not (art_dir / f"{safe_name}_frame_{i}.png").exists()
    ]


def has_macos_desktop_frames(config_dir: Path, pet_name: str) -> bool:
    """True when all 10 PNG frames required by the macos-desktop art mode are present."""
    return not missing_macos_desktop_frames(config_dir, pet_name)


# ---------------------------------------------------------------------------
# Half-block storage (.hblk) — ANSI truecolor half-block frames
# ---------------------------------------------------------------------------


def get_halfblock_frame_path(config_dir: Path, pet_name: str, frame_index: int) -> Path:
    """Get the file path for a specific half-block frame."""
    return _get_frame_path(config_dir, pet_name, frame_index, ".hblk")


def save_halfblock_frame(config_dir: Path, pet_name: str, frame_index: int, data: str) -> Path:
    """Save a half-block frame to disk."""
    return _save_text_frame(config_dir, pet_name, frame_index, data, ".hblk", "halfblock")


def load_halfblock_frame(config_dir: Path, pet_name: str, frame_index: int) -> str | None:
    """Load a half-block frame from disk."""
    return _load_text_frame(config_dir, pet_name, frame_index, ".hblk")


def has_halfblock_art(config_dir: Path, pet_name: str) -> bool:
    """Check if half-block frame files exist for a pet (4 or 6 frames)."""
    return _has_frames(config_dir, pet_name, ".hblk")


def delete_halfblock_art(config_dir: Path, pet_name: str) -> None:
    """Delete all half-block frame files for a pet."""
    _delete_frames(config_dir, pet_name, ".hblk", "halfblock")
