"""Tests for daemon session state tracking and display state computation."""

import asyncio
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from clawd_tank_daemon.daemon import ClawdDaemon, build_session_snapshot


def make_daemon(**kwargs):
    """Daemon with no transports — the menu bar is the only consumer."""
    return ClawdDaemon(**kwargs)


def snapshot(d):
    """The UI-shaped view the daemon pushes to its observer."""
    return build_session_snapshot(d._session_states, d._session_order)


def states(d):
    """Just the session states, in arrival order."""
    return [s["state"] for s in snapshot(d)]


def _add_session(d, sid, state_dict, display_id=None):
    """Helper: add a session to both _session_states and _session_order."""
    d._session_states[sid] = state_dict
    if sid not in [s for s, _ in d._session_order]:
        did = display_id if display_id is not None else d._next_display_id
        d._session_order.append((sid, did))
        if display_id is None:
            d._next_display_id += 1
        else:
            d._next_display_id = max(d._next_display_id, did + 1)


# --- Task 4: _handle_message wiring ---

@pytest.mark.asyncio
async def test_session_start_registers_session():
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    assert "s1" in d._session_states
    assert d._session_states["s1"]["state"] == "registered"

@pytest.mark.asyncio
async def test_prompt_submit_sets_thinking():
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time()}
    await d._handle_message({"event": "dismiss", "hook": "UserPromptSubmit", "session_id": "s1"})
    assert d._session_states["s1"]["state"] == "thinking"

@pytest.mark.asyncio
async def test_tool_use_sets_working():
    d = make_daemon()
    d._session_states["s1"] = {"state": "thinking", "last_event": time.time()}
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert d._session_states["s1"]["state"] == "working"

@pytest.mark.asyncio
async def test_stop_add_sets_idle():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._session_states["s1"]["state"] == "idle"

@pytest.mark.asyncio
async def test_notification_add_sets_confused():
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time()}
    await d._handle_message({
        "event": "add", "hook": "Notification", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._session_states["s1"]["state"] == "confused"

@pytest.mark.asyncio
async def test_session_end_removes_session():
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time()}
    await d._handle_message({"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"})
    assert "s1" not in d._session_states

@pytest.mark.asyncio
async def test_implicit_session_creation():
    d = make_daemon()
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert "s1" in d._session_states
    assert d._session_states["s1"]["state"] == "working"

# --- Task 5: staleness eviction and compact handling ---

def test_staleness_evicts_old_sessions():
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time() - 9999,
        "last_event_monotonic": time.monotonic() - 9999,
    }
    d._session_staleness_timeout = 1
    d._evict_stale_sessions()
    assert "s1" not in d._session_states

def test_staleness_does_not_evict_waiting_session():
    """A 'waiting' session is blocked on the human and legitimately emits no events;
    time-based eviction would wrongly drop the alert while the user is away. The
    PID-liveness checker still evicts it if the Claude process actually dies."""
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "waiting",
        "last_event": time.time() - 9999,
        "last_event_monotonic": time.monotonic() - 9999,
    }
    d._session_staleness_timeout = 1
    d._evict_stale_sessions()
    assert "s1" in d._session_states


def test_staleness_keeps_fresh_sessions():
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time(),
        "last_event_monotonic": time.monotonic(),
    }
    d._session_staleness_timeout = 600
    d._evict_stale_sessions()
    assert "s1" in d._session_states

# --- Subagent tracking ---

@pytest.mark.asyncio
async def test_subagent_start_tracks_agent_id():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    assert "a1" in d._session_states["s1"]["subagents"]

@pytest.mark.asyncio
async def test_subagent_stop_removes_agent_id():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time(), "subagents": {"a1"}}
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert "a1" not in d._session_states["s1"].get("subagents", set())

@pytest.mark.asyncio
async def test_subagent_start_creates_session_if_missing():
    d = make_daemon()
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    assert "s1" in d._session_states
    assert "a1" in d._session_states["s1"]["subagents"]

