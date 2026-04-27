# Plan: integrate sheep-screenmate into term-pet

> Plan lives at the path above per the plan-mode harness. When we execute,
> first step is to copy this file to `~/projects/term-pet/integrate.md`
> (user asked for it there).

## Context

The macOS companion pet (Swift/AppKit) lives in a separate repo
(`~/projects/sheep-screenmate`). It already talks to term-pet via a shared
config directory and a Unix socket, and term-pet's `MacosDesktopRenderer`
(`src/tpet/renderer/macos_desktop.py`) spawns the Swift binary and drives it.

The split repo creates friction:

- Two trees to keep in sync; context-switching for cross-language changes.
- Binary discovery depends on `DESKPET_BIN` or PATH, which is fragile.
- Three new asks in this round — (1) kill the pet when tpet exits,
  (2) tray icon shows session identity + cwd, (3) one pet per
  `tpet --art-mode macos-desktop` invocation — all require coordinated
  Python↔Swift changes. Monorepo makes those trivial.

Goal: fold the Swift package into term-pet under `macos_desktop/`,
rename the target, and land the three features in one pass.

## Target layout

```
term-pet/
├── src/tpet/
│   └── renderer/macos_desktop.py   # updated: per-session socket, cleanup, child tracking
├── macos_desktop/                  # NEW — Swift package
│   ├── Package.swift
│   └── Sources/Deskpet/
│       ├── main.swift              # updated: CLI flags
│       ├── AppDelegate.swift       # updated: identity-aware tray menu
│       ├── PetController.swift     # renamed from SheepController
│       ├── PetWindow.swift         # renamed from SheepWindow
│       ├── PetView.swift           # renamed from SheepView
│       ├── PetConfig.swift         # updated: socket path injected
│       ├── CommentBus.swift        # already parameterized on socket path
│       ├── CommentBubbleWindow.swift
│       ├── Sprites.swift
│       ├── WindowSurfaces.swift
│       └── Resources/frame_{0..9}.png
├── Makefile                        # NEW — build orchestration
├── integrate.md                    # this plan, copied in
└── pyproject.toml / uv.lock
```

## Steps

### 1. Import the Swift source (git subtree, squashed)

```bash
cd ~/projects/term-pet
git subtree add --prefix=macos_desktop ../sheep-screenmate main --squash
rm macos_desktop/FINDINGS.md       # already lived through its purpose
```

Single squashed commit. Clean log. Full history stays recoverable in the
sheep-screenmate remote until we delete it.

### 2. Rename the Swift target: `Sheep` → `Deskpet`

- `macos_desktop/Package.swift`: target `name: "Deskpet"`, path `"Sources/Deskpet"`.
- `git mv macos_desktop/Sources/Sheep macos_desktop/Sources/Deskpet`.
- File renames: `SheepController.swift → PetController.swift`,
  `SheepWindow.swift → PetWindow.swift`, `SheepView.swift → PetView.swift`.
- Type renames via mechanical find/replace:
  - `SheepController` → `PetController`
  - `SheepWindow` → `PetWindow`
  - `SheepView` → `PetView`
  - `SheepState` → `PetState`
- No changes needed to: `SpriteFrame`, `CommentBus`, `CommentBubbleWindow`,
  `PetConfig`, `Sprites`, `WindowSurfaces` — already pet-neutral.
- Binary output: `macos_desktop/.build/release/Deskpet`.

### 3. Swift CLI flags (enables features 2 and 3)

Current `main.swift` is 9 lines, no arg parsing. Add minimal manual parsing
(only three flags; ArgumentParser dep is overkill):

```swift
// main.swift
import AppKit

struct PetContext {
    var socketPath: String
    var session: String
    var pwd: String
}

let ctx = parseArgs(CommandLine.arguments)
PetConfig.ensureSeeded()
PetConfig.socketPath = ctx.socketPath   // new static setter

let app = NSApplication.shared
let delegate = AppDelegate(context: ctx)
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
```

Flags:
- `--socket <path>` — socket path. Default: `~/.config/tpet/display.sock`
  (backwards compat for standalone launches).
- `--session <label>` — human label shown in tray. Default: basename of `--pwd`
  or "deskpet".
