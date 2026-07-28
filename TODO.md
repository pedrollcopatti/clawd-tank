# Clawd Tank — TODO

## Status

macOS menu bar app. Claude Code hooks report session activity over a Unix
socket to an in-process daemon, which drives a status bar icon and a popover
listing every live session. 328 Python tests pass.

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

---

## Ideas / not scheduled

- **Native notification banners** — without the device, sound is the only
  ambient signal when you're not looking at the menu bar. `osascript -e 'display
  notification'` works in an unsigned bundle; `UNUserNotificationCenter` would
  need signing. Would sit behind a toggle next to Alert Sounds.
- **Click a session row to focus its terminal** — needs a way to map a session
  back to its window; the PID is already tracked.
- **Per-session mute** — silence alerts from one noisy project.
- **Animated status bar icon** — deliberately not done: a 2 Hz timer redrawing
  the status item defeats App Nap and costs battery for information you already
  have.
