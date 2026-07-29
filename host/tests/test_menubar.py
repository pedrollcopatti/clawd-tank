# host/tests/test_menubar.py
"""Tests for the menu bar app's daemon integration.

These exercise the observer contract without launching rumps, which needs a real
status bar. Preferences have their own file; the popover has test_popover_appkit.
"""
import asyncio

import pytest
from unittest.mock import patch

from clawd_tank_daemon import daemon as daemon_mod
from clawd_tank_daemon.daemon import ClawdDaemon


class FakeObserver:
    """Minimal observer for testing daemon integration."""
    def __init__(self):
        self.snapshots = []

    def on_sessions_change(self, snapshot: list[dict]) -> None:
        self.snapshots.append(snapshot)


@pytest.fixture
def fast_coalesce(monkeypatch):
    monkeypatch.setattr(daemon_mod, "NOTIFY_COALESCE_SECS", 0.01)
    return 0.05


@pytest.mark.asyncio
async def test_observer_sees_sessions_appear_and_disappear(fast_coalesce):
    """The menu bar drives its icon off the session snapshot, so session
    lifecycle has to show up there."""
    obs = FakeObserver()
    daemon = ClawdDaemon(observer=obs)

    await daemon._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1", "project": "p1",
        "message": "m",
    })
    await asyncio.sleep(fast_coalesce)
    await daemon._handle_message(
        {"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"}
    )
    await asyncio.sleep(fast_coalesce)

    assert [len(s) for s in obs.snapshots] == [1, 0]
    assert obs.snapshots[0][0]["project"] == "p1"


@pytest.mark.asyncio
async def test_snapshot_carries_what_a_row_needs(fast_coalesce):
    obs = FakeObserver()
    daemon = ClawdDaemon(observer=obs)

    await daemon._handle_message(
        {"event": "session_start", "session_id": "s1", "project": "clawd-tank"}
    )
    await daemon._handle_message(
        {"event": "tool_use", "session_id": "s1", "tool_name": "Bash"}
    )
    await asyncio.sleep(fast_coalesce)

    row = obs.snapshots[-1][0]
    assert row["project"] == "clawd-tank"
    assert row["state"] == "working"
    assert row["tool_name"] == "Bash"
    assert row["subagents"] == 0


class FakePopover:
    """Stands in for the AppKit popover — _apply_snapshot only needs these two."""
    def __init__(self, is_shown: bool):
        self.is_shown = is_shown
        self.taken = None

    def set_snapshot(self, snapshot: list[dict]) -> None:
        self.taken = snapshot


@pytest.mark.parametrize("is_shown", [True, False])
def test_every_snapshot_reaches_the_popover(is_shown):
    """Open or closed, the popover gets told — it renders from its own copy.

    Skipping the handoff while closed left it showing whatever was on screen
    the last time it was open, for as long as nobody opened it again.
    """
    from types import SimpleNamespace
    from clawd_tank_menubar.app import ClawdTankApp

    popover = FakePopover(is_shown)
    app = SimpleNamespace(
        _popover=popover, _sessions=None, _update_status_item=lambda: None,
    )
    snapshot = [{"session_id": "s1", "project": "p1"}]

    ClawdTankApp._apply_snapshot(app, snapshot)

    assert popover.taken == snapshot
    assert app._sessions == snapshot


def test_launchd_is_enabled_checks_plist():
    """launchd.is_enabled returns True iff the plist file exists."""
    from clawd_tank_menubar import launchd
    with patch.object(launchd, "PLIST_PATH") as mock_path:
        mock_path.exists.return_value = True
        assert launchd.is_enabled() is True
        mock_path.exists.return_value = False
        assert launchd.is_enabled() is False
