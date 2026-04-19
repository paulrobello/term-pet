"""Rarity enum with weighted random selection."""

import random
from enum import StrEnum

DEFAULT_RARITY_WEIGHTS: dict["Rarity", float] = {}


class Rarity(StrEnum):
    """Pet rarity levels with associated display properties."""

    COMMON = "COMMON"
    UNCOMMON = "UNCOMMON"
    RARE = "RARE"
    EPIC = "EPIC"
    LEGENDARY = "LEGENDARY"

    @property
    def stars(self) -> str:
        """Return star string for this rarity."""
        star_map: dict[Rarity, str] = {
            Rarity.COMMON: "\u2605",
            Rarity.UNCOMMON: "\u2605\u2605",
            Rarity.RARE: "\u2605\u2605\u2605",
            Rarity.EPIC: "\u2605\u2605\u2605\u2605",
            Rarity.LEGENDARY: "\u2605\u2605\u2605\u2605\u2605",
        }
        return star_map[self]

    @property
    def color(self) -> str:
        """Return Rich color name for this rarity."""
        color_map: dict[Rarity, str] = {
            Rarity.COMMON: "dim",
            Rarity.UNCOMMON: "green",
            Rarity.RARE: "yellow",
            Rarity.EPIC: "medium_purple1",
            Rarity.LEGENDARY: "bright_magenta",
        }
        return color_map[self]

    @property
    def stat_range(self) -> tuple[int, int]:
        """Return (min, max) stat range for this rarity."""
        range_map: dict[Rarity, tuple[int, int]] = {
            Rarity.COMMON: (20, 60),
            Rarity.UNCOMMON: (40, 75),
            Rarity.RARE: (60, 90),
            Rarity.EPIC: (70, 95),
            Rarity.LEGENDARY: (80, 99),
        }
        return range_map[self]


DEFAULT_RARITY_WEIGHTS.update(
    {
        Rarity.COMMON: 60,
        Rarity.UNCOMMON: 25,
        Rarity.RARE: 10,
        Rarity.EPIC: 3,
        Rarity.LEGENDARY: 2,
    }
)


def pick_rarity(weights: dict[Rarity, float] | None = None) -> Rarity:
    """Pick a rarity using weighted random selection.

    Args:
        weights: Optional custom weights. Defaults to standard distribution.

    Returns:
        A randomly selected Rarity.
    """
    w = weights or DEFAULT_RARITY_WEIGHTS
    rarities = list(w.keys())
    weight_values = list(w.values())
    return random.choices(rarities, weights=weight_values, k=1)[0]
