"""Tests for build_session_snapshot() — the UI-shaped view of session state."""

import time

import pytest

from clawd_tank_daemon.daemon import ClawdDaemon, build_session_snapshot


def _state(**overrides):
    base = {
        "state": "working",
        "last_event": 1_700_000_000.0,
        "tool_name": "Bash",
        "project": "clawd-tank",
        "subagents": set(),
    }
    base.update(overrides)
    return base


def test_empty_state_gives_empty_snapshot():
    assert build_session_snapshot({}, []) == []


def test_carries_every_ui_field():
    states = {"s1": _state(subagents={"a", "b"})}
    order = [("s1", 7)]

    assert build_session_snapshot(states, order) == [{
        "session_id": "s1",
        "display_id": 7,
        "project": "clawd-tank",
        "state": "working",
        "tool_name": "Bash",
        "subagents": 2,
        "last_event": 1_700_000_000.0,
    }]


def test_follows_session_order_not_dict_order():
    states = {
        "s1": _state(project="first"),
        "s2": _state(project="second"),
        "s3": _state(project="third"),
    }
    order = [("s3", 3), ("s1", 1), ("s2", 2)]

    projects = [s["project"] for s in build_session_snapshot(states, order)]
    assert projects == ["third", "first", "second"]


def test_skips_order_entries_whose_session_is_gone():
    """_session_order can outlive a deleted session; the snapshot must not
    invent an entry for it (mirrors the guard in _compute_display_state)."""
    states = {"s1": _state()}
    order = [("s1", 1), ("ghost", 2)]

    snapshot = build_session_snapshot(states, order)
    assert [s["session_id"] for s in snapshot] == ["s1"]


def test_missing_fields_fall_back_to_defaults():
    """A session restored from an older sessions.json may lack project/tool."""
    states = {"s1": {"state": "idle", "last_event": 5.0}}

    assert build_session_snapshot(states, [("s1", 1)]) == [{
        "session_id": "s1",
        "display_id": 1,
        "project": "",
        "state": "idle",
        "tool_name": "",
        "subagents": 0,
        "last_event": 5.0,
    }]


def test_not_capped_at_four_unlike_display_state():
    """The device shows at most four Clawds; the UI shows every session."""
    states = {f"s{i}": _state(project=f"p{i}") for i in range(6)}
    order = [(f"s{i}", i + 1) for i in range(6)]

    snapshot = build_session_snapshot(states, order)
    assert len(snapshot) == 6

    d = ClawdDaemon(sim_only=True)
    d._session_states = states
    d._session_order = order
    device_state = d._compute_display_state()
    assert len(device_state["anims"]) == 4
    assert device_state["overflow"] == 2


def test_snapshot_shares_no_mutable_state_with_the_daemon():
    """The daemon mutates _session_states on the asyncio thread while AppKit
    reads the snapshot on the main thread — the copy is the thread-safety story."""
    states = {"s1": _state(subagents={"a"})}
    order = [("s1", 1)]

    snapshot = build_session_snapshot(states, order)
    snapshot[0]["project"] = "mutated"
    snapshot[0]["subagents"] = 99

    assert states["s1"]["project"] == "clawd-tank"
    assert states["s1"]["subagents"] == {"a"}


def test_subagents_is_a_count_not_a_set():
    """A set is not JSON-safe and would leak a mutable reference."""
    states = {"s1": _state(subagents={"a", "b", "c"})}

    entry = build_session_snapshot(states, [("s1", 1)])[0]
    assert entry["subagents"] == 3
    assert isinstance(entry["subagents"], int)


@pytest.mark.asyncio
async def test_reflects_real_session_lifecycle():
    """End-to-end through _handle_message rather than hand-built dicts."""
    d = ClawdDaemon(sim_only=True)
    await d._handle_message({
        "event": "session_start", "session_id": "s1", "project": "clawd-tank",
    })
    await d._handle_message({
        "event": "tool_use", "session_id": "s1", "tool_name": "Bash",
    })

    snapshot = build_session_snapshot(d._session_states, d._session_order)
    assert len(snapshot) == 1
    assert snapshot[0]["project"] == "clawd-tank"
    assert snapshot[0]["state"] == "working"
    assert snapshot[0]["tool_name"] == "Bash"
    assert snapshot[0]["display_id"] == 1
    assert snapshot[0]["last_event"] <= time.time()
