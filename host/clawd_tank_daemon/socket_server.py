"""Unix socket server that receives hook messages from clawd-tank-notify."""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Callable, Awaitable, Optional

logger = logging.getLogger("clawd-tank.socket")

SOCKET_PATH = Path.home() / ".clawd-tank" / "sock"

# Connecting to a Unix socket on the local filesystem either succeeds at once or
# fails at once; a second is generous.
_PROBE_TIMEOUT = 1.0


class SocketServer:
    """Listens on a Unix socket for JSON messages from clawd-tank-notify."""

    def __init__(self, on_message: Callable[[dict], Awaitable[None]],
                 socket_path: Optional[Path] = None):
        self._on_message = on_message
        # Resolved here rather than as a signature default so that redirecting
        # SOCKET_PATH — which the test suite does — actually takes effect. As a
        # default argument it would be frozen at import time.
        self._socket_path = Path(socket_path) if socket_path is not None else SOCKET_PATH
        self._server: asyncio.Server | None = None
        # (st_dev, st_ino) of the socket file we created. Identity, not mere
        # existence: the file sitting at our path may be somebody else's socket.
        self._bound_id: tuple[int, int] | None = None

    async def start(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self._socket_path.exists():
            self._socket_path.unlink()

        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self._socket_path)
        )
        # Make socket writable by owner
        os.chmod(self._socket_path, 0o600)
        self._bound_id = self._path_identity()
        logger.info("Listening on %s", self._socket_path)

    def _path_identity(self) -> tuple[int, int] | None:
        """(st_dev, st_ino) of whatever is at our path — None if nothing is."""
        try:
            st = self._socket_path.stat()
        except OSError:
            return None
        return (st.st_dev, st.st_ino)

    def is_serving(self) -> bool:
        """Whether the file on disk is still the socket we're listening on.

        A listening fd is not enough: hooks reach us by path, so once the file
        is unlinked every hook gets ENOENT and the daemon goes silently deaf
        while looking perfectly healthy from the inside.
        """
        return (
            self._server is not None
            and self._bound_id is not None
            and self._path_identity() == self._bound_id
        )

    async def _has_live_listener(self) -> bool:
        """Whether something is accepting connections at our path right now."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self._socket_path)),
                timeout=_PROBE_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return True

    async def ensure_serving(self) -> bool:
        """Rebind if our socket file went missing. True if it rebound.

        Losing the file out from under a running daemon is easy to do by
        accident, and the failure is invisible — no error, no dropped
        connection, just a daemon that never hears from a hook again. One
        stat() per liveness tick buys recovery instead of a dead menu bar.

        A file that exists but isn't ours is left alone as long as something is
        listening on it: another daemon owning the path is a takeover, not a
        fault, and grabbing it back would leave two daemons fighting over it.
        """
        if self._server is None or self.is_serving():
            return False
        if self._path_identity() is not None and await self._has_live_listener():
            logger.info("Socket %s now belongs to another listener — standing down",
                        self._socket_path)
            return False

        logger.warning("Socket %s is no longer ours — rebinding", self._socket_path)
        server, self._server, self._bound_id = self._server, None, None
        server.close()
        try:
            await server.wait_closed()
        except OSError:
            logger.debug("Error closing the stale server", exc_info=True)
        await self.start()
        return True

    async def _handle_client(self, reader: asyncio.StreamReader,
                              writer: asyncio.StreamWriter) -> None:
        # Messages are newline-delimited JSON. readline() gives a clean
        # message boundary regardless of TCP/socket buffering.
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if line:
                try:
                    msg = json.loads(line.decode("utf-8"))
                    await self._on_message(msg)
                except json.JSONDecodeError:
                    logger.error("Received malformed JSON: %r", line[:200])
        except TimeoutError:
            logger.warning("Timed out waiting for message from client")
        except Exception:
            logger.exception("Unexpected error handling socket message")
        finally:
            writer.close()
            await writer.wait_closed()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        # Only ever remove the socket we created. A server that never started —
        # or one whose file has since been replaced — must not unlink a live
        # daemon's socket. Running the test suite against the default path used
        # to do exactly that, leaving the installed app deaf until it restarted.
        if self._bound_id is not None and self._path_identity() == self._bound_id:
            self._socket_path.unlink(missing_ok=True)
        self._bound_id = None
