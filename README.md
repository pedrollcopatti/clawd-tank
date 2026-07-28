<p align="center">
  <img src="assets/app-icon.svg" width="128" height="128" alt="Clawd Tank App Icon">
</p>

<h1 align="center">Clawd Tank</h1>

A menu bar crab that watches your Claude Code sessions.

Clawd Tank lives in the macOS menu bar. An animated pixel-art crab named Clawd
reacts to what your Claude Code sessions are doing — working, thinking, stuck, or
waiting on you — and a click lists every live session with its project, what it's
running, and how long it's been at it.

Multiple sessions across multiple projects show up together, so the one that's
been sitting on a permission prompt for four minutes doesn't stay hidden behind
the terminal you're not looking at.

## How It Works

```
Claude Code hooks --> clawd-tank-notify --> Unix socket --> daemon
                                                              |
                                          +-------------------+-------------------+
                                          |                   |                   |
                                    status bar icon      session popover     alert sounds
```

1. **Claude Code hooks** (`SessionStart`, `PreToolUse`, `PostToolUse`,
   `PermissionRequest`, `PostToolUseFailure`, `PreCompact`, `Stop`,
   `StopFailure`, `Notification`, `UserPromptSubmit`, `SessionEnd`,
   `SubagentStart`, `SubagentStop`) fire on session events.
2. **clawd-tank-notify** (`~/.clawd-tank/clawd-tank-notify`) forwards each event
   to the daemon over a Unix socket. It's stdlib-only, so it adds no startup cost
   to your shell.
3. The **daemon** keeps a state machine per session, evicts sessions whose
   `claude` process is gone, and pushes a snapshot to the UI.
4. The **menu bar app** turns that into an icon, a title badge, and the popover.

## Quick Start

Grab the latest `.app` from
[Releases](https://github.com/marciogranzotto/clawd-tank/releases), unzip, and
drag it to Applications.

On first launch: right-click the crab in the menu bar → **Install Claude Code
Hooks**. Restart any running Claude Code sessions.

### Build from source

```bash
cd host
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
./build.sh --install
```

No Xcode, no Homebrew dependencies — py2app and pyobjc (which arrives with
rumps) are all it needs.

## Using It

- **Left-click** the crab — the session list.
- **Right-click** (or the popover's ⚙ Settings) — session timeout, alert sounds,
  hook installation, launch at login.

The title beside the icon appears only when it says something: `3` when three
sessions are running, `2!` when two are waiting on you. A single session shows
no number.

## Clawd's Moods

| Icon | Meaning |
|------|---------|
| 💤 Sleeping | No Claude sessions running |
| 🦀 Idle | Sessions exist, none busy |
| 💭 Thinking | Claude is working out what to do |
| 💻 Working | Running a tool |
| ❗ Waiting | Blocked on you — a permission prompt or a question |
| ✨ Error | A session stopped with an API error |

When several sessions disagree, the most actionable one wins:
`error` > `waiting` > `working` > `thinking` > `idle`. A session waiting on you
is never hidden behind busier-looking ones.

Inside the popover each row picks its own sprite from what that session is
actually doing — reading, editing, running a command, searching the web, calling
an MCP tool, or conducting subagents.

## Alert Sounds

Two cues, toggleable from the menu:

- **Attention** (Submarine) — a session just became blocked on you.
- **Done** (Glass) — Claude finished its turn.

## Session Lifecycle

Sessions are evicted when they go quiet past the configured timeout (default 10
minutes), or as soon as their `claude` process exits. Sessions *waiting on you*
are exempt from the timeout — they legitimately emit nothing while they wait.

State is persisted to `~/.clawd-tank/sessions.json`, so quitting and reopening
the app doesn't lose track of sessions that are still running.

## Tests

```bash
cd host && .venv/bin/pytest -q
```

## Icons

```bash
python3 tools/make_menubar_icons.py
```

Stdlib only. Frame stills live in `assets/clawd-frames/`; the animated SVG
masters are in `assets/svg-animations/`.

## History

Clawd Tank started as a physical notification display on a Waveshare
ESP32-C6-LCD-1.47, with this app as its control panel and an SDL2 simulator for
hardware-free use. The firmware, simulator and BLE transport were removed once
the menu bar became the whole product — the git history still has all of it.

## License

See [LICENSE](LICENSE).
