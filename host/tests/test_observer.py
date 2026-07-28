# host/tests/test_observer.py
import asyncio
import pytest
from clawd_tank_daemon import daemon as daemon_mod
from clawd_tank_daemon.daemon import ClawdDaemon


class MockObserver:
    def __init__(self):
        self.snapshots = []

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
async def test_snapshot_fires_when_only_the_tool_changed(fast_coalesce):
    """Read and Grep leave the session in the same state and draw the same
    sprite, but the row's text changed — the UI still has to be told.

    The push must not be gated on any coarser derived view of the state.
    """
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)

    await daemon._handle_message(
        {"event": "tool_use", "session_id": "s1", "tool_name": "Read"}
    )
    await asyncio.sleep(fast_coalesce)

    await daemon._handle_message(
        {"event": "tool_use", "session_id": "s1", "tool_name": "Grep"}
    )
    await asyncio.sleep(fast_coalesce)

    assert len(observer.snapshots) == 2
    assert observer.snapshots[0][0]["state"] == observer.snapshots[1][0]["state"]
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
    """ClawdDaemon without an observer must consume events without crashing."""
    daemon = ClawdDaemon()
    await daemon._handle_message(
        {"event": "add", "hook": "Stop", "session_id": "s1", "project": "p",
         "message": "m"}
    )
    assert daemon._snapshot_task is None


@pytest.mark.asyncio
async def test_notify_outside_a_running_loop_is_a_no_op():
    """The daemon is constructed on the main thread before its loop exists."""
    daemon = ClawdDaemon(observer=MockObserver())
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, daemon._notify_sessions_changed)
    assert daemon._snapshot_task is None
