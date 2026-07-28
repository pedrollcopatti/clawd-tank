# host/clawd_tank_menubar/session_focus.py
"""Work out which app a Claude Code session is running inside.

Claude Code doesn't tell us where it lives, but the process tree does: walking
up from the `claude` PID reaches the terminal or editor that spawned it.

  Terminal:  claude -> -zsh -> login -> Terminal.app
  VS Code:   claude -> Code Helper (Plugin) -> Visual Studio Code.app

No AppKit here, so this is testable without a window server. popover.py does the
actual activation.
"""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("clawd-tank.focus")

# How far up the tree to look before giving up. Terminal needs 3 hops, VS Code
# 2; anything past this is a shell nested deep enough that we'd be guessing.
MAX_ANCESTRY_DEPTH = 12

_TIMEOUT = 2.0

# Editors that open one window per folder, and can be asked to focus the window
# for a given path by re-opening it. Terminals aren't in here: `open -a Terminal
# <dir>` spawns a *new* window rather than focusing the session's own.
_FOLDER_AWARE_BUNDLES = {
    "com.microsoft.VSCode",
    "com.microsoft.VSCodeInsiders",
    "com.vscodium",
    "com.todesktop.230313mzl4w4u92",  # Cursor
    "dev.zed.Zed",
}


@dataclass(frozen=True)
class HostApp:
    """The GUI application a session is running inside."""
    pid: int
    bundle_path: str
    name: str
    bundle_id: Optional[str] = None

    @property
    def is_folder_aware(self) -> bool:
        return self.bundle_id in _FOLDER_AWARE_BUNDLES


def _run(args: list[str]) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _parent_and_command(pid: int) -> tuple[Optional[int], str]:
    """(ppid, executable path) for a pid, or (None, "") if it's gone."""
    out = _run(["ps", "-o", "ppid=,comm=", "-p", str(pid)]).strip()
    if not out:
        return None, ""
    head, _, command = out.partition(" ")
    try:
        return int(head), command.strip()
    except ValueError:
        return None, ""


def _app_bundle_of(command: str) -> Optional[str]:
    """The outermost .app bundle containing an executable path.

    Outermost matters: VS Code's helpers live at
    `Visual Studio Code.app/Contents/Frameworks/Code Helper (Plugin).app/...`,
    and it's the enclosing app the user thinks of, not the helper.
    """
    if ".app/" not in command:
        return None
    head, _, _ = command.partition(".app/")
    return head + ".app"


def resolve_host_app(claude_pid: Optional[int]) -> Optional[HostApp]:
    """Walk up from a Claude Code PID to the app hosting it.

    Keeps climbing past the first process inside an app bundle until it leaves
    that bundle, and reports the last PID still inside it. This matters for
    Electron editors: the session's parent is `Code Helper (Plugin)`, which is a
    nested bundle that LaunchServices does not consider a running application —
    activating it is impossible. Its parent, `Code`, is the one that works.

    Returns None if the process is gone, or if nothing in its ancestry lives in
    an app bundle — a session started from a login shell or over SSH has no GUI
    app to focus, and that's a legitimate answer, not an error.
    """
    if not claude_pid:
        return None

    pid = int(claude_pid)
    bundle: Optional[str] = None
    app_pid: Optional[int] = None

    for _ in range(MAX_ANCESTRY_DEPTH):
        parent, command = _parent_and_command(pid)
        if not command:
            break  # process exited mid-walk
        found = _app_bundle_of(command)
        if found is not None:
            if bundle is None:
                bundle = found
            if found != bundle:
                break  # a different app above ours — ours ended one hop down
            app_pid = pid
        elif bundle is not None:
            break  # climbed out of the bundle
        if parent is None or parent <= 1:
            break
        pid = parent

    if bundle is None or app_pid is None:
        return None
    return HostApp(
        pid=app_pid,
        bundle_path=bundle,
        name=Path(bundle).stem,
        bundle_id=bundle_id_of(bundle),
    )


def bundle_id_of(bundle_path: str) -> Optional[str]:
    plist = Path(bundle_path) / "Contents" / "Info.plist"
    out = _run(["defaults", "read", str(plist), "CFBundleIdentifier"]).strip()
    return out or None


def session_cwd(claude_pid: Optional[int]) -> Optional[str]:
    """The session's working directory, read from the live process.

    Via lsof rather than stored state: it costs ~15 ms, is always current, and
    saves threading `cwd` through the hook script, the wire format and the
    session store just to support one click.
    """
    if not claude_pid:
        return None
    out = _run(["lsof", "-a", "-d", "cwd", "-p", str(int(claude_pid)), "-Fn"])
    for line in out.splitlines():
        if line.startswith("n/"):
            return line[1:]
    return None


def activation_command(host: HostApp, cwd: Optional[str]) -> list[str]:
    """A LaunchServices command that brings the session's window forward.

    Goes through `open` rather than relying on NSRunningApplication.activate
    alone: macOS only honours cross-app activation when the caller is itself the
    active app, and while that is true for a popover click, it stops being true
    the moment anything else steals focus first.

    For editors that keep one window per folder, passing the session's directory
    focuses *that* window instead of whichever was last used. Terminals get the
    bare app — handing one a directory would spawn a new window rather than
    surfacing the session's own.
    """
    if host.is_folder_aware and host.bundle_id and cwd and Path(cwd).is_dir():
        return ["open", "-b", host.bundle_id, cwd]
    if host.bundle_id:
        return ["open", "-b", host.bundle_id]
    return ["open", host.bundle_path]
