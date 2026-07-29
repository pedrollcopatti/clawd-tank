"""Shared test fixtures."""

import shutil
import tempfile
from pathlib import Path

import pytest

import clawd_tank_daemon.daemon as daemon_module
import clawd_tank_daemon.session_store as session_store
import clawd_tank_daemon.socket_server as socket_server


@pytest.fixture(autouse=True)
def _isolate_runtime_dir(tmp_path, monkeypatch):
    """Redirect everything the daemon writes into a temp dir.

    Not housekeeping — a safety belt. The suite builds real ClawdDaemons, and
    every path here belongs to the app the developer is running: a stray unlink
    of ~/.clawd-tank/sock leaves the installed menu bar unable to hear a single
    hook until someone restarts it, with nothing in the UI to say so. That
    happened. Tests must never touch that directory.

    Also stubs out alert sounds so the suite never spawns `afplay`.
    """
    monkeypatch.setattr(session_store, "SESSIONS_PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(daemon_module, "PID_PATH", tmp_path / "daemon.pid")
    monkeypatch.setattr(daemon_module, "LOCK_PATH", tmp_path / "daemon.lock")

    # The socket gets its own short directory: a Unix socket path can't exceed
    # ~104 bytes on macOS, and pytest's tmp_path spends most of that budget on
    # the test's name before we add a filename.
    sock_dir = tempfile.mkdtemp(prefix="clawd-")
    monkeypatch.setattr(socket_server, "SOCKET_PATH", Path(sock_dir) / "sock")

    monkeypatch.setattr(daemon_module.ClawdDaemon, "_play_alert_sound",
                        lambda self, kind: None)

    yield

    shutil.rmtree(sock_dir, ignore_errors=True)
