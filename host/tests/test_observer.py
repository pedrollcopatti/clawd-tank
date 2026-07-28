# host/tests/test_observer.py
import asyncio
import pytest
from unittest.mock import AsyncMock
from clawd_tank_daemon import daemon as daemon_mod
from clawd_tank_daemon.daemon import ClawdDaemon


class MockObserver:
    def __init__(self):
        self.connection_changes = []
        self.snapshots = []

    def on_connection_change(self, connected: bool, transport: str = "") -> None:
        self.connection_changes.append(connected)

    def on_sessions_change(self, snapshot: list[dict]) -> None:
        self.snapshots.append(snapshot)


@pytest.fixture
def fast_coalesce(monkeypatch):
    """Shrink the debounce so tests don't wait 150 ms per push."""
    monkeypatch.setattr(daemon_mod, "NOTIFY_COALESCE_SECS", 0.01)
    return 0.05  # comfortably longer than the debounce


# --- Session snapshot callback ---


@pytest.mark.asyncio
async def test_observer_gets_snapshot_after_session_start(fast_coalesce):
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)
    await daemon._handle_message(
        {"event": "session_start", "session_id": "s1", "project": "p"}
    )
    await asyncio.sleep(fast_coalesce)

    assert len(observer.snapshots) == 1
    assert observer.snapshots[0][0]["session_id"] == "s1"
    assert observer.snapshots[0][0]["project"] == "p"


@pytest.mark.asyncio
async def test_snapshot_fires_when_device_display_state_is_unchanged(fast_coalesce):
    """Read and Grep both map to the "debugger" animation, so the device payload
    is byte-identical — but the popover's tool text changed and must update.

    This is the regression the whole design hinges on: the snapshot push must not
    be gated on _compute_display_state() changing.
    """
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)

    await daemon._handle_message(
        {"event": "tool_use", "session_id": "s1", "tool_name": "Read"}
    )
    await asyncio.sleep(fast_coalesce)
    device_state_after_read = daemon._compute_display_state()

    await daemon._handle_message(
        {"event": "tool_use", "session_id": "s1", "tool_name": "Grep"}
    )
    await asyncio.sleep(fast_coalesce)

    # Device sees no change...
    assert daemon._compute_display_state() == device_state_after_read
    # ...but the UI got a second snapshot with the new tool.
    assert len(observer.snapshots) == 2
    assert observer.snapshots[0][0]["tool_name"] == "Read"
    assert observer.snapshots[1][0]["tool_name"] == "Grep"


@pytest.mark.asyncio
async def test_bursts_coalesce_into_one_snapshot(fast_coalesce):
    """A single Claude turn fires several hooks back to back; the UI must be
    rebuilt once, not once per event."""
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)

    for tool in ("Read", "Grep", "Bash", "Edit"):
        await daemon._handle_message(
            {"event": "tool_use", "session_id": "s1", "tool_name": tool}
        )
    await asyncio.sleep(fast_coalesce)

    assert len(observer.snapshots) == 1
    # The one push carries the newest state, not the oldest.
    assert observer.snapshots[0][0]["tool_name"] == "Edit"


@pytest.mark.asyncio
async def test_identical_snapshots_are_suppressed(fast_coalesce):
    """A message that changes nothing observable must not wake the UI."""
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)

    await daemon._handle_message(
        {"event": "tool_use", "session_id": "s1", "tool_name": "Bash"}
    )
    await asyncio.sleep(fast_coalesce)
    assert len(observer.snapshots) == 1

    # Same session, same state, same tool — only last_event moves, and the
    # snapshot pins it to the stored value, so nothing the UI sees changed.
    daemon._session_states["s1"]["last_event"] = observer.snapshots[0][0]["last_event"]
    daemon._notify_sessions_changed()
    await asyncio.sleep(fast_coalesce)

    assert len(observer.snapshots) == 1


