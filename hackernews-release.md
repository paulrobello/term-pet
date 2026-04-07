# Hacker News Post

## Title (max 80 chars)

Show HN: Tpet -- A terminal pet that roasts your coding sessions

## URL

https://github.com/paulrobello/term-pet

## Comment / Description

Hey HN, I built a terminal pet that watches you code and comments on what you're doing.

It started because I wanted something in the tmux pane next to my editor that wasn't just a clock. The pet generates its own personality, ASCII art, backstory, and stats (HUMOR, PATIENCE, CHAOS, WISDOM, SNARK) via LLM. Then it monitors your Claude Code session JSONL files in real time and produces in-character commentary about whatever you're working on.

Key bits:
- Default mode uses your Claude Code subscription through the Agent SDK, no API key needed
- Supports Ollama, OpenAI, OpenRouter, and Gemini as alternative providers
- ASCII mode works in any terminal, halfblock/sixel modes for fancier terminals
- Each pipeline (profile gen, commentary, image art) can use a different provider/model
- Rarity system -- your pet might be Common or Legendary, which determines stat ranges
- Runs at 4fps with background threading so commentary generation doesn't block rendering
- Python 3.13, MIT license

It's been surprisingly motivating to have a Legendary creature with 95 SNARK judge my refactoring decisions in real time.

Install: uv tool install term-pet (or uvx --from term-pet tpet to try without installing)

Repo: https://github.com/paulrobello/term-pet

Happy to answer questions about the architecture or the Agent SDK integration.
