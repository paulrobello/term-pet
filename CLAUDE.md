# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Development Commands

```bash
make checkall     # Run all checks: format, lint, typecheck, test (use before commits)
make test         # uv run pytest
make lint         # uv run ruff check --fix .
make fmt          # uv run ruff format .
make typecheck    # uv run pyright
make build        # uv build
make clean        # Remove dist/, build/, caches

# Run a single test
uv run pytest tests/test_parser.py::TestParseJsonlLine::test_parse_user_message -v

# Run with debug logging
uv run tpet --debug
```

## Architecture

**Entry point**: `tpet = "tpet.cli:app"` (Typer CLI, installed as `term-pet` on PyPI)

The CLI uses subcommands (`tpet new`, `tpet details`, `tpet art`, `tpet run`) with a backward-compatible root callback that also accepts `--new`, `--details`, `--gen-art`, `--regen-art`, and `--reset` flags. Run mode (default) supports two input sources: Claude Code JSONL sessions (default) or plain text file following (`--follow`). Art mode is selected with `--art-mode` (`ascii`/`sixel-art`); `tpet art` generates and saves graphical frames using the configured image art provider (openai, openrouter, or gemini).

Each pipeline (profile, commentary, image_art) has independent provider/model/API key configuration via `PipelineProviderConfig` on `TpetConfig`. Supported providers: `claude`, `ollama`, `openai`, `openrouter`, `gemini` (`LLMProvider` enum). Claude uses Agent SDK; all others use OpenAI-compatible or provider-specific SDKs.

### Core Loop (app.py)

The live display runs at 4fps with this cycle:
1. Drain `SessionEvent` queue from watchdog-based `SessionWatcher` or `TextFileWatcher`
2. Generate commentary via configured LLM provider if cooldown elapsed and budget remains
3. Check for idle chatter when no events
4. Advance `PetAnimator` state machine (IDLE→REACTING→SLEEPING)
5. Update Rich Live display

### Module Dependencies

```
cli.py → app.py → SessionWatcher or TextFileWatcher (monitor/)
                 → PetAnimator (animation/)
                 → submit_comment/submit_idle_chatter (commentary/)
                 → Renderer protocol (renderer/protocol.py)
                 → build_display_layout (renderer/display.py)
       → generate_art (art/)
       → preview_frames (renderer/preview.py)
       → llm_client.py (shared OpenAI-compatible client factory)
```

