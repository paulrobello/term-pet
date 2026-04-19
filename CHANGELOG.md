# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **EPIC rarity tier** between `RARE` and `LEGENDARY` — stats 70-95, 4 stars, color `medium_purple1`.
  `LEGENDARY` bumped from 4 to 5 stars to keep the tier progression monotonic. Default
  `rarity_weights` rebalanced from 60/25/10/5 to 60/25/10/3/2 (totals still 100).
- **Session token/cost tracking** — `SessionUsage` dataclass in `commentary/generator.py` accumulates
  input tokens, output tokens, total cost, and API call count across all LLM calls; summary displayed on exit
- **Graphical art modes** (`--art-mode pixel-art`, `--art-mode sixel-art`) — generate and display AI-drawn
  pixel art for pets using OpenAI or Gemini image models, rendered as Unicode half-blocks or sixel sequences
- `--art-mode` / `-a` CLI flag — selects display mode: `ascii` (default), `pixel-art`, or `sixel-art`
- `--art-provider` / `-P` CLI flag — selects image generation backend: `openai` (default) or `gemini`
- `--gen-art` CLI flag — generates graphical art for the current pet and saves frames to disk
- `--art-width` / `-W` CLI flag — sets maximum percentage of terminal width allocated to the art panel (1-100)
- `--art-prompt` CLI flag — custom prompt override for image generation (frame layout instructions always appended)
- `--gemini-model` / `-G` CLI flag — override the Gemini model used for image generation
- `--openai-model` / `-O` CLI flag — override the OpenAI model used for image generation
- `--regen-art` / `-A` CLI flag — regenerate ASCII art frames for the current pet without replacing its identity
- `ArtMode` and `ArtProvider` StrEnum types in `config.py` for validated art configuration
- `art/` module cluster: `generator.py`, `storage.py`, `client.py`, `openai_client.py`, `process.py`, `detect.py`
- Terminal capability detection (`art/detect.py`) — auto-detects sixel and truecolor support
- PNG frame storage at full resolution for runtime scaling (`art/storage.py`)
- Half-block ANSI rendering pipeline (`art/process.py`) — `image_to_halfblock()`, `resize_for_halfblock()`
- Chroma key removal for transparent-background sprites
- Six-frame sprite sheet support (idle, idle-shift, idle-blink, shift-blink, react, sleep) alongside the
  existing four-frame layout
- `.env` file loading from config directory for API key management
- Frame preview output after `--gen-art` showing all generated frames at half size
- `max_comment_length` and `max_idle_length` configuration fields (separate limits for event vs. idle commentary)
- `art_max_width_pct`, `art_size`, `halfblock_size`, `chroma_tolerance`, `art_dir_path`, `art_prompt`
  configuration fields

### Changed

- **Blink frames created programmatically via compositing** — `create_blink_frame()` in `art/process.py`
  transplants closed-eye pixels from the sleep frame onto idle frames using pixel-level diffing within
  the face region; eliminates AI variation and saves 2 API calls per OpenAI generation
- **Blink animation limited to 0.4s** — blink frames now display for a brief 0.4s flash instead of the
  full idle cycle duration (was 3s); controlled by `_BLINK_DURATION` constant in `animation/engine.py`
- **Background LLM calls via `ThreadPoolExecutor`** — `submit_comment()` and `submit_idle_chatter()` in
  `commentary/generator.py` dispatch LLM work to a background thread; display loop no longer freezes
  during generation
- **CLI decomposed into Typer subcommands** — `tpet new`, `tpet details`, `tpet art`, `tpet run`
  replace the previous flat flag-based interface; backward-compatible callback preserves `tpet --new` etc.
- **Renderer Protocol extraction** — `renderer/protocol.py` defines a `Renderer` Protocol with
  `AsciiRenderer`, `HalfblockRenderer`, and `PixelArtRenderer` implementations
- Default `max_comment_length` changed from 100 to 150 characters
- `run_app` art rendering adapted to support three rendering pipelines (ASCII, pixel-art, sixel-art)
- `config.py` art fields added to `TpetConfig` Pydantic model

