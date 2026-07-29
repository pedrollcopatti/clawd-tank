# host/clawd_tank_menubar/popover.py
"""NSPopover showing one row per live Claude session.

rumps hands its status item an NSMenu, which swallows button clicks, so the menu
is detached and clicks are routed by hand: left opens this popover, right (or
ctrl-click) pops the original menu back up as Settings.

Everything here runs on the main thread. Callers coming from the daemon's
asyncio thread must marshal through AppHelper.callAfter first.
"""

import logging
import subprocess
import time

import AppKit
import objc

from .session_focus import activation_command, resolve_host_app, session_cwd

from .session_row import (
    ROW_H,
    ROW_W,
    STATS_H,
    build_empty_state,
    build_separator,
    build_session_row,
    build_stats_block,
)
from .session_view_model import build_empty_state as empty_state_model
from .session_view_model import build_row_models, footer_text
from .usage_stats import (
    UsageLimits,
    UsageStats,
    collect_usage_stats,
    read_usage_limits,
)

logger = logging.getLogger("clawd-tank.popover")

FOOTER_H = 34.0
MAX_ROWS_H = 4 * ROW_H + ROW_H / 2  # ~4.5 rows, then it scrolls
TICK_SECS = 1.0


class _FlippedView(AppKit.NSView):
    """Top-down coordinates, so rows stack in reading order."""

    def isFlipped(self):
        return True


class _ClickHandler(AppKit.NSObject):
    """Target for the status item button. Routes left vs right clicks."""

    def statusItemClicked_(self, sender):
        event = AppKit.NSApplication.sharedApplication().currentEvent()
        right = False
        if event is not None:
            right = (
                event.type() == AppKit.NSEventTypeRightMouseUp
                or bool(event.modifierFlags() & AppKit.NSEventModifierFlagControl)
            )
        try:
            (self._on_right if right else self._on_left)()
        except Exception:
            logger.exception("Status item click handler failed")


class _PopoverDelegate(AppKit.NSObject):
    def popoverDidClose_(self, notification):
        try:
            self._on_close()
        except Exception:
            logger.exception("popoverDidClose handler failed")


class _TickTarget(AppKit.NSObject):
    def tick_(self, timer):
        try:
            self._on_tick()
        except Exception:
            logger.exception("Popover tick failed")


