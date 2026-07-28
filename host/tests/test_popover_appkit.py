"""Smoke tests for the AppKit half of the popover.

Constructing views without a run loop is legal and catches typo'd selectors,
missing symbols and frame arithmetic that crashes. The popover is never shown:
that needs a window server and would hang CI.
"""

import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="AppKit is macOS-only")


def session(**overrides):
    base = {
        "session_id": "s1", "display_id": 1, "project": "clawd-tank",
        "state": "working", "tool_name": "Bash", "subagents": 0,
        "last_event": time.time(),
    }
    base.update(overrides)
    return base


@pytest.fixture
def controller():
    from clawd_tank_menubar.popover import SessionPopoverController
    return SessionPopoverController(
        on_settings=lambda: None, hooks_installed=lambda: True
    )


def test_controller_constructs_and_is_not_shown(controller):
    assert controller.is_shown is False


def test_reload_with_no_sessions_builds_the_empty_state(controller):
    controller.reload([])
    assert controller._rows == []
    assert controller._content.frame().size.height > 0


def test_reload_builds_one_row_per_session(controller):
    controller.reload([session(session_id="s1"), session(session_id="s2")])
    assert len(controller._rows) == 2


def test_popover_grows_with_the_session_count(controller):
    controller.reload([session()])
    one = controller._content.frame().size.height
    controller.reload([session(session_id=f"s{i}") for i in range(3)])
    assert controller._content.frame().size.height > one


def test_many_sessions_are_capped_and_scroll(controller):
    """Past ~4.5 rows the list scrolls instead of growing off the screen.

    The half row is deliberate: a clipped row is the affordance that tells you
    there is more to scroll to.
    """
    from clawd_tank_menubar.popover import FOOTER_H, MAX_ROWS_H

    controller.reload([session(session_id=f"s{i}") for i in range(5)])
    five = controller._content.frame().size.height
    controller.reload([session(session_id=f"s{i}") for i in range(12)])
    twelve = controller._content.frame().size.height

    assert len(controller._rows) == 12
    assert five == twelve == MAX_ROWS_H + 1 + FOOTER_H


def test_reload_is_idempotent_and_does_not_leak_subviews(controller):
    snapshot = [session(session_id=f"s{i}") for i in range(3)]
    controller.reload(snapshot)
    first = len(controller._content.subviews())
    for _ in range(5):
        controller.reload(snapshot)
    assert len(controller._content.subviews()) == first
    assert len(controller._rows) == 3


@pytest.mark.parametrize("state", [
    "idle", "registered", "thinking", "working", "confused", "waiting", "error",
])
def test_every_session_state_renders(controller, state):
    controller.reload([session(state=state)])
    assert len(controller._rows) == 1


def test_tick_updates_elapsed_without_rebuilding_rows(controller):
    controller.reload([session(last_event=time.time() - 90)])
    row = controller._rows[0]
    row.apply_elapsed("2m")
    assert controller._rows[0] is row


def test_install_status_item_ui_detaches_the_menu_and_takes_over_clicks():
    """The risky bit: rumps hands the status item an NSMenu, which swallows
    button clicks. Verified against a real NSStatusItem rather than a mock,
    because the whole question is whether AppKit accepts this.
    """
    import AppKit
    from clawd_tank_menubar.popover import (
        SessionPopoverController,
        install_status_item_ui,
    )

    status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(-1)
    if status_item is None or status_item.button() is None:
        pytest.skip("no window server (headless CI runner)")
    try:
        menu = AppKit.NSMenu.alloc().init()
        menu.addItemWithTitle_action_keyEquivalent_("Settings", None, "")
        status_item.setMenu_(menu)
        assert status_item.menu() is not None

        fake_app = type("FakeApp", (), {})()
        fake_app._nsapp = type("FakeNSApp", (), {"nsstatusitem": status_item})()

        popover = SessionPopoverController(
            on_settings=lambda: None, hooks_installed=lambda: True
        )
        show_settings = install_status_item_ui(fake_app, popover)

        assert show_settings is not None
        assert status_item.menu() is None, "menu must be detached so clicks land"

        button = status_item.button()
        assert button.target() is fake_app._status_click_handler
        assert button.action() is not None
    finally:
        AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(status_item)


def test_install_status_item_ui_is_a_no_op_without_a_status_item():
    """Missing status item must leave rumps' plain menu working, not crash."""
    from clawd_tank_menubar.popover import (
        SessionPopoverController,
        install_status_item_ui,
    )

    popover = SessionPopoverController(
        on_settings=lambda: None, hooks_installed=lambda: True
    )
    assert install_status_item_ui(type("A", (), {})(), popover) is None


def test_every_row_sprite_has_a_bundled_image():
    """A sprite name the view model can produce but the bundle lacks would draw
    an empty row on the device it ships to, not here."""
    from clawd_tank_menubar.session_row import load_sprite
    from clawd_tank_menubar.session_view_model import TOOL_SPRITES

    names = set(TOOL_SPRITES.values()) | {
        "beacon", "idle", "thinking", "alert", "dizzy", "confused",
        "conducting", "sleeping", "typing",
    }
    missing = [name for name in sorted(names) if load_sprite(name) is None]
    assert missing == []