@pytest.mark.asyncio
async def test_subagent_start_refreshes_last_event():
    d = make_daemon()
    old_time = time.time() - 500
    d._session_states["s1"] = {"state": "working", "last_event": old_time}
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    assert d._session_states["s1"]["last_event"] > old_time

@pytest.mark.asyncio
async def test_subagent_stop_refreshes_last_event():
    d = make_daemon()
    old_time = time.time() - 500
    d._session_states["s1"] = {"state": "working", "last_event": old_time, "subagents": {"a1"}}
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert d._session_states["s1"]["last_event"] > old_time

@pytest.mark.asyncio
async def test_subagent_stop_for_unknown_agent_is_noop():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    # Should not crash
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "unknown"})
    assert d._session_states["s1"]["state"] == "working"

@pytest.mark.asyncio
async def test_multiple_subagents_tracked():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a2"})
    assert d._session_states["s1"]["subagents"] == {"a1", "a2"}
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert d._session_states["s1"]["subagents"] == {"a2"}


@pytest.mark.asyncio
async def test_subagent_start_with_empty_agent_id_ignored():
    """Empty agent_id must not pollute the subagents set."""
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time()}
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": ""})
    assert not d._session_states["s1"].get("subagents")


# --- Task 4 / Task 5: eviction suppression and subagent display state ---

def test_staleness_evicts_sessions_with_dead_subagents():
    """Stale sessions are evicted even if subagents exist — stale last_event_monotonic
    means subagent tool calls stopped refreshing it, so they're dead."""
    d = make_daemon()
    d._session_staleness_timeout = 1
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time() - 9999,
        "last_event_monotonic": time.monotonic() - 9999,
        "subagents": {"a1"},
    }
    d._evict_stale_sessions()
    assert "s1" not in d._session_states  # evicted — subagents are dead too


def test_staleness_keeps_sessions_with_active_subagents():
    """Sessions with active subagents stay alive because subagent tool calls
    refresh last_event_monotonic via PreToolUse on the parent session."""
    d = make_daemon()
    d._session_staleness_timeout = 600
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time(),  # fresh — subagent is active
        "last_event_monotonic": time.monotonic(),
        "subagents": {"a1"},
    }
    d._evict_stale_sessions()
    assert "s1" in d._session_states  # NOT evicted — still fresh


# --- Task 6: edge case tests and integration test ---

@pytest.mark.asyncio
async def test_session_end_clears_subagents():
    """SessionEnd removes session entirely, even with active subagents."""
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "working", "last_event": time.time(), "subagents": {"a1", "a2"},
    }
    await d._handle_message({"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"})
    assert "s1" not in d._session_states
    # Subsequent SubagentStop for orphaned agent is safe no-op
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert "s1" not in d._session_states

@pytest.mark.asyncio
async def test_duplicate_subagent_start_is_idempotent():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    assert d._session_states["s1"]["subagents"] == {"a1"}

@pytest.mark.asyncio
async def test_subagent_lifecycle():
    """Full lifecycle: subagent keeps session working while active,
    Stop doesn't clear subagents (background agents may still run),
    staleness eviction cleans up dead subagents."""
    d = make_daemon()

    # Session starts and begins working
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    assert states(d) == ["registered"]

    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert states(d) == ["working"]

    # Subagent spawned — the session reports it
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    assert snapshot(d)[0]["subagents"] == 1
    assert "a1" in d._session_states["s1"]["subagents"]

    # Stop fires — session state goes idle, but the subagent is still running
    await d._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._session_states["s1"]["state"] == "idle"
    assert "a1" in d._session_states["s1"]["subagents"]
    assert snapshot(d)[0]["subagents"] == 1

    # Subagent finishes via SubagentStop
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert not d._session_states["s1"].get("subagents")
    assert snapshot(d)[0]["subagents"] == 0

    # Staleness eviction works normally
    d._session_states["s1"]["last_event"] = time.time() - 9999
    d._session_states["s1"]["last_event_monotonic"] = time.monotonic() - 9999
    d._evict_stale_sessions()
    assert "s1" not in d._session_states
    assert snapshot(d) == []