- `--pwd <path>` — directory watched by the parent session. Default: `$PWD`
  of the Swift process (just for display).

Also handle `SIGTERM` to exit cleanly and unlink the socket file.

### 4. Identity-aware tray menu (feature 2)

`macos_desktop/Sources/Deskpet/AppDelegate.swift` updates:

```
🐾 (menu bar icon)

  ┌───────────────────────────────┐
  │ Syntaxel                      │ ← pet name from profile.yaml, bold
  │ session: term-pet             │ ← ctx.session
  │ cwd:     ~/projects/term-pet  │ ← ctx.pwd (with ~ collapsing)
  │ ─────────────────────────     │
  │ Quit Deskpet                  │
  └───────────────────────────────┘
```

- Info rows use `NSMenuItem` with `isEnabled = false`.
- Long paths truncated in the middle (NSFont line-break or `~/…/X/Y`).
- Quit renamed to "Quit Deskpet" for clarity.
- Button's accessibilityDescription becomes `"Deskpet: {petName} ({session})"`.

### 5. Per-invocation pet, unique socket (feature 3)

`src/tpet/renderer/macos_desktop.py` changes:

- Replace the global `SOCKET_PATH` with per-renderer path
  `~/.config/tpet/sessions/{pid}.sock`, where `pid` is the tpet process's PID
  (guaranteed unique while alive, auto-disappears on exit).
- `__init__` creates `~/.config/tpet/sessions/` if missing.
- Spawn args change:
  ```python
  subprocess.Popen(
      [bin_path,
       "--socket", str(sock_path),
       "--session", session_label,
       "--pwd", str(Path.cwd())],
      …,
  )
  ```
  where `session_label = Path.cwd().name or "deskpet"`.
- No more "probe for existing server" step — always spawn. Two tpet
  invocations = two pets. Standalone Swift launches (no tpet) still default
  to the old `display.sock` path for backwards compat.

### 6. Kill the pet when tpet exits (feature 1)

Python side (`src/tpet/renderer/macos_desktop.py`):

- `close()` extends to terminate the child + unlink socket:
  ```python
  def close(self) -> None:
      if self._sock is not None:
          try: self._sock.close()
          finally: self._sock = None
      if self._child is not None and self._child.poll() is None:
          try:
              self._child.terminate()
              self._child.wait(timeout=2)
          except subprocess.TimeoutExpired:
              self._child.kill()
          except ProcessLookupError:
              pass
      self._child = None
      try:
          self._sock_path.unlink()
      except (FileNotFoundError, AttributeError):
          pass
  ```
- Register `atexit.register(self.close)` in `__init__` as belt-and-suspenders
  for abnormal exits (uncaught exceptions before `finally:`).

Python side (`src/tpet/app.py`):

- In the `finally:` block of `run_app` (around line 257–260), call
  `renderer.close()` if the renderer has that method. Keep it duck-typed so
  AsciiRenderer/HalfblockRenderer don't need the method.
  ```python
  finally:
      watcher.stop()
      save_profile(pet, profile_path)
      close_fn = getattr(renderer, "close", None)
      if callable(close_fn):
          close_fn()
      _print_session_summary(console, comment_count)
  ```

Swift side:

- Install `SIGTERM` handler in `main.swift` that calls `NSApp.terminate(nil)`
  so the run loop exits cleanly and `CommentBus.deinit` unlinks the socket.
  (The Python side already sends SIGTERM via `Popen.terminate()`.)

### 7. Python binary discovery

Rewrite `MacosDesktopRenderer._resolve_binary()`:

```python
@staticmethod
def _resolve_binary() -> str | None:
    # 1. Explicit override (dev / CI)
    env = os.environ.get("DESKPET_BIN")
    if env and Path(env).is_file() and os.access(env, os.X_OK):
        return env
    # 2. Repo-local release build  (src/tpet/renderer/macos_desktop.py → repo root)
    repo_root = Path(__file__).resolve().parents[3]
    for candidate in (
        repo_root / "macos_desktop" / ".build" / "release" / "Deskpet",
        repo_root / "macos_desktop" / ".build" / "arm64-apple-macosx" / "release" / "Deskpet",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    # 3. PATH fallback
    for name in ("deskpet", "sheep-screenmate"):
        found = shutil.which(name)
        if found:
            return found
    return None
```

