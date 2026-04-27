"""Typer CLI entry point for tpet."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
import yaml
from dotenv import load_dotenv
from rich.console import Console

from tpet import __version__
from tpet.config import (
    ArtMode,
    BubblePlacement,
    LLMProvider,
    PipelineProviderConfig,
    TpetConfig,
    load_config,
)

if TYPE_CHECKING:
    from tpet.models.pet import PetProfile

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_env(config_dir: Path) -> None:
    """Load .env from config directory if it exists.

    Args:
        config_dir: Configuration directory to search for ``.env``.
    """
    env_path = config_dir / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def _build_config(config_dir: Path | None) -> TpetConfig:
    """Build config, loading from file if it exists.

    Args:
        config_dir: Optional config directory override.

    Returns:
        Loaded or default TpetConfig instance.
    """
    if config_dir:
        config = load_config(config_dir / "config.yaml")
        config = config.model_copy(update={"config_dir": config_dir})
    else:
        config = TpetConfig()
        config_path = config.config_file_path
        if config_path.exists():
            config = load_config(config_path)
    return config


def _setup_logging(
    config: TpetConfig,
    debug: bool,
    verbose: int,
    *,
    console_output: bool = False,
) -> None:
    """Configure logging based on CLI flags.

    Args:
        config: Application config.
        debug: Whether debug mode is enabled.
        verbose: Verbosity level count.
        console_output: If True, also stream logs to stderr. Only safe for
            one-shot CLI commands like ``tpet art`` / ``tpet new``; do NOT
            enable for the live ``tpet run`` TUI, which would corrupt the
            display.
    """
    level = logging.DEBUG if debug else getattr(logging, config.log_level, logging.WARNING)
    if verbose > 0:
        level = max(logging.DEBUG, level - (verbose * 10))

    config.config_dir.mkdir(parents=True, exist_ok=True)
    config.pet_data_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.FileHandler(config.log_file_path, encoding="utf-8"),
    ]
    if console_output:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        handlers=handlers,
    )


def _apply_overrides(
    config: TpetConfig,
    *,
    comment_interval: float | None,
    idle_chatter_interval: float | None,
    max_comments: int | None,
    sleep_threshold: int | None,
    log_level: str | None,
    art_mode: ArtMode | None,
    art_width: int | None,
    art_prompt: str | None,
    bubble_placement: BubblePlacement | None = None,
    profile_provider: LLMProvider | None = None,
    profile_model: str | None = None,
    commentary_provider: LLMProvider | None = None,
    commentary_model: str | None = None,
    image_art_provider: LLMProvider | None = None,
    image_art_model: str | None = None,
) -> TpetConfig:
    """Apply CLI flag overrides to config via model_copy.

    Returns a new TpetConfig with validated overrides applied.

    Args:
        config: Base configuration to override.
        comment_interval: Min seconds between comments.
        idle_chatter_interval: Seconds between idle chatter.
        max_comments: Max comments per session.
        sleep_threshold: Seconds before sleep animation.
        log_level: Log level override.
        art_mode: Art display mode.
        art_width: Max percentage of terminal width for art.
        art_prompt: Custom prompt for image generation.
        bubble_placement: Speech bubble position.
        profile_provider: Provider for profile generation.
        profile_model: Model for profile generation.
        commentary_provider: Provider for commentary.
        commentary_model: Model for commentary.
        image_art_provider: Provider for image art.
        image_art_model: Model for image art.

    Returns:
        Updated TpetConfig instance (new object if any override was set).
    """
    overrides: dict[str, object] = {}
    if comment_interval is not None:
        overrides["comment_interval_seconds"] = comment_interval
    if idle_chatter_interval is not None:
        overrides["idle_chatter_interval_seconds"] = idle_chatter_interval
    if max_comments is not None:
        overrides["max_comments_per_session"] = max_comments
    if sleep_threshold is not None:
        overrides["sleep_threshold_seconds"] = sleep_threshold
    if log_level:
        overrides["log_level"] = log_level
    if art_mode:
        overrides["art_mode"] = art_mode
    if art_width is not None:
        overrides["art_max_width_pct"] = max(10, min(100, art_width))
    if art_prompt is not None:
        overrides["art_prompt"] = art_prompt
    if bubble_placement:
        overrides["bubble_placement"] = bubble_placement

    # Pipeline provider overrides
    profile_cfg_overrides: dict[str, object] = {}
    if profile_provider:
        profile_cfg_overrides["provider"] = profile_provider
    if profile_model:
        profile_cfg_overrides["model"] = profile_model
    if profile_cfg_overrides:
        overrides["profile_provider_config"] = PipelineProviderConfig(
            **{
                **config.profile_provider_config.model_dump(),
                **profile_cfg_overrides,
            }
        )

    commentary_cfg_overrides: dict[str, object] = {}
    if commentary_provider:
        commentary_cfg_overrides["provider"] = commentary_provider
    if commentary_model:
        commentary_cfg_overrides["model"] = commentary_model
    if commentary_cfg_overrides:
        overrides["commentary_provider_config"] = PipelineProviderConfig(
            **{
                **config.commentary_provider_config.model_dump(),
                **commentary_cfg_overrides,
            }
        )

    image_art_cfg_overrides: dict[str, object] = {}
    if image_art_provider:
        image_art_cfg_overrides["provider"] = image_art_provider
    if image_art_model:
        image_art_cfg_overrides["model"] = image_art_model
    if image_art_cfg_overrides:
        overrides["image_art_provider_config"] = PipelineProviderConfig(
            **{
                **config.image_art_provider_config.model_dump(),
                **image_art_cfg_overrides,
            }
        )

    return config.model_copy(update=overrides) if overrides else config


def _resolve_project_pet(config: TpetConfig, project: str | None) -> tuple[TpetConfig, Path, PetProfile | None]:
    """Resolve the active pet profile with global fallback.

    If a project-specific pet exists, scopes art and log directories to
    the project's ``.tpet/`` directory.  Otherwise falls back to the
    global pet and global directories.

    Args:
        config: Current configuration.
        project: Optional project directory path.

    Returns:
        Tuple of ``(updated_config, profile_path, pet_or_none)``.
    """
    from tpet.profile.storage import resolve_profile

    profile_path, pet = resolve_profile(config.config_dir, project)

    if pet is not None and project:
        project_tpet = Path(project) / ".tpet"
        if profile_path.parent == project_tpet:
            config = config.model_copy(update={"project_dir": project_tpet})

    return config, profile_path, pet


def _preview_frames(config: TpetConfig, pet_name: str, frame_count: int) -> None:
    """Show a compact preview of all generated frames after art generation.

    Delegates to ``tpet.renderer.preview.preview_frames``.

    Args:
        config: Application configuration.
        pet_name: Name of the pet (for locating PNG files).
        frame_count: Number of frames generated (4 or 6).
    """
    from tpet.renderer.preview import preview_frames

    preview_frames(config, pet_name, frame_count)


# ---------------------------------------------------------------------------
# Typer application
# ---------------------------------------------------------------------------

console = Console()

app = typer.Typer(
    name="tpet",
    help="A terminal pet companion that monitors Claude Code sessions.",
    add_completion=False,
    no_args_is_help=False,
)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"tpet {__version__}")
        raise typer.Exit()


# ---------------------------------------------------------------------------
# Subcommand: new
# ---------------------------------------------------------------------------


@app.command("new")
def cmd_new(
    ctx: typer.Context,
    create_prompt: Annotated[
        str | None, typer.Option("--create-prompt", "-C", help="Custom criteria for pet generation")
    ] = None,
    create_prompt_file: Annotated[
        Path | None,
        typer.Option("--create-prompt-file", "-F", help="File containing custom criteria for pet generation"),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Bypass confirmation prompts")] = False,
    project: Annotated[str | None, typer.Option("--project", "-p", help="Project directory")] = None,
    config_dir: Annotated[str | None, typer.Option("--config-dir", "-c", help="Config directory override")] = None,
    debug: Annotated[bool, typer.Option("--debug", "-D", help="Enable debug logging")] = False,
    verbose: Annotated[int, typer.Option("--verbose", "-v", count=True, help="Increase verbosity")] = 0,
    seed: Annotated[
        int | None, typer.Option("--seed", "-s", help="Random seed for pet generation (defaults to current time)")
    ] = None,
) -> None:
    """Generate a new pet (replaces existing if confirmed)."""
    cfg_dir = Path(config_dir) if config_dir else None
    config = _build_config(cfg_dir)
    if seed is not None:
        config = config.model_copy(update={"seed": seed})
    _load_env(config.config_dir)
    _setup_logging(config, debug, verbose)

    from tpet.models.rarity import pick_rarity
    from tpet.profile.storage import get_profile_path, load_profile, save_profile
    from tpet.renderer.card import render_card

    profile_path = get_profile_path(config.config_dir, project)

    # Resolve creation criteria
    criteria: str | None = None
    if create_prompt_file is not None:
        if not create_prompt_file.exists():
            console.print(f"[red]Prompt file not found: {create_prompt_file}[/red]")
            raise typer.Exit(code=1)
        criteria = create_prompt_file.read_text(encoding="utf-8").strip()
    if create_prompt is not None:
        criteria = f"{criteria}\n{create_prompt}" if criteria else create_prompt

    pet = load_profile(profile_path)
    if pet is not None and not yes and not typer.confirm("Replace existing pet?"):
        raise typer.Exit()

    console.print("[cyan]Generating new pet...[/cyan]")
    from tpet.profile.generator import generate_pet

    rarity = pick_rarity(config.rarity_weights)
    pet = generate_pet(config, rarity=rarity, project_path=project, criteria=criteria)
    save_profile(pet, profile_path)
    console.print(f"[green]Meet {pet.name} the {pet.creature_type}![/green]")
    console.print(render_card(pet, config=config))


# ---------------------------------------------------------------------------


@app.command("details")
def cmd_details(
    backstory: Annotated[bool, typer.Option("--backstory", "-b", help="Include backstory in card")] = False,
    project: Annotated[str | None, typer.Option("--project", "-p", help="Project directory")] = None,
    config_dir: Annotated[str | None, typer.Option("--config-dir", "-c", help="Config directory override")] = None,
) -> None:
    """Show the full pet details card."""
    cfg_dir = Path(config_dir) if config_dir else None
    config = _build_config(cfg_dir)
    _load_env(config.config_dir)

    from tpet.renderer.card import render_card

    config, profile_path, pet = _resolve_project_pet(config, project)
    if pet is None:
        console.print("[yellow]No pet found. Run `tpet new` to create one.[/yellow]")
        raise typer.Exit(code=1)

    console.print(render_card(pet, show_backstory=backstory, config=config))


# ---------------------------------------------------------------------------
# Subcommand: art
# ---------------------------------------------------------------------------


@app.command("art")
def cmd_art(
    project: Annotated[str | None, typer.Option("--project", "-p", help="Project directory")] = None,
    config_dir: Annotated[str | None, typer.Option("--config-dir", "-c", help="Config directory override")] = None,
    art_mode: Annotated[
        ArtMode | None,
        typer.Option("--art-mode", "-a", help="Art display mode: ascii or sixel-art"),
    ] = None,
    image_art_provider: Annotated[
        LLMProvider | None,
        typer.Option("--art-provider", "-P", help="Art generation provider: openai or gemini"),
    ] = None,
    image_art_model: Annotated[str | None, typer.Option("--art-model", help="Model for image art generation")] = None,
    art_width: Annotated[
        int | None,
        typer.Option("--art-width", "-W", help="Max percentage of terminal width for art (1-100)"),
    ] = None,
    art_prompt: Annotated[
        str | None,
        typer.Option(
            "--art-prompt",
            help="Custom prompt for image generation (frame layout instructions appended automatically)",
        ),
    ] = None,
    base_image: Annotated[
        Path | None,
        typer.Option(
            "--base-image",
            help="Path to a custom image to use as the idle frame instead of generating one (forces OpenAI edits)",
        ),
    ] = None,
    recrop: Annotated[
        bool,
        typer.Option(
            "--recrop",
            help="Re-crop existing PNG frames to a shared bounding box; skip generation",
        ),
    ] = False,
    rechroma: Annotated[
        bool,
        typer.Option(
            "--rechroma",
            help="Re-split and chroma-key from the saved sprite PNG (no API call)",
        ),
    ] = False,
    debug: Annotated[bool, typer.Option("--debug", "-D", help="Enable debug logging")] = False,
    verbose: Annotated[int, typer.Option("--verbose", "-v", count=True, help="Increase verbosity")] = 0,
) -> None:
    """Generate graphical art for the current pet."""
    cfg_dir = Path(config_dir) if config_dir else None
    config = _build_config(cfg_dir)
    _load_env(config.config_dir)
    config = _apply_overrides(
        config,
        comment_interval=None,
        idle_chatter_interval=None,
        max_comments=None,
        sleep_threshold=None,
        log_level=None,
        art_mode=art_mode,
        art_width=art_width,
        art_prompt=art_prompt,
        image_art_provider=image_art_provider,
        image_art_model=image_art_model,
    )
    _setup_logging(config, debug, verbose, console_output=debug)

    # Validate base image if provided
    if base_image is not None and not base_image.exists():
        console.print(f"[red]Base image not found: {base_image}[/red]")
        raise typer.Exit(code=1)

    config, profile_path, pet = _resolve_project_pet(config, project)
    if pet is None:
        console.print("[yellow]No pet found. Run `tpet new` to create one.[/yellow]")
        raise typer.Exit(code=1)

    if recrop:
        from PIL import Image

        from tpet.art.process import crop_frames_to_common_bbox
        from tpet.art.storage import get_art_dir, sanitize_name

        art_dir = get_art_dir(config.pet_data_dir)
        safe = sanitize_name(pet.name)
        paths = sorted(p for p in art_dir.glob(f"{safe}_frame_*.png") if "_raw" not in p.name)
        if not paths:
            console.print(f"[yellow]No frame PNGs found for {pet.name} in {art_dir}.[/yellow]")
            raise typer.Exit(code=1)
        images = [Image.open(p).convert("RGBA") for p in paths]
        cropped = crop_frames_to_common_bbox(images)
        for p, img in zip(paths, cropped, strict=True):
            img.save(p, format="PNG")
        console.print(f"[green]Re-cropped {len(cropped)} frames for {pet.name} to {cropped[0].size}.[/green]")
        return

    if rechroma:
        from PIL import Image

        from tpet.art.process import remove_chroma_key, split_sprite_sheet
        from tpet.art.storage import get_art_dir, sanitize_name, save_png_frame

        art_dir = get_art_dir(config.pet_data_dir)
        safe = sanitize_name(pet.name)
        sprite_path = art_dir / f"{safe}_sprite.png"
        if not sprite_path.exists():
            console.print(f"[yellow]No sprite found at {sprite_path}.[/yellow]")
            raise typer.Exit(code=1)

        layout = "2x5" if config.art_mode == ArtMode.MACOS_DESKTOP else None
        sprite = Image.open(sprite_path)
        frames = split_sprite_sheet(sprite, layout=layout, inset_px=6)
        chroma_target = (255, 0, 255)
        chroma_tolerance = max(config.chroma_tolerance, 100)
        frames = [
            remove_chroma_key(f, tolerance=chroma_tolerance, target_color=chroma_target)
            for f in frames
        ]
        for i, frame in enumerate(frames):
            save_png_frame(config.pet_data_dir, pet.name, i, frame)
        console.print(f"[green]Re-chroma'd {len(frames)} frames for {pet.name} from {sprite_path.name}.[/green]")
        return

    mode_label = config.art_mode.replace("-", " ").title()

    if config.art_mode == ArtMode.MACOS_DESKTOP:
        from tpet.profile.generator import ensure_locomotion_descriptors
        from tpet.profile.storage import save_profile

        needs_backfill = not all(
            [pet.body_plan, pet.walk_description, pet.fall_description, pet.landing_description]
        )
        if needs_backfill:
            console.print(
                f"[cyan]Filling in locomotion descriptors for {pet.name} "
                f"(via {config.resolved_profile_provider.provider.value}, this may take a few seconds)...[/cyan]"
            )
        try:
            pet, updated = ensure_locomotion_descriptors(config, pet)
        except RuntimeError as exc:
            console.print(f"[red]Failed to generate locomotion descriptors:[/red] {exc}")
            raise typer.Exit(1) from None
        if updated:
            save_profile(pet, profile_path)
            console.print(f"[dim]Saved updated profile to {profile_path}.[/dim]")

    if base_image:
        console.print(f"[cyan]Generating {mode_label} art for {pet.name} from {base_image.name}...[/cyan]")
    else:
        console.print(f"[cyan]Generating {mode_label} art for {pet.name}...[/cyan]")
    from tpet.art.generator import generate_art

    def _on_progress(current: int, total: int, label: str) -> None:
        console.print(f"  [dim]({current}/{total})[/dim] Generating [bold]{label}[/bold]...")

    try:
        frames, gen_result = generate_art(config, pet, on_progress=_on_progress, base_image_path=base_image)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None
    except (RuntimeError, OSError) as exc:
        console.print(f"[red]Generation failed:[/red] {exc}")
        console.print("[yellow]Completed frames are saved. Re-run `tpet art` to resume from the failed frame.[/yellow]")
        raise typer.Exit(1) from None

    if frames:
        console.print(f"[green]{mode_label} art generated for {pet.name}! ({len(frames)} frames)[/green]")
        if gen_result:
            u = gen_result.usage
            cost = u.estimated_cost_usd
            console.print(
                f"  [dim]API calls: {gen_result.api_calls} | "
                f"Tokens: {u.total_tokens:,} "
                f"(input: {u.input_tokens:,}, output: {u.output_tokens:,}) | "
                f"Est. cost: ${cost:.3f}[/dim]"
            )
        _preview_frames(config, pet.name, len(frames))
    else:
        console.print(f"[red]Failed to generate {mode_label} art.[/red]")


# ---------------------------------------------------------------------------
# Subcommand: run (explicit alias for the default live display)
# ---------------------------------------------------------------------------


@app.command("run")
def cmd_run(
    project: Annotated[str | None, typer.Option("--project", "-p", help="Project directory")] = None,
    config_dir: Annotated[str | None, typer.Option("--config-dir", "-c", help="Config directory override")] = None,
    watch_dir: Annotated[str | None, typer.Option("--watch-dir", "-w", help="Session watch directory override")] = None,
    follow: Annotated[
        str | None, typer.Option("--follow", "-f", help="Follow a plain text file instead of Claude sessions")
    ] = None,
    commentary_provider: Annotated[
        LLMProvider | None,
        typer.Option("--commentary-provider", help="Commentary LLM provider"),
    ] = None,
    commentary_model: Annotated[str | None, typer.Option("--commentary-model", help="Model for commentary")] = None,
    comment_interval: Annotated[
        float | None, typer.Option("--comment-interval", "-i", help="Min seconds between comments")
    ] = None,
    idle_chatter_interval: Annotated[
        float | None, typer.Option("--idle-chatter-interval", "-I", help="Seconds between idle chatter")
    ] = None,
    max_comments: Annotated[int | None, typer.Option("--max-comments", "-M", help="Max comments per session")] = None,
    sleep_threshold: Annotated[
        int | None, typer.Option("--sleep-threshold", "-s", help="Seconds before sleep animation")
    ] = None,
    art_mode: Annotated[
        ArtMode | None,
        typer.Option("--art-mode", "-a", help="Art display mode: ascii or sixel-art"),
    ] = None,
    log_level: Annotated[str | None, typer.Option("--log-level", "-l", help="Log level override")] = None,
    show_session: Annotated[
        bool, typer.Option("--show-session", help="Show the session directory and file tpet would follow, then exit")
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress startup messages printed before the live display starts"),
    ] = False,
    debug: Annotated[bool, typer.Option("--debug", "-D", help="Enable debug logging")] = False,
    verbose: Annotated[int, typer.Option("--verbose", "-v", count=True, help="Increase verbosity")] = 0,
) -> None:
    """Start the live pet display (default mode)."""
    cfg_dir = Path(config_dir) if config_dir else None
    config = _build_config(cfg_dir)
    _load_env(config.config_dir)
    config = _apply_overrides(
        config,
        comment_interval=comment_interval,
        idle_chatter_interval=idle_chatter_interval,
        max_comments=max_comments,
        sleep_threshold=sleep_threshold,
        log_level=log_level,
        art_mode=art_mode,
        art_width=None,
        art_prompt=None,
        commentary_provider=commentary_provider,
        commentary_model=commentary_model,
    )
    _setup_logging(config, debug, verbose)

    project_path = project or str(Path.cwd())

    # Handle --show-session: print resolved paths and exit
    if show_session:
        from tpet.monitor.watcher import encode_project_path, find_newest_session

        if follow:
            console.print(f"[cyan]Follow file:[/cyan] {follow}")
        else:
            if watch_dir:
                session_dir = Path(watch_dir)
            else:
                claude_dir = Path.home() / ".claude" / "projects"
                encoded = encode_project_path(project_path)
                session_dir = claude_dir / encoded
            console.print(f"[cyan]Project path:[/cyan] {project_path}")
            console.print(f"[cyan]Session dir:[/cyan]  {session_dir}")
            if session_dir.exists():
                console.print("[green]  Directory exists[/green]")
                newest = find_newest_session(session_dir)
                if newest:
                    console.print(f"[cyan]Active session:[/cyan] {newest.name}")
                    console.print(f"[dim]  {newest}[/dim]")
                    stat = newest.stat()
                    size_kb = stat.st_size / 1024
                    import datetime

                    mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    console.print(f"[dim]  {size_kb:.1f} KB, last modified {mtime}[/dim]")
                else:
                    console.print("[yellow]  No .jsonl session files found[/yellow]")
            else:
                console.print("[red]  Directory not found[/red]")
                encoded = encode_project_path(project_path)
                console.print(f"[dim]  Expected: ~/.claude/projects/{encoded}/[/dim]")
        raise typer.Exit()

    config, profile_path, pet = _resolve_project_pet(config, project)
    if pet is None:
        console.print("[yellow]No pet found. Run `tpet new` to create one.[/yellow]")
        raise typer.Exit(code=1)

    from tpet.app import run_app

    run_app(
        config=config,
        pet=pet,
        profile_path=profile_path,
        project_path=project_path,
        watch_dir=watch_dir,
        follow_file=follow,
        quiet=quiet,
    )


# ---------------------------------------------------------------------------
# Root callback — backward-compatible default entry point
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version")
    ] = False,
    debug: Annotated[bool, typer.Option("--debug", "-D", help="Enable debug logging")] = False,
    verbose: Annotated[int, typer.Option("--verbose", "-v", count=True, help="Increase verbosity")] = 0,
    details: Annotated[bool, typer.Option("--details", "-d", help="Show full pet card")] = False,
    backstory: Annotated[bool, typer.Option("--backstory", "-b", help="Include backstory in details card")] = False,
    new: Annotated[bool, typer.Option("--new", "-N", help="Generate a new pet")] = False,
    create_prompt: Annotated[
        str | None, typer.Option("--create-prompt", "-C", help="Custom criteria for pet generation")
    ] = None,
    create_prompt_file: Annotated[
        Path | None,
        typer.Option("--create-prompt-file", "-F", help="File containing custom criteria for pet generation"),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Bypass confirmation prompts")] = False,
    regen_art: Annotated[bool, typer.Option("--regen-art", "-A", help="Regenerate ASCII art only")] = False,
    reset: Annotated[bool, typer.Option("--reset", "-R", help="Delete current pet")] = False,
    project: Annotated[str | None, typer.Option("--project", "-p", help="Project directory")] = None,
    config_dir: Annotated[str | None, typer.Option("--config-dir", "-c", help="Config directory override")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n", help="Validate config and exit")] = False,
    dump_config: Annotated[bool, typer.Option("--dump-config", help="Output current config")] = False,
    profile_provider: Annotated[
        LLMProvider | None,
        typer.Option("--profile-provider", help="Provider for profile generation"),
    ] = None,
    profile_model: Annotated[str | None, typer.Option("--profile-model", help="Model for profile generation")] = None,
    commentary_provider: Annotated[
        LLMProvider | None,
        typer.Option("--commentary-provider", help="Commentary LLM provider"),
    ] = None,
    commentary_model: Annotated[str | None, typer.Option("--commentary-model", help="Model for commentary")] = None,
    image_art_provider: Annotated[
        LLMProvider | None,
        typer.Option("--art-provider", "-P", help="Image art generation provider"),
    ] = None,
    image_art_model: Annotated[str | None, typer.Option("--art-model", help="Model for image art")] = None,
    comment_interval: Annotated[
        float | None, typer.Option("--comment-interval", "-i", help="Min seconds between comments")
    ] = None,
    idle_chatter_interval: Annotated[
        float | None, typer.Option("--idle-chatter-interval", "-I", help="Seconds between idle chatter")
    ] = None,
    max_comments: Annotated[int | None, typer.Option("--max-comments", "-M", help="Max comments per session")] = None,
    sleep_threshold: Annotated[
        int | None, typer.Option("--sleep-threshold", "-s", help="Seconds before sleep animation")
    ] = None,
    log_level: Annotated[str | None, typer.Option("--log-level", "-l", help="Log level override")] = None,
    watch_dir: Annotated[str | None, typer.Option("--watch-dir", "-w", help="Session watch directory override")] = None,
    follow: Annotated[
        str | None, typer.Option("--follow", "-f", help="Follow a plain text file instead of Claude sessions")
    ] = None,
    art_mode: Annotated[
        ArtMode | None,
        typer.Option("--art-mode", "-a", help="Art display mode: ascii or sixel-art"),
    ] = None,
    gen_art: Annotated[bool, typer.Option("--gen-art", help="Generate graphical art for current pet")] = False,
    art_width: Annotated[
        int | None,
        typer.Option("--art-width", "-W", help="Max percentage of terminal width for art (1-100)"),
    ] = None,
    art_prompt: Annotated[
        str | None,
        typer.Option(
            "--art-prompt",
            help="Custom prompt for image generation (frame layout instructions are appended automatically)",
        ),
    ] = None,
    base_image: Annotated[
        Path | None,
        typer.Option(
            "--base-image",
            help="Path to a custom image to use as the idle frame instead of generating one (forces OpenAI edits)",
        ),
    ] = None,
    bubble_placement: Annotated[
        BubblePlacement | None,
        typer.Option("--bubble", "-B", help="Speech bubble position: top, right, or bottom"),
    ] = None,
    show_session: Annotated[
        bool, typer.Option("--show-session", help="Show the session directory and file tpet would follow, then exit")
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress startup messages printed before the live display starts"),
    ] = False,
) -> None:
    """Start tpet - your terminal pet companion."""
    # Store CLI overrides in context so subcommands can access them
    ctx.ensure_object(dict)

    # If a subcommand was invoked, the callback still runs but should not act
    if ctx.invoked_subcommand is not None:
        return

    # Load config
    cfg_dir = Path(config_dir) if config_dir else None
    config = _build_config(cfg_dir)

    # Load .env unconditionally so API keys are available in all modes
    _load_env(config.config_dir)

    # Apply CLI overrides via model_copy so Pydantic validation runs on the new values
    config = _apply_overrides(
        config,
        comment_interval=comment_interval,
        idle_chatter_interval=idle_chatter_interval,
        max_comments=max_comments,
        sleep_threshold=sleep_threshold,
        log_level=log_level,
        art_mode=art_mode,
        art_width=art_width,
        art_prompt=art_prompt,
        bubble_placement=bubble_placement,
        profile_provider=profile_provider,
        profile_model=profile_model,
        commentary_provider=commentary_provider,
        commentary_model=commentary_model,
        image_art_provider=image_art_provider,
        image_art_model=image_art_model,
    )

    # Handle dump-config
    if dump_config:
        console.print(yaml.dump(config.model_dump(mode="json"), default_flow_style=False, sort_keys=False))
        raise typer.Exit()

    # Handle dry-run
    if dry_run:
        console.print("[green]Config is valid.[/green]")
        raise typer.Exit()

    # Setup logging
    _setup_logging(config, debug, verbose)

    # Determine project path
    project_path = project or str(Path.cwd())

    # Handle --show-session: print resolved paths and exit
    if show_session:
        from tpet.monitor.watcher import encode_project_path, find_newest_session

        if follow:
            console.print(f"[cyan]Follow file:[/cyan] {follow}")
        else:
            if watch_dir:
                session_dir = Path(watch_dir)
            else:
                claude_dir = Path.home() / ".claude" / "projects"
                encoded = encode_project_path(project_path)
                session_dir = claude_dir / encoded
            console.print(f"[cyan]Project path:[/cyan] {project_path}")
            console.print(f"[cyan]Session dir:[/cyan]  {session_dir}")
            if session_dir.exists():
                console.print("[green]  Directory exists[/green]")
                newest = find_newest_session(session_dir)
                if newest:
                    console.print(f"[cyan]Active session:[/cyan] {newest.name}")
                    console.print(f"[dim]  {newest}[/dim]")
                    stat = newest.stat()
                    size_kb = stat.st_size / 1024
                    import datetime

                    mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    console.print(f"[dim]  {size_kb:.1f} KB, last modified {mtime}[/dim]")
                else:
                    console.print("[yellow]  No .jsonl session files found[/yellow]")
            else:
                console.print("[red]  Directory not found[/red]")
                encoded = encode_project_path(project_path)
                console.print(f"[dim]  Expected: ~/.claude/projects/{encoded}/[/dim]")
        raise typer.Exit()

    # Import profile functions
    from tpet.models.rarity import pick_rarity
    from tpet.profile.storage import get_profile_path, save_profile

    # Determine profile path for creation / reset (no fallback)
    profile_path = get_profile_path(config.config_dir, project if project else None)

    # Handle reset
    if reset:
        if profile_path.exists():
            if yes or typer.confirm(f"Delete pet at {profile_path}?"):
                profile_path.unlink()
                console.print("[yellow]Pet deleted.[/yellow]")
        else:
            console.print("[dim]No pet to delete.[/dim]")
        raise typer.Exit()

    # Resolve creation criteria
    criteria: str | None = None
    if create_prompt_file is not None:
        if not create_prompt_file.exists():
            console.print(f"[red]Prompt file not found: {create_prompt_file}[/red]")
            raise typer.Exit(code=1)
        criteria = create_prompt_file.read_text(encoding="utf-8").strip()
    if create_prompt is not None:
        # --create-prompt overrides/appends to file content
        criteria = f"{criteria}\n{create_prompt}" if criteria else create_prompt

    if criteria and not new:
        # Auto-enable --new when creation criteria are provided
        new = True

    # Load or generate pet
    if new:
        # Creating a new pet — use the target path directly
        from tpet.profile.storage import load_profile

        pet = load_profile(profile_path)
    else:
        # Loading — resolve with global fallback
        config, profile_path, pet = _resolve_project_pet(config, project if project else None)
    if pet is None or new:
        if new and pet is not None and not yes and not typer.confirm("Replace existing pet?"):
            raise typer.Exit()

        console.print("[cyan]Generating new pet...[/cyan]")
        from tpet.profile.generator import generate_pet

        rarity = pick_rarity(config.rarity_weights)
        pet = generate_pet(config, rarity=rarity, project_path=project if project else None, criteria=criteria)
        save_profile(pet, profile_path)
        console.print(f"[green]Meet {pet.name} the {pet.creature_type}![/green]")

        from tpet.renderer.card import render_card

        console.print(render_card(pet, config=config))
        raise typer.Exit()

    # Handle art regeneration
    if regen_art:
        console.print(f"[cyan]Regenerating art for {pet.name}...[/cyan]")
        from tpet.profile.generator import regenerate_art

        pet.ascii_art = regenerate_art(config, pet)
        save_profile(pet, profile_path)

        # Delete old graphical art so renderer falls back to ASCII
        from tpet.art.storage import delete_halfblock_art, delete_png_frames

        art_base = config.pet_data_dir
        delete_png_frames(art_base, pet.name)
        delete_halfblock_art(art_base, pet.name)

        console.print(f"[green]New art generated for {pet.name}![/green]")

        from tpet.renderer.card import render_card

        console.print(render_card(pet, config=config))
        raise typer.Exit()

    # Handle art generation
    if gen_art or base_image is not None:
        # Validate base image if provided
        if base_image is not None and not base_image.exists():
            console.print(f"[red]Base image not found: {base_image}[/red]")
            raise typer.Exit(code=1)

        mode_label = config.art_mode.replace("-", " ").title()
        if base_image:
            console.print(f"[cyan]Generating {mode_label} art for {pet.name} from {base_image.name}...[/cyan]")
        else:
            console.print(f"[cyan]Generating {mode_label} art for {pet.name}...[/cyan]")
        from tpet.art.generator import generate_art

        def _on_progress(current: int, total: int, label: str) -> None:
            console.print(f"  [dim]({current}/{total})[/dim] Generating [bold]{label}[/bold]...")

        try:
            frames, gen_result = generate_art(
                config,
                pet,
                on_progress=_on_progress,
                base_image_path=base_image,
            )
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from None
        if frames:
            console.print(f"[green]{mode_label} art generated for {pet.name}! ({len(frames)} frames)[/green]")
            if gen_result:
                u = gen_result.usage
                cost = u.estimated_cost_usd
                console.print(
                    f"  [dim]API calls: {gen_result.api_calls} | "
                    f"Tokens: {u.total_tokens:,} "
                    f"(input: {u.input_tokens:,}, output: {u.output_tokens:,}) | "
                    f"Est. cost: ${cost:.3f}[/dim]"
                )
            _preview_frames(config, pet.name, len(frames))
        else:
            console.print(f"[red]Failed to generate {mode_label} art.[/red]")
        raise typer.Exit()

    # Handle details mode
    if details:
        from tpet.renderer.card import render_card

        console.print(render_card(pet, show_backstory=backstory, config=config))
        raise typer.Exit()

    # Default: run the live display
    from tpet.app import run_app

    run_app(
        config=config,
        pet=pet,
        profile_path=profile_path,
        project_path=project_path,
        watch_dir=watch_dir,
        follow_file=follow,
        quiet=quiet,
    )