# --- Session state persistence ---


def make_daemon_with_path(sessions_path):
    """Create a test daemon that uses a custom sessions file path."""
    d = make_daemon(sessions_path=sessions_path)
    return d


@pytest.mark.asyncio
async def test_daemon_persists_on_handle_message(tmp_path):
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    assert path.exists()
    data = json.loads(path.read_text())
    assert "s1" in data["sessions"]
    assert data["sessions"]["s1"]["state"] == "registered"


@pytest.mark.asyncio
async def test_daemon_does_not_persist_on_last_event_only_update(tmp_path):
    """tool_use when already working only updates last_event — no disk write."""
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    d._persist_sessions()  # initial save
    mtime_before = path.stat().st_mtime_ns
    import time as _time; _time.sleep(0.01)
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    mtime_after = path.stat().st_mtime_ns
    assert mtime_before == mtime_after


@pytest.mark.asyncio
async def test_daemon_persists_on_state_transition(tmp_path):
    """thinking → working is a structural change — should persist."""
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    d._session_states["s1"] = {"state": "thinking", "last_event": time.time()}
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    data = json.loads(path.read_text())
    assert data["sessions"]["s1"]["state"] == "working"


def test_daemon_persists_on_eviction(tmp_path):
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time() - 9999,
        "last_event_monotonic": time.monotonic() - 9999,
    }
    d._session_staleness_timeout = 1
    d._evict_stale_sessions()
    data = json.loads(path.read_text())
    assert "s1" not in data["sessions"]


@pytest.mark.asyncio
async def test_session_end_persists_removal(tmp_path):
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    await d._handle_message({"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"})
    data = json.loads(path.read_text())
    assert "s1" not in data["sessions"]


# --- Session order tracking ---


@pytest.mark.asyncio
async def test_session_order_tracks_arrival():
    """Sessions should be tracked in arrival order with stable display IDs."""
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "aaa"})
    await d._handle_message({"event": "session_start", "session_id": "bbb"})
    await d._handle_message({"event": "session_start", "session_id": "ccc"})
    assert d._session_order == [("aaa", 1), ("bbb", 2), ("ccc", 3)]


@pytest.mark.asyncio
async def test_session_order_removes_on_end():
    """Ending a middle session shifts later ones down."""
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "aaa"})
    await d._handle_message({"event": "session_start", "session_id": "bbb"})
    await d._handle_message({"event": "session_start", "session_id": "ccc"})
    await d._handle_message({"event": "dismiss", "session_id": "bbb", "hook": "SessionEnd"})
    assert d._session_order == [("aaa", 1), ("ccc", 3)]


@pytest.mark.asyncio
async def test_session_order_display_ids_never_reuse():
    """Display IDs increment and are never reused even after removal."""
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "aaa"})
    await d._handle_message({"event": "dismiss", "session_id": "aaa", "hook": "SessionEnd"})
    await d._handle_message({"event": "session_start", "session_id": "bbb"})
    assert d._session_order == [("bbb", 2)]


@pytest.mark.asyncio
async def test_session_order_created_on_tool_use_if_missing():
    """tool_use creates session in order if not already tracked."""
    d = make_daemon()
    await d._handle_message({"event": "tool_use", "session_id": "aaa"})
    assert len(d._session_order) == 1
    assert d._session_order[0][0] == "aaa"


# --- Task 2 new tests: display state v2 dict format ---


@pytest.mark.asyncio
async def test_snapshot_single_working_session():
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "aaa"})
    await d._handle_message({"event": "tool_use", "session_id": "aaa", "tool_name": "Edit"})
    assert snapshot(d) == [{
        "session_id": "aaa", "display_id": 1, "project": "", "state": "working",
        "tool_name": "Edit", "subagents": 0,
        "last_event": d._session_states["aaa"]["last_event"],
    }]


