"""Animation state machine and frame cycling."""

import random
import time
from enum import StrEnum


class AnimationState(StrEnum):
    """Pet animation states."""

    IDLE = "idle"
    REACTING = "reacting"
    SLEEPING = "sleeping"


# Supported frame counts
FRAME_COUNT_LEGACY = 4  # 2x2 sprite sheet (idle, idle-shift, react, sleep)
FRAME_COUNT_CURRENT = 6  # 2x3 sprite sheet (adds blink variants)
FRAME_COUNT_MACOS_DESKTOP = 10  # 2x5 sprite sheet — adds walk/fall/stun for desktop use

# 6-frame layout (2x3 sprite sheet):
#   0 = idle pose (eyes open)       1 = idle-shift (eyes open)
#   2 = idle blink (eyes closed)    3 = idle-shift blink (eyes closed)
#   4 = reaction (surprised)        5 = sleeping (zzz)
#
# Legacy 4-frame layout (2x2 sprite sheet):
#   0 = idle    1 = idle-shift    2 = reaction    3 = sleeping
IDLE_FRAMES = (0, 1)
BLINK_FRAMES = (2, 3)
REACTION_FRAME = 4
SLEEP_FRAME = 5

# Legacy frame indices for 4-frame sprite sheets
_REACTION_FRAME_4 = 2
_SLEEP_FRAME_4 = 3

# Probability of blinking per idle frame advance
_BLINK_CHANCE = 0.15

# Maximum duration (seconds) that a blink frame is displayed before
# snapping back to the open-eye idle frame.
_BLINK_DURATION = 0.4


class PetAnimator:
    """Manages pet animation state and frame selection."""

    def __init__(
        self,
        frame_count: int,
        idle_duration: float,
        reaction_duration: float,
        sleep_threshold: int,
    ) -> None:
        self._frame_count = max(frame_count, 1)
        self._idle_duration = idle_duration
        self._reaction_duration = reaction_duration
        self._sleep_threshold = sleep_threshold

        # Detect legacy 4-frame vs new 6-frame layout
        self._has_blink_frames = frame_count >= 6

        self._state = AnimationState.IDLE
        self._current_frame = 0
        self._idle_index = 0
        self._last_frame_time = time.monotonic()
        self._last_activity_time = time.monotonic()
        self._reaction_start: float = 0.0
        self._blink_start: float = 0.0
        self._blinking: bool = False

    @property
    def state(self) -> AnimationState:
        """Current animation state."""
        return self._state

    @property
    def current_frame(self) -> int:
        """Current frame index, clamped to frame_count."""
        return min(self._current_frame, self._frame_count - 1)

    @property
    def frame_count(self) -> int:
        """Total number of frames."""
        return self._frame_count

    def react(self) -> None:
        """Trigger a reaction animation."""
        self._state = AnimationState.REACTING
        self._reaction_start = time.monotonic()
        self._last_activity_time = time.monotonic()
        reaction = REACTION_FRAME if self._has_blink_frames else _REACTION_FRAME_4
        self._current_frame = min(reaction, self._frame_count - 1)

    def tick(self) -> None:
        """Advance animation state. Call periodically."""
        now = time.monotonic()

        if self._state == AnimationState.REACTING:
            if now - self._reaction_start >= self._reaction_duration:
                self._state = AnimationState.IDLE
                self._current_frame = 0
                self._last_frame_time = now
            return

        idle_seconds = now - self._last_activity_time
        sleep = SLEEP_FRAME if self._has_blink_frames else _SLEEP_FRAME_4
        if idle_seconds >= self._sleep_threshold:
            self._state = AnimationState.SLEEPING
            self._current_frame = min(sleep, self._frame_count - 1)
            return

        self._state = AnimationState.IDLE

        # End blink after its short duration — snap back to open-eye frame
        if self._blinking and now - self._blink_start >= _BLINK_DURATION:
            self._blinking = False
            self._current_frame = min(IDLE_FRAMES[self._idle_index], self._frame_count - 1)

        if now - self._last_frame_time >= self._idle_duration:
            self._idle_index = (self._idle_index + 1) % len(IDLE_FRAMES)
            base_frame = IDLE_FRAMES[self._idle_index]

            # Random blink: briefly show closed-eye frame
            if self._has_blink_frames and random.random() < _BLINK_CHANCE:
                base_frame = BLINK_FRAMES[self._idle_index]
                self._blinking = True
                self._blink_start = now

            self._current_frame = min(base_frame, self._frame_count - 1)
            self._last_frame_time = now
