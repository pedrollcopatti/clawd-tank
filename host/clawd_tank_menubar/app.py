# host/clawd_tank_menubar/app.py
"""Clawd Tank macOS status bar application.

The whole UI: a status item whose icon reflects what your Claude Code sessions
are doing, a popover listing them, and a settings menu. The daemon runs on a
background thread in this same process and pushes session snapshots here.
"""

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Optional

import rumps

from clawd_tank_daemon.daemon import ClawdDaemon, DaemonObserver
from . import hooks, launchd
from .popover import SessionPopoverController, install_status_item_ui
from .preferences import load_preferences, save_preferences
from .status_icon import aggregate_state, icon_name, status_title
from .version import get_version

logger = logging.getLogger("clawd-tank.menubar")

SESSION_TIMEOUT_OPTIONS = [
    ("1 minute", 60),
    ("2 minutes", 120),
    ("5 minutes", 300),
    ("10 minutes", 600),
    ("30 minutes", 1800),
    ("Never", 0),
]


class ClawdTankApp(rumps.App, DaemonObserver):
    def __init__(self):
        super().__init__("Clawd Tank", quit_button=None)

        self._daemon: Optional[ClawdDaemon] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_ready = threading.Event()
        self._sessions: list[dict] = []
        self._popover = None

        prefs = load_preferences()

        # How long a session may go quiet before it's assumed dead. Persisted
        # here rather than read back from a device, so the menu and the daemon
        # can't disagree about it.
        self._session_timeout_menu = rumps.MenuItem("Session Timeout")
        self._session_timeout_value = prefs["session_timeout"]
        for label, seconds in SESSION_TIMEOUT_OPTIONS:
            item = rumps.MenuItem(label, callback=self._on_session_timeout_select)
            item._seconds = seconds
            item.state = (seconds == self._session_timeout_value)
            self._session_timeout_menu.add(item)

        self._sounds_toggle = rumps.MenuItem(
            "Alert Sounds", callback=self._on_toggle_sounds
        )
        self._sounds_toggle.state = prefs["sounds_enabled"]

        self._hooks_item = rumps.MenuItem(
            "Install Claude Code Hooks", callback=self._on_install_hooks
        )
        self._hooks_item.state = hooks.are_hooks_installed()

        self._login_item = rumps.MenuItem(
            "Launch at Login", callback=self._on_toggle_login
        )
        self._login_item.state = launchd.is_enabled()

        if launchd.is_enabled() and launchd.is_stale():
            logger.info("Launchd plist is stale, updating to current executable")
            launchd.enable()

        self._version_item = rumps.MenuItem(f"Version: {get_version()}")
        self._version_item.set_callback(None)

        self._quit_item = rumps.MenuItem("Quit Clawd Tank", callback=self._on_quit)

        self.menu = [
            self._session_timeout_menu,
            self._sounds_toggle,
            None,
            self._hooks_item,
            self._login_item,
            None,
            self._version_item,
            self._quit_item,
        ]

        # Colour, not a template image: template images are drawn from alpha
        # alone, which flattens the crab to a featureless blob and loses the red
        # "!" and the error stars — the whole point of the icon.
        self.template = False
        self._update_status_item()

        # The status item doesn't exist until rumps builds it in run(); this
        # event fires right after, before the run loop starts.
        self._popover = SessionPopoverController(
            on_settings=self._show_settings_menu,
            hooks_installed=hooks.are_hooks_installed,
        )
        self._show_settings = None
        rumps.events.before_start.register(self._install_popover)

    def _install_popover(self):
        """Take over status item clicks: left opens the popover, right the menu.

        rumps.events.emit() swallows exceptions with a bare traceback print, so
        a failure here would otherwise leave a dead status item with no log.
        """
        try:
            self._show_settings = install_status_item_ui(self, self._popover)
        except Exception:
            logger.exception("Could not install the popover; falling back to the menu")

    def _show_settings_menu(self):
        if self._show_settings is not None:
            self._show_settings()

    @property
    def _daemon_alive(self) -> bool:
        return hasattr(self, "_daemon_thread") and self._daemon_thread.is_alive()

    # --- Lifecycle ---

    def _start_daemon_thread(self):
        """Start the daemon's asyncio event loop in a background thread."""
        self._daemon = ClawdDaemon(observer=self, headless=False)

        def run_loop():
            try:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                self._loop_ready.set()
                self._loop.run_until_complete(self._daemon.run())
                logger.info("Daemon thread exited normally")
            except Exception:
                logger.exception("Daemon thread crashed")
            finally:
                self._loop_ready.set()  # unblock main thread if still waiting

        self._daemon_thread = threading.Thread(target=run_loop, daemon=True)
        self._daemon_thread.start()
        self._loop_ready.wait(timeout=5)

        prefs = load_preferences()
        self._daemon.set_sounds_enabled(prefs["sounds_enabled"])
        self._daemon.set_session_timeout(prefs["session_timeout"])
        self._update_status_item()

    # --- DaemonObserver (called from the daemon's asyncio thread) ---

    def on_sessions_change(self, snapshot: list[dict]) -> None:
        self._schedule_main(self._apply_snapshot, snapshot)

    def _schedule_main(self, fn, *args):
        """Run fn on the main thread. Safe to call from the daemon's thread."""
        try:
            from PyObjCTools.AppHelper import callAfter
            callAfter(fn, *args)
        except ImportError:
            fn(*args)

    def _apply_snapshot(self, snapshot: list[dict]) -> None:
        """Main thread. Store the snapshot and refresh what it drives."""
        self._sessions = snapshot
        self._update_status_item()
        # Rebuilding views for a popover nobody is looking at is wasted work;
        # show() reloads from the stored snapshot anyway.
        if self._popover is not None and self._popover.is_shown:
            self._popover.reload(snapshot)

    # --- Status item ---

    @rumps.timer(30)
    def _health_check(self, _):
        """Periodic check to detect daemon thread death."""
        if not self._daemon_alive:
            self._update_status_item()

    def _update_status_item(self) -> None:
        """Set the status bar icon and title from the current session state."""
        state = "offline" if not self._daemon_alive else aggregate_state(self._sessions)
        self.icon = self._icon_path(icon_name(state))
        self.title = "" if state == "offline" else status_title(self._sessions)

    def _icon_path(self, name: str) -> Optional[str]:
        """Return path to icon file, or None if not found."""
        import importlib.resources
        try:
            icons_dir = importlib.resources.files("clawd_tank_menubar") / "icons"
            path = icons_dir / f"{name}.png"
            if hasattr(path, '__fspath__'):
                return str(path)
        except Exception:
            pass
        return None

    # --- Menu callbacks ---

    def _on_session_timeout_select(self, sender):
        seconds = sender._seconds
        self._session_timeout_value = seconds
        for _key, item in self._session_timeout_menu.items():
            item.state = (item._seconds == seconds)
        save_preferences(updates={"session_timeout": seconds})
        if self._daemon:
            self._daemon.set_session_timeout(seconds)

    def _on_toggle_sounds(self, sender):
        """Toggle macOS alert sounds on/off."""
        sender.state = not sender.state
        save_preferences(updates={"sounds_enabled": sender.state})
        if self._daemon:
            self._daemon.set_sounds_enabled(sender.state)

    def _on_install_hooks(self, sender):
        was_installed = hooks.are_hooks_installed()
        hooks.install_notify_script()
        hooks.install_hooks()
        sender.state = True
        if was_installed:
            rumps.alert(
                title="Hooks Updated",
                message="Claude Code hooks have been updated. "
                        "Restart your Claude Code sessions for the changes to take effect.",
            )
        else:
            rumps.alert(
                title="Hooks Installed",
                message="Claude Code hooks have been added to ~/.claude/settings.json. "
                        "Restart your Claude Code sessions for the hooks to take effect.",
            )

    def _on_toggle_login(self, sender):
        if launchd.is_enabled():
            launchd.disable()
        else:
            launchd.enable()
        sender.state = launchd.is_enabled()

    def _on_quit(self, _):
        try:
            if self._loop and self._daemon:
                future = asyncio.run_coroutine_threadsafe(
                    self._daemon._shutdown(), self._loop
                )
                future.result(timeout=8)
            rumps.quit_application()
        except Exception:
            logger.exception("Error during quit, force-killing")
            logging.shutdown()
            os._exit(1)


def main():
    log_dir = Path.home() / "Library" / "Logs" / "ClawdTank"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "clawd-tank.log"),
        ],
    )
    logger.info("Clawd Tank %s starting", get_version())

    hooks.install_notify_script()
    if not hooks.are_hooks_installed():
        logger.info("Hooks outdated, auto-updating...")
        hooks.install_hooks()

    app = ClawdTankApp()
    app._start_daemon_thread()
    app.run()


if __name__ == "__main__":
    main()
