"""Tests for preferences read-modify-write, defaults merging and migration."""

import json

import pytest

from clawd_tank_menubar.preferences import (
    DEFAULTS,
    OBSOLETE_KEYS,
    load_preferences,
    save_preferences,
)


@pytest.fixture
def prefs_file(tmp_path):
    return tmp_path / "preferences.json"


# --- Defaults and merging ---


def test_load_returns_defaults_when_missing(prefs_file):
    assert load_preferences(prefs_file) == DEFAULTS


def test_load_returns_defaults_for_malformed_json(prefs_file):
    prefs_file.write_text("not json{{{")
    assert load_preferences(prefs_file) == DEFAULTS


def test_load_merges_missing_keys_with_defaults(prefs_file):
    prefs_file.write_text(json.dumps({"sounds_enabled": False}))
    result = load_preferences(prefs_file)
    assert result["sounds_enabled"] is False
    assert result["session_timeout"] == DEFAULTS["session_timeout"]


def test_every_default_is_always_present(prefs_file):
    """app.py indexes prefs directly, so a missing key is a KeyError at launch."""
    prefs_file.write_text(json.dumps({}))
    assert set(load_preferences(prefs_file)) >= set(DEFAULTS)


# --- Saving ---


def test_save_preserves_existing_keys(prefs_file):
    prefs_file.write_text(json.dumps({"sounds_enabled": False}))
    save_preferences(path=prefs_file, updates={"session_timeout": 120})
    result = json.loads(prefs_file.read_text())
    assert result["sounds_enabled"] is False
    assert result["session_timeout"] == 120


def test_save_creates_the_file_and_parent_directory(tmp_path):
    path = tmp_path / "subdir" / "preferences.json"
    save_preferences(path=path, updates={"sounds_enabled": False})
    assert load_preferences(path)["sounds_enabled"] is False


def test_int_preference_is_truthy(prefs_file):
    """Preferences written from rumps menu state are int 0/1, not bool."""
    prefs_file.write_text(json.dumps({"sounds_enabled": 1}))
    result = load_preferences(prefs_file)
    assert result["sounds_enabled"]
    assert result["sounds_enabled"] == 1
    assert result["sounds_enabled"] is not True


# --- Migration off the BLE/simulator keys ---


def test_obsolete_keys_are_dropped_from_the_result(prefs_file):
    prefs_file.write_text(json.dumps({key: True for key in OBSOLETE_KEYS}))
    result = load_preferences(prefs_file)
    assert not (set(result) & set(OBSOLETE_KEYS))


def test_obsolete_keys_are_pruned_from_disk(prefs_file):
    """save_preferences is read-modify-write, so leaving them in the file would
    carry them forward forever."""
    prefs_file.write_text(json.dumps(
        {"sounds_enabled": False, **{key: True for key in OBSOLETE_KEYS}}
    ))
    load_preferences(prefs_file)
    on_disk = json.loads(prefs_file.read_text())
    assert not (set(on_disk) & set(OBSOLETE_KEYS))
    assert on_disk["sounds_enabled"] is False


def test_migration_does_not_rewrite_a_clean_file(prefs_file):
    prefs_file.write_text(json.dumps({"sounds_enabled": False}))
    before = prefs_file.stat().st_mtime_ns
    load_preferences(prefs_file)
    assert prefs_file.stat().st_mtime_ns == before


def test_unknown_keys_survive(prefs_file):
    """An unrecognised key may belong to a newer build; only the known-obsolete
    list is pruned, so a downgrade doesn't destroy settings."""
    prefs_file.write_text(json.dumps({"future_setting": 42, "ble_enabled": True}))
    result = load_preferences(prefs_file)
    assert result["future_setting"] == 42
    assert json.loads(prefs_file.read_text())["future_setting"] == 42


def test_unwritable_prefs_file_does_not_break_loading(prefs_file):
    prefs_file.write_text(json.dumps({"ble_enabled": True, "sounds_enabled": False}))
    prefs_file.chmod(0o444)
    try:
        result = load_preferences(prefs_file)
    finally:
        prefs_file.chmod(0o644)
    assert result["sounds_enabled"] is False
    assert "ble_enabled" not in result