@pytest.mark.asyncio
async def test_snapshot_reports_subagent_count():
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "aaa"})
    await d._handle_message({"event": "tool_use", "session_id": "aaa"})
    await d._handle_message({"event": "subagent_start", "session_id": "aaa", "agent_id": "sub1"})
    assert snapshot(d)[0]["state"] == "working"
    assert snapshot(d)[0]["subagents"] == 1


@pytest.mark.asyncio
async def test_snapshot_preserves_arrival_order():
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "aaa"})
    await d._handle_message({"event": "tool_use", "session_id": "aaa"})
    await d._handle_message({"event": "session_start", "session_id": "bbb"})
    assert states(d) == ["working", "registered"]
    assert [s["display_id"] for s in snapshot(d)] == [1, 2]


@pytest.mark.asyncio
async def test_snapshot_after_middle_session_removed():
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "aaa"})
    await d._handle_message({"event": "tool_use", "session_id": "aaa"})
    await d._handle_message({"event": "session_start", "session_id": "bbb"})
    await d._handle_message({"event": "tool_use", "session_id": "bbb"})
    await d._handle_message({"event": "session_start", "session_id": "ccc"})
    await d._handle_message({"event": "tool_use", "session_id": "ccc"})
    # Remove middle
    await d._handle_message({"event": "dismiss", "session_id": "bbb", "hook": "SessionEnd"})
    assert [s["session_id"] for s in snapshot(d)] == ["aaa", "ccc"]
    # Display IDs are stable: removing the middle session doesn't renumber.
    assert [s["display_id"] for s in snapshot(d)] == [1, 3]


# --- Per-transport protocol versioning ---


# --- Per-session sweeping (Task 6) ---


class MockTransport:
    """Minimal transport stub that captures written payloads."""

    def __init__(self, name: str = "mock"):
        self.name = name
        self.is_connected = True
        self.written: list[str] = []

    async def write_notification(self, payload: str) -> None:
        self.written.append(payload)


# --- Task 2: error state and dizzy display mapping ---


@pytest.mark.asyncio
async def test_stop_failure_add_sets_error():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({
        "event": "add", "hook": "StopFailure", "session_id": "s1",
        "project": "proj", "message": "Rate limited",
    })
    assert d._session_states["s1"]["state"] == "error"


@pytest.mark.asyncio
async def test_error_state_clears_on_prompt_submit():
    d = make_daemon()
    d._session_states["s1"] = {"state": "error", "last_event": time.time()}
    await d._handle_message({"event": "dismiss", "hook": "UserPromptSubmit", "session_id": "s1"})
    assert d._session_states["s1"]["state"] == "thinking"


@pytest.mark.asyncio
async def test_error_state_clears_on_tool_use():
    d = make_daemon()
    d._session_states["s1"] = {"state": "error", "last_event": time.time()}
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert d._session_states["s1"]["state"] == "working"


@pytest.mark.asyncio
async def test_error_state_removed_on_session_end():
    d = make_daemon()
    d._session_states["s1"] = {"state": "error", "last_event": time.time()}
    await d._handle_message({"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"})
    assert "s1" not in d._session_states


def test_error_state_evicted_on_staleness():
    d = make_daemon()
    d._session_staleness_timeout = 1
    d._session_states["s1"] = {
        "state": "error",
        "last_event": time.time() - 9999,
        "last_event_monotonic": time.monotonic() - 9999,
    }
    d._evict_stale_sessions()
    assert "s1" not in d._session_states


@pytest.mark.asyncio
async def test_stop_then_stop_failure_overwrites_to_error():
    """Stop then StopFailure on same session — card overwrites, state becomes error."""
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._session_states["s1"]["state"] == "idle"
    await d._handle_message({
        "event": "add", "hook": "StopFailure", "session_id": "s1",
        "project": "proj", "message": "Rate limited",
    })
    assert d._session_states["s1"]["state"] == "error"


# --- Task 2: Tool-aware animation mapping ---


# --- AskUserQuestion → "waiting" state + alert animation ---