@pytest.mark.asyncio
async def test_snapshot_fires_after_staleness_eviction(fast_coalesce):
    """_evict_stale_sessions() mutates state silently — the checker must notify."""
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)
    daemon._session_staleness_timeout = 0.0
    await daemon._handle_message(
        {"event": "session_start", "session_id": "s1", "project": "p"}
    )
    await asyncio.sleep(fast_coalesce)
    observer.snapshots.clear()

    daemon._evict_stale_sessions()
    daemon._notify_sessions_changed()
    await asyncio.sleep(fast_coalesce)

    assert observer.snapshots == [[]]


@pytest.mark.asyncio
async def test_snapshot_fires_after_liveness_eviction(fast_coalesce):
    """PID-gone eviction never passes through _handle_message."""
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)
    await daemon._handle_message(
        {"event": "session_start", "session_id": "s1", "project": "p", "pid": 999999}
    )
    await asyncio.sleep(fast_coalesce)
    observer.snapshots.clear()

    evicted = daemon._check_liveness()
    assert evicted == ["s1"]
    daemon._notify_sessions_changed()
    await asyncio.sleep(fast_coalesce)

    assert observer.snapshots == [[]]


@pytest.mark.asyncio
async def test_observer_that_raises_does_not_break_message_handling(fast_coalesce):
    class ExplodingObserver(MockObserver):
        def on_sessions_change(self, snapshot):
            raise RuntimeError("boom")

    daemon = ClawdDaemon(observer=ExplodingObserver())
    await daemon._handle_message(
        {"event": "session_start", "session_id": "s1", "project": "p"}
    )
    await asyncio.sleep(fast_coalesce)

    # Handling continued and state is intact.
    await daemon._handle_message(
        {"event": "tool_use", "session_id": "s1", "tool_name": "Bash"}
    )
    assert daemon._session_states["s1"]["state"] == "working"


@pytest.mark.asyncio
async def test_no_observer_does_not_crash():
    """ClawdDaemon without an observer must consume events without crashing,
    and without tracking them as cards."""
    daemon = ClawdDaemon()
    await daemon._handle_message(
        {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
    )
    assert "s1" not in daemon._active_notifications
    assert daemon._snapshot_task is None


@pytest.mark.asyncio
async def test_notify_outside_a_running_loop_is_a_no_op():
    """The daemon is constructed on the main thread before its loop exists."""
    daemon = ClawdDaemon(observer=MockObserver())
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, daemon._notify_sessions_changed)
    assert daemon._snapshot_task is None


# --- Connection callback ---


@pytest.mark.asyncio
async def test_observer_connection_via_disconnect_callback():
    """Transport disconnect callback triggers observer."""
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)
    # Mark BLE transport as disconnected so any() returns False
    mock_transport = AsyncMock()
    mock_transport.is_connected = False
    daemon._transports["ble"] = mock_transport
    daemon._on_transport_disconnect("ble")
    assert observer.connection_changes == [False]


@pytest.mark.asyncio
async def test_observer_connection_true_on_transport_sender_connect():
    """_transport_sender fires on_connection_change(True) after reconnect."""
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)
    mock_transport = AsyncMock()
    mock_transport.is_connected = False

    async def fake_ensure():
        mock_transport.is_connected = True
        # Real transports call on_connect_cb when connecting succeeds
        daemon._on_transport_connect("ble")

    mock_transport.ensure_connected = AsyncMock(side_effect=fake_ensure)
    mock_transport.write_notification = AsyncMock(return_value=True)
    daemon._transports["ble"] = mock_transport

    await daemon._transport_queues["ble"].put(
        {"event": "dismiss", "session_id": "s1"}
    )

    sender = asyncio.create_task(daemon._transport_sender("ble"))
    await asyncio.sleep(0.1)
    daemon._running = False
    sender.cancel()
    try:
        await sender
    except asyncio.CancelledError:
        pass

    assert True in observer.connection_changes
