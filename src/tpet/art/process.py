"""Image processing pipeline for sixel art generation."""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from tpet.animation.engine import FRAME_COUNT_CURRENT, FRAME_COUNT_LEGACY, FRAME_COUNT_MACOS_DESKTOP

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Chroma key background color (magenta — safest for game sprites)
CHROMA_BG = (255, 0, 255)

# Frame layout in 2x3 sprite sheet (FRAME_COUNT_CURRENT frames)
_FRAME_POSITIONS = [
    (0.0, 0.0, 0.5, 1 / 3),  # Frame 0: idle (open eyes)
    (0.5, 0.0, 1.0, 1 / 3),  # Frame 1: idle-shift (open eyes)
    (0.0, 1 / 3, 0.5, 2 / 3),  # Frame 2: idle blink (eyes closed)
    (0.5, 1 / 3, 1.0, 2 / 3),  # Frame 3: idle-shift blink (eyes closed)
    (0.0, 2 / 3, 0.5, 1.0),  # Frame 4: react (surprised)
    (0.5, 2 / 3, 1.0, 1.0),  # Frame 5: sleep (zzz)
]
assert len(_FRAME_POSITIONS) == FRAME_COUNT_CURRENT  # noqa: S101

# Legacy 2x2 layout for backward compat with old sprite sheets (FRAME_COUNT_LEGACY frames)
_FRAME_POSITIONS_2x2 = [
    (0.0, 0.0, 0.5, 0.5),  # Frame 0: top-left (idle)
    (0.5, 0.0, 1.0, 0.5),  # Frame 1: top-right (idle-shift)
    (0.0, 0.5, 0.5, 1.0),  # Frame 2: bottom-left (react)
    (0.5, 0.5, 1.0, 1.0),  # Frame 3: bottom-right (sleep)
]
assert len(_FRAME_POSITIONS_2x2) == FRAME_COUNT_LEGACY  # noqa: S101

# 2x5 layout for macos-desktop mode (10 frames: idle x2, blink x2, excited, sleep,
# walk x2, fall, stunned). Portrait sheet, 2 cols x 5 rows.
_FRAME_POSITIONS_2x5 = [
    (0.0, 0.0, 0.5, 0.2),  # Frame 0: idle A
    (0.5, 0.0, 1.0, 0.2),  # Frame 1: idle B (shift)
    (0.0, 0.2, 0.5, 0.4),  # Frame 2: blink A
    (0.5, 0.2, 1.0, 0.4),  # Frame 3: blink B
    (0.0, 0.4, 0.5, 0.6),  # Frame 4: excited / surprised
    (0.5, 0.4, 1.0, 0.6),  # Frame 5: sleeping
    (0.0, 0.6, 0.5, 0.8),  # Frame 6: walk stride A
    (0.5, 0.6, 1.0, 0.8),  # Frame 7: walk stride B
    (0.0, 0.8, 0.5, 1.0),  # Frame 8: falling
    (0.5, 0.8, 1.0, 1.0),  # Frame 9: stunned
]
assert len(_FRAME_POSITIONS_2x5) == FRAME_COUNT_MACOS_DESKTOP  # noqa: S101


