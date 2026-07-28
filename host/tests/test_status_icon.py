"""Tests for the status bar icon/title state machine.

Pure logic only — status_icon.py must not import AppKit, so these run anywhere.
"""

import importlib.resources

import pytest

from clawd_tank_menubar.status_icon import (
    ICON_FILES,
    aggregate_state,
    icon_name,
    status_title,
)


def sessions(*states):
    return [
        {"session_id": f"s{i}", "display_id": i + 1, "project": "p",
         "state": state, "tool_name": "", "subagents": 0, "last_event": 0.0}
        for i, state in enumerate(states)
    ]


# --- aggregate_state ---


def test_no_sessions_is_none():
    assert aggregate_state([]) == "none"


@pytest.mark.parametrize("state,expected", [
    ("idle", "idle"),
    ("registered", "idle"),      # momentary post-SessionStart state
    ("thinking", "thinking"),
    ("working", "working"),
    ("confused", "working"),     # a tool failure is a stumble, not a status
    ("waiting", "waiting"),
    ("error", "error"),
])
def test_single_session_states(state, expected):
    assert aggregate_state(sessions(state)) == expected


@pytest.mark.parametrize("states,expected", [
    (("idle", "thinking"), "thinking"),
    (("thinking", "working"), "working"),
    (("working", "waiting"), "waiting"),
    (("waiting", "error"), "error"),
    (("idle", "thinking", "working", "waiting", "error"), "error"),
])
def test_priority_ladder(states, expected):
    assert aggregate_state(sessions(*states)) == expected


def test_waiting_outranks_working():
    """A session blocked on you is the only state that needs you to act, so it
    must never be masked by busier-looking sessions."""
    assert aggregate_state(sessions("working", "working", "waiting")) == "waiting"


def test_priority_is_independent_of_order():
    assert aggregate_state(sessions("error", "waiting")) == "error"
    assert aggregate_state(sessions("waiting", "error")) == "error"


def test_unknown_state_falls_back_to_idle():
    assert aggregate_state([{"state": "something-new"}]) == "idle"


def test_missing_state_key_falls_back_to_idle():
    assert aggregate_state([{"session_id": "s1"}]) == "idle"


# --- status_title ---


@pytest.mark.parametrize("states,expected", [
    ((), ""),
    (("working",), ""),                       # a count of 1 is noise
    (("working", "idle"), " 2"),
    (("idle", "idle", "working"), " 3"),
])
def test_title_counts_only_when_useful(states, expected):
    assert status_title(sessions(*states)) == expected


@pytest.mark.parametrize("states,expected", [
    (("waiting",), " 1!"),
    (("waiting", "waiting"), " 2!"),
    (("waiting", "working", "idle"), " 1!"),
])
def test_waiting_count_wins_over_session_count(states, expected):
    """How many sessions need you beats how many exist — and unlike the plain
    count, 1 is worth showing."""
    assert status_title(sessions(*states)) == expected


# --- icon files ---


def test_every_aggregate_state_has_an_icon():
    reachable = {"none", "idle", "thinking", "working", "waiting", "error"}
    assert reachable <= set(ICON_FILES)


def test_offline_is_mapped_but_not_reachable_from_a_snapshot():
    """"offline" means the daemon thread died — the app sets it directly."""
    assert "offline" in ICON_FILES
    every_state = ("idle", "registered", "thinking", "working",
                   "confused", "waiting", "error")
    assert aggregate_state(sessions(*every_state)) != "offline"


def test_every_mapped_icon_file_exists():
    """Guards against a PNG that was renamed or never made it into the bundle."""
    icons = importlib.resources.files("clawd_tank_menubar") / "icons"
    missing = [
        stem for stem in ICON_FILES.values()
        if not (icons / f"{stem}.png").is_file()
    ]
    assert missing == []


def test_icon_name_falls_back_for_an_unknown_state():
    assert icon_name("nonsense") == ICON_FILES["idle"]