### Fixed (Audit Remediation)

#### Security
- Prompt injection defense: untrusted session content now wrapped in XML tags with reinforcing instructions
- Path traversal protection: `log_file` and `art_dir_path` config fields validated against directory traversal
- Temp file leak: `image_to_sixel` wrapped in unified `try/finally` for cleanup
- `.env` loading now runs unconditionally (was only in `--gen-art` branch)
- `sanitize_name` strips leading dots and falls back to `"pet"` for empty results
- Broad `except Exception` narrowed to specific types across config, profile, and generator modules

#### Architecture
- Extracted `Renderer` Protocol with `AsciiRenderer`, `HalfblockRenderer`, `PixelArtRenderer` implementations
- LLM commentary calls moved to background `ThreadPoolExecutor` — display no longer freezes during generation
- Watcher handler classes now use public APIs instead of accessing private attributes
- CLI decomposed into Typer subcommands (`tpet new`, `tpet details`, `tpet art`, `tpet run`)
- `_preview_frames` moved to `renderer/preview.py`
- Deprecated `sixel_`-prefixed aliases now emit `DeprecationWarning` (removal in v0.3.0)
- Frame count constants `FRAME_COUNT_LEGACY`/`FRAME_COUNT_CURRENT` extracted to `animation/engine.py`
- BFS flood-fill queue changed from `list` to `collections.deque`
- Config overrides use `model_copy(update=...)` instead of direct mutation

#### Code Quality
- Deduplicated `_sanitize_name` — single public `sanitize_name` in `art/storage.py`
- `load_art_frame_fn` properly typed as `Callable` (was `object` with `# type: ignore`)
- `last_rendered_frame` now updated in ASCII branch (was re-rendering every tick)
- Extracted shared `_generate_gemini_sprite_frames()` helper eliminating duplicated generator code
- Comment-budget guard extracted to `_within_comment_budget()` helper
- `import re` moved to module level in `cli.py`
- `dict` type parameterized in `art/client.py`

#### Documentation
- Added "Graphical Art Modes" section and environment variables reference to README
- Created CHANGELOG.md, CONTRIBUTING.md, LICENSE (MIT)
- Updated CLAUDE.md and ARCHITECTURE.md with art module documentation
- Expanded configuration reference to all 20 fields
- Added `Field(description=...)` to PetProfile (12 fields), StatConfig (2 fields), TpetConfig art fields (10 fields)
- Fixed misleading `chroma_tolerance` comment
- Added `--regen-art` to README; removed hard-coded model version from prose

## [0.1.0] - 2025-04-01

### Added

- Initial release
- Terminal pet companion that monitors Claude Code JSONL session files
- Pet generation via Claude Agent SDK — unique name, creature type, personality, backstory, stats, ASCII art
- Five personality stats: HUMOR, PATIENCE, CHAOS, WISDOM, SNARK
- Four rarity tiers: Common, Uncommon, Rare, Legendary (weighted selection)
- ASCII art animation engine with IDLE, REACTING, and SLEEPING states (4 frames)
- Rich Live display with animated pet art and speech bubble
- Commentary generation in-character based on session events
- Idle chatter when no events occur
- `--follow` mode to tail any plain text file instead of Claude Code sessions
- `--details` / `--backstory` modes to display the full pet card
- `--new` mode with optional custom creation criteria (`--create-prompt`, `--create-prompt-file`)
- `--reset` mode to delete the current pet
- Per-project pet profiles stored at `<project>/.tpet/profile.yaml`
- Global pet profile at `$XDG_CONFIG_HOME/tpet/profile.yaml`
- YAML configuration at `$XDG_CONFIG_HOME/tpet/config.yaml`
- XDG base directory compliance via `xdg-base-dirs`
- CLI flags: `--model`, `--comment-interval`, `--idle-chatter-interval`, `--max-comments`,
  `--sleep-threshold`, `--log-level`, `--watch-dir`, `--debug`, `--verbose`, `--dry-run`, `--dump-config`

[Unreleased]: https://github.com/paulrobello/tpet/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/paulrobello/tpet/releases/tag/v0.1.0
