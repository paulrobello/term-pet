"""Tests for the Rarity model."""

from tpet.models.rarity import Rarity, pick_rarity


class TestRarity:
    """Tests for Rarity enum."""

    def test_rarity_values(self) -> None:
        assert Rarity.COMMON.value == "COMMON"
        assert Rarity.UNCOMMON.value == "UNCOMMON"
        assert Rarity.RARE.value == "RARE"
        assert Rarity.EPIC.value == "EPIC"
        assert Rarity.LEGENDARY.value == "LEGENDARY"

    def test_stars(self) -> None:
        assert Rarity.COMMON.stars == "\u2605"
        assert Rarity.UNCOMMON.stars == "\u2605\u2605"
        assert Rarity.RARE.stars == "\u2605\u2605\u2605"
        assert Rarity.EPIC.stars == "\u2605\u2605\u2605\u2605"
        assert Rarity.LEGENDARY.stars == "\u2605\u2605\u2605\u2605\u2605"

    def test_color(self) -> None:
        assert Rarity.COMMON.color == "dim"
        assert Rarity.UNCOMMON.color == "green"
        assert Rarity.RARE.color == "yellow"
        assert Rarity.EPIC.color == "medium_purple1"
        assert Rarity.LEGENDARY.color == "bright_magenta"

    def test_stat_range(self) -> None:
        assert Rarity.COMMON.stat_range == (20, 60)
        assert Rarity.UNCOMMON.stat_range == (40, 75)
        assert Rarity.RARE.stat_range == (60, 90)
        assert Rarity.EPIC.stat_range == (70, 95)
        assert Rarity.LEGENDARY.stat_range == (80, 99)


class TestPickRarity:
    """Tests for weighted rarity selection."""

    def test_returns_valid_rarity(self) -> None:
        result = pick_rarity()
        assert isinstance(result, Rarity)

    def test_custom_weights(self) -> None:
        weights = {
            Rarity.LEGENDARY: 100,
            Rarity.COMMON: 0,
            Rarity.UNCOMMON: 0,
            Rarity.RARE: 0,
            Rarity.EPIC: 0,
        }
        for _ in range(10):
            assert pick_rarity(weights) == Rarity.LEGENDARY

    def test_default_weights_distribution(self) -> None:
        counts: dict[Rarity, int] = {r: 0 for r in Rarity}
        for _ in range(1000):
            counts[pick_rarity()] += 1
        assert counts[Rarity.COMMON] > counts[Rarity.LEGENDARY]