def _detect_bg_color(
    image: Image.Image,
    target_color: tuple[int, int, int] | None = None,
    target_radius: int = 120,
) -> tuple[int, int, int]:
    """Detect the background color by sampling the image border.

    Finds the most common color cluster in a 2px border strip.
    Quantizes to 16-step bins to cluster similar colors together.

    Args:
        image: Input PIL Image.
        target_color: If provided, bias detection toward border pixels close
            to this color (within ``target_radius``) and average them. Useful
            when the prompt requested a known chroma color but the model
            produced a slightly off variant. Falls back to dominant-cluster
            detection if no border pixels are close to the target.
        target_radius: Max Euclidean distance from ``target_color`` for a
            border pixel to count as a target match.

    Returns:
        Detected RGB background color.
    """
    from collections import Counter

    img = image.convert("RGB")
    arr = np.array(img)

    # Sample 2px border from all edges
    border_pixels = np.concatenate(
        [
            arr[:2, :, :].reshape(-1, 3),
            arr[-2:, :, :].reshape(-1, 3),
            arr[:, :2, :].reshape(-1, 3),
            arr[:, -2:, :].reshape(-1, 3),
        ]
    )

    if target_color is not None:
        target = np.array(target_color, dtype=np.float64)
        dists = np.sqrt(np.sum((border_pixels.astype(np.float64) - target) ** 2, axis=1))
        close_mask = dists <= target_radius
        # Require at least 1% of border pixels to match — otherwise the target
        # is wrong (e.g. wrong prompt) and we should fall through.
        if close_mask.sum() >= max(10, int(border_pixels.shape[0] * 0.01)):
            avg = border_pixels[close_mask].astype(np.float64).mean(axis=0)
            return (int(avg[0]), int(avg[1]), int(avg[2]))

    # Quantize to 16-step bins and find the dominant cluster
    quantized = (border_pixels.astype(np.int32) // 16 * 16).astype(np.uint8)
    tuples = [tuple(p) for p in quantized]
    dominant_bin = Counter(tuples).most_common(1)[0][0]

    # Average actual pixels in the dominant cluster
    mask = np.all(quantized == np.array(dominant_bin), axis=1)
    bg_pixels = border_pixels[mask].astype(np.float64)
    avg = bg_pixels.mean(axis=0)
    return (int(avg[0]), int(avg[1]), int(avg[2]))


def remove_chroma_key(
    image: Image.Image,
    bg_color: tuple[int, int, int] | None = None,
    tolerance: int = 80,
    target_color: tuple[int, int, int] | None = None,
) -> Image.Image:
    """Remove background using flood-fill from image edges.

    Instead of removing all pixels matching a color globally (which can
    eat into the subject), uses flood-fill from the border: only pixels
    connected to the edge through other background-matching pixels are
    removed. This preserves interior pixels that are part of the subject.

    Args:
        image: Input PIL Image (any mode, converted to RGBA).
        bg_color: RGB color to remove. When None, detect from the image
            border (optionally biased by ``target_color``).
        tolerance: Maximum Euclidean color distance for background removal.
        target_color: Hint passed to the auto-detector — biases detection
            toward border pixels close to this color (e.g. ``(255, 0, 255)``
            for the magenta chroma key Gemini is asked to produce). Avoids
            picking up the character's outline when it touches a cell edge.

    Returns:
        RGBA image with background pixels set to transparent.
    """
    detected_bg = bg_color or _detect_bg_color(image, target_color=target_color)
    logger.debug("Chroma key bg_color=%s (detected=%s)", detected_bg, bg_color is None)

    img = image.convert("RGBA")
    arr = np.array(img)
    h, w = arr.shape[:2]

    bg = np.array(detected_bg, dtype=np.float64)

    # Compute distance of every pixel from background color
    dist = np.sqrt(np.sum((arr[:, :, :3].astype(np.float64) - bg) ** 2, axis=2))

    # Flood fill from edges: background = within tolerance AND connected to border
    is_bg_local = dist <= tolerance
    visited = np.zeros((h, w), dtype=bool)
    background = np.zeros((h, w), dtype=bool)
    bfs_queue: deque[tuple[int, int]] = deque()

    # Seed with all edge pixels that match background
    for y in range(h):
        for x in (0, w - 1):
            if is_bg_local[y, x] and not visited[y, x]:
                visited[y, x] = True
                background[y, x] = True
                bfs_queue.append((y, x))
    for x in range(w):
        for y in (0, h - 1):
            if is_bg_local[y, x] and not visited[y, x]:
                visited[y, x] = True
                background[y, x] = True
                bfs_queue.append((y, x))

    # BFS flood fill
    while bfs_queue:
        cy, cx = bfs_queue.popleft()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            ny, nx = cy + dy, cx + dx
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and is_bg_local[ny, nx]:
                visited[ny, nx] = True
                background[ny, nx] = True
                bfs_queue.append((ny, nx))

    # Edge erosion: remove anti-aliased fringe pixels adjacent to background.
    # These pixels weren't caught by flood-fill (too far from bg color) but
    # are contaminated with background color from LLM anti-aliasing.
    alpha = np.ones((h, w), dtype=np.float64)
    alpha[background] = 0.0

    for _ in range(2):
        # Find pixels that neighbor a transparent pixel AND are within
        # a relaxed tolerance (1.5x) of the background color
        padded = np.pad(alpha, 1, constant_values=1.0)
        has_transparent_neighbor = (
            (padded[:-2, 1:-1] == 0) | (padded[2:, 1:-1] == 0) | (padded[1:-1, :-2] == 0) | (padded[1:-1, 2:] == 0)
        )
        edge_mask = has_transparent_neighbor & (~background) & (dist <= tolerance * 1.5)
        alpha[edge_mask] = 0.0
        background = background | edge_mask

    # Set alpha to 0 for all removed pixels
    result = arr.copy()
    result[background, 3] = 0
    return Image.fromarray(result, mode="RGBA")


def create_blink_frame(
    idle_frame: Image.Image,
    sleep_frame: Image.Image,
    face_region_pct: float = 0.55,
) -> Image.Image:
    """Create a blink frame by transplanting closed-eye pixels from the sleep frame onto the idle frame.

    Algorithm:
    1. Find the bounding box of the opaque character in both frames.
    2. Restrict changes to the upper-center "face region" of the character
       (top portion, horizontally centered) to avoid copying zzz bubbles or
       body changes from the sleep frame.
    3. Within that region, find pixels that differ significantly between idle
       and sleep frames and copy only those from the sleep frame.

    Args:
        idle_frame: The idle pose frame (eyes open).
        sleep_frame: The sleep frame (eyes closed + zzz).
        face_region_pct: Fraction of the character bounding box height
            (from the top) to consider as the face region. Default 0.55.

    Returns:
        New RGBA image with the idle body and closed eyes from the sleep frame.
    """
    idle = idle_frame.convert("RGBA")
    sleep = sleep_frame.convert("RGBA")

    idle_arr = np.array(idle)
    sleep_arr = np.array(sleep)

    # Ensure same size — resize sleep to match idle if needed
    if idle_arr.shape != sleep_arr.shape:
        sleep = sleep.resize((idle.width, idle.height), Image.Resampling.LANCZOS)
        sleep_arr = np.array(sleep)

    result = idle_arr.copy()
    h, w = idle_arr.shape[:2]

    # Find character bounding box from idle frame (opaque pixels)
    idle_alpha = idle_arr[:, :, 3]
    opaque_mask = idle_alpha > 128
    if not opaque_mask.any():
        return idle  # No visible pixels — return as-is

    rows_with_content = np.where(opaque_mask.any(axis=1))[0]
    cols_with_content = np.where(opaque_mask.any(axis=0))[0]
    char_top = int(rows_with_content[0])
    char_bottom = int(rows_with_content[-1])
    char_left = int(cols_with_content[0])
    char_right = int(cols_with_content[-1])

    char_h = char_bottom - char_top
    char_w = char_right - char_left

    # Define the face region: upper portion, horizontally centered
    face_top = char_top
    face_bottom = char_top + int(char_h * face_region_pct)
    # Narrow horizontally to the central 70% to exclude zzz bubbles on edges
    face_margin = int(char_w * 0.15)
    face_left = char_left + face_margin
    face_right = char_right - face_margin

    # Clamp
    face_top = max(0, face_top)
    face_bottom = min(h, face_bottom)
    face_left = max(0, face_left)
    face_right = min(w, face_right)

    # Find pixels in the face region that differ between idle and sleep
    face_idle = idle_arr[face_top:face_bottom, face_left:face_right, :3].astype(np.float64)
    face_sleep = sleep_arr[face_top:face_bottom, face_left:face_right, :3].astype(np.float64)
    diff = np.sqrt(np.sum((face_idle - face_sleep) ** 2, axis=2))

    # Only transplant pixels with significant color difference (eye area)
    # AND where the sleep frame has opaque content
    sleep_alpha_face = sleep_arr[face_top:face_bottom, face_left:face_right, 3]
    change_mask = (diff > 30) & (sleep_alpha_face > 128)

    # Apply changes from sleep frame to result within face region
    face_region = result[face_top:face_bottom, face_left:face_right]
    sleep_face_region = sleep_arr[face_top:face_bottom, face_left:face_right]
    face_region[change_mask] = sleep_face_region[change_mask]
    result[face_top:face_bottom, face_left:face_right] = face_region

    return Image.fromarray(result, mode="RGBA")


def split_sprite_sheet(
    image: Image.Image,
    layout: str | None = None,
    inset_px: int = 0,
) -> list[Image.Image]:
    """Split a sprite sheet into individual frames.

    Auto-detects 2x3 (6-frame) or 2x2 (4-frame) layout based on aspect ratio,
    or uses an explicit ``layout`` override.

    Args:
        image: Input sprite sheet image.
        layout: Optional explicit layout (``"2x5"``, ``"2x3"``, ``"2x2"``).
            When set, skips aspect-ratio detection. Required for 2x5 sheets
            generated at 9:16 (Gemini's tallest portrait), since their aspect
            ratio is not extreme enough to be auto-detected.
        inset_px: Pixels to shrink each cell on every side. Used to skip
            thin grid/border lines the model occasionally draws between
            cells. The shrink is symmetric per side, so character placement
            remains consistent across frames.

    Returns:
        List of PIL Images. 10 frames for 2x5, 6 frames for 2x3, 4 frames for 2x2.
        Frame order for 2x3: idle, idle-shift, blink, blink-shift, react, sleep.
        Frame order for 2x2: idle, idle-shift, react, sleep.
    """
    img = image.convert("RGBA")
    w, h = img.size
    frames: list[Image.Image] = []

    if layout == "2x5":
        positions = _FRAME_POSITIONS_2x5
    elif layout == "2x3":
        positions = _FRAME_POSITIONS
    elif layout == "2x2":
        positions = _FRAME_POSITIONS_2x2
    elif h >= w * 2.2:
        positions = _FRAME_POSITIONS_2x5
    elif h >= w * 1.3:
        positions = _FRAME_POSITIONS
    else:
        positions = _FRAME_POSITIONS_2x2

    for left_f, top_f, right_f, bottom_f in positions:
        left = int(left_f * w) + inset_px
        top = int(top_f * h) + inset_px
        right = int(right_f * w) - inset_px
        bottom = int(bottom_f * h) - inset_px
        frames.append(img.crop((left, top, right, bottom)))

    return frames


def resize_for_halfblock(
    image: Image.Image,
    target_height: int = 64,
) -> Image.Image:
    """Resize image for half-block terminal display.

    Maintains aspect ratio and ensures height is even
    (each terminal row represents 2 pixel rows).

    Args:
        image: Input PIL Image.
        target_height: Desired height in pixels (rounded to even number).
            Default 64 pixels = 32 terminal rows.

    Returns:
        Resized RGBA image with even height.
    """
    img = image.convert("RGBA")

    # Snap target height to nearest even number
    h_even = max(2, (target_height // 2) * 2)

    # Calculate width maintaining aspect ratio
    w, h = img.size
    new_w = max(1, int(w * h_even / h))

    return img.resize((new_w, h_even), Image.Resampling.LANCZOS)


def image_to_halfblock(
    image: Image.Image,
    default_bg: tuple[int, int, int] | None = None,
) -> str:
    """Convert a PIL Image to a half-block rendered string.

    Uses the upper half-block character (U+2580) with 24-bit ANSI
    color escapes. Each terminal cell encodes 2 vertical pixels:
    the foreground color is the top pixel, the background color is
    the bottom pixel.

    Args:
        image: Input PIL Image (RGBA recommended for transparency).
        default_bg: RGB color for transparent pixels, or None to use
            terminal default background (\\x1b[49m).

    Returns:
        String of half-block rendered lines separated by newlines.
    """
    img = image.convert("RGBA")
    arr = np.array(img)
    h, w = arr.shape[:2]

    # Ensure even height
    if h % 2 != 0:
        # Pad with transparent row
        pad = np.zeros((1, w, 4), dtype=np.uint8)
        arr = np.concatenate([arr, pad], axis=0)
        h += 1

    lines: list[str] = []
    for y in range(0, h, 2):
        parts: list[str] = []
        for x in range(w):
            top_r, top_g, top_b, top_a = arr[y, x]
            bot_r, bot_g, bot_b, bot_a = arr[y + 1, x]

            top_opaque = top_a > 128
            bot_opaque = bot_a > 128

            if not top_opaque and not bot_opaque:
                # Both transparent — space with default or reset
                if default_bg is not None:
                    parts.append(f"\x1b[48;2;{default_bg[0]};{default_bg[1]};{default_bg[2]}m ")
                else:
                    parts.append("\x1b[0m ")
            elif top_opaque and not bot_opaque:
                # Top visible, bottom transparent — use ▀ with fg only
                if default_bg is not None:
                    parts.append(
                        f"\x1b[38;2;{top_r};{top_g};{top_b}m\x1b[48;2;{default_bg[0]};{default_bg[1]};{default_bg[2]}m\u2580"
                    )
                else:
                    parts.append(f"\x1b[38;2;{top_r};{top_g};{top_b}m\x1b[49m\u2580")
            elif not top_opaque and bot_opaque:
                # Bottom visible, top transparent — use ▄ with fg only
                if default_bg is not None:
                    parts.append(
                        f"\x1b[38;2;{bot_r};{bot_g};{bot_b}m\x1b[48;2;{default_bg[0]};{default_bg[1]};{default_bg[2]}m\u2584"
                    )
                else:
                    parts.append(f"\x1b[38;2;{bot_r};{bot_g};{bot_b}m\x1b[49m\u2584")
            else:
                # Both visible — ▀ with fg=top, bg=bottom
                parts.append(f"\x1b[38;2;{top_r};{top_g};{top_b}m\x1b[48;2;{bot_r};{bot_g};{bot_b}m\u2580")

        lines.append("".join(parts) + "\x1b[0m")

    return "\n".join(lines)


def _autocrop(image: Image.Image, padding: int = 2) -> Image.Image:
    """Crop an RGBA image to the bounding box of its opaque pixels.

    Removes transparent padding around the character so it fills the
    available display space without wasted empty margins.

    Args:
        image: Input RGBA image.
        padding: Extra pixels to keep around the bounding box.

    Returns:
        Cropped RGBA image, or the original if fully transparent.
    """
    arr = np.array(image)
    alpha = arr[:, :, 3]
    opaque = alpha > 32  # threshold above near-transparent noise

    if not opaque.any():
        return image

    rows = np.where(opaque.any(axis=1))[0]
    cols = np.where(opaque.any(axis=0))[0]
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(cols[0]), int(cols[-1]) + 1

    # Add padding, clamped to image bounds
    h, w = arr.shape[:2]
    top = max(0, top - padding)
    bottom = min(h, bottom + padding)
    left = max(0, left - padding)
    right = min(w, right + padding)

    return image.crop((left, top, right, bottom))


def crop_frames_to_common_bbox(
    frames: list[Image.Image],
    padding: int = 2,
) -> list[Image.Image]:
    """Crop a list of frames to a single shared bounding box.

    For each frame, compute the opaque bbox (alpha > 32); take the union
    across all frames (min-left, min-top, max-right, max-bottom); crop
    every frame to that shared box. All returned frames have identical
    dimensions, preserving per-frame character position.

    Args:
        frames: List of RGBA PIL images (typically all the same source size).
        padding: Extra pixels to keep around the union bbox.

    Returns:
        List of cropped frames, all with identical dimensions. Returns the
        input unchanged if no frame contains any opaque pixel.
    """
    if not frames:
        return frames

    lefts: list[int] = []
    tops: list[int] = []
    rights: list[int] = []
    bottoms: list[int] = []
    canvas_w = 0
    canvas_h = 0

    for frame in frames:
        rgba = frame.convert("RGBA")
        arr = np.array(rgba)
        h, w = arr.shape[:2]
        canvas_w = max(canvas_w, w)
        canvas_h = max(canvas_h, h)
        opaque = arr[:, :, 3] > 32
        if not opaque.any():
            continue
        rows = np.where(opaque.any(axis=1))[0]
        cols = np.where(opaque.any(axis=0))[0]
        tops.append(int(rows[0]))
        bottoms.append(int(rows[-1]) + 1)
        lefts.append(int(cols[0]))
        rights.append(int(cols[-1]) + 1)

    if not lefts:
        return frames

    left = max(0, min(lefts) - padding)
    top = max(0, min(tops) - padding)
    right = min(canvas_w, max(rights) + padding)
    bottom = min(canvas_h, max(bottoms) + padding)

    return [frame.convert("RGBA").crop((left, top, right, bottom)) for frame in frames]


def render_halfblock_from_png(
    png_path: Path,
    max_terminal_rows: int,
    max_terminal_cols: int,
) -> str:
    """Load a PNG frame and render it as half-block art sized to terminal constraints.

    Shared runtime utility used by the display layer to scale art to fit
    alongside the speech bubble without exceeding the available space.
    PNGs on disk are pre-cropped to the shared union bbox at generation
    time, so no per-frame autocrop is applied here (that would break the
    consistent positioning across frames).

    Args:
        png_path: Path to the raw PNG frame (full resolution).
        max_terminal_rows: Max terminal rows available for the art.
        max_terminal_cols: Max terminal columns available for the art.

    Returns:
        Half-block ANSI string sized to fit within the given constraints.
    """
    image = Image.open(png_path).convert("RGBA")

    # Each terminal row = 2 pixel rows
    target_pixel_h = max(4, max_terminal_rows * 2)

    # Calculate width maintaining aspect ratio, capped by max cols
    w, h = image.size
    aspect = w / h
    target_pixel_w = int(target_pixel_h * aspect)
    target_pixel_w = min(target_pixel_w, max_terminal_cols)
    target_pixel_w = max(1, target_pixel_w)

    # Ensure even pixel height
    target_pixel_h = max(2, (target_pixel_h // 2) * 2)

    img = image.resize((target_pixel_w, target_pixel_h), Image.Resampling.LANCZOS)

    return image_to_halfblock(img)
