from clawd_tank_daemon.protocol import hook_payload_to_daemon_message


def test_idle_prompt_to_add():
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "abc-123",
        "cwd": "/Users/me/Projects/my-project",
        "message": "Claude is waiting for input",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "add"
    assert msg["session_id"] == "abc-123"
    assert msg["project"] == "my-project"
    assert msg["message"] == "Claude is waiting for input"


def test_prompt_submit_to_dismiss():
    hook = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "abc-123",
        "cwd": "/Users/me/Projects/my-project",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "dismiss"
    assert msg["session_id"] == "abc-123"


def test_session_end_to_dismiss():
    hook = {
        "hook_event_name": "SessionEnd",
        "session_id": "abc-123",
        "cwd": "/Users/me/Projects/foo",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "dismiss"
    assert msg["session_id"] == "abc-123"


def test_irrelevant_notification_ignored():
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "auth_success",
        "session_id": "abc-123",
        "cwd": "/tmp",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is None


# --- Edge cases ---

def test_unknown_hook_event_returns_none():
    """Unrecognised event names must be silently dropped."""
    assert hook_payload_to_daemon_message({"hook_event_name": "SomeFutureEvent"}) is None
    assert hook_payload_to_daemon_message({}) is None  # completely empty payload


def test_missing_session_id_defaults_to_empty_string():
    """Missing session_id should default to "" not raise."""
    hook = {
        "hook_event_name": "UserPromptSubmit",
        # no session_id
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "dismiss"
    assert msg["session_id"] == ""


def test_empty_session_id_passthrough():
    """Empty-string session_id is valid and must round-trip correctly."""
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "",
        "cwd": "/Users/me/Projects/my-project",
        "message": "waiting",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["session_id"] == ""


def test_missing_cwd_gives_unknown_project():
    """When cwd is absent the project should fall back to 'unknown'."""
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "s1",
        # no cwd
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["project"] == "unknown"


def test_cwd_trailing_slash_gives_project_name():
    """cwd ending with '/' must still yield the directory name, not ''."""
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "s1",
        "cwd": "/Users/me/Projects/my-project/",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    # Should NOT be empty — ideally "my-project"
    assert msg["project"] != "", (
        "Trailing slash in cwd causes basename to return '' — project name lost"
    )


def test_cwd_empty_string_gives_unknown_project():
    """cwd='' (explicit empty string) must fall back to 'unknown'."""
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "s1",
        "cwd": "",
        "message": "waiting",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["project"] == "unknown", (
        f"Expected 'unknown' for empty cwd, got '{msg['project']}'"
    )


def test_missing_message_field_uses_default():
    """When message is absent the default 'Waiting for input' must be used."""
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "s1",
        "cwd": "/tmp/proj",
        # no message
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["message"] == "Waiting for input"


# --- New hook event types ---

def test_session_start_produces_session_start_event():
    hook = {
        "hook_event_name": "SessionStart",
        "session_id": "sess-1",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "session_start"
    assert msg["session_id"] == "sess-1"


def test_pre_tool_use_produces_tool_use_event():
    hook = {
        "hook_event_name": "PreToolUse",
        "session_id": "sess-2",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "tool_use"
    assert msg["session_id"] == "sess-2"


def test_pre_tool_use_preserves_tool_name():
    hook = {
        "hook_event_name": "PreToolUse",
        "session_id": "sess-2",
        "tool_name": "Bash",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["tool_name"] == "Bash"


def test_pre_tool_use_missing_tool_name_defaults_empty():
    hook = {
        "hook_event_name": "PreToolUse",
        "session_id": "sess-2",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["tool_name"] == ""


def test_pre_compact_produces_compact_event():
    hook = {
        "hook_event_name": "PreCompact",
        "session_id": "sess-3",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "compact"
    assert msg["session_id"] == "sess-3"


# --- Hook discriminator field ---

def test_stop_add_includes_hook_discriminator():
    hook = {
        "hook_event_name": "Stop",
        "session_id": "s1",
        "cwd": "/tmp/my-project",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "add"
    assert msg["hook"] == "Stop"


def test_notification_add_includes_hook_discriminator():
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "s1",
        "cwd": "/tmp/my-project",
        "message": "idle",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "add"
    assert msg["hook"] == "Notification"


def test_prompt_submit_dismiss_includes_hook_discriminator():
    hook = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "s1",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "dismiss"
    assert msg["hook"] == "UserPromptSubmit"


def test_session_end_dismiss_includes_hook_discriminator():
    hook = {
        "hook_event_name": "SessionEnd",
        "session_id": "s1",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "dismiss"
    assert msg["hook"] == "SessionEnd"


def test_subagent_start_produces_subagent_start_event():
    hook = {
        "hook_event_name": "SubagentStart",
        "session_id": "sess-1",
        "agent_id": "agent-abc123",
        "agent_type": "Explore",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "subagent_start"
    assert msg["session_id"] == "sess-1"
    assert msg["agent_id"] == "agent-abc123"


def test_subagent_stop_produces_subagent_stop_event():
    hook = {
        "hook_event_name": "SubagentStop",
        "session_id": "sess-1",
        "agent_id": "agent-abc123",
        "agent_type": "Explore",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "subagent_stop"
    assert msg["session_id"] == "sess-1"
    assert msg["agent_id"] == "agent-abc123"


# --- StopFailure hook ---

def test_stop_failure_produces_add_event():
    hook = {
        "hook_event_name": "StopFailure",
        "session_id": "abc-123",
        "cwd": "/Users/me/Projects/my-project",
        "error": "Rate limit reached",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "add"
    assert msg["hook"] == "StopFailure"
    assert msg["session_id"] == "abc-123"
    assert msg["project"] == "my-project"
    assert msg["message"] == "Rate limit reached"


def test_stop_failure_fallback_message():
    hook = {
        "hook_event_name": "StopFailure",
        "session_id": "abc-123",
        "cwd": "/tmp/proj",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["message"] == "API error"


def test_stop_failure_stop_reason_fallback():
    hook = {
        "hook_event_name": "StopFailure",
        "session_id": "s1",
        "cwd": "/tmp/proj",
        "stop_reason": "max_turns",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg["message"] == "max_turns"


# --- PID, source, reason capture (ghost-crab fix) ---

def test_session_start_includes_pid_and_source():
    """SessionStart payload's pid and source fields flow through to daemon msg."""
    from clawd_tank_daemon.protocol import hook_payload_to_daemon_message
    msg = hook_payload_to_daemon_message({
        "hook_event_name": "SessionStart",
        "session_id": "s1",
        "cwd": "/foo/bar",
        "pid": 4242,
        "source": "clear",
    })
    assert msg["pid"] == 4242
    assert msg["source"] == "clear"


def test_session_end_includes_pid_and_reason():
    from clawd_tank_daemon.protocol import hook_payload_to_daemon_message
    msg = hook_payload_to_daemon_message({
        "hook_event_name": "SessionEnd",
        "session_id": "s1",
        "pid": 4242,
        "reason": "logout",
    })
    assert msg["pid"] == 4242
    assert msg["reason"] == "logout"


def test_pre_tool_use_includes_pid():
    from clawd_tank_daemon.protocol import hook_payload_to_daemon_message
    msg = hook_payload_to_daemon_message({
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "cwd": "/foo",
        "tool_name": "Edit",
        "pid": 4242,
    })
    assert msg["pid"] == 4242


def test_stop_includes_pid():
    from clawd_tank_daemon.protocol import hook_payload_to_daemon_message
    msg = hook_payload_to_daemon_message({
        "hook_event_name": "Stop",
        "session_id": "s1",
        "cwd": "/foo",
        "pid": 4242,
    })
    assert msg["pid"] == 4242


def test_session_start_missing_pid_field_omits_it():
    """Backwards compat — old notify script without pid still produces valid msg."""
    from clawd_tank_daemon.protocol import hook_payload_to_daemon_message
    msg = hook_payload_to_daemon_message({
        "hook_event_name": "SessionStart",
        "session_id": "s1",
        "cwd": "/foo",
    })
    assert "pid" not in msg or msg.get("pid") is None
    assert msg.get("source") is None or "source" not in msg


def test_subagent_start_includes_pid():
    from clawd_tank_daemon.protocol import hook_payload_to_daemon_message
    msg = hook_payload_to_daemon_message({
        "hook_event_name": "SubagentStart",
        "session_id": "s1",
        "agent_id": "a1",
        "pid": 4242,
    })
    assert msg["pid"] == 4242


# --- PostToolUse hook (AskUserQuestion "waiting for input" clearing) ---


def test_post_tool_use_produces_tool_done():
    hook = {
        "hook_event_name": "PostToolUse",
        "session_id": "sess-9",
        "tool_name": "AskUserQuestion",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "tool_done"
    assert msg["session_id"] == "sess-9"


def test_post_tool_use_preserves_tool_name():
    hook = {
        "hook_event_name": "PostToolUse",
        "session_id": "sess-9",
        "tool_name": "AskUserQuestion",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["tool_name"] == "AskUserQuestion"


def test_post_tool_use_missing_tool_name_defaults_empty():
    hook = {
        "hook_event_name": "PostToolUse",
        "session_id": "sess-9",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["tool_name"] == ""


def test_post_tool_use_includes_pid():
    hook = {
        "hook_event_name": "PostToolUse",
        "session_id": "sess-9",
        "tool_name": "AskUserQuestion",
        "pid": 4242,
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["pid"] == 4242


# --- PermissionRequest (waiting/alert) and PostToolUseFailure (confused) ---


def test_permission_request_produces_permission_event():
    hook = {
        "hook_event_name": "PermissionRequest",
        "session_id": "p1",
        "tool_name": "Bash",
        "cwd": "/x/proj",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "permission"
    assert msg["tool_name"] == "Bash"


def test_permission_request_includes_pid():
    hook = {
        "hook_event_name": "PermissionRequest",
        "session_id": "p1",
        "tool_name": "Bash",
        "pid": 4242,
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["pid"] == 4242


def test_post_tool_use_failure_produces_tool_failed_event():
    hook = {
        "hook_event_name": "PostToolUseFailure",
        "session_id": "f1",
        "tool_name": "Read",
        "cwd": "/x/proj",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "tool_failed"
    assert msg["tool_name"] == "Read"


