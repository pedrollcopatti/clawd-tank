"""Clawd Tank daemon — tracks Claude Code sessions from their hooks.

Reads hook events off a Unix socket, keeps a state machine per session, plays
alert sounds, and pushes a snapshot to an observer (the menu bar app). It has no
transport layer: the UI is the menu bar, in-process.
"""

import asyncio
import fcntl
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from .protocol import ASK_USER_QUESTION_TOOL
from .socket_server import SocketServer
from . import session_store
from .session_store import save_sessions, load_sessions

logger = logging.getLogger("clawd-tank")

# macOS alert sounds (played via `afplay`). Two cues:
#   - "attention": Claude needs you — approve a command (PermissionRequest) or
#                  answer a question (AskUserQuestion) → session enters "waiting".
#   - "done":      Claude finished its turn / stopped thinking → Stop hook.
# Swap the .aiff paths to change the sounds (see /System/Library/Sounds/).
ALERT_SOUNDS = {
    "attention": "/System/Library/Sounds/Submarine.aiff",
    "done": "/System/Library/Sounds/Glass.aiff",
}


PID_PATH = Path.home() / ".clawd-tank" / "daemon.pid"
LOCK_PATH = Path.home() / ".clawd-tank" / "daemon.lock"
PID_DEDUP_FRESHNESS_SECONDS = 60.0
# Trailing-edge debounce before pushing a session snapshot to the observer. A
# single Claude turn fires PreToolUse/PostToolUse back to back, and a handful of
# parallel sessions produce a dozen socket messages in well under 100 ms —
# rebuilding the UI once per event would thrash the main thread. 150 ms is below
# the perceptual threshold and collapses a burst into one rebuild.
NOTIFY_COALESCE_SECS = 0.15
# How often to sweep for the things that rot without announcing it: sessions
# whose Claude Code process has exited, and a hook socket that is no longer
# reachable by path.
LIVENESS_INTERVAL_SECONDS = 30.0


def build_session_snapshot(
    session_states: dict[str, dict],
    session_order: list[tuple[str, int]],
) -> list[dict]:
    """Build a UI-shaped view of every session, in arrival order.

    Unlike _compute_display_state(), which is device-shaped, this keeps
    session_id/project/tool_name, is not capped at four sessions, and does no
    animation mapping — picking a sprite is the UI's business.

    Returns freshly built dicts of JSON-safe scalars. Never hand a caller a
    reference into session_states: it is mutated on the daemon's asyncio thread
    while AppKit reads the snapshot on the main thread, and it holds a set. The
    copy is the thread-safety story.
    """
    snapshot = []
    for session_id, display_id in session_order:
        state = session_states.get(session_id)
        if state is None:
            continue
        snapshot.append({
            "session_id": session_id,
            "display_id": display_id,
            "project": state.get("project", ""),
            "state": state.get("state", "idle"),
            "tool_name": state.get("tool_name", ""),
            "subagents": len(state.get("subagents", ())),
            # The long-lived `claude` PID, so the UI can find the terminal or
            # editor hosting this session. None for a session restored from
            # disk that hasn't emitted an event yet: session_store drops PIDs on
            # load, because a recycled one would look alive to the liveness check.
            "pid": state.get("pid"),
            # Wall clock, not an elapsed time: the UI derives "2m 14s" on its own
            # tick. An age field here would make every snapshot differ from the
            # last and defeat the no-op suppression in _push_snapshot_soon().
            "last_event": state.get("last_event", 0.0),
        })
    return snapshot


@runtime_checkable
class DaemonObserver(Protocol):
    def on_sessions_change(self, snapshot: list[dict]) -> None: ...