class SessionPopoverController:
    """Owns the popover, its content views, and the while-open refresh timer."""

    def __init__(self, on_settings, hooks_installed):
        self._on_settings = on_settings
        self._hooks_installed = hooks_installed
        self._rows = []
        self._models = []
        self._snapshot = []
        self._stats = UsageStats()
        self._limits = UsageLimits()
        self._tick_timer = None
        self._click_monitor = None

        self._content = _FlippedView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, ROW_W, FOOTER_H)
        )

        controller = AppKit.NSViewController.alloc().init()
        controller.setView_(self._content)

        self._delegate = _PopoverDelegate.alloc().init()
        self._delegate._on_close = self._on_closed

        self._tick_target = _TickTarget.alloc().init()
        self._tick_target._on_tick = self._on_tick

        self._popover = AppKit.NSPopover.alloc().init()
        self._popover.setBehavior_(AppKit.NSPopoverBehaviorTransient)
        self._popover.setContentViewController_(controller)
        self._popover.setDelegate_(self._delegate)

    # --- State ---

    @property
    def is_shown(self) -> bool:
        return bool(self._popover.isShown())

    def set_snapshot(self, snapshot: list[dict]) -> None:
        """Take the latest snapshot, rebuilding views only if anyone is looking.

        Storing it is not optional while closed: show() renders from this copy,
        so a popover that skips updates it isn't displaying opens onto whatever
        was on screen last time. That is how a session evicted at 18:08 was
        still sitting there the next afternoon, idle, its dead PID behind the
        row — the daemon had long since said it was gone.
        """
        if self.is_shown:
            self.reload(snapshot)
        else:
            self._snapshot = list(snapshot)

    def reload(self, snapshot: list[dict]) -> None:
        """Rebuild the content from a session snapshot. Main thread only."""
        self._snapshot = list(snapshot)
        self._models = build_row_models(self._snapshot)

        for subview in list(self._content.subviews()):
            subview.removeFromSuperview()
        self._rows = []

        rows_h = self._build_rows()
        stats_top = rows_h + 1
        footer_top = stats_top + STATS_H + 1
        total_h = footer_top + FOOTER_H

        self._content.setFrameSize_(AppKit.NSMakeSize(ROW_W, total_h))
        self._build_stats(rows_h, stats_top)
        self._build_footer(stats_top + STATS_H, footer_top)
        self._popover.setContentSize_(AppKit.NSMakeSize(ROW_W, total_h))

    def refresh_usage(self) -> None:
        """Re-read usage and limits. Called when the popover opens, not on every
        snapshot push — the numbers move slowly and the scan touches disk."""
        try:
            self._stats = collect_usage_stats()
        except Exception:
            logger.exception("Could not read usage stats")
            self._stats = UsageStats()
        try:
            self._limits = read_usage_limits()
        except Exception:
            logger.exception("Could not read usage limits")
            self._limits = UsageLimits()

    # --- Content ---

    def _build_rows(self) -> float:
        if not self._models:
            empty = build_empty_state(empty_state_model(self._hooks_installed()))
            height = empty.frame().size.height
            empty.setFrameOrigin_(AppKit.NSMakePoint(0, 0))
            self._content.addSubview_(empty)
            return height

        natural_h = len(self._models) * ROW_H
        visible_h = min(natural_h, MAX_ROWS_H)

        container = _FlippedView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, ROW_W, natural_h)
        )
        for index, model in enumerate(self._models):
            row = build_session_row(model, on_click=self._click_handler_for(model))
            row.view.setFrameOrigin_(AppKit.NSMakePoint(0, index * ROW_H))
            container.addSubview_(row.view)
            self._rows.append(row)

        if natural_h <= visible_h:
            container.setFrameOrigin_(AppKit.NSMakePoint(0, 0))
            self._content.addSubview_(container)
            return visible_h

        scroll = AppKit.NSScrollView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, ROW_W, visible_h)
        )
        scroll.setDrawsBackground_(False)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setDocumentView_(container)
        self._content.addSubview_(scroll)
        return visible_h

    def _build_stats(self, separator_y: float, top: float) -> None:
        separator = build_separator()
        separator.setFrameOrigin_(AppKit.NSMakePoint(0, separator_y))
        self._content.addSubview_(separator)

        block = build_stats_block(self._stats, self._limits, time.time())
        block.setFrameOrigin_(AppKit.NSMakePoint(0, top))
        self._content.addSubview_(block)

    def _build_footer(self, separator_y: float, rows_h: float) -> None:
        separator = build_separator()
        separator.setFrameOrigin_(AppKit.NSMakePoint(0, separator_y))
        self._content.addSubview_(separator)

        target = self._settings_target()
        settings = AppKit.NSButton.buttonWithTitle_target_action_(
            "⚙︎  Settings",
            target,
            objc.selector(target.statusItemClicked_, signature=b"v@:@"),
        )
        settings.setBordered_(False)
        settings.setFont_(AppKit.NSFont.systemFontOfSize_(12))
        settings.setAlignment_(AppKit.NSTextAlignmentLeft)
        settings.setFrame_(AppKit.NSMakeRect(10, rows_h + 6, 110, 22))
        self._content.addSubview_(settings)

        summary = AppKit.NSTextField.labelWithString_(footer_text(self._snapshot))
        summary.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        summary.setTextColor_(AppKit.NSColor.tertiaryLabelColor())
        summary.setAlignment_(AppKit.NSTextAlignmentRight)
        summary.setFrame_(AppKit.NSMakeRect(ROW_W - 160, rows_h + 9, 150, 16))
        summary.setAutoresizingMask_(AppKit.NSViewMinXMargin)
        self._content.addSubview_(summary)

    # --- Focusing a session's app ---

    def _click_handler_for(self, model):
        """A click handler for a row, or None if there's nothing to focus.

        Resolution is deferred to click time rather than done here: it costs two
        subprocess calls per session, and reload() runs on every snapshot push.
        """
        if not model.pid:
            return None
        return lambda: self._focus_session(model)

    def _focus_session(self, model) -> None:
        host = resolve_host_app(model.pid)
        if host is None:
            # Nothing to focus — a session over SSH, or one whose process just
            # exited. Leave the popover open rather than acting on a stale row.
            logger.info("No host app for %s (pid %s)", model.project, model.pid)
            return

        self.close()

        # Two paths, deliberately. NSRunningApplication targets the exact PID,
        # which is what disambiguates two copies of the same editor; `open` goes
        # through LaunchServices, which works even when macOS refuses the direct
        # activation because we aren't the active app. Activating an app that is
        # already frontmost is a no-op, so doing both costs nothing.
        app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(
            host.pid
        )
        if app is not None:
            app.activateWithOptions_(AppKit.NSApplicationActivateAllWindows)

        try:
            subprocess.Popen(
                activation_command(host, session_cwd(model.pid)),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            logger.warning("Could not activate %s for %s", host.name, model.project)

        logger.info("Focused %s in %s", model.project, host.name)

    def _settings_target(self):
        """A click handler whose 'left' action opens Settings."""
        if not hasattr(self, "_settings_handler"):
            self._settings_handler = _ClickHandler.alloc().init()
            self._settings_handler._on_left = self._on_settings
            self._settings_handler._on_right = self._on_settings
        return self._settings_handler

    # --- Show / hide ---

    def show(self, button) -> None:
        self.refresh_usage()
        self.reload(self._snapshot)
        # An LSUIElement app that has never been active can leave a transient
        # popover without a resign-active event to close on.
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        button.setHighlighted_(True)
        self._popover.showRelativeToRect_ofView_preferredEdge_(
            button.bounds(), button, AppKit.NSRectEdgeMinY
        )
        self._button = button
        self._start_tick()
        self._install_click_monitor()

    def close(self) -> None:
        self._popover.performClose_(None)

    def toggle(self, button) -> None:
        if self.is_shown:
            self.close()
        else:
            self.show(button)

    def _on_closed(self) -> None:
        self._stop_tick()
        self._remove_click_monitor()
        button = getattr(self, "_button", None)
        if button is not None:
            button.setHighlighted_(False)

    # --- While-open refresh ---

    def _start_tick(self) -> None:
        if self._tick_timer is not None:
            return
        # Created on show and invalidated on close: a permanently scheduled 1 Hz
        # timer would wake the process every second for the app's whole life.
        self._tick_timer = (
            AppKit.NSTimer
            .scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                TICK_SECS, self._tick_target,
                objc.selector(self._tick_target.tick_, signature=b"v@:@"),
                None, True,
            )
        )

    def _stop_tick(self) -> None:
        if self._tick_timer is not None:
            self._tick_timer.invalidate()
            self._tick_timer = None

    def _on_tick(self) -> None:
        """Only the clock moved — don't tear down and rebuild views."""
        if not self.is_shown:
            self._stop_tick()
            return
        for row, model in zip(self._rows, build_row_models(self._snapshot)):
            row.apply_elapsed(model.elapsed_text)

    # --- Transient-dismissal backstop ---

    def _install_click_monitor(self) -> None:
        if self._click_monitor is not None:
            return
        mask = (AppKit.NSEventMaskLeftMouseDown | AppKit.NSEventMaskRightMouseDown)

        def _dismiss(_event):
            if self.is_shown:
                self.close()

        self._click_monitor = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            mask, _dismiss
        )

    def _remove_click_monitor(self) -> None:
        if self._click_monitor is not None:
            AppKit.NSEvent.removeMonitor_(self._click_monitor)
            self._click_monitor = None


