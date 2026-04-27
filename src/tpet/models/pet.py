"""Pet profile data model."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from tpet.models.rarity import Rarity


class PetProfile(BaseModel):
    """Complete pet profile with personality, stats, and art."""

    name: str = Field(description="Generated creature name.")
    creature_type: str = Field(description="Species or creature type (e.g. axolotl, phoenix).")
    rarity: Rarity = Field(description="Rarity tier: Common, Uncommon, Rare, Epic, or Legendary.")
    personality: str = Field(description="2-3 sentence personality summary used in commentary prompts.")
    backstory: str = Field(description="3-5 sentence origin story displayed in --details --backstory mode.")
    ascii_art: list[str] = Field(
        description=(
            "Animation frames as ASCII strings (4 or 6 frames: idle, shift, react, sleep, and optional blink variants)."
        )
    )
    stats: dict[str, int] = Field(
        description="Stat name to integer value mapping (e.g. HUMOR: 72). Values are clamped to the rarity range."
    )
    accent_color: str = Field(description="Rich color name used for the art panel border (e.g. 'bright_cyan').")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="UTC timestamp of pet creation."
    )
    project_path: str | None = Field(
        default=None, description="Absolute project path for project-specific pets, or None for the global pet."
    )
    last_comment: str | None = Field(default=None, description="Most recent comment text, persisted across sessions.")
    comment_history: list[str] = Field(
        default_factory=list, description="Rolling history of recent comments (max 20 entries)."
    )
    body_plan: str = Field(
        default="",
        description=(
            "Anatomical description of the creature for image generation (limbs, wings, body shape, "
            "locomotion mode). Generated lazily by the LLM if missing. Empty for legacy profiles."
        ),
    )
    walk_description: str = Field(
        default="",
        description=(
            "Two-stride walk-cycle description used in the macos-desktop sprite prompt. "
            "Should reflect the creature's locomotion (4-leg trot, 6-leg scuttle, slither, hover, etc.)."
        ),
    )
    fall_description: str = Field(
        default="",
        description=(
            "Description of how the creature falls (glides if winged, drifts if floaty, plummets otherwise) "
            "used in the macos-desktop sprite prompt."
        ),
    )
    landing_description: str = Field(
        default="",
        description=(
            "Description of how the creature lands (soft-touch for gliders/floaters, "
            "hard impact reaction otherwise — splat, shatter, dent, dazed) used in the macos-desktop sprite prompt."
        ),
    )
