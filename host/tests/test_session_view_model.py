"""Tests for the popover's row models.

Pure logic only — session_view_model.py must not import AppKit.
"""

import pytest

from clawd_tank_menubar.session_view_model import (
    build_empty_state,
    build_row_models,
    describe,
    footer_text,
    format_elapsed,
    tool_sprite,
)

NOW = 1_700_000_000.0


def session(**overrides):
    base = {
        "session_id": "s1", "display_id": 1, "project": "clawd-tank",
        "state": "working", "tool_name": "Bash", "subagents": 0,
        "last_event": NOW,
    }
    base.update(overrides)
    return base


# --- format_elapsed ---


@pytest.mark.parametrize("seconds,expected", [
    (0, "0s"),
    (12, "12s"),
    (59, "59s"),
    (60, "1m"),
    (134, "2m"),
    (3599, "59m"),
    (3600, "1h 0m"),
    (7380, "2h 3m"),
])
def test_format_elapsed(seconds, expected):
    assert format_elapsed(seconds) == expected


def test_negative_elapsed_clamps_to_zero():
    """Wall clock can jump backwards (NTP, sleep); never render "-3s"."""
    assert format_elapsed(-3) == "0s"


# --- describe ---


@pytest.mark.parametrize("state,tool,subagents,expected", [
    ("waiting", "AskUserQuestion", 0, "Waiting for your answer"),
    ("waiting", "Bash", 0, "Waiting for your approval"),
    ("waiting", "", 0, "Waiting for your approval"),
    ("error", "", 0, "Stopped with an error"),
    ("thinking", "", 0, "Thinking…"),
    ("idle", "", 0, "Idle"),
    ("registered", "", 0, "Starting…"),
    ("working", "Bash", 0, "Running"),
    ("working", "Read", 0, "Reading"),
    ("working", "Edit", 0, "Editing"),
    ("working", "", 0, "Working"),
    ("working", "SomeNewTool", 0, "Running SomeNewTool"),
    ("working", "mcp__github__list_prs", 0, "Calling github"),
    ("confused", "", 0, "A tool failed"),
])
def test_describe(state, tool, subagents, expected):
    assert describe(state, tool, subagents) == expected


def test_subagents_take_over_the_working_description():
    assert describe("working", "Bash", 1) == "1 subagent running"
    assert describe("working", "Bash", 3) == "3 subagents running"


def test_waiting_beats_subagents_in_the_description():
    """A blocked session is blocked no matter how much it delegated."""
    assert describe("waiting", "Bash", 4) == "Waiting for your approval"


# --- tool_sprite ---


@pytest.mark.parametrize("tool,expected", [
    ("Edit", "typing"),
    ("Read", "debugger"),
    ("Bash", "building"),
    ("WebSearch", "wizard"),
    ("mcp__anything__at_all", "beacon"),
    ("UnknownTool", "typing"),
    ("", "typing"),
])
def test_tool_sprite(tool, expected):
    assert tool_sprite(tool) == expected


# --- build_row_models ---


def test_empty_snapshot_has_no_rows():
    assert build_row_models([], now=NOW) == []


def test_row_carries_project_detail_elapsed_and_sprite():
    rows = build_row_models([session(last_event=NOW - 134)], now=NOW)
    assert len(rows) == 1
    row = rows[0]
    assert row.project == "clawd-tank"
    assert row.detail == "Running"
    assert row.elapsed_text == "2m"
    assert row.sprite == "building"
    assert row.accent is None


def test_rows_follow_snapshot_order():
    snapshot = [session(session_id=f"s{i}", project=f"p{i}") for i in range(3)]
    assert [r.project for r in build_row_models(snapshot, now=NOW)] == ["p0", "p1", "p2"]


@pytest.mark.parametrize("state,sprite,accent", [
    ("waiting", "alert", "waiting"),
    ("error", "dizzy", "error"),
    ("confused", "confused", None),
    ("thinking", "thinking", None),
    ("idle", "idle", None),
])
def test_sprite_and_accent_per_state(state, sprite, accent):
    row = build_row_models([session(state=state)], now=NOW)[0]
    assert row.sprite == sprite
    assert row.accent == accent


def test_subagents_switch_the_sprite_to_conducting():
    row = build_row_models([session(subagents=2)], now=NOW)[0]
    assert row.sprite == "conducting"
    assert row.subagents == 2


def test_blank_project_falls_back_rather_than_rendering_empty():
    assert build_row_models([session(project="")], now=NOW)[0].project == "unknown"


def test_long_project_names_are_not_truncated_in_the_model():
    """Truncation is the view's job — only it knows the pixel width."""
    name = "a-really-quite-long-monorepo-package-name-that-will-not-fit"
    assert build_row_models([session(project=name)], now=NOW)[0].project == name


def test_row_models_are_immutable():
    """Rows are handed to AppKit; nothing should be able to mutate them after."""
    row = build_row_models([session()], now=NOW)[0]
    with pytest.raises(Exception):
        row.project = "changed"


# --- empty state and footer ---


def test_empty_state_when_hooks_are_installed():
    model = build_empty_state(hooks_installed=True)
    assert model.sprite == "sleeping"
    assert "No Claude sessions" in model.title


def test_empty_state_calls_out_missing_hooks():
    """An idle popover is a lie if the hooks were never installed."""
    model = build_empty_state(hooks_installed=False)
    assert "Hooks not installed" in model.title
    assert "Settings" in model.detail


@pytest.mark.parametrize("count,expected", [
    (0, "No sessions"),
    (1, "1 session"),
    (4, "4 sessions"),
])
def test_footer_text(count, expected):
    assert footer_text([session() for _ in range(count)]) == expected