@pytest.mark.asyncio
async def test_ask_user_question_tool_use_sets_waiting():
    """PreToolUse for AskUserQuestion is a 'blocked on the human' moment, not work."""
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({
        "event": "tool_use", "session_id": "s1", "tool_name": "AskUserQuestion",
    })
    assert d._session_states["s1"]["state"] == "waiting"


@pytest.mark.asyncio
async def test_tool_done_askuserquestion_clears_waiting_to_thinking():
    """PostToolUse for AskUserQuestion means the user answered — Claude resumes thinking."""
    d = make_daemon()
    d._session_states["s1"] = {"state": "waiting", "last_event": time.time()}
    await d._handle_message({
        "event": "tool_done", "session_id": "s1", "tool_name": "AskUserQuestion",
    })
    assert d._session_states["s1"]["state"] == "thinking"


@pytest.mark.asyncio
async def test_tool_done_for_unknown_session_does_not_create_it():
    """A PostToolUse with no prior session must not spawn a phantom session."""
    d = make_daemon()
    await d._handle_message({
        "event": "tool_done", "session_id": "ghost", "tool_name": "AskUserQuestion",
    })
    assert "ghost" not in d._session_states


@pytest.mark.asyncio
async def test_tool_done_non_askuserquestion_leaves_non_waiting_state():
    """tool_done for a tool on a session that is NOT waiting just refreshes
    liveness, never changes state."""
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({
        "event": "tool_done", "session_id": "s1", "tool_name": "Bash",
    })
    assert d._session_states["s1"]["state"] == "working"


@pytest.mark.asyncio
async def test_waiting_clears_on_next_tool_use():
    """If Claude proceeds to a real tool after asking, the alert clears to working."""
    d = make_daemon()
    d._session_states["s1"] = {"state": "waiting", "last_event": time.time()}
    await d._handle_message({
        "event": "tool_use", "session_id": "s1", "tool_name": "Edit",
    })
    assert d._session_states["s1"]["state"] == "working"


@pytest.mark.asyncio
async def test_tool_done_clearing_waiting_is_persisted(tmp_path):
    """waiting → thinking is a structural change and must hit disk."""
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    d._session_states["s1"] = {"state": "waiting", "last_event": time.time()}
    await d._handle_message({
        "event": "tool_done", "session_id": "s1", "tool_name": "AskUserQuestion",
    })
    data = json.loads(path.read_text())
    assert data["sessions"]["s1"]["state"] == "thinking"


# --- PermissionRequest → waiting/alert (blocked on the human's approval) ---


@pytest.mark.asyncio
async def test_permission_request_sets_waiting():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time(), "tool_name": "Bash"}
    await d._handle_message({"event": "permission", "session_id": "s1", "tool_name": "Bash"})
    assert d._session_states["s1"]["state"] == "waiting"


@pytest.mark.asyncio
async def test_permission_request_does_not_create_missing_session():
    """A permission/tool_failed for an unknown session must NOT resurrect it (matches
    tool_done). PreToolUse always precedes a real permission prompt."""
    d = make_daemon()
    await d._handle_message({"event": "permission", "session_id": "ghost", "tool_name": "Bash"})
    assert "ghost" not in d._session_states


@pytest.mark.asyncio
async def test_permission_waiting_clears_on_next_tool_use():
    """No 'granted' hook fires — approval is followed by the tool running, so the
    alert clears on the next tool_use."""
    d = make_daemon()
    await d._handle_message({"event": "permission", "session_id": "s1", "tool_name": "Bash"})
    await d._handle_message({"event": "tool_use", "session_id": "s1", "tool_name": "Bash"})
    assert d._session_states["s1"]["state"] == "working"


@pytest.mark.asyncio
async def test_permission_waiting_clears_on_stop():
    d = make_daemon()
    await d._handle_message({"event": "permission", "session_id": "s1", "tool_name": "Bash"})
    await d._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1", "project": "p", "message": "x",
    })
    assert d._session_states["s1"]["state"] == "idle"


