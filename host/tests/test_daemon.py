"""Daemon-level integration: message handling robustness and shutdown.

The session state machine itself is covered by test_session_state.py, the
snapshot by test_session_snapshot.py, and the socket by test_socket_server.py.
"""

import asyncio
import contextlib

import pytest

from clawd_tank_daemon import daemon as daemon_module
from clawd_tank_daemon.daemon import ClawdDaemon


class FakeObserver:
    def __init__(self):
        self.snapshots = []

    def on_sessions_change(self, snapshot: list[dict]) -> None:
        self.snapshots.append(snapshot)


# --- Message handling robustness ---
# The socket is fed by a hook script running inside every Claude Code session.
# A message the daemon can't make sense of must never take the daemon down with
# it, or one bad session would blind the menu bar for all of them.


@pytest.mark.asyncio
async def test_unknown_event_is_ignored():
    daemon = ClawdDaemon()
    await daemon._handle_message({"event": "bogus", "session_id": "x"})
    assert daemon._session_states == {}


@pytest.mark.asyncio
async def test_message_with_no_event_is_ignored():
    daemon = ClawdDaemon()
    await daemon._handle_message({})
    assert daemon._session_states == {}


@pytest.mark.asyncio
async def test_dismiss_for_an_unknown_session_is_ignored():
    daemon = ClawdDaemon()
    await daemon._handle_message({"event": "dismiss", "session_id": "never-seen"})
    assert daemon._session_states == {}


@pytest.mark.asyncio
async def test_handling_continues_after_a_bad_message():
    daemon = ClawdDaemon()
    await daemon._handle_message({"event": "bogus"})
    await daemon._handle_message(
        {"event": "session_start", "session_id": "s1", "project": "p"}
    )
    assert daemon._session_states["s1"]["state"] == "registered"


@pytest.mark.asyncio
async def test_stop_add_drives_the_session_idle():
    daemon = ClawdDaemon()
    await daemon._handle_message(
        {"event": "session_start", "session_id": "s1", "project": "p"}
    )
    await daemon._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1",
        "project": "p", "message": "Waiting for input",
    })
    assert daemon._session_states["s1"]["state"] == "idle"


# --- Construction ---


def test_menubar_mode_and_headless_mode_both_construct():
    """headless only controls lock takeover and signal handling now — neither
    mode builds any transport, because there aren't any."""
    assert ClawdDaemon(headless=True)._headless is True
    assert ClawdDaemon(headless=False)._headless is False


def test_observer_is_optional():
    assert ClawdDaemon()._observer is None
    obs = FakeObserver()
    assert ClawdDaemon(observer=obs)._observer is obs


# --- Shutdown ---


@pytest.mark.asyncio
async def test_shutdown_stops_the_run_loop_and_background_tasks():
    daemon = ClawdDaemon()
    daemon._staleness_task = asyncio.create_task(daemon._staleness_checker())
    daemon._liveness_task = asyncio.create_task(daemon._liveness_checker())
    await asyncio.sleep(0)

    await daemon._shutdown()

    assert daemon._running is False
    assert daemon._shutdown_event.is_set()
    assert daemon._staleness_task.cancelled() or daemon._staleness_task.done()
    assert daemon._liveness_task.cancelled() or daemon._liveness_task.done()


@pytest.mark.asyncio
async def test_shutdown_cancels_a_pending_snapshot_push():
    """A debounced push must not outlive the daemon and call into a UI that is
    already tearing down."""
    daemon = ClawdDaemon(observer=FakeObserver())
    await daemon._handle_message(
        {"event": "session_start", "session_id": "s1", "project": "p"}
    )
    assert daemon._snapshot_task is not None and not daemon._snapshot_task.done()

    await daemon._shutdown()
    assert daemon._snapshot_task is None


@pytest.mark.asyncio
async def test_shutdown_without_background_tasks_does_not_raise():
    """_shutdown() runs on the quit path even if run() never started."""
    await ClawdDaemon()._shutdown()


@pytest.mark.asyncio
async def test_shutdown_of_an_unstarted_daemon_spares_a_running_one():
    """Building a daemon and shutting it down must not disarm the live app.

    This is the test that was missing: `pytest` once left the installed menu bar
    deaf for a day, because a daemon that never started still unlinked the
    socket the running one was serving on. Nothing crashed and nothing logged —
    hooks simply stopped arriving.
    """
    live = ClawdDaemon()
    await live._socket.start()
    try:
        await ClawdDaemon()._shutdown()
        assert live._socket.is_serving()
    finally:
        await live._socket.stop()


@pytest.mark.asyncio
async def test_liveness_loop_restores_a_deleted_socket(monkeypatch):
    """The periodic sweep is what brings a vanished socket back."""
    monkeypatch.setattr(daemon_module, "LIVENESS_INTERVAL_SECONDS", 0.01)
    daemon = ClawdDaemon()
    await daemon._socket.start()
    socket_path = daemon._socket._socket_path
    try:
        socket_path.unlink()
        assert not daemon._socket.is_serving()

        task = asyncio.create_task(daemon._liveness_checker())
        for _ in range(100):
            await asyncio.sleep(0.01)
            if daemon._socket.is_serving():
                break
        daemon._running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert daemon._socket.is_serving()
        assert socket_path.exists()
    finally:
        await daemon._socket.stop()
