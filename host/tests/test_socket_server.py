"""Tests for SocketServer: concurrent connections, malformed JSON, clean shutdown."""

import asyncio
import json
import socket
import tempfile
from pathlib import Path

import pytest
from clawd_tank_daemon import socket_server
from clawd_tank_daemon.socket_server import SocketServer


async def _send_raw(socket_path: Path, data: bytes) -> None:
    """Send data to the socket, appending a newline delimiter."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    writer.write(data + b"\n")
    await writer.drain()
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_socket_server_receives_message():
    """Server must deliver a well-formed JSON message to the callback."""
    received: list[dict] = []

    async def on_message(msg: dict) -> None:
        received.append(msg)

    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        server = SocketServer(on_message=on_message, socket_path=sock_path)
        await server.start()

        payload = {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
        await _send_raw(sock_path, json.dumps(payload).encode())
        await asyncio.sleep(0.05)  # allow handler coroutine to run

        assert len(received) == 1
        assert received[0]["event"] == "add"
        assert received[0]["session_id"] == "s1"

        await server.stop()


@pytest.mark.asyncio
async def test_socket_server_concurrent_connections():
    """Multiple simultaneous connections must each deliver their message."""
    received: list[dict] = []

    async def on_message(msg: dict) -> None:
        received.append(msg)

    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        server = SocketServer(on_message=on_message, socket_path=sock_path)
        await server.start()

        messages = [
            {"event": "add", "session_id": f"s{i}", "project": "p", "message": "m"}
            for i in range(5)
        ]
        await asyncio.gather(*[
            _send_raw(sock_path, json.dumps(m).encode()) for m in messages
        ])
        await asyncio.sleep(0.1)

        assert len(received) == 5
        session_ids = {r["session_id"] for r in received}
        assert session_ids == {"s0", "s1", "s2", "s3", "s4"}

        await server.stop()


@pytest.mark.asyncio
async def test_socket_server_malformed_json_does_not_crash():
    """Invalid JSON must be silently absorbed — server must keep running."""
    received: list[dict] = []

    async def on_message(msg: dict) -> None:
        received.append(msg)

    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        server = SocketServer(on_message=on_message, socket_path=sock_path)
        await server.start()

        # Send garbage
        await _send_raw(sock_path, b"not json {{{{")
        await asyncio.sleep(0.05)

        # Server must still accept a subsequent valid message
        payload = {"event": "dismiss", "session_id": "s1"}
        await _send_raw(sock_path, json.dumps(payload).encode())
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0]["event"] == "dismiss"

        await server.stop()


@pytest.mark.asyncio
async def test_socket_server_stop_removes_socket_file():
    """stop() must clean up the socket file."""
    async def on_message(msg: dict) -> None:
        pass

    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        server = SocketServer(on_message=on_message, socket_path=sock_path)
        await server.start()
        assert sock_path.exists()

        await server.stop()
        assert not sock_path.exists()


# --- Not deleting other people's sockets ------------------------------------
#
# stop() used to unlink whatever sat at its path. A SocketServer that had never
# started would therefore delete a *running* daemon's socket, which is how one
# `pytest` run left the installed app unable to receive a hook for a whole day:
# it kept its listening fd, so nothing looked wrong from the inside.


async def _noop(msg: dict) -> None:
    pass


@pytest.mark.asyncio
async def test_stop_without_start_leaves_the_path_alone():
    """A server that never bound must not unlink the file at its path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        live = SocketServer(on_message=_noop, socket_path=sock_path)
        await live.start()

        never_started = SocketServer(on_message=_noop, socket_path=sock_path)
        await never_started.stop()

        assert sock_path.exists()
        assert live.is_serving()
        await live.stop()


@pytest.mark.asyncio
async def test_stop_leaves_a_socket_it_no_longer_owns():
    """Once someone else owns the path, stop() must not take their file with it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        first = SocketServer(on_message=_noop, socket_path=sock_path)
        await first.start()

        # A second server rebinds the same path, as a takeover would.
        second = SocketServer(on_message=_noop, socket_path=sock_path)
        await second.start()

        await first.stop()

        assert sock_path.exists()
        assert second.is_serving()
        await second.stop()


# --- Self-healing ------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_serving_is_a_no_op_while_healthy():
    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        server = SocketServer(on_message=_noop, socket_path=sock_path)
        await server.start()
        before = sock_path.stat().st_ino

        assert await server.ensure_serving() is False
        assert sock_path.stat().st_ino == before

        await server.stop()


@pytest.mark.asyncio
async def test_ensure_serving_rebinds_after_the_socket_is_deleted():
    """The daemon must recover on its own once the file is gone."""
    received: list[dict] = []

    async def on_message(msg: dict) -> None:
        received.append(msg)

    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        server = SocketServer(on_message=on_message, socket_path=sock_path)
        await server.start()

        sock_path.unlink()
        assert server.is_serving() is False

        assert await server.ensure_serving() is True
        assert server.is_serving()

        await _send_raw(sock_path, json.dumps({"event": "dismiss"}).encode())
        await asyncio.sleep(0.05)
        assert received == [{"event": "dismiss"}]

        await server.stop()


@pytest.mark.asyncio
async def test_ensure_serving_rebinds_a_stale_file_nobody_listens_on():
    """A leftover socket file from a dead process must not block recovery."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        server = SocketServer(on_message=_noop, socket_path=sock_path)
        await server.start()

        # Replace our socket with a dead one: bound by nobody, connect refused.
        sock_path.unlink()
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(sock_path))
        stale.close()  # never listened on — the file outlives the socket

        assert await server.ensure_serving() is True
        assert server.is_serving()

        await server.stop()


@pytest.mark.asyncio
async def test_ensure_serving_stands_down_for_a_live_listener():
    """A takeover by another daemon is not a fault — don't fight over the path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        first = SocketServer(on_message=_noop, socket_path=sock_path)
        await first.start()

        second = SocketServer(on_message=_noop, socket_path=sock_path)
        await second.start()
        owner_ino = sock_path.stat().st_ino

        assert await first.ensure_serving() is False
        assert sock_path.stat().st_ino == owner_ino
        assert second.is_serving()

        await first.stop()
        await second.stop()


@pytest.mark.asyncio
async def test_ensure_serving_does_not_start_a_server_that_never_ran():
    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        server = SocketServer(on_message=_noop, socket_path=sock_path)

        assert await server.ensure_serving() is False
        assert not sock_path.exists()


def test_socket_path_default_is_resolved_at_construction(tmp_path, monkeypatch):
    """The default path must follow SOCKET_PATH, not a frozen import-time copy.

    conftest redirects SOCKET_PATH so no test can reach ~/.clawd-tank/sock; that
    only works if the default is read when the server is built.
    """
    monkeypatch.setattr(socket_server, "SOCKET_PATH", tmp_path / "redirected.sock")
    assert SocketServer(on_message=_noop)._socket_path == tmp_path / "redirected.sock"
