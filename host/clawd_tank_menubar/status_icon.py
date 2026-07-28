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

# Most urgent first. One ladder drives both which crab shows in the menu bar and
# which rows sort to the top of the popover, so the two can never disagree about
# what deserves your attention.
STATE_PRIORITY = (
    "error",       # stopped with an API error
    "waiting",     # blocked on you: a permission prompt or a question
    "confused",    # a tool failed
    "working",
    "thinking",
    "registered",  # the blink between SessionStart and the first real event
    "idle",
)

# Session state -> the icon it shows. Several states share a glyph: a tool
# failure is a stumble, not a status worth its own crab, and "registered" is
# indistinguishable from idle to anyone looking at the menu bar.
_STATE_ICONS = {
    "error": "error",
    "waiting": "waiting",
    "confused": "working",
    "working": "working",
    "thinking": "thinking",
    "registered": "idle",
    "idle": "idle",
}


def state_rank(state: str) -> int:
    """Sort key for a session state — lower is more urgent.

    Unknown states sort last rather than raising: a future daemon could invent
    one, and burying it is better than crashing the popover over it.
    """
    try:
        return STATE_PRIORITY.index(state)
    except ValueError:
        return len(STATE_PRIORITY)


def aggregate_state(snapshot: list[dict]) -> str:
    """Reduce every session's state to the one the status bar should show."""
    if not snapshot:
        return "none"
    most_urgent = min(
        (s.get("state", "idle") for s in snapshot), key=state_rank
    )
    return _STATE_ICONS.get(most_urgent, "idle")


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
