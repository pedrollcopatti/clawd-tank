# host/clawd_tank_menubar/session_view_model.py
"""Turn a daemon session snapshot into rows the popover can draw.

Pure functions over plain dicts — this module must never import AppKit, so it
runs in CI where there is no window server. session_row.py owns the views;
everything about *what* a row says lives here.
"""

import time
from dataclasses import dataclass
from typing import Optional

# Tool -> sprite. Mirrors the device's animation choice so a session looks the
# same in the popover as it does on hardware, but it lives here now: which crab
# to draw is a UI decision, and the two are free to diverge.
TOOL_SPRITES = {
    "Edit": "typing",
    "Write": "typing",
    "NotebookEdit": "typing",
    "Read": "debugger",
    "Grep": "debugger",
    "Glob": "debugger",
    "Bash": "building",
    "Agent": "conducting",
    "WebSearch": "wizard",
    "WebFetch": "wizard",
    "LSP": "beacon",
}

# Human-readable verb per tool, for the row's second line.
TOOL_VERBS = {
    "Edit": "Editing",
    "Write": "Writing",
    "NotebookEdit": "Editing",
    "Read": "Reading",
    "Grep": "Searching",
    "Glob": "Searching",
    "Bash": "Running",
    "Task": "Delegating",
    "Agent": "Delegating",
    "WebSearch": "Searching the web",
    "WebFetch": "Fetching",
}

ACCENT_WAITING = "waiting"
ACCENT_ERROR = "error"


@dataclass(frozen=True)
class RowModel:
    """Everything one popover row displays. No AppKit types."""
    session_id: str
    project: str
    detail: str
    elapsed_text: str
    sprite: str
    subagents: int
    accent: Optional[str] = None


@dataclass(frozen=True)
class EmptyStateModel:
    """Shown instead of rows when no Claude session is running."""
    sprite: str
    title: str
    detail: str


def tool_sprite(tool_name: str) -> str:
    """Sprite for a working session, chosen by the tool it's running."""
    if tool_name and tool_name.startswith("mcp__"):
        return "beacon"
    return TOOL_SPRITES.get(tool_name, "typing")


def format_elapsed(seconds: float) -> str:
    """Coarse, glanceable age. Never shows a unit finer than it needs to."""
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60}m"


def _tool_phrase(tool_name: str) -> str:
    if not tool_name:
        return "Working"
    if tool_name.startswith("mcp__"):
        # mcp__server__tool -> "server"
        parts = tool_name.split("__")
        return f"Calling {parts[1]}" if len(parts) > 2 else "Calling an MCP tool"
    verb = TOOL_VERBS.get(tool_name)
    return verb if verb else f"Running {tool_name}"


def describe(state: str, tool_name: str, subagents: int) -> str:
    """The row's second line: what this session is doing right now."""
    if state == "waiting":
        return ("Waiting for your answer" if tool_name == "AskUserQuestion"
                else "Waiting for your approval")
    if state == "error":
        return "Stopped with an error"
    if state == "confused":
        return f"{_tool_phrase(tool_name)} — failed" if tool_name else "A tool failed"
    if state == "thinking":
        return "Thinking…"
    if state == "working":
        if subagents:
            return f"{subagents} subagent{'s' if subagents != 1 else ''} running"
        return _tool_phrase(tool_name)
    if state == "registered":
        return "Starting…"
    return "Idle"


def _sprite_for(state: str, tool_name: str, subagents: int) -> str:
    if state == "waiting":
        return "alert"
    if state == "error":
        return "dizzy"
    if state == "confused":
        return "confused"
    if state == "thinking":
        return "thinking"
    if state == "working":
        # A session that farmed work out to subagents is conducting, whatever
        # tool it last touched itself.
        return "conducting" if subagents else tool_sprite(tool_name)
    return "idle"


def _accent_for(state: str) -> Optional[str]:
    if state == "waiting":
        return ACCENT_WAITING
    if state == "error":
        return ACCENT_ERROR
    return None


def build_row_models(snapshot: list[dict], now: Optional[float] = None) -> list[RowModel]:
    """One RowModel per session, in the daemon's arrival order.

    Long project names are left intact: truncation is the view's job, since only
    it knows the pixel width available.
    """
    if now is None:
        now = time.time()
    rows = []
    for session in snapshot:
        state = session.get("state", "idle")
        tool_name = session.get("tool_name", "")
        subagents = session.get("subagents", 0)
        rows.append(RowModel(
            session_id=session.get("session_id", ""),
            project=session.get("project", "") or "unknown",
            detail=describe(state, tool_name, subagents),
            elapsed_text=format_elapsed(now - session.get("last_event", now)),
            sprite=_sprite_for(state, tool_name, subagents),
            subagents=subagents,
            accent=_accent_for(state),
        ))
    return rows


def build_empty_state(hooks_installed: bool) -> EmptyStateModel:
    """What the popover says when nothing is running.

    Missing hooks are the one reason an idle popover might be lying, so that
    case gets a distinct, actionable message.
    """
    if hooks_installed:
        return EmptyStateModel(
            sprite="sleeping",
            title="No Claude sessions",
            detail="Clawd naps until one starts.",
        )
    return EmptyStateModel(
        sprite="confused",
        title="Hooks not installed",
        detail="Open Settings → Install Claude Code Hooks.",
    )


def footer_text(snapshot: list[dict]) -> str:
    """Right-hand summary in the popover footer."""
    n = len(snapshot)
    if n == 0:
        return "No sessions"
    return "1 session" if n == 1 else f"{n} sessions"
