# host/clawd_tank_menubar/status_icon.py
"""Map a session snapshot to what the status bar shows.

Pure functions over plain dicts — this module must never import AppKit. Keeping
the decision logic free of the UI framework is what makes it testable in CI,
where there is no window server. The split is structural rather than a
function-local import so it can't be violated without showing up in the imports.
"""

# Aggregate state -> icon file stem under icons/. Every value aggregate_state()
# can return must appear here; test_status_icon.py enforces that, and that the
# file exists on disk.
ICON_FILES = {
    "none": "crab-sleeping",      # no Claude sessions
    "idle": "crab-idle",          # sessions exist, none busy
    "thinking": "crab-thinking",
    "working": "crab-working",
    "waiting": "crab-waiting",    # blocked on you
    "error": "crab-error",
    "offline": "crab-offline",    # daemon thread died — not a session state
}

# Highest priority first. "waiting" outranks "working" because a session blocked
# on a human is the only state that needs you to do something; this mirrors the
# device-side ordering in ClawdDaemon._compute_display_state().
_PRIORITY = (
    ("error", {"error"}),
    ("waiting", {"waiting"}),
    # A tool failure ("confused") is a transient stumble, not an error worth its
    # own status bar glyph — the popover row still shows it distinctly.
    ("working", {"working", "confused"}),
    ("thinking", {"thinking"}),
)


def aggregate_state(snapshot: list[dict]) -> str:
    """Reduce every session's state to the one the status bar should show."""
    if not snapshot:
        return "none"
    present = {s.get("state", "idle") for s in snapshot}
    for name, states in _PRIORITY:
        if present & states:
            return name
    return "idle"  # covers "idle" and the momentary "registered"


def status_title(snapshot: list[dict]) -> str:
    """Text shown beside the icon. Empty when a number would be noise.

    A count of 1 is the common case and says nothing, so showing it would only
    make the status item change width every time a session starts or stops.
    """
    waiting = sum(1 for s in snapshot if s.get("state") == "waiting")
    if waiting:
        return f" {waiting}!"
    return f" {len(snapshot)}" if len(snapshot) > 1 else ""


def icon_name(state: str) -> str:
    """Icon file stem for an aggregate state, falling back to the idle crab."""
    return ICON_FILES.get(state, ICON_FILES["idle"])
