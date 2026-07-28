"""Message format conversion between Claude Code hooks and the daemon."""

from pathlib import Path
from typing import Optional

# Built-in tool whose PreToolUse means "Claude is blocked on a human choice".
# Single source of truth shared by the daemon (state mapping) and the hook
# installer (PostToolUse matcher). Kept here in the shared message module so
# both clawd_tank_daemon and clawd_tank_menubar can import it.
ASK_USER_QUESTION_TOOL = "AskUserQuestion"


def hook_payload_to_daemon_message(hook: dict) -> Optional[dict]:
    """Convert a Claude Code hook stdin payload to a daemon message.

    Returns None if the hook event is not relevant (should be ignored).
    """
    event_name = hook.get("hook_event_name", "")
    session_id = hook.get("session_id", "")
    cwd = hook.get("cwd", "")
    project = Path(cwd).name if cwd else ""
    pid = hook.get("pid")  # int or None; absent in older notify scripts

    if event_name == "SessionStart":
        msg = {
            "event": "session_start",
            "session_id": session_id,
            "project": project,
            "pid": pid,
        }
        source = hook.get("source")
        if source is not None:
            msg["source"] = source
        return msg

    if event_name == "PreToolUse":
        return {
            "event": "tool_use",
            "session_id": session_id,
            "tool_name": hook.get("tool_name", ""),
            "project": project,
            "pid": pid,
        }

    if event_name == "PostToolUse":
        return {
            "event": "tool_done",
            "session_id": session_id,
            "tool_name": hook.get("tool_name", ""),
            "project": project,
            "pid": pid,
        }

    if event_name == "PermissionRequest":
        return {
            "event": "permission",
            "session_id": session_id,
            "tool_name": hook.get("tool_name", ""),
            "project": project,
            "pid": pid,
        }

    if event_name == "PostToolUseFailure":
        return {
            "event": "tool_failed",
            "session_id": session_id,
            "tool_name": hook.get("tool_name", ""),
            "project": project,
            "pid": pid,
        }

    if event_name == "PreCompact":
        return {
            "event": "compact",
            "session_id": session_id,
            "pid": pid,
        }

    if event_name == "Stop":
        cwd = hook.get("cwd", "")
        project = Path(cwd).name if cwd else "unknown"
        if not project:
            project = "unknown"
        return {
            "event": "add",
            "hook": "Stop",
            "session_id": session_id,
            "project": project,
            "message": "Waiting for input",
            "pid": pid,
        }

    if event_name == "StopFailure":
        cwd = hook.get("cwd", "")
        project = Path(cwd).name if cwd else "unknown"
        if not project:
            project = "unknown"
        message = hook.get("error", "") or hook.get("stop_reason", "") or "API error"
        return {
            "event": "add",
            "hook": "StopFailure",
            "session_id": session_id,
            "project": project,
            "message": message,
            "pid": pid,
        }

    if event_name == "Notification":
        if hook.get("notification_type") != "idle_prompt":
            return None
        cwd = hook.get("cwd", "")
        project = Path(cwd).name if cwd else "unknown"
        if not project:
            project = "unknown"
        message = hook.get("message", "Waiting for input")
        return {
            "event": "add",
            "hook": "Notification",
            "session_id": session_id,
            "project": project,
            "message": message,
            "pid": pid,
        }

    if event_name == "UserPromptSubmit":
        return {
            "event": "dismiss",
            "hook": "UserPromptSubmit",
            "session_id": session_id,
            "pid": pid,
        }

    if event_name == "SessionEnd":
        msg = {
            "event": "dismiss",
            "hook": "SessionEnd",
            "session_id": session_id,
            "pid": pid,
        }
        reason = hook.get("reason")
        if reason is not None:
            msg["reason"] = reason
        return msg

    if event_name == "SubagentStart":
        return {
            "event": "subagent_start",
            "session_id": session_id,
            "agent_id": hook.get("agent_id", ""),
            "pid": pid,
        }

    if event_name == "SubagentStop":
        return {
            "event": "subagent_stop",
            "session_id": session_id,
            "agent_id": hook.get("agent_id", ""),
            "pid": pid,
        }

    return None
