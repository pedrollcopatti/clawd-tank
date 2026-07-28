# host/clawd_tank_menubar/preferences.py
"""Persistent preferences for the Clawd Tank menubar app."""

import json
import logging
from pathlib import Path

logger = logging.getLogger("clawd-tank.menubar")

DEFAULTS = {
    "sounds_enabled": True,
    "session_timeout": 600,
}

# Keys from the BLE/simulator era. save_preferences() is read-modify-write, so
# without an explicit prune these would survive in the file forever.
OBSOLETE_KEYS = (
    "ble_enabled",
    "sim_enabled",
    "sim_window_visible",
    "sim_always_on_top",
)

PREFS_PATH = Path.home() / ".clawd-tank" / "preferences.json"


def load_preferences(path: Path = PREFS_PATH) -> dict:
    """Load preferences from disk, merged with defaults for missing keys.

    Prunes keys this version no longer understands, once, on first load. Only
    the known-obsolete list is dropped — an unrecognised key might belong to a
    newer build and must survive a downgrade.
    """
    result = dict(DEFAULTS)
    try:
        stored = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return result

    if isinstance(stored, dict):
        if any(key in stored for key in OBSOLETE_KEYS):
            for key in OBSOLETE_KEYS:
                stored.pop(key, None)
            try:
                path.write_text(json.dumps(stored, indent=2) + "\n")
            except OSError:
                # Best effort — a read-only prefs file must not break startup.
                logger.warning("Could not prune obsolete preference keys")
        result.update(stored)
    return result


def save_preferences(path: Path = PREFS_PATH, updates: dict = None) -> None:
    """Read-modify-write: load existing, merge updates, save back."""
    if updates is None:
        updates = {}
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    try:
        existing = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    existing.update(updates)
    path.write_text(json.dumps(existing, indent=2) + "\n")
