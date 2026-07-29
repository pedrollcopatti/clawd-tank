# Clawd Tank — TODO

## Status

macOS menu bar app. Claude Code hooks report session activity over a Unix
socket to an in-process daemon, which drives a status bar icon and a popover
listing every live session. 406 Python tests pass.

The ESP32 device, its firmware, the SDL2 simulator and the BLE transport were
removed — the menu bar is the whole product now. Git history has the hardware
era if you need it.

---

## Menu bar widget — Complete

- [x] **Session snapshot** — `build_session_snapshot()` gives the UI a view that
  keeps `session_id`/`project`/`tool_name`, isn't capped at four sessions, and
  does no animation mapping. Pushes are debounced 150 ms on the trailing edge
  and suppressed when nothing observable changed, so a burst of hook events from
  one Claude turn rebuilds the UI once.
- [x] **Status bar icon** — driven by aggregate session state, priority
  `error` > `waiting` > `working` > `thinking` > `idle` > none. Colour, not a
  template image, at 44x44. Title badge only when it carries information.
- [x] **Popover** — one row per session: sprite, project, detail text, elapsed
  time, subagent badge, and an accent stripe for waiting/error. Scrolls past
  ~4.5 rows. Refresh timer runs only while it's open.
- [x] **Click routing** — rumps' `NSMenu` is detached on `before_start`;
  left-click opens the popover, right-click re-attaches the menu and pops it.
- [x] **Alert sounds** — Submarine on becoming blocked, Glass on turn end.
- [x] **Preferences** — session timeout and alert sounds, with a one-shot prune
  of the obsolete `ble_`/`sim_` keys.
- [x] **Icon pipeline** — `tools/make_menubar_icons.py`, stdlib only, from the
  frame stills in `assets/clawd-frames/`.

## Session tracking — Complete

- [x] Per-session state machine: `registered` / `thinking` / `working` / `idle` /
  `waiting` / `confused` / `error`.
- [x] Staleness eviction on monotonic time (survives sleep/wake); `waiting`
  sessions exempt, since they legitimately emit nothing while blocked on you.
- [x] PID liveness: a session whose `claude` process is gone is evicted within 30 s.
- [x] PID-based dedup so `/clear` doesn't leave a ghost session behind.
- [x] Subagent lifecycle tracking via `SubagentStart`/`SubagentStop`.
- [x] Atomic persistence to `~/.clawd-tank/sessions.json`, pruned on load.
- [x] `PostToolUse` registered for every tool, so the "waiting" alert clears the
  moment an approved permission-gated tool finishes.

## Hooks — Complete

- [x] Embedded stdlib-only `clawd-tank-notify`, versioned by a marker comment.
- [x] Matcher-aware, non-clobbering merge into `~/.claude/settings.json`; user
  hooks are never touched, and Clawd's own stale groups are pruned.
- [x] Auto-update on app launch when the installed version is outdated.

## Reliability — Complete

- [x] **The socket is verified, not assumed.** `SocketServer` remembers the
  `(st_dev, st_ino)` of the socket file it created. `stop()` unlinks only that
  file, and the 30 s liveness tick rebinds if it has gone missing — leaving a
  file that someone else is actively listening on alone, since a takeover isn't
  a fault. Losing the file is the app's worst failure mode: the daemon keeps its
  listening fd and looks perfectly healthy from the inside while every hook gets
  ENOENT and the menu bar quietly stops knowing anything.
- [x] **Tests never write to `~/.clawd-tank/`.** conftest redirects the sessions
  file, socket, PID file and lock into a temp dir. Running `pytest` used to
  unlink the live app's socket — via a daemon that had never started, whose
  `stop()` deleted the path anyway — and the menu bar stayed deaf for a day.
- [x] **A closed popover keeps its snapshot current.** It renders from its own
  copy on open, and updates that arrived while it was closed used to be
  dropped — so it opened onto whatever was on screen last time. Rebuilding
  views nobody is looking at is still skipped; only the data is handed over.
- [x] **Log file pinned to UTF-8.** The bundle's locale left the stream ASCII,
  so `logging` silently dropped every record naming a project like "gestão".
  The log read as though the daemon had never received those events.

---

## Ideas / not scheduled

- **Native notification banners** — without the device, sound is the only
  ambient signal when you're not looking at the menu bar. `osascript -e 'display
  notification'` works in an unsigned bundle; `UNUserNotificationCenter` would
  need signing. Would sit behind a toggle next to Alert Sounds.
- **Per-session mute** — silence alerts from one noisy project.
- **Animated status bar icon** — deliberately not done: a 2 Hz timer redrawing
  the status item defeats App Nap and costs battery for information you already
  have.
