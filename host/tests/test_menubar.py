# host/tests/test_menubar.py
"""Tests for menu bar app state transitions.

These test the observer-driven state updates without launching
the actual rumps app (which requires macOS AppKit).
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from clawd_tank_daemon.daemon import ClawdDaemon


class FakeObserver:
    """Minimal observer for testing daemon integration."""
    def __init__(self):
        self.connection_changes = []
        self.snapshots = []

    def on_connection_change(self, connected: bool, transport: str = "") -> None:
        self.connection_changes.append((connected, transport))

    def on_sessions_change(self, snapshot: list[dict]) -> None:
        self.snapshots.append(snapshot)


@pytest.mark.asyncio
async def test_observer_sees_sessions_appear_and_disappear():
    """The menu bar drives its icon off the session snapshot, so add/dismiss
    traffic has to show up there — not as a notification count."""
    import asyncio
    from clawd_tank_daemon import daemon as daemon_mod

    obs = FakeObserver()
    daemon = ClawdDaemon(observer=obs)
    daemon_mod.NOTIFY_COALESCE_SECS = 0.01
    try:
        await daemon._handle_message(
            {"event": "add", "hook": "Stop", "session_id": "s1", "project": "p1",
             "message": "m"}
        )
        await asyncio.sleep(0.05)
        await daemon._handle_message(
            {"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"}
        )
        await asyncio.sleep(0.05)
    finally:
        daemon_mod.NOTIFY_COALESCE_SECS = 0.15

    assert [len(s) for s in obs.snapshots] == [1, 0]
    assert obs.snapshots[0][0]["project"] == "p1"


@pytest.mark.asyncio
async def test_disconnect_callback_fires_observer():
    obs = FakeObserver()
    daemon = ClawdDaemon(observer=obs)
    # Mark BLE transport as disconnected so any() returns False
    mock_transport = AsyncMock()
    mock_transport.is_connected = False
    daemon._transports["ble"] = mock_transport
    daemon._on_transport_disconnect("ble")
    assert obs.connection_changes == [(False, "ble")]


@pytest.mark.asyncio
async def test_observer_receives_transport_name():
    obs = FakeObserver()
    daemon = ClawdDaemon(observer=obs)
    mock_transport = AsyncMock()
    mock_transport.is_connected = True
    daemon._transports["ble"] = mock_transport
    daemon._on_transport_connect("ble")
    assert obs.connection_changes == [(True, "ble")]


def test_launchd_is_enabled_checks_plist():
    """launchd.is_enabled returns True iff the plist file exists."""
    from clawd_tank_menubar import launchd
    with patch.object(launchd, "PLIST_PATH") as mock_path:
        mock_path.exists.return_value = True
        assert launchd.is_enabled() is True
        mock_path.exists.return_value = False
        assert launchd.is_enabled() is False


import tempfile
from pathlib import Path
from clawd_tank_menubar.preferences import load_preferences, save_preferences

def test_load_preferences_missing_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        prefs = load_preferences(Path(tmpdir) / "prefs.json")
        assert prefs == {"ble_enabled": True, "sim_enabled": True, "sim_window_visible": True, "sim_always_on_top": True}

def test_save_and_load_preferences():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "subdir" / "prefs.json"
        save_preferences(path=path, updates={"sim_enabled": False})
        prefs = load_preferences(path)
        assert prefs["sim_enabled"] is False
        assert prefs["ble_enabled"] is True

def test_load_preferences_malformed_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "prefs.json"
        path.write_text("not json{{{")
        prefs = load_preferences(path)
        assert prefs == {"ble_enabled": True, "sim_enabled": True, "sim_window_visible": True, "sim_always_on_top": True}