### 8. Makefile (top of term-pet repo)

```make
.PHONY: desktop desktop-clean dev test

desktop:          ## Build the macOS desktop pet (release)
	cd macos_desktop && swift build -c release

desktop-clean:
	cd macos_desktop && swift package clean

dev:              ## Run tpet with the desktop pet
	uv run tpet --art-mode macos-desktop

test:
	uv run pytest
```

### 9. Gitignore + README

- `.gitignore`: append
  ```
  macos_desktop/.build/
  macos_desktop/.swiftpm/
  macos_desktop/Package.resolved
  ```
- Top-level `README.md`: new "macOS desktop mode" section — what it is,
  `swift >= 5.9` toolchain requirement, `make desktop` once, `uv run tpet
  --art-mode macos-desktop` to run.

### 10. Retire sheep-screenmate repo

- Archive on GitHub (or delete) after verifying the import is clean.
- Leave a README pointer at term-pet.

## Verification

```bash
cd ~/projects/term-pet
make desktop                              # builds Deskpet binary in-tree

# --- Terminal A: first session
cd ~/projects/term-pet
uv run tpet --art-mode macos-desktop      # pet #1 spawns; tray shows "session: term-pet"

# --- Terminal B: second session, different cwd
cd ~/projects/some-other-project
uv run tpet --art-mode macos-desktop      # pet #2 spawns; tray shows "session: some-other-project"

# Inventory:
pgrep -lf Deskpet                         # 2 processes
ls ~/.config/tpet/sessions/               # 2 .sock files

# Drive comments: term-pet's watcher does this automatically when you edit
# files in either project's Claude Code session. For a manual sanity check:
python3 -c '
import socket, json
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect("<ONE OF THE SOCKETS>")
s.sendall((json.dumps({"state":"reacting","comment":"hello"})+"\n").encode())
'

# Ctrl-C Terminal A
pgrep -lf Deskpet                         # 1 process (pet #1 gone)
ls ~/.config/tpet/sessions/               # 1 .sock file (A's cleaned up)

# Ctrl-C Terminal B
pgrep -lf Deskpet                         # empty
ls ~/.config/tpet/sessions/               # empty
```

UI checks for feature 2:
- Click each pet's tray paw → menu shows its own pet name, session, and cwd.
- Cwd entry truncates gracefully for very long paths.
- "Quit Deskpet" in one pet's menu doesn't affect the other.

## Critical files to modify

- `src/tpet/renderer/macos_desktop.py` — per-session socket, child cleanup,
  in-tree binary discovery, new spawn args.
- `src/tpet/app.py` — call `renderer.close()` in the `finally:` of `run_app`.
- `macos_desktop/Package.swift` — renamed target.
- `macos_desktop/Sources/Deskpet/main.swift` — CLI flag parsing, SIGTERM handler.
- `macos_desktop/Sources/Deskpet/AppDelegate.swift` — identity-aware tray menu.
- `macos_desktop/Sources/Deskpet/PetConfig.swift` — accept injected socket path.
- `macos_desktop/Sources/Deskpet/PetController.swift` (renamed) — pass
  `PetConfig.socketPath` to CommentBus.
- `Makefile` — new.
- `.gitignore` — new entries.
- `README.md` — new section.

## Reused existing code (no re-implementation)

- `CommentBus.init(socketPath:handler:)` — already parameterized on path. The
  plan only adds a new caller; no socket-layer rewrite.
- `MacosDesktopRenderer._child: subprocess.Popen` — already stored; we only
  extend `close()` to use it.
- `MacosDesktopRenderer.close()` — already exists as a stub that closes the
  socket; extended here.

## Decisions that could change the plan

1. **Target name `Deskpet` — confirm or pick different.**
2. **Squash history vs. preserve every commit** — plan assumes squash.
3. **Socket filename scheme** — plan uses PID; could be random token, or
   derive from `--session` if we want human-readable filenames. PID is
   simplest and guaranteed unique.