def install_status_item_ui(app, popover: SessionPopoverController):
    """Detach rumps' menu and route status item clicks by hand.

    Must run after rumps.App.run() has built the status item — register this on
    rumps.events.before_start, which fires between initializeStatusBar() and the
    run loop starting.

    Returns a callable that pops the settings menu, or None if the status item
    isn't available (in which case rumps' plain menu keeps working).
    """
    nsapp = getattr(app, "_nsapp", None)
    status_item = getattr(nsapp, "nsstatusitem", None)
    if status_item is None:
        logger.warning("No status item yet; keeping the plain rumps menu")
        return None

    button = status_item.button()
    if button is None:
        logger.warning("Status item has no button; keeping the plain rumps menu")
        return None

    settings_menu = status_item.menu()

    def show_settings():
        if popover.is_shown:
            popover.close()
        # Re-attach, click, detach: popUpStatusItemMenu_ has been deprecated
        # since 10.14. performClick_ blocks until the menu closes.
        status_item.setMenu_(settings_menu)
        button.performClick_(None)
        status_item.setMenu_(None)

    handler = _ClickHandler.alloc().init()
    handler._on_left = lambda: popover.toggle(button)
    handler._on_right = show_settings

    status_item.setMenu_(None)
    button.setTarget_(handler)
    button.setAction_(objc.selector(handler.statusItemClicked_, signature=b"v@:@"))
    button.sendActionOn_(
        AppKit.NSEventMaskLeftMouseUp | AppKit.NSEventMaskRightMouseUp
    )

    # Keep the handler alive for the process lifetime — the button holds only a
    # weak reference to its target.
    app._status_click_handler = handler
    return show_settings
