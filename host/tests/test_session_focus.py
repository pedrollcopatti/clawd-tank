"""Tests for resolving which app a Claude Code session runs inside.

No AppKit — session_focus.py only shells out, so the process tree is faked by
monkeypatching the one function that runs `ps`.
"""

import pytest

from clawd_tank_menubar import session_focus
from clawd_tank_menubar.session_focus import (
    HostApp,
    _app_bundle_of,
    resolve_host_app,
    activation_command,
    session_cwd,
)

TERMINAL = "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal"
VSCODE = "/Applications/Visual Studio Code.app/Contents/MacOS/Code"
VSCODE_HELPER = (
    "/Applications/Visual Studio Code.app/Contents/Frameworks/"
    "Code Helper (Plugin).app/Contents/MacOS/Code Helper (Plugin)"
)
CLAUDE_EXT = (
    "/Users/x/.vscode/extensions/anthropic.claude-code-2.1.220-darwin-arm64/"
    "resources/native-binary/claude"
)


@pytest.fixture
def tree(monkeypatch):
    """Fake a process tree: {pid: (ppid, command)}."""
    def install(mapping, bundle_ids=None):
        monkeypatch.setattr(
            session_focus, "_parent_and_command",
            lambda pid: mapping.get(pid, (None, "")),
        )
        monkeypatch.setattr(
            session_focus, "bundle_id_of",
            lambda path: (bundle_ids or {}).get(path),
        )
    return install


# --- _app_bundle_of ---


def test_app_bundle_of_plain_binary():
    assert _app_bundle_of("/usr/bin/zsh") is None
    assert _app_bundle_of(CLAUDE_EXT) is None


def test_app_bundle_of_returns_the_outermost_bundle():
    """Electron helpers are bundles nested inside the app the user knows."""
    assert _app_bundle_of(VSCODE_HELPER) == "/Applications/Visual Studio Code.app"
    assert _app_bundle_of(VSCODE) == "/Applications/Visual Studio Code.app"


# --- resolve_host_app ---


def test_resolves_a_terminal_session(tree):
    tree({
        900: (800, "claude"),
        800: (700, "-zsh"),
        700: (600, "login"),
        600: (1, TERMINAL),
    }, {"/System/Applications/Utilities/Terminal.app": "com.apple.Terminal"})

    host = resolve_host_app(900)
    assert host.pid == 600
    assert host.name == "Terminal"
    assert host.bundle_id == "com.apple.Terminal"


def test_climbs_past_an_electron_helper_to_the_main_process(tree):
    """`Code Helper (Plugin)` is a nested bundle that LaunchServices does not
    consider a running application, so activating it is impossible. Its parent
    is the one that works."""
    tree({
        900: (800, CLAUDE_EXT),
        800: (700, VSCODE_HELPER),
        700: (1, VSCODE),
    }, {"/Applications/Visual Studio Code.app": "com.microsoft.VSCode"})

    host = resolve_host_app(900)
    assert host.pid == 700
    assert host.name == "Visual Studio Code"


def test_stops_before_a_different_app_higher_up(tree):
    """An editor launched from a terminal must resolve to the editor."""
    tree({
        900: (800, CLAUDE_EXT),
        800: (700, VSCODE),
        700: (1, TERMINAL),
    })
    assert resolve_host_app(900).name == "Visual Studio Code"


def test_no_gui_app_in_the_ancestry(tree):
    """A session over SSH or from a login shell has nothing to focus."""
    tree({900: (800, "claude"), 800: (1, "sshd")})
    assert resolve_host_app(900) is None


def test_missing_pid_resolves_to_nothing(tree):
    tree({})
    assert resolve_host_app(None) is None
    assert resolve_host_app(0) is None
    assert resolve_host_app(999999) is None


def test_process_that_exits_mid_walk_does_not_raise(tree):
    tree({900: (800, "claude")})  # 800 is already gone
    assert resolve_host_app(900) is None


def test_ancestry_walk_is_bounded(tree, monkeypatch):
    """A cycle or a pathologically deep tree must not hang the click."""
    calls = []

    def counting(pid):
        calls.append(pid)
        return (pid + 1, "some-binary")

    monkeypatch.setattr(session_focus, "_parent_and_command", counting)
    assert resolve_host_app(1000) is None
    assert len(calls) <= session_focus.MAX_ANCESTRY_DEPTH


# --- activation_command ---


def _host(bundle_id):
    return HostApp(pid=1, bundle_path="/Applications/X.app", name="X",
                   bundle_id=bundle_id)


def test_editor_is_opened_at_the_session_folder(tmp_path):
    """One window per folder, so the path picks out the session's own window."""
    assert activation_command(_host("com.microsoft.VSCode"), str(tmp_path)) == [
        "open", "-b", "com.microsoft.VSCode", str(tmp_path),
    ]


def test_terminal_is_activated_without_a_path(tmp_path):
    """Handing a terminal a directory spawns a new window instead of surfacing
    the session's own."""
    assert activation_command(_host("com.apple.Terminal"), str(tmp_path)) == [
        "open", "-b", "com.apple.Terminal",
    ]


@pytest.mark.parametrize("cwd", [None, "/nope/gone"])
def test_editor_without_a_usable_cwd_falls_back_to_the_bare_app(cwd):
    assert activation_command(_host("com.microsoft.VSCode"), cwd) == [
        "open", "-b", "com.microsoft.VSCode",
    ]


def test_unknown_bundle_id_falls_back_to_the_bundle_path(tmp_path):
    """Info.plist can be unreadable; the path still opens the app."""
    assert activation_command(_host(None), str(tmp_path)) == [
        "open", "/Applications/X.app",
    ]


def test_activation_command_is_never_empty(tmp_path):
    """A row that looked clickable must always do something when clicked."""
    for bundle_id in ("com.microsoft.VSCode", "com.apple.Terminal", None):
        for cwd in (str(tmp_path), None, "/nope/gone"):
            assert activation_command(_host(bundle_id), cwd)


# --- session_cwd ---


def test_session_cwd_parses_lsof_output(monkeypatch):
    monkeypatch.setattr(
        session_focus, "_run", lambda args: "p1234\nfcwd\nn/Users/x/code/proj\n"
    )
    assert session_cwd(1234) == "/Users/x/code/proj"


def test_session_cwd_when_lsof_says_nothing(monkeypatch):
    monkeypatch.setattr(session_focus, "_run", lambda args: "")
    assert session_cwd(1234) is None


def test_session_cwd_without_a_pid():
    assert session_cwd(None) is None