@pytest.mark.asyncio
async def test_permission_waiting_clears_when_gated_tool_finishes():
    """Bug fix: after the user approves a permission-gated tool, that tool runs
    and emits PostToolUse (now registered for all tools). The alert must clear to
    'working' right then — not linger until some later tool_use or Stop while the
    approved tool is still running."""
    d = make_daemon()
    # PreToolUse creates the session and marks it working...
    await d._handle_message({"event": "tool_use", "session_id": "s1", "tool_name": "Bash"})
    # ...then the permission prompt blocks it on the human.
    await d._handle_message({"event": "permission", "session_id": "s1", "tool_name": "Bash"})
    assert d._session_states["s1"]["state"] == "waiting"
    # The user approves; the gated tool finishes → the alert clears.
    await d._handle_message({"event": "tool_done", "session_id": "s1", "tool_name": "Bash"})
    assert d._session_states["s1"]["state"] == "working"
    assert d._session_states["s1"]["tool_name"] == "Bash"


# --- PostToolUseFailure → confused (a tool genuinely errored) ---


@pytest.mark.asyncio
async def test_post_tool_use_failure_sets_confused():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time(), "tool_name": "Read"}
    await d._handle_message({"event": "tool_failed", "session_id": "s1", "tool_name": "Read"})
    assert d._session_states["s1"]["state"] == "confused"


@pytest.mark.asyncio
async def test_tool_failed_does_not_create_missing_session():
    """A late PostToolUseFailure after SessionEnd must not resurrect a phantom session."""
    d = make_daemon()
    await d._handle_message({"event": "tool_failed", "session_id": "ghost", "tool_name": "Read"})
    assert "ghost" not in d._session_states


@pytest.mark.asyncio
async def test_tool_failed_confused_clears_on_prompt_submit():
    d = make_daemon()
    await d._handle_message({"event": "tool_failed", "session_id": "s1", "tool_name": "Read"})
    await d._handle_message({"event": "dismiss", "hook": "UserPromptSubmit", "session_id": "s1"})
    assert d._session_states["s1"]["state"] == "thinking"


# --- PID + monotonic tracking (ghost-crab fix) ---

@pytest.mark.asyncio
async def test_session_start_stamps_pid_and_monotonic():
    d = make_daemon()
    await d._handle_message({
        "event": "session_start", "session_id": "s1", "pid": 4242,
    })
    assert d._session_states["s1"]["pid"] == 4242
    assert "last_event_monotonic" in d._session_states["s1"]
    assert isinstance(d._session_states["s1"]["last_event_monotonic"], float)


@pytest.mark.asyncio
async def test_tool_use_refreshes_pid_and_monotonic():
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "working", "last_event": 1.0,
        "pid": 1111, "last_event_monotonic": 0.0,
    }
    await d._handle_message({
        "event": "tool_use", "session_id": "s1", "tool_name": "Edit", "pid": 4242,
    })
    assert d._session_states["s1"]["pid"] == 4242
    assert d._session_states["s1"]["last_event_monotonic"] > 0.0


@pytest.mark.asyncio
async def test_message_without_pid_field_does_not_crash():
    """Backwards-compat: old notify script sends no pid; daemon must cope."""
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    assert "s1" in d._session_states
    # pid should be None (or absent) — explicit absence, not error
    assert d._session_states["s1"].get("pid") is None


# --- Task 7: monotonic-based staleness eviction ---


def test_staleness_uses_monotonic_not_wall_clock():
    """A session with old wall-clock last_event but fresh monotonic time
    should NOT be evicted — covers macOS sleep/wake scenario."""
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time() - 9999,            # ancient wall clock
        "last_event_monotonic": time.monotonic(),    # but fresh monotonic
    }
    d._session_staleness_timeout = 600
    d._evict_stale_sessions()
    assert "s1" in d._session_states, "monotonic-fresh session evicted incorrectly"


def test_staleness_evicts_when_monotonic_old():
    """Session with old monotonic time is evicted regardless of wall clock."""
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time(),
        "last_event_monotonic": time.monotonic() - 9999,
    }
    d._session_staleness_timeout = 1
    d._evict_stale_sessions()
    assert "s1" not in d._session_states