- **models/**: `PetProfile` (Pydantic), `Rarity` (StrEnum with stat_range/color/stars), `StatConfig`
- **profile/**: Pet generation via configurable LLM provider (claude/ollama/openai/openrouter/gemini) + YAML storage
- **monitor/**: Watchdog file watchers — `watcher.py` for JSONL sessions, `text_watcher.py` for plain text files, `parser.py` for JSONL parsing
- **commentary/**: Multi-provider comment generation (claude via Agent SDK, ollama/openai/openrouter via OpenAI-compat client, gemini via genai SDK) with prompt builders, `ThreadPoolExecutor`-based background generation, `SessionUsage` tracking
- **llm_client.py**: Shared `create_openai_client()` factory and `generate_text_openai_compat()` helper used by commentary and profile pipelines for OpenAI-compatible providers
- **renderer/**: `protocol.py` (Renderer protocol + `AsciiRenderer`, `HalfblockRenderer` implementations), `display.py` (Rich layouts for ASCII/halfblock), `card.py` (details card), `preview.py` (post-generation frame preview), `statbars.py` (stat bar rendering)
- **art/**: Graphical art generation and rendering — `generator.py` (orchestration + dispatch), `storage.py` (PNG/.hblk file I/O), `client.py` (Gemini client), `openai_client.py` (OpenAI client), `process.py` (sprite splitting, chroma key removal, `create_blink_frame()`, half-block conversion), `detect.py` (terminal capability detection)
- **io.py**: Shared `save_yaml()`/`load_yaml()` Pydantic-to-YAML persistence utilities used by `profile/storage.py`

### LLM Provider Architecture

Each pipeline (profile, commentary, image_art) has independent provider configuration via `PipelineProviderConfig` fields on `TpetConfig`. Provider resolution fills in defaults (model, base_url, api_key_env) based on the `LLMProvider` enum and pipeline type ("text" or "image").

- **Claude** (`claude`): Uses `claude-agent-sdk` in subscription mode (no API key needed). Agent SDK `query()` is an async generator -- collect `ResultMessage` instances. `output_format` with `json_schema` does NOT work -- use prompt-based JSON output.
- **Ollama / OpenAI / OpenRouter**: Use the shared OpenAI-compatible client from `llm_client.py` (`create_openai_client()` + `generate_text_openai_compat()`). Commentary uses the shared helper; profile generation has its own `_generate_text_openai_compat()` that raises on error instead of returning None. Ollama accepts any non-empty API key string.
- **Gemini**: Uses `google.genai` SDK directly with `api_key_env` (default: `GEMINI_API_KEY`). Both commentary and profile pipelines have independent `_generate_text_gemini()` implementations.

Critical Agent SDK options (used when provider is `claude`):

```python
ClaudeAgentOptions(
    model=resolved.model,
    system_prompt=prompt,
    allowed_tools=[],        # No tool access
    max_turns=1,             # Single turn for both commentary and pet generation
    permission_mode="dontAsk",
    setting_sources=[],      # Prevent user plugins from loading
    plugins=[],              # Prevent user plugins from loading
)
```

- Pet generation includes `_extract_json()` / `_parse_json_response()` to strip markdown fences and retry logic (3 attempts) for all providers
- Commentary output is post-processed by `_clean_comment()` (first line only, truncated to `max_comment_length`, default 150)
- Image art generation supports `openai`, `openrouter`, and `gemini` providers (Ollama and Claude cannot generate images)

### Commentary Threading Model

Commentary generation runs in a background `ThreadPoolExecutor` (single worker) to avoid blocking the 4fps display loop:

- `submit_comment()` / `submit_idle_chatter()` return `Future[str | None]` immediately
- The app loop checks `future.done()` each tick and harvests results
- Each worker calls `asyncio.run()` internally (one event loop per thread)
- `SessionUsage` (dataclass) accumulates input/output tokens, cost, and API call count across all commentary calls (thread-safe via `_usage_lock`)
- Session summary with token usage and estimated cost is printed on exit

### Animation Engine

- **States**: `IDLE` -> `REACTING` -> `SLEEPING` (via `PetAnimator` state machine)
- **Frame layouts**: `FRAME_COUNT_LEGACY = 4` (2x2 sprite sheet) and `FRAME_COUNT_CURRENT = 6` (2x3 sprite sheet with blink variants)
- **6-frame layout**: idle, idle-shift, idle-blink, idle-shift-blink, reaction, sleeping
- **Blink**: Own 0.4s timer (`_BLINK_DURATION`), 15% chance per idle frame advance (`_BLINK_CHANCE`)
- **Blink frames are created programmatically** via `create_blink_frame()` in `process.py` -- composites closed-eye pixels from the sleep frame onto the idle frame using face-region detection and pixel differencing (not generated via AI edit API)

### Data Flow for Commentary

**Claude Code mode (default):**
```
JSONL file change → watchdog → _process_file() → parse_jsonl_line()
→ SessionEvent(role, summary) → Queue → app loop → submit_comment()
→ Future → ThreadPoolExecutor → _call_llm() → provider dispatch
→ (Agent SDK / OpenAI-compat / Gemini) → _clean_comment()
→ Rich Markdown speech bubble
```

**Plain text file mode (`--follow`):**
```
Text file change → watchdog → _process_new_lines()
→ SessionEvent(role="text", summary) → Queue → app loop → submit_comment()
→ Future → ThreadPoolExecutor → _call_llm() → provider dispatch
→ (Agent SDK / OpenAI-compat / Gemini) → _clean_comment()
→ Rich Markdown speech bubble
```

The JSONL parser filters aggressively: skips `tool_use`, `tool_result`, `progress`, `system`, and other noise types. Only actual user text and substantive assistant text become events. The text file watcher emits every non-blank line as an event.

The app loop tracks the last user event. When an assistant event triggers commentary, the preceding user prompt is included for context (e.g. "The developer said: X\nThe AI assistant responded: Y").

### Renderer Protocol

The display loop delegates rendering to a `Renderer` protocol (`renderer/protocol.py`) with two implementations:

- **`AsciiRenderer`**: Updates Rich Live with `build_display_layout()` (ASCII art panel + speech bubble)
- **`HalfblockRenderer`**: Renders PNG frames as ANSI half-block art at runtime scale alongside the speech bubble via `build_halfblock_layout()`

Renderer selection happens in `app.py:_build_renderer()`. For `sixel-art` mode, it checks whether PNG or `.hblk` files exist and uses `HalfblockRenderer`; otherwise falls back to `AsciiRenderer`.

### Stats System

Stats (HUMOR, PATIENCE, CHAOS, WISDOM, SNARK) are personality-driven — the LLM generates values matching the creature's character, clamped to the rarity range. Stats are included in the commentary system prompt to influence the pet's tone. Falls back to random generation if the LLM doesn't return stats.

### Config Enums

- **`LLMProvider`** (`StrEnum`): `claude`, `ollama`, `openai`, `openrouter`, `gemini`
- **`ArtMode`** (`StrEnum`): `ascii`, `sixel-art`
- **`BubblePlacement`** (`StrEnum`): `top`, `right`, `bottom`
- **`PipelineProviderConfig`** (Pydantic): Per-pipeline provider config with `provider`, `model`, `base_url`, `api_key_env` fields; resolves defaults via `resolve(pipeline_type)`
- **`ResolvedProviderConfig`** (Pydantic): Fully-resolved config with convenience properties: `api_key`, `is_openai_compat`, `uses_agent_sdk`

### Storage

- Config: `$XDG_CONFIG_HOME/tpet/config.yaml` (Pydantic -> YAML)
- Global pet: `$XDG_CONFIG_HOME/tpet/profile.yaml`
- Project pet: `<project>/.tpet/profile.yaml`
- Logs: `$XDG_CONFIG_HOME/tpet/debug.log` (file-only, never stdout)
- Sessions monitored: `~/.claude/projects/{encoded-path}/{uuid}.jsonl`
- Art frames: `$XDG_CONFIG_HOME/tpet/<art_dir_path>/<pet-name>_frame_<N>.png` (source PNGs), `.hblk` (half-block) -- `art_dir_path` defaults to `art`
- Art prompts: `$XDG_CONFIG_HOME/tpet/<art_dir_path>/<pet-name>_prompt.txt`
- API keys: `$XDG_CONFIG_HOME/tpet/.env` (loaded automatically via `python-dotenv`; env vars like `OPENAI_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`)

## Releasing & Publishing

Before triggering the **Publish to PyPI** workflow or running `make build`:

1. **Check if the version needs a bump.** Compare the current version in `src/tpet/__init__.py` (`__version__`) against the latest published version on PyPI (`pip index versions term-pet`). If they match, bump the version first — PyPI rejects re-uploads of existing versions.
2. **Update CHANGELOG.md** if present. Add a new section for the version with entries under appropriate headings (Added / Changed / Fixed / Removed).
3. **Update README.md** if the release includes user-facing changes — new flags, changed defaults, new features, or breaking behavior.
4. **Commit the version bump + docs changes** before pushing or triggering the publish workflow.

## Code Conventions

- Python 3.13, `uv` for package management
- ruff (line-length=120), pyright (standard mode)
- Built-in generics (`list`, `dict`), union operator (`|`), `Annotated` for Typer options
- No relative imports; `pathlib.Path` for all paths; `encoding="utf-8"` on all file I/O
- TUI app — debug output goes to log file, never the terminal