def _stop_existing_daemon() -> bool:
    """Send SIGTERM to an existing daemon and wait for it to exit. Returns True if stopped."""
    if not PID_PATH.exists():
        return False
    try:
        pid = int(PID_PATH.read_text().strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True  # already dead
    except PermissionError:
        return False
    # Wait up to 3 seconds for it to release the lock
    import time
    for _ in range(30):
        try:
            os.kill(pid, 0)  # check if still alive
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    logger.warning("Existing daemon (PID %d) did not exit in time", pid)
    return False


def _acquire_lock(takeover: bool = False) -> int:
    """Acquire an exclusive file lock.

    If takeover is True (menu bar mode), stop the existing daemon first.
    If takeover is False (headless mode), exit if another daemon is running.
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if takeover:
            logger.info("Stopping existing daemon to take over...")
            _stop_existing_daemon()
            # Retry the lock
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                print("Could not acquire lock after stopping existing daemon", file=sys.stderr)
                sys.exit(1)
        else:
            os.close(fd)
            print("Another clawd-tank daemon is already running", file=sys.stderr)
            sys.exit(0)
    return fd


class ClawdDaemon:
    def __init__(
        self,
        observer: Optional["DaemonObserver"] = None,
        headless: bool = True,
        sessions_path: Optional[Path] = None,
        socket_path: Optional[Path] = None,
    ):
        self._socket = SocketServer(
            on_message=self._handle_message, socket_path=socket_path
        )
        self._running = True
        self._shutdown_event = asyncio.Event()
        self._lock_fd: int | None = None
        self._observer = observer
        self._headless = headless
        self._sessions_path = sessions_path if sessions_path is not None else session_store.SESSIONS_PATH
        loaded_states, loaded_order, loaded_next_id = load_sessions(self._sessions_path)

        # One-shot startup prune by wall-clock: if last_event is older than the
        # staleness timeout, the session almost certainly belongs to a dead Claude
        # Code process from before the daemon restarted. After this prune we switch
        # to monotonic time for runtime tracking (survives macOS sleep/wake).
        now_wall = time.time()
        stale_ids = [
            sid for sid, s in loaded_states.items()
            if now_wall - s.get("last_event", now_wall) > 600.0  # default timeout
        ]
        for sid in stale_ids:
            del loaded_states[sid]
        loaded_order = [(sid, did) for sid, did in loaded_order if sid not in stale_ids]

        now_mono = time.monotonic()
        for state in loaded_states.values():
            state["last_event_monotonic"] = now_mono
        self._session_states: dict[str, dict] = loaded_states
        self._session_order: list[tuple[str, int]] = loaded_order
        self._next_display_id: int = loaded_next_id
        self._session_staleness_timeout: float = 600.0
        self._sounds_enabled: bool = True
        self._last_snapshot: list[dict] = []
        self._snapshot_task: Optional[asyncio.Task] = None
        # _evict_stale_sessions() removed — Task 6 startup prune covers this.

    async def _handle_message(self, msg: dict) -> None:
        """Handle a message from clawd-tank-notify via the socket."""
        event = msg.get("event")
        session_id = msg.get("session_id", "")
        hook = msg.get("hook", "")
        # Store project name on session if provided
        project = msg.get("project", "")
        if project and session_id and session_id in self._session_states:
            self._session_states[session_id]["project"] = project
        elif project and session_id:
            # Will be stored after _update_session_state creates the entry
            pass

        extra = ""
        if event in ("tool_use", "tool_done", "permission", "tool_failed"):
            extra = f" tool={msg.get('tool_name', '?')}"
        elif event in ("subagent_start", "subagent_stop"):
            extra = f" agent={msg.get('agent_id', '?')[:12]}"
        elif event == "add":
            extra = f" msg={msg.get('message', '')[:30]}"
        if msg.get("source"):
            extra += f" source={msg['source']}"
        if msg.get("reason"):
            extra += f" reason={msg['reason']}"
        if msg.get("pid"):
            extra += f" pid={msg['pid']}"
        session_project = ""
        if session_id:
            s = self._session_states.get(session_id)
            if s and s.get("project"):
                session_project = f" [{s['project']}]"
            elif project:
                session_project = f" [{project}]"
        logger.info("Socket msg: event=%s hook=%s session=%s%s%s",
                     event, hook, session_id[:12], session_project, extra)

        # add/dismiss are handled for their session-state side effects only
        # (Stop → idle, StopFailure → error, Notification → confused,
        # UserPromptSubmit → thinking). Neither is a card to render.

        # --- PID-based dedup: SessionStart with PID matching a recent session = /clear ---
        if event == "session_start":
            incoming_pid = msg.get("pid")
            if incoming_pid is not None:
                now_mono = time.monotonic()
                to_evict = []
                for sid, state in self._session_states.items():
                    if sid == session_id:
                        continue
                    if state.get("pid") != incoming_pid:
                        continue
                    last_mono = state.get("last_event_monotonic", now_mono)
                    if now_mono - last_mono < PID_DEDUP_FRESHNESS_SECONDS:
                        to_evict.append(sid)
                for sid in to_evict:
                    logger.info(
                        "PID dedup: evicting session %s (PID %d reused by new session %s)",
                        sid[:12], incoming_pid, session_id[:12],
                    )
                    del self._session_states[sid]
                    self._session_order = [(s, d) for s, d in self._session_order if s != sid]

        prev_sound_state = (
            self._session_states.get(session_id, {}).get("state") if session_id else None
        )

        changed = self._update_session_state(
            event, hook, session_id,
            msg.get("agent_id", ""), msg.get("tool_name", ""),
            pid=msg.get("pid"),
        )

        # --- Alert sounds ---
        # "attention": session just entered the waiting state (PermissionRequest
        # or AskUserQuestion) — Claude needs the user to approve/answer.
        if session_id:
            new_sound_state = self._session_states.get(session_id, {}).get("state")
            if new_sound_state == "waiting" and prev_sound_state != "waiting":
                self._play_alert_sound("attention")
        # "done": Claude finished its turn / stopped thinking (Stop hook).
        if event == "add" and hook == "Stop":
            self._play_alert_sound("done")

        # Store project name after session state is created
        if project and session_id and session_id in self._session_states:
            self._session_states[session_id]["project"] = project

        if changed:
            self._persist_sessions()

        # Unconditional: _push_snapshot_soon() owns the "did anything actually
        # change?" question, so callers never have to answer it themselves.
        self._notify_sessions_changed()

    def _enter_state(
        self, session_id: str, state: str, tool_name: str, now: float, *, create: bool,
    ) -> None:
        """Set a session's state/tool_name/last_event. When create is False and the
        session does not exist, do nothing (avoids resurrecting an ended session)."""
        s = self._session_states.get(session_id)
        if s is None:
            if not create:
                return
            s = {"last_event": now}
            self._session_states[session_id] = s
        s["state"] = state
        s["tool_name"] = tool_name
        s["last_event"] = now

    def _update_session_state(
        self, event: str, hook: str, session_id: str,
        agent_id: str = "", tool_name: str = "", pid: Optional[int] = None,
    ) -> bool:
        """Update per-session state based on a received event.

        Returns True if session state or subagents changed structurally
        (not just last_event), indicating the change should be persisted.
        """
        if not session_id:
            return False
        now = time.time()
        now_mono = time.monotonic()
        prev = self._session_states.get(session_id)
        prev_state = prev["state"] if prev else None
        prev_subagents = prev.get("subagents", set()).copy() if prev else None

        if event == "session_start":
            self._session_states[session_id] = {"state": "registered", "last_event": now}
        elif event == "tool_use":
            # AskUserQuestion blocks on a human choice — surface it as a distinct
            # "waiting" state (alert animation), not as ordinary work. PreToolUse is a
            # session's first signal in some flows, so it may create the session.
            new_state = "waiting" if tool_name == ASK_USER_QUESTION_TOOL else "working"
            self._enter_state(session_id, new_state, tool_name, now, create=True)
        elif event == "tool_done":
            # PostToolUse (registered for every tool): the human responded, so
            # clear the "waiting" alert and let Claude resume. Two cases:
            #   - AskUserQuestion answered  → Claude reads the answer → thinking
            #   - a permission-gated tool the user approved finished running →
            #     Claude is back at work → working (with that tool's animation)
            # Never creates a session — a PostToolUse with no prior PreToolUse is
            # ignored so a late event can't resurrect an ended session.
            cur_s = self._session_states.get(session_id)
            if cur_s is not None:
                if cur_s["state"] == "waiting":
                    if tool_name == ASK_USER_QUESTION_TOOL:
                        cur_s["state"] = "thinking"
                    else:
                        cur_s["state"] = "working"
                        cur_s["tool_name"] = tool_name
                cur_s["last_event"] = now
        elif event == "permission":
            # PermissionRequest: Claude is blocked waiting for the human to approve a
            # tool. Same "needs you" semantics as AskUserQuestion → waiting/alert. No
            # "granted" hook exists, so this clears on the next tool_use/Stop/prompt.
            # PreToolUse always precedes a real permission prompt, so never create a
            # missing session — a late event must not resurrect an ended one.
            self._enter_state(session_id, "waiting", tool_name, now, create=False)
        elif event == "tool_failed":
            # PostToolUseFailure: a tool genuinely errored (not a non-zero shell exit).
            # A transient "that didn't work" snag → confused, lighter than the API-error
            # 'error' state. No notification card; never resurrects an ended session.
            self._enter_state(session_id, "confused", tool_name, now, create=False)
        elif event == "compact":
            if session_id in self._session_states:
                self._session_states[session_id]["last_event"] = now
        elif event == "add":
            self._session_states.setdefault(session_id, {"state": "idle", "last_event": now})
            if hook == "Stop":
                self._session_states[session_id]["state"] = "idle"
            elif hook == "Notification":
                self._session_states[session_id]["state"] = "confused"
            elif hook == "StopFailure":
                self._session_states[session_id]["state"] = "error"
            self._session_states[session_id]["last_event"] = now
        elif event == "dismiss":
            if hook == "SessionEnd":
                self._session_states.pop(session_id, None)
                self._session_order = [(sid, did) for sid, did in self._session_order if sid != session_id]
            elif hook == "UserPromptSubmit":
                self._session_states.setdefault(session_id, {"state": "thinking", "last_event": now})
                self._session_states[session_id]["state"] = "thinking"
                self._session_states[session_id]["last_event"] = now
            else:
                if session_id in self._session_states:
                    self._session_states[session_id]["last_event"] = now
        elif event == "subagent_start":
            if not agent_id:
                return False
            self._session_states.setdefault(session_id, {"state": "working", "last_event": now})
            self._session_states[session_id].setdefault("subagents", set())
            self._session_states[session_id]["subagents"].add(agent_id)
            self._session_states[session_id]["last_event"] = now
        elif event == "subagent_stop":
            if session_id in self._session_states:
                subagents = self._session_states[session_id].get("subagents")
                if subagents is not None:
                    subagents.discard(agent_id)
                self._session_states[session_id]["last_event"] = now

        # Track session order — append on first appearance
        cur = self._session_states.get(session_id)
        if cur is not None and session_id not in [sid for sid, _ in self._session_order]:
            self._session_order.append((session_id, self._next_display_id))
            self._next_display_id += 1

        # Stamp PID + monotonic on every event (only if session still exists —
        # SessionEnd may have just removed it).
        if cur is not None:
            if pid is not None:
                cur["pid"] = pid
            cur["last_event_monotonic"] = now_mono

        if cur is None:
            return prev is not None  # session was removed
        return cur["state"] != prev_state or cur.get("subagents", set()) != (prev_subagents or set())

    def _evict_stale_sessions(self) -> None:
        # Active subagents refresh last_event via PreToolUse on the parent session.
        # If last_event_monotonic is stale, subagents are dead too — safe to evict.
        # Uses monotonic time so macOS sleep doesn't trigger mass-eviction on wake.
        now_mono = time.monotonic()
        stale = [
            sid for sid, s in self._session_states.items()
            # 'waiting' sessions are blocked on the human and legitimately emit no
            # events; time-based eviction would drop the alert while the user is away.
            # A dead Claude process is still caught by the PID-liveness checker.
            if s.get("state") != "waiting"
            and now_mono - s.get("last_event_monotonic", now_mono) > self._session_staleness_timeout
        ]
        for sid in stale:
            logger.info("Evicting stale session: %s", sid[:12])
            del self._session_states[sid]
        if stale:
            self._session_order = [(sid, did) for sid, did in self._session_order if sid not in stale]
            self._persist_sessions()

    def _notify_sessions_changed(self) -> None:
        """Schedule a coalesced session snapshot push. Safe to call unconditionally.

        A pending push always reads the latest state when it fires, so callers
        never need to check whether one is already in flight.
        """
        if self._observer is None:
            return
        if self._snapshot_task is not None and not self._snapshot_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — the daemon is constructed on the main thread
            # before run() starts one. run() pushes the restored state itself.
            # Check first rather than letting create_task() raise, which would
            # leave the coroutine object unawaited.
            return
        self._snapshot_task = loop.create_task(self._push_snapshot_soon())

    async def _push_snapshot_soon(self) -> None:
        await asyncio.sleep(NOTIFY_COALESCE_SECS)
        snapshot = build_session_snapshot(self._session_states, self._session_order)
        if snapshot == self._last_snapshot:
            return
        self._last_snapshot = snapshot
        try:
            self._observer.on_sessions_change(snapshot)
        except Exception:
            # An observer that raises must never take down message handling.
            logger.exception("Observer on_sessions_change raised")

    def _persist_sessions(self) -> None:
        save_sessions(
            self._session_states,
            self._sessions_path,
            order=self._session_order,
            next_id=self._next_display_id,
        )

    async def _staleness_checker(self) -> None:
        while self._running:
            await asyncio.sleep(30)
            self._evict_stale_sessions()
            # _evict_stale_sessions() mutates state and returns nothing, so this
            # is the only place the UI can learn a session went away.
            self._notify_sessions_changed()

    def _check_liveness(self) -> list[str]:
        """Synchronous half of the liveness check — separated for testability.

        Returns the list of evicted session_ids.
        """
        dead = []
        for sid, state in self._session_states.items():
            pid = state.get("pid")
            if pid is None:
                continue
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                dead.append(sid)
            except PermissionError:
                pass  # PID belongs to another user — assume alive
        for sid in dead:
            logger.info(
                "Liveness: evicting session %s (PID %d gone)",
                sid[:12], self._session_states[sid].get("pid"),
            )
            del self._session_states[sid]
            self._session_order = [(s, d) for s, d in self._session_order if s != sid]
        if dead:
            self._persist_sessions()
        return dead

    async def _liveness_checker(self) -> None:
        """Async task: every 30s, check the things that rot without saying so.

        Sessions whose Claude Code PID is gone get evicted, and the socket is
        checked for still being reachable by path — riding the existing timer
        rather than adding a second one, since neither check needs its own.
        """
        while self._running:
            await asyncio.sleep(LIVENESS_INTERVAL_SECONDS)
            try:
                await self._socket.ensure_serving()
            except Exception:
                # Never let a rebind failure take the eviction loop with it:
                # the next tick tries again 30s later.
                logger.exception("Could not re-establish the hook socket")
            evicted = self._check_liveness()
            if evicted:
                self._notify_sessions_changed()

    def set_session_timeout(self, seconds: int) -> None:
        self._session_staleness_timeout = float(seconds)
        logger.info("Session staleness timeout set to %ds", seconds)

    def set_sounds_enabled(self, enabled: bool) -> None:
        self._sounds_enabled = bool(enabled)
        logger.info("Alert sounds %s", "enabled" if enabled else "disabled")

    def _play_alert_sound(self, kind: str) -> None:
        """Fire-and-forget macOS alert sound. Never blocks or raises."""
        if not self._sounds_enabled:
            return
        path = ALERT_SOUNDS.get(kind)
        if not path or not os.path.exists(path):
            return
        try:
            subprocess.Popen(
                ["afplay", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("Played alert sound: %s", kind)
        except Exception:
            logger.debug("Failed to play alert sound '%s'", kind, exc_info=True)

    def _write_pid(self) -> None:
        PID_PATH.parent.mkdir(parents=True, exist_ok=True)
        PID_PATH.write_text(str(os.getpid()))

    def _remove_pid(self) -> None:
        if PID_PATH.exists():
            PID_PATH.unlink()

    async def _shutdown(self) -> None:
        logger.info("Shutting down...")
        self._running = False
        self._shutdown_event.set()

        if hasattr(self, '_staleness_task'):
            self._staleness_task.cancel()
            try:
                await self._staleness_task
            except asyncio.CancelledError:
                pass

        if hasattr(self, '_liveness_task'):
            self._liveness_task.cancel()
            try:
                await self._liveness_task
            except asyncio.CancelledError:
                pass

        if self._snapshot_task is not None and not self._snapshot_task.done():
            self._snapshot_task.cancel()
            try:
                await self._snapshot_task
            except asyncio.CancelledError:
                pass
            self._snapshot_task = None

        await self._socket.stop()
        self._remove_pid()
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    async def run(self) -> None:
        """Main daemon loop."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        )

        self._lock_fd = _acquire_lock(takeover=not self._headless)
        self._write_pid()

        if self._headless:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self._shutdown()))

        await self._socket.start()

        # Push the state restored from sessions.json so the UI isn't empty on
        # launch while Claude sessions are already running.
        self._notify_sessions_changed()

        self._staleness_task = asyncio.create_task(self._staleness_checker())
        self._liveness_task = asyncio.create_task(self._liveness_checker())

        await self._shutdown_event.wait()
        logger.info("Daemon run() finished")


def main():
    """Run the daemon standalone, for debugging without the menu bar app."""
    asyncio.run(ClawdDaemon().run())


if __name__ == "__main__":
    main()