# --- PID-based /clear dedup (ghost-crab fix) ---

@pytest.mark.asyncio
async def test_session_start_evicts_prior_session_with_same_pid_recent():
    """SessionStart with PID that matches a recently-active session = /clear case.
    Old session is evicted in the same _handle_message call."""
    d = make_daemon()
    d._session_states["old"] = {
        "state": "idle",
        "last_event": time.time(),
        "last_event_monotonic": time.monotonic(),  # very recent
        "pid": 4242,
    }
    d._session_order = [("old", 1)]
    d._next_display_id = 2

    await d._handle_message({"event": "session_start", "session_id": "new", "pid": 4242})

    assert "old" not in d._session_states, "/clear dedup did not evict old session"
    assert "new" in d._session_states
    assert d._session_order == [("new", 2)], "session_order not scrubbed/updated"


@pytest.mark.asyncio
async def test_session_start_does_NOT_evict_stale_session_with_same_pid():
    """If the matching session is >60s old (monotonic), assume PID recycle, no eviction."""
    d = make_daemon()
    d._session_states["old"] = {
        "state": "idle",
        "last_event": time.time(),
        "last_event_monotonic": time.monotonic() - 120,  # 2 min old
        "pid": 4242,
    }
    d._session_order = [("old", 1)]
    d._next_display_id = 2

    await d._handle_message({"event": "session_start", "session_id": "new", "pid": 4242})

    assert "old" in d._session_states, "PID-recycle dedup wrongly evicted stale session"
    assert "new" in d._session_states


@pytest.mark.asyncio
async def test_session_start_without_pid_does_not_dedup():
    """Backwards-compat: old notify script sends no pid; dedup is skipped safely."""
    d = make_daemon()
    d._session_states["old"] = {
        "state": "idle",
        "last_event": time.time(),
        "last_event_monotonic": time.monotonic(),
        "pid": 4242,
    }
    d._session_order = [("old", 1)]
    d._next_display_id = 2

    await d._handle_message({"event": "session_start", "session_id": "new"})  # no pid

    assert "old" in d._session_states
    assert "new" in d._session_states


@pytest.mark.asyncio
async def test_dedup_only_fires_on_session_start():
    """A tool_use event with matching PID does NOT trigger dedup."""
    d = make_daemon()
    d._session_states["old"] = {
        "state": "idle",
        "last_event": time.time(),
        "last_event_monotonic": time.monotonic(),
        "pid": 4242,
    }
    d._session_order = [("old", 1)]
    d._next_display_id = 2

    await d._handle_message({"event": "tool_use", "session_id": "new", "pid": 4242, "tool_name": "Edit"})

    assert "old" in d._session_states
    assert "new" in d._session_states


# --- Liveness polling (ghost-crab fix) ---

def test_liveness_evicts_dead_pid():
    """Session whose stored PID raises ProcessLookupError on kill(pid, 0) is evicted."""
    from unittest.mock import patch
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle", "last_event": time.time(),
        "last_event_monotonic": time.monotonic(), "pid": 4242,
    }
    d._session_order = [("s1", 1)]

    with patch("clawd_tank_daemon.daemon.os.kill", side_effect=ProcessLookupError):
        d._check_liveness()

    assert "s1" not in d._session_states
    assert d._session_order == []


def test_liveness_keeps_alive_pid():
    """Session whose PID is alive (kill returns) stays."""
    from unittest.mock import patch
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle", "last_event": time.time(),
        "last_event_monotonic": time.monotonic(), "pid": 4242,
    }

    with patch("clawd_tank_daemon.daemon.os.kill", return_value=None):
        d._check_liveness()

    assert "s1" in d._session_states


def test_liveness_skips_sessions_without_pid():
    """No-pid sessions (e.g. post-restart, pre-first-event) are skipped, not evicted."""
    from unittest.mock import patch
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle", "last_event": time.time(),
        "last_event_monotonic": time.monotonic(),
    }

    with patch("clawd_tank_daemon.daemon.os.kill", side_effect=ProcessLookupError):
        d._check_liveness()

    assert "s1" in d._session_states


