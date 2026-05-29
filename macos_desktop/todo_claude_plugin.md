# Claude Code plugin for term-pet — design notes

## Goal

Let the user generate and launch their pet from inside Claude Code via slash
commands. The plugin does not replace the CLI; it is a thin wrapper that
shells out to `tpet`.

## Key constraint: `tpet run` does NOT need its own terminal

`--art-mode macos-desktop` is the path this plugin is built around:

- `tpet run --art-mode macos-desktop` spawns a separate Swift binary (the
  floating desktop pet) and talks to it over a per-session Unix domain socket
  (`src/tpet/renderer/macos_desktop.py`).
- Terminal output in that mode is minimal — the Rich `Live` display is
  basically dormant because the Swift window owns the visible pet.
- So `nohup tpet run --art-mode macos-desktop &` works: no TTY required,
  no terminal tab fighting Claude Code.

The ASCII / sixel-art / halfblock modes all render into the terminal and
cannot share a terminal with Claude Code. The plugin defaults to
`macos-desktop` for `/start`.

## What Claude Code exposes to slash commands

| Variable              | Where available                    | Notes                                          |
|-----------------------|------------------------------------|------------------------------------------------|
| `$CLAUDE_PROJECT_DIR` | Bash tool invocations              | Project cwd. Not substituted in markdown body. |
| `$CLAUDE_SESSION_ID`  | Bash invocations + markdown body   | Session UUID; usable as template var too.      |
| `transcript_path`     | Hooks only (stdin JSON)            | NOT available to slash commands.               |

tpet already derives the right `.jsonl` file from cwd alone
(`~/.claude/projects/{encoded-cwd}/{newest-uuid}.jsonl`), so
`$CLAUDE_PROJECT_DIR` is enough. `$CLAUDE_SESSION_ID` is used to scope the
PID file so `/start` and `/stop` are per-session.

Children started with `nohup ... &` / `disown` are **not** killed when the
Bash tool call returns. They are orphaned when the Claude Code session ends.

## Proposed plugin layout

```
plugin/term-pet/
├── .claude-plugin/
│   └── plugin.json
├── commands/
│   ├── new.md        # /term-pet:new [criteria]
│   ├── start.md      # /term-pet:start
│   ├── stop.md       # /term-pet:stop
│   ├── details.md    # /term-pet:details
│   ├── art.md        # /term-pet:art
│   └── reset.md      # /term-pet:reset
└── README.md
```

## Command sketches

### `/term-pet:new [criteria]`

```bash
tpet new --yes ${ARGUMENTS:+--create-prompt "$ARGUMENTS"}
```

### `/term-pet:start`

```bash
LOG="/tmp/tpet-${CLAUDE_SESSION_ID}.log"
PID="/tmp/tpet-${CLAUDE_SESSION_ID}.pid"
if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then
  echo "Pet already running (pid $(cat "$PID")). Use /term-pet:stop first."
  exit 0
fi
nohup tpet run --art-mode macos-desktop --project "$CLAUDE_PROJECT_DIR" \
  >"$LOG" 2>&1 &
echo $! > "$PID"
disown
echo "Pet started (pid $(cat "$PID")), log: $LOG"
```

### `/term-pet:stop`

```bash
PID="/tmp/tpet-${CLAUDE_SESSION_ID}.pid"
if [ -f "$PID" ]; then
  kill "$(cat "$PID")" 2>/dev/null || true
  rm -f "$PID"
  echo "Pet stopped."
else
  echo "No pet PID file for this session."
fi
```

### `/term-pet:details`, `/term-pet:art`, `/term-pet:reset`

Thin wrappers around `tpet details`, `tpet art --art-mode macos-desktop`,
`tpet new --reset --yes`.

## `plugin.json` skeleton

```json
{
  "name": "term-pet",
  "version": "0.1.0",
  "description": "Generate and launch a term-pet companion from Claude Code.",
  "author": "Simon Schmidt"
}
```

## Open questions

1. **Plugin name** — `term-pet` (matches PyPI) or `tpet` (shorter, matches CLI)?
2. **Auto-create pet on `/start` if none exists**, or require explicit `/new`?
3. **Linux/tmux fallback** — skip for now? macos-desktop is macOS-only by
   definition, so non-macOS users can't use this plugin meaningfully anyway.
4. **Installation path** — local `/plugin install ./plugin/term-pet` during
   dev, marketplace later?
5. **Preflight checks** — should `/start` verify:
   - `tpet` is on PATH
   - a pet profile exists
   - the Swift Deskpet binary exists
   - API keys for image gen are present (if pet has no art yet)
