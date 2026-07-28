"""Shared test fixtures."""

import pytest

import clawd_tank_daemon.session_store as session_store


@pytest.fixture(autouse=True)
def _isolate_sessions(tmp_path, monkeypatch):
    """Redirect session persistence to a temp dir for all tests.

    Prevents tests from reading/writing the real ~/.clawd-tank/sessions.json.
    Also stubs out alert sounds so the suite never spawns `afplay`.
    """
    monkeypatch.setattr(session_store, "SESSIONS_PATH", tmp_path / "sessions.json")

    from clawd_tank_daemon.daemon import ClawdDaemon
    monkeypatch.setattr(ClawdDaemon, "_play_alert_sound", lambda self, kind: None)