def test_liveness_treats_permission_error_as_alive():
    """If kill raises PermissionError (PID belongs to another user), assume alive."""
    from unittest.mock import patch
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle", "last_event": time.time(),
        "last_event_monotonic": time.monotonic(), "pid": 4242,
    }

    with patch("clawd_tank_daemon.daemon.os.kill", side_effect=PermissionError):
        d._check_liveness()

    assert "s1" in d._session_states


# --- Startup load, prune and persistence ---

def test_daemon_loads_on_init(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "s1": {"state": "working", "last_event": time.time()},
    }))
    d = make_daemon(sessions_path=path)
    assert "s1" in d._session_states
    assert d._session_states["s1"]["state"] == "working"


def test_daemon_loads_subagents_as_sets(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "s1": {
            "state": "idle",
            "last_event": time.time(),
            "subagents": ["a1", "a2"],
        },
    }))
    d = make_daemon(sessions_path=path)
    assert d._session_states["s1"]["subagents"] == {"a1", "a2"}
    assert isinstance(d._session_states["s1"]["subagents"], set)


def test_daemon_evicts_stale_sessions_on_startup(tmp_path):
    """Stale sessions from disk are evicted immediately, not after 30s."""
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "stale": {"state": "working", "last_event": time.time() - 9999},
        "fresh": {"state": "idle", "last_event": time.time()},
    }))
    d = make_daemon(sessions_path=path)
    assert "stale" not in d._session_states
    assert "fresh" in d._session_states


def test_init_stamps_monotonic_on_loaded_sessions(tmp_path):
    """After daemon restart, loaded sessions get fresh last_event_monotonic."""
    from clawd_tank_daemon.session_store import save_sessions
    sessions_path = tmp_path / "sessions.json"
    save_sessions({"s1": {"state": "idle", "last_event": time.time()}}, sessions_path)

    from clawd_tank_daemon.daemon import ClawdDaemon
    d = make_daemon(sessions_path=sessions_path)

    assert "s1" in d._session_states
    assert "last_event_monotonic" in d._session_states["s1"]
    assert isinstance(d._session_states["s1"]["last_event_monotonic"], float)


def test_init_prunes_wall_clock_stale_sessions(tmp_path):
    """Startup prune: sessions with wall-clock last_event older than 10min
    are removed at init (their Claude Code process is almost certainly dead)."""
    from clawd_tank_daemon.session_store import save_sessions
    sessions_path = tmp_path / "sessions.json"
    save_sessions(
        {
            "fresh": {"state": "idle", "last_event": time.time()},
            "stale": {"state": "idle", "last_event": time.time() - 3600},  # 1h ago
        },
        sessions_path,
        order=[("fresh", 1), ("stale", 2)],
        next_id=3,
    )

    from clawd_tank_daemon.daemon import ClawdDaemon
    d = make_daemon(sessions_path=sessions_path)

    assert "fresh" in d._session_states
    assert "stale" not in d._session_states
    assert d._session_order == [("fresh", 1)]


def test_liveness_persists_after_eviction(tmp_path):
    """After evicting a dead session, the persisted sessions.json reflects it."""
    from unittest.mock import patch
    from clawd_tank_daemon.daemon import ClawdDaemon

    d = make_daemon(sessions_path=tmp_path / "sessions.json")
    d._session_states["s1"] = {
        "state": "idle", "last_event": time.time(),
        "last_event_monotonic": time.monotonic(), "pid": 4242,
    }
    d._session_order = [("s1", 1)]

    with patch("clawd_tank_daemon.daemon.os.kill", side_effect=ProcessLookupError):
        d._check_liveness()

    # Persist file should not contain s1
    import json as _json
    raw = _json.loads((tmp_path / "sessions.json").read_text())
    assert "s1" not in raw.get("sessions", {})
