"""Pet profile generation using configurable LLM providers."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING

from tpet.config import LLMProvider, ResolvedProviderConfig
from tpet.models.pet import PetProfile
from tpet.models.rarity import Rarity
from tpet.models.stats import generate_stats

if TYPE_CHECKING:
    from tpet.config import TpetConfig

logger = logging.getLogger(__name__)

_ART_RULES = (
    "ASCII ART RULES (critical):\n"
    "- Each frame is a multiline string with \\n between lines\n"
    "- All 6 frames MUST have the SAME number of lines (6-8 lines each)\n"
    "- Every line within a frame MUST be the SAME width (12-16 chars), padded with spaces\n"
    "- All 6 frames MUST have the SAME line width — this is essential for animation\n"
    "- COLUMN ALIGNMENT: If you use box-drawing characters, vertical edges (│) MUST appear "
    "in the exact same column on every line of the box. Horizontal borders (┌──┐, ├──┤, └──┘) "
    "MUST all span the same columns with the same number of ─ characters. "
    "Count characters carefully — a misaligned box looks broken in the terminal.\n"
    "- Only use single-cell-width characters. Avoid fullwidth or CJK characters.\n"
    "- The creature should be centered within the frame\n"
    "- Frame 0: default idle pose (eyes open)\n"
    "- Frame 1: idle shift (slight variation of frame 0, eyes open)\n"
    "- Frame 2: blink idle (same as frame 0 but with eyes closed/half-closed)\n"
    "- Frame 3: blink idle shift (same as frame 1 but with eyes closed/half-closed)\n"
    "- Frame 4: surprised/reacting (eyes wide, etc.)\n"
    "- Frame 5: sleeping (eyes closed, zzz)\n\n"
)

_LOCOMOTION_INSTRUCTIONS = (
    "LOCOMOTION FIELDS (used to drive the desktop pet's walk/fall/landing animation): "
    "describe how THIS specific creature moves. Be concrete and species-appropriate — "
    "a 4-legged animal trots, a snake slithers, a centipede ripples its many legs, "
    "a bird flaps and glides, a balloon drifts. Keep each description to 1-3 sentences "
    "and do NOT include cell/frame numbering — the consumer wraps these into a sprite-sheet prompt.\n"
    "- body_plan: anatomy in one sentence (limb count, wings, tail, body shape, etc.).\n"
    "- walk_description: a two-stride walk cycle describing stride A and stride B for THIS creature's "
    "locomotion (4-leg diagonal trot, 6-leg tripod gait, slither S-curve A vs S-curve B, hover bob, etc.). "
    "Always FACING RIGHT. Do not flip or mirror between strides.\n"
    "- fall_description: how it behaves mid-air. If it has wings or is naturally floaty (cloud, balloon, "
    "feather, jellyfish), it GLIDES or DRIFTS calmly. Otherwise it PLUMMETS with limbs splayed and a "
    "startled expression.\n"
    "- landing_description: how it hits the ground. Describe only one frame, the moment of impact."
    "Soft for gliders/floaters (touches down with wings folded / drifts to rest, neutral expression). "
    "Hard for everything else, choosing the reaction that matches the body: splatted into a puddle for squishy/gooey"
    " shattered shards for brittle/crystalline, dented + sparks for metallic/mechanical, "
    "dazed with X-eyes and stars otherwise.\n"
)

_SYSTEM_PROMPT = (
    "You are a creative terminal pet designer. Generate a unique, charming creature "
    "that would live in a developer's terminal. The creature should have a distinct personality "
    "that relates to software development. Use extended unicode characters like braille patterns "
    "(\u2800-\u28ff), half-blocks (\u2580\u2584\u2588), box-drawing (\u2500\u2502\u250c\u2510\u2514\u2518), "
    "and other special characters for the ASCII art.\n\n"
    + _ART_RULES
    + "Make the personality memorable and the art creative.\n\n"
    "STATS: Generate stats that reflect the creature's personality. "
    "Each stat is an integer from 1-100. Choose values that make sense for the character — "
    "a chaotic creature should have high CHAOS, a patient one high PATIENCE, etc.\n"
    "The 5 stats are: HUMOR, PATIENCE, CHAOS, WISDOM, SNARK\n\n"
    + _LOCOMOTION_INSTRUCTIONS
    + "\n"
    "IMPORTANT: Output ONLY a single JSON object with no markdown fencing, no explanation, "
    "and no extra text. The JSON must have these exact keys:\n"
    '- "name": string (creative creature name)\n'
    '- "creature_type": string (species like axolotl, phoenix, goblin)\n'
    '- "personality": string (2-3 sentence personality summary)\n'
    '- "backstory": string (3-5 sentence origin story)\n'
    '- "ascii_art": array of exactly 6 strings (multiline, all same dimensions)\n'
    '- "accent_color": string (Rich color name like cyan, red, bright_magenta)\n'
    '- "stats": object mapping stat name to integer value, e.g. {"HUMOR": 75, "PATIENCE": 30, ...}\n'
    '- "body_plan": string (anatomy in one sentence)\n'
    '- "walk_description": string (two-stride walk cycle for the macos-desktop sprite)\n'
    '- "fall_description": string (mid-air behavior — glide vs plummet)\n'
    '- "landing_description": string (touchdown reaction — soft vs hard impact)\n'
)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)

_MAX_RETRIES = 3


def _extract_json_from_result(result_msg: object, context: str) -> dict[str, object]:
    """Extract and parse JSON from an Agent SDK ResultMessage.

    Tries structured_output first, falls back to parsing result text
    with markdown fence stripping.

    Args:
        result_msg: The ResultMessage from an Agent SDK query.
        context: Human-readable context for error messages.

    Returns:
        Parsed JSON data as a dictionary.

    Raises:
        RuntimeError: If the result is empty or contains invalid JSON.
    """
    structured_output = getattr(result_msg, "structured_output", None)
    if structured_output is not None:
        return structured_output if isinstance(structured_output, dict) else {}

    result_text = getattr(result_msg, "result", None)
    if result_text:
        cleaned = _extract_json(result_text)
        try:
            data: dict[str, object] = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse %s JSON. Raw result:\n%s", context, result_text)
            raise RuntimeError(f"Agent SDK returned invalid JSON for {context}: {exc}") from exc
        else:
            return data

    raise RuntimeError(f"Agent SDK returned empty result for {context}")


def _parse_json_response(text: str | None, context: str) -> dict[str, object]:
    """Extract and parse JSON from a raw text response.

    Strips markdown fences before parsing. Used by non-SDK providers
    (OpenAI-compat, Gemini) that return plain text.

    Args:
        text: Raw text response from the LLM.
        context: Human-readable context for error messages.

    Returns:
        Parsed JSON data as a dictionary.

    Raises:
        RuntimeError: If the response is empty or contains invalid JSON.
    """
    if not text:
        raise RuntimeError(f"LLM returned empty result for {context}")

    cleaned = _extract_json(text)
    try:
        data: dict[str, object] = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse %s JSON. Raw result:\n%s", context, text)
        raise RuntimeError(f"LLM returned invalid JSON for {context}: {exc}") from exc
    else:
        return data


async def _generate_text_openai_compat(
    system_prompt: str,
    user_prompt: str,
    resolved: ResolvedProviderConfig,
    max_tokens: int = 4096,
) -> str:
    """Generate text using an OpenAI-compatible API, raising on failure.

    Unlike the shared ``generate_text_openai_compat`` in ``llm_client``,
    this version propagates errors so the profile generator retry logic works.

    Args:
        system_prompt: System prompt for the model.
        user_prompt: User prompt for the model.
        resolved: Fully-resolved provider config.
        max_tokens: Maximum tokens in the response.

    Returns:
        Generated text.

    Raises:
        RuntimeError: If the API call fails.
    """
    from openai import OpenAI

    try:
        client = OpenAI(base_url=resolved.base_url, api_key=resolved.api_key or "unused")
        response = client.chat.completions.create(
            model=resolved.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            timeout=120,
        )
        choice = response.choices[0] if response.choices else None
        if choice and choice.message and choice.message.content:
            return choice.message.content.strip()
        raise RuntimeError(f"OpenAI-compatible API returned no content for model={resolved.model}")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"OpenAI-compatible API call failed (provider={resolved.provider}, "
            f"model={resolved.model}, base_url={resolved.base_url}): {exc}"
        ) from exc


async def _generate_text_gemini(
    system_prompt: str,
    user_prompt: str,
    resolved: ResolvedProviderConfig,
) -> str:
    """Generate text using Google Gemini API, raising on failure.

    Args:
        system_prompt: System prompt for the model.
        user_prompt: User prompt for the model.
        resolved: Fully-resolved provider config.

    Returns:
        Generated text.

    Raises:
        RuntimeError: If the API call fails or returns no text.
    """
    from google import genai
    from google.genai import types as gx

    api_key = resolved.api_key
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY environment variable is required for Gemini text generation")

    try:
        client = genai.Client(api_key=api_key)
        config = gx.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="text/plain",
        )
        response = client.models.generate_content(
            model=resolved.model,
            contents=[user_prompt],
            config=config,
        )

        text = response.text if hasattr(response, "text") else None
        if text:
            return text.strip()
        raise RuntimeError(f"Gemini API returned no text content for model={resolved.model}")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Gemini API call failed (model={resolved.model}): {exc}") from exc


async def _call_profile_llm(
    system_prompt: str,
    user_prompt: str,
    config: TpetConfig,
    context: str = "LLM call",
) -> dict[str, object]:
    """Route profile LLM call to the configured provider and parse JSON response.

    Determines the provider from ``config.resolved_profile_provider`` and
    dispatches to the appropriate backend. All providers return parsed JSON.

    Args:
        system_prompt: System prompt for the model.
        user_prompt: User prompt for the model.
        config: Application configuration (determines provider and model).
        context: Human-readable context for error messages.

    Returns:
        Parsed JSON data as a dictionary.

    Raises:
        RuntimeError: If the LLM call fails or returns invalid JSON.
    """
    resolved = config.resolved_profile_provider

    if resolved.uses_agent_sdk:
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        options = ClaudeAgentOptions(
            model=resolved.model,
            system_prompt=system_prompt,
            allowed_tools=[],
            max_turns=1,
            permission_mode="dontAsk",
            setting_sources=[],
            plugins=[],
        )
        result_msg: object | None = None
        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, ResultMessage):
                result_msg = message

        if result_msg is None:
            raise RuntimeError(f"Agent SDK did not return a result for {context}")
        is_error = getattr(result_msg, "is_error", False)
        if is_error:
            errors_val = getattr(result_msg, "errors", None) or []
            errors = ", ".join(str(e) for e in errors_val) if errors_val else "unknown error"
            raise RuntimeError(f"Agent SDK returned an error: {errors}")

        return _extract_json_from_result(result_msg, context)

    if resolved.is_openai_compat:
        text = await _generate_text_openai_compat(system_prompt, user_prompt, resolved)
        return _parse_json_response(text, context)

    if resolved.provider == LLMProvider.GEMINI:
        text = await _generate_text_gemini(system_prompt, user_prompt, resolved)
        return _parse_json_response(text, context)

    raise RuntimeError(f"Unsupported LLM provider for profile generation: {resolved.provider}")


def _run_with_retries(fn: object, *args: object, context: str = "Operation") -> object:
    """Run an async function via asyncio.run with retry logic.

    Args:
        fn: Async callable to execute.
        *args: Arguments to pass to the callable.
        context: Human-readable context for error messages.

    Returns:
        The return value of the async function.

    Raises:
        RuntimeError: If all retry attempts fail.
    """
    last_error: Exception | None = None
    attempt = 0
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return asyncio.run(fn(*args))  # type: ignore[operator]
        except Exception as exc:
            last_error = exc
            logger.warning("%s attempt %d/%d failed: %s", context, attempt, _MAX_RETRIES, exc)
            # Don't retry on model/permission errors — they won't change
            msg = str(exc).lower()
            if any(s in msg for s in ("model", "invalid", "unauthorized", "permission", "not found")):
                break
    raise RuntimeError(f"{context} failed after {attempt} attempts: {last_error}") from last_error


def _pad_to_cell_width(line: str, target_width: int) -> str:
    """Pad a string with spaces to reach the target terminal cell width.

    Uses cell_len (not len) so wide Unicode characters are measured correctly.

    Args:
        line: Input string.
        target_width: Desired terminal cell width.

    Returns:
        Right-padded string.
    """
    from rich.cells import cell_len

    current = cell_len(line)
    if current >= target_width:
        return line
    return line + " " * (target_width - current)


def _normalize_art_frames(frames: list[str]) -> list[str]:
    """Normalize ASCII art frames to uniform dimensions.

    Ensures all frames have the same number of lines and all lines have the
    same terminal cell width. Uses cell_len for correct measurement of wide
    Unicode characters. Lines are right-padded with spaces and frames are
    bottom-padded with empty lines as needed.

    Args:
        frames: List of multiline ASCII art strings.

    Returns:
        Normalized frames with uniform dimensions.
    """
    from rich.cells import cell_len

    # Split each frame into lines
    split_frames = [frame.split("\n") for frame in frames]

    # Find max height and max cell width across all frames
    max_height = max(len(lines) for lines in split_frames)
    max_width = 0
    for lines in split_frames:
        for line in lines:
            max_width = max(max_width, cell_len(line))

    # Pad each frame to uniform dimensions
    normalized: list[str] = []
    for lines in split_frames:
        padded = [_pad_to_cell_width(line, max_width) for line in lines]
        # Add empty lines if this frame is shorter
        while len(padded) < max_height:
            padded.append(" " * max_width)
        normalized.append("\n".join(padded))

    return normalized


def _extract_json(text: str) -> str:
    """Extract JSON from text that may be wrapped in markdown code fences.

    Args:
        text: Raw text that may contain JSON with or without fences.

    Returns:
        Cleaned JSON string.
    """
    match = _JSON_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


async def _generate_pet_async(
    config: TpetConfig,
    rarity: Rarity,
    project_path: str | None,
    criteria: str | None = None,
) -> PetProfile:
    """Generate a pet profile using the configured LLM provider.

    Args:
        config: Application configuration.
        rarity: Rarity level for the pet (affects stats).
        project_path: Optional project path for project-specific pet.
        criteria: Optional custom criteria to guide pet generation.

    Returns:
        A newly generated PetProfile.

    Raises:
        RuntimeError: If the LLM call fails or returns unexpected format.
    """
    if rarity == Rarity.COMMON:
        complexity = "simple"
    elif rarity == Rarity.LEGENDARY:
        complexity = "impressive"
    else:
        complexity = "interesting"
    rarity_hint = f"This is a {rarity.value} creature ({rarity.stars}). Make it appropriately {complexity}."

    import random

    seed = config.seed
    rng = random.Random(seed)

    adjectives = [
        "whimsical",
        "mischievous",
        "stoic",
        "energetic",
        "lazy",
        "curious",
        "grumpy",
        "cheerful",
        "sassy",
        "shy",
        "brave",
        "clumsy",
        "wise",
        "chaotic",
        "gentle",
        "fierce",
        "dreamy",
        "sneaky",
        "noble",
        "quirky",
    ]
    species_hints = [
        "elemental",
        "sprite",
        "familiar",
        "spirit",
        "golem",
        "slime",
        "dragon",
        "cat",
        "bird",
        "bug",
        "robot",
        "ghost",
        "plant",
        "fish",
        "demon",
        "angel",
        "goblin",
        "fox",
        "blob",
        "crystal",
    ]
    themes = [
        "code",
        "bugs",
        "compilers",
        "networks",
        "databases",
        "cloud",
        "pixels",
        "shaders",
        "threads",
        "memory",
        "stacks",
        "loops",
        "regex",
        "syntax",
        "debugging",
        "recursion",
        "linting",
        "types",
        "containers",
        "terminals",
        "git",
        "merge conflicts",
        "deploys",
    ]

    adj = rng.choice(adjectives)
    species = rng.choice(species_hints)
    theme = rng.choice(themes)
    prompt_parts = [
        f"Generate a terminal pet creature. {rarity_hint}",
        f"Inspiration for this pet: a {adj} {species} related to {theme}. "
        f"Use this as a starting point but feel free to deviate creatively.",
    ]
    if criteria:
        prompt_parts.append(f"Additional creation criteria from the user:\n{criteria}")
    user_prompt = "\n\n".join(prompt_parts)

    data = await _call_profile_llm(_SYSTEM_PROMPT, user_prompt, config, context="pet generation")

    # Use LLM-generated stats if available, clamped to rarity range; fallback to random
    lo, hi = rarity.stat_range
    raw_stats = data.get("stats")
    if isinstance(raw_stats, dict) and raw_stats:
        stats = {str(k): max(lo, min(hi, int(v))) for k, v in raw_stats.items() if isinstance(v, (int, float))}
    else:
        stats = generate_stats(config.stat_config, rarity)

    raw_art = data.get("ascii_art")
    if not isinstance(raw_art, list) or len(raw_art) < 6:
        raise RuntimeError(f"Expected 6 ascii_art frames, got: {raw_art!r}")

    ascii_art = _normalize_art_frames([str(frame) for frame in raw_art[:6]])

    return PetProfile(
        name=str(data["name"]),
        creature_type=str(data["creature_type"]),
        rarity=rarity,
        personality=str(data["personality"]),
        backstory=str(data["backstory"]),
        ascii_art=ascii_art,
        stats=stats,
        accent_color=str(data["accent_color"]),
        project_path=project_path,
        body_plan=str(data.get("body_plan", "")).strip(),
        walk_description=str(data.get("walk_description", "")).strip(),
        fall_description=str(data.get("fall_description", "")).strip(),
        landing_description=str(data.get("landing_description", "")).strip(),
    )


_ART_REGEN_SYSTEM_PROMPT = (
    "You are a creative terminal pet artist. You will be given a pet's name, creature type, "
    "and personality. Generate NEW ASCII art for this existing pet.\n\n"
    "Use extended unicode characters like braille patterns "
    "(\u2800-\u28ff), half-blocks (\u2580\u2584\u2588), box-drawing (\u2500\u2502\u250c\u2510\u2514\u2518), "
    "and other special characters for the ASCII art.\n\n"
    + _ART_RULES
    + "IMPORTANT: Output ONLY a JSON object with a single key:\n"
    '- "ascii_art": array of exactly 6 strings (multiline, all same dimensions)\n'
)


async def _regenerate_art_async(
    config: TpetConfig,
    pet: PetProfile,
) -> list[str]:
    """Regenerate ASCII art for an existing pet using the configured LLM provider.

    Args:
        config: Application configuration.
        pet: Existing pet profile to regenerate art for.

    Returns:
        List of 6 normalized ASCII art frame strings.

    Raises:
        RuntimeError: If the LLM call fails or returns unexpected format.
    """
    user_prompt = (
        f"Generate new ASCII art for my pet:\n"
        f"- Name: {pet.name}\n"
        f"- Creature type: {pet.creature_type}\n"
        f"- Personality: {pet.personality}\n"
        f"- Rarity: {pet.rarity.value} ({pet.rarity.stars})\n"
    )

    data = await _call_profile_llm(_ART_REGEN_SYSTEM_PROMPT, user_prompt, config, context="art regeneration")

    raw_art = data.get("ascii_art")
    if not isinstance(raw_art, list) or len(raw_art) < 6:
        raise RuntimeError(f"Expected 6 ascii_art frames, got: {raw_art!r}")

    return _normalize_art_frames([str(frame) for frame in raw_art[:6]])


def regenerate_art(config: TpetConfig, pet: PetProfile) -> list[str]:
    """Regenerate ASCII art for an existing pet using the configured provider.

    Retries up to 3 times on failure.

    Args:
        config: Application configuration.
        pet: Existing pet profile.

    Returns:
        List of 6 normalized ASCII art frame strings.

    Raises:
        RuntimeError: If all retry attempts fail.
    """
    result = _run_with_retries(_regenerate_art_async, config, pet, context="Art regeneration")
    return result  # type: ignore[return-value]


def generate_pet(
    config: TpetConfig,
    rarity: Rarity,
    project_path: str | None,
    criteria: str | None = None,
) -> PetProfile:
    """Generate a new pet profile using the configured LLM provider.

    Retries up to 3 times on failure (e.g. invalid JSON from the model).

    Args:
        config: Application configuration.
        rarity: Rarity level for the pet (affects stats).
        project_path: Optional project path for project-specific pet.
        criteria: Optional custom criteria to guide pet generation.

    Returns:
        A newly generated PetProfile.

    Raises:
        RuntimeError: If all retry attempts fail.
    """
    result = _run_with_retries(_generate_pet_async, config, rarity, project_path, criteria, context="Pet generation")
    return result  # type: ignore[return-value]


_LOCOMOTION_BACKFILL_SYSTEM_PROMPT = (
    "You are a creature designer filling in missing animation fields for an existing pet. "
    "You will be given the pet's name, creature type, personality, and backstory. "
    "Produce four short descriptors used by an image-generation pipeline.\n\n"
    + _LOCOMOTION_INSTRUCTIONS
    + "\n"
    "IMPORTANT: Output ONLY a single JSON object with no markdown fencing, no explanation, "
    "and no extra text. The JSON must have these exact keys:\n"
    '- "body_plan": string\n'
    '- "walk_description": string\n'
    '- "fall_description": string\n'
    '- "landing_description": string\n'
)


async def _backfill_locomotion_async(config: TpetConfig, pet: PetProfile) -> dict[str, str]:
    """Ask the configured profile LLM for the four locomotion descriptors."""
    user_prompt = (
        f"Fill in the locomotion fields for this existing pet:\n"
        f"- Name: {pet.name}\n"
        f"- Creature type: {pet.creature_type}\n"
        f"- Personality: {pet.personality}\n"
        f"- Backstory: {pet.backstory}\n"
        f"- Rarity: {pet.rarity.value} ({pet.rarity.stars})\n"
    )
    data = await _call_profile_llm(
        _LOCOMOTION_BACKFILL_SYSTEM_PROMPT, user_prompt, config, context="locomotion backfill"
    )
    return {
        "body_plan": str(data.get("body_plan", "")).strip(),
        "walk_description": str(data.get("walk_description", "")).strip(),
        "fall_description": str(data.get("fall_description", "")).strip(),
        "landing_description": str(data.get("landing_description", "")).strip(),
    }


def ensure_locomotion_descriptors(config: TpetConfig, pet: PetProfile) -> tuple[PetProfile, bool]:
    """Return ``(pet, updated)`` with locomotion fields filled in.

    If any of the four fields is empty (legacy profile), call the configured
    profile LLM once to fill them all and return a copy of ``pet`` with the
    new values. ``updated`` is True when the caller should persist the result.

    Args:
        config: Application configuration.
        pet: Existing pet profile.

    Returns:
        Tuple of (pet, updated). ``pet`` is the same object if no change.
    """
    if all([pet.body_plan, pet.walk_description, pet.fall_description, pet.landing_description]):
        return pet, False

    descriptors = _run_with_retries(_backfill_locomotion_async, config, pet, context="Locomotion backfill")
    if not isinstance(descriptors, dict):
        return pet, False

    updated = pet.model_copy(
        update={
            "body_plan": descriptors.get("body_plan", "") or pet.body_plan,
            "walk_description": descriptors.get("walk_description", "") or pet.walk_description,
            "fall_description": descriptors.get("fall_description", "") or pet.fall_description,
            "landing_description": descriptors.get("landing_description", "") or pet.landing_description,
        }
    )
    return updated, True
